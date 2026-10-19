# -*- coding: utf-8 -*-
"""启动时等人工登录：等不到要接着等，不许把整条流水线退掉。

2026-10-16 05:18:21 面板把主账号的窗口摆到桌面上写"登进去后这一轮自己继续"，
05:23:22 就写「登录超时，主循环退出」把两条腿全撤了。他后来真登进去了（10:12
只读探 9222：两张页都停在 `chengshi/c101250800` 城市首页，不是登录页；
zhipin_cookies.json 的 mtime 是登录成功那一下存下的），可是没人在等了——
主账号 `running=false phase=stopped` 一直挂到 10:12，当天 0 单。

`login.wait_timeout` 是给"这一次看多久"定的，不是给"这个号还跑不跑"定的。
把人睡着的 5 分钟当成放弃整条腿的理由，就是把这句话变成了空头承诺。
"""
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _造循环(monkeypatch, account_index=0):
    with patch("boss_bot.main_loop.BrowserManager"):
        from boss_bot.main_loop import UnifiedBotLoop
        lp = UnifiedBotLoop(account_index=account_index)
    lp._log = lambda level, msg: lp.日志.append((level, msg))
    lp.日志 = []
    lp._stop_event = threading.Event()
    lp._running = True
    lp.browser_manager = MagicMock()
    lp.browser_manager.get_instance.return_value = MagicMock()
    # 等待之前要先问一句"端口上挂的还是不是我那份档案"（见 等登录现场）。
    # 默认答"窗口在"，让老用例只管等待本身；专门测这一问的在下面那个类里。
    lp.browser_manager.等登录现场.return_value = ("窗口在", "")
    # Cookie 路径必须落在临时目录：这条链路一旦判成登录墙就会把 Cookie 文件归档搬走，
    # 写真的文件名等于把用户那次人工登录的会话搬进 data/stale_cookies/
    # （2026-10-17 16:16 就搬走了主账号那份，靠归档原样拷回才没逼人重新登录）。
    假cookie = Path(tempfile.mkdtemp(prefix="boss_test_cookie_")) / "zhipin_cookies.json"
    假cookie.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(lp, "_cookie_file", lambda: str(假cookie))
    monkeypatch.setattr(lp, "_discard_stale_cookies", lambda *a, **k: None)
    monkeypatch.setattr(lp, "_ensure_login_window", lambda *a, **k: True)
    monkeypatch.setattr(lp, "_save_cookies_if_logged_in", lambda *a, **k: True)
    monkeypatch.setattr(lp, "_finish_login_window", lambda: None)
    # 走到等待之前那段"读 Cookie 文件 + 复核会话页"要跳过：这条测试只管等待
    monkeypatch.setattr(lp, "_login_state_now", lambda instance: "uncertain")
    monkeypatch.setattr(lp, "_has_live_auth_cookie", lambda instance: False)
    monkeypatch.setattr(lp, "_interruptible_sleep", lambda s: None)
    return lp


class 等不到要继续等Test:

    def test_第一轮超时第二轮等到就算成功(self, monkeypatch):
        """核心那条：wait_timeout 过一轮不等于这个号不干了"""
        lp = _造循环(monkeypatch)
        轮次 = []

        def 假等(instance, cookie_file):
            轮次.append(1)
            return len(轮次) >= 2        # 第一轮没登进来，第二轮他扫完了

        monkeypatch.setattr(lp, "_wait_for_login", 假等)

        assert lp._handle_login() is True, "等到第二轮就该继续，不许退出"
        assert len(轮次) == 2

    def test_每轮超时都要留一条还在等的自述(self, monkeypatch):
        """面板日志得说清"这个号在等人登录"，不能悄悄挂着 stopped 让人以为冻住"""
        lp = _造循环(monkeypatch)
        轮次 = []

        def 假等(instance, cookie_file):
            轮次.append(1)
            return len(轮次) >= 3

        monkeypatch.setattr(lp, "_wait_for_login", 假等)
        lp._handle_login()

        念 = [m for _, m in lp.日志 if "等人登录" in m]
        assert len(念) == 2, "超时的每一轮都要有一条，实际: %r" % (lp.日志,)
        assert "第 1 轮" in 念[0] and "第 2 轮" in 念[1], 念

    def test_登录成功后照旧换回配置形态(self, monkeypatch):
        """等待改成多轮以后，"登完把临时借的有头还回去"这一手不能被挤掉"""
        lp = _造循环(monkeypatch)
        还过 = []
        monkeypatch.setattr(lp, "_wait_for_login", lambda i, c: len(还过) == 0 or True)
        monkeypatch.setattr(lp, "_finish_login_window", lambda: 还过.append(1))
        lp._login_headed_temp = True

        assert lp._handle_login() is True
        assert 还过, "登录确认后必须调 _finish_login_window，否则桌面挂着有头"
        assert lp._needs_login is False
        assert lp._logged_in is True


