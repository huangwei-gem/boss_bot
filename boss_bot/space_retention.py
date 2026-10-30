# -*- coding: utf-8 -*-
"""历史数据的体积闸：涨到阈值就清，只留最近几天。

2026-10-23 他定的口径：「仓库公有没事，不怕泄露，历史数据有多少了，到了 500M 吧
自动清理吧，最好是只保留 3 天的。」——之前那条是"只挪走不删"（task #46、#203、#209），
因为那几盒是他找工作全过程唯一的底；现在他不打算靠这些底账回溯了，改口要**按体积
触发、按天数删除**。所以这条不是把上一版推翻，是加在挪走之后的第二道：
零点先按窗口把过期盒挪进 archive_kept/（还没删），体积真撞上 500MB 才动手删
3 天以外的。没到线，一个字节都不动。

三条自己定的护栏（都写了测试）：
1. **Cookie 一族永不删**。整棵 data/cookie_backups/ + data/stale_cookies/ 加起来 0.1 MB，
   删了不省地方，代价是掉登录态（那条红线是"登录态不可丢失"，他没改口）。
2. **只认按天的东西**。归档盒只删目录名是日期的；`unmix_backup` 那一类不是日期的
   一律不碰——那是别的流程留下的证据。data/ 根下的 live 记录（greet_records、
   chats_log、job_pool…）根本不在候选清单里，结构上就够不着。
3. **删之前先落台账**。清单写进 data/体积清理台账.json（不在可删范围里），
   盒没了但"哪天删了多大一堆"还查得到。
"""
from __future__ import annotations

import json
import re
import shutil
import time
from datetime import date
from pathlib import Path

MB = 1024 * 1024

# 他给的两个数
默认阈值MB = 500
默认保留天数 = 3
# 面板上填手滑的兜底：阈值取 0=每轮都清；正数低于 50MB 按 50MB 算；
# 保留天数不许小于 1——否则"留 0 天"会把连今天在内的历史一次删光，
# 那不是清理是自删底账。
最小阈值MB = 50
最小保留天数 = 1

# 计量范围：这两棵树就是机器人自己长出来的历史（代码、浏览器壳、browser_data 都不算）
计量根 = ("data", "logs")
# 可删范围：只有按天的盒与留档副本。写成白名单，不是黑名单——
# 白名单写漏了顶多多占点地方，黑名单写漏了会删掉不该删的。
盒状范围 = ("data/archive", "data/archive_kept")     # 盒名必须是日期
文件范围 = ("data/kept", "logs", "logs/kept")        # 名字里有日期，否则按 mtime
台账路径 = "data/体积清理台账.json"

_日期正则 = re.compile(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})")


def _名字日期(名字: str):
    """从文件名/目录名里抓日期，抓不到返回 None（认不出就不许删）。"""
    串 = _日期正则.search(名字 or "")
    if not 串:
        return None
    try:
        return date(int(串.group(1)), int(串.group(2)), int(串.group(3)))
    except ValueError:
        return None


def _修改日期(路径: Path):
    try:
        return date.fromtimestamp(路径.stat().st_mtime)
    except OSError:
        return None


def _豁免(相对: str) -> bool:
    """登录凭据那一族永远不碰（见模块头第 1 条护栏）。"""
    小 = (相对 or "").lower()
    return any(k in 小 for k in ("cookie",))


def _字节(路径: Path) -> int:
    try:
        return sum(f.stat().st_size for f in 路径.rglob("*") if f.is_file())
    except OSError:
        return 0


def 历史体积(项目根) -> dict:
    """数一遍 data/ 与 logs/ 各占多少，返回 {"字节","按目录","文件数"}。

    按目录统计到第二层（data/archive_kept、logs/kept 这种），根下的散文件并成一格
    "(根下散文件)"，面板上那一行看得懂是哪个东西在涨。
    """
    项目根 = Path(str(项目根))
    总 = 0
    按目录 = {}
    文件数 = 0
    for 根名 in 计量根:
        根 = 项目根 / 根名
        if not 根.is_dir():
            continue
        for 项 in sorted(根.iterdir()):
            if 项.is_dir():
                键 = "%s/%s" % (根名, 项.name)
                小 = _字节(项)
                文件数 += sum(1 for f in 项.rglob("*") if f.is_file())
            else:
                键 = "%s/(根下散文件)" % 根名
                try:
                    小 = 项.stat().st_size
                except OSError:
                    小 = 0
                文件数 += 1
            按目录[键] = 按目录.get(键, 0) + 小
            总 += 小
    return {"字节": 总, "按目录": 按目录, "文件数": 文件数}


def _候选(项目根: Path, 今天: date, 保留天数: int) -> list:
    """列出 3 天以外、允许删的按天条目：[{"路径","字节","日期","种类"}]，从旧到新。"""
    out = []

    for 相对 in 盒状范围:
        根 = 项目根 / 相对
        if not 根.is_dir():
            continue
        for 盒 in sorted(根.iterdir()):
            if not 盒.is_dir():
                continue
            if _豁免("%s/%s" % (相对, 盒.name)):
                continue
            日 = _名字日期(盒.name)      # 盒名不是日期 → 不碰（unmix_backup 那一类）
            if 日 is None:
                continue
            if (今天 - 日).days <= 保留天数:
                continue
            out.append({"路径": 盒, "字节": _字节(盒), "日期": 日.isoformat(), "种类": "归档盒"})

    for 相对 in 文件范围:
        根 = 项目根 / 相对
        if not 根.is_dir():
            continue
        for 文 in sorted(根.iterdir()):
            if 文.is_dir():
                continue
            相对路径 = "%s/%s" % (相对, 文.name)
            if _豁免(相对路径):
                continue
            日 = _名字日期(文.name) or _修改日期(文)
            if 日 is None:
                continue
            if (今天 - 日).days <= 保留天数:
                continue
            try:
                小 = 文.stat().st_size
            except OSError:
                continue
            out.append({"路径": 文, "字节": 小, "日期": 日.isoformat(), "种类": "留档文件"})

    out.sort(key=lambda x: (x["日期"], x["路径"].as_posix()))
    return out


