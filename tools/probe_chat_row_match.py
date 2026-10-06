# -*- coding: utf-8 -*-
"""查"侧栏滚到底、搜索也找不到会话"到底是真没这个会话，还是公司名对不上。

起因：2026-10-07 今日一轮里 156 次跳过、去重 96 个会话，其中 28 个的存档最后一条
消息就是今天的。行定位用的是 (姓名, 公司) 全等：`name === want.n && comp === want.c`，
而存档文件名里的公司是采集时截过的（实测有 7 个直接以 "..." 结尾）。
公司差一个字符就整条会话点不开，欠着 HR 的回复就永远回不了。

隔离方式（红线）：
- 自己起 cloakbrowser，独立端口 + 临时 user-data-dir，绝不连线上 9222/9223；
- Cookie 文件只读，跑前跑后各算一次 sha256，必须一模一样；
- 只读侧栏文本，不点任何会话行、不点发送/打招呼。

用法：
    PYTHONUTF8=1 python tools/probe_chat_row_match.py --account 0
    PYTHONUTF8=1 python tools/probe_chat_row_match.py --account 1 --limit 40
"""
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PYTHONUTF8", "1")

from boss_bot.browser_launcher import launch_browser  # noqa: E402
from boss_bot.unified_config import UnifiedConfig, resolve_path  # noqa: E402

CHAT_URL = "https://www.zhipin.com/web/geek/chat"
ROOT = Path(__file__).resolve().parent.parent

# 滚一屏 + 抓当前渲染的 (姓名, 公司)。虚拟列表要给渲染留时间，
# 所以滚动和抓取都在 Python 里一步步来，不在一个 JS 调用里闷头滚到底
# ——那样测到的是渲染速度，不是侧栏真实内容。
GRAB_ROWS_JS = r'''(
    function () {
        var out = [];
        var items = document.querySelectorAll(".friend-content");
        for (var i = 0; i < items.length; i++) {
            var el = items[i];
            var n = el.querySelector(".name-text");
            var name = n ? n.textContent.trim() : "";
            if (!name) continue;
            var box = el.querySelector(".name-box"), spans = [];
            if (box) {
                for (var k = 0; k < box.children.length; k++) {
                    var c = box.children[k];
                    if (c.tagName === "SPAN") {
                        var t = (c.textContent || "").trim();
                        if (t) spans.push(t);
                    }
                }
            }
            out.push({n: name, c: spans.length > 1 ? spans[1] : ""});
        }
        var box = document.querySelector(".user-list-content");
        return {rows: out,
                pos: box ? String(Math.round(box.scrollTop)) + "/" + String(box.scrollHeight) : "no-box"};
    }
)()'''

SCROLL_ONE_JS = r'''(
    function() {
        var box = document.querySelector(".user-list-content");
        if (!box) return "no-box";
        box.scrollTop = box.scrollTop + 700;
        box.dispatchEvent(new Event("scroll", {bubbles: true}));
        return String(Math.round(box.scrollTop)) + "/" + String(box.scrollHeight);
    }
)()'''


def dump_sidebar(instance):
    """边滚边收整个侧栏，按 (姓名, 公司) 去重。返回 {姓名: [公司...]}"""
    merged = {}
    prev = None
    for _ in range(80):
        snap = instance.run_js(GRAB_ROWS_JS, as_expr=True) or {}
        for r in snap.get("rows") or []:
            merged.setdefault(r["n"], set()).add(r["c"])
        pos = instance.run_js(SCROLL_ONE_JS, as_expr=True)
        time.sleep(0.5)
        if pos == "no-box" or pos == prev:
            break
        prev = pos
    return {n: sorted(c) for n, c in merged.items()}


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def _cookie_file(account_index: int) -> Path:
    cfg = UnifiedConfig()
    accounts = cfg.greet.accounts or []
    name = "zhipin_cookies.json"
    if account_index < len(accounts):
        name = accounts[account_index].cookie_file or name
    return Path(str(resolve_path(name)))


def skipped_targets(limit: int):
    """今天日志里"找不到会话"的 (姓名, 公司)，按出现次数排序。"""
    log = ROOT / "logs" / "boss_bot.log"
    if not log.is_file():
        return []
    text = log.read_text(encoding="utf-8", errors="replace")
    today = [l for l in text.splitlines() if l.startswith("2026-10-07")]
    pairs = re.findall(r"找不到会话 \[([^|\]]+)\|([^\]]+)\]", " ".join(today))
    counts = {}
    for n, c in pairs:
        counts[(n, c)] = counts.get((n, c), 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])[:limit]


READ_SEARCH_JS = r'''(
    function () {
        var items = document.querySelectorAll("li.search-list");
        var out = [];
        for (var i = 0; i < items.length; i++) {
            var n = items[i].querySelector(".boss-name");
            var c = items[i].querySelector(".company-name");
            var sec = items[i].querySelector(".sec-line");
            out.push({n: n ? n.textContent.trim() : "",
                      c: c ? c.textContent.trim() : "",
                      j: sec ? sec.textContent.trim().replace(/\\s+/g, "") : ""});
        }
        return out;
    }
)()'''


