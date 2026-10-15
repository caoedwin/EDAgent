"""Team OS 节点：PM 拆解任务 + 角色执行。"""

import json
import logging
import re
import uuid
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app import db
from app.config import settings
from app.llm.client import chat_ainvoke
from app.observability.tracing import get_tracer
from app.team.patch import parse_code_blocks, generate_diff
from app.team.roles import sync_roles_to_db
from app.team.sandbox import run_dev_sandbox

logger = logging.getLogger(__name__)


async def _verify_in_sandbox(pool, task: dict[str, Any], code_blocks: list[dict[str, str]]) -> None:
    """把开发产物写入一次性 Docker 沙箱做编译验证，结果存为独立产物。

    沙箱不可用（无 docker CLI / 无 socket 权限）时降级为告警，不阻塞 PR 生成。
    """
    report_id = str(uuid.uuid4())
    try:
        result = await run_dev_sandbox(
            container_name=f"edagent-sandbox-{task['id'][:8]}",
            files={f["path"]: f["content"] for f in code_blocks},
            test_command=["python", "-m", "compileall", "-q", "/workspace"],
        )
    except Exception as exc:  # 沙箱环境缺失时不应中断团队流程
        logger.exception("沙箱执行异常")
        result = {"success": False, "error": str(exc)}

    if result.get("success"):
        out = result.get("test_output") or {}
        rc = out.get("returncode", 1)
        body = (
            f"# 沙箱编译验证：{'通过' if rc == 0 else '失败'}\n\n"
            f"- 沙箱镜像：`{settings.team_sandbox_image}`（--network=none / 256MB / 0.5 CPU）\n"
            f"- 验证命令：`python -m compileall -q /workspace`\n"
            f"- 退出码：{rc}\n\n"
            f"## stdout\n```\n{(out.get('stdout') or '').strip()[:2000]}\n```\n\n"
            f"## stderr\n```\n{(out.get('stderr') or '').strip()[:2000]}\n```\n"
        )
    else:
        body = f"# 沙箱编译验证：未执行\n\n```\n{result.get('error')}\n```\n"

    db.create_artifact(
        pool,
        artifact_id=report_id,
        task_id=task["id"],
        type="sandbox_report",
        title=f"{task['title']} - 沙箱验证",
        content=body,
    )


def _parse_wbs(text: str) -> dict[str, Any]:
    """从 PM 输出中解析 WBS JSON。"""
    try:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            return json.loads(match.group(0))
    except Exception:
        pass
    return {"goal": "", "tasks": []}


async def pm_node(
    state: dict[str, Any],
    pool,
    allowed_roles: set[str] | None = None,
) -> dict[str, Any]:
    """PM 节点：理解目标 → 拆解任务 → 入库。

    allowed_roles 非空时（来自团队模板），只保留其内的任务角色。
    """
    with get_tracer().start_as_current_span("team_node_pm") as span:
        span.set_attribute("team.node", "pm")

        # 确保角色已入库
        roles = sync_roles_to_db(pool)
        role_names = [r["name"] for r in roles]

        pm_role = next((r for r in roles if r["name"] == "pm"), None)
        system_prompt = pm_role["system_prompt"] if pm_role else "你是项目经理，请拆解任务。"

        response = await chat_ainvoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=state["goal"])],
            temperature=0.2,
        )
        wbs = _parse_wbs(str(response.content))
        span.set_attribute("team.task_count", len(wbs.get("tasks", [])))

        # 入库任务
        team_id = state["team_id"]
        for task in wbs.get("tasks", []):
            if task.get("role") not in role_names:
                continue
            # 团队模板角色过滤
            if allowed_roles is not None and task.get("role") not in allowed_roles:
                continue
            db.create_team_task(
                pool,
                task_id=str(uuid.uuid4()),
                team_id=team_id,
                conversation_id=state.get("conversation_id"),
                user_id=state["user_id"],
                role=task["role"],
                title=task.get("title", ""),
                description=task.get("description", ""),
                depends_on=task.get("depends_on", []),
            )

        # 重新从 DB 读取带 id 的任务列表
        tasks = db.list_team_tasks(pool, team_id)
        return {"tasks": tasks, "current_task_index": 0}


