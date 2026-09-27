"""文本归一化。纯函数。

POC 教训（见 POC结果-P4）：同一首歌在不同数据源里会用不同的连字符（ASCII `-`、U+2010 `‐`）、
波浪号（U+301C `〜`、U+FF5E `～`）、全角星号 `＊` 与半角 `*`。这些差异对"是不是同一首歌"
毫无意义，却会让字符串直接比较失败。因此比对用的归一化只保留字母/数字/汉字。
"""

from __future__ import annotations

import re
import unicodedata

# 艺术家别名：常见的 "X, The" → "The X" 形式
_ARTICLE_SUFFIX = re.compile(r"^(?P<name>.+?),\s*(?P<article>the|a|an)$", re.IGNORECASE)

# 标题尾部杂质：**必须带引号/括号块**才认为是杂质后缀，其余一律不动。
#
# 结构：[可选媒体词] + [引号/括号块] + [可选说明] + [说明词]
# 例：'曲名　“作品名”エンディングテーマ' ／ '曲名　アニメ“作品名”挿入歌'
#
# 为什么强制要求引号块：
#   - 不收单干的「テーマ」——像「愛のテーマ」这种正当曲名会被误伤；
#   - 不靠“媒体词 + 说明词”单独触发——否则 'Little Busters! ～TV animation ver.～' 里
#     的 'TV' 会被当媒体词，把 'TV animation ver.～' 整段剥掉（实测踩过）。
_MARKERS = (
    r"主題歌|主题曲|主題曲|挿入歌|挿入曲|エンディングテーマ|オープニングテーマ"
    r"|エンディング|オープニング|キャラクターソング|キャラソン|イメージソング|テーマソング"
)
# 排除了 U+301C 等波浪号在媒体词与引号之间的情形由“可选媒体词”处理
_TRAILING_JUNK = re.compile(
    rf"""
    [\s　]*
    (?:アニメ|劇場版|映画|TV|ゲーム)?
    [“"『「（(\[][^”"』」）)\]]{{1,80}}[”"』」）)\]]
    [\s　]*[^()]{{0,40}}?
    (?:{_MARKERS})
    \s*$
    """,
    re.VERBOSE,
)


def normalize_text(s: str) -> str:
    """比对用归一化：NFKC + 小写 + 只保留字母/数字/汉字。"""
    t = unicodedata.normalize("NFKC", s or "").lower()
    return "".join(ch for ch in t if ch.isalnum())


def normalize_artist(s: str) -> str:
    """艺术家归一化：先剥 "X, The" → "The X"，再 normalize_text。"""
    raw = (s or "").strip()
    m = _ARTICLE_SUFFIX.match(raw)
    if m:
        raw = f"{m.group('article')} {m.group('name')}".strip()
    return normalize_text(raw)


def title_similarity(a: str, b: str) -> float:
    """0~1 的粗糙相似度：完全相等 1.0；一方包含另一方按长度比；否则按公共字符比。"""
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return min(len(na), len(nb)) / max(len(na), len(nb))
    common = sum(1 for ch in set(na) if ch in nb)
    return common / max(len(set(na)), len(set(nb)))


def strip_title_junk(title: str) -> str:
    """剥掉标题尾部的"主题曲/插曲"类杂质。只做减法，不改写语义。

    例：'Hanabi　アニメ"リトルバスターズ！"挿入歌' → 'Hanabi'
         '雨のち晴れ　"リトルバスターズ！"エンディングテーマ' → '雨のち晴れ'
    """
    t = (title or "").strip()
    if not t:
        return ""
    cleaned = _TRAILING_JUNK.sub("", t).strip()
    # 安全阀：必须剩下实质内容（≥2 个字符），否则不剥。
    # 不能用"长度比"做阀值：短标题（如 'Hanabi' + 一长串日文后缀）本来就会被砍掉一大半。
    if len(normalize_text(cleaned)) < 2:
        return t
    return cleaned


