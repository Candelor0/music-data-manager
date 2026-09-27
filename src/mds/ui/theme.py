"""界面主题：字号 / 间距 / 颜色 / 组件规范的**唯一出处**。

配色思路
============================
第一版把界面做成"尽可能白、尽可能少"，结果整屏缺少分区、观感廉价。

量化后发现根因不是"白"，而是**结构性中灰太少**：改版前界面里有 **19.3%** 的像素
是"结构性中灰"（表格网格线、行分隔、边框、表头底），那一版只剩 **2.0%** —— 掉了九成。
而**层次与分区感只能靠中灰来体现**：行与行的分隔、面板与面板的边界、
表头和内容的区别，全是中灰在干活。

所以这一版的原则是（最终选的"甲-2 分区带式"）：

    不是"一张 A4 纸"，而是"一张灰桌子上摆着白面板，面板靠分区带和分隔线划开"。

| 层 | 用途 | 色 |
| --- | --- | --- |
| **桌面** `desk` | 窗口底、面板之间的空隙 | 浅灰 |
| **面板** `panel` | 表格、详情、内容区 | 白 + 1px 边框 + 圆角 |
| **分区带** `band` | 工具栏、筛选行、状态栏 | 较明显的中灰 |
| **分隔线** `divider` | 带与内容之间（主要分区） | 看得见的灰 |
| **细线** `divider_soft` | 行与行、网格线 | 淡但不消失 |

硬规则（由 tests/unit/test_theme.py 用 AST 守卫）
------------------------------------------------
1. **除本文件外，`ui/` 里不得出现颜色字面量**（`#rrggbb`）
2. 字号、间距同理
3. 想加深色模式 → **只在这里加一套**

还有一条**可测量的参考线**（scripts/ui-snapshot-metrics.py）：
截图中「结构性中灰」占比必须落在 **18%~55%** 之间、纯白 ≤ 70%。
- 低于 18% → 又变成"一张 A4 纸"（第一版就是这样：只有 7%）
- 高于 55% → 反过来糊成一片灰

不达标不算做完。
"""

from __future__ import annotations

from PyQt5.QtGui import QFont

# ── 字号（pt）────────────────────────────────────────────
#: 只用这四档，不许再出现第五种大小
FONT_PT: dict[str, int] = {
    "title": 16,     # 页面标题、专辑名
    "body": 14,      # 正文、表格、按钮
    "caption": 12,   # 说明小字、次要信息
    "big": 18,       # 起始页大按钮、关键数字（如「29 首」）
}

#: 设置页的「界面字号」两档：大档 = 每档 +2pt
FONT_SCALE: dict[str, int] = {"standard": 0, "large": 2}

#: 字体族：**不指定**，用系统默认中文字体
#: （指定了在别人电脑上可能没有，会回退成很难看的字体）
FONT_FAMILY: str | None = None


# ── 间距（px）────────────────────────────────────────────
#: 一律按 8 的倍数
SPACE: dict[str, int] = {
    "xs": 4,
    "sm": 8,
    "md": 16,
    "lg": 24,
    "xl": 32,
}


# ── 颜色 ─────────────────────────────────────────────────
COLOR: dict[str, str] = {
    # 三层底色：桌面 → 面板 → 分区带
    "desk": "#edf0f4",          # 窗口底（灰桌）
    "panel": "#ffffff",         # 内容面板（白）
    "panel_soft": "#f4f6f9",    # 内容很少的面板（起始页）——免得是一整块大白
    "section_bg": "#f8fafc",    # 分区块的内容底（比 band 淡，但和纯白有区别）
    "band": "#e3e8ee",          # 分区带（工具栏 / 表头 / 筛选行 / 状态栏）
    "band_soft": "#f1f3f6",     # 更淡的带（用在带里再分层时）
    # 分隔
    "divider": "#b7c0cb",       # 主要分区线（看得见）
    "divider_soft": "#ccd4dd",  # 行内分隔 / 网格线
    # 表格
    "row_alt": "#f4f6f9",       # 斑马纹
    "row_hover": "#dbe8fd",
    # 文字
    "text": "#161c24",
    "muted": "#57616f",
    "on_accent": "#ffffff",
    # 主色
    "accent": "#1f5fd0",
    "accent_weak": "#d6e4fb",   # 勾选行底
    # 语义色
    "danger": "#a8340f",
    "danger_bg": "#fbe4dd",
    "ok_text": "#12602b",
    "warn_text": "#6f5200",
}

#: 四组待办的配色：**有颜色的底 + 左侧彩色竖条**
#: （第一版这里改成"淡到接近白"，是"像 A4 纸"的原因之一）
GROUP_COLOR: dict[str, dict[str, str]] = {
    "safe": {"bg": "#dcefdf", "bar": "#2f8f49", "text": "#12602b"},
    "choose": {"bg": "#f8ecc9", "bar": "#b8860b", "text": "#6f5200"},
    "conflict": {"bg": "#e6ddf7", "bar": "#6a48c2", "text": "#452d8f"},
    "none": {"bg": "#e2e7ee", "bar": "#7d8794", "text": "#57616f"},
}


