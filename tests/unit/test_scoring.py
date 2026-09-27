"""候选评分与证据强度测试。用例取自 POC 真实素材。"""

from __future__ import annotations

from mds.core.models import Candidate, ItemView, Tags
from mds.core.scoring import (
    album_tag_matches,
    assess_evidence,
    rank_candidates,
    score_candidate,
)


def cand(album: str, length: int | None, *, date: str = "", rg: str = "", rtype: str = "") -> Candidate:
    return Candidate(
        release_mbid=f"mbid-{album}-{length}",
        album=album,
        date=date,
        track_length_sec=length,
        release_group=rg or album,
        rg_type=rtype,
    )


def item(album_tag: str = "", duration: int = 205, name: str = "06 Track.flac") -> ItemView:
    return ItemView(path=f"/music/{name}", name=name, duration_sec=duration, tags=Tags(album=album_tag))


# ── 真实场景：《NEON SINGLE》单曲 与 《NORTHERN LIGHTS》专辑 都收录同一录音，时长几乎相同 ──
GREEDY_A = cand("NEON SINGLE", 205, date="2018-05-02", rtype="Single")
GREEDY_B = cand("NEON SINGLE", 205, date="2018-05-02", rtype="Digital Media")
HYPER = cand("NORTHERN LIGHTS", 206, date="2019-12-04", rtype="Album")


def test_album_tag_pulls_matching_candidate_to_top() -> None:
    it = item(album_tag="NORTHERN LIGHTS")
    ranked = rank_candidates([GREEDY_A, GREEDY_B, HYPER], it)
    assert ranked[0].album == "NORTHERN LIGHTS"


def test_without_album_tag_length_only_cannot_separate() -> None:
    it = item(album_tag="")
    scores = {c.album: score_candidate(c, it) for c in (GREEDY_A, HYPER)}
    # 时长差 0s vs 1s，在只有曲名/时长的证据下几乎不可分辨
    assert abs(scores["NEON SINGLE"] - scores["NORTHERN LIGHTS"]) < 0.05


def test_length_unique_when_gap_is_large() -> None:
    it = item(duration=205)
    near = cand("Same Song", 205, date="2001")
    far = cand("Other Version", 260, date="2001")
    ranked = rank_candidates([near, far], it)
    assert ranked[0] is near
    assert assess_evidence(near, ranked, it) == "length_unique"


def test_length_not_unique_when_gap_is_tiny() -> None:
    it = item(duration=205)
    a = cand("A", 205, date="2001")
    b = cand("B", 206, date="2001")
    ranked = rank_candidates([a, b], it)
    assert assess_evidence(ranked[0], ranked, it) == "insufficient"


def test_album_tag_match_is_strongest_evidence() -> None:
    it = item(album_tag="NORTHERN LIGHTS")
    ranked = rank_candidates([GREEDY_A, HYPER], it)
    assert album_tag_matches(HYPER, it)
    assert assess_evidence(HYPER, ranked, it) == "album_tag_match"


def test_no_candidates_gives_none() -> None:
    assert assess_evidence(None, [], item()) == "none"


def test_release_type_has_no_weight() -> None:
    """R5：不得因为"是合集/原声带"就降权（POC 实测该倾向会掉 4 个点）。"""
    it = item()
    as_single = cand("Some Album", 300, date="2007", rtype="Single")
    as_compilation = cand("Some Album", 300, date="2007", rtype="Compilation")
    assert score_candidate(as_single, it) == score_candidate(as_compilation, it)
