# Phase 3：能力扩展（插件系统 + 批量导入 + RAG 调参）

## Context

Team OS 阶段 1-3 已完成并验证。用户选择「能力扩展类」作为第三阶段，包含三个功能：
1. **插件系统**（进程内 Python 插件）
2. **批量文档导入**（后台任务 + 逐文件状态）
3. **RAG 检索调优**（暴露调参开关）

决策已通过 AskUserQuestion 确认。

---

## Feature 1：插件系统

### 思路
在 `plugins/` 目录放置 `.py` 文件，启动时用 `importlib` 扫描加载，收集 `@tool` 装饰的函数合并到 `TOOLS`/`TOOL_MAP`。不支持热重载，上传/删除后需重启。

### 文件变更

| 操作 | 文件 | 说明 |
|------|------|------|
| 新建 | `plugins/__init__.py` | 空文件 |
| 新建 | `plugins/example_plugin.py` | 示例插件 |
| 新建 | `app/plugins/__init__.py` | 空文件 |
| 新建 | `app/plugins/loader.py` | 扫描/加载/校验核心 |
| 新建 | `app/api/plugin_routes.py` | 管理端点（admin only） |
| 修改 | `app/agents/tools.py` | 合并插件工具到 TOOLS/TOOL_MAP |
| 修改 | `app/main.py` | 注册 plugin_router |
| 修改 | `docker-compose.yml` | 挂载 `./plugins:/app/plugins` |
| 修改 | `Dockerfile` | 预建 `/app/plugins` 并赋权 |

### 关键代码

**`app/plugins/loader.py`**：
- `load_plugins()`: 扫描 `plugins/*.py`（跳过 `_` 开头），`importlib.util.spec_from_file_location` 导入，收集带 `.name` + `.invoke` 属性的对象（即 `@tool` 装饰后的函数）
- 单个插件加载失败只记日志，不阻断启动
- `get_plugin_info()`: 返回插件文件列表（管理 API 用）
- `save_plugin_file(filename, content)`: 保存上传的 `.py` 文件
- `delete_plugin_file(name)`: 删除插件文件

**`app/agents/tools.py`** 末尾追加：
```python
from app.plugins.loader import load_plugins
_PLUGIN_TOOLS = load_plugins()
TOOLS = [calculator, current_time, word_count, knowledge_search, *_PLUGIN_TOOLS]
TOOL_MAP = {tool_item.name: tool_item for tool_item in TOOLS}
```

**`app/api/plugin_routes.py`**：
- `GET /api/v1/plugins` — 列出插件（admin）
- `POST /api/v1/plugins` — 上传 `.py` 文件（admin，1MB 上限）
- `DELETE /api/v1/plugins/{name}` — 删除插件（admin）
- 均使用 `require_admin` 依赖

---

## Feature 2：批量文档导入

### 思路
`POST /rag/batch` 接收多文件，创建任务记录后立即返回 `job_id`。`asyncio.create_task` 后台执行，`Semaphore(2)` 限制并发，逐文件调用 `ingest_document()`。`GET /rag/batch/{job_id}` 查询进度。

### 文件变更

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `app/db.py` | 新增 `ingest_jobs` 表 + CRUD |
| 新建 | `app/rag/batch.py` | 后台批量摄取 worker |
| 修改 | `app/api/routes.py` | 新增 `POST /rag/batch` + `GET /rag/batch/{job_id}` |
| 修改 | `frontend/src/views/Chat.vue` | 批量上传 UI + 进度轮询 |

### 数据库 Schema（`app/db.py` 追加）

```sql
CREATE TABLE IF NOT EXISTS ingest_jobs (
    id            UUID PRIMARY KEY,
    user_id       BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status        VARCHAR(16) NOT NULL DEFAULT 'running',
    total_files   INT NOT NULL,
    completed_files INT NOT NULL DEFAULT 0,
    failed_files  INT NOT NULL DEFAULT 0,
    results       JSONB NOT NULL DEFAULT '[]',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ingest_jobs_user ON ingest_jobs(user_id, created_at DESC);
```

新增 CRUD：`create_ingest_job()`、`update_ingest_job()`、`get_ingest_job()`（按 user_id 隔离）。

### API 端点

**`POST /rag/batch`**：
- 接收 `list[UploadFile]`，限制 20 个文件、单文件 50MB
- 读取所有文件内容到内存，创建 `ingest_jobs` 记录
- `asyncio.create_task` 启动后台摄取
- 返回 `{"job_id": ..., "total_files": ..., "status": "running"}`

**`GET /rag/batch/{job_id}`**：
- 返回任务状态 + 每文件结果（filename, status, chunks/error）

### 前端（Chat.vue）

- 将 `el-upload` 改为 `:auto-upload="false"`，收集文件到 `pendingFiles` ref
- 新增「开始导入」按钮，调用 `POST /rag/batch` 发送所有文件
- 新增进度展示区：`el-progress` + 每文件状态标签，1s 轮询 `GET /rag/batch/{job_id}`

---

## Feature 3：RAG 调参

### 文件变更

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `app/config.py` | 新增 `rag_search_type`、`rag_score_threshold` |
| 修改 | `app/rag/retriever.py` | 根据配置选择检索策略 |
| 修改 | `.env.example` | 新增环境变量示例 |

### 配置扩展

```python
rag_search_type: str = "mmr"  # mmr / similarity / similarity_score_threshold
rag_score_threshold: float = 0.0  # 仅 similarity_score_threshold 生效
```

### 检索器改造

`get_retriever()` 根据 `rag_search_type` 选择 `search_type`，非法值回退 `mmr`：
- `mmr`: 加 `lambda_mult`
- `similarity_score_threshold`: 加 `score_threshold`

---

## 验证步骤

### 插件系统
1. `plugins/test_plugin.py` 写入 `@tool` 函数，重启容器
2. 日志确认 `插件 test_plugin 注册工具: xxx`
3. `POST /api/v1/agent` 发送需要该工具的消息，确认被调用
4. `GET /api/v1/plugins` 确认返回列表
5. `DELETE` 后重启，工具不可用

### 批量导入
1. 准备 3 个不同格式文档
2. 前端知识库抽屉选多文件，点「开始导入」
3. 观察进度条 0%→100%，每文件显示成功/失败
4. `GET /api/v1/rag/batch/{job_id}` 确认 results 数组
5. `GET /api/v1/rag/stats` 确认分块数增加
6. 提问验证 RAG 检索到新文档

### RAG 调参
1. 默认启动，`/rag/query` 正常返回
2. `.env` 设 `AGENT_RAG_SEARCH_TYPE=similarity`，重启后行为变化
3. 设 `similarity_score_threshold` + `0.8`，确认低分结果被过滤
4. 设非法值，确认回退 `mmr` 不报错

---

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| 插件代码安全（进程内运行） | 文档明确禁止危险操作；本地自托管场景可接受 |
| 插件导入失败导致启动崩溃 | try/except 包裹，单插件失败不阻断 |
| 批量导入内存压力（50MB×20=1GB） | 限制单文件 50MB、总数 20；后续可改流式 |
| 并发摄取 CPU 打满 | `Semaphore(2)` 限制并发 |
| 插件工具名冲突 | 后加载覆盖先加载，记警告日志 |
