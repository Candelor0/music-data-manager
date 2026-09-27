"""清单页：展开某一组后，搜索 / 筛选 / 勾选 / 逐条裁决。

勾选框只在**允许写入**的行上可用；默认勾选由 `core/selectrules.py` 决定
（只有"填空 + 安全清洗"会被默认勾上）。
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..models import FileTableModel
from ..viewmodel import FileRow
from ..widgets.diff_panel import DiffPanel

FILTERS: tuple[tuple[str, str], ...] = (
    ("all", "全部"),
    ("changed", "有改动"),
    ("conflict", "有冲突"),
    ("error", "没查到的"),
)


class ListPage(QWidget):
    write_requested = pyqtSignal(list)   # item ids
    back_requested = pyqtSignal()
    row_selected = pyqtSignal(int)       # item id

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._all: list[FileRow] = []

        self._title = QLabel("")
        self._title.setStyleSheet(f"{theme.html_font('title')}font-weight:bold;")
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜文件名 / 曲名 / 艺术家 / 专辑（直接打字即可）")
        self._search.textChanged.connect(self._apply_filter)
        self._filter = QComboBox()
        for key, label in FILTERS:
            self._filter.addItem(label, key)
        self._filter.currentIndexChanged.connect(self._apply_filter)

        self._model = FileTableModel(self)
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self._table.selectionModel().currentRowChanged.connect(self._on_row)

        self._count = QLabel("")
        self._select_all = QPushButton("全选")
        self._select_none = QPushButton("全不选")
        self._write = QPushButton("写入勾选项")
        self._write.setEnabled(False)
        self._back = QPushButton("← 返回待办")
        self._select_all.clicked.connect(lambda: self._set_all(True))
        self._select_none.clicked.connect(lambda: self._set_all(False))
        self._write.clicked.connect(self._on_write)
        self._back.clicked.connect(self.back_requested.emit)
        self._model.dataChanged.connect(lambda *_: self._refresh_count())

        top = QHBoxLayout()
        top.addWidget(self._back)
        top.addWidget(self._title, 1)
        top.addWidget(QLabel("只看："))
        top.addWidget(self._filter)
        top.addWidget(self._search, 2)

        bottom = QHBoxLayout()
        bottom.addWidget(self._count, 1)
        bottom.addWidget(self._select_all)
        bottom.addWidget(self._select_none)
        bottom.addWidget(self._write)

        self._diff = DiffPanel()
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._table)
        splitter.addWidget(self._diff)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 4)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addLayout(top)
        layout.addWidget(splitter, 1)
        layout.addLayout(bottom)

    # ── 数据 ─────────────────────────────────────────────
    def show_group(self, title: str, rows: list[FileRow]) -> None:
        self._title.setText(title)
        self._all = list(rows)
        self._apply_filter()

    def show_detail(self, detail_data: dict) -> None:
        self._diff.show_detail(detail_data)

    def set_all_checked(self, checked: bool) -> None:
        self._set_all(checked)

    # ── 内部 ─────────────────────────────────────────────
    def _set_all(self, checked: bool) -> None:
        self._model.set_all_checked(checked)
        self._refresh_count()

    def _apply_filter(self) -> None:
        needle = self._search.text().strip().lower()
        mode = self._filter.currentData() or "all"
        rows = []
        for row in self._all:
            if mode == "changed" and not row.n_changes:
                continue
            if mode == "conflict" and not row.n_conflicts:
                continue
            if mode == "error" and row.done:
                continue
            if needle and not any(
                needle in field.lower()
                for field in (row.name, row.title, row.artist, row.album, row.path)
            ):
                continue
            rows.append(row)
        self._model.set_rows(rows)
        self._refresh_count()

    def _refresh_count(self) -> None:
        checked = len(self._model.checked_ids())
        self._count.setText(f"共 {self._model.rowCount()} 条　已勾选 {checked} 条")
        self._write.setEnabled(checked > 0)

    def _on_row(self, current, _previous=None) -> None:
        if current.isValid():
            row = self._model.row_at(current.row())
            if row is not None:
                self.row_selected.emit(row.item_id)

    def _on_write(self) -> None:
        ids = self._model.checked_ids()
        if ids:
            self.write_requested.emit(ids)

    # ── 给测试 ───────────────────────────────────────────
    @property
    def model(self) -> FileTableModel:
        return self._model

    @property
    def write_button(self) -> QPushButton:
        return self._write

    def search(self, text: str) -> None:
        self._search.setText(text)

    def set_filter(self, key: str) -> None:
        index = self._filter.findData(key)
        if index >= 0:
            self._filter.setCurrentIndex(index)
