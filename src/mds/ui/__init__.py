"""只读桌面界面（PyQt5）。

**本包有一条硬规则**：不得导入 `adapters/tagwriter.py`、`adapters/snapshot.py`、
`pipeline/apply.py`、`pipeline/rollback.py`。由 `tests/unit/test_ui_boundaries.py`
用代码断言保证 —— 也就是说，这个界面在代码层面**不具备修改文件的能力**。

用 `mds gui [run_id]` 打开。
"""

from __future__ import annotations

__all__ = ["run_gui"]


def run_gui(*args, **kwargs):  # pragma: no cover - 只有真正开窗时才会走到
    from .app import run_gui as _run_gui

    return _run_gui(*args, **kwargs)
