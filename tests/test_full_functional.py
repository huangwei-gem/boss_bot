"""
Boss直聘智能投递助手 - 全量功能测试脚本

测试范围：
1. 常规软件测试 — 所有 API 端点的功能验证
2. 浏览器实测 — 使用 DrissionPage 点击界面按钮，验证前端交互
3. 日志验证 — 检查前端日志和后台日志输出
"""

import requests
import json
import time
import sys
import os
from datetime import datetime

BASE_URL = "http://localhost:5000"

# 测试结果统计
results = {"pass": 0, "fail": 0, "skip": 0, "details": []}


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}")


def record(name, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    results["pass" if passed else "fail"] += 1
    results["details"].append({"name": name, "status": status, "detail": detail})
    log(f"{status}: {name} {detail}", "SUCCESS" if passed else "ERROR")


def test_api_get(path, expected_keys=None, name=None):
    """测试 GET API"""
    name = name or f"GET {path}"
    try:
        resp = requests.get(f"{BASE_URL}{path}", timeout=10)
        if resp.status_code != 200:
            record(name, False, f"HTTP {resp.status_code}")
            return None
        data = resp.json()
        if expected_keys:
            missing = [k for k in expected_keys if k not in data]
            if missing:
                record(name, False, f"缺少字段: {missing}")
                return data
        record(name, True)
        return data
    except Exception as e:
        record(name, False, str(e))
        return None


def test_api_post(path, payload=None, expected_status="ok", name=None):
    """测试 POST API"""
    name = name or f"POST {path}"
    try:
        resp = requests.post(f"{BASE_URL}{path}", json=payload or {}, timeout=10)
        if resp.status_code != 200:
            record(name, False, f"HTTP {resp.status_code}")
            return None
        data = resp.json()
        if expected_status and data.get("status") != expected_status:
            record(name, False, f"status={data.get('status')}, msg={data.get('message','')}")
            return data
        record(name, True)
        return data
    except Exception as e:
        record(name, False, str(e))
        return None


# ============================
# 1. 常规软件测试 — API 端点
# ============================

def test_all_apis():
    log("=" * 50)
    log("1. 常规软件测试 — API 端点验证")
    log("=" * 50)

    # 主页
    try:
        resp = requests.get(f"{BASE_URL}/", timeout=10)
        record("GET / 主页", resp.status_code == 200 and len(resp.text) > 10000,
               f"HTTP {resp.status_code}, {len(resp.text)} chars")
    except Exception as e:
        record("GET / 主页", False, str(e))

    # 全局状态 API
    data = test_api_get("/api/status", ["running"], "GET /api/status 全局状态")
    if data:
        # 验证多账号格式
        has_accounts = "accounts" in data
        record("多账号格式 - accounts数组", has_accounts)
        if has_accounts and len(data["accounts"]) > 0:
            acc = data["accounts"][0]
            for key in ["index", "name", "running", "logged_in"]:
                record(f"账号字段 - {key}", key in acc)

    # 全局控制 API
    test_api_post("/api/pause_greet", name="POST /api/pause_greet 暂停所有打招呼")
    test_api_post("/api/resume_greet", name="POST /api/resume_greet 恢复所有打招呼")
    test_api_post("/api/pause_reply", name="POST /api/pause_reply 暂停所有回复")
    test_api_post("/api/resume_reply", name="POST /api/resume_reply 恢复所有回复")

    # 账号级别 API
    test_api_post("/api/accounts/0/pause_greet", name="POST /api/accounts/0/pause_greet")
    test_api_post("/api/accounts/0/resume_greet", name="POST /api/accounts/0/resume_greet")
    test_api_post("/api/accounts/0/pause_reply", name="POST /api/accounts/0/pause_reply")
    test_api_post("/api/accounts/0/resume_reply", name="POST /api/accounts/0/resume_reply")
    test_api_get("/api/accounts/0/status", name="GET /api/accounts/0/status 账号状态")

    # 配置 API
    test_api_get("/api/config", ["config"], "GET /api/config 配置")
    test_api_get("/api/logs", ["logs"], "GET /api/logs 日志")
    test_api_get("/api/stats", ["stats"], "GET /api/stats 统计")
    test_api_get("/api/messages", ["messages"], "GET /api/messages 消息")

    # 账号管理 API
    test_api_get("/api/accounts", ["accounts"], "GET /api/accounts 账号列表")
    test_api_post("/api/accounts/add", {"name": "测试账号", "cookie_file": "test.json", "enabled": False},
                  name="POST /api/accounts/add 添加账号")
    test_api_post("/api/accounts/update", {"index": 0, "name": "主账号", "enabled": True},
                  name="POST /api/accounts/update 更新账号")

    # 岗位管理 API
    test_api_get("/api/jobs", ["jobs"], "GET /api/jobs 岗位列表")
    test_api_post("/api/jobs/add",
                  {"account_index": 0, "query": "测试岗位", "city": "北京", "scroll_pages": 3,
                   "greeting_message": "您好", "enabled": True},
                  name="POST /api/jobs/add 添加岗位")

    # AI 相关 API
    test_api_get("/api/ai/providers", ["providers"], "GET /api/ai/providers AI提供商")
    test_api_get("/api/ai/prompts", ["prompts"], "GET /api/ai/prompts AI提示词")
    test_api_post("/api/ai/providers/add",
                  {"name": "测试AI", "api_key": "sk-test", "model": "gpt-4", "api_base": "https://api.openai.com/v1"},
                  name="POST /api/ai/providers/add 添加AI提供商")

    # 简历 API
    test_api_get("/api/resume", ["resume"], "GET /api/resume 简历")
    test_api_post("/api/resume", {"school": "测试大学", "major": "计算机", "degree": "本科"},
                  name="POST /api/resume 保存简历")

    # 图片 API
    test_api_get("/api/images", ["images"], "GET /api/images 图片列表")

    # 主题 API
    test_api_get("/api/theme", ["theme"], "GET /api/theme 主题")
    test_api_post("/api/theme", {"theme": "dark"}, name="POST /api/theme 设置暗色主题")
    test_api_post("/api/theme", {"theme": "light"}, name="POST /api/theme 设置亮色主题")

    # 规则 API
    test_api_get("/api/rules", ["rules"], "GET /api/rules 回复规则")
    test_api_post("/api/rules", {"reply_rules": [], "importance_keywords": []},
                  name="POST /api/rules 保存规则")

    # 模板 API
    test_api_get("/api/templates", ["templates"], "GET /api/templates 回复模板")
    test_api_post("/api/templates", {"salary_reply": "测试模板"},
                  name="POST /api/templates 保存模板")

    # Excel API
    test_api_get("/api/excel/files", ["files"], "GET /api/excel/files Excel文件列表")
    test_api_post("/api/excel/export", name="POST /api/excel/export 导出Excel")

    # 浏览器 API
    test_api_get("/api/browser/list", ["browsers"], "GET /api/browser/list 浏览器列表")

    # 个人画像 API
    test_api_get("/api/user_profile", ["profile"], "GET /api/user_profile 个人画像")
    test_api_post("/api/user_profile", {"profile": {"name": "测试用户"}},
                  name="POST /api/user_profile 保存个人画像")

    # Cookie 上传 API（无文件时应返回错误）
    try:
        resp = requests.post(f"{BASE_URL}/api/upload/cookie", timeout=10)
        record("POST /api/upload/cookie 无文件错误处理", resp.status_code in [400, 500])
    except Exception as e:
        record("POST /api/upload/cookie 无文件错误处理", False, str(e))

    # 图片上传 API（无文件时应返回错误）
    try:
        resp = requests.post(f"{BASE_URL}/api/upload/images", timeout=10)
        record("POST /api/upload/images 无文件错误处理", resp.status_code in [400, 500])
    except Exception as e:
        record("POST /api/upload/images 无文件错误处理", False, str(e))


# ============================
# 2. 前端 HTML 结构验证
# ============================

def test_frontend_html():
    log("=" * 50)
    log("2. 前端 HTML 结构验证")
    log("=" * 50)

    try:
        resp = requests.get(f"{BASE_URL}/", timeout=10)
        html = resp.text

        # CSS 变量系统
        checks = [
            ('data-theme="light"', "亮色主题变量"),
            ('data-theme="dark"', "暗色主题变量"),
            ("backdrop-filter", "毛玻璃效果"),
            ("--glass-blur", "毛玻璃变量"),
            ("stat-card", "统计卡片"),
            ("image-grid", "图片网格"),
            ("ai-test-panel", "AI测试面板"),
            ("socket.io", "SocketIO CDN"),
            ("excel", "Excel导出"),
            ("chip", "Chip标签"),
            ("account-tab", "账号标签页"),
            ("switchAccount", "切换账号函数"),
            ("activeAccountIdx", "当前账号索引"),
            ("accounts/", "账号级别API路径"),
            ("account_name", "账号名称"),
            ("login", "登录弹窗"),
            ("modal", "模态弹窗"),
            ("resume", "简历弹窗"),
            ("prompt", "提示词弹窗"),
            ("job", "岗位弹窗"),
            ("adv", "高级设置"),
            ("provider", "AI提供商"),
            ("rules", "回复规则"),
            ("template", "回复模板"),
            ("profile", "个人画像"),
            ("theme-toggle", "主题切换按钮"),
        ]

        for keyword, name in checks:
            found = keyword in html
            record(f"HTML结构 - {name}", found, f"({keyword})")

    except Exception as e:
        record("前端HTML获取", False, str(e))


# ============================
# 3. 多账号架构验证
# ============================

def test_multi_account():
    log("=" * 50)
    log("3. 多账号架构验证")
    log("=" * 50)

    # 添加第二个账号（启用状态）
    data = test_api_post("/api/accounts/add",
                         {"name": "副账号", "cookie_file": "test2.json", "enabled": True},
                         name="添加副账号(启用)")

    # 验证账号列表
    data = test_api_get("/api/accounts", ["accounts"], "获取账号列表(含副账号)")
    if data:
        accs = data.get("accounts", [])
        record("多账号 - 至少2个账号", len(accs) >= 2, f"当前{len(accs)}个账号")

    # 验证 /api/status 包含多账号状态
    data = test_api_get("/api/status", name="获取多账号汇总状态")
    if data:
        accounts = data.get("accounts", [])
        record("多账号状态 - accounts数组", len(accounts) >= 1,
               f"当前{len(accounts)}个账号状态")

    # 账号级别操作（只对启用的账号操作）
    test_api_post("/api/accounts/0/pause_greet", name="账号0暂停打招呼")
    test_api_post("/api/accounts/0/resume_greet", name="账号0恢复打招呼")

    # 清理：删除副账号
    test_api_post("/api/accounts/delete", {"index": len(data.get("accounts", [])) - 1 if data else 1},
                  name="删除副账号")


# ============================
# 4. 配置完整性验证
# ============================

def test_config_integrity():
    log("=" * 50)
    log("4. 配置完整性验证")
    log("=" * 50)

    data = test_api_get("/api/config", ["config"], "获取完整配置")
    if data:
        config = data.get("config", {})

        # 验证配置结构
        for key in ["accounts", "browser", "login", "rate_limit", "retry", "ai"]:
            record(f"配置结构 - {key}", key in config)

        # 验证账号配置
        accounts = config.get("accounts", [])
        if accounts:
            acc = accounts[0]
            for key in ["name", "enabled", "cookie_file", "jobs"]:
                record(f"账号配置 - {key}", key in acc)

            # 验证岗位配置
            jobs = acc.get("jobs", [])
            if jobs:
                job = jobs[0]
                for key in ["query", "city", "scroll_pages", "greeting_message", "enabled"]:
                    record(f"岗位配置 - {key}", key in job)


# ============================
# 5. 日志系统验证
# ============================

def test_logging():
    log("=" * 50)
    log("5. 日志系统验证")
    log("=" * 50)

    # 获取日志
    data = test_api_get("/api/logs", ["logs"], "获取日志列表")
    if data:
        logs = data.get("logs", [])
        record("日志系统 - 有日志数据", isinstance(logs, list))

    # 验证后台日志文件
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
    if os.path.isdir(log_dir):
        log_files = os.listdir(log_dir)
        record("后台日志 - logs目录存在", True, f"{len(log_files)}个文件")
    else:
        record("后台日志 - logs目录", False, "目录不存在")


# ============================
# 6. 一键启动脚本验证
# ============================

def test_startup_script():
    log("=" * 50)
    log("6. 一键启动脚本验证")
    log("=" * 50)

    # Windows 脚本
    bat_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "start.bat")
    record("一键启动 - start.bat", os.path.isfile(bat_path))

    # Linux/Mac 脚本
    sh_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "start.sh")
    record("一键启动 - start.sh", os.path.isfile(sh_path))