async def role_executor_node(state: dict[str, Any], pool) -> dict[str, Any]:
    """角色执行节点：根据 current_task_index 执行对应任务。"""
    tasks = state.get("tasks", [])
    idx = state.get("current_task_index", 0)

    if idx >= len(tasks):
        return {"final_summary": _build_summary(state)}

    task = tasks[idx]
    with get_tracer().start_as_current_span(f"team_node_{task['role']}") as span:
        span.set_attribute("team.node", task["role"])
        span.set_attribute("team.task_title", task["title"])

        role = db.get_role(pool, task["role"])
        if role is None:
            db.update_task_status(pool, task["id"], "failed", error=f"未知角色: {task['role']}")
            return {"current_task_index": idx + 1}

        # 检查人工确认
        if role["requires_human_confirm"]:
            confirmation = state.get("confirmations", {}).get(task["id"])
            if confirmation != "approved":
                db.update_task_status(pool, task["id"], "blocked")
                return {"current_task_index": idx}  # 停在这等确认

        # 构建上下文：汇总已完成任务的产物
        context_parts = [f"任务：{task['title']}\n要求：{task.get('description', '')}"]
        artifacts = state.get("artifacts", {})
        for role_name, artifact in artifacts.items():
            context_parts.append(f"\n--- 上游 {role_name} 的产物 ---\n{artifact['content'][:3000]}")

        context = "\n".join(context_parts)

        db.update_task_status(pool, task["id"], "running")
        try:
            # reviewer 角色：把上游 developer 的 PR diff 加入上下文
            if task["role"] == "reviewer":
                prs = db.list_prs(pool, task["team_id"])
                if prs:
                    context += "\n\n--- 待审查的代码变更（PR diff）---\n"
                    for pr in prs:
                        if pr["status"] == "pending":
                            context += f"\n## PR: {pr['title']}\n```diff\n{pr['diff_content'][:4000]}\n```\n"

            response = await chat_ainvoke(
                [SystemMessage(content=role["system_prompt"]), HumanMessage(content=context)],
                temperature=0.3,
            )
            content = str(response.content)

            # 保存产物
            artifact_id = str(uuid.uuid4())
            artifact_type = role["artifact_types"][0] if role["artifact_types"] else "text"
            db.create_artifact(
                pool,
                artifact_id=artifact_id,
                task_id=task["id"],
                type=artifact_type,
                title=task["title"],
                content=content,
            )
            db.update_task_status(pool, task["id"], "done", artifact_id=artifact_id)

            # developer 角色：解析代码块 → 沙箱验证 → 生成 diff → 创建 PR
            if task["role"] == "developer":
                code_blocks = parse_code_blocks(content)
                if code_blocks:
                    if settings.team_sandbox_enabled:
                        await _verify_in_sandbox(pool, task, code_blocks)
                    diff_text = generate_diff(code_blocks)
                    if diff_text.strip():
                        pr_id = str(uuid.uuid4())
                        files_list = [f["path"] for f in code_blocks]
                        db.create_pr(
                            pool,
                            pr_id=pr_id,
                            team_id=task["team_id"],
                            task_id=task["id"],
                            title=task["title"],
                            diff_content=diff_text,
                            files_changed=files_list,
                        )

            # reviewer 角色：解析评审结论 → 更新 PR 状态
            if task["role"] == "reviewer":
                prs = db.list_prs(pool, task["team_id"])
                verdict = _parse_review_verdict(content)
                for pr in prs:
                    if pr["status"] == "pending":
                        if verdict.get("verdict") == "pass":
                            db.update_pr_status(pool, pr["id"], "approved",
                                                reviewer_notes=verdict.get("summary", ""))
                        else:
                            db.update_pr_status(pool, pr["id"], "needs_changes",
                                                reviewer_notes=verdict.get("summary", ""))

            # 更新状态
            artifacts = dict(state.get("artifacts", {}))
            artifacts[task["role"]] = {
                "id": artifact_id,
                "type": artifact_type,
                "content": content,
            }
            return {"artifacts": artifacts, "current_task_index": idx + 1}

        except Exception as exc:
            db.update_task_status(pool, task["id"], "failed", error=str(exc))
            return {"current_task_index": idx + 1}


def _build_summary(state: dict[str, Any]) -> str:
    """汇总所有产物生成最终总结。"""
    artifacts = state.get("artifacts", {})
    if not artifacts:
        return "团队协作未产出任何结果。"
    lines = [f"# 团队协作完成\n", f"目标：{state.get('goal', '')}\n"]
    for role, artifact in artifacts.items():
        lines.append(f"\n## {role} 的产物\n")
        lines.append(artifact["content"][:500])
        lines.append("...")
    return "\n".join(lines)


def _parse_review_verdict(text: str) -> dict[str, Any]:
    """从 reviewer 输出中解析评审结论 JSON。"""
    try:
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            data = json.loads(match.group(0))
            return {
                "verdict": data.get("verdict", "needs_changes"),
                "score": data.get("score", 0),
                "summary": data.get("summary", ""),
                "issues": data.get("issues", []),
            }
    except Exception:
        pass
    return {"verdict": "needs_changes", "summary": "无法解析评审结论"}
