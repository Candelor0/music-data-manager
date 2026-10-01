<div align="center">
  <img src="assets/icon.png" width="88" alt="音乐数据管家图标">
  <h1>音乐数据管家</h1>
  <p><strong>让本地音乐标签更完整，让每一次修改都由你决定。</strong></p>
  <p>声学指纹识别 · 专辑候选匹配 · AI 辅助消歧 · 人工确认 · 批次撤销</p>
  <p>
    <a href="https://github.com/Candelor0/music-data-manager/releases/latest"><img src="https://img.shields.io/github/v/release/Candelor0/music-data-manager?label=release" alt="最新版本"></a>
    <img src="https://img.shields.io/badge/platform-Windows-0078D4" alt="Windows 优先">
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0--or--later-blue" alt="GPL-3.0-or-later"></a>
  </p>
  <p>
    <a href="https://github.com/Candelor0/music-data-manager/releases/latest">下载 Windows 免安装版</a>
    · <a href="#快速上手">快速上手</a>
    · <a href="#从源码运行">从源码运行</a>
    · <a href="docs/development.md">开发指南</a>
  </p>
</div>

---

音乐数据管家是一款管理**本地音乐文件元数据**的桌面工具。它扫描音乐库，结合声学指纹、AcoustID、MusicBrainz 和可选的 DeepSeek 模型，整理出缺失标签、专辑候选及建议修改。

**扫描和分析只生成建议；你确认写入后，程序才修改音乐标签。** 写入前建立快照，写入后保留变更记录，支持撤销上一批。

