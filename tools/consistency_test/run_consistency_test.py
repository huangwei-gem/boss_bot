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

import hashlib
import json
import os
import sys
import traceback
from pathlib import Path
from datetime import datetime

import requests

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from boss_bot.unified_config import 新建临时目录, 面板端口  # noqa: E402

# 后端API基础URL —— 跟面板用同一个数（unified_config 那份默认口，含 BOSS_PANEL_PORT 覆盖）。
# 自己写死一个端口就等于"探 A 听 B"：检查看着全绿，其实读的是别的项目的服务——
# 10-27 与 10-28 两次实测，5000 上站着的是另一个项目的 app.py。
BASE_URL = "http://127.0.0.1:%s" % 面板端口()

# 配置文件路径
BOT_CONFIG_FILE = PROJECT_ROOT / "bot_config.json"
USER_PROFILE_FILE = PROJECT_ROOT / "user_profile.json"
GREET_RECORDS_FILE = PROJECT_ROOT / "data" / "greet_records.json"
REPLY_RECORDS_FILE = PROJECT_ROOT / "data" / "reply_records.json"

# 测试报告输出目录
REPORT_DIR = Path(__file__).resolve().parent
REPORT_FILE = REPORT_DIR / "test_report.md"
REPORT_JSON_FILE = REPORT_DIR / "test_report.json"

# 副作用类用例（POST）只打这一个隔离实例，配置是临时副本，跑完就扔
SANDBOX = {"url": None, "proc": None, "目录": None}
SANDBOX_PORT = int(os.environ.get("BOSS_CONSISTENCY_SANDBOX_PORT", "5078"))


def 起隔离实例():
    """抄一份配置到临时目录，在那里起一个面板实例给 POST 用例挨。

    复用实测套件的 start_server（LAUNCHER 只有一处），别再抄第三份启动代码。
    """
    sys.path.insert(0, str(PROJECT_ROOT / "tests"))
    import e2e_account_scope_ui as E
    tmp = 新建临时目录("boss_consistency_")
    E.seed(tmp)                       # 拿真实配置的副本（只把招呼语清空，不改用户那份）
    proc = E.start_server(tmp, SANDBOX_PORT)
    SANDBOX.update(url=f"http://127.0.0.1:{SANDBOX_PORT}", proc=proc, 目录=tmp)
    print(f"  [隔离实例] {SANDBOX['url']}（配置是 {tmp} 里的副本）")
    return True


def 停隔离实例():
    proc, tmp = SANDBOX.get("proc"), SANDBOX.get("目录")
    if proc:
        from boss_bot.platform_compat import 终止进程
        终止进程(proc.pid)
    if tmp:
        import shutil
        shutil.rmtree(str(tmp), ignore_errors=True)
    SANDBOX.update(url=None, proc=None, 目录=None)


用户配置文件 = ("bot_config.json", "user_profile.json", "config_overrides.json")
# 这几份是面板自己在写的：回复腿落一条记录、抓一轮池子，哈希就变一次。
# 面板在跑的时候把它们算进"这趟没动用户数据"，这条门永远是红的（10-20 16:17 实测
# 红在 reply_records + job_pool 两份上），而一条永远红的门等于没有门。
# 面板停着跑这一趟时，它们照样钉死——脚本自己写了照样抓得出。
运行中会写 = ("data/greet_records.json", "data/reply_records.json",
              "data/job_pool.json", "data/job_pool_account_1.json")


def 快照配置文件():
    """给"这一趟跑完不许动用户任何数据"这条门用：算每个受管文件的哈希。

    清单必须盖住沙箱顶掉的每一个配置出口——10-20 那次 bot_config 恢复了、
    config_overrides 漏在清单外，结果 importance_keywords 一直是空的，
    「收到重要消息就暂停」悄悄停了两天，直到画像那条事实闸门才把它牵出来。
    """
    快照 = {}
    for 名 in 用户配置文件 + 运行中会写:
        p = PROJECT_ROOT / 名
        快照[名] = (hashlib.sha256(p.read_bytes()).hexdigest()[:16]
                    if p.is_file() else "不存在")
    return 快照


