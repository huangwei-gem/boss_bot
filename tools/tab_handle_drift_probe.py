# -*- coding: utf-8 -*-
"""真机取证：搜索页句柄到底落在哪个标签页上。

为什么要这个：用户报"第二个浏览器打开后无内容"。代码上
`BrowserManager.get_search_page()` 存的是浏览器级对象而不是某个标签页
（browser_launcher.py:1234 `self._search_tab = self._instance`），而
`BrowserInstance` 的 ele/eles/get/url 全转发给 `_get_active()`。要判定这是不是
真凶，只能起真号、真开一个聊天标签页再关掉，看搜索页那次读还落不落得回原页面。

只读边界（硬要求）：
- 不点「立即沟通」「继续沟通」，不找输入框，不碰任何发送路径；
- 结束走 `close()`，它不保存 Cookie；脚本另外用 sha 核对两个 Cookie 文件前后一致，
  不一致就如实报，不解释成"正常"。
- 标签页增删用 `get_greet_chat_tab()` / `close_greet_chat_tab()` 复现（真实投递里
  就是它在动标签页），但不开任何岗位页，所以不会有发送风险。

用法：
    python -X utf8 tools/tab_handle_drift_probe.py            # 两个号都测
    python -X utf8 tools/tab_handle_drift_probe.py --only 1    # 只测账号2
退出码：0=没有句柄漂移；1=复现出漂移；2=环境/启动问题。
"""
import argparse
import hashlib
import io
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from boss_bot.browser_launcher import BrowserManager
from boss_bot.unified_config import UnifiedConfig, resolve_path

ROOT = Path(__file__).resolve().parents[1]
CARD_SELECTOR = ".job-card-wrap"


def sha_of(name: str) -> str:
    p = ROOT / name
    if not p.exists():
        return "<缺失>"
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12]