def _落台账(项目根: Path, 台账内容: dict) -> str:
    """把"删了什么"记下来——盒没了，账还得在。"""
    落 = 项目根 / 台账路径
    旧 = []
    try:
        if 落.is_file():
            读 = json.loads(落.read_text(encoding="utf-8"))
            旧 = 读.get("轮次") or []
    except (OSError, ValueError):
        旧 = []
    轮次 = (旧 + [台账内容])[-20:]
    try:
        落.parent.mkdir(parents=True, exist_ok=True)
        临 = 落.with_suffix(".json.tmp")
        临.write_text(json.dumps({"轮次": 轮次}, ensure_ascii=False, indent=1),
                      encoding="utf-8")
        临.replace(落)
        return str(落)
    except OSError:
        return ""


def 清理超期历史(项目根, 阈值MB=None, 保留天数=None, 今天=None, 干跑=False) -> dict:
    """历史数据 ≥ 阈值 → 删掉 保留天数 以外的按天条目；没到线 → 一个字节都不动。

    干跑只算账不删（面板上那个"先看一眼"用它）。返回值不许静默：调用方要把
    "到没到线、删了几盒、省了多少"念到日志里，删完还要能查出是哪天删的。
    """
    项目根 = Path(str(项目根))
    原阈值 = 默认阈值MB if 阈值MB is None else int(阈值MB)
    原窗口 = 默认保留天数 if 保留天数 is None else int(保留天数)
    # 0 = 不看体积，每轮都只留窗口内那几天；正数低于 最小阈值MB 一律抬到 最小阈值MB
    不看体积 = 原阈值 == 0
    阈值 = 默认阈值MB if 不看体积 else max(最小阈值MB, 原阈值)
    窗口 = max(最小保留天数, 原窗口)
    今天 = 今天 or date.today()

    量 = 历史体积(项目根)
    结果 = {
        "触发": False, "阈值MB": 阈值, "保留天数": 窗口, "不看体积": 不看体积,
        "钳制了": (阈值 != 原阈值 and not 不看体积) or (窗口 != 原窗口),
        "清理前字节": 量["字节"], "清理后字节": 量["字节"], "释放字节": 0,
        "删的": [], "失败": [], "清单": [], "台账": "", "仍超线": False, "原因": "",
        "按目录": 量["按目录"],
    }

    候选 = _候选(项目根, 今天, 窗口)
    结果["清单"] = [{"路径": c["路径"].as_posix(), "字节": c["字节"],
                     "种类": c["种类"], "日期": c["日期"]} for c in 候选]

    if not 不看体积 and 量["字节"] < 阈值 * MB:
        结果["原因"] = ("历史数据 %.1f MB，没到 %d MB，一条不删"
                        % (量["字节"] / MB, 阈值))
        return 结果
    if 不看体积:
        结果["原因"] = "阈值填了 0：不看体积，每轮只留 %d 天" % 窗口

    结果["触发"] = True
    删了 = 0
    for c in 候选:
        if 干跑:
            结果["删的"].append({"路径": c["路径"].as_posix(), "字节": c["字节"],
                                 "种类": c["种类"], "日期": c["日期"], "实际": "干跑没删"})
            删了 += c["字节"]
            continue
        try:
            if c["路径"].is_dir():
                shutil.rmtree(str(c["路径"]))
            else:
                c["路径"].unlink()
            结果["删的"].append({"路径": c["路径"].as_posix(), "字节": c["字节"],
                                 "种类": c["种类"], "日期": c["日期"], "实际": "已删"})
            删了 += c["字节"]
        except OSError as e:
            结果["失败"].append({"路径": c["路径"].as_posix(),
                                 "错": "%s: %s" % (type(e).__name__, str(e)[:80])})

    后 = 历史体积(项目根)
    结果["清理后字节"] = 后["字节"]
    结果["释放字节"] = max(0, 量["字节"] - 后["字节"]) if not 干跑 else 删了
    结果["按目录"] = 后["按目录"]
    结果["仍超线"] = 后["字节"] >= 阈值 * MB
    前提 = "阈值填 0（每轮都清）" if 不看体积 else "已过 %d MB" % 阈值
    if not 候选:
        结果["原因"] = ("%s，但 %d 天以外的按天条目一个都没有——"
                        "剩下的都在窗口内或是 live 记录，不动"
                        % (前提, 窗口))
    elif 干跑:
        结果["原因"] = ("%s，干跑：本回合要删 %d 项、约 %.1f MB"
                        % (前提, len(候选), 删了 / MB))
    else:
        结果["台账"] = _落台账(项目根, {
            "时刻": time.strftime("%Y-%m-%d %H:%M:%S"),
            "阈值MB": 阈值, "保留天数": 窗口, "清理前MB": round(量["字节"] / MB, 2),
            "清理后MB": round(后["字节"] / MB, 2), "删的": 结果["删的"],
            "失败": 结果["失败"],
        })
        结果["原因"] = ("%s：删掉 %d 天以外的 %d 项，省 %.1f MB，台账 %s"
                        % (前提, 窗口, len(结果["删的"]), 结果["释放字节"] / MB,
                           Path(结果["台账"]).name if 结果["台账"] else "写不下去"))
    return 结果
