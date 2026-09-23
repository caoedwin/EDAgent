"""RAG 文档入库流水线。

阶段：文档加载(PDF/Markdown/TXT/HTML) -> 语义分块(512/50) -> 向量化 -> ChromaDB 持久化。
集合命名规范：agent_{project_name}（见 AGENT_COLLECTION_NAME=agent_edagent）。
"""

import io
import re
import uuid
from pathlib import Path

from bs4 import BeautifulSoup
from chromadb import HttpClient as ChromaHttpClient
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

from app.config import settings
from app.llm.client import get_embedding_model

SUPPORTED_SUFFIXES = {".pdf", ".md", ".markdown", ".txt", ".html", ".htm"}

_splitter = RecursiveCharacterTextSplitter(
    chunk_size=settings.rag_chunk_size,
    chunk_overlap=settings.rag_chunk_overlap,
    length_function=len,
)


def get_chroma_client() -> ChromaHttpClient:
    # chromadb>=0.5 的 host 支持直接传入完整 URL
    return ChromaHttpClient(host=settings.chroma_url)


def _load_pages(filename: str, content: bytes) -> list[tuple[str, int | None]]:
    """读取文档，返回 [(页面文本, 页码)]；非 PDF 页码为 None。"""
    suffix = Path(filename).suffix.lower()

    if suffix == ".pdf":
        reader = PdfReader(io.BytesIO(content))
        pages: list[tuple[str, int | None]] = []
        for index, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                pages.append((text, index))
        return pages

    raw = content.decode("utf-8", errors="ignore")
    if suffix in {".html", ".htm"}:
        raw = BeautifulSoup(raw, "html.parser").get_text("\n")
    # Markdown / TXT / HTML 提取后统一做一次空行压缩
    raw = re.sub(r"\n{3,}", "\n\n", raw).strip()
    return [(raw, None)] if raw else []


def _safe_filename(filename: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fa5]+", "_", filename).strip("._")
    return f"{uuid.uuid4().hex[:8]}_{cleaned or 'document'}"


def ingest_document(
    filename: str, content: bytes, user_id: str | None = None
) -> dict:
    """保存原始文件并把分块向量写入 ChromaDB，返回入库统计。

    user_id 非空时文档归属该用户（检索按用户隔离）；None 表示全局共享文档，
    仅允许通过 Bearer API Key 的程序化调用写入。
    """
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"不支持的文档类型: {suffix}，支持 {sorted(SUPPORTED_SUFFIXES)}")

    owner = user_id or "_global"
    stored_name = _safe_filename(filename)
    ingest_dir = Path(settings.ingest_dir) / owner
    ingest_dir.mkdir(parents=True, exist_ok=True)
    (ingest_dir / stored_name).write_bytes(content)

    pages = _load_pages(filename, content)
    if not pages:
        raise ValueError("文档内容为空，未能提取到任何文本")

    documents: list[Document] = []
    for page_text, page_no in pages:
        for chunk_index, chunk in enumerate(_splitter.split_text(page_text)):
            documents.append(
                Document(
                    page_content=chunk,
                    metadata={
                        "source": stored_name,
                        "original_name": filename,
                        "doc_type": suffix.lstrip("."),
                        "page": page_no if page_no is not None else 0,
                        "chunk_index": chunk_index,
                        "owner": owner,
                    },
                )
            )

    if not documents:
        raise ValueError("分块结果为空，请检查文档内容")

    client = get_chroma_client()
    # 同一用户同名文件重复入库：先清掉旧块，再写入新块
    collection = client.get_or_create_collection(settings.collection_name)
    collection.delete(
        where={"$and": [{"owner": owner}, {"source": stored_name}]}
    )

    # 使用 langchain-chroma 写入（embedding 走 OpenAI 兼容协议）
    from langchain_chroma import Chroma

    vector_store = Chroma(
        collection_name=settings.collection_name,
        embedding_function=get_embedding_model(),
        client=client,
    )
    vector_store.add_documents(documents)

    return {
        "filename": stored_name,
        "original_name": filename,
        "pages": len(pages),
        "chunks": len(documents),
    }


def user_filter(user_id: str | None) -> dict | None:
    """Chroma where 条件：UI 用户只检索自己的文档；API Key 调用不做隔离。"""
    return {"owner": user_id} if user_id is not None else None


def delete_document(source: str, user_id: str | None = None) -> bool:
    """删除指定文档的全部分块（按归属隔离），返回是否有数据被删除。"""
    owner = user_id or "_global"
    client = get_chroma_client()
    try:
        collection = client.get_collection(settings.collection_name)
    except Exception:
        return False
    existing = collection.get(
        where={"$and": [{"owner": owner}, {"source": source}]}, include=[]
    )
    ids = existing.get("ids") or []
    if not ids:
        return False
    collection.delete(ids=ids)
    # 同步删除落盘原文件（忽略失败）
    file_path = Path(settings.ingest_dir) / owner / source
    try:
        file_path.unlink(missing_ok=True)
    except OSError:
        pass
    return True


def kb_document_count(user_id: str | None = None) -> int:
    """轻量查询知识库分块数量（供路由节点判断是否可走 RAG）。"""
    try:
        collection = get_chroma_client().get_collection(settings.collection_name)
        return int(collection.count(where=user_filter(user_id)))
    except Exception:
        return 0


def collection_stats(user_id: str | None = None) -> dict:
    client = get_chroma_client()
    try:
        collection = client.get_collection(settings.collection_name)
    except Exception:
        return {"collection": settings.collection_name, "documents": 0, "files": []}

    result = collection.get(where=user_filter(user_id), include=["metadatas"])
    metadatas = result.get("metadatas") or []
    # 按 source 聚合成文件视图（原始文件名 + 分块数）
    files: dict[str, dict] = {}
    for meta in metadatas:
        if not meta or not meta.get("source"):
            continue
        source = meta["source"]
        item = files.setdefault(
            source,
            {"filename": source, "original_name": meta.get("original_name") or source, "chunks": 0},
        )
        item["chunks"] += 1
    return {
        "collection": settings.collection_name,
        "documents": len(result.get("ids") or []),
        "files": sorted(files.values(), key=lambda item: item["original_name"]),
    }
