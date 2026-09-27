"""音乐数据管家（mds）—— 本地音乐元数据管理工具。

分层约定：
    core/      纯函数：业务规则，禁止 import PyQt6 / picard / httpx
    adapters/  唯一允许接触外部世界（网络、文件、系统）的地方
    storage/   SQLite 持久化
    pipeline/  编排
    cli.py     入口，只做参数解析与调用

许可证：GPL-3.0-or-later（继承 MusicBrainz Picard 的 GPL-2.0-or-later，
因链接 PyQt5 的 GPL v3 版而整体按 v3 分发）。详见根目录 LICENSE 与
licenses/README.md。本项目与 MusicBrainz / MetaBrainz / Picard 官方无隶属关系。
"""

__version__ = "0.5.1"
APP_NAME = "音乐数据管家"
APP_ID = "mds"
