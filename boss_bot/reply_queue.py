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

# 拒过这一单的完整说法。上面那三条只是我们自己写死的收尾模板，接不住 AI 现编的拒话：
# 盘上我方气泡里真实出现过的形状是 不耽误您 479 / 不考虑 281 / 不打扰 98 / 方向不 90 /
# 不太合适 74 / 暂时不 72 / 不匹配 12 / 就先不 4 / 不投了 1，朱微那一路是最后一种。
拒话说法 = REFUSAL_ENDINGS + ("不投了", "不考虑", "不太合适", "不适合我", "方向不合适",
                              "暂时不投", "就先不", "不耽误您", "不打扰")

# 问"这个岗位还在不在招"的那一句。同一会话里这句只许问一次：
# 武先生那一路 10-23 12:49 问过「天盛传媒这个岗位还在招吗？」没人回，
# 10-24 13:03 又问一遍「哈，还在吗？」——用户 10-25 的指正「同一个岗位，你多次问
# 人家要不要招聘」就是这一族。第一次都没回，第二次只会更像机器。
还招吗说法 = ("还在招吗", "还招人吗", "还在招人", "是否还在招", "岗位还在",
             "还招吗", "在招吗", "还在考虑吗", "还在吗")


def 我方说过(chat, 说法) -> bool:
    """这一段对话里，我们自己有没有说过带这些字样的话（整段都算，不只最后一句）。"""
    for m in _sorted((chat or {}).get("messages") or []):
        if m.get("is_mine") and any(k in msg_body(m) for k in 说法):
            return True
    return False


def 拒过这单(chat) -> bool:
    """这一单我们是不是已经当面拒过了。

    只看最后一句不够：朱微那一路 10-21 16:24 说的是「…招聘实习这个岗位方向不太合适，
    就不投了，谢谢您～」，可那句被后面两条我方气泡（10-23 12:56 的占位符、13:19 的
    「嗯嗢，好的～」）压在了下面，followup_due 原来只比 info["said"]（最后那句我方），
    于是 10-24 13:21 又追出一句「之前聊的人力招聘实习生…我很有兴趣」——
    前脚拒、后脚说有兴趣，HR 当场认出「是用ai插件主动投递的吗老师？」。
    """
    return 我方说过(chat, 拒话说法)


def 问过还招吗(chat) -> bool:
    """那句"还在招吗"问出去之后对方一句没回——这种就不许问第二遍。

    对方后来开过口就放开：那是新的一轮对话，再提一次不是复读（我自己第一版写成"整段
    问过就拦"，回放盘上存档发现 179 个被拦的会话里有 49 个 HR 后来回了话，比如
    河北鼎联那句之后对方回的是「我们有线上的项目，每天在家里用手机电脑做一做…」——
    那种正是该接着往下聊的，拦掉等于自己把漏斗掐了）。
    """
    seq = _sorted((chat or {}).get("messages") or [])
    问 = 对方 = -1
    for i, m in enumerate(seq):
        if inbound_body(m):
            对方 = i
        elif m.get("is_mine") and any(k in msg_body(m) for k in 还招吗说法):
            问 = i
    return 0 <= 问 > 对方


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


