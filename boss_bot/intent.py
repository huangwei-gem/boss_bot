"""
BOSS 自动回复机器人 - 意图分类模块

基于正则模式识别对方消息的意图，比单纯关键词匹配更精准：
- 区分"对方询问薪资"与"我方报价"等语义差异
- 意图优先级：邀约面试 > 询问薪资 > 要简历 > 约面试时间 > 问工作内容 > 要联系方式 > 报价 > 问候
"""

import re
from typing import Optional

from boss_bot.unified_config import TITLE_VETO_EXEMPT_KEYWORDS

# 意图 -> 正则模式列表（按优先级排序）
INTENT_PATTERNS = {
    "invite_interview": [
        r"邀.{0,3}面试",
        r"约个?.{0,3}(面试|时间|电话)",
        r"(来|到)(公司|我们这|线下|现场)",
        r"(明天|后天|本周|下周|周[一二三四五六日天]).{0,10}(面试|方便|有空|聊聊|沟通|到)",
        r"(方便|可以|愿意).{0,6}(面试|来公司|线下)",
        r"视频面试",
        r"电话.{0,3}沟通.{0,3}(吗|吧)",
    ],
    "ask_salary": [
        r"(你们|公司|岗位|职位|这边).{0,8}(薪资|工资|待遇|预算|报酬|range|范围)",
        r"(薪资|工资|待遇|预算|报酬).{0,4}(多少|范围|怎么样|如何|什么)",
        r"(这个|该)岗位.{0,6}(给|开|出)",
        r"(最高|最多).{0,4}(给|开|到)",
    ],
    "ask_resume": [
        r"发.{0,4}简历",
        r"看看.{0,4}简历",
        r"简历.{0,4}(发|看|收|给)",
        r"收(到|取)?(一份)?简历",
        r"(想|要|方便).{0,6}简历",
    ],
    "ask_interview": [
        r"(什么时候|哪天|哪会|几时).{0,8}(面试|方便|有空)",
        r"(想|想跟|和您).{0,6}(约|安排).{0,4}(面试|时间|电话)",
    ],
    "ask_job_content": [
        r"工作内容",
        r"岗位职责",
        r"(主要|平时|日常|具体)?(做|干|负责)(些什么|什么|啥)",
        r"岗位.{0,3}(介绍|详情|情况)",
    ],
    "contact_request": [
        r"(加|留|交换).{0,4}(微信|vx|wx|v信)",
        r"(微信|电话|手机号?|联系方式).{0,4}(多少|是|吗|留|给|方便)",
        r"联系(方式|一下)",
    ],
    "tell_salary": [
        r"(薪资|工资|待遇|月薪|年薪|底薪|base).{0,6}(是|为|在)?\s*[0-9０-９]+",
        r"[0-9０-９]+\s*[Kk千]\s*([-—~至到]\s*[0-9０-９]+\s*[Kk千])?",
    ],
    "greeting": [
        r"^您好?[,，。!！？?~\s]*$",
        r"^在(吗|不在|么|嘛)[?？]?[,，。!！~\s]*$",
        r"^(hi|hello|hey)[!！~.。\s]*$",
        r"^(早上|下午|晚上)好[,，。!！~\s]*$",
        r"^你好呀?[,，。!！~\s]*$",
    ],
}

# 预编译
_COMPILED = {
    intent: [re.compile(p, re.IGNORECASE) for p in patterns]
    for intent, patterns in INTENT_PATTERNS.items()
}

# 意图优先级（从前到后）
INTENT_PRIORITY = [
    "invite_interview",
    "ask_salary",
    "ask_resume",
    "ask_interview",
    "ask_job_content",
    "contact_request",
    "tell_salary",
    "greeting",
]


def classify(message: str) -> str:
    """
    识别消息意图，返回意图名，未识别返回 'other'。
    按优先级顺序匹配，第一个命中的意图生效。
    """
    if not message or not message.strip():
        return "other"
    text = message.strip()
    for intent in INTENT_PRIORITY:
        for pattern in _COMPILED[intent]:
            if pattern.search(text):
                return intent
    return "other"