def 判定用户数据(跑前, 跑后, 面板在跑):
    """对比两份快照，分成"这趟犯规"和"面板自己在写、本轮豁免"。

    拆成纯函数是为了能被 pytest 真验一遍（门要是只会放行，它和没有一样）：
    配置文件任何时候都钉死；记录文件只在确实读到 running=true 时才豁免。
    返回 (犯规字典, 豁免字典)。
    """
    变了 = {k: (跑前[k], 跑后[k]) for k in 跑前
            if k in 跑后 and 跑前[k] != 跑后[k]}
    动了记录 = {k: v for k, v in 变了.items() if k in 运行中会写}
    犯规 = {k: v for k, v in 变了.items() if k not in 运行中会写}
    if not 面板在跑:
        犯规.update(动了记录)
        return 犯规, {}
    return 犯规, 动了记录


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


def api_get(path, timeout=10, base=None):
    """GET请求API。"""
    url = f"{base or BASE_URL}{path}"
    try:
        resp = requests.get(url, timeout=timeout)
        return resp
    except Exception as e:
        print(f"  [警告] API请求失败 {url}: {e}")
        return None


def api_post(path, json_data=None, timeout=15, send_json=True, base=None):
    """POST请求API——只准打隔离实例，不许打用户正在用的 :5000。

    base 缺省取 SANDBOX_URL；沙箱没起来就当场拒发。理由不是洁癖：
    2026-10-10 这类"全量脚本对着线上面板 POST /api/config"把用户的
    bot_config.json 真写空过（providers、两套词表、简历全归 0），
    而这份报告只是来"看"的，没有任何一眼需要它写。
    """
    目标 = base or SANDBOX.get("url")
    if not 目标:
        print("  [拒发] 没有隔离实例，这个 POST 不打线上面板：%s" % path)
        return None
    url = f"{目标}{path}"
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


# ===================== 1f. 岗位候选池一致性 =====================

池状态键 = ("new", "selected", "applied", "rejected")


def _池行id们(接: dict) -> set:
    # id 为 0 是合法值，不能写成 `r.get("id") or ""`——那会把 0 当成"没读到"丢掉。
    ids = {str(r.get("id")).strip() for r in (接.get("rows") or []) if r.get("id") is not None}
    ids.discard("")
    return ids


def _池界面问题(名: str, total, 画: dict, 接口id集: set, 查重复: bool) -> list:
    """界面上那 60 行与角标该不该红——单号与合计共用这一份。

    表是分批铺的（一批 60 行，近三千行要铺好几秒），所以"屏幕行数 == 池里条数"
    只在池子装得下一批时成立；10-29 这一趟 1492 条对上屏幕 60 行，旧判据当场红成
    "三端不一致"，其实一行都没错。能钉死的是：角标与「筛出 N 条」必须等于接口那个数，
    屏幕上铺出来的行必须真在接口那份里、不重复、不超过总数——少画几行是还没铺完，
    画出池里没有的行才是真坏（删掉的又冒出来）。
    """
    问题 = []
    if 画.get("读取失败"):
        问题.append({"field": f"{名}.界面读取", "config_file_value": "",
                     "api_value": "", "frontend_value": str(画.get("读取失败"))})
        return 问题
    if str(画.get("角", "")).strip() != str(total):
        问题.append({"field": f"{名}.角标", "config_file_value": "",
                     "api_value": total, "frontend_value": 画.get("角")})
    if str(画.get("条数格", "")).strip() != "筛出 %s 条" % total:
        问题.append({"field": f"{名}.筛出条数", "config_file_value": "",
                     "api_value": f"筛出 {total} 条", "frontend_value": 画.get("条数格")})
    行 = 画.get("行")
    if not isinstance(行, int) or 行 <= 0:
        问题.append({"field": f"{名}.界面空表", "config_file_value": "",
                     "api_value": total, "frontend_value": f"{行} 行"})
    elif total is not None and 行 > int(total):
        问题.append({"field": f"{名}.界面比池里多", "config_file_value": "",
                     "api_value": total, "frontend_value": f"{行} 行"})
    id们 = [str(i) for i in (画.get("id们") or [])]
    if 查重复 and len(id们) != len(set(id们)):
        问题.append({"field": f"{名}.界面重复行", "config_file_value": "",
                     "api_value": "每行 id 唯一", "frontend_value": f"{行} 行里只有 {len(set(id们))} 个 id"})
    多出来 = [i for i in id们 if i and i not in 接口id集]
    if 多出来:
        问题.append({"field": f"{名}.界面上有池里没有的", "config_file_value": "",
                     "api_value": f"接口 {len(接口id集)} 个 id",
                     "frontend_value": "屏幕上是这些池里没有的：" + "、".join(多出来[:5])})
    return 问题


