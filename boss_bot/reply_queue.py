# -*- coding: utf-8 -*-
"""谁在等我们回、谁该去追——回复与跟进的候选判定。

为什么不能只认 BOSS 侧栏的红点：
1) 侧栏是虚拟列表，一屏只渲染 ~20 行，红点落在渲染窗口外就扫不到；
2) 启动全量同步会把每个会话点开读一遍，读过红点就被清掉——等于我们自己把触发信号消耗掉了。
   2026-10-04 实测：连续 289 轮"0 个未读"，同期本地存档里压着「大四还有课吗」
   「通勤距离怎么样」这类真提问没人接。
所以候选走两条腿：线上红点 + 本地存档里"对方最后说话而我们没接"的那批。
"""

import re
from datetime import datetime, timedelta

from boss_bot.intent import veto_hit_anywhere

# 平台自己塞进会话的卡片，回它等于对着空气说话（实测 248/303 张卡片都是这一句）
NOISE_CARDS = ("竞争者PK", "查看详细分析", "请求已发送", "已发送给Boss",
               "AI自动沟通", "AI生成回复", "点击预览附件简历", "对方向你发送了")
# 要我们表态的卡片：同意与否直接决定能不能换到微信/电话/面试
ACTION_CARDS = ("附件简历，您是否同意", "交换微信", "电话号码，您是否同意",
                "是否接受此工作地点", "互换电话", "电话联系TA")

# 我们自己的收尾话术里带这几串，说明这一单已经当面拒过了：岗位类型拒绝
# （reply_engine.FAMILY_DECLINE_REPLY）、线下面试拒绝（main_loop.OFFLINE_INTERVIEW_DECLINE）。
# 追这种会话等于前脚说"不考虑"、后脚问"还在招人吗"。
# 文案改了要同步这里——tests/test_blue_collar_gate.py 有一致性锁。
REFUSAL_ENDINGS = ("就不耽误您时间了", "不占用您的时间", "先放弃")


def msg_body(m: dict) -> str:
    """气泡正文在 text/content，卡片正文只在 card_text——只认 text 会把卡片当成没说话。"""
    for key in ("text", "content", "card_text"):
        v = (m.get(key) or "").strip()
        if v:
            return v
    return ""


def inbound_body(m: dict) -> str:
    """对方发来的、值得接的一句；自己发的、系统提示、平台噪声卡片一律返回空串。"""
    if m.get("is_mine") or m.get("is_system"):
        return ""
    body = msg_body(m)
    if not body:
        return ""
    if m.get("kind") == "card":
        return body if any(k in body for k in ACTION_CARDS) else ""
    if any(k in body for k in NOISE_CARDS):
        return ""
    return body


def order_key(m: dict):
    """顺序按 data-mid 判，不按时间字符串——BOSS 的时间只到分钟且跨年要猜。"""
    mid = str(m.get("mid") or "")
    return (int(mid) if mid.isdigit() else 0, str(m.get("time") or ""))


