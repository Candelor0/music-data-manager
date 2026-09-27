"""设计系统测试。

**最重要的一条**：除 `theme.py` 外，`ui/` 里不得出现颜色字面量。
改版前量到 **46 处**散落的颜色、13 处硬编码字号 ——
这就是"简陋"的根源（不是不漂亮，是没有系统）。这些断言就是防止它再散回去。
"""

from __future__ import annotations

import re
from pathlib import Path

from mds.ui import glossary, theme

UI_DIR = Path(__file__).resolve().parents[2] / "src" / "mds" / "ui"
HEX_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b")
FONT_SIZE = re.compile(r"font-size:\s*\d+")
THEME_FILE = UI_DIR / "theme.py"


def _ui_py_files() -> list[Path]:
    return [p for p in sorted(UI_DIR.rglob("*.py")) if p != THEME_FILE]


# ── 颜色必须集中 ─────────────────────────────────────────
def test_no_color_literals_outside_theme() -> None:
    violations: list[str] = []
    for path in _ui_py_files():
        text = path.read_text(encoding="utf-8")
        for match in HEX_COLOR.finditer(text):
            line = text[: match.start()].count("\n") + 1
            violations.append(f"{path.relative_to(UI_DIR)}:{line}  {match.group(0)}")
    assert not violations, (
        "颜色必须从 ui/theme.py 取，不许写在别处：\n" + "\n".join(violations)
    )


def test_no_hardcoded_font_sizes_outside_theme() -> None:
    violations: list[str] = []
    for path in _ui_py_files():
        text = path.read_text(encoding="utf-8")
        for match in FONT_SIZE.finditer(text):
            line = text[: match.start()].count("\n") + 1
            violations.append(f"{path.relative_to(UI_DIR)}:{line}  {match.group(0)}")
    assert not violations, (
        "字号必须用 theme.html_font() / theme.pt()：\n" + "\n".join(violations)
    )


def test_theme_colors_are_valid_hex() -> None:
    for name, value in theme.COLOR.items():
        assert HEX_COLOR.fullmatch(value), f"COLOR[{name}] 不是合法色值：{value}"
    for key, style in theme.GROUP_COLOR.items():
        for field, value in style.items():
            assert HEX_COLOR.fullmatch(value), f"GROUP_COLOR[{key}][{field}] 非法：{value}"


# ── 字号与间距 ───────────────────────────────────────────
def test_font_roles_are_the_four_defined_ones() -> None:
    assert set(theme.FONT_PT) == {"title", "body", "caption", "big"}


def test_font_size_is_consistent_per_role() -> None:
    """同一个角色在任何地方取到的字号必须一致（这就是"系统"的意义）。"""
    for role in theme.FONT_PT:
        assert theme.pt(role) == theme.pt(role, scale="standard")
    assert theme.pt("title") > theme.pt("body") > theme.pt("caption")


def test_large_scale_bumps_every_role() -> None:
    for role in theme.FONT_PT:
        assert theme.pt(role, scale="large") > theme.pt(role)


def test_spacing_is_multiples_of_four() -> None:
    """间距按 8 的倍数（xs=4 是唯一例外）—— 防止又出现 13、17 这种随手数字。"""
    for key, value in theme.SPACE.items():
        if key == "xs":
            assert value == 4
        else:
            assert value % 8 == 0, f"SPACE[{key}]={value} 不是 8 的倍数"


def test_group_styles_exist_for_all_four_groups() -> None:
    for key in ("safe", "choose", "conflict", "none"):
        style = theme.group_style(key)
        assert {"bg", "bar", "text"} <= set(style), f"组 {key} 缺配色"


def test_unknown_group_falls_back_safely() -> None:
    assert theme.group_style("不存在的组") == theme.GROUP_COLOR["none"]


# ── 术语表 ───────────────────────────────────────────────
def test_every_term_has_an_explanation() -> None:
    """术语表里每个词都必须有解释 —— 界面上不懂就能查。"""
    for name in glossary.all_terms():
        assert len(glossary.explanation(name)) >= 8, f"{name} 的解释太短了"


def test_self_made_jargon_is_never_left_in_the_glossary() -> None:
    """自造词不该进术语表 —— 它应该被换成白话，而不是"解释一下继续用"。"""
    for jargon in glossary.PLAIN:
        assert jargon not in glossary.TERMS, f"{jargon} 是自造词，应换白话"


def test_jargon_is_replaced_by_plain_words() -> None:
    assert glossary.plain("同目录共识") == "同一文件夹内其他曲目"
    assert glossary.plain("批次") == "批次"
    assert glossary.plain("没这个词") == "没这个词"


def test_term_html_has_link_when_enabled() -> None:
    html = glossary.term_html("发行版")
    assert 'href="term:发行版"' in html
    assert "border-bottom" in html


def test_term_html_is_plain_text_when_disabled() -> None:
    """设置页关掉「术语悬停解释」后，文字一个字不少，只是没有下划线和链接。"""
    html = glossary.term_html("发行版", enabled=False)
    assert html == "发行版"
    assert "href" not in html


def test_unknown_term_renders_as_plain_text() -> None:
    assert glossary.term_html("没有解释的词") == "没有解释的词"


