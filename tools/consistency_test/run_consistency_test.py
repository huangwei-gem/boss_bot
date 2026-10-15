"""
三端一致性测试 + 常见软件测试

测试三端（前端显示 vs 后端API vs 配置文件）的一致性，并执行常见软件测试：
1. 三端一致性测试（AI配置、打招呼记录、回复记录、用户画像、统计卡片）
2. API端点测试
3. 边界case测试
4. 数据完整性测试

约束：
- 不修改任何代码，只做测试和报告
- 用简体中文输出
- 发现不一致问题，详细记录字段名、前端值、后端值、配置文件值

使用方式：
    python run_consistency_test.py
"""

import json
import os
import sys
import time
import traceback
from pathlib import Path
from datetime import datetime

import requests

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 后端API基础URL
BASE_URL = "http://127.0.0.1:5000"

# 配置文件路径
BOT_CONFIG_FILE = PROJECT_ROOT / "bot_config.json"
USER_PROFILE_FILE = PROJECT_ROOT / "user_profile.json"
GREET_RECORDS_FILE = PROJECT_ROOT / "data" / "greet_records.json"
REPLY_RECORDS_FILE = PROJECT_ROOT / "data" / "reply_records.json"

# 测试报告输出目录
REPORT_DIR = Path(__file__).resolve().parent
REPORT_FILE = REPORT_DIR / "test_report.md"
REPORT_JSON_FILE = REPORT_DIR / "test_report.json"


# ===================== 测试结果收集器 =====================

class TestResult:
    """单个测试结果。"""

    def __init__(self, name, category):
        self.name = name
        self.category = category
        self.passed = False
        self.details = {}
        self.issues = []
        self.error = None

    def pass_test(self, details=None):
        self.passed = True
        if details:
            self.details = details

    def fail_test(self, issues, details=None):
        self.passed = False
        self.issues = issues
        if details:
            self.details = details

    def error_test(self, error_msg):
        self.passed = False
        self.error = error_msg

    def to_dict(self):
        return {
            "name": self.name,
            "category": self.category,
            "passed": self.passed,
            "details": self.details,
            "issues": self.issues,
            "error": self.error,
        }


