# -*- coding: utf-8 -*-
"""日志留痕只挪走不删除。

2026-10-21 查自动回复腿时顺手翻 logs/ 发现的：app.py 里那个每天跑一次的
cleanup_old_logs() 把 7 天前的日志 `f.unlink()` 掉了。git 里对得上账——
`boss_bot.log.2026-10-03 ~ 10-06` 四个文件在 HEAD 里、在工作区已经没了，
而且不是人工删的，是那个后台线程自己删的。同一份代码还有一个更狠的分支：
单个日志涨过 20MB 就 `write_text("")` 清空——主日志 14:11 已经 16.7MB，
再跑一天就被整瓶倒掉，当天投递/回复过程唯一的底没了。

红线和 data/archive 一样（见 task #46、#203）：窗口只管"面板默认摆在哪儿"，
不管"数据还在不在"。所以这里改成挪进 logs/kept/，大文件先整份备份再截断。

10-30 又加了一支：归档压成 .gz。这条不是放水——`_压走` 必须把 .gz 解回来
逐字节比过才删原件，比不过就原样留着并报"压失败"，所以"一个字不删"这条
判据从"字节总量不变"改成"**解压回来的**字节总量不变"（下面有现算的账）。
"""
import gzip
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "flask-version"))

from log_retention import (清理日志, 压缩后缀, 值得压字节, 主日志名, 日志文件,  # noqa: E402
                           读日志文本)

一天 = 86400


