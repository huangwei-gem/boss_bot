"""用破解浏览器cloakbrowser登录BOSS直聘，提取聊天页面和岗位详情页的完整CSS字段集合。

本脚本完成以下工作：
1. 启动cloakbrowser并加载cookies登录BOSS直聘
2. 提取聊天页面完整CSS选择器和字段（包括toolbar按钮、发简历按钮HTML、确认弹层HTML等）
3. 提取岗位详情页完整字段
4. 记录发送简历完整交互流程（点击发简历按钮 → 等待弹层 → 截图 → 点击确定 → 验证）
5. 保存所有信息到 tools/css_fields_full.json 和 tools/resume_send_flow.json
6. 截图保存到 logs/ 目录

输出文件:
- tools/css_fields_full.json  : CSS选择器集合
- tools/resume_send_flow.json: 发送简历完整交互流程
- logs/chat_page_full.png    : 聊天页面截图
- logs/job_detail_full.png   : 岗位详情页截图
- logs/resume_confirm_dialog.png : 发简历确认弹层截图
- logs/resume_after_send.png : 发送简历后页面截图
"""
import json
import os
import time
import traceback
from DrissionPage import ChromiumPage, ChromiumOptions

# 路径配置（基于本脚本所在目录）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
CLOAK_BROWSER = os.path.join(PROJECT_ROOT, "cloakbrowser", "chrome.exe")
COOKIE_FILE = os.path.join(PROJECT_ROOT, "zhipin_cookies.json")
OUTPUT_FILE = os.path.join(BASE_DIR, "css_fields_full.json")
RESUME_FLOW_FILE = os.path.join(BASE_DIR, "resume_send_flow.json")
LOGS_DIR = os.path.join(PROJECT_ROOT, "logs")

CHAT_URL = "https://www.zhipin.com/web/geek/chat"
HOME_URL = "https://www.zhipin.com/"

# 截图路径
CHAT_PAGE_SCREENSHOT = os.path.join(LOGS_DIR, "chat_page_full.png")
JOB_DETAIL_SCREENSHOT = os.path.join(LOGS_DIR, "job_detail_full.png")
RESUME_DIALOG_SCREENSHOT = os.path.join(LOGS_DIR, "resume_confirm_dialog.png")
RESUME_AFTER_SEND_SCREENSHOT = os.path.join(LOGS_DIR, "resume_after_send.png")


def build_browser():
    """启动破解浏览器并返回页面对象。"""
    print(f"[INFO] 破解浏览器路径: {CLOAK_BROWSER}")
    if not os.path.isfile(CLOAK_BROWSER):
        raise FileNotFoundError(f"未找到破解浏览器: {CLOAK_BROWSER}")

    co = ChromiumOptions()
    co.set_browser_path(CLOAK_BROWSER)
    co.auto_port()
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-gpu')
    co.set_argument('--no-first-run')
    co.set_argument('--no-default-browser-check')
    co.set_argument('--window-size=1280,900')
    page = ChromiumPage(co)
    print("[INFO] 浏览器启动成功")
    return page


def load_cookies(page):
    """加载cookies到浏览器。"""
    if not os.path.isfile(COOKIE_FILE):
        raise FileNotFoundError(f"未找到cookies文件: {COOKIE_FILE}")

    with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
        cookies = json.load(f)
    print(f"[INFO] 加载 {len(cookies)} 个Cookie")

    # 先访问主域，再设置cookie
    page.get(HOME_URL)
    time.sleep(3)

    ok, fail = 0, 0
    for c in cookies:
        try:
            page.set.cookies(c)
            ok += 1
        except Exception:
            fail += 1
    print(f"[INFO] Cookie设置完成: 成功={ok}, 失败={fail}")


def check_login(page):
    """检查登录状态，打印调试信息。"""
    url = page.url
    title = page.title
    print(f"[DEBUG] 当前URL: {url}")
    print(f"[DEBUG] 页面标题: {title}")
    if "login" in url.lower() or "passport" in url.lower():
        print("[WARN] 检测到登录页，可能登录失败！")
        return False
    return True


def take_screenshot(page, path, full_page=False):
    """安全截图，失败不抛异常。"""
    try:
        page.get_screenshot(path=path, full_page=full_page)
        if os.path.isfile(path):
            print(f"[OK] 截图已保存: {path}")
            return True
        print(f"[WARN] 截图调用完成但文件不存在: {path}")
        return False
    except Exception as e:
        print(f"[ERROR] 截图失败 {path}: {e}")
        return False


