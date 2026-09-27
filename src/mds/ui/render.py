"""把视图模型渲染成给人看的 HTML / 文本。**纯函数，不导入 Qt。**

单独拆出来是为了能在没有图形环境的机器上直接测"界面到底会显示什么"。

**所有颜色/字号/间距都从 `theme` 取** —— 本文件里不许出现颜色字面量和字号数字
（有 AST 测试守着）。想换配色或加深色模式，只改 `theme.py`。
"""

from __future__ import annotations

from html import escape

from . import glossary, theme
from .viewmodel import ChangeView, GroupNode

#: 处理结果 → 颜色（全部来自主题）
_VERDICT_COLORS: dict[str, str] = {
    "✅": theme.COLOR["ok_text"],
    "➕": theme.COLOR["accent"],
    "🧹": theme.COLOR["warn_text"],
    "🛡": theme.GROUP_COLOR["conflict"]["text"],
    "❔": theme.COLOR["danger"],
    "⌀": theme.COLOR["muted"],
}

_MUTED = f"color:{theme.COLOR['muted']};{theme.html_font('caption')}"
_H4 = (
    f"margin:{theme.px('md')}px 0 {theme.px('xs')}px 0;"
    f"{theme.html_font('body')};font-weight:bold;"
)
_ANCHOR = f"color:{theme.COLOR['accent']};text-decoration:none;font-weight:bold;"
_CELL = f"padding:2px {theme.px('sm')}px 2px 0;"
_BOX = (
    f"margin-top:{theme.px('sm')}px;padding:{theme.px('sm')}px;"
)
#: 信息框（accent 淡底 + 左侧竖条）
_NOTE = f"{_BOX}background:{theme.COLOR['accent_weak']};" f"border-left:{theme.BAR_WIDTH}px solid {theme.COLOR['accent']};"
#: 警告框（危险色淡底 + 左侧竖条）
_WARN = (
    f"{_BOX}background:{theme.COLOR['danger_bg']};"
    f"border-left:{theme.BAR_WIDTH}px solid {theme.COLOR['danger']};"
)


def _row(cells: list[str], *, widths: list[str] | None = None) -> str:
    tds = []
    for index, cell in enumerate(cells):
        width = f' width="{widths[index]}"' if widths and index < len(widths) else ""
        tds.append(f'<td{width} style="{_CELL}">{cell}</td>')
    return "<tr>" + "".join(tds) + "</tr>"


def _change_table(changes: list[ChangeView]) -> str:
    if not changes:
        return f'<p style="{_MUTED}">没有建议改动。</p>'
    rows = []
    for change in changes:
        before = change.before or "（空）"
        color = theme.COLOR["accent"] if change.is_fill else theme.COLOR["warn_text"]
        rows.append(
            _row(
                [
                    f"<b>{escape(change.field_label)}</b>",
                    escape(before),
                    f'<span style="color:{theme.COLOR["muted"]};">→</span>',
                    f'<span style="color:{color};">{escape(change.after)}</span>',
                    f'<span style="{_MUTED}">{escape(change.kind_label)}</span>',
                ],
                widths=["52", "150", "16", "220", "120"],
            )
        )
    return f'<table cellspacing="0" cellpadding="0">{"".join(rows)}</table>'