def 单号池该不该红(号: int, 盘条数: int, 盘状态: dict, 接: dict, 画: dict) -> list:
    """一个号的池子：盘上 / 接口 / 界面 三端该不该红。"""
    问题 = []
    total = 接.get("total")
    if total != 盘条数:
        问题.append({"field": f"岗位池.account{号}.条数", "config_file_value": 盘条数,
                     "api_value": total, "frontend_value": "接口按盘上那份生成"})
    接口状态 = {k: v for k, v in (接.get("stats") or {}).items() if k in 池状态键}
    for s in 池状态键:
        if 盘状态.get(s, 0) != 接口状态.get(s, 0):
            问题.append({"field": f"岗位池.account{号}.状态{s}",
                         "config_file_value": 盘状态.get(s, 0),
                         "api_value": 接口状态.get(s, 0),
                         "frontend_value": "接口按状态给计数，界面按接口画"})
    问题 += _池界面问题(f"岗位池.account{号}", total, 画, _池行id们(接), 查重复=True)
    return 问题


def 合并池该不该红(各号: list, 接: dict, 画: dict) -> list:
    """「全部账号」这一档：两个号的池并成一张表，合计数与屏幕要跟两份盘上的和相等。

    10-26 之前这一档是"接口照实拒、界面说没有"，10-26 改成合并视图（#259/#260），
    这条判据也得跟着落到新口径上，否则每次跑都是红的。
    """
    问题 = []
    和 = sum(int(x.get("total") or 0) for x in 各号)
    if 接.get("status_code") != 200:
        问题.append({"field": "岗位池.全部账号.接口", "config_file_value": 和,
                     "api_value": f"HTTP {接.get('status_code')}",
                     "frontend_value": "合并视图这一档该正常返回"})
        return 问题
    if 接.get("total") != 和:
        问题.append({"field": "岗位池.全部账号.total", "config_file_value": 和,
                     "api_value": 接.get("total"), "frontend_value": "角标按接口画"})
    合并状态 = {k: v for k, v in (接.get("stats") or {}).items() if k in 池状态键}
    for s in 池状态键:
        应 = sum(int((x.get("状态") or {}).get(s, 0)) for x in 各号)
        if 合并状态.get(s, 0) != 应:
            问题.append({"field": f"岗位池.全部账号.状态{s}", "config_file_value": 应,
                         "api_value": 合并状态.get(s, 0),
                         "frontend_value": "合计按两份盘上的和"})
    问题 += _池界面问题("岗位池.全部账号", 接.get("total"), 画, _池行id们(接), 查重复=False)
    if 画.get("空态可见") is True:
        问题.append({"field": "岗位池.全部账号.空态", "config_file_value": "",
                     "api_value": "有行时该藏起来", "frontend_value": "空态框还露在表上"})
    return 问题


