#!/usr/bin/env python3
"""构建「音乐数据管家 · Windows 便携版」（免安装，解压即用）。

为什么要在 macOS 上交叉构建：
    目标机器上不该先装 Python / uv / 编译器。所以包里自带
    一个 Windows 版 Python 运行时和全部依赖轮子，解压后双击即用。

怎么做到跨平台构建：
    1. Windows 嵌入式 Python —— python.org 官方发行版，解压即用
    2. 依赖 —— 用 `uv pip install --python-platform x86_64-pc-windows-msvc
       --only-binary :all:` 只下载 **Windows 预编译轮子**（不许有 sdist 构建，
       否则会混进 macOS 的二进制）
    3. fpcalc.exe —— Chromaprint 官方 Windows 预编译版

包内结构：
    音乐数据管家-试用版/
    ├── 1-先做环境自检.bat     ← 纯 ASCII，中文提示由 Python 打印
    ├── 2-打开界面.bat
    ├── 使用说明.txt
    ├── 许可证说明.txt
    ├── runtime/               ← Windows 嵌入式 Python 3.12
    │   ├── python.exe
    │   ├── python312._pth     ← 改写：把 app\\src 与 site-packages 加进去
    │   └── Lib/site-packages/ ← 全部 Windows 轮子
    └── app/                   ← 我们的代码（工程根的形状）
        ├── src/mds/...
        ├── tools/fpcalc.exe
        └── .env.example

    app/ 保持"工程根"的形状，是为了让 config.project_root() 仍然等于 app/，
    于是 tools/fpcalc.exe 与 .env 的查找逻辑一行都不用改。

用法：
    uv run python scripts/build-windows-trial.py [--out 目录]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tomllib
import urllib.request
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_VERSION = "3.12.10"
PYTHON_EMBED_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
)
FPCALC_URL = (
    "https://github.com/acoustid/chromaprint/releases/download/v1.5.1/"
    "chromaprint-fpcalc-1.5.1-windows-x86_64.zip"
)
def _version() -> str:
    """从 src/mds/__init__.py 读版本号（单一出处，不在别处再写一遍）。"""
    import re as _re

    text = (PROJECT_ROOT / "src" / "mds" / "__init__.py").read_text(encoding="utf-8")
    match = _re.search(r'__version__ = "([^"]+)"', text)
    return match.group(1) if match else "0.0.0"


PACKAGE_NAME = f"音乐数据管家-便携版-{_version()}"
WIN_PLATFORM = "x86_64-pc-windows-msvc"


def say(text: str = "") -> None:
    print(text, flush=True)


# ── 小工具 ────────────────────────────────────────────────


def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    say(f"  下载 {url.rsplit('/', 1)[-1]} …")
    with urllib.request.urlopen(url, timeout=180) as resp, dest.open("wb") as fh:
        shutil.copyfileobj(resp, fh)
    size_mb = dest.stat().st_size / 1024 / 1024
    say(f"    完成（{size_mb:.1f} MB）")
    return dest


def app_dependencies() -> list[str]:
    """从 pyproject 读依赖，避免两处清单不同步。"""
    data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return list(data["project"]["dependencies"])


def write_bat(path: Path, body: str) -> None:
    """写 .bat：**纯 ASCII + CRLF**。

    中文绝不出现在 .bat 里 —— cmd.exe 按当前代码页逐字节读脚本，
    中文 Windows 默认 CP936，UTF-8 的中文一旦被误解码就可能连带把
    语法搞坏（.ps1 上已经踩过一次同样的坑）。中文提示交给 Python 打印。
    """
    text = body.replace("\n", "\r\n")
    bad = [(i, c) for i, c in enumerate(text) if ord(c) < 32 and c not in "\r\n"]
    if bad:
        raise AssertionError(f"{path.name} 里有控制字符（多半是 \t / \f 被当转义了）：{bad[:3]}")
    if "\\\\" in text:
        raise AssertionError(f"{path.name} 里出现双反斜杠（Windows 会折叠，但不该靠它）")
    path.write_bytes(text.encode("ascii"))
    if not text.endswith("\r\n"):
        raise AssertionError("bat 应以换行结尾")


# ── 步骤 ──────────────────────────────────────────────────


def stage_runtime(stage: Path, cache: Path) -> None:
    say("[1/6] 准备 Windows 嵌入式 Python")
    archive = cache / f"python-{PYTHON_VERSION}-embed-amd64.zip"
    if not archive.is_file():
        download(PYTHON_EMBED_URL, archive)

    runtime = stage / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(runtime)

    # 嵌入式发行版用 ._pth 控制 sys.path，而且**存在 ._pth 时 PYTHONPATH 会被忽略**，
    # 所以必须把我们的源码目录直接写进去。
    major_minor = "".join(PYTHON_VERSION.split(".")[:2])  # 3.12.10 → "312"
    pth = runtime / f"python{major_minor}._pth"
    if not pth.is_file():
        found = sorted(runtime.glob("python*._pth"))
        if not found:
            raise FileNotFoundError("嵌入式发行版里没有 ._pth 文件")
        pth = found[0]
    expected = f"python{major_minor}._pth"
    if pth.name != expected:
        raise RuntimeError(f"._pth 名字不对：拿到 {pth.name}，应为 {expected}")
    content = (
        "python312.zip\n"
        ".\n"
        "Lib\\site-packages\n"
        "..\\app\\src\n"
        "import site\n"
    )
    # 双反斜杠是踩过的坑：写 "Lib\\\\site-packages" 会真的往文件里写两条斜杠。
    # Windows 一般会折叠重复分隔符，但不能靠运气。
    if "\\\\" in content:
        raise AssertionError("._pth 里出现了双反斜杠")
    pth.write_text(content, encoding="ascii")
    say(f"  已改写 {pth.name}（加入 app\\src 与 site-packages）")


def stage_dependencies(stage: Path) -> None:
    say("[2/6] 交叉安装 Windows 依赖轮子")
    target = stage / "runtime" / "Lib" / "site-packages"
    target.mkdir(parents=True, exist_ok=True)
    req = stage / "_requirements.txt"
    req.write_text("\n".join(app_dependencies()) + "\n", encoding="utf-8")

    cmd = [
        "uv",
        "pip",
        "install",
        "--target",
        str(target),
        "--python-platform",
        WIN_PLATFORM,
        "--python-version",
        "3.12",
        "--only-binary",
        ":all:",
        "--quiet",
        "-r",
        str(req),
    ]
    say(f"   {' '.join(cmd[:6])} …")
    subprocess.run(cmd, check=True, cwd=PROJECT_ROOT)
    req.unlink()

    leaked = list(target.rglob("*.so")) + list(target.rglob("*.dylib"))
    if leaked:
        raise RuntimeError(f"混进了 macOS 二进制，包不可用：{leaked[:3]}")
    pyd = len(list(target.rglob("*.pyd")))
    say(f"  完成（Windows 扩展 {pyd} 个，无 macOS 残留）")


def stage_fpcalc(stage: Path, cache: Path) -> None:
    say("[3/6] 准备 fpcalc.exe（声学指纹）")
    archive = cache / "chromaprint-fpcalc-1.5.1-windows-x86_64.zip"
    if not archive.is_file():
        download(FPCALC_URL, archive)

    tools = stage / "app" / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        member = next((n for n in zf.namelist() if n.lower().endswith("fpcalc.exe")), None)
        if member is None:
            raise FileNotFoundError("压缩包里没有 fpcalc.exe")
        with zf.open(member) as src, (tools / "fpcalc.exe").open("wb") as dst:
            shutil.copyfileobj(src, dst)
    say(f"  完成（{tools / 'fpcalc.exe'}）")


def stage_app(stage: Path) -> None:
    say("[4/6] 复制程序本体")
    app = stage / "app"
    shutil.copytree(PROJECT_ROOT / "src", app / "src", ignore=shutil.ignore_patterns("__pycache__"))
    assets = PROJECT_ROOT / "assets"
    if assets.is_dir():
        shutil.copytree(assets, app / "assets", ignore=shutil.ignore_patterns("__pycache__"))
    for name in (".env.example", "README.md", "LICENSE"):
        source = PROJECT_ROOT / name
        if source.is_file():
            shutil.copy2(source, app / name)
    # 许可证文本必须随二进制一起给出去（GPL / LGPL 要求）
    lic = PROJECT_ROOT / "licenses"
    if lic.is_dir():
        shutil.copytree(lic, app / "licenses", ignore=shutil.ignore_patterns("__pycache__"))
    say("  完成（app/src + .env.example + LICENSE + licenses）")


def stage_launchers(stage: Path) -> None:
    say("[5/6] 写启动器与说明")
    # ⚠️ .bat 只用相对路径 + 纯 ASCII：
    #   cmd.exe 按当前代码页逐字节读脚本，中文一旦被误解码可能连带把语法搞坏
    #   （.ps1 上踩过一次）。中文提示交给 Python 打印（用 UTF-8 控制台）。
    write_bat(
        stage / "音乐数据管家.bat",
        """@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
