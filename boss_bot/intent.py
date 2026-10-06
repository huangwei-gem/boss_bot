"""
BOSS 自动回复机器人 - 意图分类模块

基于正则模式识别对方消息的意图，比单纯关键词匹配更精准：
- 区分"对方询问薪资"与"我方报价"等语义差异
- 意图优先级：邀约面试 > 询问薪资 > 要简历 > 约面试时间 > 问工作内容 > 要联系方式 > 报价 > 问候
"""

import re
from typing import Optional

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

