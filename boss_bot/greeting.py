# -*- coding: utf-8 -*-
"""招呼语的来源解析：账号默认（按本账号信息生成）+ AI 按岗位定制 + 岗位手写。

口径 2026-10-03：以前"留空即不发送"，结果是两个号都没写那句话时一整轮
218 条全是「未配置招呼语，跳过」——用户看到的只是"日志一片跳过、记录对不上"。
现在每个账号先按**它自己的**信息（城市/搜索方向/技能/作品图）给一条能发的默认，
发送时再由 AI 判分链按岗位名+公司+JD 现编一条更贴的；岗位里手写的仍然最大。
"""

from boss_bot.unified_config import DEFAULT_GREETING, strip_default_greeting

# 简历里常见的占位值：这种字样绝不能出现在发给 HR 的话里
_PLACEHOLDER_MARKS = ("某某", "示例", "待填", "test", "xxx", "TBD")

# AI 现编招呼语的长度上限：BOSS 招呼语实测 60~110 字最像人打的，
# 超过这个线基本是在复述 JD（判据同 reply_engine 的聊天回复上限，这里更紧）
AI_GREETING_MAX_CHARS = 140


def _g(obj, key, default=None):
    """字典或 dataclass 都按属性名取值：配置对象和测试里的替身走同一条路。"""
    if obj is None:
        return default
    if isinstance(obj, dict):
        val = obj.get(key, default)
    else:
        val = getattr(obj, key, default)
    return default if val is None else val


def _is_placeholder(text: str) -> bool:
    low = str(text or "").strip().lower()
    return not low or any(m in low for m in _PLACEHOLDER_MARKS)


def _dedupe(items):
    out = []
    for it in items:
        it = str(it or "").strip()
        if it and it not in out:
            out.append(it)
    return out


def enabled_jobs(account):
    return [j for j in (_g(account, "jobs", []) or []) if _g(j, "enabled", True)]


def account_focus(account, resume=None, profile=None):
    """这个账号在找什么：城市 + 方向。岗位里没写才回落到画像/简历。"""
    jobs = enabled_jobs(account)
    cities = _dedupe(_g(j, "city", "") for j in jobs)
    directions = _dedupe(_g(j, "query", "") for j in jobs)
    direction = (directions[0] if directions
                 else str(_g(profile, "position", "") or _g(resume, "target_position", "")).strip())
    return cities, direction


def _skills_text(skills, direction, resume) -> str:
    """技能清单要不要写、怎么写。

    两条规矩：方向和技能对不上时整条不写（账号2 找 AI 漫剧，提 SQL 就是答非所问）；
    对得上时也不把方向本身当技能再念一遍（"用 Excel、SQL、数据分析做数据分析"读不通）。
    """
    d = str(direction or "").strip()
    toks = [str(s).strip() for s in (skills or []) if str(s).strip()]
    if not toks:
        return ""
    if d:
        target = str(_g(resume, "target_position", "") or "").strip()
        related = any(s != d and (s in d or d in s) for s in toks) or bool(
            target and (target in d or d in target))
        if not related:
            return ""
        toks = [s for s in toks if s != d]
    return "、".join(toks)


def compose_account_default(account, resume=None, profile=None) -> str:
    """按这个账号自己的信息拼一条默认招呼语——不联网、不调 AI，永远给得出来。"""
    cities, direction = account_focus(account, resume, profile)
    skills = _g(resume, "skills", []) or _g(profile, "skills", []) or []
    if isinstance(skills, str):
        skills = [skills]
    skills_txt = _skills_text(_dedupe(skills), direction, resume)
    degree = str(_g(resume, "degree", "") or "").strip()
    major = "" if _is_placeholder(_g(resume, "major", "")) else str(_g(resume, "major", "")).strip()
    school = "" if _is_placeholder(_g(resume, "school", "")) else str(_g(resume, "school", "")).strip()

    who = "".join(x for x in (school, major, degree) if x)
    looking = ""
    if direction:
        looking = f"在{'、'.join(cities)}找{direction}的机会" if cities else f"想找{direction}方向的机会"

    first = "您好～"
    if who and looking:
        first += f"我是{who}，{looking}"
    elif who:
        first += f"我是{who}，{direction or ''}".rstrip("，")
    elif looking:
        first += looking
    else:
        first += "看到贵司这个岗位，想了解一下"

    second = f"平时用{skills_txt}做数据整理和分析" if skills_txt else ""
    exp = str(_g(resume, "experience", "") or "").strip()
    if skills_txt and exp and not _is_placeholder(exp) and len(exp) <= 30:
        second = f"{second}，{exp}"

    closing = "看到贵司这个岗位和我的方向挺匹配，方便的话想把简历发您细聊"
    if _g(account, "image_files", []):
        closing = "作品集可以马上发您，" + closing
    itime = str(_g(profile, "available_interview_time", "") or "").strip()
    if itime and not _is_placeholder(itime):
        closing += f"，{itime}可以面试"

    return "。".join(x for x in (first, second, closing) if x) + "。"


def effective_account_greeting(account, resume=None, profile=None) -> str:
    """这个账号真正会用的账号级招呼语：自己写的，没有就用按本账号信息生成的默认。

    等于历史默认串的一律当"没写"（口径同 strip_default_greeting），否则配置里
    看着有字、引擎判的却是空，又是一轮全跳过。
    """
    text = strip_default_greeting(_g(account, "greeting_message", ""))
    if str(text or "").strip():
        return str(text).strip()
    return compose_account_default(account, resume, profile)


def account_greeting_mode(account, resume=None, profile=None) -> str:
    """这句话是谁的话：用户自己写的，还是系统按本账号信息生成后落盘的默认。

    生成的默认是会写进配置、界面里可以改的，所以"是不是默认"不能只看它空不空——
    内容还和生成结果一模一样就仍算默认，改过一个字才算用户自己的话。
    """
    written = str(strip_default_greeting(_g(account, "greeting_message", "")) or "").strip()
    if not written:
        return "自动生成的默认"
    if written == compose_account_default(account, resume, profile).strip():
        return "自动生成的默认"
    return "账号自写"


def ensure_account_default(account, resume=None, profile=None) -> bool:
    """账号没写招呼语就把生成的默认落进这个账号的配置。

    返回 True 表示这次改动过配置（调用方决定要不要写盘）。自己写过的绝不覆盖。
    """
    current = str(_g(account, "greeting_message", "") or "")
    if str(strip_default_greeting(current) or "").strip():
        return False
    generated = compose_account_default(account, resume, profile)
    if not generated.strip():
        return False
    if isinstance(account, dict):
        account["greeting_message"] = generated
    else:
        account.greeting_message = generated
    return True


def sanitize_ai_greeting(text) -> str:
    """AI 现编的招呼语能不能直接发：不能就返回空串，由调用方回落账号默认。

    判据取自 reply_engine 那条现成的闸门（空正文/markdown/提示词脚手架/超长），
    招呼语比聊天更短，所以再压一道长度。发出去的话收不回来，宁可退回默认。
    """
    raw = str(text or "").strip()
    if not raw:
        return ""
    if len(raw) > AI_GREETING_MAX_CHARS:
        return ""
    try:
        from boss_bot.reply_engine import reply_rejection
        if reply_rejection(raw):
            return ""
    except Exception:
        # 回复引擎import不了时只退化到最基础的两条：结构/markdown 仍然拦
        if any(mark in raw for mark in ("```", "**", "\n- ", "思考过程")):
            return ""
    return raw
