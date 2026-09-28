"""真机验证 — 只读，不发送任何打招呼/回复消息。

用项目内置的破解版浏览器（cloakbrowser）跑一遍自动化链路的关键前置环节，
逐环节给出可核对的证据：

  1. 选中的浏览器二进制确实是 cloakbrowser（BOSS 直聘有反爬，用原版必被风控）
  2. 启动参数里带了反检测开关，且使用独立调试端口与独立 profile
  3. Cookie 注入成功，聊天页登录态可读
  4. 搜索页岗位卡片选择器能取到真实岗位（不点击）
  5. 双标签页隔离：关闭打招呼临时标签页不会带走回复引擎的标签页
  6. 健康检查在真实页面上的返回值

运行：python tools/verify_live_browser.py
退出码 0 表示全部环节通过。
"""

import json
import os
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

# Windows 控制台默认 GBK，中文输出会乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 与用户日常浏览器完全隔离：独立端口 + 独立 profile
VERIFY_PORT = 9333
VERIFY_PROFILE = BASE / "browser_data" / "account_verify"

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")


def _browser_process(psutil, profile_dir):
    """按独立 profile 目录反查 Chrome 主进程。

    不用 net_connections 找调试端口：Windows 上非管理员常常读不到别的进程的
    连接表。主进程的特征是带 --user-data-dir 且不带 --type=（渲染子进程才有）。
    """
    marker = str(profile_dir)
    for proc in psutil.process_iter(["name"]):
        try:
            if (proc.info["name"] or "").lower() not in ("chrome.exe", "msedge.exe"):
                continue
            cmd = proc.cmdline()
            joined = " ".join(cmd)
            if marker in joined and not any(a.startswith("--type=") for a in cmd):
                return proc, joined
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            continue
    return None, ""


def main() -> int:
    import psutil
    from boss_bot.unified_config import UnifiedConfig, resolve_path
    from boss_bot.browser_launcher import (
        BrowserManager, _find_best_browser_path, _get_portable_browser_path,
    )

    print("== 1. 浏览器选择 ==")
    portable = _get_portable_browser_path()
    check("检测到内置破解版", bool(portable), portable)
    chosen_path, chosen_type = _find_best_browser_path("chrome")
    check("自动选择结果为破解版", chosen_type == "portable", f"{chosen_type} -> {chosen_path}")

    cfg = UnifiedConfig.load()
    check("配置未写死系统浏览器", not cfg.browser.chrome_path,
          f"chrome_path={cfg.browser.chrome_path!r}")

    print("== 2. 启动 ==")
    browser_cfg = cfg.browser
    mgr = BrowserManager(
        config=browser_cfg,
        account_index=9,
        port=VERIFY_PORT,
        user_data_dir=str(VERIFY_PROFILE),
    )
    instance = None
    try:
        instance = mgr.launch()
        page = instance._page or instance._tab
        check("浏览器实例已启动", page is not None)

        proc, cmdline = _browser_process(psutil, VERIFY_PROFILE)
        exe = proc.exe() if proc else ""
        check("找到本次启动的浏览器主进程", proc is not None, f"profile={VERIFY_PROFILE}")
        check("实际运行进程为破解版",
              os.path.normcase(exe) == os.path.normcase(portable or ""),
              f"exe={exe}")
        check("带 AutomationControlled 反检测参数",
              "AutomationControlled" in cmdline,
              "" if cmdline else "命令行不可读")
        check("使用独立调试端口", f"--remote-debugging-port={VERIFY_PORT}" in cmdline,
              f"port={VERIFY_PORT}")

        print("== 3. 登录态 ==")
        cookie_file = str(resolve_path(cfg.login.cookie_file))
        has_cookie = os.path.exists(cookie_file)
        check("Cookie 文件存在", has_cookie, cookie_file)
        if has_cookie:
            check("Cookie 注入成功", instance.load_cookies(cookie_file), cookie_file)

        page.get("https://www.zhipin.com/web/geek/chat")
        time.sleep(3)
        url = page.url or ""
        logged_in = not any(k in url for k in ("login", "/web/user", "passport"))
        print(f"    当前 URL: {url}")
        check("聊天页处于登录态", logged_in,
              "Cookie 已失效，需在破解版浏览器里重新扫码登录" if not logged_in else "")

        print("== 4. 选择器 ==")
        if logged_in:
            try:
                chats = page.run_js("""
                    var sels = [".chat-user-list .geek-item", ".chat-user-item",
                                "[role='listitem']"];
                    for (var i = 0; i < sels.length; i++) {
                        var n = document.querySelectorAll(sels[i]).length;
                        if (n > 0) return sels[i] + "=" + n;
                    }
                    return "";
                """)
                check("聊天列表可枚举", bool(chats), str(chats))
            except Exception as e:
                check("聊天列表可枚举", False, str(e)[:120])

            try:
                page.get("https://www.zhipin.com/web/geek/job-recommend")
                time.sleep(4)
                cards = page.run_js("""
                    var sels = [".job-card-body", ".job-card-wrap", "li.job-card-wrapper"];
                    for (var i = 0; i < sels.length; i++) {
                        var n = document.querySelectorAll(sels[i]).length;
                        if (n > 0) return sels[i] + "=" + n;
                    }
                    return "";
                """)
                check("推荐页岗位卡片可取到", bool(cards), str(cards))
            except Exception as e:
                check("推荐页岗位卡片可取到", False, str(e)[:120])
        else:
            print("    (未登录，跳过选择器环节)")

        print("== 5. 双标签页隔离 ==")
        chat_tab = mgr.get_chat_page()
        greet_tab = mgr.get_greet_chat_tab()
        tabs_before = page.tab_count if hasattr(page, "tab_count") else None
        # 把回复引擎标签页切到前台，再关闭打招呼临时页 —— 老代码按「当前标签页」
        # 关闭，此时会误关回复页
        chat_tab.get("https://www.zhipin.com/web/geek/chat")
        time.sleep(1)
        mgr.close_greet_chat_tab()
        time.sleep(1)
        try:
            chat_url_after = chat_tab.url or ""
            alive = "zhipin.com" in chat_url_after
        except Exception as e:
            alive = False
            chat_url_after = str(e)
        check("关闭临时页后回复标签页仍可用", alive, f"url={chat_url_after[:60]}")
        check("打招呼临时页已置空", mgr._greet_chat_tab is None)

        print("== 6. 健康检查 ==")
        from boss_bot.page_handler import BossChatHandler
        handler = BossChatHandler(browser_manager=mgr, browser_instance=chat_tab)
        health = handler.check_health()
        check("健康检查返回已知状态", health in ("ok", "need_login", "captcha", "unknown"),
              f"health={health}")
    except Exception as e:
        import traceback
        check("流程未抛出异常", False, f"{type(e).__name__}: {e}")
        traceback.print_exc()
    finally:
        try:
            if mgr is not None:
                mgr.close()
        except Exception as e:
            print(f"  (关闭浏览器异常: {e})")

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n合计 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")
    if failed:
        print("失败项: " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
