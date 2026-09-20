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

# 默认打招呼话术
DEFAULT_GREETING = (
    "您好，我是双一流的本科，应聘数据分析岗位。在校系统学习数据分析相关知识，"
    "掌握Excel、基础SQL与数据整理技能，具备数据思维。做事严谨细心，学习能力强，"
    "愿意踏实积累。十分认可贵公司，希望能获得面试机会。"
)

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
    scroll_pages: int = 5
    greeting_message: str = DEFAULT_GREETING
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
    max_replies_per_hour: int = 30
    min_delay: int = 2
    max_delay: int = 5
    pause_on_important: bool = True
    resume_send_once: bool = True
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
    max_tokens: int = 200
    rate_limit_wait: int = 30
    match_threshold: int = 70            # auto_boss: match_threshold
    api_key: str = ""                    # 兼容旧格式
    api_base: str = "https://apihub.agnes-ai.com/v1"  # 兼容旧格式
    model: str = "agnes-2.5-flash"       # 兼容旧格式


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

    @classmethod
    def load(cls, config_path: Optional[str] = None,
             profile_path: Optional[str] = None) -> "UnifiedConfig":
        """加载统一配置。

        加载顺序：代码默认值 → JSON 配置文件 → 环境变量

        Args:
            config_path: bot_config.json 路径，默认使用项目根目录下的
            profile_path: user_profile.json 路径，默认使用项目根目录下的

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
        cfg_path = Path(config_path) if config_path else BOT_CONFIG_FILE
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
        if OVERRIDES_FILE.exists():
            try:
                with open(OVERRIDES_FILE, "r", encoding="utf-8") as f:
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

    def _apply_bot_config(self, data: dict):
        """将 bot_config.json 的数据应用到各配置域。"""
        if not isinstance(data, dict):
            return

        # 浏览器配置
        browser = data.get("browser", {})
        if isinstance(browser, dict):
            if "headless" in browser:
                self.browser.headless = bool(browser["headless"])
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

        # 账号/打招呼配置
        accounts = data.get("accounts", [])
        if isinstance(accounts, list) and accounts:
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
                        scroll_pages=job.get("scroll_pages", 5),
                        greeting_message=job.get("greeting_message", DEFAULT_GREETING),
                        image_files=job.get("image_files", []),
                    ))
                parsed_accounts.append(AccountConfig(
                    name=acc.get("name", "主账号"),
                    enabled=acc.get("enabled", True),
                    cookie_file=acc.get("cookie_file", "zhipin_cookies.json"),
                    image_files=acc.get("image_files", []),
                    message_interval_min=acc.get("message_interval_min", 3),
                    message_interval_max=acc.get("message_interval_max", 8),
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

        # 浏览器：无头模式
        if os.environ.get("BOSS_BOT_HEADLESS", "") == "1":
            self.browser.headless = True

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

        Args:
            config_path: 保存路径，默认为 bot_config.json
        """
        path = Path(config_path) if config_path else BOT_CONFIG_FILE
        data = self.to_dict()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def to_dict(self) -> dict:
        """将配置转为可序列化的字典。"""
        return {
            "browser": {
                "headless": self.browser.headless,
                "viewport_width": self.browser.viewport_width,
                "viewport_height": self.browser.viewport_height,
                "page_load_timeout": self.browser.page_load_timeout,
                "custom_user_agent": self.browser.custom_user_agent,
                "proxy": self.browser.proxy,
                "browser_type": self.browser.browser_type,
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
                "fail_action": self.ai.fail_action,
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
                    "jobs": [
                        {
                            "enabled": job.enabled,
                            "city": job.city,
                            "query": job.query,
                            "scroll_pages": job.scroll_pages,
                            "greeting_message": job.greeting_message,
                            "image_files": job.image_files,
                        }
                        for job in acc.jobs
                    ],
                }
                for acc in self.greet.accounts
            ],
            "reply": {
                "enabled": self.reply.enabled,
                "check_interval": self.reply.check_interval,
                "context_message_count": self.reply.context_message_count,
                "max_replies_per_hour": self.reply.max_replies_per_hour,
                "min_delay": self.reply.min_delay,
                "max_delay": self.reply.max_delay,
                "pause_on_important": self.reply.pause_on_important,
                "resume_send_once": self.reply.resume_send_once,
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
        for job in acc.get("jobs", []):
            job.setdefault("greeting_message", DEFAULT_GREETING)
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
    """保存配置字典到 bot_config.json（兼容 auto_boss 接口）。"""
    with open(BOT_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


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
                        "greeting_message": DEFAULT_GREETING,
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