class ReportCollector:
    """测试报告收集器。"""

    def __init__(self):
        self.results = []
        self.start_time = datetime.now()

    def add(self, result):
        self.results.append(result)

    def summary(self):
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        failed = total - passed
        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "pass_rate": f"{(passed / total * 100):.2f}%" if total > 0 else "0%",
        }

    def save_json(self):
        """保存JSON格式的测试报告。"""
        report = {
            "test_time": self.start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "duration_seconds": (datetime.now() - self.start_time).total_seconds(),
            "summary": self.summary(),
            "results": [r.to_dict() for r in self.results],
        }
        with open(REPORT_JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        return report


# ===================== 工具函数 =====================

def load_json_file(path):
    """加载JSON文件。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"  [警告] 加载文件失败 {path}: {e}")
        return None


def api_get(path, timeout=10):
    """GET请求API。"""
    url = f"{BASE_URL}{path}"
    try:
        resp = requests.get(url, timeout=timeout)
        return resp
    except Exception as e:
        print(f"  [警告] API请求失败 {url}: {e}")
        return None


def api_post(path, json_data=None, timeout=15, send_json=True):
    """POST请求API。

    Args:
        path: API路径
        json_data: JSON数据，如果为None且send_json=True则发送空JSON
        timeout: 超时秒数
        send_json: 是否发送JSON body，False时发送空body（用于测试空数据场景）
    """
    url = f"{BASE_URL}{path}"
    try:
        if send_json:
            resp = requests.post(url, json=json_data, timeout=timeout)
        else:
            # 不发送body，模拟空数据请求
            resp = requests.post(url, timeout=timeout)
        return resp
    except requests.exceptions.Timeout as e:
        print(f"  [警告] API请求超时 {url}: {e}")
        return None
    except requests.exceptions.ConnectionError as e:
        print(f"  [警告] API连接错误 {url}: {e}")
        return None
    except Exception as e:
        print(f"  [警告] API请求失败 {url}: {type(e).__name__}: {e}")
        return None


def get_frontend_values(playwright_page, eval_scripts):
    """通过Playwright evaluate读取前端值。

    Args:
        playwright_page: Playwright页面对象
        eval_scripts: dict, {字段名: JS表达式}

    Returns:
        dict, {字段名: 值或错误信息}
    """
    results = {}
    for field, script in eval_scripts.items():
        try:
            value = playwright_page.evaluate(script)
            results[field] = value
        except Exception as e:
            results[field] = f"<读取失败: {e}>"
    return results


def compare_three_ways(field_name, config_val, api_val, frontend_val):
    """比较三端值是否一致。

    Returns:
        (is_consistent, issue_dict)
    """
    # 处理 providers 数量比较
    if field_name.endswith("_count") and isinstance(config_val, int):
        config_v = config_val
        api_v = api_val if isinstance(api_val, int) else (len(api_val) if isinstance(api_val, list) else api_val)
        fe_v = frontend_val if isinstance(frontend_val, int) else (len(frontend_val) if isinstance(frontend_val, list) else frontend_val)
    else:
        config_v = config_val
        api_v = api_val
        fe_v = frontend_val

    is_consistent = (config_v == api_v == fe_v)
    if not is_consistent:
        issue = {
            "field": field_name,
            "config_file_value": config_v,
            "api_value": api_v,
            "frontend_value": fe_v,
        }
        return False, issue
    return True, None


def compare_two_ways(field_name, api_val, frontend_val):
    """比较两端值是否一致。"""
    is_consistent = (api_val == frontend_val)
    if not is_consistent:
        issue = {
            "field": field_name,
            "api_value": api_val,
            "frontend_value": frontend_val,
        }
        return False, issue
    return True, None


# ===================== Playwright 浏览器管理 =====================

class FrontendReader:
    """使用Playwright读取前端显示值。"""

    def __init__(self):
        self.pw = None
        self.browser = None
        self.page = None
        self.available = False

    def start(self):
        try:
            from playwright.sync_api import sync_playwright
            self.pw = sync_playwright().start()
            # 自带 headless shell 不一定在这台机器上（实测 ms-playwright 缓存是空的），
            # 那样四条前端比对会全报"前端不可用"，红的是工具不是页面。逐个退到已装的浏览器。
            for 方式 in ({}, {"channel": "chrome"}, {"channel": "msedge"}):
                try:
                    self.browser = self.pw.chromium.launch(headless=True, **方式)
                    print("  [前端] Playwright 启动成功%s"
                          % ("（用系统已装的 %s）" % 方式["channel"] if 方式 else ""))
                    break
                except Exception as e:
                    print("  [警告] 换浏览器形态重试：%s" % str(e)[:90])
                    self.browser = None
            if self.browser is None:
                raise RuntimeError("headless shell / chrome / msedge 都起不来")
            context = self.browser.new_context()
            self.page = context.new_page()
            # 使用 domcontentloaded 而不是 networkidle，避免长连接导致超时
            self.page.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=60000)
            # 等待页面加载完配置（config对象填充）
            self.page.wait_for_timeout(5000)
            # 额外等待config对象可用
            try:
                self.page.wait_for_function(
                    "() => typeof config !== 'undefined' && config !== null && config.ai",
                    timeout=15000,
                )
            except Exception:
                print("  [警告] 等待config对象超时，继续测试")
            # 统计卡片是 /api/metrics 回来后才发现实值的。不在这儿等一下，
            # 1e 会拿"卡片=0"去比接口，红了也是假红。
            try:
                self.page.wait_for_function(
                    "() => typeof metricsCache !== 'undefined' && metricsCache",
                    timeout=15000,
                )
            except Exception:
                print("  [警告] 等待 /api/metrics 落进卡片超时，统计卡片可能还是 0")
            self.available = True
            print("  [前端] Playwright浏览器启动成功")
            return True
        except Exception as e:
            print(f"  [警告] Playwright启动失败: {e}")
            self.available = False
            return False

    def evaluate(self, script):
        if not self.available:
            return "<前端不可用>"
        try:
            return self.page.evaluate(script)
        except Exception as e:
            return f"<读取失败: {e}>"

    def close(self):
        try:
            if self.browser:
                self.browser.close()
            if self.pw:
                self.pw.stop()
        except Exception:
            pass


# ===================== 1. 三端一致性测试 =====================

def test_ai_config_consistency(report, frontend):
    """1a. AI配置一致性测试。"""
    print("\n[1a] AI配置一致性测试...")
    result = TestResult("AI配置一致性", "三端一致性")

    # 从配置文件读取
    config_data = load_json_file(BOT_CONFIG_FILE)
    if not config_data:
        result.error_test("无法加载 bot_config.json")
        report.add(result)
        return

    config_ai = config_data.get("ai", {})
    config_values = {
        "ai.enabled": config_ai.get("enabled"),
        "ai.match_threshold": config_ai.get("match_threshold"),
        "ai.providers_count": len(config_ai.get("providers", [])),
        "ai.custom_filter_keywords": config_ai.get("custom_filter_keywords", []),
        "ai.custom_scoring_prompt": config_ai.get("custom_scoring_prompt", ""),
    }

    # 从API读取
    resp = api_get("/api/config")
    if resp is None or resp.status_code != 200:
        result.error_test(f"API /api/config 请求失败: {resp.status_code if resp is not None else '无响应'}")
        report.add(result)
        return

    api_data = resp.json().get("config", {}).get("ai", {})
    api_values = {
        "ai.enabled": api_data.get("enabled"),
        "ai.match_threshold": api_data.get("match_threshold"),
        "ai.providers_count": len(api_data.get("providers", [])),
        "ai.custom_filter_keywords": api_data.get("custom_filter_keywords", []),
        "ai.custom_scoring_prompt": api_data.get("custom_scoring_prompt", ""),
    }

    # 从前端读取
    frontend_values = {
        "ai.enabled": frontend.evaluate(
            "() => { const el = document.getElementById('aiEnabled'); "
            "if (!el) return '<元素不存在>'; "
            "return el.classList.contains('on') ? true : false; }"
        ),
        "ai.match_threshold": frontend.evaluate(
            "() => { const el = document.getElementById('aiThreshold'); "
            "return el ? parseInt(el.value) : '<元素不存在>'; }"
        ),
        "ai.providers_count": frontend.evaluate(
            "() => { const el = document.getElementById('aiProviders'); "
            "if (!el) return 0; "
            "return el.children.length; }"
        ),
        # 分隔符要照抄前端自己保存时用的那一条（index.html 的 onCustomAiChange：
        # split(/[，,、;；\n]+/)）。这里原来只认逗号，而回填进输入框的是顿号，
        # 于是 41 个词被当成 1 个整串去比配置文件——红了也不是三端不一致。
        "ai.custom_filter_keywords": frontend.evaluate(
            "() => { const el = document.getElementById('customFilterKeywords'); "
            "if (!el) return '<元素不存在>'; "
            "const v = el.value.trim(); "
            "return v ? v.split(/[，,、;；\\n]+/).map(s => s.trim())"
            ".filter(Boolean) : []; }"
        ),
        "ai.custom_scoring_prompt": frontend.evaluate(
            "() => { const el = document.getElementById('customScoringPrompt'); "
            "return el ? el.value : '<元素不存在>'; }"
        ),
    }

    # 比较三端
    issues = []
    details = {
        "config_file": config_values,
        "api": api_values,
        "frontend": frontend_values,
    }

    for field in config_values:
        consistent, issue = compare_three_ways(
            field, config_values[field], api_values[field], frontend_values[field]
        )
        if not consistent:
            issues.append(issue)

    if issues:
        result.fail_test(issues, details)
    else:
        result.pass_test(details)

    report.add(result)
    print(f"  结果: {'通过' if result.passed else '失败'}, 问题数: {len(issues)}")


def test_greet_records_consistency(report, frontend):
    """1b. 打招呼记录一致性测试。"""
    print("\n[1b] 打招呼记录一致性测试...")
    result = TestResult("打招呼记录一致性", "三端一致性")

    # 从配置文件读取
    file_data = load_json_file(GREET_RECORDS_FILE)
    if file_data is None:
        result.error_test("无法加载 greet_records.json")
        report.add(result)
        return

    file_records = file_data.get("records", [])
    # 按timestamp降序取最新5条
    file_sorted = sorted(
        file_records,
        key=lambda x: x.get("timestamp", "") or "",
        reverse=True,
    )
    file_latest5 = file_sorted[:5]
    config_values = {
        "total_count": len(file_records),
        "latest_5_count": len(file_latest5),
        "latest_5_timestamps": [r.get("timestamp", "") for r in file_latest5],
    }

    # 从API读取
    resp = api_get("/api/greet_records")
    if resp is None or resp.status_code != 200:
        result.error_test(f"API /api/greet_records 请求失败: {resp.status_code if resp is not None else '无响应'}")
        report.add(result)
        return

    api_resp_data = resp.json()
    api_records = api_resp_data.get("records", [])
    api_latest5 = api_records[:5]
    api_values = {
        "total_count": api_resp_data.get("total", 0),
        "latest_5_count": len(api_latest5),
        "latest_5_timestamps": [r.get("timestamp", "") for r in api_latest5],
    }

    # 从前端读取（表格行数 + 最新5条内容）
    frontend_values = {
        "table_row_count": frontend.evaluate(
            "() => { const body = document.getElementById('greetTableBody'); "
            "return body ? body.querySelectorAll('tr').length : 0; }"
        ),
        "tab_count": frontend.evaluate(
            "() => { const el = document.getElementById('greetTabCount'); "
            "return el ? parseInt(el.textContent) || 0 : 0; }"
        ),
        "first_5_timestamps": frontend.evaluate(
            "() => { const body = document.getElementById('greetTableBody'); "
            "if (!body) return []; "
            "const rows = body.querySelectorAll('tr'); "
            "const ts = []; "
            "for (let i = 0; i < Math.min(5, rows.length); i++) { "
            "  const td = rows[i].querySelector('td:first-child'); "
            "  ts.push(td ? (td.getAttribute('title') || td.textContent.trim()) : ''); "
            "} "
            "return ts; }"
        ),
    }

    # 比较
    issues = []
    details = {
        "config_file": config_values,
        "api": api_values,
        "frontend": frontend_values,
    }

    # 比较1: 文件记录数 vs API total
    if config_values["total_count"] != api_values["total_count"]:
        issues.append({
            "field": "total_count (文件 vs API)",
            "config_file_value": config_values["total_count"],
            "api_value": api_values["total_count"],
            "frontend_value": frontend_values["table_row_count"],
        })
    # 比较2: API total vs 前端表格行数
    if api_values["total_count"] != frontend_values["table_row_count"]:
        issues.append({
            "field": "total_count (API vs 前端表格行数)",
            "config_file_value": config_values["total_count"],
            "api_value": api_values["total_count"],
            "frontend_value": frontend_values["table_row_count"],
        })
    # 比较3: 最新5条timestamp（文件 vs API）
    if config_values["latest_5_timestamps"] != api_values["latest_5_timestamps"]:
        issues.append({
            "field": "latest_5_timestamps (文件 vs API)",
            "config_file_value": config_values["latest_5_timestamps"],
            "api_value": api_values["latest_5_timestamps"],
            "frontend_value": frontend_values["first_5_timestamps"],
        })
    # 比较4: API最新5条 vs 前端前5条
    if api_values["latest_5_timestamps"] != frontend_values["first_5_timestamps"]:
        issues.append({
            "field": "latest_5_timestamps (API vs 前端)",
            "config_file_value": config_values["latest_5_timestamps"],
            "api_value": api_values["latest_5_timestamps"],
            "frontend_value": frontend_values["first_5_timestamps"],
        })

    if issues:
        result.fail_test(issues, details)
    else:
        result.pass_test(details)

    report.add(result)
    print(f"  结果: {'通过' if result.passed else '失败'}, 问题数: {len(issues)}")


def test_reply_records_consistency(report, frontend):
    """1c. 回复记录一致性测试。"""
    print("\n[1c] 回复记录一致性测试...")
    result = TestResult("回复记录一致性", "三端一致性")

    # 从配置文件读取
    file_data = load_json_file(REPLY_RECORDS_FILE)
    if file_data is None:
        result.error_test("无法加载 reply_records.json")
        report.add(result)
        return

    file_records = file_data.get("records", [])
    config_values = {
        "total_count": len(file_records),
    }

    # 从API读取
    resp = api_get("/api/reply_records")
    if resp is None or resp.status_code != 200:
        result.error_test(f"API /api/reply_records 请求失败: {resp.status_code if resp is not None else '无响应'}")
        report.add(result)
        return

    api_resp_data = resp.json()
    api_records = api_resp_data.get("records", [])
    api_values = {
        "total_count": api_resp_data.get("total", 0),
        "records_count": len(api_records),
    }

    # 从前端读取（回复记录显示为聊天分组，使用 replyTabCount）
    frontend_values = {
        "tab_count": frontend.evaluate(
            "() => { const el = document.getElementById('replyTabCount'); "
            "return el ? parseInt(el.textContent) || 0 : 0; }"
        ),
        "chat_list_count": frontend.evaluate(
            "() => { return document.querySelectorAll('.chat-card, .reply-chat-item, [data-chat-name]').length; }"
        ),
    }

    # 比较
    issues = []
    details = {
        "config_file": config_values,
        "api": api_values,
        "frontend": frontend_values,
    }

    # 比较1: 文件记录数 vs API total
    if config_values["total_count"] != api_values["total_count"]:
        issues.append({
            "field": "total_count (文件 vs API)",
            "config_file_value": config_values["total_count"],
            "api_value": api_values["total_count"],
            "frontend_value": frontend_values["tab_count"],
        })
    # 比较2: API total vs API records实际数量
    if api_values["total_count"] != api_values["records_count"]:
        issues.append({
            "field": "API total vs records count",
            "api_total": api_values["total_count"],
            "api_records_count": api_values["records_count"],
        })

    if issues:
        result.fail_test(issues, details)
    else:
        result.pass_test(details)

    report.add(result)
    print(f"  结果: {'通过' if result.passed else '失败'}, 问题数: {len(issues)}")


def test_user_profile_consistency(report, frontend):
    """1d. 用户画像一致性测试。"""
    print("\n[1d] 用户画像一致性测试...")
    result = TestResult("用户画像一致性", "三端一致性")

    # 从配置文件读取
    config_data = load_json_file(USER_PROFILE_FILE)
    if not config_data:
        result.error_test("无法加载 user_profile.json")
        report.add(result)
        return

    # 同时从 bot_config.json 读取 user_profile 字段
    bot_config = load_json_file(BOT_CONFIG_FILE) or {}
    bot_config_profile = bot_config.get("user_profile", {})

    config_values = {
        "name": config_data.get("name"),
        "position": config_data.get("position"),
        "education": config_data.get("education"),
        "experience": config_data.get("experience"),
        "salary_expectation": config_data.get("salary_expectation"),
        "available_interview_time": config_data.get("available_interview_time"),
        "contact": config_data.get("contact"),
        "skills": config_data.get("skills", []),
        "highlights": config_data.get("highlights", []),
    }
    bot_config_values = {
        "name": bot_config_profile.get("name"),
        "position": bot_config_profile.get("position"),
        "education": bot_config_profile.get("education"),
        "experience": bot_config_profile.get("experience"),
        "salary_expectation": bot_config_profile.get("salary_expectation"),
        "available_interview_time": bot_config_profile.get("available_interview_time"),
        "contact": bot_config_profile.get("contact"),
        "skills": bot_config_profile.get("skills", []),
        "highlights": bot_config_profile.get("highlights", []),
    }

    # 从API读取
    resp = api_get("/api/user_profile")
    if resp is None or resp.status_code != 200:
        result.error_test(f"API /api/user_profile 请求失败: {resp.status_code if resp is not None else '无响应'}")
        report.add(result)
        return

    api_profile = resp.json().get("profile", {})
    api_values = {
        "name": api_profile.get("name"),
        "position": api_profile.get("position"),
        "education": api_profile.get("education"),
        "experience": api_profile.get("experience"),
        "salary_expectation": api_profile.get("salary_expectation"),
        "available_interview_time": api_profile.get("available_interview_time"),
        "contact": api_profile.get("contact"),
        "skills": api_profile.get("skills", []),
        "highlights": api_profile.get("highlights", []),
    }

    # 从前端读取（通过JS从内存config对象读取，config是let声明的全局变量）
    frontend_values = {
        "name": frontend.evaluate("() => { try { return config?.user_profile?.name ?? null; } catch(e) { return '<读取失败: ' + e.message + '>'; } }"),
        "position": frontend.evaluate("() => { try { return config?.user_profile?.position ?? null; } catch(e) { return '<读取失败: ' + e.message + '>'; } }"),
        "education": frontend.evaluate("() => { try { return config?.user_profile?.education ?? null; } catch(e) { return '<读取失败: ' + e.message + '>'; } }"),
        "experience": frontend.evaluate("() => { try { return config?.user_profile?.experience ?? null; } catch(e) { return '<读取失败: ' + e.message + '>'; } }"),
        "salary_expectation": frontend.evaluate("() => { try { return config?.user_profile?.salary_expectation ?? null; } catch(e) { return '<读取失败: ' + e.message + '>'; } }"),
        "available_interview_time": frontend.evaluate("() => { try { return config?.user_profile?.available_interview_time ?? null; } catch(e) { return '<读取失败: ' + e.message + '>'; } }"),
        "contact": frontend.evaluate("() => { try { return config?.user_profile?.contact ?? null; } catch(e) { return '<读取失败: ' + e.message + '>'; } }"),
        "skills": frontend.evaluate("() => { try { return config?.user_profile?.skills || []; } catch(e) { return '<读取失败: ' + e.message + '>'; } }"),
        "highlights": frontend.evaluate("() => { try { return config?.user_profile?.highlights || []; } catch(e) { return '<读取失败: ' + e.message + '>'; } }"),
    }

    # 比较
    issues = []
    details = {
        "user_profile.json": config_values,
        "bot_config.json.user_profile": bot_config_values,
        "api": api_values,
        "frontend": frontend_values,
    }

    for field in config_values:
        # 比较 user_profile.json vs API
        if config_values[field] != api_values[field]:
            issues.append({
                "field": f"{field} (user_profile.json vs API)",
                "config_file_value": config_values[field],
                "api_value": api_values[field],
                "frontend_value": frontend_values[field],
            })
        # 比较 API vs 前端
        if api_values[field] != frontend_values[field]:
            issues.append({
                "field": f"{field} (API vs 前端)",
                "config_file_value": config_values[field],
                "api_value": api_values[field],
                "frontend_value": frontend_values[field],
            })
        # 比较 user_profile.json vs bot_config.json
        if config_values[field] != bot_config_values[field]:
            issues.append({
                "field": f"{field} (user_profile.json vs bot_config.json)",
                "user_profile.json": config_values[field],
                "bot_config.json.user_profile": bot_config_values[field],
            })

    if issues:
        result.fail_test(issues, details)
    else:
        result.pass_test(details)

    report.add(result)
    print(f"  结果: {'通过' if result.passed else '失败'}, 问题数: {len(issues)}")


def test_stats_card_consistency(report, frontend):
    """1e. 统计卡片一致性测试。

    比的是 /api/metrics，不是 /api/status：卡片上那四个数是 renderMetrics 从
    /api/metrics 的 total/daily 填进去的，而 /api/status 的 stats 只有机器人
    跑起来才有值——号停着的时候那边空、这边 1729，怎么比都是红，红了也不说明三端不一致。
    """
    print("\n[1e] 统计卡片一致性测试...")
    result = TestResult("统计卡片一致性", "三端一致性")

    resp = api_get("/api/metrics")
    if resp is None or resp.status_code != 200:
        result.error_test(f"API /api/metrics 请求失败: {resp.status_code if resp is not None else '无响应'}")
        report.add(result)
        return

    metrics = resp.json()
    total = metrics.get("total") or {}
    daily = metrics.get("daily") or {}
    scope = str(metrics.get("scope", "all"))
    if scope != "all":
        # 页面默认范围是"全部账号"，不带 account 参数的这一份才对得上；
        # 界面切到某个号时这份数据不是卡片看到的那份
        result.error_test(f"页面数据范围不是 all（当前 {scope}），这一项要带 ?account= 再比")
        report.add(result)
        return

    # 卡片自改成 累计投递/今日已投递/接收简历/面试数 之后，
    # skipped、reply_sent、reply_skipped 三个 id 已经不在页面上，读回来永远是"元素不存在"
    fields = {
        "累计投递": ("statTotal", total.get("greet_sent", 0)),
        "今日已投递": ("statApplied", daily.get("greet_sent", 0)),
        "接收简历": ("statResume", daily.get("resume_sent", 0)),
        "面试数": ("statInterview", daily.get("interview", 0)),
    }

    def read_card(el_id):
        return frontend.evaluate(
            "() => { const el = document.getElementById('%s'); "
            "return el ? (parseInt(el.textContent) || 0) : '<元素不存在>'; }" % el_id
        )

    api_values = {name: value for name, (el_id, value) in fields.items()}
    frontend_values = {name: read_card(el_id) for name, (el_id, _v) in fields.items()}

    # 比较
    issues = []
    details = {
        "api": api_values,
        "frontend": frontend_values,
        "来源": "api=/api/metrics（页面数据范围 all），前端=四张统计卡片",
    }

    for field in api_values:
        consistent, issue = compare_two_ways(field, api_values[field], frontend_values[field])
        if not consistent:
            issues.append(issue)

    if issues:
        result.fail_test(issues, details)
    else:
        result.pass_test(details)

    report.add(result)
    print(f"  结果: {'通过' if result.passed else '失败'}, 问题数: {len(issues)}")


# ===================== 2. API端点测试 =====================

def test_api_endpoints(report):
    """2a. API端点测试。"""
    print("\n[2a] API端点测试...")
    endpoints = [
        ("GET", "/api/status", "返回200，包含running/statistics字段", lambda r: (
            r.status_code == 200 and "running" in r.json() and "stats" in r.json()
        )),
        ("GET", "/api/config", "返回200，config包含ai/greet/reply字段", lambda r: (
            r.status_code == 200 and "config" in r.json() and
            "ai" in r.json()["config"] and "greet" in r.json()["config"] and "reply" in r.json()["config"]
        )),
        ("GET", "/api/greet_records", "返回200，records是数组", lambda r: (
            r.status_code == 200 and "records" in r.json() and isinstance(r.json()["records"], list)
        )),
        ("GET", "/api/reply_records", "返回200，records是数组", lambda r: (
            r.status_code == 200 and "records" in r.json() and isinstance(r.json()["records"], list)
        )),
        ("GET", "/api/logs", "返回200，logs是数组", lambda r: (
            r.status_code == 200 and "logs" in r.json() and isinstance(r.json()["logs"], list)
        )),
        ("GET", "/api/user_profile", "返回200，包含name/position字段", lambda r: (
            r.status_code == 200 and "profile" in r.json() and
            "name" in r.json()["profile"] and "position" in r.json()["profile"]
        )),
        ("POST", "/api/accounts/0/check_cookie_simple", "返回200，包含valid字段", lambda r: (
            r.status_code == 200 and "valid" in r.json()
        )),
    ]

    for method, path, desc, validator in endpoints:
        result = TestResult(f"API {method} {path}", "API端点测试")
        print(f"  测试 {method} {path} - {desc}")

        if method == "GET":
            resp = api_get(path)
        else:
            resp = api_post(path)

        # 判空一律用 is None：requests 2.33 里 bool(Response) 就是 Response.ok，
        # 用 "if not resp" 会把 404/400/500 这些"真的回了话"读成"无响应"
        if resp is None:
            result.error_test(f"{method} {path} 请求失败（无响应）")
            report.add(result)
            print(f"    结果: 错误 - 无响应")
            continue

        try:
            passed = validator(resp)
            details = {
                "status_code": resp.status_code,
                "response_preview": resp.text[:500],
            }
            if passed:
                result.pass_test(details)
                print(f"    结果: 通过 (HTTP {resp.status_code})")
            else:
                result.fail_test([{"endpoint": f"{method} {path}", "desc": desc, "status_code": resp.status_code}], details)
                print(f"    结果: 失败 (HTTP {resp.status_code}) - {desc}")
        except Exception as e:
            result.error_test(f"验证异常: {e}")
            print(f"    结果: 错误 - {e}")

        report.add(result)


# ===================== 3. 边界case测试 =====================

def test_edge_cases(report):
    """2b. 边界case测试。"""
    print("\n[2b] 边界case测试...")

    # Case 1: 请求不存在的账号
    result = TestResult("请求不存在的账号 GET /api/accounts/99/check_cookie_simple", "边界case测试")
    print(f"  测试: 请求不存在的账号")
    resp = api_post("/api/accounts/99/check_cookie_simple")
    if resp is None:
        # 判空必须用 is None：requests 2.33 里 bool(Response) 就是 Response.ok，
        # 用 "if not resp" 会把 404/400/500 这些"真的回了话"读成"无响应"
        result.error_test("无响应")
    else:
        details = {"status_code": resp.status_code, "response": resp.text[:500]}
        # 应返回错误（404或status=error）
        is_error = resp.status_code == 404 or (resp.json().get("status") == "error" if resp.headers.get("content-type", "").startswith("application/json") else False)
        if is_error:
            result.pass_test(details)
            print(f"    结果: 通过 (HTTP {resp.status_code})")
        else:
            result.fail_test([{"case": "请求不存在的账号应返回错误", "actual_status": resp.status_code}], details)
            print(f"    结果: 失败 (HTTP {resp.status_code}) - 期望错误状态")
    report.add(result)

    # Case 2: 请求不存在的API
    result = TestResult("请求不存在的API GET /api/nonexistent", "边界case测试")
    print(f"  测试: 请求不存在的API")
    resp = api_get("/api/nonexistent")
    if resp is None:
        # 判空必须用 is None：requests 2.33 里 bool(Response) 就是 Response.ok，
        # 用 "if not resp" 会把 404/400/500 这些"真的回了话"读成"无响应"
        result.error_test("无响应")
    else:
        details = {"status_code": resp.status_code, "response": resp.text[:500]}
        # 应返回404
        if resp.status_code == 404:
            result.pass_test(details)
            print(f"    结果: 通过 (HTTP {resp.status_code})")
        else:
            result.fail_test([{"case": "请求不存在的API应返回404", "actual_status": resp.status_code}], details)
            print(f"    结果: 失败 (HTTP {resp.status_code}) - 期望404")
    report.add(result)

    # Case 3: POST空数据到 /api/config
    result = TestResult("POST空数据到 /api/config", "边界case测试")
    print(f"  测试: POST空数据到 /api/config")
    # 使用send_json=False发送空body，模拟空数据请求
    resp = api_post("/api/config", send_json=False)
    if resp is None:
        # 判空必须用 is None：requests 2.33 里 bool(Response) 就是 Response.ok，
        # 用 "if not resp" 会把 404/400/500 这些"真的回了话"读成"无响应"
        result.error_test("无响应")
    else:
        details = {"status_code": resp.status_code, "response": resp.text[:500]}
        # 应不崩溃（返回400或200，但不能500）
        if resp.status_code < 500:
            result.pass_test(details)
            print(f"    结果: 通过 (HTTP {resp.status_code}) - 未崩溃")
        else:
            result.fail_test([{"case": "POST空数据不应导致500错误", "actual_status": resp.status_code}], details)
            print(f"    结果: 失败 (HTTP {resp.status_code}) - 服务器崩溃")
    report.add(result)

    # Case 4: 请求负数页码
    result = TestResult("请求负数页码 GET /api/greet_records?page=-1", "边界case测试")
    print(f"  测试: 请求负数页码")
    resp = api_get("/api/greet_records?page=-1")
    if resp is None:
        # 判空必须用 is None：requests 2.33 里 bool(Response) 就是 Response.ok，
        # 用 "if not resp" 会把 404/400/500 这些"真的回了话"读成"无响应"
        result.error_test("无响应")
    else:
        details = {"status_code": resp.status_code, "response": resp.text[:500]}
        # 应不崩溃（返回200或400，但不能500）
        if resp.status_code < 500:
            result.pass_test(details)
            print(f"    结果: 通过 (HTTP {resp.status_code}) - 未崩溃")
        else:
            result.fail_test([{"case": "负数页码不应导致500错误", "actual_status": resp.status_code}], details)
            print(f"    结果: 失败 (HTTP {resp.status_code}) - 服务器崩溃")
    report.add(result)


# ===================== 4. 数据完整性测试 =====================

def test_data_integrity(report):
    """2c. 数据完整性测试。"""
    print("\n[2c] 数据完整性测试...")

    # 测试打招呼记录完整性
    result = TestResult("打招呼记录必填字段完整性", "数据完整性测试")
    print(f"  测试: 打招呼记录必填字段（job_name, company, timestamp）")

    file_data = load_json_file(GREET_RECORDS_FILE)
    if file_data is None:
        result.error_test("无法加载 greet_records.json")
        report.add(result)
        return

    records = file_data.get("records", [])
    required_fields = ["job_name", "company", "timestamp"]
    field_stats = {f: {"total": 0, "empty": 0, "empty_rate": 0.0} for f in required_fields}

    for rec in records:
        for field in required_fields:
            field_stats[field]["total"] += 1
            value = rec.get(field, "")
            if not value or (isinstance(value, str) and not value.strip()):
                field_stats[field]["empty"] += 1

    for field in required_fields:
        total = field_stats[field]["total"]
        empty = field_stats[field]["empty"]
        field_stats[field]["empty_rate"] = (empty / total * 100) if total > 0 else 0.0

    # 找出空率超过50%的字段
    high_empty_fields = [f for f in required_fields if field_stats[f]["empty_rate"] > 50]

    details = {
        "total_records": len(records),
        "field_stats": field_stats,
        "high_empty_rate_fields": high_empty_fields,
    }

    if high_empty_fields:
        result.fail_test(
            [{"field": f, "empty_rate": f"{field_stats[f]['empty_rate']:.2f}%"} for f in high_empty_fields],
            details,
        )
        print(f"    结果: 失败 - 空率超过50%的字段: {high_empty_fields}")
    else:
        result.pass_test(details)
        print(f"    结果: 通过 - 所有必填字段空率均低于50%")

    report.add(result)

    # 测试回复记录完整性
    result = TestResult("回复记录必填字段完整性", "数据完整性测试")
    print(f"  测试: 回复记录必填字段（received_message, reply_content, timestamp）")

    file_data = load_json_file(REPLY_RECORDS_FILE)
    if file_data is None:
        result.error_test("无法加载 reply_records.json")
        report.add(result)
        return

    records = file_data.get("records", [])
    required_fields = ["received_message", "reply_content", "timestamp"]
    field_stats = {f: {"total": 0, "empty": 0, "empty_rate": 0.0} for f in required_fields}

    # is_skipped 是"看了这条、决定不回"的留痕行，reply_content 天生是空的，
    # 拿它进分母会算出 68% 的空字段率并天天报红——那是设计，不是数据缺。
    # 该有回复的只有没跳过的那批。
    跳过行 = [r for r in records if r.get("is_skipped")]
    该回复行 = [r for r in records if not r.get("is_skipped")]

    def 该看某字段的记录(field):
        return 该回复行 if field == "reply_content" else records

    for field in required_fields:
        for rec in 该看某字段的记录(field):
            field_stats[field]["total"] += 1
            value = rec.get(field, "")
            if not value or (isinstance(value, str) and not value.strip()):
                field_stats[field]["empty"] += 1

    for field in required_fields:
        total = field_stats[field]["total"]
        empty = field_stats[field]["empty"]
        field_stats[field]["empty_rate"] = (empty / total * 100) if total > 0 else 0.0
    field_stats["reply_content"]["口径"] = "只算没跳过的 %d 条（另有 %d 条 is_skipped 留痕行不该有回复内容）" % (
        len(该回复行), len(跳过行))

    high_empty_fields = [f for f in required_fields if field_stats[f]["empty_rate"] > 50]

    details = {
        "total_records": len(records),
        "field_stats": field_stats,
        "high_empty_rate_fields": high_empty_fields,
    }

    if high_empty_fields:
        result.fail_test(
            [{"field": f, "empty_rate": f"{field_stats[f]['empty_rate']:.2f}%"} for f in high_empty_fields],
            details,
        )
        print(f"    结果: 失败 - 空率超过50%的字段: {high_empty_fields}")
    else:
        result.pass_test(details)
        print(f"    结果: 通过 - 所有必填字段空率均低于50%")

    report.add(result)

    # 额外：检查备份文件中是否有数据（当前文件为空但备份有数据的情况）
    result = TestResult("数据文件非空检查", "数据完整性测试")
    print(f"  测试: 检查当前数据文件是否有记录")

    greet_file_data = load_json_file(GREET_RECORDS_FILE) or {}
    reply_file_data = load_json_file(REPLY_RECORDS_FILE) or {}
    greet_count = len(greet_file_data.get("records", []))
    reply_count = len(reply_file_data.get("records", []))

    # 检查备份目录中是否有非空备份
    data_dir = PROJECT_ROOT / "data"
    greet_backups = []
    reply_backups = []
    for f in data_dir.iterdir():
        if not f.is_file():
            continue
        if "greet_records" in f.name and f.name.endswith(".json") and f.name != "greet_records.json":
            try:
                d = json.load(open(f, "r", encoding="utf-8"))
                cnt = len(d.get("records", [])) if isinstance(d, dict) else 0
                if cnt > 0:
                    greet_backups.append({"file": f.name, "records": cnt})
            except Exception:
                pass
        if "reply_records" in f.name and f.name.endswith(".json") and f.name != "reply_records.json":
            try:
                d = json.load(open(f, "r", encoding="utf-8"))
                cnt = len(d.get("records", [])) if isinstance(d, dict) else 0
                if cnt > 0:
                    reply_backups.append({"file": f.name, "records": cnt})
            except Exception:
                pass

    details = {
        "current_greet_records": greet_count,
        "current_reply_records": reply_count,
        "non_empty_greet_backups": greet_backups,
        "non_empty_reply_backups": reply_backups,
    }

    issues = []
    if greet_count == 0 and greet_backups:
        issues.append({
            "warning": "当前 greet_records.json 为空，但存在非空备份文件",
            "current_records": greet_count,
            "backups": greet_backups,
        })
    if reply_count == 0 and reply_backups:
        issues.append({
            "warning": "当前 reply_records.json 为空，但存在非空备份文件",
            "current_records": reply_count,
            "backups": reply_backups,
        })

    if issues:
        result.fail_test(issues, details)
        print(f"    结果: 失败 - 当前数据文件为空但存在非空备份")
    else:
        result.pass_test(details)
        print(f"    结果: 通过 - 数据文件状态正常")

    report.add(result)


# ===================== 报告生成 =====================

def generate_markdown_report(report):
    """生成Markdown格式的测试报告。"""
    summary = report.summary()
    lines = []
    lines.append("# 三端一致性测试报告\n")
    lines.append(f"**测试时间**: {report.start_time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append(f"**测试耗时**: {(datetime.now() - report.start_time).total_seconds():.2f} 秒\n")
    lines.append(f"**项目路径**: `{PROJECT_ROOT}`\n")
    lines.append(f"**后端API**: `{BASE_URL}`\n\n")

    # 总体结果
    lines.append("## 一、总体结果\n")
    lines.append(f"| 指标 | 值 |")
    lines.append(f"|------|----|")
    lines.append(f"| 测试总数 | {summary['total']} |")
    lines.append(f"| 通过数 | {summary['passed']} |")
    lines.append(f"| 失败数 | {summary['failed']} |")
    lines.append(f"| 通过率 | {summary['pass_rate']} |\n")

    # 分类结果统计
    categories = {}
    for r in report.results:
        if r.category not in categories:
            categories[r.category] = {"total": 0, "passed": 0, "failed": 0}
        categories[r.category]["total"] += 1
        if r.passed:
            categories[r.category]["passed"] += 1
        else:
            categories[r.category]["failed"] += 1

    lines.append("### 分类统计\n")
    lines.append("| 类别 | 总数 | 通过 | 失败 |")
    lines.append("|------|------|------|------|")
    for cat, stats in categories.items():
        lines.append(f"| {cat} | {stats['total']} | {stats['passed']} | {stats['failed']} |")
    lines.append("")

    # 1. 三端一致性结果
    lines.append("## 二、三端一致性测试结果\n")
    consistency_results = [r for r in report.results if r.category == "三端一致性"]
    for r in consistency_results:
        status_icon = "✅" if r.passed else "❌"
        lines.append(f"### {status_icon} {r.name}\n")
        if r.error:
            lines.append(f"- **错误**: {r.error}\n")
            continue
        if r.passed:
            lines.append("- **结果**: 三端一致 ✅\n")
        else:
            lines.append("- **结果**: 存在不一致 ❌\n")

        # 详细数据
        if r.details:
            lines.append("\n<details>")
            lines.append("<summary>详细数据</summary>\n")
            lines.append("```json")
            lines.append(json.dumps(r.details, ensure_ascii=False, indent=2))
            lines.append("```\n")
            lines.append("</details>\n")

        # 问题列表
        if r.issues:
            lines.append(f"\n**发现的不一致问题** ({len(r.issues)} 个):\n")
            for i, issue in enumerate(r.issues, 1):
                lines.append(f"\n**问题 {i}**:")
                lines.append(f"```json")
                lines.append(json.dumps(issue, ensure_ascii=False, indent=2))
                lines.append(f"```")
            lines.append("")

    # 2. API端点测试结果
    lines.append("\n## 三、API端点测试结果\n")
    api_results = [r for r in report.results if r.category == "API端点测试"]
    lines.append("| 状态 | 端点 | HTTP状态码 |")
    lines.append("|------|------|-----------|")
    for r in api_results:
        status_icon = "✅" if r.passed else "❌"
        status_code = r.details.get("status_code", "N/A") if r.details else "N/A"
        lines.append(f"| {status_icon} | {r.name} | {status_code} |")
    lines.append("")

    # API端点详细信息
    lines.append("### 详细信息\n")
    for r in api_results:
        status_icon = "✅" if r.passed else "❌"
        lines.append(f"#### {status_icon} {r.name}\n")
        if r.error:
            lines.append(f"- **错误**: {r.error}\n")
        if r.details:
            lines.append(f"- **HTTP状态码**: {r.details.get('status_code', 'N/A')}")
            resp_preview = r.details.get("response_preview", "")
            if resp_preview:
                lines.append(f"- **响应预览**:")
                lines.append(f"```")
                lines.append(resp_preview[:300])
                lines.append(f"```")
            lines.append("")

    # 3. 边界case测试结果
    lines.append("\n## 四、边界case测试结果\n")
    edge_results = [r for r in report.results if r.category == "边界case测试"]
    lines.append("| 状态 | 测试case | HTTP状态码 |")
    lines.append("|------|----------|-----------|")
    for r in edge_results:
        status_icon = "✅" if r.passed else "❌"
        status_code = r.details.get("status_code", "N/A") if r.details else "N/A"
        lines.append(f"| {status_icon} | {r.name} | {status_code} |")
    lines.append("")

    for r in edge_results:
        status_icon = "✅" if r.passed else "❌"
        lines.append(f"### {status_icon} {r.name}\n")
        if r.error:
            lines.append(f"- **错误**: {r.error}\n")
        if r.details:
            lines.append(f"- **HTTP状态码**: {r.details.get('status_code', 'N/A')}")
            lines.append(f"- **响应**: `{r.details.get('response', '')[:200]}`\n")
        if r.issues:
            lines.append(f"- **问题**: {r.issues}\n")

    # 4. 数据完整性测试结果
    lines.append("\n## 五、数据完整性测试结果\n")
    integrity_results = [r for r in report.results if r.category == "数据完整性测试"]
    for r in integrity_results:
        status_icon = "✅" if r.passed else "❌"
        lines.append(f"### {status_icon} {r.name}\n")
        if r.error:
            lines.append(f"- **错误**: {r.error}\n")
            continue
        if r.details:
            lines.append("\n<details>")
            lines.append("<summary>详细数据</summary>\n")
            lines.append("```json")
            lines.append(json.dumps(r.details, ensure_ascii=False, indent=2))
            lines.append("```\n")
            lines.append("</details>\n")
        if r.issues:
            lines.append(f"\n**发现的问题** ({len(r.issues)} 个):\n")
            for i, issue in enumerate(r.issues, 1):
                lines.append(f"\n**问题 {i}**:")
                lines.append(f"```json")
                lines.append(json.dumps(issue, ensure_ascii=False, indent=2))
                lines.append(f"```")
            lines.append("")

    # 5. 发现的问题汇总
    lines.append("\n## 六、发现的问题汇总\n")
    all_issues = []
    for r in report.results:
        if not r.passed and r.issues:
            for issue in r.issues:
                all_issues.append({"test": r.name, "category": r.category, "issue": issue})
        if r.error:
            all_issues.append({"test": r.name, "category": r.category, "error": r.error})

    if all_issues:
        lines.append(f"共发现 **{len(all_issues)}** 个问题:\n")
        for i, item in enumerate(all_issues, 1):
            lines.append(f"\n### 问题 {i}: [{item['category']}] {item['test']}\n")
            lines.append(f"```json")
            lines.append(json.dumps(item, ensure_ascii=False, indent=2))
            lines.append(f"```\n")
    else:
        lines.append("✅ 未发现任何问题，所有测试均通过！\n")

    # 6. 测试结论
    lines.append("\n## 七、测试结论\n")
    if summary["failed"] == 0:
        lines.append("✅ **所有测试通过**，三端数据一致，API端点正常，边界case处理合理，数据完整性良好。\n")
    else:
        lines.append(f"❌ **{summary['failed']} 项测试失败**，请查看上述详细问题列表。\n")
        lines.append("\n### 建议修复措施\n")
        # 根据问题类型给出建议
        consistency_failed = any(r.category == "三端一致性" and not r.passed for r in report.results)
        api_failed = any(r.category == "API端点测试" and not r.passed for r in report.results)
        edge_failed = any(r.category == "边界case测试" and not r.passed for r in report.results)
        integrity_failed = any(r.category == "数据完整性测试" and not r.passed for r in report.results)

        if consistency_failed:
            lines.append("- **三端一致性问题**: 检查前端页面是否正确加载了后端API返回的数据，以及配置文件是否被正确读取。\n")
        if api_failed:
            lines.append("- **API端点问题**: 检查后端路由是否正确注册，以及对应的处理函数是否能正常执行。\n")
        if edge_failed:
            lines.append("- **边界case问题**: 加强后端对异常输入的校验和错误处理。\n")
        if integrity_failed:
            lines.append("- **数据完整性问题**: 检查数据写入逻辑是否正确填充了所有必填字段。\n")

    # 写入文件
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return "\n".join(lines)


# ===================== 主函数 =====================

def main():
    print("=" * 70)
    print("三端一致性测试 + 常见软件测试")
    print("=" * 70)
    print(f"项目路径: {PROJECT_ROOT}")
    print(f"后端API: {BASE_URL}")
    print(f"报告输出: {REPORT_FILE}")
    print("=" * 70)

    # 检查后端是否运行
    print("\n[预检] 检查后端API是否运行...")
    resp = api_get("/api/status")
    if resp is None or resp.status_code != 200:
        print(f"  [错误] 后端API不可用 (HTTP {resp.status_code if resp is not None else '无响应'})")
        print("  请先启动Flask服务器: python flask-version/app.py")
        return
    print("  [OK] 后端API运行正常")

    # 初始化报告收集器
    report = ReportCollector()

    # 启动前端浏览器
    print("\n[预检] 启动Playwright前端浏览器...")
    frontend = FrontendReader()
    frontend.start()

    try:
        # 1. 三端一致性测试
        print("\n" + "=" * 70)
        print("一、三端一致性测试")
        print("=" * 70)

        test_ai_config_consistency(report, frontend)
        test_greet_records_consistency(report, frontend)
        test_reply_records_consistency(report, frontend)
        test_user_profile_consistency(report, frontend)
        test_stats_card_consistency(report, frontend)

        # 2. 常见软件测试
        print("\n" + "=" * 70)
        print("二、常见软件测试")
        print("=" * 70)

        # 2a. API端点测试
        test_api_endpoints(report)

        # 2b. 边界case测试
        test_edge_cases(report)

        # 2c. 数据完整性测试
        test_data_integrity(report)

    finally:
        frontend.close()

    # 生成报告
    print("\n" + "=" * 70)
    print("三、生成测试报告")
    print("=" * 70)

    report.save_json()
    markdown_report = generate_markdown_report(report)

    summary = report.summary()
    print(f"\n测试完成!")
    print(f"  总数: {summary['total']}")
    print(f"  通过: {summary['passed']}")
    print(f"  失败: {summary['failed']}")
    print(f"  通过率: {summary['pass_rate']}")
    print(f"\n报告已保存到:")
    print(f"  Markdown: {REPORT_FILE}")
    print(f"  JSON:     {REPORT_JSON_FILE}")


if __name__ == "__main__":
    main()