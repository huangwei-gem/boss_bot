# -*- coding: utf-8 -*-
"""日志留痕：出窗口就挪走，超限就先备份，一个字都不删。

app.py 里原来那个 cleanup_old_logs() 每天跑一次，做两件不可逆的事：
7 天前的日志 `f.unlink()`，单个文件涨过 20MB 就 `write_text("")` 清空。
2026-10-21 对账对出来的：`logs/boss_bot.log.2026-10-03 ~ 10-06` 四个文件在
git 的 HEAD 里、工作区已经没了——那四天他投了多少、回了什么、哪一轮被风控挡住，
面板上再也翻不出来。同一时刻主日志 16.7MB，再写一天就撞上那条清空分支。

这和 data/archive 的红线是同一条（task #46、#203）：**保留窗口只管"面板默认
摆在哪儿"，不管"数据还在不在"**。所以判据写成纯函数放这儿，app.py 只管调用，
tests/test_log_retention.py 能真的把它跑红。
"""
import gzip
import shutil
import time
from pathlib import Path

# 主日志正被 FileHandler 开着写，挪走等于把当天这份塞进另一个 inode；它只按大小处理
主日志名 = "boss_bot.log"
# 测试自己造的临时面板日志，删掉不欠账
临时前缀 = ("flask_e2e", "flask_output")
保留目录名 = "kept"
# 归档一律 gzip：日志是纯文本，实测压得动（2026-10-30 现算 logs/ 116MB 里
# 出窗口与 kept/ 的那部分是主体）。这不算"删"——回读比过字节才动原件。
压缩后缀 = ".gz"
在写后缀 = ".在写"
gzip魔术字节 = bytes([0x1F, 0x8B])
# gzip 自带 18 字节头尾，几十字节的散件压完反而更大（现测同一段日志文本：
# 16 与 48 字节两份都从原样涨到 36/68 字节，120 字节起才真的变小）。这条下限
# 只管"要不要动手"，压完到底划不划算另由 _压走 那句现算的比对兜住。
值得压字节 = 4096


class 压不动(OSError):
    """压完不比原来小——这不是故障，别记进"压失败"里每轮响一次。"""


def _去处(保留目录, 文件名):
    """同一天挪两次（手点一次 + 零点一次）不能把第一份盖掉。"""
    目标 = 保留目录 / 文件名
    if 目标.exists():
        目标 = 保留目录 / ("%s.挪走于%s" % (文件名, time.strftime("%Y%m%d%H%M%S")))
    return 目标


def _压走(源, 目标):
    """整份 gzip 到 目标，**回读比过字节**才删源。

    回读不上就留下原件、把临时文件拔掉，然后照实报错——这条线是"只挪不删"那条
    红线的另一半：报"已挪走"而内容没了，比不挪坏得多。
    压完还比原来大（小文件才会这样）就抛 压不动，让调用方原样搬、别压。
    """
    临时 = 目标.with_name(目标.name + 在写后缀)
    原样 = 源.read_bytes()
    try:
        with gzip.open(临时, "wb") as 写:
            写.write(原样)
        压后 = 临时.stat().st_size
        回 = gzip.decompress(临时.read_bytes())
        if 回 != 原样:
            raise OSError("回读对不上（解压 %d 字节 ≠ 原 %d 字节）" % (len(回), len(原样)))
        if 压后 >= len(原样):
            raise 压不动("压完不比原来小（%d → %d 字节）" % (len(原样), 压后))
        临时.replace(目标)
        源.unlink()
    except (OSError, ValueError) as e:
        try:
            临时.unlink()
        except OSError:
            pass
        if isinstance(e, 压不动):
            raise
        raise OSError(str(e)) from None
    return 目标.stat().st_size


def 读日志文本(路径):
    """日志现在在哪儿就顺带决定怎么读：`.gz` 的先解压。

    面板那侧只该管"这是哪天那份"，不该管它压没压过——两头各写一份读法，
    早晚会有一头把归档读成一片乱码。
    """
    路径 = Path(str(路径))
    字节 = 路径.read_bytes()
    if 路径.name.endswith(压缩后缀) or 字节[:2] == gzip魔术字节:
        字节 = gzip.decompress(字节)
    return 字节.decode("utf-8", errors="replace")


