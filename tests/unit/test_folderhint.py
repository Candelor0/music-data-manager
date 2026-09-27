"""目录名清洗测试 —— 用例取自真实样例库。"""

from __future__ import annotations

import pytest

from mds.core.folderhint import folder_hint, strip_format_brackets

# (目录名, 组内艺术家线索, 期望的清洗结果) —— 真实样本
REAL_CASES = [
    ("[HR]Don't be afraid!", "Glitter Green", "Don't be afraid!"),
    ("[HR]見本の夜に", "Kagero", "見本の夜に"),
    (
        "[220630]Kagero - 見本楽曲集[48kHz／24bit][FLAC]",
        "Kagero",
        "見本楽曲集",
    ),
    (
        "Kagero 6thアルバム「見本アルバム」[FLAC 48kHz／24bit]",
        "Kagero",
        "見本アルバム",
    ),
    ("陆离 - 假想专辑2000", "陆离", "假想专辑2000"),
    (
        "Fear, and Loathing in Las Vegas - Let Me Hear",
        "Fear, and Loathing in Las Vegas",
        "Let Me Hear",
    ),
    ("Star Beats - Fresh Start! [FLAC 24bit ⁄ 96kHz]", "Star Beats", "Fresh Start!"),
    ("ERA", "RAISE A SUILEN", "ERA"),
    ("乌云典当记", "", "乌云典当记"),
]


@pytest.mark.parametrize(("raw", "artist", "expected"), REAL_CASES)
def test_folder_hint_on_real_library(raw: str, artist: str, expected: str) -> None:
    assert folder_hint(raw, artist) == expected


def test_keeps_parentheses_that_are_part_of_album_name() -> None:
    """真实样本：括号里就是专辑名的一部分，绝不能剥。"""
    raw = "[Hi-Res]Star Beats - 見本曲(テスト バージョン)最高(いこう)!"
    assert folder_hint(raw, "Star Beats") == "見本曲(テスト バージョン)最高(いこう)!"
    assert strip_format_brackets("見本曲(テスト バージョン)最高(いこう)!") == (
        "見本曲(テスト バージョン)最高(いこう)!"
    )


@pytest.mark.parametrize(
    "raw",
    [
        "[FLAC]",
        "[320K]",
        "[20220630]",
    ],
)
def test_returns_original_when_nothing_meaningful_left(raw: str) -> None:
    """安全边界：只做减法；剥完剩不下东西就返回原值，绝不返回空。"""
    assert folder_hint(raw) == raw


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("专辑 [FLAC 48kHz／24bit]", "专辑"),
        ("专辑 [320K]", "专辑"),
        ("专辑 [Hi-Res]", "专辑"),
        ("专辑 [HR]", "专辑"),
        ("专辑 [220630]", "专辑"),
        ("专辑 [Remastered 2011]", "专辑"),  # 版本词也按格式类剥掉
        ("专辑 (2019)", "专辑"),
    ],
)
def test_strip_format_brackets(raw: str, expected: str) -> None:
    assert strip_format_brackets(raw) == expected


def test_no_artist_hint_leaves_prefix() -> None:
    """没有艺术家线索时，不猜前缀（宁可留着，也不乱剥）。"""
    assert folder_hint("陆离 - 假想专辑2000", "") == "陆离 - 假想专辑2000"


def test_artist_prefix_requires_match() -> None:
    """前缀必须与已知艺术家匹配才剥 —— 防止把 `Album - Part 2` 当成艺术家前缀。"""
    assert folder_hint("Album - Part 2", "别的艺术家") == "Album - Part 2"
