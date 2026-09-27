"""默认勾选规则测试。

这是**本版本最要紧的一组断言**：说了「凡是会改动你已有标签的
一律默认不勾」，所以这里要逐条证明它成立。
"""

from __future__ import annotations

import pytest

from mds.core.cleaning import propose_changes
from mds.core.models import Candidate, CleanedTags, Decision, TagChange, Tags, WritePlan
from mds.core.selectrules import (
    REASON_ALBUM,
    REASON_KIND,
    REASON_NOT_ALLOWED,
    REASON_OK,
    assert_only_safe,
    check,
    default_checked_ids,
    is_checked,
)


def dec(action: str = "fill_missing", **kw) -> Decision:
    return Decision(action=action, **kw)  # type: ignore[arg-type]


def plan(changes=(), *, allowed: bool = True, source: str = "fill_missing") -> WritePlan:
    changes = list(changes)
    return WritePlan(allowed=allowed, changes=changes, source=source)


FILL = TagChange(field="date", before="", after="2022", kind="fill")
CONSENSUS = TagChange(field="genre", before="", after="JPop", kind="consensus")
ALBUM_CLEANUP = TagChange(
    field="album", before="假想专辑（实体版）", after="假想专辑", kind="cleanup"
)
TITLE_CLEANUP = TagChange(field="title", before="曲名【杂质】", after="曲名", kind="cleanup")
NORMALIZE = TagChange(field="title", before="Song  Name", after="Song Name", kind="normalize")
ALBUM_SWAP = TagChange(field="album", before="假想专辑2000(珍藏版)", after="假想专辑2000年", kind="normalize")


# ── 5 个条件，逐个证明不可绕过 ────────────────────────────
def test_safe_fill_is_checked() -> None:
    verdict = check(dec("fill_missing"), plan([FILL]))
    assert verdict.checked is True
    assert verdict.reason == REASON_OK


def test_consensus_fill_is_checked() -> None:
    assert is_checked(dec("fill_missing"), plan([CONSENSUS])) is True


def test_plan_not_allowed_is_not_checked() -> None:
    verdict = check(dec("fill_missing"), plan([FILL], allowed=False))
    assert verdict.checked is False
    assert verdict.reason == REASON_NOT_ALLOWED


def test_keep_existing_safe_part_is_checked_but_stays_visible() -> None:
    """`keep_existing` = "AI 想换发行版但给你保留现有的"，换名那项已被丢掉，
    计划里只剩安全填空/清洗 —— 按既定规则，这部分该默认勾。

    它仍然留在 🟠 组里显示（提醒"AI 有不同判断，可能想看一眼"），
    但不阻止那几条安全的填空被一起写掉。
    """
    assert is_checked(dec("keep_existing"), plan([FILL])) is True
    assert is_checked(dec("keep_existing", alternative=Candidate(album="X")), plan([FILL])) is True
    # 但换成另一个发行版的值，绝不默认勾
    assert is_checked(dec("keep_existing"), plan([ALBUM_SWAP])) is False
    assert is_checked(dec("keep_existing"), plan([NORMALIZE])) is False


def test_ask_user_is_never_checked() -> None:
    assert is_checked(dec("ask_user"), plan([FILL])) is False


@pytest.mark.parametrize("action", ["no_op", "no_evidence"])
def test_other_actions_are_not_checked(action: str) -> None:
    assert is_checked(dec(action), plan([FILL])) is False


def test_conflict_does_not_block_but_is_flagged() -> None:
    """冲突说的是**另一个字段**（它有值，不会被写），所以不该拦默认勾选。

    初稿把"有冲突"当成拦条，结果这类条目掉出 🟢 组、又不符合 🟡/🟠，
    最后从待办里消失（实测踩到）。现在照旧可以默认勾，但理由里写明冲突。
    """
    verdict = check(dec("fill_missing", conflicts=["genre"]), plan([FILL]))
    assert verdict.checked is True
    assert "不一致" in verdict.reason


# ── normalize（写法统一）必须排除 ────────────────────────
def test_normalize_is_never_checked() -> None:
    """写法统一会改动你亲手写的值 —— 语义没变也不自动勾。"""
    verdict = check(dec("cleanup", changes=[NORMALIZE]), plan([NORMALIZE]))
    assert verdict.checked is False
    assert verdict.reason == REASON_KIND


def test_album_swap_is_never_checked() -> None:
    """把专辑名换成另一个发行版 —— 这是不能碰的红线。"""
    verdict = check(dec("cleanup"), plan([ALBUM_SWAP]))
    assert verdict.checked is False
    assert verdict.reason in (REASON_ALBUM, REASON_KIND)


def test_album_fill_with_nonempty_before_is_not_checked() -> None:
    """防御性：fill 理论上 before 必为空；万一有人造出非空的，也不能默认勾。"""
    sneaky = TagChange(field="album", before="旧专辑", after="新专辑", kind="fill")
    assert is_checked(dec("fill_missing"), plan([sneaky])) is False


# ── 安全清洗可以勾（既定规则）────────────────────
def test_album_version_cleanup_is_checked() -> None:
    """「假想专辑（实体版）」→「假想专辑」是已验证过的安全清洗。"""
    verdict = check(dec("cleanup"), plan([ALBUM_CLEANUP]))
    assert verdict.checked is True, "安全清洗属于同意的默认勾选范围"
    assert verdict.reason == REASON_OK


