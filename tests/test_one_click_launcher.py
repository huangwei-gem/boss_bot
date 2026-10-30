# tests/test_one_click_launcher.py
"""一键启动：start.bat 只能是 ASCII 壳，判断全在 tools/start_accounts.py 里。

2026-10-23 双击实测踩到的三个坑，逐个钉住：
1) `chcp 65001` 之后 cmd 读带中文的 UTF-8 批处理会走偏——`set PYTHONUTF8=1` 被当成
   `YTHONUTF8` 执行，REM 注释里掉前缀的半截中文被当成命令跑（报 `'and' 不是内部或外部命令`），
   `goto` 还会落错标签。所以整个文件必须纯 ASCII。
2) 我第一版写了 `>/dev/null`（bash 写法），cmd 只会打印"系统找不到指定的路径"。
3) 旧脚本第 [4/6] 步是 `python kill_port.py 5000`——"强力终止占用指定端口的进程"；
   面板自己有单实例守卫，这里只许判断端口有没有人应答，绝不动别人的进程。
"""
import importlib.util
import io
import subprocess
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
工具 = ROOT / "tools" / "start_accounts.py"
# 假面板用的口。故意不取 500x：那些是"面板口形状"的数，测试里再写一遍就成了第二份判据
# （`tests/test_panel_port_source.py` 有一条门扫仓库里的端口字面量），也容易哪天真撞上门。
假口 = 18080


def _载具():
    spec = importlib.util.spec_from_file_location("start_accounts_被测", 工具)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Test批处理形状:
    def test_start_bat必须纯ASCII(self):
        """中文写进 bat 不是"难看"，是会改变执行结果：见模块 docstring 第 1 条。"""
        字节 = (ROOT / "start.bat").read_bytes()
        非ascii = [(i, b) for i, b in enumerate(字节) if b > 127]
        assert not 非ascii, "start.bat 里有 %d 个非 ASCII 字节，cmd 会读走偏: %r" % (
            len(非ascii), 字节[max(0, 非ascii[0][0] - 20):非ascii[0][0] + 20])

    def test_start_bat每一行都得是认识的那几种(self):
        """掉前缀的注释行会被当命令执行——上面那条噪声就是这一门抓的。"""
        允许 = ("@echo", "rem ", "title ", "cd ", "set ", "where ", "if ", "echo ",
                "echo.", "goto", "start ", "python ", "pause", ":")
        坏 = [条 for 条 in (l.strip() for l in (ROOT / "start.bat")
                            .read_text(encoding="ascii").splitlines())
              if 条 and not 条.lower().startswith(允许)]
        assert not 坏, "这几行既不是命令也不是 REM（多半是注释掉了前缀）: %r" % (坏,)

    def test_start_bat用CRLF且不许有bash写法(self):
        字节 = (ROOT / "start.bat").read_bytes()
        assert 字节.count(b"\n") == 字节.count(b"\r\n"), "start.bat 混进了 LF 行，cmd 会拼错行"
        assert b"/dev/null" not in 字节, "cmd 不认 /dev/null，只会打印'系统找不到指定的路径'"

    def test_任何一端都不许真去调用kill_port(self):
        """注释/文档里可以提它的名字（要交代为什么不用），可执行代码里出现就算违规。"""
        for 名 in ("start.bat", "start.sh"):
            for 行 in (ROOT / 名).read_text(encoding="utf-8").splitlines():
                条 = 行.strip()
                if "kill_port" not in 条:
                    continue
                assert 条.lower().startswith(("rem ", "#")), \
                    "%s 里 kill_port 只能待在注释里: %s" % (名, 条)
        # Python 那份：docstring 里要讲历史，所以按 token 剥掉注释和字符串再看
        源 = 工具.read_text(encoding="utf-8")
        剩下 = "".join(t.string for t in
                       tokenize.generate_tokens(io.StringIO(源).readline)
                       if t.type not in (tokenize.COMMENT, tokenize.STRING))
        assert "kill_port" not in 剩下, "起号脚本不许再去杀面板口上的进程"

    def test_隐藏起面板那条路要先探端口再动日志(self):
        """tools/start_panel.ps1 用 -RedirectStandardOutput 起面板：一开就把 panel_stdout.log
        截成空的，而正在跑的那个面板还按旧偏移往里写，现场就变成一片 NUL——那份日志是
        "面板被不明终止"(#190) 唯一的事实来源。所以 Start-Process 必须排在 TCP 探活之后。"""
        文 = (ROOT / "tools" / "start_panel.ps1").read_text(encoding="utf-8-sig")
        assert 文.index("TcpClient") < 文.index("$p = Start-Process"), \
            "没先问面板口有没有人应答就 Start-Process，等于把事故现场日志截断"
        assert "已在跑" in 文 or "已经有面板" in 文, "守卫分支要说清这次不起第二个"

    def test_ps1必须带BOM(self):
        """Windows PowerShell 5.1 读无 BOM 的 UTF-8 .ps1 会按 GBK 解，中文注释和字符串一起烂掉。"""
        assert (ROOT / "tools" / "start_panel.ps1").read_bytes().startswith(b"\xef\xbb\xbf")

    def test_壳必须把流程交给同一个python出口(self):
        """两端口径一份实现，否则 mac 能用、Windows 少一步。"""
        bat = (ROOT / "start.bat").read_text(encoding="ascii")
        sh = (ROOT / "start.sh").read_text(encoding="utf-8")
        assert "--launch" in bat and "--launch" in sh
        assert "PYTHONUTF8=1" in bat and "PYTHONUTF8=1" in sh

    def test_中文长选项必须有ASCII别名(self):
        """代码页没切对时 `--自检` 会变成乱码参数，面板就永远起不来。"""
        源 = 工具.read_text(encoding="utf-8")
        for 旗 in ('"--selfcheck"', '"--readonly"', '"--launch"'):
            assert 旗 in 源, "argparse 少了 ASCII 别名 %s" % 旗

    def test_别名门要会红(self):
        """门的反向变异：把 --launch 改掉，上一个门必须挑出来（不是恒绿）。"""
        源 = 工具.read_text(encoding="utf-8").replace('"--launch"', '"--别的"')
        assert '"--launch"' not in 源


