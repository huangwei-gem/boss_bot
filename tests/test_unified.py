"""
BOSS Bot 统一项目单元测试

覆盖配置系统、规则引擎、意图分类、状态存储、统计、消息存储、
通知、回复引擎、打招呼引擎、主循环和 Flask 应用。

使用 pytest 框架，通过 mock/patch 隔离外部依赖（浏览器、AI API、文件系统），
确保测试可以在没有浏览器和 API Key 的环境下运行。
"""

import pytest
import os
import json
import time
import threading
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open
from datetime import datetime


# ===================== 配置系统测试 =====================

class UnifiedConfigTest:
    """UnifiedConfig 类的单元测试"""

    def test_default_config(self):
        """测试默认配置值"""
        from boss_bot.unified_config import UnifiedConfig
        cfg = UnifiedConfig()
        assert cfg.browser.headless is False
        assert cfg.browser.viewport_width == 1280
        assert cfg.browser.viewport_height == 800
        assert cfg.browser.page_load_timeout == 30
        assert cfg.browser.browser_type == "chrome"
        assert cfg.login.wait_timeout == 300
        assert cfg.login.clear_cookies_on_failure is True
        assert cfg.reply.check_interval == 8
        assert cfg.reply.max_replies_per_hour == 30
        assert cfg.reply.min_delay == 2
        assert cfg.reply.max_delay == 5
        assert cfg.reply.pause_on_important is True
        assert cfg.ai.enabled is False
        assert cfg.ai.fail_action == "default"
        assert cfg.ai.max_tokens == 200
        assert cfg.test_mode is False
        assert cfg.test_page == ""

    def test_load_with_no_files(self, tmp_path):
        """测试没有配置文件时的加载"""
        from boss_bot.unified_config import UnifiedConfig
        cfg = UnifiedConfig.load(
            config_path=str(tmp_path / "nonexistent.json"),
            profile_path=str(tmp_path / "nonexistent_profile.json"),
            overrides_path=str(tmp_path / "nonexistent_overrides.json")
        )
        assert cfg is not None
        assert cfg.browser.headless is False
        assert cfg.reply.check_interval == 8

    def test_load_with_bot_config(self, tmp_path):
        """测试从 bot_config.json 加载"""
        from boss_bot.unified_config import UnifiedConfig
        config_data = {
            "browser": {"headless": True, "viewport_width": 1920},
            "reply": {"check_interval": 5, "max_replies_per_hour": 50},
            "ai": {"enabled": True, "api_key": "test-key"},
        }
        config_file = tmp_path / "bot_config.json"
        config_file.write_text(json.dumps(config_data), encoding="utf-8")

        cfg = UnifiedConfig.load(
            config_path=str(config_file),
            profile_path=str(tmp_path / "nonexistent.json"),
            overrides_path=str(tmp_path / "nonexistent_overrides.json")
        )
        assert cfg.browser.headless is True
        assert cfg.browser.viewport_width == 1920
        assert cfg.reply.check_interval == 5
        assert cfg.reply.max_replies_per_hour == 50
        assert cfg.ai.enabled is True
        assert cfg.ai.api_key == "test-key"

    def test_load_with_user_profile(self, tmp_path):
        """测试从 user_profile.json 加载"""
        from boss_bot.unified_config import UnifiedConfig
        profile_data = {
            "name": "张三",
            "position": "Python开发",
            "skills": ["Python", "SQL"],
            "salary_expectation": "15-20K",
        }
        profile_file = tmp_path / "user_profile.json"
        profile_file.write_text(json.dumps(profile_data), encoding="utf-8")

        cfg = UnifiedConfig.load(
            config_path=str(tmp_path / "nonexistent.json"),
            profile_path=str(profile_file),
            overrides_path=str(tmp_path / "nonexistent_overrides.json")
        )
        assert cfg.user_profile.name == "张三"
        assert cfg.user_profile.position == "Python开发"
        assert cfg.user_profile.skills == ["Python", "SQL"]
        assert cfg.user_profile.salary_expectation == "15-20K"

    def test_overrides_beat_user_profile(self, tmp_path):
        """config_overrides.json 是最高优先级的用户画像来源（前端保存走这里）"""
        from boss_bot.unified_config import UnifiedConfig
        profile_file = tmp_path / "user_profile.json"
        profile_file.write_text(json.dumps({"name": "画像名字"}), encoding="utf-8")
        overrides_file = tmp_path / "config_overrides.json"
        overrides_file.write_text(
            json.dumps({"user_profile": {"name": "覆盖名字"}}), encoding="utf-8")

        cfg = UnifiedConfig.load(
            config_path=str(tmp_path / "nonexistent.json"),
            profile_path=str(profile_file),
            overrides_path=str(overrides_file)
        )
        assert cfg.user_profile.name == "覆盖名字"

    def test_load_browser_executable_keys(self, tmp_path):
        """browser_path/chrome_path/user_data_dir/debug_port 必须真正生效（README 承诺可配）"""
        from boss_bot.unified_config import UnifiedConfig
        config_file = tmp_path / "bot_config.json"
        config_file.write_text(json.dumps({
            "browser": {
                "browser_path": "C:/cloak/chrome.exe",
                "user_data_dir": "C:/cloak/User Data",
                "debug_port": 9333,
            }
        }), encoding="utf-8")

        cfg = UnifiedConfig.load(
            config_path=str(config_file),
            profile_path=str(tmp_path / "nonexistent.json"),
            overrides_path=str(tmp_path / "nonexistent_overrides.json")
        )
        assert cfg.browser.chrome_path == "C:/cloak/chrome.exe"
        assert cfg.browser.user_data_dir == "C:/cloak/User Data"
        assert cfg.browser.debug_port == 9333

    def test_browser_path_survives_save_roundtrip(self, tmp_path):
        """load().save() 不能把破解版浏览器路径写没了（start.bat 会执行这一步）"""
        from boss_bot.unified_config import UnifiedConfig
        config_file = tmp_path / "bot_config.json"
        config_file.write_text(json.dumps({
            "browser": {"browser_path": "D:/cloak/chrome.exe", "debug_port": 9444}
        }), encoding="utf-8")

        cfg = UnifiedConfig.load(
            config_path=str(config_file),
            profile_path=str(tmp_path / "none.json"),
            overrides_path=str(tmp_path / "none_overrides.json")
        )
        cfg.save(str(config_file))
        saved = json.loads(config_file.read_text(encoding="utf-8"))
        assert saved["browser"]["browser_path"] == "D:/cloak/chrome.exe"
        assert saved["browser"]["debug_port"] == 9444

        again = UnifiedConfig.load(
            config_path=str(config_file),
            profile_path=str(tmp_path / "none.json"),
            overrides_path=str(tmp_path / "none_overrides.json")
        )
        assert again.browser.chrome_path == "D:/cloak/chrome.exe"
        assert again.browser.debug_port == 9444

    def test_greet_enabled_is_honored(self, tmp_path):
        """greet.enabled=false 必须真的关掉打招呼（曾经被 accounts 无条件覆盖成 True）"""
        from boss_bot.unified_config import UnifiedConfig
        config_file = tmp_path / "bot_config.json"
        config_file.write_text(json.dumps({
            "greet": {"enabled": False},
            "accounts": [{"name": "主账号", "enabled": True,
                          "jobs": [{"city": "上海", "query": "数据分析"}]}],
        }), encoding="utf-8")

        cfg = UnifiedConfig.load(
            config_path=str(config_file),
            profile_path=str(tmp_path / "none.json"),
            overrides_path=str(tmp_path / "none_overrides.json")
        )
        assert cfg.greet.enabled is False
        assert len(cfg.greet.accounts) == 1   # 账号本身照常解析

    def test_greet_enabled_survives_save_roundtrip(self, tmp_path):
        """save() 不能把 greet 段写没了，否则下次启动又自动启用"""
        from boss_bot.unified_config import UnifiedConfig
        config_file = tmp_path / "bot_config.json"
        config_file.write_text(json.dumps({"greet": {"enabled": False}}), encoding="utf-8")

        cfg = UnifiedConfig.load(
            config_path=str(config_file),
            profile_path=str(tmp_path / "none.json"),
            overrides_path=str(tmp_path / "none_overrides.json")
        )
        cfg.save(str(config_file))
        saved = json.loads(config_file.read_text(encoding="utf-8"))
        assert saved["greet"]["enabled"] is False

    def test_save_and_reload(self, tmp_path):
        """测试保存和重新加载配置"""
        from boss_bot.unified_config import UnifiedConfig
        cfg = UnifiedConfig()
        cfg.browser.headless = True
        cfg.reply.check_interval = 15

        config_file = tmp_path / "bot_config.json"
        cfg.save(str(config_file))

        assert config_file.exists()
        saved_data = json.loads(config_file.read_text(encoding="utf-8"))
        assert saved_data["browser"]["headless"] is True
        assert saved_data["reply"]["check_interval"] == 15

    def test_to_dict(self):
        """测试配置转字典"""
        from boss_bot.unified_config import UnifiedConfig
        cfg = UnifiedConfig()
        d = cfg.to_dict()
        assert isinstance(d, dict)
        assert "browser" in d
        assert "login" in d
        assert "ai" in d
        assert "reply" in d
        assert "accounts" in d
        assert d["browser"]["headless"] is False
        assert d["reply"]["check_interval"] == 8

    def test_validate_ok(self):
        """测试配置校验通过"""
        from boss_bot.unified_config import UnifiedConfig
        cfg = UnifiedConfig()
        errors = cfg.validate()
        assert errors == []

    def test_validate_fail_action(self):
        """测试 fail_action 校验"""
        from boss_bot.unified_config import UnifiedConfig
        cfg = UnifiedConfig()
        cfg.ai.fail_action = "invalid"
        errors = cfg.validate()
        assert any("fail_action" in e for e in errors)

    def test_validate_delay(self):
        """测试 min_delay > max_delay 校验"""
        from boss_bot.unified_config import UnifiedConfig
        cfg = UnifiedConfig()
        cfg.reply.min_delay = 10
        cfg.reply.max_delay = 5
        errors = cfg.validate()
        assert any("min_delay" in e for e in errors)

    def test_validate_no_accounts(self):
        """测试无账号配置校验"""
        from boss_bot.unified_config import UnifiedConfig
        cfg = UnifiedConfig()
        cfg.greet.accounts = []
        errors = cfg.validate()
        assert any("账号" in e for e in errors)

    def test_env_overrides_test_mode(self):
        """测试环境变量覆盖测试模式"""
        from boss_bot.unified_config import UnifiedConfig
        with patch.dict(os.environ, {"BOSS_BOT_TEST_MODE": "1", "BOSS_BOT_TEST_PAGE": "file:///test.html"}):
            cfg = UnifiedConfig()
            cfg._apply_env_overrides()
            assert cfg.test_mode is True
            assert cfg.test_page == "file:///test.html"

    def test_env_overrides_headless(self):
        """测试环境变量覆盖无头模式"""
        from boss_bot.unified_config import UnifiedConfig
        with patch.dict(os.environ, {"BOSS_BOT_HEADLESS": "1"}):
            cfg = UnifiedConfig()
            cfg._apply_env_overrides()
            assert cfg.browser.headless is True

    def test_env_overrides_ai_enabled(self):
        """测试环境变量覆盖 AI 启用"""
        from boss_bot.unified_config import UnifiedConfig
        with patch.dict(os.environ, {"ENABLE_AI": "true"}):
            cfg = UnifiedConfig()
            cfg._apply_env_overrides()
            assert cfg.ai.enabled is True

    def test_env_overrides_ai_providers(self):
        """测试环境变量覆盖 AI 提供商"""
        from boss_bot.unified_config import UnifiedConfig
        with patch.dict(os.environ, {
            "AI_PROVIDERS_1": "sk-test|gpt-4|https://api.openai.com/v1",
            "AI_PROVIDERS_2": "sk-test2|claude-3|https://api.anthropic.com/v1",
        }):
            cfg = UnifiedConfig()
            cfg._apply_env_overrides()
            assert len(cfg.ai.providers) == 2
            assert cfg.ai.providers[0].api_key == "sk-test"
            assert cfg.ai.providers[0].model == "gpt-4"
            assert cfg.ai.providers[1].api_key == "sk-test2"

    def test_env_overrides_log_level(self):
        """测试环境变量覆盖日志级别"""
        from boss_bot.unified_config import UnifiedConfig
        with patch.dict(os.environ, {"LOG_LEVEL": "DEBUG"}):
            cfg = UnifiedConfig()
            cfg._apply_env_overrides()
            assert cfg.log.log_level == "DEBUG"

    def test_profile_dict(self):
        """测试 _profile_dict 方法"""
        from boss_bot.unified_config import UnifiedConfig
        cfg = UnifiedConfig()
        cfg.user_profile.name = "测试用户"
        cfg.user_profile.skills = ["Python", "SQL"]
        d = cfg._profile_dict()
        assert d["name"] == "测试用户"
        assert d["skills"] == ["Python", "SQL"]
        assert d["education"] == "本科学历"
        assert d["position"] == "数据分析"

    def test_flatten_jobs_for_run(self):
        """测试 flatten_jobs_for_run 方法"""
        from boss_bot.unified_config import UnifiedConfig, AccountConfig, JobConfig
        cfg = UnifiedConfig()
        cfg.greet.accounts = [
            AccountConfig(name="账号1", jobs=[
                JobConfig(city="上海", query="数据分析"),
                JobConfig(city="北京", query="Python开发"),
            ]),
            AccountConfig(name="账号2", enabled=False, jobs=[
                JobConfig(city="深圳", query="产品经理"),
            ]),
        ]
        tasks = cfg.flatten_jobs_for_run()
        assert len(tasks) == 2
        assert tasks[0]["city"] == "上海"
        assert tasks[1]["city"] == "北京"

    def test_build_default_rules(self):
        """测试 _build_default_rules 方法"""
        from boss_bot.unified_config import UnifiedConfig
        cfg = UnifiedConfig()
        cfg._build_default_rules()
        rules = cfg.rules.reply_rules
        assert "简历" in rules
        assert rules["简历"] == "send_resume"
        assert "面试" in rules
        assert "薪资" in rules
        assert "您好" in rules


class RenderTemplateTest:
    """render_template 函数的单元测试"""

    def test_basic_replacement(self):
        """测试基本占位符替换"""
        from boss_bot.unified_config import render_template
        profile = {"salary_expectation": "15-20K", "position": "数据分析"}
        result = render_template("期望薪资 {salary}", profile)
        assert result == "期望薪资 15-20K"

    def test_position_replacement(self):
        """测试岗位占位符替换"""
        from boss_bot.unified_config import render_template
        profile = {"position": "Python开发"}
        result = render_template("应聘 {position} 岗位", profile)
        assert result == "应聘 Python开发 岗位"

    def test_list_field(self):
        """测试列表字段替换（用顿号拼接）"""
        from boss_bot.unified_config import render_template
        profile = {"skills": ["Python", "SQL", "Excel"]}
        result = render_template("掌握 {skills}", profile)
        assert result == "掌握 Python、SQL、Excel"

    def test_missing_field(self):
        """测试缺失字段替换为空字符串"""
        from boss_bot.unified_config import render_template
        profile = {}
        result = render_template("期望薪资 {salary}", profile)
        assert result == "期望薪资 "

    def test_no_placeholders(self):
        """测试无占位符的文本"""
        from boss_bot.unified_config import render_template
        profile = {"salary_expectation": "15K"}
        result = render_template("你好，感谢消息", profile)
        assert result == "你好，感谢消息"

    def test_empty_text(self):
        """测试空文本"""
        from boss_bot.unified_config import render_template
        result = render_template("", {})
        assert result == ""

    def test_none_text(self):
        """测试 None 文本"""
        from boss_bot.unified_config import render_template
        result = render_template(None, {})
        assert result is None

    def test_multiple_placeholders(self):
        """测试多个占位符同时替换"""
        from boss_bot.unified_config import render_template
        profile = {
            "salary_expectation": "15K",
            "available_interview_time": "工作日下午",
            "position": "数据分析",
            "name": "张三",
        }
        text = "{name}应聘{position}，期望{salary}，{interview_time}可面试"
        result = render_template(text, profile)
        assert "张三" in result
        assert "数据分析" in result
        assert "15K" in result
        assert "工作日下午" in result


