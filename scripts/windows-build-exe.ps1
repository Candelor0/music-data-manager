<#
  音乐数据管家 · Windows 的 exe 构建脚本

  做什么：
    1. 找一个能用的 Python（优先 uv，其次 py 启动器，最后 python）
    2. 建一个干净的构建用虚拟环境，装依赖 + PyInstaller
    3. 用 scripts\mds.spec 打包（无控制台、带图标）
    4. 把 fpcalc.exe、.env.example、README、使用说明、LICENSE、「许可证」目录放到 exe 旁边
    5. 自检：启动一次 exe；再跑一次命令行自检并**检查输出内容**
    6. 打成 zip

  怎么用：
    双击同目录的 `构建exe.bat`（它会带好执行策略参数调用本脚本）。

  说明：
    - **必须在 Windows 上跑**（PyInstaller 不支持交叉编译）
    - 需要联网（装依赖）；约 5～10 分钟
    - 全程只在本机的构建目录里操作，不碰你的音乐文件
    - .ps1 必须保存为 UTF-8 with BOM，否则 Windows PowerShell 5.1
      会把中文读成乱码并报语法错误（这个坑踩过）

  ⚠️ 写这个脚本时踩过的坑（都别再踩）：
    1. **不要用 `$ErrorActionPreference = "Stop"` 配 `2>&1 | Tee-Object`**。
       Windows PowerShell 5.1 会把「原生命令写到 stderr 的正常信息」也当成
       致命错误 —— uv 打印一句 `Using CPython 3.12.14` 就能把脚本掐断。
       所以：错误偏好保持 Continue，每步**显式检查 `$LASTEXITCODE`**。
    2. **`Start-Transcript` 抓不到原生命令的 stderr**，所以日志里会看不到
       报错。原生命令的输出一律先收进变量再写日志（见 `Invoke-Native`）。
    3. **路径可能带空格**（实测 `D:\exe build\`），所以一律 `& $exe @参数数组`，
       不要拼命令串。
    4. **Windows 的 `python.exe` 可能只是微软商店的占位符**（App Execution
       Alias）：`Get-Command python` 找得到，一跑就打开商店、返回非 0。
       所以候选 Python 必须**真的跑一次**再采用（见 `Test-PythonWorks`）。
    5. 无控制台窗口的 exe 里 `sys.stdout` 可能是 None —— 命令行自检的输出
       要靠**重定向到文件**拿（见 `诊断工具.bat` 的写法）。
#>

# 注意：这里刻意**不是** "Stop" —— 见文件头的说明
$ErrorActionPreference = "Continue"

try { chcp 65001 | Out-Null } catch { }
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$ProgressPreference = "SilentlyContinue"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$logFile = Join-Path $root "构建日志.txt"

# 全过程抄进日志（比 `2>&1 | Tee-Object` 稳，不会误判原生命令的 stderr）
try { Start-Transcript -Path $logFile -Force | Out-Null } catch { }

function Say  { param([string]$Text) Write-Host $Text }
function Ok   { param([string]$Text) Write-Host "  [OK]   $Text" -ForegroundColor Green }
function Bad  { param([string]$Text) Write-Host "  [失败] $Text" -ForegroundColor Red }
function Note { param([string]$Text) Write-Host "  [提示] $Text" -ForegroundColor Cyan }
function Step { param([string]$Text) Write-Host ""; Write-Host ("=" * 70); Write-Host $Text; Write-Host ("=" * 70) }

<#
  跑一个外部命令，返回它的退出码，并**把它的输出写进日志**。

  两个细节都是踩出来的：
    · 用 `& $exe @参数` 而不是拼命令串 —— 项目路径可能带空格（实测 `D:\exe build\`）
    · **必须先把输出收进变量再写日志** —— `Start-Transcript` 抓不到原生命令的
      stderr，直接导致上一次构建失败时日志里看不到任何报错（实测踩到）
#>
function Invoke-Native {
    param([string]$Exe, [string[]]$Arguments)
    $lines = & $Exe @Arguments 2>&1
    $code = $LASTEXITCODE
    foreach ($line in $lines) {
        $text = $line.ToString()
        Write-Host $text
        Add-Content -Path $logFile -Value $text -Encoding UTF8
    }
    return $code
}

function Fail {
    param([string]$Text)
    Bad $Text
    Say ""
    # 顺手把环境信息也写进日志 —— 免得来回问一轮
    Add-Content -Path $logFile -Value ("`n---- 失败时的环境信息 ----") -Encoding UTF8
    foreach ($probe in @(@("uv", @("--version")), @("py", @("-0p")), @("python", @("--version")))) {
        $out = try { & $probe[0] @($probe[1]) 2>&1 | Out-String } catch { "（不可用）" }
        Add-Content -Path $logFile -Value ($probe[0] + " -> " + $out.Trim()) -Encoding UTF8
    }
    if (Test-Path $venvPy) {
        $out = (& $venvPy -m pip list 2>&1 | Out-String)
        Add-Content -Path $logFile -Value ("pip list ->`n" + $out) -Encoding UTF8
    }
    Say ("构建日志：" + $logFile)
    Say "把这份日志发给开发即可定位。"
    try { Stop-Transcript | Out-Null } catch { }
    exit 1
}