def _够压(串):
    """把样例撑到压缩下限以上。低于 值得压字节 的压根不进压的名单，
    拿一份几十个字节的样例去验"压完还读得回来"，测的是走不到的那一条分支。"""
    return 串 * (值得压字节 // max(len(串.encode("utf-8")), 1) + 2)


def _造日志(目录, 名, 内容="一行日志\n", 天数前=9):
    目录.mkdir(parents=True, exist_ok=True)
    p = 目录 / 名
    # 按字节写：write_text 在 Windows 上会把 \n 换算成 \r\n，那"读回来等于原串"
    # 这条判据就永远对不上，红的是测试的形状，不是清理逻辑
    p.write_bytes(内容.encode("utf-8"))
    旧 = time.time() - 天数前 * 一天
    os.utime(p, (旧, 旧))
    return p


def _读回(p):
    """盘上那份的内容（.gz 的解压回来比）——"没删掉东西"的唯一硬证据。"""
    return 读日志文本(p)


@pytest.fixture()
def 日志目录(tmp_path):
    return tmp_path / "logs"


def test_出窗口的日志挪进kept一个字不删(日志目录):
    """这条就是 10-03~10-06 那四个文件的账：出窗口要挪走，不能没了。"""
    正文 = _够压("2026-10-03 那天投了 120 单\n")
    p = _造日志(日志目录, "boss_bot.log.2026-10-03", 内容=正文)
    结果 = 清理日志(日志目录)
    assert not p.exists(), "出窗口的日志还留在原处（说明窗口没生效）"
    去处 = 日志目录 / "kept" / ("boss_bot.log.2026-10-03" + 压缩后缀)
    assert 去处.exists(), f"没挪进 kept/，文件凭空消失：{结果}"
    assert _读回(去处) == 正文
    assert 结果["挪走"] and 结果["挪走"][0]["name"] == "boss_bot.log.2026-10-03"
    assert 结果["挪走"][0]["压后字节"] == 去处.stat().st_size, "报的字节数不是盘上那份"


def test_不压缩那条路还留着挪走(日志目录):
    """压缩坏了（磁盘满、权限、gzip 崩）不该把挪走一起拖死：关掉压缩还得能搬。"""
    正文 = _够压("关压缩也要搬走\n")
    p = _造日志(日志目录, "boss_bot.log.2026-10-03", 内容=正文)
    结果 = 清理日志(日志目录, 压缩=False, 就地压=False)
    assert not p.exists()
    去处 = 日志目录 / "kept" / "boss_bot.log.2026-10-03"
    assert 去处.exists() and not 去处.name.endswith(压缩后缀)
    assert 去处.read_text(encoding="utf-8") == 正文
    assert 结果["挪走"][0].get("压后字节") is None


def test_轮转出来躺过两天的那份就地压成gz(日志目录):
    """他 10-30 说的是"日志文件一大堆"：窗口 14 天，那 14 天里每天都有一份 8 MB
    躺在 logs/ 顶层。挪不走、又不能删，就压——15.8 MB 实测压成 2.29 MB。"""
    p = _造日志(日志目录, "boss_bot.log.2026-10-20", 内容=("那一天的日志行\n" * 500),
              天数前=3)
    原样 = _读回(p)
    原字节 = p.stat().st_size
    结果 = 清理日志(日志目录, 保留天数=14)
    assert not p.exists(), "没压：logs/ 顶层还是一份纯文本"
    压过 = 日志目录 / ("boss_bot.log.2026-10-20" + 压缩后缀)
    assert 压过.exists(), "就地压的那份没落在原处"
    assert _读回(压过) == 原样, "压完内容变了"
    assert 压过.stat().st_size < 原字节, "压了个寂寞：%d → %d" % (
        原字节, 压过.stat().st_size)
    assert 结果["压缩"] and 结果["压缩"][0]["name"] == "boss_bot.log.2026-10-20"
    assert 结果["挪走"] == [], "窗口内的一份不该被挪走"


def test_正在写的与昨天的都不许多压一刀(日志目录):
    """两条边界都得守住：① `*.log` 是 FileHandler 正握着的（Windows 上 unlink 必失败，
    报出来就是一轮"压失败"的红）；② 昨天那份刚轮出来，工具和我都可能还在读它。
    三份都撑到压缩下限以上——低于下限的压根不进这一支，那条规则就成了没被测过。"""
    正写 = _造日志(日志目录, "boss_bot.log", 内容=_够压("今天\n"), 天数前=30)
    别的腿 = _造日志(日志目录, "greet_engine.log", 内容=_够压("投递腿\n"), 天数前=30)
    昨天 = _造日志(日志目录, "boss_bot.log.2026-10-29", 内容=_够压("昨天\n"), 天数前=1)
    清理日志(日志目录, 保留天数=99)      # 窗口拉到 99 天，只剩"就地压"这一支会动
    assert 正写.exists() and not 正写.with_name(正写.name + 压缩后缀).exists(), "主日志被压了"
    assert 别的腿.exists() and 别的腿.read_text(encoding="utf-8") == _够压("投递腿\n"), \
        "正写的引擎日志被压了"
    assert 昨天.exists() and not 昨天.with_name(昨天.name + 压缩后缀).exists(), "昨天那份就压了"


def test_空文件不许压成一份空gz(日志目录):
    """0 字节留原样：压一份空 .gz 会把界面上的"这天没日志"变成"有日志但一行没有"。"""
    空 = _造日志(日志目录, "boss_bot.log.2026-10-10", 内容="", 天数前=3)
    清理日志(日志目录, 保留天数=14)      # 窗口内：只有"就地压"这一支会看它
    assert 空.exists() and not 空.with_name(空.name + 压缩后缀).exists()


def test_几十个字节的出窗口照旧挪走只是不压(日志目录):
    """gzip 自带 18 字节头尾，几十字节压完反而更大（现测同一段日志文本：
    16 字节 → 36、48 字节 → 68，120 字节起才真的小）。logs/ 顶层就躺着
    logs_panel_0139.txt 这种 48 字节的。不压，但挪出窗口这件事照做——
    窗口管的是"默认摆在哪儿"，跟压不压没关系。"""
    p = _造日志(日志目录, "logs_panel_0139.txt", 内容="2026-10-05 01:39:02 面板起来了\n")
    原字节 = p.stat().st_size
    assert 原字节 < 值得压字节, "这条的前提就是一份低于压缩下限的散件：%d 字节" % 原字节
    结果 = 清理日志(日志目录)
    assert not p.exists(), "太小就连窗口也不管：它还躺在 logs/ 顶层"
    去处 = 日志目录 / "kept" / "logs_panel_0139.txt"
    assert 去处.exists(), "小文件凭空没了：%s" % 结果
    assert 去处.stat().st_size == 原字节, "压完更大还留着：%d" % 去处.stat().st_size
    assert 结果["挪走"][0].get("压后字节") is None, 结果["挪走"]
    assert not 结果["压失败"] and not 结果["压不动"], 结果


def test_躺过两天的小散件不去压它(日志目录):
    """就地压那一支也不能碰低于下限的：多一个 .gz 名字就多一处 日志文件()
    要认的形状，换来的却是更大的体积。名字不带 .log，才轮得到这一支看它。"""
    小 = _造日志(日志目录, "logs_panel_20261005.txt", 内容="2026-10-05 面板起来了\n", 天数前=3)
    结果 = 清理日志(日志目录, 保留天数=14)      # 窗口内：只有"就地压"这一支会看它
    assert 小.exists() and not 小.with_name(小.name + 压缩后缀).exists()
    assert 小.read_text(encoding="utf-8") == "2026-10-05 面板起来了\n"
    # 连"试一下发现不划算"都不该有：每轮白吃一份预算，几百个这种就够把大的挤到下一轮
    assert 结果["压不动"] == [] and 结果["压缩"] == [], 结果


def test_压完不比原来小的算压不动不算压失败(日志目录):
    """真压了才发现不划算（压不动的内容）要单独一句：记进"压失败"就会每 6 小时
    warning 一次，同一句话响到第三遍就没人看了——可它一条错都没犯。"""
    保留 = 日志目录 / "kept"
    保留.mkdir(parents=True, exist_ok=True)
    硬 = 保留 / "boss_bot.log.2026-09-30"
    原样 = os.urandom(值得压字节 * 2)     # 随机字节：gzip 压不动，只会加头尾
    硬.write_bytes(原样)
    结果 = 清理日志(日志目录)
    assert 结果["压不动"] and 结果["压不动"][0]["name"] == "boss_bot.log.2026-09-30", 结果
    assert 结果["压失败"] == [], "把不划算报成故障：%s" % 结果["压失败"]
    assert 硬.exists() and 硬.read_bytes() == 原样, "压不动却动了原件"
    assert not list(保留.glob("*" + 压缩后缀)), "留了一份更大的 .gz 在 kept/"
    assert not list(保留.glob("*在写*")), "临时文件留残在盘上"


def test_回读对不上就一份都不动(日志目录):
    """这条是"压缩不算删"的全部依据：删原件之前必须把 .gz 解回来比过字节。
    把解压那一头打成假的，原件必须还躺在原处，并报一条"压失败"。"""
    p = _造日志(日志目录, "boss_bot.log.2026-10-03", 内容=_够压("那天投了 120 单\n"))
    with patch("log_retention.gzip.decompress", return_value="才不是原来那份".encode("utf-8")):
        结果 = 清理日志(日志目录)
    assert p.exists(), "回读没比过就把原件删了——这就是一条不可逆的删除"
    assert 结果["挪走"] == [] and len(结果["压失败"]) == 1, 结果
    assert not list((日志目录 / "kept").glob("*" + 压缩后缀)), "失败还留下一份半吊子 .gz"
    assert not list((日志目录 / "kept").glob("*在写*")), "临时文件留残在盘上"


def test_一轮压不完的要报剩余并下一轮接着压(日志目录):
    """启动时同步跑，所以一轮必须有界；有界就一定会剩——剩多少得说，不许默默丢下。"""
    for i in (1, 2, 3):
        # 每份都得高于压缩下限，否则"压不完"这一支根本不会看它（见 _够压）
        _造日志(日志目录, "boss_bot.log.2026-10-0%d" % i,
              内容=_够压("行啊\n") * i, 天数前=20 + i)
    结果 = 清理日志(日志目录, 保留天数=99, 每轮最多压个=1)   # 窗口拉满：只走就地压
    assert len(结果["压缩"]) == 1, "预算 1 个却压了 %d 个" % len(结果["压缩"])
    assert 结果["存量剩余"] == 2, "还剩 2 份却没报出来：%s" % 结果["存量剩余"]
    for _ in range(3):
        清理日志(日志目录, 保留天数=99, 每轮最多压个=1)
    剩 = [f.name for f in 日志目录.iterdir()
          if f.is_file() and not f.name.endswith(压缩后缀)]
    assert 剩 == [], "跑了几轮还压不完：%s" % 剩


def test_先压大的(日志目录):
    """预算只有一个，先吃大的：一轮下来体积就该掉下来，而不是先收十份零头。"""
    _造日志(日志目录, "boss_bot.log.2026-10-11", 内容=_够压("小\n"), 天数前=20)
    _造日志(日志目录, "boss_bot.log.2026-10-12", 内容="大" * 4000 + "\n", 天数前=21)
    结果 = 清理日志(日志目录, 保留天数=99, 每轮最多压个=1)
    assert 结果["压缩"] and 结果["压缩"][0]["name"].endswith("10-12"), \
        "先压了零头：%s" % [x["name"] for x in 结果["压缩"]]


def test_正在写的主日志不许挪走(日志目录):
    """主日志被 FileHandler 开着，挪走等于把当天写的那份塞进另一个 inode。"""
    p = _造日志(日志目录, "boss_bot.log", 内容="今天\n", 天数前=30)
    清理日志(日志目录)
    assert p.exists() and p.read_text(encoding="utf-8") == "今天\n"


def test_超过上限先整份备份进kept再截断(日志目录):
    """20MB 那一支以前直接 write_text("")：当天到现在的日志全没了。"""
    内容 = ("x" * 4096 + "\n") * 30
    p = _造日志(日志目录, "boss_bot.log.2026-10-20", 内容=内容, 天数前=1)
    原字节 = p.stat().st_size          # 换行在 Windows 上会被写开，按盘上真实字节算
    结果 = 清理日志(日志目录, 单文件上限字节=40 * 1024)
    assert p.stat().st_size <= 40 * 1024, "过了上限却没被截断"
    备份 = list((日志目录 / "kept").glob("boss_bot.log.2026-10-20.截断前*字节"))
    assert len(备份) == 1, f"截断前没留整份备份：{结果}"
    assert 备份[0].stat().st_size == 原字节
    assert 备份[0].read_text(encoding="utf-8") == 内容
    assert 结果["截断"][0]["字节"] == 原字节


def test_临时面板日志还是照删(日志目录):
    """flask_e2e*/flask_output* 是测试自己造的垃圾，删掉不欠账。"""
    p = _造日志(日志目录, "flask_e2e_probe.log", 内容="噪声\n", 天数前=1)
    清理日志(日志目录)
    assert not p.exists()


def test_除了临时日志没有任何删除出口(日志目录):
    """反向哨兵：二十个真日志进一遍，**解压回来**的字节总量只能挪位置不能变少。

    原来这条比的是盘上字节数；归档压成 .gz 之后盘上字节本来就该变少，
    再比原始字节就成了"压缩一上就全红"。所以读的那一头换成 读日志文本：
    少一个字（或解压解不回来）照样红。
    """
    留 = {}
    for i in range(10):
        n = "boss_bot.log.2026-09-%02d" % (i + 1)
        留[n] = ("第 %d 天的日志\n" % i) * (i + 3)
        _造日志(日志目录, n, 内容=留[n], 天数前=8 + i)
    for i in range(10):
        n = "decline_inbound_acc%d.log" % i
        留[n] = "补拒第 %d 轮\n" % i
        _造日志(日志目录, n, 内容=留[n], 天数前=1)
    进 = {n: len(_读回(日志目录 / n).encode("utf-8")) for n in 留}
    assert sum(进.values()) > 0, "一份都没造出来，这条等于没测"
    结果 = 清理日志(日志目录, 就地压龄秒=0)
    在场 = {}
    for 根 in (日志目录, 日志目录 / "kept"):
        for f in 根.glob("*.log*"):
            在场[f.name.replace(压缩后缀, "")] = len(_读回(f).encode("utf-8"))
    assert 在场 == 进, f"逐份内容对不上账：盘上 {在场}\n原本 {进}"
    assert set(留) <= set(在场), "有文件没了（只剩字节数相等不够）"
    assert not 结果["压失败"], 结果["压失败"]


def test_当天轮换出来的日期文件在窗口内就别动(日志目录):
    _造日志(日志目录, "boss_bot.log.2026-10-20", 内容="昨天\n", 天数前=1)
    结果 = 清理日志(日志目录)
    assert (日志目录 / "boss_bot.log.2026-10-20").exists()
    assert not 结果["挪走"]


def test_面板每天跑的那一支确实接的是挪走不是删():
    """上面几条测的是 清理日志() 本身；这条管接线。

    app.py 里那个 cleanup_old_logs() 才是每天真的会跑的那一支——它要是还留着
    unlink()/write_text("")，本模块写得再干净也没用，日志照样悄悄没了。
    """
    src = (ROOT / "flask-version" / "app.py").read_text(encoding="utf-8")
    函数 = src.split("def cleanup_old_logs():", 1)[1].split("\ndef ", 1)[0]
    assert "清理日志" in 函数, "app.py 没接新判据，还在自己那一套"
    assert ".unlink()" not in 函数, "还在直接删日志文件"
    assert ".write_text(" not in 函数, "还在把超限日志整份清空"
    assert "日志归档压缩" in 函数, "压了多少、还剩多少没上屏，界面和日志里都看不见"
    # 判据要打在"读的是那个清单"上：光比"压失败"这三个字，文档串里提一句就能糊过去
    assert '结果.get("压失败"' in 函数 or '结果["压失败"]' in 函数, \
        "压坏了不吭声，等于把「挪走不删」降级成悄悄少一批"
    assert '结果.get("压不动")' in 函数, \
        "压完不划算的那批被混进「压失败」：每 6 小时一条 warning，响几次就没人信了"
    读日志 = src.split("def api_logs_file():", 1)[1].split("\ndef ", 1)[0]
    assert "日志文件(" in 读日志, "/api/logs/file 还按死路径找，挪进 kept/ 就报这天没日志"
    assert "读日志文本(" in 读日志, "/api/logs/file 还 read_text：.gz 会读成一片乱码"


def test_四方一致那份也不再拼死日志路径():
    """读仓库真日志只有 日志文件()+读日志文本() 这一份读法，测试脚本自己也不例外。

    10-30 同一天中两次：e2e_boss_greet_parity 的 log_truth() 按 `logs/boss_bot.log.<天>`
    找当天那份，那天已经挪进 kept/ 且压成 .gz，它就报"日志文件不存在"；同一族里那条
    比例门更狠——`open()` 读 .gz 读到乱码、一条地址都匹配不上，等于把老行从分母里
    悄悄抽走，直接把判据顶红。两头都是"换了地方"被读成"没有"。
    """
    源 = (ROOT / "tests" / "e2e_boss_greet_parity.py").read_text(encoding="utf-8")
    函数 = 源.split("def log_truth(", 1)[1].split("\ndef ", 1)[0]
    assert "日志文件(" in 函数 and "读日志文本(" in 函数, \
        "log_truth 又回到按死路径读当天日志：压一轮就把那天的证据读没了"
    # 只咬构造式写法（f"boss_bot.log.{day}"），文档串里提一句那个老落点不算犯禁
    代码 = "\n".join(l for l in 函数.splitlines() if '"""' not in l)
    assert 'f"boss_bot.log.' not in 代码 and "f'boss_bot.log." not in 代码, \
        "还在自己拼 boss_bot.log.<日期> 那个老落点"


def test_定时那一支不许把异常吞掉():
    """10-21 那四天日志就是被一支静默的清理线程删掉的：`except Exception: pass`
    等于把"没清"报成"清过了"。"""
    src = (ROOT / "flask-version" / "app.py").read_text(encoding="utf-8")
    线程 = src.split("def _log_cleanup_loop():", 1)[1].split("\ndef ", 1)[0]
    assert "cleanup_old_logs()" in 线程, "后台这一支不再跑清理了？"
    assert not 吞异常(线程), "清理失败还是 pass 掉：这一轮没做成得在日志里留一行"
    assert "6 * 3600" in 线程, "周期回到 24 小时：面板活不过几小时（#276）就等于不跑"


def 吞异常(体):
    import re
    return re.search(r"except Exception[^:]*:\s*\n\s*pass", 体)


def test_挪进kept之后按日期还读得到(日志目录):
    """/api/logs/file 只认 logs/boss_bot.log.<日期> 一个死路径：数据换了地方
    不能跟着报成"这天没日志"。"""
    _造日志(日志目录, "boss_bot.log.2026-10-03", 内容="10-03 那天的日志\n")
    清理日志(日志目录)
    找到 = 日志文件(日志目录, "2026-10-03")
    assert 找到 is not None and 找到.exists(), "挪走之后按日期找不到了（会报成这天没日志）"
    assert _读回(找到) == "10-03 那天的日志\n"


def test_挪进kept又压成gz的那天按日期还读得到(日志目录):
    """线上真实形状：出窗口 + 过了压缩下限 ⇒ 既挪进 kept/ 又带 .gz。

    上一条用的正文只有 17 字节，低于 值得压字节，走的是"原样挪走"那一支，
    所以 `样式` 里 `名 + 压缩后缀` 那一条其实一直没人测到——面板点某天看日志
    走的正是这一条。
    """
    正文 = _够压("10-03 那天的日志\n")
    _造日志(日志目录, "boss_bot.log.2026-10-03", 内容=正文)
    结果 = 清理日志(日志目录)
    assert 结果["挪走"] and 结果["挪走"][0]["去处"].endswith(压缩后缀), \
        "这条前提是那天那份已被压成 .gz 挪进 kept/：%s" % 结果["挪走"]
    找到 = 日志文件(日志目录, "2026-10-03")
    assert 找到 is not None, "压过的归档按日期找不到，界面把这天报成没日志"
    assert _读回(找到) == 正文, "解回来的正文对不上：%s" % 找到


def test_按今天的日期问要拿到正在写的那一份(日志目录):
    """今天这份住在没有日期的 `boss_bot.log` 里，到午夜轮转才改名。按 今天 这个
    日期问，`日志文件()` 只会去拼 `boss_bot.log.<今天>`——拼不出来就回 None，
    于是"正在写的这一份"被报成"这天没日志"。面板那条接口自己绕开了（date==今天
    就传 None），直接按日期问的人（e2e 的 log_truth、逐号体检脚本）拿不到。
    10-30 起号监控时现踩：logs/boss_bot.log 1.3 MB 在写，问今天回 None。"""
    今 = time.strftime("%Y-%m-%d")
    _造日志(日志目录, 主日志名, 内容="今天这一行\n", 天数前=0)
    找到 = 日志文件(日志目录, 今)
    assert 找到 is not None, "按今天的日期问，正在写的这一份读不到"
    assert 找到.name == 主日志名, "拿错了文件：%s" % 找到
    assert _读回(找到) == "今天这一行\n"
    # 反向哨兵：过去那天没有份就许回 None，不许拿今天这份冒充
    assert 日志文件(日志目录, "2026-01-01") is None, "把今天的当成 2026-01-01 的了"


def test_就地压过的按日期也读得到(日志目录):
    """就地压那一支留在 logs/ 原地：`日志文件()` 只认没后缀的那份，
    界面上那几天就从"有日志"变成"这天没日志"——狼来了就是这么来的。"""
    正文 = _够压("10-20 那天\n")
    _造日志(日志目录, "boss_bot.log.2026-10-20", 内容=正文, 天数前=5)
    清理日志(日志目录, 保留天数=14)
    找到 = 日志文件(日志目录, "2026-10-20")
    assert 找到 is not None and 找到.name.endswith(压缩后缀), "没找到原地那份 .gz：%s" % 找到
    assert _读回(找到) == 正文


def test_读日志文本认魔术字节不只认后缀(日志目录):
    """名字改成了 .log 但内容还是 gzip 的那份（手工挪名、half-finished 的旧账）：
    按后缀判会读回一串二进制，按头两字节判才读得回来。"""
    日志目录.mkdir(parents=True, exist_ok=True)
    正文 = "这一行是真的\n"
    伪装 = 日志目录 / "boss_bot.log.2026-10-05"
    伪装.write_bytes(gzip.compress(正文.encode("utf-8")))
    assert 读日志文本(伪装) == 正文
    纯的 = 日志目录 / "boss_bot.log.2026-10-06"
    纯的.write_bytes(正文.encode("utf-8"))
    assert 读日志文本(纯的) == 正文, "没压过的反而读不回来了"


def test_截断过的要回备份那一份(日志目录):
    p = _造日志(日志目录, "boss_bot.log.2026-10-20",
                内容=("y" * 4096 + chr(10)) * 20, 天数前=1)
    清理日志(日志目录, 单文件上限字节=20 * 1024)
    assert p.stat().st_size == 0, "这条前提是已被截断"
    找到 = 日志文件(日志目录, "2026-10-20")
    assert 找到 is not None and 找到.stat().st_size > 0, "读到了被清空的空壳"


def test_当天主日志照旧直接读到(日志目录):
    _造日志(日志目录, "boss_bot.log", 内容="今天这一行\n", 天数前=0)
    找到 = 日志文件(日志目录)
    assert 找到 is not None and 找到.name == "boss_bot.log"


def test_真没有的那天回None(日志目录):
    日志目录.mkdir(parents=True, exist_ok=True)
    assert 日志文件(日志目录, "2026-01-01") is None