def render_detail_html(detail: dict, *, terms_enabled: bool = True) -> str:
    """单个文件的详情（右侧面板内容）。

    `terms_enabled=False` 时术语渲染成普通文字（设置页的开关）。
    """
    out: list[str] = []
    mark = detail.get("mark") or ""
    verdict = detail.get("verdict") or "未分析"
    color = _VERDICT_COLORS.get(mark, theme.COLOR["text"])
    title_style = f"{theme.html_font('title')};font-weight:bold;"

    out.append(f'<div style="{title_style}">{escape(detail.get("name", ""))}</div>')
    out.append(
        f'<div style="{_MUTED}">{escape(detail.get("path", ""))} · '
        f'{escape(detail.get("duration", ""))}</div>'
    )
    out.append(
        f'<div style="margin-top:{theme.px("sm")}px;{theme.html_font("body")}color:{color};">'
        f"{escape(mark)} {escape(verdict)}</div>"
    )
    if detail.get("reason"):
        out.append(
            f'<div style="margin-top:2px;{_MUTED}">为什么：{escape(detail["reason"])}</div>'
        )
    out.append(f'<div style="{_MUTED}">{escape(detail.get("analysis_status", ""))}</div>')

    changes = detail.get("changes") or []
    out.append(f'<h4 style="{_H4}">建议改动（{len(changes)} 处）</h4>')
    out.append(_change_table(changes))

    if detail.get("conflicts"):
        # 「同目录共识」是自造词 → 换白话（glossary.replace_jargon 会处理）
        out.append(
            f'<div style="{_WARN}">'
            f"<b>⚠ 已有标签与{escape(glossary.plain('同目录共识'))}不一致</b>"
            f'<div style="{_MUTED}">这些字段不会被自动修改。</div>'
            f"</div>"
        )
        rows = [
            _row(
                [
                    f"<b>{escape(item['field'])}</b>",
                    f"你的：{escape(item['current'])}",
                    f"→ 同目录多数：{escape(item['consensus'])}"
                    f'<span style="{_MUTED}">（{escape(item["votes"])} 首）</span>',
                ],
                widths=["70", "", ""],
            )
            for item in detail["conflicts"]
        ]
        out.append(
            f'<table cellspacing="0" cellpadding="0" style="margin-top:{theme.px("xs")}px;">'
            f'{"".join(rows)}</table>'
        )

    if detail.get("consensus"):
        # 自造词「同目录共识」→ 白话
        out.append(
            f'<h4 style="{_H4}">{escape(glossary.plain("同目录共识"))}的标注</h4>'
        )
        items = "".join(f"<li>{escape(line)}</li>" for line in detail["consensus"])
        out.append(f'<ul style="margin:2px 0 0 {theme.px("md")}px;">{items}</ul>')

    write = detail.get("write") or {}
    out.append(f'<h4 style="{_H4}">写入计划</h4>')
    if write.get("allowed"):
        out.append(
            f'<div style="{_NOTE}">'
            f'已生成计划：{escape(str(write.get("n_changes", 0)))} 处改动'
            f'（来源：{escape(str(write.get("source", "")))}）'
            "</div>"
        )
    else:
        out.append(f'<div style="{_MUTED}">不写入：{escape(str(write.get("reason", "")))}</div>')

    out.append(f'<h4 style="{_H4}">当前{escape(glossary.plain("标签"))}</h4>')
    tag_rows = [
        _row(
            [
                f'<span style="{_MUTED}">{escape(name)}</span>',
                escape(value or "（空）"),
            ],
            widths=["80", ""],
        )
        for name, value in (detail.get("tags") or {}).items()
    ]
    out.append(f'<table cellspacing="0" cellpadding="0">{"".join(tag_rows)}</table>')

    return "".join(out)


def render_group_label(node: GroupNode) -> str:
    """左侧树里一行的标题。"""
    parts = [f"{node.title}（{node.n_files} 首）"]
    if node.n_with_changes:
        parts.append(f"· {node.n_with_changes} 首有建议")
    if node.n_conflicts:
        parts.append(f"· ⚠ {node.n_conflicts} 首有冲突")
    return " ".join(parts)


def render_group_tooltip(node: GroupNode) -> str:
    lines = [node.folder_path or node.title]
    if node.folder_hint and node.folder_hint != node.title:
        lines.append(f"目录名线索：{node.folder_hint}")
    if node.artist_hint:
        lines.append(f"组内艺术家：{node.artist_hint}")
    lines.extend(node.consensus_lines())
    if node.n_files < 2:
        lines.append("单文件目录，不参与同目录比对")
    return "\n".join(lines)


def render_candidates_note(detail: dict) -> str:
    """候选表的说明行（让人知道为什么给这些候选）。"""
    candidates = detail.get("candidates") or []
    if not candidates:
        return "未查到匹配候选。"
    chosen = detail.get("chosen_album") or ""
    head = f"{len(candidates)} 个候选，按时长与标签匹配度排序。"
    if chosen:
        head += f"当前认定：{chosen}"
    return head


def render_candidate_header(*, terms_enabled: bool = True) -> str:
    """候选表格上方的标题（术语在这里出现，所以需要开关）。"""
    return (
        f"<b>MusicBrainz 候选{escape(glossary.term_html('发行版', enabled=False))}</b>"
    )


# ── 待办 4 组（600 行压成 4 行）───────────────────────────

