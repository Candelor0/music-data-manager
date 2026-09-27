"""界面小组件。每个组件只管自己那点事，业务逻辑都在 viewmodel / pipeline 里。"""

from __future__ import annotations

__all__ = ["AlbumPicker", "DiffPanel", "FilterTabs", "ProgressPanel", "StartPanel", "Stepper"]


def __getattr__(name: str):
    # 延迟导入：只 import 包时不强制拉 Qt
    mapping = {
        "DiffPanel": ".diff_panel",
        "ProgressPanel": ".progress_bar",
        "Stepper": ".stepper",
        "FilterTabs": ".filter_tabs",
        "StartPanel": ".start_panel",
        "AlbumPicker": ".album_picker",
    }
    if name in mapping:
        import importlib

        module = importlib.import_module(mapping[name], __name__)
        return getattr(module, name)
    raise AttributeError(name)