if not exist "runtime\\pythonw.exe" (
  echo.
  echo [ERROR] runtime\\pythonw.exe not found.
  echo         Please unzip the WHOLE folder, then run this file again.
  echo.
  pause
  exit /b 1
)
rem pythonw = no console window. Errors are shown by the app itself.
start "" "runtime\\pythonw.exe" -m mds.cli gui
""",
    )
    write_bat(
        stage / "诊断工具.bat",
        """@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
title MusicDataManager - diagnostics
set "OUT=diagnostics.txt"
if not exist "runtime\\python.exe" (
  echo [ERROR] runtime\\python.exe not found. Please unzip the WHOLE folder.
  pause
  exit /b 1
)
> "%OUT%" 2>&1 (
  echo ==== MusicDataManager - diagnostics ====
  echo.
  cmd /c ver
  echo.
  echo ---- files ----
  if exist "runtime\\python.exe" (echo python.exe        OK) else (echo python.exe        MISSING)
  if exist "app\\tools\\fpcalc.exe" (echo fpcalc.exe       OK) else (echo fpcalc.exe       MISSING)
  if exist "app\\.env" (echo .env              OK) else (echo .env              MISSING ^(app never started^))
  echo.
  echo ---- doctor ----
  "runtime\\python.exe" -m mds.cli doctor
  echo.
  echo ---- fpcalc ----
  "app\\tools\\fpcalc.exe" -version
  echo.
  echo ==== end ====
)
echo.
echo Diagnostics written to: %OUT%
echo Please send that file to the developer. It contains NO API keys.
echo.
pause
""",
    )

    (stage / "使用说明.txt").write_text(
        rf"""音乐数据管家 {_version()}（Windows 便携版）