def test_job_pool_consistency(report, frontend):
    """1f. 岗位候选池：盘上那份池文件 / 接口 / 界面表格，三端必须同一份数。

    这一张表是他手工勾投递的地方，行数对不上就两种坏法：
    界面少行=有岗位永远投不出去，界面多行=删掉的又冒出来。界面是分批铺的，
    所以判据钉在「盘上 == 接口 total == 角标 == 筛出 N 条」与「屏幕上的行都在池里」
    上，而不是逼屏幕一次铺完几千行（10-29 那趟就是被这条旧判据红掉的）。
    """
    result = TestResult("岗位候选池一致性", "三端一致性")
    try:
        from boss_bot.job_pool import 岗位池
    except Exception as e:
        result.error_test(f"导入岗位池失败：{e}")
        report.add(result)
        return

    resp = api_get("/api/config")
    if resp is None or resp.status_code != 200:
        result.error_test("API /api/config 请求失败，拿不到账号数")
        report.add(result)
        return
    账号数 = len(((resp.json().get("config") or {}).get("accounts")) or [])
    if 账号数 <= 0:
        result.error_test("接口里一个账号都没有，池无从按号分开")
        report.add(result)
        return

    明细, 问题 = {}, []
    for 号 in range(账号数):
        盘 = 岗位池(account_index=号).列表()
        盘状态 = {}
        for r in 盘:
            盘状态[r.get("status") or "new"] = 盘状态.get(r.get("status") or "new", 0) + 1

        # limit=0 才给全量：接口默认只回一页（200 条），体检要的是整份池
        g = api_get(f"/api/job_pool?account={号}&limit=0", timeout=60)
        if g is None or g.status_code != 200:
            问题.append({"field": f"岗位池.account{号}.接口",
                         "config_file_value": len(盘),
                         "api_value": f"HTTP {getattr(g, 'status_code', None)}",
                         "frontend_value": "没读"})
            continue
        接 = g.json()
        接口状态 = {k: v for k, v in (接.get("stats") or {}).items()
                  if k in ("new", "selected", "applied", "rejected")}

        # 界面这一端：切到该号的数据范围、打开池那一页，看角标与真铺出来的行。
        # FrontendReader.evaluate 出错时回的是 "<读取失败: …>" 这种字符串，
        # 不是 dict——照 dict 用会当场抛 AttributeError，红成"脚本坏了"而不是"哪一端对不上"。
        frontend.evaluate("() => { setDataScope(%d); return 1; }" % 号)
        frontend.evaluate("() => { switchRecordTab('pool'); return 1; }")
        总数 = 接.get("total")
        画 = frontend.evaluate("""() => new Promise(function(resolve){
            var 试 = 0;
            var 期望 = %s;
            var t = setInterval(function(){
                试++;
                var 行s = document.querySelectorAll('#poolTableBody tr');
                var 角 = (document.getElementById('poolTabCount')||{}).textContent||'';
                if ((String(角) === String(期望) && 行s.length > 0) || 试 > 20) {
                    clearInterval(t);
                    resolve({行: 行s.length, 角: 角,
                             条数格: (document.getElementById('poolFilterCount')||{}).textContent||'',
                             id们: Array.prototype.map.call(行s, function(tr){
                                 return tr.getAttribute('data-id')||''; })});
                }
            }, 300);
        })""" % 总数)
        if not isinstance(画, dict):
            画 = {"读取失败": str(画)}
        明细[f"账号{号}"] = {
            "盘上": {"条数": len(盘), "状态": 盘状态, "文件": str(岗位池(account_index=号).路径)},
            "接口": {"total": 接.get("total"), "状态": 接口状态},
            "界面": 画,
        }
        问题 += 单号池该不该红(号, len(盘), 盘状态, 接, 画)

    # 「全部账号」这一档：两个号的池并成一张表，合计与屏幕都得跟着两份盘上的和
    frontend.evaluate("() => { setDataScope('all'); return 1; }")
    frontend.evaluate("() => { switchRecordTab('pool'); return 1; }")
    全部 = api_get("/api/job_pool?account=all&limit=0", timeout=120)
    全部json = 全部.json() if (全部 is not None and 全部.status_code == 200) else {}
    期望合计 = 全部json.get("total")
    合并画 = frontend.evaluate("""() => new Promise(function(resolve){
        var 试 = 0;
        var 期望 = %s;
        var t = setInterval(function(){
            试++;
            var 行s = document.querySelectorAll('#poolTableBody tr');
            var 角 = (document.getElementById('poolTabCount')||{}).textContent||'';
            var 空 = document.getElementById('poolEmpty');
            if ((String(角) === String(期望) && 行s.length > 0) || 试 > 25) {
                clearInterval(t);
                resolve({行: 行s.length, 角: 角,
                         条数格: (document.getElementById('poolFilterCount')||{}).textContent||'',
                         空态可见: !!空 && getComputedStyle(空).display !== 'none',
                         id们: Array.prototype.map.call(行s, function(tr){
                             return tr.getAttribute('data-id')||''; })});
            }
        }, 300);
    })""" % 期望合计)
    if not isinstance(合并画, dict):
        合并画 = {"读取失败": str(合并画)}
    明细["全部账号"] = {"接口": {"status_code": 全部.status_code if 全部 else None,
                              "total": 全部json.get("total"),
                              "状态": 全部json.get("stats")},
                      "界面": 合并画}
    问题 += 合并池该不该红(
        [{"total": 明细[f"账号{号}"]["接口"]["total"],
          "状态": 明细[f"账号{号}"]["接口"]["状态"]} for 号 in range(账号数)],
        {"status_code": 全部.status_code if 全部 else None,
         "total": 全部json.get("total"),
         "stats": 全部json.get("stats") or {},
         "rows": 全部json.get("rows") or []},
        合并画)

    if 问题:
        result.fail_test(问题, 明细)
    else:
        result.pass_test(明细)
    report.add(result)
    print(f"  结果: {'通过' if result.passed else '失败'}, 问题数: {len(问题)}")


# ===================== 1g. 候选池两把开关一致性 =====================

