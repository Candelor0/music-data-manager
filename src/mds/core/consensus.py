"""同目录字段共识与"补空"改动。纯函数。

这是核心增值点：

    真实样例库实测：
        曲名/艺术家/专辑/音轨号  = 57/57 全有
        年份 缺 31、流派 缺 31、专辑艺术家 缺 26、碟号 缺 26

    这些缺的都是**专辑级字段**——同一张专辑必然一致。
    所以"同目录 N 首都是 2017"就是把缺的那几首补上的最强证据，
    而且**完全本地、不花钱**。

三条安全规则：
    R-1  **只补空字段**，绝不覆盖已有值（与 R2 一致）
    R-2  只有"有值文件里的众数占比 ≥ 阈值"才叫共识；占比不够就不猜
    R-3  组内文件数 < 2 不做互证（"一首歌一个文件夹"就是这种情况）
"""

from __future__ import annotations

from collections import Counter

from .models import FieldConsensus, FolderGroup, ItemView, TagChange
from .normalize import normalize_text

#: 参与互证的字段：只含"整张专辑共有"的字段。
#: 曲名与音轨号是逐文件的，绝不能互证。
ALBUM_LEVEL_FIELDS: tuple[str, ...] = ("album", "albumartist", "date", "genre", "discnumber")

#: 默认阈值（可用配置覆盖）
DEFAULT_MIN_FILES = 2
DEFAULT_MIN_RATIO = 0.6
#: 至少要有这么多个文件“都有值且一致”，才算共识。
#: 否则“12 首里只有 1 首有年份”也会被判成共识 —— 那就不是共识，是孤证。
DEFAULT_MIN_VOTES = 2


def compute_consensus(
    members: list[ItemView],
    *,
    min_files: int = DEFAULT_MIN_FILES,
    min_ratio: float = DEFAULT_MIN_RATIO,
    min_votes: int = DEFAULT_MIN_VOTES,
) -> dict[str, FieldConsensus]:
    """算出组内各专辑级字段的共识。组太小、或只有孤证时返回空。

    `ratio` 的分母是「**有值**的文件数」，不是组内总数：
        12 首里 5 首写 2017、7 首为空 → 占比 5/5 = 1.0 → 共识成立
        12 首里 1 首写 2017、11 首为空 → 只有孤证（votes=1 < 2）→ 不成立
    """
    if len(members) < min_files:
        return {}

    out: dict[str, FieldConsensus] = {}
    for field in ALBUM_LEVEL_FIELDS:
        raw_values = [m.tags.value_of(field) for m in members]
        present = [v for v in raw_values if v.strip()]
        if not present:
            continue

        counts: Counter[str] = Counter()
        canonical: dict[str, str] = {}
        for value in present:
            key = normalize_text(value)
            counts[key] += 1
            canonical.setdefault(key, value.strip())

        key, votes = max(counts.items(), key=lambda kv: kv[1])
        ratio = votes / len(present)
        if votes < min_votes or ratio < min_ratio:
            continue
        out[field] = FieldConsensus(
            value=canonical[key], votes=votes, total=len(members), ratio=round(ratio, 3)
        )
    return out


def _fill_consensus_impl(
    group: FolderGroup,
    members: list[ItemView],
    *,
    min_files: int = DEFAULT_MIN_FILES,
    min_ratio: float = DEFAULT_MIN_RATIO,
) -> FolderGroup:
    return group.model_copy(
        update={"consensus": compute_consensus(members, min_files=min_files, min_ratio=min_ratio)}
    )


def fill_consensus(
    group: FolderGroup,
    members: list[ItemView],
    *,
    min_files: int = DEFAULT_MIN_FILES,
    min_ratio: float = DEFAULT_MIN_RATIO,
) -> FolderGroup:
    """把共识写回组对象（返回新对象，不改原对象）。"""
    return _fill_consensus_impl(group, members, min_files=min_files, min_ratio=min_ratio)


def apply_consensus(item: ItemView, group: FolderGroup | None) -> list[TagChange]:
    """针对单个文件，给出"用同目录共识补空字段"的改动。

    只补空；已有值一律不动（R-1）。
    """
    if group is None or group.n_files < 2 or not group.consensus:
        return []

    changes: list[TagChange] = []
    for field in ALBUM_LEVEL_FIELDS:
        consensus = group.consensus.get(field)
        if consensus is None or not consensus.value:
            continue
        if item.tags.value_of(field):
            continue  # 已有值 → 绝不覆盖
        changes.append(
            TagChange(field=field, before="", after=consensus.value, kind="consensus")
        )
    return changes


def conflicts_with_consensus(item: ItemView, group: FolderGroup | None) -> list[str]:
    """找出"已有值 与 同目录共识不一致"的字段（只用于提示，不产生改动）。"""
    if group is None or group.n_files < 2 or not group.consensus:
        return []
    out: list[str] = []
    for field in ALBUM_LEVEL_FIELDS:
        consensus = group.consensus.get(field)
        current = item.tags.value_of(field)
        if not consensus or not consensus.value or not current:
            continue
        if normalize_text(current) != normalize_text(consensus.value):
            out.append(field)
    return out