class ParseAiProvidersTest:
    """_parse_ai_providers 函数的单元测试"""

    def test_parse_single_provider(self):
        """测试解析单个提供商"""
        from boss_bot.unified_config import _parse_ai_providers
        with patch.dict(os.environ, {"AI_PROVIDERS_1": "sk-test|gpt-4|https://api.openai.com/v1"}):
            providers = _parse_ai_providers()
            assert len(providers) == 1
            assert providers[0]["key"] == "sk-test"
            assert providers[0]["model"] == "gpt-4"
            assert providers[0]["url"] == "https://api.openai.com/v1"

    def test_parse_multiple_providers(self):
        """测试解析多个提供商"""
        from boss_bot.unified_config import _parse_ai_providers
        with patch.dict(os.environ, {
            "AI_PROVIDERS_1": "sk-test1|model1|https://api1.com/v1",
            "AI_PROVIDERS_2": "sk-test2|model2|https://api2.com/v1",
            "AI_PROVIDERS_3": "sk-test3|model3|https://api3.com/v1",
        }):
            providers = _parse_ai_providers()
            assert len(providers) == 3

    def test_parse_no_providers(self):
        """测试无提供商"""
        from boss_bot.unified_config import _parse_ai_providers
        env_clean = {k: v for k, v in os.environ.items() if not k.startswith("AI_PROVIDERS")}
        with patch.dict(os.environ, env_clean, clear=True):
            providers = _parse_ai_providers()
            assert providers == []

    def test_default_url(self):
        """测试缺少 URL 时使用默认值"""
        from boss_bot.unified_config import _parse_ai_providers
        with patch.dict(os.environ, {"AI_PROVIDERS_1": "sk-test|model-name"}):
            providers = _parse_ai_providers()
            assert len(providers) == 1
            assert providers[0]["url"] == "https://apihub.agnes-ai.com/v1"

    def test_invalid_format_missing_model(self):
        """测试无效格式（缺少模型名）"""
        from boss_bot.unified_config import _parse_ai_providers
        with patch.dict(os.environ, {"AI_PROVIDERS_1": "sk-test"}):
            providers = _parse_ai_providers()
            assert providers == []

    def test_empty_key_skipped(self):
        """测试空 key 被跳过"""
        from boss_bot.unified_config import _parse_ai_providers
        with patch.dict(os.environ, {"AI_PROVIDERS_1": "|model-name|https://api.com"}):
            providers = _parse_ai_providers()
            assert providers == []


class BuildProvidersFromLegacyTest:
    """_build_providers_from_legacy 函数的单元测试"""

    def test_main_keys(self):
        """测试主 API 密钥"""
        from boss_bot.unified_config import _build_providers_from_legacy
        with patch.dict(os.environ, {
            "AI_API_KEY_1": "sk-main1",
            "AI_MODEL_1": "gpt-4",
        }):
            providers = _build_providers_from_legacy()
            assert len(providers) >= 1
            assert providers[0]["key"] == "sk-main1"
            assert providers[0]["model"] == "gpt-4"

    def test_backup_keys(self):
        """测试备用 API 密钥"""
        from boss_bot.unified_config import _build_providers_from_legacy
        with patch.dict(os.environ, {
            "AI_BACKUP_KEY_1": "sk-backup1",
            "AI_BACKUP_MODEL_1": "deepseek-v4",
        }):
            providers = _build_providers_from_legacy()
            assert any(p["key"] == "sk-backup1" for p in providers)

    def test_fallback_key(self):
        """测试兜底 API 密钥"""
        from boss_bot.unified_config import _build_providers_from_legacy
        with patch.dict(os.environ, {
            "AI_FALLBACK_KEY": "sk-fallback",
            "AI_FALLBACK_MODEL": "deepseek-flash",
        }):
            providers = _build_providers_from_legacy()
            assert any(p["key"] == "sk-fallback" for p in providers)

    def test_no_legacy_keys(self):
        """测试无旧格式密钥"""
        from boss_bot.unified_config import _build_providers_from_legacy
        env_clean = {k: v for k, v in os.environ.items()
                     if not k.startswith(("AI_API_KEY", "AI_MODEL", "AI_BASE_URL",
                                          "AI_BACKUP", "AI_FALLBACK"))}
        with patch.dict(os.environ, env_clean, clear=True):
            providers = _build_providers_from_legacy()
            assert providers == []


class ConfigCompatTest:
    """config.py 兼容层的单元测试"""

    def test_module_import(self):
        """测试 config 模块可正常导入"""
        import boss_bot.config as config
        assert hasattr(config, 'CHECK_INTERVAL')
        assert hasattr(config, 'MIN_DELAY')
        assert hasattr(config, 'MAX_DELAY')
        assert hasattr(config, 'SALARY_REPLY')
        assert hasattr(config, 'DEFAULT_REPLY')
        assert hasattr(config, 'REPLY_RULES')
        assert hasattr(config, 'IMPORTANCE_KEYWORDS')

    def test_load_config(self):
        """测试 load_config 函数"""
        from boss_bot.config import load_config
        cfg_dict = load_config()
        assert isinstance(cfg_dict, dict)
        assert "browser" in cfg_dict
        assert "accounts" in cfg_dict

    def test_get_unified_config(self):
        """测试 get_unified_config 函数"""
        from boss_bot.config import get_unified_config
        cfg = get_unified_config()
        assert cfg is not None
        assert hasattr(cfg, 'browser')
        assert hasattr(cfg, 'reply')

    def test_reload_config(self):
        """测试 reload_config 函数"""
        from boss_bot.config import reload_config
        cfg = reload_config()
        assert cfg is not None
        assert hasattr(cfg, 'browser')

    def test_validate_config(self):
        """测试 validate_config 函数"""
        from boss_bot.config import validate_config
        from boss_bot.unified_config import _default_config_dict
        cfg_dict = _default_config_dict()
        errors = validate_config(cfg_dict)
        assert errors == []

    def test_validate_config_empty(self):
        """测试 validate_config 空配置"""
        from boss_bot.config import validate_config
        errors = validate_config({})
        assert len(errors) > 0


# ===================== 规则引擎测试 =====================

class RuleEngineTest:
    """RuleEngine 类的单元测试"""

    def test_match_keyword_text(self):
        """测试关键词匹配返回文字回复"""
        from boss_bot.rules import RuleEngine
        engine = RuleEngine({"薪资": "我的期望薪资是面议"})
        result = engine.match("请问薪资多少？")
        assert result is not None
        assert result[0] == "text"
        assert result[1] == "我的期望薪资是面议"

    def test_match_resume(self):
        """测试关键词匹配返回简历动作"""
        from boss_bot.rules import RuleEngine
        engine = RuleEngine({"简历": "send_resume"})
        result = engine.match("请发一份简历")
        assert result is not None
        assert result[0] == "resume"
        assert result[1] is None

    def test_no_match(self):
        """测试无匹配"""
        from boss_bot.rules import RuleEngine
        engine = RuleEngine({"薪资": "面议"})
        result = engine.match("今天天气不错")
        assert result is None

    def test_empty_message(self):
        """测试空消息"""
        from boss_bot.rules import RuleEngine
        engine = RuleEngine({"薪资": "面议"})
        result = engine.match("")
        assert result is None

    def test_question_context_filter(self):
        """测试疑问上下文过滤 — '是否投递过简历' 不应触发简历规则"""
        from boss_bot.rules import RuleEngine
        engine = RuleEngine({"简历": "send_resume"})
        result = engine.match("是否投递过简历？")
        assert result is None

    def test_negative_context_filter(self):
        """测试否定上下文过滤 — '不用发简历' 不应触发简历规则"""
        from boss_bot.rules import RuleEngine
        engine = RuleEngine({"简历": "send_resume"})
        result = engine.match("不用发简历了")
        assert result is None

    def test_add_rule(self):
        """测试添加规则"""
        from boss_bot.rules import RuleEngine
        engine = RuleEngine({})
        engine.add_rule("测试关键词", "测试回复")
        result = engine.match("这是一个测试关键词的消息")
        assert result is not None
        assert result[1] == "测试回复"

    def test_remove_rule(self):
        """测试移除规则"""
        from boss_bot.rules import RuleEngine
        engine = RuleEngine({"测试": "回复"})
        engine.remove_rule("测试")
        result = engine.match("测试消息")
        assert result is None

    def test_case_insensitive(self):
        """测试大小写不敏感匹配"""
        from boss_bot.rules import RuleEngine
        engine = RuleEngine({"Salary": "面议"})
        result = engine.match("what is the salary?")
        assert result is not None

    def test_default_rules_from_config(self):
        """测试使用默认配置规则"""
        from boss_bot.rules import RuleEngine
        engine = RuleEngine()
        # 默认规则应包含 "简历"、"面试"、"薪资" 等
        result = engine.match("请发简历")
        assert result is not None
        assert result[0] == "resume"


# ===================== 意图分类测试 =====================

class IntentClassifyTest:
    """classify 函数的单元测试"""

    def test_invite_interview(self):
        """测试面试邀约意图"""
        from boss_bot.intent import classify
        assert classify("邀您面试") == "invite_interview"
        assert classify("约个面试时间") == "invite_interview"
        assert classify("来公司面试吧") == "invite_interview"
        assert classify("明天方便面试吗") == "invite_interview"
        assert classify("视频面试可以吗") == "invite_interview"

    def test_ask_salary(self):
        """测试询问薪资意图"""
        from boss_bot.intent import classify
        assert classify("你们薪资多少？") == "ask_salary"
        assert classify("这个岗位待遇怎么样？") == "ask_salary"
        assert classify("公司薪资范围是多少？") == "ask_salary"

    def test_ask_resume(self):
        """测试要简历意图"""
        from boss_bot.intent import classify
        assert classify("发一份简历吧") == "ask_resume"
        assert classify("看看你的简历") == "ask_resume"
        assert classify("想看简历") == "ask_resume"

    def test_ask_interview(self):
        """测试约面试时间意图"""
        from boss_bot.intent import classify
        assert classify("什么时候面试方便？") == "ask_interview"
        assert classify("哪天有空面试？") == "ask_interview"

    def test_ask_job_content(self):
        """测试问工作内容意图"""
        from boss_bot.intent import classify
        assert classify("工作内容是什么？") == "ask_job_content"
        assert classify("岗位职责有哪些？") == "ask_job_content"
        assert classify("主要做什么？") == "ask_job_content"

    def test_contact_request(self):
        """测试要联系方式意图"""
        from boss_bot.intent import classify
        assert classify("加个微信吧") == "contact_request"
        assert classify("留个联系方式") == "contact_request"
        assert classify("微信多少？") == "contact_request"

    def test_tell_salary(self):
        """测试报价意图"""
        from boss_bot.intent import classify
        assert classify("薪资是15K") == "tell_salary"
        assert classify("月薪20K-30K") == "tell_salary"
        assert classify("待遇8000") == "tell_salary"

    def test_greeting(self):
        """测试问候意图"""
        from boss_bot.intent import classify
        assert classify("您好") == "greeting"
        assert classify("在吗？") == "greeting"
        assert classify("你好呀") == "greeting"
        assert classify("早上好") == "greeting"

    def test_other(self):
        """测试未识别意图"""
        from boss_bot.intent import classify
        assert classify("今天天气不错") == "other"
        assert classify("随便聊聊") == "other"

    def test_empty_message(self):
        """测试空消息"""
        from boss_bot.intent import classify
        assert classify("") == "other"
        assert classify("   ") == "other"

    def test_priority_invite_over_salary(self):
        """测试邀约面试优先于询问薪资"""
        from boss_bot.intent import classify
        assert classify("邀您面试，薪资面议") == "invite_interview"

    def test_priority_ask_salary_over_ask_resume(self):
        """测试询问薪资优先于要简历"""
        from boss_bot.intent import classify
        assert classify("薪资多少？发个简历") == "ask_salary"


# ===================== 状态存储测试 =====================

class StateStoreTest:
    """StateStore 类的单元测试"""

    def test_mark_and_check_handled(self, tmp_path):
        """测试标记和检查已处理消息"""
        from boss_bot.state_store import StateStore
        store = StateStore(path=str(tmp_path / "state.json"))
        assert not store.was_handled("HR张三", "你好")
        store.mark_handled("HR张三", "你好", "text")
        assert store.was_handled("HR张三", "你好")

    def test_not_handled_different_message(self, tmp_path):
        """测试不同消息不被标记为已处理"""
        from boss_bot.state_store import StateStore
        store = StateStore(path=str(tmp_path / "state.json"))
        store.mark_handled("HR张三", "你好", "text")
        assert not store.was_handled("HR张三", "不同消息")

    def test_not_handled_different_chat(self, tmp_path):
        """测试不同会话不被标记为已处理"""
        from boss_bot.state_store import StateStore
        store = StateStore(path=str(tmp_path / "state.json"))
        store.mark_handled("HR张三", "你好", "text")
        assert not store.was_handled("HR李四", "你好")

    def test_pause_resume(self, tmp_path):
        """测试暂停和恢复"""
        from boss_bot.state_store import StateStore
        store = StateStore(path=str(tmp_path / "state.json"))
        assert not store.is_paused()
        store.pause(reason="人工接管", chat_name="HR张三")
        assert store.is_paused()
        info = store.pause_info()
        assert info["paused"] is True
        assert info["reason"] == "人工接管"
        assert info["chat"] == "HR张三"
        was_paused = store.resume()
        assert was_paused is True
        assert not store.is_paused()

    def test_resume_without_pause(self, tmp_path):
        """测试没有暂停时恢复返回 False"""
        from boss_bot.state_store import StateStore
        store = StateStore(path=str(tmp_path / "state.json"))
        was_paused = store.resume()
        assert was_paused is False

    def test_resume_sent(self, tmp_path):
        """测试简历发送记录"""
        from boss_bot.state_store import StateStore
        store = StateStore(path=str(tmp_path / "state.json"))
        assert not store.resume_sent("HR张三")
        store.mark_resume_sent("HR张三")
        assert store.resume_sent("HR张三")

    def test_resume_sent_different_chat(self, tmp_path):
        """测试不同会话的简历发送记录独立"""
        from boss_bot.state_store import StateStore
        store = StateStore(path=str(tmp_path / "state.json"))
        store.mark_resume_sent("HR张三")
        assert not store.resume_sent("HR李四")

    def test_persistence(self, tmp_path):
        """测试状态持久化"""
        from boss_bot.state_store import StateStore
        path = str(tmp_path / "state.json")
        store1 = StateStore(path=path)
        store1.mark_handled("HR张三", "你好", "text")
        store1.pause(reason="测试")
        store1.mark_resume_sent("HR张三")

        store2 = StateStore(path=path)
        assert store2.was_handled("HR张三", "你好")
        assert store2.is_paused()
        assert store2.resume_sent("HR张三")


# ===================== 统计测试 =====================

