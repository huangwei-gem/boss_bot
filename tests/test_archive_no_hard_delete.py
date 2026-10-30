# -*- coding: utf-8 -*-
"""归档过窗要挪走、不许删（2026-10-20 现场定的）。

盘上事实：data/archive 里 09-30~10-09 共 9 盒 18 份真实投递/回复存档没了，
git 状态是工作区删除（未暂存）——动手的是机器自己：
`main_loop._clean_old_archives` 在每天 00:00 轮换后 `shutil.rmtree` 掉 >10 天的整盒，
`/api/archive/auto_clean` 是同一条逻辑的第二份。他找工作全过程唯一的底，被一条
写死的保留期一早上吃掉，这不叫清理，叫丢数据。

窗口该管的只是"面板默认摆哪几天"，不是"数据还在不在"。
"""
import inspect
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "flask-version" / "app.py"


def _盒(根: Path, 日: date, 正文="x"):
    d = 根 / 日.isoformat()
    d.mkdir(parents=True, exist_ok=True)
    (d / "reply_records.json").write_text(正文, encoding="utf-8")
    (d / "greet_records.json").write_text(正文, encoding="utf-8")
    return d


def _接口体(函数名: str) -> str:
    码 = APP.read_text(encoding="utf-8")
    m = re.search(r"\ndef " + 函数名 + r"\(.*?\n(?=@app\.route|\ndef |\Z)", 码, re.S)
    assert m, f"app.py 里找不到 {函数名}"
    return m.group(0)


@pytest.fixture()
def 归档区(tmp_path):
    return tmp_path / "data" / "archive"


def test_过期盒整盒挪走并且一份都不少(归档区):
    from boss_bot.main_loop import 挪走过期归档, 过期归档去处名
    今 = date(2026, 10, 20)
    旧 = _盒(归档区, 今 - timedelta(days=25), "旧文件")
    assert len(挪走过期归档(归档区, 10, 今天=今)) == 1
    assert not 旧.exists(), "过窗的那盒该被搬走"
    去处 = 归档区.parent / 过期归档去处名 / 旧.name
    assert 去处.is_dir(), "挪去的地方不对就是没挪"
    assert (去处 / "reply_records.json").read_text(encoding="utf-8") == "旧文件"
    assert (去处 / "greet_records.json").is_file()


def test_窗口内的不许动(归档区):
    from boss_bot.main_loop import 挪走过期归档
    今 = date(2026, 10, 20)
    新 = _盒(归档区, 今 - timedelta(days=3))
    assert 挪走过期归档(归档区, 10, 今天=今) == []
    assert 新.is_dir()


def test_目录名不是日期的不碰(归档区):
    """unmix_backup 那一类是别的流程留下的证据，保留期管不着它"""
    from boss_bot.main_loop import 挪走过期归档
    怪 = 归档区 / "unmix_backup"
    怪.mkdir(parents=True)
    (怪 / "keep.json").write_text("{}", encoding="utf-8")
    assert 挪走过期归档(归档区, 0, 今天=date(2026, 10, 20)) == []
    assert 怪.is_dir() and (怪 / "keep.json").is_file()


def test_同一天挪两次不许把第一份盖掉(归档区):
    from boss_bot.main_loop import 挪走过期归档, 过期归档去处名
    今 = date(2026, 10, 20)
    日 = 今 - timedelta(days=25)
    _盒(归档区, 日, "第一次的内容")
    挪走过期归档(归档区, 10, 今天=今)
    _盒(归档区, 日, "第二次的内容")          # 手点一次、零点又一次
    挪走过期归档(归档区, 10, 今天=今)
    去处根 = 归档区.parent / 过期归档去处名
    两份 = sorted(p.name for p in 去处根.iterdir())
    assert len(两份) == 2, f"只留下 {两份}，有一份被第二次挪盖掉了"
    内容 = sorted((去处根 / n / "reply_records.json").read_text(encoding="utf-8") for n in 两份)
    assert 内容 == ["第一次的内容", "第二次的内容"]


def test_归档盒能找到被挪走的那天(归档区):
    from boss_bot.main_loop import 归档盒, 挪走过期归档
    今 = date(2026, 10, 20)
    日 = 今 - timedelta(days=25)
    _盒(归档区, 日, "老底")
    挪走过期归档(归档区, 10, 今天=今)
    找到 = 归档盒(归档区, 日.isoformat())
    assert 找到 and 找到.is_dir(), "挪走以后历史数据面板就该报这天没数据了"
    assert (找到 / "reply_records.json").read_text(encoding="utf-8") == "老底"


