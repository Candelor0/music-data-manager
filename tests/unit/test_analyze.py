"""编排层测试：续跑语义与预算熔断。全部 mock，不联网、不花钱。"""

from __future__ import annotations

import pytest

from mds.adapters import acoustid, fpcalc, llm, musicbrainz
from mds.config import Settings
from mds.core.models import Candidate
from mds.pipeline.analyze import AnalyzeOptions, analyze
from mds.storage.db import Database


@pytest.fixture()
def db(tmp_path) -> Database:
    database = Database(tmp_path / "t.db")
    database.migrate()
    yield database
    database.close()


def _settings(**over) -> Settings:
    base = dict(
        acoustid_api_key="k",
        deepseek_api_key="",  # 默认不调云端
        musicbrainz_user_agent="mds/0.1 ( mailto:a@b.c )",
        acoustid_rate=10000.0,
        musicbrainz_rate=10000.0,
        budget_per_100_tracks=0.5,
    )
    base.update(over)
    return Settings(**base)


@pytest.fixture()
def patched(monkeypatch):
    counters = {"fp": 0, "ac": 0, "mb": 0, "llm": 0}

    def fake_fp(path, **kwargs):
        counters["fp"] += 1
        return fpcalc.Fingerprint(status="ok", fingerprint="FP", duration_sec=200)

    def fake_ac(api_key, fp, duration, limiter, **kwargs):
        counters["ac"] += 1
        return acoustid.AcoustIDResult(status="ok", score=1.0, recording_mbid="rec-1",
                                       title="T", artists=["A"])

    def fake_mb(recording_mbid, ua, limiter, duration, **kwargs):
        counters["mb"] += 1
        return musicbrainz.MBCandidates(
            status="ok", track_title="T",
            candidates=[Candidate(release_mbid="r1", album="AL", date="2020-01-01",
                                  track_length_sec=200)],
        )

    monkeypatch.setattr(fpcalc, "compute", fake_fp)
    monkeypatch.setattr(acoustid, "lookup", fake_ac)
    monkeypatch.setattr(musicbrainz, "fetch_candidates", fake_mb)
    return counters


def test_analyze_processes_pending_items(db: Database, patched) -> None:
    run_id = db.create_run("/music", {})
    db.insert_items(run_id, ["/m/a.flac", "/m/b.flac"])
    # insert_items 只写 path/name，size/mtime 由 scan 填；这里直接补齐以免指纹缓存键为空
    for row in db.iter_items(run_id):
        db.update_item(row["id"], size=1, mtime=1.0, tags_json={"title": "T", "artist": "A"})

    stats = analyze(db, _settings(), run_id, options=AnalyzeOptions(use_llm=False))
    assert stats.total == 2
    assert stats.processed == 2
    assert patched["fp"] == 2 and patched["ac"] == 2
    # 两个文件解析到同一个 recording ⇒ MusicBrainz 只该查一次（缓存复用是正确的）
    assert patched["mb"] == 1
    assert stats.mb_cache_hits == 1
    assert db.item_counts(run_id)["llm_skipped"] == 2  # 未配置 Key ⇒ 跳过云端
    # 未配置 Key 时也必须给出决策（基于评分与现有标签）
    assert all(dict(r)["decision_json"] for r in db.iter_items(run_id))


def test_analyze_skips_completed_items(db: Database, patched) -> None:
    """S6 的核心：已出决策的条目不得重复处理（不重复花钱）。"""
    run_id = db.create_run("/music", {})
    db.insert_items(run_id, ["/m/done.flac", "/m/todo.flac"])
    rows = [dict(r) for r in db.iter_items(run_id)]
    for row in rows:
        db.update_item(row["id"], size=1, mtime=1.0, tags_json={"title": "T"})
    db.update_item(rows[0]["id"], fp_status="ok", ac_status="ok", cand_status="ok",
                   llm_status="ok", decision_json={"action": "no_op"}, verdict="无需改动")

    stats = analyze(db, _settings(), run_id, options=AnalyzeOptions(use_llm=False))
    assert stats.skipped_done == 1
    assert stats.processed == 1
    # 只对未完成的那条真正调用了外部服务
    assert patched["ac"] == 1 and patched["mb"] == 1