> **直接使用：** 下载 [Windows 免安装版](https://github.com/Candelor0/music-data-manager/releases/latest)，无需安装 Python。当前发布版本为 **v0.5.1**。
> **运行源码或参与开发：** 查看[源码安装](#从源码运行)和[开发指南](docs/development.md)。

## 导航

[主要功能](#主要功能) · [快速上手](#快速上手) · [联网与费用](#联网与费用) · [写入与撤销](#写入与撤销) · [命令行](#命令行) · [常见问题](#常见问题) · [许可证](#许可证)

## 主要功能

| 功能 | 对你的音乐库有什么帮助 |
| --- | --- |
| 声学指纹识别 | 从音频指纹寻找录音与发行版候选，辅助补全缺失标签 |
| AI 辅助消歧 | 在多个候选间给出建议与理由；证据不足时交给你选择 |
| 同目录互证 | 利用同一目录中多首歌曲的一致信息补全空字段，已有值有分歧时提示冲突 |
| 按专辑裁决 | 同一专辑的候选可以整组确认，减少逐首操作 |
| 差异预览 | 写入前检查原值和建议值；默认保留与匹配结果不一致的已有专辑名 |
| 快照与撤销 | 保留修改记录，支持按运行或按批次回滚 |
| 续跑与缓存 | 保存分析进度，复用指纹、候选和模型结果，减少重复请求 |
| 图形界面与 CLI | 日常使用桌面界面，批量处理与排查可用命令行 |

- **扫描格式：** MP3、FLAC、M4A、OGG。
- **可写字段：** 曲名、艺术家、专辑、年份、流派、碟号；具体改动受规则与人工选择限制。
- **平台：** Windows 优先，桌面界面基于 PyQt5；macOS 可从源码运行。

## 快速上手

### 1. 下载并启动

到 [Releases](https://github.com/Candelor0/music-data-manager/releases/latest) 下载 Windows 便携包（v0.5.1 附件名为 `mds-0.5.1-windows-portable.zip`）。

**解压整个文件夹**，再双击 `音乐数据管家.exe`。请保留随包提供的 `_internal/`、`tools/` 和其他文件。

### 2. 选择音乐库并配置服务

在主界面选择音乐文件夹，在**设置**页填写联系邮箱和需要的 API Key，保存后可用**测试连接**检查服务。

| 配置 | 是否需要 | 说明 |
| --- | --- | --- |
| 音乐库文件夹 | 必填 | 要扫描的本地目录，支持子目录 |
| MusicBrainz 联系邮箱 | 联网查询时需要 | 用于生成服务要求的 User-Agent，无需注册 MusicBrainz 账号 |
| AcoustID 应用 Key | 声学指纹匹配时需要 | 从 [My applications](https://acoustid.org/my-applications) 获取应用 Key |
| DeepSeek API Key | 可选 | 从 [DeepSeek 平台](https://platform.deepseek.com) 获取，启用模型消歧后按服务用量计费 |

不配置 Key 也可扫描和分组；缺少 AcoustID Key 会跳过依赖它的指纹查询，缺少 DeepSeek Key 则不进行模型消歧。

> AcoustID 的**应用 Key**与账号页用于提交指纹的 Key 用途不同。查歌请使用应用 Key，详见[密钥与配置](#密钥与配置)。

### 3. 分析、检查建议并写入

点击**开始**，依次完成扫描、分组、分析和计划生成。界面会显示费用预估；模型调用受预算限制。

在待办中检查以下四组结果：

| 分组 | 建议操作 |
| --- | --- |
| 🟢 可以写入 | 检查空字段补全与规则清洗的差异，再确认批量写入 |
| 🟡 需要确认专辑 | 比较发行版候选，按歌曲或专辑选择 |
| 🟠 标签不一致 | 默认保留已有专辑名；需要更换时显式采纳建议 |
| ⚪ 无需处理 | 查看未命中、无需改动或被安全规则拦下的条目 |

写入后可以使用**撤销上一批**。更完整的操作说明见 [`packaging/使用说明.txt`](packaging/使用说明.txt)。

## 联网与费用

| 阶段 | 联网情况 | 费用 |
| --- | --- | --- |
| 扫描、目录分组、同目录互证 | 在本地完成 | 不产生云端费用 |
| AcoustID / MusicBrainz 查询 | 发送指纹或查询所需的元数据 | 不产生模型费用 |
| DeepSeek 消歧 | 发送候选与标签证据供模型判断 | 按 DeepSeek 服务用量计费 |
| 标签写入、校验、撤销 | 在本地完成 | 不产生云端费用 |

程序**不上传音频文件本体**。联网分析会发送匹配所需的指纹、标签或候选信息。

模型预算默认是**每 100 首 ¥0.50**，可通过 `.env` 的 `CLOUD_BUDGET_PER_100_TRACKS` 调整。达到预算限制后停止后续模型调用；界面费用为预估值，实际费用以服务商账单为准。

命令行可用 `--no-llm` 跳过 DeepSeek。**这个选项仍会进行 AcoustID / MusicBrainz 查询，并不等于完全离线。** 只需本地处理时，使用 `scan` 与 `group`。

## 写入与撤销

扫描、分析和计划生成都不修改音乐文件。确认写入后，程序按以下流程处理：

```text
建立写入前快照 → 在同目录副本上写标签 → 读回校验
→ 检查完整性 → 原子替换原文件 → 记录变更与批次
```

- 保留音乐文件的名称和位置，不执行重命名、移动或删除操作。
- 默认补空与规则清洗；匹配结果与已有专辑名冲突时，默认保留已有值。
- CLI 的 `apply` 和 `rollback` 默认只预览，需显式加 `--yes` 才执行。
- 回滚前检查文件是否在写入后又被外部修改；发现变化时默认跳过。

**完整性校验的范围：** 当前仅 **FLAC** 实现音频数据与内嵌图片的分区 SHA-256 校验，不一致时中止替换。MP3、M4A、OGG 仍采用副本写入、标签读回校验和原子替换，但尚未实现同等的音频/图片分区校验。

| 快照模式 | 恢复范围 | 空间占用 |
| --- | --- | --- |
| `tags`（默认） | 写回快照中记录的原标签；容器字节、填充块或块顺序可能变化 | 通常远小于原音乐文件 |
| `full` | 从整文件备份逐字节恢复 | 约等于本批次原文件大小 |

CLI 可用 `apply --snapshot-mode full` 选择整文件快照。**撤销依赖本机数据库和快照；清空分析数据会删除这些记录，旧批次将无法再撤销。**

## 从源码运行

建议使用 **Python 3.12** 与 [uv](https://docs.astral.sh/uv/)。项目声明支持 Python `>=3.12,<3.14`，当前环境自检按 3.12 检查。

```powershell
git clone https://github.com/Candelor0/music-data-manager.git
cd music-data-manager
uv sync --python 3.12
uv run mds gui
```

需要声学指纹分析时，还要准备 **Chromaprint 的 `fpcalc`**：

1. 从 [Chromaprint 官方 Releases](https://github.com/acoustid/chromaprint/releases) 下载适合本机平台的 `fpcalc`。
2. Windows 将解压出的 `fpcalc.exe` 放到项目根目录的 `tools/` 下；macOS 使用 `tools/fpcalc` 并确保可执行。也可将 `fpcalc` 加入 `PATH`。
3. 在设置页配置服务，或复制 `.env.example` 为 `.env`，填写自己的配置。

Windows 发布包已经包含 `fpcalc.exe`，无需另行下载。macOS 也可双击仓库根目录的 `打开界面.command` 启动。

> Picard 与界面共用 **PyQt5**。请勿在同一个环境中混装 PyQt6。

## 密钥与配置

设置页保存的 API Key 优先使用**系统凭据库**（Windows 凭据管理器 / macOS 钥匙串）。凭据库不可用时会退回本地文件，界面会提示明文存储。

源码与 CLI 用户可复制模板：

```powershell
Copy-Item .env.example .env
```

| `.env` 变量 | 用途 |
| --- | --- |
| `MUSIC_LIBRARY_PATH` | CLI 扫描的默认音乐库目录 |
| `MUSICBRAINZ_USER_AGENT` | 例如 `MusicDataManager/0.5.1 ( contact: you@example.com )`，请替换成自己的邮箱 |
| `ACOUSTID_API_KEY` | [AcoustID 应用 Key](https://acoustid.org/my-applications)，用于查歌 |
| `DEEPSEEK_API_KEY` | DeepSeek 模型消歧 |
| `CLOUD_BUDGET_PER_100_TRACKS` | 每 100 首的模型预算上限，单位为元 |

设置页保存的 Key 优先于 `.env`；邮箱、音乐库等偏好由 GUI 加载。CLI 建议显式传入目录并配置 `.env`，不要假定所有界面偏好都会被命令行读取。

`.env` 已被 `.gitignore` 排除。不要把真实 Key 放进提交、Issue、截图或日志附件。

## 命令行

以下是 Windows PowerShell 示例。把路径替换成自己的音乐库；运行完成后，将输出的 `run_id` 填入 `$runId`。

```powershell
# 扫描 → 分组 → 分析 → 生成计划，尚不写入音乐文件
uv run mds run "D:\Music" --limit 20
$runId = "替换为输出的 run_id"

# 查看待办与差异
uv run mds todo $runId --limit 5
uv run mds plan $runId --show --limit 10
uv run mds apply $runId

# 确认差异后执行写入，再读回校验
uv run mds apply $runId --yes
uv run mds verify $runId

# 查看变更；预览并撤销最近一批
uv run mds changes $runId
uv run mds rollback $runId --batch latest
uv run mds rollback $runId --batch latest --yes
```

分析可用 `Ctrl-C` 中断；再次执行 `uv run mds analyze $runId` 可续跑。更多分步操作、专辑裁决、报告导出与参数说明见[开发指南](docs/development.md#命令行进阶)，每条命令也支持 `--help`。

## 常见问题

<details>
<summary><strong>Windows 提示“Windows 已保护你的电脑”怎么办？</strong></summary>

当前发布包未做代码签名，首次运行可能触发 SmartScreen。确认下载来源为本仓库 Release，并核对附件提供的 SHA-256 后，可在提示中选择“更多信息 → 仍要运行”。

</details>

<details>
<summary><strong>写入后 FLAC 文件变小，是音频丢失了吗？</strong></summary>

FLAC 的 `PADDING` 是为后续标签修改预留的空白。标签重写可能重新分配这部分空间，使文件变小。程序在替换前检查 FLAC 音频数据与内嵌图片的哈希；不能仅凭文件大小判断音频是否改变。详见[写入与撤销](#写入与撤销)。

</details>

<details>
<summary><strong>明明匹配到歌曲，为什么还要我确认专辑？</strong></summary>

同一录音可能被收录在原版、再版或合辑等多个发行版中，音频本身未必能区分你持有哪一版。程序会保留候选，支持按专辑整组裁决；已有专辑名与匹配结果不同时默认保留已有值。

</details>

<details>
<summary><strong>结果和快照存在哪里？能清空重来吗？</strong></summary>

- Windows：`%LOCALAPPDATA%\MusicDataManager\音乐数据管家\`
- macOS：`~/Library/Application Support/音乐数据管家/`
- 日志：数据目录下的 `logs/mds.log`

设置页的“清空分析数据”会删除分析结果、缓存、变更记录与快照，保留密钥、邮箱和界面设置，不修改音乐文件。**清空后，旧批次无法再撤销。**

历史结果保存在本机数据目录，重新解压软件不会清除它们。

</details>

<details>
<summary><strong>启动或连接失败，如何排查？</strong></summary>

Windows 发布包可双击 `诊断工具.bat`；源码用户可执行 `uv run mds doctor`。自检会检查运行环境、`fpcalc`、数据目录与服务连接，仅报告密钥是否配置。

可通过 [Issues](https://github.com/Candelor0/music-data-manager/issues) 反馈，附软件版本、系统版本、复现步骤与相关错误。分享诊断文件前检查本机路径和用户名，不要附 `.env` 或真实密钥。

</details>

## 开发与反馈

代码结构、架构约束、测试和 Windows 打包流程见[开发指南](docs/development.md)。

欢迎通过 [Issues](https://github.com/Candelor0/music-data-manager/issues) 报告问题或提出建议，也欢迎提交 Pull Request。

## 许可证

本项目以 **GPL-3.0-or-later** 授权，全文见 [`LICENSE`](LICENSE)。项目复用 MusicBrainz Picard 的标签读写层，并使用 PyQt5；第三方组件及许可证见 [`licenses/README.md`](licenses/README.md)。

本项目是第三方衍生作品，与 MusicBrainz、MetaBrainz 基金会、MusicBrainz Picard 官方及 Riverbank Computing 均无隶属或背书关系。项目不改写 Picard 本体，仅在其上进行适配。

分发二进制时需同时提供完整对应源码及要求的许可证材料；本项目通过发布标签与 Release 提供对应源码。