===========================================

免安装：整个文件夹解压出来就能用，不需要装 Python 或任何运行环境。
全程**不会自动修改你的音乐文件** —— 写入必须由你按下按钮，且可一键撤销。


怎么用
------
1. 把整个文件夹解压到一个你记得住的位置（建议路径短一点，例如 D:\mds）。
   注意要**连 runtime 文件夹一起解压**，不要只把里面的文件拖出来。

2. 双击 `音乐数据管家.bat` 打开软件。

3. 第一次先点窗口右上角的「设置」，填三样：

   MusicBrainz 邮箱
     → 填你自己的邮箱即可（不用注册）。
       MusicBrainz 要求请求方带联系方式。

   AcoustID Key
     → 免费。注册：https://acoustid.org/new-application
       注意要拿「应用」key（页面上叫 Application key），不是账号页那把。

   DeepSeek Key
     → 按量计费（约 1 毛钱 / 100 首）。注册：https://platform.deepseek.com
       可以留空 —— 不用 AI 也能完成扫描与匹配。

   填完点「测试连接」，三项都显示 ✅ 就说明填对了；再点「保存」。
   密钥存放在 Windows 凭据管理器里（加密），关掉软件、重启电脑都不用重填。

4. 回到主界面：
   ① 点「选择目录…」选中你的音乐文件夹
   ② 点「开始」—— 读取标签、匹配发行版信息、生成改动建议
   ③ 看结果：可以写入的会列在「可以写入」里，点「全部写入」并确认
      （写入前会自动生成快照，之后可以点「撤销上一批写入」恢复）


