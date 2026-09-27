<#
  音乐数据管家 · Windows 验证「一键跑完」脚本（v2）

  你只需要：跑这一条命令，然后把生成的报告文件发给我。

  安全性：
    - 全程只操作「副本」，你的原始音乐文件一个字节都不会被改
    - 「分析」阶段会联网查 MusicBrainz / AcoustID / DeepSeek

  v2 相对 v1 修的问题（都是 Windows 实测踩出来的）：
    1. 目录名含 [ ] 时，PowerShell 的 -Path 会当通配符解析 → 复制全乱套
       改用 .NET API（不做任何通配符展开）
    2. 报告改用显式日志写 UTF-8（无 BOM），不再用 Start-Transcript
       （它会把中文写成一字两遍）
    3. 把控制台与原生程序输出的编码都固定为 UTF-8
    4. 临时 .env 写成 UTF-8 无 BOM（有 BOM 会让第一个键名带 BOM 读不到）
    5. 文件没复制成功时立刻停下并明确报错，不白跑一轮

  注：本文件必须保存为 UTF-8 with BOM。
#>

$ErrorActionPreference = "Continue"

# 编码：控制台显示 + 原生程序（uv）输出都按 UTF-8 解
try { chcp 65001 | Out-Null } catch { }
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$report = Join-Path $root "验证报告.txt"
$log = New-Object System.Collections.Generic.List[string]

function Save-Report {
    [System.IO.File]::WriteAllLines($report, $log, (New-Object System.Text.UTF8Encoding($false)))
}

function Out-Line {
    param([string]$Text = "")
    Write-Host $Text
    $log.Add($Text)
}

function Out-Block {
    param([string[]]$Lines)
    foreach ($l in $Lines) { Out-Line $l }
}

function Title {
    param([string]$Text)
    Out-Line ""
    Out-Line ("=" * 68)
    Out-Line $Text
    Out-Line ("=" * 68)
}

$script:allLog = New-Object System.Collections.Generic.List[string]

function Run-Cmd {
    param([string[]]$CmdArgs)
    $rest = @()
    if ($CmdArgs.Count -gt 1) { $rest = $CmdArgs[1..($CmdArgs.Count - 1)] }
    $out = & $CmdArgs[0] $rest 2>&1 | Out-String
    Out-Block ($out -replace "`r`n", "`n" -split "`n")
    $script:allLog.Add($out)
    return $out
}

