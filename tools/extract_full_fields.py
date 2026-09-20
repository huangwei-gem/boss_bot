"""用破解浏览器cloakbrowser登录BOSS直聘，提取聊天页面和岗位详情页的完整CSS字段集合。

输出: tools/css_fields_full.json
"""
import json
import os
import time
from DrissionPage import ChromiumPage, ChromiumOptions

# 路径配置（基于本脚本所在目录）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
CLOAK_BROWSER = os.path.join(PROJECT_ROOT, "cloakbrowser", "chrome.exe")
COOKIE_FILE = os.path.join(PROJECT_ROOT, "zhipin_cookies.json")
OUTPUT_FILE = os.path.join(BASE_DIR, "css_fields_full.json")

CHAT_URL = "https://www.zhipin.com/web/geek/chat"
HOME_URL = "https://www.zhipin.com/"


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


# 聊天页面字段提取JS
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
        ".btn.btn-resume"
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
        ".boss-dialog"
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
        ".top-info-content button, .top-info-content a"
    );
    for (var i = 0; i < btnCandidates.length; i++) {
        var b = btnCandidates[i];
        var t = (b.textContent || '').trim();
        if (t && t.length < 30) {
            toolbarBtns.push({
                tag: b.tagName.toLowerCase(),
                class: (b.className || '').substring(0, 80),
                text: t.substring(0, 30)
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
                    class: (b2.className || '').substring(0, 80),
                    text: t2.substring(0, 30)
                });
            }
        }
    }
    result.toolbar_buttons = toolbarBtns;

    // 11. 额外采集所有含name/company/position的候选元素（便于扩展备选选择器）
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


# 岗位详情页字段提取JS
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
        ".btn.btn-chat"
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
        ".job-banner .name"
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
    page = build_browser()
    try:
        load_cookies(page)
        chat_data = extract_chat_page(page)
        job_data = extract_job_detail_page(page)

        # 同时保存详细原始结果和简化选择器列表
        output = {
            "_meta": {
                "chat_page_url": CHAT_URL,
                "extracted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "browser": "cloakbrowser"
            },
            "chat_page": to_selector_lists(chat_data),
            "chat_page_detail": chat_data,
            "job_detail_page": to_selector_lists(job_data),
            "job_detail_page_detail": job_data
        }

        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"\n[OK] 结果已保存到 {OUTPUT_FILE}")

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
    finally:
        try:
            page.quit()
        except Exception:
            pass
        print("\n[DONE] 浏览器已关闭")


if __name__ == "__main__":
    main()