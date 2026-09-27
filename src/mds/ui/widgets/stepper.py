"""三步进度条：① 选音乐文件夹 → ② 开始分析 → ③ 处理待办。

**它不是教程，是状态显示** —— 让人随时知道"我在哪一步、下一步往哪走"。

使用频率大约几周一次，所以：

- 前 5 次打开显示**完整版**（带说明）
- 用满 5 次后自动收成**一行细条**（不消失 —— 几周后打开还能一眼定位）
- 设置页关掉「显示上手提示」→ 直接就是细条
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QLabel, QVBoxLayout, QWidget

from .. import theme

#: 三个步骤的短名（**不带说明文字** —— 带说明就像教程，不像软件）
STEPS: tuple[str, ...] = ("① 选文件夹", "② 分析", "③ 处理")


class Stepper(QWidget):
    """一行状态指示：① 选文件夹 → ② 分析 → ③ 处理。

    - 已完成的打勾、当前的加粗高亮、未到的是空心圆
    - **不写说明文字**（早期记录：带说明像教程，让软件看着像未成品）
    - 设置页关掉「显示界面提示」→ 整条隐藏（由 MainWindow 控制）
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._label = QLabel()
        self._label.setWordWrap(False)
        self._label.setTextFormat(Qt.RichText)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, theme.px("xs"), 0, theme.px("xs"))
        layout.setSpacing(0)
        layout.addWidget(self._label)

        self._current = 0
        self.render()

    def set_state(self, current: int) -> None:
        """`current`：0=选文件夹 1=分析 2=处理（大于 2 表示都完成了）。"""
        self._current = current
        self.render()

    @property
    def current(self) -> int:
        return self._current

    def plain_text(self) -> str:
        """当前显示的文字（去掉标签，给测试断言用）。"""
        import re

        return re.sub(r"<[^>]+>", "", self._label.text())

    def render(self) -> None:
        parts: list[str] = []
        for index, name in enumerate(STEPS):
            mark, color = self._mark(index)
            weight = "bold" if index == self._current else "normal"
            parts.append(
                f'<span style="color:{color};font-weight:{weight};">{mark} {name}</span>'
            )
        arrow = f'<span style="color:{theme.COLOR["muted"]};">&nbsp;→&nbsp;</span>'
        self._label.setText(arrow.join(parts))

    def _mark(self, index: int) -> tuple[str, str]:
        if index < self._current:
            return "✓", theme.COLOR["muted"]
        if index == self._current:
            return "●", theme.COLOR["accent"]
        return "○", theme.COLOR["muted"]
