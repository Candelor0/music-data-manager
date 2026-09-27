"""同目录共识测试 —— 核心增值点。

真实背景：真实样例库里
    曲名/艺术家/专辑/音轨号 全有；年份缺 31、流派缺 31、专辑艺术家缺 26、碟号缺 26。
这些缺的都是「专辑级字段」，同目录必然一致。
"""

from __future__ import annotations

from mds.core.consensus import (
    ALBUM_LEVEL_FIELDS,
    apply_consensus,
    compute_consensus,
    conflicts_with_consensus,
)
from mds.core.grouping import group_by_folder
from mds.core.models import FolderGroup, ItemView, Tags


def item(path: str, **tags: str) -> ItemView:
    return ItemView(path=path, name=path.rsplit("/", 1)[-1], duration_sec=200, tags=Tags(**tags))


def test_only_album_level_fields_participate() -> None:
    """曲名与音轨号是逐文件的，绝不能互证。"""
    assert "title" not in ALBUM_LEVEL_FIELDS
    assert "tracknumber" not in ALBUM_LEVEL_FIELDS
    assert set(ALBUM_LEVEL_FIELDS) == {"album", "albumartist", "date", "genre", "discnumber"}


def test_consensus_fills_from_majority_of_present_values() -> None:
    """12 首里 5 首写 2017、7 首为空 → 共识成立（分母是有值的文件数）。"""
    members = [item(f"/lib/al/{i:02}.flac", date="2017") for i in range(5)]
    members += [item(f"/lib/al/{i:02}.flac") for i in range(5, 12)]
    consensus = compute_consensus(members)
    assert consensus["date"].value == "2017"
    assert consensus["date"].votes == 5
    assert consensus["date"].total == 12
    assert consensus["date"].ratio == 1.0


def test_lone_value_is_not_a_consensus() -> None:
    """12 首里只有 1 首有年份 → 那是孤证，不是共识；不应拿它去填另外 11 首。"""
    members = [item("/lib/al/01.flac", date="2017")]
    members += [item(f"/lib/al/{i:02}.flac") for i in range(2, 13)]
    assert "date" not in compute_consensus(members)


def test_split_values_below_ratio_is_not_a_consensus() -> None:
    """5 首里 2 个 2017、其余各不同 → 众数占比 2/5 = 0.4 < 0.6 → 不成立。"""
    members = [
        item("/lib/al/01.flac", date="2017"),
        item("/lib/al/02.flac", date="2017"),
        item("/lib/al/03.flac", date="2018"),
        item("/lib/al/04.flac", date="2019"),
        item("/lib/al/05.flac", date="2020"),
    ]
    assert "date" not in compute_consensus(members)


def test_majority_at_two_thirds_is_a_consensus() -> None:
    """3 首里 1 个 2017、2 个 2018 → 众数占比 2/3 = 0.67 ≥ 0.6 → 成立。"""
    members = [
        item("/lib/al/01.flac", date="2017"),
        item("/lib/al/02.flac", date="2018"),
        item("/lib/al/03.flac", date="2018"),
    ]
    assert compute_consensus(members)["date"].value == "2018"


def test_small_folder_is_not_grouped() -> None:
    """一首歌一个文件夹 → 不做互证。"""
    assert compute_consensus([item("/lib/one/one.flac", date="2017")]) == {}


def test_consensus_never_overwrites_existing_value() -> None:
    """R-1：只补空，绝不覆盖已有值。"""
    group = FolderGroup(
        folder_path="/lib/al",
        n_files=3,
        consensus={"date": {"value": "2017", "votes": 3, "total": 3, "ratio": 1.0}},
    )
    have_value = item("/lib/al/01.flac", date="1999")
    assert apply_consensus(have_value, group) == []

    empty = item("/lib/al/02.flac")
    changes = apply_consensus(empty, group)
    assert len(changes) == 1
    assert changes[0].field == "date"
    assert changes[0].after == "2017"
    assert changes[0].kind == "consensus"


def test_conflict_is_reported_but_not_changed() -> None:
    """已有值与共识冲突 → 只提示，不产生改动（R2）。"""
    group = FolderGroup(
        folder_path="/lib/al",
        n_files=3,
        consensus={"date": {"value": "2017", "votes": 3, "total": 3, "ratio": 1.0}},
    )
    odd = item("/lib/al/09.flac", date="1999")
    assert apply_consensus(odd, group) == []
    assert conflicts_with_consensus(odd, group) == ["date"]


def test_no_group_or_single_file_group_is_safe() -> None:
    assert apply_consensus(item("/lib/x/01.flac"), None) == []
    single = FolderGroup(folder_path="/lib/x", n_files=1, consensus={})
    assert apply_consensus(item("/lib/x/01.flac"), single) == []


def test_end_to_end_with_real_folder_shape() -> None:
    """模拟真实库：一个 11 首的专辑目录，6 首有年份、11 首有专辑名，其余为空。"""
    members = []
    for i in range(1, 12):
        tags = {"artist": "Kagero", "album": "見本アルバム"}
        if i <= 6:
            tags["date"] = "2022"
        members.append(item(f"/lib/Kagero/7th/見本盤/{i:02}.flac", **tags))

    groups = group_by_folder(members)
    assert len(groups) == 1
    group = groups[0]
    from mds.core.consensus import fill_consensus

    group = fill_consensus(group, members)
    assert group.consensus["album"].value == "見本アルバム"
    assert group.consensus["date"].value == "2022"
    assert group.consensus["date"].votes == 6

    # 第 7 首没有年份 → 用共识补上
    changes = apply_consensus(members[6], group)
    assert [(c.field, c.after, c.kind) for c in changes] == [("date", "2022", "consensus")]
    # 第 1 首已有年份 → 不动
    assert apply_consensus(members[0], group) == []
