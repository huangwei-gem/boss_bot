"""
BOSS 自动回复机器人 - 关键词规则模块

定义关键词到回复内容的匹配规则。
规则引擎会按优先级匹配，第一个命中的规则生效。

疑问/否定上下文过滤：
"是否投递过简历"、"不用发简历" 这类消息虽然包含关键词，
但语义是询问/否定，不应触发动作。
"""

import re
from typing import Optional, Tuple
from boss_bot.config import REPLY_RULES

# 关键词前缀窗口内的疑问/否定标记（命中则跳过该规则）
_QUESTION_NEGATIVE_RE = re.compile(
    r"(是否|可有|有没有|需不需要|要不要|用不用|不用|无需|不必|还没|尚未|没投|别投|先不|暂不|不用了)"
)
# 关键词前缀检查窗口长度（字符）
_CONTEXT_WINDOW = 10


class RuleEngine:
    """关键词规则引擎"""

    def __init__(self, rules: dict = None):
        self.rules = rules or REPLY_RULES
        # 编译正则，提高匹配效率
        self._compiled = {
            keyword: re.compile(re.escape(keyword), re.IGNORECASE)
            for keyword in self.rules
        }

    def match(self, message: str) -> Optional[Tuple[str, str]]:
        """
        匹配消息内容，返回 (动作类型, 回复内容)
        动作类型: 'text' 表示直接发文字, 'resume' 表示发送简历
        未匹配返回 None

        疑问/否定上下文中的关键词会被跳过（如"是否投递过简历"）。
        """
        if not message:
            return None
        for keyword, response in self.rules.items():
            for m in self._compiled[keyword].finditer(message):
                prefix = message[max(0, m.start() - _CONTEXT_WINDOW):m.start()]
                if _QUESTION_NEGATIVE_RE.search(prefix):
                    continue  # 疑问/否定上下文，跳过本次命中
                if response == "send_resume":
                    return ("resume", None)
                return ("text", response)
        return None

    def add_rule(self, keyword: str, response):
        """动态添加规则"""
        self.rules[keyword] = response
        self._compiled[keyword] = re.compile(re.escape(keyword), re.IGNORECASE)

    def remove_rule(self, keyword: str):
        """移除规则"""
        self.rules.pop(keyword, None)
        self._compiled.pop(keyword, None)