def test_resume_after_crash_state(db: Database, patched) -> None:
    """模拟"卡在某一步被强杀"：已完成的前置步骤不应重跑。"""
    run_id = db.create_run("/music", {})
    db.insert_items(run_id, ["/m/a.flac"])
    row = dict(next(iter(db.iter_items(run_id))))
    db.update_item(row["id"], size=1, mtime=1.0, tags_json={"title": "T"},
                   fp_status="ok", ac_status="ok", recording_mbid="rec-1",
                   cand_status="ok", n_candidates=1)
    db.fingerprint_put("/m/a.flac", 1, 1.0, "FP", 200)

    stats = analyze(db, _settings(), run_id, options=AnalyzeOptions(use_llm=False))
    assert patched["fp"] == 0, "指纹已完成，不该重跑"
    assert patched["ac"] == 0, "AcoustID 已完成，不该重跑"
    # 指纹阶段因状态已 ok 而被整个跳过（不是"缓存未命中"）
    assert stats.fp_ok == 0 and stats.fp_cache_hits == 0
    assert stats.processed == 1


def test_budget_exhausted_skips_cloud_calls(db: Database, patched, monkeypatch) -> None:
    run_id = db.create_run("/music", {})
    db.insert_items(run_id, [f"/m/{i}.flac" for i in range(3)])
    for row in db.iter_items(run_id):
        db.update_item(row["id"], size=1, mtime=1.0, tags_json={"title": "T"})

    def fake_chat(self, item, candidates, **kwargs):
        patched["llm"] += 1
        self.tracker.add(1.0)  # 一次就超预算
        return llm.LLMResult(status="ok", suggestion=None, error="")

    monkeypatch.setattr(llm.DeepSeekClient, "chat", fake_chat)

    stats = analyze(db, _settings(deepseek_api_key="dk"), run_id,
                    options=AnalyzeOptions(use_llm=True, budget_per_100_tracks=0.01))
    assert stats.llm_skipped >= 1, "超预算后必须停止云端调用"
    assert db.item_counts(run_id)["llm_skipped"] >= 1


def test_analyze_marks_error_but_continues(db: Database, patched, monkeypatch) -> None:
    def failing_ac(api_key, fp, duration, limiter, **kwargs):
        return acoustid.AcoustIDResult(status="error", error="boom")

    monkeypatch.setattr(acoustid, "lookup", failing_ac)
    run_id = db.create_run("/music", {})
    db.insert_items(run_id, ["/m/a.flac", "/m/b.flac"])
    for row in db.iter_items(run_id):
        db.update_item(row["id"], size=1, mtime=1.0, tags_json={"title": "T"})

    stats = analyze(db, _settings(), run_id, options=AnalyzeOptions(use_llm=False))
    assert stats.errors == 2
    assert db.item_counts(run_id)["total"] == 2  # 一条失败不影响其它条目


def test_no_candidates_produces_no_evidence_decision(db: Database, patched, monkeypatch) -> None:
    monkeypatch.setattr(
        musicbrainz, "fetch_candidates",
        lambda *a, **k: musicbrainz.MBCandidates(status="no_result"),
    )
    run_id = db.create_run("/music", {})
    db.insert_items(run_id, ["/m/a.flac"])
    for row in db.iter_items(run_id):
        db.update_item(row["id"], size=1, mtime=1.0, tags_json={"title": "T"})

    stats = analyze(db, _settings(), run_id, options=AnalyzeOptions(use_llm=False))
    assert stats.verdicts.get("无法处理（没有候选）") == 1


def test_run_is_finished_with_stats(db: Database, patched) -> None:
    run_id = db.create_run("/music", {})
    db.insert_items(run_id, ["/m/a.flac"])
    for row in db.iter_items(run_id):
        db.update_item(row["id"], size=1, mtime=1.0, tags_json={"title": "T"})
    analyze(db, _settings(), run_id, options=AnalyzeOptions(use_llm=False))
    run = db.get_run(run_id)
    assert run is not None and run["status"] == "done"
    assert "musicbrainz_limiter" in (run["stats_json"] or "")