# ============================================================
# 聊天页面字段提取JS
# ============================================================
CHAT_PAGE_JS = r'''(
function() {
    var result = {};
    var seen = {};

    function trySelectors(selectors, key) {
        var hits = [];
        for (var i = 0; i < selectors.length; i++) {
            var s = selectors[i];
            try {
                var el = document.querySelector(s);
                if (el) {
                    var text = (el.textContent || '').trim();
                    hits.push({
                        selector: s,
                        tag: el.tagName.toLowerCase(),
                        class: el.className || '',
                        text: text.substring(0, 80),
                        visible: el.offsetParent !== null
                    });
                }
            } catch(e) {}
        }
        if (hits.length) result[key] = hits;
        return hits;
    }

    // 1. 聊天对象名称
    trySelectors([
        ".top-info-content .name-text",
        ".top-info-box .name-text",
        ".chat-header .name-text",
        ".user-info .name-text",
        ".header-content .name-text",
        ".top-info .name",
        ".boss-name",
        ".chat-title .name",
        ".name-text",
        ".name-box .name-text",
        ".friend-info .name-text",
        ".chat-friend .name"
    ], "chat_name");

    // 2. 输入框
    trySelectors([
        "#chat-input",
        ".chat-input",
        ".input-area",
        "textarea",
        "[contenteditable=true]",
        ".chat-input-area textarea",
        ".message-input textarea",
        ".edit-area",
        ".chat-footer textarea"
    ], "input_box");

    // 3. 发送按钮
    trySelectors([
        ".btn-send",
        ".btn-v2.btn-sure-v2.btn-send",
        ".send-message",
        ".chat-send",
        "button[type=submit]",
        ".btn-sure-v2",
        ".btn.btn-send",
        "[class*='send']"
    ], "send_button");

    // 4. 岗位名称
    trySelectors([
        ".chat-position-content .position-content",
        ".job-info .job-name",
        ".chat-header .job-name",
        ".position-info .position-name",
        ".job-title",
        ".position-content",
        ".job-name",
        ".chat-position .position-name"
    ], "job_name");

    // 5. 会话列表项
    trySelectors([
        ".friend-content",
        ".friend-item",
        ".chat-friend",
        ".friend-list li",
        ".friend-list-item",
        "[class*='friend']",
        ".chat-list li",
        ".user-list li"
    ], "friend_item");

    // 6. 发简历按钮
    trySelectors([
        ".btn-send-resume",
        ".send-resume",
        ".btn-resume",
        "[class*='resume']",
        ".btn-v2.btn-send-resume",
        ".btn.btn-resume",
        ".toolbar-btn .btn-resume",
        ".chat-toolbar .btn-resume"
    ], "send_resume_btn");

    // 7. 确认弹层
    trySelectors([
        ".dialog-content",
        ".modal-content",
        ".confirm-dialog",
        ".dialog-box",
        ".layer-content",
        ".popup-content",
        ".dialog-wrap",
        ".boss-dialog",
        ".upload-resume-dialog"
    ], "confirm_dialog");

    // 8. 聊天消息列表
    trySelectors([
        ".chat-message-list",
        ".message-list",
        ".chat-content",
        ".chat-messages",
        ".message-content",
        ".chat-box",
        ".chat-history",
        ".message-wrap"
    ], "chat_message_list");

    // 9. 公司名称
    trySelectors([
        ".company-name",
        ".friend-company",
        ".company-info .name",
        ".friend-info .company",
        ".name-box .company",
        ".title-box .company",
        "[class*='company']"
    ], "company_name");

    // 10. 所有toolbar按钮的文本和class
    var toolbarBtns = [];
    var btnCandidates = document.querySelectorAll(
        ".chat-toolbar button, .toolbar button, .tool-bar button, " +
        ".chat-tools button, .chat-toolbar a, .toolbar a, " +
        "[class*='toolbar'] button, [class*='toolbar'] a, " +
        "[class*='tool-bar'] button, [class*='tool-bar'] a, " +
        ".chat-header button, .chat-header a, " +
        ".top-info-content button, .top-info-content a, " +
        "[class*='toolbar'] div, .chat-toolbar div"
    );
    for (var i = 0; i < btnCandidates.length; i++) {
        var b = btnCandidates[i];
        var t = (b.textContent || '').trim();
        if (t && t.length < 30) {
            toolbarBtns.push({
                tag: b.tagName.toLowerCase(),
                class: (b.className || '').substring(0, 120),
                text: t.substring(0, 30),
                outerHTML: b.outerHTML.substring(0, 300)
            });
        }
    }
    // 兜底：抓取所有可见按钮
    if (toolbarBtns.length === 0) {
        var allBtns = document.querySelectorAll("button, a.btn, [class*='btn']");
        for (var j = 0; j < Math.min(allBtns.length, 50); j++) {
            var b2 = allBtns[j];
            var t2 = (b2.textContent || '').trim();
            if (t2 && t2.length < 30 && b2.offsetParent !== null) {
                toolbarBtns.push({
                    tag: b2.tagName.toLowerCase(),
                    class: (b2.className || '').substring(0, 120),
                    text: t2.substring(0, 30),
                    outerHTML: b2.outerHTML.substring(0, 300)
                });
            }
        }
    }
    result.toolbar_buttons = toolbarBtns;

    // 11. 发简历按钮的完整HTML结构
    var resumeBtnHTML = [];
    var resumeBtns = document.querySelectorAll(
        "[class*='resume'], .btn-send-resume, .send-resume, .btn-resume, " +
        ".toolbar-btn .btn-resume, .chat-toolbar .btn-resume"
    );
    for (var r = 0; r < resumeBtns.length; r++) {
        var rb = resumeBtns[r];
        resumeBtnHTML.push({
            tag: rb.tagName.toLowerCase(),
            class: (rb.className || '').substring(0, 120),
            text: (rb.textContent || '').trim().substring(0, 50),
            outerHTML: rb.outerHTML.substring(0, 800),
            parentHTML: rb.parentElement ? rb.parentElement.outerHTML.substring(0, 800) : ''
        });
    }
    result.send_resume_btn_html = resumeBtnHTML;

    // 12. 确认弹层的完整HTML结构（如果存在）
    var dialogHTML = [];
    var dialogs = document.querySelectorAll(
        ".dialog-wrap, .dialog-content, .modal-content, .confirm-dialog, " +
        ".dialog-box, .layer-content, .popup-content, .upload-resume-dialog"
    );
    for (var d = 0; d < dialogs.length; d++) {
        var dlg = dialogs[d];
        dialogHTML.push({
            tag: dlg.tagName.toLowerCase(),
            class: (dlg.className || '').substring(0, 120),
            visible: dlg.offsetParent !== null,
            outerHTML: dlg.outerHTML.substring(0, 1500)
        });
    }
    result.confirm_dialog_html = dialogHTML;

    // 13. 聊天消息列表中每条消息的HTML结构
    var messageItems = [];
    var msgSelectors = [
        ".chat-message-list .message-item",
        ".message-list .message-item",
        ".chat-content .message-item",
        ".chat-content li",
        ".chat-messages li",
        ".message-content li",
        ".chat-history li",
        ".message-wrap li",
        ".chat-content > div",
        ".message-content > div"
    ];
    var msgList = null;
    for (var ms = 0; ms < msgSelectors.length; ms++) {
        var found = document.querySelectorAll(msgSelectors[ms]);
        if (found.length > 0) {
            msgList = found;
            result.message_list_selector_used = msgSelectors[ms];
            break;
        }
    }
    if (msgList) {
        for (var mi = 0; mi < Math.min(msgList.length, 5); mi++) {
            var m = msgList[mi];
            messageItems.push({
                tag: m.tagName.toLowerCase(),
                class: (m.className || '').substring(0, 120),
                text: (m.textContent || '').trim().substring(0, 100),
                outerHTML: m.outerHTML.substring(0, 800)
            });
        }
    }
    result.chat_message_items = messageItems;

    // 14. 额外采集所有含name/company/position的候选元素（便于扩展备选选择器）
    var candidates = [];
    var allEls = document.querySelectorAll(
        "[class*='name'], [class*='title'], [class*='company'], " +
        "[class*='position'], [class*='job'], [class*='user']"
    );
    for (var k = 0; k < Math.min(allEls.length, 60); k++) {
        var el = allEls[k];
        var txt = (el.textContent || '').trim();
        if (txt && txt.length < 60) {
            candidates.push({
                tag: el.tagName.toLowerCase(),
                class: (el.className || '').substring(0, 80),
                text: txt.substring(0, 50)
            });
        }
    }
    result.element_candidates = candidates;

    return JSON.stringify(result);
}
)()'''


