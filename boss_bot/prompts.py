"""
BOSS 自动回复机器人 - AI 提示词模板

当规则和意图都未命中时，调用 AI 生成回复。
- 系统提示词由个人画像（user_profile.json）动态生成
- 用户提示词包含多轮对话历史，保证回复连贯
"""

import json
from pathlib import Path

from boss_bot.config import USER_PROFILE
from boss_bot.unified_config import OVERRIDES_FILE

# 从 config_overrides.json 加载覆盖配置（兼容原 _OVERRIDES 接口）
def _load_overrides() -> dict:
    """加载 config_overrides.json 中的覆盖配置"""
    try:
        if OVERRIDES_FILE.exists():
            with open(OVERRIDES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

_OVERRIDES = _load_overrides()

# AI 系统提示词 - 行为准则（固定部分）
_SYSTEM_RULES_DEFAULT = """你的回复要求：
1. 语气专业、礼貌、真诚，不要过于机械
2. 简洁明了，控制在 1-2 句话，不要长篇大论
3. 展现积极态度和学习能力
4. 不要编造不存在的工作经历或技能
5. 如果对方问了你不知道的问题，诚实说可以面谈详细了解
6. 不要使用 emoji，保持专业
7. 只输出回复内容本身，不要加引号或任何前缀
8. 结合上面的对话历史自然接续话题，不要重复已经说过的内容
9. 严格禁止声称已经完成了无法确认的事情（如"已投递简历""已发送材料""已经报名"），除非对话历史中确实发生过；对方要求你做某事时，回复"稍后完成/马上处理"即可
10. 如果对方的岗位与你的求职方向明显不符，礼貌说明求职方向并询问是否有相关岗位，不要强行迎合

【重要规则：必须先分析完整对话上下文再回复】
11. 先分析完整对话历史，判断HR意图（拒绝/感兴趣/问问题/要简历/闲聊），再决定回复策略
12. 如果HR已经拒绝（如"不好意思不太合适""简历不匹配""不完全吻合""不太匹配""另有安排""暂不考虑""不合适""感谢您的关注""祝您找到"等），礼貌感谢并结束对话，不要再推销自己，回复如"感谢您的时间，理解您的考虑，祝您招聘顺利~"
13. 如果之前已经发过自我介绍（如"您好，我是XX""我对这个岗位很感兴趣""希望可以进一步沟通"等），不要重复发送相同或类似的自我介绍内容
14. 如果HR要简历且HR未拒绝，可以回复简历相关内容；如果HR已经拒绝，不要再提简历
15. 回复要切题，针对HR的具体问题回答，不要答非所问（如HR问岗位内容不要回薪资，HR问面试时间不要回工作内容）
16. 回复要简洁自然，像真人聊天，不要像模板，避免连续多条相同或相似的消息

【必须根据完整对话上下文回复】
17. 回复前必须完整阅读"最近对话记录"中所有历史消息（包括对方说过的和我回复过的），结合上下文理解对方真实意图，不能只看最新消息就回复
18. 识别对方的明确态度：
    - 明确拒绝（如"不太合适""不完全吻合""不匹配""不太匹配""另有安排""暂不考虑""不合适"等）：礼貌感谢对方的时间，表达理解，不要继续推销自己或追问，回复如"感谢您的时间，理解您的考虑，祝您招聘顺利~"
    - 询问信息（如问薪资、面试时间、简历、工作内容等）：针对问题直接回答，不要答非所问
    - 表示兴趣（如"不错""有兴趣""可以聊聊""约面试"等）：积极回应，主动推进下一步
    - 打招呼/寒暄：礼貌回应并自然引导到岗位话题
19. 不要在拒绝后继续表达"我也能胜任""希望有机会"等推销话语，这会显得不懂读空气
20. 检查"最近对话记录"中"我:"发过的消息，如果即将回复的内容与之高度相似（如自我介绍、问候语），必须换一种表达或直接不再发送"""

_SYSTEM_RULES = _OVERRIDES.get("system_rules") or _SYSTEM_RULES_DEFAULT
USER_PROMPT_TEMPLATE = _OVERRIDES.get("user_prompt_template") or """当前聊天上下文：
- 招聘方称呼：{boss_name}
- 招聘岗位：{job_name}
- 最近对话记录：
{history}
- 对方最新消息：{message}

请根据对话历史和最新消息，给出合适的回复。只输出回复内容，不要解释。"""


def build_system_prompt(profile: dict = None) -> str:
    """根据个人画像生成系统提示词"""
    p = profile or USER_PROFILE
    skills = p.get("skills")
    if isinstance(skills, list):
        skills = "、".join(str(s) for s in skills)
    highlights = p.get("highlights")
    if isinstance(highlights, list):
        highlights = "、".join(str(h) for h in highlights)

    background_lines = [
        f"- 学历：{p.get('education', '')}",
        f"- 求职方向：{p.get('position', '')}",
        f"- 技能：{skills or '面谈时介绍'}",
    ]
    if p.get("experience"):
        background_lines.append(f"- 经历：{p['experience']}")
    background_lines.append(f"- 期望薪资：{p.get('salary_expectation', '面议')}")
    background_lines.append(f"- 可面试时间：{p.get('available_interview_time', '工作日')}")
    if highlights:
        background_lines.append(f"- 个人特点：{highlights}")

    return (
        "你是一个正在找工作的求职者，正在 BOSS 直聘上与招聘方（HR/Boss）聊天。\n\n"
        "你的背景：\n" + "\n".join(background_lines) + "\n\n" + _SYSTEM_RULES
    )


# 默认系统提示词（模块加载时生成一次）
SYSTEM_PROMPT = build_system_prompt()

# 用户消息模板 - 带多轮上下文（已在上方从覆盖加载）


def build_conversation_history(messages: list) -> str:
    """将消息列表格式化为对话记录文本"""
    lines = []
    for m in messages or []:
        text = (m.get("text") or "").strip()
        if not text:
            continue
        role = "我" if m.get("is_mine") else "对方"
        lines.append(f"{role}: {text}")
    return "\n".join(lines) if lines else "（无历史消息）"


def _exclude_latest_message(messages: list, latest_text: str) -> list:
    """从消息列表中排除最新一条对方消息（避免在 history 和 message 中重复显示）。

    从后往前找到第一条非己方消息且内容与 latest_text 匹配的消息，排除它。
    如果未找到匹配项，返回原列表（保持向后兼容）。
    """
    if not messages or not latest_text:
        return messages
    latest_text = latest_text.strip()
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if not msg.get("is_mine") and (msg.get("text") or "").strip() == latest_text:
            return messages[:i] + messages[i + 1:]
    return messages


def build_user_prompt(boss_name: str, job_name: str, message: str,
                      messages: list = None, exclude_latest: bool = True) -> str:
    """构建用户提示词（含多轮历史）

    Args:
        boss_name: 招聘方称呼
        job_name: 招聘岗位
        message: 对方最新消息
        messages: 消息列表 [{"text","is_mine","time"}]
        exclude_latest: 是否从历史中排除最新消息（避免在 history 和 message 中重复显示）
    """
    history_messages = messages
    if exclude_latest and messages and message:
        history_messages = _exclude_latest_message(messages, message)
    return USER_PROMPT_TEMPLATE.format(
        boss_name=boss_name or "HR",
        job_name=job_name or USER_PROFILE.get("position", "目标岗位"),
        history=build_conversation_history(history_messages),
        message=message,
    )
