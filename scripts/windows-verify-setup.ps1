<#
  音乐数据管家 · Windows 验证环境一键准备脚本

  做什么：
    1. 检查 Git / uv 是否就绪（Python 交给 uv 自己装，避免误用 Anaconda）
    2. 用 uv 装一个干净的 CPython 3.12
    3. 下载 Windows 版 fpcalc.exe（声学指纹工具）放进 tools\
    4. 安装 Python 依赖（uv sync）
    5. 跑环境自检（mds doctor）

  怎么用（在 PowerShell 里，切到项目文件夹后执行）：
    powershell -ExecutionPolicy Bypass -File scripts\windows-verify-setup.ps1

  本脚本只在本机操作，不修改任何音乐文件。

  注：本文件必须保存为 UTF-8 with BOM，否则 Windows PowerShell 5.1
      会把中文读成乱码并报语法错误。
#>

$ErrorActionPreference = "Stop"

# 让控制台用 UTF-8，否则中文输出会显示成乱码（数据没错，只是显示）
try { chcp 65001 | Out-Null } catch { }
$ProgressPreference = "SilentlyContinue"

function Write-Say { param([string]$Text) Write-Host $Text }
function Write-Ok { param([string]$Text) Write-Host "  [OK]   $Text" -ForegroundColor Green }
function Write-Bad { param([string]$Text) Write-Host "  [缺失] $Text" -ForegroundColor Yellow }
function Write-Note { param([string]$Text) Write-Host "  [提示] $Text" -ForegroundColor Cyan }
function Write-Step {
    param([string]$Text)
    Write-Host ""
    Write-Host ("=" * 70)
    Write-Host $Text
    Write-Host ("=" * 70)
}

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
Write-Say "项目目录：$root"

if ($root -match "[^\x00-\x7F]") {
    Write-Note "项目路径里有中文或特殊字符。建议放到纯英文路径（例如 D:\mds-verify），"
    Write-Note "避免 Python 虚拟环境在个别工具链上出问题。"
}

# ─────────────────────────────────────────────────────────
Write-Step "第 1 步 / 共 5 步：检查 Git 与 uv"

$uvOk = $false
$uvCmd = Get-Command uv -ErrorAction SilentlyContinue
if ($null -eq $uvCmd) {
    Write-Bad "没有找到 uv 命令"
} else {
    $uvv = & uv --version 2>&1 | Out-String
    Write-Ok ($uvv.Trim())
    $uvOk = $true
}

$gitOk = $false
$gitCmd = Get-Command git -ErrorAction SilentlyContinue
if ($null -eq $gitCmd) {
    Write-Bad "没有找到 git 命令"
} else {
    $gv = & git --version 2>&1 | Out-String
    Write-Ok ($gv.Trim())
    $gitOk = $true
}

# Python 只是参考信息：第 2 步会用 uv 自己装一个干净的 3.12
$pyCmd = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $pyCmd) {
    Write-Note "系统里没有 python 命令 —— 不影响，第 2 步会由 uv 装好 3.12"
} else {
    $pv = & python --version 2>&1 | Out-String
    $pv = $pv.Trim()
    $pyPath = $pyCmd.Source
    Write-Note "系统 python：$pv   （$pyPath）"
    if ($pyPath -match "conda|anaconda|miniconda") {
        Write-Note "看起来是 Anaconda/Miniconda 的 Python。本项目不使用它，"
        Write-Note "第 2 步会用 uv 装一个独立的干净 3.12。"
    }
}

if ((-not $uvOk) -or (-not $gitOk)) {
    Write-Say ""
    Write-Say "请先补齐上面标了 [缺失] 的东西，然后重新运行本脚本。"
    Write-Say ""
    Write-Say "  Git : https://git-scm.com/download/win"
    Write-Say "  uv  : 见 https://docs.astral.sh/uv/getting-started/installation/"
    Write-Say ""
    Write-Say "  或者用 winget 一条命令装齐（开始菜单搜 PowerShell，右键以管理员身份运行）："
    Write-Say "      winget install --id Git.Git -e"
    Write-Say "      winget install --id astral-sh.uv -e"
    Write-Say ""
    Write-Say "  装完请关掉 PowerShell 重新开一个，再跑本脚本。"
    exit 1
}