# ============================================================
# 岗位详情页字段提取JS
# ============================================================
JOB_DETAIL_JS = r'''(
function() {
    var result = {};

    function trySelectors(selectors, key) {
        var hits = [];
        for (var i = 0; i < selectors.length; i++) {
            var s = selectors[i];
            try {
                var el = document.querySelector(s);
                if (el) {
                    var text = (el.textContent || '').trim();
                    hits.push({
                        selector: s,
                        tag: el.tagName.toLowerCase(),
                        class: el.className || '',
                        text: text.substring(0, 100),
                        visible: el.offsetParent !== null
                    });
                }
            } catch(e) {}
        }
        if (hits.length) result[key] = hits;
        return hits;
    }

    // 1. 沟通按钮
    trySelectors([
        ".btn.btn-startchat",
        ".btn-startchat",
        ".btn-start-chat",
        ".btn.btn-start-chat",
        "[class*='startchat']",
        "[class*='start-chat']",
        ".btn-chat",
        ".btn-greet",
        ".btn.btn-chat",
        ".btn-greet.btn-startchat",
        ".job-detail .btn-startchat"
    ], "chat_button");

    // 2. 岗位名称
    trySelectors([
        ".job-title .name",
        ".job-name",
        ".name h1",
        ".job-title",
        ".position-title",
        ".job-detail .name",
        ".info-primary .name",
        "h1.name",
        ".job-banner .name",
        ".job-detail .job-title"
    ], "job_name");

    // 3. 薪资
    trySelectors([
        ".job-title .salary",
        ".salary",
        ".job-salary",
        ".info-primary .salary",
        ".job-detail .salary",
        ".banner-salary",
        "[class*='salary']"
    ], "salary");

    // 4. 公司名称
    trySelectors([
        ".company-info .name",
        ".company-name",
        ".job-detail .company-name",
        ".business-info .name",
        ".company-info .company-name",
        "[class*='company-info'] [class*='name']"
    ], "company_name");

    // 5. 岗位描述
    trySelectors([
        ".job-sec-text",
        ".job-detail-section .text",
        ".job-description",
        ".job-content",
        ".job-sec .text",
        "[class*='job-sec-text']",
        ".detail-text"
    ], "job_description");

    // 6. 岗位要求（关键字段）
    trySelectors([
        ".job-keywords",
        ".job-tags",
        ".tag-list",
        ".job-detail .tag-list",
        ".job-sec .job-keywords",
        ".keywords"
    ], "job_keywords");

    // 7. 工作地点
    trySelectors([
        ".info-primary .text-city",
        ".text-city",
        ".job-title .job-area",
        ".job-area",
        ".info-primary .job-area",
        ".job-detail .job-area",
        ".location-address",
        ".job-location",
        ".info-primary p",
        "[class*='job-area']",
        ".location"
    ], "job_area");

    // 8. 沟通按钮完整HTML
    var chatBtnHTML = [];
    var chatBtns = document.querySelectorAll(
        ".btn-startchat, .btn-start-chat, [class*='startchat'], [class*='start-chat'], " +
        ".btn-chat, .btn-greet, .btn.btn-chat"
    );
    for (var cb = 0; cb < chatBtns.length; cb++) {
        var cbtn = chatBtns[cb];
        chatBtnHTML.push({
            tag: cbtn.tagName.toLowerCase(),
            class: (cbtn.className || '').substring(0, 120),
            text: (cbtn.textContent || '').trim().substring(0, 50),
            outerHTML: cbtn.outerHTML.substring(0, 500)
        });
    }
    result.chat_button_html = chatBtnHTML;

    // 额外：采集所有btn候选，便于扩展沟通按钮备选
    var btnCandidates = [];
    var allBtns = document.querySelectorAll("button, a.btn, [class*='btn'], [class*='chat'], [class*='greet']");
    for (var i = 0; i < Math.min(allBtns.length, 40); i++) {
        var b = allBtns[i];
        var t = (b.textContent || '').trim();
        if (t && t.length < 30 && b.offsetParent !== null) {
            btnCandidates.push({
                tag: b.tagName.toLowerCase(),
                class: (b.className || '').substring(0, 80),
                text: t.substring(0, 30)
            });
        }
    }
    result.button_candidates = btnCandidates;

    return JSON.stringify(result);
}
)()'''


