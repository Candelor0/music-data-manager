"""界面偏好与密钥的读写（薄封装）。

**三类东西分开放，不要混：**

| 内容 | 放哪 | 为什么 |
| --- | --- | --- |
| 音乐库目录 / 费用弹窗开关 / 窗口大小 | 数据库 `preferences` 表 | 普通偏好，不含机密 |
| AcoustID / DeepSeek 的 Key | **系统凭据库**（`adapters/secrets.py`） | 密钥不明文落盘 |
| MusicBrainz 邮箱 | `preferences` 表 | 它不是密钥；User-Agent 由程序拼 |

本模块**绝不打印、不记录密钥值**；给界面返回的一律是掩码后的样子。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..adapters.secrets import SecretStore, masked
from ..config import Settings, load_settings, secret_store
from ..storage.db import Database

# ── 偏好键名（**不得出现 KEY/SECRET/PASSWORD 之类的名字**，有测试守着）──
COST_CONFIRM = "cost_estimate_confirm"
LIBRARY_PATH = "library_path"
WINDOW_GEOMETRY = "window_geometry"
TODO_EXPANDED = "todo_expanded"
EMAIL = "musicbrainz_email"
# ─────────────────────
FONT_SCALE = "font_scale"                 # "standard" | "large"
SHOW_HINTS = "show_hints"                 # 显示上手提示（三步进度 + 就地说明）
TERM_TOOLTIPS = "term_tooltips"           # 术语悬停解释
USAGE_COUNT = "usage_count"               # 打开过几次（用满 5 次后进度条收成细条）
FILTER_TAB = "filter_tab"                 # 上次选中的筛选标签

FONT_SCALES = ("standard", "large")

_TRUTHY = ("1", "true", "yes", "on", "是", "开")


def get_pref(db: Database, key: str, default: str = "") -> str:
    """取一个界面偏好（读不到就用默认值）。"""
    return db.get_pref(key, default) or default


def get_bool(db: Database, key: str, default: bool = False) -> bool:
    raw = db.get_pref(key, "")
    if raw == "":
        return default
    return raw.strip().lower() in _TRUTHY


def set_bool(db: Database, key: str, value: bool) -> None:
    db.set_pref(key, "1" if value else "0")


def get_int(db: Database, key: str, default: int = 0) -> int:
    try:
        return int(db.get_pref(key, "") or default)
    except ValueError:
        return default


def bump_usage(db: Database) -> int:
    """打开次数 +1 并返回新值（用来决定"用熟了没"）。"""
    value = get_int(db, USAGE_COUNT, 0) + 1
    db.set_pref(USAGE_COUNT, str(value))
    return value


def font_scale(db: Database) -> str:
    value = db.get_pref(FONT_SCALE, "standard")
    return value if value in FONT_SCALES else "standard"


def interface_prefs(db: Database) -> dict[str, object]:
    """界面相关的偏好（字号 / 两个开关 / 打开次数），一次取齐给窗口用。"""
    return {
        "font_scale": font_scale(db),
        "show_hints": get_bool(db, SHOW_HINTS, True),
        "term_tooltips": get_bool(db, TERM_TOOLTIPS, True),
        "usage_count": get_int(db, USAGE_COUNT, 0),
    }


def effective_settings(db: Database, *, store: SecretStore | None = None) -> Settings:
    """把设置页里的偏好 + 凭据库里的密钥合并成一份生效配置。

    优先级：设置页 > `.env` > 内置默认（见 config.load_settings 的说明）。
    """
    return load_settings(store=store, prefs=db.get_prefs())


@dataclass
class SettingsView:
    """设置页要显示的东西（**全部是掩码或布尔量，不含明文密钥**）。"""

    email: str = ""
    has_acoustid: bool = False
    has_deepseek: bool = False
    acoustid_masked: str = ""
    deepseek_masked: str = ""
    secret_location: str = ""
    secret_is_secure: bool = True
    secret_source: str = ""
    cost_confirm: bool = False
    library_path: str = ""
    batch_limit: int = 2000
    env_file: str = ""

    @property
    def secret_place_line(self) -> str:
        if self.secret_is_secure:
            return f"密钥存在：{self.secret_location}（加密）"
        return f"密钥存在：{self.secret_location}（**明文文件**，请注意保管）"

    @property
    def source_line(self) -> str:
        return f"当前生效来源：{self.secret_source}" if self.secret_source else "当前生效来源：未配置"


def settings_view(db: Database, *, store: SecretStore | None = None) -> SettingsView:
    """给设置页用的只读视图。"""
    secrets_store = store if store is not None else secret_store()
    settings = effective_settings(db, store=secrets_store)

    acoustid = secrets_store.get("ACOUSTID_API_KEY") or ""
    deepseek = secrets_store.get("DEEPSEEK_API_KEY") or ""
    # 界面显示掩码：**不把值往外传**
    return SettingsView(
        email=settings.musicbrainz_email,
        has_acoustid=settings.has_acoustid,
        has_deepseek=settings.has_deepseek,
        acoustid_masked=masked(acoustid) if acoustid else ("已配置（来自 .env）" if settings.has_acoustid else ""),
        deepseek_masked=masked(deepseek) if deepseek else ("已配置（来自 .env）" if settings.has_deepseek else ""),
        secret_location=secrets_store.location(),
        secret_is_secure=secrets_store.is_secure,
        secret_source=settings.secret_source,
        cost_confirm=settings.cost_estimate_confirm,
        library_path=settings.music_library_path,
        batch_limit=settings.batch_size_limit,
        env_file=settings.env_file,
    )


@dataclass
class SaveReport:
    saved: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed

    def message(self) -> str:
        parts = []
        if self.saved:
            parts.append("已保存：" + "、".join(self.saved))
        if self.failed:
            parts.append("没存成：" + "、".join(self.failed))
        return "；".join(parts) if parts else "没有需要保存的改动"


def save_profile(
    db: Database,
    *,
    email: str | None = None,
    acoustid_key: str | None = None,
    deepseek_key: str | None = None,
    store: SecretStore | None = None,
) -> SaveReport:
    """保存设置页填的邮箱与密钥。

    - `None` 表示"这一项没动"，**不会**清空已有值
    - 空串表示"清空这一项"
    - 密钥只走凭据库；邮箱走 preferences 表
    """
    secrets_store = store if store is not None else secret_store()
    report = SaveReport()

    if email is not None:
        db.set_pref(EMAIL, email.strip())
        report.saved.append("邮箱")

    jobs: list[tuple[str, str, str]] = []
    if acoustid_key is not None:
        jobs.append(("AcoustID Key", "ACOUSTID_API_KEY", acoustid_key))
    if deepseek_key is not None:
        jobs.append(("DeepSeek Key", "DEEPSEEK_API_KEY", deepseek_key))

    for label, name, value in jobs:
        try:
            secrets_store.set(name, value.strip())
        except Exception as exc:  # noqa: BLE001 - 凭据库不可用要给人话，不是堆栈
            report.failed.append(f"{label}（{type(exc).__name__}）")
        else:
            report.saved.append(label)
    return report
