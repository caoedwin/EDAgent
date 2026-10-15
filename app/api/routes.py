"""业务路由：简单问答 / LangGraph Agent / RAG 文档管理与问答。"""

import asyncio
import uuid

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from langchain_core.messages import HumanMessage, SystemMessage

from app import db
from app.api.schemas import (
    AgentRequest,
    AgentResponse,
    ChatRequest,
    ChatResponse,
    RagDocumentResponse,
    RagQueryRequest,
    RagQueryResponse,
)
from app.config import settings
from app.llm.client import chat_invoke
from app.rag.batch import run_batch_ingest
from app.rag.ingest import collection_stats, delete_document, ingest_document
from app.rag.retriever import format_context, retrieve
from app.security.web_auth import request_owner_id, session_user_id

router = APIRouter(prefix="/api/v1")

_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 单个文档 50MB 上限


@router.post("/chat", response_model=ChatResponse, tags=["chat"])
def simple_chat(payload: ChatRequest) -> ChatResponse:
    """简单问答：直接调用 LLM，不进入 Agent 工作流。"""
    response = chat_invoke(
        [HumanMessage(content=payload.message)],
        temperature=payload.temperature,
    )
    return ChatResponse(answer=str(response.content), model=settings.llm_model)


@router.post("/agent", response_model=AgentResponse, tags=["agent"])
async def run_agent(payload: AgentRequest, request: Request) -> AgentResponse:
    """运行 LangGraph Agent：Router -> (直接回答 / ReAct+工具 / RAG) -> Report。"""
    thread_id = payload.thread_id or f"thread-{uuid.uuid4().hex}"
    graph = request.app.state.graph

    final_state = await graph.ainvoke(
        {
            "messages": [HumanMessage(content=payload.message)],
            "session_id": thread_id,
            "tool_results": [],
        },
        config={
            "configurable": {
                "thread_id": thread_id,
                "user_id": request_owner_id(request),
            },
            # agent/tools 各占一次递归，按最大步数换算并留余量
            "recursion_limit": settings.max_agent_steps * 2 + 4,
        },
    )

    report = final_state.get("final_report")
    if not report:
        # 兜底：理论上 report 节点一定执行
        report = {
            "answer": str(final_state["messages"][-1].content),
            "route": None,
            "tool_results": [],
            "sources": [],
            "model": settings.llm_model,
        }
    return AgentResponse(thread_id=thread_id, **report)


@router.post("/rag/documents", response_model=RagDocumentResponse, tags=["rag"])
async def upload_document(request: Request, file: UploadFile = File(...)) -> RagDocumentResponse:
    """上传 PDF / Word / Excel / Markdown / TXT / HTML 文档并写入向量库。

    登录用户上传的文档仅本人可检索；Bearer API Key 上传的为全局共享文档。
    """
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件超过 50MB 上限")
    try:
        result = ingest_document(
            file.filename or "document.txt", content, user_id=request_owner_id(request)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RagDocumentResponse(**result)


@router.delete("/rag/documents/{source}", tags=["rag"])
def remove_document(source: str, request: Request) -> dict:
    """删除知识库中的指定文档（按调用者归属隔离）。"""
    deleted = delete_document(source, user_id=request_owner_id(request))
    if not deleted:
        raise HTTPException(status_code=404, detail="文档不存在或无权删除")
    return {"ok": True, "filename": source}


@router.get("/rag/stats", tags=["rag"])
def rag_stats(request: Request) -> dict:
    """查看知识库集合统计（分块数、文件列表；按调用者归属过滤）。"""
    return collection_stats(user_id=request_owner_id(request))


@router.post("/rag/query", response_model=RagQueryResponse, tags=["rag"])
def rag_query(payload: RagQueryRequest, request: Request) -> RagQueryResponse:
    """RAG 问答：MMR 检索 Top-K=5；answer=true 时基于资料生成带来源的回答。"""
    documents = retrieve(payload.query, request_owner_id(request))
    sources = [
        {
            "source": doc.metadata.get("source"),
            "page": doc.metadata.get("page") or None,
            "original_name": doc.metadata.get("original_name"),
            "snippet": doc.page_content[:300],
        }
        for doc in documents
    ]

    answer = None
    if payload.answer:
        context = format_context(documents)
        system_prompt = (
            "你是本地知识库问答助手。请只依据下面“知识库资料”回答问题，"
            "并在回答末尾以“参考来源”列出引用的文件名与页码；"
            "如果资料不足以回答问题，请直接说明，不要编造。\n\n"
            f"知识库资料：\n{context or '（无检索结果）'}"
        )
        response = chat_invoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=payload.query)]
        )
        answer = str(response.content)

    return RagQueryResponse(
        query=payload.query,
        answer=answer,
        sources=sources,
        model=settings.llm_model if payload.answer else None,
    )


# ==================== 批量导入 ====================

_MAX_BATCH_FILES = 20


@router.post("/rag/batch", tags=["rag"])
async def batch_upload_documents(
    request: Request,
    files: list[UploadFile] = File(...),
) -> dict:
    """批量上传文档（后台异步摄取）。需登录，文档归属当前用户。"""
    user_id = session_user_id(request)
    if user_id is None:
        raise HTTPException(status_code=401, detail="请先登录")

    if len(files) > _MAX_BATCH_FILES:
        raise HTTPException(status_code=400, detail=f"单次最多上传 {_MAX_BATCH_FILES} 个文件")

    job_id = str(uuid.uuid4())
    pool = request.app.state.pool

    # 读取所有文件内容到内存
    file_data: list[tuple[str, bytes]] = []
    for f in files:
        content = await f.read()
        if not content:
            continue
        if len(content) > _MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"文件 {f.filename} 超过 50MB 上限")
        file_data.append((f.filename or "unknown", content))

    if not file_data:
        raise HTTPException(status_code=400, detail="没有有效的上传文件")

    db.create_ingest_job(pool, job_id=job_id, user_id=user_id, total_files=len(file_data))

    # 后台异步摄取
    asyncio.create_task(run_batch_ingest(pool, job_id, user_id, file_data))

    return {"job_id": job_id, "total_files": len(file_data), "status": "running"}


@router.get("/rag/batch/{job_id}", tags=["rag"])
def get_batch_job(job_id: str, request: Request) -> dict:
    """查询批量导入任务状态。"""
    user_id = session_user_id(request)
    if user_id is None:
        raise HTTPException(status_code=401, detail="请先登录")
    job = db.get_ingest_job(request.app.state.pool, job_id, user_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job