def test_pool_switches_consistency(report, frontend):
    """1g. 「只抓不投」与「每轮精筛额度」：文件里那份 / 接口 / 界面控件。

    文件里没这一条时接口给的是代码默认值，这不是不一致——所以默认值从
    unified_config 现读，不写死在这份脚本里（写死了就等于又造第四个口径）。
    """
    result = TestResult("候选池开关一致性", "三端一致性")
    try:
        import dataclasses
        from boss_bot.unified_config import UnifiedConfig
    except Exception as e:
        result.error_test(f"导入配置类失败：{e}")
        report.add(result)
        return

    现盘 = UnifiedConfig.load()
    默认只抓 = next(f.default for f in dataclasses.fields(现盘.greet)
                    if f.name == "capture_only")
    默认额度 = next(f.default for f in dataclasses.fields(现盘.ai)
                    if f.name == "screen_max_per_round")
    默认详情间隔 = next(f.default for f in dataclasses.fields(现盘.greet.rate_limit)
                        if f.name == "job_detail_min_interval")
    默认验证冷却 = next(f.default for f in dataclasses.fields(现盘.greet.rate_limit)
                        if f.name == "after_verify_cooldown")
    默认整页地板 = next(f.default for f in dataclasses.fields(现盘.greet.rate_limit)
                        if f.name == "page_load_min_interval")
    默认加载模式 = next(f.default for f in dataclasses.fields(现盘.browser)
                        if f.name == "load_mode")

    resp = api_get("/api/config")
    if resp is None or resp.status_code != 200:
        result.error_test("API /api/config 请求失败")
        report.add(result)
        return
    接 = resp.json().get("config") or {}
    盘 = 现盘.to_dict() if hasattr(现盘, "to_dict") else {}

    frontend.evaluate("() => { var t=document.getElementById('advToggle');"
                      "if (t && !t.classList.contains('open')) t.click(); return 1; }")
    界面 = frontend.evaluate("""() => ({
        开关: (function(){var e=document.getElementById('advCaptureOnly');
                        return e ? e.classList.contains('on') : '<元素不存在>';})(),
        额度: (function(){var e=document.getElementById('aiScreenMaxPerRound');
                        return e ? parseInt(e.value, 10) : '<元素不存在>';})(),
        详情间隔: (function(){var e=document.getElementById('jobDetailMinInterval');
                        return e ? parseInt(e.value, 10) : '<元素不存在>';})(),
        验证冷却: (function(){var e=document.getElementById('afterVerifyCooldown');
                        return e ? parseInt(e.value, 10) : '<元素不存在>';})(),
        整页地板: (function(){var e=document.getElementById('pageLoadMinInterval');
                        return e ? parseInt(e.value, 10) : '<元素不存在>';})(),
        加载模式: (function(){var e=document.getElementById('advLoadMode');
                        return e ? e.value : '<元素不存在>';})()
    })""") or {}

    原文件 = load_json_file(BOT_CONFIG_FILE) or {}
    明细 = {
        "greet.capture_only": {
            "配置文件里字面有这条吗": "capture_only" in (原文件.get("greet") or {}),
            "盘上生效值(读码算)": bool(盘.get("greet", {}).get("capture_only", 默认只抓)),
            "接口": 接.get("greet", {}).get("capture_only"),
            "界面开关": 界面.get("开关"),
            "代码默认": 默认只抓,
        },
        "ai.screen_max_per_round": {
            "配置文件里字面有这条吗": "screen_max_per_round" in (原文件.get("ai") or {}),
            "盘上生效值(读码算)": 盘.get("ai", {}).get("screen_max_per_round", 默认额度),
            "接口": 接.get("ai", {}).get("screen_max_per_round"),
            "界面输入框": 界面.get("额度"),
            "代码默认": 默认额度,
        },
        # 安全验证那一族的两条：频率是按 IP 算的，界面上改了就得当场生效，
        # 不能只住在代码默认里（见 memory「改了必须生效」）
        "rate_limit.job_detail_min_interval": {
            "配置文件里字面有这条吗":
                "job_detail_min_interval" in (原文件.get("rate_limit") or {}),
            "盘上生效值(读码算)": 盘.get("rate_limit", {}).get(
                "job_detail_min_interval", 默认详情间隔),
            "接口": 接.get("rate_limit", {}).get("job_detail_min_interval"),
            "界面输入框": 界面.get("详情间隔"),
            "代码默认": 默认详情间隔,
        },
        "rate_limit.after_verify_cooldown": {
            "配置文件里字面有这条吗":
                "after_verify_cooldown" in (原文件.get("rate_limit") or {}),
            "盘上生效值(读码算)": 盘.get("rate_limit", {}).get(
                "after_verify_cooldown", 默认验证冷却),
            "接口": 接.get("rate_limit", {}).get("after_verify_cooldown"),
            "界面输入框": 界面.get("验证冷却"),
            "代码默认": 默认验证冷却,
        },
        # 整页加载这一族：地板按进程摊平同一秒的并发加载，加载模式决定
        # 每一发等到哪一步（10-24 实测 normal 一次要 126~135 秒，eager 1.6~4.8 秒）
        "rate_limit.page_load_min_interval": {
            "配置文件里字面有这条吗":
                "page_load_min_interval" in (原文件.get("rate_limit") or {}),
            "盘上生效值(读码算)": 盘.get("rate_limit", {}).get(
                "page_load_min_interval", 默认整页地板),
            "接口": 接.get("rate_limit", {}).get("page_load_min_interval"),
            "界面输入框": 界面.get("整页地板"),
            "代码默认": 默认整页地板,
        },
        "browser.load_mode": {
            "配置文件里字面有这条吗": "load_mode" in (原文件.get("browser") or {}),
            "盘上生效值(读码算)": 盘.get("browser", {}).get("load_mode", 默认加载模式),
            "接口": 接.get("browser", {}).get("load_mode"),
            "界面输入框": 界面.get("加载模式"),
            "代码默认": 默认加载模式,
        },
    }

    问题 = []
    for 字段 in ("greet.capture_only", "ai.screen_max_per_round",
                 "rate_limit.job_detail_min_interval",
                 "rate_limit.after_verify_cooldown",
                 "rate_limit.page_load_min_interval",
                 "browser.load_mode"):
        d = 明细[字段]
        段, 键 = 字段.split(".")
        文件值 = d["盘上生效值(读码算)"]
        接口值 = d["接口"]
        界面值 = d["界面开关"] if 键 == "capture_only" else d["界面输入框"]
        if 键 == "capture_only":
            界面值 = bool(界面值)
        if 接口值 is None or 文件值 != 接口值 or 界面值 != 接口值:
            问题.append({"field": 字段, "config_file_value": 文件值,
                         "api_value": 接口值, "frontend_value": 界面值})
    if 问题:
        result.fail_test(问题, 明细)
    else:
        result.pass_test(明细)
    report.add(result)
    print(f"  结果: {'通过' if result.passed else '失败'}, 问题数: {len(问题)}")


