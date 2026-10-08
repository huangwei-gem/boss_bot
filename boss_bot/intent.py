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
    # HR 问"有没有经验"。单独一条意图是因为这一问的话术必须固定：
    # 用户 2026-10-08 的口径是"别人问你有没有经验直接说有经验就行，先拿下面试再说"，
    # 交给 AI 现编就会编出"我可以学"——画像里经历写得再全，模型看见
    # "不要编造不存在的工作经历"这条规则仍然往保守里答。
    # 只认问句形状：「经验不限」「有经验者优先」「没做过也没关系」是 HR 在陈述要求，
    # 不是提问，不能抢来答"有经验"。
    "ask_experience": [
        r"经验.{0,6}(吗|么|没|没有|多少|几年|丰富|如何|怎样|怎么样)",
        r"(有没有|有无|是否有|有木有).{0,10}经验",
        r"(?<![没不])(做|干|从事|接触|搞)过.{0,10}(吗|么|没|没有)",
        r"(之前|以前|过往|以往).{0,10}(经验|做过|从事)",
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
    "ask_experience",
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
# 小句按"，。；：！？（）/ 换行"切，**不按顿号切**：「无需招生、销售」里那个顿号是并列，
# 不是新起一句——按顿号切就看不出"无需"也管着"销售"。
# '+' '~' '-' 这些是 BOSS 标题里当项目符号用的（"28/H包吃住+可预支+不体检"），
# 不当分隔符的话，"不体检"会把后面正儿八经的"包吃住"赦免掉（实测红过一条）。
_CLAUSE_SEPS = "，,。.;；!！?？:：\n（）()【】[]/\\|｜~～+-－— "
# 命中前后都是顿号 = 它在列举业务范围，不是在说这份工本身
# （「线上运营」写"统筹产品、市场、销售、供应链等业务模块"就这么被杀过）。
_ENUM = "、"
# "中介"在数据岗 JD 里通常是统计学方法（中介效应）或"无中介费"的免责说法。
# 2026-10-07 它单枪匹马误杀了 26 条「数据分析师」、25 条「数据标注/AI训练师」。
_EXCEPTIONS = {
    "中介": {"后缀": ("效应", "变量", "费", "作用"),
             "同句": ("检验", "回归", "稳健性", "内生性", "异质性", "调节", "计量", "DID")},
}


def _clause(body: str, idx: int) -> str:
    start = max([body.rfind(sep, 0, idx) for sep in _CLAUSE_SEPS] + [-1]) + 1
    return body[start:idx]


_DISCLAIMER_RE = re.compile(
    r"(无需|不需|不用|不要|不涉及|不接受|不承担|不收取|不含|没有|不会)"
    r"[\u4e00-\u9fff、，]{0,6}$")


def _negated(body: str, idx: int) -> bool:
    """命中位置前面是不是否定说法。

    两种认法：①紧邻窗口（"不坐班""无需坐班"）；②整段免责说法后面跟着的并列项
    （"无需招生、销售"、"不涉及课程销售"、"不承担任何销售压力"）。
    光看"小句里有没有 不/无/没"是不行的——「直招·无押金提供住宿」那个"无"管的是押金，
    不是住宿（这条被测试抓回来过）。
    """
    前 = body[:idx]
    if _NEGATED_RE.search(前):
        return True
    return bool(_DISCLAIMER_RE.search(前))


def _is_exception(word: str, body: str, idx: int) -> bool:
    """命中位置其实在列举或其实是别的词，不算否决。"""
    前 = body[idx - 1:idx]
    后 = body[idx + len(word):idx + len(word) + 1]
    if 前 == _ENUM and 后 == _ENUM:
        return True
    条 = _EXCEPTIONS.get(word)
    if not 条:
        return False
    if body[idx + len(word):idx + len(word) + 2].startswith(tuple(条["后缀"])):
        return True
    # 同句看命中两侧各 24 字：「进阶：中介、调节、稳健性、分组回归」里
    # "中介"前面只有"进阶："，统计方法的线索都在它后面。
    窗口 = body[max(0, idx - 24):idx + len(word) + 24]
    return any(k in 窗口 for k in 条["同句"])


def keyword_hit(words, text: str, honor_negation: bool = True) -> str:
    """这段文本里出现了哪一条关键词（没有返回空串）。

    口径只有一份：否决词要求整条出现，面板上把"主播，地推"输成一格就该拆成两条，
    所以这里不做分词、不做模糊；只抹掉 BOSS 的隔字写法（_tight），
    外加认否定式（_negated）与列举/同音词例外（_is_exception）。
    同一个词在一段里可能出现两次（"不用坐班，但周末要坐班"），逐处看，
    有一处是肯定说法就算命中。

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
            if not honor_negation:
                return str(kw).strip()
            if not _negated(body, i) and not _is_exception(word, body, i):
                return str(kw).strip()
            start = i + len(word)
    return ""


def title_is_target_shaped(title: str) -> bool:
    """这个标题像不像"他要的那类活"（数据/标注/客服/运营/剪辑…）。

    单独拿出来给正文那一遍用：标题带这些词时，正文里的"快递/打包/直播"多半是
    内容场景（「标注快递物流场景的录音」），照正文杀就把方向杀掉了；标题里一个
    职业词都没有时（「山姆新仓开业大量招人」），岗位性质只剩正文一条证据，
    不查正文就等于没判据。
    """
    body = _tight(title)
    if not body:
        return False
    return any(_tight(word) in body for word in TITLE_VETO_EXEMPT_KEYWORDS)


def title_veto_hit(title_keywords, title: str) -> str:
    """岗位标题里的岗位类型否决词（普工/主播/快递/保洁这一类），命中返回那一条。

    只量标题是有道理的：类型词写在 JD 正文里是顺带一提（"标注快递物流场景的录音"），
    写在标题里才是这份工本身。标题同时带用户点名要的职业词（"直播运营助理"
    "电商客服"）时放行——那种是挂着类型皮的线上活，见
    unified_config.TITLE_VETO_EXEMPT_KEYWORDS。
    但 NO_EXEMPT 那几个词压过白名单：「AI+数据合伙人」里的"数据"不是它是数据岗的理由，
    合伙人本身就是骗局形状（用户 2026-10-08："这种合伙人的一看就是骗子不要"）。
    """
    body = _tight(title)
    if not body or not title_keywords:
        return ""
    硬拦 = keyword_hit([w for w in title_keywords if w in NO_EXEMPT], body,
                       honor_negation=False)
    if 硬拦:
        return 硬拦
    if title_is_target_shaped(title):
        return ""
    return keyword_hit([w for w in title_keywords if w not in NO_EXEMPT], body,
                       honor_negation=False)


# 压过职业白名单的类型词：这些词本身就是"这份工是什么"，标题里再带"数据/分析"也不给放行。
# 「AI+数据合伙人」被"数据"白名单放过一次，就是用户说的"一看就是骗子"那一类。
NO_EXEMPT = frozenset({"合伙人", "老师", "助教", "家教", "讲师", "速记", "伴读",
                       "主播", "主包", "团播", "语音厅", "陪聊", "情感互动"})
# 主播族的词，BOSS 常只写在 JD 正文里（"不用露脸，居家语音厅"），所以正文这一遍要查它们；
# 而且查的时候不认否定式——"不用露脸"本身就是这一行的名字。
ANCHOR_FAMILY = frozenset({"主播", "主包", "直播", "互动播", "团播", "带播", "口播", "带货",
                           "语音厅", "场控", "露脸", "不露脸", "陪聊", "情感互动", "聊天室"})
# 但只有这一小组能在正文里定罪：「数据标注专员」的 JD 写"包含直播间的语音切片"、
# 「视频剪辑」写"抖音主播素材二次剪辑"，那是内容不是岗位（回归里就锁着这条）。
# "口播"从这一组里拿掉了：盘上两条正文带它的会话都是他要的剪辑岗——
# 「兼职·线上短视频剪辑师」写"剪辑类目包括：口播、信息流、电商带货…"、
# 「兼职·线上剪辑师」要"截图以前剪辑过的视频…要口播视频"。它在讲素材类型，
# 主播岗自己会把这个字写进标题（「口播主播」「短视频口播」），标题那一遍拦。
ANCHOR_IN_BODY = frozenset({"露脸", "不露脸", "主包", "语音厅", "团播", "带播",
                            "互动播", "陪聊", "情感互动", "聊天室", "场控"})
# 既是岗位类型又描述"这份工本身"的词，正文里出现照样算（销售/中介/派遣）。
KEEP_IN_BODY = frozenset({"销售", "中介", "派遣职位", "电销", "电话销售"})
# 只在标题定罪的词（老师/助教/合伙人这一族）：用户说的是"兼职老师这种不要"，
# 讲的是岗位；写进正文时它在说别的——「极氪零售实习生」HR 原话"有带教老师一对一辅导"
# 是培养机制，「兼职·跨境电商学员」写"我们招募项目合伙人"是招商话术。
# 这两条 2026-10-08 回放当天会话时都会被正文那一遍杀掉，所以钉死只查标题。
TITLE_ONLY = frozenset({"合伙人", "老师", "助教", "家教", "讲师", "速记", "伴读"})


# 标题已经说明这是数据/分析/标注的活时，正文里的"销售"多半是**被分析的业务域**
# ——"清洗销售及进销存数据""分析防盗门销售数据""监控销售指标波动""对接销售、采购部门
# 落地数据建议"。2026-10-08 00:04 的「数据分析师」（河北日上防盗门）就是这么被
# 「销售」连杀两天：10-07+10-08 目标标题里这种形状 15 条。
# 只豁免这一小撮词、只豁免正文那一遍，并且词只要挨着"经验/专员/拓展/负责…"这种
# 岗位形状就照旧定罪——「AI+数据合伙人」写"有一定的销售/商务拓展经验"仍然拦得住。
_DATA_SHAPE_RE = re.compile(r"数据|分析|标注")
_DOMAIN_WORDS = frozenset({"销售"})
_ROLE_TAIL = ("经验", "专员", "代表", "顾问", "经理", "总监", "提成", "考核",
              "业绩", "拓展", "开发", "谈单", "拜访", "跟单")
_ROLE_HEAD = ("负责", "从事", "做过", "担任", "岗位", "承担", "完成")
# 词后面直接接着"被统计的东西"时，它在讲数据域不在讲岗位——"完成销售数据整理"
# 里"完成"挨着岗位词，可这份工是整理数据的那个人。
_DATA_TAIL = ("数据", "指标", "报表", "看板", "口径", "明细", "周报", "月报",
              "统计", "分析", "情况")


def _word_is_the_job(word: str, body: str) -> bool:
    """正文里这个词是不是在说"这份工就要干这个"（而不是被分析的对象）。"""
    start = 0
    while True:
        i = body.find(word, start)
        if i < 0:
            return False
        后 = body[i + len(word):i + len(word) + 6]
        前 = body[max(0, i - 4):i]
        if not any(k in 后 for k in _DATA_TAIL) and (
                any(k in 后 for k in _ROLE_TAIL)
                or any(k in 前 for k in _ROLE_HEAD)):
            return True
        start = i + len(word)


def body_veto_hit(body_keywords, title_keywords, title: str = "", text: str = "") -> str:
    """否决词那一遍：蓝领类型词在标题不是目标职业形状时也要量正文。

    普工/分拣/骑手/外卖这些词写进 JD 正文，九成是"内容场景"而不是"这份工的岗位"——
    「兼职数据采集」让人录超市理货视频、「语音标注」要标快递场景录音，都是这种。
    2026-10-07 回放当天 1278 条投递记录：这么一分开放行 290 条（数据标注/线上运营/
    线上老师为主），仍拦 170 条，放行的里面没有一条带主播族字样。

    但"只查标题"有个前提：标题得说明白了这是什么岗。用户 2026-10-08 点名的
    上海仟嘉百供应链——标题「山姆新仓开业大量招人8-9K长沙」一个字都没交代岗位，
    岗位性质全在 HR 第一句「工作内容骑电瓶车配送山姆超市日常用品 固定点取货多点配送」
    里。这种标题下去类型词一条都量不着，于是 10-06 用文字拒过之后，10-07 21:48
    主动跟进还替他说"我很有兴趣"。所以：标题带职业白名单词（数据/标注/客服…）
    仍旧只查标题，不带的那一类连正文一起查。
    """
    类型 = set(title_keywords or ())
    # 标题这一遍用全词表：「线上主播助理」被"助理"这张职业白名单放行之后，
    # 他自己填的"主播"总得在标题上拦得住（回归里锁着这条）。
    正文词 = [w for w in (body_keywords or [])
             if w not in 类型 or w in KEEP_IN_BODY]
    主播词 = sorted((类型 | set(body_keywords or ())) & ANCHOR_IN_BODY)
    正文 = _tight(text)
    标题 = _tight(title or "")
    数据岗 = bool(_DATA_SHAPE_RE.search(标题))
    标题命中 = keyword_hit(body_keywords, 标题)
    if 标题命中:
        return 标题命中
    # 类型词的正文这一遍认否定式：HR 说"不需要坐班、无配送费"讲的是条件，
    # 不是岗位；「系统派单、多点配送」这种没有否定词挡着，照样定罪。
    # 销售/中介那一族本来就走上面 正文词 那条认否定式的路，这里排除掉免得绕开豁免。
    if 标题 and not title_is_target_shaped(标题):
        hit = keyword_hit([w for w in (title_keywords or ())
                           if w not in ANCHOR_IN_BODY and w not in KEEP_IN_BODY
                           and w not in TITLE_ONLY], 正文)
        if hit:
            return hit
    for word in 正文词:
        hit = keyword_hit([word], 正文)
        if not hit:
            continue
        if 数据岗 and hit in _DOMAIN_WORDS and not _word_is_the_job(hit, 正文):
            continue          # 销售是被分析的业务域，不是岗位
        return hit
    return keyword_hit(主播词, 正文, honor_negation=False)


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
            or body_veto_hit(body_keywords, title_keywords, title=title, text=text))


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
# HR 约到场时经常不提"面试"两个字，只发门牌号或只问几点过来。2026-10-07 中午
# 就是这样连发六条到场承诺：「公司地址新天地1310」→"明天10点准时到"、
# 「a栋3307 到前台刷电梯卡上来」→"下午三点我准时过去"、
# 「聊城创业大厦A塔3115 到了联系我就行」、「明天下午几点可以过来」、
# 「方便来线下看看吗」、「来的话可以接受一下邀请 上面也有地址」。
# 用词都取具体形状（到前台/刷电梯/写字楼…），不敢用裸的"线下""地址"——
# HR 也会说"我们不是线下，全程线上"，那种判成现场就是凭空撤掉别人的面试。
_OFFLINE_VISIT_MARKS = ("过来面试", "可以过来", "方便过来", "过来一趟", "过来聊聊",
                        "几点过来", "什么时候方便过来", "方便过来", "来公司", "到公司",
                        "去公司面试", "面试地点", "公司地址", "写字楼", "大厦", "到前台",
                        "刷电梯", "电梯卡", "到楼下", "楼层", "来线下", "线下看看",
                        "过来一趟聊", "也有地址")
# 平台/HR 提醒"别贸然上门"的话术，不是邀约：盘上 3 条都是这个形状。
_NOT_AN_INVITE = re.compile(r"(不要|别|切勿|先别).{0,6}(直接过来|直接来|贸然)|请等待.{0,12}(确认|沟通)")


def classify_interview_invite(message: str) -> str:
    """这条面试邀请是要人到场的、还是远程的、还是说不清。

    返回 "offline" / "online" / "unknown"。判错一次就是当着 HR 的面把人家发的面试撤掉，
    所以说不清的（只写"邀请您面试"）一律 unknown，不自动点拒绝。
    """
    text = (message or "").strip()
    if not text or _NOT_AN_INVITE.search(text):
        return "unknown"
    if any(k in text for k in _OFFLINE_INTERVIEW_MARKS):
        return "offline"
    if any(k in text for k in _OFFLINE_VISIT_MARKS):
        return "offline"
    if any(k in text.lower() for k in _ONLINE_INTERVIEW_MARKS):
        return "online"
    return "unknown"


# 我们自己应下到场面的说法——这些一律不许发出去（发送出口最后一道闸）。
# 词形取自当天真发出去的那几条：「准时到新天地1310面试」「我确定来…四点半准时到」
# 「我会准时到达。期待与您见面交流」。拒绝话术里不会出现这些。
_COMMIT_VISIT_RE = re.compile(
    r"准时到|按时到|我会到|我准时|我确定来|按约定准时|这就过去|马上到|稍后过去"
    r"|过去面试|到公司面试|来公司面试|到楼下联系|到了联系(您|你)|见面交流")


def commits_offline_visit(text: str) -> str:
    """这句要发出去的话是不是答应了到场——是就返回命中的说法，否则空串。

    放在发送出口而不只在策略层：策略层判的是 HR 说了什么，可承诺是从我们这头说出去的；
    哪一层（AI、规则直通、意图、主动跟进、人工点发）漏判，句子都不该离开这台机器。
    """
    body = str(text or "")
    if not body:
        return ""
    m = _COMMIT_VISIT_RE.search(body)
    return m.group(0) if m else ""