# ── "发简历"这个动作的专用判据 ─────────────────────────────────────
# ask_resume 那组正则太宽（r"简历.{0,4}(发|看|收|给)" 连"简历已收到"都命中），
# 而它下游挂的是一个真实动作：点开发简历按钮把附件塞过去。判错的代价不是
# 说错一句话，是把简历发给一个刚刚拒绝过我们的人。
#
# 判据取自盘上 105 个会话里 26 条会命中 resume 的最新 HR 消息：真在要的 11 条
# 全部是"发/送 + 简历"或那张"我想要一份您的附件简历"卡片；另外 15 条只是提到
# 简历（已收到、看过了、不匹配、内推链接，以及我们自己发出去之后的系统确认卡）。
_RESUME_ASK_PATTERNS = [
    r"(发|送).{0,8}简历",                      # 发一份简历 / 发我一份简历 / 可以发份简历
    r"简历.{0,4}(发|给).{0,3}(我|过来|您|你)",  # 简历发我 / 简历发过来
    r"(想|要).{0,4}一份.{0,6}简历",            # HR 卡片：我想要一份您的附件简历
    r"(看看|看下|提供|上传).{0,4}简历",
]

# 出现这些字样就说明"简历"是过去式或客套，不是索取
_RESUME_NOT_ASK_MARKS = (
    "已发送", "已收到", "收到", "看过", "看了", "印象", "初筛", "同步给",
    "不符合", "不合适", "不匹配", "内推", "链接", "http",
)


def is_resume_request(message: str) -> bool:
    """这句话是不是真的在向我要附件简历。"""
    text = (message or "").strip()
    if not text:
        return False
    if any(k in text for k in _RESUME_NOT_ASK_MARKS):
        return False
    return any(re.search(p, text) for p in _RESUME_ASK_PATTERNS)


# BOSS 的交换联系方式卡片：标题一律是"我想要和您交换微信/一个您的电话号码，您是否同意"，
# 下面挂着 拒绝 / 同意 两个按钮（读回来时按钮文字会并进同一句 card_text）
_CONTACT_CARD_MARKS = ("交换微信", "交换电话", "您的电话号码", "您的微信号")


# 否定式：HR 写"（线上）不坐班""无需坐班""不需要坐班"是在保证没有这条限制，
# 不是在提这条限制。盘上 1416 个标题里「坐班」命中 14 处，9 处是否定式，
# 而那 9 个全是用户点名要的那一类（线上短视频剪辑、线上私域运营、
# 远程办公/不坐班分析师、线上塔罗师）。
# 窗口只放 不/无/没 后面跟几个虚词，不能整段往前扫："不露脸主播"里那个"不"
# 隔了两个字，要也算否定，主播岗就又被放回来了（用户明确要拒的那一类）。
# "非中介"同样不在这儿：派遣/工厂岗最爱挂"直招非中介"，那条照旧要拦。
# BOSS 会把广告敏感的字拆开写（"免 费 提 供"），盘上还见过插不可见分隔符的
# 「哈⁢啰⁢出⁢行」——不抹掉这些，一条骑手岗的标题看着就不像骑手岗。
_SEP_RE = re.compile(r"[\s\u00a0\u200b-\u200f\u2060-\u2064\ufeff]+")


def _tight(text) -> str:
    return _SEP_RE.sub("", str(text or ""))


_NEGATED_RE = re.compile(r"[不无没][得有要需用不没]{0,2}$")
# 小句开头的"不用/无需/没有/不需要"管到整句：「全程远程协作，无需到公司坐班」
# 里那个"坐班"离否定词隔了四个字，就近窗口够不着，但意思仍然是否定。
# 只认 不/无/没 + 虚词 这一种开头，"不露脸主播"那种描述性的不算。
_CLAUSE_SEPS = "，,。.;；、!！?？:：\n（）()【】[]/\\|｜~～ "
_NEGATED_CLAUSE_RE = re.compile(r"^[不无没][得有要需用]")


def _negated(body: str, idx: int) -> bool:
    """命中位置前面是不是否定说法（就近窗口，或所在小句以否定词开头）。"""
    if _NEGATED_RE.search(body[:idx]):
        return True
    start = max([body.rfind(sep, 0, idx) for sep in _CLAUSE_SEPS] + [-1]) + 1
    return bool(_NEGATED_CLAUSE_RE.match(body[start:idx]))