<#
  这个 Python 能不能真的干活？
  ⚠️ 必须"跑一次"再判断：Windows 上 `python.exe` 可能只是微软商店的占位符，
  `Get-Command` 找得到，一执行就打开商店、返回非 0。只看是否存在会踩坑。
#>
function Test-PythonWorks {
    param([string]$Exe, [string[]]$Prefix = @())
    try {
        $out = (& $Exe @Prefix "-c" "import sys;print('%d.%d'%sys.version_info[:2])" 2>&1 | Out-String).Trim()
    } catch {
        return $false
    }
    return ($LASTEXITCODE -eq 0 -and $out -match "3\.12")
}

$script:version = "0.0.0"
$script:venvPy = ""

Say "音乐数据管家 · exe 构建"
Say ("项目目录：" + $root)
Say ("日志：      " + $logFile)

# ── 版本号（从源码读，单一出处）──────────────────────────
$initPy = Join-Path $root "src\mds\__init__.py"
if (Test-Path $initPy) {
    $m = Select-String -Path $initPy -Pattern '__version__\s*=\s*"([^"]+)"'
    if ($m) { $script:version = $m.Matches[0].Groups[1].Value }
}
Say ("版本：      " + $script:version)

# 路径越长，PyInstaller 里那些深目录越容易撞上 Windows 的 260 字符上限
if ($root.Length -gt 40) {
    Note ("项目目录有 " + $root.Length + " 个字符，偏长。")
    Note "如果第 4 步报「路径太长」之类，把整个文件夹移到更短的位置（例如 D:\mds-build）再跑。"
}
if ($root -match "[^\x00-\x7F]") {
    Note "项目路径里有中文。一般没问题；如果构建报奇怪的错，"
    Note "可以放到纯英文路径（例如 D:\mds-build）再跑一次。"
}

# ── 1. 找 Python ─────────────────────────────────────────
Step "1/6  准备 Python 环境"

$pythonKind = ""
if (Get-Command uv -ErrorAction SilentlyContinue) {
    $pythonKind = "uv"
    Note "用 uv 准备 Python 3.12（它自己会下载，不用你装）"
} elseif ((Get-Command py -ErrorAction SilentlyContinue) -and (Test-PythonWorks "py" @("-3.12"))) {
    $pythonKind = "py"
    Note "用 Windows 的 py 启动器（Python 3.12）"
} elseif ((Get-Command python -ErrorAction SilentlyContinue) -and (Test-PythonWorks "python")) {
    $pythonKind = "python"
    Note "用 PATH 里的 python（Python 3.12）"
} else {
    Bad "这台电脑上找不到可用的 Python 3.12，也没有 uv。"
    Say ""
    Say "请二选一："
    Say "  1) 装 uv（推荐，它能自己下载 Python）："
    Say "     https://docs.astral.sh/uv/getting-started/installation/"
    Say "  2) 或到 https://www.python.org/downloads/ 装 Python 3.12"
    Say "     （安装时记得勾选 Add python.exe to PATH）"
    Say ""
    Say "注意：如果是从微软商店装的「python」占位符，它不能用来构建。"
    Say "装好后重新双击「构建exe.bat」。"
    Fail "找不到可用的 Python 3.12"
}
Ok ("使用方式：" + $pythonKind)

