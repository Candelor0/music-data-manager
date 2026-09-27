"""Qt 表格模型：文件表 + 候选表。

只做展示，不持有任何写文件能力。
"""

from __future__ import annotations

from PyQt5.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt5.QtWidgets import QStyledItemDelegate

from . import theme
from .viewmodel import CandidateView, FileRow

#: 按处理结果给出的行底色（浅色，保证文字仍然清晰）
#: Qt 的 rowCount/columnCount 签名要求默认参数是一个 QModelIndex。
#: 模块级建一个复用即可（每次调用都新建会踩 B008，且没必要）。
_ROOT_INDEX = QModelIndex()

MARK_COLORS: dict[str, str] = {
    "no_op": theme.group_style("none")["bg"],
    "fill_missing": theme.group_style("safe")["bg"],
    "cleanup": theme.group_style("safe")["bg"],
    "keep_existing": theme.group_style("conflict")["bg"],
    "ask_user": theme.group_style("choose")["bg"],
    "no_evidence": theme.group_style("none")["bg"],
}


class FileTableModel(QAbstractTableModel):
    #: 8 列减到 **6 列**
    #: （去掉冗余的「状态」列 —— 状态用行首图标表示；艺术家并在曲名那一列）
    #:
    #: 为什么不把「文件名 / 曲名」挤成一格两行：Qt 的多行单元格在
    #: 样式表 + macOS 下**只画一行**（第二行被吞掉，实测反复验证过）。
    #: 分成两列更可靠，也更好对。
    HEADERS = ("✓", "文件名", "曲名", "专辑", "年份", "改动")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: list[FileRow] = []

    # ── Qt 接口 ──────────────────────────────────────────
    def rowCount(self, parent=_ROOT_INDEX) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=_ROOT_INDEX) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if orientation != Qt.Horizontal or role != Qt.DisplayRole:
            return None
        return self.HEADERS[section]

    def flags(self, index):
        base = super().flags(index)
        if index.isValid() and index.column() == 0:
            row = self._rows[index.row()]
            if row.writable:
                return base | Qt.ItemIsUserCheckable
        return base

    def setData(self, index, value, role=Qt.EditRole):  # noqa: N802
        """只允许改第 0 列的勾选状态。"""
        if not index.isValid() or index.column() != 0 or role != Qt.CheckStateRole:
            return False
        row = self._rows[index.row()]
        if not row.writable:
            return False
        row.checked = Qt.CheckState(value) == Qt.Checked
        # 整行都要重画 —— 不然只有第 0 列变蓝，看不出"这行被选了"
        self.dataChanged.emit(
            self.index(index.row(), 0),
            self.index(index.row(), self.columnCount() - 1),
            [Qt.CheckStateRole, Qt.BackgroundRole],
        )
        return True

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        col = index.column()

        if role == Qt.CheckStateRole and col == 0:
            if not row.writable:
                return None
            return int(Qt.Checked if row.checked else Qt.Unchecked)
        if role == Qt.DisplayRole:
            return self._display(row, col)
        if role == Qt.TextAlignmentRole and col in (0, 4, 5):
            return int(Qt.AlignCenter)
        if role == Qt.ToolTipRole:
            if index.column() == 2 and row.artist:
                return f"{row.title}\n艺术家：{row.artist}"
            return self._tooltip(row)
        if role == Qt.BackgroundRole:
            from PyQt5.QtGui import QBrush, QColor

            # 勾选的行整行淡蓝（比只勾一个小框明显得多）——
            # 这是早期反馈：看不出到底选中了没有
            if row.checked:
                return QBrush(QColor(theme.COLOR["accent_weak"]))
            return QBrush(QColor(MARK_COLORS.get(row.action, theme.COLOR["panel"])))
        return None

    # ── 便捷方法 ─────────────────────────────────────────
    def set_rows(self, rows: list[FileRow]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def row_at(self, position: int) -> FileRow | None:
        if 0 <= position < len(self._rows):
            return self._rows[position]
        return None

    @property
    def rows(self) -> list[FileRow]:
        return list(self._rows)

    def checked_ids(self) -> list[int]:
        """当前勾选、且允许写入的条目 id。"""
        return [r.item_id for r in self._rows if r.checked and r.writable]

    def set_all_checked(self, checked: bool) -> None:
        """全选 / 全不选（**只影响允许写入的行**）。

        ⚠️ 这里**不能**发 layoutAboutToBeChanged / layoutChanged：
        行数没有变化，而那两个信号会让表头**重算 ResizeToContents 列的宽度** ——
        「专辑」列会按最长内容撑开，把拉伸的「文件名/曲名」挤到只剩几十像素
        （实测：文件名被压成「0…」）。只发 dataChanged 就够重画了。
        """
        if not self._rows:
            return
        for row in self._rows:
            row.checked = checked and row.writable
        for row_index in range(len(self._rows)):
            self.dataChanged.emit(
                self.index(row_index, 0),
                self.index(row_index, self.columnCount() - 1),
                [Qt.CheckStateRole, Qt.BackgroundRole],
            )

    def _display(self, row: FileRow, col: int) -> str:
        """5 列。文件名那一格放两行：文件名（次要色）+ 曲名 · 艺术家。"""
        if col == 0:
            return ""
        if col == 1:
            return f"{row.mark} {row.name}".strip()
        if col == 2:
            # 只放曲名 —— 拼上艺术家会被挤成省略号（实测），
            # 艺术家改放提示里（同一张专辑里通常都一样，不占列）
            return row.title
        if col == 3:
            return row.album or "（无专辑）"
        if col == 4:
            return row.date
        if col == 5:
            if row.n_conflicts:
                return f"{row.n_changes} 处 · ⚠冲突 {row.n_conflicts}"
            return f"{row.n_changes} 处" if row.n_changes else "—"
        return ""

    def _tooltip(self, row: FileRow) -> str:
        parts = [row.path or row.name]
        if row.write_allowed:
            parts.append("已生成写入计划（本界面不会执行）")
        elif row.write_reason:
            parts.append(f"不写入：{row.write_reason}")
        if row.n_conflicts:
            parts.append("注意：已有标签与同目录共识不一致（不会自动改动）")
        return "\n".join(parts)


class RowHighlightDelegate(QStyledItemDelegate):
    """给勾选的行在最左侧画一条主色竖条。

    底色由 model 的 BackgroundRole 负责（整行淡蓝），
    这里只负责那条竖条 —— 扫一眼就能数出选了哪些行。
    """

    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        if index.column() != 0:
            return
        model = index.model()
        row = model.row_at(index.row()) if hasattr(model, "row_at") else None
        if row is None or not row.checked:
            return
        from PyQt5.QtGui import QColor

        painter.save()
        painter.fillRect(
            option.rect.left(),
            option.rect.top(),
            theme.BAR_WIDTH,
            option.rect.height(),
            QColor(theme.COLOR["accent"]),
        )
        painter.restore()


class CandidateTableModel(QAbstractTableModel):
    HEADERS = ("", "专辑", "年份", "国家", "格式", "时长", "时长差", "分数")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: list[CandidateView] = []

    def rowCount(self, parent=_ROOT_INDEX) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=_ROOT_INDEX) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if orientation != Qt.Horizontal or role != Qt.DisplayRole:
            return None
        return self.HEADERS[section]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        if role == Qt.DisplayRole:
            return self._display(row, index.column())
        if role == Qt.ToolTipRole:
            return f"发行版 {row.release_mbid}\n标签：{row.label_name or '—'}\n状态：{row.status or '—'}"
        if role == Qt.TextAlignmentRole and index.column() >= 5:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def set_rows(self, rows: list[CandidateView]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def row_at(self, position: int) -> CandidateView | None:
        if 0 <= position < len(self._rows):
            return self._rows[position]
        return None

    def _display(self, row: CandidateView, col: int) -> str:
        if col == 0:
            return "★" if row.chosen else ""
        if col == 1:
            return row.album
        if col == 2:
            return row.date
        if col == 3:
            return row.country
        if col == 4:
            return row.fmt
        if col == 5:
            return row.length
        if col == 6:
            return row.delta
        if col == 7:
            return f"{row.score:.2f}"
        return ""