class Test一键流程:
    def _面板目录(self, tmp_path):
        (tmp_path / "flask-version").mkdir()
        (tmp_path / "flask-version" / "app.py").write_text("", encoding="utf-8")
        return tmp_path

    def _装好(self, monkeypatch, mod, 面板应答, 收场序列=None, 缺料=()):
        """只替身四个出口：查缺料、读配置、探面板、起进程——其余走真实代码。"""
        记 = {"popen": [], "拉起来": [], "后台": 0, "探针": 0, "timeout": [], "terminate": 0}
        收场 = list(收场序列 if 收场序列 is not None else [0])

        class 子进程替身:
            pid = 4242

            def wait(self, timeout=None):
                记["timeout"].append(timeout)
                结局 = 收场.pop(0) if 收场 else 0
                if isinstance(结局, BaseException):
                    raise 结局
                return 结局

            def terminate(self):
                记["terminate"] += 1

        def 探(base, token=""):
            记["探针"] += 1
            return 面板应答[min(记["探针"] - 1, len(面板应答) - 1)]

        monkeypatch.setattr(mod, "自检", lambda root: list(缺料))
        monkeypatch.setattr(mod, "_读配置", lambda root: (["主账号", "账号2"], ""))
        monkeypatch.setattr(mod, "面板已在跑", 探)
        monkeypatch.setattr(mod, "拉起来",
                            lambda base, 数, token, 等秒: 记["拉起来"].append((base, 数)) or {})
        monkeypatch.setattr(mod, "汇报", lambda 名s, 数, 够: 0)
        monkeypatch.setattr(mod, "_后台拉号",
                            lambda *a, **k: 记.__setitem__("后台", 记["后台"] + 1))

        def 假Popen(*位, **键):
            记["popen"].append((位, 键))
            return 子进程替身()

        monkeypatch.setattr(mod.subprocess, "Popen", 假Popen)
        return 记

    def test_缺料时一个进程都不许起(self, tmp_path, monkeypatch, capsys):
        mod = _载具()
        目 = self._面板目录(tmp_path)
        记 = self._装好(monkeypatch, mod, [True], 缺料=("cloakbrowser/ 不在",))
        assert mod.一键(str(目), 假口, "", 30) == 1
        assert 记["popen"] == [] and 记["拉起来"] == []
        念 = capsys.readouterr().out
        assert "cloakbrowser" in 念 and "补齐" in 念, 念

    def test_面板已经在跑就只补号不动端口(self, tmp_path, monkeypatch, capsys):
        mod = _载具()
        目 = self._面板目录(tmp_path)
        记 = self._装好(monkeypatch, mod, [True])
        assert mod.一键(str(目), 假口, "", 30) == 0
        assert 记["popen"] == [], "面板在家还起第二个 = 每个号两条打招呼腿、两份记录"
        assert 记["拉起来"] == [(f"http://127.0.0.1:{假口}", 2)], 记["拉起来"]
        assert "不再起第二个" in capsys.readouterr().out

    def test_面板没跑时必须真的起一个(self, tmp_path, monkeypatch):
        """反向哨兵：没有这一条，"一键起面板"可以整个是空话。"""
        mod = _载具()
        目 = self._面板目录(tmp_path)
        记 = self._装好(monkeypatch, mod, [False], 收场序列=[0])
        assert mod.一键(str(目), 假口, "", 30) == 0
        ((位, 键),) = 记["popen"]
        assert 位[0] == [sys.executable, "app.py"], 位
        assert 键["cwd"] == str(目 / "flask-version"), \
            "必须在 flask-version 里跑，否则 app.py import 不到同目录的 access_guard"
        assert 记["后台"] == 1, "起了面板还得有人等它就绪去拉号，不然号照样停着"

    def test_让路那种退出码不许说成服务已停止(self, tmp_path, monkeypatch, capsys):
        """app.py 那句 sys.exit(1) 是故意的：第二个面板让路不能被读成"起好了"。"""
        mod = _载具()
        目 = self._面板目录(tmp_path)
        self._装好(monkeypatch, mod, [False, True], 收场序列=[1])
        assert mod.一键(str(目), 假口, "", 30) == 1
        念 = capsys.readouterr().out
        assert "让路" in 念 and "服务已停止" not in 念, 念

    def test_他按CtrlC之后收场算正常(self, tmp_path, monkeypatch, capsys):
        mod = _载具()
        目 = self._面板目录(tmp_path)
        self._装好(monkeypatch, mod, [False, False], 收场序列=[1])
        assert mod.一键(str(目), 假口, "", 30) == 0
        assert "服务已停止" in capsys.readouterr().out

    def test_CtrlC要先让面板自己收_超时才替它收尾(self, tmp_path, monkeypatch):
        mod = _载具()
        目 = self._面板目录(tmp_path)
        记 = self._装好(monkeypatch, mod, [False],
                       收场序列=[KeyboardInterrupt(), subprocess.TimeoutExpired("app.py", 10)])
        assert mod.一键(str(目), 假口, "", 30) == 0
        assert 记["timeout"] == [None, 10], 记["timeout"]
        assert 记["terminate"] == 1, "只许终止自己起的那一个 PID，不许按名字全杀"

    def test_退出码不是0或1要说成失败(self, tmp_path, monkeypatch, capsys):
        mod = _载具()
        目 = self._面板目录(tmp_path)
        self._装好(monkeypatch, mod, [False], 收场序列=[3])
        assert mod.一键(str(目), 假口, "", 30) == 1
        assert "退出码 3" in capsys.readouterr().out

    def test_没有面板入口就照实说结构不全(self, tmp_path, monkeypatch, capsys):
        mod = _载具()
        记 = self._装好(monkeypatch, mod, [False])   # 故意不建 flask-version
        assert mod.一键(str(tmp_path), 假口, "", 30) == 1
        assert 记["popen"] == []
        assert "项目结构" in capsys.readouterr().out


