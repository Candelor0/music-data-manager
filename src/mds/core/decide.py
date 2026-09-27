"""决策编排 —— 核心规则层。纯函数。

四条不可违反的规则：
  R1  **同目录共识补空字段**：同一张专辑必然同字段，
      所以"同目录 N 首一致"是把空字段补上的最强**本地**证据，且不花钱。
  R2  **文件已有专辑标签 → 默认保留**。发现冲突时不覆盖，只把 AI 的判断列为可选项。
  R3  **证据不足 → 呈现候选交用户选择**，不自动写入。
  R4  **不得用模型自报的 confidence 做自动决策门槛**。

本模块只输出 Decision，**不做任何写入**。
"""

from __future__ import annotations

from .cleaning import album_conflict, propose_changes
from .consensus import apply_consensus, conflicts_with_consensus
from .models import (
    Candidate,
    CleanedTags,
    Decision,
    DecisionAction,
    FolderGroup,
    ItemView,
    Suggestion,
    TagChange,
)
from .scoring import assess_evidence, rank_candidates

# 证据不足时呈现给用户的候选数量（R9：POC 实测有正确答案排在第 10 位）
LOW_EVIDENCE_CANDIDATES = 10

VERDICT_LABELS: dict[str, str] = {
    "no_op": "无需改动",
    "fill_missing": "建议补全缺失字段",
    "cleanup": "建议清洗/规范化",
    "keep_existing": "保留现有专辑（AI 有不同判断）",
    "ask_user": "需你选择出自哪张专辑",
    "no_evidence": "无法处理（没有候选）",
}


def _drop_album_replacements(changes: list[TagChange]) -> list[TagChange]:
    """丢掉"换一个发行版"的专辑改动，但**保留**安全清洗与同目录共识补全。

    区别很关键：
      - 把「假想专辑（实体版）」改成「假想之路」→ 换了发行版 → 必须丢掉（R2）
      - 把「假想专辑（实体版）」改成「假想专辑」→ 只是去括号 → 保留
      - 空专辑 → 同目录共识给出的专辑名         → 保留（那是 R1）
    """
    return [
        c
        for c in changes
        if not (c.field == "album" and c.kind not in ("cleanup", "consensus"))
    ]


def _merge_changes(consensus_changes: list[TagChange], other: list[TagChange]) -> list[TagChange]:
    """共识优先：同一字段已有共识值时，不再使用其它来源的建议。"""
    covered = {c.field for c in consensus_changes if c.after}
    return consensus_changes + [c for c in other if c.field not in covered]


def pick_chosen(
    candidates: list[Candidate], ranked: list[Candidate], model_out: Suggestion | None
) -> Candidate | None:
    """选出"AI 认定的那一个"。

    ⚠️ **下标契约**：`model_out.chosen_index` 指的是**发给模型的那份候选列表**里的下标，
    不是重排后的下标。因此必须用 `candidates`（原始顺序）取值。
    """
    if not ranked:
        return None
    if model_out is not None and 0 <= model_out.chosen_index < len(candidates):
        return candidates[model_out.chosen_index]
    return ranked[0]


