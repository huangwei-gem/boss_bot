# -*- coding: utf-8 -*-
"""面板模板里的内联 JS 必须能过语法检查。

起因（2026-10-28）：给打招呼表加城市列时，其中一行少写了一个收尾引号。
按函数抽出来跑的那几条测试照样绿——它们只看得见自己抽走的那一段；
真机 e2e 则表现成「typeof allGreetRecords === undefined」，看着像面板没加载。
整个内联脚本只要错一个字符，浏览器一条语句都不执行：HTML 还在、点什么都没反应。
"""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[1] / "flask-version" / "templates"


def _内联脚本(html: str):
    for attr, body in re.findall(
            r"<script(?P<attr>[^>]*)>(?P<body>.*?)</script>", html, re.S):
        if "src=" in attr or not body.strip():
            continue
        yield body


def _语法检查(body: str):
    路径 = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(body)
            路径 = f.name
        r = subprocess.run(["node", "--check", 路径], capture_output=True)
        return r.returncode, r.stderr.decode("utf-8", "replace")
    finally:
        if 路径:
            Path(路径).unlink(missing_ok=True)


def test_坏脚本要判红():
    """门自己先证明会失败：一个没闭合的字符串必须在这里红"""
    if shutil.which("node") is None:
        pytest.skip("需要 node 才能做语法检查")
    rc, err = _语法检查("var a = '<td>';\nvar b = a;\n'<td>'+x+'</td>\n")
    assert rc != 0 and "SyntaxError" in err, (rc, err[:400])


@pytest.mark.parametrize("模板", sorted(p.name for p in TEMPLATES.glob("*.html")))
def test_内联脚本能过语法检查(模板):
    if shutil.which("node") is None:
        pytest.skip("需要 node 才能做语法检查")
    块 = list(_内联脚本((TEMPLATES / 模板).read_text(encoding="utf-8")))
    if not 块:
        pytest.skip("%s 没有内联脚本" % 模板)
    for i, body in enumerate(块):
        rc, err = _语法检查(body)
        assert rc == 0, "%s 第 %d 段内联脚本语法有错：\n%s" % (模板, i, err[:1200])
