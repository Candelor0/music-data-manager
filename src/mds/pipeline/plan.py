"""生成写入计划 —— **只读**：不碰任何音乐文件，也不建快照。

它的产出是一份"打算改什么"的清单，存进 `items.plan_json`，供人预览与后续 apply 消费。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ..core.models import WritePlan
from ..core.writeplan import CHOICE_SKIPPED, build_write_plan
from ..logging_setup import get_logger
from ..storage.db import Database
from .common import candidates_of, decision_of, item_view

log = get_logger("plan")


@dataclass
class PlanStats:
    total: int = 0
    with_decision: int = 0
    allowed: int = 0
    not_allowed: int = 0
    touches_album: int = 0
    by_source: dict[str, int] = field(default_factory=dict)
    by_field: dict[str, int] = field(default_factory=dict)
    by_reason: dict[str, int] = field(default_factory=dict)


def build_plans(
    db: Database,
    run_id: str,
    *,
    choices: dict[int, str] | None = None,
    only: list[int] | None = None,
    assume_no_album_tag: bool = False,
    refresh: bool = False,
) -> PlanStats:
    """为 run 里所有条目生成写入计划。

    参数：
        choices           每条目的人工选择（item_id → kept_existing/adopted_ai/picked/skipped）
        only              只处理这些 item_id
        refresh           已有计划也重算（默认跳过已有计划的条目）
    """
    choices = choices or {}
    only_set = set(only) if only else None
    rows = [dict(r) for r in db.iter_items(run_id)]

    stats = PlanStats(total=len(rows))
    sources: Counter[str] = Counter()
    fields: Counter[str] = Counter()
    reasons: Counter[str] = Counter()

    for row in rows:
        item_id = int(row["id"])
        if only_set is not None and item_id not in only_set:
            continue
        if row.get("plan_json") and not refresh:
            # 已算过：仍然计入统计，避免重复计算
            plan = _load_plan(row)
            if plan is not None:
                _count(plan, stats, sources, fields, reasons)
                stats.with_decision += 1
            continue

        decision = decision_of(row)
        if decision is None:
            db.set_plan(item_id, {}, write_status="pending")
            continue

        # 人工选择：优先命令行传入，其次读上次存下的
        choice = choices.get(item_id) or row.get("user_choice") or None
        if choice:
            db.set_user_choice(item_id, choice)
            decision = _apply_choice(db, row, decision, choice)

        plan = build_write_plan(
            item_view(row, assume_no_album_tag=assume_no_album_tag),
            decision,
            choice if choice != CHOICE_SKIPPED else CHOICE_SKIPPED,
        )
        db.set_plan(
            item_id,
            plan.model_dump(mode="json"),
            write_status="planned" if plan.allowed else "skipped",
        )
        _count(plan, stats, sources, fields, reasons)
        stats.with_decision += 1

    stats.by_source = dict(sources.most_common())
    stats.by_field = dict(fields.most_common())
    stats.by_reason = dict(reasons.most_common())
    log.debug("计划生成完毕：%s", stats)
    return stats


def _apply_choice(
    db: Database, row: dict[str, Any], decision, choice: str
) -> Any:
    """把人工选择落到决策上。

    目前只处理一种：`picked:<候选序号>` —— 用于「需你选择出自哪张专辑」的条目，
    把选中的候选换进决策里。其它选择（adopted_ai / kept_existing / skipped）由
    `build_write_plan` 自己处理。
    """
    if not choice.startswith("picked:"):
        return decision
    try:
        index = int(choice.split(":", 1)[1])
    except (ValueError, IndexError):
        return decision
    candidates = candidates_of(db, row)
    if not (0 <= index < len(candidates)):
        return decision

    from ..core.decide import resolve_with_choice, verdict_of

    resolved = resolve_with_choice(decision, candidates[index])
    if resolved is not decision:
        # 把"用户已裁决"这件事落库：否则待办里会永远显示「需要你选」
        db.update_item(
            int(row["id"]),
            decision_json=resolved.model_dump(mode="json"),
            verdict=verdict_of(resolved),
        )
    return resolved


def _load_plan(row: dict[str, Any]) -> WritePlan | None:
    from .common import load_json

    data = load_json(row.get("plan_json"))
    if not data:
        return None
    try:
        return WritePlan(**data)
    except Exception:  # noqa: BLE001
        return None


def _count(
    plan: WritePlan,
    stats: PlanStats,
    sources: Counter[str],
    fields: Counter[str],
    reasons: Counter[str],
) -> None:
    if plan.allowed:
        stats.allowed += 1
        sources[plan.source or "?"] += 1
        if plan.touches_album:
            stats.touches_album += 1
        for change in plan.changes:
            fields[change.field] += 1
    else:
        stats.not_allowed += 1
        reasons[(plan.reason or "?")[:40]] += 1


# ────────────────────── 预览渲染 ──────────────────────

FIELD_LABELS = {"title": "曲名", "artist": "艺术家", "album": "专辑",
                "albumartist": "专辑艺术家", "date": "年份", "genre": "流派",
                "discnumber": "碟号"}
KIND_LABELS = {"fill": "补全", "cleanup": "清洗", "normalize": "规范化",
               "consensus": "同目录补全"}
SOURCE_LABELS = {
    "fill_missing": "补全缺失字段",
    "cleanup": "清洗/规范化",
    "ask_user_choice": "你选的候选",
    "adopted_ai": "采纳 AI 判断",
}


def render_plan(
    db: Database,
    run_id: str,
    *,
    only_allowed: bool = False,
    limit: int = 0,
    only: list[int] | None = None,
    only_pending: bool = False,
) -> list[str]:
    """把计划渲染成给人看的文本行（带 item id，便于用 --adopt-ai/--skip 指定）。

    only_pending=True 时只显示"还没写完"的条目（已 verified / skipped 的不重复展示）。
    """
    only_set = set(only) if only else None
    pending_states = {"planned", "failed", "snapshotted", "written", "pending"}
    rows = [dict(r) for r in db.iter_items(run_id)]
    lines: list[str] = []
    shown = 0
    for row in rows:
        if only_set is not None and int(row["id"]) not in only_set:
            continue
        if only_pending and (row.get("write_status") or "pending") not in pending_states:
            continue
        plan = _load_plan(row)
        if plan is None:
            continue
        if only_allowed and not plan.allowed:
            continue
        if limit and shown >= limit:
            break
        shown += 1
        tags = item_view(row).tags
        lines.append(f"── [#{row['id']}] {row['name']}")
        lines.append(
            f"   现在：{tags.value_of('artist') or '—'} / {tags.value_of('title') or '—'} / "
            f"{tags.value_of('album') or '（无专辑）'}"
            + (f" · {tags.value_of('date')}" if tags.value_of("date") else "")
        )
        if plan.allowed:
            for change in plan.changes:
                kind = KIND_LABELS.get(change.kind, change.kind)
                label = FIELD_LABELS.get(change.field, change.field)
                lines.append(f"   {label}：[{kind}] {change.before or '（空）'} → {change.after}")
            lines.append(f"   来源：{SOURCE_LABELS.get(plan.source, plan.source or '?')}" + ("（含专辑改动）" if plan.touches_album else ""))
        else:
            lines.append(f"   ⏭ 不写入：{plan.reason}")
        lines.append("")
    return lines


def render_decisions(db: Database, run_id: str) -> list[str]:
    """列出**需要你决定**的条目（带 item id，供 --adopt-ai / --skip 使用）。"""
    from .common import decision_of

    lines: list[str] = []
    keep_existing: list[tuple[int, str, str, str]] = []
    ask_user: list[tuple[int, str, int, str]] = []
    for row in db.iter_items(run_id):
        row = dict(row)
        decision = decision_of(row)
        if decision is None:
            continue
        if decision.action == "keep_existing" and decision.alternative is not None:
            keep_existing.append(
                (int(row["id"]), row["name"],
                 item_view(row).tags.value_of("album"), decision.alternative.album)
            )
        elif decision.action == "ask_user":
            ask_user.append(
                (int(row["id"]), row["name"], len(decision.show_candidates),
                 item_view(row).tags.value_of("album"))
            )

    if keep_existing:
        lines.append(f"## A. AI 有不同判断，目前保留你的（{len(keep_existing)} 条）")
        lines.append("   想采纳 AI 的，把编号填到：mds plan <run_id> --adopt-ai <编号...>")
        lines.append("")
        for item_id, name, mine, theirs in keep_existing:
            lines.append(f"   [#{item_id}] {name}")
            lines.append(f"        你的：{mine or '（空）'}")
            lines.append(f"        AI 的：{theirs}")
        lines.append("")

    if ask_user:
        lines.append(f"## B. 需你先选出自哪张专辑（{len(ask_user)} 条）")
        lines.append("   选择后填到：mds plan <run_id> --pick <编号> <候选序号>")
        lines.append("")
        for item_id, name, n_cand, mine in ask_user:
            lines.append(f"   [#{item_id}] {name}   当前专辑：{mine or '（空）'}   候选 {n_cand} 个")
        lines.append("")

    if not keep_existing and not ask_user:
        lines.append("（没有需要你决定的条目）")
    return lines
