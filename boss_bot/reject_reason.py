# -*- coding: utf-8 -*-
"""HR 亲口说出的拒绝原因：当场问一次、记下来、回流到投递侧。

用户口径（2026-11-03，第二次说）："碰到拒绝的你要追问原因啊，之前就说让你追问原因，
然后自调整优化投递策略。" 2026-09-29 那版我把口径定成了"只问 AI 不问 HR"，
所以这条腿一直存在但只问模型，盘上现算的账是：2026-11-03 一整天
reply_source=rejection 发出去 5 条，全是"祝您招聘顺利~"这种收尾，问原因 0 条。
现在改成 HR 拒了当场问一句。

三条设计约束：
- 每段会话只问一次，判据读存档里我方气泡（跟 reply_queue.问过还招吗 同一套路），
  不新开状态文件——重启、换号都不会把"问过了"这件事忘掉。
- "已经招满了"这种是**状态**不是**评价**，问"差在哪一条"等于自言自语，不问。
- HR 给的原因只进两份地方：reply_records.decline_reason（自净化按列读它）和
  data/reject_reasons.json（投递侧按公司查、复盘按原因聚合）。
  自动改配置那一步仍然要人点采纳——和判分复盘原有契约一致。
"""

from __future__ import annotations

import json
import random
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path

from boss_bot.reply_queue import msg_body

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "reject_reasons.json"
RECENT_DAYS = 14          # 这家 HR 亲口拒过之后，多少天内不再把它的岗位放进池子
MIN_ANSWER_LEN = 6        # "嗯""好的""祝您顺利"这种不算回答（真原因都是一句话）
锁 = threading.RLock()
缓存秒 = 60
_缓存 = {}                # {文件路径: {"取于": datetime, "表": {公司: 最近一条}}}

# 请教原因的三种说法，随机挑一条，别每次都一个字都不差。
# 都是"先认下来、再问一句"的形状：只问一条最关键的，不追问第二遍，
# 也不在这句里推销自己（红线：拒过的岗不再提简历）。
请教话术池 = (
    "好的，理解～方便请教一下这次主要是差在哪一条吗？我好知道接下来该补什么。",
    "明白，谢谢您直说～如果不麻烦的话，能点一下主要是哪方面不匹配吗？我记下来改。",
    "好的～想跟您请教一句，卡住的是经验、技能还是别的？说一句我就知道了，谢谢您。",
)

# 我方气泡里出现这些字样，就算这一句已经问过了（整段都算，不只最后一句）。
请教说法 = ("差在哪一条", "主要是哪方面不匹配", "卡住的是经验", "哪一条吗", "该补什么")

# 这些是"岗位自己的状态"，不是对我的评价——问"差在哪"没有对象。
状态类拒因 = ("已招满", "招满了", "已关闭", "已结束", "暂停招聘", "岗位已下线",
              "已经找到", "找到合适的人", "先不招了")

# HR 答完原因后又开口约人，那是新的一轮，不能当成"原因"存下来。
邀约信号 = ("再聊聊", "约面试", "可以聊聊", "聊聊看", "有兴趣", "感兴趣",
            "面试", "来公司", "约一下", "加个微信", "加微信")

# 纯客套不构成原因。
_客套 = {"好", "好的", "嗯", "嗯嗯", "没事", "没什么", "就这样", "谢谢", "谢谢你",
         "不客气", "OK", "ok", "行", "可以"}


def 请教话术(随机=random) -> str:
    return 随机.choice(请教话术池)


def 该问原因(latest: str) -> bool:
    """这句拒绝是不是在"评价人"（该问一句差在哪），而不是"岗位没了吧"（问也没用）。"""
    t = (latest or "").strip()
    if not t:
        return False
    return not any(k in t for k in 状态类拒因)


def 已问过(messages) -> bool:
    """这段对话里我方有没有已经把原因问出去了——问过就不再问第二遍。

    扫整段不只最后一句：那句"祝您招聘顺利"后面还跟着别的我方气泡时，
    只看最后一句会漏（项目里 reply_queue.拒过这单 为同样的形状踩过一次）。
    """
    for m in messages or []:
        if m.get("is_mine") and any(k in msg_body(m) for k in 请教说法):
            return True
    return False


def 刚问过(messages) -> bool:
    """我方最后那句就是请教——紧跟着的对方一句才是对这句的回答。

    用"问过就收"当捕获判据太宽：那之后又聊了几轮的话也会被当成原因存下来
    （"祝您顺利"这种客套话尤其容易撞上）。只认这一问一答的相邻形状。

    取"最后那句对方说的话"作为 latest（和 reply_engine._split_messages 同一找法），
    再看它前面紧挨着的是不是我方那句请教——存档里我方气泡有时排在后面，
    直接取倒数第二条会看错人。
    """
    序 = [m for m in (messages or []) if msg_body(m)]
    位 = -1
    for i, m in enumerate(序):
        if not m.get("is_mine") and not m.get("is_system"):
            位 = i
    if 位 <= 0:
        return False
    前 = 序[位 - 1]
    return bool(前.get("is_mine")) and \
        any(k in msg_body(前) for k in 请教说法)


def 是原因回答(text: str) -> bool:
    """HR 这句能不能当"拒绝原因"收下来：有实质内容、且在回答而不是在约人。"""
    t = (text or "").strip()
    if len(t) < MIN_ANSWER_LEN or t in _客套:
        return False
    return not any(k in t for k in 邀约信号)


