"""
BOSS 自动回复机器人 - AI 提示词模板

当规则和意图都未命中时，调用 AI 生成回复。
- 系统提示词由个人画像（user_profile.json）动态生成
- 用户提示词包含多轮对话历史，保证回复连贯
"""

import hashlib
import json
import logging

from boss_bot.config import USER_PROFILE
from boss_bot.unified_config import OVERRIDES_FILE, USER_PROFILE_FILE
from boss_bot.untrusted import injection_hint, sanitize_untrusted, untrusted_note

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


# 模型把"它的决定"写成了正文——2026-10-23 12:56:40 真发出去过一句
# 「（无需回复，对话已自然结束）」（logs/boss_bot.log.2026-10-23:23721「已发送文字」）。
# 这种句子不该再被当成聊天历史喂回模型：那句发出去之后进了存档，10-24 13:27 的提示词里
# 就带着「我: （无需回复，对话已自然结束）」，跟进腿更把它当成"对方最新消息"记进记录。
# 发送闸门（reply_engine._format_rejection）从今往后不再发它，但盘上已经发出去的那些
# 还躺在存档里——用户数据不删，就在拼上下文这一处把它挡掉。
_旁白说法 = ("无需回复", "不用回复", "不需要回复", "已自然结束", "不再回复",
            "不回复了", "无需发送", "不用发送", "（完）", "(完)")
# 这四个词只在句子开头才是"旁白"：「应该回复」「建议回复」夹在句中是完全正常的人话
# （「这个我应该回复您一下」），整句都比对会把要回的话拦掉。
_旁白开头 = ("回复如下", "我的回复是", "应该回复", "建议回复")


def 旁白(句: str) -> str:
    """这句是旁白（在谈论"回不回"这件事本身），不是要发给 HR 的话；命中就返回那一条。"""
    文 = str(句 or "").strip()
    if not 文:
        return ""
    hit = next((k for k in _旁白说法 if k in 文), None)
    if hit:
        return f"旁白说法「{hit}」"
    hit = next((k for k in _旁白开头 if 文.startswith(k)), None)
    if hit:
        return f"以旁白开头「{hit}」"
    if (len(文) <= 40 and 文[0] in "（([" and 文[-1] in "）)]"
            and 文.count("（") == 1 and 文.count("(") == 0):
        return f"整句裹在括号里「{文[:24]}」"
    return ""


def _是旁白(m: dict) -> bool:
    return bool(旁白(消息正文(m)))


def 对话窗口(messages: list, 最少句数: int = 最少对话句数,
            原始行上限: int = 历史原始行上限) -> list:
    """从最后一条往前取，取够 最少句数 句看得懂的对话就收，最多回看 原始行上限 行。

    按"句"而不是按"行"取：按行截 30 的话，卡片行占掉一成多，模型实际只剩二十来句。
    旁白行不进上下文，也不占那 30 句的名额。
    系统卡（"对方已查看了您的附件简历"那一类，存档里 is_system=True）同样不占名额：
    现盘 2779 份会话实测，窗口里 13452 句有 651 句（4.8%）是这种平台回执，364 个会话
    吃到过——它们既不是 HR 说的话，也不该把 HR 那三十句挤出去。行本身留着，
    因为"她看过简历了"是有用的事实，由 build_conversation_history 标成「系统:」。
    """
    收 = []
    句 = 0
    for m in reversed(messages or []):
        if len(收) >= 原始行上限:
            break
        正文 = 消息正文(m)
        if 正文 and _是旁白(m):
            continue          # 旁白既不占回看的那 120 行，也不凑进那 30 句
        收.append(m)
        if 正文 and not m.get("is_system"):
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

