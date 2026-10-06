# -*- coding: utf-8 -*-
"""联系与简历台账：把"HR 给了电话/微信""简历发出去了"的会话单独捞出来。

用户要看的是"这个岗位值不值得跟"，所以一行必须自己说清：谁、哪家公司、
什么岗位、多少钱、拿到了什么联系方式、简历发没发、依据是哪句原话。

数据来源用会话存档（messages/*.json）而不是回复记录：回复记录里只有姓名和
岗位标题，公司名要么没有、要么得靠 fuzzy 去打招呼记录里对；会话存档的文件本身
就带 company 和 job_name，而且联系方式那张卡片（"13272464870 蒲慧芝的微信号…
复制微信号"）是当成对方消息存进对话流的，从对话里捞最准。

薪资和城市也不用去打招呼记录里对：侧栏岗位标题本身就带着（"…110-120元/天成都
查看职位"），拆它比 fuzzy join 靠谱，打招呼记录只用来补岗位链接和拆不出时的薪资。

判据全部留痕在 matched_quote 里，方便人工核对判错了什么，也方便以后按真实分布改。
"""
import re

# 手机号：前后不能再有数字，否则会把"10-14K上海"这类薪资串里的数字吃掉
PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
# 微信号：只在有明确字样时才认，光靠"字母+数字"会把岗位标题里的 MJ000290 当成微信号
WECHAT_RE = re.compile(
    r"(?:微信号|添加微信|加我微信|加我微|加v|加V|vx|VX|wx|WX|weixin|微❤|V信|维信)"
    r"\s*[:：]?\s*([A-Za-z][A-Za-z0-9_-]{4,19}|1[3-9]\d{9})")
# BOSS 交换联系方式的卡片整行：「<号码> <姓名>的微信号 <号码> 复制微信号」。
# 连尾巴一起匹配掉，否则同一个数字会被下面的手机号正则再报一遍，界面上像两个联系方式。
CARD_CONTACT_RE = re.compile(
    r"(?:\d{6,20}|[A-Za-z][A-Za-z0-9_-]{4,19})\s+\S{0,12}的微信号[^\n]*复制微信号")
# 这些词紧跟在"添加微信"后面是链接不是微信号（真实误报：…扫描二维码添加微信https://docs…）
URL_SCHEMES = {"https", "http", "www", "ftp", "mailto"}

# 对方主动要加联系方式的说法。不能用"微信""电话"这种裸关键词判：
# 真实存档里被判成"说要给"的一多半是 JD 文本（"我们招电话客服岗位能接受吗"
# "请保持电话畅通""不用打电话"），那些一行号码都没有，混进表里就是让人白翻。
CONTACT_REQUEST_RE = re.compile(
    r"加(?:一下|个|下)?(?:我的?|您)?微信|加v|加V|留个?(?:电话|微信号?)"
    r"|(?:微信|电话)\s*(?:详聊|聊一下|聊吗|沟通|联系我)|把.{0,8}微信给")
# BOSS 的交换请求系统卡片（"我想要和您交换微信，您是否同意 拒绝 同意"，
# 以及"你可以直接跟我互换电话联系我 电话联系TA"，两型合起来存档里有 61 条）。
# 它带"微信/电话"字样但没有号，得和 HR 自己打字说的"加个微信"分开写，
# 不然看表的人会以为对话里有个号是自己没找到，其实那一步还没点同意。
EXCHANGE_REQUEST_RE = re.compile(
    r"交换(?:微信|联系方式|电话|手机)|互换|电话联系TA"
    r"|我想要[一]?(?:份|个)?您的?(?:电话号码|微信号码)")

RESUME_SENT_MARK = "[简历已发送]"
CONTACT_AGREED_MARK = "[已同意交换联系方式]"
# 我们自己的话里出现"加微信"不算对方给了联系方式，只是回应对方的客套
OUR_OWN_ECHO = ("我稍后添加您微信", "稍后加您微信", "方便的话我加您微信")
RESUME_ASK_WORDS = ("简历发我", "发份简历", "发一下简历", "投个简历", "简历发一份",
                    "附件简历", "简历发给我", "发下简历", "简历发过来")