def test_title_cleanup_is_checked() -> None:
    assert is_checked(dec("cleanup"), plan([TITLE_CLEANUP])) is True


def test_mixed_fill_and_cleanup_is_checked() -> None:
    assert is_checked(dec("fill_missing"), plan([FILL, CONSENSUS, TITLE_CLEANUP])) is True


def test_one_unsafe_change_poisons_the_whole_item() -> None:
    """只要有一项不安全，整条都不默认勾（宁可让用户多点一次）。"""
    assert is_checked(dec("fill_missing"), plan([FILL, NORMALIZE])) is False
    assert is_checked(dec("fill_missing"), plan([FILL, ALBUM_CLEANUP])) is True


# ── 空计划 / 缺对象 ──────────────────────────────────────
def test_empty_plan_is_not_checked() -> None:
    assert is_checked(dec("fill_missing"), plan([])) is False


def test_missing_decision_or_plan_is_not_checked() -> None:
    assert is_checked(None, plan([FILL])) is False
    assert is_checked(dec("fill_missing"), None) is False


# ── 核心安全断言 ─────────────────────────────────────────
def test_assert_only_safe_accepts_pure_fill_and_cleanup() -> None:
    assert_only_safe([FILL, CONSENSUS, TITLE_CLEANUP, ALBUM_CLEANUP])  # 不抛


@pytest.mark.parametrize(
    "change",
    [
        NORMALIZE,
        ALBUM_SWAP,
        TagChange(field="artist", before="老艺术家", after="新艺术家", kind="fill"),
        TagChange(field="date", before="1999", after="2022", kind="normalize"),
    ],
)
def test_assert_only_safe_rejects_anything_that_overwrites(change: TagChange) -> None:
    with pytest.raises(AssertionError, match="可能覆盖已有值"):
        assert_only_safe([change])


def test_every_checked_plan_passes_the_safety_assertion() -> None:
    """把各种计划过一遍：凡是判定为默认勾选的，都必须通过安全断言。

    这条是"数学保证"的化身：默认勾选 → 只会填空或去杂质。
    """
    candidates = [
        plan([FILL]),
        plan([CONSENSUS]),
        plan([TITLE_CLEANUP]),
        plan([ALBUM_CLEANUP]),
        plan([FILL, CONSENSUS]),
        plan([FILL, NORMALIZE]),
        plan([ALBUM_SWAP]),
        plan([FILL], allowed=False),
    ]
    for candidate in candidates:
        if is_checked(dec("fill_missing"), candidate):
            assert_only_safe(list(candidate.changes))


# ── 真实案例 ─────────────────────────────────────────────
def test_real_case_pu_shu_album_is_not_default_checked() -> None:
    """陆离那张：标签 `假想专辑2000(珍藏版)` 比候选 `假想专辑2000年` 更准（实测中确认过）。

    「珍藏版」不在版本词表里（只有「典藏」），所以不会被误剥 —— 实测确认。
    """
    current = Tags(title="New Boy", artist="陆离", album="假想专辑2000(珍藏版)", date="2000", genre="Rock")
    chosen = Candidate(release_mbid="rel-x", album="假想专辑2000年", date="2003-11")
    changes = propose_changes(
        current, chosen, CleanedTags(), allow_album_change=False, candidate_albums=["假想专辑2000年"]
    )
    # 这张专辑名不该被动（既没换成候选，也没被误当成版本说明剥掉）
    assert [c for c in changes if c.field == "album"] == []


def test_real_case_orion_cleanup_would_be_checked() -> None:
    """「假想专辑（实体版）」→「假想专辑」：有依据的安全清洗，可默认勾。"""
    current = Tags(title="假想专辑", artist="赵雷", album="假想专辑（实体版）")
    chosen = Candidate(release_mbid="rel-y", album="假想专辑")
    changes = propose_changes(
        current, chosen, CleanedTags(), allow_album_change=False, candidate_albums=["假想专辑"]
    )
    album_changes = [c for c in changes if c.field == "album"]
    assert album_changes and album_changes[0].kind == "cleanup"
    assert is_checked(dec("cleanup"), plan(album_changes)) is True


def test_real_case_filling_missing_year_is_checked() -> None:
    current = Tags(title="某首", artist="某人", album="某专辑")
    chosen = Candidate(release_mbid="rel-z", album="某专辑", date="2022-06-29")
    changes = propose_changes(current, chosen, CleanedTags())
    assert [c.kind for c in changes] == ["fill"]
    assert is_checked(dec("fill_missing"), plan(changes)) is True


# ── 批量 ─────────────────────────────────────────────────
def test_default_checked_ids() -> None:
    entries = [
        (1, dec("fill_missing"), plan([FILL])),
        (2, dec("keep_existing"), plan([FILL])),      # 安全的填空 → 勾
        (3, dec("cleanup"), plan([ALBUM_CLEANUP])),
        (4, dec("ask_user"), plan([FILL])),           # 要用户先选 → 不勾
        (5, None, None),
        (6, dec("keep_existing"), plan([ALBUM_SWAP])),  # 换发行版 → 不勾
    ]
    assert default_checked_ids(entries) == [1, 2, 3]
