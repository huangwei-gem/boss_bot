"""用破解浏览器登录BOSS直聘，提取聊天页面的CSS选择器。"""
import json
import os
import time
from DrissionPage import ChromiumPage, ChromiumOptions

CLOAK_BROWSER = os.path.join(os.path.dirname(__file__), "cloakbrowser", "chrome.exe")
COOKIE_FILE = os.path.join(os.path.dirname(__file__), "zhipin_cookies.json")
CHAT_URL = "https://www.zhipin.com/web/geek/chat"

def main():
    print(f"破解浏览器: {CLOAK_BROWSER}")

    co = ChromiumOptions()
    co.set_browser_path(CLOAK_BROWSER)
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-gpu')
    co.set_argument('--no-first-run')
    co.set_argument('--no-default-browser-check')
    co.set_argument('--window-size=1280,800')

    page = ChromiumPage(co)
    print("浏览器启动成功")

    page.get("https://www.zhipin.com")
    time.sleep(2)

    if os.path.isfile(COOKIE_FILE):
        with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
            cookies = json.load(f)
        print(f"加载 {len(cookies)} 个Cookie")
        try:
            page.set.cookies(cookies)
        except Exception as e:
            print(f"设置Cookie失败: {e}")

    print("打开聊天页面...")
    page.get(CHAT_URL)
    time.sleep(5)
    print(f"当前URL: {page.url}")

    # 提取页面关键元素
    result = page.run_js('''(
        function() {
            var info = {};

            // 聊天对象名称
            var nameSels = [
                ".top-info-content .name-text",
                ".top-info-box .name-text",
                ".chat-header .name-text",
                ".user-info .name-text",
                ".header-content .name-text",
                ".top-info .name",
                ".boss-name",
                ".chat-title .name"
            ];
            for (var i = 0; i < nameSels.length; i++) {
                var el = document.querySelector(nameSels[i]);
                if (el && el.textContent.trim()) {
                    info.boss_name_selector = nameSels[i];
                    info.boss_name = el.textContent.trim();
                    break;
                }
            }
            if (!info.boss_name) {
                var allNames = document.querySelectorAll("[class*='name'], [class*='title']");
                info.name_candidates = [];
                for (var j = 0; j < Math.min(allNames.length, 20); j++) {
                    var text = allNames[j].textContent.trim();
                    if (text && text.length < 30) {
                        info.name_candidates.push(allNames[j].className + " => " + text);
                    }
                }
            }

            // 岗位名称
            var jobSels = [
                ".chat-position-content .position-content",
                ".job-info .job-name",
                ".chat-header .job-name",
                ".position-info .position-name",
                ".job-title"
            ];
            for (var i = 0; i < jobSels.length; i++) {
                var el = document.querySelector(jobSels[i]);
                if (el && el.textContent.trim()) {
                    info.job_name_selector = jobSels[i];
                    info.job_name = el.textContent.trim();
                    break;
                }
            }

            // 输入框
            var inputSels = [".input-area", "#chat-input", ".chat-input", "textarea", "[contenteditable=true]"];
            for (var i = 0; i < inputSels.length; i++) {
                var el = document.querySelector(inputSels[i]);
                if (el) {
                    info.input_selector = inputSels[i];
                    info.input_class = el.className;
                    break;
                }
            }

            // 发送按钮
            var sendSels = [".send-message", ".btn-send", ".btn-v2.btn-sure-v2.btn-send", "button[type=submit]", ".chat-send"];
            for (var i = 0; i < sendSels.length; i++) {
                var el = document.querySelector(sendSels[i]);
                if (el) {
                    info.send_selector = sendSels[i];
                    info.send_class = el.className;
                    break;
                }
            }

            // 会话列表
            var friends = document.querySelectorAll(".friend-content");
            info.friend_count = friends.length;

            // 未读标记
            var badges = document.querySelectorAll(".notice-badge");
            info.unread_count = 0;
            for (var i = 0; i < badges.length; i++) {
                if (badges[i].offsetParent !== null) info.unread_count++;
            }

            return JSON.stringify(info);
        }
    )()''', as_expr=True)

    print("\n=== 提取结果 ===")
    try:
        info = json.loads(result)
        for k, v in info.items():
            if k == "name_candidates":
                print(f"  {k}:")
                for c in v:
                    print(f"    {c}")
            else:
                print(f"  {k}: {v}")
        with open("css_extract_result.json", "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)
        print("\n结果已保存到 css_extract_result.json")
    except Exception as e:
        print(f"解析失败: {e}")
        print(f"原始: {result}")

    page.quit()
    print("完成")

if __name__ == "__main__":
    main()
