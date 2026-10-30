# -*- coding: utf-8 -*-
"""公网面板隧道（tools/public_panel.py）的回归。

盯三件事：地址只从隧道自己的输出里取、命令只指向本机端口、断了要把地址撤下 ——
面板挂着 cookie 和启停接口，地址留在文件里却隧道已经没了，下次照着它点就是 404。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.public_panel import 挑出地址, 隧道命令, 写地址


class Test挑出地址:
    def test_从localhost_run横幅里取出公网地址(self):
        行 = ("f30d75d614a709.lhr.life tunneled with tls termination, "
              "https://f30d75d614a709.lhr.life")
        assert 挑出地址(行) == "https://f30d75d614a709.lhr.life"

    def test_从cloudflared的框线日志里取出公网地址(self):
        # cloudflared 把地址打在 ASCII 方框里，两侧有竖线和空格
        行 = "|  https://skipped-prior-surgeon-else.trycloudflare.com  |"
        assert 挑出地址(行) == "https://skipped-prior-surgeon-else.trycloudflare.com"

    def test_文档站和裸域名都不算地址(self):
        # localhost.run 的横幅里混着它自己的文档站；cloudflared 的提示行里什么都没有。
        # 把这些当成隧道地址写出去，给他的是个打不开的假链接。
        for 行 in ("More details at https://localhost.run/docs/",
                   "manage custom domains go to https://admin.localhost.run/",
                   "f30d75d614a709.lhr.life tunneled with tls termination"):
            assert 挑出地址(行) == ""


class Test隧道命令:
    # 假口用 18080，不用 500x：端口字面量在仓库里只许有 unified_config 那一份判据，
    # 测试再写一遍"看着像面板口"的数，换口那天就悄悄测了个不存在的目标。
    假口 = 18080

    def test_localhost_run只把本机端口的服务转出去(self):
        命令 = 隧道命令(self.假口, "localhost-run")
        assert 命令[0] == "ssh"
        i = 命令.index("-R")
        assert 命令[i + 1] == "80:localhost:%s" % self.假口
        assert 命令[-1] == "nokey@localhost.run"

    def test_cloudflared指向127_0_0_1而不是localhost(self):
        # 面板绑在 0.0.0.0，隧道只该连本机；Windows 上 localhost 有走代理被吃成 502 的先例
        命令 = 隧道命令(self.假口, "cloudflared")
        assert "--url" in 命令
        assert 命令[命令.index("--url") + 1] == "http://127.0.0.1:%s" % self.假口

    def test_默认就转现在那个面板口(self):
        """隧道要是默认转一个写死的旧口，他点开链接看到的是别的项目。"""
        from boss_bot.unified_config import 面板端口
        assert 隧道命令() == 隧道命令(面板端口(), "cloudflared")

    def test_不能卡在首次连接的确认提示上(self):
        # 无人值守的重连循环里弹一句 yes/no，隧道就永远起不来
        命令 = " ".join(隧道命令(self.假口, "localhost-run"))
        assert "StrictHostKeyChecking=no" in 命令
        assert "BatchMode=yes" in 命令


class Test写地址:
    def test_空地址等于把旧链接撤下(self, tmp_path):
        文件 = tmp_path / "public_url.txt"
        写地址(文件, "https://abc.lhr.life")
        assert 文件.read_text(encoding="utf-8").strip() == "https://abc.lhr.life"
        写地址(文件, "")
        assert not 文件.exists(), "隧道断了却留着旧地址，等于给人一个假链接"
