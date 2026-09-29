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
        time.sleep(5)
        url = (tab.url or "") if tab else ""
        out["url"] = url
        # 被踢到登录页 = 没登录；停在 chat 页 = 登录着
        logged = "zhipin.com" in url and "login" not in url and "passport" not in url
        if logged:
            counts = tab.run_js("""
                var a = document.querySelectorAll('.chat-user-list .geek-item').length;
                var b = document.querySelectorAll('.list-box .item-box').length;
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
        # 只断开连接，不 quit：浏览器留着，方便未登录的号直接扫码
    return out


def main() -> int:
    from boss_bot.unified_config import UnifiedConfig

    cfg = UnifiedConfig.load()
    accounts = cfg.greet.accounts
    print(f"配置里共 {len(accounts)} 个账号\n", flush=True)
    bad = 0
    for idx, acc in enumerate(accounts):
        r = probe(idx, acc, cfg)
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
        print()
    print(f"未就绪账号 {bad} 个（浏览器都留着，未登录的直接在窗口里扫码）", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
