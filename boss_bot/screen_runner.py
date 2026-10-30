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
from boss_bot.screen_judge import 判分写回

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
                if str(判.get("doc") or "") == 哈希 and 判.get("判于"):
                    continue
                队列.append((号, r))
        if 上限 > 0:
            队列 = 队列[:上限]
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
                话, 写 = 判分写回(岗位池(account_index=号), 引擎, r, 文档=文档)
            except Exception as e:
                话, 写 = "判分抛异常: %s" % (str(e) or type(e).__name__), None
            if 写 is not None and not 写.get("接口异常"):
                _加(judged=1)
                if 写.get("合"):
                    _加(matched=1)
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