# ── 组件规范 ─────────────────────────────────────────────
RADIUS = 6            # 圆角
ROW_HEIGHT = 34       # 表格行高（每格单行；多行单元格在 Qt+样式表下不可靠，已改用两列）
BUTTON_HEIGHT = 32
BAR_WIDTH = 3         # 勾选行左侧竖条宽度
PAD = 10              # 带的内边距
PX_PAD = 12           # 带与面板外围的内边距（统一用这个，别各处写数）


def pt(role: str, *, scale: str = "standard") -> int:
    """取某个角色的字号（pt）。"""
    base = FONT_PT.get(role, FONT_PT["body"])
    return base + FONT_SCALE.get(scale, 0)


def px(space: str) -> int:
    """取某个间距档位（px）。"""
    return SPACE.get(space, SPACE["sm"])


def font(role: str = "body", *, scale: str = "standard", bold: bool = False) -> QFont:
    f = QFont()
    if FONT_FAMILY:
        f.setFamily(FONT_FAMILY)
    f.setPointSize(pt(role, scale=scale))
    f.setBold(bold)
    return f


def group_style(key: str) -> dict[str, str]:
    return GROUP_COLOR.get(key, GROUP_COLOR["none"])


#: core 传来的组标签前面带 emoji（🟢🟡🟠⚪）。界面**不用**它们 ——
#: emoji 按大字号渲染会变成四个彩色大圆球，看着很廉价（实测反馈）。
#: 界面自己画一个 8~10px 的小圆点。
_GROUP_EMOJI = ("🟢", "🟡", "🟠", "⚪", "🔵")


def clean_label(label: str) -> str:
    """去掉组标签里的 emoji（界面自己画小圆点）。"""
    out = label
    for icon in _GROUP_EMOJI:
        out = out.replace(icon, "")
    return out.strip()


def strip_group_emoji(text: str) -> str:
    """去掉一段文字里所有出现过的组 emoji（「下一步」提示里也有）。"""
    return clean_label(text)


def dot_html(key: str) -> str:
    """富文本里的小圆点。"""
    return f'<span style="color:{group_style(key)["bar"]};">●</span>'


def dot_icon(key: str, size: int = 10):
    """生成一个小圆点图标（给按钮用 —— QPushButton 不认 HTML）。"""
    from PyQt5.QtCore import Qt as _Qt
    from PyQt5.QtGui import QBrush, QColor, QPainter, QPixmap

    pixmap = QPixmap(size, size)
    pixmap.fill(_Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QBrush(QColor(group_style(key)["bar"])))
    painter.setPen(_Qt.NoPen)
    painter.drawEllipse(0, 0, size, size)
    painter.end()
    return pixmap


def html_font(role: str = "body", *, scale: str = "standard") -> str:
    """给富文本用的 `font-size` 片段（pt）。"""
    return f"font-size:{pt(role, scale=scale)}pt;"


# ── 全局样式表 ────────────────────────────────────────────

