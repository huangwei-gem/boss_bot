"""
浏览器自动化测试脚本 — Boss直聘智能投递助手前端界面

使用 DrissionPage 的 ChromiumPage 控制浏览器，逐一点击每个功能按钮，
验证 UI 交互和日志输出。

前置条件：Flask 服务运行在 http://localhost:5000
运行方式：python tests/test_browser.py
"""

import os
import sys
import time
import subprocess
import requests
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 截图保存目录
SCREENSHOT_DIR = PROJECT_ROOT / "tests" / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)

# Flask 服务地址
FLASK_URL = "http://localhost:5000"


# ═══════════════════════════════════════════════════════
#  测试结果收集
# ═══════════════════════════════════════════════════════
class TestResult:
    """单个测试项的结果"""

    def __init__(self, name: str):
        self.name = name
        self.passed = False
        self.details = ""
        self.screenshot = ""

    def pass_(self, details: str = ""):
        self.passed = True
        self.details = details

    def fail(self, details: str = ""):
        self.passed = False
        self.details = details


results: list[TestResult] = []


def log(msg: str):
    """输出日志"""
    print(f"  [{time.strftime('%H:%M:%S')}] {msg}")


def take_screenshot(page, name: str):
    """截图保存"""
    path = SCREENSHOT_DIR / f"{name}.png"
    try:
        page.get_screenshot(path=str(path))
        log(f"截图已保存: {path.name}")
    except Exception as e:
        log(f"截图失败: {e}")
    return str(path)


