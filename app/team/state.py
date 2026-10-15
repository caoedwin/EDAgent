"""Team OS 状态定义。"""

from typing import Any, TypedDict


class TeamState(TypedDict, total=False):
    team_id: str
    goal: str
    conversation_id: str | None
    user_id: int
    # PM 拆解出的任务列表
    tasks: list[dict[str, Any]]
    # 当前正在执行的任务
    current_task_index: int
    # 各角色已产出的内容（role -> artifact dict）
    artifacts: dict[str, dict[str, Any]]
    # 人工确认记录（task_id -> "approved" | "rejected"）
    confirmations: dict[str, str]
    # 最终总结
    final_summary: str