class Test起号那条腿:
    def test_已经在跑的号不重复发start(self, monkeypatch, capsys):
        mod = _载具()
        发过 = []

        def 假请求(base, 路径, 方法="GET", token="", 等秒=10):
            if 路径 == "/api/status":
                return {"accounts": [{"index": 0, "running": True, "name": "主账号"}]}
            发过.append(路径)
            return {"message": "ok"}

        monkeypatch.setattr(mod, "_请求", 假请求)
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        mod.拉起来(f"http://127.0.0.1:{假口}", 2, "", 0)
        assert 发过 == ["/api/accounts/1/start"], 发过
        assert "不重复发" in capsys.readouterr().out

    def test_只有start这一种写请求(self, monkeypatch):
        """一键不许顺手带上清空/删除类出口——那是另外三条要他点头的路。"""
        mod = _载具()
        发过 = []

        def 假请求(base, 路径, 方法="GET", token="", 等秒=10):
            发过.append((方法, 路径))
            if 路径 == "/api/status":
                return {"accounts": []}
            return {"message": "ok"}

        monkeypatch.setattr(mod, "_请求", 假请求)
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        mod.拉起来(f"http://127.0.0.1:{假口}", 2, "", 0)
        assert 发过 == [("GET", "/api/status"),
                        ("POST", "/api/accounts/0/start"),
                        ("POST", "/api/accounts/1/start")], 发过
        危险 = [路径 for _, 路径 in 发过
                if any(w in 路径 for w in ("clear", "delete", "stop", "logout", "cookies"))]
        assert not 危险, "一键里不许出现清空/删除/停止类出口: %r" % (危险,)

    def test_面板始终没应答要留一句没人应答(self, monkeypatch, capsys):
        mod = _载具()
        monkeypatch.setattr(mod, "等面板", lambda base, 等秒, token: False)

        def 不许发(*a, **k):
            raise AssertionError("没人应答就不该去发 start")

        monkeypatch.setattr(mod, "拉起来", 不许发)
        mod._后台拉号(f"http://127.0.0.1:{假口}", 2, "", 5)
        assert "没人应答" in capsys.readouterr().out


