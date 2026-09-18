"""
真机实测启动脚本 — 启动浏览器让用户登录，保存 Cookie，然后开始投递

使用方式：
    python login_and_test.py

流程：
1. 启动浏览器（非无头模式，可见窗口）
2. 导航到 BOSS 直聘登录页面
3. 等待用户手动登录
4. 用户登录后按 Enter 确认
5. 保存 Cookie
6. 开始自动打招呼+回复（双标签页并行）
"""
import sys
import os
import time
import json

# 添加项目根目录到 path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from boss_bot.browser_launcher import launch_browser, BrowserInstance, BrowserManager
from boss_bot.unified_config import UnifiedConfig


def main():
    print("=" * 60)
    print("  BOSS 直聘统一机器人 — 真机实测")
    print("  双标签页架构：打招呼 + 自动回复")
    print("=" * 60)

    # 加载配置
    config = UnifiedConfig.load()
    config.browser.headless = False  # 确保可见窗口

    # Cookie 文件路径
    cookie_file = "zhipin_cookies.json"

    # 1. 启动浏览器
    print("\n[1/4] 正在启动浏览器...")
    instance = launch_browser(headless=False)
    print("✅ 浏览器已启动")

    # 2. 导航到 BOSS 直聘
    print("\n[2/4] 正在导航到 BOSS 直聘...")
    instance.get("https://www.zhipin.com")
    time.sleep(2)

    # 尝试加载已有 Cookie
    if os.path.exists(cookie_file):
        print(f"发现已有 Cookie 文件: {cookie_file}")
        try:
            instance.load_cookies(cookie_file)
            print("已加载 Cookie，验证登录状态...")
            instance.get("https://www.zhipin.com/web/geek/chat")
            time.sleep(3)
            current_url = instance.url or ""
            if "login" not in current_url and "user" not in current_url and "passport" not in current_url:
                print("✅ Cookie 有效，已自动登录！")
                logged_in = True
            else:
                print("❌ Cookie 已过期，需要重新登录")
                logged_in = False
        except Exception as e:
            print(f"Cookie 加载失败: {e}")
            logged_in = False
    else:
        print("无 Cookie 文件，需要手动登录")
        logged_in = False

    # 3. 等待用户登录
    if not logged_in:
        print("\n[3/4] 请在浏览器中手动登录 BOSS 直聘")
        instance.get("https://www.zhipin.com/web/user/?ka=header-login")
        print("浏览器已跳转到登录页面，请在浏览器窗口中完成登录")
        print("登录完成后，请回到这里按 Enter 继续...")

        input()  # 等待用户按 Enter

        # 保存 Cookie
        print("正在保存 Cookie...")
        try:
            instance.save_cookies(cookie_file)
            print(f"✅ Cookie 已保存到 {cookie_file}")
        except Exception as e:
            print(f"❌ Cookie 保存失败: {e}")

    # 4. 创建双标签页并开始运行
    print("\n[4/4] 正在创建双标签页...")

    # 创建 BrowserManager 并共享浏览器实例
    manager = BrowserManager(config=config.browser)
    manager._instance = instance  # 复用已启动的浏览器

    # 创建搜索标签页（打招呼用）
    search_page = manager.get_search_page()
    print("✅ 搜索/打招呼标签页已创建")

    # 创建聊天标签页（自动回复用）
    chat_page = manager.get_chat_page()
    print("✅ 聊天/回复标签页已创建")

    print("\n" + "=" * 60)
    print("  双标签页已就绪！")
    print("  - 标签页1: 搜索/打招呼 (岗位推荐页)")
    print("  - 标签页2: 聊天/回复 (聊天页)")
    print("=" * 60)

    # 开始运行统一主循环
    from boss_bot.main_loop import UnifiedBotLoop

    loop = UnifiedBotLoop(
        config=config,
        log_callback=lambda msg: print(msg),
    )
    # 复用已启动的浏览器和登录状态
    loop.browser_manager = manager
    loop._logged_in = True

    print("\n正在启动双线程（打招呼 + 回复）...")
    loop.start()

    print("\n机器人正在运行中！")
    print("- 打招呼线程：在搜索标签页中自动搜索岗位并发送打招呼")
    print("- 回复线程：在聊天标签页中监控未读消息并自动回复")
    print("\n按 Ctrl+C 停止机器人...")

    try:
        while loop._running:
            time.sleep(5)
            status = loop.get_status()
            stats = status.get("stats", {})
            print(f"\r[{time.strftime('%H:%M:%S')}] "
                  f"打招呼: {stats.get('greet_applied', 0)}已投/{stats.get('greet_total', 0)}总数 | "
                  f"回复: {stats.get('reply_sent', 0)}已回 | "
                  f"模式: {status.get('current_mode', 'idle')}", end="", flush=True)
    except KeyboardInterrupt:
        print("\n\n正在停止机器人...")
        loop.stop()
        print("机器人已停止")


if __name__ == "__main__":
    main()