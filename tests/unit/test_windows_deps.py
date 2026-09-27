"""盯着「这个轮子能不能装在 Windows + CPython 3.12 上」的判定逻辑。

为什么单独测它：第一版判定写错了 —— 只认 `cp312-abi3-win_amd64`，把
`PyQt5-5.15.11-cp38-abi3-win_amd64.whl`（**能装**，abi3 向下兼容）
报成了「Windows 上装不上」。误报比不检查更危险：会让人去修一个不存在的问题。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "check_windows_deps", PROJECT_ROOT / "scripts" / "check-windows-deps.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load()


@pytest.mark.parametrize(
    "filename",
    [
        # 稳定 ABI：cp3X（X ≤ 12）编译的都兼容 3.12 —— 这是第一版漏掉的那类
        "PyQt5-5.15.11-cp38-abi3-win_amd64.whl",
        "PyQt5-5.15.10-cp37-abi3-win_amd64.whl",
        "pydantic_core-2.46.5-cp39-abi3-win_amd64.whl",
        # 精确匹配
        "pydantic_core-2.46.5-cp312-cp312-win_amd64.whl",
        "pywin32-312-cp312-cp312-win_amd64.whl",
        "cffi-1.17.0-cp312-none-win_amd64.whl",
        # 纯 Python
        "mutagen-1.48.1-py3-none-any.whl",
        "six-1.17.0-py2.py3-none-any.whl",
        # 纯数据包（PyQt5-Qt5 就是这种）
        "PyQt5_Qt5-5.15.2-py3-none-win_amd64.whl",
    ],
)
def test_installable_on_windows(filename: str) -> None:
    assert mod.wheel_is_installable(filename), f"这个轮子本该判为可装：{filename}"


@pytest.mark.parametrize(
    "filename",
    [
        # 只有 macOS / Linux 的轮子
        "PyQt5-5.15.11-cp38-abi3-macosx_11_0_arm64.whl",
        "PyQt5-5.15.11-cp38-abi3-manylinux_2_17_x86_64.whl",
        # 别的 CPython 小版本（非 abi3，不能跨版本）
        "foo-1.0-cp311-cp311-win_amd64.whl",
        "foo-1.0-cp313-cp313-win_amd64.whl",
        # abi3 但比目标新（cp313 编译的不能用在 3.12 上）
        "foo-1.0-cp313-abi3-win_amd64.whl",
        # 32 位 Windows 轮子：64 位 CPython 装不了
        "foo-1.0-cp312-cp312-win32.whl",
        "PyQt5-5.15.11-cp38-abi3-win32.whl",
        # 别的平台
        "foo-1.0-cp312-cp312-manylinux_2_17_x86_64.whl",
        "foo-1.0-py3-none-macosx_11_0_arm64.whl",
        # 不是轮子
        "foo-1.0.tar.gz",
    ],
)
def test_not_installable_on_windows(filename: str) -> None:
    assert not mod.wheel_is_installable(filename), f"这个轮子本该判为不可装：{filename}"


def test_judge_reports_sdist_only_as_failure() -> None:
    ok, detail = mod.judge("foo", "1.0", ["foo-1.0.tar.gz"])
    assert not ok
    assert "源码包" in detail


def test_judge_reports_missing_package_as_failure() -> None:
    ok, detail = mod.judge("foo", "1.0", [])
    assert not ok
    assert "查不到" in detail


def test_judge_picks_the_windows_wheel() -> None:
    ok, detail = mod.judge(
        "PyQt5",
        "5.15.11",
        [
            "PyQt5-5.15.11-cp38-abi3-macosx_11_0_arm64.whl",
            "PyQt5-5.15.11-cp38-abi3-manylinux_2_17_x86_64.whl",
            "PyQt5-5.15.11-cp38-abi3-win_amd64.whl",
        ],
    )
    assert ok
    assert "win_amd64" in detail