# ===================== 1h. 粗筛标签与现盘词表对得上 =====================

def 粗筛标签该不该红(行s, 自定义, 标题表):
    """盘上池子里贴过标签的行，`否决命中` 必须等于现盘词表当场算出来的那一条。

    这条门是 2026-10-21 那个 bug 的回归锁：粗筛读的是引擎上根本不存在的属性，
    `or []` 把"没读到"兜成"空表"，于是号0+号1 共 1623 条池子里 `否决命中` 一条都没有，
    而标题写着「兼职在线打字客服」「5K居家线上客服」的岗位按现盘词表明明能命中。
    界面那时画的是"方向不合"，看起来像判过了 —— 只有拿配置现算再比才抓得住。
    返回差异列表（空 = 对得上）。
    """
    from boss_bot.greet_engine import veto_keyword_hit
    from boss_bot.job_pool import 岗位行成抓取
    差异 = []
    for 行 in 行s or []:
        标 = 行.get("gate") or {}
        if not (标.get("否决命中") or 标.get("词表没读到")
                or 标.get("标题词表为空") or "方向命中" in 标):
            continue      # 压根没贴过标的行（手工粘进来的）不算"对不上"，那是另一件事
        if not 自定义 and not 标题表:
            if not 标.get("词表没读到"):
                差异.append({"id": 行.get("id"), "job_name": 行.get("job_name"),
                             "盘上": "否决命中=%r" % (标.get("否决命中") or ""),
                             "现算": "词表没读到"})
            continue
        if 自定义 and not 标题表 and not 标.get("标题词表为空"):
            # 10-22 那两行的形状：标题表是空的，而盘上既没写命中也没写"空表"，
            # 界面看着就像"这岗干干净净"——空表这一格必须落在行上。
            差异.append({"id": 行.get("id"), "job_name": 行.get("job_name"),
                         "盘上": "（空）", "现算": "标题词表为空"})
            continue
        期 = veto_keyword_hit(list(自定义), 岗位行成抓取(行),
                              title_keywords=list(标题表)) or ""
        实 = 标.get("否决命中") or ""
        if 期 != 实:
            差异.append({"id": 行.get("id"), "job_name": 行.get("job_name"),
                         "盘上": 实 or "（空）", "现算": 期 or "（空）"})
    return 差异