class 退出等待的只有这两种Test:
    """等待只在"人叫停了"或"浏览器真没了"时结束，别的情况都得继续等。"""

    def test_收到停止信号立刻退出(self, monkeypatch):
        lp = _造循环(monkeypatch)
        lp._stop_event.set()
        轮次 = []
        monkeypatch.setattr(lp, "_wait_for_login",
                            lambda i, c: (轮次.append(1), False)[1])

        assert lp._handle_login() is False
        assert len(轮次) == 1, "叫停了还要再开一轮等待就是在空转"

    def test_运行标志被撤也要退出(self, monkeypatch):
        """面板点停止后 _running 变假，等待线程必须跟着收，不然停不干净"""
        lp = _造循环(monkeypatch)
        轮次 = []

        def 假等(instance, cookie_file):
            轮次.append(1)
            lp._running = False
            return False

        monkeypatch.setattr(lp, "_wait_for_login", 假等)

        assert lp._handle_login() is False
        assert len(轮次) == 1

    def test_浏览器没了当场重开再接着等(self, monkeypatch):
        """人把窗口关了 / 进程被杀：重开一台、把窗口重新摆出来，接着等。

        这里不能"如实失败"地撤腿（旧写法），但也不能对着一个死句柄白轮——
        句柄必须当场摘掉，否则两条腿接着拿死对象干活几百次（10-18 11:23 那种）。
        """
        lp = _造循环(monkeypatch)
        次 = []

        def 假等(instance, cookie_file):
            次.append(1)
            if len(次) == 1:
                lp.browser_manager.等登录现场.return_value = ("窗口没了", "端口上没人")
            else:
                lp._running = False        # 第二轮起人叫停，等待这才收尾
            return False

        monkeypatch.setattr(lp, "_wait_for_login", 假等)

        assert lp._handle_login() is False
        assert lp.browser_manager.launch.called, "浏览器没了要当场重开"
        assert lp.browser_manager.摘掉句柄.called, "死句柄不摘掉，下一轮还拿它白轮"
        assert any("重开" in m for _, m in lp.日志), lp.日志