# ── 2. 建构建用虚拟环境、装依赖 ──────────────────────────
Step "2/6  安装依赖（首次要下载几百 MB，请耐心等）"

$venvDir = Join-Path $root ".build-venv"
$script:venvPy = Join-Path $venvDir "Scripts\python.exe"

if (Test-Path $venvDir) {
    Remove-Item -Recurse -Force $venvDir -ErrorAction SilentlyContinue
    if (Test-Path $venvDir) { Fail "旧的 .build-venv 删不掉（可能有程序在占用），请重启电脑后再试" }
}

if ($pythonKind -eq "uv") {
    $code = Invoke-Native "uv" @("venv", $venvDir, "--python", "3.12")
} elseif ($pythonKind -eq "py") {
    $code = Invoke-Native "py" @("-3.12", "-m", "venv", $venvDir)
} else {
    $code = Invoke-Native "python" @("-m", "venv", $venvDir)
}
if ($code -ne 0 -or -not (Test-Path $script:venvPy)) {
    Fail "虚拟环境没建起来（退出码 $code）"
}
Ok "虚拟环境就绪"

# 直接装「本项目」（会自动读 pyproject.toml 里的依赖）+ PyInstaller
if ($pythonKind -eq "uv") {
    $code = Invoke-Native "uv" @("pip", "install", "--python", $script:venvPy, ".", "pyinstaller")
} else {
    Invoke-Native $script:venvPy @("-m", "pip", "install", "--upgrade", "pip") | Out-Null
    $code = Invoke-Native $script:venvPy @("-m", "pip", "install", ".", "pyinstaller")
}
if ($code -ne 0) {
    Fail "依赖安装失败（退出码 $code）"
}
# 依赖装好了，但 exe 还得**导入得动**这些库才算数
$importProbe = (& $script:venvPy "-c" "import PyQt5.QtCore, httpx, mutagen, keyring; print('ok')" 2>&1 | Out-String).Trim()
if ($importProbe -notmatch "ok") {
    Add-Content -Path $logFile -Value ("import 探测失败：" + $importProbe) -Encoding UTF8
    Fail "依赖装上了但导入失败：`n$importProbe"
}
Ok "依赖安装完成（PyQt5 / httpx / mutagen / keyring 都能导入）"

# ── 3. 准备随包资源（fpcalc / 图标）──────────────────────
Step "3/6  准备随包资源"

$toolsDir = Join-Path $root "tools"
$fpcalc = Join-Path $toolsDir "fpcalc.exe"
if (-not (Test-Path $fpcalc)) {
    Note "没有 tools\fpcalc.exe，从 Chromaprint 官方 release 下载"
    New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null
    $zip = Join-Path $env:TEMP "chromaprint-fpcalc.zip"
    $url = "https://github.com/acoustid/chromaprint/releases/download/v1.5.1/chromaprint-fpcalc-1.5.1-windows-x86_64.zip"
    $unpacked = Join-Path $env:TEMP "fpcalc-x"
    try {
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
        if (Test-Path $unpacked) { Remove-Item -Recurse -Force $unpacked }
        Expand-Archive -Path $zip -DestinationPath $unpacked -Force
        $found = Get-ChildItem -Path $unpacked -Filter "fpcalc.exe" -Recurse | Select-Object -First 1
        Copy-Item $found.FullName $fpcalc -Force
    } catch {
        Bad ("fpcalc 下载失败：" + $_)
        Say "请手动下载并放到 tools\fpcalc.exe："
        Say ("  " + $url)
        Fail "缺少 fpcalc.exe"
    }
}
if (-not (Test-Path $fpcalc)) { Fail "缺少 tools\fpcalc.exe" }
Ok "fpcalc.exe 已就绪"

