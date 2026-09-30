# -*- coding: utf-8 -*-
"""判分复盘：把 AI 追问到的"到底卡在哪一条"聚合成可采纳的建议。

需求（2026-09-29 用户）："如果说不符合，可以追问一下为什么不符合，然后不符合的原因
可以用于 AI 的自进化。" 已定口径：只问 AI 不问 HR、只追边界带、原因**只出建议**，
人工点采纳才写配置 —— 与自进化引擎原有契约一致（只记录与评估，不替用户改写配置）。

这里刻意**不做语义聚类**：同一个"要 3 年 SQL 经验"被模型写成两种说法时会分成两组。
要聚类就得再打一次 AI，而复盘是给人看的辅助信息，不值得为它再加一层不确定性和开销；
归并按"去空白后取前 40 字"做键，宁可分两组也不要合并错。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

REVIEW_DAYS_DEFAULT = 7
SUGGESTION_MIN_HITS = 2      # 出现两次以上才值得动配置，一次可能是误判
SUGGESTION_MAX = 8
BLOCKER_KEY_LEN = 40
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# 建议类型 → 允许写入的配置字段。白名单之外的 target 一律拒写：
# 采纳按钮能碰的字段必须是这里列出来的那几个，不能由前端传什么写什么。
_ALLOWED_TARGETS = {
    "add_scoring_rule": "ai.custom_scoring_prompt",
    "add_resume_evidence": "resume.skills",
    "add_veto_keyword": "ai.custom_filter_keywords",
}


def _field(row: Any, key: str, default: Any = None) -> Any:
    """记录可能是 GreetRecord 对象，也可能是落库后的 dict。"""
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _norm(text: Any) -> str:
    return " ".join(str(text or "").split())


def _row_time(row: Any) -> Optional[datetime]:
    raw = _field(row, "timestamp")
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw)[:19], TIME_FORMAT)
    except ValueError:
        return None


def _probe_of(row: Any) -> Optional[Dict[str, Any]]:
    probe = _field(row, "ai_probe")
    if isinstance(probe, dict) and _norm(probe.get("blocking_requirement")):
        return probe
    return None


def build_review(records: List[Any], account_index: Optional[int] = None,
                 days: int = REVIEW_DAYS_DEFAULT,
                 now: Optional[datetime] = None) -> Dict[str, Any]:
    """把带追问的不匹配记录聚合成复盘数据（含建议，全部 auto_applied:false）。"""
    now = now or datetime.now()
    window = now - timedelta(days=max(0, int(days)))
    groups: Dict[str, Dict[str, Any]] = {}
    probed = 0
    today_probed = 0
    fixable_rows = 0
    today = now.date()

    for row in records or []:
        probe = _probe_of(row)
        if probe is None:
            continue
        # 不按 ai_is_match 过滤：判定走的是 is_match AND score>=阈值，而记录里存的是
        # 模型原话。2026-09-30 实测就有 is_match=true + score=65 被拦下的行，
        # 按 ai_is_match 筛正好把这种"分数不够"的漏掉。有追问 = 它确实走了不匹配分支。
        if _field(row, "ai_error"):
            continue
        if account_index is not None and \
                int(_field(row, "account_index", 0) or 0) != int(account_index):
            continue
        t = _row_time(row)
        if t is not None and t < window:
            continue

        probed += 1
        if t is not None and t.date() == today:
            today_probed += 1
        fixable = bool(probe.get("fixable_by_resume"))
        fixable_rows += 1 if fixable else 0
        blocker = _norm(probe.get("blocking_requirement"))
        key = blocker[:BLOCKER_KEY_LEN]
        g = groups.setdefault(key, {
            "requirement": blocker, "count": 0, "fixable": 0, "not_fixable": 0,
            "score_max": 0, "evidence": "", "evidence_counts": {},
        })
        g["count"] += 1
        g["fixable"] += 1 if fixable else 0
        g["not_fixable"] += 0 if fixable else 1
        try:
            g["score_max"] = max(g["score_max"], int(_field(row, "ai_score", 0) or 0))
        except (TypeError, ValueError):
            pass
        if fixable:
            ev = _norm(probe.get("evidence_missing"))
            if ev:
                g["evidence_counts"][ev] = g["evidence_counts"].get(ev, 0) + 1

    ordered = sorted(groups.values(), key=lambda g: (-g["count"], -g["score_max"]))
    for g in ordered:
        if g["evidence_counts"]:
            g["evidence"] = min(g["evidence_counts"],
                                key=lambda k: (-g["evidence_counts"][k], len(k)))
        g.pop("evidence_counts", None)

    return {
        "days": days,
        "scope": account_index,
        "probed": probed,
        "today_probed": today_probed,
        "fixable_by_resume": fixable_rows,
        "not_fixable": probed - fixable_rows,
        "top_blockers": ordered[:SUGGESTION_MAX],
        "suggestions": _suggestions(ordered),
    }


def _suggestions(groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把反复出现的原因折成几条能照着做的建议。"""
    out: List[Dict[str, Any]] = []
    for g in groups:
        if g["count"] < SUGGESTION_MIN_HITS:
            continue
        blocker = g["requirement"]
        if g["fixable"]:
            evidence = g["evidence"] or blocker
            out.append({
                "kind": "add_resume_evidence",
                "target": _ALLOWED_TARGETS["add_resume_evidence"],
                "value": evidence[:80],
                "because": f"有 {g['count']} 个岗位卡在这条：{blocker}",
                "count": g["count"],
                "auto_applied": False,
            })
            out.append({
                "kind": "add_scoring_rule",
                "target": _ALLOWED_TARGETS["add_scoring_rule"],
                "value": f"「{blocker[:60]}」这条：简历里写明「{evidence[:60]}」即视为满足",
                "because": f"同上，{g['count']} 次都判不过；补齐证据后模型给到 "
                           f"{max(g['score_max'], 0)} 分以上",
                "count": g["count"],
                "auto_applied": False,
            })
        if g["not_fixable"]:
            out.append({
                "kind": "add_veto_keyword",
                "target": _ALLOWED_TARGETS["add_veto_keyword"],
                "value": blocker[:30],
                "because": f"{g['not_fixable']} 次判定为「确实不具备」，这类岗位可以继续让 AI "
                           f"逐条判，也可以直接否决省下来回调用",
                "count": g["count"],
                "auto_applied": False,
            })
        if len(out) >= SUGGESTION_MAX:
            break
    return out


