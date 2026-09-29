# -*- coding: utf-8 -*-
"""多账号隔离自检：端口 / profile / cookie 文件 / 账号身份 四层逐个比对。

为什么要这个：2026-09-29 排查"第二个浏览器没东西"时发现，两个账号的 cookie 文件里
`wt2`（BOSS 的登录态令牌）**完全相同** —— 也就是两个槽位登录的其实是同一个账号，
所谓多账号从来没成立过。光看"文件路径不同、端口不同"是查不出这件事的，
必须把 cookie 里的身份字段抽出来比。

用法：
    python -X utf8 tools/check_account_isolation.py            # 检查当前配置
    python -X utf8 tools/check_account_isolation.py --json     # 机器可读输出
退出码：0 = 四层都隔离开；1 = 有槽位共用同一账号或撞端口/profile。
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# BOSS 里真正代表"这是哪个登录者"的字段。__zp_stoken__/bst 这类是风控与埋点，
# 每次都不一样，拿它们比会误判成两个不同账号。
IDENTITY_COOKIES = ("wt2", "wbg", "zp_at")


def identity_fingerprint(cookies) -> str:
    """从 cookie 列表算出"这是哪个账号"的稳定指纹；拿不到身份字段返回空串。"""
    import hashlib
    m = {}
    for c in cookies or []:
        if isinstance(c, dict) and c.get("name") in IDENTITY_COOKIES:
            m[c["name"]] = str(c.get("value") or "")
    if not m:
        return ""
    joined = "&".join(f"{k}={m[k]}" for k in sorted(m))
    return hashlib.sha1(joined.encode()).hexdigest()[:10]


def read_cookie_file(path):
    if not path or not os.path.exists(path):
        return None
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        return "bad"
    return data if isinstance(data, list) else (data.get("cookies") or [])


def check(cfg):
    """返回每个账号的隔离事实 + 冲突清单。"""
    from boss_bot.browser_launcher import BrowserManager

    rows, conflicts = [], []
    ports, profiles, files, fp_seen = {}, {}, {}, {}
    for i, acc in enumerate(cfg.greet.accounts or []):
        udd = str(Path("browser_data") / f"account_{i}")
        mgr = BrowserManager(config=cfg.browser, account_index=i,
                             port=cfg.browser.debug_port + i,
                             user_data_dir=udd)
        cookie_file = getattr(acc, "cookie_file", "") or ""
        cookies = read_cookie_file(cookie_file)
        fp = "" if cookies in (None, "bad") else identity_fingerprint(cookies)
        rows.append({
            "index": i, "name": acc.name, "port": mgr._debug_port,
            "profile": mgr._user_data_dir,
            "profile_exists": os.path.isdir(mgr._user_data_dir),
            "cookie_file": cookie_file,
            "cookie_state": ("缺失" if cookies is None else
                             "读不出" if cookies == "bad" else
                             "无身份字段" if not fp else fp),
            "fingerprint": fp,
        })
        if ports.get(mgr._debug_port):
            conflicts.append(f"端口 {mgr._debug_port} 被 "
                             f"{ports[mgr._debug_port]} 和 {acc.name} 共用")
        ports[mgr._debug_port] = acc.name
        if profiles.get(mgr._user_data_dir):
            conflicts.append(f"profile {mgr._user_data_dir} 被 "
                             f"{profiles[mgr._user_data_dir]} 和 {acc.name} 共用")
        profiles[mgr._user_data_dir] = acc.name
        if cookie_file and files.get(os.path.abspath(cookie_file)):
            conflicts.append(f"cookie 文件 {cookie_file} 被 "
                             f"{files[os.path.abspath(cookie_file)]} 和 {acc.name} 共用")
        files[os.path.abspath(cookie_file or f"__none_{i}")] = acc.name
        if fp:
            if fp in fp_seen:
                conflicts.append(f"{acc.name} 和 {fp_seen[fp]} 的 wt2 完全相同 —— "
                                 f"两个槽位登录的是同一个 BOSS 账号，多账号不成立")
            else:
                fp_seen[fp] = acc.name
    return rows, conflicts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    from boss_bot.unified_config import UnifiedConfig
    cfg = UnifiedConfig.load()
    rows, conflicts = check(cfg)

    if args.json:
        print(json.dumps({"accounts": rows, "conflicts": conflicts},
                         ensure_ascii=False, indent=2))
        return 1 if conflicts else 0

    print(f"{'槽位':<4} {'名称':<8} {'端口':<6} {'profile':<26} {'cookie 文件':<26} 身份指纹")
    for r in rows:
        print(f"{r['index']:<4} {r['name']:<8} {r['port']:<6} "
              f"{Path(r['profile']).name:<26} {os.path.basename(r['cookie_file'] or '-'):<26} "
              f"{r['cookie_state']}")
    print()
    if conflicts:
        print("❌ 隔离没做到位：")
        for c in conflicts:
            print("   -", c)
        return 1
    print("✅ 端口 / profile / cookie 文件 / 账号身份 四层都分开")
    return 0


if __name__ == "__main__":
    sys.exit(main())
