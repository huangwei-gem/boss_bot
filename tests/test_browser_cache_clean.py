# -*- coding: utf-8 -*-
"""`tools/clean_browser_cache.py`：只准长回来的那族缓存被动，登录原件一个都不许碰。

他 2026-10-30 的口径：「browser_data 那 1782.5 MB 怎么办」→「只删可再生缓存」。
现数（本机 account_0 = 820.4 MB）：`Default/Cache` 405.2 + `Default/Code Cache` 312.9
= 718.1 MB（87.5%）是渲染缓存；其余是 Cookies / Local State / Sessions / Crashpad 这些原件。

这一族门全部打在**盘上真实结果**上：造一台盒、跑一次、逐个路径回读——
"报告说清了" 而原件没了，或者"原件还在" 而缓存没清掉，两边都得红。
配合 `.scratch/mutate_cache.py` 的变异跑，每条都验过对应门会红。
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import clean_browser_cache as C  # noqa: E402

原件族 = {
    "Default/Network/Cookies": b"zp_at=SECRET\n",
    "Default/Cookies": b"old sqlite\n",
    "Default/Login Data": b"logins\n",
    "Default/Web Data": b"forms\n",
    "Default/Preferences": b"{}\n",
    "Default/Secure Preferences": b"{}\n",
    "Local State": b"os_crypt key here\n",
    "Default/Local Storage/leveldb/CURRENT": b"LOCK\n",
    "Default/Session Storage/CURRENT": b"LOCK\n",
    "Default/Sessions/Session_1": b"tabs\n",
    "Default/IndexedDB/www.zhipin.com_0/indexeddb.leveldb/CURRENT": b"LOCK\n",
    "Default/Service Worker/CacheStorage/xx/index": b"sw cache\n",
    "Default/Crashpad/settings.dat": b"crashpad scene\n",
    "component_crx_cache/a.txt": b"crx\n",
    "optimization_guide_model_store/m.bin": b"model\n",
    "Safe Browsing/bytes": b"list\n",
}
缓存族样例 = {
    "Default/Cache/Cache_Data/f1": b"x" * 4096,
    "Default/Cache/Cache_Data/index": b"idx\n",
    "Default/Code Cache/js/abc": b"y" * 8192,
}


def _造假盒(根, 名="account_0", 带锁=False):
    盒 = Path(str(根)) / "browser_data" / 名
    for 相, 内 in list(原件族.items()) + list(缓存族样例.items()):
        p = 盒 / 相
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(内)
    (盒 / "Default").mkdir(parents=True, exist_ok=True)
    if 带锁:
        (盒 / "lockfile").write_text("")
    return 盒


@pytest.fixture()
def 假仓库(tmp_path):
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_只动那两族缓存其余原件一份不少(假仓库):
    盒 = _造假盒(假仓库)
    前 = {相: (盒 / 相).read_bytes() for 相 in 原件族}
    址 = C.缓存目录(盒)
    assert [p.name for p in 址] == ["Cache", "Code Cache"], 址
    结果 = C.清一台(盒)
    assert 结果["个"] == len(缓存族样例) and 结果["字节"] == sum(
        len(v) for v in 缓存族样例.values()), 结果
    for 相 in 缓存族样例:
        assert not (盒 / 相).exists(), "缓存族里这个没删掉：%s" % 相
    for p in 址:
        assert not p.exists(), "空目录本身没拔掉，下次扫还当它是缓存：%s" % p
    后 = {相: (盒 / 相).read_bytes() for 相 in 原件族}
    assert 前 == 后, "登录原件被动了：%s" % [相 for 相 in 原件族 if 前[相] != 后[相]]
    assert 结果["失败"] == [], 结果["失败"]


def test_带lockfile那台整台跳过不动一个文件(假仓库):
    """Chrome 正在用上时清它的缓存：Windows 会留一叠"删不掉"，还可能把那次会话搞崩。
    lockfile 是实测信号——五台里只有挂着 6 个 chrome.exe 的 account_1 带着它。"""
    盒 = _造假盒(假仓库, 名="account_1", 带锁=True)
    清 = C.扫(假仓库)
    assert len(清) == 1 and 清[0]["跳过"], 清
    assert C.main(["--run"], 仓库根=假仓库) == 0
    for 相 in 缓存族样例:
        assert (盒 / 相).exists(), "锁着还动手：%s" % 相
    assert not (假仓库 / "data" / C.台账名).exists(), "什么都没清还落台账，台账就成了假账"


def test_不写run一个字节都不许掉(假仓库):
    """默认必须是干跑：这条脚本唯一的不可逆动作就是删，而"顺手跑一下"最容易变成删。"""
    盒 = _造假盒(假仓库)
    前 = {相: (盒 / 相).stat().st_size for 相 in list(缓存族样例) + list(原件族)}
    assert C.main([], 仓库根=假仓库) == 0
    后 = {相: (盒 / 相).stat().st_size for 相 in list(缓存族样例) + list(原件族)}
    assert 前 == 后, "干跑把文件删了/改了"
    assert not (假仓库 / "data" / C.台账名).exists()


def test_报的数就是盘上少的数(假仓库):
    """「释放 1325 MB」这种话只能从盘上算出来：逐盒报的字节必须等于消失的字节。"""
    盒 = _造假盒(假仓库)
    前 = sum((盒 / 相).stat().st_size for 相 in 缓存族样例)
    assert C.main(["--run"], 仓库根=假仓库) == 0
    落 = (假仓库 / "data" / C.台账名)
    行 = [json.loads(x) for x in 落.read_text(encoding="utf-8").splitlines()]
    assert len(行) == 1 and 行[0]["盒"].endswith("account_0"), 行
    assert 行[0]["字节"] == 前 and 行[0]["个"] == len(缓存族样例), "台账和盘上对不上"
    assert 行[0]["时刻"] and "失败" in 行[0]


def test_锁着的文件要单列出来不许当成清了(假仓库):
    """rmtree 一把梭会"删到第一个锁着的就抛"，剩下的整片没删还被报成成功。
    这里把 unlink 打桩成"Cache_Data/f1 永远删不掉"，那一半必须原样在、必须进失败清单。"""
    盒 = _造假盒(假仓库)
    钉 = 盒 / "Default" / "Cache" / "Cache_Data" / "f1"
    真unlink = Path.unlink

    def 只咬一个(self, *a, **kw):
        if self == 钉:
            raise OSError(32, "另一个程序正在使用此文件")
        return 真unlink(self, *a, **kw)

    with patch.object(Path, "unlink", 只咬一个):
        结果 = C.清一台(盒)
    assert 钉.exists(), "锁着的那份被当成删掉了"
    assert any(钉.name in x["路径"] for x in 结果["失败"]), 结果["失败"]
    assert 结果["字节"] < sum(len(v) for v in 缓存族样例.values()), "报数把没删的算进去了"


def test_两处profile根都要认到(假仓库):
    """只扫 browser_data 就漏掉 flask-version 那台 410 MB 的旧盒——它是当年按 cwd 锚
    长出来的，正因为在第二处，一直没进过任何清理口径。"""
    _造假盒(假仓库, 名="account_0")
    另 = Path(str(假仓库)) / "flask-version" / "browser_data" / "account_0"
    for 相 in 缓存族样例:
        p = 另 / 相
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"z" * 16)
    相对 = sorted(Path(x["盒"]).relative_to(假仓库) for x in C.扫(假仓库))
    assert 相对 == [Path("browser_data") / "account_0",
                Path("flask-version") / "browser_data" / "account_0"], 相对
    assert sum(x["字节"] for x in C.扫(假仓库)) == sum(
        len(v) for v in 缓存族样例.values()) + 48


def test_没有Default子目录的不算一台盒(假仓库):
    """`browser_data/` 底下还有 e2e/、ui_verify/ 这类空壳散件；把它们当账号盒扫，
    报出来的"能清多少"就是虚的。"""
    盒 = _造假盒(假仓库)
    (盒.parent / "散件").mkdir()
    (盒.parent / "散件" / "readme.txt").write_text("不是 profile")
    assert [Path(x["盒"]).name for x in C.扫(假仓库)] == ["account_0"]


def test_缓存族里不许混进任何一个原件名():
    """反向哨兵：这一族是清单不是判据，谁往里加一个"Network"或"Cookies"，
    下一次跑就把登录态当缓存删了——那是不可逆的掉登录。"""
    assert C.缓存族 == ("Cache", "Code Cache"), C.缓存族
    for 名 in C.缓存族:
        小 = 名.lower()
        撞 = [其 for 其 in C.豁免原件 if 其 in 小 or 小 in 其]
        assert not 撞, "%s 撞进豁免名单 %s" % (名, 撞)


def test_豁免名单盖住掉登录那一族():
    """Local State 里是解密 Cookie 的 key——它没了等于那份 Cookies 变成读不懂的字节。"""
    for 关键 in ("cookies", "local state", "login data", "preferences", "crashpad"):
        assert 关键 in C.豁免原件, "豁免名单少了 %s" % 关键


def test_这脚本谁都没挂上自动跑():
    """他 10-29 定死"不许自启动，只在他点的时候跑"：这脚本只许他自己跑或叫机器人跑，
    面板线程、计划任务、start.bat 里出现一次就是绕过口径。"""
    查 = ["flask-version/app.py", "boss_bot/main_loop.py", "start.bat",
          "tools/start_accounts.py", "boss_bot/browser_launcher.py"]
    for 相对 in 查:
        源 = (ROOT / 相对).read_text(encoding="utf-8")
        assert "clean_browser_cache" not in 源, "%s 里挂上了自动清缓存" % 相对
    注册 = " ".join((ROOT / 相对).read_text(encoding="utf-8")
               for 相对 in 查 if (ROOT / 相对).name.endswith(".py"))
    assert "schtasks" not in 注册
