"""目录分组测试。用真实库的目录形状。"""

from __future__ import annotations

from mds.core.grouping import group_by_folder, group_map
from mds.core.models import ItemView, Tags


def item(path: str, **tags: str) -> ItemView:
    return ItemView(path=path, name=path.rsplit("/", 1)[-1], duration_sec=200, tags=Tags(**tags))


def test_same_folder_goes_to_same_group() -> None:
    items = [
        item("/lib/Kagero/見本盤/01.flac", artist="Kagero", album="見本盤"),
        item("/lib/Kagero/見本盤/02.flac", artist="Kagero", album="見本盤"),
        item("/lib/Kagero/見本盤/03.flac", artist="Kagero", album="見本盤"),
    ]
    groups = group_by_folder(items)
    assert len(groups) == 1
    assert groups[0].n_files == 3
    assert groups[0].folder_path == "/lib/Kagero/見本盤"


def test_different_folders_are_different_groups() -> None:
    items = [
        item("/lib/A/album1/01.flac", artist="A"),
        item("/lib/A/album2/01.flac", artist="A"),
    ]
    groups = group_by_folder(items)
    assert len(groups) == 2
    assert {g.n_files for g in groups} == {1}


def test_depth_does_not_matter() -> None:
    """真实库深度 2/3/4 混着，分组只看上一层。"""
    items = [
        item("/lib/Sample Land!/Star Beats/单曲&专辑/ERA/001.flac"),
        item("/lib/Sample Land!/Star Beats/单曲&专辑/ERA/002.flac"),
        item("/lib/陆离/陆离 - 假想专辑2000/01.flac"),
    ]
    groups = group_by_folder(items)
    assert len(groups) == 2
    sizes = sorted(g.n_files for g in groups)
    assert sizes == [1, 2]


def test_single_file_folder_is_still_a_group() -> None:
    groups = group_by_folder([item("/lib/乌云典当记/乌云典当记.flac")])
    assert len(groups) == 1
    assert groups[0].n_files == 1
    assert groups[0].folder_hint == "乌云典当记"


def test_artist_hint_is_majority_and_used_for_folder_hint() -> None:
    items = [
        item("/lib/陆离/陆离 - 假想专辑2000/01.flac", artist="陆离"),
        item("/lib/陆离/陆离 - 假想专辑2000/02.flac", artist="陆离"),
        item("/lib/陆离/陆离 - 假想专辑2000/03.flac", artist="LU LI"),  # 少数、写法不同
    ]
    groups = group_by_folder(items)
    assert groups[0].artist_hint == "陆离"
    assert groups[0].folder_hint == "假想专辑2000"  # 前缀被剥掉


def test_group_map_for_reverse_lookup() -> None:
    items = [item("/lib/A/al/01.flac"), item("/lib/A/al/02.flac")]
    groups = group_by_folder(items)
    mapping = group_map(groups)
    assert mapping["/lib/A/al"].n_files == 2


def test_empty_input() -> None:
    assert group_by_folder([]) == []
