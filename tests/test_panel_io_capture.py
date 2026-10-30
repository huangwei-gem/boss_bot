# -*- coding: utf-8 -*-
"""面板的标准输出要落进文件：无声死掉的那一次得留下现场。

实测 2026-10-27（探针 .scratch/探针_面板死因.py、.scratch/探针_电源事件2.py）：
- `logs/boss_bot.log` 最后一条是 10-25 11:08:59，之后到下次开机为止整个项目没被
  写过任何一个文件；
- Windows 系统日志给的是 Kernel-Power 41「rebooted without cleanly shutting down
  first」+ EventLog 6008「previous system shutdown at 10:58:37 was unexpected」，
  10-25 11:09:16、20:48 各起来一次；
- 面板这边一句痕迹没有：tools/start_accounts.py 让人去看 `data/panel_stderr.log`，
  那个文件是 10-23 另一次用 PowerShell -RedirectStandardError 拉起来时留下的，
  0KB，双击 start.bat 那条路根本不写它。空文件看着像"没报错"，其实等于没装表。

这里锁的是"装表"这件事本身：起停各一行、stdout 和 stderr 都进文件、屏幕照旧看得见、
涨过阈值挪进 kept/ 而不是删、重复调用不叠两层。
"""
import io
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "flask-version"))

import panel_io  # noqa: E402


@pytest.fixture
def 干净流():
    """接完必须还原：不还原等于让后面每一趟测试的打印都往一个临时文件里灌，
    pytest 自己的输出也跟着进去——那种"绿"是没跑过的。句柄不关，Windows 上
    临时目录删不干净（pytest 会留着几百个目录）。

    原来这里还想把 sys.stdout 换成内存流再断言"屏上看得见"，实测行不通：
    pytest 的捕获器在 fixture 之后、测试函数之前又换一次 sys.stdout，
    拿到的是它的 EncodedFile，不是我自己塞进去的那个。"上屏"那一半改成
    直接喂内存流测 _复写（见 test_屏幕那一侧不许变哑）。
    """
    旧出, 旧错 = sys.stdout, sys.stderr
    try:
        yield
    finally:
        for 流 in (sys.stdout, sys.stderr):
            文件 = getattr(getattr(流, "_盘", None), "_文件", None)
            if 文件 is not None:
                try:
                    文件.close()
                except Exception:
                    pass
        sys.stdout, sys.stderr = 旧出, 旧错


def test_启动与退出各留一行(tmp_path):
    去 = tmp_path / "logs" / "io.log"
    盘 = panel_io._落盘(去)
    盘.写("│面板启动│ pid=%s\n" % os.getpid())
    盘.收场()
    文 = 去.read_text(encoding="utf-8")
    assert "面板启动" in 文 and "面板退出" in 文
    assert "pid=%s" % os.getpid() in 文


def test_stdout_与_stderr_都要进文件(干净流, tmp_path):
    去 = panel_io.接管(路径=str(tmp_path / "io.log"))
    print("这句话要落盘")
    sys.stderr.write("报错也要落盘\n")
    sys.stdout.flush()
    文 = 去.read_text(encoding="utf-8")
    assert "这句话要落盘" in 文 and "报错也要落盘" in 文
    assert isinstance(sys.stdout, panel_io._复写) and isinstance(sys.stderr, panel_io._复写)
    # 起手那一行要 接管 自己写：没有它就分不开"面板起过但没别的话"和
    # "这个文件本来就是旧的"
    assert "面板启动" in 文, "接管 没写下起手那一行"


def test_屏幕那一侧不许变哑(tmp_path):
    """他是双击 start.bat 看着这个窗口跑的：为了留档把窗口变成哑的不行。

    这一条直接喂一个内存流当"控制台"——pytest 自己在每条测试前后都要换
    sys.stdout（现测：fixture 里 setattr 上去的流，进到测试函数就被它换成了
    EncodedFile），拿真 sys.stdout 断言"屏上有没有"必然测的是 pytest 的捕获器。
    """
    屏 = io.StringIO()
    盘 = panel_io._落盘(tmp_path / "io.log")
    流 = panel_io._复写(屏, 盘)
    流.write("既上屏也落盘\n")
    assert "既上屏也落盘" in 屏.getvalue() and "既上屏也落盘" in (tmp_path / "io.log").read_text(encoding="utf-8")