class StatsTest:
    """Stats 类的单元测试"""

    def test_record_reply(self, tmp_path):
        """测试记录回复"""
        from boss_bot.stats import Stats
        stats = Stats(path=str(tmp_path / "stats.json"))
        stats.record_reply(source="rule", action="text")
        stats.record_reply(source="intent", action="text")
        stats.record_reply(source="ai", action="resume")

        today = stats.today()
        assert today["replies"] == 3
        assert today["by_source"]["rule"] == 1
        assert today["by_source"]["intent"] == 1
        assert today["by_source"]["ai"] == 1
        assert today["by_action"]["text"] == 2
        assert today["by_action"]["resume"] == 1

    def test_record_skip(self, tmp_path):
        """测试记录跳过"""
        from boss_bot.stats import Stats
        stats = Stats(path=str(tmp_path / "stats.json"))
        stats.record_skip()
        stats.record_skip()
        today = stats.today()
        assert today["skipped_duplicates"] == 2

    def test_record_important(self, tmp_path):
        """测试记录重要事件"""
        from boss_bot.stats import Stats
        stats = Stats(path=str(tmp_path / "stats.json"))
        stats.record_important()
        today = stats.today()
        assert today["important_events"] == 1

    def test_total(self, tmp_path):
        """测试总计统计"""
        from boss_bot.stats import Stats
        stats = Stats(path=str(tmp_path / "stats.json"))
        stats.record_reply(source="rule", action="text")
        stats.record_skip()
        stats.record_important()
        total = stats.total()
        assert total["replies"] == 1
        assert total["skipped_duplicates"] == 1
        assert total["important_events"] == 1

    def test_summary(self, tmp_path):
        """测试统计摘要"""
        from boss_bot.stats import Stats
        stats = Stats(path=str(tmp_path / "stats.json"))
        stats.record_reply(source="rule", action="text")
        summary = stats.summary()
        assert "date" in summary
        assert "today" in summary
        assert "total" in summary
        assert summary["today"]["replies"] == 1

    def test_persistence(self, tmp_path):
        """测试统计持久化"""
        from boss_bot.stats import Stats
        path = str(tmp_path / "stats.json")
        stats1 = Stats(path=path)
        stats1.record_reply(source="rule", action="text")
        stats1.record_important()

        stats2 = Stats(path=path)
        total = stats2.total()
        assert total["replies"] == 1
        assert total["important_events"] == 1


# ===================== 消息存储测试 =====================

class MessageStoreTest:
    """MessageStore 类的单元测试"""

    def test_save_messages(self, tmp_path):
        """测试保存消息"""
        from boss_bot.message_store import MessageStore
        store = MessageStore(base_dir=str(tmp_path / "messages"))
        messages = [
            {"text": "你好", "is_mine": False, "time": "10:00"},
            {"text": "您好，很高兴认识您", "is_mine": True, "time": "10:01"},
        ]
        store.save_messages("HR张三", messages, job_name="数据分析")
        retrieved = store.get_messages("HR张三")
        assert len(retrieved) == 2
        assert retrieved[0]["text"] == "你好"
        assert retrieved[1]["text"] == "您好，很高兴认识您"

    def test_append_message(self, tmp_path):
        """测试追加消息"""
        from boss_bot.message_store import MessageStore
        store = MessageStore(base_dir=str(tmp_path / "messages"))
        store.append_message("HR张三", {"text": "你好", "is_mine": False, "time": "10:00"})
        store.append_message("HR张三", {"text": "您好", "is_mine": True, "time": "10:01"})
        retrieved = store.get_messages("HR张三")
        assert len(retrieved) == 2
        assert retrieved[0]["text"] == "你好"
        assert retrieved[1]["text"] == "您好"

    def test_get_messages_empty(self, tmp_path):
        """测试获取不存在的会话消息"""
        from boss_bot.message_store import MessageStore
        store = MessageStore(base_dir=str(tmp_path / "messages"))
        retrieved = store.get_messages("不存在的会话")
        assert retrieved == []

    def test_get_chat_list(self, tmp_path):
        """测试获取聊天列表"""
        from boss_bot.message_store import MessageStore
        store = MessageStore(base_dir=str(tmp_path / "messages"))
        store.save_messages("HR张三", [{"text": "你好", "is_mine": False}], "数据分析")
        store.save_messages("HR李四", [{"text": "在吗", "is_mine": False}], "Python开发")
        chat_list = store.get_chat_list()
        assert len(chat_list) == 2
        names = [c["chat_name"] for c in chat_list]
        assert "HR张三" in names
        assert "HR李四" in names

    def test_get_chat_detail(self, tmp_path):
        """测试获取聊天详情"""
        from boss_bot.message_store import MessageStore
        store = MessageStore(base_dir=str(tmp_path / "messages"))
        messages = [{"text": "你好", "is_mine": False, "time": "10:00"}]
        store.save_messages("HR张三", messages, job_name="数据分析")
        detail = store.get_chat_detail("HR张三")
        assert detail["chat_name"] == "HR张三"
        assert detail["job_name"] == "数据分析"
        assert len(detail["messages"]) == 1

    def test_get_chat_detail_empty(self, tmp_path):
        """测试获取不存在的聊天详情"""
        from boss_bot.message_store import MessageStore
        store = MessageStore(base_dir=str(tmp_path / "messages"))
        detail = store.get_chat_detail("不存在")
        assert detail["chat_name"] == "不存在"
        assert detail["messages"] == []

    def test_cache(self, tmp_path):
        """测试缓存机制"""
        from boss_bot.message_store import MessageStore
        store = MessageStore(base_dir=str(tmp_path / "messages"))
        store.save_messages("HR张三", [{"text": "你好", "is_mine": False}])
        msgs1 = store.get_messages("HR张三")
        assert len(msgs1) == 1
        msgs2 = store.get_messages("HR张三")
        assert len(msgs2) == 1


# ===================== 通知测试 =====================

class NotifierTest:
    """Notifier 类的单元测试"""

    def test_is_important_keyword(self, tmp_path):
        """测试关键词检测重要事件"""
        from boss_bot.notify import Notifier
        notifier = Notifier(path=str(tmp_path / "notify.json"))
        assert notifier.is_important("恭喜你获得offer！")
        assert notifier.is_important("面试邀请，请确认时间")
        assert notifier.is_important("欢迎加入我们公司")

    def test_is_important_intent(self, tmp_path):
        """测试意图检测重要事件"""
        from boss_bot.notify import Notifier
        notifier = Notifier(path=str(tmp_path / "notify.json"))
        assert notifier.is_important("明天来公司聊聊", intent="invite_interview")

    def test_not_important(self, tmp_path):
        """测试非重要事件"""
        from boss_bot.notify import Notifier
        notifier = Notifier(path=str(tmp_path / "notify.json"))
        assert not notifier.is_important("今天天气不错")
        assert not notifier.is_important("你好，在吗？")

    def test_notify_if_important_hit(self, tmp_path):
        """测试重要事件通知命中"""
        from boss_bot.notify import Notifier
        notifier = Notifier(path=str(tmp_path / "notify.json"))
        result = notifier.notify_if_important(
            "恭喜获得offer！", chat_name="HR张三", job_name="数据分析"
        )
        assert result is True
        records = notifier.records()
        assert len(records) >= 1
        assert records[-1]["level"] == "important"

    def test_notify_if_important_miss(self, tmp_path):
        """测试非重要事件不触发通知"""
        from boss_bot.notify import Notifier
        notifier = Notifier(path=str(tmp_path / "notify.json"))
        result = notifier.notify_if_important("今天天气不错", chat_name="HR张三")
        assert result is False

    def test_send_notification_disabled(self, tmp_path):
        """测试禁用通知时不发送"""
        from boss_bot.notify import Notifier
        notifier = Notifier(path=str(tmp_path / "notify.json"))
        notifier.enabled = False
        result = notifier.send_notification("测试", "内容", level="info")
        assert result is False
        records = notifier.records()
        assert len(records) >= 1

    def test_send_notification_enabled(self, tmp_path):
        """测试启用通知时发送"""
        from boss_bot.notify import Notifier
        notifier = Notifier(path=str(tmp_path / "notify.json"))
        notifier.enabled = True
        notifier.webhook_url = ""
        result = notifier.send_notification("测试", "内容", level="info")
        assert result is True

    def test_records_limit(self, tmp_path):
        """测试通知记录上限"""
        from boss_bot.notify import Notifier
        notifier = Notifier(path=str(tmp_path / "notify.json"))
        for i in range(5):
            notifier.send_notification(f"标题{i}", f"内容{i}")
        records = notifier.records(limit=3)
        assert len(records) <= 3


# ===================== 回复引擎测试 =====================

class ReplyEngineTest:
    """ReplyEngine 类的单元测试"""

    def test_rule_match_priority(self):
        """测试规则匹配优先级 — 规则命中时直接返回"""
        from boss_bot.reply_engine import ReplyEngine
        engine = ReplyEngine()
        action, content, meta = engine.get_reply("请发简历")
        assert action == "resume"
        assert meta["source"] == "rule"

    def test_intent_match(self):
        """测试意图匹配 — 规则未命中时走意图识别"""
        from boss_bot.reply_engine import ReplyEngine
        engine = ReplyEngine()
        # 使用不包含默认规则关键词的消息来测试意图匹配
        action, content, meta = engine.get_reply("这个岗位给多少？")
        assert action == "text"
        assert meta["source"] == "intent"
        assert meta["intent"] == "ask_salary"
        assert content is not None

    def test_greeting_intent(self):
        """测试问候意图匹配"""
        from boss_bot.reply_engine import ReplyEngine
        engine = ReplyEngine()
        action, content, meta = engine.get_reply("您好")
        assert action == "text"
        assert meta["source"] == "rule"

    def test_default_skip(self):
        """测试兜底跳过 — AI 未启用且无匹配时跳过"""
        from boss_bot.reply_engine import ReplyEngine
        with patch('boss_bot.reply_engine.config') as mock_config:
            mock_config.ENABLE_AI = False
            mock_config.AI_API_KEYS = []
            mock_config.AI_FAIL_ACTION = "skip"
            mock_config.AI_PROVIDERS = []
            mock_config.AI_MAX_TOKENS = 200
            mock_config.AI_RATE_LIMIT_WAIT = 30
            mock_config.MAX_REPLIES_PER_HOUR = 30
            mock_config.MIN_DELAY = 2
            mock_config.MAX_DELAY = 5
            # 引擎会把可热重载项快照到实例属性上，所以必须在 mock 生效后再构造
            engine = ReplyEngine()
            action, content, meta = engine.get_reply("今天天气不错啊")
            assert action == "none"
            assert content is None
            assert meta["source"] == "default"

    def test_default_reply_when_configured(self):
        """测试配置为 default 时使用兜底话术"""
        from boss_bot.reply_engine import ReplyEngine
        with patch('boss_bot.reply_engine.config') as mock_config:
            mock_config.ENABLE_AI = False
            mock_config.AI_API_KEYS = []
            mock_config.AI_FAIL_ACTION = "default"
            mock_config.DEFAULT_REPLY = "兜底回复"
            mock_config.AI_PROVIDERS = []
            mock_config.AI_MAX_TOKENS = 200
            mock_config.AI_RATE_LIMIT_WAIT = 30
            mock_config.MAX_REPLIES_PER_HOUR = 30
            mock_config.MIN_DELAY = 2
            mock_config.MAX_DELAY = 5
            engine = ReplyEngine()
            action, content, meta = engine.get_reply("今天天气不错啊")
            assert action == "text"
            assert content == "兜底回复"
            assert meta["source"] == "default"

    def test_can_reply_within_limit(self):
        """测试未超过限制时可以回复"""
        from boss_bot.reply_engine import ReplyEngine
        engine = ReplyEngine()
        assert engine.can_reply() is True

    def test_can_reply_exceed_limit(self):
        """测试超过限制后不能回复"""
        from boss_bot.reply_engine import ReplyEngine
        engine = ReplyEngine()
        engine._reply_count = 100
        assert engine.can_reply() is False

    def test_can_reply_reset_after_hour(self):
        """测试一小时后计数重置"""
        from boss_bot.reply_engine import ReplyEngine
        engine = ReplyEngine()
        engine._reply_count = 100
        engine._hour_start = time.time() - 3700
        assert engine.can_reply() is True
        assert engine._reply_count == 0

    def test_record_reply_increments(self):
        """测试 record_reply 递增计数"""
        from boss_bot.reply_engine import ReplyEngine
        engine = ReplyEngine()
        assert engine._reply_count == 0
        engine.record_reply()
        assert engine._reply_count == 1
        engine.record_reply()
        assert engine._reply_count == 2

    def test_split_messages_string(self):
        """测试字符串消息拆分"""
        from boss_bot.reply_engine import ReplyEngine
        latest, history = ReplyEngine._split_messages("你好")
        assert latest == "你好"
        assert history == []

    def test_split_messages_list(self):
        """测试列表消息拆分"""
        from boss_bot.reply_engine import ReplyEngine
        messages = [
            {"text": "你好", "is_mine": False},
            {"text": "您好", "is_mine": True},
            {"text": "在吗", "is_mine": False},
        ]
        latest, history = ReplyEngine._split_messages(messages)
        assert latest == "在吗"
        assert history == messages

    def test_split_messages_empty(self):
        """测试空消息拆分"""
        from boss_bot.reply_engine import ReplyEngine
        latest, history = ReplyEngine._split_messages([])
        assert latest == ""
        assert history == []

    def test_split_messages_all_mine(self):
        """测试全是自己发的消息"""
        from boss_bot.reply_engine import ReplyEngine
        messages = [
            {"text": "你好", "is_mine": True},
            {"text": "在吗", "is_mine": True},
        ]
        latest, history = ReplyEngine._split_messages(messages)
        assert latest == ""

    def test_reply_cache(self):
        """测试回复缓存"""
        from boss_bot.reply_engine import ReplyCache
        cache = ReplyCache()
        assert cache.get("你好", "HR", "数据分析") is None
        cache.set("你好", "HR", "数据分析", "你好，很高兴认识您")
        result = cache.get("你好", "HR", "数据分析")
        assert result == "你好，很高兴认识您"

    def test_reply_cache_different_key(self):
        """测试缓存不同 key"""
        from boss_bot.reply_engine import ReplyCache
        cache = ReplyCache()
        cache.set("你好", "HR张三", "数据分析", "回复1")
        assert cache.get("你好", "HR李四", "数据分析") is None

    def test_is_rate_limit_error(self):
        """测试限流错误识别"""
        from boss_bot.reply_engine import ReplyEngine
        assert ReplyEngine._is_rate_limit_error(Exception("429 Too Many Requests"))
        assert ReplyEngine._is_rate_limit_error(Exception("rate limit exceeded"))
        assert ReplyEngine._is_rate_limit_error(Exception("insufficient quota"))
        assert not ReplyEngine._is_rate_limit_error(Exception("connection error"))


# ===================== 打招呼引擎测试 =====================

class GreetEngineCityCodesTest:
    """城市编码映射的单元测试"""

    def test_major_cities(self):
        """测试主要城市编码"""
        from boss_bot.greet_engine import CITY_CODES
        assert CITY_CODES["北京"] == "101010100"
        assert CITY_CODES["上海"] == "101020100"
        assert CITY_CODES["广州"] == "101280100"
        assert CITY_CODES["深圳"] == "101280600"
        assert CITY_CODES["杭州"] == "101210100"
        assert CITY_CODES["成都"] == "101270100"

    def test_city_codes_count(self):
        """测试城市编码数量"""
        from boss_bot.greet_engine import CITY_CODES
        assert len(CITY_CODES) >= 30

    def test_city_codes_format(self):
        """测试城市编码格式（9位数字）"""
        from boss_bot.greet_engine import CITY_CODES
        for city, code in CITY_CODES.items():
            assert len(code) == 9
            assert code.isdigit()


