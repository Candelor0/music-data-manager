"""pipeline 共用小工具：把数据库的一行还原成决策/建议所需的领域对象。"""

from __future__ import annotations

import json
from typing import Any

from ..core.models import Candidate, Decision, ItemView, Suggestion, Tags


def load_json(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def item_view(row: dict[str, Any], *, assume_no_album_tag: bool = False) -> ItemView:
    raw_tags = load_json(row.get("tags_json"))
    tags = Tags(**{k: v for k, v in raw_tags.items() if k in Tags.model_fields})
    if assume_no_album_tag:
        # 只影响决策输入；不修改数据库里的真实标签，也不碰任何文件
        tags = tags.model_copy(update={"album": ""})
    return ItemView(
        path=str(row.get("path", "")),
        name=str(row.get("name", "")),
        duration_sec=int(row.get("duration_sec") or 0),
        tags=tags,
    )


def decision_of(row: dict[str, Any]) -> Decision | None:
    data = load_json(row.get("decision_json"))
    if not data:
        return None
    try:
        return Decision(**data)
    except Exception:  # noqa: BLE001 - 旧数据格式不合时当作没有决策
        return None


def suggestion_of(row: dict[str, Any]) -> Suggestion | None:
    data = load_json(row.get("suggestion_json"))
    if not data:
        return None
    try:
        return Suggestion(**data)
    except Exception:  # noqa: BLE001
        return None


def candidates_of(db: Any, row: dict[str, Any]) -> list[Candidate]:
    """候选来自 MusicBrainz 缓存（以 recording MBID 为键）。"""
    mbid = row.get("recording_mbid")
    if not mbid:
        return []
    payload = db.mb_get(mbid) or {}
    out: list[Candidate] = []
    for item in payload.get("candidates") or []:
        try:
            out.append(Candidate(**item))
        except Exception:  # noqa: BLE001
            continue
    return out
