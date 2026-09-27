"""写入计划测试 —— R2 / R3 / R4 在写入路径上的落实。

用例基于 真实样例里人工判断过的条目。
"""

from __future__ import annotations

import pytest

from mds.core.decide import decide
from mds.core.models import Candidate, CleanedTags, ItemView, Suggestion, Tags
from mds.core.writeplan import (
    CHOICE_ADOPTED_AI,
    CHOICE_SKIPPED,
    build_write_plan,
)


def cand(album: str, length: int | None, *, date: str = "", rtype: str = "") -> Candidate:
    return Candidate(release_mbid=f"m-{album}-{length}", album=album, date=date,
                     track_length_sec=length, release_group=album, rg_type=rtype)


def model_out(index: int, *, title: str = "", artist: str = "", album: str = "",
              date: str = "", genre: str = "", confidence: float = 0.8) -> Suggestion:
    return Suggestion(
        chosen_index=index, confidence=confidence, reason="测试",
        cleaned=CleanedTags(title=title, artist=artist, album=album, date=date, genre=genre),
    )


# ── R2：已有专辑标签 → 默认不覆盖 ────────────────────────────
def _conflict_case() -> tuple[ItemView, list[Candidate], Suggestion]:
    """实测样例第 4 条：文件专辑标签 NORTHERN LIGHTS 是对的，AI 却选了《NEON SINGLE》。"""
    item = ItemView(
        path="/m/06.flac", name="06 Keep the Heat.flac", duration_sec=205,
        tags=Tags(title="Keep the Heat", artist="Fear, and Loathing in Las Vegas",
                  album="NORTHERN LIGHTS", date=""),  # 年份为空，可安全补全
    )
    cands = [cand("NEON SINGLE", 205, date="2018-05-02", rtype="Single"),
             cand("NORTHERN LIGHTS", 206, date="2019-12-04", rtype="Album")]
    out = model_out(0, title="Keep the Heat", artist="Fear, and Loathing in Las Vegas",
                    album="NEON SINGLE", date="2018", genre="Metalcore")
    return item, cands, out


def test_r2_default_does_not_touch_album() -> None:
    item, cands, out = _conflict_case()
    decision = decide(item, cands, out)
    assert decision.action == "keep_existing"

    plan = build_write_plan(item, decision, user_choice=None)
    assert all(c.field != "album" for c in plan.changes), "默认绝不能改专辑标签"
    assert plan.touches_album is False
    # 但空白字段（年份）应当被补上
    assert any(c.field == "date" for c in plan.changes)


def test_r2_adopt_ai_changes_only_album() -> None:
    item, cands, out = _conflict_case()
    decision = decide(item, cands, out)
    plan = build_write_plan(item, decision, user_choice=CHOICE_ADOPTED_AI)
    fields = {c.field for c in plan.changes}
    assert "album" in fields
    assert plan.touches_album is True
    assert [c.after for c in plan.changes if c.field == "album"] == ["NEON SINGLE"]


def test_r2_adopt_ai_requires_alternative() -> None:
    from mds.core.models import Decision

    item, _, _ = _conflict_case()
    # 手工构造一个"冲突但没有可采纳候选"的决策
    broken = Decision(action="keep_existing", alternative=None, chosen=None)
    plan = build_write_plan(item, broken, user_choice=CHOICE_ADOPTED_AI)
    assert plan.allowed is False
    assert "未给出可采纳" in plan.reason


# ── R3：未选候选前不许写 ────────────────────────────────────
def _ask_user_case() -> tuple[ItemView, list[Candidate], Suggestion]:
    item = ItemView(
        path="/m/a.flac", name="04.もういちど テスト.flac", duration_sec=260,
        tags=Tags(title="もういちど テスト", artist="Lime*Notes"),  # 无专辑标签
    )
    cands = [cand("LIVE ANTHEM", 260, date="2021-05-19", rtype="Album"),
             cand("もういちど テスト", 261, date="2018-08-08", rtype="Single")]
    out = model_out(1, album="もういちど テスト", date="2018", genre="Anison")
    return item, cands, out


def test_r3_no_write_before_user_chooses() -> None:
    item, cands, out = _ask_user_case()
    decision = decide(item, cands, out)
    assert decision.action == "ask_user"

    plan = build_write_plan(item, decision, user_choice=None)
    assert plan.allowed is False
    assert "需先确认" in plan.reason
    assert plan.changes == []


def test_r3_after_user_choice_album_is_filled() -> None:
    item, cands, out = _ask_user_case()
    decision = decide(item, cands, out)
    plan = build_write_plan(item, decision, user_choice="picked")
    assert plan.allowed is True
    album = [c for c in plan.changes if c.field == "album"]
    assert album and album[0].after == "もういちど テスト"


