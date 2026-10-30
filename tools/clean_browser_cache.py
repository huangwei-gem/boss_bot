# -*- coding: utf-8 -*-
"""把 Chrome profile 里"自己会长回来"那一族缓存清掉，登录原件一个都不碰。

为什么值得清（本机 2026-10-30 现数）：`browser_data/` 1782.5 MB + `flask-version/browser_data/`
410.5 MB，而体积闸那道只数 `data/` + `logs/`（现数 301.2 MB），这两块**根本不在计量里**。
account_0 那 820.4 MB 里 `Default/Cache` 405.2 + `Default/Code Cache` 312.9 = **718.1 MB（87.5%）**
是渲染缓存——浏览器下次开自己重写，里面没有账号的东西。

不碰的那些都是原件，缺了就掉登录或丢证据：`Network/Cookies`、`Cookies`、`Login Data`、
`Web Data`、`Preferences`、`Secure Preferences`、`Local State`（解密 Cookie 的 key 在这儿）、
`Local Storage`、`Session Storage`、`Sessions`、`IndexedDB`、`Service Worker`（站点自己缓存的
东西，量小、不该我替它决定）、`Crashpad`（面板莫名其妙死过那几次的现场，task #190/#245 还靠它），
以及 Chrome 下载的组件（`component_crx_cache`、`optimization_guide_model_store`、`Safe Browsing`
——删了要重新联网拉，不为省这点地方给用户添一次卡顿）。

正被握着的盒整台跳过：profile 根下有 `lockfile` 就等于有 Chrome 在上面跑（10-30 实测五台里
只有 `browser_data/account_1` 带着它，那台确实挂着 6 个 chrome.exe）。Windows 上删它正在写的
缓存会留一叠"删不掉"的噪声，还可能把那次会话搞崩——宁可少清一台。

默认**只报不删**（干跑），真要动手得写 `--run`；每一次动手都逐盒落台账
`data/浏览器缓存清理台账.jsonl`。这脚本谁都不挂：面板不会自己跑它（他 10-29 定死"不许自启动"），
要清的时候他自己跑一次，或者叫机器人跑。

跑法：
    python tools/clean_browser_cache.py            # 干跑：一台都不动，只报会清多少
    python tools/clean_browser_cache.py --run      # 真删（只删缓存族，跳过带 lockfile 的盒）
    python tools/clean_browser_cache.py --run --box account_0
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
台账名 = "浏览器缓存清理台账.jsonl"

# 就这两族：`Default/` 下面的渲染缓存，实测占一台的 87.5%
缓存族 = ("Cache", "Code Cache")
# 反向哨兵用：这些原件名字一旦混进上面那族，测试当场红
豁免原件 = ("cookies", "login data", "web data", "preferences", "local state",
            "local storage", "session storage", "sessions", "indexeddb",
            "service worker", "crashpad", "network")


def 各盒(仓库根=None):
    """两处都认：`browser_data/` 与 `flask-version/browser_data/`。

    只扫一处就等于留着一台 410 MB 的旧盒——`flask-version/browser_data/account_0`
    是当年 profile 按 cwd 锚时长的，一直没人管过它。
    """
    根 = Path(str(仓库根 or ROOT))
    out = []
    for 相对 in ("browser_data", "flask-version/browser_data"):
        父 = 根 / 相对
        if not 父.is_dir():
            continue
        for 盒 in sorted(父.iterdir()):
            if 盒.is_dir() and (盒 / "Default").is_dir():
                out.append(盒)
    return out


def 缓存目录(盒):
    """这台盒里属于缓存族的那几个目录（不存在就不列，报数才不会凭空多一项）。"""
    out = []
    for 名 in 缓存族:
        p = Path(str(盒)) / "Default" / 名
        if p.is_dir():
            out.append(p)
    return out


def 正被握着(盒):
    return (Path(str(盒)) / "lockfile").exists()


def _数(路径):
    个 = 字节 = 0
    for 根, _, 档s in os.walk(str(路径)):
        for 档 in 档s:
            try:
                字节 += os.path.getsize(os.path.join(根, 档))
                个 += 1
            except OSError:
                continue
    return 个, 字节


def 扫(仓库根=None):
    """每台盒报一行：缓存多少、能不能动手、动不了是因为什么。"""
    out = []
    for 盒 in 各盒(仓库根):
        个 = 字节 = 0
        for p in 缓存目录(盒):
            a, b = _数(p)
            个 += a
            字节 += b
        锁 = 正被握着(盒)
        out.append({"盒": str(盒), "个": 个, "字节": 字节,
                    "跳过": "Chrome 正在用这台（有 lockfile）" if 锁 and 字节 else "",
                    "可清目录": [str(p) for p in 缓存目录(盒)]})
    return out


def _删一棵(路径):
    """自底向上删；锁着的文件跳过并记进清单，不冲掉整轮。"""
    个 = 字节 = 0
    败 = []
    目 = Path(str(路径))
    档s = []
    子 = []
    for e in 目.iterdir():
        (子 if e.is_dir() and not e.is_symlink() else 档s).append(e)
    for e in 档s:
        # 先量后删、删成才算数：反过来的话，锁着没删掉的那份会被算进"释放了多少"
        try:
            这字节 = e.stat().st_size
            e.unlink()
        except OSError as ex:
            败.append({"路径": str(e), "错": str(ex)[:120]})
            continue
        个 += 1
        字节 += 这字节
    for e in 子:
        a, b, 更 = _删一棵(e)
        个 += a
        字节 += b
        败 += 更
        if not any(True for _ in e.iterdir()):
            try:
                e.rmdir()
            except OSError as ex:
                败.append({"路径": str(e), "错": str(ex)[:120]})
    return 个, 字节, 败


def 清一台(盒, 现在=None):
    """只删缓存族那几个目录；返回个/字节/失败，失败不静默。"""
    个 = 字节 = 0
    败 = []
    for p in 缓存目录(盒):
        a, b, 更 = _删一棵(p)
        个 += a
        字节 += b
        败 += 更
        # 目录本身清空后拔掉，Chrome 下次开自己重建
        try:
            if p.exists() and not any(p.iterdir()):
                p.rmdir()
        except OSError as ex:
            败.append({"路径": str(p), "错": str(ex)[:120]})
    return {"盒": str(盒), "个": 个, "字节": 字节, "失败": 败,
            "时刻": 现在 or time.strftime("%Y-%m-%d %H:%M:%S")}


def 落台账(结果, 仓库根=None):
    """逐盒一行 JSONL：哪天清了哪台、多少个、多少字节、有没有删不掉的。

    这是"删"唯一的凭据。体积闸那条也是这个规矩（`space_retention._落台账`）：
    东西没了不要紧，"什么时候、多大一堆、为什么动它"必须还查得到。
    """
    路径 = Path(str(仓库根 or ROOT)) / "data" / 台账名
    路径.parent.mkdir(parents=True, exist_ok=True)
    with open(str(路径), "a", encoding="utf-8") as 写:
        for r in 结果:
            写.write(json.dumps(r, ensure_ascii=False) + "\n")
    return 路径


def main(argv=None, 仓库根=None):
    解 = argparse.ArgumentParser(description="清 Chrome profile 的渲染缓存（登录原件不碰）")
    解.add_argument("--run", action="store_true", help="真删；不写就只报数")
    解.add_argument("--box", default="", help="只动这一台（按目录名，如 account_0）")
    参 = 解.parse_args(argv)

    清 = [x for x in 扫(仓库根) if not 参.box or Path(x["盒"]).name == 参.box]
    if 参.box and not 清:
        print("✗ 没有叫 %s 的 profile 盒（现有：%s）"
              % (参.box, "、".join(Path(x["盒"]).name for x in 扫(仓库根))))
        return 1
    能动 = [x for x in 清 if not x["跳过"]]
    print("%s：%d 台里有 %d 台能动手，共 %.1f MB"
          % ("动手" if 参.run else "干跑（一个字都不删）", len(清), len(能动),
             sum(x["字节"] for x in 能动) / 1e6))
    for x in 清:
        print("  %-46s %7.1f MB / %5d 个  %s"
              % (x["盒"], x["字节"] / 1e6, x["个"], x["跳过"] or "可清"))
    if not 参.run:
        print("\n只报数。要真删：python tools/clean_browser_cache.py --run")
        return 0

    结果 = []
    for x in 清:
        if x["跳过"]:
            print("· 跳过 %s——%s" % (x["盒"], x["跳过"]))
            continue
        r = 清一台(x["盒"])
        结果.append(r)
        print("→ %s 清了 %d 个、%.1f MB%s"
              % (r["盒"], r["个"], r["字节"] / 1e6,
                 ("，还有 %d 个删不掉（锁着）" % len(r["失败"])) if r["失败"] else ""))
        for 失 in r["失败"][:3]:
            print("   ✗ %s：%s" % (失["路径"], 失["错"]))
    if 结果:
        print("台账：%s" % 落台账(结果, 仓库根))
    print("\n合计释放 %.1f MB" % (sum(r["字节"] for r in 结果) / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