def test_没接住的异常也进文件(干净流, tmp_path):
    """线程里抛出来、由 excepthook 打到 stderr 的那一族，logging 本来就收不到。"""
    去 = panel_io.接管(路径=str(tmp_path / "io.log"))
    try:
        raise ValueError("投递腿里的崩")
    except ValueError:
        sys.excepthook(*sys.exc_info())
    文 = 去.read_text(encoding="utf-8")
    assert "Traceback" in 文 and "投递腿里的崩" in 文


def test_重复调用不叠两层(干净流, tmp_path):
    一 = tmp_path / "a.log"
    二 = tmp_path / "b.log"
    去 = panel_io.接管(路径=str(一))
    再 = panel_io.接管(路径=str(二))
    print("只该出现一次")
    sys.stdout.flush()
    assert str(再) == str(一), "第二次调用换了去处＝两层复写叠起来了"
    assert 一.read_text(encoding="utf-8").count("只该出现一次") == 1
    assert not 二.exists(), "第二次调用又开了一个文件"


def test_涨过阈值整份挪进_kept_而不是删掉(tmp_path):
    去 = tmp_path / "logs" / "io.log"
    盘 = panel_io._落盘(去, 上限=40)
    盘.写("第一段很长很长\n")
    盘.写("第二段也很长很长\n")
    留档 = tmp_path / "logs" / "kept"
    assert 留档.is_dir() and list(留档.iterdir()), "旧的一段没留档"
    assert "第一段" in sorted(留档.iterdir())[0].read_text(encoding="utf-8")
    assert "第二段" in 去.read_text(encoding="utf-8"), "新的一段没接着写"


def test_没定义的属性要转给真正的流(tmp_path):
    """werkzeug/socketio 会问 fileno、encoding 这类东西；转不出去就是启动时一句 AttributeError。

    用内存流自带的 getvalue/writelines 当"复写自己没定义的那个属性"：断言的是
    转发这条机制，不是 StringIO 有没有编码。
    """
    class 带标记的流(io.StringIO):
        标记 = "只有真流才有这个"

    屏 = 带标记的流()
    日志 = tmp_path / "io.log"
    流 = panel_io._复写(屏, panel_io._落盘(日志))
    流.writelines(["一行\n", "两行\n"])
    assert "一行" in 屏.getvalue(), "writelines 没转发到控制台"
    assert "一行" in 日志.read_text(encoding="utf-8"), "writelines 绕过了落盘那一路"
    assert 流.标记 == "只有真流才有这个", "属性没转给真正的流"
    assert 流.getvalue() == 屏.getvalue()


def test_app_的_main_第一件事就接住输出():
    """接不住就等于没装：这一条按代码行判，注释里提到不算。"""
    文 = (ROOT / "flask-version" / "app.py").read_text(encoding="utf-8")
    起 = 文.index("def main():")
    前 = 文[起:起 + 1600]
    真调用 = [l.strip() for l in 前.splitlines()
               if "_接管标准输出()" in l and not l.strip().startswith("#")]
    assert 真调用, "main() 里只 import 了 panel_io，没有真的调用接管"
    assert 前.index("panel_io") < 前.index('logger.info("'), \
        "接住输出必须排在第一句日志之前：面板起不来多半是 logging 还没通就抛了"


def test_启动脚本不许再指那个空文件():
    """data/panel_stderr.log 是 10-23 另一次拉法留下的 0KB 旧文件。
    把人指向它，等于把"查不到原因"写成"没有原因"。"""
    文 = (ROOT / "tools" / "start_accounts.py").read_text(encoding="utf-8")
    assert "panel_stderr" not in 文, "还在让人看那个根本不写的旧文件"
    assert "panel_io.log" in 文
