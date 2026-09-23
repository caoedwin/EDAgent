"""Agent 内置工具集。

刻意不提供 code_interpreter / 任意 Shell 执行（避免对宿主机 Docker 的强依赖，
所有工具均为纯 Python、无外部副作用的安全工具）。
"""

import ast
import datetime as dt
import operator
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.rag.retriever import format_context, retrieve


def _current_user_id(config: RunnableConfig) -> str | None:
    """从图运行配置中提取当前用户标识（由 API 层注入 configurable.user_id）。"""
    return ((config or {}).get("configurable") or {}).get("user_id")

# 安全计算器允许的 AST 节点与二元/一元运算符
_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_ALLOWED_UNARYOPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _eval_node(node: ast.AST) -> Any:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        return _ALLOWED_BINOPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARYOPS:
        return _ALLOWED_UNARYOPS[type(node.op)](_eval_node(node.operand))
    raise ValueError("仅支持数字与 + - * / // % ** 运算")


@tool
def calculator(expression: str) -> str:
    """对数学表达式进行精确求值，例如 '2 * (3 + 4.5)'。仅支持数字和 + - * / // % ** 运算符。"""
    try:
        tree = ast.parse(expression.strip(), mode="eval")
        result = _eval_node(tree)
        return str(result)
    except Exception as exc:
        return f"计算失败: {exc}"


@tool
def current_time() -> str:
    """获取当前日期与时间（ISO 8601 格式）。"""
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


@tool
def word_count(text: str) -> str:
    """统计给定文本的字符数（含空白）与非空白字符数。"""
    return f"总字符数: {len(text)}，非空白字符数: {len(''.join(text.split()))}"


@tool
def knowledge_search(query: str, config: RunnableConfig) -> str:
    """在本地私有知识库中检索资料（基于语义检索，返回带来源标注的资料片段）。
    当用户问题需要公司/项目/私有文档中的信息时使用。"""
    documents = retrieve(query, _current_user_id(config))
    if not documents:
        return "知识库中没有检索到相关资料。"
    return format_context(documents)


# LangGraph 工具节点按名称索引
TOOLS = [calculator, current_time, word_count, knowledge_search]
TOOL_MAP = {tool_item.name: tool_item for tool_item in TOOLS}