def _norm(s: str) -> str:
    """去掉所有空白再比：浮层和侧栏的渲染会插换行/全角空格。"""
    return "".join((s or "").split())


def classify_search(ph, name, company):
    """只读地走一遍搜索认人逻辑：写字、读浮层，但不点那一行。

    不点是因为点开会把 HR 的消息标成已读，线上引擎的红点采集就漏了这条——
    这里是取证，不是替它回话。判定规则和 _SEARCH_RESULT_JS 保持一致。
    """
    if not ph._set_contact_filter(name):
        return "no_input"
    deadline = time.time() + 6
    rows = []
    while time.time() < deadline:
        rows = ph.page.run_js(READ_SEARCH_JS, as_expr=True) or []
        if rows:
            break
        time.sleep(0.4)
    ph._clear_contact_filter()
    if not rows:
        return "no_result"
    want_c = _norm(company)
    for r in rows:
        got_c = _norm(r["c"])
        if _norm(r["n"]) == _norm(name) and got_c and (
                want_c == got_c or want_c in got_c or got_c in want_c):
            return "ok"
    same_name = [r for r in rows if _norm(r["n"]) == _norm(name)]
    return "unique_name" if len(same_name) == 1 else "name_only"


def run_search_phase(instance, targets, count):
    """搜索浮层那一级：这些被跳过的会话里，有多少其实搜得到。

    只写字、读浮层，不点那一行——点了就把 HR 的消息标成已读，线上轮会漏红点。
    """
    if not count or instance is None:
        return {}
    from boss_bot.page_handler import BossChatHandler

    class _Shim:
        def run_js(self, script, as_expr=False):
            return instance.run_js(script, as_expr=as_expr)

    ph = BossChatHandler.__new__(BossChatHandler)
    ph.page = _Shim()
    verdicts = {}
    for ((n, c), _cnt) in targets[:count]:
        try:
            v = classify_search(ph, n, c)
        except Exception as e:
            v = f"error:{type(e).__name__}"
        verdicts[v] = verdicts.get(v, 0) + 1
    print("搜索浮层判定:", json.dumps(verdicts, ensure_ascii=False), flush=True)
    print("  ok=公司对上 unique_name=只剩这一行(修复后才点得开) "
          "name_only=多个同名对不上 no_result=搜不到", flush=True)
    return verdicts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, default=0)
    ap.add_argument("--port", type=int, default=9470)
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--search", type=int, default=0,
                    help="再用联系人搜索浮层核对前 N 个会话（0=不测）")
    args = ap.parse_args()

    cookie_path = _cookie_file(args.account)
    sha_before = _sha(cookie_path)
    targets = skipped_targets(args.limit)
    print(f"账号 {args.account} Cookie={cookie_path.name} sha={sha_before} "
          f"待查会话 {len(targets)} 个", flush=True)

    profile = Path(tempfile.mkdtemp(prefix="boss_row_match_"))
    instance = None
    by_name = {}
    search_verdicts = {}
    try:
        instance = launch_browser(headless=True, port=args.port,
                                  user_data_dir=str(profile),
                                  viewport_width=1280, viewport_height=800)
        if not instance.load_cookies(str(cookie_path)):
            print("Cookie 加载失败", flush=True)
            return 2
        instance.get(CHAT_URL)
        time.sleep(6)
        by_name = dump_sidebar(instance)
        print(f"侧栏抓到 {sum(len(v) for v in by_name.values())} 行、"
              f"{len(by_name)} 个姓名", flush=True)
        search_verdicts = run_search_phase(instance, targets, args.search)
    finally:
        if instance is not None:
            try:
                instance.quit()
            except Exception:
                pass
        sha_after = _sha(cookie_path)
        print(f"Cookie sha after={sha_after} "
              f"{'UNCHANGED' if sha_after == sha_before else '!! CHANGED !!'}", flush=True)

    name_gone = comp_diff = comp_ok = ellipsis = 0
    for ((n, c), _cnt) in targets:
        if c.endswith("...") or c.endswith("…"):
            ellipsis += 1
        comps = by_name.get(n)
        if comps is None:
            name_gone += 1
        elif c in comps:
            comp_ok += 1
        else:
            comp_diff += 1
            if comp_diff <= 8:
                print(f"  姓名在、公司全等失败: [{n}] 存档={c!r} 侧栏={comps!r}", flush=True)
    print(json.dumps({
        "侧栏行数": sum(len(v) for v in by_name.values()),
        "待查会话": len(targets),
        "姓名根本不在侧栏": name_gone,
        "姓名在但公司全等失败": comp_diff,
        "姓名公司都吻合(不该失败)": comp_ok,
        "存档公司带省略号": ellipsis,
        "搜索浮层判定": search_verdicts,
    }, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
