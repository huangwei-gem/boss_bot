# -*- coding: utf-8 -*-
"""停掉发送的开关要留一行能查到是谁按的。

2026-10-08 13:15:36 两个号的回复被同一秒置成「人工接管」，`bot_state*.json` 里
只有时间没有来源，面板又没开 HTTP 访问日志——结果只能回一句"不是我点的"，
证明不了任何事。临时面板（5059 那条）和线上（5000）共用同一份状态文件，
一边点按钮两边都停，所以这一行必须带是哪端口、哪个进程按的。

这里按源码文本查，不 import app.py：那个模块一加载就会去碰真实配置与状态文件。
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "flask-version" / "app.py").read_text(encoding="utf-8")
TREE = ast.parse(APP)
FUNCS = {n.name: ast.get_source_segment(APP, n) or ""
         for n in TREE.body if isinstance(n, ast.FunctionDef)}


class 开关留痕Test:
    def test_四个开关都记账(self):
        for name in ("api_pause_reply", "api_resume_reply",
                     "api_account_pause_reply", "api_account_resume_reply"):
            assert "_audit_toggle" in FUNCS[name], name

    def test_账里带来源(self):
        源 = FUNCS["_audit_toggle"]
        assert "request.host" in 源, "只写'谁按的'不写哪端口，两个面板分不清"
        assert "os.getpid()" in 源
