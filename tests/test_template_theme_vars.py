# -*- coding: utf-8 -*-
"""模板里不许引用没定义的主题变量。

引用一个不存在的变量不会报错，只会静默继承父级样式——实测有两处这样哑过：
无头运行态那句"与启动记录不符"本该是橙色，却写成 `var(--warn)`（令牌表里只有
`--warning`），置顶标记第一版也抄了同一个错。界面看上去"改了、在跑"，
其实颜色和正文一模一样，谁都看不出哪一条是异常。
"""
import re
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / "flask-version" / "templates" / "index.html"


def test_用到的主题变量都必须定义过():
    src = TEMPLATE.read_text(encoding="utf-8")
    defined = set(re.findall(r"(--[a-zA-Z0-9_-]+)\s*:", src))
    used = set(re.findall(r"var\(\s*(--[a-zA-Z0-9_-]+)", src))
    missing = sorted(used - defined)
    assert not missing, f"这些 var() 引用没有对应定义，会静默继承父级样式：{missing}"
