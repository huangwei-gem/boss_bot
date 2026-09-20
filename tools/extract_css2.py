"""用破解浏览器获取聊天窗口顶栏CSS选择器。"""
import json, os, time
from DrissionPage import ChromiumPage, ChromiumOptions

CLOAK_BROWSER = os.path.join(os.path.dirname(__file__), "cloakbrowser", "chrome.exe")
COOKIE_FILE = os.path.join(os.path.dirname(__file__), "zhipin_cookies.json")

co = ChromiumOptions()
co.set_browser_path(CLOAK_BROWSER)
co.set_argument('--no-sandbox')
co.set_argument('--disable-gpu')
co.set_argument('--no-first-run')
co.set_argument('--window-size=1280,800')
page = ChromiumPage(co)

page.get("https://www.zhipin.com")
time.sleep(2)
with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
    page.set.cookies(json.load(f))

page.get("https://www.zhipin.com/web/geek/chat")
time.sleep(5)
print(f"URL: {page.url}")

# 点击第一个会话
page.run_js('document.querySelector(".friend-content").click()', as_expr=True)
time.sleep(3)

# 提取顶栏所有元素
result = page.run_js('''(
    function() {
        var info = {};

        // 顶栏区域所有元素
        var topSels = [
            ".top-info-content", ".top-info-box", ".chat-header",
            ".user-info", ".header-content", ".header-box",
            ".chat-top", ".top-info", ".current-chat",
            ".chat-user-info", ".chat-friend-info"
        ];
        for (var i = 0; i < topSels.length; i++) {
            var el = document.querySelector(topSels[i]);
            if (el) {
                info[topSels[i]] = el.textContent.trim().substring(0, 100);
            }
        }

        // 找所有含name的元素
        info.all_name_elements = [];
        var allEls = document.querySelectorAll("[class*='name'], [class*='title'], [class*='user']");
        for (var j = 0; j < Math.min(allEls.length, 30); j++) {
            var t = allEls[j].textContent.trim();
            if (t && t.length < 50) {
                info.all_name_elements.push(allEls[j].className.substring(0, 50) + " => " + t.substring(0, 30));
            }
        }

        // 输入框详情
        var input = document.querySelector("#chat-input") || document.querySelector("textarea");
        if (input) {
            info.input_id = input.id;
            info.input_class = input.className;
            info.input_tag = input.tagName;
            info.input_parent_class = input.parentElement ? input.parentElement.className : "";
        }

        // 发送按钮详情
        info.all_buttons = [];
        var btns = document.querySelectorAll("button, [class*='send'], [class*='btn']");
        for (var k = 0; k < Math.min(btns.length, 20); k++) {
            var t = btns[k].textContent.trim();
            if (t && t.length < 20) {
                info.all_buttons.push(btns[k].className.substring(0, 50) + " => " + t);
            }
        }

        // 岗位名称
        var jobSels = [".chat-position-content .position-content", ".job-info .job-name", ".position-info"];
        for (var i = 0; i < jobSels.length; i++) {
            var el = document.querySelector(jobSels[i]);
            if (el && el.textContent.trim()) {
                info.job_selector = jobSels[i];
                info.job_text = el.textContent.trim().substring(0, 60);
                break;
            }
        }

        return JSON.stringify(info);
    }
)()''', as_expr=True)

print("\n=== 顶栏元素 ===")
info = json.loads(result)
for k, v in info.items():
    if isinstance(v, list):
        print(f"\n{k}:")
        for item in v:
            print(f"  {item}")
    else:
        print(f"  {k}: {v}")

page.quit()