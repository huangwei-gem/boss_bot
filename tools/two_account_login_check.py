"""逐账号真实登录态体检 — 只读：不点「立即沟通」，不发送任何消息。

每个账号连自己专属的端口与 profile（9222+idx / browser_data/account_idx），
在**新标签页**里打开 BOSS 聊天页判断是否被踢到登录页，尽量不动用户当前那一页。

运行：python tools/two_account_login_check.py
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

CHAT_URL = "https://www.zhipin.com/web/geek/chat"

# 未登录时 BOSS 会把会话页重定向到 /web/user/，那才是登录页。第一版这里只排除了
# login/passport，于是把"已踢出登录"报成"已登录"，正好把今天最要紧的坏消息盖掉了。
LOGIN_PAGE_MARKS = ("/web/user", "/login", "passport.")
LOGGED_IN_MARKS = ("/web/geek/chat", "/web/geek/job", "job_detail")


def logged_in_from_url(url: str) -> bool:
    """会话页/岗位页留着 = 登录着；落到登录页或读不到 URL = 不算。"""
    u = (url or "").lower()
    if any(k in u for k in LOGIN_PAGE_MARKS):
        return False
    return any(k in u for k in LOGGED_IN_MARKS)


def probe(idx: int, acc, cfg) -> dict:
    from boss_bot.browser_launcher import (BrowserManager, _is_port_open,
                                           check_cookie_valid_simple)

    cookie_file = BASE / (acc.cookie_file or "zhipin_cookies.json")
    simple = check_cookie_valid_simple(str(cookie_file))
    port = 9222 + idx
    out = {
        "index": idx, "name": getattr(acc, "name", f"账号{idx}"), "port": port,
        "profile": str(BASE / "browser_data" / f"account_{idx}"),
        "port_open": _is_port_open("127.0.0.1", port),
        "cookie_file": cookie_file.name,
        "cookie_ok": bool(simple.get("valid")),
        "cookie_reason": simple.get("reason", ""),
        "live_logged_in": None, "url": "", "evidence": "", "error": "",
    }

    mgr = BrowserManager(config=cfg.browser, account_index=idx, port=port,
                         user_data_dir=str(BASE / "browser_data" / f"account_{idx}"))
    tab = None
    try:
        inst = mgr.launch()
        tab = inst.new_tab(CHAT_URL)
        # BOSS 是 SPA：未登录时会先渲染会话页再跳 /web/user。等 URL 连读两次一致
        # 再定罪，只看一眼就是把"来不及看"当成结论。
        deadline = time.time() + 8
        url = ""
        while time.time() < deadline:
            cur = (tab.url or "") if tab else ""
            if cur and cur == url:
                break
            url = cur
            time.sleep(0.5)
        url = ((tab.url or "") if tab else "") or url
        out["url"] = url
        logged = logged_in_from_url(url)
        if logged:
            counts = tab.run_js("""
                var a = document.querySelectorAll('.friend-content').length;
                var b = document.querySelectorAll('.chat-user-list .geek-item').length;
                var t = document.body.innerText || '';
                var m = t.match(/(\\d+)\\s*条?未读/);
                return JSON.stringify({users: Math.max(a, b), unread: m ? m[1] : '0'});
            """)
            out["evidence"] = str(counts)
        else:
            out["evidence"] = "页面停在登录/跳转页"
        out["live_logged_in"] = logged
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:160]}"
    finally:
        try:
            if tab is not None:
                tab.close()
        except Exception:
            pass
        # 只断开连接，不 quit：浏览器留着，方便未登录的号直接扫码，也留给回存用
    return out, mgr


def salvage_missing_cookie_file(idx: int, acc, mgr, out) -> str:
    """Cookie 文件不见了但浏览器里还有登录项时，把会话存回文件。

    只补"文件不存在"这一种情况：文件在就说明判定链认为它有效，不该被这里覆盖。
    需要这个是因为 2026-09-29 之前那版登录判定误判一次就直接删文件，
    用户既丢会话又丢了排查依据。
    """
    from boss_bot.browser_launcher import BOSS_AUTH_COOKIES
    from boss_bot.unified_config import resolve_path

    path = resolve_path(acc.cookie_file or "zhipin_cookies.json")
    if path.exists():
        return ""
    if mgr is None:
        return "浏览器没连上，无法回存"
    try:
        cookies = [c for c in (mgr.get_instance()._get_all_cookies() or []) if isinstance(c, dict)]
    except Exception as e:
        return f"读浏览器 Cookie 失败: {str(e)[:60]}"
    live = [c for c in cookies if c.get("name") in BOSS_AUTH_COOKIES and c.get("value")]
    if not live:
        return f"浏览器里也没有登录 Cookie（{','.join(BOSS_AUTH_COOKIES)}），只能重新扫码"
    try:
        mgr.get_instance().save_cookies(str(path))
    except Exception as e:
        return f"回存失败: {str(e)[:60]}"
    return (f"已从浏览器回存 {len(cookies)} 个 Cookie（含 "
            f"{','.join(sorted({c['name'] for c in live}))}）→ {path.name}")


def main() -> int:
    from boss_bot.unified_config import UnifiedConfig

    cfg = UnifiedConfig.load()
    accounts = cfg.greet.accounts
    do_salvage = "--salvage" in sys.argv
    print(f"配置里共 {len(accounts)} 个账号\n", flush=True)
    bad = 0
    for idx, acc in enumerate(accounts):
        r, mgr = probe(idx, acc, cfg)
        print(f"== 账号 {idx}「{r['name']}」 ==", flush=True)
        print(f"  端口 {r['port']}  {'已被本 profile 占用' if r['port_open'] else '空闲'}"
              f" / profile {r['profile']}")
        print(f"  Cookie 文件 {r['cookie_file']}: "
              f"{'有效' if r['cookie_ok'] else '无效'} — {r['cookie_reason']}")
        if r["error"]:
            print(f"  实连失败: {r['error']}")
            bad += 1
        else:
            print(f"  实连登录态: {'已登录' if r['live_logged_in'] else '未登录'}"
                  f" — {r['url'][:80]}")
            print(f"  证据: {r['evidence']}")
            if not r["live_logged_in"]:
                bad += 1
        if do_salvage:
            note = salvage_missing_cookie_file(idx, acc, mgr, r)
            if note:
                print(f"  回存: {note}")
        print()
    print(f"未就绪账号 {bad} 个（浏览器都留着，未登录的直接在窗口里扫码）", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