def test_pool_gate_matches_wordlist(report):
    """1h. 候选池的本地粗筛标签 vs 现盘否决词（只读文件与配置，不开浏览器）。"""
    print("\n[1h] 候选池粗筛标签与词表一致性...")
    result = TestResult("候选池粗筛标签与词表一致性", "三端一致性")
    try:
        from boss_bot.job_pool import 岗位池
        from boss_bot.unified_config import UnifiedConfig
    except Exception as e:
        result.error_test(f"导入失败：{e}")
        report.add(result)
        return

    cfg = UnifiedConfig.load()
    ai = getattr(cfg, "ai", None)
    自定义 = list(getattr(ai, "custom_filter_keywords", None) or [])
    标题 = getattr(ai, "title_veto_keywords", None)
    if 标题 is None:
        from boss_bot.unified_config import TITLE_VETO_KEYWORDS_DEFAULT
        标题 = list(TITLE_VETO_KEYWORDS_DEFAULT)
    标题表 = list(标题)

    明细 = {"词表": {"自定义否决词": len(自定义), "标题否决词": len(标题表)}}
    问题 = []
    for 号 in (0, 1):
        行s = 岗位池(account_index=号).列表()
        贴过标 = [r for r in 行s if (r.get("gate") or {}).get("否决命中")
                  or (r.get("gate") or {}).get("词表没读到")
                  or "方向命中" in (r.get("gate") or {})]
        差异 = 粗筛标签该不该红(行s, 自定义, 标题表)
        明细[f"账号{号}"] = {"池内": len(行s), "贴过标": len(贴过标),
                            "否决命中条数": len([r for r in 行s
                                             if (r.get("gate") or {}).get("否决命中")]),
                            "对不上": len(差异), "样例": 差异[:5]}
        if 差异:
            问题.append({"field": f"账号{号}.粗筛标签", "config_file_value": "现算为准",
                         "api_value": "盘上 gate", "frontend_value": 差异[:5]})
    if not (自定义 or 标题表):
        明细["提示"] = "两份词表都是空的：这时盘上该写「词表没读到」而不是留空"

    if 问题:
        result.fail_test(问题, 明细)
    else:
        result.pass_test(明细)
    report.add(result)
    print(f"  结果: {'通过' if result.passed else '失败'}, 问题数: {len(问题)}")


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
        elif SANDBOX.get("url"):
            # POST 一律打隔离实例：线上面板那份配置是用户攒的，不是测试场
            resp = api_post(path, base=SANDBOX["url"])
        else:
            result.details = {"原因": "没起到隔离实例，这条不发线上面板（红线：不许拿真实实例做副作用验证）"}
            result.fail_test([{"case": f"{method} {path} 需要隔离实例", "实际": "沙箱没起来"}],
                             result.details)
            report.add(result)
            print("    结果: 失败 - 隔离实例没起来，这条没法测")
            continue

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
    result = TestResult("请求不存在的账号 POST /api/accounts/99/check_cookie_simple", "边界case测试")
    print(f"  测试: 请求不存在的账号")
    resp = api_post("/api/accounts/99/check_cookie_simple")
    if resp is None:
        # 判空必须用 is None：requests 2.33 里 bool(Response) 就是 Response.ok，
        # 用 "if not resp" 会把 404/400/500 这些"真的回了话"读成"无响应"
        result.error_test("没打到（隔离实例没起来；这条不许打线上面板）")
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
        result.error_test("没打到（隔离实例没起来；这条绝不能打线上面板——"
                          "20-10 就是这类脚本把用户的 bot_config 写空过）")
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

