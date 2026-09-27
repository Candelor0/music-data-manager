"""读标签（mutagen）。只读，不写。

为什么读用 mutagen 而不是 Picard：读这一步不需要 Qt 事件循环，
让 core/adapters 的读路径保持简单可测。写入才必须用 Picard。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import mutagen

from .. import config
from ..core.models import Tags

# easy 模式的统一键 → 我们的字段
#
# ⚠️ 实测踩过：漏了 discnumber/tracknumber，导致这两个字段**永远读回来是空的**。
# 后果很严重："不覆盖已有碟号"的安全判断会永远认为碟号为空。
_EASY_KEYS = {
    "title": "title",
    "artist": "artist",
    "album": "album",
    "albumartist": "albumartist",
    "date": "date",
    "genre": "genre",
    "composer": "composer",
    "tracknumber": "tracknumber",
    "discnumber": "discnumber",
}

# 原始键别名（各容器命名不同），用于 easy 模式取不到时的兜底
_RAW_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("TIT2", "\xa9nam", "INAM", "Title"),
    "artist": ("TPE1", "\xa9ART", "IART", "Artist"),
    "album": ("TALB", "\xa9alb", "IPRD", "Album"),
    "albumartist": ("TPE2", "aART", "WM/AlbumArtist"),
    "date": ("TDRC", "TYER", "\xa9day", "ICRD", "Date"),
    "genre": ("TCON", "\xa9gen", "IGNR", "Genre"),
    "composer": ("TCOM", "\xa9wrt", "IMUS", "Composer"),
    "tracknumber": ("tracknumber", "TRACKNUMBER", "TRCK", "trkn", "track"),
    "discnumber": ("discnumber", "DISCNUMBER", "TPOS", "disk", "disc"),
}


def is_supported(path: str | Path) -> bool:
    return Path(path).suffix.lower() in config.SUPPORTED_EXTS


def iter_audio_files(root: str | Path, *, limit: int = 0) -> list[Path]:
    """列出音乐库里的受支持文件（排序后返回，保证可复现）。"""
    base = Path(root)
    if not base.is_dir():
        return []
    found: list[Path] = []
    for path in sorted(base.rglob("*")):
        if path.is_file() and path.suffix.lower() in config.SUPPORTED_EXTS:
            found.append(path)
            if limit and len(found) >= limit:
                break
    return found


def read_file_stat(path: str | Path) -> tuple[int, float]:
    stat = os.stat(path)
    return int(stat.st_size), float(stat.st_mtime)


def _first(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value).strip()


def _read_fields(path: str | Path) -> dict[str, str]:
    """两遍策略：easy 模式 → 原始键别名。"""
    fields: dict[str, str] = {}
    mf = None
    try:
        mf = mutagen.File(str(path), easy=True)
    except Exception:  # noqa: BLE001
        mf = None
    if mf is None:
        try:
            mf = mutagen.File(str(path))
        except Exception:  # noqa: BLE001
            return fields
    if mf is None:
        return fields

    for key, field_name in _EASY_KEYS.items():
        try:
            value = mf.get(key)
        except Exception:  # noqa: BLE001
            value = None
        text = _first(value)
        if text:
            fields[field_name] = text

    missing = [f for f in _RAW_ALIASES if not fields.get(f)]
    if missing:
        for field_name in missing:
            for alias in _RAW_ALIASES[field_name]:
                try:
                    value = mf.get(alias)
                except Exception:  # noqa: BLE001
                    value = None
                text = _first(value)
                if text:
                    fields[field_name] = text
                    break
    return fields


@dataclass
class ReadResult:
    path: str
    name: str
    size: int = 0
    mtime: float = 0.0
    tags: Tags = field(default_factory=Tags)
    error: str = ""


def read_tags(path: str | Path) -> ReadResult:
    p = Path(path)
    result = ReadResult(path=str(p), name=p.name)
    try:
        result.size, result.mtime = read_file_stat(p)
    except OSError as exc:
        result.error = f"无法读取文件属性: {exc}"
        return result
    try:
        fields = _read_fields(p)
    except Exception as exc:  # noqa: BLE001
        result.error = f"读取标签失败: {type(exc).__name__}"
        return result
    result.tags = Tags(**{k: v for k, v in fields.items() if k in Tags.model_fields})
    return result


def read_duration_sec(path: str | Path) -> int | None:
    """从文件本身读时长（不需要指纹）。返回整数秒，失败返回 None。"""
    try:
        mf = mutagen.File(str(path))
        if mf is not None and getattr(mf, "info", None) is not None:
            return int(float(mf.info.length))
    except Exception:  # noqa: BLE001
        return None
    return None