# ─────────────────────────────────────────────────────────
Write-Step "第 2 步 / 共 5 步：准备干净的 CPython 3.12（交给 uv 管理）"

Write-Say "  这样可以避开 Anaconda 等环境带来的版本与库冲突。"
& uv python install 3.12
if ($LASTEXITCODE -ne 0) {
    Write-Bad "uv 安装 Python 3.12 失败"
    exit 1
}
$pyList = & uv python list --only-installed 2>&1 | Out-String
Write-Ok "uv 管理的 Python 已就绪"
Write-Say ($pyList.Trim())

# ─────────────────────────────────────────────────────────
Write-Step "第 3 步 / 共 5 步：准备 fpcalc（声学指纹工具）"

$toolsDir = Join-Path $root "tools"
if (-not (Test-Path $toolsDir)) {
    New-Item -ItemType Directory -Path $toolsDir | Out-Null
}
$fpcalc = Join-Path $toolsDir "fpcalc.exe"

if (Test-Path $fpcalc) {
    Write-Ok "fpcalc.exe 已存在，跳过下载"
} else {
    $url = "https://github.com/acoustid/chromaprint/releases/download/v1.6.1/chromaprint-fpcalc-1.6.1-windows-x86_64.zip"
    $zip = Join-Path $env:TEMP "chromaprint-fpcalc.zip"
    $extract = Join-Path $env:TEMP "chromaprint-fpcalc"
    Write-Say "  正在下载（约 5 MB）..."
    Write-Say "  $url"
    $downloadOk = $true
    try {
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    } catch {
        $downloadOk = $false
        Write-Bad "自动下载失败：$($_.Exception.Message)"
    }
    if ($downloadOk) {
        $extractOk = $true
        try {
            if (Test-Path $extract) { Remove-Item -Recurse -Force $extract }
            Expand-Archive -Path $zip -DestinationPath $extract -Force
        } catch {
            $extractOk = $false
            Write-Bad "解压失败：$($_.Exception.Message)"
        }
        if ($extractOk) {
            $found = Get-ChildItem -Path $extract -Recurse -Filter "fpcalc.exe" | Select-Object -First 1
            if ($null -eq $found) {
                Write-Bad "压缩包里没有找到 fpcalc.exe"
                exit 1
            }
            Copy-Item $found.FullName $fpcalc -Force
            Write-Ok "已放入：$fpcalc"
        } else {
            exit 1
        }
    } else {
        Write-Say ""
        Write-Say "  请手动下载后把 fpcalc.exe 放到 tools\ 目录："
        Write-Say "  $url"
        exit 1
    }
}

# ─────────────────────────────────────────────────────────
Write-Step "第 4 步 / 共 5 步：安装 Python 依赖（首次约 2 分钟）"

& uv sync
if ($LASTEXITCODE -ne 0) {
    Write-Bad "uv sync 失败，请把上面的报错截图发给 AI"
    exit 1
}
Write-Ok "依赖安装完成"

$check = & uv run python -c "import importlib.util as u, picard, PyQt5.QtCore as qc; print('picard', picard.__version__); print('PyQt5', qc.QT_VERSION_STR); print('PyQt6_present', bool(u.find_spec('PyQt6')))" 2>&1 | Out-String
Write-Say ($check.Trim())
if ($check -match "PyQt6_present True") {
    Write-Bad "环境里同时存在 PyQt6，会和 PyQt5 冲突。请执行： uv pip uninstall PyQt6"
} else {
    Write-Ok "环境干净（只有 PyQt5，没有 PyQt6）"
}

# ─────────────────────────────────────────────────────────
Write-Step "第 5 步 / 共 5 步：环境自检"

$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) {
    Write-Note "还没有 .env 文件。"
    Write-Say  "         它需要包含：ACOUSTID_API_KEY / DEEPSEEK_API_KEY /"
    Write-Say  "                      MUSICBRAINZ_USER_AGENT / MUSIC_LIBRARY_PATH"
    Write-Say  "         参考同目录的 .env.example。没有 .env 也能跑 doctor，只是密钥会显示缺失。"
    Write-Say ""
}

& uv run mds doctor

Write-Say ""
Write-Say ("=" * 70)
Write-Say "准备完成。下一步请照项目根目录的 WINDOWS-CHECKLIST.md 继续。"
Write-Say ("=" * 70)
