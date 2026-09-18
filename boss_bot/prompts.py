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
10. 如果对方的岗位与你的求职方向明显不符，礼貌说明求职方向并询问是否有相关岗位，不要强行迎合"""

_SYSTEM_RULES = _OVERRIDES.get("system_rules", _SYSTEM_RULES_DEFAULT)
USER_PROMPT_TEMPLATE = _OVERRIDES.get("user_prompt_template", """当前聊天上下文：
- 招聘方称呼：{boss_name}
- 招聘岗位：{job_name}
- 最近对话记录：
{history}
- 对方最新消息：{message}

请根据对话历史和最新消息，给出合适的回复。只输出回复内容，不要解释。""")


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


def build_user_prompt(boss_name: str, job_name: str, message: str,
                      messages: list = None) -> str:
    """构建用户提示词（含多轮历史）"""
    return USER_PROMPT_TEMPLATE.format(
        boss_name=boss_name or "HR",
        job_name=job_name or USER_PROFILE.get("position", "目标岗位"),
        history=build_conversation_history(messages),
        message=message,
    )