def msg_time(m: dict, fallback: str = "", now=None) -> datetime:
    """把 BOSS 的相对时间折成绝对时间：'10:50'=今天，'09-29 09:18'=今年，带年就用年。

    读不准就退回存档 updated_at：跟进只看"够不够久"，误差一小时可以接受。

    `now` 是折算的基准钟。'昨天'/'前天' 必须按调用方的钟推，不能各读各的墙上时间：
    否则同一条存档在跨午夜前后跑出来的绝对时间差一天，欠回复的账龄会从 16 小时
    变成 0 小时（这条测试 2026-10-05 00:02 就红过一次）。
    """
    now = now or datetime.now()
    raw = (m.get("time") or "").strip()
    parts = raw.split()
    try:
        if len(parts) == 2 and parts[0] in ("昨天", "前天"):
            # 盘上实测有 5 条是这种写法：不认的话会解析成 datetime.min，
            # 于是"欠回复"被当成三个月前（不再补）、"该追的"被当成过期（不再追）
            hm = parts[1].split(":")
            base = now - timedelta(days=1 if parts[0] == "昨天" else 2)
            return base.replace(hour=int(hm[0]), minute=int(hm[1]), second=0, microsecond=0)
        if len(parts) == 2 and "-" in parts[0]:
            md, hm = parts
            if md.count("-") == 2:
                return datetime.strptime(raw, "%Y-%m-%d %H:%M")
            year = (fallback[:4] or str(now.year))
            dt = datetime.strptime("%s-%s %s" % (year, md, hm), "%Y-%m-%d %H:%M")
            if dt > now + timedelta(days=1):   # 12-30 出现在 1 月 → 是去年的
                dt = dt.replace(year=dt.year - 1)
            return dt
        if len(parts) == 1 and ":" in parts[0]:
            hm = parts[0].split(":")
            base = datetime.strptime((fallback or now.strftime("%Y-%m-%d"))[:10], "%Y-%m-%d")
            return base.replace(hour=int(hm[0]), minute=int(hm[1]))
    except Exception:
        pass
    try:
        return datetime.strptime((fallback or "")[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return datetime.min


def _sorted(messages):
    return sorted([m for m in messages if msg_body(m)], key=order_key)


def chat_state(messages, now=None, fallback_time=""):
    """一个会话现在卡在谁手上。

    返回 (kind, 详情)：
      'reply'   —— 对方最后一句还没人接，详情=那句原文
      'follow'  —— 我们最后一句对方没回，详情=我们说的原文
      'idle'    —— 没有可动作的内容
    """
    now = now or datetime.now()
    seq = _sorted(messages)
    if not seq:
        return "idle", {}
    last_in = None
    last_out = None
    for m in seq:
        if inbound_body(m):
            last_in = m
        if m.get("is_mine"):
            last_out = m
    if last_in and (not last_out or order_key(last_in) > order_key(last_out)):
        return "reply", {"ask": msg_body(last_in),
                         "at": msg_time(last_in, fallback_time, now),
                         "mid": str(last_in.get("mid") or "")}
    if last_out:
        return "follow", {"said": msg_body(last_out),
                          "at": msg_time(last_out, fallback_time, now)}
    return "idle", {}


def owed_replies(chats, now=None, max_age_hours=72, limit=15):
    """存档里"对方最后说话、我们没接"的会话，按最新优先。

    max_age_hours 是为了不去翻三个月前的旧账：那些 HR 早就不在招了，回过去只是浪费一次发送额度。
    """
    now = now or datetime.now()
    out = []
    for c in chats or []:
        kind, info = chat_state(c.get("messages") or [], now, c.get("updated_at") or "")
        if kind != "reply":
            continue
        age = now - info.get("at", datetime.min)
        if age > timedelta(hours=max_age_hours):
            continue
        out.append({"name": c.get("chat_name") or c.get("name") or "未知",
                    "company": (c.get("company") or "").strip(),
                    "job_name": c.get("job_name") or "",
                    "ask": info.get("ask", ""),
                    "age_hours": round(max(age.total_seconds() / 3600.0, 0.0), 1),
                    "index": 0})
    out.sort(key=lambda r: -r["age_hours"])   # 等得最久的先回，免得新消息一直插队把它挤掉
    return out[:limit]


def worth_following_up(chat, title_keywords=(), body_keywords=()) -> bool:
    """这一会话值不值得去追一句"还在招人吗"。

    两条否决：
    1. 连公司名都空着（补扫那条链以前没带 company）、或 HR 从没开过口的孤儿存档——
       给从没理过我们的 HR 发"约面试"是骚扰，也最容易踩反爬；
    2. 岗位类型命中：普工/主播/快递/保洁这一类我们已经判死不投了，
       再去追一句等于自己打自己脸。判据和回复轮那一层完全同一套（veto_hit_anywhere），
       否则就会出现"文字拒了但还在被追"的自相矛盾（2026-10-07 11:57 那条
       「【白班坐岗】28/H包吃住…」就是这么追出去的）。
    """
    if not (chat.get("company") or "").strip():
        return False
    bodies = [inbound_body(m) for m in _sorted(chat.get("messages") or [])]
    bodies = [b for b in bodies if b]
    if not bodies:
        return False
    return not veto_hit_anywhere(title_keywords, body_keywords,
                                 title=chat.get("job_name") or "",
                                 text=bodies[-1])


def followup_due(chats, state, now=None, after_hours=8, gap_hours=24,
                 max_times=2, within_days=4, limit=6):
    """我们说完对方就沉默的会话——要追，不然漏斗永远停在"已沟通"。

    三个闸门：静默够久（after_hours）、同一会话最多追 max_times 次、两次之间隔够 gap_hours。
    没有这三条就会变成骚扰，而且很容易踩 BOSS 的反爬。
    """
    now = now or datetime.now()
    out = []
    for c in chats or []:
        kind, info = chat_state(c.get("messages") or [], now, c.get("updated_at") or "")
        if kind != "follow":
            continue
        # 上一句就是我们自己说的"这个不考虑了"，再追一句"还在招人吗"就是反悔骚扰
        # （2026-10-07 09:54 账号2 真的对一单不露脸主播追过）
        if any(mark in (info.get("said") or "") for mark in REFUSAL_ENDINGS):
            continue
        idle = now - info.get("at", datetime.min)
        if idle < timedelta(hours=after_hours) or idle > timedelta(days=within_days):
            continue
        key = "%s|%s|%s" % (c.get("account_index", 0), c.get("chat_name") or c.get("name"),
                            (c.get("company") or "").strip())
        rec = state.get(key) or {}
        times = int(rec.get("times") or 0)
        if times >= max_times:
            continue
        last = rec.get("last_at") or ""
        try:
            since_last = now - datetime.strptime(last[:19], "%Y-%m-%d %H:%M:%S") if last else None
        except Exception:
            since_last = None
        if since_last is not None and since_last < timedelta(hours=gap_hours):
            continue
        out.append({"name": c.get("chat_name") or c.get("name") or "未知",
                    "company": (c.get("company") or "").strip(),
                    "job_name": c.get("job_name") or "",
                    "key": key, "idle_hours": round(idle.total_seconds() / 3600.0, 1),
                    "times": times, "last_we_said": info.get("said", "")[:80], "index": 0})
    out.sort(key=lambda r: -r["idle_hours"])
    return out[:limit]


def mark_followed(state, key, now=None):
    now = now or datetime.now()
    rec = state.get(key) or {}
    state[key] = {"times": int(rec.get("times") or 0) + 1,
                  "last_at": now.strftime("%Y-%m-%d %H:%M:%S")}
    return state[key]
