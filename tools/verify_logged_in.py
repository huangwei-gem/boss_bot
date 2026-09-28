"""登录态下的真机链路验证 — 全程只读，不点「立即沟通」、不发送任何消息。

复用 cloakbrowser/User Data 里已有的登录会话，用项目自己的类去跑真实页面，
验证选择器、双标签页隔离、健康检查在 BOSS 直聘当前版本上是否真的可用。

运行：python tools/verify_logged_in.py
"""

import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PORT = 9348
SHOT = BASE / "tools"

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}", flush=True)


def main() -> int:
    from boss_bot.unified_config import UnifiedConfig, resolve_path
    from boss_bot.browser_launcher import BrowserManager
    from boss_bot.page_handler import BossChatHandler
    from boss_bot.greet_engine import SELECTOR_JOB_NAME, SELECTOR_REC_JOB_LIST

    cfg = UnifiedConfig.load()
    # 用机器人实际使用的 profile，并像主循环一样靠 Cookie 文件恢复登录态，
    # 这样这个脚本可以在扫码之后反复运行，不需要人工再登录一次。
    profile = BASE / "browser_data" / "account_0"
    mgr = BrowserManager(config=cfg.browser, account_index=8, port=PORT,
                         user_data_dir=str(profile))
    try:
        inst = mgr.launch()
        page = inst._page or inst._tab
        cookie_file = resolve_path(cfg.login.cookie_file)
        if cookie_file.exists():
            inst.load_cookies(str(cookie_file))

        print("== 登录态 ==", flush=True)
        page.get("https://www.zhipin.com/web/geek/chat")
        time.sleep(5)
        url = page.url or ""
        logged = "chat" in url
        check("聊天页保持登录", logged, url[:70])
        if not logged:
            print("  (未登录，后续环节无意义，直接结束)")
            return 1

        print("== 回复侧选择器 ==", flush=True)
        counts = page.run_js("""
            var sels = ['.chat-user-list .geek-item', '.chat-user-item',
                        '.list-box .item-box', '.chat-item', '[role="listitem"]'];
            var out = [];
            for (var i = 0; i < sels.length; i++) {
                var n = document.querySelectorAll(sels[i]).length;
                if (n > 0) out.push(sels[i] + '=' + n);
            }
            return out.join(', ');
        """)
        check("会话列表选择器命中", bool(counts), str(counts))
        unread = page.run_js("""
            var t = document.body.innerText || '';
            var m = t.match(/(\\d+)\\s*条?未读/); return m ? m[1] : '';
        """)
        print(f"    未读提示: {unread!r}")
        try:
            page.get_screenshot(str(SHOT / "ui_05_chat.png"))
        except Exception as e:
            print(f"    (截图跳过: {str(e)[:60]})")

        # BossChatHandler 需要 BrowserInstance 包装对象，不是底层 ChromiumPage
        handler = BossChatHandler(browser_manager=mgr,
                                  browser_instance=mgr.get_chat_page())
        health = handler.check_health()
        check("健康检查返回 ok", health == "ok", f"health={health}")

        print("== 打招呼侧选择器 ==", flush=True)
        page.get("https://www.zhipin.com/web/geek/job-recommend")
        time.sleep(6)
        for _ in range(4):
            page.scroll.down(900)
            time.sleep(1)
        cards = page.eles(SELECTOR_JOB_NAME)
        check("岗位名称选择器命中", len(cards) > 0, f"{SELECTOR_JOB_NAME} -> {len(cards)} 条")
        rec = page.eles(SELECTOR_REC_JOB_LIST)
        check("推荐列表容器命中", len(rec) > 0, f"{SELECTOR_REC_JOB_LIST} -> {len(rec)}")
        names = [c.text.strip() for c in cards[:5]]
        print(f"    样例岗位: {names}")
        btns = page.eles("text=立即沟通")
        check("「立即沟通」按钮可定位（只定位不点击）", len(btns) > 0, f"{len(btns)} 个")
        try:
            page.get_screenshot(str(SHOT / "ui_06_jobs.png"))
        except Exception as e:
            print(f"    (截图跳过: {str(e)[:60]})")

        print("== 双标签页隔离（真实页面）==", flush=True)
        chat_tab = mgr.get_chat_page()
        greet_tab = mgr.get_greet_chat_tab()
        chat_tab.get("https://www.zhipin.com/web/geek/chat")
        time.sleep(2)
        before = chat_tab.url or ""
        mgr.close_greet_chat_tab()
        time.sleep(1)
        after = ""
        try:
            after = chat_tab.url or ""
        except Exception as e:
            after = f"<异常 {e}>"
        check("关闭临时页后回复页仍在聊天页", after == before and "chat" in after,
              f"{before[:40]} -> {after[:40]}")
    except Exception as e:
        import traceback
        check("流程未抛出异常", False, f"{type(e).__name__}: {e}")
        traceback.print_exc()
    finally:
        try:
            mgr.close()
        except Exception as e:
            print(f"  (关闭异常: {e})")

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n合计 {len(results)} 项，失败 {len(failed)}", flush=True)
    if failed:
        print("失败项: " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
