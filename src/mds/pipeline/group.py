"""目录分组与共识落库。**不联网、不碰音乐文件。**

把"逐个文件判断"升级为"按专辑目录成组判断"：
    1. 按父目录分组（规整的真实库：父目录就是专辑目录）
    2. 算出组内各专辑级字段的共识（年份/流派/专辑艺术家/碟号/专辑）
    3. 用共识重算决策 —— 空字段直接补，已有值一律不动
    4. 落库：groups 表 + items.group_id

本模块**只读写数据库**，不产生任何网络请求，也不修改任何音乐文件。
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..core.consensus import ALBUM_LEVEL_FIELDS, fill_consensus
from ..core.decide import decide, verdict_of
from ..core.grouping import group_by_folder
from ..core.models import FolderGroup, ItemView
from ..logging_setup import get_logger
from ..storage.db import Database
from .common import candidates_of, item_view, suggestion_of

log = get_logger("group")


@dataclass
class GroupStats:
    total: int = 0
    n_groups: int = 0
    n_multi: int = 0            # 文件数 ≥ 2 的组
    n_single: int = 0           # 单文件组（"一首歌一个文件夹"）
    files_in_multi: int = 0     # 有多少文件落在多文件组里
    with_consensus: int = 0     # 至少有一个字段达成共识的组数
    consensus_fields: dict[str, int] = field(default_factory=dict)  # 字段 → 有多少组有共识
    consensus_changes: int = 0  # 本次由共识补出的字段改动数
    conflicts: int = 0          # 已有值与共识冲突的字段数（只提示，不改）
    decisions_updated: int = 0


def groups_from_rows(
    rows: list[dict], *, min_files: int = 2, min_ratio: float = 0.6
) -> dict[str, FolderGroup]:
    """从数据库行直接算出「父目录 → 组（含共识）」，**不落库、不联网**。

    analyze 用它给每个文件带上"同目录共识"，从而在决策阶段就能补空字段。
    """
    views = [item_view(r) for r in rows]
    out: dict[str, FolderGroup] = {}
    for group in group_by_folder(views):
        members = [v for v in views if _belongs(v, group)]
        out[group.folder_path] = fill_consensus(
            group, members, min_files=min_files, min_ratio=min_ratio
        )
    return out


def build_groups(
    db: Database,
    run_id: str,
    *,
    min_files: int = 2,
    min_ratio: float = 0.6,
    recompute_decisions: bool = True,
    refresh_plans: bool = True,
) -> GroupStats:
    """生成分组与共识，并用共识重算决策（再从决策重建写入计划）。"""
    rows = [dict(r) for r in db.iter_items(run_id)]
    stats = GroupStats(total=len(rows))
    if not rows:
        return stats

    views: dict[int, ItemView] = {int(r["id"]): item_view(r) for r in rows}
    by_path: dict[str, list[int]] = {}
    for item_id, view in views.items():
        by_path.setdefault(str(view.path), []).append(item_id)

    groups = group_by_folder(list(views.values()))

    db.clear_groups(run_id)
    field_counter: Counter[str] = Counter()
    groups_by_folder: dict[str, FolderGroup] = {}

    for group in groups:
        members = [v for v in views.values() if _belongs(v, group)]
        filled = fill_consensus(group, members, min_files=min_files, min_ratio=min_ratio)
        item_ids = [i for i, v in views.items() if _belongs(v, group)]
        db.insert_group(
            run_id,
            folder_path=filled.folder_path,
            n_files=filled.n_files,
            folder_hint=filled.folder_hint,
            artist_hint=filled.artist_hint,
            consensus={k: v.model_dump() for k, v in filled.consensus.items()},
            item_ids=item_ids,
        )
        groups_by_folder[filled.folder_path] = filled

        stats.n_groups += 1
        if filled.n_files >= min_files:
            stats.n_multi += 1
            stats.files_in_multi += filled.n_files
        else:
            stats.n_single += 1
        if filled.consensus:
            stats.with_consensus += 1
            for name in filled.consensus:
                if name in ALBUM_LEVEL_FIELDS:
                    field_counter[name] += 1

    stats.consensus_fields = dict(field_counter.most_common())

    if recompute_decisions:
        stats.decisions_updated, cons_changes, conflicts = _recompute(
            db, rows, views, groups_by_folder
        )
        stats.consensus_changes = cons_changes
        stats.conflicts = conflicts

    if refresh_plans:
        from .plan import build_plans

        build_plans(db, run_id, refresh=True)

    log.debug("分组完成：%s", stats)
    return stats


def _belongs(view: ItemView, group: FolderGroup) -> bool:
    return str(Path(view.path).parent) == group.folder_path


def _recompute(
    db: Database,
    rows: list[dict],
    views: dict[int, ItemView],
    groups_by_folder: dict[str, FolderGroup],
) -> tuple[int, int, int]:
    """用共识重算每个条目的决策（不调模型：复用已缓存的候选与建议）。"""
    updated = cons_changes = conflict_count = 0
    for row in rows:
        item_id = int(row["id"])
        view = views[item_id]
        group = groups_by_folder.get(str(Path(view.path).parent))
        decision = decide(
            view,
            candidates_of(db, row),
            suggestion_of(row),
            group=group,
        )
        db.update_item(
            item_id,
            decision_json=decision.model_dump(mode="json"),
            verdict=verdict_of(decision),
        )
        updated += 1
        cons_changes += sum(1 for c in decision.changes if c.kind == "consensus")
        conflict_count += len(decision.conflicts)
    return updated, cons_changes, conflict_count


def render_groups(db: Database, run_id: str, *, limit: int = 0) -> list[str]:
    """把分组与共识渲染成给人看的文本。"""
    rows = list(db.list_groups(run_id))
    items_by_group: dict[int, list[dict]] = {}
    for raw in db.iter_items(run_id):
        row = dict(raw)
        if row.get("group_id"):
            items_by_group.setdefault(int(row["group_id"]), []).append(row)

    label_of = {
        "album": "专辑",
        "albumartist": "专辑艺术家",
        "date": "年份",
        "genre": "流派",
        "discnumber": "碟号",
    }

    lines: list[str] = []
    shown = 0
    for group in rows:
        if limit and shown >= limit:
            break
        shown += 1
        consensus = _load_json(group.get("consensus_json"))
        members = items_by_group.get(int(group["id"]), [])

        lines.append(f"── {group['folder_path']}")
        lines.append(
            f"   {group['n_files']} 个文件"
            + ("（单文件目录，不做互证）" if group["n_files"] < 2 else "")
        )
        if group.get("folder_hint"):
            lines.append(f"   目录名线索：{group['folder_hint']}")
        if consensus:
            parts = [
                f"{label_of.get(name, name)}={info['value']}"
                f"（{info['votes']}/{group['n_files']} 首一致）"
                for name, info in consensus.items()
            ]
            lines.append("   同目录共识：" + "；".join(parts))

            fillable = 0
            for member in members:
                tags = _load_json(member.get("tags_json"))
                fillable += sum(1 for name in consensus if not (tags.get(name) or "").strip())
            if fillable:
                lines.append(f"   → 可据此补全 {fillable} 处空字段")
        lines.append("")
    return lines


def _load_json(raw: object) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
