"""按专辑裁决页：同一张专辑选一次候选，落到这个目录里的每一首歌。

真实样例库里 12 首同属一张专辑的歌 —— 逐条点 12 次是折磨，选 1 次是合理成本。
"""

from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QHBoxLayout, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from ..render import render_album_choices


class AlbumPage(QWidget):
    #: (目录, 发行版 MBID)
    choice_made = pyqtSignal(str, str)
    back_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._views: list[dict] = []

        self._view = QTextBrowser()
        self._view.setOpenExternalLinks(False)
        self._view.anchorClicked.connect(self._on_anchor)

        self._back = QPushButton("← 返回待办")
        self._back.clicked.connect(self.back_requested.emit)
        top = QHBoxLayout()
        top.addWidget(self._back)
        top.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addLayout(top)
        layout.addWidget(self._view)

    def show_albums(self, views: list[dict]) -> None:
        self._views = list(views)
        self._view.setHtml(render_album_choices(self._views))
        self._view.verticalScrollBar().setValue(0)

    def plain_text(self) -> str:
        return self._view.toPlainText()

    def _on_anchor(self, url) -> None:
        text = str(url.toString())
        if not text.startswith("pick:"):
            return
        payload = text[len("pick:") :]
        folder, _, index_text = payload.rpartition("|")
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
