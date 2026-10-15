"""补丁生成与应用：将开发 Agent 的代码输出解析为 unified diff。"""

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

# 匹配 ```python path:app/example.py ... ``` 风格的代码块
_CODE_BLOCK_RE = re.compile(
    r"```(?:\w+)?\s*(?:path:)?([^\n`]*)\n([\s\S]*?)```",
    re.MULTILINE,
)


def parse_code_blocks(text: str) -> list[dict[str, str]]:
    """从开发 Agent 的文本输出中解析代码块。

    返回 [{"path": "app/example.py", "content": "..."}]
    """
    blocks: list[dict[str, str]] = []
    for match in _CODE_BLOCK_RE.finditer(text):
        path = match.group(1).strip()
        content = match.group(2)
        if path and "/" in path:
            blocks.append({"path": path, "content": content.rstrip("\n")})
    return blocks


def generate_diff(
    files: list[dict[str, str]],
    *,
    base_dir: str | None = None,
) -> str:
    """将代码文件列表生成 unified diff。

    对每个文件，如果 base_dir 中存在同名文件则做 diff，
    否则视为新增文件（/dev/null → 新文件）。

    Args:
        files: [{"path": "app/example.py", "content": "..."}]
        base_dir: 项目根目录（用于对比已有文件）

    Returns:
        unified diff 文本
    """
    base_path = Path(base_dir) if base_dir else None
    diff_parts: list[str] = []

    for f in files:
        file_path = f["path"]
        new_content = f["content"]
        old_path = base_path / file_path if base_path else None
        old_content = old_path.read_text(encoding="utf-8") if old_path and old_path.exists() else None

        # 写入临时文件用于 diff
        with tempfile.NamedTemporaryFile(mode="w", suffix=f".{file_path.split('.')[-1]}",
                                         delete=False, encoding="utf-8") as new_tmp:
            new_tmp.write(new_content)
            new_tmp_path = new_tmp.name

        if old_content is not None:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".old",
                                             delete=False, encoding="utf-8") as old_tmp:
                old_tmp.write(old_content)
                old_tmp_path = old_tmp.name

            result = subprocess.run(
                ["diff", "-u", "--label", f"a/{file_path}", "--label", f"b/{file_path}",
                 old_tmp_path, new_tmp_path],
                capture_output=True, text=True,
            )
            diff_parts.append(result.stdout)
        else:
            # 新增文件
            line_count = new_content.count("\n") + 1
            diff_parts.append(
                f"--- /dev/null\n"
                f"+++ b/{file_path}\n"
                f"@@ -0,0 +1,{line_count} @@\n"
            )
            for line in new_content.splitlines():
                diff_parts.append(f"+{line}")
            diff_parts.append("")

    return "\n".join(diff_parts)


def apply_diff(diff_content: str, target_dir: str) -> dict[str, Any]:
    """将 unified diff 应用到目标目录。

    Args:
        diff_content: unified diff 文本
        target_dir: 目标目录

    Returns:
        {"success": bool, "applied_files": [...], "error": str | None}
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".diff",
                                     delete=False, encoding="utf-8") as f:
        f.write(diff_content)
        diff_path = f.name

    result = subprocess.run(
        ["patch", "-p1", "--dry-run", "-i", diff_path],
        capture_output=True, text=True,
        cwd=target_dir,
    )

    if result.returncode != 0:
        return {"success": False, "applied_files": [], "error": result.stderr}

    # 实际应用
    result = subprocess.run(
        ["patch", "-p1", "-i", diff_path],
        capture_output=True, text=True,
        cwd=target_dir,
    )

    if result.returncode != 0:
        return {"success": False, "applied_files": [], "error": result.stderr}

    applied = [line for line in result.stdout.splitlines() if "patching file" in line]
    applied_files = [l.replace("patching file ", "") for l in applied]

    return {"success": True, "applied_files": applied_files, "error": None}
