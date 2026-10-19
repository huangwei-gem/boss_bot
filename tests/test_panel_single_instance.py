# -*- coding: utf-8 -*-
"""面板只许有一个进程在跑。

2026-10-18 他原话「记得优化一下进程，要始终保持你的进程只有这一个，
保证不会他同时运行俩个程序导致内存占用提升」。

两个面板同时跑代价不是"多占点内存"这么简单：两边各建一套 MultiAccountManager，
每个号就有两条打招呼腿——同一个岗位被点两遍、记录写两份、两个浏览器树各 ~400 MB，
而界面上分不清哪条日志是谁说的。重启面板时最容易撞上一个还没死透的：
`taskkill` 只带走 python，浏览器树是独立进程，旧面板若还挂着，新面板就该拒绝起来。

判据写成 access_guard 里的纯函数（探测动作由调用方给），这样单测不用真占端口，
也能造出"有东西在听但不是面板"这一档——光"有人应答"就认领自己，等于把 5000
上别的 web 服务当成面板杀不掉。
"""

import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "flask-version"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from access_guard import 已有面板在跑

面板回音 = (200, json.dumps({"accounts": [], "running": False, "stats": {}}))


class Test认不认得出是面板:
    def test_面板自己应答就认领(self):
        在跑, 说明 = 已有面板在跑(lambda u: 面板回音)
        assert 在跑 is True
        assert "面板" in 说明 and "5000" in 说明

    def test_连不上就是没人跑(self):
        def 连不上(u):
            raise ConnectionError("拒绝连接")
        在跑, 说明 = 已有面板在跑(连不上)
        assert 在跑 is False and 说明 == ""

    def test_有东西在听但不是面板要说不清(self):
        """光"有人应答"就认领，会把 5000 上别的 web 服务当成面板而拒不下来。"""
        在跑, 说明 = 已有面板在跑(lambda u: (200, "<html><body>别的服务</body></html>"))
        assert 在跑 is False
        assert "占" in 说明, f"该说清端口被别的东西占了: {说明}"

    def test_回音里没有账号列表就不算面板(self):
        在跑, _ = 已有面板在跑(lambda u: (200, json.dumps({"foo": 1})))
        assert 在跑 is False

    def test_要凭据的面板也算面板(self):
        """带 token 的来源回 401，也是"面板在跑"——第二个副本照样不该起来。"""
        在跑, 说明 = 已有面板在跑(lambda u: (401, "认证失败"))
        assert 在跑 is True, 说明
        assert "401" in 说明

    def test_面板在跑时不能返回空说明(self):
        """exit 之前要打一句人话，否则日志里只有一条莫名其妙的退出。"""
        在跑, 说明 = 已有面板在跑(lambda u: 面板回音)
        assert 在跑 and 说明.strip()


class Test真接进了启动路径:
    def _源码(self):
        return io.open(os.path.join(ROOT, "flask-version", "app.py"),
                       encoding="utf-8").read()

    def test_main在起服务之前先问有没有第二个(self):
        src = self._源码()
        i = src.index("def main()")
        段 = src[i:src.index("socketio.run(app", i)]
        assert "_拒绝第二个面板()" in 段, "启动路径没接上这道门，测试就只是摆设"

    def test_端口不写死两处(self):
        """探测的口和真正监听的口必须同一个数：写死两遍就会有一天探 A 听 B。"""
        src = self._源码()
        assert '"port": 5000' not in src, "监听端口还写着字面量 5000"
        assert "面板端口" in src

    def test_退出用的是非零码(self):
        src = self._源码()
        i = src.index("def _拒绝第二个面板")
        段 = src[i:i + 1200]
        assert "sys.exit(1)" in 段, "退出码 0 会让 start.bat 以为正常起来了"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
