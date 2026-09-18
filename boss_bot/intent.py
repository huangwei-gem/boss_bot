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
