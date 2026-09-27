"""完整性校验：证明"写标签只动标签"。

背景（实测，见开发记录§2.4）：
    FLAC 文件里通常有一块很大的 **PADDING（预留空白）**，供未来就地改标签用。
    Picard/mutagen 重写标签块时会把 padding 重算成很小的一块，于是文件会**变小**。
    实测一首 56.9 MB 的文件变成 45.6 MB（少 11.4 MB），但：

        音频数据 sha256  ✅ 完全一致
        内嵌图片 sha256  ✅ 完全一致

    也就是说 —— **少的只是预留空白，不是数据**。

本模块把这件事变成**每次写入都强制的检查**：
在替换原文件**之前**，先比对"副本"与"原文件"的音频与图片指纹；
不一致就中止（原文件根本没被碰过）。这样"只动标签"从承诺变成证据。

实现要点：用 seek 只读元数据块区，音频部分流式哈希 —— 不把上百 MB 读进内存。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ..logging_setup import get_logger

log = get_logger("integrity")

CHUNK = 1 << 20
FLAC_MAGIC = b"fLaC"
BLOCK_PADDING = 1
BLOCK_VORBIS_COMMENT = 4
BLOCK_PICTURE = 6
#: 元数据块区最大读取量（正常几 MB 以内；超过就说明文件异常，放弃校验）
MAX_HEADER_BYTES = 64 << 20


@dataclass
class Digest:
    audio_sha: str = ""
    audio_len: int = 0
    pictures_sha: str = ""
    n_pictures: int = 0
    tags_sha: str = ""
    padding: int = 0
    size: int = 0
    file_sha: str = ""
    supported: bool = False


def _stream_sha(fh, start: int, length: int) -> str:
    digest = hashlib.sha256()
    fh.seek(start)
    remaining = length
    while remaining > 0:
        chunk = fh.read(min(CHUNK, remaining))
        if not chunk:
            break
        digest.update(chunk)
        remaining -= len(chunk)
    return digest.hexdigest()


def digest(path: str | Path) -> Digest:
    """计算完整性指纹。非 FLAC 时 supported=False（只算整文件 sha）。"""
    p = Path(path)
    size = p.stat().st_size
    with open(p, "rb") as fh:
        head = fh.read(4)
        if head != FLAC_MAGIC:
            return Digest(size=size, file_sha=_stream_sha(fh, 0, size), supported=False)

        # 只读到元数据块结束
        fh.seek(4)
        blocks: list[tuple[int, bytes]] = []
        position = 4
        while position + 4 <= min(size, MAX_HEADER_BYTES):
            fh.seek(position)
            header = fh.read(4)
            if len(header) < 4:
                break
            is_last = bool(header[0] & 0x80)
            code = header[0] & 0x7F
            length = int.from_bytes(header[1:4], "big")
            body = fh.read(length)
            blocks.append((code, body))
            position += 4 + length
            if is_last:
                break
        audio_offset = position

    pictures = b"".join(body for code, body in blocks if code == BLOCK_PICTURE)
    tags = b"".join(body for code, body in blocks if code == BLOCK_VORBIS_COMMENT)
    with open(p, "rb") as fh:
        file_sha = _stream_sha(fh, 0, size)
        audio_sha = _stream_sha(fh, audio_offset, size - audio_offset)
    return Digest(
        audio_sha=audio_sha,
        audio_len=size - audio_offset,
        pictures_sha=hashlib.sha256(pictures).hexdigest(),
        n_pictures=sum(1 for code, _ in blocks if code == BLOCK_PICTURE),
        tags_sha=hashlib.sha256(tags).hexdigest(),
        padding=sum(len(body) for code, body in blocks if code == BLOCK_PADDING),
        size=size,
        file_sha=file_sha,
        supported=True,
    )


def compare(before: Digest, after: Digest) -> tuple[bool, str]:
    """比较两次 digest。返回 (是否完好, 说明)。"""
    if not before.supported or not after.supported:
        return True, "（该格式未做分区校验，仅靠副本+原子替换保护）"
    if before.audio_sha != after.audio_sha:
        return False, "❌ 音频数据被改变"
    if before.audio_len != after.audio_len:
        return False, "❌ 音频长度被改变"
    if before.pictures_sha != after.pictures_sha:
        return False, "❌ 内嵌图片被改变"
    if before.n_pictures != after.n_pictures:
        return False, "❌ 内嵌图片数量被改变"
    delta = after.size - before.size
    note = "音频与图片完好"
    if delta:
        note += f"（文件大小 {delta:+,} 字节：FLAC 预留空白被重算，非数据）"
    return True, note
