"""业务路由：简单问答 / LangGraph Agent / RAG 文档管理与问答。"""

import uuid

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from langchain_core.messages import HumanMessage, SystemMessage

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
from app.rag.ingest import collection_stats, delete_document, ingest_document
from app.rag.retriever import format_context, retrieve
from app.security.web_auth import request_owner_id

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
    """上传 PDF / Markdown / TXT / HTML 文档并写入向量库。

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
