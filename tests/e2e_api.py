"""
Boss直聘智能投递助手 - 全量功能测试脚本

测试范围：
1. 常规软件测试 — 所有 API 端点的功能验证
2. 前端 HTML 结构验证 — 直接 GET 页面文本
3. 日志/启动脚本验证

**这个脚本会往配置里加账号、加 AI 接口、改简历、清空规则词表，所以它绝对不许
打用户正在用的那台线上面板。** 2026-10-10 和 2026-10-20 两次都是它把真配置写脏的
（10-20 那次抹掉了 12 条重要关键词、薪资回复模板、真实学校/专业，还留下两个
"测试账号"）——它自己 POST 完不会还原，因为还原要靠的那一份已经被覆盖。
现在改成：抄一份配置到临时目录，在那里起一个自己的面板实例，状态文件、
数据目录、岗位池全部指向那份副本，跑完整体扔掉。

运行：python tests/e2e_api.py
"""

import requests
import json
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from tests.conftest import mk测试临时目录, 挑空闲端口  # noqa: E402
sys.path.insert(0, str(PROJECT_ROOT / "tests"))
from boss_bot.unified_config import 面板端口 as _面板端口  # 线上面板口只有一个出处

# 跑起来后由 起隔离实例() 填；留空是为了让任何一处漏改都当场炸在请求上，
# 而不是悄悄回落到线上面板
BASE_URL = ""
SANDBOX_PORT = int(os.environ.get("BOSS_API_TEST_PORT", "5079"))
# 跑起来后由 main() 填成沙箱目录；测试里凡是"要落盘的名字"都从这里取，
# 给裸名字会被 resolve_path 拼到仓库根（根目录那两个 test.json/test2.json 就是）
副本根目录 = None


def _沙箱名(标签: str) -> str:
    根 = Path(副本根目录 or PROJECT_ROOT)
    return str(根 / ("e2e_cookie_%s.json" % 标签))

LAUNCHER = """
import sys, pathlib
sys.path.insert(0, %(root)r)
sys.path.insert(0, %(flask)r)
import boss_bot.unified_config as UC
from boss_bot.unified_config import 面板端口 as _面板端口
import boss_bot.config as C
import boss_bot.job_pool as JP
import app as A
tmp = pathlib.Path(%(tmp)r)
UC.BOT_CONFIG_FILE = tmp / "bot_config.json"
UC.OVERRIDES_FILE = tmp / "config_overrides.json"
UC.USER_PROFILE_FILE = tmp / "user_profile.json"
# 暂停/恢复、统计、通知都写这几个文件：不顶掉就会动到线上那两份号的状态
C.STATE_FILE = tmp / "bot_state.json"
C.STATS_FILE = tmp / "bot_stats.json"
C.NOTIFY_FILE = tmp / "notifications.json"
# 导出 Excel、按号分开的记录与岗位池都从 DATA_DIR / resolve_path 走
A.DATA_DIR = tmp / "data"
JP.resolve_path = lambda p: tmp / "data"
A._ensure_config()
kwargs = {"host": "127.0.0.1", "port": %(port)d, "debug": False, "use_reloader": False}
if (getattr(A, "_socketio_kwargs", {}) or {}).get("async_mode") == "threading":
    kwargs["allow_unsafe_werkzeug"] = True
A.socketio.run(A.app, **kwargs)
"""

# 测试结果统计
results = {"pass": 0, "fail": 0, "skip": 0, "details": []}

# 沙箱要顶掉的三份用户数据：顶失败不会抛错，只会悄悄写进真文件，
# 所以跑前跑后各算一次哈希，变了就当红报出来（10-20 那次漏了 config_overrides，
# 重要关键词被清成 []、「收到重要消息就暂停」悄悄停了两天）
用户数据文件 = ("bot_config.json", "user_profile.json", "config_overrides.json")


def 快照用户数据():
    快照 = {}
    for 名 in 用户数据文件:
        p = PROJECT_ROOT / 名
        快照[名] = (hashlib.sha256(p.read_bytes()).hexdigest()[:16]
                    if p.is_file() else "不存在")
    return 快照


def 找变了(跑前, 跑后):
    return [f"{名} {跑前.get(名)}→{跑后.get(名)}" for 名 in 用户数据文件
            if 跑后.get(名) != 跑前.get(名)]




