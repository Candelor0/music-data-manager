"""界面分页。每个页面只管自己那点事，业务逻辑都在 viewmodel / pipeline 里。"""

from __future__ import annotations

__all__ = ["AlbumPage", "ListPage", "SettingsPage", "TodoPage"]


def __getattr__(name: str):
    # 延迟导入：只 import 包时不强制拉 Qt
    mapping = {
        "TodoPage": ".todo",
        "ListPage": ".list",
        "AlbumPage": ".album",
        "SettingsPage": ".settings",
    }
    if name in mapping:
        import importlib

        module = importlib.import_module(mapping[name], __name__)
        return getattr(module, name)
    raise AttributeError(name)