if (-not (Test-Path (Join-Path $root "assets\icon.ico"))) {
    Note "没有图标文件，用脚本现画一个"
    $code = Invoke-Native $script:venvPy @("scripts\make-icon.py")
    if ($code -ne 0) { Fail "图标生成失败（退出码 $code）" }
}
Ok "图标已就绪"

# 打包时 PyInstaller 会写一堆中间文件，放到系统临时目录（路径短，避开 260 字符上限）
$workPath = Join-Path $env:TEMP ("mds-pyi-" + $PID)

# ── 4. 打包 ──────────────────────────────────────────────
Step "4/6  打包（PyInstaller，约 2～5 分钟）"
Note "PyInstaller 的完整输出会写进构建日志.txt"

$distDir = Join-Path $root "dist"
$buildDir = Join-Path $root "build"
if (Test-Path $distDir) { Remove-Item -Recurse -Force $distDir -ErrorAction SilentlyContinue }
if (Test-Path $buildDir) { Remove-Item -Recurse -Force $buildDir -ErrorAction SilentlyContinue }
if (Test-Path $workPath) { Remove-Item -Recurse -Force $workPath -ErrorAction SilentlyContinue }

$code = Invoke-Native $script:venvPy @(
    "-m", "PyInstaller", "scripts\mds.spec",
    "--noconfirm", "--clean",
    "--workpath", $workPath,
    "--distpath", $distDir
)
if (Test-Path $workPath) { Remove-Item -Recurse -Force $workPath -ErrorAction SilentlyContinue }
if ($code -ne 0) { Fail "打包失败（退出码 $code）" }

$exePath = Join-Path $distDir "音乐数据管家\音乐数据管家.exe"
if (-not (Test-Path $exePath)) {
    Fail ("打包命令跑完了，但没找到 exe：" + $exePath)
}
# 顺带确认 schema.sql 进来了 —— 它漏掉的表现是"能打包、一启动就报数据库不可用"
$schema = Join-Path $distDir "音乐数据管家\_internal\mds\storage\schema.sql"
if (-not (Test-Path $schema)) { Fail "包里缺 schema.sql（数据库建表脚本），请把日志发给开发" }
$exeMb = [math]::Round((Get-Item $exePath).Length / 1MB, 1)
Ok ("已生成 exe（" + $exeMb + " MB），schema.sql 已随包")

# ── 5. 组装 + 自检 ───────────────────────────────────────
Step "5/6  组装产物并自检"

$outName = "音乐数据管家-" + $script:version
$outDir = Join-Path $distDir $outName
if (Test-Path $outDir) { Remove-Item -Recurse -Force $outDir }
Move-Item (Join-Path $distDir "音乐数据管家") $outDir

# 这些要放在 exe 旁边 —— 程序打包后把"exe 所在目录"当程序根，
# 于是 tools\fpcalc.exe 与 .env 的查找逻辑一行都不用改
New-Item -ItemType Directory -Force -Path (Join-Path $outDir "tools") | Out-Null
Copy-Item $fpcalc (Join-Path $outDir "tools\fpcalc.exe") -Force
foreach ($name in @(".env.example", "README.md", "LICENSE")) {
    $src = Join-Path $root $name
    if (Test-Path $src) { Copy-Item $src (Join-Path $outDir $name) -Force }
}