def 起隔离实例():
    """抄一份真配置到临时目录，在那儿起一个面板；返回 (进程, 临时目录, URL)。"""
    port = 挑空闲端口(SANDBOX_PORT, "全量接口实测")
    if port != SANDBOX_PORT:
        log(f"端口 {SANDBOX_PORT} 已被占用（多半是上一轮被强杀留下的临时面板），"
            f"这一趟改用 {port}，绝不连到别的实例上")
    tmp = mk测试临时目录("boss_api_suite")
    源 = PROJECT_ROOT / "bot_config.json"
    (tmp / "bot_config.json").write_text(
        源.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp / "data").mkdir(parents=True, exist_ok=True)
    script = LAUNCHER % {"root": str(PROJECT_ROOT),
                         "flask": str(PROJECT_ROOT / "flask-version"),
                         "tmp": str(tmp), "port": port}
    env = dict(os.environ, BOSS_BOT_CONFIG=str(tmp / "bot_config.json"), PYTHONUTF8="1")
    proc = subprocess.Popen([sys.executable, "-c", script], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    for _ in range(80):
        if proc.poll() is not None:
            raise RuntimeError("隔离实例自己退了，检查 LAUNCHER 里顶掉的路径")
        try:
            if requests.get(f"http://127.0.0.1:{port}/api/config", timeout=1).ok:
                return proc, tmp, f"http://127.0.0.1:{port}"
        except Exception:
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError("隔离实例 40 秒内没起来")


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
    test_api_post("/api/accounts/add", {"name": "测试账号", "cookie_file": _沙箱名("测试账号"),
                                        "enabled": False},
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

    # 个人画像 API —— 这个接口是整体覆盖写盘，先存原值、测完还原，
    # 否则跑一次 e2e 就把用户真实画像抹掉了
    try:
        _orig_profile = requests.get(f"{BASE_URL}/api/user_profile", timeout=10).json().get("profile")
    except Exception:
        _orig_profile = None
    test_api_get("/api/user_profile", ["profile"], "GET /api/user_profile 个人画像")
    test_api_post("/api/user_profile", {"profile": {"name": "测试用户"}},
                  name="POST /api/user_profile 保存个人画像")
    if isinstance(_orig_profile, dict) and _orig_profile:
        try:
            requests.post(f"{BASE_URL}/api/user_profile", json={"profile": _orig_profile},
                          timeout=15)
        except Exception as e:
            print(f"  [WARN] 个人画像还原失败，请手工检查 user_profile.json: {e}")

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
            ("loginAccount", "账号登录入口"),
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
                         {"name": "副账号", "cookie_file": _沙箱名("副账号"), "enabled": True},
                         name="添加副账号(启用)")

    # 验证账号列表
    data = test_api_get("/api/accounts", ["accounts"], "获取账号列表(含副账号)")
    if data:
        accs = data.get("accounts", [])
        record("多账号 - 至少2个账号", len(accs) >= 2, f"当前{len(accs)}个账号")

    # 验证 /api/status 的多账号行
    # 没启动时 accounts=[] 是这台面板的既有行为（要控制类接口才会建 manager），
    # 拿"至少 1 行"当判据会年年假红；running 为真时才要求逐个号有行
    data = test_api_get("/api/status", name="获取多账号汇总状态")
    if data:
        accounts = data.get("accounts", [])
        在跑 = bool(data.get("running"))
        record("多账号状态 - 跑着的时候每个号都要有一行",
               (not 在跑) or len(accounts) >= 2,
               f"running={在跑} 当前{len(accounts)}个账号状态"
               + ("" if 在跑 else "（没启动，按面板既有行为留空）"))

    # 账号级别操作（只对启用的账号操作）
    test_api_post("/api/accounts/0/pause_greet", name="账号0暂停打招呼")
    test_api_post("/api/accounts/0/resume_greet", name="账号0恢复打招呼")

    # 清理：把刚加进去那一颗删掉，并且回读确认真的没了
    加完 = test_api_get("/api/accounts", ["accounts"], "获取账号列表(加过副账号)") or {}
    全部 = 加完.get("accounts") or []
    尾索引 = len(全部) - 1
    删 = test_api_post("/api/accounts/delete", {"index": 尾索引}, name="删除副账号")
    if 删 and 删.get("status") == "ok":
        现在 = (test_api_get("/api/accounts", ["accounts"], "删除后回读账号列表") or {}).get("accounts") or []
        record("删除后账号数真的少一颗", len(现在) == len(全部) - 1,
               f"{len(全部)} → {len(现在)}")
    else:
        record("删除后账号数真的少一颗", False,
               f"删除就失败了：{(删 or {}).get('message', '没回话')}（索引 {尾索引}）")


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
    global BASE_URL, 副本根目录
    跑前 = 快照用户数据()
    try:
        进程, 副本目录, BASE_URL = 起隔离实例()
    except Exception as e:
        log(f"隔离实例起不来，这一趟不跑（绝不回落到线上面板）：{type(e).__name__}: {e}", "ERROR")
        return 1
    副本根目录 = 副本目录
    # 再钉一遍：这个脚本会写配置，任何情况下都不许指用户那台线上面板
    if (":%s" % _面板端口()) in BASE_URL:
        log(f"测试目标被指到了线上面板（{BASE_URL}），拒绝运行", "ERROR")
        return 1

    log("=" * 50)
    log("Boss直聘智能投递助手 - 全量功能测试")
    log(f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"测试目标: {BASE_URL}（配置副本在 {副本目录}）")
    log("=" * 50)
    print()

    try:
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
    finally:
        from boss_bot.platform_compat import 终止进程
        终止进程(进程.pid)
        shutil.rmtree(str(副本目录), ignore_errors=True)

    跑后 = 快照用户数据()
    变了 = 找变了(跑前, 跑后)
    record("这一趟没动用户那三份配置", not 变了,
           ("被写了: " + "; ".join(变了)) if 变了 else "跑前跑后三个哈希一个都没变")

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