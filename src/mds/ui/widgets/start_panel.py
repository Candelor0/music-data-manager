"""起始页 / 待开始页 —— 解决"新用户进来是大白框，不知道怎么上手"。

新用户进来只看到一个大白框，不知道该干什么；而弹个框让人"先看说明书"
又太笨重 —— 目标是扫几眼就能看懂该怎么用。

所以这里**不弹框、不提说明书**：页面上就一个明确的动作，加上
"按下去会发生什么"的一句话说明。
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import theme


class StartPanel(QWidget):
    choose_folder = pyqtSignal()
    start = pyqtSignal()
    change_folder = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # 卡片自己就是那块"面板"：灰桌上一张白卡片，而不是一整块空面板
        self._card = QWidget()
        theme.style_container(self._card, "panel")
        self._card.setMaximumWidth(760)
        card_layout = QVBoxLayout(self._card)
        card_layout.setContentsMargins(
            theme.px("lg"), theme.px("lg"), theme.px("lg"), theme.px("lg")
        )
        card_layout.setSpacing(theme.px("md"))

        self._title = QLabel("")
        self._title.setWordWrap(True)
        self._title.setAlignment(Qt.AlignCenter)
        self._title.setStyleSheet(f"{theme.html_font('title')}font-weight:bold;")

        self._body = QLabel("")
        self._body.setWordWrap(True)
        self._body.setMinimumWidth(560)
        self._body.setAlignment(Qt.AlignCenter)
        self._body.setObjectName("caption")

        self._primary = QPushButton("")
        self._primary.setObjectName("primary")
        self._primary.setMinimumWidth(240)
        self._primary.clicked.connect(self._on_primary)

        self._secondary = QPushButton("换一个文件夹")
        self._secondary.clicked.connect(self.change_folder.emit)
        self._secondary.setVisible(False)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self._primary)
        buttons.addWidget(self._secondary)
        buttons.addStretch(1)

        self._footnote = QLabel("")
        self._footnote.setWordWrap(True)
        self._footnote.setAlignment(Qt.AlignCenter)
        self._footnote.setObjectName("caption")

        card_layout.addWidget(self._title)
        card_layout.addWidget(self._body)
        card_layout.addLayout(buttons)
        card_layout.addWidget(self._footnote)

        # 顶部对齐（留一点上边距），**不垂直居中** ——
        # 居中会让"内容很少的页面"上下各空一大片，仍然像一个大白框
        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            theme.px("xl"), theme.px("xl"), theme.px("xl"), theme.px("md")
        )
        outer.addWidget(self._card, 0, Qt.AlignTop | Qt.AlignHCenter)
        outer.addStretch(1)

        self._mode = "first_run"
        self.show_first_run()

    # ── 两种样子 ─────────────────────────────────────────
    def show_first_run(self) -> None:
        """从没选过音乐库：只教第一步，页面上就一个动作。"""
        self._mode = "first_run"
        self._title.setText("选择你的音乐文件夹")
        self._body.setText("只读取文件信息，不做任何修改")
        self._primary.setText("选择文件夹")
        self._secondary.setVisible(False)
        self._footnote.setText("")

    def show_ready(
        self,
        *,
        library: str,
        stats_line: str = "",
        cost_line: str = "",
        note: str = "",
    ) -> None:
        """选好目录、还没开始（或上次已处理完）。"""
        self._mode = "ready"
        self._title.setText("准备就绪")
        self._body.setText(
            f"音乐库：{library}"
            + (f"\n{stats_line}" if stats_line else "")
            + (f"\n{cost_line}" if cost_line else "")
        )
        self._primary.setText("开  始")
        self._secondary.setVisible(True)
        self._footnote.setText(
            (note + "\n" if note else "")
            + "开始后将读取标签、匹配发行版信息并生成改动建议；\n"
            "匹配过程不会修改任何文件。"
        )

    # ── 给测试 ───────────────────────────────────────────
    @property
    def mode(self) -> str:
        return self._mode

    @property
    def primary_button(self) -> QPushButton:
        return self._primary

    def text_content(self) -> str:
        return " ".join(
            w.text() for w in (self._title, self._body, self._primary, self._footnote)
        )

    def _on_primary(self) -> None:
        if self._mode == "first_run":
            self.choose_folder.emit()
        else:
            self.start.emit()
