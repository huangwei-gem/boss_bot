"""
BOSS 自动回复机器人 - 配置兼容层

作为 unified_config 的快捷访问入口，保持与 BOSS-auto-reply-bot 原有
config.py 的接口兼容，让 reply_engine.py 等模块能直接使用：

    from boss_bot.config import CHECK_INTERVAL, SALARY_REPLY, ...

所有配置值在模块加载时从 UnifiedConfig 实例中提取，确保与统一配置
管理器保持一致。同时保留 auto_boss 的 load_config/save_config 接口。
"""

import os
from pathlib import Path

from boss_bot.unified_config import (
    UnifiedConfig,
    BASE_DIR,
    USER_PROFILE_FILE,
    BOT_CONFIG_FILE,
    render_template,
    load_user_profile,
    _parse_ai_providers,
    load_config as _load_config_dict,
    save_config as _save_config_dict,
    validate_config as _validate_config_dict,
)

# 状态与数据文件路径
STATE_FILE = BASE_DIR / "bot_state.json"
STATS_FILE = BASE_DIR / "bot_stats.json"
NOTIFY_FILE = BASE_DIR / "notifications.json"
LOG_DIR = BASE_DIR / "logs"

# 加载统一配置实例
_cfg = UnifiedConfig.load()

# ===================== 测试模式 =====================

TEST_MODE = _cfg.test_mode
TEST_PAGE = _cfg.test_page

# ===================== 浏览器配置 =====================

HEADLESS = _cfg.browser.headless

# ===================== 基础回复配置 =====================

CHECK_INTERVAL = _cfg.reply.check_interval
MIN_DELAY = _cfg.reply.min_delay
MAX_DELAY = _cfg.reply.max_delay
MAX_REPLIES_PER_HOUR = _cfg.reply.max_replies_per_hour
CONTEXT_MESSAGE_COUNT = _cfg.reply.context_message_count
CHAT_URL = _cfg.reply.chat_url

# ===================== 登录配置 =====================

COOKIE_FILE = _cfg.login.cookie_file

# ===================== 个人画像配置 =====================

PROFILE_FILE = USER_PROFILE_FILE
USER_PROFILE = _cfg._profile_dict()

# ===================== AI 配置 =====================

ENABLE_AI = _cfg.ai.enabled

# AI 提供商列表（兼容旧格式：key/model/url）
AI_PROVIDERS = [
    {"key": p.api_key, "model": p.model, "url": p.api_base}
    for p in _cfg.ai.providers
]

# 快捷访问属性（兼容旧代码）
AI_API_KEYS = [p["key"] for p in AI_PROVIDERS]
AI_MODELS = [p["model"] for p in AI_PROVIDERS]
AI_BASE_URL = AI_PROVIDERS[0]["url"] if AI_PROVIDERS else ""

AI_MAX_TOKENS = _cfg.ai.max_tokens
AI_FAIL_ACTION = _cfg.ai.fail_action
AI_RATE_LIMIT_WAIT = _cfg.ai.rate_limit_wait

# ===================== 回复内容配置（话术模板） =====================

SALARY_REPLY = _cfg.templates.salary_reply
INTERVIEW_TIME_REPLY = _cfg.templates.interview_time_reply
JOB_CONTENT_REPLY = _cfg.templates.job_content_reply
GREETING_REPLY = _cfg.templates.greeting_reply
DEFAULT_REPLY = _cfg.templates.default_reply
RESUME_DUPLICATE_REPLY = _cfg.templates.resume_duplicate_reply
RESUME_UNAVAILABLE_REPLY = _cfg.templates.resume_unavailable_reply

# ===================== 规则配置 =====================

REPLY_RULES = _cfg.rules.reply_rules
IMPORTANCE_KEYWORDS = _cfg.rules.importance_keywords

# ===================== 意图识别与重要事件 =====================

PAUSE_ON_IMPORTANT = _cfg.reply.pause_on_important
RESUME_SEND_ONCE = _cfg.reply.resume_send_once

# ===================== 通知配置 =====================

NOTIFY_ENABLED = _cfg.notify.enabled
NOTIFY_WEBHOOK_URL = _cfg.notify.webhook_url

# ===================== 日志配置 =====================

LOG_LEVEL = _cfg.log.log_level
LOG_RETENTION_DAYS = _cfg.log.log_retention_days
EVENT_LOG_ENABLED = _cfg.log.event_log_enabled

# ===================== auto_boss 兼容接口 =====================

# 配置文件路径（兼容 auto_boss）
CONFIG_FILE = str(BOT_CONFIG_FILE)


def load_config() -> dict:
    """加载配置字典（兼容 auto_boss 接口）。

    返回与 auto_boss DEFAULT_CONFIG 结构一致的字典。
    """
    return _load_config_dict()


def save_config(cfg: dict) -> None:
    """保存配置字典到文件（兼容 auto_boss 接口）。"""
    _save_config_dict(cfg)


def validate_config(cfg: dict) -> list:
    """校验配置字典（兼容 auto_boss 接口）。

    Returns:
        错误列表，空列表表示校验通过
    """
    return _validate_config_dict(cfg)


def get_unified_config() -> UnifiedConfig:
    """获取当前 UnifiedConfig 实例。

    用于需要访问完整配置对象的场景：
        cfg = get_unified_config()
        cfg.browser.headless
        cfg.greet.accounts
    """
    return _cfg


def reload_config() -> UnifiedConfig:
    """重新加载配置（运行时热更新）。

    重新从 JSON 文件和环境变量加载配置，返回新的 UnifiedConfig 实例。
    注意：此函数不会自动更新模块级常量，需要重新 import 才能生效。
    """
    return UnifiedConfig.load()
