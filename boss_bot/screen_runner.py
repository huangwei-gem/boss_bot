# -*- coding: utf-8 -*-
"""按他给的那份「筛选要求」把池里的岗位重判一遍（面板上一个按钮的事）。

为什么要单独一条线程：一轮池里近三千行、每行一次 AI，几分钟到几十分钟。
放在 HTTP 线程里就是面板转圈、超时；放在打招呼线程里会把投递腿堵死。
这里只判分、只写池，不碰浏览器、不点沟通——投不投仍由他在界面上勾。

挑哪些行判：
- 已经「按当前这份文件」判过的跳过（`ai.doc` 等于当前指纹）；
- 本地粗筛已经明确否掉的跳过（否决命中/方向不合）——那是他自己的口径，
  再花 AI 去问一遍也不会放行；
- 其余按"新抓的在前"排，判完一批再来一批（本线程一次跑完，可随时停）。
"""

import logging
import threading
import time

from boss_bot import ai_screen_doc
from boss_bot.job_pool import 岗位池
from boss_bot.greet_engine import in_probe_band
from boss_bot.screen_judge import 判分写回, 结论过期

logger = logging.getLogger(__name__)

_锁 = threading.Lock()
_停 = threading.Event()
_线程 = None

_状态 = {
    "running": False,
    "started_at": 0,
    "finished_at": 0,
    "doc_hash": "",
    "doc_name": "",
    "total": 0,       # 本轮挑出来要判的行数（不含本地已否/当前文件下已判的）
    "left": 0,        # 还剩多少条没轮到
    "judged": 0,      # 真判完并写回的行数
    "matched": 0,     # 其中 AI 判"合"的
    "failed": 0,      # 没判成的（接口不给话、写回失败）
    "skipped": 0,     # 本地粗筛已否、直接不送的
    "error": "",
    "last": "",
    "stopped": False,
}


def 状态() -> dict:
    with _锁:
        return dict(_状态)


def 停() -> bool:
    _停.set()
    return True


def 起(账号们, 引擎工厂, 上限: int = 0) -> tuple:
    """开跑。账号们是 [0, 1, ...]，引擎工厂(号) -> GreetEngine。"""
    global _线程
    文档 = ai_screen_doc.当前()
    if not 文档.get("hash"):
        return False, "还没有要求文件：先上传或粘贴一份要求，保存后再匹配"
    with _锁:
        if _状态["running"]:
            return False, "上一轮还在跑（%d/%d），等它跑完或先点停止" % (
                _状态["judged"], _状态["total"])
        _停.clear()
        _状态.update(running=True, started_at=time.time(), finished_at=0,
                     doc_hash=文档["hash"], doc_name=文档.get("name") or "",
                     total=0, left=0, judged=0, matched=0, failed=0, skipped=0,
                     error="", last="", stopped=False)
        _线程 = threading.Thread(target=_跑, args=(list(账号们), 引擎工厂, 文档,
                                                  int(上限 or 0)),
                                 daemon=True, name="ai-screen-run")
        _线程.start()
    return True, "开始按「%s」匹配岗位" % (文档.get("name") or "要求")


def _加(**增量) -> None:
    with _锁:
        for 键, 值 in 增量.items():
            if 键 == "last":
                _状态["last"] = 值
            elif 键 == "error":
                _状态["error"] = 值
            else:
                _状态[键] = _状态.get(键, 0) + int(值)


def _设(**字段) -> None:
    with _锁:
        _状态.update(字段)


def _追问器(引擎):
    """这一趟"按文件重判"里的追问回调：每号每轮 ≤ probe_max_per_round。

    配额按账号各算（两个号投的工种不同，边界带密度不一样）。没接这条的话，
    面板上「判分复盘」永远是 0 条——判分实际发生在这条线程里，不在打招呼腿里。
    """
    ai = getattr(getattr(引擎, "config", None), "ai", None)
    try:
        上限 = int(getattr(ai, "probe_max_per_round", 0) or 0)
    except (TypeError, ValueError):
        上限 = 0
    if 上限 <= 0:
        return None
    try:
        阈值 = int(getattr(ai, "match_threshold", 70) or 70)
    except (TypeError, ValueError):
        阈值 = 70
    已用 = {"n": 0}

    def 追问(job: dict, verdict: dict):
        if 已用["n"] >= 上限:
            return None
        # 命中硬否决不是"差一点"：卡在哪条词上结论里已经写出来了，再问一次是白烧配额
        if str((verdict or {}).get("veto_hit") or "").strip():
            return None
        if not in_probe_band((verdict or {}).get("score"), 阈值):
            return None
        # 链是懒加载的（greet_engine 起判分那一刻才建），所以只能在要问的时候取：
        # 这趟重判一开始一次判分都还没跑，早取一次就是整趟 None，界面永远 0 条追问。
        链 = getattr(引擎, "_ai_analyzer", None)
        if 链 is None:
            return None
        已用["n"] += 1
        return 链.probe_rejection(job, verdict or {})

    return 追问


