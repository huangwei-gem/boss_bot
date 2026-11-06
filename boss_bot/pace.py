# -*- coding: utf-8 -*-
"""节奏闸：所有"这一小时/今天还能不能再发一发"的判据**只住这里**。

2026-11-04 被 BOSS 按出口 IP 关进「访问受限 · 24 小时」，盘上现数是
每小时 57~61 次搜索整页加载 + 每小时 255~316 轮会话页，连着五个小时。
当天装的那道 `browser_launcher` 小时账只数"整页加载"这一族、一个总数，
分不出"搜索页打了 60 发"还是"会话页打了 60 发"——而这两族的敏感度完全不同：
搜索页是查询接口，会话页是 SPA 首屏。所以这一份按**类别**分账。

四件事都在这里，别处不许再抄一套：
1. 分小时 / 分日的配额（按类别），窗口滑走就自动腾位置；
2. 随机抖动：等间隔的请求就是模式匹配送给风控的靶子（"请勿频繁提交刷新请求"
   数的不只是量，还有"每 11 秒一次"这种规律性）；
3. 活跃时段：北京时间 09:00~18:00 之外完全静默——凌晨三点还在刷页面的机器人
   既不像真人，也白白消耗当天的额度；
4. 熔断升级：5 分钟内命中 2 次风控就按 5min → 15min → 60min 的阶梯冷却，
   而不是照原频率一直重试（重试本身就是那句"请勿频繁"）。

进程级全局是有意的：风控记的是**出口 IP**，两个号挤在同一个家宽 IP 上，
按号分账等于没装。测试必须自己 `清零()`，否则这条造的账会漏给下一条
（`tests/conftest.py` 里那两道还原夹具就是为这个）。
"""
import random
import threading
import time
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:                                    # pragma: no cover
    ZoneInfo = None

时区名 = "Asia/Shanghai"

# 类别 → (每小时, 每日)。搜索页是查询接口最敏感；会话页轮次多但轻；
# 详情页 8 天日志里 111/112 处风控都出在它上面，所以给得最小。
默认限额 = {
    "搜索页": {"小时": 18, "日": 200},
    "会话页": {"小时": 60, "日": 1200},
    "详情页": {"小时": 8, "日": 60},
}
类别s = tuple(默认限额)

# 熔断阶梯：第 1 次升 5 分钟、第 2 次 15 分钟、第 3 次及以后 60 分钟
熔断阶梯 = (300, 900, 3600)
风控窗口秒 = 300          # 多久之内的两次命中算"连着被打"
抖动比例 = 0.35           # 基准秒 × [1-0.35, 1+0.35]
活跃起 = 9
活跃止 = 18
活跃启用 = True

_锁 = threading.RLock()
_状态 = {}
# 抖动用的随机源。模块级、可注入种子：
# 测试要的是"抖动确实把间隔打散了"这种可断言的事实，不是"今天摇到几"。
_rng = random.Random()


def 清零(现在=None):
    """回到初始状态（测试用；也是"改了限额就当场按新的来"的入口）。"""
    with _锁:
        _状态.clear()
        _状态.update({
            "小时": {k: [] for k in 类别s},      # 每类最近一小时的发点
            "日": {k: 0 for k in 类别s},          # 每类今天累计
            "日戳": _今天(现在),
            "风控": [],                            # 最近命中风控的时刻
            "熔断级": 0,
            "冷却到": 0.0,
        })


def _确保活着():
    if not _状态:
        清零()


def _今天(现在=None) -> str:
    现在 = 现在 if 现在 is not None else time.time()
    return datetime.fromtimestamp(现在, _时区()).strftime("%Y-%m-%d")


def _时区():
    if ZoneInfo is not None:
        try:
            return ZoneInfo(时区名)
        except Exception:
            pass
    return None          # 拿不到就退回系统本地时间（这台机器本身就是 +08:00）


def _跨天了(现在) -> bool:
    _确保活着()
    return _状态["日戳"] != _今天(现在)