class AIProviderConfigTest:
    """AIProviderConfig 类的单元测试"""

    def test_valid_config(self):
        """测试有效配置"""
        from boss_bot.greet_engine import AIProviderConfig
        provider = AIProviderConfig(
            name="测试", api_key="sk-test",
            api_base="https://api.test.com/v1", model="gpt-4"
        )
        assert provider.is_valid() is True

    def test_invalid_config_no_key(self):
        """测试无效配置 — 缺少 API key"""
        from boss_bot.greet_engine import AIProviderConfig
        provider = AIProviderConfig(
            name="测试", api_key="",
            api_base="https://api.test.com/v1", model="gpt-4"
        )
        assert provider.is_valid() is False

    def test_invalid_config_no_base(self):
        """测试无效配置 — 缺少 API base"""
        from boss_bot.greet_engine import AIProviderConfig
        provider = AIProviderConfig(
            name="测试", api_key="sk-test",
            api_base="", model="gpt-4"
        )
        assert provider.is_valid() is False

    def test_invalid_config_no_model(self):
        """测试无效配置 — 缺少 model"""
        from boss_bot.greet_engine import AIProviderConfig
        provider = AIProviderConfig(
            name="测试", api_key="sk-test",
            api_base="https://api.test.com/v1", model=""
        )
        assert provider.is_valid() is False

    def test_api_base_trailing_slash(self):
        """测试 API base 去除尾部斜杠"""
        from boss_bot.greet_engine import AIProviderConfig
        provider = AIProviderConfig(
            name="测试", api_key="sk-test",
            api_base="https://api.test.com/v1/", model="gpt-4"
        )
        assert provider.api_base == "https://api.test.com/v1"


class AIAnalyzerChainTest:
    """AIAnalyzerChain 类的单元测试"""

    def test_no_providers(self):
        """测试无提供商时分析岗位"""
        from boss_bot.greet_engine import AIAnalyzerChain
        chain = AIAnalyzerChain(providers=[])
        result = chain.analyze_job({"job_name": "数据分析", "url": "test"})
        assert result["score"] == 50
        assert result["is_match"] is True

    def test_dict_providers_conversion(self):
        """测试 dict 格式提供商转换"""
        from boss_bot.greet_engine import AIAnalyzerChain, AIProviderConfig
        chain = AIAnalyzerChain(providers=[
            {"name": "测试1", "api_key": "sk-1", "api_base": "https://api1.com", "model": "gpt-4"},
            {"name": "测试2", "key": "sk-2", "url": "https://api2.com", "model": "claude-3"},
        ])
        assert len(chain.providers) == 2
        assert isinstance(chain.providers[0], AIProviderConfig)
        assert chain.providers[0].api_key == "sk-1"
        assert chain.providers[1].api_key == "sk-2"

    def test_get_stats(self):
        """测试获取统计"""
        from boss_bot.greet_engine import AIAnalyzerChain
        chain = AIAnalyzerChain(providers=[])
        stats = chain.get_stats()
        assert "analyzed" in stats
        assert "matched" in stats
        assert "cache_hits" in stats
        assert "match_rate" in stats
        assert "threshold" in stats
        assert stats["providers_total"] == 0

    def test_set_resume(self):
        """测试设置简历"""
        from boss_bot.greet_engine import AIAnalyzerChain
        chain = AIAnalyzerChain(providers=[])
        chain.set_resume({"school": "北大", "major": "计算机"})
        assert chain._resume is not None
        assert chain._resume_hash != ""


class RetryConfigWiredTest:
    """greet.retry 必须真的被运行时代码读取（否则前端改了没生效）"""

    def _cfg(self, attempts):
        from boss_bot.unified_config import UnifiedConfig
        cfg = UnifiedConfig()
        cfg.greet.retry.max_attempts = attempts
        return cfg

    def test_打招呼引擎读到配置(self):
        from boss_bot.greet_engine import GreetEngine
        ge = GreetEngine(MagicMock(), self._cfg(5))
        assert ge._retry_max_attempts == 5

    def test_热重载会把改动同步给引擎(self):
        from boss_bot.main_loop import UnifiedBotLoop, UnifiedConfig
        from boss_bot.greet_engine import GreetEngine
        with patch("boss_bot.main_loop.BrowserManager"):
            loop = UnifiedBotLoop(config=self._cfg(4))
        loop._greet_engine = GreetEngine(MagicMock(), loop.config)
        loop._reply_engine = None
        loop._state_store = MagicMock()
        loop._state_store.is_paused.return_value = False
        with patch.object(UnifiedConfig, "load", return_value=self._cfg(9)):
            loop._hot_reload_config()
        assert loop._greet_engine._retry_max_attempts == 9


# ===================== 主循环测试 =====================

class UnifiedBotLoopTest:
    """UnifiedBotLoop 类的单元测试"""

    def test_initial_status(self):
        """测试初始状态"""
        from boss_bot.main_loop import UnifiedBotLoop
        with patch('boss_bot.main_loop.BrowserManager') as mock_bm:
            loop = UnifiedBotLoop()
            status = loop.get_status()
            assert status["running"] is False
            assert status["logged_in"] is False
            assert status["needs_login"] is False
            assert status["greet_paused"] is False
            assert status["reply_paused"] is False
            assert status["current_mode"] == "idle"
            assert status["current_chat"] is None
            assert "stats" in status

    def test_pause_greet(self):
        """测试暂停打招呼"""
        from boss_bot.main_loop import UnifiedBotLoop
        with patch('boss_bot.main_loop.BrowserManager'):
            loop = UnifiedBotLoop()
            loop.pause_greet()
            assert loop._greet_paused is True
            status = loop.get_status()
            assert status["greet_paused"] is True

    def test_resume_greet(self):
        """测试恢复打招呼"""
        from boss_bot.main_loop import UnifiedBotLoop
        with patch('boss_bot.main_loop.BrowserManager'):
            loop = UnifiedBotLoop()
            loop.pause_greet()
            loop.resume_greet()
            assert loop._greet_paused is False

    def test_pause_reply(self):
        """测试暂停回复"""
        from boss_bot.main_loop import UnifiedBotLoop
        with patch('boss_bot.main_loop.BrowserManager'):
            loop = UnifiedBotLoop()
            loop.pause_reply()
            assert loop._reply_paused is True
            status = loop.get_status()
            assert status["reply_paused"] is True

    def test_resume_reply(self):
        """测试恢复回复"""
        from boss_bot.main_loop import UnifiedBotLoop
        with patch('boss_bot.main_loop.BrowserManager'):
            loop = UnifiedBotLoop()
            loop.pause_reply()
            loop.resume_reply()
            assert loop._reply_paused is False

    def test_get_status_stats(self):
        """测试状态中的统计数据"""
        from boss_bot.main_loop import UnifiedBotLoop
        with patch('boss_bot.main_loop.BrowserManager'):
            loop = UnifiedBotLoop()
            status = loop.get_status()
            stats = status["stats"]
            assert stats["greet_applied"] == 0
            assert stats["greet_skipped"] == 0
            assert stats["reply_sent"] == 0
            assert stats["important_events"] == 0

    def test_confirm_login(self):
        """测试确认登录"""
        from boss_bot.main_loop import UnifiedBotLoop
        with patch('boss_bot.main_loop.BrowserManager'):
            loop = UnifiedBotLoop()
            loop._needs_login = True
            loop.confirm_login()
            assert loop._logged_in is True
            assert loop._needs_login is False

    def test_start_already_running(self):
        """测试已在运行时再次启动"""
        from boss_bot.main_loop import UnifiedBotLoop
        with patch('boss_bot.main_loop.BrowserManager'):
            loop = UnifiedBotLoop()
            loop._running = True
            loop.start()
            assert loop._init_thread is None

    def test_build_greet_tasks(self):
        """测试构建打招呼任务列表"""
        from boss_bot.main_loop import UnifiedBotLoop
        from boss_bot.unified_config import UnifiedConfig
        with patch('boss_bot.main_loop.BrowserManager'):
            cfg = UnifiedConfig()
            loop = UnifiedBotLoop(config=cfg)
            tasks = loop._build_greet_tasks()
            assert len(tasks) >= 1
            assert "query" in tasks[0]
            assert "city" in tasks[0]


# ===================== Prompts 模块测试 =====================

class PromptsTest:
    """prompts.py 模块的单元测试"""

    def test_build_system_prompt(self):
        """测试构建系统提示词"""
        from boss_bot.prompts import build_system_prompt
        profile = {
            "education": "硕士",
            "position": "数据分析师",
            "skills": ["Python", "SQL"],
            "experience": "3年数据分析经验",
            "salary_expectation": "20K",
            "available_interview_time": "工作日全天",
            "highlights": ["逻辑思维强"],
        }
        prompt = build_system_prompt(profile)
        assert "硕士" in prompt
        assert "数据分析师" in prompt
        assert "Python" in prompt
        assert "20K" in prompt

    def test_build_system_prompt_default(self):
        """测试默认画像构建系统提示词"""
        from boss_bot.prompts import build_system_prompt
        prompt = build_system_prompt({})
        assert "学历" in prompt

    def test_build_conversation_history(self):
        """测试构建对话历史"""
        from boss_bot.prompts import build_conversation_history
        messages = [
            {"text": "你好", "is_mine": False},
            {"text": "您好", "is_mine": True},
            {"text": "在吗", "is_mine": False},
        ]
        history = build_conversation_history(messages)
        assert "对方: 你好" in history
        assert "我: 您好" in history
        assert "对方: 在吗" in history

    def test_build_conversation_history_empty(self):
        """测试空消息列表"""
        from boss_bot.prompts import build_conversation_history
        history = build_conversation_history([])
        assert "无历史消息" in history

    def test_build_user_prompt(self):
        """测试构建用户提示词"""
        from boss_bot.prompts import build_user_prompt
        prompt = build_user_prompt("HR张三", "数据分析", "你好")
        assert "HR张三" in prompt
        assert "数据分析" in prompt
        assert "你好" in prompt


# ===================== Browser Launcher 工具函数测试 =====================

class BrowserLauncherUtilTest:
    """browser_launcher.py 工具函数的单元测试"""

    def test_set_and_get_preferred_browser(self):
        """测试设置和获取偏好浏览器"""
        from boss_bot.browser_launcher import set_preferred_browser, get_preferred_browser
        set_preferred_browser("chrome")
        assert get_preferred_browser() == "chrome"
        set_preferred_browser("")
        assert get_preferred_browser() == ""

    def test_is_port_open_false(self):
        """测试端口检测 — 不存在的端口"""
        from boss_bot.browser_launcher import _is_port_open
        assert _is_port_open("127.0.0.1", 59999, timeout=0.5) is False

    def test_portable_beats_system_default(self):
        """BOSS 直聘有反爬：内置破解版必须优先于系统默认浏览器"""
        from boss_bot import browser_launcher as bl
        avail = {
            "portable": "C:/proj/cloakbrowser/chrome.exe",
            "chrome": "C:/Program Files/Google/Chrome/Application/chrome.exe",
            "edge": "C:/Program Files (x86)/Microsoft/Edge/msedge.exe",
        }
        with patch.object(bl, "detect_available_browsers", return_value=avail), \
             patch.object(bl, "_detect_default_browser", return_value="chrome"), \
             patch.object(bl, "_preferred_browser", ""):
            path, btype = bl._find_best_browser_path("chrome")
        assert path == "C:/proj/cloakbrowser/chrome.exe"
        assert btype == "portable"

    def test_explicit_preference_beats_portable(self):
        """用户在界面上手动选过浏览器时，尊重用户选择"""
        from boss_bot import browser_launcher as bl
        avail = {
            "portable": "C:/proj/cloakbrowser/chrome.exe",
            "edge": "C:/Edge/msedge.exe",
        }
        with patch.object(bl, "detect_available_browsers", return_value=avail), \
             patch.object(bl, "_preferred_browser", "edge"):
            path, btype = bl._find_best_browser_path("chrome")
        assert path == "C:/Edge/msedge.exe"
        assert btype == "edge"

    def test_system_browser_used_when_no_portable(self):
        """没有破解版时退回系统浏览器，不能报错"""
        from boss_bot import browser_launcher as bl
        avail = {"chrome": "C:/Chrome/chrome.exe"}
        with patch.object(bl, "detect_available_browsers", return_value=avail), \
             patch.object(bl, "_detect_default_browser", return_value="chrome"), \
             patch.object(bl, "_preferred_browser", ""):
            path, btype = bl._find_best_browser_path("chrome")
        assert path == "C:/Chrome/chrome.exe"

    def test_portable_path_detection_returns_exe(self):
        """_get_portable_browser_path 要么返回真实存在的文件，要么返回空串"""
        from boss_bot.browser_launcher import _get_portable_browser_path
        path = _get_portable_browser_path()
        assert path == "" or os.path.isfile(path)


class ResolvePathTest:
    """unified_config.resolve_path — 相对路径必须锚定到项目根目录"""

    def test_relative_anchored_to_base_dir(self):
        from boss_bot.unified_config import resolve_path, BASE_DIR
        assert resolve_path("zhipin_cookies.json") == BASE_DIR / "zhipin_cookies.json"

    def test_absolute_unchanged(self):
        from boss_bot.unified_config import resolve_path
        assert str(resolve_path("D:/other/cookies.json")) == os.path.normpath(
            "D:/other/cookies.json")

    def test_empty_returns_empty_path(self):
        from boss_bot.unified_config import resolve_path
        from pathlib import Path as _P
        assert resolve_path("") == _P()
        assert resolve_path(None) == _P()
        assert resolve_path("   ") == _P()

    def test_nested_relative(self):
        from boss_bot.unified_config import resolve_path, BASE_DIR
        assert resolve_path("browser_data/account_1") == BASE_DIR / "browser_data" / "account_1"


# ===================== Flask 应用测试 =====================

class FlaskAppTest:
    """Flask Web 应用路由的单元测试"""

    @pytest.fixture
    def flask_client(self):
        """创建 Flask 测试客户端"""
        import sys
        flask_dir = Path(__file__).parent.parent / "flask-version"
        if str(flask_dir) not in sys.path:
            sys.path.insert(0, str(flask_dir))

        with patch('boss_bot.main_loop.BrowserManager'), \
             patch('app.UnifiedBotLoop') as mock_loop_class:
            mock_loop = MagicMock()
            mock_loop._running = False
            mock_loop.get_status.return_value = {
                "running": False, "logged_in": False, "needs_login": False,
                "greet_paused": False, "reply_paused": False,
                "current_mode": "idle", "current_chat": None,
                "last_check": "", "stats": {},
            }
            mock_loop_class.return_value = mock_loop

            from app import app
            app.config["TESTING"] = True
            with app.test_client() as client:
                yield client

    def test_index_route(self, flask_client):
        """测试主页路由"""
        response = flask_client.get("/")
        assert response.status_code == 200

    def test_api_status_no_bot(self, flask_client):
        """测试状态 API — 无 bot 实例"""
        response = flask_client.get("/api/status")
        assert response.status_code == 200
        data = response.get_json()
        assert data["running"] is False

    def test_api_logs(self, flask_client):
        """测试日志 API"""
        response = flask_client.get("/api/logs")
        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "ok"
        assert "logs" in data

    def test_api_stats_no_bot(self, flask_client):
        """测试统计 API — 无 bot 实例"""
        response = flask_client.get("/api/stats")
        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "ok"
        assert "stats" in data

    def test_api_config_get(self, flask_client):
        """测试获取配置 API"""
        response = flask_client.get("/api/config")
        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "ok"
        assert "config" in data

    def test_api_user_profile_get(self, flask_client):
        """测试获取个人画像 API"""
        response = flask_client.get("/api/user_profile")
        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "ok"
        assert "profile" in data

    def test_api_browser_list(self, flask_client):
        """测试浏览器列表 API"""
        response = flask_client.get("/api/browser/list")
        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "ok"
        assert "browsers" in data


# ===================== 热重载生效性测试 =====================

