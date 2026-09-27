"""进度与取消面板：显示「已完成 / 总数 / 当前文件」。

⚠️ 这里的文本长度**不能影响窗口宽度**（实测踩到的界面问题）：

    进度行是 `[12/57] 某个很长的文件名.flac  ✅ 已写入并校验  大小+58,651  date=2013`，
    长度随文件而变。Qlabel 默认会把自己的宽度需求报给布局，于是
    ① 每换一个文件窗口边框就跳一下；② 写到超长路径时窗口被撑得很宽，
    而且**写完之后停在那个宽度上**。

    所以这里的做法是：横向 sizePolicy 设为 Ignored（尺寸需求不参与布局），
    再用 fontMetrics 按当前可用宽度**省略号截断**，完整文本放 tooltip。
"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QSizePolicy, QWidget

from .. import theme


class ProgressPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(14)

        self._count = QLabel("就绪")
        self._count.setMinimumWidth(190)
        self._current = QLabel("")
        self._current.setStyleSheet(f"color:{theme.COLOR['muted']};")
        # 关键：进度行的宽度需求不参与布局，超长时用省略号
        self._current.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._current.setMinimumWidth(0)
        self._full_message = ""

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.addWidget(self._bar, 3)
        layout.addWidget(self._count, 0)
        layout.addWidget(self._current, 4)

    def set_progress(self, done: int, total: int, current: str = "") -> None:
        total = max(total, 0)
        done = max(done, 0)
        self._bar.setRange(0, max(total, 1))
        self._bar.setValue(min(done, max(total, 1)))
        self._count.setText(f"{done} / {total}" if total else "—")
        self._current.setText(current)

    def set_message(self, text: str) -> None:
        """显示一行进度。超长会被省略，完整内容在鼠标悬停的提示里。"""
        self._full_message = text
        self._current.setToolTip(text)
        self._refresh_message()

    def _refresh_message(self) -> None:
        if not self._full_message:
            self._current.setText("")
            return
        available = max(self._current.width() - 4, 60)
        self._current.setText(
            self._current.fontMetrics().elidedText(self._full_message, Qt.ElideRight, available)
        )

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 接口
        super().resizeEvent(event)
        self._refresh_message()

    def reset(self) -> None:
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._count.setText("就绪")
        self.set_message("")

    @property
    def full_message(self) -> str:
        """没被省略的完整进度文本（给测试断言用）。"""
        return self._full_message

    @property
    def value(self) -> int:
        return int(self._bar.value())

    @property
    def maximum(self) -> int:
        return int(self._bar.maximum())