def 装限额(小时=None, 日=None, 抖动=None, 活跃=None, 熔断=None, 窗口=None):
    """把配置灌进闸（界面改了热重载走这里）。每一项都单独夹范围，坏值退回默认。

    返回装好后的样子，调用方回读才能确认"改了真生效"——以前有过一次：
    配置读到了、闸没装，界面上改了没反应，看的人只能猜。
    """
    _确保活着()
    with _锁:
        if 小时 is not None:
            for 类, 值 in (小时 or {}).items():
                if 类 in 默认限额 and 值 is not None:
                    try:
                        默认限额[类]["小时"] = max(0, min(600, int(float(值))))
                    except (TypeError, ValueError):
                        pass
        if 日 is not None:
            for 类, 值 in (日 or {}).items():
                if 类 in 默认限额 and 值 is not None:
                    try:
                        默认限额[类]["日"] = max(0, min(20000, int(float(值))))
                    except (TypeError, ValueError):
                        pass
        if 抖动 is not None:
            全局("抖动比例", _夹(抖动, 0.0, 0.9, 抖动比例))
        if 活跃 is not None:
            起, 止, 启用 = 活跃
            全局("活跃启用", bool(启用))
            if 启用:
                全局("活跃起", int(_夹(起, 0, 23, 活跃起)))
                全局("活跃止", int(_夹(止, 1, 24, 活跃止)))
        if 熔断 is not None:
            try:
                级 = [int(float(x)) for x in 熔断 if str(x).strip() != ""]
            except (TypeError, ValueError):
                级 = []
            # 按原样装，**不排序**：排序等于替调用方把写反的阶梯"修好"，
            # 结果就是配置里说 A、闸里装 B。形状不对（非递增、越界）就整条不装，
            # 保留上一份，让上层那条门去红。
            if 级 and all(60 <= x <= 21600 for x in 级) and 级 == sorted(级):
                全局("熔断阶梯", tuple(级))
        if 窗口 is not None:
            全局("风控窗口秒", int(_夹(窗口, 30, 3600, 风控窗口秒)))
    return 现状()


def _夹(值, 下, 上, 兜底):
    try:
        return max(下, min(上, float(值)))
    except (TypeError, ValueError):
        return float(兜底)


def 装随机种子(种子=None):
    """测试钉种子用（让"抖动"这件事可断言）；不给就退回真随机。"""
    global _rng
    _rng = random.Random(种子)
    return _rng


def 全局(名, 值=None):
    """读/改那几个模块级默认。给测试和配置灌入用，别处不要直接改常量。"""
    _确保活着()
    表 = globals()
    if 值 is None:
        return 表[名]
    表[名] = 值
    return 值


def 请求闸(类: str, 现在=None, 记=True) -> tuple:
    """这一发能不能发。返回 (能不能, 为什么, 还要等多久秒)。

    不睡、不拦在闸里——只回答，调用方决定是收手这一轮还是等下一轮。
    闸里睡觉会把两条腿钉住，日志上出现一整段"什么都没发生"，那是最难查的停。
    """
    现在 = 现在 if 现在 is not None else time.time()
    _确保活着()
    if 类 not in 默认限额:
        return True, "没这一类的账（%s），不拦" % 类, 0.0
    with _锁:
        if _跨天了(现在):
            _状态["日戳"] = _今天(现在)
            _状态["日"] = {k: 0 for k in 类别s}
            _状态["小时"] = {k: [] for k in 类别s}
            _状态["熔断级"] = 0

        冷却到 = _状态.get("冷却到") or 0.0
        if 冷却到 > 现在:
            return False, ("熔断冷却中（5 分钟内命中过 %d 次风控）"
                           % len(_状态["风控"])), 冷却到 - 现在

        限 = 默认限额[类]
        串 = _状态["小时"][类]
        串[:] = [t for t in 串 if 现在 - t < 3600.0]
        if 限["小时"] and len(串) >= 限["小时"]:
            等多久 = max(1.0, 3600.0 - (现在 - 串[0]))
            return False, "本小时 %s 已发 %d/%d 次" % (类, len(串), 限["小时"]), 等多久
        if 限["日"] and _状态["日"][类] >= 限["日"]:
            等多久 = max(1.0, _到明天秒(现在))
            return False, "今日 %s 已发 %d/%d 次" % (类, _状态["日"][类], 限["日"]), 等多久
        if 记:
            串.append(现在)
            _状态["日"][类] += 1
        return True, "", 0.0


def _到明天秒(现在) -> float:
    明 = (datetime.fromtimestamp(现在, _时区())
          .replace(hour=0, minute=0, second=0, microsecond=0))
    return max(1.0, (明 + __import__("datetime").timedelta(days=1)
                     - datetime.fromtimestamp(现在, _时区())).total_seconds())


def 抖动(基准秒, 比例=None, 现在=None) -> float:
    """把固定间隔打散成随机间隔。

    等间隔本身就是特征：11-04 那天平均 11 秒一趟会话页，
    这种规律性比总量更好认。比例默认 ±35%，基准 ≤0 或比例 0 就原样返回。
    """
    _确保活着()
    try:
        基准 = float(基准秒 or 0)
    except (TypeError, ValueError):
        return 0.0
    if 基准 <= 0:
        return 0.0
    比 = 比例 if 比例 is not None else 全局("抖动比例")
    比 = _夹(比, 0.0, 0.9, 0.0)
    if 比 <= 0:
        return 基准
    with _锁:
        return max(1.0, 基准 * (1.0 + _rng.uniform(-比, 比)))