def test_启动器默认口跟着面板那一份走():
    """面板靠 unified_config 那一个默认口（BOSS_PANEL_PORT 可顶）；启动器要是还自己写死
    一个数，就会"面板听 5001、启动器一直等另一个口应答"——等 240 秒然后报面板没起来，
    看着像面板坏了。两处必须取同一份。"""
    import io
    import re
    源 = io.open("tools/start_accounts.py", encoding="utf-8").read()
    assert '--port", type=int, default=_面板端口())' in 源, \
        "启动器的默认端口没跟面板同源（现在它是自己算的）"
    assert "from boss_bot.unified_config import 面板端口" in 源, \
        "启动器引的不是那一份端口（`default=_面板端口()` 就成了无根的话）"
    写死 = [l.strip()[:70] for l in 源.splitlines() if re.search(r"default=\s*\d", l)]
    assert not 写死, "启动器里还有把数字写死的默认值：\n" + "\n".join(写死)
    # 注释里写"不许自己算 BOSS_PANEL_PORT"是讲道理，不算第二处读；只看真会跑的行
    代码 = "\n".join(l for l in 源.splitlines() if not l.strip().startswith("#"))
    assert "BOSS_PANEL_PORT" not in 代码, \
        "启动器自己再读一遍环境变量 = 第二份判据，换口那天就和面板对不上"