# ============================================================
# 确认弹层提取JS（点击发简历按钮后调用）
# ============================================================
DIALOG_EXTRACT_JS = r'''(
function() {
    var result = {found: false, dialogs: [], confirm_btn: null, cancel_btn: null};

    // 查找所有可能的弹层
    var dialogSelectors = [
        ".dialog-wrap", ".dialog-content", ".modal-content",
        ".confirm-dialog", ".dialog-box", ".layer-content",
        ".popup-content", ".upload-resume-dialog", ".v-transfer-dom"
    ];
    for (var i = 0; i < dialogSelectors.length; i++) {
        var dlgs = document.querySelectorAll(dialogSelectors[i]);
        for (var j = 0; j < dlgs.length; j++) {
            var d = dlgs[j];
            if (d.offsetParent !== null || d.style.display !== 'none') {
                result.found = true;
                result.dialogs.push({
                    selector: dialogSelectors[i],
                    tag: d.tagName.toLowerCase(),
                    class: (d.className || '').substring(0, 150),
                    visible: d.offsetParent !== null,
                    text: (d.textContent || '').trim().substring(0, 200),
                    outerHTML: d.outerHTML.substring(0, 2000)
                });
            }
        }
    }

    // 查找确定按钮
    var confirmSelectors = [
        ".btn-sure-v2", ".btn-confirm", ".btn-ok", ".btn-primary",
        ".dialog-wrap .btn-sure-v2", ".dialog-content .btn-sure-v2",
        ".upload-resume-dialog .btn-sure-v2",
        "button[class*='sure']", "button[class*='confirm']",
        "a[class*='sure']", "a[class*='confirm']",
        ".btn.btn-primary", ".btn.btn-sure"
    ];
    for (var c = 0; c < confirmSelectors.length; c++) {
        var cbtns = document.querySelectorAll(confirmSelectors[c]);
        for (var cn = 0; cn < cbtns.length; cn++) {
            var cb = cbtns[cn];
            if (cb.offsetParent !== null) {
                result.confirm_btn = {
                    selector: confirmSelectors[c],
                    tag: cb.tagName.toLowerCase(),
                    class: (cb.className || '').substring(0, 120),
                    text: (cb.textContent || '').trim().substring(0, 30),
                    outerHTML: cb.outerHTML.substring(0, 400)
                };
                break;
            }
        }
        if (result.confirm_btn) break;
    }

    // 查找取消按钮
    var cancelSelectors = [
        ".btn-cancel", ".btn-close", ".btn-default",
        ".dialog-wrap .btn-cancel", ".dialog-content .btn-cancel",
        ".upload-resume-dialog .btn-cancel",
        "button[class*='cancel']", "button[class*='close']",
        "a[class*='cancel']", "a[class*='close']",
        ".btn.btn-default", ".btn.btn-close"
    ];
    for (var x = 0; x < cancelSelectors.length; x++) {
        var xbtns = document.querySelectorAll(cancelSelectors[x]);
        for (var xn = 0; xn < xbtns.length; xn++) {
            var xb = xbtns[xn];
            if (xb.offsetParent !== null) {
                result.cancel_btn = {
                    selector: cancelSelectors[x],
                    tag: xb.tagName.toLowerCase(),
                    class: (xb.className || '').substring(0, 120),
                    text: (xb.textContent || '').trim().substring(0, 30),
                    outerHTML: xb.outerHTML.substring(0, 400)
                };
                break;
            }
        }
        if (result.cancel_btn) break;
    }

    return JSON.stringify(result);
}
)()'''


