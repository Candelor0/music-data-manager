"""主页面：一个页面管三种样子（起始 / 待开始 / 有结果）。

早期反馈的几条：

    「待办页面在没有任务时是个大白框，什么内容都没有……要不要直接取消待办页面，
      让功能页面常驻？」
    「对于新用户来说刚刚进入时工作区显示是一个大白框，会有些让用户不知道
      怎么上手使用」

所以不再有"待办页 / 清单页 / 按专辑裁决页"三页来回 —— 全在这一个页面里：

    起始页  →  待开始页  →  结果页（筛选标签 + 清单 + 详情）
                              └ 🟡 标签选中时，清单换成"按专辑选版本"

本页面只负责**展示与收集用户动作**；读写数据库由 MainWindow 做，
通过上面那些信号上来、通过 `show_*` / `set_*` 下来。
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..models import FileTableModel, RowHighlightDelegate
from ..viewmodel import FileRow, TodoCard
from ..widgets.album_picker import AlbumPicker
from ..widgets.diff_panel import DiffPanel
from ..widgets.filter_tabs import FilterTabs
from ..widgets.start_panel import StartPanel

PANE_START = 0
PANE_RESULTS = 1
LIST_TABLE = 0
LIST_ALBUMS = 1

FILTER_LABELS: dict[str, str] = {
    "all": "全部",
    "changed": "有改动",
    "conflict": "有冲突",
    "nochange": "没改动",
}


class MainPage(QWidget):
    choose_folder = pyqtSignal()
    change_folder = pyqtSignal()
    start = pyqtSignal()
    write_requested = pyqtSignal(list)      # item ids
    album_picked = pyqtSignal(str, str)     # 目录, 发行版 MBID
    row_selected = pyqtSignal(int)          # item id
    group_changed = pyqtSignal(str)         # 换了筛选标签，窗口据此加载数据

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[FileRow] = []
        self._selected_group = "safe"

        self._panes = QStackedWidget()
        self._start_panel = StartPanel()
        self._start_panel.choose_folder.connect(self.choose_folder.emit)
        self._start_panel.change_folder.connect(self.change_folder.emit)
        self._start_panel.start.connect(self.start.emit)
        # 起始页 / 待开始页也放进一块面板里 —— 浮在灰桌上，不再是「大白框」
        start_wrap = QWidget()
        # 外面这层只是桌面底，**不画面板** ——
        # 大面板会撑满整个区域，内容挤在顶部就成了"大白框"（实测）
        theme.style_container(start_wrap, "desk")
        wrap_layout = QVBoxLayout(start_wrap)
        wrap_layout.setContentsMargins(0, 0, 0, 0)
        wrap_layout.addWidget(self._start_panel)
        self._panes.addWidget(start_wrap)
        self._panes.addWidget(self._build_results_pane())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._panes)

        self.show_first_run()

    # ── 结果页的搭法 ─────────────────────────────────────
    def _build_results_pane(self) -> QWidget:
        """结果区 = **一块白面板**，里面靠分区带划开表头和内容。

        设计说明：面板有边框，顶部是一条**筛选带**（浅灰底 + 底线），
        下面是内容 —— 靠底色与分隔线避免整屏糊成一片（「甲-2 分区带式」）。
        """
        pane = QWidget()
        theme.style_container(pane, "panel")
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        theme.style_container(header, "tabsRow")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(
            theme.px("md"), theme.px("sm"), theme.px("md"), theme.px("sm")
        )
        header_layout.setSpacing(theme.px("sm"))

        self._headline = QLabel("")
        self._headline.setObjectName("section")
        header_layout.addWidget(self._headline)

        self._tabs = FilterTabs()
        self._tabs.changed.connect(self._on_tab_changed)
        header_layout.addWidget(self._tabs)

        header_layout.addLayout(self._build_toolbar())
        layout.addWidget(header)

        self._lists = QStackedWidget()
        self._lists.addWidget(self._build_table())
        self._albums = AlbumPicker()
        self._albums.choice_made.connect(self.album_picked.emit)
        self._lists.addWidget(self._albums)

        self._diff = DiffPanel()
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._lists)
        splitter.addWidget(self._diff)
        # 列表是主任务、详情是辅助 —— 默认给列表多一点宽度
        # （之前 5:4，详情区平时是空的，白占地方，文件名被挤到截断）
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([820, 540])
        layout.addWidget(splitter, 1)

        self._footnote = QLabel("")
        self._footnote.setObjectName("caption")
        self._footnote.setWordWrap(True)
        self._footnote.setContentsMargins(
            theme.px("md"), theme.px("xs"), theme.px("md"), theme.px("sm")
        )
        layout.addWidget(self._footnote)
        return pane

    def _build_toolbar(self) -> QHBoxLayout:
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜文件名 / 曲名 / 艺术家 / 专辑")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)

        self._only = QLineEdit()
        self._only.setVisible(False)  # 占位：筛选下拉以后再加

        self._select_all = QPushButton("全选")
        self._select_none = QPushButton("全不选")
        self._write_group = QPushButton("")
        self._write_group.setObjectName("primary")
        self._select_all.clicked.connect(lambda: self._set_all(True))
        self._select_none.clicked.connect(lambda: self._set_all(False))
        self._write_group.clicked.connect(self._on_write_group)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.px("sm"))
        row.addWidget(self._search, 1)
        row.addWidget(self._select_all)
        row.addWidget(self._select_none)
        row.addWidget(self._write_group)
        return row

    def _build_table(self) -> QWidget:
        self._model = FileTableModel(self)
        self._model.dataChanged.connect(lambda *_: self._refresh_counts())
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setItemDelegate(RowHighlightDelegate(self._table))
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        # 斑马纹 + 网格线是表格骨架，第一版关掉它正是「像一张白纸」的元凶之一
        # ⚠️ Qt 的默认表格绘制**只画一行**，不认 \n ——
        # 不开 wordWrap 的话"文件名/曲名"那两行里的第二行会被吞掉（实测踩到）
        self._table.setWordWrap(True)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(True)
        self._table.setFrameShape(QTableView.NoFrame)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(theme.ROW_HEIGHT)
        header = self._table.horizontalHeader()
        # 列宽策略：两张主列拉伸，其余**固定**宽度。
        # 不用 ResizeToContents —— 长专辑名会把列撑开，把主列挤没（实测踩到）。
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        self._table.setColumnWidth(0, 36)
        header.setSectionResizeMode(1, QHeaderView.Stretch)   # 文件名
        header.setSectionResizeMode(2, QHeaderView.Stretch)   # 曲名
        for column, width in ((3, 200), (4, 64), (5, 96)):
            header.setSectionResizeMode(column, QHeaderView.Fixed)
            self._table.setColumnWidth(column, width)
        self._table.selectionModel().currentRowChanged.connect(self._on_row)
        self._table.doubleClicked.connect(self._on_double_click)
        return self._table

    # ── 三种样子 ─────────────────────────────────────────
    def show_first_run(self) -> None:
        self._start_panel.show_first_run()
        self._panes.setCurrentIndex(PANE_START)

    def show_ready(self, **kwargs) -> None:
        self._start_panel.show_ready(**kwargs)
        self._panes.setCurrentIndex(PANE_START)

    def show_results(self, cards: list[TodoCard], *, headline: str = "", selected: str = "safe") -> None:
        self._headline.setText(headline)
        self._tabs.set_cards(cards, selected=selected)
        self._panes.setCurrentIndex(PANE_RESULTS)

    def set_group_rows(self, key: str, rows: list[FileRow]) -> None:
        """设置当前筛选组要显示的条目。

        **不在这里改勾选态** —— 勾选与否由 `core/selectrules.py` 按规则算好，
        随行一起传进来（`FileRow.checked`）。
        """
        self._selected_group = key
        self._rows = list(rows)
        self._apply_filter()

    def set_albums(self, views: list[dict]) -> None:
        self._albums.show_choices(views)

    def set_detail(self, detail: dict) -> None:
        self._diff.show_detail(detail)

    def set_note(self, text: str) -> None:
        self._footnote.setText(text)

    # ── 内部 ─────────────────────────────────────────────
    def _on_tab_changed(self, key: str) -> None:
        self._selected_group = key
        self.group_changed.emit(key)
        # 🟡 组的操作方式不一样：不是勾选，而是"每张专辑选一个版本"
        self._lists.setCurrentIndex(LIST_ALBUMS if key == "choose" else LIST_TABLE)
        self._select_all.setVisible(key != "choose")
        self._select_none.setVisible(key != "choose")
        self._write_group.setVisible(key != "choose")
        self._search.setVisible(key != "choose")

    def _apply_filter(self) -> None:
        needle = self._search.text().strip().lower()
        if not needle:
            self._model.set_rows(self._rows)
        else:
            self._model.set_rows(
                [
                    row
                    for row in self._rows
                    if any(
                        needle in field.lower()
                        for field in (row.name, row.title, row.artist, row.album, row.path)
                    )
                ]
            )
        self._refresh_counts()

    def _refresh_counts(self) -> None:
        checked = len(self._model.checked_ids())
        writable = sum(1 for row in self._model.rows if row.writable)
        key = self._selected_group
        if key == "safe":
            self._write_group.setText(f"全部写入这 {writable} 首" if writable else "没有可写入的")
        else:
            self._write_group.setText(f"写入勾选的 {checked} 首" if checked else "写入勾选项")
        self._write_group.setEnabled(checked > 0)

    def _set_all(self, checked: bool) -> None:
        self._model.set_all_checked(checked)
        self._refresh_counts()

    def _on_row(self, current, _previous=None) -> None:
        if current.isValid():
            row = self._model.row_at(current.row())
            if row is not None:
                self.row_selected.emit(row.item_id)

    def _on_double_click(self, index) -> None:
        """双击整行 = 切换勾选（按钮/空格键之外的第二种顺手方式）。"""
        if not index.isValid():
            return
        row = self._model.row_at(index.row())
        if row is None or not row.writable:
            return
        from PyQt5.QtCore import Qt as _Qt

        self._model.setData(
            self._model.index(index.row(), 0),
            _Qt.Unchecked if row.checked else _Qt.Checked,
            _Qt.CheckStateRole,
        )

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt 接口
        """空格键 = 切换当前行的勾选。"""
        if event.key() == Qt.Key_Space:
            current = self._table.currentIndex()
            if current.isValid():
                self._on_double_click(current)
                return
        super().keyPressEvent(event)

    def _on_write_group(self) -> None:
        ids = self._model.checked_ids()
        if ids:
            self.write_requested.emit(ids)

    # ── 给窗口与测试 ─────────────────────────────────────
    @property
    def tabs(self) -> FilterTabs:
        return self._tabs

    @property
    def model(self) -> FileTableModel:
        return self._model

    @property
    def albums(self) -> AlbumPicker:
        return self._albums

    @property
    def start_panel(self) -> StartPanel:
        return self._start_panel

    @property
    def diff_panel(self) -> DiffPanel:
        return self._diff

    @property
    def current_pane(self) -> int:
        return self._panes.currentIndex()

    @property
    def current_list(self) -> int:
        return self._lists.currentIndex()

    def write_button(self) -> QPushButton:
        return self._write_group

    def search_text(self) -> str:
        return self._search.text()

    def set_search(self, text: str) -> None:
        self._search.setText(text)