# 反例放最前：短块比长编号规则更管用（26 条编号规则会把每条当硬约束去套，反而写出客服腔）。
# 三组红线各锁一条单测：不编个人信息、拒绝别推销、别复述（tests/test_reply_tone.py）。
_SYSTEM_RULES_DEFAULT = """你在 BOSS 直聘上替一个大学生找工作，跟 HR 打字聊天。照下面说的，别写成客服。

先看两条写砸的：
❌「您好，感谢您的介绍。不过我的求职方向是数据分析，跟这个岗位不太匹配。请问贵司是否有数据分析相关岗位在招呢？」
✅「我就是想找数据整理这类的，这个岗合适我把简历发您看下？」
❌「您刚才提到的薪资问题，我的期望薪资为面议，具体可面谈。」
✅「薪资面谈就行，倒是想先问下这个岗具体做什么？」

一句话回答人家问的，最多三句，带一两个字（哈、嗯、好嘞、在的）就自然了。「您好」一条对话用一次就够，「感谢您的来信」「期待与您进一步沟通」「静候佳音」这类客服话一个都不写。不复述对方刚说过的话，也不总结前面聊了什么——直接接话往下说。不加编号、不分点、不用「首先其次」，不发 emoji，不加引号前缀。

下面的话只许照【你的背景】里写的说：年龄、学历（大几/应届/毕业）、工作年限、电话。背景写 21 就绝不写别的数；背景没写的（大几、哪年毕业、证书编号）一律不给数字，改说「这个面试时详细聊」。做过的事才说做过，别回「我可以学」这种自贬的话。对方问有没有经验，直接答有经验——标注、数据分析、Excel/SQL/Python 都做过。

对方说不合适（不匹配/不太合适/暂不考虑/另有安排/祝您找到…），回一句收尾就停，别再推销。收尾那句里可以顺口请教一句「主要是差在哪一条」——这段对话里问过就绝不问第二遍，也别在「岗位已招满/暂停招聘」这种话上问（那不是对你的评价，问了等于自言自语）。对方已经拒绝过，就不再提简历，也不说「希望有机会」。之前说过的话不重复发，自我介绍别来第二遍。

不知道的就说「这个面谈时细聊」；没投递、没发送过的动作别说「已投递/已发送」，对方催就回「稍后处理」。这一轮只回文字、没点那张卡片，就不说「已同意交换」「已点了」，想说就回「我这就点一下」。加微信是在黄维自己手机上做的事，机器做不了，别说「我这就加您」「马上加您」「我马上添加」，收到号就说「微信号我记下了」。钉钉、企业微信里「我这边已经通过您的好友申请了」同理——那一下不是机器点的，别说「已通过」，让对方自己去看。表没打开过就不说「我来填一下」「刚去填了」；别的岗没投就不说「投完跟您说」；视频剪不出来就不说「剪好发您」，想接话就回「这个我确认后回复您」。只有真的要做、而且机器做得到的事，才可以答应。岗位方向明显不对，说一句「我主要找线上数据分析这类的」，别硬凑。只回这句话本身，别的都别写。"""

_USER_PROMPT_DEFAULT = """当前聊天上下文：
- 招聘方称呼：{boss_name}
- 招聘岗位：{job_name}
- 最近对话记录：
{history}
- 对方最新消息：{message}

请根据对话历史和最新消息，给出合适的回复。只输出回复内容，不要解释。
直接像平时打字那样回这一条：别复述他刚说的话，也别总结前面聊了什么，只说你要说的话。"""


# 模板腔判据：客观字符串，不是"看着像"。两族来路都取自盘上真发出去过的句子：
# ① 把这段对话念出来（"您刚才说的/针对您的问题"）——2026-10-17 他明确说
#    「记得不要回复上下文过程」；
# ② 书面敬语堆砌（"您好，感谢您的介绍…"）——同一句「我看有好多的人说我像机器人了」，
#    test_reply_output_guard.NORMAL 里这种开头占了大半。
# 刻意不放「感谢您的时间」：拒单那句收尾就是它，拦了就没话可回。
复述腔 = ("您刚才说", "您刚才提", "您提到的", "针对您的问题", "针对您提到",
          "根据我们的对话", "根据上面的对话", "结合我们的沟通", "根据我们刚才",
          "根据您刚才", "关于您说的", "关于您提到", "关于您刚才")
书面腔 = ("感谢您的来信", "感谢您的回复", "感谢您的介绍", "感谢您的信任",
          "感谢您的消息", "非常感谢您的", "期待与您", "敬候佳音", "顺祝商祺",
          "烦请", "谨此")


def 机器人腔(句: str) -> str:
    """这句像不像客服模板腔：命中就返回命中的那一条（空串 = 像人打的）。

    生成后拿它兜一道，命中就换说法再问一次（只重问一次，见 reply_engine._ask_ai）。
    """
    文 = str(句 or "").strip()
    if not 文:
        return ""
    头 = 文[:40]
    for 词 in 复述腔:
        if 词 in 头:
            return f"把上下文念出来了（{词}）"
    for 词 in 书面腔:
        if 词 in 文:
            return f"书面敬语腔（{词}）"
    return ""