def extract_chat_page(page):
    """提取聊天页面字段。"""
    print("\n[STEP] 打开聊天页面...")
    page.get(CHAT_URL)
    time.sleep(5)
    check_login(page)

    # 点击第一个会话以激活聊天窗口
    try:
        page.run_js('document.querySelector(".friend-content") && document.querySelector(".friend-content").click()', as_expr=True)
        time.sleep(3)
        print("[INFO] 已点击第一个会话")
    except Exception as e:
        print(f"[WARN] 点击会话失败: {e}")

    # 截图聊天页面
    take_screenshot(page, CHAT_PAGE_SCREENSHOT, full_page=True)

    print("[STEP] 提取聊天页面字段...")
    raw = page.run_js(CHAT_PAGE_JS, as_expr=True)
    try:
        return json.loads(raw) if raw else {}
    except Exception as e:
        print(f"[ERROR] 解析聊天页面JS结果失败: {e}")
        print(f"[DEBUG] 原始返回前500字符: {str(raw)[:500]}")
        return {}


def find_job_detail_url(page):
    """从聊天页面或主页导航到一个岗位详情页，返回URL。"""
    print("\n[STEP] 尝试从聊天页面获取岗位详情URL...")
    # 优先从聊天页面的岗位链接获取
    url = page.run_js(r'''(
        function() {
            var links = document.querySelectorAll('a[href*="/job_detail/"], a[href*="job-detail"]');
            for (var i = 0; i < links.length; i++) {
                var href = links[i].href;
                if (href && href.indexOf('job_detail') >= 0) return href;
            }
            return '';
        }
    )()''', as_expr=True)
    if url:
        print(f"[INFO] 从聊天页面找到岗位详情URL: {url}")
        return url

    # 兜底：访问推荐页找岗位链接
    print("[STEP] 聊天页未找到岗位链接，访问推荐页...")
    page.get("https://www.zhipin.com/web/geek/recommend")
    time.sleep(5)
    url = page.run_js(r'''(
        function() {
            var links = document.querySelectorAll('a[href*="/job_detail/"], a[href*="job-detail"]');
            for (var i = 0; i < links.length; i++) {
                var href = links[i].href;
                if (href && href.indexOf('job_detail') >= 0) return href;
            }
            return '';
        }
    )()''', as_expr=True)
    if url:
        print(f"[INFO] 从推荐页找到岗位详情URL: {url}")
    else:
        print("[WARN] 未找到任何岗位详情URL")
    return url


def extract_job_detail_page(page):
    """提取岗位详情页字段。"""
    url = find_job_detail_url(page)
    if not url:
        print("[WARN] 跳过岗位详情页提取（无URL）")
        return {"_note": "未找到岗位详情URL", "_page_url": page.url}

    print(f"[STEP] 打开岗位详情页: {url}")
    page.get(url)
    time.sleep(5)
    check_login(page)

    # 截图岗位详情页
    take_screenshot(page, JOB_DETAIL_SCREENSHOT, full_page=True)

    print("[STEP] 提取岗位详情页字段...")
    raw = page.run_js(JOB_DETAIL_JS, as_expr=True)
    try:
        data = json.loads(raw) if raw else {}
        data["_page_url"] = url
        return data
    except Exception as e:
        print(f"[ERROR] 解析岗位详情JS结果失败: {e}")
        print(f"[DEBUG] 原始返回前500字符: {str(raw)[:500]}")
        return {"_error": str(e), "_page_url": url}


