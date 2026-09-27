# 音乐数据管家

给你的本地音乐库请一个管家：它扫一遍目录，用声学指纹和 AI 认出每首歌、每张专辑，
把"哪些标签缺了、哪些写错了、建议改成什么"列成一张待办清单；**你点确认，它才动手**，
而且随时能一键撤销。

它基于开源标签器 **MusicBrainz Picard** 的读写引擎二次开发（复用它的 `formats` 层，
不改写 Picard 本体），界面与 AI 决策链是自己写的。

> **只想用，不想碰代码？** 看 [`使用说明.txt`](packaging/使用说明.txt)，或到 Releases 下载
> Windows 免安装版。下面的内容主要是给开发和排查用的。

| | |
| --- | --- |
| 终端 | 桌面应用（PyQt5），**Windows 优先**，代码保持可移植（平台差异集中在 `adapters/`） |
| 当前版本 | **0.5.1** —— 界面、扫描、分析、写入、撤销已全部打通，Windows 便携版已发布 |
| 许可证 | **GPL-3.0-or-later**（继承 Picard 的 GPL-2.0-or-later；因链接 PyQt5 的 GPL v3 版，整体按 v3 分发） |

---

## 它做什么，不做什么

**做：**

- 扫描音乐库（支持分层目录、中文/方括号/全角文件名），建立一次"运行"（run）并可续跑
- 声学指纹 → AcoustID → MusicBrainz → 云端模型消歧，给出标签建议与理由
- 利用**同目录互证**（同一个文件夹基本就是同一张专辑）先补空字段，再交给 AI
- 待办按风险分成 4 组（安全 / 需留意 / 需裁决 / 冲突），长列表压成几行
- 写入前给你看差异，写入后能按批次一键撤销；命令行与界面共用同一套内核

**不做：**

- **不上传音频本体** —— 只发送匹配需要的元数据与指纹，且云端调用可关闭
- **不自动改标签** —— 没有你点确认，一个字都不改
- **不动文件本身** —— 不改名、不移动、不删除
- **不覆盖已有的正确标签** —— 只填空、只按规则清洗，改已有值必须你逐条点头

---

## 图形界面：三步上手

```bash
uv sync            # 需要 uv 与 Python 3.12
uv run mds gui     # 打开界面（macOS 上也可以双击工程根目录的「打开界面.command」）
```

1. **设置页**填三样东西：音乐库文件夹、联系邮箱（MusicBrainz 要求，不用注册）、
   可选的 AcoustID / DeepSeek Key（不填也能跑，只是能力少一些）。填过的密钥存在
   **系统凭据库**里，关掉软件不用重输；「测试连接」可以分别验三个服务与 Key 是否有效。
2. 点**开始**。第一段（扫描 + 分组）全在本地、免费；第二段才联网并用模型，**要花钱**——
   界面常驻显示"本次预计花多少钱"，超出预算自动停下。
3. 到**待办**页看清单：安全的可以直接批量勾选，需要你决定的逐条裁决（或按专辑一次裁决）。
   写入前有差异预览，写入后底部有**撤销上一批**。

> 界面的配色、字号、间距集中在 `ui/theme.py`，术语解释在 `ui/glossary.py`；
> 想换外观不需要动别的地方（有测试守着，别处不许出现颜色字面量与硬编码字号）。

---

## 命令行（可选）

界面上的「开始」按钮走的就是下面这条链路，命令行适合批量、脚本化、以及出问题时排查。

### 跑一遍

```bash
uv run mds doctor                     # 环境自检（密钥只报有无，绝不回显内容）
uv run mds run "D:\Music"             # 一键：扫描 → 分组 → 分析 → 生成计划（不写文件）

# 也可以分步来
uv run mds scan                       # 扫描（默认读 .env 的 MUSIC_LIBRARY_PATH）
uv run mds analyze <run_id>           # 指纹 → AcoustID → MusicBrainz → 模型 → 决策
uv run mds group   <run_id>           # 同目录分组 + 共识补空（本地、免费，不联网）
```

`analyze` 可以随时 `Ctrl-C` 中断，再跑同一条命令就是**续跑**（只补没做完的，不重复花钱）。

### 看待办、做决定

