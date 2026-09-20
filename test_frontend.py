"""全量前端测试 — DOM验证多账号隔离和UI功能"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from DrissionPage import ChromiumPage, ChromiumOptions

def find_cloakbrowser():
    project_root = os.path.dirname(os.path.abspath(__file__))
    for p in [os.path.join(project_root, "cloakbrowser", "chrome.exe"),
              os.path.join(project_root, "cloakbrowser-windows-x64", "chrome.exe")]:
        if os.path.isfile(p):
            return p
    return ""

def main():
    chrome_path = find_cloakbrowser()
    co = ChromiumOptions()
    co.set_browser_path(chrome_path)
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--no-first-run")
    
    page = ChromiumPage(co)
    page.get("http://127.0.0.1:5000")
    time.sleep(4)
    
    results = []
    all_pass = True
    
    # 1. 检查账号标签
    try:
        tabs = page.els('.account-tab')
        results.append(f"[{'✅' if len(tabs)>=2 else '❌'}] 账号标签数: {len(tabs)}")
        if len(tabs) < 2: all_pass = False
        for i, tab in enumerate(tabs):
            results.append(f"     标签{i}: {tab.text}")
    except Exception as e:
        results.append(f"[❌] 账号标签检查失败: {e}")
        all_pass = False
    
    # 2. 检查账号0 Cookie
    cookie0 = None
    try:
        cookie_input = page.ele('#accCookie')
        cookie0 = cookie_input.attr('value')
        ok = cookie0 == 'zhipin_cookies.json'
        results.append(f"[{'✅' if ok else '❌'}] 账号0 Cookie: {cookie0}")
        if not ok: all_pass = False
    except Exception as e:
        results.append(f"[❌] 账号0 Cookie检查失败: {e}")
        all_pass = False
    
    # 3. 切换到账号2并检查Cookie
    cookie1 = None
    try:
        tabs = page.els('.account-tab')
        if len(tabs) >= 2:
            tabs[1].click()
            time.sleep(1)
            cookie_input = page.ele('#accCookie')
            cookie1 = cookie_input.attr('value')
            ok = cookie1 == 'zhipin_cookies_1.json'
            results.append(f"[{'✅' if ok else '❌'}] 账号1 Cookie: {cookie1}")
            if not ok: all_pass = False
    except Exception as e:
        results.append(f"[❌] 切换账号1失败: {e}")
        all_pass = False
    
    # 4. Cookie隔离验证
    if cookie0 and cookie1 and cookie0 != cookie1:
        results.append(f"[✅] Cookie隔离: {cookie0} ≠ {cookie1}")
    else:
        results.append(f"[❌] Cookie隔离失败: {cookie0} == {cookie1}")
        all_pass = False
    
    # 5. 切回账号0
    try:
        tabs = page.els('.account-tab')
        tabs[0].click()
        time.sleep(0.5)
        cookie0_check = page.ele('#accCookie').attr('value')
        ok = cookie0_check == 'zhipin_cookies.json'
        results.append(f"[{'✅' if ok else '❌'}] 切回账号0 Cookie: {cookie0_check}")
        if not ok: all_pass = False
    except Exception as e:
        results.append(f"[❌] 切回账号0失败: {e}")
        all_pass = False
    
    # 6. 检查AI开关说明文字
    try:
        html = page.html
        has_shared = "全局共享" in html
        has_independent = "独立" in html
        has_ai_note = "仅控制打招呼" in html or "自动回复AI始终开启" in html
        results.append(f"[{'✅' if has_shared else '❌'}] AI配置'全局共享'标识")
        results.append(f"[{'✅' if has_independent else '❌'}] 账号配置'独立'标识")
        results.append(f"[{'✅' if has_ai_note else '❌'}] AI开关说明文字")
        if not (has_shared and has_independent and has_ai_note): all_pass = False
    except Exception as e:
        results.append(f"[❌] AI说明文字检查失败: {e}")
        all_pass = False
    
    # 7. 检查回复记录tab
    try:
        reply_tab = page.ele('xpath://*[contains(text(),"回复记录")]')
        ok = reply_tab is not None
        results.append(f"[{'✅' if ok else '❌'}] 回复记录tab存在")
        if not ok: all_pass = False
        if reply_tab:
            reply_tab.click()
            time.sleep(1)
            # 检查是否有聊天卡片
            cards = page.els('.chat-card') or page.els('[class*="chat"]') or page.els('[class*="fold"]')
            results.append(f"[{'✅' if len(cards)>0 else '⚠️'}] 回复记录聊天卡片数: {len(cards)}")
    except Exception as e:
        results.append(f"[❌] 回复记录tab检查失败: {e}")
        all_pass = False
    
    # 8. 检查打招呼/投递记录tab
    try:
        greet_tab = page.ele('xpath://*[contains(text(),"投递记录")]') or page.ele('xpath://*[contains(text(),"打招呼记录")]')
        ok = greet_tab is not None
        results.append(f"[{'✅' if ok else '❌'}] 投递记录tab存在")
        if not ok: all_pass = False
    except Exception as e:
        results.append(f"[❌] 投递记录tab检查失败: {e}")
        all_pass = False
    
    # 9. 检查系统日志区域
    try:
        log_area = page.ele('#logArea') or page.ele('[class*="log"]')
        ok = log_area is not None
        results.append(f"[{'✅' if ok else '❌'}] 系统日志区域存在")
        if not ok: all_pass = False
    except Exception as e:
        results.append(f"[❌] 日志区域检查失败: {e}")
        all_pass = False
    
    # 10. 检查启动按钮
    try:
        start_btn = page.ele('xpath://*[contains(text(),"启动")]') or page.ele('#startBtn') or page.ele('[onclick*="start"]')
        ok = start_btn is not None
        results.append(f"[{'✅' if ok else '❌'}] 启动按钮存在")
        if not ok: all_pass = False
    except Exception as e:
        results.append(f"[❌] 启动按钮检查失败: {e}")
        all_pass = False
    
    # 输出结果
    print("\n" + "=" * 60)
    print("全量前端测试结果")
    print("=" * 60)
    for r in results:
        print(f"  {r}")
    print("=" * 60)
    if all_pass:
        print("\n✅ 全量前端测试通过！")
    else:
        print("\n❌ 部分测试未通过，请检查上方标记❌的项")
    print("=" * 60)

if __name__ == "__main__":
    main()
