"""决策层测试 —— 三条不可违反的规则 R2 / R3 / R4。

用例对应 真实样例里人工判断过的条目。
"""

from __future__ import annotations

from mds.core.decide import decide, verdict_of
from mds.core.models import Candidate, CleanedTags, ItemView, Suggestion, Tags


def cand(album: str, length: int | None, *, date: str = "", rtype: str = "") -> Candidate:
    return Candidate(
        release_mbid=f"mbid-{album}-{length}",
        album=album,
        date=date,
        track_length_sec=length,
        release_group=album,
        rg_type=rtype,
    )


def model_out(index: int, confidence: float, *, title: str = "", album: str = "", artist: str = "",
              date: str = "", genre: str = "") -> Suggestion:
    return Suggestion(
        chosen_index=index,
        confidence=confidence,
        reason="测试用理由",
        cleaned=CleanedTags(title=title, artist=artist, album=album, date=date, genre=genre),
    )


# ── R2：已有专辑标签 → 默认保留，不得覆盖 ─────────────────────────
def test_r2_conflict_keeps_existing_album() -> None:
    """实测样例第 4 条：文件专辑标签 NORTHERN LIGHTS 是对的，AI 却选了《NEON SINGLE》。"""
    it = ItemView(
        path="/m/06 Keep the Heat.flac",
        name="06 Keep the Heat and Fire Yourself Up.flac",
        duration_sec=205,
        tags=Tags(title="Keep the Heat and Fire Yourself Up", artist="Fear, and Loathing in Las Vegas",
                  album="NORTHERN LIGHTS"),
    )
    cands = [cand("NEON SINGLE", 205, date="2018-05-02", rtype="Single"),
             cand("NORTHERN LIGHTS", 206, date="2019-12-04", rtype="Album")]
    d = decide(it, cands, model_out(0, confidence=0.9, album="NEON SINGLE"))
    assert d.action == "keep_existing"
    assert d.alternative is not None and d.alternative.album == "NEON SINGLE"
    # 最关键的断言：**不得产生 album 的改动**
    assert all(c.field != "album" for c in d.changes)


def test_r2_no_change_when_existing_album_matches_model() -> None:
    it = ItemView(
        path="/m/x.flac",
        name="x.flac",
        duration_sec=205,
        tags=Tags(title="T", artist="A", album="NORTHERN LIGHTS"),
    )
    cands = [cand("NORTHERN LIGHTS", 205, date="2019-12-04")]
    d = decide(it, cands, model_out(0, 0.8, album="NORTHERN LIGHTS"))
    assert d.action in ("no_op", "cleanup", "fill_missing")
    assert all(c.field != "album" for c in d.changes)


# ── R3：缺专辑标签 + 证据不足 → 交给用户选 ────────────────────────
def test_r3_asks_user_when_evidence_insufficient() -> None:
    """实测样例第 2/3/6 条：多个候选时长一致，无法从音频分辨。"""
    it = ItemView(
        path="/m/04.もういちど テスト.flac",
        name="04.もういちど テスト.flac",
        duration_sec=260,
        tags=Tags(title="もういちど テスト", artist="Lime*Notes"),  # 无专辑标签
    )
    cands = [
        cand("LIVE ANTHEM", 260, date="2021-05-19", rtype="Album"),
        cand("もういちど テスト", 261, date="2018-08-08", rtype="Single"),
    ]
    d = decide(it, cands, model_out(1, confidence=0.9))
    assert d.action == "ask_user"
    assert len(d.show_candidates) == 2
    # 不确定出自哪张专辑时，不得给出 album 的改动
    assert all(c.field != "album" for c in d.changes)


def test_r3_provides_up_to_n_candidates() -> None:
    it = ItemView(path="/m/a.flac", name="a.flac", duration_sec=200, tags=Tags(title="t", artist="a"))
    cands = [cand(f"Album {i}", 200 + i, date="2020") for i in range(15)]
    d = decide(it, cands, model_out(0, 0.5), low_evidence_candidates=10)
    assert d.action == "ask_user"
    assert len(d.show_candidates) == 10  # R9：低证据时放宽到 10 个