def 按号轮转(队列: list, 上限: int) -> list:
    """这一趟要判哪些行：号与号轮着取，不许一个号把名额吃光。

    2026-11-03 线上现测：上限 120 那一趟 judged=120 全落在号0，号1 一行没判。
    队列是「号0 的池 → 号1 的池」拼起来的，直接截前 N 条，就等于只要号0 的
    待判数 ≥ N，另一个号永远排不上——他这两条腿各投各的方向，号1（数据分析）
    判不到，界面上就一直是旧口径留下的 95 分，勾哪条都像在盲投。
    """
    if 上限 <= 0:
        return list(队列)
    分组 = {}
    for 项 in 队列:
        分组.setdefault(项[0], []).append(项)
    出 = []
    第几 = 0
    while len(出) < 上限 and any(len(v) > 第几 for v in 分组.values()):
        for 号 in sorted(分组):
            批 = 分组[号]
            if 第几 < len(批):
                出.append(批[第几])
                if len(出) >= 上限:
                    break
        第几 += 1
    return 出


def _跑(账号s, 引擎工厂, 文档, 上限) -> None:
    哈希 = 文档["hash"]
    try:
        引擎s = {}
        for 号 in 账号s:
            try:
                引擎s[号] = 引擎工厂(号)
            except Exception as e:
                logger.warning("AI 自定义筛选：账号 %s 的判分引擎起不来: %s", 号, e)
                引擎s[号] = None
        if not any(引擎s.values()):
            _设(error="AI 引擎起不来：AI 没启用，或者一个可用接口都没有")
            return
        追问器s = {号: _追问器(引擎) for 号, 引擎 in 引擎s.items() if 引擎}

        队列 = []
        for 号 in 账号s:
            try:
                行s = 岗位池(account_index=号).列表(排序="新抓的在前")
            except Exception as e:
                logger.warning("AI 自定义筛选：读账号 %s 的池失败: %s", 号, e)
                continue
            for r in 行s:
                判 = r.get("ai") or {}
                粗 = r.get("gate") or {}
                if str(粗.get("否决命中") or "").strip() or 粗.get("方向命中") is False:
                    _加(skipped=1)
                    continue
                引擎 = 引擎s.get(号)
                if 引擎 is None:
                    # 引擎起不来的号退回老判据（按这份文件判过就跳过），别把整池排进
                    # 队列再逐条记失败
                    if str(判.get("doc") or "") == 哈希 and 判.get("判于"):
                        continue
                elif not 结论过期(引擎, r, 文档):
                    continue
                队列.append((号, r))
        队列 = 按号轮转(队列, 上限)
        _设(total=len(队列), left=len(队列))

        连续没接口 = 0
        for 序号, (号, r) in enumerate(队列):
            if _停.is_set():
                _设(stopped=True)
                break
            引擎 = 引擎s.get(号)
            if 引擎 is None:
                _加(failed=1)
                _设(left=len(队列) - 序号 - 1, last="账号 %s 的判分引擎不可用" % 号)
                continue
            try:
                话, 写 = 判分写回(岗位池(account_index=号), 引擎, r, 文档=文档,
                                  追问=追问器s.get(号))
            except Exception as e:
                话, 写 = "判分抛异常: %s" % (str(e) or type(e).__name__), None
            if 写 is not None and not 写.get("接口异常"):
                _加(judged=1)
                if 写.get("合"):
                    _加(matched=1)
                if 写.get("追问"):
                    话 = "%s · 追问: %s" % (
                        话, str(写["追问"].get("blocking_requirement") or "")[:30])
                连续没接口 = 0
            else:
                # 「没判成」一律算进连败：接口异常、没接口、写回失败。只认其中一种的话，
                # AI 没启用时会拿着整池近三千行空转一遍，界面看着在跑、实际一个字都不出。
                _加(failed=1)
                连续没接口 += 1
            _设(left=len(队列) - 序号 - 1, last=str(话 or "")[:80])
            if 连续没接口 >= 10:
                _设(error="连着 10 条都没判成（AI 接口不通或没启用），这一轮先停手（等下再点）")
                break
    except Exception as e:
        logger.exception("AI 自定义筛选轮次异常")
        _设(error="跑的时候炸了：%s" % (str(e) or type(e).__name__))
    finally:
        _设(running=False, finished_at=time.time())
