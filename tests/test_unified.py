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
        assert cfg.ai.fail_action == "skip"
        assert cfg.ai.max_tokens == 200
        assert cfg.test_mode is False
        assert cfg.test_page == ""

    def test_load_with_no_files(self, tmp_path):
        """测试没有配置文件时的加载"""
        from boss_bot.unified_config import UnifiedConfig
        cfg = UnifiedConfig.load(
            config_path=str(tmp_path / "nonexistent.json"),
            profile_path=str(tmp_path / "nonexistent_profile.json")
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
            profile_path=str(tmp_path / "nonexistent.json")
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
            profile_path=str(profile_file)
        )
        assert cfg.user_profile.name == "张三"
        assert cfg.user_profile.position == "Python开发"
        assert cfg.user_profile.skills == ["Python", "SQL"]
        assert cfg.user_profile.salary_expectation == "15-20K"

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
        engine = ReplyEngine()
        action, content, meta = engine.get_reply("今天天气不错啊")
        assert action == "none"
        assert content is None
        assert meta["source"] == "default"

    def test_default_reply_when_configured(self):
        """测试配置为 default 时使用兜底话术"""
        from boss_bot.reply_engine import ReplyEngine
        engine = ReplyEngine()
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


class RetryDecoratorTest:
    """retry 装饰器的单元测试"""

    def test_retry_success_first_try(self):
        """测试第一次就成功"""
        from boss_bot.greet_engine import retry
        call_count = 0

        class Dummy:
            @retry(max_attempts=3, base_delay=0.01)
            def succeed(self):
                nonlocal call_count
                call_count += 1
                return "ok"

        d = Dummy()
        result = d.succeed()
        assert result == "ok"
        assert call_count == 1

    def test_retry_success_after_failure(self):
        """测试失败后重试成功"""
        from boss_bot.greet_engine import retry
        call_count = 0

        class Dummy:
            def _log(self, level, msg):
                pass

            @retry(max_attempts=3, base_delay=0.01, backoff_factor=1.0)
            def fail_then_succeed(self):
                nonlocal call_count
                call_count += 1
                if call_count < 2:
                    raise ValueError("临时错误")
                return "ok"

        d = Dummy()
        result = d.fail_then_succeed()
        assert result == "ok"
        assert call_count == 2

    def test_retry_all_fail(self):
        """测试全部失败后抛出异常"""
        from boss_bot.greet_engine import retry
        call_count = 0

        class Dummy:
            def _log(self, level, msg):
                pass

            @retry(max_attempts=2, base_delay=0.01, backoff_factor=1.0)
            def always_fail(self):
                nonlocal call_count
                call_count += 1
                raise ValueError("永久错误")

        d = Dummy()
        with pytest.raises(ValueError, match="永久错误"):
            d.always_fail()
        assert call_count == 2


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