def 归一(reason: str) -> str:
    """去掉空白与标点后取前 40 字做键——和判分复盘同一套归并口径，宁可分两组也不合并错。"""
    s = re.sub(r"\s+", "", str(reason or ""))
    s = re.sub(r"[，。！？；：、,.!?;:\"'（）()【】\[\]]", "", s)
    return s[:40]


def _读(path) -> list:
    p = Path(path)
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            数据 = json.load(f)
    except Exception:
        return []
    条目 = 数据.get("条目") if isinstance(数据, dict) else 数据
    return 条目 if isinstance(条目, list) else []


def _写(path, 条目: list) -> None:
    from boss_bot.unified_config import write_json_atomic
    write_json_atomic(Path(path), {"条目": 条目, "更新": datetime.now().strftime(TIME_FORMAT)})


def 记一条(reason: str, company: str = "", job_name: str = "", chat_name: str = "",
           account_index: int = 0, path=None, now: datetime = None) -> bool:
    """把 HR 亲口说的原因落库。同一家公司同一句话只记一次（每轮都会重读同一段对话）。

    返回 True 表示这是新记的一条。异常一律吞掉并返回 False——原因落不了库
    不能把回复腿带崩。
    """
    文本 = (reason or "").strip()
    公司 = (company or "").strip()
    if not 文本:
        return False
    path = Path(path) if path else DEFAULT_PATH
    时刻 = (now or datetime.now()).strftime(TIME_FORMAT)
    with 锁:
        条目 = _读(path)
        键 = (归一(文本), 公司)
        if any((归一(r.get("reason")), str(r.get("company") or "").strip()) == 键 for r in 条目):
            return False
        条目.append({"at": 时刻, "account_index": int(account_index or 0),
                     "company": 公司, "job_name": job_name or "",
                     "chat_name": chat_name or "", "reason": 文本[:200]})
        _写(path, 条目)
        _缓存.pop(str(path), None)      # 刚拒的这一家，下一轮抓取就要挡得住
    return True


def 全部条目(path=None, days: int = 0, account_index: int = None) -> list:
    """读回原因清单。days>0 只给这些天内的，account_index 不是 None 时按号筛。"""
    条目 = _读(Path(path) if path else DEFAULT_PATH)
    if days:
        界 = datetime.now() - timedelta(days=days)
        条目 = [r for r in 条目 if _时间(r.get("at")) and _时间(r.get("at")) >= 界]
    if account_index is not None:
        条目 = [r for r in 条目 if int(r.get("account_index") or 0) == int(account_index)]
    return 条目


def 这家拒过(company: str, days: int = RECENT_DAYS, path=None,
            now: datetime = None) -> dict:
    """这家公司这些天内被 HR 亲口拒过就返回最近一条（带原因），没拒过返回空字典。"""
    return 按公司索引(days=days, path=path, now=now).get(
        (company or "").strip(), {})


def 按公司索引(days: int = RECENT_DAYS, path=None, now: datetime = None) -> dict:
    """{公司: 最近一条拒因}。一轮抓几十个岗位逐个读盘没必要，这里带 60 秒缓存。

    缓存过期就重读，所以面板上手动清掉文件、或直接改文件，最多 60 秒后生效；
    记一条 之后立刻失效，本号刚拒的那一家下一轮就挡得住。
    """
    path = Path(path) if path else DEFAULT_PATH
    时刻 = now or datetime.now()
    键 = str(path)
    with 锁:
        缓 = _缓存.get(键)
        if not 缓 or (时刻 - 缓["取于"]).total_seconds() > 缓存秒:
            缓 = {"取于": 时刻, "表": _建索引(全部条目(path=path), 时刻, days)}
            _缓存[键] = 缓
        return 缓["表"]


def 筛掉刚拒过的(可投, days: int = RECENT_DAYS, path=None,
                now: datetime = None) -> tuple:
    """抓取收池那一步的判据：这家 HR 亲口拒过的，本轮不再放进池子。

    返回 (剩下的, {公司: 原因})。挡在进池这一步而不是勾完再撤，是因为界面口径
    「池里不留投不出去的岗位」；只挪不删，被挡的公司在日志里逐个报名字。
    """
    表 = 按公司索引(days=days, path=path, now=now)
    挡 = {}
    剩下 = []
    for j in 可投 or []:
        公司 = str((j or {}).get("company") or "").strip()
        if 公司 and 公司 in 表:
            挡[公司] = 表[公司].get("reason") or ""
            continue
        剩下.append(j)
    return 剩下, 挡


def _建索引(条目, 时刻: datetime, days: int) -> dict:
    界 = 时刻 - timedelta(days=max(0, int(days)))
    表 = {}
    for r in 条目:
        公司 = str(r.get("company") or "").strip()
        if not 公司:
            continue
        时 = _时间(r.get("at"))
        if 时 is None or 时 < 界:
            continue
        旧 = 表.get(公司)
        if not 旧 or 时 > _时间(旧.get("at")):
            表[公司] = r
    return 表


def _时间(原始) -> datetime:
    try:
        return datetime.strptime(str(原始 or "")[:19], TIME_FORMAT)
    except ValueError:
        return None
