"""候选评分与证据强度评估。纯函数。

实测结论：**时长能区分"不同版本"，但区分不了"同一录音的不同发行版"**。
因此评分里时长权重很高，但它只能把候选分成"同录音"和"不同录音"两堆，
堆内要靠专辑名。这就是为什么"缺专辑标签"时必然需要用户介入。

也正因如此，**发行版类型（Single/Album/Compilation）不给权重**——
POC 实测：给"偏好原始发行版"的倾向会让准确率从 81% 降到 77%（R5）。
"""

from __future__ import annotations

from .models import Candidate, EvidenceLevel, ItemView
from .normalize import normalize_artist, normalize_text, title_similarity

# 权重（合计 1.0）
W_LENGTH = 0.50
W_ALBUM = 0.40
W_TEXT = 0.10
#: 目录名线索的权重：接近「专辑标签命中」(0.40) 但略低 ——
#: 实测存在「标签比目录名更准」的情形（实测），所以不能等权。
W_FOLDER_HINT = 0.35

# 时长差判定阈值（秒）
DELTA_STRONG = 5
DELTA_WEAK = 15
# "唯一强候选"判定：最佳与次佳的时长差至少要拉开这么多秒
DELTA_UNIQUE_GAP = 10

_NEUTRAL = 0.5  # 缺少该证据时的中性分（不偏袒任何候选）


def _length_score(delta: int | None) -> float:
    if delta is None:
        return _NEUTRAL
    if delta <= DELTA_STRONG:
        return 1.0
    if delta <= DELTA_WEAK:
        return 0.4
    return 0.1


def _album_score(cand: Candidate, item: ItemView) -> float:
    tag = normalize_text(item.tags.album)
    if not tag:
        return _NEUTRAL  # 没有可依据的专辑标签 → 中性
    cand_album = normalize_text(cand.album)
    if not cand_album:
        return 0.0
    if cand_album == tag:
        return 1.0
    if cand_album in tag or tag in cand_album:
        return 0.6
    return 0.0


def _text_score(cand: Candidate, item: ItemView) -> float:
    """候选专辑名/发行组名与"文件名 + 现有曲名"的文本相似度，作为弱证据。"""
    probe = " ".join(x for x in (item.name, item.tags.title) if x)
    if not probe:
        return _NEUTRAL
    a = title_similarity(cand.album, probe)
    b = title_similarity(cand.release_group, probe)
    return max(a, b)


def _folder_hint_score(cand: Candidate, folder_hint: str) -> float:
    """候选专辑名与「清洗后的目录名」是否一致。

    只作**打分**，绝不产生写入值 —— 实测有「标签比目录名更准」的情形。
    """
    hint = normalize_text(folder_hint)
    if not hint:
        return _NEUTRAL
    cand_album = normalize_text(cand.album)
    if not cand_album:
        return 0.0
    if cand_album == hint:
        return 1.0
    if cand_album in hint or hint in cand_album:
        return 0.6
    return 0.0


def score_candidate(cand: Candidate, item: ItemView, folder_hint: str = "") -> float:
    delta = cand.length_delta(item.duration_sec)
    base = (
        W_LENGTH * _length_score(delta)
        + W_ALBUM * _album_score(cand, item)
        + W_TEXT * _text_score(cand, item)
    )
    hint_score = _folder_hint_score(cand, folder_hint)
    if hint_score == _NEUTRAL:  # 没有目录线索时不改变总分
        return base
    return base + W_FOLDER_HINT * hint_score


def rank_candidates(
    cands: list[Candidate], item: ItemView, folder_hint: str = ""
) -> list[Candidate]:
    """按分数降序（稳定排序，同分保持原顺序）。"""
    scored = [(score_candidate(c, item, folder_hint), i, c) for i, c in enumerate(cands)]
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [c for _, _, c in scored]


def album_tag_matches(cand: Candidate | None, item: ItemView) -> bool:
    """候选专辑名是否与文件现有专辑标签一致（归一化后）。"""
    if cand is None:
        return False
    tag = normalize_text(item.tags.album)
    return bool(tag) and normalize_text(cand.album) == tag


def artist_matches(cand_artists: list[str], tag_artist: str) -> bool:
    if not cand_artists or not tag_artist:
        return False
    want = normalize_artist(tag_artist)
    return any(normalize_artist(a) == want for a in cand_artists)


def _length_unique(ranked: list[Candidate], item: ItemView) -> bool:
    """最佳候选的时长显著优于其它候选 → 视为"时长可唯一确定"。"""
    if not ranked:
        return False
    first = ranked[0].length_delta(item.duration_sec)
    if first is None or first > DELTA_STRONG:
        return False
    if len(ranked) == 1:
        return True
    second = ranked[1].length_delta(item.duration_sec)
    if second is None:
        return True
    return (second - first) >= DELTA_UNIQUE_GAP


def assess_evidence(chosen: Candidate | None, ranked: list[Candidate], item: ItemView) -> EvidenceLevel:
    """证据强度。顺序：专辑标签命中 > 时长唯一 > 不足。

    注意：此处**不使用**模型自报的 confidence（R4）。
    """
    if not ranked:
        return "none"
    if album_tag_matches(chosen, item):
        return "album_tag_match"
    if chosen is not None and chosen is ranked[0] and _length_unique(ranked, item):
        return "length_unique"
    return "insufficient"
