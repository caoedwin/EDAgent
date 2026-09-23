"""RAG 检索层：ChromaDB HttpClient + MMR（Top-K=5, lambda_mult=0.5）。"""

from functools import lru_cache

from langchain_core.documents import Document
from langchain_chroma import Chroma

from app.config import settings
from app.llm.client import get_embedding_model
from app.rag.ingest import get_chroma_client


@lru_cache(maxsize=1)
def get_vector_store() -> Chroma:
    client = get_chroma_client()
    # get_or_create_collection：集合不存在时自动创建（维度由首批 embedding 决定）
    client.get_or_create_collection(settings.collection_name)
    return Chroma(
        collection_name=settings.collection_name,
        embedding_function=get_embedding_model(),
        client=client,
    )


def get_retriever(user_id: str | None = None):
    """MMR 检索器：相似度 Top-K=5 基础上做最大边际相关性去重。

    user_id 非空时只检索该用户拥有的文档；None 表示不做归属过滤（API Key 调用）。
    """
    search_kwargs: dict = {
        "k": settings.rag_top_k,
        "lambda_mult": settings.rag_mmr_lambda_mult,
    }
    if user_id is not None:
        search_kwargs["filter"] = {"owner": user_id}
    return get_vector_store().as_retriever(
        search_type="mmr",
        search_kwargs=search_kwargs,
    )


def retrieve(query: str, user_id: str | None = None) -> list[Document]:
    return get_retriever(user_id).invoke(query)


def format_context(documents: list[Document]) -> str:
    """把检索结果注入 LLM 上下文，标注来源文档与页码。"""
    blocks = []
    for index, doc in enumerate(documents, start=1):
        source = doc.metadata.get("source", "unknown")
        page = doc.metadata.get("page") or 0
        page_label = f"，第 {page} 页" if page else ""
        blocks.append(f"[资料 {index}｜来源: {source}{page_label}]\n{doc.page_content}")
    return "\n\n".join(blocks)
