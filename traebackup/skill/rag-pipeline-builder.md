---
name: rag-pipeline-builder
description: 帮助用户构建完整的 RAG（检索增强生成）流水线，包括文档导入、文本分块、向量化存储和语义检索。当用户需要实现基于私有文档的问答系统时使用。
---

# RAG Pipeline 构建器

## 流水线阶段

1. **文档加载**：支持 PDF、Markdown、TXT、HTML
2. **文本分块**：按语义分块，chunk_size=512，chunk_overlap=50
3. **向量化**：使用 nomic-embed-text（通过 Ollama）
4. **存储**：ChromaDB，配置持久化目录
5. **检索**：相似度检索 + MMR（最大边际相关性）

## ChromaDB 配置要点

- 使用 HttpClient 模式连接 ChromaDB 容器
- 集合命名规范：`agent_{project_name}`
- 必须启用持久化（IS_PERSISTENT=TRUE）
- 禁用遥测（ANONYMIZED_TELEMETRY=FALSE）

## 检索策略

- 默认返回 Top-K=5 结果
- 使用 MMR 去重，lambda_mult=0.5
- 检索结果注入 LLM 上下文时，标注来源文档和页码
- 支持元数据过滤（按文档类型、日期范围等）