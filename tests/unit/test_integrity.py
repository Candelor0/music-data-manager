"""完整性校验测试（S17）。

用**手工构造的最小 FLAC** 测试，不依赖真实音乐文件。
"""

from __future__ import annotations

from pathlib import Path

from mds.adapters import integrity

STREAMINFO = bytes(34)
PICTURE = b"\xff\xd8\xff\xe0" + b"FAKEJPEG" * 8
AUDIO = b"AUDIODATA" * 500


def make_flac(*, padding: int = 1000, picture: bytes = PICTURE, audio: bytes = AUDIO,
              comment: bytes = b"TITLE=hello", crc_ok: bool = True) -> bytes:
    blocks: list[tuple[int, bytes]] = [
        (0, STREAMINFO),
        (4, len(comment).to_bytes(4, "little") + comment),
    ]
    if padding:
        blocks.append((1, b"\x00" * padding))
    if picture:
        blocks.append((6, picture))

    out = bytearray(b"fLaC")
    for index, (code, body) in enumerate(blocks):
        last = 0x80 if index == len(blocks) - 1 else 0
        out += bytes([last | code]) + len(body).to_bytes(3, "big") + body
    out += audio
    return bytes(out)


def write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_digest_detects_audio_and_picture(tmp_path: Path) -> None:
    path = write(tmp_path, "a.flac", make_flac())
    d = integrity.digest(path)
    assert d.supported is True
    assert d.audio_len == len(AUDIO)
    assert d.padding == 1000
    assert d.n_pictures == 1
    assert d.size == path.stat().st_size
    assert d.file_sha


def test_compare_passes_when_only_padding_changes(tmp_path: Path) -> None:
    """这正是 Picard 的行为：音频/图片不变，PADDING 被重算 → 应该判定为完好。"""
    before = integrity.digest(write(tmp_path, "before.flac", make_flac(padding=1_000_000)))
    after = integrity.digest(write(tmp_path, "after.flac", make_flac(padding=10)))
    ok, note = integrity.compare(before, after)
    assert ok is True
    assert "音频与图片完好" in note
    assert before.size > after.size


def test_compare_fails_when_audio_changes(tmp_path: Path) -> None:
    before = integrity.digest(write(tmp_path, "b.flac", make_flac()))
    after = integrity.digest(write(tmp_path, "c.flac", make_flac(audio=b"AUDIODATA" * 499 + b"X")))
    ok, note = integrity.compare(before, after)
    assert ok is False
    assert "音频" in note


def test_compare_fails_when_audio_length_changes(tmp_path: Path) -> None:
    before = integrity.digest(write(tmp_path, "d.flac", make_flac()))
    after = integrity.digest(write(tmp_path, "e.flac", make_flac(audio=AUDIO[:-100])))
    ok, _ = integrity.compare(before, after)
    assert ok is False


def test_compare_fails_when_picture_changes(tmp_path: Path) -> None:
    before = integrity.digest(write(tmp_path, "f.flac", make_flac()))
    after = integrity.digest(write(tmp_path, "g.flac", make_flac(picture=b"\xff\xd8DIFFERENT")))
    ok, note = integrity.compare(before, after)
    assert ok is False
    assert "图片" in note


def test_compare_fails_when_picture_removed(tmp_path: Path) -> None:
    """丢封面是典型的\"数据丢失\"，必须报失败。"""
    before = integrity.digest(write(tmp_path, "h.flac", make_flac()))
    after = integrity.digest(write(tmp_path, "i.flac", make_flac(picture=b"")))
    ok, note = integrity.compare(before, after)
    assert ok is False
    assert "图片" in note


def test_non_flac_is_unsupported_but_still_has_file_sha(tmp_path: Path) -> None:
    path = write(tmp_path, "song.mp3", b"ID3\x04\x00\x00" + b"\x00" * 500)
    d = integrity.digest(path)
    assert d.supported is False
    assert d.file_sha  # 仍可做"整文件"手段
    ok, note = integrity.compare(d, d)
    assert ok is True
    assert "未做分区校验" in note


def test_unsupported_format_never_blocks_write(tmp_path: Path) -> None:
    """非 FLAC 不能因为\"没做分区校验\"就被判失败（否则整个产品没法用）。"""
    a = integrity.digest(write(tmp_path, "x.mp3", b"ID3" + b"\x00" * 100))
    b = integrity.digest(write(tmp_path, "y.mp3", b"ID3" + b"\x00" * 200))
    ok, _ = integrity.compare(a, b)
    assert ok is True


def test_digest_is_deterministic(tmp_path: Path) -> None:
    data = make_flac()
    first = integrity.digest(write(tmp_path, "m.flac", data))
    second = integrity.digest(write(tmp_path, "n.flac", data))
    assert first.audio_sha == second.audio_sha
    assert first.pictures_sha == second.pictures_sha
    assert first.file_sha == second.file_sha