def keyword_hit(words, text: str, honor_negation: bool = True) -> str:
    """这段文本里出现了哪一条关键词（没有返回空串）。

    口径只有一份：否决词要求整条出现，面板上把"主播，地推"输成一格就该拆成两条，
    所以这里不做分词、不做模糊；只抹掉 BOSS 的隔字写法（_tight），
    外加认否定式（_negated）这一种语法变化。
    同一个词在一段里可能出现两次（"不用坐班，但周末要坐班"），逐处看，
    有一处不是否定式就算命中。

    honor_negation=False 给岗位类型表用：那里的否定式不改变岗位性质——
    "不露脸主播""无需坐班的普工"还主播、还是普工；"非中介"更是派遣岗的卖点。
    """
    body = _tight(text)
    if not body or not words:
        return ""
    for kw in words:
        word = _tight(kw)
        if not word:
            continue
        start = 0
        while True:
            i = body.find(word, start)
            if i < 0:
                break
            if not honor_negation or not _negated(body, i):
                return str(kw).strip()
            start = i + len(word)
    return ""


def title_veto_hit(title_keywords, title: str) -> str:
    """岗位标题里的岗位类型否决词（普工/主播/快递/保洁这一类），命中返回那一条。

    只量标题是有道理的：类型词写在 JD 正文里是顺带一提（"标注快递物流场景的录音"），
    写在标题里才是这份工本身。标题同时带用户点名要的职业词（"直播运营助理"
    "电商客服"）时放行——那种是挂着类型皮的线上活，见
    unified_config.TITLE_VETO_EXEMPT_KEYWORDS。
    """
    body = _tight(title)
    if not body or not title_keywords:
        return ""
    if any(_tight(word) in body for word in TITLE_VETO_EXEMPT_KEYWORDS):
        return ""
    return keyword_hit(title_keywords, body, honor_negation=False)


def veto_hit_anywhere(title_keywords, body_keywords, title: str = "",
                      text: str = "") -> str:
    """这一单是不是"不要的那一类"，命中就返回命中的那个词（回复/卡片/跟进共用）。

    标题过两遍表：岗位类型词（普工/主播/快递/保洁）+ 正文否决词（包吃住/需坐班/到店）。
    后者一旦写进标题就不是顺带一提了——2026-10-07 11:57 那条
    「【白班坐岗】28/H包吃住+可预支+不体检（派遣职位）」正是只过了第一遍表，
    才被主动跟进追问了一句"还在招人吗"。
    消息正文只过否决词那一遍：类型词写进 HR 的话里常是"要标注快递场景的录音"这种
    内容，量正文会误杀；否决词本来就是"要坐班/包吃住"的说法，出现在哪一句都算数。
    """
    return (title_veto_hit(title_keywords, title)
            or keyword_hit(body_keywords, title)
            or keyword_hit(body_keywords, text))


def is_contact_exchange_card(message: str) -> bool:
    """这句话是不是那张可以点"同意"的交换联系方式卡片。

    判据要窄：HR 在正文里顺口说"加个微信聊"、平台那句
    "为保障您的安全建议在平台内沟通，微信沟通中需特别保护您的…"的安全提示，
    都不是卡片 —— 对着它们点"同意"等于凭空找一个不存在的按钮。
    """
    text = (message or "").strip()
    if not text or "是否同意" not in text:
        return False
    return any(k in text for k in _CONTACT_CARD_MARKS)


# 面试邀请分现场/线上用字样。BOSS 的邀请卡片实测一律写成"邀请您现场面试"
# （messages/ 里 7 条全是这个形状），日程页那一栏直接印"线下面试"；
# 视频/线上/远程是要留下的那一头，所以"现场/线下"优先判。
_OFFLINE_INTERVIEW_MARKS = ("现场面试", "线下面试", "到面", "到场", "来公司", "面试地点",
                            "过来面试", "上门面试", "到店面试")
_ONLINE_INTERVIEW_MARKS = ("视频面试", "线上面试", "远程面试", "电话面试",
                           "腾讯会议", "钉钉会议", "zoom", "飞书会议")


def classify_interview_invite(message: str) -> str:
    """这条面试邀请是要人到场的、还是远程的、还是说不清。

    返回 "offline" / "online" / "unknown"。判错一次就是当着 HR 的面把人家发的面试撤掉，
    所以说不清的（只写"邀请您面试"）一律 unknown，不自动点拒绝。
    """
    text = (message or "").strip()
    if not text or "面试" not in text:
        return "unknown"
    if any(k in text for k in _OFFLINE_INTERVIEW_MARKS):
        return "offline"
    if any(k in text.lower() for k in _ONLINE_INTERVIEW_MARKS):
        return "online"
    return "unknown"

