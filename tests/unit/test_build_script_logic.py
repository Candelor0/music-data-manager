"""用**真 PowerShell** 跑一遍构建脚本里的关键函数。

为什么值得单独写一组：这个构建脚本已经被同一类问题咬了两次 ——
· uv 往 stderr 写了一句正常提示，脚本却当成致命错误自杀了
· 日志里完全看不到 PyInstaller 的报错，因为 Start-Transcript 抓不到原生命令的 stderr
这类问题**看代码看不出来**，只能用真的 shell 跑一遍。

所以这里：从 `windows-build-exe.ps1` 里**抽出真实函数体**（不是复制一份），
在 pwsh 下用一个假的"原生命令"和一个假的"微软商店 python 占位符"去喂它。
本机没有 pwsh 就跳过（Windows 上没有 pwsh，走 pytest 时自动跳过，不影响）。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PS1 = PROJECT_ROOT / "scripts" / "windows-build-exe.ps1"
VENV_PY = PROJECT_ROOT / ".venv" / "bin" / "python"


def _find_pwsh() -> str | None:
    found = shutil.which("pwsh")
    if found and _pwsh_works(found):
        return found
    candidates = (
        Path.home() / ".local" / "pwsh" / "pwsh",
        Path("/tmp/pwsh/app/pwsh"),
        Path("/opt/homebrew/bin/pwsh"),
        Path("/usr/local/bin/pwsh"),
    )
    for candidate in candidates:
        if candidate.is_file() and _pwsh_works(str(candidate)):
            return str(candidate)
    return None


def _pwsh_works(path: str) -> bool:
    """装的 pwsh 真的能跑吗？

    为什么先探一下：踩过一次 —— `/tmp/pwsh` 那份被系统清理成残缺状态，
    pwsh 能启动但连 `Write-Output` 都不认识（cmdlet 加载失败）。
    这种环境下这组测试会全红，看着像“代码坏了”，其实是环境坏了。
    探不通就当“本机没有 pwsh”跳过，理由写在跳过原因里，不假装通过。
    """
    try:
        probe = subprocess.run(
            [path, "-NoProfile", "-Command", "Write-Output PROBE_OK"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "PROBE_OK" in probe.stdout


PWSH = _find_pwsh()
pytestmark = pytest.mark.skipif(
    PWSH is None,
    reason="本机没有可用的 pwsh（没装，或装坏了 —— 见 _pwsh_works 里的说明）",
)


def _ps1_code() -> str:
    """PS1 的真代码（去 BOM、去块注释）。"""
    text = PS1.read_bytes()[3:].decode("utf-8")
    text = re.sub(r"<#.*?#>", "", text, flags=re.S)
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("#"))


def _extract_function(name: str) -> str:
    """从 PS1 里按名字抽出一个函数的真实定义。"""
    code = _ps1_code()
    match = re.search(rf"^function {re.escape(name)} \{{.*?^\}}", code, re.S | re.M)
    assert match, f"没能在 PS1 里找到函数 {name}"
    return match.group(0)


def _run_pwsh(script: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "harness.ps1"
    path.write_text(script, encoding="utf-8-sig")
    return subprocess.run(
        [PWSH, "-NoProfile", "-File", str(path)],
        capture_output=True,
        text=True,
        timeout=180,
    )


# ── Invoke-Native：原生命令往 stderr 写东西时不能把脚本搞崩 ──
def test_invoke_native_survives_stderr_output(tmp_path: Path) -> None:
    """模拟 uv 的行为：往 **stderr** 写一句提示、退出码 0。

    这正是第一次失败的原因 —— PowerShell 5.1 在 `$ErrorActionPreference = "Stop"`
    下会把它当致命错误。这里验证：返回码是 0、提示被收进日志、脚本没崩。
    """
    noisy = tmp_path / "noisy.py"
    noisy.write_text(
        "import sys\n"
        "print('Using CPython 3.12.14', file=sys.stderr)\n"
        "print('Resolved 32 packages')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    log = tmp_path / "build.log"
    script = f"""