class HotReloadEffectivenessTest:
    """热重载必须写进引擎真正读取的属性 — 防止「前端改了没反应」"""

    def _make_loop(self):
        from boss_bot.main_loop import UnifiedBotLoop
        from boss_bot.greet_engine import GreetEngine
        with patch('boss_bot.main_loop.BrowserManager'):
            loop = UnifiedBotLoop()
        # 打招呼引擎用真身：热重载现在通过引擎自己的 reload_runtime_settings()
        # 落地，换成 MagicMock 就只会自动造属性，测不出"改了没生效"
        loop._greet_engine = GreetEngine(MagicMock(), loop.config)
        loop._reply_engine = MagicMock()
        loop._state_store = MagicMock()
        loop._state_store.is_paused.return_value = False
        return loop

    def _reload(self, loop, cfg):
        from boss_bot.main_loop import UnifiedConfig
        with patch.object(UnifiedConfig, "load", return_value=cfg):
            loop._hot_reload_config()

    def test_rate_limit_lands_on_read_attributes(self):
        """引擎读的是 _max_per_hour/_max_per_day，不是 _rate_per_hour"""
        from boss_bot.unified_config import UnifiedConfig
        loop = self._make_loop()
        cfg = UnifiedConfig()
        cfg.greet.rate_limit.enabled = True
        cfg.greet.rate_limit.max_per_hour = 7
        cfg.greet.rate_limit.max_per_day = 99
        self._reload(loop, cfg)
        assert loop._greet_engine._max_per_hour == 7
        assert loop._greet_engine._max_per_day == 99
        assert loop._greet_engine._rate_limit_enabled is True

    def test_reply_delay_and_providers_landed(self):
        """回复侧延迟区间与 provider 列表要落到 reply_engine 实例上"""
        from boss_bot.unified_config import UnifiedConfig
        loop = self._make_loop()
        cfg = UnifiedConfig()
        cfg.reply.min_delay = 11
        cfg.reply.max_delay = 22
        cfg.reply.max_replies_per_hour = 8
        cfg.ai.max_tokens = 1234
        cfg.ai.fail_action = "skip"
        cfg.ai.rate_limit_wait = 45
        self._reload(loop, cfg)
        assert loop._reply_engine._min_delay == 11
        assert loop._reply_engine._max_delay == 22
        assert isinstance(loop._reply_engine._ai_providers, list)
        # 这几项曾经只在启动时读一次快照，前端改了必须热重载可见
        assert loop._reply_engine._max_replies_per_hour == 8
        assert loop._reply_engine._ai_max_tokens == 1234
        assert loop._reply_engine._ai_fail_action == "skip"
        assert loop._reply_engine._ai_rate_limit_wait == 45

    def test_greet_engine_receives_new_config(self):
        """热重载要把新 config 交给引擎，账号级话术才能跟着变"""
        from boss_bot.unified_config import UnifiedConfig
        loop = self._make_loop()
        cfg = UnifiedConfig()
        cfg.greet.accounts[0].jobs[0].greeting_message = "改过的招呼语"
        self._reload(loop, cfg)
        assert loop._greet_engine.config is cfg
        assert loop._greet_engine._greeting_message == "改过的招呼语"

    def test_enable_switches_propagate_without_restart(self):
        """打招呼/回复总开关热重载后立即可见"""
        from boss_bot.unified_config import UnifiedConfig
        loop = self._make_loop()
        cfg = UnifiedConfig()
        cfg.greet.enabled = False
        cfg.reply.enabled = False
        self._reload(loop, cfg)
        assert loop._greet_enabled is False
        assert loop._reply_enabled is False

    def test_reload_failure_does_not_raise(self):
        """配置读不出来时只记日志，绝不能把调用线程炸掉"""
        from boss_bot.main_loop import UnifiedBotLoop, UnifiedConfig
        with patch('boss_bot.main_loop.BrowserManager'):
            loop = UnifiedBotLoop()
        loop._greet_engine = MagicMock()
        loop._reply_engine = MagicMock()
        with patch.object(UnifiedConfig, "load", side_effect=RuntimeError("坏了")):
            loop._hot_reload_config()

    def test_reload_is_safe_when_engines_not_created(self):
        """登录后才创建引擎，登录前热重载不能 AttributeError"""
        from boss_bot.main_loop import UnifiedBotLoop, UnifiedConfig
        with patch('boss_bot.main_loop.BrowserManager'):
            loop = UnifiedBotLoop()
        loop._greet_engine = None
        loop._reply_engine = None
        loop._state_store = None
        with patch.object(UnifiedConfig, "load", return_value=UnifiedConfig()):
            loop._hot_reload_config()
        assert loop._greet_enabled is True


class ManualPauseSurvivesTest:
    """人工接管暂停不能被热重载自动解除"""

    def _make_loop(self, reason):
        from boss_bot.main_loop import UnifiedBotLoop
        with patch('boss_bot.main_loop.BrowserManager'):
            loop = UnifiedBotLoop()
        loop._reply_paused = True
        loop._state_store = MagicMock()
        loop._state_store.is_paused.return_value = True
        loop._state_store.pause_info.return_value = {"reason": reason}
        return loop

    def test_manual_takeover_not_auto_resumed(self):
        loop = self._make_loop("手动暂停回复（人工接管）")
        loop._maybe_auto_resume_reply()
        assert loop._reply_paused is True
        loop._state_store.resume.assert_not_called()

    def test_stale_reason_auto_resumes(self):
        loop = self._make_loop("一些不再匹配重要关键词的原因")
        loop._maybe_auto_resume_reply()
        assert loop._reply_paused is False
        loop._state_store.resume.assert_called_once()

    def test_missing_state_store_is_noop(self):
        from boss_bot.main_loop import UnifiedBotLoop
        with patch('boss_bot.main_loop.BrowserManager'):
            loop = UnifiedBotLoop()
        loop._state_store = None
        loop._maybe_auto_resume_reply()


# ===================== AI 不可用时不能全军覆没 =====================

class AiOutageFailOpenTest:
    """AI 挂了不等于「AI 判定不匹配」，不能把所有岗位都跳过"""

    def test_all_providers_failed_marks_ai_error(self):
        from boss_bot.greet_engine import AIAnalyzerChain
        chain = AIAnalyzerChain(providers=[
            {"name": "坏接口", "api_key": "sk-1", "api_base": "https://bad", "model": "m"}
        ])
        with patch.object(chain, "_call_provider_api", side_effect=RuntimeError("超时")):
            result = chain.analyze_job({"job_name": "数据分析", "url": "u1"})
        assert result["ai_error"] is True
        assert result["is_match"] is True

    def test_no_provider_marks_ai_error(self):
        from boss_bot.greet_engine import AIAnalyzerChain
        result = AIAnalyzerChain(providers=[]).analyze_job({"job_name": "x", "url": "u"})
        assert result["ai_error"] is True

    def _engine(self):
        from boss_bot.greet_engine import GreetEngine
        from boss_bot.unified_config import UnifiedConfig
        return GreetEngine(MagicMock(), UnifiedConfig())

    def test_outage_result_passes_gate(self):
        """analyze_job 报 AI 不可用时，_analyze_job_with_ai 必须返回结果而不是 None"""
        from boss_bot.greet_engine import AIAnalyzerChain
        engine = self._engine()
        with patch.object(engine, "_init_ai", return_value=AIAnalyzerChain(providers=[])):
            result, _ = engine._analyze_job_with_ai({"job_name": "数据分析", "url": "u"})
        assert result is not None
        assert result.get("ai_error") is True

    def test_analyzer_init_failure_passes_gate(self):
        engine = self._engine()
        with patch.object(engine, "_init_ai", return_value=None):
            result, _ = engine._analyze_job_with_ai({"job_name": "数据分析", "url": "u"})
        assert result is not None
        assert result.get("ai_error") is True

    def test_genuine_low_score_still_skipped(self):
        """真正的不匹配（低分）仍然要跳过，不能被 fail-open 放水"""
        engine = self._engine()
        engine._ai_threshold = 70
        chain = MagicMock()
        chain.analyze_job.return_value = {"score": 10, "is_match": True, "reason": "不相关"}
        chain.last_system_prompt = chain.last_user_prompt = None
        chain.last_model_name = "m"
        chain.last_raw_response = None
        with patch.object(engine, "_init_ai", return_value=chain):
            result, _ = engine._analyze_job_with_ai({"job_name": "销售", "url": "u"})
        assert result is None


class AiChainBudgetTest:
    """容灾链不能串行试完 22 个接口 — 实测每岗位曾耗时 7~8 分钟"""

    def _chain(self, n=22):
        from boss_bot.greet_engine import AIAnalyzerChain
        providers = [{"name": f"p{i}", "api_key": f"k{i}",
                      "api_base": f"https://a{i}", "model": "m"} for i in range(n)]
        return AIAnalyzerChain(providers=providers, cache_enabled=False)

    def test_stops_after_max_attempts(self):
        chain = self._chain(22)
        calls = []
        with patch.object(chain, "_call_provider_api",
                          side_effect=lambda p, m: calls.append(p.name) or (_ for _ in ()).throw(RuntimeError("挂了"))):
            chain.analyze_job({"job_name": "x", "url": "u"})
        assert len(calls) == chain.MAX_ATTEMPTS_PER_JOB

    def test_failed_provider_cools_down_and_is_skipped_next_job(self):
        chain = self._chain(3)
        with patch.object(chain, "_call_provider_api", side_effect=RuntimeError("403 FreeTier")):
            chain.analyze_job({"job_name": "x", "url": "u1"})
        assert set(chain._cooldown_until) == {"p0", "p1", "p2"}
        # 第二个岗位不该再试这些接口，而是直接报 AI 不可用
        second = []
        with patch.object(chain, "_call_provider_api",
                          side_effect=lambda p, m: second.append(p.name)):
            result = chain.analyze_job({"job_name": "y", "url": "u2"})
        assert second == []
        assert result["ai_error"] is True

    def test_success_clears_only_that_provider(self):
        """成功只解除该接口的冷却；仍在冷却里的其它接口继续跳过"""
        chain = self._chain(2)
        chain._cooldown_until["p0"] = time.time() + 100
        tried = []
        with patch.object(chain, "_call_provider_api",
                          side_effect=lambda p, m: (tried.append(p.name),
                                                    {"score": 88, "is_match": True, "reason": "ok"})[1]):
            result = chain.analyze_job({"job_name": "z", "url": "u3"})
        assert result["score"] == 88
        assert tried == ["p1"]
        assert "p0" in chain._cooldown_until      # 冷却未到期，保留
        assert "p1" not in chain._cooldown_until  # 成功后解除

    def test_budget_limits_total_time(self):
        """超出预算就放弃剩余接口，不能把 22 个全试完"""
        chain = self._chain(22)
        clock = [1000.0]

        def fake_now():
            clock[0] += 40.0     # 每次读时钟就"过去" 40 秒
            return clock[0]

        tried = []
        with patch("boss_bot.greet_engine.time.time", side_effect=fake_now), \
             patch.object(chain, "_call_provider_api",
                          side_effect=lambda p, m: tried.append(p.name) or (_ for _ in ()).throw(RuntimeError("超时"))):
            result = chain.analyze_job({"job_name": "w", "url": "u4"})
        assert result["ai_error"] is True
        assert len(tried) < 5, f"预算失效，试了 {len(tried)} 个接口"


class CookieValidityCheckTest:
    """Cookie 检测必须真的看过期时间 — 旧实现只看字段名，过期也报有效"""

    def _write(self, tmp_path, cookies):
        path = tmp_path / "cookies.json"
        path.write_text(json.dumps(cookies), encoding="utf-8")
        return str(path)

    def test_valid_with_future_expiry(self, tmp_path):
        from boss_bot.browser_launcher import check_cookie_valid_simple
        now = time.time()
        r = check_cookie_valid_simple(self._write(tmp_path, [
            {"name": "wt2", "value": "x", "expires": now + 5 * 86400},
            {"name": "zp_at", "value": "y", "expires": now + 2 * 86400},
        ]))
        assert r["valid"] is True
        assert r["checks"]["expires_in_days"] == pytest.approx(2.0, abs=0.1)

    def test_expired_reported_as_invalid(self, tmp_path):
        from boss_bot.browser_launcher import check_cookie_valid_simple
        now = time.time()
        r = check_cookie_valid_simple(self._write(tmp_path, [
            {"name": "wt2", "value": "x", "expires": now - 3 * 86400},
            {"name": "bst", "value": "y", "expires": now - 1 * 86400},
        ]))
        assert r["valid"] is False
        assert r["checks"]["expired"] is True
        assert "过期" in r["reason"]

    def test_missing_auth_cookie(self, tmp_path):
        from boss_bot.browser_launcher import check_cookie_valid_simple
        r = check_cookie_valid_simple(self._write(tmp_path, [
            {"name": "BAIDUID", "value": "x", "expires": -1},
        ]))
        assert r["valid"] is False
        assert "缺少登录字段" in r["reason"]

    def test_session_cookie_counts_as_usable(self, tmp_path):
        from boss_bot.browser_launcher import check_cookie_valid_simple
        r = check_cookie_valid_simple(self._write(tmp_path, [
            {"name": "wbg", "value": "x", "expires": -1},
        ]))
        assert r["valid"] is True
        assert r["checks"]["expires_in_days"] is None

    def test_real_login_cookie_names_recognized(self):
        """字段名来自实测的真实会话，不能退回以前那个不存在的 wbct"""
        from boss_bot.browser_launcher import BOSS_AUTH_COOKIES
        assert "wbct" not in BOSS_AUTH_COOKIES
        assert set(BOSS_AUTH_COOKIES) == {"wt2", "zp_at", "bst", "wbg"}


class AutoLoginDetectionTest:
    """登录成功要自动识别，不再强制人工点「我已登录」"""

    def _loop(self, wait_timeout=300):
        from boss_bot.main_loop import UnifiedBotLoop, UnifiedConfig
        with patch('boss_bot.main_loop.BrowserManager'):
            loop = UnifiedBotLoop()
        loop.config = UnifiedConfig()
        loop.config.login.wait_timeout = wait_timeout
        loop._running = True
        loop._stop_event = threading.Event()
        return loop

    def test_detects_login_without_manual_click(self):
        import boss_bot.main_loop as ml
        loop = self._loop()
        instance = MagicMock()
        instance._get_all_cookies.return_value = [
            {"name": "wt2", "value": "x", "expires": time.time() + 86400}]
        instance.url = "https://www.zhipin.com/web/geek/chat"
        with patch.object(ml, "LOGIN_POLL_INTERVAL", 0.1):
            assert loop._wait_for_login(instance, "cookies.json") is True

    def test_expired_auth_cookie_does_not_count_as_logged_in(self):
        import boss_bot.main_loop as ml
        loop = self._loop(wait_timeout=0)     # 超时立刻到 → 不进循环
        instance = MagicMock()
        instance._get_all_cookies.return_value = [
            {"name": "wt2", "value": "x", "expires": time.time() - 86400}]
        assert loop._has_live_auth_cookie(instance) is False

    def test_redirect_to_login_page_is_not_logged_in(self):
        loop = self._loop()
        instance = MagicMock()
        instance.url = "https://www.zhipin.com/web/user/?ka=header-login"
        assert loop._chat_page_reachable(instance) is False

    def test_manual_button_still_works(self):
        import boss_bot.main_loop as ml
        loop = self._loop()
        loop._login_event.set()
        instance = MagicMock()
        instance._get_all_cookies.return_value = []
        with patch.object(ml, "LOGIN_POLL_INTERVAL", 0.1):
            assert loop._wait_for_login(instance, "cookies.json") is True

    def test_timeout_returns_false(self):
        loop = self._loop(wait_timeout=0)
        instance = MagicMock()
        instance._get_all_cookies.return_value = []
        assert loop._wait_for_login(instance, "cookies.json") is False

    def test_stop_breaks_the_wait(self):
        import boss_bot.main_loop as ml
        loop = self._loop(wait_timeout=600)
        loop._running = False
        instance = MagicMock()
        with patch.object(ml, "LOGIN_POLL_INTERVAL", 0.1):
            assert loop._wait_for_login(instance, "cookies.json") is False


# ===================== 回复引擎 AI 调用 =====================

