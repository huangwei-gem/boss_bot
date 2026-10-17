"""
BOSS 自动回复机器人 - AI 提示词模板

当规则和意图都未命中时，调用 AI 生成回复。
- 系统提示词由个人画像（user_profile.json）动态生成
- 用户提示词包含多轮对话历史，保证回复连贯
"""

import hashlib
import json
import logging
from pathlib import Path

from boss_bot.config import USER_PROFILE
from boss_bot.unified_config import OVERRIDES_FILE, USER_PROFILE_FILE

logger = logging.getLogger(__name__)

# 回 HR 之前至少要看够这段对话最近多少句（2026-10-17 他第五次提这件事，
# 口径从 10-10 的"20 条"抬到"至少上下 30 句"）。
最少对话句数 = 30
# 凑这 30 句最多回看多少行原始消息：实测存档里 16.16% 的行是卡片，
# 120 行足够凑满 30 句，又能挡住几百行的长会话把 token 打满。
历史原始行上限 = 120


def 消息正文(m: dict) -> str:
    """这一行到底说了什么 — text 是空的就退到卡片正文。

    取值顺序和界面、存档一致（text → card_text → block），三处必须一致，否则会出现
    "界面上看得见、AI 提示词里没有"的那种错位：HR 的"我想要一份您的附件简历，您是否同意"
    线上 .text-content 是空的，正文只在 card_text/block 里。以前拼对话历史只读 text，
    11404 行里 1843 行（16.16%）从来没进过提示词——机器答不上话就是这么来的。
    """
    for key in ("text", "card_text", "block"):
        v = (m.get(key) or "").strip()
        if v and v != (m.get("time") or "").strip():
            return v
    return ""


def 对话窗口(messages: list, 最少句数: int = 最少对话句数,
            原始行上限: int = 历史原始行上限) -> list:
    """从最后一条往前取，取够 最少句数 句看得懂的对话就收，最多回看 原始行上限 行。

    按"句"而不是按"行"取：按行截 30 的话，卡片行占掉一成多，模型实际只剩二十来句。
    """
    收 = []
    句 = 0
    for m in reversed(messages or []):
        if len(收) >= 原始行上限:
            break
        收.append(m)
        if 消息正文(m):
            句 += 1
            if 句 >= 最少句数:
                break
    收.reverse()
    return 收


def 对话指纹(messages: list) -> str:
    """这段对话的短指纹——回复缓存的键必须带上它。

    只按"最新消息+称呼+岗位"缓存时，HR 隔一会儿把同一句再问一遍（「在吗」「好的」这种），
    中间对话已经往前走了，模型却被跳过、第一次那句话原样复读出来。
    """
    行 = [f"{'我' if m.get('is_mine') else '对方'}|{消息正文(m)}"
          for m in 对话窗口(messages or []) if 消息正文(m)]
    if not 行:
        return ""
    return hashlib.md5("\n".join(行).encode("utf-8")).hexdigest()[:12]