常见问题
--------
Q: 双击后没反应？
A: 先双击 `诊断工具.bat`，它会把环境信息写进「诊断信息.txt」。
   如果提示缺少 runtime\python.exe，说明解压不完整。

Q: 会改坏我的文件吗？
A: 不会自动改。写入由你按键触发，且写入前会生成快照、写入后校验完整性，
   随时可以整体撤销。程序也从不重命名、移动或删除你的文件。

Q: 想换台电脑用？
A: 把整个文件夹拷过去即可。分析结果存放在
   %LOCALAPPDATA%\MusicDataManager\ 下，不跟着文件夹走。

Q: 怎么卸载？
A: 删掉整个文件夹。想连数据一起删，再删 %LOCALAPPDATA%\MusicDataManager\。


技术信息
--------
版本         {_version()}
许可证       GPL-2.0-or-later（详见「许可证说明.txt」）
数据位置     %LOCALAPPDATA%\MusicDataManager\（数据库、快照、日志）
包含组件     Python 运行时、PyQt5、MusicBrainz Picard（formats 层）、fpcalc
""",
        encoding="utf-8-sig",
    )

    (stage / "许可证说明.txt").write_text(
        """许可证说明
==========

本程序：GPL-3.0-or-later（全文见 LICENSE）

为什么是 v3：本程序复用 MusicBrainz Picard 的 formats 层（GPL-2.0-or-later），
同时链接 PyQt5（GPL v3）。v2-only 与 v3 不能混用，所以整体按 v3 分发。

本程序与 MusicBrainz / MetaBrainz 基金会 / MusicBrainz Picard 官方以及
Riverbank Computing 均无任何隶属或背书关系。

本包内包含的第三方组件及其许可证：

1. Python 3.12 —— PSF License
   https://www.python.org/

2. MusicBrainz Picard 2.13.3 —— GPL-2.0-or-later
   https://picard.musicbrainz.org/
   本程序复用它的 formats 层做标签读写，因此整体按 GPL 发布。
   对应源码：https://pypi.org/project/picard/2.13.3/

3. PyQt5 —— GPL-3.0（PyQt5 的 GPL 版）；Qt 5.15 运行库 —— LGPL-3.0
   https://riverbankcomputing.com/software/pyqt/
   https://www.qt.io/

4. Chromaprint / fpcalc 1.5.1 —— LGPL-2.1
   https://acoustid.org/chromaprint
   它以独立可执行文件形式随包提供，可按 LGPL 要求替换。

5. discid —— LGPL-3.0-or-later

6. 其余依赖（mutagen / pydantic / httpx / platformdirs / keyring 等）
   均为宽松许可（MIT / BSD / Apache-2.0）；逐项清单见 licenses/README.md。

