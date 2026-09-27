"""筛选标签：原来的「待办 4 组」变成顶部一排带计数的按钮。

早期反馈：待办页是个大白框，内容只占一小块，"去处理""看看是哪几首"
两个按钮还重复。合并进主页面后变成标签 —— **点标签就筛选，不再有进/返回页面**。

默认选中 🟢（那是用户能马上做的事）。
"""

from __future__ import annotations

from PyQt5.QtCore import QSize, pyqtSignal
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import theme
from ..viewmodel import TodoCard

#: 显示顺序
TAB_ORDER: tuple[str, ...] = ("safe", "choose", "conflict", "none")


class FilterTabs(QWidget):
    changed = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cards: dict[str, TodoCard] = {}
        self._buttons: dict[str, QPushButton] = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.px("sm"))
        for key in TAB_ORDER:
            button = QPushButton("")
            button.setCheckable(True)
            button.setIconSize(QSize(10, 10))
            button.clicked.connect(lambda _checked, k=key: self.changed.emit(k))
            self._group.addButton(button)
            self._buttons[key] = button
            row.addWidget(button)
        row.addStretch(1)

        self._hint = QLabel("")
        self._hint.setObjectName("caption")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.px("xs"))
        layout.addLayout(row)
        layout.addWidget(self._hint)

    # ── 接口 ─────────────────────────────────────────────
    def set_cards(self, cards: list[TodoCard], *, selected: str = "safe") -> None:
        self._cards = {card.key: card for card in cards}
        for key, button in self._buttons.items():
            card = self._cards.get(key)
            # 界面自己画小圆点，不用 emoji（emoji 会渲染成彩色大圆球，很廉价）
            button.setIcon(QIcon(theme.dot_icon(key)))
            label = theme.clean_label(card.label) if card else key
            button.setText(f"{label} {card.n_items}" if card else label)
            button.setEnabled(bool(card and card.n_items))
            button.setStyleSheet(self._button_style(key))
        self.set_current(selected)

    def set_current(self, key: str) -> None:
        if key not in self._buttons:
            key = "safe"
        self._buttons[key].setChecked(True)
        card = self._cards.get(key)
        self._hint.setText(card.hint if card else "")
        self.changed.emit(key)

    def current_key(self) -> str:
        for key, button in self._buttons.items():
            if button.isChecked():
                return key
        return "safe"

    def count_of(self, key: str) -> int:
        card = self._cards.get(key)
        return card.n_items if card else 0

    def _button_style(self, key: str) -> str:
        style = theme.group_style(key)
        return (
            f"QPushButton {{"
            f" border:1px solid {theme.COLOR['divider']};"
            f" border-radius:{theme.RADIUS}px;"
            f" background:{theme.COLOR['panel']};"
            f" padding:0 {theme.px('md')}px;"
            f"}}"
            f"QPushButton:checked {{"
            f" background:{style['bg']};"
            f" border:1px solid {style['bar']};"
            f" border-left:{theme.BAR_WIDTH * 2}px solid {style['bar']};"
            f" font-weight:bold;"
            f"}}"
            f"QPushButton:disabled {{ color:{theme.COLOR['muted']}; }}"
        )
