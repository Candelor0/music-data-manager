"""报告渲染：Markdown（给人看）与 CSV（给表格）。"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from ..core.diff import render_changes
from ..core.models import Candidate, Decision
from ..storage.db import Database

GROUP_ORDER = [
    "需你选择出自哪张专辑",
    "保留现有专辑（AI 有不同判断）",
    "建议清洗/规范化",
    "建议补全缺失字段",
    "无需改动",
    "无法处理（没有候选）",
]


def _load(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _rows(db: Database, run_id: str) -> list[dict[str, Any]]:
    out = []
    for row in db.iter_items(run_id):
        item = dict(row)
        item["_tags"] = _load(row["tags_json"])
        item["_decision"] = _load(row["decision_json"])
        item["_candidates"] = _load(row["candidates_json"]) or _load_mb_candidates(db, row)
        out.append(item)
    return out


def _load_mb_candidates(db: Database, row) -> list[dict[str, Any]]:
    mbid = row["recording_mbid"]
    if not mbid:
        return []
    payload = db.mb_get(mbid)
    return payload.get("candidates", []) if payload else []


def _fmt_candidate(index: int, cand: dict[str, Any], duration: int) -> str:
    delta = None
    length = cand.get("track_length_sec")
    if length and duration:
        delta = abs(int(length) - int(duration))
    delta_text = f"时长差 {delta}s" if delta is not None else "时长未知"
    rg = cand.get("rg_type") or "未知类型"
    if cand.get("rg_secondary"):
        rg = f"{rg}/{cand['rg_secondary']}"
    return (
        f"{index + 1}. 《{cand.get('album') or '—'}》 {cand.get('date') or '年份未知'} · "
        f"{delta_text} · {rg} · 曲目数 {cand.get('track_count') or 0}"
        + (f" · {cand.get('country')}" if cand.get("country") else "")
    )


def write_markdown(db: Database, run_id: str, out_path: str | Path,
                   *, disagreements_only: bool = False) -> dict[str, int]:
    run = db.get_run(run_id) or {}
    counts = db.item_counts(run_id)
    rows = _rows(db, run_id)
    stats = _load(run.get("stats_json") or "{}")

    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        verdict = row["verdict"] or "未处理"
        groups.setdefault(verdict, []).append(row)

    lines: list[str] = []
    lines.append(f"# 建议报告 · run `{run_id}`")
    lines.append("")
    lines.append(f"- 音乐库：`{run.get('music_root', '')}`")
    lines.append(f"- 文件总数：**{counts['total']}**｜完成决策：**{sum(len(v) for v in groups.values())}**")
    lines.append(
        f"- 指纹成功 {counts['fp_ok']}｜AcoustID 命中 {counts['ac_ok']}｜"
        f"有候选 {counts['cand_ok']}｜AI 成功 {counts['llm_ok']}"
    )
    cost_cny = float(stats.get("cost_usd", 0.0)) * 7.1
    lines.append(f"- 云端成本（估算）：**¥{cost_cny:.4f}**")
    limiters = stats.get("musicbrainz_limiter", {})
    lines.append(f"- MusicBrainz 限流等待：{limiters.get('total_wait', 0)} 秒")
    lines.append("")
    lines.append("> ⚠️ **本阶段全程只读：没有修改任何音乐文件。** 下面是「将来可以怎么改」的建议。")
    lines.append("")
    lines.append("## 汇总")
    lines.append("")
    lines.append("| 类别 | 数量 |")
    lines.append("| --- | --- |")
    for verdict in GROUP_ORDER:
        if verdict in groups:
            lines.append(f"| {verdict} | {len(groups[verdict])} |")
    for verdict, items in groups.items():
        if verdict not in GROUP_ORDER:
            lines.append(f"| {verdict} | {len(items)} |")
    lines.append("")

    if disagreements_only:
        show = {k: v for k, v in groups.items() if k in ("需你选择出自哪张专辑", "保留现有专辑（AI 有不同判断）",
                                                          "建议清洗/规范化", "建议补全缺失字段")}
    else:
        show = groups

    lines.append("## 明细")
    lines.append("")
    for verdict in GROUP_ORDER + [k for k in groups if k not in GROUP_ORDER]:
        items = show.get(verdict)
        if not items:
            continue
        lines.append(f"### {verdict}（{len(items)} 条）")
        lines.append("")
        for row in items:
            tags = row["_tags"]
            decision = row["_decision"]
            lines.append(f"#### `{row['name']}`")
            lines.append("")
            lines.append(
                f"- 现有标签：{tags.get('artist') or '—'} / {tags.get('title') or '—'} / "
                f"{tags.get('album') or '（无专辑）'}"
                + (f" · {tags.get('date')}" if tags.get("date") else "")
            )
            lines.append(f"- 时长：{row['duration_sec'] or '未知'} 秒")

            changes = decision.get("changes") or []
            if changes:
                lines.append("- 建议改动：")
                for change_line in render_changes(_to_changes(changes)):
                    lines.append(f"  - {change_line}")
            reason = decision.get("reason")
            if reason:
                lines.append(f"- 说明：{reason}")
            confidence = row["confidence"]
            if confidence is not None:
                lines.append(f"- AI 自报把握：{confidence:.2f}（**仅参考，不用作自动决策依据**）")

            if verdict == "需你选择出自哪张专辑":
                lines.append("- 候选（请确认出自哪一张）：")
                for index, cand in enumerate(decision.get("show_candidates") or []):
                    lines.append(f"  {_fmt_candidate(index, cand, int(row['duration_sec'] or 0))}")
            elif verdict == "保留现有专辑（AI 有不同判断）":
                alt = decision.get("alternative")
                if alt:
                    lines.append(f"- AI 的不同判断：《{alt.get('album')}》 {alt.get('date') or ''}")
            lines.append("")

    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return {k: len(v) for k, v in groups.items()}


def _to_changes(raw: list[dict[str, Any]]):
    from ..core.models import TagChange

    out = []
    for item in raw:
        try:
            out.append(TagChange(**item))
        except Exception:  # noqa: BLE001
            continue
    return out


def write_csv(db: Database, run_id: str, out_path: str | Path) -> int:
    rows = _rows(db, run_id)
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "path", "verdict", "action", "evidence", "current_album", "suggested_album",
            "confidence", "n_candidates", "changes", "reason", "error",
        ])
        for row in rows:
            decision = row["_decision"]
            chosen = decision.get("chosen") or {}
            changes = "; ".join(render_changes(_to_changes(decision.get("changes") or [])))
            writer.writerow([
                row["path"],
                row["verdict"] or "",
                decision.get("action", ""),
                decision.get("evidence", ""),
                row["_tags"].get("album", ""),
                chosen.get("album", ""),
                "" if row["confidence"] is None else f"{row['confidence']:.2f}",
                row["n_candidates"] or 0,
                changes,
                decision.get("reason", ""),
                row["error"] or "",
            ])
    return len(rows)


def candidate_from_dict(data: dict[str, Any]) -> Candidate:
    return Candidate(**{k: v for k, v in data.items() if k in Candidate.model_fields})


def decision_from_dict(data: dict[str, Any]) -> Decision:
    return Decision(**data)