def 活跃窗口(现在=None) -> tuple:
    """现在是不是该干活的时段。返回 (在不在窗口内, 距窗口打开还要等多久)。

    默认北京时间 09:00~18:00。窗口外完全静默是两件事：既不像真人（凌晨三点
    每 11 秒刷一次 BOSS），也不该把当天的额度白白花在没人看的时间上。
    """
    现在 = 现在 if 现在 is not None else time.time()
    if not 全局("活跃启用"):
        return True, 0.0
    起 = int(全局("活跃起"))
    止 = int(全局("活跃止"))
    本 = datetime.fromtimestamp(现在, _时区())
    时 = 本.hour + 本.minute / 60.0
    if 起 <= 止:
        在 = 起 <= 时 < 止
    else:                                   # 跨零点窗口（比如 22:00~06:00）
        在 = 时 >= 起 or 时 < 止
    if 在:
        return True, 0.0
    开 = 本.replace(hour=起, minute=0, second=0, microsecond=0)
    if 开 <= 本:
        开 = 开 + timedelta(days=1)
    return False, max(1.0, (开 - 本).total_seconds())


def 可以出门(现在=None) -> tuple:
    """只看"现在该不该碰 BOSS"：活跃时段 + 熔断冷却。**不记账、不消耗额度**。

    为什么单独一句：轮子开头那些动作（排空勾选队列要开详情页、启动全量同步要逐个点会话）
    不属于任何一族额度，但同样是被封的对象。把它们塞进 `请求闸` 会让"能不能做"
    顺手扣掉一格配额，读代码的人会以为额度是被这些动作吃光的——那是假账。
    """
    现在 = 现在 if 现在 is not None else time.time()
    在窗口, 等多久 = 活跃窗口(现在)
    if not 在窗口:
        return False, "活跃时段外", 等多久
    剩 = 冷却剩余(现在)
    if 剩 > 0:
        return False, "熔断冷却中", 剩
    return True, "", 0.0


def 命中风控(现在=None) -> float:
    """记一次风控命中（验证码、访问受限、连续空搜索都算），返回该冷却多少秒。

    阶梯 5min → 15min → 60min，**每次命中升一档**，比"5 分钟内两次才升级"严一档：
    单次滑块之后照原频率继续撞，正是 11-04 那天的做法，而那做法的结果是 24 小时封禁。
    安静过了窗口（默认 5 分钟没有新命中）就归零回第一档——不把偶发一次滑块
    记成永久前科，否则跑一天会被自己钉死在 60 分钟一档上。
    """
    现在 = 现在 if 现在 is not None else time.time()
    _确保活着()
    with _锁:
        窗 = int(全局("风控窗口秒"))
        阶梯 = 全局("熔断阶梯")
        上次 = max(_状态["风控"]) if _状态["风控"] else None
        if 上次 is not None and 现在 - 上次 > 窗:
            _状态["熔断级"] = 0            # 隔得够久，前科清零
            表 = []
        else:
            表 = [t for t in _状态["风控"] if 现在 - t <= 窗]
        级 = min(_状态["熔断级"], len(阶梯) - 1)
        秒 = float(阶梯[级])
        _状态["熔断级"] = min(_状态["熔断级"] + 1, len(阶梯) - 1)
        _状态["冷却到"] = 现在 + 秒
        _状态["风控"] = 表 + [现在]
        return 秒


def 冷却剩余(现在=None) -> float:
    现在 = 现在 if 现在 is not None else time.time()
    _确保活着()
    with _锁:
        return max(0.0, (_状态.get("冷却到") or 0.0) - 现在)


def 现状(现在=None) -> dict:
    """给界面和日志看的实时节奏快照：已用/额度/还要等多久/窗口/冷却。"""
    现在 = 现在 if 现在 is not None else time.time()
    _确保活着()
    with _锁:
        每类 = {}
        for 类 in 类别s:
            串 = [t for t in _状态["小时"][类] if 现在 - t < 3600.0]
            限 = 默认限额[类]
            等多久 = 0.0
            if 限["小时"] and len(串) >= 限["小时"]:
                等多久 = max(1.0, 3600.0 - (现在 - 串[0])) if 串 else 0.0
            每类[类] = {
                "小时已用": len(串), "小时额度": 限["小时"],
                "日已用": int(_状态["日"].get(类, 0)), "日额度": 限["日"],
                "恢复还要秒": round(等多久, 1),
                "还能发": bool((not 限["小时"] or len(串) < 限["小时"])
                              and (not 限["日"] or _状态["日"].get(类, 0) < 限["日"])),
            }
        在窗口, 等多久 = 活跃窗口(现在)
        return {
            "每类": 每类,
            "熔断冷却剩秒": round(冷却剩余(现在), 1),
            "熔断级": int(_状态.get("熔断级") or 0),
            "最近命中风控": [round(现在 - t) for t in (_状态.get("风控") or [])][-5:],
            "活跃窗口": {"启用": bool(全局("活跃启用")), "起": int(全局("活跃起")),
                        "止": int(全局("活跃止")), "在窗口内": bool(在窗口),
                        "距打开还要秒": round(等多久, 1), "时区": 时区名},
            "抖动比例": float(全局("抖动比例")),
            "日戳": _状态.get("日戳"),
        }


清零()