```bash
uv run mds todo  <run_id> --limit 5          # 待办 4 组（默认只输出 4 行，--limit 才展开明细）
uv run mds choose <run_id>                   # 列出需要你裁决的专辑
uv run mds choose <run_id> --folder "某专辑目录" --candidate 0   # 同一张专辑只选一次

uv run mds plan  <run_id> --decisions        # 哪几条需要你决定（带 #编号）
uv run mds plan  <run_id> --adopt-ai 118 --refresh   # 采纳 AI 对 #118 的判断
uv run mds plan  <run_id> --keep-existing 118        # 改回来（保留我的）
uv run mds plan  <run_id> --skip 120 --refresh       # 跳过 #120
uv run mds plan  <run_id> --pick 130 2 --refresh     # #130 选第 3 个候选（从 0 数起）
```

### 写入、校验、撤销

```bash
uv run mds apply    <run_id>          # 只预览（默认 dry-run，不加 --yes 永远不写）
uv run mds apply    <run_id> --yes    # 真的写
uv run mds verify   <run_id>          # 重新读文件核对
uv run mds changes  <run_id>          # 变更记录（含前后 sha256）
uv run mds rollback <run_id> --yes                        # 一键回滚
uv run mds rollback <run_id> --batch latest --yes         # 只回滚最近一批（界面的「撤销上一批」）
uv run mds report   <run_id> --md 建议报告.md --csv 建议报告.csv
uv run mds runs                       # 历史运行
```

### 常用参数

| 参数 | 说明 |
| --- | --- |
| `--limit N` | 只处理前 N 个文件 |
| `--new` | 强制新建 run（默认复用未完成的 run，以便续跑） |
| `--no-llm` | 不调用云端模型（状态标为 `skipped`，之后可用 AI 补跑） |
| `--retry-errors` | 重试之前失败的条目 |
| `--refresh-decisions` | 用已缓存结果重算决策（**不重复调用云端、不花钱**） |
| `--assume-no-album-tag` | 验证用：忽略已有专辑标签，模拟"标签缺失的音乐库" |
| `--mode full\|no_album\|none` | 给模型的标签证据级别 |
| `--budget` | 每 100 首的云端预算上限（元），超出即停止云端调用 |
| `apply --snapshot-mode tags\|full` | 快照模式：`tags`（默认，只存原标签）/ `full`（整文件字节备份） |
| `apply --skip-integrity` | 跳过写入前的音频/图片指纹比对（大库提速，代价是少一道证据） |
| `rollback --force` | 即使文件在写入后又被外部改过，也强制回滚（危险） |

---

## 它凭什么保证不弄坏你的音乐

这一节是"只动标签、不动音频"这句话的实际依据，不是承诺：

```text
1. 算原文件的「音频 + 内嵌图片」sha256
2. 建快照（默认只存原标签，约占文件大小的 0.07%）
3. 复制一份副本 → 在【副本】上写标签 → 读回校验
4. 比对副本与原文件的音频/图片 sha256   ← 不一致就中止，此刻原文件还没被碰
5. os.replace 原子替换（到这一步原文件才变）
6. 记录变更（含前后 sha256），可按批次回滚
```

### ⚠️ 写 FLAC 会让文件变小，但那不是数据丢失

FLAC 里有一块 **`PADDING`（预留空白）**，是给"将来就地改标签"用的。Picard 重写标签块时
会把它重算成很小的一块，于是文件更小（实测一首 56.9 MB 的变成 45.6 MB）。

**音频与内嵌图片的 sha256 完全不变**（有强制校验）。少的只是预留空白。

### 回滚能回到什么程度

| 模式 | 能回到什么程度 |
| --- | --- |
| `tags`（默认） | 标签 100% 还原；**音频与图片 sha256 与原文件一致**；文件字节不完全相同（padding/块顺序） |
| `full` | **逐字节完全一致**（代价：快照与音乐库等大） |

---

## 工程结构

| 位置 | 说明 |
| --- | --- |
| `src/mds/core/` | **纯函数层**：归一化、清洗、差异、候选评分、决策编排。禁止 `import PyQt5 / PyQt6 / picard / httpx`（有测试守卫） |
| `src/mds/adapters/` | 唯一接触外部世界的地方：限流、指纹、AcoustID、MusicBrainz、模型、读/写标签、快照、凭据库 |
| `src/mds/storage/` | SQLite：任务、条目、分组、审计、三层缓存（指纹 / 候选 / 模型） |
| `src/mds/pipeline/` | 编排：扫描 → 分析 → 分组 → 计划 → 写入 → 校验 → 回滚 |
| `src/mds/ui/` | PyQt5 界面。只经由 `pipeline.apply` / `pipeline.rollback` 这两个受控入口写入，**界面自己没有改文件的能力**（`tests/unit/test_ui_boundaries.py` 用 AST 断言守着） |
| `src/mds/cli.py` | 入口，只做参数解析与调用 |
| `tests/unit/` | mock 自动化测试（不联网、不花钱） |
| `poc/` | 早期的可行性验证脚本（保留备查） |
| `tools/fpcalc` | 声学指纹计算器（第三方二进制，不入库，用脚本下载） |
| `assets/` | 应用图标（由 `scripts/make-icon.py` **用代码画**出来，仓库不放美术资源） |
| `packaging/` | 随发布包发给最终用户的文件（如 `使用说明.txt`） |