各许可证全文在 licenses/ 目录中。
""",
        encoding="utf-8-sig",
    )
    say("  完成（启动器 ×2 + 使用说明 + 许可证说明）")


def stage_zip(stage: Path, out_dir: Path) -> Path:
    say("[6/6] 打包 zip")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{PACKAGE_NAME}.zip"
    if target.exists():
        target.unlink()
    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                zf.write(path, Path(PACKAGE_NAME) / path.relative_to(stage))
                count += 1
    say(f"  完成：{target}（{count} 个文件，{target.stat().st_size / 1024 / 1024:.1f} MB）")
    return target


def verify_zip(target: Path) -> None:
    """打包后自检：每次构建都把能查的都查一遍，不让问题流到使用者那边。"""
    say()
    say("自检产物")
    say("-" * 60)
    problems: list[str] = []
    with zipfile.ZipFile(target) as zf:
        names = set(zf.namelist())
        root = PACKAGE_NAME

        def need(rel: str, *, label: str = "") -> None:
            key = f"{root}/{rel}"
            if key not in names:
                problems.append(f"缺少 {rel}{('（' + label + '）') if label else ''}")

        need("runtime/python.exe", label="Windows 运行时就位")
        need("runtime/pythonw.exe", label="无控制台启动")
        need("runtime/python312._pth")
        need("app/src/mds/cli.py", label="程序本体")
        need("app/tools/fpcalc.exe", label="声学指纹工具")
        need("app/assets/icon.ico", label="应用图标")
        need("app/assets/icon.png")
        need("app/.env.example")
        need("app/LICENSE", label="许可证正文")
        need("app/licenses/LGPL-2.1.txt", label="fpcalc 的 LGPL 文本")
        need("app/licenses/PSF-Python-3.12.txt", label="内置 Python 的许可证")
        need("音乐数据管家.bat", label="主启动器")
        need("诊断工具.bat")
        need("使用说明.txt")
        need("许可证说明.txt")

        pth = zf.read(f"{root}/runtime/python312._pth").decode("ascii")
        if "\\\\" in pth:
            problems.append("._pth 里有双反斜杠")
        for entry in ("python312.zip", "Lib\\site-packages", "..\\app\\src", "import site"):
            if entry not in pth:
                problems.append(f"._pth 缺少 {entry!r}")

        # 不许混进 macOS/Linux 二进制
        foreign = [n for n in names if n.endswith((".so", ".dylib"))]
        if foreign:
            problems.append(f"混进非 Windows 二进制：{foreign[:2]}")

        for bat in ("音乐数据管家.bat", "诊断工具.bat"):
            raw = zf.read(f"{root}/{bat}")
            if any(b > 127 for b in raw):
                problems.append(f"{bat} 含非 ASCII 字符（cmd 会误解码）")
            if raw.count(b"\\n") - raw.count(b"\\r\\n") != 0:
                problems.append(f"{bat} 有裸换行（cmd 需要 CRLF）")

        pyd = sum(1 for n in names if n.endswith(".pyd"))
        dll = sum(1 for n in names if n.endswith(".dll"))

    if problems:
        for item in problems:
            say(f"  ❌ {item}")
        raise SystemExit("自检未通过，产物不可交付")
    say(f"  ✅ 关键文件齐全（Windows 扩展 {pyd} 个、DLL {dll} 个）")
    say("  ✅ ._pth / .bat 编码 / 无跨平台二进制残留 全部正确")


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 Windows 试用版绿色包")
    parser.add_argument("--out", default=str(Path.home() / "Downloads"), help="输出目录")
    parser.add_argument("--keep-stage", action="store_true", help="保留中间目录（便于排查）")
    args = parser.parse_args()

    cache = PROJECT_ROOT / ".build-cache"
    stage = PROJECT_ROOT / ".build-stage"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    say("构建「音乐数据管家 · Windows 试用版」")
    say("=" * 60)
    stage_runtime(stage, cache)
    stage_dependencies(stage)
    stage_fpcalc(stage, cache)
    stage_app(stage)
    stage_launchers(stage)
    target = stage_zip(stage, Path(args.out).expanduser())
    verify_zip(target)

    if not args.keep_stage:
        shutil.rmtree(stage)
    say("=" * 60)
    say(f"✅ 构建完成：{target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
