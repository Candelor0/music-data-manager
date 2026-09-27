# 许可证说明

本文件说明「音乐数据管家」自身与它包含的第三方组件的许可证。**发布（让别人下载）时，
本目录与根目录的 `LICENSE` 必须一起提供**，这是 GPL 的硬性要求。

## 1. 本项目的许可证

- **GPL-3.0-or-later**，全文见根目录 [`../LICENSE`](../LICENSE)。
- 为什么是 v3（而不是 v2）：
  - 本项目复用 **MusicBrainz Picard 2.13.3** 的 `formats` 层做标签读写。Picard 是
    **GPL-2.0-or-later**，它的 "or later" 允许我们整体改用 v3。
  - 界面用 **PyQt5**，它按 **GPL v3** 授权。GPLv2-only 与 GPLv3 不能混用，
    所以整体以 **GPLv3** 分发是唯一合规的档位。
- 本项目对 Picard 的修改：见仓库提交历史（我们**不 fork** Picard，只在上层适配，
  Picard 本体未被改写）。

## 2. 非官方声明

本项目是**第三方衍生作品，与 MusicBrainz、MetaBrainz 基金会、MusicBrainz Picard 官方
以及 Riverbank Computing（PyQt）均无任何隶属或背书关系**。名称中若出现相关字样，仅用于
说明技术来源。

## 3. 第三方组件与许可证

### 3.1 随程序一起分发（打包进 exe / 便携版）

| 组件 | 版本 | 许可证 | 说明 |
| --- | --- | --- | --- |
| MusicBrainz Picard | 2.13.3 | GPL-2.0-or-later | 复用其 formats 层读写标签；文本见 `GPL-2.0.txt` |
| PyQt5 | 5.15.11 | GPL-3.0 | 界面框架；文本见根目录 `LICENSE` |
| PyQt5-Qt5（Qt 5.15 运行库） | 5.15.19 | LGPL-3.0 | 文本见 `LGPL-3.0.txt` |
| PyQt5_sip | 12.19.0 | BSD-2-Clause | 代码生成运行时 |
| mutagen | 1.48.1 | GPL-2.0-or-later | 音频标签读写 |
| discid | 1.4.2 | LGPL-3.0-or-later | 光盘 ID；文本见 `LGPL-3.0.txt` |
| Chromaprint / `fpcalc.exe` | 1.5.1 | LGPL-2.1-or-later | 声学指纹；文本见 `LGPL-2.1.txt`。以**独立可执行文件**形式随包分发，可按 LGPL 要求替换 |
| Python（Windows 嵌入式版） | 3.12.10 | PSF-2.0 | 文本见 `PSF-Python-3.12.txt`（便携版）；exe 版由 PyInstaller 冻结 |
| pydantic / pydantic_core | 2.13.5 / 2.46.5 | MIT | 数据校验 |
| httpx / httpcore / h11 | 0.28.1 / 1.0.9 / 0.16.0 | BSD-3-Clause / BSD-3-Clause / MIT | HTTP 客户端 |
| keyring | 25.7.0 | MIT | 把密钥存进系统凭据库 |
| platformdirs | 4.11.12 | MIT | 数据目录定位 |
| certifi / idna / charset-normalizer | 2026.7.22 / 3.20 / 3.5.1 | MPL-2.0 / BSD-3-Clause / MIT | HTTP 依赖 |
| anyio | 4.15.1 | MIT | 异步兼容层 |
| annotated-types / typing-inspection / typing_extensions | 0.8.0 / 0.4.4 / 4.16.0 | MIT / MIT / PSF-2.0 | 类型标注 |
| fasteners / jaraco.classes / jaraco.context / jaraco.functools / more-itertools | 0.20 / 3.4.0 / 6.1.2 / 4.6.0 / 11.1.0 | Apache-2.0 / MIT / MIT / MIT / MIT | keyring 依赖 |
| PyJWT | 2.14.0 | MIT | keyring 依赖 |
| Markdown / Pygments / PyYAML / python-dateutil / six | 3.10.3 / 2.21.0 / 6.0.3 / 2.9.0.post0 / 1.17.0 | BSD-3-Clause / BSD-2-Clause / MIT / BSD-3-Clause / MIT | Picard 依赖 |
| pyobjc-core / pyobjc-framework-Cocoa | 10.3.2 | MIT | 仅 macOS 上使用（macOS 钥匙串） |
| packaging | 26.3 | Apache-2.0 或 BSD-2-Clause | 版本比较 |

### 3.2 仅使用（不随程序分发）

| 组件 | 版本 | 许可证 |
| --- | --- | --- |
| pytest | 9.1.1 | MIT |
| pytest-qt | 4.5.0 | MIT |
| ruff | 0.16.8 | MIT |
| iniconfig / pluggy | 2.3.0 / 1.6.0 | MIT |

> 完整的许可证全文在打包时会一并放进产物的 `许可证/` 目录；上游纯 MIT/BSD 类许可证
> 以"组件名 + 版本 + 许可证名"清单形式给出（GPL 系列与 PSF 的全文已随本目录提供）。

## 4. 本程序使用的网络服务（不涉及代码分发，但需遵守其条款）

| 服务 | 用途 | 条款/限速 |
| --- | --- | --- |
| AcoustID | 音频指纹 → 录音 ID | 需自备免费 API Key；客户端限速 **2 次/秒** |
| MusicBrainz | 录音/发行版元数据 | 需填 User-Agent（应用名/版本 + 邮箱）；限速 **1 次/秒** |
| DeepSeek | 多候选消歧 | 需自备 Key；按量计费，费用由用户自己承担 |

## 5. 用户的音乐文件

本程序**不上传音频本体**；指纹、标签等元数据才会发给上述服务，且可在设置里关闭云端调用。
详见产品文档中的隐私边界（D7）。
