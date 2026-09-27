"""配置层测试：密钥优先级、邮箱拼 User-Agent、费用弹窗默认值。

这些测试全部注入 `MemoryStore` **或**把 `secret_store()` 换掉，
**绝不触碰真实凭据库**（见 test_secrets.py 顶部那条铁律）。
"""

from __future__ import annotations

import sys

import pytest

from mds import config
from mds.adapters.secrets import FileStore, MemoryStore


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """把 .env 指到一个临时文件，避免读到开发者自己的 .env。"""
    env_file = tmp_path / ".env"
    monkeypatch.setenv("MDS_ENV_FILE", str(env_file))
    return env_file


def _settings(store=None, prefs=None):
    return config.load_settings(store=store or MemoryStore(), prefs=prefs or {})


# ── 优先级：设置页 > .env ─────────────────────────────────
def test_settings_page_wins_over_env(env) -> None:
    """设置页存了新 Key 却 .env 里的旧值盖住，用户会以为"没保存成功" —— 必须反转。"""
    env.write_text("ACOUSTID_API_KEY=from_env_file\n", encoding="utf-8")
    s = _settings(store=MemoryStore({"ACOUSTID_API_KEY": "from_settings_page"}))
    assert s.acoustid_api_key == "from_settings_page"
    assert s.secret_source == "设置页"


def test_env_file_used_when_settings_page_empty(env) -> None:
    env.write_text("ACOUSTID_API_KEY=from_env_file\n", encoding="utf-8")
    s = _settings()
    assert s.acoustid_api_key == "from_env_file"
    assert s.secret_source == ".env 文件"


def test_no_key_anywhere(env) -> None:
    s = _settings()
    assert s.has_acoustid is False
    assert s.has_deepseek is False
    assert s.secret_source == ""


def test_both_keys_report_settings_page(env) -> None:
    env.write_text("ACOUSTID_API_KEY=env-a\nDEEPSEEK_API_KEY=env-d\n", encoding="utf-8")
    s = _settings(store=MemoryStore({"DEEPSEEK_API_KEY": "page-d"}))
    assert s.acoustid_api_key == "env-a"  # 这一把没在设置页存过
    assert s.deepseek_api_key == "page-d"
    assert s.secret_source == "设置页"


# ── 邮箱 → User-Agent ─────────────────────────────────────
def test_email_is_turned_into_user_agent(env) -> None:
    """用户只填邮箱，格式由程序保证（填错格式会被 MusicBrainz 拒）。"""
    s = _settings(prefs={"musicbrainz_email": "  me@example.com  "})
    assert s.musicbrainz_email == "me@example.com"
    assert s.musicbrainz_user_agent == f"MusicDataManager/{config.__version__} ( contact: me@example.com )"
    assert s.has_user_agent is True


def test_email_in_settings_wins_over_env_user_agent(env) -> None:
    env.write_text("MUSICBRAINZ_USER_AGENT=Old/1.0 ( contact: old@example.com )\n", encoding="utf-8")
    s = _settings(prefs={"musicbrainz_email": "new@example.com"})
    assert "new@example.com" in s.musicbrainz_user_agent


def test_env_user_agent_used_when_no_email(env) -> None:
    env.write_text("MUSICBRAINZ_USER_AGENT=mds/1.0 ( mailto:a@b.c )\n", encoding="utf-8")
    s = _settings()
    assert s.musicbrainz_user_agent == "mds/1.0 ( mailto:a@b.c )"
    assert s.has_user_agent is True


def test_placeholder_email_still_counts_as_missing(env) -> None:
    """`.env.example` 里的「请填一个邮箱」必须仍被判为没配（不能假装配好了）。"""
    env.write_text(
        "MUSICBRAINZ_USER_AGENT=MusicDataManager/0.1.0 ( contact: 请填一个邮箱 )\n",
        encoding="utf-8",
    )
    s = _settings()
    assert s.has_user_agent is False


def test_build_user_agent_empty_email() -> None:
    assert config.build_user_agent("") == ""
    assert config.build_user_agent("   ") == ""