def 清理日志(日志目录, 现在=None, 保留天数=7, 单文件上限字节=20 * 1024 * 1024,
          压缩=True, 就地压=True, 就地压龄秒=2 * 86400,
          每轮最多压个=200, 每轮最多压字节=192 * 1024 * 1024) -> dict:
    """日志留痕：出窗口的挪进 logs/kept/，躺过两天的就地压一份，超限的先备份再截断。

    返回 {"挪走": [...], "截断": [...], "删除临时": [...], "压缩": [...],
    "压失败": [...], "压不动": [...], "存量剩余": n}，调用方要把它念到日志里，
    不许静默——上一版就是静默删掉了四天的留痕。

    压缩这一支是 10-30 为"项目里日志堆成山"加的。现测（本机 logs/ 里真文件，
    gzip level 6）：15.8 MB 那份压成 2.29 MB（6.9 倍）用 0.24 秒，kept/ 里 26.5 MB
    那份压成 3.16 MB（8.4 倍）用 0.36 秒——约 73 MB/秒，所以一轮 192 MB 的上限
    最多也就 2~3 秒，不会把"双击 start.bat 到面板能点"拖长；上限留着是因为
    这一支启动时同步跑，得有个界。压不完的照实报"存量剩余"，下一轮接着收。

    只压"名字不以 .log 结尾"的那些：`boss_bot.log.2026-10-23` 是轮出来的历史，
    没人握着句柄；`boss_bot.log`、`greet_engine.log`、`panel_io.log` 是正在写的，
    Windows 上连删都删不掉，压它等于给自己造一个"压失败"的红。

    小于 值得压字节 的那批根本不进压的名单，压完反倒变大（gzip 头尾 18 字节）。
    真压了才发现没变小，`_压走` 抛 压不动：那份原样留着、报成"压不动"，不记进
    "压失败"——同一句话每 6 小时响一次就成了狼来了。
    """
    日志目录 = Path(str(日志目录))
    保留目录 = 日志目录 / 保留目录名
    现在 = 现在 if 现在 is not None else time.time()
    结果 = {"挪走": [], "截断": [], "删除临时": [], "压缩": [], "压失败": [], "压不动": []}
    if not 日志目录.is_dir():
        return 结果

    # 存量清单先照此刻拍一张：本轮刚挪进去、刚备份出来的那些留给下一轮——
    # "这一轮只处理本来就躺在 kept/ 里的"才解释得了为什么截断备份当场还是纯文本
    待压 = _存量待压(保留目录) if 压缩 else []
    就地待压 = []

    for 文件 in sorted(日志目录.iterdir()):
        if not 文件.is_file():
            continue
        try:
            年龄秒 = 现在 - 文件.stat().st_mtime
            字节 = 文件.stat().st_size
        except OSError:
            continue

        if 文件.name.startswith(临时前缀):
            try:
                文件.unlink()
                结果["删除临时"].append({"name": 文件.name, "字节": 字节})
            except OSError:
                pass
            continue

        if 年龄秒 > 保留天数 * 86400 and 文件.name != 主日志名:
            保留目录.mkdir(parents=True, exist_ok=True)
            目标 = _去处(保留目录, 文件.name)
            if 压缩 and 字节 >= 值得压字节:
                try:
                    压后 = _压走(文件, Path(str(目标) + 压缩后缀))
                    结果["挪走"].append({"name": 文件.name, "字节": 字节, "压后字节": 压后,
                                          "天数": int(年龄秒 // 86400),
                                          "去处": str(目标) + 压缩后缀})
                    continue
                except 压不动:
                    # 压完还更大：不压，照原样搬进 kept/，挪走这件事照做。
                    # 记一笔是为了让"下限被拿掉"这种改动在测试里现形，不是故障
                    结果["压不动"].append({"name": 文件.name, "字节": 字节})
                except OSError as e:
                    # 压不动就原样留着，下一轮再试：报"挪走了"而文件没了是不能接受的事
                    结果["压失败"].append({"name": 文件.name, "字节": 字节, "错": str(e)[:120]})
                    continue
            try:
                shutil.move(str(文件), str(目标))
                结果["挪走"].append({"name": 文件.name, "字节": 字节,
                                     "天数": int(年龄秒 // 86400), "去处": str(目标)})
            except OSError:
                pass
            continue

        if (就地压 and 压缩 and 字节 >= 值得压字节 and 年龄秒 > 就地压龄秒
                and not 文件.name.endswith((".log", 压缩后缀, 在写后缀))):
            就地待压.append((字节, 文件))
            continue

        if 字节 > 单文件上限字节:
            保留目录.mkdir(parents=True, exist_ok=True)
            目标 = _去处(保留目录, "%s.截断前%d字节" % (文件.name, 字节))
            try:
                shutil.copy2(str(文件), str(目标))
                文件.write_text("", encoding="utf-8")
                结果["截断"].append({"name": 文件.name, "字节": 字节, "去处": str(目标)})
            except OSError:
                pass

    # 就地压的这批（logs/ 里躺过两天的轮转文件）+ kept/ 的存量，一起按大小从大到小排：
    # 大的先来，一轮 192 MB 的上限里先把体积吃掉，零头等下一轮
    待压 = sorted(待压 + 就地待压, key=lambda x: (-x[0], x[1].name))
    已压字节 = 0
    压了 = 0
    for 字节, 文 in 待压:
        if 压了 >= 每轮最多压个 or 已压字节 >= 每轮最多压字节:
            break
        try:
            压后 = _压走(文, 文.with_name(文.name + 压缩后缀))
            结果["压缩"].append({"name": 文.name, "字节": 字节, "压后字节": 压后})
        except 压不动:
            # 压完不比原来小（已经压过一遍的、或本来就没内容的）：原样留着，
            # 这不算故障，报进"压失败"就成了每轮都响一次的假警报
            结果["压不动"].append({"name": 文.name, "字节": 字节})
        except OSError as e:
            结果["压失败"].append({"name": 文.name, "字节": 字节, "错": str(e)[:120]})
        压了 += 1
        已压字节 += 字节
    结果["存量剩余"] = max(0, len(待压) - 压了)

    return 结果


def _存量待压(保留目录):
    """kept/ 里还没压过的存量，按大小从大到小排（大的先来，头几轮就把体积吃掉）。"""
    out = []
    if not 保留目录.is_dir():
        return out
    for 文 in 保留目录.iterdir():
        try:
            if not 文.is_file() or 文.name.endswith((压缩后缀, 在写后缀)):
                continue
            字节 = 文.stat().st_size
            if 字节 < 值得压字节:      # 几十个字节的压完更大，不值得动
                continue
            out.append((字节, 文))
        except OSError:
            continue
    out.sort(key=lambda x: (-x[0], x[1].name))
    return out


def 日志文件(日志目录, 日期=None):
    """某一天/正在写的这份日志现在在哪儿，读不到回 None。

    面板上「按日期看日志」原来只认 logs/boss_bot.log.<日期> 这一个死路径。数据
    挪进 kept/ 之后要是还按老路径找，界面就把"换了地方"报成"这天没日志"——
    和归档盒一个道理，读的那一头必须跟着走。挪进去之后又被压过一轮的，
    就得连 `.gz` 一起找：少了这一支，压一次等于把那几天的日志从界面上删了。
    """
    日志目录 = Path(str(日志目录))
    名 = 主日志名 if not 日期 else "boss_bot.log.%s" % 日期
    直接 = 日志目录 / 名
    if 直接.is_file() and 直接.stat().st_size:
        return 直接
    原地压的 = Path(str(直接) + 压缩后缀)      # 就地压那一支留在 logs/，没挪走
    if 原地压的.is_file() and 原地压的.stat().st_size:
        return 原地压的
    保留目录 = 日志目录 / 保留目录名
    样式 = (名, 名 + 压缩后缀, 名 + ".截断前*字节", 名 + ".截断前*字节" + 压缩后缀,
          名 + ".挪走于*", 名 + ".挪走于*" + 压缩后缀)
    if 保留目录.is_dir():
        for 样式串 in 样式:
            for f in sorted(保留目录.glob(样式串), reverse=True):
                try:
                    if f.is_file() and f.stat().st_size:
                        return f
                except OSError:
                    continue
    return 直接 if 直接.is_file() else None
