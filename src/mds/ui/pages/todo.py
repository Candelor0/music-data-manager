"""待办页（主界面）：600 行压成 4 行。

界面只是把 `render.render_todo_page()` 生成的 HTML 放上去；
点「全部写入 / 去处理 / 先看看清单」时发一个信号出去，由主窗口接住。
"""

from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QTextBrowser, QVBoxLayout, QWidget

from ..render import render_todo_page


class TodoPage(QWidget):
    #: `write:<组>` 或 `open:<组>`
    action = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view = QTextBrowser()
        self._view.setOpenExternalLinks(False)
        self._view.anchorClicked.connect(self._on_anchor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self._view)

    def show_todo(
        self,
        cards: list[dict],
        *,
        headline: str = "",
        library: str = "",
        progress: str = "",
        notice: str = "",
        last_batch: str = "",
    ) -> None:
        self._view.setHtml(
            render_todo_page(
                cards,
                headline=headline,
                library=library,
                progress=progress,
                notice=notice,
                last_batch=last_batch,
            )
        )
        self._view.verticalScrollBar().setValue(0)

    def html(self) -> str:
        """给测试断言用。"""
        return self._view.toHtml()

    def plain_text(self) -> str:
        return self._view.toPlainText()

    def _on_anchor(self, url) -> None:
        text = str(url.toString())
        if text:
            self.action.emit(text)