# 侧栏岗位标题 = 岗位名 + 薪资 + 城市 + 「查看职位」。K 为单位时数字最多三位
# （8-13K），因为「月入9000」和「9-14K」会被拼成 90009-14K，收紧位数才能切开。
# (?!0\d) 是这一刀的关键：BOSS 不会印前导零，所以 "009-14K" 这种读法一律否掉，
# 让正则回溯到 "9-14K"。
SALARY_RE = re.compile(
    r"(?!0\d)\d{1,3}(?:\.\d)?\s*[-~至到]\s*(?!0\d)\d{1,3}(?:\.\d)?\s*[Kk]"
    r"|(?!0\d)\d{1,3}(?:\.\d)?\s*[Kk](?![A-Za-z])"
    r"|(?!0\d)\d{1,6}\s*[-~至到]\s*(?!0\d)\d{1,6}\s*元\s*/\s*[天月时单日年]"
    r"|(?!0\d)\d{1,6}\s*[-~至到]\s*(?!0\d)\d{1,6}\s*元"
    r"|(?!0\d)\d{1,4}\s*[-~至到]\s*(?!0\d)\d{1,4}\s*万\s*(?:[/月年]*)"
    r"|(?!0\d)\d{1,6}\s*元\s*/\s*[天月时单日年]")


def split_job_title(raw) -> dict:
    """侧栏岗位标题 → 岗位名 / 薪资 / 城市。拆不出来就留空，不猜。"""
    text = str(raw or "").strip()
    if text.endswith("查看职位"):
        text = text[:-len("查看职位")]
    hit = SALARY_RE.search(text)
    if not hit:
        return {"title": text.strip(), "salary": "", "city": ""}
    salary = re.sub(r"\s+", "", hit.group(0))
    title = text[:hit.start()].strip()
    rest = text[hit.end():].strip()
    city = rest if len(rest) <= 6 else ""
    return {"title": title, "salary": salary, "city": city}


def _message_text(msg: dict) -> str:
    """一条消息的可读正文：卡片在 card_text，纯文字在 text，都没有退回 block。"""
    for key in ("text", "card_text", "block"):
        val = str(msg.get(key) or "").strip()
        if val:
            return val
    return ""


def _is_from_hr(msg: dict) -> bool:
    """这条是 HR 说的吗。存档里 isFriend 是"对方发的"，is_mine/sender 是我们自己。"""
    if msg.get("is_mine") or msg.get("is_system") or str(msg.get("sender") or "") in ("bot", "me"):
        return False
    return bool(msg.get("isFriend"))


def extract_contacts(text) -> dict:
    """一段对方原文 → 拿到的电话/微信号 + 是否只提到要给。"""
    body = str(text or "")
    wechats, phones = [], []
    for hit in CARD_CONTACT_RE.finditer(body):
        token = hit.group(0).split()[0]
        if token not in wechats:
            wechats.append(token)
    masked = CARD_CONTACT_RE.sub(lambda m: " " * len(m.group(0)), body)
    for hit in WECHAT_RE.finditer(masked):
        token = hit.group(1)
        if token.lower() in URL_SCHEMES:
            continue
        if token not in wechats:
            wechats.append(token)
    for hit in PHONE_RE.finditer(masked):
        if hit.group(0) not in phones and hit.group(0) not in wechats:
            phones.append(hit.group(0))
    hinted = (not wechats and not phones
              and bool(CONTACT_REQUEST_RE.search(body))
              and not any(w in body for w in OUR_OWN_ECHO))
    return {"phones": phones, "wechats": wechats, "hinted": bool(hinted)}


def _norm_job(job_name) -> str:
    """岗位名归一化：去掉数字、薪资单位、括号这类噪音，只留能对上的一串字。"""
    text = re.sub(r"\d+", "", str(job_name or ""))
    text = re.sub(r"[Kk万薪元/时天年·•\s\-—_（）()【】\[\]!！?？,，。]", "", text)
    return text[:24]