class 等的必须是自己那台浏览器Test:
    """2026-10-19 12:35:22 账号2 的页面句柄抛 PageDisconnectedError（自己那台没了），
    12:35:46 另一个项目拿 DrissionPage 临时档案（`Temp\\DrissionPage\\userData\\9223`）
    占了同一个 9223。面板从 12:37 起每 5 分钟念一句"窗口继续留在桌面上等着，
    登进去自己继续"，可桌面上根本没有这个号的窗口——他妹妹在那台别人的浏览器里
    登完，面板读的却是自己那份死句柄，于是"我妹妹登录了，为什么还是让我登录"。

    端口有人应答 ≠ 我的浏览器还在。等登录之前必须先问这一句。
    """

    def test_端口上挂着别人的档案要认出来(self):
        from boss_bot.browser_launcher import 端口上是谁的浏览器
        结 = 端口上是谁的浏览器(
            9223, r"C:\Users\me\orca\boss_bot\browser_data\account_1",
            owners=[{"pid": 45228, "port": 9223,
                     "user_data_dir": r"C:\Users\me\AppData\Local\Temp\DrissionPage\userData\9223"}])
        assert 结["归属"] == "别人", 结
        assert "45228" in 结["说明"] and "Temp" in 结["说明"], 结["说明"]

    def test_自家档案在端口上就是还在(self):
        from boss_bot.browser_launcher import 端口上是谁的浏览器
        目录 = r"C:\Users\me\orca\boss_bot\browser_data\account_1"
        结 = 端口上是谁的浏览器(9223, 目录,
                              owners=[{"pid": 7, "port": 9223, "user_data_dir": 目录}])
        assert 结["归属"] == "自家", 结

    def test_端口上没进程就是没了(self):
        from boss_bot.browser_launcher import 端口上是谁的浏览器
        结 = 端口上是谁的浏览器(9223, r"C:\x\account_1", owners=[])
        assert 结["归属"] == "没人", 结

    def test_比对不了的不许说成别人的(self):
        """老 CLI 不写 --user-data-dir 时无从比对：说不准→调用方当作还在。

        判成"别人"会让等登录这条腿去摘自己正在用的句柄、甚至重开，
        把一个好端端跑着的号弄成打断。
        """
        from boss_bot.browser_launcher import 端口上是谁的浏览器
        结 = 端口上是谁的浏览器(9223, "", owners=[{"pid": 7, "port": 9223, "user_data_dir": ""}])
        assert 结["归属"] == "说不准", 结

    def test_管理器把归属翻成现场(self):
        import tempfile
        from pathlib import Path
        from boss_bot.browser_launcher import BrowserManager
        m = BrowserManager(config=None, account_index=1, port=9223,
                           user_data_dir=str(Path(tempfile.mkdtemp()) / "account_1"))
        现场 = m.等登录现场(owners=[{"pid": 45228, "port": 9223,
                                    "user_data_dir": r"C:\other\temp\9223"}])
        assert 现场[0] == "窗口被别人占了", 现场

    def test_被别人占了要说得出那不是这个号的窗口(self, monkeypatch):
        lp = _造循环(monkeypatch, account_index=1)
        lp.browser_manager.等登录现场.return_value = (
            "窗口被别人占了", "端口 9223 上挂着 PID=45228，档案 C:\\Temp\\9223")
        次 = []

        def 假等(instance, cookie_file):
            次.append(1)
            if len(次) >= 2:
                lp._running = False
            return False

        monkeypatch.setattr(lp, "_wait_for_login", 假等)

        assert lp._handle_login() is False
        assert lp._login_reason == "browser_taken", lp._login_reason
        念 = [m for _, m in lp.日志 if "窗口继续留在桌面上等着" in m]
        assert not 念, "端口上不是自己那台还念这句就是狼来了: %r" % (念,)
        assert any("不是这个号" in m for _, m in lp.日志), lp.日志

    def test_被别人占了不许去关别人的浏览器(self, monkeypatch):
        """DrissionPage 的 quit 是照端口发的：端口上挂着邻居时 quit 会把邻居
        连带他刚登进去的会话一起关掉。红线是只关自己起的。"""
        lp = _造循环(monkeypatch, account_index=1)
        lp.browser_manager.等登录现场.return_value = ("窗口被别人占了", "PID=45228")
        次 = []

        def 假等(instance, cookie_file):
            次.append(1)
            if len(次) >= 2:
                lp._running = False
            return False

        monkeypatch.setattr(lp, "_wait_for_login", 假等)
        lp._handle_login()

        assert lp.browser_manager.摘掉句柄.called, "死在邻居身上的句柄要摘掉"
        assert not lp.browser_manager.close.called, "close 会 quit 到邻居的浏览器上"
        assert not lp.browser_manager.launch.called, "端口还被占着重开只会撞守卫"

    def test_配置那行要说这个号真正用的端口(self):
        """12:31:27 日志写 `[账号2] 浏览器配置: ... debug_port=9222`——那是全局偏移量。
        照着它去查"账号2 连到了哪台浏览器"会一路追到主账号身上。"""
        import tempfile
        from boss_bot.browser_launcher import BrowserManager
        m = BrowserManager(config=None, account_index=1, port=9223,
                           user_data_dir=str(Path(tempfile.mkdtemp()) / "account_1"))
        现场 = m.启动现场()
        assert "9223" in 现场 and "account_1" in 现场, 现场
        源 = (ROOT / "boss_bot" / "main_loop.py").read_text(encoding="utf-8")
        块 = 源[源.index("浏览器配置:"):源.index("浏览器进程已启动")]
        assert "debug_port=self.config.browser.debug_port" not in 块, "又把偏移量印成端口"

    def test_探针说窗口在就照旧念还在等(self, monkeypatch):
        """反向哨兵：这一问不许把正常的"等人登录"自述也一起改掉"""
        lp = _造循环(monkeypatch)
        次 = []

        def 假等(instance, cookie_file):
            次.append(1)
            if len(次) >= 2:
                lp._running = False
            return False

        monkeypatch.setattr(lp, "_wait_for_login", 假等)
        lp._handle_login()

        assert any("窗口继续留在桌面上等着" in m for _, m in lp.日志), lp.日志
        assert lp._login_reason != "browser_taken"


