"""目录名 → 专辑线索（清洗）。纯函数。

背景（实测）：
    真实样例库里，专辑目录名往往带"包装"，不能直接当专辑名：

        [HR]Don't be afraid!                                  → Don't be afraid!
        [220630]Kagero - 見本楽曲集[48kHz／24bit][FLAC]   → 見本楽曲集
        Star Beats - Fresh Start! [FLAC 24bit ⁄ 96kHz]      → Fresh Start!
        Kagero 6thアルバム「見本アルバム」[FLAC …]         → 見本アルバム
        陆离 - 假想专辑2000                                          → 假想专辑2000

**但要非常克制**：下面这种括号里是专辑名本身的一部分，绝不能剥：

        見本曲(テスト バージョン)最高(いこう)!   ← 括号里就是专辑名

所以只剥"格式 / 品质 / 日期"类括号，其余一律保留。
"""

from __future__ import annotations

import re

from .normalize import EDITION_WORDS, normalize_text

#: 格式 / 品质类关键词（括号内容命中这些才剥）
_FORMAT_WORDS = re.compile(
    r"(?i)(hires|hi-?res|\bhr\b|flac|mp3|wav|ape|alac|aac|dsd|dsf|dff|mqa|"
    r"\bweb\b|lossless|无损|高解析|音质|\b\d{2,3}\s*k\b|\b\d{2,3}kbps\b|"
    r"bit|khz|hz|cd\s*rip|\bep\b|\bsingle\b)"
)

#: 纯日期类（如 [220630] / [20220630] / [2022-06-30]）
_DATE_ONLY = re.compile(r"^\s*\d{4,8}(\s*[-/.]\s*\d{1,2})*(\s*[-/.]\s*\d{1,2})?\s*$")

#: 各种括号对（含全角）
_BRACKET_BLOCK = re.compile(r"([\[\(（【])([^\]\)）】]{1,45})([\]\)）】])")

#: 「」『』包装（如 `… 6thアルバム「見本アルバム」`）
_WRAPPED_TITLE = re.compile(r"[「『]([^」』]{2,80})[」』]")


def _is_strippable(inner: str) -> bool:
    """括号内容是否属于"格式 / 品质 / 日期 / 版本"类。

    版本词表（Remastered / Deluxe / 初回限定 …）**复用 normalize.EDITION_WORDS**，
    避免同一件事在代码里出现两份不同的清单。
    """
    text = inner.strip()
    if not text:
        return False
    if _DATE_ONLY.match(text) or _FORMAT_WORDS.search(text):
        return True
    lowered = text.lower()
    return any(word in lowered for word in EDITION_WORDS)


def strip_format_brackets(name: str) -> str:
    """只剥"格式/品质/日期"类括号块，其余括号原样保留。

    剥完顺手整理空白（`专辑 [FLAC]` → `专辑`），否则会留下尾部空格。
    """

    def repl(match: re.Match[str]) -> str:
        return "" if _is_strippable(match.group(2)) else match.group(0)

    cleaned = _BRACKET_BLOCK.sub(repl, name)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def strip_artist_prefix(name: str, artist_hint: str) -> str:
    """剥掉 `{艺术家} - ` 前缀（仅当与已知艺术家匹配时）。"""
    artist = (artist_hint or "").strip()
    if not artist:
        return name
    pattern = re.compile(r"^\s*" + re.escape(artist) + r"\s*[-–—―‐]\s*", re.IGNORECASE)
    return pattern.sub("", name, count=1)


def folder_hint(folder_name: str, artist_hint: str = "") -> str:
    """把目录名清洗成专辑线索。

    顺序：取「」内内容 → 剥格式/日期括号 → 剥艺术家前缀 → 整理空白。
    安全边界：**只做减法**；若剥完剩余不足 2 个有效字符，返回原目录名。
    """
    original = (folder_name or "").strip()
    if not original:
        return ""

    text = original
    wrapped = _WRAPPED_TITLE.search(text)
    if wrapped:
        text = wrapped.group(1)
    text = strip_format_brackets(text)
    text = strip_artist_prefix(text, artist_hint)
    text = re.sub(r"\s{2,}", " ", text).strip()
    # 去掉剥完后残留的分隔符
    text = text.strip(" -–—―‐_").strip()

    if len(normalize_text(text)) < 2:
        return original
    return text
