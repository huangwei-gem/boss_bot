# -*- coding: utf-8 -*-
"""把一条池里的岗位送进 AI 判分，结论写回这一行——这件事只住这一份。

抓取腿（main_loop._精筛一条）和面板上的「按这份文件重判」都走这里：
两边各写一套的话，"AI 没答上来"和"AI 判了不合"这两种结局迟早会一边写成另一种，
池里就会出现永远判不完的行（10-23 那 13348 条「暂无可用 AI 接口」就是这么来的）。
"""

import hashlib
import time

from boss_bot import ai_screen_doc
from boss_bot.job_pool import 岗位行成抓取


def 判定口径(引擎, 文档=None) -> str:
    """这一条结论是按哪套口径判出来的：筛选文件 + 阈值 + 打分提示词 + 否决/方向词。

    为什么要多这一层：池里只有 `ai.doc`（筛选文件的指纹），没有文件时它恒为空串，
    于是改了打分提示词/方向后，旧结论（95 分、合）永远算"已判过"，下一轮
    `_精筛一条` 直接跳过——界面看着改生效了，池子还是旧账。把口径也写进结论，
    "改了必须生效"才落到数据上。
    """
    文档 = 文档 if 文档 is not None else ai_screen_doc.当前()
    ai = getattr(getattr(引擎, "config", None), "ai", None)
    部 = [
        str((文档 or {}).get("hash") or ""),
        str(getattr(ai, "match_threshold", "")),
        str(getattr(ai, "veto_only_match", "")),
        str(getattr(ai, "custom_scoring_prompt", "") or ""),
        ",".join(str(x) for x in (getattr(ai, "custom_filter_keywords", None) or [])),
        ",".join(str(x) for x in (getattr(ai, "target_job_keywords", None) or [])),
        ",".join(str(x) for x in (getattr(ai, "title_veto_keywords", None) or [])),
    ]
    return hashlib.sha1("|".join(部).encode("utf-8")).hexdigest()[:16]


def 结论过期(引擎, row: dict, 文档=None) -> bool:
    """这一行池子的 AI 结论是不是按旧口径判的（没判过也算过期）。"""
    ai = row.get("ai") or {}
    if not ai.get("判于"):
        return True
    return str(ai.get("口径") or "") != 判定口径(引擎, 文档)


def 判分写回(池, 引擎, row: dict, 文档=None, 追问=None) -> tuple:
    """判一条并写回。返回 (给人看的一句话, 真写进去的结论dict 或 None)。

    第二个返回值是给"按文件重判"那条线程用的：它要按真写进去的结论数匹配数，
    不能拿屏幕上的提示去猜（提示里"不合"和"没判成"是两种完全不同的结局）。

    追问 是可选回调 (job, verdict) -> dict|None：AI 判"不合"时追问它到底卡在哪一条。
    配额的扣减与边界带的判断都住在调用方（每号每轮 5 次、只追边界带），这里只负责
    在"真判了不合"之后问一句并写进同一行——池子这条筛岗路以前没接追问，
    所以界面上「今日追问 0 条」一直是真话，README 那套复盘等于空转。
    """
    文档 = 文档 if 文档 is not None else ai_screen_doc.当前()
    哈希 = str((文档 or {}).get("hash") or "")
    job = 岗位行成抓取(row)
    try:
        ai, _耗时 = 引擎._analyze_job_with_ai(job)
    except Exception as e:
        return "判分抛异常: %s" % (str(e) or type(e).__name__), None
    写 = {}
    if ai is None:
        # 「返回 None」有三种完全不同的来路，只按一种处理会把两种真结论抹掉：
        # ①一个接口都没轮到试（体检判死+冷却清零）→ 真没判，留给下一轮；
        # ②AI 这次没答上来（超时/正文被 thinking 吃满）带 ai_error → 也不能钉死；
        # ③AI 真判了、判的是"不合/分不够"→ 打招呼腿靠 None 不投，可结论必须落进池子。
        判 = job.get("_ai_result") or {}
        if 判.get("ai_error") and not job.get("_ai_no_candidate"):
            return "AI 这次没给出判断（%s），留给下一轮" % (
                str(判.get("reason") or "").strip()[:40] or "接口没回话"), None
        if job.get("_ai_no_candidate") or not 判:
            return "暂无可用 AI 接口，留给下一轮", None
        写 = {"分": 判.get("score"),
              "合": bool(判.get("is_match")),
              "理由": str(判.get("reason") or "")[:200],
              "接口": str((job.get("_ai_meta") or {}).get("model") or ""),
              "否决": str(判.get("veto_hit") or ""),
              "判于": time.time()}
        写["doc"] = 哈希
        写["口径"] = 判定口径(引擎, 文档)
        追问结果 = _追问一条(追问, job, 判, 写)
        if 追问结果:
            写["追问"] = 追问结果
        成, 话 = 池.改(row.get("id", ""), ai=写)
        if not 成:
            return "结论没写回池子: %s" % 话, None
        return "%s分 不合（%s）" % (判.get("score"),
                                   判.get("veto_hit") or str(判.get("reason") or "")[:24]), 写
    写 = {"分": ai.get("score"),
         "合": bool(ai.get("is_match")),
         "理由": str(ai.get("reason") or "")[:200],
         "接口": ai.get("model") or "",
         "否决": str(ai.get("veto_hit") or ""),
         "判于": time.time()}
    if ai.get("ai_error"):
        写["接口异常"] = True
    else:
        # 接口异常那次不算"按这份文件判过"：下次还得重判，否则一行错误结论会永远
        # 冒充"已经看过他的要求了"
        写["doc"] = 哈希
    写["口径"] = 判定口径(引擎, 文档)
    追问结果 = _追问一条(追问, job, ai, 写)
    if 追问结果:
        写["追问"] = 追问结果
    成, 话 = 池.改(row.get("id", ""), ai=写)
    if not 成:
        return "结论没写回池子: %s" % 话, None
    if 写.get("接口异常"):
        return "AI 没判成（%s）" % (str(ai.get("reason") or "")[:40] or "接口没回话"), 写
    return ("%s分 %s" % (ai.get("score"), "像能投" if ai.get("is_match") else "不合")), 写


def _追问一条(追问, job: dict, 判: dict, 写: dict):
    """只在"AI 真判了不合、且不是接口异常"时问一句；任何异常都不许把写回带崩。"""
    if 追问 is None or 写.get("接口异常") or 写.get("合"):
        return None
    try:
        return 追问(job, 判 or {})
    except Exception:
        return None