def test_窗口内和窗口外都认(归档区):
    from boss_bot.main_loop import 归档盒
    今 = date(2026, 10, 20)
    在窗口 = _盒(归档区, 今)
    assert 归档盒(归档区, 今.isoformat()) == 在窗口
    assert 归档盒(归档区, "2020-01-01") is None


def test_轮换里不许再rmtree归档():
    from boss_bot import main_loop
    src = inspect.getsource(main_loop.UnifiedBotLoop._clean_old_archives)
    assert "rmtree" not in src, "又改成直接删了"
    assert "挪走过期归档" in src


def test_接口那条也得走同一个函数():
    """两条各写一份保留期逻辑，早晚一条删一条挪（项目里判据四份那种毛病）"""
    体 = _接口体("api_archive_auto_clean")
    assert "rmtree(" not in 体, "注释里提一句 rmtree 可以，真调用不行"
    assert "挪走过期归档" in 体


def test_历史数据列表把挪走的也列出来():
    """零点挪出窗口只是换个地方；列表少了这几天，界面就成了「这几天没数据」。"""
    体 = _接口体("api_archive_list")
    assert "过期归档去处名" in 体
    assert "移出窗口" in 体


def test_查看与下载都从一份盒定位():
    for 名 in ("api_archive_download", "api_archive_view"):
        assert "归档盒(" in _接口体(名), f"{名} 还只认 data/archive 里那一盒"


def test_历史闸门也读挪走那一处():
    """「历史上替他说错的那句现在拦得住」这条靠真实存档；搬家后只读 archive/ 会假红
    （10-20 就这么红过一次），所以把两处一起读这件事本身钉住。"""
    src = (ROOT / "tests" / "test_reply_output_guard.py").read_text(encoding="utf-8")
    assert "archive_kept" in src, "闸门只读 data/archive 了，过期盒一搬家就假红"


def test_2026_10_23之后唯一允许删除历史的只有体积闸():
    """他 10-23 改口：「到了 500M 吧自动清理吧，最好是只保留 3 天的」。

    这条不是放水——删除从"不许有"变成"只许存在一个地方，而且那个地方必须同时带
    体积阈值、天数窗口和落盘台账"。别的模块再写 rmtree/unlink 就是绕过口径，
    所以那一族文件（归档、日志留痕、快照、Cookie 备份）继续零真删除。
    """
    闸 = (ROOT / "boss_bot" / "space_retention.py").read_text(encoding="utf-8")
    assert "shutil.rmtree(" in 闸 and ".unlink()" in 闸, "体积闸不再真删了？那这条要翻面"
    for 关键字 in ("阈值MB", "保留天数", "_落台账", "cookie"):
        assert 关键字 in 闸, "体积闸少了 %s：删除必须带阈值、窗口、台账，且豁免登录凭据" % 关键字

    许 = {  # 现算出来的基线：数的是"去掉 # 注释后"的字面出现，含文档串里提一句
        # 4 = 模块头陈述旧行为 1 次 + 删测试临时日志 1 次
        #     + _压走 删原件 1 次（这一步之前必须把 .gz 解回来逐字节比过，比不过不删，
        #       见 test_log_retention::test_回读对不上就一份都不动）
        #     + _压走 失败时删自己那份 .在写 临时文件 1 次（删的是本轮刚写的半成品）
        "flask-version/log_retention.py": 4,
        "flask-version/app.py": 1,             # 探针自己的临时 profile
        "boss_bot/greet_engine.py": 1,         # 它自己写的临时文件
    }
    for 相对 in ("boss_bot/main_loop.py", "boss_bot/self_evolve.py",
                 "boss_bot/browser_launcher.py", "boss_bot/safe_log.py",
                 "boss_bot/config.py", "boss_bot/reply_engine.py",
                 "flask-version/log_retention.py", "flask-version/app.py",
                 "boss_bot/greet_engine.py"):
        源 = (ROOT / 相对).read_text(encoding="utf-8")
        去注释 = re.sub(r'#[^\n]*', "", 源)
        处数 = 去注释.count("rmtree(") + 去注释.count(".unlink()")
        assert 处数 <= 许.get(相对, 0), \
            "%s 里真删除从 %d 处涨到 %d 处——按 10-20 的口径只能挪走，要删请改 space_retention" % (
                相对, 许.get(相对, 0), 处数)