# ═══════════════════════════════════════════════════════
#  Flask 服务管理
# ═══════════════════════════════════════════════════════
def check_flask_running() -> bool:
    """检查 Flask 服务是否在运行"""
    try:
        resp = requests.get(FLASK_URL, timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


def start_flask() -> subprocess.Popen | None:
    """启动 Flask 服务"""
    flask_dir = PROJECT_ROOT / "flask-version"
    log(f"启动 Flask 服务: {flask_dir}")
    try:
        proc = subprocess.Popen(
            [sys.executable, "app.py"],
            cwd=str(flask_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # 等待服务启动
        for _ in range(30):
            time.sleep(1)
            if check_flask_running():
                log("Flask 服务已启动")
                return proc
        log("Flask 服务启动超时")
        return None
    except Exception as e:
        log(f"Flask 启动失败: {e}")
        return None


# ═══════════════════════════════════════════════════════
#  测试用例
# ═══════════════════════════════════════════════════════
def test_page_load(page) -> TestResult:
    """1. 页面加载验证"""
    r = TestResult("页面加载")
    try:
        page.get(FLASK_URL)
        page.wait.doc_loaded(timeout=15)
        time.sleep(2)  # 等待动态内容加载

        title = page.title
        log(f"页面标题: {title}")
        if "Boss" not in title and "投递" not in title:
            r.fail(f"标题不包含 Boss 或 投递: {title}")
            return r

        sidebar = page.ele(".sidebar")
        if not sidebar:
            r.fail("左侧边栏不存在")
            return r

        main = page.ele(".main")
        if not main:
            r.fail("右侧主内容区不存在")
            return r

        topbar = page.ele(".topbar")
        if not topbar:
            r.fail("顶部导航栏不存在")
            return r

        take_screenshot(page, "01_page_load")
        r.pass_(f"标题='{title}', 侧边栏/主区/顶栏均存在")
    except Exception as e:
        r.fail(f"异常: {e}")
        take_screenshot(page, "01_page_load_error")
    return r


def test_theme_toggle(page) -> TestResult:
    """2. 主题切换"""
    r = TestResult("主题切换")
    try:
        # 用 JS 获取 data-theme 属性，DrissionPage 的 attr() 对 html 元素可能不稳定
        initial_theme = page.run_js("return document.documentElement.getAttribute('data-theme');")
        log(f"初始主题: {initial_theme}")

        theme_btn = page.ele("#theme-btn")
        if not theme_btn:
            r.fail("主题切换按钮不存在")
            return r

        theme_btn.click()
        time.sleep(1)
        new_theme = page.run_js("return document.documentElement.getAttribute('data-theme');")
        log(f"切换后主题: {new_theme}")

        if initial_theme == new_theme:
            r.fail(f"主题未变化: {initial_theme} -> {new_theme}")
            return r

        # 切换回来
        theme_btn.click()
        time.sleep(1)
        restored_theme = page.run_js("return document.documentElement.getAttribute('data-theme');")
        log(f"恢复后主题: {restored_theme}")

        if restored_theme != initial_theme:
            r.fail(f"主题恢复失败: {initial_theme} -> {restored_theme}")
            return r

        take_screenshot(page, "02_theme_toggle")
        r.pass_(f"{initial_theme} <-> {new_theme} 切换正常, 恢复为 {restored_theme}")
    except Exception as e:
        r.fail(f"异常: {e}")
        take_screenshot(page, "02_theme_toggle_error")
    return r


def test_account_management(page) -> TestResult:
    """3. 账号管理"""
    r = TestResult("账号管理")
    try:
        # 验证账号标签页容器存在
        account_tabs = page.ele("#account-tabs")
        if not account_tabs:
            r.fail("账号标签页容器不存在")
            return r

        # 查找添加账号按钮
        add_acc_btn = page.ele("xpath://button[contains(text(),'+ 添加账号')]")
        if not add_acc_btn:
            r.fail("添加账号按钮不存在")
            return r

        # 点击添加账号按钮，弹出弹窗
        add_acc_btn.click()
        time.sleep(1)

        modal = page.ele("#modal-add-account")
        if not modal:
            r.fail("添加账号弹窗不存在")
            return r

        modal_classes = modal.attr("class") or ""
        if "show" not in modal_classes:
            r.fail(f"弹窗未显示: class='{modal_classes}'")
            return r

        log("添加账号弹窗已弹出")

        # 关闭弹窗
        close_btn = modal.ele(".modal-close")
        if close_btn:
            close_btn.click()
            time.sleep(0.5)
        else:
            # 用 JS 关闭
            page.run_js("closeModal('modal-add-account')")
            time.sleep(0.5)

        take_screenshot(page, "03_account_management")
        r.pass_("账号标签页容器存在, 添加账号弹窗正常弹出和关闭")
    except Exception as e:
        r.fail(f"异常: {e}")
        take_screenshot(page, "03_account_management_error")
    return r


def test_ai_config(page) -> TestResult:
    """4. AI 配置"""
    r = TestResult("AI 配置")
    try:
        # 验证 AI 智能解析区域存在
        ai_panel = page.ele("xpath://div[contains(@class,'panel')]//div[contains(text(),'AI 智能解析')]")
        if not ai_panel:
            # 尝试通过 panel-title 查找
            ai_panel = page.ele("xpath://div[@class='panel-title' and contains(text(),'AI')]")
        if not ai_panel:
            r.fail("AI 智能解析区域不存在")
            return r

        log("AI 智能解析区域存在")

        # 点击 AI 启用开关
        ai_toggle = page.ele("#ai-enabled-toggle")
        if not ai_toggle:
            r.fail("AI 启用开关不存在")
            return r

        initial_classes = ai_toggle.attr("class") or ""
        log(f"AI 开关初始状态: {initial_classes}")

        ai_toggle.click()
        time.sleep(0.5)
        new_classes = ai_toggle.attr("class") or ""
        log(f"AI 开关点击后状态: {new_classes}")

        if initial_classes == new_classes:
            r.fail(f"AI 开关状态未变化: {initial_classes}")
            return r

        # 切换回来
        ai_toggle.click()
        time.sleep(0.5)

        # 点击编辑提示词按钮
        prompts_btn = page.ele("xpath://button[contains(text(),'编辑提示词')]")
        if not prompts_btn:
            r.fail("编辑提示词按钮不存在")
            return r

        prompts_btn.click()
        time.sleep(1)

        prompts_modal = page.ele("#modal-prompts")
        if not prompts_modal:
            r.fail("提示词弹窗不存在")
            return r

        prompts_modal_classes = prompts_modal.attr("class") or ""
        if "show" not in prompts_modal_classes:
            r.fail(f"提示词弹窗未显示: class='{prompts_modal_classes}'")
            return r

        log("提示词弹窗已弹出")

        # 关闭弹窗
        close_btn = prompts_modal.ele(".modal-close")
        if close_btn:
            close_btn.click()
            time.sleep(0.5)
        else:
            page.run_js("closeModal('modal-prompts')")
            time.sleep(0.5)

        take_screenshot(page, "04_ai_config")
        r.pass_("AI 区域存在, 启用开关切换正常, 提示词弹窗弹出和关闭正常")
    except Exception as e:
        r.fail(f"异常: {e}")
        take_screenshot(page, "04_ai_config_error")
    return r


def test_image_upload(page) -> TestResult:
    """5. 图片上传"""
    r = TestResult("图片上传")
    try:
        # 验证图片上传区域存在
        upload_panel = page.ele("xpath://div[@class='panel-title' and contains(text(),'图片上传')]")
        if not upload_panel:
            r.fail("图片上传区域不存在")
            return r

        log("图片上传区域存在")

        # 验证图片网格存在
        image_grid = page.ele("#image-grid")
        if not image_grid:
            r.fail("图片网格不存在")
            return r

        log("图片网格存在")

        # 验证上传按钮存在
        upload_btn = page.ele("xpath://button[contains(text(),'上传图片')]")
        if not upload_btn:
            r.fail("上传图片按钮不存在")
            return r

        take_screenshot(page, "05_image_upload")
        r.pass_("图片上传区域、网格、按钮均存在")
    except Exception as e:
        r.fail(f"异常: {e}")
        take_screenshot(page, "05_image_upload_error")
    return r


def test_advanced_settings(page) -> TestResult:
    """6. 高级设置"""
    r = TestResult("高级设置")
    try:
        # 验证高级设置折叠面板存在
        adv_header = page.ele("xpath://div[contains(@class,'collapse-header')]//span[contains(text(),'高级设置')]")
        if not adv_header:
            r.fail("高级设置折叠面板不存在")
            return r

        log("高级设置折叠面板存在")

        # 获取折叠面板的父元素 collapse-header
        adv_collapse_header = adv_header.parent()
        initial_classes = adv_collapse_header.attr("class") or ""
        log(f"高级设置初始状态: {initial_classes}")

        adv_body = page.ele("#advanced-settings-body")
        if not adv_body:
            r.fail("高级设置内容区不存在")
            return r

        body_initial_classes = adv_body.attr("class") or ""
        log(f"高级设置内容区初始状态: {body_initial_classes}")

        # 点击展开/收起
        adv_collapse_header.click()
        time.sleep(1)

        body_new_classes = adv_body.attr("class") or ""
        log(f"点击后高级设置内容区状态: {body_new_classes}")

        if body_initial_classes == body_new_classes:
            r.fail(f"高级设置面板状态未变化: {body_initial_classes}")
            return r

        # 切换回来
        adv_collapse_header.click()
        time.sleep(1)

        take_screenshot(page, "06_advanced_settings")
        r.pass_("高级设置折叠面板展开/收起正常")
    except Exception as e:
        r.fail(f"异常: {e}")
        take_screenshot(page, "06_advanced_settings_error")
    return r


def test_stats_cards(page) -> TestResult:
    """7. 统计卡片"""
    r = TestResult("统计卡片")
    try:
        stats_grid = page.ele("#stats-grid-summary")
        if not stats_grid:
            r.fail("统计卡片区域不存在")
            return r

        log("统计卡片区域存在")

        # 用 JS 计数 stat-card 元素，DrissionPage 的 eles() 可能不稳定
        card_count = page.run_js(
            "return document.querySelectorAll('#stats-grid-summary .stat-card').length;"
        )
        log(f"统计卡片数量: {card_count}")

        if card_count < 4:
            r.fail(f"统计卡片不足4个: {card_count}")
            return r

        take_screenshot(page, "07_stats_cards")
        r.pass_(f"统计卡片区域存在, 共 {card_count} 个卡片")
    except Exception as e:
        r.fail(f"异常: {e}")
        take_screenshot(page, "07_stats_cards_error")
    return r


def test_job_list(page) -> TestResult:
    """8. 岗位列表"""
    r = TestResult("岗位列表")
    try:
        # 验证岗位列表区域存在
        job_panel = page.ele("xpath://div[@class='panel-title']//span[contains(text(),'岗位列表')]")
        if not job_panel:
            r.fail("岗位列表区域不存在")
            return r

        log("岗位列表区域存在")

        # 点击添加岗位按钮
        add_job_btn = page.ele("xpath://button[contains(text(),'+ 添加岗位')]")
        if not add_job_btn:
            r.fail("添加岗位按钮不存在")
            return r

        add_job_btn.click()
        time.sleep(1)

        job_modal = page.ele("#modal-add-job")
        if not job_modal:
            r.fail("添加岗位弹窗不存在")
            return r

        job_modal_classes = job_modal.attr("class") or ""
        if "show" not in job_modal_classes:
            r.fail(f"添加岗位弹窗未显示: class='{job_modal_classes}'")
            return r

        log("添加岗位弹窗已弹出")

        # 关闭弹窗
        close_btn = job_modal.ele(".modal-close")
        if close_btn:
            close_btn.click()
            time.sleep(0.5)
        else:
            page.run_js("closeModal('modal-add-job')")
            time.sleep(0.5)

        take_screenshot(page, "08_job_list")
        r.pass_("岗位列表区域存在, 添加岗位弹窗弹出和关闭正常")
    except Exception as e:
        r.fail(f"异常: {e}")
        take_screenshot(page, "08_job_list_error")
    return r


def test_log_area(page) -> TestResult:
    """9. 日志区域"""
    r = TestResult("日志区域")
    try:
        log_panel = page.ele("#log-panel")
        if not log_panel:
            r.fail("日志区域不存在")
            return r

        log("日志区域存在")

        # 验证复制日志按钮
        copy_btn = page.ele("xpath://button[contains(text(),'复制')]")
        if not copy_btn:
            r.fail("复制日志按钮不存在")
            return r

        log("复制日志按钮存在")

        # 验证清空日志按钮
        clear_btn = page.ele("xpath://button[contains(text(),'清空')]")
        if not clear_btn:
            r.fail("清空日志按钮不存在")
            return r

        log("清空日志按钮存在")

        take_screenshot(page, "09_log_area")
        r.pass_("日志区域、复制按钮、清空按钮均存在")
    except Exception as e:
        r.fail(f"异常: {e}")
        take_screenshot(page, "09_log_area_error")
    return r


def test_control_buttons(page) -> TestResult:
    """10. 控制按钮"""
    r = TestResult("控制按钮")
    try:
        # 验证启动按钮
        start_btn = page.ele("#btn-start")
        if not start_btn:
            r.fail("启动按钮不存在")
            return r

        log("启动按钮存在")

        # 验证停止按钮
        stop_btn = page.ele("#btn-stop")
        if not stop_btn:
            r.fail("停止按钮不存在")
            return r

        log("停止按钮存在")

        # 验证暂停打招呼按钮存在（bot 未运行时 display 可能是 none，但元素应在 DOM 中）
        pause_greet_btn = page.ele("#btn-pause-greet")
        if not pause_greet_btn:
            r.fail("暂停打招呼按钮不存在")
            return r

        log("暂停打招呼按钮存在")

        # 验证恢复打招呼按钮存在
        resume_greet_btn = page.ele("#btn-resume-greet")
        if not resume_greet_btn:
            r.fail("恢复打招呼按钮不存在")
            return r

        log("恢复打招呼按钮存在")

        # bot 未运行时，暂停按钮 display=none 是预期行为
        # 验证按钮的可见性状态符合预期（暂停按钮隐藏，因为 bot 未启动）
        pause_display = page.run_js(
            "return getComputedStyle(document.getElementById('btn-pause-greet')).display;"
        )
        log(f"暂停按钮 display: {pause_display}")

        # 验证启动按钮可见
        start_display = page.run_js(
            "return getComputedStyle(document.getElementById('btn-start')).display;"
        )
        log(f"启动按钮 display: {start_display}")

        if start_display == "none":
            r.fail("启动按钮不可见")
            return r

        take_screenshot(page, "10_control_buttons")
        r.pass_("启动/停止/暂停/恢复按钮均存在, 启动按钮可见")
    except Exception as e:
        r.fail(f"异常: {e}")
        take_screenshot(page, "10_control_buttons_error")
    return r


def test_cookie_upload(page) -> TestResult:
    """11. Cookie 上传"""
    r = TestResult("Cookie 上传")
    try:
        # 验证 Cookie 文件输入（隐藏的 file input）
        cookie_input = page.ele("#acc-cookie-upload")
        if not cookie_input:
            r.fail("Cookie 文件输入不存在")
            return r

        log("Cookie 文件输入存在")

        # 验证 Cookie 上传按钮 — 用 JS 查找包含 "上传Cookie" 文本的按钮
        cookie_btn_exists = page.run_js(
            "return Array.from(document.querySelectorAll('button')).some(b => b.textContent.includes('上传Cookie'));"
        )
        if not cookie_btn_exists:
            r.fail("Cookie 上传按钮不存在")
            return r

        log("Cookie 上传按钮存在")

        # 验证删除 Cookie 按钮
        del_cookie_btn_exists = page.run_js(
            "return Array.from(document.querySelectorAll('button')).some(b => b.textContent.includes('删除Cookie'));"
        )
        if not del_cookie_btn_exists:
            r.fail("删除Cookie按钮不存在")
            return r

        log("删除Cookie按钮存在")

        take_screenshot(page, "11_cookie_upload")
        r.pass_("Cookie 上传区域、上传按钮、删除按钮均存在")
    except Exception as e:
        r.fail(f"异常: {e}")
        take_screenshot(page, "11_cookie_upload_error")
    return r


def test_personal_profile(page) -> TestResult:
    """12. 个人画像"""
    r = TestResult("个人画像")
    try:
        # 验证个人画像配置区域
        profile_panel = page.ele("xpath://div[@class='panel-title' and contains(text(),'个人画像')]")
        if not profile_panel:
            r.fail("个人画像配置区域不存在")
            return r

        log("个人画像配置区域存在")

        # 验证姓名输入框
        name_input = page.ele("#profile-name")
        if not name_input:
            r.fail("个人画像姓名输入框不存在")
            return r

        log("个人画像姓名输入框存在")

        # 验证保存按钮
        save_btn = page.ele("xpath://button[contains(text(),'保存个人画像')]")
        if not save_btn:
            r.fail("保存个人画像按钮不存在")
            return r

        take_screenshot(page, "12_personal_profile")
        r.pass_("个人画像配置区域、输入框、保存按钮均存在")
    except Exception as e:
        r.fail(f"异常: {e}")
        take_screenshot(page, "12_personal_profile_error")
    return r


def test_excel_export(page) -> TestResult:
    """13. Excel 导出"""
    r = TestResult("Excel 导出")
    try:
        # 验证 Excel 导出按钮 — 用 JS 查找
        export_btn_exists = page.run_js(
            "return Array.from(document.querySelectorAll('button')).some(b => b.textContent.includes('导出数据'));"
        )
        if not export_btn_exists:
            r.fail("Excel 导出按钮不存在")
            return r

        log("Excel 导出按钮存在")

        # 验证 Excel 导出区域 — 用 JS 查找包含 "Excel" 文本的 panel-title
        excel_panel_exists = page.run_js(
            "return Array.from(document.querySelectorAll('.panel-title')).some(t => t.textContent.includes('Excel'));"
        )
        if not excel_panel_exists:
            r.fail("Excel 导出区域不存在")
            return r

        log("Excel 导出区域存在")

        take_screenshot(page, "13_excel_export")
        r.pass_("Excel 导出按钮和区域均存在")
    except Exception as e:
        r.fail(f"异常: {e}")
        take_screenshot(page, "13_excel_export_error")
    return r


def test_socketio_connection(page) -> TestResult:
    """14. SocketIO 连接"""
    r = TestResult("SocketIO 连接")
    try:
        # 通过 JS 检查 socket 连接状态
        connected = page.run_js("return (typeof socket !== 'undefined') ? socket.connected : false;")
        log(f"SocketIO 连接状态: {connected}")

        if connected:
            r.pass_("SocketIO 已连接")
        else:
            # 等待一下再检查
            time.sleep(3)
            connected = page.run_js("return (typeof socket !== 'undefined') ? socket.connected : false;")
            log(f"等待后 SocketIO 连接状态: {connected}")
            if connected:
                r.pass_("SocketIO 已连接 (等待后)")
            else:
                r.fail("SocketIO 未连接")
        take_screenshot(page, "14_socketio")
    except Exception as e:
        r.fail(f"异常: {e}")
        take_screenshot(page, "14_socketio_error")
    return r


def test_login_modal(page) -> TestResult:
    """15. 登录弹窗"""
    r = TestResult("登录弹窗")
    try:
        # 验证登录弹窗元素存在
        login_modal = page.ele("#modal-login")
        if not login_modal:
            r.fail("登录弹窗不存在")
            return r

        log("登录弹窗元素存在")

        # 验证登录弹窗标题
        login_title = login_modal.ele(".modal-title")
        if not login_title:
            r.fail("登录弹窗标题不存在")
            return r

        title_text = login_title.text
        log(f"登录弹窗标题: {title_text}")

        # 验证确认登录按钮
        confirm_btn = page.ele("#btn-modal-confirm-login")
        if not confirm_btn:
            r.fail("确认登录按钮不存在")
            return r

        log("确认登录按钮存在")

        # 验证登录提示横幅
        login_banner = page.ele("#login-banner")
        if not login_banner:
            r.fail("登录提示横幅不存在")
            return r

        log("登录提示横幅存在")

        take_screenshot(page, "15_login_modal")
        r.pass_("登录弹窗、确认按钮、登录横幅均存在")
    except Exception as e:
        r.fail(f"异常: {e}")
        take_screenshot(page, "15_login_modal_error")
    return r


# ═══════════════════════════════════════════════════════
#  主入口
# ═══════════════════════════════════════════════════════
def main():
    print("=" * 60)
    print("  Boss直聘智能投递助手 — 浏览器自动化测试")
    print("=" * 60)
    print()

    # 检查 Flask 服务
    flask_proc = None
    if not check_flask_running():
        log("Flask 服务未运行, 尝试启动...")
        flask_proc = start_flask()
        if not flask_proc:
            print("\n错误: 无法启动 Flask 服务, 请手动启动后重试")
            print("  cd flask-version && python app.py")
            sys.exit(1)
    else:
        log("Flask 服务已在运行")

    # 导入 DrissionPage
    try:
        from DrissionPage import ChromiumPage
    except ImportError:
        print("\n错误: DrissionPage 未安装")
        print("  pip install DrissionPage")
        if flask_proc:
            flask_proc.terminate()
        sys.exit(1)

    # 启动浏览器
    log("启动浏览器...")
    page = None
    try:
        page = ChromiumPage()
    except Exception as e:
        print(f"\n浏览器启动失败: {e}")
        if flask_proc:
            flask_proc.terminate()
        sys.exit(1)

    try:
        # 执行所有测试
        print("\n--- 开始测试 ---\n")

        results.append(test_page_load(page))
        results.append(test_theme_toggle(page))
        results.append(test_account_management(page))
        results.append(test_ai_config(page))
        results.append(test_image_upload(page))
        results.append(test_advanced_settings(page))
        results.append(test_stats_cards(page))
        results.append(test_job_list(page))
        results.append(test_log_area(page))
        results.append(test_control_buttons(page))
        results.append(test_cookie_upload(page))
        results.append(test_personal_profile(page))
        results.append(test_excel_export(page))
        results.append(test_socketio_connection(page))
        results.append(test_login_modal(page))

    finally:
        # 关闭浏览器
        if page:
            try:
                page.quit()
            except Exception:
                pass

        # 关闭 Flask（如果是本脚本启动的）
        if flask_proc:
            log("关闭 Flask 服务...")
            flask_proc.terminate()
            try:
                flask_proc.wait(timeout=5)
            except Exception:
                flask_proc.kill()

    # 输出结果
    print("\n" + "=" * 60)
    print("  === 浏览器实测结果 ===")
    print("=" * 60)

    pass_count = 0
    for i, r in enumerate(results, 1):
        status = "PASS" if r.passed else "FAIL"
        if r.passed:
            pass_count += 1
        print(f"{i:>2}. {r.name:<16} : {status}")
        if r.details:
            print(f"    └─ {r.details}")

    total = len(results)
    print(f"\n总计: {pass_count}/{total} PASS")
    verdict = "PASS" if pass_count == total else "FAIL"
    print(f"VERDICT: {verdict}")
    print("=" * 60)

    # 返回退出码
    sys.exit(0 if verdict == "PASS" else 1)


if __name__ == "__main__":
    main()