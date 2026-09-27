"""设置页：填邮箱与密钥、测试连接、费用与批量开关。

**安全约定**

1. 密钥输入框里**不显示明文**：已保存的值只以掩码形式出现在提示文字里
   （`••••••1234`），要点「显示」才看得到自己正在输入的内容。
2. 留空 = 不动已保存的值；填了就覆盖。
3. `collect()` 返回的是用户在**本次会话里新输入**的内容，不会把已存的值回读出来。
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..prefs import SettingsView


class SecretEdit(QWidget):
    """带「显示」开关的密钥输入框（默认掩码）。"""

    def __init__(self, placeholder: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._edit = QLineEdit()
        self._edit.setEchoMode(QLineEdit.Password)
        self._edit.setPlaceholderText(placeholder)
        self._toggle = QPushButton("显示")
        self._toggle.setCheckable(True)
        self._toggle.setFixedWidth(64)
        self._toggle.toggled.connect(self._on_toggle)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._edit, 1)
        layout.addWidget(self._toggle)

    def _on_toggle(self, shown: bool) -> None:
        self._edit.setEchoMode(QLineEdit.Normal if shown else QLineEdit.Password)
        self._toggle.setText("隐藏" if shown else "显示")

    def value(self) -> str:
        return self._edit.text().strip()

    def clear(self) -> None:
        self._edit.clear()

    def set_placeholder(self, text: str) -> None:
        self._edit.setPlaceholderText(text)

    @property
    def edit(self) -> QLineEdit:
        return self._edit


class SettingsPage(QWidget):
    save_requested = pyqtSignal()
    reset_requested = pyqtSignal()
    purge_requested = pyqtSignal()
    test_requested = pyqtSignal()
    back_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # 设置页也是一块白面板（分区感）—— 必须用 style_container，
        # 普通 QWidget 只设 objectName 是不会应用 QSS 背景的（实测踩到）
        theme.style_container(self, "panel")

        self._email = QLineEdit()
        self._email.setPlaceholderText("你的邮箱（用于 MusicBrainz 请求）")
        self._acoustid = SecretEdit("留空 = 不改动已保存的 Key")
        self._deepseek = SecretEdit("留空 = 不改动已保存的 Key")

        self._place = QLabel("")
        self._place.setWordWrap(True)
        self._source = QLabel("")
        self._source.setStyleSheet(f"color:{theme.COLOR['muted']};")

        self._test = QPushButton("测试连接")
        self._test.clicked.connect(self.test_requested.emit)
        self._test_result = QLabel("")
        self._test_result.setWordWrap(True)

        # ── 界面 ──────────────────────────────────────
        # 只有两个选项 → 用单选按钮而不是下拉框：
        # 一眼能看全、不用点开，也就没有下拉弹窗的配色/截断问题（实测反馈过）
        self._font_standard = QRadioButton("标准")
        self._font_large = QRadioButton("大")
        self._font_group = QButtonGroup(self)
        self._font_group.addButton(self._font_standard, 0)
        self._font_group.addButton(self._font_large, 1)
        font_box = QWidget()
        font_row = QHBoxLayout(font_box)
        font_row.setContentsMargins(0, 0, 0, 0)
        font_row.setSpacing(theme.px("md"))
        font_row.addWidget(self._font_standard)
        font_row.addWidget(self._font_large)
        font_row.addStretch(1)

        self._show_hints = QCheckBox("显示界面提示")
        self._show_hints.setToolTip("界面上的引导与说明文字")

        self._term_tooltips = QCheckBox("术语悬停解释")
        self._term_tooltips.setToolTip(
            "像「发行版」这类词，鼠标停上去会显示一句人话解释"
        )

        self._cost = QCheckBox("开始前显示费用预估")
        self._cost.setToolTip("关闭后不再弹出确认框；界面上仍显示预估金额")
        self._batch = QSpinBox()
        self._batch.setRange(1, 100000)
        self._batch.setSingleStep(100)
        self._batch.setSuffix(" 首/批")

        self._reset = QPushButton("恢复默认设置")
        self._reset.setObjectName("danger")
        self._reset.clicked.connect(self.reset_requested.emit)
        self._save = QPushButton("保存")
        self._save.setObjectName("primary")
        self._save.clicked.connect(self.save_requested.emit)
        self._back = QPushButton("← 返回待办")
        self._back.clicked.connect(self.back_requested.emit)

        def field(widget: QWidget, width: int = 520) -> QWidget:
            """限制输入框宽度 —— 拉满整行（1300px）看着很松散。"""
            widget.setMaximumWidth(width)
            return widget

        def left_button(button: QWidget) -> QWidget:
            """按钮靠左，不要被表单拉成一整条。"""
            box = QWidget()
            row = QHBoxLayout(box)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(button)
            row.addStretch(1)
            return box

        # 数据占用说明 + 清空按钮（实测反馈：需要给人一个清空入口）
        self._data_note = QLabel("")
        self._data_note.setObjectName("caption")
        self._data_note.setWordWrap(True)

        self._purge = QPushButton("清空分析数据")
        self._purge.setObjectName("danger")
        self._purge.setToolTip(
            "删除全部分析结果、缓存与快照（删除后旧批次不能再撤销）。"
            "密钥、邮箱、界面设置都会保留；不会改动任何音乐文件。"
        )
        self._purge.clicked.connect(self.purge_requested.emit)

        def section(title: str, rows: list) -> QWidget:
            """一个分区块：小标题带（灰底）+ 内容区（更淡的底）。

            为什么这么做：设置页原来是把所有字段平铺在一整块白底上，
            看着又是一个"大白框"。按最终选的「甲-2 分区带式」，
            用带分隔的块把设置分组。
            """
            block = QWidget()
            layout = QVBoxLayout(block)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)

            header = QWidget()
            theme.style_container(header, "sectionHeader")
            header_layout = QHBoxLayout(header)
            header_layout.setContentsMargins(theme.px("md"), theme.px("xs"), theme.px("md"), theme.px("xs"))
            label = QLabel(title)
            label.setStyleSheet("font-weight:bold;")
            header_layout.addWidget(label)
            header_layout.addStretch(1)
            layout.addWidget(header)

            body = QWidget()
            theme.style_container(body, "sectionBody")
            body_form = QFormLayout(body)
            # ⚠️ QFormLayout 默认会把整张表单**居中** —— 窗口一宽就明显错位
            #（实测：2560 宽的窗口里标签全被推到中间）。显式左对齐。
            body_form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
            body_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
            body_form.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)
            body_form.setContentsMargins(
                theme.px("md"), theme.px("sm"), theme.px("md"), theme.px("sm")
            )
            body_form.setSpacing(theme.px("sm"))
            for item in rows:
                if isinstance(item, tuple):
                    body_form.addRow(item[0], item[1])
                else:
                    # 只给一个控件 → 占满整行（长句子否则会被挤成好几行）
                    body_form.addRow(item)
            layout.addWidget(body)
            return block

        account = section(
            "账号",
            [
                ("MusicBrainz 邮箱", field(self._email)),
                ("AcoustID Key", field(self._acoustid)),
                ("DeepSeek Key", field(self._deepseek)),
                self._place,
                self._source,
                left_button(self._test),
                self._test_result,
            ],
        )
        interface = section(
            "界面",
            [
                ("界面字号", font_box),
                ("", self._show_hints),
                ("", self._term_tooltips),
            ],
        )
        processing = section(
            "处理",
            [
                ("", self._cost),
                ("单批写入上限", field(self._batch, 200)),
                ("", self._data_note),
                ("", left_button(self._purge)),
            ],
        )

        self._data_note.setObjectName("caption")
        self._data_note.setWordWrap(True)
        self._data_note.setContentsMargins(
            theme.px("md"), theme.px("sm"), theme.px("md"), 0
        )

        self._purge.setObjectName("danger")
        self._purge.setToolTip(
            "删除全部分析结果、缓存与快照（删除后旧批次不能再撤销）。"
            "密钥、邮箱、界面设置都会保留；不会改动任何音乐文件。"
        )
        self._purge.clicked.connect(self.purge_requested.emit)

        # 页头（分区块上方的一行说明）
        title = QLabel("设置")
        title.setStyleSheet(f"{theme.html_font('title')}font-weight:bold;")
        hint = QLabel("保存后长期有效。密钥存放于系统凭据库，不写入配置文件。")
        hint.setWordWrap(True)
        hint.setObjectName("caption")

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.addWidget(self._back)
        buttons.addWidget(self._reset)
        buttons.addStretch(1)
        buttons.addWidget(self._save)

        # ⚠️ 内容必须放进滚动区：窗口不够高时，几个分区块会被布局压叠，
        # 结果标签互相盖住、按钮被拉满宽（实测截图里就是这个样子）
        content = QWidget()
        inner = QVBoxLayout(content)
        inner.setContentsMargins(
            theme.px("md"), theme.px("md"), theme.px("md"), theme.px("md")
        )
        inner.setSpacing(theme.px("xs"))
        layout = inner
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addSpacing(theme.px("sm"))
        layout.addWidget(account)
        layout.addSpacing(theme.px('sm'))
        layout.addWidget(interface)
        layout.addSpacing(theme.px('sm'))
        layout.addWidget(processing)
        layout.addWidget(self._data_note)
        layout.addStretch(1)
        layout.addLayout(buttons)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    # ── 显示 ─────────────────────────────────────────────
    def set_usage_note(self, text: str) -> None:
        """数据占用说明（由窗口算好传进来）。"""
        self._data_note.setText(text)

    def show_view(self, view: SettingsView, *, prefs: dict | None = None) -> None:
        prefs = prefs or {}
        large = str(prefs.get("font_scale", "standard")) == "large"
        self._font_large.setChecked(large)
        self._font_standard.setChecked(not large)
        self._show_hints.setChecked(bool(prefs.get("show_hints", True)))
        self._term_tooltips.setChecked(bool(prefs.get("term_tooltips", True)))

        self._email.setText(view.email)
        self._acoustid.set_placeholder(
            f"已保存 {view.acoustid_masked}（留空 = 不改动）"
            if view.acoustid_masked
            else "还没填"
        )
        self._deepseek.set_placeholder(
            f"已保存 {view.deepseek_masked}（留空 = 不改动）"
            if view.deepseek_masked
            else "还没填（可以空着，只是不用 AI）"
        )
        self._acoustid.clear()
        self._deepseek.clear()
        self._place.setText(view.secret_place_line)
        self._source.setText(view.source_line)
        self._cost.setChecked(view.cost_confirm)
        self._batch.setValue(max(1, view.batch_limit))
        self._test_result.setText("")

    def show_test_result(self, text: str) -> None:
        self._test_result.setText(text)
        self._test.setEnabled(True)

    def set_busy(self, busy: bool) -> None:
        self._test.setEnabled(not busy)
        self._save.setEnabled(not busy)
        if busy:
            self._test_result.setText("正在测试连接…")

    # ── 取值（只返回本次新输入的内容）─────────────────────
    def collect(self) -> dict[str, str | bool]:
        return {
            "email": self._email.text().strip(),
            "acoustid_key": self._acoustid.value(),
            "deepseek_key": self._deepseek.value(),
            "cost_confirm": self._cost.isChecked(),
            "batch_limit": int(self._batch.value()),
            "font_scale": "large" if self._font_large.isChecked() else "standard",
            "show_hints": self._show_hints.isChecked(),
            "term_tooltips": self._term_tooltips.isChecked(),
        }

    # ── 给测试 ───────────────────────────────────────────
    @property
    def email_edit(self) -> QLineEdit:
        return self._email

    @property
    def acoustid_edit(self) -> SecretEdit:
        return self._acoustid

    @property
    def deepseek_edit(self) -> SecretEdit:
        return self._deepseek

    @property
    def cost_check(self) -> QCheckBox:
        return self._cost

    @property
    def test_button(self) -> QPushButton:
        return self._test

    def test_result_text(self) -> str:
        return self._test_result.text()

    @property
    def font_standard_radio(self) -> QRadioButton:
        return self._font_standard

    @property
    def font_large_radio(self) -> QRadioButton:
        return self._font_large

    @property
    def show_hints_check(self) -> QCheckBox:
        return self._show_hints

    @property
    def term_tooltips_check(self) -> QCheckBox:
        return self._term_tooltips

    @property
    def purge_button(self):
        return self._purge

    @property
    def reset_button(self):
        return self._reset
