#!/usr/bin/env python3
"""检查「Windows 上打包时用到的依赖」是否都有现成的 Windows 轮子。

为什么需要这个
--------------
`uv pip compile --python-platform windows` 只能证明**依赖关系解析成功** ——
它可以用 sdist 的元数据解析，所以即使某个包在 Windows 上根本没有轮子
（必须现场编译，通常要装 VS Build Tools），解析也会"成功"。

这个坑我们踩过一次：`pyqt5-qt5` 被锁到了只有 macOS/Linux 轮子的版本，
在 Windows 上装不上。

所以这里做一件更硬的事：**逐个问 PyPI「这个版本到底有没有能在 Windows 上装的轮子」**。

⚠️ 轮子标签的判定很容易写错（第一版就写错了）：
`PyQt5-5.15.11-**cp38-abi3**-win_amd64.whl` 对 Python 3.12 是**可以装**的，
因为 `abi3`（稳定 ABI）向下兼容 —— cp38 编译的扩展能在 3.12 上跑。
只认 `cp312-abi3` 会把这种正常情况报成"装不上"，而**误报比不检查更危险**：
会让人去修一个根本不存在的问题。判定逻辑见 `wheel_is_installable()`，
并且有一组单元测试盯着它（`tests/unit/test_windows_deps.py`）。

用法
----
    uv run python scripts/check-windows-deps.py

退出码 1 表示存在「Windows 上装不了」的依赖 —— 那就不该拿去试。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYPI_JSON = "https://pypi.org/pypi/{name}/{version}/json"

#: 目标解释器：Windows + CPython 3.12 + 64 位
TARGET_MAJOR = 3
TARGET_MINOR = 12
#: 只认 64 位 Windows —— 64 位 CPython **装不了** win32（32 位）轮子，
#: 把它算通过会漏掉「只有 32 位轮子」这种真问题。
WIN_PLATFORMS = frozenset({"win_amd64", "any"})
PURE_PY_TAGS = frozenset({"py2", "py3", "py2.py3", "py310", "py311", "py312"})


def wheel_is_installable(filename: str, major: int = TARGET_MAJOR, minor: int = TARGET_MINOR) -> bool:
    """这个轮子文件能不能装在「Windows + CPython major.minor」上。

    只认三种情况：
    1. 纯 Python：`py3-none-any` 之类，平台是 any
    2. 精确匹配：`cp312-cp312-win_amd64`（或 abi 为 none）
    3. 稳定 ABI：`cp38-abi3-win_amd64` —— cp3X（X ≤ 目标）编译的 abi3 扩展向下兼容
    """
    if not filename.lower().endswith(".whl"):
        return False
    parts = filename[:-4].split("-")
    if len(parts) < 3:
        return False
    pytag, abitag, plattag = parts[-3].lower(), parts[-2].lower(), parts[-1].lower()

    if plattag not in WIN_PLATFORMS:
        return False

    # ① 纯 Python：平台 any，且 python 标签是 py 系列
    if plattag == "any":
        return any(tag in PURE_PY_TAGS for tag in pytag.split("."))

    # ② 精确匹配目标解释器（cp312-cp312 / cp312-none）
    exact = f"cp{major}{minor}"
    if pytag == exact and abitag in (exact, "none"):
        return True

    # ③ 稳定 ABI：cp3X-abi3，且 X ≤ 目标 minor（abi3 向下兼容）
    if abitag == "abi3":
        match = re.fullmatch(r"cp3(\d+)", pytag)
        if match and int(match.group(1)) <= minor:
            return True

    # ④ 少数包会发 py3-none-win_amd64（比如 pyqt5-qt5 这类纯数据包）
    return pytag == "py3"


def resolve_for_windows() -> dict[str, str]:
    """让 uv 按 Windows/AMD64 解一遍依赖，返回 {归一化包名: 版本}。"""
    cmd = [
        "uv",
        "pip",
        "compile",
        str(PROJECT_ROOT / "pyproject.toml"),
        "--python-platform",
        "windows",
        "--python-version",
        f"{TARGET_MAJOR}.{TARGET_MINOR}",
        "--no-header",
        "-q",
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    pinned: dict[str, str] = {}
    for line in out.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or "==" not in line:
            continue
        name, version = line.split("==", 1)
        pinned[re.sub(r"[-_.]+", "-", name).lower()] = version.strip()
    return pinned


def files_for(name: str, version: str) -> list[str]:
    """问 PyPI 这个版本发布了哪些文件；查不到就返回空。"""
    url = PYPI_JSON.format(name=name, version=version)
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
            data = json.load(resp)
    except Exception:  # noqa: BLE001 - 网络/404 都归为"查不到"
        return []
    return [f.get("filename", "") for f in data.get("urls", [])]


def judge(name: str, version: str, filenames: list[str]) -> tuple[bool, str]:
    """判断这个包能不能在 Windows 上直接装。"""
    if not filenames:
        return False, "PyPI 查不到（可能是私有源或版本号异常）"
    wheels = [f for f in filenames if f.lower().endswith(".whl")]
    if not wheels:
        sdists = [f for f in filenames if f.lower().endswith((".tar.gz", ".zip"))]
        return False, f"只有源码包（{sdists[0] if sdists else '?'}），Windows 上要现场编译"
    ok = [f for f in wheels if wheel_is_installable(f)]
    if ok:
        # 优先报 64 位、再优先 abi3，这样日志里看到的是"真正会装上的那个"
        picked = sorted(ok, key=lambda f: (0 if "win_amd64" in f.lower() else 1, f))[0]
        return True, picked
    return False, f"有轮子但都装不上：{', '.join(wheels[:3])}"


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 Windows 轮子是否齐全")
    parser.parse_args()

    print(f"按 Windows/AMD64 + CPython {TARGET_MAJOR}.{TARGET_MINOR} 解一遍依赖 …")
    pinned = resolve_for_windows()
    print(f"解出 {len(pinned)} 个包，逐个查 PyPI\n")

    bad: list[tuple[str, str, str]] = []
    width = max(len(n) for n in pinned)
    for name, version in sorted(pinned.items()):
        ok, detail = judge(name, version, files_for(name, version))
        print(f"  {'✅' if ok else '❌'} {name:<{width}} {version:<12} {detail}")
        if not ok:
            bad.append((name, version, detail))

    print()
    if bad:
        print(f"❌ {len(bad)} 个包在 Windows 上装不上：")
        for name, version, detail in bad:
            print(f"   · {name}=={version} —— {detail}")
        print("\n先修掉再构建（否则会在第 2 步卡住）。")
        return 1
    print(f"✅ 全部 {len(pinned)} 个包在 Windows 上都能直接装（无需现场编译）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