def cdp_tabs(port: int) -> list:
    """CDP 自己报的标签页清单 —— 这是唯一不依赖句柄语义的第三方证据"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=4) as r:
            rows = json.loads(r.read().decode("utf-8"))
        return [{"id": t.get("id", "")[:8], "type": t.get("type"),
                 "url": (t.get("url") or "")[:70], "title": (t.get("title") or "")[:24]}
                for t in rows if t.get("type") == "page"]
    except Exception as e:
        return [{"error": str(e)[:60]}]


def read_search(mgr, sp) -> dict:
    """读搜索页那边的所见：URL + 岗位卡片数"""
    out = {"url": "", "cards": -1, "err": ""}
    try:
        out["url"] = (sp.url or "")[:80]
    except Exception as e:
        out["err"] = f"url: {str(e)[:50]}"
    try:
        out["cards"] = len(sp.eles(CARD_SELECTOR, timeout=3) or [])
    except Exception as e:
        out["cards"] = -1
        out["err"] = (out["err"] + f" | eles: {str(e)[:50]}").strip(" |")
    return out


def probe_account(idx: int, cfg) -> dict:
    port = cfg.browser.debug_port + idx
    user_data_dir = str(resolve_path(cfg.browser.user_data_dir
                                     or Path("browser_data") / f"account_{idx}"))
    mgr = BrowserManager(config=cfg.browser, account_index=idx,
                         port=port, user_data_dir=user_data_dir)
    report = {"idx": idx, "port": port, "steps": [], "alias_is_instance": None,
              "cookie_loaded": None, "drift": False}
    try:
        mgr.launch()
    except Exception as e:
        report["fatal"] = f"启动失败: {str(e)[:120]}"
        return report

    try:
        report["cookie_loaded"] = bool(mgr._cookie_file)
        sp = mgr.get_search_page()
        report["alias_is_instance"] = (sp is mgr._instance)
        report["steps"].append(["①取搜索页", read_search(mgr, sp), cdp_tabs(port)])

        # 复现真实运行里的标签页增删：回复侧建 _chat_tab，打招呼侧建/关临时会话 tab
        cp = mgr.get_chat_page()
        chat_url = ""
        try:
            chat_url = (cp.url or "")[:60]
        except Exception:
            pass
        report["steps"].append(["②建聊天标签页(回复侧)", {"url": chat_url}, cdp_tabs(port)])

        tmp = mgr.get_greet_chat_tab("https://www.zhipin.com/web/geek/job-recommend")
        try:
            tmp_url = (tmp.url or "")[:60]
        except Exception:
            tmp_url = "?"
        report["steps"].append(["③建临时会话标签页", {"url": tmp_url}, cdp_tabs(port)])

        mgr.close_greet_chat_tab()
        report["steps"].append(["④关临时会话标签页", read_search(mgr, sp), cdp_tabs(port)])

        try:
            cp.close_current_tab()
        except Exception as e:
            report["steps"].append(["④b 关聊天标签页失败", {"err": str(e)[:60]}, []])
        after = read_search(mgr, sp)
        report["steps"].append(["⑤关聊天标签页后再读搜索页", after, cdp_tabs(port)])

        before = report["steps"][0][1]
        # 漂移判据：搜索页原本读得到卡片，动过别的标签页之后读不到了 / URL 变了
        if before["cards"] > 0 and (after["cards"] == 0 or after["cards"] == -1):
            report["drift"] = True
        elif before["url"] and after["url"] and before["url"] != after["url"]:
            report["drift"] = True
    finally:
        mgr.close()
    return report


def process_owner_table() -> list:
    """当前真在跑的浏览器进程：PID / 端口 / user-data-dir。

    并发场景的判据之一：如果两个账号的进程只有一个是带 profile 起来的，
    那"第二个浏览器打开后无内容"就不是句柄问题，是它根本没起来第二个浏览器。
    """
    from boss_bot.browser_launcher import _browser_owners

    rows = []
    for o in _browser_owners() or []:
        rows.append({"pid": o.get("pid"), "port": o.get("port"),
                     "profile": (o.get("user_data_dir") or "")[-28:]})
    return rows


def probe_concurrent(idxs: list, cfg) -> dict:
    """两个实例同时在跑 —— 这才是用户报的场景（一个一个起测不出并发问题）"""
    base_port = cfg.browser.debug_port
    mgrs = {}
    report = {"steps": [], "owners": []}
    for i in idxs:
        port = base_port + i
        udd = str(resolve_path(cfg.browser.user_data_dir
                               or Path("browser_data") / f"account_{i}"))
        mgrs[i] = BrowserManager(config=cfg.browser, account_index=i,
                                 port=port, user_data_dir=udd)
        try:
            mgrs[i].launch()
            report["steps"].append([f"账号{i + 1} launch(port={port})", {"ok": True}, []])
        except Exception as e:
            report["steps"].append([f"账号{i + 1} launch(port={port})",
                                    {"fatal": str(e)[:140]}, []])
            report.setdefault("fatal", f"账号{i + 1}: {str(e)[:140]}")

    try:
        # 1) 两个号各自取搜索页
        search = {}
        for i, mgr in mgrs.items():
            try:
                search[i] = mgr.get_search_page()
            except Exception as e:
                report["steps"].append([f"账号{i + 1} get_search_page",
                                        {"err": str(e)[:100]}, []])
        # 2) 交叉动作：让"后起来的那个号"（idxs[-1]）建/关标签页，每步看两个端口。
        #    为什么只动它：真实运行里账号2 的标签页建得最晚，最先漂；两个都动就分不清
        #    是谁把谁拽走了。
        mover = idxs[-1]
        for label, act in (
            ("建聊天标签页(回复侧专用)", lambda m: m.get_chat_page()),
            ("建临时会话标签页", lambda m: m.get_greet_chat_tab(
                "https://www.zhipin.com/web/geek/job-recommend")),
            ("关临时会话标签页", lambda m: m.close_greet_chat_tab()),
        ):
            if mover in mgrs:
                try:
                    act(mgrs[mover])
                except Exception as e:
                    report["steps"].append([f"账号{mover + 1} {label}",
                                            {"err": str(e)[:80]}, []])
            for j in mgrs:
                seen = read_search(None, search[j]) if j in search else {"url": "?"}
                report["steps"].append(
                    [f"[{label}] 账号{j + 1} 搜索页所见", seen,
                     cdp_tabs(base_port + j)])
        report["owners"] = process_owner_table()
        # 3) 判读：搜索页所见 URL 是否还是自己那个端口上的页面
        for j in mgrs:
            if j not in search:
                continue
            seen = read_search(None, search[j])
            tabs = cdp_tabs(base_port + j)
            here = any(seen.get("url", "")[:30] in (t.get("url") or "") for t in tabs)
            report["steps"].append([f"判定 账号{j + 1}", {"url": seen.get("url"),
                                                          "cards": seen.get("cards"),
                                                          "url在本端口上": here}, tabs])
    finally:
        for mgr in mgrs.values():
            mgr.close()
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", type=int, default=-1, help="只测这个 account_index")
    ap.add_argument("--concurrent", action="store_true",
                    help="两个实例同时在跑（用户报的场景）")
    args = ap.parse_args()

    cfg = UnifiedConfig.load()
    accounts = cfg.greet.accounts
    idxs = [args.only] if args.only >= 0 else [i for i, a in enumerate(accounts)
                                              if a.enabled]
    if not idxs:
        print("没有启用的账号，测不了")
        return 2

    sha_before = {f"account{i}": sha_of(a.cookie_file) for i, a in enumerate(accounts)
                  if a.cookie_file}
    print(f"Cookie 文件 sha（跑前）: {sha_before}\n")

    drifted = False
    if args.concurrent:
        if len(idxs) < 2:
            print("并发模式至少要两个启用的账号")
            return 2
        rep = probe_concurrent(idxs, cfg)
        print("=" * 78)
        print(f"并发：账号 {[i + 1 for i in idxs]}（端口 "
              f"{[cfg.browser.debug_port + i for i in idxs]}）")
        print("=" * 78)
        print(f"当前浏览器进程: {rep['owners']}")
        if rep.get("fatal"):
            print(f"⚠️ 启动失败：{rep['fatal']}")
            return 2
        for name, seen, tabs in rep["steps"]:
            print(f"  {name}: {seen}")
            if tabs:
                print(f"      本端口 page 标签页: {tabs}")
    else:
        for i in idxs:
            print("=" * 78)
            print(f"账号 {i + 1}（端口 {cfg.browser.debug_port + i}）")
            print("=" * 78)
            rep = probe_account(i, cfg)
            if rep.get("fatal"):
                print(f"  ⚠️ {rep['fatal']}")
                return 2
            print(f"  搜索页句柄是不是浏览器级对象: {rep['alias_is_instance']}"
                  f"（True 就说明它不绑标签页）")
            for name, seen, tabs in rep["steps"]:
                print(f"  {name}: url={seen.get('url', '')!r} cards={seen.get('cards', '')}"
                      f"{' err=' + seen['err'] if seen.get('err') else ''}")
                print(f"      CDP 此刻的 page 标签页: {tabs}")
            print(f"  → 漂移: {'是' if rep['drift'] else '否'}")
            drifted = drifted or rep["drift"]

    sha_after = {f"account{i}": sha_of(a.cookie_file) for i, a in enumerate(accounts)
                 if a.cookie_file}
    print(f"\nCookie 文件 sha（跑后）: {sha_after}")
    if sha_after != sha_before:
        print("  ❗ 有 Cookie 文件被改动过，本次取证不该写它 —— 立刻人工确认")
    else:
        print("  ✅ 两个 Cookie 文件前后一致，登录态没被动过")

    if drifted is None:
        return 2
    return 1 if drifted else 0


if __name__ == "__main__":
    sys.exit(main())
