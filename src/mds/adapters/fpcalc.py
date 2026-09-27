"""声学指纹（Chromaprint 的 fpcalc 可执行文件）。

fpcalc 是随包分发的第三方二进制（官方提供 Windows/macOS/Linux 预编译版本，无需编译）。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .. import config
from ..logging_setup import get_logger

log = get_logger("fpcalc")


@dataclass
class Fingerprint:
    status: str  # ok | error
    fingerprint: str = ""
    duration_sec: int = 0
    error: str = ""


def version(binary: Path | None = None) -> str:
    exe = binary or config.fpcalc_path()
    try:
        proc = subprocess.run([str(exe), "-version"], capture_output=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"不可用: {type(exc).__name__}"
    out = (proc.stdout or proc.stderr).decode("utf-8", "replace").strip()
    return out.splitlines()[0] if out else f"退出码 {proc.returncode}"


def compute(
    path: str | Path,
    *,
    length: int = config.DEFAULT_FINGERPRINT_LENGTH,
    binary: Path | None = None,
    timeout: int = 120,
) -> Fingerprint:
    """计算指纹。返回 Fingerprint，不抛异常（失败信息放在 status/error 里）。"""
    exe = binary or config.fpcalc_path()
    if not Path(exe).is_file():
        return Fingerprint(status="error", error=f"找不到 fpcalc：{exe}")
    try:
        proc = subprocess.run(
            [str(exe), "-json", "-length", str(length), str(path)],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return Fingerprint(status="error", error="指纹计算超时")
    except OSError as exc:
        return Fingerprint(status="error", error=f"fpcalc 启动失败: {type(exc).__name__}")

    if proc.returncode != 0:
        return Fingerprint(status="error", error=f"fpcalc 退出码 {proc.returncode}")
    try:
        data = json.loads(proc.stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return Fingerprint(status="error", error="fpcalc 输出无法解析")

    fp = data.get("fingerprint") or ""
    if not fp:
        return Fingerprint(status="error", error="fpcalc 未产出指纹")
    return Fingerprint(status="ok", fingerprint=fp, duration_sec=int(data.get("duration", 0)))