def decide(
    item: ItemView,
    candidates: list[Candidate],
    model_out: Suggestion | None = None,
    *,
    low_evidence_candidates: int = LOW_EVIDENCE_CANDIDATES,
    strip_disc: bool = True,
    group: FolderGroup | None = None,
) -> Decision:
    """产出一个文件的处置决策。

    决策顺序（先本地、再安全、后有用）：
      0. 同目录共识补空字段（R1）—— 免费且最强
      1. 没有候选且无共识      → no_evidence
      2. 专辑标签冲突          → keep_existing（R2）
      3. 缺专辑标签且证据不足  → ask_user（R3）
      4. 有安全改动            → cleanup / fill_missing
      5. 否则                  → no_op
    """
    consensus_changes = apply_consensus(item, group)
    conflicts = conflicts_with_consensus(item, group)
    group_path = group.folder_path if group else ""
    folder_hint = group.folder_hint if group else ""

    ranked = rank_candidates(candidates, item, folder_hint)

    # ── 1. 没有候选：共识仍能把空字段补上 ──────────────────
    if not ranked:
        if consensus_changes:
            return Decision(
                action="fill_missing",
                evidence="insufficient",
                changes=consensus_changes,
                reason="同目录其他文件一致，据此补全空字段",
                conflicts=conflicts,
                group_path=group_path,
            )
        return Decision(action="no_evidence", evidence="none", reason="没有候选可判断",
                        conflicts=conflicts, group_path=group_path)

    chosen = pick_chosen(candidates, ranked, model_out)
    evidence = assess_evidence(chosen, ranked, item)
    # 同目录共识里有专辑名 → 等价于「已知正确专辑名」，不必再问用户
    if any(c.field == "album" and c.after for c in consensus_changes):
        evidence = "album_tag_match"

    cleaned = model_out.cleaned if model_out is not None else CleanedTags()
    changes = _merge_changes(
        consensus_changes,
        propose_changes(
            item.tags,
            chosen,
            cleaned,
            allow_album_change=False,
            candidate_albums=[c.album for c in ranked],
            strip_disc=strip_disc,
        ),
    )
    reason = (model_out.reason if model_out is not None else "") or ""

    # ── 2. R2：已有专辑标签与 AI 判断冲突 → 默认保留 ───────
    if album_conflict(item.tags, chosen):
        return Decision(
            action="keep_existing",
            evidence=evidence,
            changes=_drop_album_replacements(changes),
            chosen=chosen,
            alternative=chosen,
            reason=reason or "现有专辑标签与 AI 判断不一致，默认保留现有值",
            conflicts=conflicts,
            group_path=group_path,
        )

    # ── 3. R3：缺专辑标签且证据不足 → 交给用户选 ───────────
    if evidence == "insufficient" and not item.tags.value_of("album"):
        return Decision(
            action="ask_user",
            evidence=evidence,
            changes=_drop_album_replacements(changes),  # 曲名/艺术家等与发行版无关的仍可补
            chosen=chosen,
            show_candidates=ranked[:low_evidence_candidates],
            reason=reason or "多个候选时长一致，无法从音频分辨，请确认出自哪张专辑",
            conflicts=conflicts,
            group_path=group_path,
        )

    # ── 4. 有安全改动 ─────────────────────────────────────
    if changes:
        action: DecisionAction = (
            "cleanup" if any(c.kind in ("cleanup", "normalize") for c in changes) else "fill_missing"
        )
        return Decision(
            action=action,
            evidence=evidence,
            changes=changes,
            chosen=chosen,
            reason=reason,
            conflicts=conflicts,
            group_path=group_path,
        )

    return Decision(action="no_op", evidence=evidence, chosen=chosen, reason=reason,
                    conflicts=conflicts, group_path=group_path)


def resolve_with_choice(decision: Decision, chosen: Candidate) -> Decision:
    """用户已经选定了发行版 → 这条不再是"待你决定"，而是"按所选候选补全"。

    为什么必须这样：`ask_user` 的条目在待办里属于「🟡 需要你选出自哪张专辑」。
    如果选完之后 `action` 还是 `ask_user`，待办里会**永远显示需要你选**，
    永远不消失 —— 用户会以为选择没生效（实测踩到）。
    """
    if decision.action != "ask_user":
        return decision
    label = chosen.album or chosen.release_mbid or "所选候选"
    return decision.model_copy(
        update={
            "action": "fill_missing",
            "chosen": chosen,
            "show_candidates": [],
            "reason": f"你已选定发行版：{label}",
        }
    )


def verdict_of(decision: Decision) -> str:
    """给报告与数据库用的短标签。"""
    return VERDICT_LABELS.get(decision.action, decision.action)