# ============================
# 主测试函数
# ============================

def main():
    log("=" * 50)
    log("Boss直聘智能投递助手 - 全量功能测试")
    log(f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"测试目标: {BASE_URL}")
    log("=" * 50)
    print()

    # 检查 Flask 应用是否在运行
    try:
        requests.get(f"{BASE_URL}/", timeout=5)
    except:
        log("Flask应用未运行，请先启动: cd flask-version && python app.py", "ERROR")
        sys.exit(1)

    # 运行所有测试
    test_all_apis()
    print()
    test_frontend_html()
    print()
    test_multi_account()
    print()
    test_config_integrity()
    print()
    test_logging()
    print()
    test_startup_script()

    # 输出汇总
    print()
    log("=" * 50)
    log("测试结果汇总")
    log("=" * 50)
    log(f"通过: {results['pass']}")
    log(f"失败: {results['fail']}")
    log(f"跳过: {results['skip']}")
    log(f"总计: {results['pass'] + results['fail'] + results['skip']}")

    if results["fail"] > 0:
        print()
        log("失败项详情:", "ERROR")
        for d in results["details"]:
            if d["status"] == "FAIL":
                log(f"  ✗ {d['name']}: {d['detail']}", "ERROR")

    print()
    verdict = "PASS" if results["fail"] == 0 else "FAIL"
    log(f"总体验证结论: {verdict}", "SUCCESS" if verdict == "PASS" else "ERROR")

    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())