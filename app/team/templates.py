"""Team OS 团队模板库：预定义角色组合与目标提示。

模板用于约束 PM 拆解出的 WBS：只保留模板 roles 内的任务角色，
从而适配不同协作场景（软件开发 / 内容创作 / 数据分析）。
"""

from typing import Any

TEMPLATES: list[dict[str, Any]] = [
    {
        "key": "software_dev",
        "name": "软件开发团队",
        "description": "从需求到代码评审的完整软件开发流程，覆盖产品、架构、开发、测试与评审。",
        "roles": ["pm", "product", "architect", "developer", "tester", "reviewer"],
        "goal_hint": "例如：开发一个支持增删改查的待办清单 REST API",
    },
    {
        "key": "content_creation",
        "name": "内容创作团队",
        "description": "面向文章 / 文案的内容创作流程，覆盖需求拆解、选题撰写与评审。",
        "roles": ["pm", "product", "developer", "reviewer"],
        "goal_hint": "例如：撰写一篇介绍本地化 AI Agent 平台的技术博客",
    },
    {
        "key": "data_analysis",
        "name": "数据分析团队",
        "description": "数据分析与洞察流程，覆盖指标定义、数据处理、分析验证与结论评审。",
        "roles": ["pm", "product", "developer", "tester", "reviewer"],
        "goal_hint": "例如：分析近 30 天用户活跃度变化趋势并给出结论",
    },
]


def get_template(key: str) -> dict[str, Any] | None:
    """按 key 查模板；不存在返回 None。"""
    return next((t for t in TEMPLATES if t["key"] == key), None)
