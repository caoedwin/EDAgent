"""统一配置：所有设置以 AGENT_ 为前缀，来自环境变量（compose 注入）或本地 .env。

业务代码不判断本地 Ollama / 在线 API，统一走 OpenAI 兼容协议，
仅通过 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL 切换。
"""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "development"

    # ---------- LLM（OpenAI 兼容协议）----------
    llm_base_url: str = "http://localhost:11534/v1"
    llm_api_key: SecretStr = SecretStr("ollama")
    llm_model: str = "deepseek-r1:8b"

    embedding_base_url: str = "http://localhost:11534/v1"
    embedding_api_key: SecretStr = SecretStr("ollama")
    embedding_model: str = "nomic-embed-text"

    llm_temperature: float = 0.1
    llm_max_tokens: int = 2048
    llm_timeout: int = 180
    llm_max_retries: int = 2

    # ---------- 存储 / 缓存 ----------
    database_url: str = "postgresql://agent:change_me@localhost:15432/ai_agent"
    redis_url: str = "redis://localhost:16379/0"
    chroma_url: str = "http://localhost:18000"
    collection_name: str = "agent_edagent"
    ingest_dir: str = "/data/ingest"

    # ---------- Agent ----------
    max_agent_steps: int = 15

    # ---------- 多 Agent 协作 ----------
    team_max_rounds: int = 2
    team_planner_prompt: str = (
        '你是分析小组的"规划者"。把用户的问题拆解为 2-4 个关键分析要点，'
        "帮助执行者给出全面、有深度的回答。只输出要点列表，不要回答问题本身。格式：\n"
        "1. 要点一\n2. 要点二"
    )
    team_reviewer_prompt: str = (
        '你是分析小组的"审稿者"。对照用户问题和分析计划，评估草稿是否：\n'
        "- 完整覆盖了各个要点\n- 结论明确、逻辑自洽\n- 没有遗漏用户问的子问题\n\n"
        "当前是第 {iteration}/{max_rounds} 轮审稿（达到上限时请放行）。\n"
        "只输出一行 JSON，不要输出其他内容：\n"
        '{{"verdict": "approve|revise", "notes": "一句话审稿意见（revise 时给出具体修改要求）"}}'
    )

    # ---------- RAG ----------
    rag_chunk_size: int = 512
    rag_chunk_overlap: int = 50
    rag_top_k: int = 5
    rag_mmr_lambda_mult: float = 0.5

    # ---------- 可观测性 ----------
    otel_exporter_otlp_endpoint: str = ""  # 为空则不向 collector 导出
    otel_service_name: str = "edagent-api"

    # ---------- 安全 ----------
    api_key: SecretStr = SecretStr("change_me")
    # Web UI 登录会话 Cookie 签名密钥（生产环境必须通过 AGENT_SESSION_SECRET 覆盖）
    session_secret: SecretStr = SecretStr("edagent-dev-session-secret-change-me")
    session_max_age_seconds: int = 7 * 24 * 3600
    # SPA 静态文件目录（Docker 构建时由前端产物填充）
    static_dir: str = "/app/static"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