def test_render_jargon_free() -> None:
    """渲染出来的文字里不该出现自造词。"""
    from mds.ui import render
    from mds.ui.viewmodel import ChangeView

    html = render.render_detail_html(
        {
            "name": "01.flac",
            "verdict": "建议补全缺失字段",
            "mark": "➕",
            "changes": [
                ChangeView(field="date", field_label="年份", kind="fill",
                           kind_label="补全", before="", after="2022")
            ],
            "conflicts": [{"field": "流派", "current": "Rock", "consensus": "JPop", "votes": "11"}],
            "consensus": ["专辑：X（11/11 首一致）"],
            "write": {"allowed": True, "n_changes": 1, "source": "补全缺失字段"},
            "tags": {"曲名": "X"},
        }
    )
    for jargon in glossary.PLAIN:
        assert jargon not in html, f"渲染结果里还有自造词：{jargon}"


# ── 主页面三态与「下一步」（纯函数）──────────────────────
def _cards(**counts: int):
    """按关键字造几张待办卡片，例如 _cards(safe=3, conflict=2)。"""
    from mds.ui.viewmodel import TodoCard

    labels = {
        "safe": "🟢 可以写入",
        "choose": "🟡 需要确认专辑",
        "conflict": "🟠 标签不一致",
        "none": "⚪ 不用管",
    }
    return [
        TodoCard(key=key, label=labels[key], hint="", n_items=counts.get(key, 0))
        for key in labels
    ]


def test_view_state_first_run_when_no_library() -> None:
    from mds.ui.viewmodel import STATE_FIRST_RUN, view_state

    assert view_state(library="", run_exists=False, has_actionable=False) == STATE_FIRST_RUN
    assert view_state(library="   ", run_exists=True, has_actionable=True) == STATE_FIRST_RUN


def test_view_state_ready_when_no_results() -> None:
    from mds.ui.viewmodel import STATE_READY, view_state

    assert view_state(library="/Music", run_exists=False, has_actionable=False) == STATE_READY


def test_view_state_ready_when_everything_done() -> None:
    """上次处理完了 → 不显示陈旧结果，回到待开始页（早期反馈）。"""
    from mds.ui.viewmodel import STATE_READY, view_state

    assert view_state(library="/Music", run_exists=True, has_actionable=False) == STATE_READY


def test_view_state_results_when_something_to_do() -> None:
    from mds.ui.viewmodel import STATE_RESULTS, view_state

    assert view_state(library="/Music", run_exists=True, has_actionable=True) == STATE_RESULTS


def test_only_none_group_means_nothing_actionable() -> None:
    from mds.ui.viewmodel import has_actionable

    assert has_actionable(_cards(none=12)) is False
    assert has_actionable(_cards(safe=3, none=12)) is True
    assert has_actionable(_cards(conflict=1)) is True


# ── 分区感：配色必须有三档明度 ───────────────────────────
def _lum(hex_color: str) -> float:
    """相对亮度（0~255），和 scripts/ui-snapshot-metrics.py 用同一套算法。"""
    raw = hex_color.lstrip("#")
    r, g, b = int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
    return (r * 299 + g * 587 + b * 114) / 1000


def test_palette_has_three_distinct_tone_levels() -> None:
    """**这是"分区感"的根**：界面必须有"桌面 / 分区带 / 面板"三档明度。

    第一版美化把这层做塌了 —— 只剩"白纸 + 细线"，量出来结构性中灰
    从 20.2% 掉到 7.0%。这条断言不用渲染就能拦住同类错误复现。
    """
    desk = _lum(theme.COLOR["desk"])
    band = _lum(theme.COLOR["band"])
    panel = _lum(theme.COLOR["panel"])
    text = _lum(theme.COLOR["text"])

    # 桌面与分区带都要落在"结构性中灰"区间（200~245）
    assert 200 <= desk <= 245, f"桌面底色太亮/太暗：{desk:.0f}"
    assert 200 <= band <= 245, f"分区带颜色太亮/太暗：{band:.0f}"
    # 面板要够白，才能"浮"在灰上
    assert panel > 250, f"面板不够白：{panel:.0f}"
    # 关键：带和桌面要能区分开（差值太小就糊在一起）
    assert abs(band - desk) >= 6, "分区带与桌面几乎同色，区分不出来"
    # 文字要够深
    assert text < 120, f"正文色太浅：{text:.0f}"


def test_divider_is_visible_but_not_harsh() -> None:
    """分隔线要"看得见"：既不能淡到看不见，也不能黑得像边框。"""
    divider = _lum(theme.COLOR["divider"])
    soft = _lum(theme.COLOR["divider_soft"])
    assert 120 <= divider <= 215, f"主分隔线不合规：{divider:.0f}"
    assert divider < soft, "主分隔线应当比细线更深"
    assert soft < _lum(theme.COLOR["panel"]), "细线在白面板上要能看见"


def test_group_colors_are_not_washed_out() -> None:
    """四组待办的底色要**看得出颜色**，不能淡到接近白。

    第一版把这里改成近白，是"像 A4 纸"的原因之一。
    """
    for key in ("safe", "choose", "conflict", "none"):
        bg = theme.group_style(key)["bg"]
        assert _lum(bg) <= 245, f"{key} 组的底色太淡了（接近白）"
        bar = theme.group_style(key)["bar"]
        assert _lum(bar) <= 190, f"{key} 组的竖条不够明显"