def _load_overrides() -> dict:
    """加载 config_overrides.json 中的覆盖配置。

    每次生成提示词都重新读盘：前端改的就是这个文件里的 system_rules /
    user_prompt_template，import 时快照一次等于让提示词编辑框变成摆设。
    """
    try:
        if OVERRIDES_FILE.exists():
            with open(OVERRIDES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

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
20. 检查"最近对话记录"中"我:"发过的消息，如果即将回复的内容与之高度相似（如自我介绍、问候语），必须换一种表达或直接不再发送
21. 对方问有没有经验/做过没有，直接答有经验，上面【经历】那一段就是依据（标注、数据分析、AI 工具都做过），不要回"我可以学""没做过但愿意学"这种自减的话——先争取到面试再细谈；但也不要编造上面没有的公司名、年限、项目数字
22. 年龄、学历阶段（几年级/应届/毕业）、工作年限、电话这类个人信息，**只许照【你的背景】里写着的原话说**：背景里是 21 岁就绝不写别的数，背景没写的（比如大几、哪年毕业、有没有证书编号）一律不给数字或阶段名，改说"这个面试时详细聊"。实测机器替使用者答过「我今年25岁」（实际 21），是 HR 翻简历回一句「不是兄弟，你21」才发现的"""

_USER_PROMPT_DEFAULT = """当前聊天上下文：
- 招聘方称呼：{boss_name}
- 招聘岗位：{job_name}
- 最近对话记录：
{history}
- 对方最新消息：{message}

请根据对话历史和最新消息，给出合适的回复。只输出回复内容，不要解释。"""


def _system_rules() -> str:
    return _load_overrides().get("system_rules") or _SYSTEM_RULES_DEFAULT


def user_prompt_template() -> str:
    return _load_overrides().get("user_prompt_template") or _USER_PROMPT_DEFAULT


_PROFILE_CACHE: dict = {"mtime": None, "data": None}


def current_profile() -> dict:
    """当前个人画像（按 mtime 缓存）—— 画像改了，提示词就要跟着改。

    config.USER_PROFILE 是进程启动时的快照，/api/user_profile 只写文件，
    直接用它会让"改了画像但 AI 还在报旧学历/旧方向"。
    """
    try:
        mtime = Path(USER_PROFILE_FILE).stat().st_mtime
    except OSError:
        mtime = None
    if _PROFILE_CACHE["mtime"] != mtime or _PROFILE_CACHE["data"] is None:
        data = None
        try:
            with open(USER_PROFILE_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            data = None
        _PROFILE_CACHE.update(mtime=mtime, data=data or dict(USER_PROFILE))
    return _PROFILE_CACHE["data"]


def build_system_prompt(profile: dict = None) -> str:
    """根据个人画像生成系统提示词"""
    p = profile or current_profile()
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
    if p.get("age"):
        # 年龄只有这一个数：2026-10-08 机器在朱鑫磊那一单答了「我今年25岁」，
        # 本人实际 21，是 HR 翻简历回「不是兄弟，你21」才发现的（何女士那单答成 23）。
        background_lines.append(f"- 年龄：{p['age']}岁")
    if p.get("experience"):
        background_lines.append(f"- 经历：{p['experience']}")
    background_lines.append(f"- 期望薪资：{p.get('salary_expectation', '面议')}")
    background_lines.append(f"- 可面试时间：{p.get('available_interview_time', '工作日')}")
    if highlights:
        background_lines.append(f"- 个人特点：{highlights}")

    return (
        "你是一个正在找工作的求职者，正在 BOSS 直聘上与招聘方（HR/Boss）聊天。\n\n"
        "你的背景：\n" + "\n".join(background_lines) + "\n\n" + _system_rules()
    )


def build_conversation_history(messages: list) -> str:
    """将消息列表格式化为对话记录文本（卡片行按 card_text/block 取正文并标出是卡片）"""
    lines = []
    for m in messages or []:
        正文 = 消息正文(m)
        if not 正文:
            continue
        role = "我" if m.get("is_mine") else "对方"
        标 = "[卡片] " if not (m.get("text") or "").strip() else ""
        lines.append(f"{role}: {标}{正文}")
    return "\n".join(lines) if lines else "（无历史消息）"


def _exclude_latest_message(messages: list, latest_text: str) -> list:
    """从消息列表中排除最新一条对方消息（避免在 history 和 message 中重复显示）。

    从后往前找到第一条非己方、且正文与 latest_text 相同的消息，排除它。
    正文一律走 消息正文()：最新那句常常就是张卡片（text 是空的），
    只比 text 会一条都排不掉，于是同一句在提示词里出现两遍。
    如果未找到匹配项，返回原列表（保持向后兼容）。
    """
    if not messages or not latest_text:
        return messages
    latest_text = latest_text.strip()
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if not msg.get("is_mine") and 消息正文(msg) == latest_text:
            return messages[:i] + messages[i + 1:]
    return messages


def build_user_prompt(boss_name: str, job_name: str, message: str,
                      messages: list = None, exclude_latest: bool = True,
                      追加指令: str = "") -> str:
    """构建用户提示词（含多轮历史）

    Args:
        boss_name: 招聘方称呼
        job_name: 招聘岗位
        message: 对方最新消息
        messages: 消息列表 [{"text","card_text","block","is_mine","time"}]
        exclude_latest: 是否从历史中排除最新消息（避免在 history 和 message 中重复显示）
        追加指令: 这次要它干什么（被动回复留空；主动跟进要说清"对方沉默了"）
    """
    history_messages = messages
    if exclude_latest and messages and message:
        history_messages = _exclude_latest_message(messages, message)
    history_messages = 对话窗口(history_messages)
    历史 = build_conversation_history(history_messages)
    句数 = sum(1 for 行 in 历史.splitlines() if 行.startswith(("对方:", "我: ")))
    logger.info("[对话窗口] 这次带进提示词 %d 句（口径至少 %d 句，最多回看 %d 行）",
                句数, 最少对话句数, 历史原始行上限)
    提示词 = user_prompt_template().format(
        boss_name=boss_name or "HR",
        job_name=job_name or current_profile().get("position", "目标岗位"),
        history=历史,
        message=message,
    )
    return 提示词 + ("\n\n" + 追加指令 if 追加指令 else "")