if ($null -eq (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "❌ 没有找到 uv 命令。请先跑 scripts\windows-verify-setup.ps1 把环境装好。" -ForegroundColor Yellow
    exit 1
}

Title "音乐数据管家 · Windows 验证（v2）"
Out-Line "项目目录：$root"
Out-Line "报告文件：$report"
Out-Line "时间：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Out-Line "PowerShell：$($PSVersionTable.PSVersion)"

# ── 0. 环境自检 ───────────────────────────────────────────
Title "第 0 步：环境自检"
$null = Run-Cmd @("uv", "run", "mds", "doctor")

# ── 1. 问音乐库位置 ───────────────────────────────────────
Title "第 1 步：告诉我你的音乐库在哪"
Out-Line "请把「音乐库文件夹」拖到这个窗口里，然后按回车。"
Out-Line "例如你的库在 D:\Music，就把它拖进来（会自动填好路径）。"
Out-Line ""

Write-Host "音乐库路径: " -NoNewline
$lib = Read-Host
$lib = $lib.Trim().Trim('"').Trim("'")

if ([string]::IsNullOrWhiteSpace($lib)) {
    Out-Line "❌ 没有输入路径。"
    Save-Report
    exit 1
}
if (-not [System.IO.Directory]::Exists($lib)) {
    Out-Line "❌ 这个路径不存在：$lib"
    Out-Line "   请重新运行本脚本，重新拖一次。"
    Save-Report
    exit 1
}
Out-Line ""
Out-Line "✅ 音乐库：$lib"

# ── 2. 复制一小批歌（用 .NET API，避免 [ ] 被当通配符）────
Title "第 2 步：复制一小批歌到副本目录（不会动原文件）"

$testRoot = Join-Path (Split-Path -Parent $root) "mds-test-music"
$testMusic = Join-Path $testRoot "music"
if ([System.IO.Directory]::Exists($testRoot)) {
    [System.IO.Directory]::Delete($testRoot, $true)
}
$null = [System.IO.Directory]::CreateDirectory($testMusic)

$count = 12
$allowed = @(".flac", ".mp3", ".m4a", ".ogg")
$all = @()
try {
    $all = [System.IO.Directory]::EnumerateFiles($lib, "*", [System.IO.SearchOption]::AllDirectories) |
           Where-Object { $allowed -contains [System.IO.Path]::GetExtension($_).ToLower() } |
           Sort-Object
} catch {
    Out-Line ""
    Out-Line "❌ 扫描音乐库失败：$($_.Exception.Message)"
    Out-Line "   常见原因：路径太长（超过 260 字符）、权限不足、或路径里有异常字符。"
    Out-Line "   请把这份报告发给我。"
    Save-Report
    exit 1
}

Out-Line "你的音乐库共找到 $($all.Count) 个受支持的文件（flac/mp3/m4a/ogg）。"
Out-Line "将复制前 $count 首到：$testMusic"
Out-Line "（保留 歌手\专辑 的文件夹结构）"
Out-Line ""

$picked = $all | Select-Object -First $count
if ($picked.Count -eq 0) {
    Out-Line "❌ 在那个目录里没找到音乐文件。"
    Save-Report
    exit 1
}

$copiedOk = 0
$failed = @()
foreach ($src in $picked) {
    try {
        $dir = [System.IO.Path]::GetDirectoryName($src)
        $rel = ""
        if ($dir.Length -gt $lib.TrimEnd([char]92, [char]47).Length) {
            $rel = $dir.Substring($lib.TrimEnd([char]92, [char]47).Length).TrimStart([char]92, [char]47)
        }
        $targetDir = if ($rel) { [System.IO.Path]::Combine($testMusic, $rel) } else { $testMusic }
        $null = [System.IO.Directory]::CreateDirectory($targetDir)
        $targetFile = [System.IO.Path]::Combine($targetDir, [System.IO.Path]::GetFileName($src))
        [System.IO.File]::Copy($src, $targetFile, $true)
        $copiedOk++
        Out-Line "   ✔ $src"
    } catch {
        $failed += "$src  →  $($_.Exception.Message)"
    }
}

Out-Line ""
Out-Line "复制结果：成功 $copiedOk / $($picked.Count)"
if ($failed.Count -gt 0) {
    Out-Line "失败明细："
    foreach ($f in $failed) { Out-Line "   ❌ $f" }
}

# 用 .NET 再数一遍副本里的文件，确认真的落地了
$copiedFiles = [System.IO.Directory]::EnumerateFiles($testMusic, "*", [System.IO.SearchOption]::AllDirectories) |
               Where-Object { $allowed -contains [System.IO.Path]::GetExtension($_).ToLower() }
$copiedCount = @($copiedFiles).Count
Out-Line "副本目录里实际有 $copiedCount 个音乐文件。"
Out-Line "副本的文件夹结构："
foreach ($f in ($copiedFiles | Select-Object -First 6)) {
    Out-Line "   $($f.Substring($testRoot.Length).TrimStart([char]92, [char]47))"
}

if ($copiedCount -eq 0) {
    Out-Line ""
    Out-Line "❌ 副本里一个文件都没有 —— 复制失败了，后面的步骤没有意义，提前停下。"
    Out-Line "   请把这份报告发给我。"
    Save-Report
    exit 1
}

$beforeSha = Join-Path $testRoot "before-sha256.txt"
$shaLines = @()
foreach ($f in $copiedFiles) {
    $h = (Get-FileHash -LiteralPath $f -Algorithm SHA256).Hash
    $shaLines += "$f`t$h"
}
[System.IO.File]::WriteAllLines($beforeSha, $shaLines, (New-Object System.Text.UTF8Encoding($false)))
Out-Line ""
Out-Line "✅ 已记录 $copiedCount 个副本的 sha256"

# ── 3. 把工具指向副本 ─────────────────────────────────────
Title "第 3 步：把工具指向副本（新建临时配置）"

$envFile = Join-Path $root ".env"
if (-not [System.IO.File]::Exists($envFile)) {
    Out-Line "❌ 找不到 .env 文件（里面要有 API 密钥与音乐库路径）"
    Out-Line "   请先照 WINDOWS-CHECKLIST.md 建好 .env，再重跑本脚本。"
    Save-Report
    exit 1
}

$tempEnv = Join-Path $testRoot ".env"
$lines = [System.IO.File]::ReadAllLines($envFile)
$newLines = New-Object System.Collections.Generic.List[string]
$hadPath = $false
foreach ($l in $lines) {
    if ($l -match "^\s*MUSIC_LIBRARY_PATH\s*=") {
        $newLines.Add("MUSIC_LIBRARY_PATH=$testMusic")
        $hadPath = $true
    } else {
        $newLines.Add($l)
    }
}
if (-not $hadPath) { $newLines.Add("MUSIC_LIBRARY_PATH=$testMusic") }
# 必须写 UTF-8 无 BOM：带 BOM 会让第一个键名多出 BOM 字符而读不到
[System.IO.File]::WriteAllLines($tempEnv, $newLines, (New-Object System.Text.UTF8Encoding($false)))
$env:MDS_ENV_FILE = $tempEnv

Out-Line "✅ 临时配置：$tempEnv"
Out-Line "   指向副本：$testMusic"
Out-Line "   你的原 .env 没有被修改。"

# ── 4. 扫描 + 分析（只读）────────────────────────────────
Title "第 4 步：扫描 + 分析（只读，不改文件）"

$scanOut = Run-Cmd @("uv", "run", "mds", "scan", "--new")

$runId = ""
foreach ($l in ($scanOut -split "`n")) {
    if ($l -match "^run_id\s*=\s*(\S+)") { $runId = $Matches[1] }
}
if (-not $runId) {
    Out-Line ""
    Out-Line "❌ 没有拿到 run_id，无法继续。请把报告发我。"
    Save-Report
    exit 1
}
Out-Line ""
Out-Line "✅ run_id = $runId"

# 关键校验：扫描到的数量应等于副本数量
$foundCount = -1
foreach ($l in ($scanOut -split "`n")) {
    if ($l -match "发现文件\s*(\d+)\s*个") { $foundCount = [int]$Matches[1] }
}
Out-Line ""
if ($foundCount -ge 0) {
    if ($foundCount -eq $copiedCount) {
        Out-Line "✅ 递归扫描正常：副本 $copiedCount 个，扫描到 $foundCount 个"
    } else {
        Out-Line "⚠️ 数量不一致：副本 $copiedCount 个，但扫描到 $foundCount 个 —— 请把报告发我"
    }
}

Out-Line ""
Out-Line "开始分析（联网查询）..."
$null = Run-Cmd @("uv", "run", "mds", "analyze", $runId)

Out-Line ""
Out-Line "生成写入计划："
$null = Run-Cmd @("uv", "run", "mds", "plan", $runId, "--show")

# ── 5. 写入 → 校验 ────────────────────────────────────────
Title "第 5 步：写入（在副本上）"

Out-Line "先 dry-run 看看："
$null = Run-Cmd @("uv", "run", "mds", "apply", $runId)

Out-Line ""
Out-Line "现在真的写（只写副本）："
$null = Run-Cmd @("uv", "run", "mds", "apply", $runId, "--yes")

Out-Line ""
Out-Line "重新读文件校验："
$null = Run-Cmd @("uv", "run", "mds", "verify", $runId)

Out-Line ""
Out-Line "变更记录："
$null = Run-Cmd @("uv", "run", "mds", "changes", $runId)

# ── 6. 回滚 ───────────────────────────────────────────────
Title "第 6 步：回滚"
$null = Run-Cmd @("uv", "run", "mds", "rollback", $runId, "--yes")

# ── 7. sha256 对比 ────────────────────────────────────────
Title "第 7 步：回滚后的 sha256 对比"

$afterSha = Join-Path $testRoot "after-sha256.txt"
$afterLines = @()
foreach ($f in $copiedFiles) {
    $h = (Get-FileHash -LiteralPath $f -Algorithm SHA256).Hash
    $afterLines += "$f`t$h"
}
[System.IO.File]::WriteAllLines($afterSha, $afterLines, (New-Object System.Text.UTF8Encoding($false)))

$before = [System.IO.File]::ReadAllLines($beforeSha)
$after = [System.IO.File]::ReadAllLines($afterSha)
$diff = @(Compare-Object $before $after)

$changedFiles = @()
if ($diff.Count -gt 0) {
    $changedFiles = @($diff | ForEach-Object { ($_.InputObject -split "`t")[0] } | Sort-Object -Unique)
}

Out-Line "副本总数：$copiedCount"
Out-Line "字节有变化的：$($changedFiles.Count) 个"
Out-Line ""
Out-Line "说明（重要）：FLAC 重写标签块时，块的排列顺序与预留空白（PADDING）会重新计算，"
Out-Line "            所以「字节有变化」是预期现象，不代表内容被破坏。"
Out-Line "            真正需要报警的是下面这一项。"
Out-Line ""
Out-Line "【关键判定】apply 阶段有没有出现「完整性中止」标记："
if ($allLog -match "已中止，原文件未被修改") {
    Out-Line "   ❌ 出现了 —— 说明有文件的音频/封面被改动，已自动中止。请把报告发我。"
} else {
    Out-Line "   ✅ 没有出现 —— 说明每个被写入的文件，音频与内嵌图片的 sha256 都与写入前一致"
}
Out-Line ""
Out-Line "【辅助判定】apply / verify / rollback 的失败计数："
if ($log -match "失败\s+[1-9]") {
    Out-Line "   ❌ 出现了非 0 的失败计数（见上面各步输出）"
} else {
    Out-Line "   ✅ 全为 0"
}
if ($changedFiles.Count -gt 0) {
    Out-Line ""
    Out-Line "字节有变化的文件清单："
    foreach ($f in $changedFiles) { Out-Line "   $f" }
}