def test_no_false_ask_when_album_tag_resolves_it() -> None:
    it = ItemView(
        path="/m/a.flac", name="a.flac", duration_sec=200,
        tags=Tags(title="t", artist="a", album="LIVE ANTHEM"),
    )
    cands = [cand("LIVE ANTHEM", 200, date="2021"), cand("Single X", 201, date="2018")]
    d = decide(it, cands, model_out(0, 0.7, album="LIVE ANTHEM"))
    assert d.action != "ask_user"


# ── R4：不得用模型自报 confidence 做自动决策门槛 ──────────────────
def test_r4_high_confidence_does_not_bypass_ask_user() -> None:
    """模型自报高置信度的条目也可能被拒绝，因此不据此做自动决策。"""
    it = ItemView(path="/m/a.flac", name="a.flac", duration_sec=219, tags=Tags(title="t", artist="a"))
    cands = [cand("A", 219, date="2019"), cand("B", 220, date="2020")]
    low = decide(it, cands, model_out(0, confidence=0.1))
    high = decide(it, cands, model_out(0, confidence=0.99))
    assert low.action == high.action == "ask_user"


def test_r4_confidence_absent_still_works() -> None:
    it = ItemView(path="/m/a.flac", name="a.flac", duration_sec=219, tags=Tags(title="t", artist="a"))
    cands = [cand("A", 219, date="2019"), cand("B", 220, date="2020")]
    d = decide(it, cands, None)
    assert d.action == "ask_user"


# ── 补全与清洗 ────────────────────────────────────────────────
def test_fills_missing_fields_only() -> None:
    it = ItemView(
        path="/m/a.flac", name="a.flac", duration_sec=300,
        tags=Tags(title="假想之路", artist="陆离", album="假想专辑（实体版）", date="2017"),
    )
    cands = [cand("假想专辑（实体版）", 301, date="2017-04-30")]
    d = decide(it, cands, model_out(0, 0.8, title="假想之路", artist="陆离",
                                    album="假想专辑（实体版）", date="2017", genre="Folk"))
    assert d.action == "fill_missing"
    assert [c.field for c in d.changes] == ["genre"]
    assert d.changes[0].before == "" and d.changes[0].after == "Folk"


def test_cleanup_title_with_junk() -> None:
    """真实素材：标题尾部拼了"…插入歌"这类杂质。"""
    it = ItemView(
        path="/m/h.flac", name="Hanabi　アニメ“リトルバスターズ！”挿入歌.flac", duration_sec=304,
        tags=Tags(title="Hanabi　アニメ“リトルバスターズ！”挿入歌", artist="Lia", album="Some Album"),
    )
    cands = [cand("Some Album", 304, date="2008")]
    d = decide(it, cands, model_out(0, 0.8, title="Hanabi", artist="Lia", album="Some Album"))
    assert d.action == "cleanup"
    changes = {c.field: c for c in d.changes}
    assert changes["title"].after == "Hanabi"
    assert changes["title"].kind == "cleanup"


def test_no_op_when_everything_is_already_correct() -> None:
    it = ItemView(
        path="/m/a.flac", name="a.flac", duration_sec=275,
        tags=Tags(title="サンプル年", artist="Kagero", album="サンプル、テスト盤", date="2020",
                  genre="Rock"),
    )
    cands = [cand("サンプル、テスト盤", 275, date="2020-12-16")]
    d = decide(it, cands, model_out(0, 0.85, title="サンプル年", artist="Kagero",
                                    album="サンプル、テスト盤", date="2020", genre="Rock"))
    assert d.action == "no_op"
    assert d.changes == []


def test_no_evidence_when_no_candidates() -> None:
    it = ItemView(path="/m/a.flac", name="a.flac", duration_sec=100, tags=Tags())
    d = decide(it, [], None)
    assert d.action == "no_evidence"
    assert verdict_of(d) == "无法处理（没有候选）"