def test_r3_does_not_overwrite_existing_album() -> None:
    """若文件已有专辑标签，ask_user 分支也绝不覆盖它。"""
    item = ItemView(
        path="/m/a.flac", name="a.flac", duration_sec=260,
        tags=Tags(title="T", artist="A", album="我自己攒的合集"),
    )
    cands = [cand("别的专辑", 260, date="2018"), cand("另一个", 261, date="2019")]
    decision = decide(item, cands, model_out(0))
    plan = build_write_plan(item, decision, user_choice="picked")
    assert all(c.field != "album" for c in plan.changes)


# ── 通用安全约束 ────────────────────────────────────────────
def test_no_op_produces_no_plan() -> None:
    item = ItemView(
        path="/m/a.flac", name="a.flac", duration_sec=275,
        tags=Tags(title="サンプル年", artist="Kagero", album="サンプル、テスト盤",
                  date="2020", genre="Rock"),
    )
    cands = [cand("サンプル、テスト盤", 275, date="2020-12-16")]
    out = model_out(0, title="サンプル年", artist="Kagero",
                    album="サンプル、テスト盤", date="2020", genre="Rock")
    decision = decide(item, cands, out)
    plan = build_write_plan(item, decision, user_choice=None)
    assert plan.allowed is False
    assert plan.reason == "无需改动"


def test_never_clears_non_empty_value() -> None:
    """AI 没给出值时，绝不能把用户已有的值清空。"""
    item = ItemView(
        path="/m/a.flac", name="a.flac", duration_sec=200,
        tags=Tags(title="歌名", artist="歌手", album="专辑", date="1999", genre="Rock"),
    )
    cands = [cand("专辑", 200, date="1999")]
    out = model_out(0, title="", artist="", album="", date="", genre="")  # 全空
    decision = decide(item, cands, out)
    plan = build_write_plan(item, decision, user_choice=None)
    assert plan.allowed is False
    assert plan.changes == []


def test_missing_cleaned_does_not_blank_existing() -> None:
    """没有 cleaned 数据时，目标值来自候选；仍不得清空原值。"""
    item = ItemView(
        path="/m/a.flac", name="a.flac", duration_sec=200,
        tags=Tags(title="歌名", artist="歌手", album="", date=""),
    )
    cands = [cand("新专辑", 200, date="2001")]
    decision = decide(item, cands, None)  # 无模型输出
    plan = build_write_plan(item, decision, user_choice=None)
    for change in plan.changes:
        assert change.after != ""
        assert change.before != change.after


def test_user_skip_produces_no_plan() -> None:
    item, cands, out = _conflict_case()
    decision = decide(item, cands, out)
    plan = build_write_plan(item, decision, user_choice=CHOICE_SKIPPED)
    assert plan.allowed is False and plan.reason == "用户已跳过"


def test_stale_plan_is_dropped() -> None:
    """计划里的 before 与当前标签对不上（数据已变）→ 丢弃该改动。"""
    item = ItemView(
        path="/m/a.flac", name="a.flac", duration_sec=200,
        tags=Tags(title="T", artist="A", album="AL", date="2000", genre=""),
    )
    cands = [cand("AL", 200, date="2000")]
    out = model_out(0, genre="Jazz")
    decision = decide(item, cands, out)
    # 模拟：计划生成后，数据库里的 genre 已被别的流程改过
    plan = build_write_plan(item, decision, user_choice=None)
    assert [c.field for c in plan.changes] == ["genre"]
    # 现在把 before 改错，模拟数据漂移
    stale = plan.model_copy(update={"changes": [
        plan.changes[0].model_copy(update={"before": "已被别人改过"})
    ]})
    rebuilt = build_write_plan(
        item,
        decision.model_copy(update={"changes": stale.changes}),
        user_choice=None,
    )
    assert rebuilt.allowed is False


def test_only_whitelisted_fields_are_written() -> None:
    item = ItemView(
        path="/m/a.flac", name="a.flac", duration_sec=200,
        tags=Tags(title="T", artist="A", album="AL", albumartist="", tracknumber=""),
    )
    cands = [cand("AL", 200, date="2000")]
    out = model_out(0, genre="Rock")
    decision = decide(item, cands, out)
    plan = build_write_plan(item, decision, user_choice=None)
    assert all(c.field in ("title", "artist", "album", "date", "genre") for c in plan.changes)
    assert all(c.field not in ("albumartist", "tracknumber") for c in plan.changes)


