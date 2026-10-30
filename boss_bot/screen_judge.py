# -*- coding: utf-8 -*-
"""把一条池里的岗位送进 AI 判分，结论写回这一行——这件事只住这一份。

抓取腿（main_loop._精筛一条）和面板上的「按这份文件重判」都走这里：
两边各写一套的话，"AI 没答上来"和"AI 判了不合"这两种结局迟早会一边写成另一种，
池里就会出现永远判不完的行（10-23 那 13348 条「暂无可用 AI 接口」就是这么来的）。
"""

import time

from boss_bot import ai_screen_doc
from boss_bot.job_pool import 岗位行成抓取


def 判分写回(池, 引擎, row: dict, 文档=None) -> tuple:
    """判一条并写回。返回 (给人看的一句话, 真写进去的结论dict 或 None)。

    第二个返回值是给"按文件重判"那条线程用的：它要按真写进去的结论数匹配数，
    不能拿屏幕上的提示去猜（提示里"不合"和"没判成"是两种完全不同的结局）。
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
    成, 话 = 池.改(row.get("id", ""), ai=写)
    if not 成:
        return "结论没写回池子: %s" % 话, None
    if 写.get("接口异常"):
        return "AI 没判成（%s）" % (str(ai.get("reason") or "")[:40] or "接口没回话"), 写
    return ("%s分 %s" % (ai.get("score"), "像能投" if ai.get("is_match") else "不合")), 写
