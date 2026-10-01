# 开发指南

[返回 README](../README.md)

本文汇总源码结构、进阶命令、开发约束与打包流程。安装依赖、配置服务和启动界面见 [README：从源码运行](../README.md#从源码运行)。

## 工程结构

| 位置 | 职责 |
| --- | --- |
| `src/mds/core/` | 纯函数：归一化、清洗、差异、候选评分、共识和写入决策 |
| `src/mds/adapters/` | 外部适配：指纹、网络服务、限流、标签读写、完整性校验、快照和凭据库 |
| `src/mds/storage/` | SQLite：运行、条目、分组、变更审计与缓存 |
| `src/mds/pipeline/` | 扫描、分组、分析、计划、写入、校验、回滚的编排 |
| `src/mds/ui/` | PyQt5 界面、视图模型与后台任务 |
| `src/mds/cli.py` | 命令行参数解析与调用 |
| `tests/unit/` | 使用 mock 的自动化测试 |
| `scripts/` | 构建、发布清理、图标生成与界面预览工具 |
| `packaging/` | 随发布包提供的最终用户说明 |
| `licenses/` | 第三方组件清单与许可证文本 |
| `assets/` | 应用图标，由 `scripts/make-icon.py` 生成 |
| `tools/` | 本机准备的 `fpcalc`，第三方二进制不提交到仓库 |
| `poc/` | 早期可行性验证脚本 |

运行数据位于用户数据目录，不写入工程目录：

- Windows：`%LOCALAPPDATA%\MusicDataManager\音乐数据管家\`
- macOS：`~/Library/Application Support/音乐数据管家/`
- 数据库：`mds.db`；快照：`snapshots/`；日志：`logs/mds.log`（滚动 2 MB × 3）。

## 命令行进阶

以下示例使用 PowerShell。将 `$runId` 替换为扫描输出的运行 ID；编号 `118`、`120`、`130` 仅作示例，请使用自己报告中的条目编号。

### 分步执行与本地分组

```powershell
uv run mds doctor
uv run mds scan "D:\Music" --limit 20
$runId = "替换为输出的 run_id"

# 同目录分组与共识补空：本地完成，不联网、不写音乐文件
uv run mds group $runId

# 不使用模型，但仍会查询 AcoustID / MusicBrainz
uv run mds analyze $runId --no-llm
uv run mds plan $runId
uv run mds todo $runId --limit 5

# 启用已配置的模型，可继续处理先前跳过的条目
uv run mds analyze $runId
```

`analyze` 支持 `Ctrl-C` 中断后续跑。缓存命中会复用已有结果；`--retry-errors` 会重试失败条目，可能产生新的服务请求与费用。

### 专辑候选与人工裁决

```powershell
# 先查看需要确认的专辑，再为整组选择候选
uv run mds choose $runId
uv run mds choose $runId --folder "D:\Music\某专辑目录" --candidate 0

# 按条目裁决
uv run mds plan $runId --decisions
uv run mds plan $runId --adopt-ai 118 --refresh
uv run mds plan $runId --keep-existing 118
uv run mds plan $runId --skip 120 --refresh
uv run mds plan $runId --pick 130 2 --refresh
```

候选序号从 **0** 开始：`--candidate 0` 是第一个，`--pick 130 2` 为条目 130 选择第三个候选。采纳 AI 的专辑判断需显式操作。

### 写入、快照与回滚

```powershell
# 默认预览，确认后才写入；整文件快照需预留相应磁盘空间
uv run mds apply $runId
uv run mds apply $runId --snapshot-mode full --yes
uv run mds verify $runId
uv run mds changes $runId

# 先预览，再执行最近一批的撤销
uv run mds rollback $runId --batch latest
uv run mds rollback $runId --batch latest --yes

# 导出报告与查看运行历史
uv run mds report $runId --md "建议报告.md" --csv "建议报告.csv"
uv run mds runs
```

不指定 `--batch` 时，回滚针对该运行的已写入记录。`--force` 可绕过外部改动检查，会覆盖文件写入后的修改，使用前应先检查差异。

### 常用参数及适用命令

| 命令 | 参数 | 用途 |
| --- | --- | --- |
| `scan` / `run` | `--limit N` | 限制扫描文件数；`todo` / `plan --show` 的同名参数限制展示数量 |
| `scan` | `--new` | 强制新建运行，而非复用未完成的运行 |
| `analyze` / `run` | `--no-llm` | 跳过模型消歧，仍进行指纹服务与元数据查询 |
| `analyze` | `--retry-errors` | 重试之前失败的条目 |
| `analyze` | `--refresh-decisions` | 使用已有结果重算已完成条目的决策 |
| `analyze` | `--mode full\|no_album\|none` | 调整给模型的标签证据级别 |
| `analyze` | `--budget 0.5` | 每 100 首的模型预算上限，单位为元 |
| `analyze` / `run` / `plan` | `--assume-no-album-tag` | 验证用：模拟缺少专辑标签的音乐库 |
| `plan` | `--show --limit N` | 展示写入计划 |
| `plan` | `--refresh` | 重建已有计划 |
| `apply` | `--snapshot-mode tags\|full` | 标签快照或整文件快照 |
| `apply` / `rollback` | `--only ID ...` | 仅处理指定条目 |
| `apply` | `--limit N` | 限制本次处理条目数 |
| `apply` / `rollback` | `--yes` | 执行实际写入或回滚 |
| `rollback` | `--batch latest` | 仅撤销最近一批 |
| `rollback` | `--force` | 绕过外部修改保护 |

具体参数以 `uv run mds <命令> --help` 为准。当前 CLI **没有** `apply --skip-integrity` 选项；内部 `ApplyOptions.skip_integrity` 不应当作用户可用的命令行参数。

`analyze --refresh-decisions` 若遇到尚未完成或先前跳过的分析阶段，仍可能补发服务请求。仅需在本地重建已有结果的写入计划时，使用 `plan --refresh`。

`run --yes` 仅跳过已启用的费用预估确认，**不会写入音乐标签**。实际写入通过 `apply --yes` 执行。

## 设计与架构约束

修改行为前，请同时检查相应测试。以下约束用于保持已有的数据保护与决策边界。

| 约束 | 原因或实现位置 |
| --- | --- |
| `core/` 不导入 Qt、Picard 或网络库 | 保持规则为可独立测试的纯函数 |
| 功能层不反向依赖界面 | CLI 与 GUI 共用同一内核 |
| 界面写入与撤销必须通过 `pipeline.apply` / `pipeline.rollback` | 集中快照、校验与审计流程 |
| 界面颜色、字号与间距来自 `ui/theme.py` | 保持主题一致，避免分散硬编码 |
| 新增网络调用须遵守 `adapters/ratelimit.py` 的限流 | AcoustID 默认 2 次/秒，MusicBrainz 默认 1 次/秒 |
| MusicBrainz 发行版查询带 `inc=media` | 获取用于候选评分的曲目与时长证据 |
| 模型默认 `deepseek-flash`，关闭思考模式 | 与当前配置和输出解析流程一致 |
| 提示词不预设“原始发行版更正确” | 再版、合辑等发行版均可能是用户持有的版本 |
| 不依赖模型自报置信度决定写入 | 使用可核对的证据与人工裁决 |
| 已有专辑名默认保留 | AI 建议须经用户显式采纳 |
| 证据不足时保留候选 | 同一录音可对应多个发行版 |
| 写入限于 `core/writeplan.py` 的白名单字段 | 约束实际修改范围，不将非空值清空 |
| 同目录共识只补空、不覆盖已有值 | 分歧作为提示保留 |
| 共识至少需要两首一致的证据 | 孤证不视为共识；曲名与音轨号不参与目录互证 |
| 目录名用于候选评分，不直接作为写入值 | 文件夹名不一定比文件内标签准确 |
| CLI 写入与回滚默认预览 | 显式 `--yes` 才执行 |
| 默认在副本上写入、校验后原子替换 | 失败时尽量保持原文件完整 |
| FLAC 替换前比对音频与内嵌图片 SHA-256 | 当前分区完整性校验仅实现 FLAC，见 `adapters/integrity.py` |
| 回滚前检查写入后的文件哈希 | 避免覆盖外部修改 |
| 发布包清理本机信息并补齐许可证材料 | 由发布清理脚本与相关测试检查 |

界面术语集中在 `src/mds/ui/glossary.py`。快照语义、支持格式与密钥存储说明见 [README](../README.md)。

## 开发检查

在完成 [源码安装](../README.md#从源码运行) 后执行：

```powershell
uv run pytest
uv run ruff check src tests
uv run ruff format --check src tests
```

需要格式化代码时使用 `uv run ruff format src tests`。测试使用 mock，避免真实服务调用；`tests/conftest.py` 会将 Qt 切到 `offscreen`，以便在无显示环境运行界面测试。

关键测试入口：

| 测试 | 检查内容 |
| --- | --- |
| `tests/unit/test_boundaries.py` | 规则层依赖边界 |
| `tests/unit/test_ui_boundaries.py` | 界面与文件写入边界 |
| `tests/unit/test_theme.py` | 主题来源 |
| `tests/unit/test_licensing.py` | 许可证与打包材料 |
| `tests/unit/test_release_zip.py` | 发布包结构、本机信息与密钥排除 |

## 构建与发布

```powershell
# 生成图标
uv run python scripts/make-icon.py

# 准备 Windows exe 构建包
uv run python scripts/build-exe-kit.py
```

将生成的构建包放到 **Windows** 机器解压，双击 `构建exe.bat`。该脚本调用 `scripts/windows-build-exe.ps1`，安装构建依赖并用 PyInstaller 生成 exe。

**PyInstaller 的 Windows exe 需在 Windows 上构建。** `scripts/build-windows-portable.py` 是另一条使用 Windows 嵌入式 Python 的便携目录构建流程，可在 macOS 上准备 Windows 便携包；它不等同于跨平台编译 exe。

发布前，对构建产物执行清理：

```powershell
uv run python scripts/make-release-zip.py "dist\音乐数据管家-0.5.1"
```

将示例中的版本替换为实际构建版本，路径应指向包含 exe 的产物目录。脚本也接受已有 ZIP 的路径。它会检查包结构、清理构建者信息、补齐许可证材料，并检查中文文件名编码。未经发布清理的构建产物不应直接上传 Release。

- Release 附件使用 ASCII 文件名，例如 `mds-<版本>-windows-portable.zip`；包内目录可保留中文。
- 分发二进制时提供完整对应源码与许可证材料，详见 [许可证说明](../licenses/README.md)。
- 界面预览：`uv run python scripts/ui-preview.py <输出目录>`，需要可用的本机分析数据。
- 截图度量：`uv run python scripts/ui-snapshot-metrics.py <图片路径>`。

构建流程与发布清理实现以 `scripts/` 中对应脚本为准。