def test_album_cleanup_survives_keep_existing() -> None:
    """早期 的要求：把「假想专辑（实体版）」改成「假想专辑」。

    难点：这一条同时存在两种专辑改变：
      - AI 想换成《假想之路》（单曲）→ 换发行版 → 必须丢掉（R2）
      - 去括号「（实体专辑版）」        → 只是装帧说明 → 应该保留
    """
    item = ItemView(
        path="/m/a.flac", name="陆离 - 假想之路.flac", duration_sec=301,
        tags=Tags(title="假想之路", artist="陆离", album="假想专辑（实体版）", date="2017"),
    )
    cands = [
        cand("假想之路", 302, date="2014-07-16", rtype="Single"),
        cand("假想专辑", 301, date="2017-04-30", rtype="Album"),
    ]
    out = model_out(0, title="假想之路", artist="陆离", album="假想之路")
    decision = decide(item, cands, out)
    assert decision.action == "keep_existing"

    plan = build_write_plan(item, decision, user_choice=None)
    albums = [c for c in plan.changes if c.field == "album"]
    assert albums, "去版本说明的安全清洗不该被 keep_existing 丢掉"
    assert albums[0].after == "假想专辑"
    assert albums[0].kind == "cleanup"
    assert plan.allowed is True


def test_album_replacement_still_requires_adoption() -> None:
    """即使有去括号，也不能顺带把专辑换成另一个发行版。"""
    item = ItemView(
        path="/m/a.flac", name="a.flac", duration_sec=301,
        tags=Tags(title="T", artist="A", album="假想专辑（实体版）"),
    )
    cands = [cand("完全不同的专辑", 302, date="2014")]
    decision = decide(item, cands, model_out(0, album="完全不同的专辑"))
    plan = build_write_plan(item, decision, user_choice=None)
    assert all(c.field != "album" for c in plan.changes)


def test_summary_is_readable() -> None:
    item = ItemView(path="/m/a.flac", name="a.flac", duration_sec=200,
                    tags=Tags(title="T", artist="A", album="AL"))
    cands = [cand("AL", 200, date="2000")]
    plan = build_write_plan(item, decide(item, cands, model_out(0, genre="Jazz")), None)
    assert "genre" in plan.summary()


def test_confidence_never_influences_plan() -> None:
    """R4：自报把握度高低不得改变计划。"""
    item = ItemView(path="/m/a.flac", name="a.flac", duration_sec=200,
                    tags=Tags(title="T", artist="A"))
    cands = [cand("X", 200, date="2001"), cand("Y", 201, date="2002")]
    low = build_write_plan(item, decide(item, cands, model_out(0, confidence=0.01)), None)
    high = build_write_plan(item, decide(item, cands, model_out(0, confidence=0.99)), None)
    assert low.allowed == high.allowed
    assert [c.field for c in low.changes] == [c.field for c in high.changes]


@pytest.mark.parametrize("action", ["no_op", "no_evidence"])
def test_terminal_actions_never_write(action: str) -> None:
    from mds.core.models import Decision

    item = ItemView(path="/m/a.flac", name="a.flac", duration_sec=100, tags=Tags())
    plan = build_write_plan(item, Decision(action=action), None)  # type: ignore[arg-type]
    assert plan.allowed is False


def test_disc_suffix_moves_to_discnumber() -> None:
    """早期记录：『Disc 2』这种后缀应该拆出去。

    关键：拆出去的同时必须把碟号写进 discnumber —— 否则信息就丢了。
    """
    item = ItemView(
        path="/m/a.flac",
        name="04.もういちど テスト.flac",
        duration_sec=260,
        tags=Tags(title="T", artist="A",
                  album="リトルバスターズ！パーフェクトボーカルコレクション Disc 2"),
    )
    cands = [cand("リトルバスターズ！パーフェクトボーカルコレクション", 260, date="2014")]
    decision = decide(item, cands, model_out(0))
    plan = build_write_plan(item, decision, user_choice=None)

    fields = {c.field: c for c in plan.changes}
    assert fields["album"].after == "リトルバスターズ！パーフェクトボーカルコレクション"
    assert fields["album"].kind == "cleanup"
    assert fields["discnumber"].after == "2"
    assert fields["discnumber"].kind == "fill"


def test_disc_suffix_never_overwrites_existing_discnumber() -> None:
    item = ItemView(
        path="/m/a.flac", name="a.flac", duration_sec=260,
        tags=Tags(title="T", artist="A", album="Album Disc 2", discnumber="5"),
    )
    cands = [cand("Album", 260, date="2014")]
    plan = build_write_plan(item, decide(item, cands, model_out(0)), user_choice=None)
    assert all(c.field != "discnumber" for c in plan.changes), "不得覆盖已有碟号"
    # 但专辑名仍然可以安全地去后缀
    assert [c.after for c in plan.changes if c.field == "album"] == ["Album"]


def test_disc_strip_can_be_turned_off() -> None:
    from mds.core.cleaning import propose_changes
    from mds.core.models import CleanedTags

    tags = Tags(title="T", artist="A", album="Album Disc 2")
    off = propose_changes(tags, cand("Album", 260), CleanedTags(), strip_disc=False)
    on = propose_changes(tags, cand("Album", 260), CleanedTags(), strip_disc=True)
    assert all(c.field != "album" for c in off)
    assert any(c.field == "album" for c in on)