# 给用户看的说明（放在 packaging\ 里，与便携版同一份内容）
$usageSrc = Join-Path $root "packaging\使用说明.txt"
if (Test-Path $usageSrc) {
    Copy-Item $usageSrc (Join-Path $outDir "使用说明.txt") -Force
} else {
    Bad "找不到 packaging\使用说明.txt（发布包会缺用户说明）"
}
Ok "fpcalc / 配置模板 / README / LICENSE / 使用说明 已放到 exe 旁边"

# 许可证：GPL 要求把完整许可证文本随二进制一起给出去。
# 第三方组件的全文（GPL-2.0 / LGPL-3.0 / LGPL-2.1 / PSF）放「许可证\」目录。
$licSrc = Join-Path $root "licenses"
if (Test-Path $licSrc) {
    $licDir = Join-Path $outDir "许可证"
    New-Item -ItemType Directory -Force -Path $licDir | Out-Null
    Copy-Item (Join-Path $licSrc "*") $licDir -Recurse -Force
    # 这份 README 讲的是许可证，重命名以免和程序自己的 README.md 混淆
    $licReadme = Join-Path $licDir "README.md"
    if (Test-Path $licReadme) { Move-Item $licReadme (Join-Path $licDir "许可证说明.md") -Force }
    $licCount = (Get-ChildItem $licDir -File).Count
    Ok ("许可证文本已放入「许可证\」（" + $licCount + " 个文件）")
} else {
    Bad "找不到 licenses\ 目录 —— 产物将缺少 GPL 要求的许可证文本"
    Fail "缺少 licenses\（发布前必须补上）"
}

# 诊断工具.bat：让"命令行自检"的结果看得见。
# 无控制台窗口的 exe 里 sys.stdout 可能是 None，所以**必须重定向到文件**再显示。
# ⚠️ 这个 .bat 只用 ASCII：cmd.exe 按当前代码页逐字节读脚本，中文可能连语法一起搞坏。
#    所以它用 `%~dp0*.exe` 通配找主程序，不写死中文文件名。
$batText = @'
@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
title MusicDataManager - diagnostics
set "OUT=%~dp0diagnostics.txt"
set "APP="
for %%f in ("%~dp0*.exe") do if not defined APP set "APP=%%~ff"
if not defined APP (
  echo [ERROR] No .exe found next to this file.
  echo         Please unzip the WHOLE folder, then run again.
  pause
  exit /b 1
)
echo Running self-check, please wait...
> "%OUT%" 2>&1 (
  echo ==== MusicDataManager - diagnostics ====
  echo.
  cmd /c ver
  echo.
  echo ---- app ----
  echo %APP%
  echo.
  echo ---- doctor ----
  "%APP%" doctor
)
type "%OUT%"
echo.
echo ----------------------------------------
echo Saved to: %OUT%
echo Please send this file to the developer.
pause
'@ -replace "(?<!`r)`n", "`r`n"
[System.IO.File]::WriteAllText(
    (Join-Path $outDir "诊断工具.bat"),
    $batText,
    [System.Text.ASCIIEncoding]::new()
)
Ok "诊断工具.bat 已生成"

$targetExe = Join-Path $outDir "音乐数据管家.exe"

# 自检 A：命令行自检能不能跑出**内容**
# ⚠️ 结果写到构建目录 $root，**不能写到 $outDir**：
#    $outDir 整个会被打成 zip 发给用户，写进去就等于把"我这台机器的
#    用户名和路径"一起发布出去（0.5.1 首次构建时真发生过）。
Note "跑一次命令行自检（doctor），确认 exe 真的能工作"
$diagText = (& $targetExe "doctor" 2>&1 | Out-String)
Set-Content -Path (Join-Path $root "自检输出.txt") -Value $diagText -Encoding UTF8
if ($diagText -notmatch "Python|fpcalc") {
    Bad "exe 的命令行自检没有任何输出"
    Fail "exe 跑不起来（请把构建日志.txt 发给开发）"
}
# 断言只用 ASCII 关键词：万一控制台编码不对，中文匹配会假绿/假红。
# `mds.db` 这行只有在数据库真的建起来时才会出现 —— 正好盯住 schema.sql 那个坑。
if ($diagText -notmatch "mds\.db") {
    Bad "命令行自检里没有数据库那一行"
    Fail "exe 跑起来了但数据库不可用（多半是 schema.sql 没随包），请把日志发给开发"
}
Ok "命令行自检输出正常（含数据库）"

