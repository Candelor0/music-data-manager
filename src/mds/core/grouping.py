"""按「专辑目录」分组。纯函数。

依据（实测）：
    真实库里 **每个音频文件的父目录就是专辑目录**（10 个目录，零例外），
    而且目录层级深度不固定（实测 2/3/4 层混着）—— 所以只看向上一层，不猜深度。

另外：
    - 「分类目录」（只含子目录、不含音频）天然不会成为组，无需特殊处理
    - 「一首歌一个文件夹」很常见 → 单文件组仍然生成，只是不做互证（见 consensus.py）
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from .folderhint import folder_hint
from .models import FolderGroup, ItemView
from .normalize import normalize_artist


def group_by_folder(items: list[ItemView]) -> list[FolderGroup]:
    """按父目录分组，返回按目录名排序的组列表。

    同时算出：
      - `n_files`：组内文件数
      - `artist_hint`：组内艺术家众数（用于清洗目录名里的艺术家前缀）
      - `folder_hint`：清洗后的目录名（**仅作线索，不作写入值**）
    `consensus` 由 consensus.fill_consensus() 填。
    """
    buckets: dict[str, list[ItemView]] = {}
    for item in items:
        parent = str(Path(item.path).parent)
        buckets.setdefault(parent, []).append(item)

    groups: list[FolderGroup] = []
    for folder, members in buckets.items():
        artist_hint = _majority([m.tags.value_of("artist") for m in members])
        groups.append(
            FolderGroup(
                folder_path=folder,
                n_files=len(members),
                artist_hint=artist_hint,
                folder_hint=folder_hint(Path(folder).name, artist_hint),
            )
        )
    groups.sort(key=lambda g: g.folder_path)
    return groups


def _majority(values: list[str]) -> str:
    """归一化后取众数；有并列时取出现更早的那个（保持稳定）。"""
    counts: Counter[str] = Counter()
    first_seen: dict[str, str] = {}
    for raw in values:
        key = normalize_artist(raw)
        if not key:
            continue
        counts[key] += 1
        first_seen.setdefault(key, raw.strip())
    if not counts:
        return ""
    best = max(counts.items(), key=lambda kv: kv[1])[0]
    return first_seen[best]


def group_map(groups: list[FolderGroup]) -> dict[str, FolderGroup]:
    """目录路径 → 组，便于按文件反查。"""
    return {g.folder_path: g for g in groups}
