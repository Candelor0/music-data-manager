"""差异渲染。纯函数，供报告使用。"""

from __future__ import annotations

from .models import TagChange

FIELD_LABELS = {
    "title": "曲名",
    "artist": "艺术家",
    "album": "专辑",
    "date": "年份",
    "genre": "流派",
}

KIND_LABELS = {"fill": "补全", "cleanup": "清洗", "normalize": "规范化"}


def field_label(field: str) -> str:
    return FIELD_LABELS.get(field, field)


def has_meaningful_change(changes: list[TagChange]) -> bool:
    return any(c.before != c.after and c.after for c in changes)


def render_change_line(change: TagChange) -> str:
    kind = KIND_LABELS.get(change.kind, change.kind)
    before = change.before or "（空）"
    return f"{field_label(change.field)}：[{kind}] {before} → {change.after}"


def render_changes(changes: list[TagChange]) -> list[str]:
    return [render_change_line(c) for c in changes if c.before != c.after]