def click_send_resume_and_record_flow(page):
    """在聊天页面点击发简历按钮，记录完整交互流程。

    流程:
    1. 回到聊天页面
    2. 点击第一个会话
    3. 查找发简历按钮
    4. 点击发简历按钮
    5. 等待确认弹层出现
    6. 截图弹层
    7. 记录弹层中确定/取消按钮的完整选择器
    8. 点击确定按钮
    9. 验证简历是否发送成功
    10. 截图发送后页面
    """
    flow = {
        "step_1_open_chat": {"status": "pending", "detail": "打开聊天页面"},
        "step_2_click_friend": {"status": "pending", "detail": "点击第一个会话"},
        "step_3_find_resume_btn": {"status": "pending", "detail": "查找发简历按钮"},
        "step_4_click_resume_btn": {"status": "pending", "detail": "点击发简历按钮"},
        "step_5_wait_dialog": {"status": "pending", "detail": "等待确认弹层出现"},
        "step_6_screenshot_dialog": {"status": "pending", "detail": "截图弹层", "screenshot": RESUME_DIALOG_SCREENSHOT},
        "step_7_record_dialog_selectors": {"status": "pending", "detail": "记录弹层确定/取消按钮选择器"},
        "step_8_click_confirm": {"status": "pending", "detail": "点击确定按钮"},
        "step_9_verify_send": {"status": "pending", "detail": "验证简历是否发送成功"},
        "step_10_screenshot_after": {"status": "pending", "detail": "截图发送后页面", "screenshot": RESUME_AFTER_SEND_SCREENSHOT},
        "summary": {},
        "errors": []
    }

    # Step 1: 打开聊天页面
    print("\n[STEP] 发简历流程 - 1. 打开聊天页面")
    try:
        page.get(CHAT_URL)
        time.sleep(5)
        check_login(page)
        flow["step_1_open_chat"]["status"] = "success"
        flow["step_1_open_chat"]["url"] = page.url
    except Exception as e:
        flow["step_1_open_chat"]["status"] = "failed"
        flow["step_1_open_chat"]["error"] = str(e)
        flow["errors"].append(f"step1: {e}")
        return flow

    # Step 2: 点击第一个会话
    print("[STEP] 发简历流程 - 2. 点击第一个会话")
    try:
        clicked = page.run_js(r'''(
            function() {
                var f = document.querySelector(".friend-content");
                if (f) { f.click(); return true; }
                return false;
            }
        )()''', as_expr=True)
        time.sleep(3)
        flow["step_2_click_friend"]["status"] = "success" if clicked else "failed"
        flow["step_2_click_friend"]["clicked"] = bool(clicked)
    except Exception as e:
        flow["step_2_click_friend"]["status"] = "failed"
        flow["step_2_click_friend"]["error"] = str(e)
        flow["errors"].append(f"step2: {e}")

    # Step 3: 查找发简历按钮
    print("[STEP] 发简历流程 - 3. 查找发简历按钮")
    resume_btn_info = None
    try:
        # 优先查找toolbar中的"发简历"按钮
        btn_js = r'''(
            function() {
                // 优先查找文本为"发简历"的可点击元素
                var allEls = document.querySelectorAll(
                    "[class*='toolbar'] div, [class*='toolbar'] a, [class*='toolbar'] button, " +
                    ".chat-toolbar div, .chat-toolbar a, .chat-toolbar button, " +
                    "[class*='resume'], .btn-send-resume, .send-resume, .btn-resume"
                );
                for (var i = 0; i < allEls.length; i++) {
                    var el = allEls[i];
                    var t = (el.textContent || '').trim();
                    if (t === '发简历' && el.offsetParent !== null) {
                        return JSON.stringify({
                            found: true,
                            tag: el.tagName.toLowerCase(),
                            class: (el.className || '').substring(0, 150),
                            text: t,
                            outerHTML: el.outerHTML.substring(0, 500),
                            selector_hint: "文本为'发简历'的toolbar按钮"
                        });
                    }
                }
                // 兜底：查找任何class含resume的按钮
                var resumeEls = document.querySelectorAll("[class*='resume']");
                for (var j = 0; j < resumeEls.length; j++) {
                    var re = resumeEls[j];
                    if (re.offsetParent !== null) {
                        return JSON.stringify({
                            found: true,
                            tag: re.tagName.toLowerCase(),
                            class: (re.className || '').substring(0, 150),
                            text: (re.textContent || '').trim().substring(0, 50),
                            outerHTML: re.outerHTML.substring(0, 500),
                            selector_hint: "class含resume的元素"
                        });
                    }
                }
                return JSON.stringify({found: false});
            }
        )()'''
        raw = page.run_js(btn_js, as_expr=True)
        resume_btn_info = json.loads(raw) if raw else {"found": False}
        flow["step_3_find_resume_btn"]["status"] = "success" if resume_btn_info.get("found") else "failed"
        flow["step_3_find_resume_btn"]["button_info"] = resume_btn_info
        print(f"[INFO] 发简历按钮查找结果: {resume_btn_info.get('found')}")
    except Exception as e:
        flow["step_3_find_resume_btn"]["status"] = "failed"
        flow["step_3_find_resume_btn"]["error"] = str(e)
        flow["errors"].append(f"step3: {e}")
        resume_btn_info = {"found": False}

    # Step 4: 点击发简历按钮
    print("[STEP] 发简历流程 - 4. 点击发简历按钮")
    try:
        click_js = r'''(
            function() {
                // 优先点击文本为"发简历"的toolbar按钮
                var allEls = document.querySelectorAll(
                    "[class*='toolbar'] div, [class*='toolbar'] a, [class*='toolbar'] button, " +
                    ".chat-toolbar div, .chat-toolbar a, .chat-toolbar button"
                );
                for (var i = 0; i < allEls.length; i++) {
                    var el = allEls[i];
                    var t = (el.textContent || '').trim();
                    if (t === '发简历' && el.offsetParent !== null) {
                        el.click();
                        return JSON.stringify({clicked: true, method: "toolbar文本匹配"});
                    }
                }
                // 兜底：点击class含resume的元素
                var resumeEls = document.querySelectorAll("[class*='resume']");
                for (var j = 0; j < resumeEls.length; j++) {
                    var re = resumeEls[j];
                    if (re.offsetParent !== null) {
                        re.click();
                        return JSON.stringify({clicked: true, method: "class含resume"});
                    }
                }
                return JSON.stringify({clicked: false});
            }
        )()'''
        raw = page.run_js(click_js, as_expr=True)
        click_result = json.loads(raw) if raw else {"clicked": False}
        time.sleep(2)
        flow["step_4_click_resume_btn"]["status"] = "success" if click_result.get("clicked") else "failed"
        flow["step_4_click_resume_btn"]["click_result"] = click_result
        print(f"[INFO] 点击发简历按钮结果: {click_result}")
    except Exception as e:
        flow["step_4_click_resume_btn"]["status"] = "failed"
        flow["step_4_click_resume_btn"]["error"] = str(e)
        flow["errors"].append(f"step4: {e}")

    # Step 5: 等待确认弹层出现
    print("[STEP] 发简历流程 - 5. 等待确认弹层出现")
    dialog_info = None
    try:
        # 轮询等待弹层出现（最多10秒）
        for wait_round in range(10):
            time.sleep(1)
            raw = page.run_js(DIALOG_EXTRACT_JS, as_expr=True)
            dialog_info = json.loads(raw) if raw else {"found": False}
            if dialog_info.get("found"):
                print(f"[INFO] 弹层已出现（等待{wait_round + 1}秒）")
                break

        flow["step_5_wait_dialog"]["status"] = "success" if dialog_info and dialog_info.get("found") else "failed"
        flow["step_5_wait_dialog"]["dialog_found"] = bool(dialog_info and dialog_info.get("found"))
        if dialog_info:
            flow["step_5_wait_dialog"]["dialogs"] = dialog_info.get("dialogs", [])[:3]
    except Exception as e:
        flow["step_5_wait_dialog"]["status"] = "failed"
        flow["step_5_wait_dialog"]["error"] = str(e)
        flow["errors"].append(f"step5: {e}")
        dialog_info = {"found": False}

    # Step 6: 截图弹层
    print("[STEP] 发简历流程 - 6. 截图弹层")
    try:
        ok = take_screenshot(page, RESUME_DIALOG_SCREENSHOT, full_page=True)
        flow["step_6_screenshot_dialog"]["status"] = "success" if ok else "failed"
    except Exception as e:
        flow["step_6_screenshot_dialog"]["status"] = "failed"
        flow["step_6_screenshot_dialog"]["error"] = str(e)
        flow["errors"].append(f"step6: {e}")

    # Step 7: 记录弹层中确定/取消按钮的完整选择器
    print("[STEP] 发简历流程 - 7. 记录弹层确定/取消按钮选择器")
    try:
        # 再次提取弹层信息（确保最新）
        raw = page.run_js(DIALOG_EXTRACT_JS, as_expr=True)
        dialog_info = json.loads(raw) if raw else {"found": False}
        flow["step_7_record_dialog_selectors"]["status"] = "success"
        flow["step_7_record_dialog_selectors"]["confirm_btn"] = dialog_info.get("confirm_btn")
        flow["step_7_record_dialog_selectors"]["cancel_btn"] = dialog_info.get("cancel_btn")
        flow["step_7_record_dialog_selectors"]["dialogs"] = dialog_info.get("dialogs", [])[:3]
        print(f"[INFO] 确定按钮: {dialog_info.get('confirm_btn')}")
        print(f"[INFO] 取消按钮: {dialog_info.get('cancel_btn')}")
    except Exception as e:
        flow["step_7_record_dialog_selectors"]["status"] = "failed"
        flow["step_7_record_dialog_selectors"]["error"] = str(e)
        flow["errors"].append(f"step7: {e}")

    # Step 8: 点击确定按钮
    print("[STEP] 发简历流程 - 8. 点击确定按钮")
    try:
        click_confirm_js = r'''(
            function() {
                // 优先点击弹层中的确定按钮
                var confirmSelectors = [
                    ".dialog-wrap .btn-sure-v2", ".dialog-content .btn-sure-v2",
                    ".upload-resume-dialog .btn-sure-v2",
                    ".dialog-wrap .btn-confirm", ".dialog-content .btn-confirm",
                    ".dialog-wrap .btn-primary", ".dialog-content .btn-primary",
                    ".dialog-wrap button[class*='sure']", ".dialog-content button[class*='sure']",
                    ".dialog-wrap .btn.btn-sure", ".dialog-content .btn.btn-sure"
                ];
                for (var i = 0; i < confirmSelectors.length; i++) {
                    var btns = document.querySelectorAll(confirmSelectors[i]);
                    for (var j = 0; j < btns.length; j++) {
                        if (btns[j].offsetParent !== null) {
                            btns[j].click();
                            return JSON.stringify({clicked: true, selector: confirmSelectors[i]});
                        }
                    }
                }
                return JSON.stringify({clicked: false});
            }
        )()'''
        raw = page.run_js(click_confirm_js, as_expr=True)
        confirm_result = json.loads(raw) if raw else {"clicked": False}
        time.sleep(3)
        flow["step_8_click_confirm"]["status"] = "success" if confirm_result.get("clicked") else "failed"
        flow["step_8_click_confirm"]["click_result"] = confirm_result
        print(f"[INFO] 点击确定按钮结果: {confirm_result}")
    except Exception as e:
        flow["step_8_click_confirm"]["status"] = "failed"
        flow["step_8_click_confirm"]["error"] = str(e)
        flow["errors"].append(f"step8: {e}")

    # Step 9: 验证简历是否发送成功
    print("[STEP] 发简历流程 - 9. 验证简历是否发送成功")
    try:
        verify_js = r'''(
            function() {
                // 检查聊天消息列表中是否出现"简历"相关消息
                var messages = document.querySelectorAll(
                    ".chat-content li, .message-content li, .chat-content > div, .message-content > div"
                );
                var recentMessages = [];
                for (var i = Math.max(0, messages.length - 5); i < messages.length; i++) {
                    var m = messages[i];
                    var t = (m.textContent || '').trim();
                    recentMessages.push(t.substring(0, 100));
                    if (t.indexOf('简历') >= 0 || t.indexOf('已发送') >= 0 || t.indexOf('附件') >= 0) {
                        return JSON.stringify({sent: true, message: t.substring(0, 100), recent: recentMessages});
                    }
                }
                // 检查弹层是否已关闭（间接验证）
                var dialog = document.querySelector(".dialog-wrap, .dialog-content, .upload-resume-dialog");
                var dialogVisible = dialog && dialog.offsetParent !== null;
                return JSON.stringify({
                    sent: !dialogVisible,
                    message: "弹层已关闭",
                    recent: recentMessages,
                    dialog_still_visible: dialogVisible
                });
            }
        )()'''
        raw = page.run_js(verify_js, as_expr=True)
        verify_result = json.loads(raw) if raw else {"sent": False}
        flow["step_9_verify_send"]["status"] = "success" if verify_result.get("sent") else "failed"
        flow["step_9_verify_send"]["verify_result"] = verify_result
        print(f"[INFO] 验证发送结果: {verify_result}")
    except Exception as e:
        flow["step_9_verify_send"]["status"] = "failed"
        flow["step_9_verify_send"]["error"] = str(e)
        flow["errors"].append(f"step9: {e}")

    # Step 10: 截图发送后页面
    print("[STEP] 发简历流程 - 10. 截图发送后页面")
    try:
        ok = take_screenshot(page, RESUME_AFTER_SEND_SCREENSHOT, full_page=True)
        flow["step_10_screenshot_after"]["status"] = "success" if ok else "failed"
    except Exception as e:
        flow["step_10_screenshot_after"]["status"] = "failed"
        flow["step_10_screenshot_after"]["error"] = str(e)
        flow["errors"].append(f"step10: {e}")

    # 汇总
    success_count = sum(1 for k in flow if k.startswith("step_") and flow[k].get("status") == "success")
    fail_count = sum(1 for k in flow if k.startswith("step_") and flow[k].get("status") == "failed")
    flow["summary"] = {
        "total_steps": 10,
        "success_steps": success_count,
        "failed_steps": fail_count,
        "all_success": success_count == 10,
        "screenshots": {
            "resume_dialog": RESUME_DIALOG_SCREENSHOT,
            "resume_after_send": RESUME_AFTER_SEND_SCREENSHOT
        }
    }
    return flow


