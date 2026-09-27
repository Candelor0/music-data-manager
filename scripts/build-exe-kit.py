#!/usr/bin/env python3
"""打一个「exe 构建包」交给使用者，他在 Windows 上双击即可构建 exe。

为什么单独打一个小包（而不是让他用那个 81 MB 的便携版）：
    构建只需要**源码 + 依赖清单 + 打包脚本**，不需要运行时和已装依赖。
    小包约 2 MB，传给他比 81 MB 方便得多。

包里带了 `tools/fpcalc.exe`，所以构建时不用再下载。
构建需要联网（首次装依赖）。

用法：
    uv run python scripts/build-exe-kit.py [--out 目录]
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CACHE = PROJECT_ROOT / ".build-cache"
KIT_NAME = "音乐数据管家-exe构建包"
FPCALC_URL = (
    "https://github.com/acoustid/chromaprint/releases/download/v1.5.1/"
    "chromaprint-fpcalc-1.5.1-windows-x86_64.zip"
)

#: 要放进包里的（相对项目根）
INCLUDE_FILES = (
    "pyproject.toml",
    "README.md",
    "LICENSE",
    ".env.example",
    "构建exe.bat",
)
INCLUDE_DIRS = ("src", "scripts", "assets", "licenses", "packaging")


def _version() -> str:
    import re

    text = (PROJECT_ROOT / "src" / "mds" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__ = "([^"]+)"', text)
    return match.group(1) if match else "0.0.0"


def _ensure_fpcalc() -> Path:
    """拿到 fpcalc.exe（缓存里没有就下）。"""
    import urllib.request

    target = CACHE / "fpcalc.exe"
    if target.is_file():
        return target
    CACHE.mkdir(parents=True, exist_ok=True)
    archive = CACHE / "chromaprint-fpcalc-1.5.1-windows-x86_64.zip"
    if not archive.is_file():
        print("  下载 fpcalc.exe …")
        urllib.request.urlretrieve(FPCALC_URL, archive)  # noqa: S310
    with zipfile.ZipFile(archive) as zf:
        member = next((n for n in zf.namelist() if n.lower().endswith("fpcalc.exe")), None)
        if member is None:
            raise SystemExit("压缩包里没有 fpcalc.exe")
        target.write_bytes(zf.read(member))
    return target


HOWTO = """音乐数据管家 · 怎么构建 exe
===============================

这个包里是"构建 exe"需要的东西。**必须在 Windows 上做**
（把 Python 程序打成 exe 的工具只能在 Windows 上跑）。


三步
----
1. 把**整个文件夹**解压出来（别只拖里面的文件），建议放到纯英文路径，
   例如 D:\\mds-build

2. 双击 `构建exe.bat`

3. 等 5～10 分钟。看到「构建成功」就好了，产物在：
      dist\\音乐数据管家-{version}.zip
   解压那个 zip，双击里面的 `音乐数据管家.exe` 即可（无控制台窗口、带图标）。


需要什么
--------
- 联网（首次要下载依赖，约几百 MB）
- Python 或 uv 二者之一：
    如果机器上没有，脚本会提示你装哪个、去哪装
    （之前验证环境时装过 uv 的话，这次直接能用）


出问题了怎么办
--------------
脚本会把过程写进 `构建日志.txt`。
**把它发给开发**即可 —— 日志里有完整的报错信息。


它到底做了什么（给开发看的）
----------------------------
1. 找一个 Python（优先 uv → py 启动器 → python），建一个干净的虚拟环境
2. 装依赖 + PyInstaller
3. 跑 `scripts\\mds.spec` 打包：`--windowed`（无控制台）+ 图标 +
   `collect_submodules` 收集 mds/picard/mutagen/keyring.backends 这些**动态导入**的模块
4. 把 `tools\\fpcalc.exe`、`.env.example`、`README.md`、`LICENSE`、`许可证\\`
   一起放到 exe 旁边
   （程序的 `project_root()` 在打包后等于 exe 所在目录，所以它找得到）
5. 自检：跑一次命令行自检并检查输出内容；再启动一次界面，确认进程没立刻退出
6. 打成 zip（优先用系统自带 tar.exe，它能扛更长的路径）

产物文件夹里除了 exe，还有 `tools\\fpcalc.exe`（声学指纹）、`.env.example`、
`README.md`、`LICENSE` 与 `许可证\\`（第三方许可证全文，GPL 要求随包提供），
以及一个 **`诊断工具.bat`** —— 双击它会把环境自检结果写到
`diagnostics.txt` 并显示出来（出问题时把它发给我最有用）。

注：`diagnostics.txt` 是用的人**自己那台机器**上的产物，不随包分发。
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="打 exe 构建包")
    parser.add_argument("--out", default=str(Path.home() / "Downloads"))
    args = parser.parse_args()

    version = _version()
    out_dir = Path(args.out).expanduser()
    stage = PROJECT_ROOT / ".build-kit"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    print(f"打「{KIT_NAME}」（版本 {version}）")
    for name in INCLUDE_FILES:
        source = PROJECT_ROOT / name
        if source.is_file():
            shutil.copy2(source, stage / name)
    for name in INCLUDE_DIRS:
        source = PROJECT_ROOT / name
        if source.is_dir():
            shutil.copytree(
                source,
                stage / name,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
            )

    tools = stage / "tools"
    tools.mkdir(exist_ok=True)
    shutil.copy2(_ensure_fpcalc(), tools / "fpcalc.exe")

    (stage / "怎么构建.txt").write_text(HOWTO.format(version=version), encoding="utf-8-sig")

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{KIT_NAME}.zip"
    if target.exists():
        target.unlink()
    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                zf.write(path, Path(KIT_NAME) / path.relative_to(stage))
                count += 1
    shutil.rmtree(stage)

    print(f"完成：{target}（{count} 个文件，{target.stat().st_size / 1024 / 1024:.1f} MB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