class 面板那条一次性等待不动Test:
    """「我已登录」按钮起的后台线程是一次性的：等不到就标超时，别常驻。

    它不在跑流水线，长期挂着只会占一个线程和一个 _login_wait_active 顶位。
    """

    def test_超时仍然收口(self, monkeypatch):
        lp = _造循环(monkeypatch, account_index=1)
        lp._login_wait_active = False
        monkeypatch.setattr(lp, "_wait_for_login", lambda i, c: False)
        lp._needs_login = True

        lp._finish_manual_login(MagicMock())

        assert lp._login_reason == "login_timeout"
        assert lp._logged_in is not True


class 承诺和代码要对得上Test:
    """摆窗口那句话写的是"登进去后这一轮自己继续"，代码不许反过来退出。"""

    def test_启动路径里没有登录超时退流水线(self):
        src = (ROOT / "boss_bot" / "main_loop.py").read_text(encoding="utf-8")
        块 = src[src.index("def _handle_login"):src.index("def open_login_page")]
        assert "主循环退出" not in 块, "等登录这一路不许自己判死整条腿"
        assert "wait_timeout" in 块 or "_wait_for_login" in 块

    def test_面板那句话不许再写成会自动继续却又退(self):
        src = (ROOT / "boss_bot" / "main_loop.py").read_text(encoding="utf-8")
        assert "登进去后这一轮自己继续" in src, "摆窗口的承诺要留着"
        assert "登录超时，主循环退出" not in src


class 自动登录成功要把等登录那盏灯灭掉Test:
    """16:11 实测：主账号这一轮 Cookie 注入成功、页面验过已登录、两条腿都在跑，
    面板却还写着 needs_login=true / phase=waiting_login——那是上一轮
    （16:06 那次 load_cookies 超时）留下的标记没人清。
    他就是被这种"界面说卡住、其实没卡"骗过好几回（"我登录了主账号为什么还是卡住了"）。"""

    def test_遗留的等登录标记要清掉(self, monkeypatch):
        lp = _造循环(monkeypatch)
        lp._needs_login = True
        lp._login_reason = "no_cookie"
        monkeypatch.setattr(lp, "_login_state_now", lambda instance: "logged_in")
        assert lp._handle_login() is True
        assert lp._needs_login is False, "页面都验过登录了还挂着等登录"
        态 = lp.get_status()
        assert 态["logged_in"] is True, 态
        assert 态["phase"] != "waiting_login", 态["phase"]

    def test_界面那盏灯不许自己把跑着的号说成卡住(self):
        """status 的 logged_in 是 _logged_in and not _needs_login 拼出来的，
        谁改了 _needs_login 就得两处一起对，否则面板又开始骗人"""
        src = (ROOT / "boss_bot" / "main_loop.py").read_text(encoding="utf-8")
        成功块 = src[src.index('if state == "logged_in":'):
                     src.index('if state == "login_wall":')]
        assert "_needs_login = False" in 成功块, "自动登录成功却不清等登录标记"

    def test_自动登录没成时这盏灯要还亮着(self, monkeypatch):
        """反向哨兵：判不准就是判不准，不能顺手把等登录灭掉"""
        lp = _造循环(monkeypatch)
        lp._needs_login = False

        def 等不到(instance, cookie_file):
            lp._running = False      # 人叫停，等待这才收尾（不许自己判死整条腿）
            return False

        monkeypatch.setattr(lp, "_login_state_now", lambda instance: "login_wall")
        monkeypatch.setattr(lp, "_wait_for_login", 等不到)
        assert lp._handle_login() is False
        assert lp._needs_login is True


