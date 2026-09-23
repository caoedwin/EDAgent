---
name: langgraph-agent-builder
description: ---
name: langgraph-agent-builder
description: 帮助用户设计和实现 LangGraph 状态机式 Agent 工作流。当用户需要创建 Agent 图、定义节点和边、配置状态管理或实现检查点持久化时使用。
---

# LangGraph Agent 构建器

## 图结构模板

标准 Agent 图包含以下节点：
1. **Router 节点**：判断任务类型（简单问答 / 复杂任务 / RAG 检索）
2. **ReAct 节点**：思考-行动-观察循环
3. **Tool 节点**：工具执行（沙箱中运行）
4. **RAG 节点**：ChromaDB 语义检索
5. **Report 节点**：结构化输出生成

## 状态定义要求

- 所有状态字段必须有类型注解
- 使用 TypedDict 或 Pydantic Model 定义 State
- 必须包含 `messages`、`next_action`、`tool_results`、`checkpoint_id` 字段

## 检查点配置

- 使用 PostgreSQL 作为检查点存储后端
- 每个节点执行后自动保存状态
- 支持从任意检查点恢复执行
- 配置最大重试次数（默认 3 次）

## 条件路由规则

- 简单问答：直接调用 LLM，不进入 ReAct 循环
- 需要工具调用：进入 ReAct 循环，最大步数由 `MAX_AGENT_STEPS` 控制（默认 15）
- 需要知识检索：先执行 RAG，再进入 ReAct
- 任务完成：跳转到 Report 节点
---

#langgraph-agent-builder