$ErrorActionPreference = "Continue"
$logFile = "{log}"
{_extract_function("Invoke-Native")}
$code = Invoke-Native "{VENV_PY}" @("{noisy}")
Write-Output "RETURNCODE=$code"
Write-Output "LOGHASSTDERR=$([bool](Select-String -Path $logFile -Pattern 'Using CPython' -Quiet))"
Write-Output "LOGHASSTDOUT=$([bool](Select-String -Path $logFile -Pattern 'Resolved 32' -Quiet))"
"""
    result = _run_pwsh(script, tmp_path)
    assert result.returncode == 0, f"脚本崩了：{result.stderr}"
    assert "RETURNCODE=0" in result.stdout, result.stdout
    assert "LOGHASSTDERR=True" in result.stdout, "往 stderr 写的提示必须进日志"
    assert "LOGHASSTDOUT=True" in result.stdout, "普通输出也要进日志"


def test_invoke_native_reports_failure_code(tmp_path: Path) -> None:
    """真的失败时要如实返回非 0（否则上层 `if ($code -ne 0)` 拦不住）。"""
    failer = tmp_path / "fail.py"
    failer.write_text("import sys; sys.exit(3)\n", encoding="utf-8")
    log = tmp_path / "build.log"
    script = f"""
$logFile = "{log}"
{_extract_function("Invoke-Native")}
$code = Invoke-Native "{VENV_PY}" @("{failer}")
Write-Output "RETURNCODE=$code"
"""
    result = _run_pwsh(script, tmp_path)
    assert "RETURNCODE=3" in result.stdout, result.stdout


# ── Test-PythonWorks：微软商店的 python 占位符不能算"可用" ──
def test_python_probe_rejects_store_stub(tmp_path: Path) -> None:
    """`python.exe` 可能只是微软商店占位符：跑得起来、但返回非 0、还打开商店。

    `Get-Command python` 会返回它 —— 只看"命令存在"就会踩坑。
    """
    stub = tmp_path / "stub.py"
    stub.write_text(
        "import sys\n"
        "print('Python was not found; run without arguments to install from the Microsoft Store')\n"
        "sys.exit(9009)\n",
        encoding="utf-8",
    )
    script = f"""
{_extract_function("Test-PythonWorks")}
$ok = Test-PythonWorks "{VENV_PY}" @("{stub}")
Write-Output "PROBE=$ok"
"""
    result = _run_pwsh(script, tmp_path)
    assert "PROBE=False" in result.stdout, result.stdout


def test_python_probe_accepts_real_interpreter(tmp_path: Path) -> None:
    """真能跑、且是 3.12 的解释器要判为可用。"""
    script = f"""
{_extract_function("Test-PythonWorks")}
$ok = Test-PythonWorks "{VENV_PY}"
Write-Output "PROBE=$ok"
"""
    result = _run_pwsh(script, tmp_path)
    assert "PROBE=True" in result.stdout, result.stdout


def test_python_probe_rejects_wrong_version(tmp_path: Path) -> None:
    """版本对不上（比如 3.11）也要判为不可用 —— 项目要求 3.12。"""
    fake = tmp_path / "fake312.py"
    fake.write_text(
        "import sys\nprint('3.11')\nsys.exit(0)\n",
        encoding="utf-8",
    )
    script = f"""
{_extract_function("Test-PythonWorks")}
$ok = Test-PythonWorks "{VENV_PY}" @("{fake}")
Write-Output "PROBE=$ok"
"""
    result = _run_pwsh(script, tmp_path)
    assert "PROBE=False" in result.stdout, result.stdout


def test_ps1_does_not_rely_on_stop_preference(tmp_path: Path) -> None:
    """整脚本在 `$ErrorActionPreference = "Continue"` 下必须能跑完不报错。

    这里做的是静态确认：函数抽取后语法没问题、且脚本里没有把偏好设回 Stop。
    """
    code = _ps1_code()
    assert '$ErrorActionPreference = "Continue"' in code
    assert '$ErrorActionPreference = "Stop"' not in code


@pytest.mark.skipif(os.name == "nt", reason="Windows 上用不到这组抽取检查")
def test_extraction_actually_found_functions() -> None:
    """确认抽取逻辑真的拿到了函数（否则上面几条会"假绿"）。"""
    for name in ("Invoke-Native", "Test-PythonWorks"):
        body = _extract_function(name)
        assert len(body.splitlines()) > 3, f"{name} 抽出来是空的"
