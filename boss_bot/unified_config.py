"""
统一配置管理模块

合并 auto_boss 和 BOSS-auto-reply-bot 的配置系统，
提供统一的配置加载、环境变量覆盖和持久化支持。

配置加载顺序：JSON 配置文件 → 环境变量(.env) → 代码默认值

支持两种配置文件：
  - bot_config.json    : 浏览器、登录、打招呼/投递、回复、AI、规则、通知等
  - user_profile.json  : 个人画像、简历信息
"""

import os
import json
import re
import copy
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Any

logger = logging.getLogger(__name__)

# 项目根目录（boss_bot/ 的上一级）
BASE_DIR = Path(__file__).parent.parent

# 配置文件路径
BOT_CONFIG_FILE = BASE_DIR / "bot_config.json"
USER_PROFILE_FILE = BASE_DIR / "user_profile.json"
OVERRIDES_FILE = BASE_DIR / "config_overrides.json"


def write_json_atomic(path, data) -> None:
    """原子写入 JSON：先写同目录临时文件，再 os.replace 覆盖目标。

    直接 open(path, "w") 会先把文件截断再序列化，进程被杀掉或磁盘写满时就留下
    半截 JSON；各存储的 _load() 遇到解析失败一律回退到空字典，等于静默清空
    去重记录，表现为重启后重复回复、重复打招呼。

    Args:
        path: 目标文件路径（str / Path），父目录会自动创建
        data: 可 json 序列化的对象
    """
    target = Path(str(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        # 序列化中途失败要把临时文件清掉，否则目录里留下永远用不上的垃圾
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


def account_file(path, account_index: int) -> str:
    """多账号数据分文件：账号0 沿用原路径（历史数据不迁移），其余加 _account_N。

    两个账号共用一份 bot_state.json 时，账号2 会看到账号1 标记过的"已处理"消息
    而不回复，人工接管/每日上限也会互相串。
    """
    if account_index <= 0:
        return str(path)
    p = Path(path)
    return str(p.with_name(f"{p.stem}_account_{account_index}{p.suffix}"))


def resolve_path(path) -> Path:
    """把配置中的相对路径锚定到项目根目录。

    配置文件里存的是 "zhipin_cookies.json" 这类相对路径，而进程 CWD 取决于启动方式
    （start.bat 会先 cd 到 flask-version/）。不锚定的话同一份配置在不同启动方式下会
    读写到不同文件，表现为登录态丢失。

    Args:
        path: 绝对或相对路径（str / Path）

    Returns:
        绝对 Path；空路径原样返回空 Path
    """
    p = Path(str(path).strip()) if path is not None else Path()
    if str(p) in ("", "."):
        return Path()
    return p if p.is_absolute() else (BASE_DIR / p)


# 可以按账号覆盖的配置段。
# 2026-09-30 用户要求"每个账号独立一套配置"：以前只放开 ai/reply，界面却写着
# "独立配置：…浏览器…"，那是假声明。现在凡是"换个号就该换套设置"的段都可以按号覆盖，
# 但**仍然只存差异**（accounts[i].settings 里只放和基准不一样的键）——整份复制会在
# 用户改过之后被一次全局更新覆盖掉，而且两个号从此各抄一份、再也对不上账。

# 段名 → 容器在 UnifiedConfig 上的属性路径（rate_limit/retry 挂在 greet 下面）
ACCOUNT_OVERLAY_CONTAINERS = {
    "ai": ("ai",),
    "reply": ("reply",),
    "templates": ("templates",),
    "rate_limit": ("greet", "rate_limit"),
    "retry": ("greet", "retry"),
    "login": ("login",),
    "notify": ("notify",),
    "browser": ("browser",),
}

# 对外仍然用这个名字：从容器表推出来，免得两处清单各写一份、改一边忘另一边
ACCOUNT_OVERLAY_SECTIONS = tuple(ACCOUNT_OVERLAY_CONTAINERS)

# 段内不许按号覆盖的键：这两个是账号索引算出来的（端口 9222+idx、目录 account_{idx}），
# 让界面去改 debug_port 会把整段端口偏移量加两遍，两个号反而抢同一个浏览器
ACCOUNT_OVERLAY_LOCKED = {"browser": {"debug_port", "user_data_dir"}}

# 全局共享、不按号覆盖的部分：一份简历画像与一套回复规则。两个号同一个人投，
# 学历/技能/自我介绍不可能一个是"统计学本科"另一个是"风景园林本科"，
# 真出现按号不同的自我介绍就等于对两个 HR 说互相矛盾的话。
# resume 在这里而不是可覆盖段里，还有一层：判分复盘的「补简历证据」建议只能写全局
# （tests/test_judgement_review.py::test_补简历建议只能写全局），一旦 resume 可按号覆盖，
# 采纳账号2 的建议就把简历劈成两半
ACCOUNT_GLOBAL_ONLY = ("resume", "user_profile", "reply_rules", "importance_keywords")


# 默认打招呼话术
DEFAULT_GREETING = (
    "您好，我是双一流的本科，应聘数据分析岗位。在校系统学习数据分析相关知识，"
    "掌握Excel、基础SQL与数据整理技能，具备数据思维。做事严谨细心，学习能力强，"
    "愿意踏实积累。十分认可贵公司，希望能获得面试机会。"
)

def strip_default_greeting(text) -> str:
    """岗位上的招呼语若就是那段历史默认文案，视作"没写"。

    pick_greeting 把"等于默认串"当成未定制（否则账号级自定义永远被岗位那份旧的
    覆盖掉），而界面输入框里却显示着有字——于是日志一片"未配置招呼语，跳过"，
    用户看不出哪里没配。加载时就把它清成空，输入框和引擎判的才是同一件事。
    """
    raw = str(text or "")
    return "" if raw.strip() == DEFAULT_GREETING.strip() else raw


# 话术模板占位符 → 画像字段映射
_TEMPLATE_MAP = {
    "{salary}": "salary_expectation",
    "{interview_time}": "available_interview_time",
    "{position}": "position",
    "{skills}": "skills",
    "{experience}": "experience",
    "{name}": "name",
    "{contact}": "contact",
}


def render_template(text: str, profile: dict) -> str:
    """将话术模板中的 {salary} 等占位符替换为画像字段。

    Args:
        text: 含占位符的模板文本，如 "期望薪资 {salary}"
        profile: 个人画像字典，包含 salary_expectation 等字段

    Returns:
        替换后的文本；列表类型字段会用顿号拼接
    """
    if not text or not isinstance(text, str):
        return text
    for ph, key in _TEMPLATE_MAP.items():
        if ph in text:
            val = profile.get(key, "")
            if isinstance(val, list):
                val = "、".join(str(x) for x in val)
            text = text.replace(ph, str(val))
    return text


def diff_against(submitted: dict, base: dict) -> dict:
    """只留下 submitted 中与 base 不同的键，返回稀疏覆盖（全一样就是空字典）。

    只比 base 里已有的键：前端会把它顺带认识的字段一起回传，那些不是"这个账号
    改过的设置"，写进覆盖就成了基准改名后永远清不掉的僵尸值。
    嵌套段递归下去，子层没差异就整段不留。
    """
    out = {}
    for key, value in (submitted or {}).items():
        if key not in (base or {}):
            continue
        base_val = base[key]
        if isinstance(value, dict) and isinstance(base_val, dict):
            nested = diff_against(value, base_val)
            if nested:
                out[key] = nested
        elif value != base_val:
            out[key] = value
    return out


def _parse_ai_providers(env_prefix: str = "AI_PROVIDERS") -> list:
    """解析 AI 提供商配置，支持多模型自动切换。

    环境变量格式：AI_PROVIDERS_1=api_key|model_name|base_url
    示例：
        AI_PROVIDERS_1=sk-xxx|agnes-2.5-flash|https://apihub.agnes-ai.com/v1
        AI_PROVIDERS_2=sk-yyy|deepseek-v4-flash|https://token.sensenova.cn/v1

    Args:
        env_prefix: 环境变量前缀，默认 "AI_PROVIDERS"

    Returns:
        provider 字典列表，每个包含 key/model/url 三个字段
    """
    providers = []
    for i in range(1, 20):
        value = os.environ.get(f"{env_prefix}_{i}", "")
        if not value:
            continue
        parts = value.split("|")
        if len(parts) >= 2:
            key = parts[0].strip()
            model = parts[1].strip()
            url = parts[2].strip() if len(parts) >= 3 else "https://apihub.agnes-ai.com/v1"
            if key and model:
                providers.append({"key": key, "model": model, "url": url})
    return providers


def _build_providers_from_legacy() -> list:
    """从旧格式环境变量构建 providers 列表（向后兼容）。

    支持的旧格式环境变量：
      - AI_API_KEY_1/2/3 + AI_MODEL_1/2/3 + AI_BASE_URL（主 API）
      - AI_BACKUP_KEY_1/2 + AI_BACKUP_MODEL_1/2 + AI_BACKUP_BASE_URL（备用 API）
      - AI_FALLBACK_KEY + AI_FALLBACK_MODEL + AI_FALLBACK_BASE_URL（兜底 API）
    """
    providers = []

    main_keys = [
        os.environ.get("AI_API_KEY_1", ""),
        os.environ.get("AI_API_KEY_2", ""),
        os.environ.get("AI_API_KEY_3", ""),
    ]
    main_models = [
        os.environ.get("AI_MODEL_1", "agnes-2.5-flash"),
        os.environ.get("AI_MODEL_2", "agnes-2.5-flash"),
        os.environ.get("AI_MODEL_3", "agnes-2.5-flash"),
    ]
    main_url = os.environ.get("AI_BASE_URL", "https://apihub.agnes-ai.com/v1")
    for key, model in zip(main_keys, main_models):
        if key:
            providers.append({"key": key, "model": model, "url": main_url})

    backup_keys = [
        os.environ.get("AI_BACKUP_KEY_1", ""),
        os.environ.get("AI_BACKUP_KEY_2", ""),
    ]
    backup_models = [
        os.environ.get("AI_BACKUP_MODEL_1", "deepseek-v4-flash"),
        os.environ.get("AI_BACKUP_MODEL_2", "deepseek-v4-flash"),
    ]
    backup_url = os.environ.get("AI_BACKUP_BASE_URL", "https://token.sensenova.cn/v1")
    for key, model in zip(backup_keys, backup_models):
        if key:
            providers.append({"key": key, "model": model, "url": backup_url})

    fallback_key = os.environ.get("AI_FALLBACK_KEY", "")
    fallback_model = os.environ.get("AI_FALLBACK_MODEL", "deepseek-flash")
    fallback_url = os.environ.get("AI_FALLBACK_BASE_URL", "https://api.deepseek.com")
    if fallback_key:
        providers.append({"key": fallback_key, "model": fallback_model, "url": fallback_url})

    return providers


# ===================== 数据类定义 =====================

@dataclass
class BrowserConfig:
    """浏览器配置（合并 auto_boss + BOSS-auto-reply-bot）"""
    headless: bool = False
    # 后台运行：有头模式下窗口启动即收进任务栏，不前置、不抢用户焦点。
    # 无头时这个开关没有意义（根本没有窗口），保留是为了"从有头切回无头
    # 再切回来"不至于又变成抢焦点的老行为。
    background: bool = True
    viewport_width: int = 1280
    viewport_height: int = 800
    page_load_timeout: int = 30          # auto_boss: page_load_timeout
    page_timeout: int = 30               # 兼容 browser_launcher.py 已有接口
    custom_user_agent: str = ""
    proxy: str = ""
    browser_type: str = "chrome"
    user_data_dir: Optional[str] = None  # 兼容 browser_launcher.py 已有接口
    chrome_path: Optional[str] = None    # 兼容 browser_launcher.py 已有接口
    debug_port: int = 9222               # Chrome 远程调试端口


@dataclass
class LoginConfig:
    """登录配置（来自 auto_boss）"""
    wait_timeout: int = 300
    clear_cookies_on_failure: bool = True
    cookie_file: str = "zhipin_cookies.json"


@dataclass
class RateLimitConfig:
    """频率限制配置（来自 auto_boss）"""
    enabled: bool = True
    max_per_hour: int = 30
    max_per_day: int = 100


@dataclass
class RetryConfig:
    """重试配置（来自 auto_boss）"""
    max_attempts: int = 3
    base_delay: float = 2.0
    backoff_factor: float = 2.0


@dataclass
class JobConfig:
    """单个岗位配置（来自 auto_boss）"""
    enabled: bool = True
    city: str = "上海"
    query: str = "数据分析"
    # 求职类型："全职"/"实习"/"兼职"，空=不限。BOSS 的 jobType 参数，实测编码见
    # greet_engine.JOB_TYPE_CODES
    job_type: str = ""
    scroll_pages: int = 5
    greeting_message: str = ""      # 留空 = 这个岗位不发招呼；不再回落默认串
    image_files: list = field(default_factory=list)


@dataclass
class AccountConfig:
    """单个账号配置（来自 auto_boss）"""
    name: str = "主账号"
    enabled: bool = True
    cookie_file: str = "zhipin_cookies.json"
    image_files: list = field(default_factory=list)
    message_interval_min: int = 3
    message_interval_max: int = 8
    # 本账号自己的招呼语：填了就用它，不再用那段每个岗位都塞着的默认文案
    greeting_message: str = ""
    # 本账号相对全局基准的差异覆盖，形如 {"ai": {"match_threshold": 88}}。
    # 只存差异不存整份：全局改了阈值，没动过这一项的账号要跟着变。
    settings: dict = field(default_factory=dict)
    jobs: list = field(default_factory=lambda: [JobConfig()])


@dataclass
class GreetConfig:
    """打招呼/投递配置（来自 auto_boss）

    合并了 auto_boss 的 accounts、rate_limit、retry 配置。
    """
    enabled: bool = True
    accounts: list = field(default_factory=lambda: [AccountConfig()])
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)


@dataclass
class ReplyConfig:
    """自动回复配置（来自 BOSS-auto-reply-bot）"""
    enabled: bool = True
    check_interval: int = 8
    context_message_count: int = 10
    max_replies_per_hour: int = 60
    min_delay: int = 2
    max_delay: int = 5
    pause_on_important: bool = True
    # 重要消息触发的"人工接管"暂停多久自己解除：0 = 永不自动恢复。
    # 一句 HR 的"可以聊聊吗"不该把整个号的回复轮锁住——实测暂停期间
    # 后面 9 个真提问一个都没点开。面板上的「暂停回复」按钮不受这条影响。
    pause_auto_resume_minutes: int = 10
    resume_send_once: bool = True
    # HR 发"我想要和您交换微信/电话号码，您是否同意"那张卡片时要不要点同意；
    # 关掉就是只回话不交换，留给人工决定（联系方式给出去收不回来）
    accept_contact_exchange: bool = True
    # 回复候选的第二条腿：BOSS 的红点会被启动全量同步清掉，只认红点会静默停摆
    owed_per_round: int = 12            # 每轮最多补回几个存档里欠着的会话
    owed_max_age_hours: int = 72        # 太旧的账不翻（对方早就不在招了）
    # 追到面试：我们说完对方沉默够久，主动跟一次，不追就永远停在"已沟通"
    followup_enabled: bool = True
    followup_after_hours: int = 8       # 静默多久算"没回"
    followup_gap_hours: int = 24        # 同一会话两次跟进的最小间隔
    followup_max_times: int = 2         # 同一会话最多追几次，再多就是骚扰
    followup_every_minutes: int = 15    # 跟进扫描的频率（比回复轮次低得多）
    chat_url: str = "https://www.zhipin.com/web/geek/chat"
    use_ai: bool = True                 # 兼容 reply_engine.py 已有接口
    ai_model: str = "agnes-2.5-flash"    # 兼容 reply_engine.py 已有接口
    ai_temperature: float = 0.7          # 兼容 reply_engine.py 已有接口


@dataclass
class AIProvider:
    """单个 AI 提供商配置"""
    name: str = "默认"
    api_key: str = ""
    api_base: str = "https://apihub.agnes-ai.com/v1"
    model: str = "agnes-2.5-flash"
    timeout: int = 30


@dataclass
class AIConfig:
    """AI 配置（合并 auto_boss + BOSS-auto-reply-bot）

    支持多模型池，调用时随机选择，失败自动切换。
    """
    enabled: bool = False
    providers: list = field(default_factory=list)  # AIProvider 列表
    fail_action: str = "default"         # skip | default (default=句句有回应)
    max_tokens: int = 1200
    # 回复的输出预算：带 thinking 的接口实测正文被思考吃光（content 为空），
    # 今日 160 次失败里思考字数中位 318、p95 896，200 的预算连思考都不够，
    # 正文一个字不剩 → 只能换接口，四个接口连着同一个坑就等于没回复。
    # 判分那条链同理，早就放到 analyze_max_tokens=1600。
    # 岗位判分的输出预算：带 thinking 的接口实测 1024 token 会被思考吃光，
    # 正文为空 → 只能换接口，见 greet_engine.AIAnalyzerChain
    analyze_max_tokens: int = 1600
    rate_limit_wait: int = 30
    # 判分时单个接口的超时上限（秒）。体检那边仍按服务商自己的 timeout（默认 45 秒），
    # 慢但活着的服务商不该被判成不可用；判分等不起——实测 AMD/NVIDIA 三家
    # 46~50 秒超时，链子按 45 秒干等，一轮还没换到能用的一家，这一单就按默认通过投了
    # （2026-10-07 00 点那段：167 条记录 41 条 ai_error，其中 30 条真的发出去了）。
    #
    # 这个数只能从"真的判成功的那些用了多久"里取，不能从超时的那头取。
    # 两天日志 411 条成功判分的耗时：p50 12.8s、p75 20.2s、p90 42.2s、p95 53.0s。
    # 第一版取了 12 秒，正好砍在一半上：56% 的成功判分被自己的上限掐死，
    # 而这些超时会被 report_runtime_result 记 strike（连续 2 次就判该接口不可用），
    # 于是活着的服务商一家家被除名，池子空了 —— 每个岗位 0.0 秒就报
    # "所有 AI 接口均失败"，全按默认通过盲投，比原来的毛病更严重。
    # 30 秒才削尾巴：20 秒那一版仍然掐死了大约 1/4 的成功判分，02:44 重体检
    # 出来的 10 家可用服务商平均延迟就是 19.4 秒，掐它们等于把池子再次清空。
    # 单岗位还有 JOB_BUDGET_SECONDS=60 的总预算兜着，不会因为放宽而拖死整轮。
    # 0 = 不另设上限。
    judge_timeout: int = 30
    match_threshold: int = 70            # auto_boss: match_threshold
    api_key: str = ""                    # 兼容旧格式
    api_base: str = "https://apihub.agnes-ai.com/v1"  # 兼容旧格式
    model: str = "agnes-2.5-flash"       # 兼容旧格式
    custom_filter_keywords: list = field(default_factory=list)  # 用户自定义筛选关键词
    # 只看否决词：AI 的 score/is_match 不再参与放行决定，只有命中自定义筛选词才拦。
    # 用户要"线上兼职先放开量"时用；提示词单独说"别考虑背景"压不住基础提示词那段简历。
    veto_only_match: bool = False
    custom_scoring_prompt: str = ""  # 用户自定义打分提示词（追加到系统默认提示词后）
    # 容灾链是否跳过"体检明确不可用"的接口。关掉就是按原顺序硬试：
    # 22 个接口里 16 个不可用时，单岗位判分会烧光 60s 预算并落到"默认通过"，
    # 表现成"AI 筛岗没生效"。见 tools/e2e_live_boss.py 的耗时断言。
    skip_unhealthy: bool = True
    # 判分复盘：AI 判"不符合"后追问它卡在哪一条，每号每轮最多几条。
    # 一次追问≈一次判分（实测中位 5.4 秒），所以默认给 5；填 0 等于关掉。
    probe_max_per_round: int = 5


@dataclass
class TemplateConfig:
    """话术模板配置（来自 BOSS-auto-reply-bot）

    模板中的 {salary} 等占位符会在加载时用个人画像替换。
    """
    salary_reply: str = "我的期望薪资是 {salary}，具体可以面谈，更看重发展机会和团队氛围。"
    interview_time_reply: str = "{interview_time}都可以安排面试，您看哪个时间段方便？"
    job_content_reply: str = "我了解这个岗位主要负责{position}相关工作，我{experience}，相信能快速上手。"
    greeting_reply: str = "您好！我对这个岗位很感兴趣，方便了解一下具体情况吗？"
    default_reply: str = "好的，感谢您的消息，我会尽快回复您。"
    resume_duplicate_reply: str = "您好，简历刚刚已经发您了，您看一下，有任何问题随时沟通~"
    resume_unavailable_reply: str = "不好意思，简历文件暂时不在我这边，稍后我补发给您，可以先看看我主页的在线简历~"


@dataclass
class RuleConfig:
    """规则配置（来自 BOSS-auto-reply-bot）"""
    reply_rules: dict = field(default_factory=dict)
    importance_keywords: list = field(default_factory=lambda: [
        "offer", "入职", "录取", "录用", "欢迎加入", "报到",
        "面试邀请", "邀约", "来公司", "到岗",
    ])


@dataclass
class NotifyConfig:
    """通知配置（来自 BOSS-auto-reply-bot）"""
    enabled: bool = True
    webhook_url: str = ""
    sound: bool = True                   # 兼容 notify.py 已有接口
    desktop: bool = True                 # 兼容 notify.py 已有接口


@dataclass
class ResumeConfig:
    """简历配置（来自 auto_boss）"""
    school: str = ""
    major: str = ""
    degree: str = ""
    skills: list = field(default_factory=list)
    experience: str = ""
    target_position: str = ""
    self_intro: str = ""


@dataclass
class UserProfileConfig:
    """个人画像配置（来自 BOSS-auto-reply-bot）"""
    name: str = "求职者"
    education: str = "本科学历"
    position: str = "数据分析"
    skills: list = field(default_factory=list)
    experience: str = ""
    salary_expectation: str = "面议"
    available_interview_time: str = "工作日下午"
    contact: str = ""
    highlights: list = field(default_factory=list)


@dataclass
class LogConfig:
    """日志配置（来自 BOSS-auto-reply-bot）"""
    log_dir: str = "logs"
    log_level: str = "INFO"
    log_retention_days: int = 14
    event_log_enabled: bool = True


_FILTER_SEP_RE = re.compile("[，,、;；\\n]+")


def normalize_filter_keywords(value) -> list:
    """把"一整串用逗号连着的"筛选词拆成一条条能用的词。

    面板早期只按英文逗号切，用户输中文逗号（主播，地推，销售）就落成一个条目，
    而否决词要求整条出现在 JD 里，于是三个词一个都没生效。读盘时自愈，
    不依赖前端有没有修好。
    """
    if not value:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    out = []
    for item in items:
        if item is None:
            continue
        for part in _FILTER_SEP_RE.split(str(item)):
            word = part.strip()
            if word and word not in out:
                out.append(word)
    return out


def _accounts_of(data: dict) -> list:
    """配置文件里的账号列表：新版写在 accounts，老版写在 greet.accounts。"""
    accounts = data.get("accounts")
    if isinstance(accounts, list):
        return accounts
    greet = data.get("greet")
    if isinstance(greet, dict) and isinstance(greet.get("accounts"), list):
        return greet["accounts"]
    return []


def _looks_like_default_fallback(new: dict, old: dict) -> bool:
    """新内容是不是"加载失败退回的默认值"。

    只看这一个组合：账号比磁盘上少，同时 AI 接口从有到无。用户正常删账号 or
    删接口只会命中一边，不会两个一起缩水。
    """
    new_accounts, old_accounts = _accounts_of(new), _accounts_of(old)
    if len(new_accounts) >= len(old_accounts):
        return False
    new_providers = (new.get("ai") or {}).get("providers") or []
    old_providers = (old.get("ai") or {}).get("providers") or []
    return bool(old_providers) and not new_providers


@dataclass
class UnifiedConfig:
    """统一配置 — 包含所有子配置域

    通过 UnifiedConfig.load() 加载，加载顺序：
      1. 代码默认值
      2. JSON 配置文件覆盖（bot_config.json, user_profile.json）
      3. 环境变量覆盖（.env / os.environ）
    """
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    login: LoginConfig = field(default_factory=LoginConfig)
    greet: GreetConfig = field(default_factory=GreetConfig)
    reply: ReplyConfig = field(default_factory=ReplyConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    templates: TemplateConfig = field(default_factory=TemplateConfig)
    rules: RuleConfig = field(default_factory=RuleConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    resume: ResumeConfig = field(default_factory=ResumeConfig)
    user_profile: UserProfileConfig = field(default_factory=UserProfileConfig)
    log: LogConfig = field(default_factory=LogConfig)
    test_mode: bool = False
    test_page: str = ""
    # 演练模式：照常搜索岗位、照常让 AI 判分和生成回复，只在最后一步不点发送。
    # 用于换话术/换筛选标准后先确认"本来会发什么"，不消耗每日上限也不打扰 HR。
    dry_run: bool = False
    # 自进化：只做回复效果记录与评估（不自动改你写的规则/话术）
    self_evolve_enabled: bool = True

    @classmethod
    def load(cls, config_path: Optional[str] = None,
             profile_path: Optional[str] = None,
             overrides_path: Optional[str] = None) -> "UnifiedConfig":
        """加载统一配置。

        加载顺序：代码默认值 → JSON 配置文件 → 环境变量

        Args:
            config_path: bot_config.json 路径，默认使用项目根目录下的
            profile_path: user_profile.json 路径，默认使用项目根目录下的
            overrides_path: config_overrides.json 路径，默认使用项目根目录下的。
                该文件是前端写入的运行时状态，测试需要隔离时指向一个不存在的路径。

        Returns:
            完整的 UnifiedConfig 实例
        """
        config = cls()

        # ---- 加载 .env 文件（如果存在）----
        _env_path = BASE_DIR / ".env"
        if _env_path.exists():
            with open(_env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, _, value = line.partition("=")
                        key = key.strip()
                        value = value.strip()
                        if key and not os.environ.get(key):
                            os.environ[key] = value

        # ---- 加载 JSON 配置文件 ----
        bot_data = {}
        cfg_path = Path(config_path or os.environ.get("BOSS_BOT_CONFIG") or BOT_CONFIG_FILE)
        if cfg_path.exists():
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    bot_data = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"加载 bot_config.json 失败，使用默认值: {e}")

        profile_data = {}
        prof_path = Path(profile_path) if profile_path else USER_PROFILE_FILE
        if prof_path.exists():
            try:
                with open(prof_path, "r", encoding="utf-8") as f:
                    profile_data = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"加载 user_profile.json 失败，使用默认值: {e}")

        # 加载覆盖文件
        overrides_data = {}
        ovr_path = Path(overrides_path) if overrides_path else OVERRIDES_FILE
        if ovr_path.exists():
            try:
                with open(ovr_path, "r", encoding="utf-8") as f:
                    overrides_data = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        # ---- 应用 JSON 配置到各子域 ----
        config._apply_bot_config(bot_data)
        config._apply_user_profile(profile_data)
        config._apply_overrides(overrides_data)

        # ---- 应用环境变量覆盖 ----
        config._apply_env_overrides()

        # ---- 渲染话术模板（用个人画像替换占位符）----
        config._render_templates()

        return config

    def apply_account(self, index: int) -> "UnifiedConfig":
        """返回这个账号的生效配置：基准的深拷贝 + 该账号的差异覆盖。

        必须是新对象：运行循环原先和 MultiAccountManager 共用同一份 config，
        账号 0 的阈值一盖就把账号 1 也改了。
        覆盖只认 ACCOUNT_OVERLAY_SECTIONS 里段、且段里已有的字段——写错字段名
        或塞进 accounts/resume 这类段，宁可静默忽略也不能改坏配置结构。
        """
        cfg = copy.deepcopy(self)
        if not (0 <= index < len(cfg.greet.accounts)):
            return cfg
        overlay = cfg.greet.accounts[index].settings or {}
        for section, path in ACCOUNT_OVERLAY_CONTAINERS.items():
            values = overlay.get(section)
            if not isinstance(values, dict):
                continue
            target = cfg
            for attr in path:
                target = getattr(target, attr, None)
                if target is None:
                    break
            if target is None:
                continue
            locked = ACCOUNT_OVERLAY_LOCKED.get(section, set())
            for field_name, value in values.items():
                # 锁住的键（浏览器端口、用户目录）即使在覆盖里也不生效：那两个是账号
                # 索引算出来的，覆盖穿过去等于两个号抢同一个浏览器
                if field_name in locked or not hasattr(target, field_name):
                    continue
                setattr(target, field_name, value)
        return cfg

    def _apply_bot_config(self, data: dict):
        """将 bot_config.json 的数据应用到各配置域。"""
        if not isinstance(data, dict):
            return

        # 演练模式 / 自进化记录：顶层开关，不属于任何子域
        if "dry_run" in data:
            self.dry_run = bool(data["dry_run"])
        if "self_evolve_enabled" in data:
            self.self_evolve_enabled = bool(data["self_evolve_enabled"])

        # 浏览器配置
        browser = data.get("browser", {})
        if isinstance(browser, dict):
            if "headless" in browser:
                self.browser.headless = bool(browser["headless"])
            if "background" in browser:
                self.browser.background = bool(browser["background"])
            if "viewport_width" in browser:
                self.browser.viewport_width = int(browser["viewport_width"])
            if "viewport_height" in browser:
                self.browser.viewport_height = int(browser["viewport_height"])
            if "page_load_timeout" in browser:
                self.browser.page_load_timeout = int(browser["page_load_timeout"])
                self.browser.page_timeout = self.browser.page_load_timeout
            if "custom_user_agent" in browser:
                self.browser.custom_user_agent = str(browser["custom_user_agent"])
            if "proxy" in browser:
                self.browser.proxy = str(browser["proxy"])
            if "browser_type" in browser:
                self.browser.browser_type = str(browser["browser_type"])
            # 浏览器可执行文件路径：browser_path 是 README/前端使用的名字，
            # chrome_path 是内部字段名，两者都接受，显式配置优先于自动检测。
            for key in ("browser_path", "chrome_path"):
                if browser.get(key):
                    self.browser.chrome_path = str(browser[key])
            if browser.get("user_data_dir"):
                self.browser.user_data_dir = str(browser["user_data_dir"])
            if "debug_port" in browser:
                self.browser.debug_port = int(browser["debug_port"])

        # 登录配置
        login = data.get("login", {})
        if isinstance(login, dict):
            if "wait_timeout" in login:
                self.login.wait_timeout = int(login["wait_timeout"])
            if "clear_cookies_on_failure" in login:
                self.login.clear_cookies_on_failure = bool(login["clear_cookies_on_failure"])
            if "cookie_file" in login:
                self.login.cookie_file = str(login["cookie_file"])

        # 频率限制
        rl = data.get("rate_limit", {})
        if isinstance(rl, dict):
            if "enabled" in rl:
                self.greet.rate_limit.enabled = bool(rl["enabled"])
            if "max_per_hour" in rl:
                self.greet.rate_limit.max_per_hour = int(rl["max_per_hour"])
            if "max_per_day" in rl:
                self.greet.rate_limit.max_per_day = int(rl["max_per_day"])

        # 重试配置
        retry = data.get("retry", {})
        if isinstance(retry, dict):
            if "max_attempts" in retry:
                self.greet.retry.max_attempts = int(retry["max_attempts"])
            if "base_delay" in retry:
                self.greet.retry.base_delay = float(retry["base_delay"])
            if "backoff_factor" in retry:
                self.greet.retry.backoff_factor = float(retry["backoff_factor"])

        # AI 配置
        ai = data.get("ai", {})
        if isinstance(ai, dict):
            if "enabled" in ai:
                self.ai.enabled = bool(ai["enabled"])
            if "api_key" in ai:
                self.ai.api_key = str(ai["api_key"])
            if "api_base" in ai:
                self.ai.api_base = str(ai["api_base"])
            if "model" in ai:
                self.ai.model = str(ai["model"])
            if "match_threshold" in ai:
                self.ai.match_threshold = int(ai["match_threshold"])
            if "analyze_max_tokens" in ai:
                self.ai.analyze_max_tokens = int(ai["analyze_max_tokens"])
            if "judge_timeout" in ai:
                self.ai.judge_timeout = max(0, int(ai["judge_timeout"] or 0))
            if ai.get("max_tokens"):
                # 回复预算以前只认环境变量，文件里的值直接丢掉，
                # 于是面板改不动、文件里写了也不生效
                self.ai.max_tokens = int(ai["max_tokens"])
            if "probe_max_per_round" in ai:
                # 夹住而不是照收：界面填 999 就是每号每轮 999 次额外 AI 调用
                self.ai.probe_max_per_round = max(
                    0, min(20, int(ai["probe_max_per_round"])))
            if "fail_action" in ai and ai["fail_action"]:
                self.ai.fail_action = str(ai["fail_action"])
            if "providers" in ai and isinstance(ai["providers"], list):
                self.ai.providers = [
                    AIProvider(
                        name=p.get("name", f"Provider-{i+1}"),
                        api_key=p.get("api_key", ""),
                        api_base=p.get("api_base", "https://apihub.agnes-ai.com/v1"),
                        model=p.get("model", "agnes-2.5-flash"),
                        timeout=p.get("timeout", 30),
                    )
                    for i, p in enumerate(ai["providers"])
                    if isinstance(p, dict)
                ]
            if "custom_filter_keywords" in ai:
                self.ai.custom_filter_keywords = normalize_filter_keywords(
                    ai["custom_filter_keywords"])
            if "veto_only_match" in ai:
                self.ai.veto_only_match = bool(ai["veto_only_match"])
            if "custom_scoring_prompt" in ai:
                self.ai.custom_scoring_prompt = str(ai["custom_scoring_prompt"])
            if "skip_unhealthy" in ai:
                self.ai.skip_unhealthy = bool(ai["skip_unhealthy"])

        # 简历配置
        resume = data.get("resume", {})
        if isinstance(resume, dict):
            if "school" in resume:
                self.resume.school = str(resume["school"])
            if "major" in resume:
                self.resume.major = str(resume["major"])
            if "degree" in resume:
                self.resume.degree = str(resume["degree"])
            if "skills" in resume and isinstance(resume["skills"], list):
                self.resume.skills = list(resume["skills"])
            if "experience" in resume:
                self.resume.experience = str(resume["experience"])
            if "target_position" in resume:
                self.resume.target_position = str(resume["target_position"])
            if "self_intro" in resume:
                self.resume.self_intro = str(resume["self_intro"])

        # 打招呼总开关 — 显式配置优先。
        # 之前这里只要 accounts 非空就无条件置 True，导致 bot_config.json / 前端
        # 关掉「打招呼」完全无效（账号照样开始投递）。
        greet_block = data.get("greet", {})
        greet_enabled_explicit = isinstance(greet_block, dict) and "enabled" in greet_block
        if greet_enabled_explicit:
            self.greet.enabled = bool(greet_block["enabled"])

        # 账号/打招呼配置
        accounts = data.get("accounts", [])
        if isinstance(accounts, list) and accounts:
            if not greet_enabled_explicit:
                self.greet.enabled = True
            parsed_accounts = []
            for acc in accounts:
                if not isinstance(acc, dict):
                    continue
                jobs = []
                for job in acc.get("jobs", []):
                    if not isinstance(job, dict):
                        continue
                    jobs.append(JobConfig(
                        enabled=job.get("enabled", True),
                        city=job.get("city", "上海"),
                        query=job.get("query", "数据分析"),
                        job_type=str(job.get("job_type", "") or ""),
                        scroll_pages=job.get("scroll_pages", 5),
                        greeting_message=strip_default_greeting(
                            job.get("greeting_message", "")),
                        image_files=job.get("image_files", []),
                    ))
                parsed_accounts.append(AccountConfig(
                    name=acc.get("name", "主账号"),
                    enabled=acc.get("enabled", True),
                    cookie_file=acc.get("cookie_file", "zhipin_cookies.json"),
                    image_files=acc.get("image_files", []),
                    message_interval_min=acc.get("message_interval_min", 3),
                    message_interval_max=acc.get("message_interval_max", 8),
                    greeting_message=acc.get("greeting_message", ""),
                    settings=acc.get("settings")
                    if isinstance(acc.get("settings"), dict) else {},
                    jobs=jobs,
                ))
            if parsed_accounts:
                self.greet.accounts = parsed_accounts

        # 回复配置
        reply = data.get("reply", {})
        if isinstance(reply, dict):
            if "enabled" in reply:
                self.reply.enabled = bool(reply["enabled"])
            if "check_interval" in reply:
                self.reply.check_interval = int(reply["check_interval"])
            if "context_message_count" in reply:
                self.reply.context_message_count = int(reply["context_message_count"])
            if "max_replies_per_hour" in reply:
                self.reply.max_replies_per_hour = int(reply["max_replies_per_hour"])
            if "min_delay" in reply:
                self.reply.min_delay = int(reply["min_delay"])
            if "max_delay" in reply:
                self.reply.max_delay = int(reply["max_delay"])
            if "pause_on_important" in reply:
                self.reply.pause_on_important = bool(reply["pause_on_important"])
            if "resume_send_once" in reply:
                self.reply.resume_send_once = bool(reply["resume_send_once"])
            if "accept_contact_exchange" in reply:
                self.reply.accept_contact_exchange = bool(reply["accept_contact_exchange"])
            for k in ("owed_per_round", "owed_max_age_hours", "followup_after_hours",
                      "followup_gap_hours", "followup_max_times", "followup_every_minutes",
                      "pause_auto_resume_minutes"):
                if k in reply:
                    setattr(self.reply, k, int(reply[k]))
            if "followup_enabled" in reply:
                self.reply.followup_enabled = bool(reply["followup_enabled"])
            if "chat_url" in reply:
                self.reply.chat_url = str(reply["chat_url"])

        # 通知配置
        notify = data.get("notify", {})
        if isinstance(notify, dict):
            if "enabled" in notify:
                self.notify.enabled = bool(notify["enabled"])
            if "webhook_url" in notify:
                self.notify.webhook_url = str(notify["webhook_url"])

        # 日志配置
        log = data.get("log", {})
        if isinstance(log, dict):
            if "log_level" in log:
                self.log.log_level = str(log["log_level"]).upper()
            if "log_retention_days" in log:
                self.log.log_retention_days = int(log["log_retention_days"])
            if "event_log_enabled" in log:
                self.log.event_log_enabled = bool(log["event_log_enabled"])

        # 关键词回复规则
        reply_rules = data.get("reply_rules", {})
        if isinstance(reply_rules, dict):
            for rk, rv in reply_rules.items():
                if not isinstance(rk, str) or not rk.strip():
                    continue
                if rv == "send_resume" or (isinstance(rv, str) and 0 < len(rv.strip()) <= 200):
                    self.rules.reply_rules[rk.strip()] = rv

        # 回复模板
        # bot_config.json 使用 templates（小写字段名），但也兼容 reply_templates（大写字段名）
        templates = data.get("templates", {})
        if isinstance(templates, dict):
            if "salary_reply" in templates:
                self.templates.salary_reply = str(templates["salary_reply"])
            if "interview_time_reply" in templates:
                self.templates.interview_time_reply = str(templates["interview_time_reply"])
            if "job_content_reply" in templates:
                self.templates.job_content_reply = str(templates["job_content_reply"])
            if "greeting_reply" in templates:
                self.templates.greeting_reply = str(templates["greeting_reply"])
            if "default_reply" in templates:
                self.templates.default_reply = str(templates["default_reply"])
            if "resume_duplicate_reply" in templates:
                self.templates.resume_duplicate_reply = str(templates["resume_duplicate_reply"])
            if "resume_unavailable_reply" in templates:
                self.templates.resume_unavailable_reply = str(templates["resume_unavailable_reply"])
        # 兼容 config_overrides.json 风格的 reply_templates（大写字段名）
        reply_templates = data.get("reply_templates", {})
        if isinstance(reply_templates, dict):
            if "SALARY_REPLY" in reply_templates:
                self.templates.salary_reply = str(reply_templates["SALARY_REPLY"])
            if "INTERVIEW_TIME_REPLY" in reply_templates:
                self.templates.interview_time_reply = str(reply_templates["INTERVIEW_TIME_REPLY"])
            if "JOB_CONTENT_REPLY" in reply_templates:
                self.templates.job_content_reply = str(reply_templates["JOB_CONTENT_REPLY"])
            if "GREETING_REPLY" in reply_templates:
                self.templates.greeting_reply = str(reply_templates["GREETING_REPLY"])
            if "DEFAULT_REPLY" in reply_templates:
                self.templates.default_reply = str(reply_templates["DEFAULT_REPLY"])
            if "RESUME_DUPLICATE_REPLY" in reply_templates:
                self.templates.resume_duplicate_reply = str(reply_templates["RESUME_DUPLICATE_REPLY"])
            if "RESUME_UNAVAILABLE_REPLY" in reply_templates:
                self.templates.resume_unavailable_reply = str(reply_templates["RESUME_UNAVAILABLE_REPLY"])

        # 重要事件关键词
        importance_keywords = data.get("importance_keywords", [])
        if isinstance(importance_keywords, list):
            self.rules.importance_keywords = [str(k) for k in importance_keywords if k]

        # 个人画像（从 bot_config.json 的 user_profile 字段）
        user_profile = data.get("user_profile", {})
        if isinstance(user_profile, dict):
            for k, v in user_profile.items():
                if v is None:
                    continue
                if hasattr(self.user_profile, k):
                    setattr(self.user_profile, k, v)

    def _apply_user_profile(self, data: dict):
        """将 user_profile.json 的数据应用到个人画像。"""
        if not isinstance(data, dict):
            return
        for k, v in data.items():
            if v is None:
                continue
            if hasattr(self.user_profile, k):
                setattr(self.user_profile, k, v)

    def _apply_overrides(self, data: dict):
        """应用 config_overrides.json 的覆盖配置。"""
        if not isinstance(data, dict):
            return

        # 话术模板覆盖
        if "reply_templates" in data:
            rt = data["reply_templates"]
            if isinstance(rt, dict):
                if "SALARY_REPLY" in rt:
                    self.templates.salary_reply = rt["SALARY_REPLY"]
                if "INTERVIEW_TIME_REPLY" in rt:
                    self.templates.interview_time_reply = rt["INTERVIEW_TIME_REPLY"]
                if "JOB_CONTENT_REPLY" in rt:
                    self.templates.job_content_reply = rt["JOB_CONTENT_REPLY"]
                if "GREETING_REPLY" in rt:
                    self.templates.greeting_reply = rt["GREETING_REPLY"]
                if "DEFAULT_REPLY" in rt:
                    self.templates.default_reply = rt["DEFAULT_REPLY"]
                if "RESUME_DUPLICATE_REPLY" in rt:
                    self.templates.resume_duplicate_reply = rt["RESUME_DUPLICATE_REPLY"]
                if "RESUME_UNAVAILABLE_REPLY" in rt:
                    self.templates.resume_unavailable_reply = rt["RESUME_UNAVAILABLE_REPLY"]

        # 重要事件关键词覆盖
        if "importance_keywords" in data and isinstance(data["importance_keywords"], list):
            self.rules.importance_keywords = list(data["importance_keywords"])

        # 关键词规则覆盖
        if "reply_rules" in data and isinstance(data["reply_rules"], dict):
            for rk, rv in data["reply_rules"].items():
                if not isinstance(rk, str) or not rk.strip():
                    continue
                if rv == "send_resume" or (isinstance(rv, str) and 0 < len(rv.strip()) <= 200):
                    self.rules.reply_rules[rk.strip()] = rv

        # 个人画像覆盖（如果 config_overrides.json 中有 user_profile 字段）
        if "user_profile" in data and isinstance(data["user_profile"], dict):
            for k, v in data["user_profile"].items():
                if v is None:
                    continue
                if hasattr(self.user_profile, k):
                    setattr(self.user_profile, k, v)

    def _apply_env_overrides(self):
        """应用环境变量覆盖（最高优先级）。"""
        # 测试模式
        self.test_mode = os.environ.get("BOSS_BOT_TEST_MODE", "") == "1"
        self.test_page = os.environ.get("BOSS_BOT_TEST_PAGE", "")

        # 演练模式（命令行 --dry-run 经此变量下传，热重载不会丢）
        if os.environ.get("BOSS_BOT_DRY_RUN", "") == "1":
            self.dry_run = True

        # 浏览器：无头模式 / 后台运行（=0 用来临时把窗口放回桌面，比如扫码登录）
        if os.environ.get("BOSS_BOT_HEADLESS", "") == "1":
            self.browser.headless = True
        if os.environ.get("BOSS_BOT_BACKGROUND", "") in ("0", "1"):
            self.browser.background = os.environ["BOSS_BOT_BACKGROUND"] == "1"

        # AI 配置
        if os.environ.get("ENABLE_AI", "").lower() == "true":
            self.ai.enabled = True
        elif os.environ.get("ENABLE_AI", "").lower() == "false":
            self.ai.enabled = False

        env_providers = _parse_ai_providers("AI_PROVIDERS")
        if env_providers:
            self.ai.providers = [
                AIProvider(
                    name=f"EnvProvider-{i+1}",
                    api_key=p["key"],
                    api_base=p["url"],
                    model=p["model"],
                    timeout=30,
                )
                for i, p in enumerate(env_providers)
            ]
        elif not self.ai.providers:
            legacy = _build_providers_from_legacy()
            if legacy:
                self.ai.providers = [
                    AIProvider(
                        name=f"LegacyProvider-{i+1}",
                        api_key=p["key"],
                        api_base=p["url"],
                        model=p["model"],
                        timeout=30,
                    )
                    for i, p in enumerate(legacy)
                ]

        # AI 其他参数
        if os.environ.get("AI_MAX_TOKENS"):
            self.ai.max_tokens = int(os.environ["AI_MAX_TOKENS"])
        if os.environ.get("AI_FAIL_ACTION"):
            self.ai.fail_action = os.environ["AI_FAIL_ACTION"].lower()
        if os.environ.get("AI_RATE_LIMIT_WAIT"):
            self.ai.rate_limit_wait = int(os.environ["AI_RATE_LIMIT_WAIT"])
        if os.environ.get("AI_SKIP_UNHEALTHY"):
            self.ai.skip_unhealthy = os.environ["AI_SKIP_UNHEALTHY"].lower() != "false"

        # 回复配置
        if os.environ.get("PAUSE_ON_IMPORTANT"):
            self.reply.pause_on_important = os.environ["PAUSE_ON_IMPORTANT"].lower() == "true"
        if os.environ.get("RESUME_SEND_ONCE"):
            self.reply.resume_send_once = os.environ["RESUME_SEND_ONCE"].lower() == "true"

        # 通知配置
        if os.environ.get("NOTIFY_ENABLED"):
            self.notify.enabled = os.environ["NOTIFY_ENABLED"].lower() == "true"
        if os.environ.get("NOTIFY_WEBHOOK_URL"):
            self.notify.webhook_url = os.environ["NOTIFY_WEBHOOK_URL"]

        # 日志配置
        if os.environ.get("LOG_LEVEL"):
            self.log.log_level = os.environ["LOG_LEVEL"].upper()
        if os.environ.get("LOG_RETENTION_DAYS"):
            self.log.log_retention_days = int(os.environ["LOG_RETENTION_DAYS"])
        if os.environ.get("EVENT_LOG"):
            self.log.event_log_enabled = os.environ["EVENT_LOG"].lower() == "true"

        # 单引擎运行模式（CLI --greet / --reply 通过此变量下传）。
        # 必须走环境变量：主循环每轮热重载都会重新 load()，
        # 只改内存里的 config 对象会在第一次热重载时被配置文件覆盖掉。
        only_engine = os.environ.get("BOSS_BOT_ONLY_ENGINE", "").lower()
        if only_engine == "greet":
            self.reply.enabled = False
        elif only_engine == "reply":
            self.greet.enabled = False

    def _render_templates(self):
        """用个人画像渲染话术模板中的占位符。"""
        profile = self._profile_dict()
        self.templates.salary_reply = render_template(self.templates.salary_reply, profile)
        self.templates.interview_time_reply = render_template(self.templates.interview_time_reply, profile)
        self.templates.job_content_reply = render_template(self.templates.job_content_reply, profile)
        self.templates.greeting_reply = render_template(self.templates.greeting_reply, profile)
        # default_reply 和 resume_duplicate_reply 不含占位符，但也渲染以保持一致
        self.templates.default_reply = render_template(self.templates.default_reply, profile)
        self.templates.resume_duplicate_reply = render_template(self.templates.resume_duplicate_reply, profile)
        self.templates.resume_unavailable_reply = render_template(self.templates.resume_unavailable_reply, profile)

        # 同步到 reply_rules（规则中的回复也需渲染）
        self._build_default_rules()

    def _profile_dict(self) -> dict:
        """将 UserProfileConfig 转为字典（用于模板渲染）。"""
        return {
            "name": self.user_profile.name,
            "education": self.user_profile.education,
            "position": self.user_profile.position,
            "skills": self.user_profile.skills,
            "experience": self.user_profile.experience,
            "salary_expectation": self.user_profile.salary_expectation,
            "available_interview_time": self.user_profile.available_interview_time,
            "contact": self.user_profile.contact,
            "highlights": self.user_profile.highlights,
        }

    def _build_default_rules(self):
        """构建默认关键词规则集（使用渲染后的话术模板）。"""
        default_rules = {
            "简历": "send_resume",
            "发简历": "send_resume",
            "看看简历": "send_resume",
            "面试": self.templates.interview_time_reply,
            "约面试": self.templates.interview_time_reply,
            "时间安排": self.templates.interview_time_reply,
            "薪资": self.templates.salary_reply,
            "待遇": self.templates.salary_reply,
            "工资": self.templates.salary_reply,
            "多少钱": self.templates.salary_reply,
            "您好": self.templates.greeting_reply,
            "你好": self.templates.greeting_reply,
            "在吗": self.templates.greeting_reply,
            "在不在": self.templates.greeting_reply,
            "工作内容": self.templates.job_content_reply,
            "岗位职责": self.templates.job_content_reply,
            "做什么": self.templates.job_content_reply,
        }
        # 用户自定义规则覆盖默认规则
        for k, v in self.rules.reply_rules.items():
            default_rules[k] = v
        self.rules.reply_rules = default_rules

    def save(self, config_path: Optional[str] = None):
        """保存配置到 JSON 文件。

        bot_config.json 里有 UnifiedConfig 没建模的顶层键（例如前端直接写入的
        theme），to_dict() 不包含它们。整体覆盖写会把别的入口刚写进去的设置抹掉，
        表现为"主题/高级设置在保存配置后弹回默认"，所以先读回旧文件再补空位。

        两道防出厂设置的闸，都来自 2026-10-04 那次覆盖事故（2 账号 / 12 个 AI
        接口 / 17 条规则的配置被一份 3644 字节的默认值盖掉）：
          1. 覆盖写之前先把旧文件存成 <name>.bak-<时间>，出事了能原地回滚；
          2. "账号变少"且"AI 接口清空"同时出现时拒绝写盘 —— 单独删账号或单独
             清空接口都是用户在界面上的正常操作，只有加载失败退回默认值才会
             两个一起缩水。
        Args:
            config_path: 保存路径，默认为 bot_config.json
        """
        from datetime import datetime
        path = Path(config_path) if config_path else BOT_CONFIG_FILE
        data = self.to_dict()
        existing = None
        try:
            if path.exists():
                existing = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"读取 {path.name} 以保留未建模字段失败: {e}")
        if not isinstance(existing, dict):
            existing = None
        if existing and _looks_like_default_fallback(data, existing):
            logger.warning(
                f"拒绝写 {path.name}：内存里这份是出厂默认（账号 "
                f"{len(_accounts_of(existing))}→{len(_accounts_of(data))} 且 AI 接口被清空），"
                f"落盘会覆盖已有的账号/接口/规则。先排查加载为什么退回了默认值。")
            return
        if existing:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            try:
                write_json_atomic(path.with_name(f"{path.name}.bak-{stamp}"), existing)
            except OSError as e:
                logger.warning(f"备份 {path.name} 失败: {e}")
            for key, value in existing.items():
                data.setdefault(key, value)
        write_json_atomic(path, data)

    def to_dict(self) -> dict:
        """将配置转为可序列化的字典。"""
        return {
            "browser": {
                "headless": self.browser.headless,
                "background": self.browser.background,
                "viewport_width": self.browser.viewport_width,
                "viewport_height": self.browser.viewport_height,
                "page_load_timeout": self.browser.page_load_timeout,
                "custom_user_agent": self.browser.custom_user_agent,
                "proxy": self.browser.proxy,
                "browser_type": self.browser.browser_type,
                # 这三项必须写回，否则 start.bat 里的 load().save() 会把用户
                # 配置的破解版浏览器路径静默清掉
                "browser_path": self.browser.chrome_path or "",
                "user_data_dir": self.browser.user_data_dir or "",
                "debug_port": self.browser.debug_port,
            },
            "login": {
                "wait_timeout": self.login.wait_timeout,
                "clear_cookies_on_failure": self.login.clear_cookies_on_failure,
                "cookie_file": self.login.cookie_file,
            },
            "rate_limit": {
                "enabled": self.greet.rate_limit.enabled,
                "max_per_hour": self.greet.rate_limit.max_per_hour,
                "max_per_day": self.greet.rate_limit.max_per_day,
            },
            "retry": {
                "max_attempts": self.greet.retry.max_attempts,
                "base_delay": self.greet.retry.base_delay,
                "backoff_factor": self.greet.retry.backoff_factor,
            },
            "ai": {
                "enabled": self.ai.enabled,
                "api_key": self.ai.api_key,
                "api_base": self.ai.api_base,
                "model": self.ai.model,
                "match_threshold": self.ai.match_threshold,
                "max_tokens": self.ai.max_tokens,
                "analyze_max_tokens": self.ai.analyze_max_tokens,
                "judge_timeout": self.ai.judge_timeout,
                "probe_max_per_round": self.ai.probe_max_per_round,
                "fail_action": self.ai.fail_action,
                "custom_filter_keywords": list(self.ai.custom_filter_keywords),
                "veto_only_match": bool(self.ai.veto_only_match),
                "custom_scoring_prompt": self.ai.custom_scoring_prompt,
                "skip_unhealthy": self.ai.skip_unhealthy,
                "providers": [
                    {
                        "name": p.name,
                        "api_key": p.api_key,
                        "api_base": p.api_base,
                        "model": p.model,
                        "timeout": p.timeout,
                    }
                    for p in self.ai.providers
                ],
            },
            "resume": {
                "school": self.resume.school,
                "major": self.resume.major,
                "degree": self.resume.degree,
                "skills": self.resume.skills,
                "experience": self.resume.experience,
                "target_position": self.resume.target_position,
                "self_intro": self.resume.self_intro,
            },
            "accounts": [
                {
                    "name": acc.name,
                    "enabled": acc.enabled,
                    "cookie_file": acc.cookie_file,
                    "image_files": acc.image_files,
                    "message_interval_min": acc.message_interval_min,
                    "message_interval_max": acc.message_interval_max,
                    "greeting_message": acc.greeting_message,
                    "settings": dict(acc.settings or {}),
                    "jobs": [
                        {
                            "enabled": job.enabled,
                            "city": job.city,
                            "query": job.query,
                            "job_type": job.job_type,
                            "scroll_pages": job.scroll_pages,
                            "greeting_message": job.greeting_message,
                            "image_files": job.image_files,
                        }
                        for job in acc.jobs
                    ],
                }
                for acc in self.greet.accounts
            ],
            # 打招呼总开关必须写回，否则任何一次 save() 都会把
            # bot_config.json 里的 greet 段整体抹掉，下次启动又自动变成启用
            "greet": {
                "enabled": self.greet.enabled,
            },
            "reply": {
                "enabled": self.reply.enabled,
                "check_interval": self.reply.check_interval,
                "context_message_count": self.reply.context_message_count,
                "max_replies_per_hour": self.reply.max_replies_per_hour,
                "min_delay": self.reply.min_delay,
                "max_delay": self.reply.max_delay,
                "pause_on_important": self.reply.pause_on_important,
                "pause_auto_resume_minutes": self.reply.pause_auto_resume_minutes,
                "resume_send_once": self.reply.resume_send_once,
                "accept_contact_exchange": self.reply.accept_contact_exchange,
                "owed_per_round": self.reply.owed_per_round,
                "owed_max_age_hours": self.reply.owed_max_age_hours,
                "followup_enabled": self.reply.followup_enabled,
                "followup_after_hours": self.reply.followup_after_hours,
                "followup_gap_hours": self.reply.followup_gap_hours,
                "followup_max_times": self.reply.followup_max_times,
                "followup_every_minutes": self.reply.followup_every_minutes,
                "chat_url": self.reply.chat_url,
            },
            "notify": {
                "enabled": self.notify.enabled,
                "webhook_url": self.notify.webhook_url,
            },
            "log": {
                "log_level": self.log.log_level,
                "log_retention_days": self.log.log_retention_days,
                "event_log_enabled": self.log.event_log_enabled,
            },
            "reply_rules": dict(self.rules.reply_rules),
            "importance_keywords": list(self.rules.importance_keywords),
            "templates": {
                "salary_reply": self.templates.salary_reply,
                "interview_time_reply": self.templates.interview_time_reply,
                "job_content_reply": self.templates.job_content_reply,
                "greeting_reply": self.templates.greeting_reply,
                "default_reply": self.templates.default_reply,
                "resume_duplicate_reply": self.templates.resume_duplicate_reply,
                "resume_unavailable_reply": self.templates.resume_unavailable_reply,
            },
            "user_profile": {
                "name": self.user_profile.name,
                "education": self.user_profile.education,
                "position": self.user_profile.position,
                "skills": list(self.user_profile.skills),
                "experience": self.user_profile.experience,
                "salary_expectation": self.user_profile.salary_expectation,
                "available_interview_time": self.user_profile.available_interview_time,
                "contact": self.user_profile.contact,
                "highlights": list(self.user_profile.highlights),
            },
            "dry_run": self.dry_run,
            "self_evolve_enabled": self.self_evolve_enabled,
        }

    def validate(self) -> list:
        """校验配置，返回错误列表（空 = 校验通过）。"""
        errors = []

        if not isinstance(self.browser.page_load_timeout, (int, float)) or self.browser.page_load_timeout <= 0:
            errors.append("browser.page_load_timeout 必须是正数")

        if self.greet.rate_limit.max_per_hour < 0:
            errors.append("rate_limit.max_per_hour 必须是非负数")
        if self.greet.rate_limit.max_per_day < 0:
            errors.append("rate_limit.max_per_day 必须是非负数")

        if not self.greet.accounts:
            errors.append("至少需要一个账号配置")
        else:
            for i, acc in enumerate(self.greet.accounts):
                if not acc.jobs:
                    errors.append(f"账号「{acc.name}」至少需要一个岗位")

        if self.ai.fail_action not in ("skip", "default"):
            errors.append("ai.fail_action 必须是 skip 或 default")

        if self.reply.min_delay > self.reply.max_delay:
            errors.append("reply.min_delay 不能大于 reply.max_delay")

        return errors

    def flatten_jobs_for_run(self) -> list:
        """将配置展平为任务列表，每个任务为一个 (账号, 岗位) 组合。

        Returns:
            任务字典列表，每个包含 account_name/city/query/cookie_file 等字段
        """
        tasks = []
        for acc in self.greet.accounts:
            if not acc.enabled:
                continue
            for job in acc.jobs:
                if not job.enabled:
                    continue
                tasks.append({
                    "account_name": acc.name,
                    "city": job.city,
                    "query": job.query,
                    "scroll_pages": job.scroll_pages,
                    "greeting_message": job.greeting_message,
                    "cookie_file": acc.cookie_file,
                    "image_files": job.image_files,
                    "message_interval_min": acc.message_interval_min,
                    "message_interval_max": acc.message_interval_max,
                })
        return tasks


# ===================== 兼容函数 =====================

def load_user_profile() -> dict:
    """加载个人画像配置（兼容 BOSS-auto-reply-bot 接口）。

    Returns:
        个人画像字典
    """
    default = {
        "name": "求职者",
        "education": "本科学历",
        "position": "数据分析",
        "skills": [],
        "experience": "",
        "salary_expectation": "面议",
        "available_interview_time": "工作日下午",
        "contact": "",
        "highlights": [],
    }
    try:
        if USER_PROFILE_FILE.exists():
            with open(USER_PROFILE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            default.update({k: v for k, v in data.items() if v is not None})
    except Exception as e:
        logger.warning(f"加载个人画像失败，使用默认值: {e}")
    return default


def load_config() -> dict:
    """加载 bot_config.json 并返回字典格式（兼容 auto_boss 接口）。

    Returns:
        完整配置字典，结构与 auto_boss 的 DEFAULT_CONFIG 一致
    """
    if not BOT_CONFIG_FILE.exists():
        return _default_config_dict()

    try:
        with open(BOT_CONFIG_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
    except (json.JSONDecodeError, OSError):
        return _default_config_dict()

    merged = _default_config_dict()
    _fill_defaults(saved, merged)
    merged = saved

    for acc in merged.get("accounts", []):
        acc.setdefault("image_files", [])
        acc.setdefault("message_interval_min", 3)
        acc.setdefault("message_interval_max", 8)
        acc.setdefault("greeting_message", "")
        acc.setdefault("settings", {})
        for job in acc.get("jobs", []):
            job.setdefault("greeting_message", "")
            job.setdefault("scroll_pages", 5)
            job.setdefault("city", "上海")
            job.setdefault("enabled", True)

    merged.setdefault("ai", copy.deepcopy(_default_config_dict()["ai"]))
    merged.setdefault("resume", copy.deepcopy(_default_config_dict()["resume"]))

    ai = merged.get("ai", {})
    if not ai.get("providers") and ai.get("api_key"):
        ai["providers"] = [{
            "name": ai.get("name", "默认"),
            "api_key": ai.get("api_key", ""),
            "api_base": ai.get("api_base", "https://apihub.agnes-ai.com/v1"),
            "model": ai.get("model", "agnes-2.5-flash"),
            "timeout": 30,
        }]

    return merged


def save_config(cfg: dict) -> None:
    """保存配置字典到 bot_config.json（兼容 auto_boss 接口）。

    前端 POST /api/config 会把整份配置回写，而 to_dict() 不含 theme 这类
    未建模键 —— 不补空位的话，改完主题再点一次保存就把主题抹掉了。
    """
    data = dict(cfg)
    try:
        if BOT_CONFIG_FILE.exists():
            existing = json.loads(BOT_CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                for key, value in existing.items():
                    data.setdefault(key, value)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"读取 bot_config.json 以保留未建模字段失败: {e}")
    with open(BOT_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def save_overrides(cfg: dict) -> None:
    """将 reply_rules、reply_templates、importance_keywords、user_profile 同步写入 config_overrides.json。

    字段名映射：
      - bot_config.json 的 templates（小写字段名）→ config_overrides.json 的 reply_templates（大写字段名）
      - reply_rules、importance_keywords、user_profile 字段名保持一致

    保留 config_overrides.json 中已有的 system_rules、user_prompt_template 等其他字段。

    Args:
        cfg: 来自前端的配置字典（与 bot_config.json 结构一致）
    """
    if not isinstance(cfg, dict):
        return

    # 读取现有 config_overrides.json，保留其他字段
    existing = {}
    if OVERRIDES_FILE.exists():
        try:
            with open(OVERRIDES_FILE, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if not isinstance(existing, dict):
                existing = {}
        except (json.JSONDecodeError, OSError):
            existing = {}

    # 同步 reply_rules
    if "reply_rules" in cfg and isinstance(cfg["reply_rules"], dict):
        existing["reply_rules"] = dict(cfg["reply_rules"])

    # 同步 importance_keywords
    if "importance_keywords" in cfg and isinstance(cfg["importance_keywords"], list):
        existing["importance_keywords"] = list(cfg["importance_keywords"])

    # 同步 user_profile
    if "user_profile" in cfg and isinstance(cfg["user_profile"], dict):
        existing["user_profile"] = dict(cfg["user_profile"])

    # 同步 templates → reply_templates（字段名映射：小写 → 大写）
    templates = cfg.get("templates", {})
    if isinstance(templates, dict) and templates:
        rt = existing.get("reply_templates", {})
        if not isinstance(rt, dict):
            rt = {}
        if "salary_reply" in templates:
            rt["SALARY_REPLY"] = str(templates["salary_reply"])
        if "interview_time_reply" in templates:
            rt["INTERVIEW_TIME_REPLY"] = str(templates["interview_time_reply"])
        if "job_content_reply" in templates:
            rt["JOB_CONTENT_REPLY"] = str(templates["job_content_reply"])
        if "greeting_reply" in templates:
            rt["GREETING_REPLY"] = str(templates["greeting_reply"])
        if "default_reply" in templates:
            rt["DEFAULT_REPLY"] = str(templates["default_reply"])
        if "resume_duplicate_reply" in templates:
            rt["RESUME_DUPLICATE_REPLY"] = str(templates["resume_duplicate_reply"])
        if "resume_unavailable_reply" in templates:
            rt["RESUME_UNAVAILABLE_REPLY"] = str(templates["resume_unavailable_reply"])
        existing["reply_templates"] = rt

    with open(OVERRIDES_FILE, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)


def validate_config(cfg: dict) -> list:
    """校验配置字典，返回错误列表（兼容 auto_boss 接口）。"""
    errors = []
    if not isinstance(cfg, dict):
        errors.append("配置必须是一个对象")
        return errors

    browser = cfg.get("browser", {})
    if isinstance(browser, dict):
        if not isinstance(browser.get("page_load_timeout", 30), (int, float)):
            errors.append("browser.page_load_timeout 必须是数字")

    rl = cfg.get("rate_limit", {})
    if isinstance(rl, dict):
        for k in ("max_per_hour", "max_per_day"):
            v = rl.get(k, 0)
            if not isinstance(v, (int, float)) or v < 0:
                errors.append(f"rate_limit.{k} 必须是非负数")

    accounts = cfg.get("accounts", [])
    if not isinstance(accounts, list) or not accounts:
        errors.append("至少需要一个账号配置")
        return errors

    for ai_idx, acc in enumerate(accounts):
        if not isinstance(acc, dict):
            errors.append(f"账号 #{ai_idx} 必须是一个对象")
            continue
        jobs = acc.get("jobs", [])
        if not isinstance(jobs, list) or not jobs:
            errors.append(f"账号「{acc.get('name', ai_idx)}」至少需要一个岗位")

    return errors


def _default_config_dict() -> dict:
    """返回 auto_boss 兼容的默认配置字典。"""
    return {
        "browser": {
            "headless": False,
            "background": True,
            "viewport_width": 1280,
            "viewport_height": 800,
            "page_load_timeout": 30,
            "custom_user_agent": "",
            "proxy": "",
            "browser_type": "chrome",
        },
        "login": {
            "wait_timeout": 300,
            "clear_cookies_on_failure": True,
        },
        "rate_limit": {
            "enabled": True,
            "max_per_hour": 30,
            "max_per_day": 100,
        },
        "retry": {
            "max_attempts": 3,
            "base_delay": 2.0,
            "backoff_factor": 2.0,
        },
        "ai": {
            "enabled": False,
            "api_key": "",
            "api_base": "https://apihub.agnes-ai.com/v1",
            "model": "agnes-2.5-flash",
            "match_threshold": 70,
            "providers": [
                {
                    "name": "Agnes",
                    "api_key": "",
                    "api_base": "https://apihub.agnes-ai.com/v1",
                    "model": "agnes-2.5-flash",
                    "timeout": 30,
                }
            ],
        },
        "resume": {
            "school": "",
            "major": "",
            "degree": "",
            "skills": [],
            "experience": "",
            "target_position": "",
            "self_intro": "",
        },
        "accounts": [
            {
                "name": "主账号",
                "enabled": True,
                "cookie_file": "zhipin_cookies.json",
                "image_files": [],
                "message_interval_min": 3,
                "message_interval_max": 8,
                "jobs": [
                    {
                        "enabled": True,
                        "city": "上海",
                        "query": "数据分析",
                        "scroll_pages": 5,
                        "greeting_message": "",
                        "image_files": [],
                    },
                ],
            }
        ],
    }


def _fill_defaults(target: dict, default: dict) -> dict:
    """递归填充缺失的默认值（不覆盖已存在的键）。"""
    for key, val in default.items():
        if key not in target:
            target[key] = copy.deepcopy(val)
        elif isinstance(val, dict) and isinstance(target[key], dict):
            _fill_defaults(target[key], val)
    return target
