"""配置与密钥。

**优先级**：

    设置页（系统凭据库 / preferences 表） > `.env` 文件 > 代码默认值

反转的原因：设置页存了新 Key 却被 `.env` 里的旧值盖住，用户会以为"没保存成功"。
设置页会明确显示当前生效来源，不让它成为谜。

- **密钥**（AcoustID / DeepSeek）走 `adapters/secrets.py`：系统凭据库优先，
  不可用时退回数据目录下的本地文件（并把"明文"如实告诉用户）。
- **邮箱**存 `preferences` 表，本模块按 MusicBrainz 规范拼成 User-Agent。
- 其余限流 / 预算 / 上限仍走 `.env`。

本模块**绝不在日志或异常里回显密钥值**，只暴露 `has_*` 布尔量与掩码后的末 4 位。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import platformdirs

from . import __version__
from .adapters.secrets import SecretStore

APP_DIR_NAME = "音乐数据管家"
APP_VENDOR = "MusicDataManager"

# 默认值（依据/§2.4 与既定默认值）
DEFAULT_ACOUSTID_RATE = 2.0  # 限流：每秒最多 2 次
DEFAULT_MUSICBRAINZ_RATE = 1.0  # 官方要求
DEFAULT_BUDGET_PER_100 = 0.5  # 元 / 100 首
DEFAULT_BATCH_LIMIT = 2000
DEFAULT_MAX_CANDIDATES_TO_LLM = 10
DEFAULT_LOW_EVIDENCE_CANDIDATES = 10
DEFAULT_FINGERPRINT_LENGTH = 120
DEFAULT_MODEL = "deepseek-flash"
#: 费用预估单价（元 / 100 首）。取实测值 ¥0.096 向上取整到 0.1
DEFAULT_ESTIMATED_COST_PER_100 = 0.1
#: 「开始」前是否弹费用预估确认框。**默认关闭**（既定）；
#: 关的是「点击」，不是「知情」—— 预估金额常驻界面，熔断永远生效。
DEFAULT_COST_ESTIMATE_CONFIRM = False

# 支持的容器
SUPPORTED_EXTS = {".mp3", ".flac", ".m4a", ".ogg"}


def config_dir() -> Path:
    return Path(platformdirs.user_config_dir(APP_DIR_NAME, APP_VENDOR))


def data_dir() -> Path:
    return Path(platformdirs.user_data_dir(APP_DIR_NAME, APP_VENDOR))


def log_dir() -> Path:
    return data_dir() / "logs"


def db_path() -> Path:
    return data_dir() / "mds.db"


def snapshot_dir() -> Path:
    """写入前的快照存放位置（回滚依据）。"""
    return data_dir() / "snapshots"


def is_frozen() -> bool:
    """是不是「打包成 exe 之后」在跑（PyInstaller 会设 `sys.frozen`）。"""
    return bool(getattr(sys, "frozen", False))


def project_root() -> Path:
    """程序根目录。

    - **源码运行**：`src/mds/config.py` 往上三级 = 工程根
    - **打包成 exe 后**：exe 所在的目录

    两者都「长得像工程根」：下面有 `tools/fpcalc.exe`、`assets/icon.*`，
    可能还有 `.env`。所以打包时把这些一起放在 exe 旁边，
    `fpcalc_path()`、`find_env_file()` 这些就一行都不用改。
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def resource_dir() -> Path:
    """只读资源目录（图标等）。

    打包后资源会被解到 `sys._MEIPASS`（PyInstaller 的临时目录），
    所以先找那里；找不到再回落到程序根目录。
    """
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidate = Path(bundle)
        if candidate.is_dir():
            return candidate
    return project_root()


def find_env_file() -> Path | None:
    explicit = os.environ.get("MDS_ENV_FILE")
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None
    for base in (Path.cwd(), project_root()):
        candidate = base / ".env"
        if candidate.is_file():
            return candidate
    return None


def parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    # utf-8-sig：Windows 记事本保存 UTF-8 时会加 BOM，
    # 不剥掉的话第一个键名会变成 "\ufeffKEY" 而读不到（实测踩过）。
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip()
    return out


@dataclass(frozen=True)
class Settings:
    acoustid_api_key: str = ""
    deepseek_api_key: str = ""
    musicbrainz_user_agent: str = ""
    music_library_path: str = ""
    acoustid_rate: float = DEFAULT_ACOUSTID_RATE
    musicbrainz_rate: float = DEFAULT_MUSICBRAINZ_RATE
    budget_per_100_tracks: float = DEFAULT_BUDGET_PER_100
    batch_size_limit: int = DEFAULT_BATCH_LIMIT
    max_candidates_to_llm: int = DEFAULT_MAX_CANDIDATES_TO_LLM
    low_evidence_candidates: int = DEFAULT_LOW_EVIDENCE_CANDIDATES
    fingerprint_length: int = DEFAULT_FINGERPRINT_LENGTH
    model: str = DEFAULT_MODEL
    env_file: str = ""

    # ─────────────────────
    #: 只填邮箱，User-Agent 由 build_user_agent() 按规范拼装
    musicbrainz_email: str = ""
    #: 「开始」前是否弹费用预估确认框
    cost_estimate_confirm: bool = DEFAULT_COST_ESTIMATE_CONFIRM
    #: 预估单价（元/100 首）
    estimated_cost_per_100: float = DEFAULT_ESTIMATED_COST_PER_100
    #: 密钥当前生效来源（"设置页" / ".env 文件" / ""）—— 不含任何密钥值
    secret_source: str = ""
    #: 密钥存放位置说明（凭据库描述或文件路径）—— 不含任何密钥值
    secret_location: str = ""
    #: 存放位置是否加密（界面要如实显示「明文」）
    secret_is_secure: bool = True

    @property
    def has_acoustid(self) -> bool:
        return bool(self.acoustid_api_key)

    @property
    def has_deepseek(self) -> bool:
        return bool(self.deepseek_api_key)

    @property
    def has_user_agent(self) -> bool:
        return bool(self.musicbrainz_user_agent) and "请填" not in self.musicbrainz_user_agent

    def secret_values(self) -> list[str]:
        """用于日志脱敏的密钥真值列表（只在本进程内使用，不外传）。"""
        return [v for v in (self.acoustid_api_key, self.deepseek_api_key) if v]


