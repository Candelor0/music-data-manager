"""按专辑批量裁决：同一张专辑选一次候选，落到该目录里的每一条。

为什么需要这个（真实样例库）：

    真实样例库里有 12 首陆离的歌同属一张专辑。如果「需要你选出自哪张专辑」要逐条点，
    就是 12 次；按专辑只选一次就够了。

做法：

    1. 收集某个专辑目录里所有 `action == "ask_user"` 的条目
    2. 取它们候选列表的**交集**（按 release MBID）——
       交集里的候选才可能同时适用于这一整张专辑
    3. 用户选定一个候选 → 对每一条写 `user_choice = "picked:<该条目自己列表里的序号>"`
    4. **拿不到这个候选的条目**：不改，保留待决（在界面上说明原因，不硬套）

命令行对应：`mds choose <run_id> --folder <目录> --candidate <序号>`
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..core.models import Candidate
from ..logging_setup import get_logger
from ..storage.db import Database
from .common import candidates_of, decision_of
from .plan import build_plans

log = get_logger("choose")


@dataclass
class AlbumCandidate:
    """一个候选发行版，以及它在该专辑目录里的命中情况。"""

    release_mbid: str
    album: str = ""
    date: str = ""
    country: str = ""
    status: str = ""
    fmt: str = ""
    track_count: int = 0
    hits: int = 0                       # 该目录里有几首的候选列表含它
    #: item_id → 该条目候选列表里的序号（供写 user_choice 用）
    indexes: dict[int, int] = field(default_factory=dict)

    @property
    def covers_all(self) -> bool:
        return self.hits > 0


@dataclass
class AlbumChoice:
    """一个需要用户裁决的专辑目录。"""

    folder: str
    item_ids: list[int] = field(default_factory=list)
    candidates: list[AlbumCandidate] = field(default_factory=list)

    @property
    def n_items(self) -> int:
        return len(self.item_ids)

    @property
    def title(self) -> str:
        return Path(self.folder).name or self.folder

    def covering_candidates(self) -> list[AlbumCandidate]:
        """能覆盖全目录的候选排前面（其余仍然列出，并标明覆盖几首）。"""
        return sorted(self.candidates, key=lambda c: (-c.hits, c.album))

    def candidate_by_index(self, index: int) -> AlbumCandidate | None:
        ranked = self.covering_candidates()
        if 0 <= index < len(ranked):
            return ranked[index]
        return None


@dataclass
class ChooseStats:
    folder: str = ""
    release_mbid: str = ""
    n_total: int = 0
    n_chosen: int = 0
    n_unmatched: int = 0
    plans_rebuilt: int = 0
    unmatched_items: list[int] = field(default_factory=list)


def _pending_by_folder(db: Database, run_id: str) -> dict[str, list[int]]:
    """哪些条目需要用户选专辑（按目录归组）。"""
    out: dict[str, list[int]] = {}
    for raw in db.iter_items(run_id):
        row = dict(raw)
        decision = decision_of(row)
        if decision is None or decision.action != "ask_user":
            continue
        folder = str(Path(str(row.get("path", ""))).parent)
        out.setdefault(folder, []).append(int(row["id"]))
    return out


def album_choices(db: Database, run_id: str) -> list[AlbumChoice]:
    """列出所有需要裁决的专辑目录及其候选（按待决数量降序）。"""
    by_folder = _pending_by_folder(db, run_id)
    rows = {int(r["id"]): dict(r) for r in db.iter_items(run_id)}

    choices: list[AlbumChoice] = []
    for folder, item_ids in by_folder.items():
        tally: dict[str, AlbumCandidate] = {}
        for item_id in item_ids:
            row = rows.get(item_id)
            if row is None:
                continue
            for index, cand in enumerate(candidates_of(db, row)):
                entry = tally.get(cand.release_mbid)
                if entry is None:
                    entry = _to_view(cand)
                    tally[cand.release_mbid] = entry
                entry.hits += 1
                entry.indexes[item_id] = index
        order = list(tally.values())
        choices.append(
            AlbumChoice(
                folder=folder,
                item_ids=list(item_ids),
                candidates=sorted(order, key=lambda c: (-c.hits, c.album)),
            )
        )
    choices.sort(key=lambda c: (-c.n_items, c.folder))
    return choices


def _to_view(cand: Candidate) -> AlbumCandidate:
    return AlbumCandidate(
        release_mbid=cand.release_mbid,
        album=cand.album,
        date=cand.date,
        country=cand.country,
        status=cand.status,
        fmt=cand.format,
        track_count=cand.track_count,
    )


def apply_album_choice(
    db: Database, run_id: str, folder: str, release_mbid: str
) -> ChooseStats:
    """把"这张专辑选自这一个发行版"落到该目录的每一条上。

    落不下去的条目**保留待决**并如实报数，不硬套一个不适合的候选。
    """
    stats = ChooseStats(folder=folder, release_mbid=release_mbid)
    item_ids = _pending_by_folder(db, run_id).get(folder, [])
    stats.n_total = len(item_ids)
    if not item_ids:
        return stats

    rows = {int(r["id"]): dict(r) for r in db.iter_items(run_id)}
    applied: list[int] = []
    for item_id in item_ids:
        row = rows.get(item_id)
        if row is None:
            continue
        index = next(
            (
                i
                for i, cand in enumerate(candidates_of(db, row))
                if cand.release_mbid == release_mbid
            ),
            None,
        )
        if index is None:
            stats.n_unmatched += 1
            stats.unmatched_items.append(item_id)
            continue
        db.set_user_choice(item_id, f"picked:{index}")
        applied.append(item_id)

    stats.n_chosen = len(applied)
    if applied:
        build_plans(db, run_id, only=applied, refresh=True)
        stats.plans_rebuilt = len(applied)
    log.debug("按专辑裁决：%s", stats)
    return stats


def render_album_choices(db: Database, run_id: str, *, limit: int = 0) -> list[str]:
    """把"需要你裁决的专辑"渲染成给人看的文本。"""
    choices = album_choices(db, run_id)
    lines: list[str] = []
    if not choices:
        lines.append("（没有需要你选出自哪张专辑的条目）")
        return lines

    total = sum(c.n_items for c in choices)
    lines.append(f"需要你决定的有 {total} 首，集中在 {len(choices)} 张专辑 —— 每张选一次即可")
    lines.append("")
    shown = 0
    for choice in choices:
        if limit and shown >= limit:
            lines.append(f"（还有 {len(choices) - shown} 张专辑没显示，去掉 --limit 看全部）")
            break
        shown += 1
        lines.append(f"── {choice.title}　{choice.n_items} 首待定")
        lines.append(f"   {choice.folder}")
        for index, cand in enumerate(choice.covering_candidates()):
            marks = []
            if cand.date:
                marks.append(cand.date)
            if cand.country:
                marks.append(cand.country)
            if cand.fmt:
                marks.append(cand.fmt)
            coverage = f"覆盖 {cand.hits}/{choice.n_items} 首"
            lines.append(
                f"   [{index}] {cand.album or '（无专辑名）':<32} {'  '.join(marks):<22} {coverage}"
            )
        lines.append(f"   选定：mds choose {run_id} --folder \"{choice.folder}\" --candidate 0")
        lines.append("")
    return lines