class ReplyAiCallTest:
    """回复侧 AI：provider 字段两种命名都要认，且必须带超时"""

    def _engine(self):
        from boss_bot.reply_engine import ReplyEngine
        return ReplyEngine()

    def test_hot_reload_provider_shape_accepted(self):
        engine = self._engine()
        engine._ai_providers = [{
            "name": "主", "api_key": "sk-1", "api_base": "https://a/v1",
            "model": "m1", "timeout": 12,
        }]
        with patch("openai.OpenAI") as mock_oai, \
             patch.object(engine, "_call_with_rate_limit_retry", return_value="在的"):
            out = engine._ask_ai("在吗", "HR", "数据分析", [])
        assert out == "在的"
        assert mock_oai.call_args.kwargs["api_key"] == "sk-1"
        assert mock_oai.call_args.kwargs["base_url"] == "https://a/v1"
        assert mock_oai.call_args.kwargs["timeout"] == 12
        assert mock_oai.call_args.kwargs["max_retries"] == 0

    def test_legacy_provider_shape_still_works(self):
        engine = self._engine()
        engine._ai_providers = [{"key": "sk-2", "url": "https://b/v1", "model": "m2"}]
        with patch("openai.OpenAI") as mock_oai, \
             patch.object(engine, "_call_with_rate_limit_retry", return_value="好的"):
            out = engine._ask_ai("在吗", "HR", "数据分析", [])
        assert out == "好的"
        assert mock_oai.call_args.kwargs["api_key"] == "sk-2"

    def test_incomplete_provider_skipped_not_crash(self):
        engine = self._engine()
        engine._ai_providers = [{"key": "", "url": "", "model": ""}]
        with patch.object(engine, "_call_with_rate_limit_retry") as call:
            out = engine._ask_ai("在吗", "HR", "岗位", [])
        call.assert_not_called()
        assert out is None

    def test_wait_human_delay_uses_instance_range(self):
        engine = self._engine()
        engine._min_delay = 41
        engine._max_delay = 42
        with patch("boss_bot.reply_engine.random.uniform", return_value=41.5) as uni, \
             patch("boss_bot.reply_engine.time.sleep"):
            engine.wait_human_delay()
        assert (uni.call_args.args[0], uni.call_args.args[1]) == (41, 42)


# ===================== 原子写入 =====================

class AtomicWriteTest:
    """write_json_atomic — 进程被杀不能留下半截 JSON"""

    def test_roundtrip(self, tmp_path):
        from boss_bot.unified_config import write_json_atomic
        target = tmp_path / "sub" / "state.json"
        write_json_atomic(target, {"a": 1, "中文": "值"})
        assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1, "中文": "值"}

    def test_no_tmp_leftover(self, tmp_path):
        from boss_bot.unified_config import write_json_atomic
        target = tmp_path / "state.json"
        write_json_atomic(target, {"a": 1})
        assert list(tmp_path.iterdir()) == [target]

    def test_failed_dump_keeps_original(self, tmp_path):
        """序列化失败时旧文件必须仍然完整可读（原子写的全部意义）"""
        from boss_bot.unified_config import write_json_atomic
        target = tmp_path / "state.json"
        write_json_atomic(target, {"keep": "me"})

        class Boom:
            pass

        with pytest.raises(TypeError):
            write_json_atomic(target, {"new": Boom()})
        assert json.loads(target.read_text(encoding="utf-8")) == {"keep": "me"}
        assert list(tmp_path.glob("*.tmp")) == []

    def test_state_store_recovers_from_torn_file(self, tmp_path):
        path = tmp_path / "bot_state.json"
        path.write_text('{"chats": {"a": ', encoding="utf-8")
        from boss_bot.state_store import StateStore
        store = StateStore(path=str(path))
        assert store.is_paused() is False
        store.pause(reason="测试")
        assert json.loads(path.read_text(encoding="utf-8"))["paused"]["reason"] == "测试"


# ===================== 标签页精确关闭 =====================

class TabCloseTest:
    """close_current_tab 只能关掉自己那一个标签页"""

    def test_tab_object_closed_precisely(self):
        from boss_bot.browser_launcher import BrowserInstance
        tab = MagicMock()
        del tab.get_tabs
        BrowserInstance(chrome_page=tab).close_current_tab()
        tab.close.assert_called_once()

    def test_browser_level_object_not_closed(self):
        """浏览器级对象的 close() 会带走整个浏览器，必须拒绝"""
        from boss_bot.browser_launcher import BrowserInstance

        class FakePage:
            def get_tabs(self):
                return []

            def close(self):
                raise AssertionError("不能关闭整个浏览器")

        BrowserInstance(chrome_page=FakePage()).close_current_tab()

    def test_drissionpage_tab_has_no_close_current_tab(self):
        """4.1 的标签页对象没有 close_current_tab，老代码调它必然抛异常"""
        from DrissionPage._pages.chromium_tab import ChromiumTab
        assert not hasattr(ChromiumTab, "close_current_tab")


# ===================== 拒绝跳过的判定范围 =====================

def _hr(text, **kw):
    return {"text": text, "is_mine": False, **kw}


def _me(text):
    return {"text": text, "is_mine": True}


class RejectionScopeTest:
    """防骚扰跳过只能由当前这段对话的拒绝决定，不能拿昵称或岗位名去猜人。

    BOSS 只显示"杨女士""胡女士"，同名的是不同的人，扫全部聊天记录必然误判。
    """

    def test_empty_history_is_not_rejected(self):
        from boss_bot.reply_engine import conversation_rejected
        assert conversation_rejected([]) is False
        assert conversation_rejected(None) is False

    def test_tail_rejection_counts(self):
        from boss_bot.reply_engine import conversation_rejected
        dialog = [_hr("您好，还招数据分析吗"),
                  _me("招的，我有5年经验"),
                  _hr("抱歉，经验跟岗位不太合适")]
        assert conversation_rejected(dialog) is True

    def test_rejection_is_not_searched_through_whole_history(self):
        """拒绝之后 HR 又主动说话 — 对话还在继续，不算已拒绝"""
        from boss_bot.reply_engine import conversation_rejected
        dialog = [_hr("抱歉，不太合适"),
                  _me("好的，祝您招聘顺利"),
                  _hr("刚问错岗位了，我们这个其实很匹配你的简历")]
        assert conversation_rejected(dialog) is False

    def test_our_reply_after_rejection_keeps_it_rejected(self):
        from boss_bot.reply_engine import conversation_rejected
        assert conversation_rejected([_hr("暂不考虑"), _me("好的谢谢")]) is True

    def test_non_text_tail_does_not_hide_rejection(self):
        from boss_bot.reply_engine import conversation_rejected
        dialog = [_hr("不合适"), _hr("", type="image")]
        assert conversation_rejected(dialog) is True

    def test_two_same_name_hr_scoped_independently(self):
        """两个"杨女士"各判各的：一份对话以拒绝收尾，另一份不受影响"""
        from boss_bot.reply_engine import conversation_rejected
        assert conversation_rejected([_hr("简历看了，不合适")]) is True
        assert conversation_rejected([_hr("方便下周来面试吗")]) is False


class RejectionDecisionTest:
    """get_reply 的拒绝分支只能看当前这段对话"""

    def _engine(self):
        from boss_bot.reply_engine import ReplyEngine
        engine = ReplyEngine()
        engine._ai_providers = []
        engine._ask_ai = MagicMock(return_value=None)
        return engine

    def test_skip_only_when_this_conversation_was_rejected(self):
        engine = self._engine()
        dialog = [_hr("在吗"), _hr("抱歉不太合适"), _me("好的谢谢"), _hr("在忙吗")]
        action, content, meta = engine.get_reply(
            dialog, "杨女士", "数据分析岗", chat_name="杨女士")
        assert action == "none"
        assert meta["source"] == "rejection"

    def test_re_engage_after_rejection_is_answered(self):
        engine = self._engine()
        dialog = [_hr("抱歉不太合适"), _me("好的谢谢"), _hr("再聊聊？想约你面试")]
        action, content, meta = engine.get_reply(
            dialog, "杨女士", "数据分析岗", chat_name="杨女士")
        assert meta["source"] != "rejection"

    def test_unanswered_rejection_gets_polite_closing(self):
        engine = self._engine()
        action, content, meta = engine.get_reply(
            [_hr("简历跟岗位要求不匹配，算了")], "胡女士", "供应链数据分析", chat_name="胡女士")
        assert action == "text"
        assert meta["intent"] == "rejection"

    def test_store_history_not_used_when_list_passed(self):
        """调用方已经给了对话列表时，不能再按昵称覆盖成 store 里的合并历史"""
        from boss_bot.reply_engine import ReplyEngine
        store = MagicMock()
        store.get_full_dialog.return_value = [_hr("抱歉，暂不合适")]
        engine = ReplyEngine(message_store=store)
        engine._ai_providers = []
        engine._ask_ai = MagicMock(return_value=None)
        engine.get_reply([_hr("方便聊一下吗")], "杨女士", "岗位A", chat_name="杨女士")
        store.get_full_dialog.assert_not_called()


class _FakeChatStore:
    """按 姓名+公司 存文件的会话库 — 同名不同公司是两个文件，不再共用一路对话"""

    def __init__(self, detail, dialog):
        self._detail = detail
        self._dialog = dialog
        self.merged = []
        self.dialog_reads = []

    def get_chat_detail(self, name, job_name="", company=""):
        return self._detail

    def get_full_dialog(self, name, limit=50, job_name="", company=""):
        self.dialog_reads.append((name, company))
        return self._dialog

    def merge_messages(self, chat_name, new_messages, job_name="", company=""):
        self.merged.append((chat_name, job_name, company))
        return len(new_messages)


class ProcessSingleChatRejectionTest:
    """_process_single_chat 的跳过必须来自当前会话本身"""

    def _make_loop(self, live_messages, live_job, stored_detail, stored_dialog,
                   selected_company="某某科技"):
        from boss_bot.main_loop import UnifiedBotLoop
        with patch('boss_bot.main_loop.BrowserManager'):
            loop = UnifiedBotLoop()
        loop._chat_handler = MagicMock()
        loop._chat_handler.enter_chat.return_value = True
        loop._chat_handler.read_all_messages.return_value = live_messages
        loop._chat_handler.get_boss_name.return_value = "杨女士"
        loop._chat_handler.get_job_name.return_value = live_job
        # 会话身份以"点完之后真正 selected 的那一行"为准
        loop._chat_handler.read_selected_row.return_value = {
            "index": 3, "name": "杨女士", "company": selected_company, "title": "HR"}
        loop._msg_store = _FakeChatStore(stored_detail, stored_dialog)
        loop._reply_engine = MagicMock()
        loop._reply_engine.get_reply.return_value = ("none", None, {})
        loop._state_store = MagicMock()
        loop._state_store.was_handled.return_value = False
        loop._state_store.is_paused.return_value = False
        loop._notifier = MagicMock()
        loop._notifier.notify_if_important.return_value = False
        loop._stats = MagicMock()
        loop._stats_dict = {"reply_skipped": 0, "reply_sent": 0, "important_events": 0}
        loop._emit_reply_event = MagicMock()
        loop._log = MagicMock()
        return loop

    def _skip_reasons(self, loop):
        return [c.kwargs.get("skip_reason", "")
                for c in loop._reply_engine._add_record.call_args_list]

    def test_no_pre_enter_scan_by_nickname(self):
        """store 里有同名 HR 的拒绝记录，也必须照常进入会话再判断"""
        loop = self._make_loop(
            live_messages=[_hr("方便聊一下吗")],
            live_job="数据分析",
            stored_detail={"chat_name": "杨女士", "job_name": "其他公司岗位"},
            stored_dialog=[_hr("抱歉，暂不合适")],
        )
        loop._process_single_chat({"name": "杨女士"})
        loop._chat_handler.enter_chat.assert_called_once()
        assert not any("已拒绝" in r for r in self._skip_reasons(loop))

    def test_rejected_and_already_answered_skips(self):
        loop = self._make_loop(
            live_messages=[_hr("抱歉，不合适"), _me("好的，祝您招聘顺利")],
            live_job="数据分析",
            stored_detail={"chat_name": "杨女士", "job_name": "数据分析"},
            stored_dialog=[_hr("抱歉，不合适")],
        )
        loop._process_single_chat({"name": "杨女士"})
        loop._reply_engine.get_reply.assert_not_called()
        assert any("该会话HR已拒绝" in r for r in self._skip_reasons(loop))

    def test_unanswered_rejection_still_replies(self):
        loop = self._make_loop(
            live_messages=[_hr("抱歉，不合适")],
            live_job="数据分析",
            stored_detail={"chat_name": "杨女士", "job_name": "数据分析"},
            stored_dialog=[_hr("抱歉，不合适")],
        )
        loop._process_single_chat({"name": "杨女士"})
        loop._reply_engine.get_reply.assert_called_once()

    def test_会话身份用姓名加公司(self):
        """同名的两个 HR 分开存：落文件时带上 selected 行上的公司，不再靠岗位猜"""
        loop = self._make_loop(
            live_messages=[_hr("方便聊一下吗")],
            live_job="供应链数据分析员",
            stored_detail={"chat_name": "杨女士", "company": "某某科技"},
            stored_dialog=[_hr("抱歉，暂不合适"), _hr("随便看看")],
        )
        loop._process_single_chat({"name": "杨女士", "company": "某某科技"})
        assert loop._msg_store.merged == [("杨女士", "供应链数据分析员", "某某科技")]
        # 取历史也用同一个身份，否则读到的是另一个"杨女士"的对话
        assert loop._msg_store.dialog_reads == [("杨女士", "某某科技")]

    def test_selected行不是目标时不记录(self):
        """侧栏会重排：点完发现 selected 是别人，就不能把消息写进目标会话里"""
        loop = self._make_loop(
            live_messages=[_hr("方便聊一下吗")],
            live_job="数据分析",
            stored_detail={"chat_name": "杨女士", "job_name": "数据分析"},
            stored_dialog=[],
        )
        loop._chat_handler.read_selected_row.return_value = {
            "index": 9, "name": "李女士", "company": "另一家", "title": "HR"}
        loop._process_single_chat({"name": "杨女士"})
        assert loop._msg_store.merged == []
        loop._reply_engine.get_reply.assert_not_called()

    def test_greet_round_does_not_scan_other_chats(self):
        """打招呼侧不能再按昵称扫别的会话，同名会误伤"""
        import inspect
        from boss_bot.main_loop import UnifiedBotLoop
        src = inspect.getsource(UnifiedBotLoop._run_greet_round)
        assert "get_chat_list" not in src
        assert "get_full_dialog" not in src


# ===================== 漏斗指标 =====================