def to_selector_lists(data):
    """把提取结果转换成任务要求的简洁选择器列表格式。"""
    simplified = {}
    for key, val in data.items():
        if key.startswith("_"):
            simplified[key] = val
            continue
        if isinstance(val, list):
            selectors = []
            for item in val:
                if isinstance(item, dict) and item.get("selector"):
                    selectors.append(item["selector"])
            if selectors:
                simplified[key] = selectors
            elif val:
                simplified[key] = val
        else:
            simplified[key] = val
    return simplified


def main():
    # 确保logs目录存在
    os.makedirs(LOGS_DIR, exist_ok=True)

    page = build_browser()
    try:
        load_cookies(page)

        # 1. 提取聊天页面字段
        chat_data = extract_chat_page(page)

        # 2. 提取岗位详情页字段
        job_data = extract_job_detail_page(page)

        # 3. 记录发送简历完整交互流程
        print("\n[STEP] === 开始记录发送简历完整交互流程 ===")
        resume_flow = click_send_resume_and_record_flow(page)

        # 保存发送简历流程
        with open(RESUME_FLOW_FILE, "w", encoding="utf-8") as f:
            json.dump(resume_flow, f, ensure_ascii=False, indent=2)
        print(f"\n[OK] 发送简历流程已保存到 {RESUME_FLOW_FILE}")

        # 4. 保存CSS选择器集合
        output = {
            "_meta": {
                "chat_page_url": CHAT_URL,
                "extracted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "browser": "cloakbrowser",
                "browser_path": CLOAK_BROWSER,
                "screenshots": {
                    "chat_page_full": CHAT_PAGE_SCREENSHOT,
                    "job_detail_full": JOB_DETAIL_SCREENSHOT,
                    "resume_confirm_dialog": RESUME_DIALOG_SCREENSHOT,
                    "resume_after_send": RESUME_AFTER_SEND_SCREENSHOT
                }
            },
            "chat_page": to_selector_lists(chat_data),
            "chat_page_detail": chat_data,
            "job_detail_page": to_selector_lists(job_data),
            "job_detail_page_detail": job_data,
            "resume_send_flow_summary": resume_flow.get("summary", {})
        }

        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"\n[OK] CSS选择器集合已保存到 {OUTPUT_FILE}")

        # 打印简要摘要
        print("\n=== 摘要 ===")
        print("[chat_page]")
        for k, v in output["chat_page"].items():
            if isinstance(v, list):
                print(f"  {k}: {v[:3]}{' ...' if len(v) > 3 else ''}")
            else:
                print(f"  {k}: {v}")
        print("[job_detail_page]")
        for k, v in output["job_detail_page"].items():
            if isinstance(v, list):
                print(f"  {k}: {v[:3]}{' ...' if len(v) > 3 else ''}")
            else:
                print(f"  {k}: {v}")
        print("[resume_flow_summary]")
        print(f"  {resume_flow.get('summary', {})}")

    finally:
        try:
            page.quit()
        except Exception:
            pass
        print("\n[DONE] 浏览器已关闭")


if __name__ == "__main__":
    main()