def _system_rules() -> str:
    return _load_overrides().get("system_rules") or _SYSTEM_RULES_DEFAULT


def user_prompt_template() -> str:
    return _load_overrides().get("user_prompt_template") or _USER_PROMPT_DEFAULT


def current_profile(account_index=None) -> dict:
    """当前个人画像 —— 每次现读盘，跟 `_load_overrides()` 同一个口径。

    config.USER_PROFILE 是进程启动时的快照，/api/user_profile 只写文件，
    直接用它会让"改了画像但 AI 还在报旧学历/旧方向"。
    以前这里按 mtime 缓存过一份，Windows 的 mtime 颗粒是系统时钟那一格（约 15.6 毫秒）：
    同一格里"写完立刻读"会拿回上一份内容，而缓存只记 mtime 不记路径——两个不同文件
    撞在同一个 mtime 上也会串数据（10-29 全量回归里 `test_改完画像立刻生效` 就是这么随机红的）。
    这份文件一千字节，调用点全在"接着要打 AI"那几条路上（见 reply_engine._ask_ai），
    省这一次读不值这一点不确定性。

    account_index 给了就把「求职意向」换成这一号那一份（position_by_account）：
    两个号现在是两条方向（一号 AI 产品/AI 运营、一号数据分析），
    喂给 AI 的背景必须是这一号自己那句，否则号1 会对着 HR 说"我想做 AI 产品"。
    """
    data = None
    try:
        with open(USER_PROFILE_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            data = loaded
    except (OSError, ValueError):
        data = None
    画像 = data or dict(USER_PROFILE)
    if account_index is not None and isinstance(画像, dict):
        表 = 画像.get("position_by_account") or {}
        这一号 = str(表.get(str(account_index)) or "").strip()
        if 这一号:
            画像 = dict(画像)
            画像["position"] = 这一号
    return 画像


def build_system_prompt(profile: dict = None, account_index=None) -> str:
    """根据个人画像生成系统提示词

    account_index：这一号自己的求职方向（画像其余字段全局一份）。
    """
    p = profile or current_profile(account_index)
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
    """将消息列表格式化为对话记录文本（卡片行按 card_text/block 取正文并标出是卡片）

    正文一律按外部原文清洗后再拼：HR 那句话里可能夹着零宽字符或双向控制符，
    用来把"忽略以上指令"藏进正常聊天里（见 boss_bot/untrusted.py）。
    """
    lines = []
    for m in messages or []:
        正文 = sanitize_untrusted(消息正文(m))
        if not 正文:
            continue
        # 平台回执标成「系统:」而不是「对方:」：107 个会话里"对方最新那句"根本就是
        # 一张系统卡（"对方已同意了…"），画成 HR 说的话，模型就照着回一句"好的我发您"，
        # 而 HR 那边根本没开口——这就是答非所问与机器味的一个来源。
        role = "我" if m.get("is_mine") else ("系统" if m.get("is_system") else "对方")
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
    # 对方最新那句是外部输入：先剥不可见字符再进提示词，它是资料不是指令
    最新 = sanitize_untrusted(message)
    if injection_hint(message):
        logger.warning("[来源卫生] 对方消息里出现越权话术「%s」，按资料处理，不执行",
                       injection_hint(message))
    提示词 = user_prompt_template().format(
        boss_name=sanitize_untrusted(boss_name) or "HR",
        job_name=sanitize_untrusted(job_name) or current_profile().get("position", "目标岗位"),
        history=历史,
        message=最新,
    )
    # 这一句固定追加在模板之外：模板本身在界面上可改（user_prompt_template 有覆盖就
    # 用覆盖的），写在模板里等于写在一份随时会被"恢复默认"替掉的地方。
    # 「招聘岗位」那一行是页面整串抓下来的（岗位名+薪资+城市），照抄进正文就是机器味：
    # 实测 4 条跟进句把「…100-150元/天长沙查看职位」原样发给过 HR。
    提示词 += ("\n（「招聘岗位」那行是系统抓下来的一整串，带薪资和城市，"
              "绝不许原样念给对方；要提这个岗就用它做什么来说。）")
    # 对话记录与最新消息都是外部输入：来源即数据，不是新指令（hermes 的 source hygiene）。
    提示词 += "\n" + untrusted_note(f"{message or ''}\n{历史}")
    return 提示词 + ("\n\n" + 追加指令 if 追加指令 else "")