def apply_suggestion(cfg: Any, suggestion: Dict[str, Any]) -> Tuple[bool, str]:
    """把**一条**被人工点采纳的建议写进配置对象。

    只认这里登记的三种 kind，且只动它对应的那一个字段：越界写入（改 api_key、
    改阈值、整段覆盖规则）一律拒绝——采纳按钮的权限必须比"保存配置"小。
    """
    kind = str((suggestion or {}).get("kind") or "")
    value = _norm((suggestion or {}).get("value"))
    if kind not in _ALLOWED_TARGETS:
        return False, f"未登记的建议类型：{kind or '(空)'}，不写入任何配置"
    if not value:
        return False, "这条建议没有内容，不写入配置"
    if str((suggestion or {}).get("target") or "") != _ALLOWED_TARGETS[kind]:
        return False, "建议的目标字段与登记的不一致，拒绝写入"

    if kind == "add_scoring_rule":
        current = str(getattr(cfg.ai, "custom_scoring_prompt", "") or "")
        if value in current:
            return False, "这条规则已经在打分要求里了"
        cfg.ai.custom_scoring_prompt = (
            (current + "\n" if current else "") + value)
        return True, "已追加到 AI 自定义打分要求"

    attr = "custom_filter_keywords" if kind == "add_veto_keyword" else "skills"
    owner = cfg.ai if kind == "add_veto_keyword" else cfg.resume
    items = list(getattr(owner, attr, []) or [])
    if any(_norm(x) == value for x in items):
        return False, "这条已经在列表里了"
    items.append(value)
    setattr(owner, attr, items)
    return True, ("已追加到硬性筛选条件" if kind == "add_veto_keyword"
                  else "已追加到简历技能")
