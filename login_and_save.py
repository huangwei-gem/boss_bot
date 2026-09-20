"""
启动破解浏览器登录BOSS直聘，登录成功后自动保存Cookie。
用法: python login_and_save.py [cookie_file]
"""
import sys
import os
import time
import json

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from DrissionPage import ChromiumPage, ChromiumOptions

def find_cloakbrowser():
    """查找破解浏览器路径"""
    project_root = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(project_root, "cloakbrowser", "chrome.exe"),
        os.path.join(project_root, "cloakbrowser-windows-x64", "chrome.exe"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return ""

def main():
    cookie_file = sys.argv[1] if len(sys.argv) > 1 else "zhipin_cookies.json"
    cookie_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), cookie_file)
    
    chrome_path = find_cloakbrowser()
    if not chrome_path:
        print("❌ 未找到破解浏览器，请确认 cloakbrowser/chrome.exe 存在")
        return
    
    print(f"✅ 找到破解浏览器: {chrome_path}")
    
    # 启动浏览器
    co = ChromiumOptions()
    co.set_browser_path(chrome_path)
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--no-first-run")
    co.set_argument("--no-default-browser-check")
    
    page = ChromiumPage(co)
    
    # 打开BOSS直聘登录页
    print("🌐 正在打开BOSS直聘登录页面...")
    page.get("https://www.zhipin.com/web/user/?ka=header-login")
    
    print("\n" + "=" * 60)
    print("📋 请在浏览器中完成登录")
    print("   登录成功后，脚本会自动检测并保存Cookie")
    print("=" * 60 + "\n")
    
    # 等待登录成功（检测URL变化或cookie）
    max_wait = 300  # 最多等5分钟
    start = time.time()
    
    while time.time() - start < max_wait:
        try:
            current_url = page.url
            # 登录成功后会跳转到首页或web目录
            if "zhipin.com/web/user/" not in current_url and "zhipin.com" in current_url:
                # 再等2秒确保页面稳定
                time.sleep(2)
                # 检查是否有用户cookie
                cookies = page.cookies()
                has_token = any(c.get("name") == "wt2" or c.get("name") == "__zp_stoken__" for c in cookies)
                if has_token or "zhipin.com/web/user/" not in page.url:
                    print("✅ 检测到登录成功！")
                    break
        except Exception:
            pass
        time.sleep(1)
    else:
        print("⚠️ 等待超时（5分钟），尝试保存当前Cookie...")
    
    # 保存Cookie
    try:
        cookies = page.cookies()
        # 转换为项目兼容格式
        cookie_list = []
        for c in cookies:
            cookie_list.append({
                "name": c.get("name", ""),
                "value": c.get("value", ""),
                "domain": c.get("domain", ""),
                "path": c.get("path", "/"),
                "expiry": c.get("expiry", c.get("expires", -1)),
                "httpOnly": c.get("httpOnly", False),
                "secure": c.get("secure", False),
            })
        
        with open(cookie_path, "w", encoding="utf-8") as f:
            json.dump(cookie_list, f, ensure_ascii=False, indent=2)
        
        print(f"✅ Cookie已保存到: {cookie_path}")
        print(f"   共 {len(cookie_list)} 条Cookie")
        
        # 验证关键Cookie
        key_names = {"wt2", "__zp_stoken__", "bp4_token", "Hm_lvt_194df7c0c4f0c4f0c4f0c4f0c4f0c4f0"}
        found_keys = {c["name"] for c in cookie_list}
        important = found_keys & key_names
        if important:
            print(f"   关键Cookie: {', '.join(important)}")
        else:
            print("   ⚠️ 未找到关键Cookie(wt2)，请确认登录成功")
            
    except Exception as e:
        print(f"❌ 保存Cookie失败: {e}")
    
    print("\n浏览器保持打开，你可以关闭它或继续操作。")
    print("接下来可以启动Flask服务进行多账号测试: python flask-version/app.py")

if __name__ == "__main__":
    main()