**运行期数据**不落在工程目录里：

- Windows：数据 `%LOCALAPPDATA%\MusicDataManager\音乐数据管家\`，配置 `%APPDATA%\音乐数据管家\`
- macOS：`~/Library/Application Support/音乐数据管家/`
- 日志：`<数据目录>/logs/mds.log`（滚动 2MB × 3）

---

## 密钥与配置

密钥只放本地 `.env`：**不进仓库、不进代码、不进日志、不进前端产物**。日志层有脱敏过滤器，
即使某处把密钥拼进了消息，落盘前也会被换成 `***`。

- `.env` 是以点开头的隐藏文件（Finder 里按 `Cmd + Shift + .` 显示）
- 仓库里只提交 `.env.example`（模板，无真值）
- 优先级：**设置页 > `.env` > 默认值**；设置页里存的密钥进**系统凭据库**，
  凭据库不可用时退回本地文件，并如实告诉你"这是明文"

| 变量 | 从哪来 | 用途 |
| --- | --- | --- |
| `ACOUSTID_API_KEY` | https://acoustid.org/my-applications（**要用"应用"key，不是账号 key**） | 声学指纹查歌 |
| `DEEPSEEK_API_KEY` | https://platform.deepseek.com | 候选消歧 |
| `MUSICBRAINZ_USER_AGENT` | 自己填 `应用名/版本 ( 邮箱 )` | MusicBrainz 强制要求 |
| `MUSIC_LIBRARY_PATH` | 本机音乐库目录 | `mds scan` 的默认目录 |

> ⚠️ AcoustID 有两种 key，格式一模一样（10 位字母数字）但用途不同：
> 账号页 `/api-key` 那把只能**提交**指纹；**查歌必须用 `/my-applications` 里的应用 key**。
> 用错时服务端只回 `invalid API key`，不告诉你是哪一种 —— 这个坑踩过。

---

## 设计约束（改之前先看这里）

这些不是风格偏好，每一条背后都有一次实测或一次事故：

| 约束 | 原因 |
| --- | --- |
| **AcoustID 每秒最多 2 次** | 产品要求（官方上限 3） |
| **MusicBrainz 每秒最多 1 次** | 官方要求；全链路瓶颈（1 万首全扫约 3 小时） |
| 所有网络请求必须经过 `adapters/ratelimit.py` | 唯一出口，绕不过去 |
| MusicBrainz 查询必须带 `inc=media` | 少了它候选里没有时长，而时长是最强证据 |
| 模型用 `deepseek-flash` + **关闭思考模式** | 实测输出 token 36→11、耗时 0.9s→0.4s |
| 提示词**不得**偏好原始发行版 | 实测该倾向会让准确率掉 4 个点 |
| **不覆盖文件里已有的正确标签** | 用户在真实样例打分时的明确要求 |
| **不用模型自报置信度做自动决策** | 实测高置信度的 2 条被用户 100% 拒绝 |
| 证据不足时给候选让用户选 | 同一录音有几十个发行版，机器无法从音频分辨你手上是哪张 |
| **写入默认 dry-run** | 不加 `--yes` 永远不写文件 |
| **替换前必须过音频/图片指纹校验** | 让"只动标签"成为证据，而不是承诺 |
| **回滚前检查文件是否被外部改过** | 免得覆盖用户后来的修改 |
| **同目录共识只补空字段，绝不覆盖已有值** | 与"不覆盖已有正确标签"同源；实测同一目录就是同一张专辑 |
| **孤证不算共识（至少 2 首一致）** | 12 首里只有 1 首写了年份，那是孤证不是共识 |
| **曲名与音轨号不参与同目录互证** | 它们逐文件本来就该不同 |
| **目录名只用于给候选打分，不作为写入值** | 实测存在"标签比目录名更准"（标签 `假想专辑2000(珍藏版)` vs 文件夹 `假想专辑2000`） |
| **界面不得直接改文件** | 只能走 `pipeline` 的受控入口（AST 测试保证） |

---

## 开发

```bash
uv run pytest                 # 557 项 mock 测试（另有 2 项有意跳过）
uv run ruff check src tests   # 静态检查
uv run ruff format src tests  # 可选
```

几条由测试守着的架构约束（改坏了测试会立刻变红）：

- `core/` 不许引入 Qt / Picard / 网络库 —— `tests/unit/test_boundaries.py`
- 功能层不许反向依赖界面；界面不许直接碰写入链路与文件操作 —— `tests/unit/test_ui_boundaries.py`
- 界面颜色/字号/间距只能来自 `ui/theme.py` —— `tests/unit/test_theme.py`
- 许可证文本、依赖清单、打包脚本必须带上许可证 —— `tests/unit/test_licensing.py`
- 发布包不许夹带本机信息与密钥 —— `tests/unit/test_release_zip.py`

> 界面测试在没人看屏幕的机器上也能跑：`tests/conftest.py` 会自动把 Qt 切到 `offscreen` 平台。

---

## 构建与发布

```bash
uv run python scripts/make-icon.py                 # 画应用图标 → assets/icon.png + icon.ico
uv run python scripts/build-windows-portable.py    # 打 Windows 便携版（在 macOS 上交叉构建）
uv run python scripts/build-exe-kit.py             # 打「exe 构建包」（约 2 MB）交给 Windows 机器
uv run python scripts/make-release-zip.py <构建产物.zip 或 dist 目录>
                                                   # 收拾成可上传 Release 的版本