def _to_float(value: str, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _to_bool(value: str, fallback: bool) -> bool:
    if value is None or str(value).strip() == "":
        return fallback
    return str(value).strip().lower() in ("1", "true", "yes", "on", "是", "开")


def _to_int(value: str, fallback: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return fallback


def secret_store() -> SecretStore:
    """当前应该用的密钥存储（凭据库优先，不可用则本地文件）。"""
    from .adapters.secrets import default_store  # 延迟导入，避免 config ↔ adapters 循环

    return default_store(service=APP_DIR_NAME, data_dir=data_dir())


def build_user_agent(email: str) -> str:
    """按 MusicBrainz 要求拼 User-Agent：`应用名/版本 ( contact: 邮箱 )`。

    用户只需要在设置页填一个邮箱，格式由程序保证 —— 填错格式会被 MusicBrainz 拒。
    """
    address = (email or "").strip()
    if not address:
        return ""
    return f"MusicDataManager/{__version__} ( contact: {address} )"


def load_settings(
    *,
    store: SecretStore | None = None,
    prefs: dict[str, str] | None = None,
) -> Settings:
    """读配置。

    参数：
        store  密钥存储；测试注入 MemoryStore 即可**完全不碰真实凭据库**
        prefs  界面偏好（来自数据库 `preferences` 表）；不传则忽略设置页的值
    """
    from .adapters.secrets import SECRET_KEYS  # 延迟导入

    prefs = prefs or {}
    env_file = find_env_file()
    merged: dict[str, str] = parse_env_file(env_file) if env_file else {}

    # ── 密钥：设置页（凭据库）优先于 .env ────────────────
    secrets_store = store if store is not None else secret_store()
    from_secrets: dict[str, str] = {}
    for name in SECRET_KEYS:
        value = secrets_store.get(name)
        if value:
            from_secrets[name] = value

    def secret(name: str) -> tuple[str, str]:
        """返回 (值, 来源)。"""
        if from_secrets.get(name):
            return from_secrets[name], "设置页"
        if merged.get(name):
            return merged[name], ".env 文件"
        return "", ""

    acoustid, acoustid_src = secret("ACOUSTID_API_KEY")
    deepseek, deepseek_src = secret("DEEPSEEK_API_KEY")

    # ── 邮箱：设置页只填邮箱，User-Agent 由程序拼 ─────────
    email = prefs.get("musicbrainz_email", "").strip()
    user_agent = build_user_agent(email) if email else merged.get("MUSICBRAINZ_USER_AGENT", "")

    # ── 音乐库目录：设置页优先 ───────────────────────────
    library = prefs.get("library_path", "").strip() or merged.get("MUSIC_LIBRARY_PATH", "")

    source = ""
    if acoustid_src or deepseek_src:
        source = "设置页" if "设置页" in (acoustid_src, deepseek_src) else ".env 文件"

    return Settings(
        acoustid_api_key=acoustid,
        deepseek_api_key=deepseek,
        musicbrainz_user_agent=user_agent,
        musicbrainz_email=email,
        music_library_path=library,
        acoustid_rate=_to_float(merged.get("ACOUSTID_RATE", ""), DEFAULT_ACOUSTID_RATE),
        musicbrainz_rate=_to_float(merged.get("MUSICBRAINZ_RATE", ""), DEFAULT_MUSICBRAINZ_RATE),
        budget_per_100_tracks=_to_float(
            merged.get("CLOUD_BUDGET_PER_100_TRACKS", ""), DEFAULT_BUDGET_PER_100
        ),
        batch_size_limit=_to_int(merged.get("BATCH_SIZE_LIMIT", ""), DEFAULT_BATCH_LIMIT),
        max_candidates_to_llm=_to_int(
            merged.get("MAX_CANDIDATES_TO_LLM", ""), DEFAULT_MAX_CANDIDATES_TO_LLM
        ),
        low_evidence_candidates=_to_int(
            merged.get("LOW_EVIDENCE_CANDIDATES", ""), DEFAULT_LOW_EVIDENCE_CANDIDATES
        ),
        fingerprint_length=_to_int(
            merged.get("FINGERPRINT_LENGTH", ""), DEFAULT_FINGERPRINT_LENGTH
        ),
        cost_estimate_confirm=_to_bool(
            prefs.get("cost_estimate_confirm", merged.get("COST_ESTIMATE_CONFIRM", "")),
            DEFAULT_COST_ESTIMATE_CONFIRM,
        ),
        estimated_cost_per_100=_to_float(
            merged.get("ESTIMATED_COST_PER_100", ""), DEFAULT_ESTIMATED_COST_PER_100
        ),
        secret_source=source,
        secret_location=secrets_store.location(),
        secret_is_secure=secrets_store.is_secure,
        env_file=str(env_file) if env_file else "",
    )



def fpcalc_path() -> Path:
    """随包分发的 fpcalc。优先工程 tools/，其次 PATH。"""
    import shutil  # noqa: PLC0415

    for name in ("fpcalc", "fpcalc.exe"):
        candidate = project_root() / "tools" / name
        if candidate.is_file():
            return candidate
    found = shutil.which("fpcalc")
    if found:
        return Path(found)
    return project_root() / "tools" / "fpcalc"  # 返回期望路径，便于报错提示
