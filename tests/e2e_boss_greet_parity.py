"""四方一致性审计（只读）：BOSS 端会话列表 ↔ 打招呼记录 ↔ 回复记录 ↔ 日志。

红线：全程不点「发送」「打招呼」「继续沟通」，只读消息列表 DOM。
Cookie 文件跑前跑后 sha256 必须一致——本脚本不写 Cookie，
但浏览器进程自己会落盘，所以两头都要验。

用法：python tests/e2e_boss_greet_parity.py [--account 0]
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import sys
import tempfile
import time
import shutil
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from boss_bot.browser_launcher import BrowserInstance, BrowserManager  # noqa: E402
from boss_bot.unified_config import BASE_DIR, UnifiedConfig  # noqa: E402

COOKIE_FILES = ("zhipin_cookies.json", "zhipin_cookies_1.json")
CHAT_URL = "https://www.zhipin.com/web/geek/chat"

LIST_JS = r'''(
    function() {
        var out = [];
        var items = document.querySelectorAll(".friend-content");
        for (var i = 0; i < items.length; i++) {
            var el = items[i];
            var txt = function(sel) {
                var n = el.querySelector(sel);
                return n ? (n.textContent || "").trim() : "";
            };
            var spans = [];
            var box = el.querySelector(".name-box");
            if (box) {
                var kids = box.querySelectorAll("span");
                for (var k = 0; k < kids.length; k++) {
                    var t = (kids[k].textContent || "").trim();
                    if (t) spans.push(t);
                }
            }
            out.push({
                name: txt(".name-text"),
                company: spans.length > 1 ? spans[1] : "",
                // 时间标签实测挂在 .time 上（"刚刚/12分钟前/14:23/昨天/09月29日"）
                time: txt(".time") || txt(".time-text") || txt(".item-time"),
                last: txt(".last-msg-text")
            });
        }
        return JSON.stringify(out);
    }
)()'''

# 消息列表是虚拟滚动：DOM 里同时只挂 40 条，剩下的靠 .user-list-content 的占位高度
# （实测 7842px）在滚动时增量渲染，还带无限加载。
# 只读一次就当"BOSS 一共 40 条会话"会得出完全错误的结论。
# 注意：整段必须是自执行 IIFE —— DrissionPage 的 as_expr 只把脚本当表达式求值，
# 光一个 function(...) 定义会返回函数对象本身，滚动作从而一次都没发生（踩过）。
SCROLL_JS = r'''(function(){
  var box = document.querySelector(".user-list-content");
  if (!box) return "no-box";
  box.scrollTop = box.scrollTop + 700;
  box.dispatchEvent(new Event("scroll", {bubbles: true}));
  return String(Math.round(box.scrollTop)) + "/" + String(box.scrollHeight);
})()'''


def sha_of_cookies() -> dict:
    res = {}
    for name in COOKIE_FILES:
        p = Path(BASE_DIR) / name
        res[name] = hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.exists() else "缺失"
    return res


def matches_day(label: str, day: str) -> bool:
    """BOSS 列表里的时间标签是不是指向审计日。

    标签是相对串（刚刚 / 12分钟前 / 14:23 / 昨天 / 09月29日），
    所以要先看审计日离今天几天：跨日的运行拿"今天"去比会全盘对不上。
    """
    if not label:
        return False
    d = datetime.strptime(day, "%Y-%m-%d").date()
    delta = (datetime.now().date() - d).days
    if delta == 0:
        return (label == "刚刚" or "分钟前" in label or "小时前" in label
                or label.startswith("今天") or bool(re.fullmatch(r"\d\d:\d\d", label)))
    if delta == 1:
        return label == "昨天"
    return label == f"{d.month:02d}月{d.day:02d}日"


def covered(comp: str, boss_companies: set) -> bool:
    """记录里的公司名能不能在 BOSS 会话列表里找到一个对应。

    打招呼记录存的公司常带地区尾巴（"万物集采长沙·长沙县·黄兴镇"），
    会话列表只给"万物集采"，逐字比会一片"找不到"——那是字段脏，不是没发出去。
    """
    for b in boss_companies:
        if comp in b or b in comp:
            return True
    return False


def read_boss_list(idx: int, max_rounds: int = 80) -> list:
    """用该账号的登录态读完消息列表。只读，不点任何发送类按钮。

    profile 走临时目录：登录态本来就靠 Cookie 注入（账号 profile 里没有登录），
    临时目录还顺带避免了和机器人正在用的 profile 抢锁。
    """
    acc = UnifiedConfig.load().greet.accounts[idx]
    cookie = str(Path(BASE_DIR) / (acc.cookie_file or "zhipin_cookies.json"))
    tmp = tempfile.mkdtemp(prefix=f"boss_parity_{idx}_")
    mgr = BrowserManager(
        config=type("C", (), {
            "headless": False,
            "user_agent": "",
            "proxy": "",
            "viewport_width": 1280,
            "viewport_height": 900,
            "chrome_path": "",          # 空 = 自动用项目内 cloakbrowser
            "browser_type": "chrome",
            "cookie_file": cookie,
            "user_data_dir": tmp,
        })(),
        account_index=idx,
        port=9360 + idx,
    )
    try:
        instance = mgr.launch()
        # 裸 tab 包一层 BrowserInstance：run_js(as_expr=) 只在包装类上转发过
        page = BrowserInstance(chrome_page=instance.new_tab(CHAT_URL))
        for _ in range(30):
            time.sleep(1)
            try:
                if "geek/chat" in (page.url or ""):
                    break
            except Exception:
                pass
        seen: dict = {}
        stagnant = 0
        prev_n = -1
        prev_pos = ""
        for _ in range(max_rounds):
            pos = str(page.run_js(SCROLL_JS, as_expr=True))
            time.sleep(0.6)
            for it in json.loads(page.run_js(LIST_JS, as_expr=True) or "[]"):
                if it.get("name"):
                    seen[(it["name"], it.get("company", ""), it.get("time", ""))] = it
            # 条数、滚动位置、总高度三者都不再变化才算读到底
            # （无限加载时 scrollHeight 会一边滚一边长，只看 scrollTop 到顶会早停）
            if len(seen) == prev_n and pos == prev_pos:
                stagnant += 1
            else:
                stagnant = 0
            prev_n, prev_pos = len(seen), pos
            if stagnant >= 3:
                break
        return list(seen.values())
    finally:
        mgr.close()
        shutil.rmtree(tmp, ignore_errors=True)


def latest_run_day() -> str:
    """最近一次真正跑过打招呼的那天——日志按天分文件，比对要指着同一天。"""
    gr = json.loads((Path(BASE_DIR) / "data" / "greet_records.json").read_text(encoding="utf-8"))
    gr = gr if isinstance(gr, list) else gr["records"]
    return max(r["timestamp"][:10] for r in gr) if gr else time.strftime("%Y-%m-%d")


def local_truth(idx: int, day: str) -> dict:
    gr = json.loads((Path(BASE_DIR) / "data" / "greet_records.json").read_text(encoding="utf-8"))
    gr = gr if isinstance(gr, list) else gr["records"]
    mine = [r for r in gr if r.get("account_index") == idx]
    sent = [r for r in mine if r.get("is_greeted")]
    rr = json.loads((Path(BASE_DIR) / "data" / "reply_records.json").read_text(encoding="utf-8"))
    rr = rr if isinstance(rr, list) else rr.get("records", [])
    rmine = [r for r in rr if r.get("account_index") == idx]
    today = [r for r in mine if r["timestamp"].startswith(day)]
    return {
        "greet_total": len(mine),
        "sent": sent,
        "greet_sent": len(sent),
        "greet_sent_today": sum(1 for r in sent if r["timestamp"].startswith(day)),
        "reply_total": len(rmine),
        "reply_chats": len({(r.get("chat_name"), r.get("job_name"))
                           for r in rmine if r.get("chat_name")}),
        "skip_reasons": dict(collections.Counter(
            (r.get("skip_reason") or "<无>")[:14] for r in mine)),
        "today_missing_greeting": sum(1 for r in today
                                      if "未配置招呼语" in (r.get("skip_reason") or "")),
        "today_ai_reject": sum(1 for r in today
                               if "AI判定不匹配" in (r.get("skip_reason") or "")),
        "today_records": len(today),
    }


def log_truth(idx_name: str, day: str) -> dict:
    """当天日志里这几类跳过各出现了几行——和记录条数应当 1:1。"""
    p = Path(BASE_DIR) / "logs" / f"boss_bot.log.{day}"
    if not p.exists():
        return {}
    tag = f"[{idx_name}]"
    lines = [l for l in p.read_text(encoding="utf-8", errors="replace").splitlines() if tag in l]
    return {
        "未配置招呼语": sum(1 for l in lines if "未配置招呼语" in l),
        "AI 判定不匹配": sum(1 for l in lines if "AI 判定不匹配，跳过" in l),
        "已沟通过": sum(1 for l in lines if "⏭️ 已沟通过" in l),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, default=-1, help="只审计某个账号（-1=全部）")
    args = ap.parse_args()
    cfg = UnifiedConfig.load()
    idxs = [args.account] if args.account >= 0 else list(range(len(cfg.greet.accounts)))

    before = sha_of_cookies()
    day = latest_run_day()
    problems = []
    notes = []
    try:
        for idx in idxs:
            name = cfg.greet.accounts[idx].name
            truth = local_truth(idx, day)
            boss = read_boss_list(idx)
            boss_today = [b for b in boss if matches_day(b.get("time", ""), day)]
            boss_companies = {re.sub(r"\s+", "", b.get("company", "")) for b in boss} - {""}
            sent_companies = {re.sub(r"\s+", "", r.get("company") or "") for r in truth["sent"]} - {""}
            missing = sorted(c for c in sent_companies if not covered(c, boss_companies))
            print(f"\n{'=' * 66}\n账号 {idx}（{name}）　审计日 {day}")
            print(f"  BOSS 会话列表读到 {len(boss)} 条，其中「{day}」{len(boss_today)} 条")
            print(f"  本地打招呼记录 {truth['greet_total']} 条 → 真正发出 {truth['greet_sent']} 条"
                  f"（{day} 发出 {truth['greet_sent_today']} 条）")
            print(f"  {day} 本地记录：共 {truth['today_records']} 条 / "
                  f"未配置招呼语 {truth['today_missing_greeting']} / AI 判不匹配 {truth['today_ai_reject']}")
            lg = log_truth(name, day)
            print(f"  {day} 日志行数：{lg or '日志文件不存在'}")
            if lg:
                # 2026-10-02 那份日志是修复前跑的：闸门和收尾各打一行，
                # "未配置招呼语"的行数正好是记录条数的两倍。比率算出来，
                # 2.0 归因为已知缺陷（已收敛成一行），不再算差异；
                # 既不 1:1 也不是 2:1 的，才是新问题。
                for label, rec_n in (("未配置招呼语", truth["today_missing_greeting"]),
                                     ("AI 判定不匹配", truth["today_ai_reject"])):
                    log_n = lg[label]
                    ratio = (log_n / rec_n) if rec_n else 0.0
                    print(f"    {label}: 日志 {log_n} 行 / 记录 {rec_n} 条 = {ratio:.2f}")
                    if rec_n and ratio not in (1.0, 2.0):
                        problems.append(f"账号{idx}: {label} 日志 {log_n} 行 ≠ 记录 {rec_n} 条"
                                        f"（比率 {ratio:.2f}）")
            print(f"  本地回复记录 {truth['reply_total']} 条，覆盖会话 {truth['reply_chats']} 个")
            print(f"  累计跳过原因分布: {truth['skip_reasons']}")
            if truth["greet_sent"] > len(boss):
                problems.append(f"账号{idx}: 本地发出 {truth['greet_sent']} 条 > BOSS 会话 {len(boss)} 条")
            if missing:
                # BOSS 的会话列表不是台账：HR 关掉会话、岗位下架、对方转招聘号都会让它
                # 少一条，所以这只能是提示，不能当成"我们记错了一次投递"
                days = sorted({r["timestamp"][:10] for r in truth["sent"]
                               if re.sub(r"\s+", "", r.get("company") or "") in missing})
                notes.append(f"账号{idx}: {len(missing)} 个记为已发出的公司在 BOSS 会话列表里"
                             f"找不到（{missing[:5]}），这些发送集中在 {days}")
            if truth["greet_sent_today"] and not boss_today:
                problems.append(f"账号{idx}: 本地 {day} 发出 {truth['greet_sent_today']} 条，"
                                f"BOSS 列表却没有当天的会话")
            for b in boss_today[:10]:
                print(f"     · {day} {b['name']} | {b.get('company', '')} | {b.get('time')} "
                      f"| {(b.get('last') or '')[:40]}")
    finally:
        after = sha_of_cookies()
        print("\n" + "=" * 66)
        print(f"  Cookie 跑前: {before}")
        print(f"  Cookie 跑后: {after}")
        if before != after:
            problems.append(f"Cookie 被改动: {before} -> {after}")
        print("=" * 66)
        print("  审计结论: " + ("四方一致，未发现差异" if not problems
                                else f"{len(problems)} 项差异"))
        for p in problems:
            print(f"  ✗ {p}")
        for n in notes:
            print(f"  · {n}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