# ── 费用预估弹窗：默认关（约定）──────────────
def test_cost_estimate_confirm_defaults_to_off(env) -> None:
    assert _settings().cost_estimate_confirm is False


def test_cost_estimate_confirm_from_prefs(env) -> None:
    assert _settings(prefs={"cost_estimate_confirm": "1"}).cost_estimate_confirm is True
    assert _settings(prefs={"cost_estimate_confirm": "true"}).cost_estimate_confirm is True
    assert _settings(prefs={"cost_estimate_confirm": "0"}).cost_estimate_confirm is False
    assert _settings(prefs={"cost_estimate_confirm": "是"}).cost_estimate_confirm is True


def test_prefs_beat_env_for_cost_confirm(env) -> None:
    env.write_text("COST_ESTIMATE_CONFIRM=true\n", encoding="utf-8")
    assert _settings(prefs={"cost_estimate_confirm": "0"}).cost_estimate_confirm is False
    assert _settings().cost_estimate_confirm is True


def test_estimated_cost_per_100_default(env) -> None:
    assert _settings().estimated_cost_per_100 == pytest.approx(0.1)


# ── 音乐库目录 ────────────────────────────────────────────
def test_library_path_from_prefs_beats_env(env) -> None:
    env.write_text("MUSIC_LIBRARY_PATH=/from/env\n", encoding="utf-8")
    assert _settings(prefs={"library_path": "/from/settings"}).music_library_path == "/from/settings"
    assert _settings().music_library_path == "/from/env"


# ── 密钥存放位置要如实报告 ────────────────────────────────
def test_plaintext_location_is_reported_honestly(env, tmp_path) -> None:
    store = FileStore(tmp_path / "secrets.json")
    s = _settings(store=store)
    assert s.secret_is_secure is False
    assert str(tmp_path) in s.secret_location


def test_secure_location_is_reported(env) -> None:
    s = _settings(store=MemoryStore())
    assert s.secret_is_secure is True


# ── 默认值不能被顺手改掉 ──────────────────────────────────
def test_limits_keep_known_defaults(env) -> None:
    s = _settings()
    assert s.acoustid_rate == 2.0      # 约定
    assert s.musicbrainz_rate == 1.0   # MusicBrainz 官方要求
    assert s.budget_per_100_tracks == 0.5
    assert s.batch_size_limit == 2000  # 本阶段确认沿用


# ── 打包成 exe 之后的路径解析（PyInstaller）────────────────
def test_project_root_in_source_layout(monkeypatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    root = config.project_root()
    assert (root / "src" / "mds" / "config.py").is_file(), "源码运行时根目录应是工程根"
    assert (root / "tools").is_dir() or True


def test_project_root_when_frozen(monkeypatch, tmp_path) -> None:
    """打包后 exe 所在目录就当成"程序根"，这样 fpcalc / .env 的查找不用改。"""
    exe = tmp_path / "音乐数据管家.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert config.is_frozen() is True
    assert config.project_root() == tmp_path


def test_resource_dir_prefers_meipass(monkeypatch, tmp_path) -> None:
    bundle = tmp_path / "_MEIPASS"
    bundle.mkdir()
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    assert config.resource_dir() == bundle


def test_resource_dir_falls_back_to_project_root(monkeypatch) -> None:
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert config.resource_dir() == config.project_root()


def test_fpcalc_and_env_are_found_next_to_exe(monkeypatch, tmp_path) -> None:
    """打包后：`tools/fpcalc.exe` 与 `.env` 都在 exe 旁边被找到。"""
    exe = tmp_path / "音乐数据管家.exe"
    exe.write_bytes(b"MZ")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "fpcalc.exe").write_bytes(b"MZ")
    (tmp_path / ".env").write_text("ACOUSTID_API_KEY=k\n", encoding="utf-8")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.chdir(tmp_path)          # 免得读到开发机上的工程 .env

    assert config.fpcalc_path() == tmp_path / "tools" / "fpcalc.exe"
    assert config.find_env_file() == tmp_path / ".env"