def 数据文件该不该红(打招呼条数: int, 回复条数: int, 有打招呼备份: bool,
                     有回复备份: bool, 只抓不投: bool) -> list:
    """当前记录文件为空、备份里却有数据——到底算不算事故，只看一条：这台机器现在该不该写。

    10-19 起口径是只抓不投，打招呼记录 0 条就是预期，历史在归档里；
    真在投却一条没落下才是丢数据。回复记录没有"预期为空"这一档，0 条就红。
    """
    红 = []
    if 打招呼条数 == 0 and 有打招呼备份 and not 只抓不投:
        红.append({"warning": "当前 greet_records.json 为空，但存在非空备份文件",
                   "current_records": 打招呼条数})
    if 回复条数 == 0 and 有回复备份:
        红.append({"warning": "当前 reply_records.json 为空，但存在非空备份文件",
                   "current_records": 回复条数})
    return 红


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

    # 「当前文件为空」有两种意思，得先分清再定罪：10-19 起改成只抓不投，0 条打招呼记录
    # 是口径本身，历史都在 data/archive/<日期>/ 里；真在投却一条没落下才可能是数据丢了。
    from boss_bot.unified_config import UnifiedConfig
    只抓不投 = bool(UnifiedConfig.load().greet.capture_only)
    归档最近一条 = None
    for d in sorted((data_dir / "archive").glob("2026-*"), reverse=True):
        f = d / "greet_records.json"
        if not f.is_file():
            continue
        try:
            cnt = len((json.load(open(f, "r", encoding="utf-8")) or {}).get("records", []))
        except Exception:
            continue
        if cnt:
            归档最近一条 = {"日期": d.name, "条数": cnt}
            break
    details["口径"] = ("只抓不投（capture_only）：打招呼记录 0 条是预期"
                       if 只抓不投 else "在投：打招呼记录 0 条要查")
    details["归档里最近的打招呼记录"] = 归档最近一条

    issues = 数据文件该不该红(greet_count, reply_count,
                              bool(greet_backups), bool(reply_backups), 只抓不投)

    if issues:
        result.fail_test(issues, details)
        print(f"    结果: 失败 - 在投状态下打招呼记录文件空了，但备份里有数据")
    else:
        result.pass_test(details)
        print("    结果: 通过 - %s，回复记录 %d 条%s"
              % (details["口径"], reply_count,
                 "，历史在归档 %s（%s 条）" % (归档最近一条["日期"], 归档最近一条["条数"])
                 if 归档最近一条 else ""))

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

    # 这一趟会读哪些用户数据，跑完必须一模一样（只读不许写，写只准发生在隔离实例里）
    初始快照 = 快照配置文件()

    # 起一个隔离实例专门挨 POST 这类有副作用的用例
    print("\n[预检] 起隔离实例（配置是临时副本，POST 用例只打它，不打线上面板）...")
    try:
        起隔离实例()
    except Exception as e:
        print(f"  [警告] 隔离实例没起来：{type(e).__name__}: {e}——POST 类用例会记成测不了")

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
        test_job_pool_consistency(report, frontend)
        test_pool_switches_consistency(report, frontend)
        test_pool_gate_matches_wordlist(report)

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
        停隔离实例()

    # 这一趟只该"看"：任何一份用户数据被写动了，就是工具自己犯规
    末尾快照 = 快照配置文件()
    # 面板在跑时，回复腿落一条记录、抓一轮池子，那几份哈希自己就会变——不是这一趟写的。
    # 把它们算进犯规，这条门就永远是红的，而永远红的门等于没有门（10-20 16:17 实测
    # 红在 reply_records + job_pool）。豁免只在**真读到 running=true** 时给，
    # 而且必须点名豁免了哪几份、前后哈希各是多少，不许糊成一句"没动"。
    在跑 = False
    答 = api_get("/api/status")
    try:
        在跑 = bool(答 and 答.json().get("running"))
    except Exception as e:
        print("  [状态读不准] /api/status 没解析出来(%s)，按面板没在跑办——不给豁免" % e)
    犯规, 动了记录 = 判定用户数据(初始快照, 末尾快照, 在跑)
    守身 = TestResult("这一趟没动用户任何数据", "三端一致性")
    if 犯规:
        守身.fail_test([{"field": k, "config_file_value": "跑前 " + v[0],
                         "api_value": "跑后 " + v[1], "frontend_value": "不该变"}
                        for k, v in 犯规.items()],
                       {"跑前": 初始快照, "跑后": 末尾快照, "面板在跑": 在跑})
    else:
        守身.pass_test({"核对过": 初始快照,
                        "面板在跑": 在跑,
                        "本轮豁免(面板自己在写)": {k: "跑前 %s / 跑后 %s" % (v[0], v[1])
                                                 for k, v in 动了记录.items()}})
    report.add(守身)
    if 动了记录 and 在跑:
        print("\n[收尾] 用户配置一个字节没动；记录文件 %s 变了，"
              "是面板自己在写（running=true），这一趟不判红"
              % "、".join(动了记录))
    else:
        print(f"\n[收尾] 用户数据一个字节都没动：{'是' if not 犯规 else '否——动了 ' + str(list(犯规))}")

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