def _key(account, company):
    return (str(account), str(company or "").strip())


def _greet_lookup(greet_rows):
    """(账号, 公司) → 该公司在这一号上投过的岗位，用来给台账补岗位链接。"""
    table = {}
    for row in greet_rows or []:
        company = str(row.get("company") or "").strip()
        if not company:
            continue
        table.setdefault(_key(row.get("account_index"), company), []).append({
            "norm": _norm_job(row.get("job_name")),
            "job_url": row.get("job_url") or "",
            "salary": row.get("salary") or "",
        })
    return table


def _match_greet(greets, account, company, archive_norm):
    """同公司同账号下对上岗位名；对不上但只投过一单的，那一单就是它。"""
    cands = [c for c in greets.get(_key(account, company), []) if c["norm"]]
    if not cands:
        return {}
    nested = [c for c in cands if c["norm"] in archive_norm]
    if nested:
        return max(nested, key=lambda c: len(c["norm"]))
    if len({c["norm"] for c in cands}) == 1:
        return cands[0]
    return {}


def contact_rows(chats, greet_rows=None) -> list:
    """会话存档列表 → 台账行（按最后活动时间倒序）。

    一行 = 一个"给了联系方式或发了/欠了简历"的会话；判据原文一起带出来。
    """
    greets = _greet_lookup(greet_rows)
    rows = []
    for chat in chats or []:
        phones, wechats, hinted = [], [], False
        resume_sent = contact_agreed = asked_resume = exchange_requested = False
        hr_last = ""
        quotes = []
        for msg in chat.get("messages") or []:
            text = _message_text(msg)
            if not text:
                continue
            if RESUME_SENT_MARK in text:
                resume_sent = True
                continue
            if CONTACT_AGREED_MARK in text:
                contact_agreed = True
                continue
            if not _is_from_hr(msg):
                continue
            hr_last = text
            got = extract_contacts(text)
            phones += [p for p in got["phones"] if p not in phones]
            wechats += [w for w in got["wechats"] if w not in wechats]
            hinted = hinted or got["hinted"]
            is_exchange_request = bool(EXCHANGE_REQUEST_RE.search(text))
            exchange_requested = exchange_requested or is_exchange_request
            if (got["phones"] or got["wechats"] or got["hinted"]
                    or is_exchange_request):
                quotes.append(text.replace("\n", " ")[:80])
            if any(w in text for w in RESUME_ASK_WORDS):
                asked_resume = True
        if not (phones or wechats or hinted or exchange_requested
                or resume_sent or contact_agreed or asked_resume):
            continue
        raw_job = chat.get("job_name") or ""
        parts = split_job_title(raw_job)
        hit = _match_greet(greets, chat.get("account_index"),
                           chat.get("company"), _norm_job(parts["title"]))
        rows.append({
            "account_index": chat.get("account_index"),
            "chat_name": chat.get("chat_name") or "",
            "company": chat.get("company") or "",
            "job_name": raw_job,
            "job_title": parts["title"],
            "salary": parts["salary"] or hit.get("salary", ""),
            "city": parts["city"],
            "job_url": hit.get("job_url", ""),
            "phones": phones,
            "wechats": wechats,
            "contact_kind": ("卡片已同意" if contact_agreed else
                             "给了号码" if (phones or wechats) else
                             "发起交换请求" if exchange_requested else
                             "说要给没留号" if hinted else ""),
            "resume_sent": bool(resume_sent),
            "resume_asked": bool(asked_resume and not resume_sent),
            "last_activity": chat.get("updated_at") or "",
            "hr_last_message": hr_last.replace("\n", " ")[:80],
            "matched_quote": " / ".join(quotes[-2:])[:160],
        })
    rows.sort(key=lambda r: str(r["last_activity"]), reverse=True)
    return rows