class MetricsStoreTest:
    """累计/当日/分账号计数器 — 记录文件会截断，指标不能跟着丢"""

    def _store(self, tmp_path):
        from boss_bot.metrics import MetricsStore
        return MetricsStore(path=str(tmp_path / "metrics.json"))

    def test_bump_both_scopes(self, tmp_path):
        s = self._store(tmp_path)
        s.bump(0, "greet_sent")
        s.bump(0, "greet_sent")
        snap = s.snapshot()
        assert snap["daily"]["greet_sent"] == 2
        assert snap["total"]["greet_sent"] == 2

    def test_accounts_do_not_share_counters(self, tmp_path):
        s = self._store(tmp_path)
        s.bump(0, "greet_sent", 3)
        s.bump(1, "greet_sent", 5)
        assert s.snapshot([0])["total"]["greet_sent"] == 3
        assert s.snapshot([1])["total"]["greet_sent"] == 5
        assert s.snapshot()["total"]["greet_sent"] == 8

    def test_interview_deduped_per_conversation(self, tmp_path):
        s = self._store(tmp_path)
        assert s.add_interview(0, "杨女士") is True
        assert s.add_interview(0, "杨女士") is False   # 同一个人反复问只算一次
        assert s.add_interview(0, "胡女士") is True
        assert s.add_interview(1, "杨女士") is True    # 换账号是另一个会话
        snap = s.snapshot()
        assert snap["total"]["interview"] == 3
        assert snap["interview_chats_total"] == 3

    def test_day_rollover_clears_today_keeps_total(self, tmp_path):
        from datetime import date, timedelta
        s = self._store(tmp_path)
        s.bump(0, "resume_sent")
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        s._data["date"] = yesterday
        s._data["today"]["0"]["resume_sent"] = 42
        snap = s.snapshot()
        assert snap["daily"]["resume_sent"] == 0
        assert snap["total"]["resume_sent"] == 1
        assert snap["date"] == date.today().isoformat()

    def test_survives_reload(self, tmp_path):
        s = self._store(tmp_path)
        s.bump(0, "greet_sent", 7)
        s.add_interview(0, "李女士")
        again = self._store(tmp_path)
        assert again.snapshot()["total"]["greet_sent"] == 7
        assert again.snapshot()["total"]["interview"] == 1

    def test_unknown_field_ignored(self, tmp_path):
        s = self._store(tmp_path)
        s.bump(0, "nonsense")
        assert s.snapshot()["total"] == {"greet_sent": 0, "resume_sent": 0,
                                        "interview": 0}

    def test_backfill_from_history_once(self, tmp_path):
        """首次要把历史记录换算成累计值，否则用户看到的累计是 0"""
        from datetime import date
        from boss_bot import reply_record
        from boss_bot.metrics import MetricsStore
        today = date.today().isoformat()
        greets = [reply_record.GreetRecord(is_greeted=True, account_index=0,
                                          timestamp=f"{today} 10:00:00"),
                  reply_record.GreetRecord(is_greeted=True, account_index=1,
                                          timestamp="2026-01-01 10:00:00"),
                  reply_record.GreetRecord(is_greeted=False, account_index=0,
                                          timestamp=f"{today} 10:01:00")]
        replies = [
            reply_record.ReplyRecord(chat_name="杨女士", reply_intent="invite_interview",
                                     account_index=0, timestamp=f"{today} 11:00:00"),
            reply_record.ReplyRecord(chat_name="杨女士", reply_intent="invite_interview",
                                     account_index=0, timestamp=f"{today} 12:00:00"),
            reply_record.ReplyRecord(chat_name="杨女士", reply_content="[简历已发送]",
                                     account_index=0, timestamp=f"{today} 12:01:00"),
        ]
        with patch("boss_bot.reply_record._get_greet_store",
                   return_value=MagicMock(get_all=MagicMock(return_value=greets))), \
             patch("boss_bot.reply_record._get_reply_store",
                   return_value=MagicMock(get_all=MagicMock(return_value=replies))):
            s = MetricsStore(path=str(tmp_path / "m.json"), backfill=True)
        snap = s.snapshot()
        assert snap["total"]["greet_sent"] == 2          # 只算成功的那两条
        assert snap["daily"]["greet_sent"] == 1          # 今天只有账号0 那条
        assert snap["total"]["interview"] == 1           # 同一会话两条只算一次
        assert snap["total"]["resume_sent"] == 1
        # 再建一次不该重复累加
        again = MetricsStore(path=str(tmp_path / "m.json"), backfill=True)
        assert again.snapshot()["total"]["greet_sent"] == 2


# ===================== 多账号数据隔离 =====================

class AccountIsolationTest:
    """两个账号不能再共用 state/stats/聊天记录"""

    def test_account_file_keeps_legacy_path_for_first_account(self, tmp_path):
        from boss_bot.unified_config import account_file
        p = str(tmp_path / "bot_state.json")
        assert account_file(p, 0) == p
        out = account_file(p, 1)
        assert out.endswith("bot_state_account_1.json")

    def test_stores_are_per_account(self):
        import inspect
        from boss_bot.main_loop import UnifiedBotLoop
        src = inspect.getsource(UnifiedBotLoop._init_engines)
        assert "StateStore(\n            path=account_file" in src or "account_file(STATE_FILE" in src
        assert "account_file(STATS_FILE" in src
        assert "MessageStore(account_index=self.account_index)" in src

    def test_message_files_split_by_account(self, tmp_path):
        from boss_bot.message_store import MessageStore
        a0 = MessageStore(base_dir=tmp_path, account_index=0)
        a1 = MessageStore(base_dir=tmp_path, account_index=1)
        a0.save_messages("杨女士", [{"text": "A账号的对话", "is_mine": False}], "岗位甲")
        a1.save_messages("杨女士", [{"text": "B账号的对话", "is_mine": False}], "岗位乙")
        assert a0.get_messages("杨女士")[0]["text"] == "A账号的对话"
        assert a1.get_messages("杨女士")[0]["text"] == "B账号的对话"
        # 文件名带会话身份（姓名+岗位）：同昵称不同岗位是两段对话，
        # 只按昵称存会让两个 HR 的消息混在一个文件里，界面就和 BOSS 对不上
        names = sorted(p.name for p in tmp_path.glob("*.json"))
        assert names == ["a1_杨女士_岗位乙.json", "杨女士_岗位甲.json"]

    def test_default_account_still_sees_all_chats(self, tmp_path):
        """Web 端用账号0 的实例列会话，账号2 的也要能看到"""
        from boss_bot.message_store import MessageStore
        MessageStore(base_dir=tmp_path, account_index=1).save_messages(
            "胡女士", [{"text": "只有账号2 有", "is_mine": False}], "岗位丙")
        a0 = MessageStore(base_dir=tmp_path, account_index=0)
        listing = a0.get_chat_list()
        assert [c["chat_name"] for c in listing] == ["胡女士"]
        assert listing[0]["account_index"] == 1
        assert a0.get_messages("胡女士")[0]["text"] == "只有账号2 有"

    def test_meta_files_not_listed_as_chats(self, tmp_path):
        from boss_bot.message_store import MessageStore
        ms = MessageStore(base_dir=tmp_path)
        ms.save_messages("女士", [{"text": "正文", "is_mine": False}], "岗位")
        (tmp_path / "女士.meta.json").write_text('{"chat_name": "女士"}', encoding="utf-8")
        assert len(ms.get_chat_list()) == 1

    def test_daily_cap_counted_per_account(self):
        import inspect
        from boss_bot.main_loop import UnifiedBotLoop
        src = inspect.getsource(UnifiedBotLoop._run_greet_round)
        assert "r.account_index == self.account_index" in src


# ===================== AI 接口体检 =====================

class AiHealthTest:
    """体检必须真的发一条消息并拿到内容才算可用"""

    class _BlankMessage:
        """只有 content 为空、且没有 reasoning 属性的真实形状"""
        content = "   "

    def _provider(self):
        return {"name": "测试", "api_key": "sk-1", "api_base": "https://a/v1",
                "model": "m1"}

    def test_reply_content_means_available(self):
        from boss_bot.ai_health import probe_one, STATUS_AVAILABLE
        with patch("openai.OpenAI") as mock_oai:
            mock_oai.return_value.chat.completions.create.return_value = \
                MagicMock(choices=[MagicMock(message=MagicMock(content="连接成功"))])
            res = probe_one(self._provider())
        assert res["status"] == STATUS_AVAILABLE
        assert res["reply"] == "连接成功"
        assert res["latency_ms"] is not None

    def test_empty_reply_counts_as_unavailable(self):
        from boss_bot.ai_health import probe_one, STATUS_UNAVAILABLE
        with patch("openai.OpenAI") as mock_oai:
            mock_oai.return_value.chat.completions.create.return_value = \
                MagicMock(choices=[MagicMock(message=self._BlankMessage())])
            res = probe_one(self._provider())
        assert res["status"] == STATUS_UNAVAILABLE
        assert "没有任何内容" in res["reason"]

    def test_reasoning_only_reply_still_counts_as_alive(self):
        """推理模型正文为空、话在 reasoning 字段 — 接口本身是好的"""
        from boss_bot.ai_health import probe_one, STATUS_AVAILABLE

        class Msg:
            content = ""
            reasoning_content = "用户要求只回四个字，我回复：连接成功"

        with patch("openai.OpenAI") as mock_oai:
            mock_oai.return_value.chat.completions.create.return_value = \
                MagicMock(choices=[MagicMock(message=Msg())])
            res = probe_one(self._provider())
        assert res["status"] == STATUS_AVAILABLE
        assert "reasoning" in res["note"]

    def test_error_classified_in_chinese(self):
        from boss_bot.ai_health import probe_one, classify_error
        assert classify_error("Error code: 401 - Unauthorized") == "API Key 无效或已过期"
        assert classify_error("insufficient quota") == "额度用尽或被限流"
        assert classify_error("Connection error.") == "网络不通/域名解析失败"
        with patch("openai.OpenAI", side_effect=RuntimeError("bad key")):
            res = probe_one(self._provider())
        assert res["status"] == "unavailable"
        assert res["error"]

    def test_incomplete_config_skipped_without_request(self):
        from boss_bot.ai_health import probe_one
        with patch("openai.OpenAI") as mock_oai:
            res = probe_one({"name": "x", "api_key": "", "api_base": "", "model": ""})
        mock_oai.assert_not_called()
        assert "配置不完整" in res["reason"]

    def test_request_has_timeout_and_no_retry(self):
        from boss_bot.ai_health import probe_one
        with patch("openai.OpenAI") as mock_oai:
            mock_oai.return_value.chat.completions.create.return_value = \
                MagicMock(choices=[MagicMock(message=MagicMock(content="ok"))])
            probe_one(self._provider(), timeout=7)
        assert mock_oai.call_args.kwargs["timeout"] == 7
        assert mock_oai.call_args.kwargs["max_retries"] == 0

    def test_merge_and_summarize(self, tmp_path):
        from boss_bot.ai_health import merge_results, summarize, save_health, load_health
        providers = [self._provider(), {"name": "坏", "api_key": "sk-2",
                                        "api_base": "https://b/v1", "model": "m2"}]
        results = [
            {"name": "测试", "api_key": "", "api_base": "https://a/v1", "model": "m1",
             "status": "available", "latency_ms": 300, "reply": "连接成功", "reason": ""},
            {"name": "坏", "api_key": "", "api_base": "https://b/v1", "model": "m2",
             "status": "unavailable", "latency_ms": 1200, "reply": "",
             "reason": "API Key 无效或已过期"},
        ]
        merged = merge_results({}, results, providers)
        assert summarize(merged["results"]) == {
            "total": 2, "available": 1, "unavailable": 1, "avg_latency_ms": 750}
        save_health(merged, path=tmp_path / "ai_health.json")
        assert load_health(path=tmp_path / "ai_health.json")["results"]

    def test_probe_all_reports_progress_per_provider(self):
        from boss_bot.ai_health import probe_all
        seen = []
        with patch("boss_bot.ai_health.probe_one", return_value={"status": "available"}), \
             patch("boss_bot.ai_health.time.sleep"):
            out = probe_all([self._provider(), self._provider()],
                            on_result=lambda i, p, r: seen.append(i))
        assert seen == [0, 1]
        assert len(out) == 2
        assert out[0]["index"] == 0


class CookieIsolationTest:
    """Cookie 按账号选取 + 失效清理（前端「清除失效Cookie」开关的真实消费方）"""

    def _make_loop(self, tmp_path, account_index=0, clear_on_failure=True,
                   account_cookie="a1.json"):
        from boss_bot.main_loop import UnifiedBotLoop
        from boss_bot.unified_config import UnifiedConfig, AccountConfig
        cfg = UnifiedConfig()
        cfg.login.clear_cookies_on_failure = clear_on_failure
        cfg.login.cookie_file = str(tmp_path / "global.json")
        cfg.greet.accounts = [
            AccountConfig(name="主账号", cookie_file=str(tmp_path / "a0.json")),
            AccountConfig(name="账号2", cookie_file=str(tmp_path / account_cookie)),
        ]
        with patch("boss_bot.main_loop.BrowserManager"):
            return UnifiedBotLoop(config=cfg, account_index=account_index)

    def test_每个账号读自己的cookie文件(self, tmp_path):
        loop = self._make_loop(tmp_path, account_index=1)
        assert loop._cookie_file().endswith("a0.json") is False
        assert loop._cookie_file().endswith("a1.json")

    def test_账号未填时回落到全局(self, tmp_path):
        loop = self._make_loop(tmp_path, account_index=1)
        loop.config.greet.accounts[1].cookie_file = ""
        assert loop._cookie_file().endswith("global.json")

    def test_登录态失效时删除失效cookie(self, tmp_path):
        loop = self._make_loop(tmp_path, account_index=0)
        stale = tmp_path / "a0.json"
        stale.write_text("[]", encoding="utf-8")
        loop._discard_stale_cookies("测试")
        assert not stale.exists()

    def test_关闭开关时保留cookie供排查(self, tmp_path):
        loop = self._make_loop(tmp_path, account_index=0, clear_on_failure=False)
        keep = tmp_path / "a0.json"
        keep.write_text("[]", encoding="utf-8")
        loop._discard_stale_cookies("测试")
        assert keep.exists()

    def test_文件本就不存在时不报错(self, tmp_path):
        loop = self._make_loop(tmp_path, account_index=0)
        loop._discard_stale_cookies("测试")


class CliEngineModeTest:
    """命令行 --greet / --reply 经环境变量下传给配置（热重载不会冲掉）"""

    def test_仅打招呼会关掉回复(self, monkeypatch):
        from boss_bot.unified_config import UnifiedConfig
        monkeypatch.setenv("BOSS_BOT_ONLY_ENGINE", "greet")
        cfg = UnifiedConfig.load()
        assert cfg.greet.enabled is True
        assert cfg.reply.enabled is False

    def test_仅回复会关掉打招呼(self, monkeypatch):
        from boss_bot.unified_config import UnifiedConfig
        monkeypatch.setenv("BOSS_BOT_ONLY_ENGINE", "reply")
        cfg = UnifiedConfig.load()
        assert cfg.greet.enabled is False
        assert cfg.reply.enabled is True

    def test_未指定时两边都按配置(self, monkeypatch):
        from boss_bot.unified_config import UnifiedConfig
        monkeypatch.delenv("BOSS_BOT_ONLY_ENGINE", raising=False)
        cfg = UnifiedConfig.load()
        assert cfg.greet.enabled is True and cfg.reply.enabled is True

    def test_配置文件路径可用环境变量指定(self, monkeypatch, tmp_path):
        from boss_bot.unified_config import UnifiedConfig
        custom = tmp_path / "custom_config.json"
        custom.write_text(json.dumps({"retry": {"max_attempts": 7}}),
                          encoding="utf-8")
        monkeypatch.setenv("BOSS_BOT_CONFIG", str(custom))
        assert UnifiedConfig.load().greet.retry.max_attempts == 7


class ConfigSavePreservesKeysTest:
    """cfg.save() 不得抹掉 UnifiedConfig 未建模的顶层键（theme 等）"""

    def test_保存后未建模字段仍在(self, tmp_path):
        from boss_bot.unified_config import UnifiedConfig
        target = tmp_path / "bot_config.json"
        target.write_text(json.dumps({
            "theme": "light",
            "notify": {"sound": True, "desktop": False},
            "retry": {"max_attempts": 5},
        }), encoding="utf-8")
        UnifiedConfig.load(config_path=str(target), overrides_path=str(tmp_path / "none.json")).save(str(target))
        saved = json.loads(target.read_text(encoding="utf-8"))
        assert saved["theme"] == "light"
        assert saved["retry"]["max_attempts"] == 5

    def test_已建模字段以内存值为准(self, tmp_path):
        from boss_bot.unified_config import UnifiedConfig
        target = tmp_path / "bot_config.json"
        target.write_text(json.dumps({"rate_limit": {"max_per_day": 150}}), encoding="utf-8")
        cfg = UnifiedConfig.load(config_path=str(target), overrides_path=str(tmp_path / "none.json"))
        cfg.greet.rate_limit.max_per_day = 42
        cfg.save(str(target))
        saved = json.loads(target.read_text(encoding="utf-8"))
        assert saved["rate_limit"]["max_per_day"] == 42