def 排序位置(消息们: list) -> list:
    """给每条消息算一个可比较的排序键，和存档写回时用的那一份是同一套规则。

    data-mid 是雪花 id，同一会话内自上而下单调递增，是唯一可靠的顺序；BOSS 的时间
    标签只到分钟、还常常整段缺失（"昨天 21:54"、"09-23 21:37" 解析不出戳，"HH:MM"
    会被当成今天），按它重排必然把昨天的消息排到今天之后。
    没有 mid 的行沿用上一条带 mid 的位置，再用列表下标兜住先后——这种行只有两种来路：
    我们刚发出去、还没从页面回填 mid 的自记气泡，和工具补写的动作标记（实测全库
    13031 行带 mid、527 行不带 mid 的**全部**是我方的、对方一行都没有）。

    这一族以前是一条单句键 `(int(mid) if mid.isdigit() else 0, time)`：没 mid 就是 0，
    等于把我们刚发出去的那条排到整段对话最前面，于是"我们已经接过了"被读成
    "卡在 HR 手上"——欠回复名单每一轮把同一句重新端上来，而 owed 那条腿是故意绕开
    was_handled 的（"看过但没回"要能翻账），结果同一句被回两三遍：盘上 52 组、
    涉及 112 条，赵女士那句「好的」被回了 6 次，朱微那句「好嘟」12:56 回过一次、
    13:19 又回一次。同一段存档回放：旧键判成"欠回复"的 280 个会话里有 33 个其实早回过了。
    """
    原文 = list(消息们 or [])
    第一个 = 0
    for m in 原文:
        mid = str(m.get("mid") or m.get("msg_id") or "").strip()
        if mid.isdigit():
            第一个 = int(mid)
            break
    键 = []
    上一个 = 0
    for idx, m in enumerate(原文):
        mid = str(m.get("mid") or m.get("msg_id") or "").strip()
        if mid.isdigit():
            上一个 = int(mid)
        键.append(((上一个 or 第一个), idx))
    return 键


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
    原文 = list(messages or [])
    键 = 排序位置(原文)
    return [原文[i] for i in sorted(range(len(原文)), key=lambda i: 键[i])
            if msg_body(原文[i])]


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
    for i, m in enumerate(seq):
        if inbound_body(m):
            last_in = i
        if m.get("is_mine"):
            last_out = i
    # 谁在这段对话里更靠后，就卡在谁手上（顺序已由 排序位置 按 data-mid 定好）
    if last_in is not None and (last_out is None or last_in > last_out):
        m = seq[last_in]
        return "reply", {"ask": msg_body(m),
                         "at": msg_time(m, fallback_time, now),
                         "mid": str(m.get("mid") or "")}
    if last_out is not None:
        m = seq[last_out]
        return "follow", {"said": msg_body(m),
                          "at": msg_time(m, fallback_time, now)}
    return "idle", {}


def is_pinned(chat) -> bool:
    """这一路是不是他自己在 BOSS 里置顶的会话。用户口径（2026-10-08）：
    「我置顶的岗位你就不要动了，因为那我打算单独聊。」
    侧栏行和存档都用 pinned 这一个字段，所以四条会动手的腿（回复、跟进、
    补发简历、批量清剿工具）能共用这一句判据。
    """
    return bool((chat or {}).get("pinned"))


def owed_replies(chats, now=None, max_age_hours=72, limit=15):
    """存档里"对方最后说话、我们没接"的会话，按最新优先。

    max_age_hours 是为了不去翻三个月前的旧账：那些 HR 早就不在招了，回过去只是浪费一次发送额度。
    """
    now = now or datetime.now()
    out = []
    for c in chats or []:
        if is_pinned(c):
            continue
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
    置顶的那一路是他自己聊的，第三条否决走同一句 is_pinned。
    """
    if not (chat.get("company") or "").strip():
        return False
    if is_pinned(chat):
        return False
    bodies = [inbound_body(m) for m in _sorted(chat.get("messages") or [])]
    bodies = [b for b in bodies if b]
    if not bodies:
        return False
    # 量整段对方说过的话，不只最后一句：上海仟嘉百供应链那一单第一句就交代了
    # 「工作内容骑电瓶车配送山姆超市日常用品」，最后一句只是「加个微信吧，我查下
    # 你离哪个站点近」——只看最后一句，跟进轮 10-07 21:48 替回了
    # 「之前聊的…我很有兴趣」，等于我们一边拒配送一边追配送。
    return not veto_hit_anywhere(title_keywords, body_keywords,
                                 title=chat.get("job_name") or "",
                                 text="\n".join(bodies))


def followup_due(chats, state, now=None, after_hours=8, gap_hours=24,
                 max_times=2, within_days=4, limit=6):
    """我们说完对方就沉默的会话——要追，不然漏斗永远停在"已沟通"。

    三个闸门：静默够久（after_hours）、同一会话最多追 max_times 次、两次之间隔够 gap_hours。
    没有这三条就会变成骚扰，而且很容易踩 BOSS 的反爬。
    """
    now = now or datetime.now()
    out = []
    for c in chats or []:
        if is_pinned(c):
            continue
        kind, info = chat_state(c.get("messages") or [], now, c.get("updated_at") or "")
        if kind != "follow":
            continue
        # 上一句就是我们自己说的"这个不考虑了"，再追一句"还在招人吗"就是反悔骚扰
        # （2026-10-07 09:54 账号2 真的对一单不露脸主播追过）。
        # 判据扫我方整段，不只最后一句——拒话被后面的客气话压住就漏（见 拒过这单）。
        if 拒过这单(c):
            continue
        # 这句已经问过、对方一句没回，就不再问第二遍
        if 问过还招吗(c):
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