def render_todo_page(
    cards: list[dict],
    *,
    headline: str = "",
    library: str = "",
    progress: str = "",
    notice: str = "",
    last_batch: str = "",
) -> str:
    """待办页的 HTML。`cards` 是 viewmodel.TodoCard 的字典形态。

    卡片样式：**淡底 + 左侧彩色竖条**（原来是整块染色，看着"糊"）。
    """
    out: list[str] = []
    out.append(
        f'<div style="{theme.html_font("title")};font-weight:bold;">本次结果</div>'
        f'<div style="margin:2px 0 {theme.px("xs")}px 0;">{escape(headline)}</div>'
    )
    if library:
        out.append(f'<div style="{_MUTED}">音乐库：{escape(library)}</div>')
    if progress:
        out.append(f'<div style="{_MUTED}">{escape(progress)}</div>')
    if notice:
        out.append(f'<div style="{_NOTE}">{escape(notice)}</div>')
    out.append(f'<div style="height:{theme.px('sm')}px;"></div>')

    for card in cards:
        if not card.get("n_items"):
            continue
        style = theme.group_style(str(card.get("key")))
        key = escape(str(card.get("key")))
        out.append(
            f'<div style="margin-bottom:{theme.px("sm")}px;'
            f'padding:{theme.px("sm")}px {theme.px("md")}px;'
            f'background:{style['bg']};border-left:{theme.BAR_WIDTH * 2}px solid {style['bar']};'
            f'border-radius:{theme.RADIUS}px;">'
            f'<div style="{theme.html_font("body")}color:{style['text']};">'
            f"{theme.dot_html(str(card.get('key')))} "
            f"{escape(theme.clean_label(str(card.get('label'))))}"
            f"　<b>{card.get('n_items')}</b> 首"
        )
        hint = str(card.get("hint") or "")
        summary = str(card.get("summary") or "")
        folders = card.get("folder_names") or []
        if folders and card.get("key") in ("choose", "conflict"):
            shown = "、".join(escape(str(f)) for f in folders[:4])
            more = f" 等 {len(folders)} 张" if len(folders) > 4 else ""
            out.append(f'<div style="{_MUTED}">{escape(summary)}　（{shown}{more}）</div>')
        elif summary:
            out.append(f'<div style="{_MUTED}">{escape(summary)}</div>')
        elif hint:
            out.append(f'<div style="{_MUTED}">{escape(hint)}</div>')

        actions: list[str] = []
        if card.get("key") == "safe":
            actions.append(f'<a href="write:safe" style="{_ANCHOR}">全部写入</a>')
            actions.append(f'<a href="open:safe" style="{_ANCHOR}">先看看清单</a>')
        elif card.get("key") in ("choose", "conflict"):
            actions.append(f'<a href="open:{key}" style="{_ANCHOR}">去处理</a>')
        elif card.get("key") == "none":
            actions.append(f'<a href="open:none" style="{_ANCHOR}">看看是哪几首</a>')
        if actions:
            out.append(
                f'<div style="margin-top:{theme.px("xs")}px;">' + "　".join(actions) + "</div>"
            )
        out.append("</div>")

    if not any(card.get("n_items") for card in cards):
        out.append(f'<div style="{_MUTED}">还没有分析结果。选好音乐库后点「开始」。</div>')
    if last_batch:
        out.append(
            f'<div style="{_MUTED}">最近写入批次：{escape(last_batch)}'
            "（可用下方「撤销上一批写入」回滚）</div>"
        )
    return "".join(out)


def render_album_choices(views: list[dict]) -> str:
    """按专辑裁决区（就地展开用）的 HTML。"""
    if not views:
        return f'<div style="{_MUTED}">没有需要你选出自哪张专辑的条目。</div>'
    out: list[str] = []
    total = sum(int(v.get("n_items", 0)) for v in views)
    style = theme.group_style("choose")
    out.append(
        f'<div style="{theme.html_font("body")}">共 {total} 首，分布在 {len(views)} 张专辑'
        "　—— <b>每张专辑选择一次</b></div>"
        f'<div style="{_MUTED}">候选按时长与曲目数匹配度排序；'
        "选定后将应用到该专辑内全部曲目。</div>"
        f'<div style="height:{theme.px('sm')}px;"></div>'
    )
    for view in views:
        folder = str(view.get("folder") or "")
        key = escape(folder)
        out.append(
            f'<div style="margin-bottom:{theme.px("sm")}px;'
            f'padding:{theme.px("sm")}px;background:{style['bg']};'
            f'border-left:{theme.BAR_WIDTH * 2}px solid {style['bar']};'
            f'border-radius:{theme.RADIUS}px;">'
            f'<div style="{theme.html_font("body")}color:{style['text']};"><b>'
            f"{escape(str(view.get('title')))}</b>　{view.get('n_items')} 首待定</div>"
            f'<div style="{_MUTED}">{escape(folder)}</div>'
            '<table cellspacing="0" cellpadding="0" '
            f'style="margin-top:{theme.px("xs")}px;">'
        )
        coverages = view.get("coverages") or []
        for index, cand in enumerate(view.get("candidates") or []):
            coverage = str(coverages[index]) if index < len(coverages) else ""
            meta = "  ".join(
                str(part)
                for part in (cand.get("date"), cand.get("country"), cand.get("fmt"))
                if part
            )
            out.append(
                "<tr>"
                f'<td style="{_CELL}"><a href="pick:{key}|{index}" '
                f'style="{_ANCHOR}">选这个</a></td>'
                f'<td style="{_CELL}">[{index}] {escape(str(cand.get("album")))}</td>'
                f'<td style="{_CELL}{_MUTED}">{escape(meta)}</td>'
                f'<td style="padding:2px 0;{_MUTED}">{escape(coverage)}</td>'
                "</tr>"
            )
        out.append("</table></div>")
    return "".join(out)
