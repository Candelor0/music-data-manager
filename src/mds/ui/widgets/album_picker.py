"""按专辑选版本（🟡 组的操作方式）。

其他三组都是"勾选 → 写入"，但 🟡 不一样：它是「**每张专辑选一个版本**」。
真实样例库里 12 首同属一张专辑，逐条点 12 次是折磨，选 1 次是合理成本。

所以这里**就地展开**（不跳页）：点「选一个版本」→ 展开候选 → 选完这组就定了。
"""

from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QTextBrowser, QVBoxLayout, QWidget

from ..render import render_album_choices


class AlbumPicker(QWidget):
    #: (专辑目录, 发行版 MBID)
    choice_made = pyqtSignal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._views: list[dict] = []
        self._view = QTextBrowser()
        self._view.setOpenExternalLinks(False)
        self._view.anchorClicked.connect(self._on_anchor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)

    def show_choices(self, views: list[dict]) -> None:
        self._views = list(views)
        self._view.setHtml(render_album_choices(self._views))
        self._view.verticalScrollBar().setValue(0)

    def text_content(self) -> str:
        return self._view.toPlainText()

    @property
    def empty(self) -> bool:
        return not self._views

    def _on_anchor(self, url) -> None:
        text = str(url.toString())
        if not text.startswith("pick:"):
            return
        folder, _, index_text = text[len("pick:") :].rpartition("|")
        view = next((v for v in self._views if str(v.get("folder")) == folder), None)
        if view is None:
            return
        try:
            index = int(index_text)
        except ValueError:
            return
        release_ids = view.get("release_ids") or []
        if 0 <= index < len(release_ids):
            self.choice_made.emit(folder, str(release_ids[index]))
