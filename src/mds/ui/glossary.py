"""术语表：**保留**专业术语，但鼠标停上去给一句人话解释。

术语处理的三条规则
------------------------------
一开始想的是"术语全换白话"，但那太激进：术语用久了自然会懂，换掉反而更难查资料。

| 词的种类 | 例子 | 处理 |
| --- | --- | --- |
| 行业标准术语 | 专辑、曲目、标签、年份、流派 | **保留**（换了反而绕，而且查资料、看别的软件都是这套词） |
| 通用但偏专业 | 发行版、元数据 | **保留 + 悬停解释** |
| 我们自己造的词 | 同目录共识、批次、证据等级 | **换白话**（不是行业词，用户查不到也没必要学） |
| 纯内部实现词 | `plan_json`、`album_tag_match` | **界面上根本不出现** |

还有一条频率判断法：**这个词多久露一次面？** 每次用都会看到的，学得会，保留；
只在某个角落偶然出现一次的，根本没机会学会，就换白话。

技术说明（有个坑）
------------------
Qt 的 `QLabel` **不支持按词悬停** —— tooltip 是整块控件的，不是某几个字的。
所以带解释的术语一律放在**富文本区**（`QTextBrowser`）里，用链接实现：

    <a href="term:发行版" style="border-bottom:1px dotted">发行版</a>

鼠标悬停时 `highlighted` 信号触发 → 在**底部提示区**显示解释
（和浏览器左下角显示链接地址一个套路）。**不弹框、不遮挡内容。**

术语表有测试守着：**每个术语都必须有解释**；开关关闭后渲染成普通文字。
"""

from __future__ import annotations

from html import escape

#: 术语 → 一句人话解释。**只用通用/专业词，自造词不进这里**（那些要换成白话）
TERMS: dict[str, str] = {
    "发行版": "这张专辑的哪个版本。同一首歌可能被几十张专辑收录，"
    "内容差不多，但年份、国家、曲目数不一样。",
    "标签": "歌曲文件里存着的信息，比如曲名、艺术家、专辑、年份、流派。",
    "元数据": "同「标签」—— 文件里存着的曲名、艺术家、专辑、年份这些信息。",
    "MusicBrainz": "一个公开的音乐资料库，程序从那里查每首歌属于哪张专辑。",
    "AcoustID": "一项「听声音认歌」的服务：算出音频指纹，用它去反查是哪首歌。",
    "声学指纹": "从音频本身算出来的一串特征码，用来判断两段音频是不是同一首歌。",
    "曲目": "专辑里的一首歌。",
}

#: 内部叫法 → 界面上给用户看的说法
#:
#: 措辞标准：**不口语、也不堆术语**，
#: 像一款正常软件里的文字 —— 陈述、简洁、有分寸。
#: 反面例子：「这个文件夹里其他歌都这么标」（太口语，像内部备注）
#:          「同目录共识」（自造术语，用户没听过）
PLAIN: dict[str, str] = {
    "同目录共识": "同一文件夹内其他曲目",
    "批次": "批次",
    "证据等级": "依据",
    "待办": "待处理",
    "无需处理": "无需处理",
}


def explanation(name: str) -> str:
    """取某个术语的解释；没有就返回空串。"""
    return TERMS.get(name, "")


def plain(name: str) -> str:
    """把自造词换成人话；不在表里就原样返回。"""
    return PLAIN.get(name, name)


def has_explanation(name: str) -> bool:
    return bool(TERMS.get(name))


def term_html(name: str, *, enabled: bool = True, style: str = "") -> str:
    """把一个术语渲染成带虚线下划线的链接。

    `enabled=False`（设置页关掉「术语悬停解释」时）→ 渲染成普通文字，
    不带下划线、不带链接，但**文字内容一个字都不少**。
    """
    if not enabled or not has_explanation(name):
        return escape(name)
    dotted = "border-bottom:1px dotted currentColor;"
    return f'<a href="term:{name}" style="{dotted}{style}">{escape(name)}</a>'


def replace_jargon(text: str) -> str:
    """把一段文字里的自造词换成白话（自造词不该出现在界面上）。"""
    out = text
    for jargon, human in PLAIN.items():
        out = out.replace(jargon, human)
    return out


def all_terms() -> list[str]:
    return sorted(TERMS)
