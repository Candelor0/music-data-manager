"""标签清洗与目标标签构造。纯函数。

自我约束：
    **只做明确规则的规范化（补空、大小写/写法统一、去除标题尾部杂质），不做语义改写。**
    有歧义的一律标"需你确认"，不自动决定。
"""

from __future__ import annotations

import re

from .models import Candidate, CleanedTags, TagChange, Tags
from .normalize import (
    album_cleanup_kind,
    field_kind,
    normalize_text,
    strip_album_qualifier,
    strip_disc_suffix,
    strip_title_junk,
)

# 可以补空的字段（不含 albumartist/tracknumber 等，避免臆造）
FILLABLE_FIELDS = ("title", "artist", "album", "date", "genre")

_YEAR = re.compile(r"(\d{4})")


def year_of(date_str: str) -> str:
    m = _YEAR.search(date_str or "")
    return m.group(1) if m else ""


def build_target_tags(
    current: Tags,
    chosen: Candidate | None,
    cleaned: CleanedTags,
    *,
    allow_album_change: bool = False,
) -> Tags:
    """构造"目标标签"。

    默认只补空字段，**绝不覆盖已有非空值**（R2）。
    allow_album_change=True 时才允许用候选专辑名替换已有专辑标签
    —— 该开关只能在用户明确选择"采用 AI 判断"时打开。
    """
    target = current.model_copy()

    proposed = {
        "title": cleaned.title,
        "artist": cleaned.artist,
        "album": cleaned.album or (chosen.album if chosen else ""),
        # date 统一存**四位年份**
        "date": year_of(cleaned.date) or (year_of(chosen.date) if chosen else ""),
        "genre": cleaned.genre,
    }

    for field in FILLABLE_FIELDS:
        if field == "album" and allow_album_change:
            newval = proposed[field].strip()
            if newval:
                setattr(target, field, newval)
            continue
        if not current.value_of(field):
            newval = (proposed[field] or "").strip()
            if newval:
                setattr(target, field, newval)

    return target


def propose_changes(
    current: Tags,
    chosen: Candidate | None,
    cleaned: CleanedTags,
    *,
    allow_album_change: bool = False,
    candidate_albums: list[str] | None = None,
    strip_disc: bool = True,
) -> list[TagChange]:
    """给出**允许自动建议**的字段改动清单。

    判定规则（见 normalize.field_kind）：
      - fill      ：原值为空，补上
      - normalize ：字面不同但归一化相同（写法统一，如 'Hip-Hop' → 'hip hop'）
      - cleanup   ：原值含可剥离的尾部杂质，且剥离后与目标一致
      - 其他一律不产生改动（不臆造、不覆盖）

    `candidate_albums`：本次的全部候选专辑名。用于判定"去掉专辑尾部版本说明"是否有依据
    （例如「假想专辑（实体版）」→「假想专辑」，需能在候选里找到「假想专辑」）。
    """
    target = build_target_tags(current, chosen, cleaned, allow_album_change=allow_album_change)
    changes: list[TagChange] = []

    for field in FILLABLE_FIELDS:
        before = current.value_of(field)
        after = target.value_of(field)
        if before == after:
            continue
        kind = field_kind(after, before)
        if before and kind is None:
            continue  # 有值且无法归类为安全改动 → 不动
        changes.append(TagChange(field=field, before=before, after=after, kind=kind or "fill"))

    # 标题清洗单独处理：即使"目标标题"因已有值而未被替换，
    # 也应指出"原标题含可剥离杂质"（POC 实测有 10/64 属于这种情况）。
    if current.value_of("title"):
        stripped = strip_title_junk(current.value_of("title"))
        if stripped != current.value_of("title"):
            already = any(c.field == "title" for c in changes)
            if not already:
                changes.append(
                    TagChange(
                        field="title",
                        before=current.value_of("title"),
                        after=stripped,
                        kind="cleanup",
                    )
                )

    # 专辑：只允许"去尾部版本说明"这一种清洗，且必须有依据
    album_now = current.value_of("album")
    if album_now and not allow_album_change:
        target_album = (cleaned.album if cleaned else "") or (chosen.album if chosen else "")
        if album_cleanup_kind(target_album, album_now):
            changes.append(
                TagChange(
                    field="album",
                    before=album_now,
                    after=strip_album_qualifier(album_now),
                    kind="cleanup",
                )
            )
        elif candidate_albums and normalize_text(strip_album_qualifier(album_now)) != normalize_text(
            album_now
        ):
            # 候选里有这个名字 → 有依据
            known = {normalize_text(a) for a in candidate_albums if a}
            stripped = strip_album_qualifier(album_now)
            if normalize_text(stripped) in known:
                changes.append(
                    TagChange(field="album", before=album_now, after=stripped, kind="cleanup")
                )

    # 多碟专辑：album 带 "Disc N" → 拆成 album + discnumber
    # （『Disc 2』这种后缀要不要去掉）
    if album_now and strip_disc and not any(c.field == "album" for c in changes):
        stripped_album, disc_no = strip_disc_suffix(album_now)
        if disc_no is not None:
            changes.append(
                TagChange(field="album", before=album_now, after=stripped_album, kind="cleanup")
            )
            # 关键：碟号不能丢 —— 只有 discnumber 为空时才补，绝不覆盖
            if not current.value_of("discnumber"):
                changes.append(
                    TagChange(field="discnumber", before="", after=str(disc_no), kind="fill")
                )

    return changes


def album_conflict(current: Tags, chosen: Candidate | None) -> bool:
    """现有专辑标签与候选专辑是否"存在冲突"（两边都有值且归一化后不同）。"""
    tag = normalize_text(current.value_of("album"))
    cand = normalize_text(chosen.album if chosen else "")
    return bool(tag) and bool(cand) and tag != cand