# 专辑尾部括号说明：只到"版本/装帧"类说明，不碰"内容"类描述。
#
# 为什么区分：
#   （实体专辑版）/ (Deluxe Edition) → 只是装帧/版本说法，剥掉不丢信息 → 可以剥
#   （Live）/ （现场版）/ (Remix)   → 描述的是"另一个版本的内容" → 剥掉会丢信息 → **不动**
EDITION_WORDS = (
    "实体", "数字", "數位", "豪华", "豪華", "限定", "初回", "通常", "完全",
    "典藏", "纪念", "紀念", "复刻", "復刻", "重制", "重置", "特别版", "特別版",
    "deluxe", "edition", "remaster", "reissue", "expanded",
    "limited", "explicit", "mono", "stereo", "special",
)
_ALBUM_TAIL = re.compile(r"[\s　]*[（(\[][^）)\]]{1,60}[）)\]]\s*$")


def strip_album_qualifier(album: str) -> str:
    """去掉专辑名尾部的**装帧/版本**类括号说明。

    例：'假想专辑（实体版）' → '假想专辑'
         '专辑 (Deluxe Edition)' → '专辑'
         '专辑（Live）' → **不动**（那是内容差异，不是装帧差异）
    """
    text = (album or "").strip()
    if not text:
        return text
    match = _ALBUM_TAIL.search(text)
    if not match:
        return text
    inner = match.group(0).lower()
    if not any(word in inner for word in EDITION_WORDS):
        return text
    stripped = text[: match.start()].strip()
    return stripped if len(normalize_text(stripped)) >= 2 else text


def album_cleanup_kind(expected: str, actual: str) -> str | None:
    """判断专辑字段能不能安全地"去括号"。

    expected 是目标专辑名（来自候选），actual 是文件现有值。
    返回 'cleanup' 表示可安全剥；None 表示不碰。
    """
    e, a = (expected or "").strip(), (actual or "").strip()
    if not a or not e or e == a:
        return None
    stripped = strip_album_qualifier(a)
    if stripped == a:
        return None
    if normalize_text(stripped) == normalize_text(e):
        return "cleanup"
    return None


# 多碟专辑：album 里带 "Disc 2" / "CD 2" / "碟 2" 这类后缀
#
# 标准写法是：**专辑名不带 Disc**，碟号放在 `discnumber` 字段里。
# 所以这里只做"拆两半"，不丢弃 —— 上层会同时把碟号写进 discnumber。
_DISC_SUFFIX = re.compile(
    r"(?:^|[\s　（(\[])"
    r"(?:disc|disk|cd|ディスク|碟)\s*0?(\d{1,2})"
    r"[\s　）)\]]*$",
    re.IGNORECASE,
)


def strip_disc_suffix(album: str) -> tuple[str, int | None]:
    """把 '专辑名 Disc 2' 拆成 ('专辑名', 2)。

    例：'リトルバスターズ！パーフェクトボーカルコレクション Disc 2'
        → ('リトルバスターズ！パーフェクトボーカルコレクション', 2)
    没有碟号后缀时原样返回 (album, None)。
    """
    text = (album or "").strip()
    if not text:
        return text, None
    match = _DISC_SUFFIX.search(text)
    if not match:
        return text, None
    try:
        number = int(match.group(1))
    except (TypeError, ValueError):
        return text, None
    if not 1 <= number <= 30:
        return text, None
    stripped = text[: match.start()].strip()
    if len(normalize_text(stripped)) < 2:
        return text, None
    return stripped, number


def field_kind(expected: str, actual: str) -> str | None:
    """判断某个字段属于哪种改动：fill（补空）/ cleanup（去杂质）/ normalize（写法统一）/ None（无需改）。

    注意：仅在字面不同但归一化相同（写法差异）时算 normalize；
    字面不同且归一化也不同时，**不算**我们可以自动决定的改动，返回 None。
    """
    e, a = (expected or "").strip(), (actual or "").strip()
    if a == e:
        return None
    if not a:
        return "fill" if e else None
    if not e:
        return None
    if normalize_text(e) == normalize_text(a):
        return "normalize"
    stripped = strip_title_junk(a)
    if stripped != a and normalize_text(stripped) == normalize_text(e):
        return "cleanup"
    return None