class PerAccountGreetSettingsTest:
    """打招呼话术/简历图片必须按账号取，且热重载能刷到新值"""

    def _cfg(self, msg0="主号话术", msg1="二号话术", imgs1=None):
        from boss_bot.unified_config import UnifiedConfig, AccountConfig, JobConfig
        cfg = UnifiedConfig()
        cfg.greet.accounts = [
            AccountConfig(name="主账号", jobs=[JobConfig(greeting_message=msg0)]),
            AccountConfig(name="账号2", image_files=imgs1 or [],
                          jobs=[JobConfig(greeting_message=msg1)]),
        ]
        return cfg

    def _engine(self, cfg, idx):
        from boss_bot.greet_engine import GreetEngine
        return GreetEngine(MagicMock(), cfg, account_index=idx)

    def test_账号2用自己的话术(self):
        ge = self._engine(self._cfg(), 1)
        assert ge._greeting_message == "二号话术"

    def test_主账号用自己的话术(self):
        ge = self._engine(self._cfg(), 0)
        assert ge._greeting_message == "主号话术"

    def test_索引越界回落首个账号(self):
        ge = self._engine(self._cfg(), 7)
        assert ge._greeting_message == "主号话术"

    def test_改话术后热重载即生效(self):
        cfg = self._cfg()
        ge = self._engine(cfg, 1)
        assert ge._greeting_message == "二号话术"
        cfg.greet.accounts[1].jobs[0].greeting_message = "改过的二号话术"
        ge.reload_runtime_settings()
        assert ge._greeting_message == "改过的二号话术"

    def test_简历图片按账号取(self):
        cfg = self._cfg()
        cfg.greet.accounts[1].image_files = ["r2.pdf"]
        assert self._engine(cfg, 1)._image_files == ["r2.pdf"]
        assert self._engine(cfg, 0)._image_files == []


class ReplyRuntimeFieldsTest:
    """回复侧的每小时上限/兜底策略读的是可热重载的实例属性"""

    def _engine(self):
        from boss_bot.reply_engine import ReplyEngine
        return ReplyEngine()

    def test_上限改为零则不再回复(self):
        eng = self._engine()
        assert eng.can_reply() is True
        eng._max_replies_per_hour = 0
        assert eng.can_reply() is False

    def test_默认值来自配置(self):
        from boss_bot import config
        eng = self._engine()
        assert eng._max_replies_per_hour == config.MAX_REPLIES_PER_HOUR
        assert eng._ai_max_tokens == config.AI_MAX_TOKENS
        assert eng._ai_fail_action == config.AI_FAIL_ACTION


class DryRunGateTest:
    """演练模式：搜索与 AI 决策照做，最后一下发送必须被拦住"""

    def _loop(self, dry):
        from boss_bot.main_loop import UnifiedBotLoop
        from boss_bot.unified_config import UnifiedConfig
        cfg = UnifiedConfig()
        cfg.dry_run = dry
        with patch("boss_bot.main_loop.BrowserManager"):
            return UnifiedBotLoop(config=cfg)

    def test_演练模式下拦截发送动作(self):
        loop = self._loop(True)
        assert loop._dry_run("本应回复", "你好") is True

    def test_正常模式不拦截(self):
        assert self._loop(False)._dry_run("本应回复", "你好") is False

    def test_热重载后可随时切换(self):
        from boss_bot.unified_config import UnifiedConfig
        loop = self._loop(False)
        assert loop._dry_run("x") is False
        loop.config = UnifiedConfig()
        loop.config.dry_run = True
        assert loop._dry_run("x") is True

    def test_环境变量可开启(self, monkeypatch):
        from boss_bot.unified_config import UnifiedConfig
        monkeypatch.setenv("BOSS_BOT_DRY_RUN", "1")
        assert UnifiedConfig.load().dry_run is True

    def test_json配置可开启(self, tmp_path, monkeypatch):
        from boss_bot.unified_config import UnifiedConfig
        monkeypatch.delenv("BOSS_BOT_DRY_RUN", raising=False)
        target = tmp_path / "bot_config.json"
        target.write_text(json.dumps({"dry_run": True}), encoding="utf-8")
        cfg = UnifiedConfig.load(config_path=str(target), overrides_path=str(tmp_path / "none.json"))
        assert cfg.dry_run is True
        assert cfg.to_dict()["dry_run"] is True

    def test_真实发送点前都有演练闸门(self):
        """main_loop 里每处真实发送前必须先问 _dry_run，漏一处就会误发"""
        import pathlib
        import re
        src = pathlib.Path("boss_bot/main_loop.py").read_text(encoding="utf-8")
        guarded = 0
        for m in re.finditer(r"self\._(greet_engine\.send_greeting|chat_handler\.send_resume|"
                             r"chat_handler\.send_text)\(", src):
            before = src[:m.start()]
            tail = before[-600:]
            assert "_dry_run(" in tail, f"第 {src[:m.start()].count(chr(10)) + 1} 行发送前缺演练闸门"
            guarded += 1
        assert guarded >= 4, f"发送点数量异常: {guarded}"


class GreetCapAutoResumeTest:
    """投满每日上限暂停后，跨过零点要能自己恢复（人工暂停不恢复）"""

    def _loop(self):
        from boss_bot.main_loop import UnifiedBotLoop
        with patch("boss_bot.main_loop.BrowserManager"):
            loop = UnifiedBotLoop()
        loop._reply_engine = None
        loop._state_store = MagicMock()
        loop._state_store.is_paused.return_value = False
        eng = MagicMock()
        eng._max_per_day = 150
        eng._greet_store.filter.return_value = []   # 新的一天：今天还没投
        loop._greet_engine = eng
        return loop

    def test_跨零点自动恢复(self):
        loop = self._loop()
        loop._greet_paused = True
        loop._greet_paused_by_cap = True
        loop._greet_cap_paused_on = "2000-01-01"    # 昨天投满的
        loop._maybe_auto_resume_greet()
        assert loop._greet_paused is False
        assert loop._greet_paused_by_cap is False

    def test_同一天不反复扫记录(self):
        from datetime import date
        loop = self._loop()
        loop._greet_paused = True
        loop._greet_paused_by_cap = True
        loop._greet_cap_paused_on = date.today().isoformat()
        loop._maybe_auto_resume_greet()
        assert loop._greet_paused is True
        loop._greet_engine._greet_store.filter.assert_not_called()

    def test_人工暂停不自动恢复(self):
        loop = self._loop()
        loop._greet_paused = True
        loop._greet_paused_by_cap = False
        loop._greet_cap_paused_on = "2000-01-01"
        loop._maybe_auto_resume_greet()
        assert loop._greet_paused is True

    def test_恢复检查在暂停分支里真的会被调到(self):
        """暂停分支若不调用，跨零点恢复永远不会触发（曾真实错过）"""
        import pathlib
        import re
        src = pathlib.Path("boss_bot/main_loop.py").read_text(encoding="utf-8")
        m = re.search(r"if self\._greet_paused:\n(.*?)\n\s*continue", src, re.S)
        assert m and "_maybe_auto_resume_greet()" in m.group(1)


class GreetRoundDryRunTest:
    """真跑一遍打招呼轮次：演练模式必须一次都不点发送"""

    def _setup(self, dry):
        from boss_bot.main_loop import UnifiedBotLoop, UnifiedConfig
        cfg = UnifiedConfig()
        cfg.dry_run = dry
        with patch("boss_bot.main_loop.BrowserManager"):
            loop = UnifiedBotLoop(config=cfg)
        loop._running = True
        loop._stop_event = MagicMock()
        loop._hot_reload_config = lambda: None
        loop._build_greet_tasks = lambda: [{"query": "数据分析", "city": "长沙",
                                            "scroll_pages": 1,
                                            "message_interval_min": 0,
                                            "message_interval_max": 0}]
        eng = MagicMock()
        eng._max_per_day = 150
        eng._greet_store.filter.return_value = []
        # 关掉限流与 AI 分支，让轮次走最短路径到发送点
        eng._rate_limit_enabled = False
        eng._ai_enabled = False
        eng.applied_count = 0
        eng.skipped_count = 0
        eng._init_ai.return_value = None
        eng._last_ai_result = None
        eng._analyze_job_with_ai.return_value = (None, 0)
        eng.search_jobs.return_value = [{"job_name": "数据分析师", "company": "A",
                                         "url": "https://www.zhipin.com/job_detail/x.html"}]
        eng._is_already_chatted.return_value = False
        eng.send_greeting.return_value = True
        loop._greet_engine = eng
        loop._metrics = MagicMock()
        loop._greet_event_cb = None
        return loop, eng

    def test_演练模式不发送(self):
        loop, eng = self._setup(dry=True)
        loop._run_greet_round()
        eng.send_greeting.assert_not_called()
        assert loop._stats_dict["greet_applied"] == 0

    def test_正常模式会发送(self):
        loop, eng = self._setup(dry=False)
        loop._run_greet_round()
        assert eng.send_greeting.called
        assert loop._stats_dict["greet_applied"] == 1


class AiSkipUnhealthyTest:
    """容灾链按体检结果跳过已知不可用的接口（相对顺序不变）"""

    def _chain(self, tmp_path, updated_at, statuses, skip=True):
        from boss_bot.greet_engine import AIAnalyzerChain
        providers = [{"name": n, "api_key": "sk-" + n, "api_base": f"https://a{i}/v1",
                      "model": f"m{i}"} for i, n in enumerate(["A", "B", "C"])]
        health = {"updated_at": updated_at, "results": {}}
        for p, st in zip(providers, statuses):
            health["results"][f"{p['api_base']}|{p['model']}|{p['name']}"] = {
                "name": p["name"], "api_base": p["api_base"], "model": p["model"],
                "status": st, "latency_ms": 100, "reply": "连接成功", "reason": ""}
        f = tmp_path / "ai_health.json"
        f.write_text(json.dumps(health), encoding="utf-8")
        chain = AIAnalyzerChain(providers=providers, match_threshold=70,
                                cache_enabled=False, skip_unhealthy=skip)
        return chain, f

    def _usable(self, chain, tmp_path, f):
        with patch("boss_bot.ai_health.HEALTH_FILE", f):
            used = []

            def fake_call(provider, messages):
                used.append(provider.name)
                return {"score": 90, "is_match": True, "reason": "匹配",
                        "suggested_greeting": "hi"}
            chain._call_provider_api = fake_call
            chain._unhealthy_cache = None
            chain.analyze_job({"job_name": "数据分析", "salary": "8-12K",
                               "description": "x" * 200, "url": "u1",
                               "requirements": "r", "company": "c"})
            return used

    def test_跳过体检不可用的接口(self, tmp_path):
        chain, f = self._chain(tmp_path, "2026-09-28 00:00:00",
                               ["unavailable", "unavailable", "available"])
        with patch("boss_bot.greet_engine.time") as t:
            t.time.return_value = __import__("datetime").datetime.strptime(
                "2026-09-28 00:10:00", "%Y-%m-%d %H:%M:%S").timestamp()
            t.sleep = time.sleep
            used = self._usable(chain, tmp_path, f)
        assert used == ["C"], used

    def test_体检过旧则不跳过(self, tmp_path):
        chain, f = self._chain(tmp_path, "2020-01-01 00:00:00",
                               ["unavailable", "unavailable", "available"])
        used = self._usable(chain, tmp_path, f)
        assert used[0] == "A", used

    def test_全部不可用时不跳过(self, tmp_path):
        chain, f = self._chain(tmp_path, "2026-09-28 00:00:00",
                               ["unavailable", "unavailable", "unavailable"])
        with patch("boss_bot.greet_engine.time") as t:
            t.time.return_value = __import__("datetime").datetime.strptime(
                "2026-09-28 00:10:00", "%Y-%m-%d %H:%M:%S").timestamp()
            t.sleep = time.sleep
            used = self._usable(chain, tmp_path, f)
        assert used[0] == "A", used

    def test_开关关掉后按原顺序全试(self, tmp_path):
        chain, f = self._chain(tmp_path, "2026-09-28 00:00:00",
                               ["unavailable", "unavailable", "available"], skip=False)
        used = self._usable(chain, tmp_path, f)
        assert used[0] == "A", used

    def test_配置项能读写往返(self, tmp_path):
        from boss_bot.unified_config import UnifiedConfig
        target = tmp_path / "bot_config.json"
        target.write_text(json.dumps({"ai": {"skip_unhealthy": False}}), encoding="utf-8")
        cfg = UnifiedConfig.load(config_path=str(target),
                                 overrides_path=str(tmp_path / "none.json"))
        assert cfg.ai.skip_unhealthy is False
        assert cfg.to_dict()["ai"]["skip_unhealthy"] is False


class SelfEvolveWiringTest:
    """自进化引擎必须真的被构造并交给回复引擎（以前永远是 None）"""

    def test_初始化时接线并按账号分文件(self, tmp_path, monkeypatch):
        import boss_bot.main_loop as ML
        import boss_bot.config as C
        monkeypatch.setattr(C, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr(C, "STATS_FILE", tmp_path / "stats.json")
        monkeypatch.setattr(C, "NOTIFY_FILE", tmp_path / "notify.json")
        monkeypatch.setattr(ML, "BASE_DIR", tmp_path)
        from boss_bot.unified_config import UnifiedConfig
        cfg = UnifiedConfig()
        with patch.object(ML, "BossChatHandler"), patch.object(ML, "BrowserManager"):
            loop = ML.UnifiedBotLoop(config=cfg, account_index=1)
            loop._init_engines()
        assert loop._self_evolve is not None
        assert loop._reply_engine._self_evolve is loop._self_evolve
        assert loop._self_evolve.enabled is True
        assert "_account_1" in str(loop._self_evolve._data_file)
        # 质量数据（模板效果/规则调整）也要按账号分开
        assert "_account_1" in str(loop._self_evolve._quality_data_file)

    def test_关闭开关后引擎不记录(self, tmp_path, monkeypatch):
        import boss_bot.main_loop as ML
        import boss_bot.config as C
        monkeypatch.setattr(C, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr(C, "STATS_FILE", tmp_path / "stats.json")
        monkeypatch.setattr(ML, "BASE_DIR", tmp_path)
        from boss_bot.unified_config import UnifiedConfig
        cfg = UnifiedConfig()
        cfg.self_evolve_enabled = False
        with patch.object(ML, "BossChatHandler"), patch.object(ML, "BrowserManager"):
            loop = ML.UnifiedBotLoop(config=cfg, account_index=0)
            loop._init_engines()
        assert loop._self_evolve.enabled is False


class AiResponseParseDiagnosticTest:
    """解析失败时必须说清是哪一种失败，否则只看到"默认通过"查不出原因"""

    def _call(self, payload):
        from boss_bot.greet_engine import AIAnalyzerChain, AIProviderConfig
        chain = AIAnalyzerChain(providers=[{"name": "T", "api_key": "sk-t",
                                            "api_base": "https://a/v1", "model": "m"}],
                                cache_enabled=False)
        provider = AIProviderConfig(name="T", api_key="sk-t",
                                    api_base="https://a/v1", model="m")

        class _Resp:
            def read(self):
                return json.dumps(payload).encode("utf-8")
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        with patch("boss_bot.greet_engine.urlopen", return_value=_Resp()):
            return chain._call_provider_api(provider, [])

    def test_正常JSON(self):
        out = self._call({"choices": [{"message": {"content": '```json\n{"score":80,"is_match":true,"reason":"对口"}\n```'}}]})
        assert out["score"] == 80

    def test_空正文说成未返回正文(self):
        with pytest.raises(Exception) as ei:
            self._call({"choices": [{"message": {"content": ""}}]})
        assert "未返回正文" in str(ei.value)

    def test_有正文没JSON说成没JSON并带前文(self):
        with pytest.raises(Exception) as ei:
            self._call({"choices": [{"message": {"content": "这个岗位不太合适，原因很多。"}}]})
        assert "没有 JSON" in str(ei.value)

    def test_缺字段说成缺字段(self):
        with pytest.raises(Exception) as ei:
            self._call({"code": 429, "msg": "rate limited"})
        assert "响应缺少字段 choices" in str(ei.value)

    def test_推理内容兜底(self):
        out = self._call({"choices": [{"message": {"content": "",
                                     "reasoning_content": '{"score":40,"is_match":false,"reason":"不对口"}'}}]})
        assert out["score"] == 40
