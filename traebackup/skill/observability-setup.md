---
name: observability-setup
description: 帮助用户为 AI Agent 配置完整的可观测性体系，包括 OpenTelemetry 追踪、Prometheus 指标和 Grafana 仪表板。当用户需要监控 Agent 性能、Token 用量或调试执行流程时使用。
---

# 可观测性集成

## OpenTelemetry 追踪

每个 Agent 步骤创建 Span，必须标注：
- gen_ai.system: ollama
- gen_ai.request.model: 模型名称
- gen_ai.operation.name: chat / embeddings
- gen_ai.usage.input_tokens: 输入 Token 数
- gen_ai.usage.output_tokens: 输出 Token 数

## Prometheus 指标

必须暴露的指标：
- agent_request_duration_seconds（直方图）
- agent_token_usage_total（计数器）
- agent_tool_call_total（计数器）
- agent_error_total（计数器）
- llm_inference_duration_seconds（直方图）

## OTel Collector 配置

接收器：OTLP（gRPC :4317, HTTP :4318）
导出器：Jaeger（追踪）+ Prometheus（指标）

## 告警规则

- p95 请求延迟 > 5s
- 错误率 > 1%
- GPU 显存使用率 > 90%
- 队列深度 > 100