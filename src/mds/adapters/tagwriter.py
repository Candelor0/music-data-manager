"""标签写入（经无界面 Picard）。

**只做一件事**：把一个已经存在的文件（通常是原文件的副本）的标签，按目标值改掉，
然后**等写入真正结束**。

不做的事：不复制、不替换、不删文件、不移动文件 —— 那些由上层 pipeline 负责，
以便"原文件在 os.replace 之前一个字节都没被动过"。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..core.models import Tags
from ..core.writeplan import ALLOWED_FIELDS
from ..logging_setup import get_logger
from . import tagreader
from .picard_headless import HeadlessPicard, PicardUnavailable

log = get_logger("tagwriter")


@dataclass
class WriteOutcome:
    ok: bool
    error: str = ""
    readback: Tags | None = None


def write_tags(ctx: HeadlessPicard, path: str | Path, target: Tags) -> WriteOutcome:
    """把 `target` 里的白名单字段写进文件。

    安全约束：
    - **只写非空值**：目标值为空时跳过该字段（绝不因 AI 没给值而清空用户数据）
    - 非白名单字段（albumartist / tracknumber / composer 等）一律不动
    - 写入后**用 mutagen 读回校验**（走另一条代码路径，比自证更可信）
    """
    try:
        f = ctx.open(path)
    except PicardUnavailable as exc:
        return WriteOutcome(ok=False, error=str(exc))

    try:
        for field in ALLOWED_FIELDS:
            value = target.value_of(field)
            if not value:
                continue
            f.metadata[field] = value
        ctx.save(f)
    except Exception as exc:  # noqa: BLE001
        return WriteOutcome(ok=False, error=f"写入失败：{type(exc).__name__}: {exc}"[:200])

    # 读回校验（S14）
    try:
        readback = tagreader.read_tags(path).tags
    except Exception as exc:  # noqa: BLE001
        return WriteOutcome(ok=False, error=f"写后读回失败：{type(exc).__name__}")

    mismatch = [
        f"{field}={target.value_of(field)!r}≠{readback.value_of(field)!r}"
        for field in ALLOWED_FIELDS
        if target.value_of(field) and target.value_of(field) != readback.value_of(field)
    ]
    if mismatch:
        return WriteOutcome(ok=False, error="写后校验不一致：" + "; ".join(mismatch[:4]), readback=readback)

    return WriteOutcome(ok=True, readback=readback)


def read_raw_metadata(ctx: HeadlessPicard, path: str | Path) -> dict[str, list[str]]:
    """取 Picard 视角下的**全部原始标签**，供快照保存。

    用 Picard 而不是 mutagen：只有它能给出与写入路径完全一致的原始键值，
    回滚时才可能做到"和原来一样"。
    """
    try:
        f = ctx.open(path)
    except PicardUnavailable:
        return {}
    out: dict[str, list[str]] = {}
    try:
        for key, values in f.metadata.rawitems():
            out[key] = [str(v) for v in values]
    except Exception as exc:  # noqa: BLE001
        log.warning("读取原始标签失败：%s", type(exc).__name__)
    return out
