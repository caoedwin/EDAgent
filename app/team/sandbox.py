"""Docker 沙箱：为开发 Agent 提供隔离的代码执行环境。

工作流：
1. 创建临时容器（python:3.12-slim）
2. 将开发产物写入容器临时目录
3. 在容器内执行测试/安装依赖
4. 收集输出
5. 销毁容器
"""

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# 沙箱容器名前缀
_SANDBOX_PREFIX = "edagent-sandbox-"


async def _run_docker(*args: str, timeout: int | None = None) -> tuple[int, str, str]:
    """执行 docker CLI 命令，返回 (returncode, stdout, stderr)。"""
    cmd = ["docker", *args]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout or settings.team_sandbox_timeout
        )
        return proc.returncode or 0, stdout_b.decode(), stderr_b.decode()
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "", "timeout"


async def create_sandbox(container_name: str) -> str:
    """创建一个临时沙箱容器（不启动，仅 create），返回容器名。"""
    image = settings.team_sandbox_image
    # docker create 不会自动拉镜像：本地缺失时先拉取，避免直接失败
    rc, _, _ = await _run_docker("image", "inspect", image, timeout=30)
    if rc != 0:
        await _run_docker("pull", image, timeout=300)
    rc, out, err = await _run_docker(
        "create", "--name", container_name,
        "--memory=256m", "--cpus=0.5",
        "--network=none",  # 沙箱无网络（除非需要 pip install）
        "-w", "/workspace",
        image, "sleep", "infinity",
    )
    if rc != 0:
        logger.error("create sandbox failed: %s", err)
        raise RuntimeError(f"沙箱创建失败: {err}")
    return container_name


async def start_sandbox(container_name: str) -> None:
    """启动沙箱容器。"""
    rc, _, err = await _run_docker("start", container_name)
    if rc != 0:
        raise RuntimeError(f"沙箱启动失败: {err}")


async def write_files(container_name: str, files: dict[str, str]) -> None:
    """将文件写入沙箱容器（通过 docker cp）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        for file_path, content in files.items():
            full_path = Path(tmpdir) / file_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")
        # cp 整个目录到容器
        rc, _, err = await _run_docker("cp", f"{tmpdir}/.", f"{container_name}:/workspace/")
        if rc != 0:
            raise RuntimeError(f"写入文件失败: {err}")


async def exec_in_sandbox(container_name: str, command: list[str]) -> dict[str, Any]:
    """在沙箱容器内执行命令，返回 {returncode, stdout, stderr}。"""
    rc, out, err = await _run_docker("exec", container_name, *command)
    return {"returncode": rc, "stdout": out, "stderr": err}


async def read_files(container_name: str, paths: list[str]) -> dict[str, str]:
    """从沙箱容器读取文件内容。"""
    result: dict[str, str] = {}
    for path in paths:
        rc, out, _ = await _run_docker("exec", container_name, "cat", path)
        if rc == 0:
            result[path] = out
    return result


async def destroy_sandbox(container_name: str) -> None:
    """销毁沙箱容器（强制删除，忽略错误）。"""
    await _run_docker("rm", "-f", container_name, timeout=30)


async def run_dev_sandbox(
    *,
    container_name: str,
    files: dict[str, str],
    test_command: list[str] | None = None,
) -> dict[str, Any]:
    """完整的沙箱生命周期：创建→写入→执行→读取→销毁。

    Args:
        container_name: 容器名
        files: 文件路径 -> 内容
        test_command: 在容器内执行的测试命令

    Returns:
        {success, files, test_output, error}
    """
    try:
        await create_sandbox(container_name)
        await start_sandbox(container_name)
        # /workspace 由容器运行时以 root 身份创建；放开写权限，
        # 使镜像默认的非 root 用户也能写入编译产物（__pycache__ 等）
        await _run_docker(
            "exec", "-u", "root", container_name,
            "sh", "-c", "mkdir -p /workspace && chmod 777 /workspace",
            timeout=30,
        )
        await write_files(container_name, files)

        test_output = None
        if test_command:
            test_output = await exec_in_sandbox(container_name, test_command)

        # 读取所有文件（可能被测试修改）
        result_files = await read_files(
            container_name,
            [f"/workspace/{p}" for p in files],
        )

        return {
            "success": True,
            "files": result_files,
            "test_output": test_output,
        }
    except Exception as exc:
        logger.exception("sandbox error")
        return {"success": False, "error": str(exc)}
    finally:
        await destroy_sandbox(container_name)