uv run python scripts/ui-preview.py <输出目录>      # 用真实库的 run 出界面预览图
uv run python scripts/ui-snapshot-metrics.py 图…    # 量截图的明暗分布（防"整体像一张白纸"）
```

**exe 只能在 Windows 上构建**（PyInstaller 不支持交叉编译）：在 macOS 上跑
`build-exe-kit.py` 打一个小包，拷到 Windows 解压后双击 `构建exe.bat` 即可。

发布前**必须**再跑一遍 `scripts/make-release-zip.py`。构建脚本产出的 zip 是"给自己用"的：
里面可能有构建者那台机器的信息（`diagnostics.txt` 就有用户名与路径），也缺 GPL 要求随二进制
分发的许可证文本。这个脚本会扔掉不该外发的、补上该有的、把中文文件名统一成 UTF-8，
并在发现本机信息、密钥或缺失文件时**直接拒绝放行**。

> 附件名请用 ASCII：实测 GitHub 会把 Release 附件名里的中文**直接抹掉**
> （`音乐数据管家-0.5.1-发布版.zip` 上传后变成 `-0.5.1-.zip`，文件完好、名字没了）。
> 所以脚本产出的是 `mds-<版本>-windows-portable.zip`。包**里面**的目录名仍是中文。

---

## 常见问题

**Q：第一次运行 Windows 弹"Windows 已保护你的电脑"？**
没有代码签名，属正常。点「更多信息 → 仍要运行」。

**Q：跑完发现音乐文件变小了？**
见上文「写 FLAC 会让文件变小」。音频与图片的 sha256 没变，少的只是预留空白。

**Q：想彻底清空，重来一遍？**
界面设置页有「清空分析数据」（删结果/缓存/快照，**保留密钥与设置，绝不碰音乐文件**）；
或者直接删掉上面「运行期数据」那个目录。

**Q：界面里"上次的结果"是哪来的？**
都来自数据目录里的数据库。交付的包不带任何数据；看到旧记录说明那是你自己机器上的历史。

**Q：同一个专辑在待办里点很多次很烦？**
用「按专辑裁决」：同一张专辑选一次，整组跟着走。

---

## 许可证 / 非官方声明

- 本项目以 **GPL-3.0-or-later** 授权，全文见 [`LICENSE`](LICENSE)。
  继承 MusicBrainz Picard（GPL-2.0-or-later）；因链接 PyQt5（GPL v3），整体按 v3 分发。
- 第三方组件清单与完整许可证文本见 [`licenses/README.md`](licenses/README.md)。
- **本项目是第三方衍生作品，与 MusicBrainz / MetaBrainz 基金会 / MusicBrainz Picard 官方
  以及 Riverbank Computing 均无隶属或背书关系。** 本程序不改写 Picard 本体，只在其上做适配。
- 发布二进制时必须同时提供完整对应源码（本项目用 git tag + Release 附件满足这一条）。