def qss(*, scale: str = "standard") -> str:
    """应用级样式表。

    这里定义的是**分层**：`#desk` / `#band` / `#panel` 三种对象名对应三种底色，
    界面代码只负责把控件放进对应的容器里，不用各写各的颜色。
    """
    body = pt("body", scale=scale)
    caption = pt("caption", scale=scale)
    return f"""
    QWidget {{
        color: {COLOR['text']};
        font-size: {body}pt;
    }}
    QMainWindow, QDialog {{
        background: {COLOR['desk']};
    }}
    /* ── 三种容器：桌面 / 分区带 / 面板 ── */
    QWidget#desk {{ background: {COLOR['desk']}; }}
    QWidget#band {{
        background: {COLOR['band']};
        border-bottom: 1px solid {COLOR['divider']};
    }}
    QWidget#bandTop {{
        background: {COLOR['band']};
        border-bottom: 1px solid {COLOR['divider']};
    }}
    QWidget#bandBottom {{
        background: {COLOR['band']};
        border-top: 1px solid {COLOR['divider']};
    }}
    QWidget#panel {{
        background: {COLOR['panel']};
        border: 1px solid {COLOR['divider']};
        border-radius: {RADIUS}px;
    }}
    QWidget#panelSoft {{
        background: {COLOR['panel_soft']};
        border: 1px solid {COLOR['divider']};
        border-radius: {RADIUS}px;
    }}
    QWidget#sectionHeader {{
        background: {COLOR['band']};
        border-bottom: 1px solid {COLOR['divider']};
    }}
    QWidget#sectionBody {{
        background: {COLOR['section_bg']};
        border-bottom: 1px solid {COLOR['divider_soft']};
    }}
    QWidget#tabsRow {{
        background: {COLOR['band_soft']};
        border-bottom: 1px solid {COLOR['divider']};
    }}
    QScrollArea {{
        background: {COLOR['panel']};
        border: none;
    }}
    QScrollArea > QWidget > QWidget {{
        background: transparent;
    }}
    QToolTip {{
        background: {COLOR['panel']};
        color: {COLOR['text']};
        border: 1px solid {COLOR['divider']};
        padding: 4px 6px;
    }}
    /* ── 按钮 ── */
    QPushButton {{
        min-height: {BUTTON_HEIGHT}px;
        padding: 0 14px;
        border: 1px solid {COLOR['divider']};
        border-radius: {RADIUS}px;
        background: {COLOR['panel']};
    }}
    QPushButton:hover {{ background: {COLOR['row_hover']}; }}
    QPushButton:disabled {{
        color: {COLOR['muted']};
        background: {COLOR['band_soft']};
        border-color: {COLOR['divider_soft']};
    }}
    QPushButton#primary {{
        background: {COLOR['accent']};
        border: 1px solid {COLOR['accent']};
        color: {COLOR['on_accent']};
        font-weight: bold;
    }}
    QPushButton#primary:disabled {{
        background: {COLOR['band']};
        border-color: {COLOR['divider']};
        color: {COLOR['muted']};
    }}
    QPushButton#danger {{ color: {COLOR['danger']}; }}
    QPushButton#big {{
        min-height: 46px;
        font-size: {pt('big', scale=scale)}pt;
        font-weight: bold;
    }}
    /* ── 输入 ── */
    /* ⚠️ 只给「纯文本输入框」套扁平样式。
       QComboBox / QSpinBox **不套** —— 它们带子控件（下拉箭头、上下按钮），
       一旦用 QSS 接管，Qt 就不再画原生箭头，用户看到的是几个没有标记的白方块，
       而且和内容栏没有边界（实测反馈）。
       这两类改用系统原生外观：边界、箭头都正常。 */
    QLineEdit {{
        min-height: {BUTTON_HEIGHT}px;
        padding: 0 8px;
        border: 1px solid {COLOR['divider']};
        border-radius: {RADIUS}px;
        background: {COLOR['panel']};
    }}
    QLineEdit:focus {{
        border: 1px solid {COLOR['accent']};
    }}
    /* ── 表格：斑马纹 + 行线 + 表头带 ── */
    QTableView {{
        background: {COLOR['panel']};
        alternate-background-color: {COLOR['row_alt']};
        gridline-color: {COLOR['divider_soft']};
        border: none;
        selection-background-color: {COLOR['accent_weak']};
        selection-color: {COLOR['text']};
    }}
    QTableView::item {{ padding: 0 6px; }}
    QHeaderView::section {{
        background: {COLOR['band']};
        color: {COLOR['muted']};
        border: none;
        border-bottom: 1px solid {COLOR['divider']};
        border-right: 1px solid {COLOR['divider_soft']};
        padding: 7px 8px;
        font-weight: bold;
    }}
    /* ── 富文本区 ── */
    QTextBrowser {{
        background: {COLOR['panel']};
        border: none;
    }}
    /* ── 进度条 ── */
    QProgressBar {{
        border: 1px solid {COLOR['divider']};
        border-radius: 4px;
        background: {COLOR['panel']};
    }}
    QProgressBar::chunk {{
        border-radius: 3px;
        background: {COLOR['accent']};
    }}
    QSplitter::handle {{ background: {COLOR['divider']}; }}
    QSplitter::handle:horizontal {{ width: 1px; }}
    /* ── 文字角色 ── */
    QLabel#muted, QLabel#caption {{
        color: {COLOR['muted']};
        font-size: {caption}pt;
    }}
    QLabel#title {{
        font-size: {pt('title', scale=scale)}pt;
        font-weight: bold;
    }}
    QLabel#section {{
        font-size: {pt('title', scale=scale)}pt;
        font-weight: bold;
        color: {COLOR['text']};
    }}
    QLabel#bigNumber {{
        font-size: {pt('big', scale=scale)}pt;
        color: {COLOR['accent']};
        font-weight: bold;
    }}
    """


def style_container(widget, name: str) -> None:
    """给自定义容器套上分区底色（desk / band / panel / tabsRow / panelSoft）。

    ⚠️ **Qt 的坑（实测踩到）**：普通 `QWidget` 只设 `objectName` 是**不会**应用
    QSS 里的 background 的 —— 必须同时设 `WA_StyledBackground`，否则背景不生效，
    界面看起来"该有底色的地方还是灰的/白的"。子类化的 QWidget 尤其如此。
    """
    from PyQt5.QtCore import Qt as _Qt

    widget.setObjectName(name)
    widget.setAttribute(_Qt.WA_StyledBackground, True)


def apply(app, *, scale: str = "standard") -> None:
    """把主题应用到 QApplication（字体 + 样式表）。"""
    app.setFont(font("body", scale=scale))
    app.setStyleSheet(qss(scale=scale))
