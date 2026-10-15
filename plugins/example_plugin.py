"""示例插件：提供一个随机数工具。"""

import random

from langchain_core.tools import tool


@tool
def random_number(min_val: int = 1, max_val: int = 100) -> str:
    """生成指定范围内的随机整数。"""
    return str(random.randint(min_val, max_val))
