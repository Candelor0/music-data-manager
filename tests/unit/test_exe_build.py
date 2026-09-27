"""exe 打包相关测试。

打包本身（PyInstaller）**只能在 Windows 上跑**，所以这里测的是"能在任何机器上验的部分"：
入口脚本的参数默认值、spec 文件的关键设置、以及构建脚本的存在与编码。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PROJECT_ROOT / "scripts"
ENTRY = SCRIPTS / "exe_entry.py"
SPEC = SCRIPTS / "mds.spec"
PS1 = SCRIPTS / "windows-build-exe.ps1"
KIT_BAT = PROJECT_ROOT / "构建exe.bat"


# ── 入口脚本：双击 exe（无参数）要开界面 ──────────────────
def test_entry_defaults_to_gui(monkeypatch) -> None:
    """双击 exe 时没有任何命令行参数 —— 必须默认开界面，而不是报"用法错误"。"""
    import sys

    from mds import cli

    captured: list[list[str]] = []
    monkeypatch.setattr(cli, "main", lambda argv: captured.append(list(argv)) or 0)
    monkeypatch.setattr(sys, "argv", ["音乐数据管家.exe"])

    import importlib.util

    spec = importlib.util.spec_from_file_location("exe_entry", ENTRY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    assert module.main() == 0
    assert captured == [["gui"]]


def test_entry_passes_through_arguments(monkeypatch) -> None:
    """带参数时原样传给 CLI（`音乐数据管家.exe doctor` 要能用）。"""
    import sys

    from mds import cli

    captured: list[list[str]] = []
    monkeypatch.setattr(cli, "main", lambda argv: captured.append(list(argv)) or 0)
    monkeypatch.setattr(sys, "argv", ["音乐数据管家.exe", "doctor"])

    import importlib.util

    spec = importlib.util.spec_from_file_location("exe_entry", ENTRY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    module.main()
    assert captured == [["doctor"]]


def test_entry_parses_as_python() -> None:
    ast.parse(ENTRY.read_text(encoding="utf-8"))


# ── spec：动态导入必须显式声明 ───────────────────────────
@pytest.mark.parametrize(
    "package",
    ["mds", "picard", "mutagen", "keyring.backends"],
)
def test_spec_collects_dynamic_imports(package: str) -> None:
    """这几个包的子模块是**运行时动态导入**的，不显式收集就会缺功能。

    （Picard 的 formats 注册表、mutagen 的各格式、keyring 的平台后端都是这种。）
    """
    text = SPEC.read_text(encoding="utf-8")
    assert f'collect_submodules("{package}")' in text, f"spec 里应当收集 {package} 的子模块"


def test_spec_is_windowed_and_has_icon() -> None:
    text = SPEC.read_text(encoding="utf-8")
    assert "console=False" in text, "必须是窗口程序（无控制台）"
    assert "icon=ICON" in text, "要带上图标"
    assert (PROJECT_ROOT / "assets" / "icon.ico").is_file(), "图标文件必须存在"
    assert '"assets" / "icon.ico"' in text, "图标要随包带上"


def test_spec_paths_are_absolute_not_relative() -> None:
    """spec 里**不许用相对路径**。

    PyInstaller 解析 spec 里的路径时参照的是 **spec 文件所在目录**，
    不是当前工作目录 —— 写成 `scripts/exe_entry.py` 会被拼成
    `scripts/scripts/exe_entry.py` 而报 not found（实测踩到，构建到第 4 步退出码 1）。
    """
    text = SPEC.read_text(encoding="utf-8")
    assert "SPECPATH" in text, "应当基于 SPECPATH 算绝对路径"
    assert "[ENTRY]" in text, "入口脚本要用绝对路径变量"
    assert 'pathex=["src"]' not in text, "pathex 不能是相对路径"
    assert 'icon="assets' not in text, "图标不能是相对路径"
    assert '("assets/icon' not in text, "图标数据不能是相对路径"


def test_spec_output_name_is_product_name() -> None:
    text = SPEC.read_text(encoding="utf-8")
    assert 'name="音乐数据管家"' in text, "产物名应当是产品名（用户看到的就是它）"


# ── Windows 构建脚本 ─────────────────────────────────────
def test_build_scripts_exist() -> None:
    assert PS1.is_file(), "缺少 scripts/windows-build-exe.ps1"
    assert KIT_BAT.is_file(), "缺少双击入口「构建exe.bat」"


def test_ps1_has_utf8_bom() -> None:
    """.ps1 必须带 UTF-8 BOM：Windows PowerShell 5.1 无 BOM 会把中文读成乱码，
    进而报出莫名其妙的"语法错误"（这个坑踩过两次）。"""
    raw = PS1.read_bytes()
    assert raw[:3] == b"\xef\xbb\xbf", "windows-build-exe.ps1 必须以 UTF-8 BOM 开头"
    assert "音乐数据管家" in raw[3:].decode("utf-8"), "中文应能正常解码"


def test_bat_is_ascii_crlf() -> None:
    """双击入口的 .bat：纯 ASCII + CRLF（cmd 按代码页逐字节读脚本）。"""
    raw = KIT_BAT.read_bytes()
    assert all(b < 128 for b in raw), "构建exe.bat 必须是纯 ASCII"
    assert raw.count(b"\n") == raw.count(b"\r\n"), "构建exe.bat 必须全部是 CRLF"
    assert b"windows-build-exe.ps1" in raw, "它应当去调用构建脚本"
    assert b"-ExecutionPolicy Bypass" in raw, "要带执行策略参数，否则双击会被拦下"


def test_ps1_mentions_it_must_run_on_windows() -> None:
    text = PS1.read_bytes()[3:].decode("utf-8")
    assert "必须在 Windows 上跑" in text, "脚本里要写清这一点（免得在别的系统上试）"


def _ps1_code() -> str:
    """取出 .ps1 里的**真代码**（去掉 BOM、块注释 `<# #>`、行注释）。

    为什么要剥注释：文件头正好在用反例讲解那两个坑
    （`$ErrorActionPreference = "Stop"` 与 `2>&1 | Tee-Object`），
    不剥掉的话断言会把自己的说明文档当成违规。
    """
    import re

    text = PS1.read_bytes()[3:].decode("utf-8")
    text = re.sub(r"<#.*?#>", "", text, flags=re.S)          # 块注释
    return "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("#")
    )


# ── 防复发：那个把脚本掐断的 PowerShell 坑 ────────────────
def test_ps1_does_not_use_tee_with_native_commands() -> None:
    """**不许再出现 `2>&1 | Tee-Object`**。

    Windows PowerShell 5.1 在 `$ErrorActionPreference = "Stop"` 下，会把
    「原生命令写到 stderr 的正常信息」也当成致命错误 —— uv 打印一句
    `Using CPython 3.12.14` 就把构建脚本掐断了（实测踩到）。

    改用 Start-Transcript 记日志 + 显式检查 $LASTEXITCODE。
    """
    code = _ps1_code()
    offenders = [line.strip() for line in code.splitlines() if "Tee-Object" in line]
    assert not offenders, "不许用 Tee-Object 接原生命令的输出：\n" + "\n".join(offenders)
    assert "Start-Transcript" in code, "日志应当用 Start-Transcript"
    assert "$LASTEXITCODE" in code, "每步要对原生命令显式检查退出码"


def test_ps1_does_not_set_erroractionpreference_stop() -> None:
    """错误偏好不能是 Stop（否则原生命令的正常 stderr 会终止脚本）。"""
    for line in _ps1_code().splitlines():
        assert '$ErrorActionPreference = "Stop"' not in line, "不要在脚本里设 Stop：" + line.strip()


def test_ps1_invokes_native_commands_as_argument_arrays() -> None:
    """外部命令用 `& $exe @参数数组` 调用 —— 项目路径可能带空格（实测遇到过 `D:\\exe build\\`）。"""
    code = _ps1_code()
    assert "function Invoke-Native" in code
    assert "& $Exe @Arguments" in code


def test_every_package_data_file_is_bundled() -> None:
    """`src/mds` 下的**每一个非 Python 文件**都必须在 spec 的 datas 里。

    PyInstaller 只自动收 `.py`，其它文件要显式列出。漏一个的后果很隐蔽：
    打包能成功、一运行才报错（`schema.sql` 漏掉时表现为「数据库不可用」，
    在 macOS 上冻一次就抓到了）。
    """
    spec_text = SPEC.read_text(encoding="utf-8")
    data_files = [
        path
        for path in (PROJECT_ROOT / "src" / "mds").rglob("*")
        if path.is_file() and path.suffix != ".py" and "__pycache__" not in path.parts
    ]
    assert data_files, "src/mds 下应当至少有一个数据文件（schema.sql）"
    missing = [str(p.relative_to(PROJECT_ROOT)) for p in data_files if p.name not in spec_text]
    assert not missing, "这些包内数据文件没写进 spec 的 datas：\n" + "\n".join(missing)


# ── 构建脚本：这一轮为「提高成功率」补的检查 ────────────────
def _bat_text() -> str:
    """把 PS1 里的 `诊断工具.bat` 内容抽出来（测真实文本，不是副本）。"""
    import re

    text = PS1.read_bytes()[3:].decode("utf-8")
    match = re.search(r"\$batText = @'\r?\n(.*?)\r?\n'@", text, re.S)
    assert match, "PS1 里应当有一段 $batText here-string"
    # 复现脚本里的 CRLF 转换
    return re.sub(r"(?<!\r)\n", "\r\n", match.group(1))


def test_ps1_generates_ascii_crlf_diagnostic_bat() -> None:
    """「诊断工具.bat」必须是纯 ASCII + CRLF。

    两个原因，都是踩出来的：
    · cmd.exe 按当前代码页**逐字节**读脚本，中文一旦被误解码可能连带把语法搞坏
      → 所以它用 `%~dp0*.exe` 通配找主程序，不写死中文文件名
    · 无控制台窗口的 exe 里 `sys.stdout` 可能是 None
      → 所以必须把输出**重定向到文件**再 `type` 出来，否则自检结果看不见
    """
    bat = _bat_text()
    assert all(ord(c) < 128 for c in bat), "bat 里不许出现非 ASCII 字符"
    assert bat.count("\n") == bat.count("\r\n"), "bat 行尾必须全是 CRLF"
    assert "*.exe" in bat, "要用通配找主程序（避开中文文件名）"
    assert '> "%OUT%" 2>&1' in bat, "输出要重定向到文件"
    assert "doctor" in bat, "要跑命令行自检"
    assert 'type "%OUT%"' in bat, "要把自检结果显示出来"
    assert "pause" in bat, "要 pause，否则窗口一闪就没"
    assert "chcp 65001" in bat, "要开 UTF-8 代码页，中文才不乱"


def test_ps1_probes_python_actually_runs() -> None:
    """候选 Python 必须**真的跑一次**才采用。

    Windows 的 `python.exe` 可能只是微软商店的占位符（App Execution Alias）：
    `Get-Command python` 找得到，一执行就打开商店、返回非 0。
    只看"命令存在"会踩这个坑。
    """
    code = _ps1_code()
    assert "function Test-PythonWorks" in code, "要有真实探测函数"
    assert 'Test-PythonWorks "py"' in code and 'Test-PythonWorks "python"' in code, (
        "两个候选都要探测"
    )


def test_ps1_zips_with_tar_and_falls_back() -> None:
    """优先用系统自带 tar.exe 打 zip，失败再退回 Compress-Archive。

    PyInstaller 的产物目录很深，Compress-Archive 在长路径上会直接报错。
    """
    code = _ps1_code()
    assert "tar.exe" in code, "优先用 tar.exe（更能扛长路径）"
    assert "Compress-Archive" in code, "要有 Compress-Archive 兜底"
    assert "ZipFile]::OpenRead" in code, "打完要确认 zip 不是空的"


def test_ps1_verifies_what_it_built() -> None:
    """构建脚本要自己验产物，而不是"命令返回 0 就算成功"。"""
    code = _ps1_code()
    assert "schema.sql" in code, "要确认 schema.sql 随包（漏了会一启动就报数据库不可用）"
    assert "数据库不可用" in code, "要检查 doctor 输出里有没有数据库报错"
    assert '"doctor"' in code, "要跑一次命令行自检并检查输出内容"
    assert "WorkingDirectory" in code, "启动自检时要指定工作目录"


def test_ps1_uses_short_workpath() -> None:
    """PyInstaller 的中间文件放系统临时目录（路径短，避开 260 字符上限）。"""
    code = _ps1_code()
    assert "--workpath" in code and "$env:TEMP" in code
    assert "路径太长" in code or "偏长" in code, "路径过长时应当提醒"


def test_ps1_probes_imports_after_install() -> None:
    """依赖装完要真的 import 一次 —— 装上了不代表导入得动。"""
    code = _ps1_code()
    assert "import PyQt5.QtCore" in code
    assert "都能导入" in code


def test_spec_collects_win32ctypes() -> None:
    """Windows 凭据库后端依赖 win32ctypes，必须显式收集。

    漏掉不会报错，只会**悄悄降级**成明文文件存密钥 —— 最难发现的那种问题。
    """
    assert "win32ctypes" in SPEC.read_text(encoding="utf-8")


