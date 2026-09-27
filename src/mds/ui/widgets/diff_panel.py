"""右侧「差异预览」面板：当前标签 → 建议改动 → 候选发行版。

**只读**：面板里没有任何可编辑控件，也没有写入按钮。
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QSplitter,
    QTableView,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..models import CandidateTableModel
from ..render import render_candidates_note, render_detail_html


def empty_hint_html() -> str:
    """没选中任何条目时的提示（颜色字号一律取主题）。"""
    return (
        f'<div style="color:{theme.COLOR["muted"]};padding:{theme.px("md")}px;'
        f'{theme.html_font("body")}">'
        "选择左侧任意条目，此处显示：<br>"
        "· 当前标签<br>· 建议改动与依据<br>"
        "· 同一文件夹内其他曲目的标注<br>"
        "· MusicBrainz 匹配到的候选"
        "</div>"
    )


class DiffPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._detail_view = QTextBrowser()
        self._detail_view.setOpenExternalLinks(False)
        self._detail_view.setHtml(empty_hint_html())

        self._cand_note = QLabel("")
        self._cand_note.setWordWrap(True)
        self._cand_note.setStyleSheet(
            f"color:{theme.COLOR['muted']};"
            f"{theme.html_font('caption')}padding:2px 4px;"
        )

        self._cand_model = CandidateTableModel(self)
        self._cand_view = QTableView()
        self._cand_view.setModel(self._cand_model)
        self._cand_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._cand_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._cand_view.setAlternatingRowColors(True)
        self._cand_view.verticalHeader().setVisible(False)
        self._cand_view.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._cand_view.setToolTip("MusicBrainz 返回的候选发行版")

        self._cand_box = cand_box = QWidget()
        cand_layout = QVBoxLayout(cand_box)
        cand_layout.setContentsMargins(0, 0, 0, 0)
        cand_layout.setSpacing(2)
        title = QLabel("<b>MusicBrainz 候选发行版</b>")
        cand_layout.addWidget(title)
        cand_layout.addWidget(self._cand_note)
        cand_layout.addWidget(self._cand_view)

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self._detail_view)
        splitter.addWidget(cand_box)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*([theme.px("xs")] * 4))
        layout.addWidget(splitter)
        # 一开始没有候选 —— 直接收起，别画一张只有表头的空表格
        cand_box.setVisible(False)

    # ── 公开接口 ─────────────────────────────────────────
    def show_detail(self, detail: dict) -> None:
        self._detail_view.setHtml(
            render_detail_html(detail, terms_enabled=bool(detail.get("terms_enabled", True)))
        )
        self._detail_view.verticalScrollBar().setValue(0)
        candidates = detail.get("candidates") or []
        self._cand_note.setText(render_candidates_note(detail))
        self._cand_model.set_rows(candidates)
        # 没有候选时**整块收起来** —— 画一张只有表头的空表格看起来像界面坏了
        # （实测反馈）
        self._cand_box.setVisible(bool(candidates))
        if candidates:
            self._cand_view.resizeColumnsToContents()
            # 列宽加上限：专辑名很长的候选会把这一列撑到几百像素，
            # 进而把窗口顶宽（右侧面板在 splitter 里，最小宽度会传上去）
            header = self._cand_view.horizontalHeader()
            for column in range(self._cand_model.columnCount()):
                header.resizeSection(column, min(header.sectionSize(column), 200))
            header.setSectionResizeMode(1, QHeaderView.Stretch)

    def show_message(self, text: str) -> None:
        self._detail_view.setHtml(
            f'<div style="color:{theme.COLOR["muted"]};padding:{theme.px("md")}px;'
            f'{theme.html_font("body")}">{text}</div>'
        )
        self._cand_note.setText("")
        self._cand_model.set_rows([])

    def clear(self) -> None:
        self._detail_view.setHtml(empty_hint_html())
        self._cand_note.setText("")
        self._cand_model.set_rows([])
        self._cand_box.setVisible(False)

    # 供测试断言
    @property
    def candidate_model(self) -> CandidateTableModel:
        return self._cand_model

    def detail_html(self) -> str:
        return self._detail_view.toHtml()
