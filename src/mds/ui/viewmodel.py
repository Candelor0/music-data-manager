"""界面用视图模型 —— 只读，**不导入 Qt**，可单独做单元测试。

本模块只做一件事：把数据库里的行整理成界面能直接显示的纯数据。
它不写数据库、不碰音乐文件、不联网。

之所以把"取数"和"画界面"分开：这样绝大多数逻辑（分组、共识、差异、汇总）
都能在没有图形环境的机器上被测到，界面层只剩"把数据放上去"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.models import Candidate, Decision, WritePlan
from ..core.scoring import score_candidate
from ..pipeline.common import (
    candidates_of,
    decision_of,
    item_view,
    load_json,
)

# ── 文案映射（界面与报告共用同一套词）──────────────────────

FIELD_LABELS: dict[str, str] = {
    "title": "曲名",
    "artist": "艺术家",
    "album": "专辑",
    "albumartist": "专辑艺术家",
    "date": "年份",
    "genre": "流派",
    "discnumber": "碟号",
    "composer": "作曲",
}

KIND_LABELS: dict[str, str] = {
    "fill": "补全",
    "cleanup": "清洗",
    "normalize": "规范化",
    "consensus": "同目录补全",
}

SOURCE_LABELS: dict[str, str] = {
    "fill_missing": "补全缺失字段",
    "cleanup": "清洗/规范化",
    "ask_user_choice": "你选的候选",
    "adopted_ai": "采纳 AI 判断",
}

VERDICT_MARKS: dict[str, str] = {
    "no_op": "✅",
    "fill_missing": "➕",
    "cleanup": "🧹",
    "keep_existing": "🛡",
    "ask_user": "❔",
    "no_evidence": "⌀",
}

#: 「冲突」提示里显示的字段顺序（专辑级字段优先）
CONFLICT_FIELDS: tuple[str, ...] = ("album", "albumartist", "date", "genre", "discnumber")

#: 未分组条目所在的虚拟节点标题
UNGROUPED_TITLE = "（未分组）"


def label(field_name: str) -> str:
    return FIELD_LABELS.get(field_name, field_name)


_PROGRESS_RE = re.compile(r"^\[(\d+)/(\d+)\]\s*(.*)$")


def parse_progress_line(text: str) -> tuple[int, int, str] | None:
    """从分析pipeline的进度行里解析出「已完成 / 总数 / 当前文件」。

    分析pipeline 输出的格式是 `  [12/57] 05. xxx.flac  ➕ 建议补全缺失字段  候选 3`。
    解析不出来就返回 None（调用方把它当普通消息显示）。
    """
    match = _PROGRESS_RE.match(text.strip())
    if not match:
        return None
    done, total, rest = match.groups()
    current = rest.split("  ")[0].strip()
    return int(done), int(total), current


def fmt_length(seconds: int | None) -> str:
    if not seconds:
        return "—"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}:{secs:02d}"


def fmt_delta(delta: int | None) -> str:
    if delta is None:
        return "—"
    return f"{delta}s"


# ── 数据结构 ──────────────────────────────────────────────


@dataclass
class ChangeView:
    field: str
    field_label: str
    kind: str
    kind_label: str
    before: str
    after: str

    @property
    def is_fill(self) -> bool:
        return not self.before


@dataclass
class CandidateView:
    index: int
    album: str
    date: str
    country: str
    status: str
    fmt: str
    label_name: str
    release_mbid: str
    length: str
    delta: str
    score: float
    chosen: bool


@dataclass
class FileRow:
    item_id: int
    name: str
    path: str
    title: str
    artist: str
    album: str
    date: str
    genre: str
    action: str
    verdict: str
    mark: str
    n_changes: int
    n_conflicts: int
    write_allowed: bool
    write_reason: str
    done: bool  # 已经跑过分析没（没跑过显示为「未分析」）
    #: 这一条能不能写入（决定勾选框是否可用）
    writable: bool = False
    #: 当前是否被勾选
    checked: bool = False


@dataclass
class GroupNode:
    group_id: int
    folder_path: str
    title: str
    n_files: int
    folder_hint: str
    artist_hint: str
    consensus: dict[str, dict] = field(default_factory=dict)
    files: list[FileRow] = field(default_factory=list)

    @property
    def n_with_changes(self) -> int:
        return sum(1 for f in self.files if f.n_changes)

    @property
    def n_conflicts(self) -> int:
        return sum(1 for f in self.files if f.n_conflicts)

    def consensus_lines(self) -> list[str]:
        out: list[str] = []
        for name, info in self.consensus.items():
            votes = info.get("votes", 0)
            value = info.get("value", "")
            out.append(f"{label(name)}：{value}（{votes}/{self.n_files} 首一致）")
        return out


@dataclass
class Summary:
    total: int = 0
    analyzed: int = 0
    by_verdict: dict[str, int] = field(default_factory=dict)
    n_groups: int = 0
    n_conflicts: int = 0
    n_writable: int = 0
    n_consensus_changes: int = 0
    not_analyzed: int = 0


# ── 取数 ──────────────────────────────────────────────────


def _plan_of(row: dict[str, Any]) -> WritePlan | None:
    data = load_json(row.get("plan_json"))
    if not data:
        return None
    try:
        return WritePlan(**data)
    except Exception:  # noqa: BLE001 - 旧数据格式不合就当没有计划
        return None


def file_row(row: dict[str, Any]) -> FileRow:
    decision = decision_of(row)
    plan = _plan_of(row)
    tags = item_view(row).tags
    action = decision.action if decision else ""
    return FileRow(
        item_id=int(row["id"]),
        name=str(row.get("name", "")),
        path=str(row.get("path", "")),
        title=tags.value_of("title"),
        artist=tags.value_of("artist"),
        album=tags.value_of("album"),
        date=tags.value_of("date"),
        genre=tags.value_of("genre"),
        action=action,
        verdict=verdict_text(decision),
        mark=VERDICT_MARKS.get(action, ""),
        n_changes=len(decision.changes) if decision else 0,
        n_conflicts=len(decision.conflicts) if decision else 0,
        write_allowed=bool(plan and plan.allowed),
        write_reason=plan.reason if plan else "",
        done=decision is not None,
        writable=bool(plan and plan.allowed),
    )


def verdict_text(decision: Decision | None) -> str:
    if decision is None:
        return "未分析"
    from ..core.decide import VERDICT_LABELS

    return VERDICT_LABELS.get(decision.action, decision.action)


def build_groups(db: Any, run_id: str) -> list[GroupNode]:
    """按目录分组返回界面用的树。没有分组时退化成「未分组」一个节点。"""
    rows = [dict(r) for r in db.iter_items(run_id)]
    group_rows = [dict(g) for g in db.list_groups(run_id)]
    by_group: dict[int, list[dict]] = {}
    ungrouped: list[dict] = []
    for row in rows:
        gid = row.get("group_id")
        if gid:
            by_group.setdefault(int(gid), []).append(row)
        else:
            ungrouped.append(row)

    nodes: list[GroupNode] = []
    for group in group_rows:
        gid = int(group["id"])
        members = by_group.get(gid, [])
        node = GroupNode(
            group_id=gid,
            folder_path=str(group.get("folder_path", "")),
            title=Path(str(group.get("folder_path", ""))).name or str(group.get("folder_path", "")),
            n_files=int(group.get("n_files") or len(members)),
            folder_hint=str(group.get("folder_hint") or ""),
            artist_hint=str(group.get("artist_hint") or ""),
            consensus=load_json(group.get("consensus_json")),
            files=[file_row(r) for r in sorted(members, key=lambda r: str(r.get("name", "")))],
        )
        nodes.append(node)

    if ungrouped or not group_rows:
        nodes.append(
            GroupNode(
                group_id=0,
                folder_path="",
                title=UNGROUPED_TITLE,
                n_files=len(ungrouped),
                folder_hint="",
                artist_hint="",
                files=[file_row(r) for r in sorted(ungrouped, key=lambda r: str(r.get("name", "")))],
            )
        )
    return nodes


def change_views(decision: Decision | None) -> list[ChangeView]:
    if decision is None:
        return []
    return [
        ChangeView(
            field=c.field,
            field_label=label(c.field),
            kind=c.kind,
            kind_label=KIND_LABELS.get(c.kind, c.kind),
            before=c.before or "",
            after=c.after or "",
        )
        for c in decision.changes
    ]


def candidate_views(
    item_row: dict[str, Any],
    candidates: list[Candidate],
    *,
    folder_hint: str = "",
    chosen: Candidate | None = None,
) -> list[CandidateView]:
    view = item_view(item_row)
    chosen_id = chosen.release_mbid if chosen else ""
    out: list[CandidateView] = []
    for index, cand in enumerate(candidates):
        delta = cand.length_delta(view.duration_sec)
        out.append(
            CandidateView(
                index=index,
                album=cand.album or "（无专辑名）",
                date=cand.date,
                country=cand.country,
                status=cand.status,
                fmt=cand.format,
                label_name=cand.label,
                release_mbid=cand.release_mbid,
                length=fmt_length(cand.track_length_sec),
                delta=fmt_delta(delta),
                score=round(score_candidate(cand, view, folder_hint), 3),
                chosen=bool(chosen_id) and cand.release_mbid == chosen_id,
            )
        )
    out.sort(key=lambda c: -c.score)
    return out


def consensus_note(group: GroupNode) -> list[str]:
    """该文件的「同目录共识」提示行（空表示没有共识）。"""
    lines = group.consensus_lines()
    if group.n_files < 2:
        return []
    return lines


def detail(
    db: Any,
    row: dict[str, Any],
    *,
    group: GroupNode | None = None,
    terms_enabled: bool = True,
) -> dict[str, Any]:
    """单个文件的完整只读详情，供右侧差异面板渲染。"""
    decision = decision_of(row)
    plan = _plan_of(row)
    tags = item_view(row).tags
    candidates = candidates_of(db, row)
    folder_hint = group.folder_hint if group else ""

    conflicts: list[dict[str, str]] = []
    if decision and group:
        for name in CONFLICT_FIELDS:
            current = tags.value_of(name)
            consensus = group.consensus.get(name, {})
            if not current or not consensus:
                continue
            if name not in decision.conflicts:
                continue
            conflicts.append(
                {
                    "field": label(name),
                    "current": current,
                    "consensus": str(consensus.get("value", "")),
                    "votes": str(consensus.get("votes", "")),
                }
            )

    return {
        "item_id": int(row["id"]),
        "name": str(row.get("name", "")),
        "path": str(row.get("path", "")),
        "duration": fmt_length(int(row.get("duration_sec") or 0)),
        "group_title": group.title if group else "",
        "group_path": str(group.folder_path) if group else "",
        "analysis_status": _analysis_status(row),
        "action": decision.action if decision else "",
        "verdict": verdict_text(decision),
        "mark": VERDICT_MARKS.get(decision.action if decision else "", ""),
        "evidence": decision.evidence if decision else "",
        "reason": decision.reason if decision else "",
        "changes": change_views(decision),
        "conflicts": conflicts,
        "consensus": consensus_note(group) if group else [],
        "candidates": candidate_views(
            row, candidates, folder_hint=folder_hint, chosen=decision.chosen if decision else None
        ),
        "chosen_album": decision.chosen.album if decision and decision.chosen else "",
        "write": {
            "allowed": bool(plan and plan.allowed),
            "reason": plan.reason if plan else "（还没有生成写入计划）",
            "source": SOURCE_LABELS.get(plan.source or "", plan.source or "") if plan else "",
            "n_changes": len(plan.changes) if plan else 0,
        },
        "tags": {label(name): tags.value_of(name) for name in FIELD_LABELS},
        #: 设置页的「术语悬停解释」开关（关掉后术语渲染成普通文字）
        "terms_enabled": terms_enabled,
    }


def _analysis_status(row: dict[str, Any]) -> str:
    if not row.get("fp_status"):
        return "未分析（只有标签，还没做声学指纹）"
    if row.get("fp_status") == "error":
        return f"指纹失败：{row.get('fp_error') or '未知原因'}"
    return "已分析"


# ─────────────────────
@dataclass
class TodoCard:
    """首屏的一张卡片（一个待办组）。"""

    key: str
    label: str
    hint: str
    n_items: int = 0
    n_changes: int = 0
    n_checked: int = 0
    n_folders: int = 0
    summary: str = ""
    folder_names: list[str] = field(default_factory=list)

    @property
    def actionable(self) -> bool:
        """这一组需不需要用户动手。"""
        return self.key in ("safe", "choose", "conflict") and self.n_items > 0


@dataclass
class AlbumChoiceView:
    """按专辑裁决面板里的一张专辑。"""

    folder: str
    title: str
    n_items: int
    candidates: list[CandidateView] = field(default_factory=list)
    release_ids: list[str] = field(default_factory=list)
    coverages: list[str] = field(default_factory=list)


def build_todo_cards(db: Any, run_id: str) -> tuple[list[TodoCard], str]:
    """待办 4 组 + 顶部那句话。**只读数据库**。"""
    from ..core.todo import headline
    from ..pipeline.run_all import build_todo_from_db

    groups = build_todo_from_db(db, run_id)
    cards = [
        TodoCard(
            key=group.key,
            label=group.label,
            hint=group.hint,
            n_items=group.n_items,
            n_changes=group.n_changes,
            n_checked=group.n_checked,
            n_folders=group.n_folders,
            summary=group.summary(),
            folder_names=[group.folder_label(f) for f in group.folder_names()],
        )
        for group in groups
    ]
    return cards, headline(groups)


def group_rows(db: Any, run_id: str, group_key: str) -> list[FileRow]:
    """展开某一组时，显示哪些条目。**只读**。

    「是否默认勾选」由 `core/selectrules.py` 按**计划里的改动**决定，
    **不按标签一刀切** —— 所以 🟠 组里那些"只有安全填空"的条目也是默认勾上的
    （它们想换专辑名的那一项早已被丢掉）。这是既定规则。
    """
    from ..core.selectrules import is_checked
    from ..core.todo import classify
    from ..pipeline.run_all import todo_entries

    out: list[FileRow] = []
    rows = {int(r["id"]): dict(r) for r in db.iter_items(run_id)}
    for entry in todo_entries(db, run_id):
        if classify(entry.decision, entry.plan) != group_key:
            continue
        raw = rows.get(entry.item_id)
        if raw is None:
            continue
        row = file_row(raw)
        row.checked = is_checked(entry.decision, entry.plan)
        out.append(row)
    return out


def album_choice_views(db: Any, run_id: str, *, limit: int = 0) -> list[AlbumChoiceView]:
    """按专辑裁决面板的数据：每张专辑 + 可按的候选。"""
    from ..pipeline.choose import album_choices

    out: list[AlbumChoiceView] = []
    for choice in album_choices(db, run_id):
        if limit and len(out) >= limit:
            break
        ranked = choice.covering_candidates()
        out.append(
            AlbumChoiceView(
                folder=choice.folder,
                title=choice.title,
                n_items=choice.n_items,
                candidates=[
                    CandidateView(
                        index=index,
                        album=cand.album or "（无专辑名）",
                        date=cand.date,
                        country=cand.country,
                        status=cand.status,
                        fmt=cand.fmt,
                        label_name="",
                        release_mbid=cand.release_mbid,
                        length="",
                        delta="",
                        score=float(cand.track_count),
                        chosen=False,
                    )
                    for index, cand in enumerate(ranked)
                ],
                release_ids=[c.release_mbid for c in ranked],
                coverages=[f"覆盖 {c.hits}/{choice.n_items} 首" for c in ranked],
            )
        )
    return out


def default_writable_ids(db: Any, run_id: str) -> list[int]:
    """所有组里"默认勾选"的条目 —— 底部「确认写入」写的就是这些。

    不分成一组一组点，因为设计目标是"一个按钮写完所有可以放心写的"。
    """
    from ..core.selectrules import is_checked
    from ..pipeline.run_all import todo_entries

    return [
        entry.item_id
        for entry in todo_entries(db, run_id)
        if is_checked(entry.decision, entry.plan)
    ]


def writable_ids(db: Any, run_id: str, group_key: str) -> list[int]:
    """某一组里"允许写入"的条目 id（写入按钮用）。"""
    from ..core.todo import classify
    from ..pipeline.run_all import todo_entries

    out: list[int] = []
    for entry in todo_entries(db, run_id):
        if classify(entry.decision, entry.plan) != group_key:
            continue
        if entry.plan is not None and entry.plan.allowed:
            out.append(entry.item_id)
    return out


# ─────────────────────
STATE_FIRST_RUN = "first_run"   # 还没选过音乐库
STATE_READY = "ready"           # 选过目录，但没有待处理的结果
STATE_RESULTS = "results"       # 有结果，且还有要处理的


def view_state(*, library: str, run_exists: bool, has_actionable: bool) -> str:
    """主页面该显示哪种样子。

    - 没选过音乐库 → 起始页
    - 选过但还没结果，或结果已经处理完 → 待开始页
    - 还有要处理的事 → 结果页（筛选标签 + 清单）
    """
    if not (library or "").strip():
        return STATE_FIRST_RUN
    if not run_exists:
        return STATE_READY
    return STATE_RESULTS if has_actionable else STATE_READY


def has_actionable(cards: list[TodoCard]) -> bool:
    """除了「无需处理」那一组，还有没有别的事要做。"""
    return any(card.n_items for card in cards if card.key != "none")


def settings_hint(view) -> str:
    """设置页顶部的两行说明（含密钥存放位置，如实说明是否加密）。"""
    return view.secret_place_line