class 端口被占时初始化不许自己判死Test:
    """13:23:12 账号2：「浏览器启动失败，主循环退出」——9223 上挂着兄弟项目的窗口，
    守卫拒连是对的，可整条腿一撤，他把那扇窗口关掉之后没有任何东西会再试一次：
    界面停在"未启动"，只能回来喊我一句再点启动。等登录那条腿已经改过一轮
    （只有人叫停才算走不通），初始化这条腿是同一种病。"""

    def _造初始化(self, monkeypatch, 现场):
        lp = _造循环(monkeypatch, account_index=1)
        成功 = []

        def 假登录():
            成功.append(1)
            # 走到这一步就证明"端口空出来以后这一趟自己接上了"。
            # 后面那两条腿不在本测试的范围里，叫停让 _init_and_run 收尾——
            # 它尾部那句 while self._running 陪一个永远不 set 的 Event 就是死循环。
            lp._running = False
            return False

        lp.browser_manager.等登录现场.return_value = 现场
        monkeypatch.setattr(lp, "_claim_run", lambda: None)
        monkeypatch.setattr(lp, "_handle_login", 假登录)
        monkeypatch.setattr(lp, "_init_engines", lambda: None)
        monkeypatch.setattr(lp, "_interruptible_sleep", lambda s: None)
        monkeypatch.setattr(lp._stop_event, "wait", lambda timeout=None: False)
        return lp, 成功

    def test_端口被占要接着试到人有空(self, monkeypatch):
        lp, _ = self._造初始化(monkeypatch, ("窗口被别人占了", "PID=45228 档案 Temp"))
        趟 = []

        def 假起():
            趟.append(1)
            if len(趟) >= 3:
                lp._running = False      # 第三趟之后人叫停，这才算走不通
            return False

        monkeypatch.setattr(lp, "_init_browser", 假起)
        lp._init_and_run()

        assert len(趟) >= 3, "端口被占就当这个号不干了，空出来也没人接: %s" % (趟,)
        assert lp._login_reason == "browser_taken", lp._login_reason
        assert lp.browser_manager.摘掉句柄.called, "死句柄留着下一趟照样白干"
        assert any("一空出来" in m or "空出来" in m for _, m in lp.日志), lp.日志

    def test_端口一空出来自己就把号跑起来(self, monkeypatch):
        lp, 成功 = self._造初始化(monkeypatch, ("窗口被别人占了", "PID=45228"))
        趟 = []

        def 假起():
            趟.append(1)
            return len(趟) >= 2          # 第二趟他关掉了那个窗口

        monkeypatch.setattr(lp, "_init_browser", 假起)
        lp._init_and_run()

        assert 成功 == [1], "浏览器一起来就必须往下走登录，不许停在初始化"
        assert lp._login_reason != "browser_taken", "端口空出来了还挂着被占"

    def test_别的原因起不来还是当场认失败(self, monkeypatch):
        """反向哨兵：cloakbrowser 没了/参数坏了那种重试没意义，不许改成空转。"""
        lp, 成功 = self._造初始化(monkeypatch, ("窗口在", ""))
        趟 = []

        def 假起():
            趟.append(1)
            return False

        monkeypatch.setattr(lp, "_init_browser", 假起)
        lp._init_and_run()

        assert len(趟) == 1, "不是端口被占却一遍遍重开，就是在拿坏配置空转"
        assert lp._running is False
        assert 成功 == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
