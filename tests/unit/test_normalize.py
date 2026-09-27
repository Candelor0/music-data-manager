"""归一化与标题清洗测试。

用例全部取自 POC 真实素材（64 首 FLAC），不是编的。
"""

from __future__ import annotations

import pytest

from mds.core.normalize import (
    album_cleanup_kind,
    field_kind,
    normalize_artist,
    normalize_text,
    strip_album_qualifier,
    strip_disc_suffix,
    strip_title_junk,
    title_similarity,
)

# (原始标题, 期望清洗结果) —— 取自 POC 实测的 10 条"AI 更干净"素材
JUNK_CASES = [
    ("Hanabi　アニメ“リトルバスターズ！”挿入歌", "Hanabi"),
    ("雨のち晴れ　“リトルバスターズ！”エンディングテーマ", "雨のち晴れ"),
    ("Song for friends　“リトルバスターズ！”エンディングテーマ", "Song for friends"),
    ("遥か彼方　“リトルバスターズ！”挿入歌", "遥か彼方"),
    ("ピクルスをおいしくするつくりかた　“二木 佳奈多”キャラクターソング", "ピクルスをおいしくするつくりかた"),
    ("Alicemagic ～Rockstar ver.～　“リトルバスターズ！EX”エンディングテーマ", "Alicemagic ～Rockstar ver.～"),
    ("Little Busters! ～TV animation ver.～アニメ“リトルバスターズ！”オープニングテーマ", "Little Busters! ～TV animation ver.～"),
    ("Saya's Song　“リトルバスターズ！EX”朱鷺戸 沙耶エンディングテーマ", "Saya's Song"),
]

# 绝对不能被误伤的正当曲名
UNTOUCHED_CASES = [
    "見本曲",
    "愛のテーマ",  # 单干的「テーマ」是正当曲名，绝不能剥
    "Interlude",
    "リトルバスターズ！Synth-Magnetic Megamix",
    "Baby ，До свидания",
    "假想之路",
    "Another Theme Song",
]


@pytest.mark.parametrize(("raw", "expected"), JUNK_CASES)
def test_strip_title_junk_removes_suffix(raw: str, expected: str) -> None:
    assert strip_title_junk(raw) == expected


@pytest.mark.parametrize("raw", UNTOUCHED_CASES)
def test_strip_title_junk_keeps_legit_titles(raw: str) -> None:
    assert strip_title_junk(raw) == raw


@pytest.mark.parametrize(
    ("a", "b"),
    [
        # POC 实测：不同数据源用不同连字符/波浪号/全角星号，必须归一为同一个
        ("ゆら・ゆらRing-Dong-Dance", "ゆら・ゆらRing‐Dong‐Dance"),
        ("しゅわりん☆どり～みん", "しゅわりん☆どり〜みん"),
        ("Lime*Notes", "Lime＊Notes"),
        ("リトルバスターズ！", "リトルバスターズ!"),
    ],
)
def test_normalize_text_makes_variants_equal(a: str, b: str) -> None:
    assert normalize_text(a) == normalize_text(b)


def test_normalize_artist_handles_article_suffix() -> None:
    assert normalize_artist("Beatles, The") == normalize_artist("The Beatles")
    assert normalize_artist("the beatles") == normalize_artist("The Beatles")


def test_title_similarity_bounds() -> None:
    assert title_similarity("Yesterday", "yesterday") == 1.0
    assert title_similarity("", "x") == 0.0
    assert 0 < title_similarity("Little Busters!", "Little Busters! TV ver.") < 1


def test_field_kind_classification() -> None:
    # 原值为空 → fill
    assert field_kind("Rock", "") == "fill"
    # 同值不同写法 → normalize
    assert field_kind("hip hop", "Hip-Hop") == "normalize"
    # 含可剥离杂质 → cleanup
    assert field_kind("Hanabi", "Hanabi　アニメ“リトルバスターズ！”挿入歌") == "cleanup"
    # 实质不同的值 → 不允许我们自动改
    assert field_kind("NEON SINGLE", "NORTHERN LIGHTS") is None
    # 无需改动
    assert field_kind("same", "same") is None


# ── 专辑尾部版本说明的剥离─────────
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("假想专辑（实体版）", "假想专辑"),
        ("专辑 (Deluxe Edition)", "专辑"),
        ("专辑 [Remastered 2011]", "专辑"),
        ("专辑（初回限定盤）", "专辑"),
        ("Album (Deluxe)", "Album"),
    ],
)
def test_strip_album_qualifier_removes_packaging_notes(raw: str, expected: str) -> None:
    assert strip_album_qualifier(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "假想专辑",
        "专辑（Live）",             # 内容差异（现场版），不是装帧差异 → 绝不能剥
        "专辑（现场版）",
        "专辑 (Acoustic Version)",
        "专辑（演唱会版）",
        "专辑",
    ],
)
def test_strip_album_qualifier_keeps_content_variants(raw: str) -> None:
    assert strip_album_qualifier(raw) == raw


def test_album_cleanup_kind_requires_evidence() -> None:
    # 剥完之后能在候选里找到 → cleanup
    assert album_cleanup_kind("假想专辑", "假想专辑（实体版）") == "cleanup"
    # 候选里没有这个名字 → 不动（避免臆造）
    assert album_cleanup_kind("假想之路", "假想专辑（实体版）") is None
    # 没有括号可剥
    assert album_cleanup_kind("假想专辑", "假想专辑") is None
    # 内容类括号不剥
    assert album_cleanup_kind("专辑", "专辑（Live）") is None


# ── 多碟专辑：从专辑名里拆碟号─────────
@pytest.mark.parametrize(
    ("raw", "album", "disc"),
    [
        ("リトルバスターズ！パーフェクトボーカルコレクション Disc 2",
         "リトルバスターズ！パーフェクトボーカルコレクション", 2),
        ("Album CD 1", "Album", 1),
        ("Album (Disc 2)", "Album", 2),
        ("Album [Disc 02]", "Album", 2),
        ("专辑 碟2", "专辑", 2),
        ("Album Disc 10", "Album", 10),
    ],
)
def test_strip_disc_suffix(raw: str, album: str, disc: int) -> None:
    assert strip_disc_suffix(raw) == (album, disc)


@pytest.mark.parametrize(
    "raw",
    [
        "假想专辑",
        "NORTHERN LIGHTS",
        "Disc 2",              # 拆完什么都不剩 → 不动
        "Album Disc 0",        # 编号不合理
        "Album Disc 99",
        "The CD Collection",   # 不是"碟号"后缀
        "Album Discover",      # 只是碰巧以 Disc 开头
    ],
)
def test_strip_disc_suffix_does_not_overreach(raw: str) -> None:
    album, disc = strip_disc_suffix(raw)
    assert album == raw and disc is None
