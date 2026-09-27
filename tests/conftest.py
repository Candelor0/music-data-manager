"""测试公共夹具。

这里做两件事：
  1. 让 Qt 在无人看屏幕的机器上也能跑（offscreen 平台插件），
     否则 CI 上一跑界面测试就报 "could not find the Qt platform plugin"。
  2. 提供一个「已经分好组的音乐库」夹具，供界面与视图模型测试复用。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def _configure_qt_offscreen() -> None:
    """必须在 import PyQt5 之前设置，否则平台插件找不到。

    插件路径的补齐复用产品代码里那份（`mds.ui.qt_env`），避免测试与线上两套逻辑。
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from mds.ui.qt_env import configure_plugin_paths
    except ImportError:  # 没装 PyQt5 的环境（例如只跑 core 测试）
        return
    configure_plugin_paths()


_configure_qt_offscreen()


@pytest.fixture()
def seeded_db(tmp_path):
    """一个已经分好组、并带决策与写入计划的库。

    结构（按真实样例库的形状）：
        /Music/見本アルバム/   3 首、同专辑、其中 1 首缺年份（可被共识补上）
        /Music/乌云典当记/          1 首（单文件目录，不做互证）
    """
    from mds.pipeline.group import build_groups
    from mds.storage.db import Database

    path = tmp_path / "ui.db"
    db = Database(path)
    db.migrate()
    run_id = db.create_run("/Music", {"source": "test"})

    album_dir = "/Music/見本アルバム"
    paths = [
        f"{album_dir}/01. 感情道路見本盤.flac",
        f"{album_dir}/02. 火種.flac",
        f"{album_dir}/03. 境界線.flac",
        "/Music/乌云典当记/乌云典当记.flac",
        f"{album_dir}/04. 追加一曲.flac",
    ]
    db.insert_items(run_id, paths)
    rows = {r["path"]: dict(r) for r in db.iter_items(run_id)}

    def tag(path: str, **values) -> None:
        base = {
            "title": Path(path).stem,
            "artist": "Kagero",
            "album": "見本アルバム",
            "tracknumber": "",
        }
        base.update(values)
        db.update_item(
            rows[path]["id"],
            tags_json=base,
            duration_sec=240,
            fp_status="ok",
            recording_mbid="rec-1",
        )

    tag(paths[0], tracknumber="1", date="2022", genre="JPop")
    tag(paths[1], tracknumber="2", date="2022", genre="JPop")
    # 第 3 首：缺年份（可由共识补上）、流派与其他两首**冲突**（只提示，不改）
    tag(paths[2], tracknumber="3", genre="Rock")
    tag(paths[3], title="乌云典当记", artist="", album="")
    tag(paths[4], tracknumber="4", genre="JPop")

    # 生成分组（会顺带重算决策，所以下面再覆盖成我们想要的形状）
    build_groups(db, run_id)

    from mds.core.models import Candidate, Decision, TagChange, WritePlan

    candidate = Candidate(
        release_mbid="rel-1",
        album="見本アルバム",
        date="2022-06-29",
        country="JP",
        status="Official",
        format="CD",
        track_count=11,
        track_number="3",
        track_length_sec=241,
    )
    # MusicBrainz 候选缓存（以 recording MBID 为键）—— 界面读的就是它
    db.mb_put("rec-1", {"candidates": [candidate.model_dump()], "error": None})

    def decide_for(path: str, decision: Decision, plan: WritePlan | None = None) -> None:
        item_id = rows[path]["id"]
        db.update_item(item_id, decision_json=decision.model_dump(mode="json"))
        if plan is not None:
            db.set_plan(item_id, plan.model_dump(mode="json"), write_status="planned")

    decide_for(
        paths[2],
        Decision(
            action="fill_missing",
            evidence="album_tag_match",
            changes=[TagChange(field="date", before="", after="2022", kind="consensus")],
            chosen=candidate,
            reason="同目录其他文件一致，据此补全空字段",
            conflicts=["genre"],
            group_path=album_dir,
        ),
        WritePlan(
            allowed=True,
            changes=[TagChange(field="date", before="", after="2022", kind="consensus")],
            source="fill_missing",
            target={},  # 界面不读 target，这里留空避免构造麻烦
        ),
    )
    decide_for(paths[0], Decision(action="no_op", evidence="album_tag_match", chosen=candidate))
    decide_for(
        paths[1],
        Decision(
            action="ask_user",
            evidence="insufficient",
            chosen=candidate,
            show_candidates=[candidate],
            reason="多个候选时长一致，无法从音频分辨，请确认出自哪张专辑",
        ),
    )
    decide_for(paths[3], Decision(action="no_evidence", evidence="none", reason="没有候选可判断"))
    # 🟠 组：AI 想换专辑名，但计划里只剩安全填空（换名那一项已被丢掉）
    decide_for(
        paths[4],
        Decision(
            action="keep_existing",
            evidence="album_tag_match",
            changes=[TagChange(field="date", before="", after="2022", kind="consensus")],
            chosen=candidate,
            alternative=candidate,
            reason="现有专辑标签与 AI 判断不一致，默认保留现有值",
            conflicts=["genre"],
            group_path=album_dir,
        ),
        WritePlan(
            allowed=True,
            changes=[TagChange(field="date", before="", after="2022", kind="consensus")],
            source="fill_missing",
            target={},
        ),
    )

    # 决策是上面才写进去的；旧的 rows 快照会读到过期内容，必须重读一遍
    rows = {r["path"]: dict(r) for r in db.iter_items(run_id)}

    yield {"db": db, "path": path, "run_id": run_id, "paths": paths, "rows": rows}
    db.close()


@pytest.fixture(scope="session")
def qapp_cls():
    """让 pytest-qt 用**和线上同一个** QApplication 类（Picard 友好的那个）。

    为什么必须这样：
        Picard 要求 `QCoreApplication.instance()` 上带 `tagger_stats_changed` 信号，
        并且由 `event()` 派发后台线程回调。若测试里用普通 QApplication，
        写标签会**静默失败**（界面显示"写完了"、文件一个字节没变）——
        而测试却"全绿"。用同一个类，这个坑就能在测试里暴露出来。
    """
    try:
        from PyQt5.QtWidgets import QApplication
    except ImportError:  # 没装 PyQt5 的环境
        return None
    from mds.adapters.picard_headless import make_app_class

    return make_app_class(QApplication)