# 自检 B：界面能不能起来（进程存活 12 秒）
Note "启动一次界面做自检（会弹出一个窗口，12 秒后自动关掉）"
$proc = Start-Process -FilePath $targetExe -WorkingDirectory $outDir -PassThru
Start-Sleep -Seconds 12
if ($proc.HasExited) {
    Bad ("界面进程提前退出了（退出码 " + $proc.ExitCode + "）")
    Say "可能原因："
    Say "  1) 杀毒软件把 exe 拦了 —— 在杀毒软件的记录里放行后重试"
    Say "  2) 缺少 Windows 运行库（少见）"
    Say "也可以先双击 outputs 里的 诊断工具.bat 看看输出。"
    Fail "自检失败：界面进程提前退出"
}
Ok "界面启动正常"
Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue

# ── 6. 打 zip ────────────────────────────────────────────
Step "6/6  打包 zip"

# 兜底：万一用户自己先双击过「诊断工具.bat」，产物目录里会有他自己的 diagnostics.txt，
# 里面是他机器的用户名与路径 —— 不该随包发出去。
$strayDiag = Join-Path $outDir "diagnostics.txt"
if (Test-Path $strayDiag) { Remove-Item -Force $strayDiag }

$zipPath = Join-Path $root ($outName + ".zip")
if (Test-Path $zipPath) { Remove-Item -Force $zipPath }

# 优先用系统自带的 tar.exe（bsdtar）：它比 Compress-Archive 更能扛长路径。
# PyInstaller 的产物目录很深，Compress-Archive 在长路径上会直接报错。
$tar = Join-Path $env:SystemRoot "System32\tar.exe"
$zipped = $false
if (Test-Path $tar) {
    $code = Invoke-Native $tar @("-a", "-c", "-f", $zipPath, "-C", $distDir, $outName)
    $zipped = ($code -eq 0 -and (Test-Path $zipPath))
}
if (-not $zipped) {
    Note "tar 打包没成功，改用 Compress-Archive"
    try {
        Compress-Archive -Path $outDir -DestinationPath $zipPath -CompressionLevel Optimal -ErrorAction Stop
        $zipped = Test-Path $zipPath
    } catch {
        Bad ("打 zip 失败：" + $_)
    }
}
if (-not $zipped) { Fail "打 zip 失败（产物目录本身是好的，可直接用：" + $outDir + "）" }

# 确认 zip 里真的装满了（而不是只写进去几个文件）
try {
    Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction SilentlyContinue
    $archive = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
    $entryCount = $archive.Entries.Count
    $archive.Dispose()
} catch {
    $entryCount = -1
}
if ($entryCount -eq 0) { Fail "zip 是空的" }
$zipMb = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
Ok ("已生成：" + $zipPath + "（" + $zipMb + " MB" + $(if ($entryCount -gt 0) { "，" + $entryCount + " 个文件" } else { "" }) + "）")

try { Stop-Transcript | Out-Null } catch { }

Say ""
Say "════════════════════════════════════════════════════════════"
Say "  构建成功"
Say "════════════════════════════════════════════════════════════"
Say ("  产物：        " + $zipPath)
Say ("  也可以直接用：" + $outDir)
Say  "  双击其中的「音乐数据管家.exe」即可（无控制台窗口、带图标）"
Say  "  命令行自检的结果已写到：" + (Join-Path $root "自检输出.txt") + "（不随包分发）"
Say  "  许可证文本随包提供：LICENSE 与「许可证\」目录"
Say ""
Say ("  构建日志：" + $logFile)
Say ""
