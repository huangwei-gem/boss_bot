# -*- coding: utf-8 -*-
"""包结构、frontmatter、文档同源性 —— skill 能不能被别人导入，全看这些。"""
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "boss-apply"


def test_example配置能解析且字段齐全():
    data = json.loads((PKG / "assets" / "rules.example.json").read_text(encoding="utf-8"))
    for key in ("cities", "keywords", "match_threshold", "greeting",
                "interval_seconds", "round_cap"):
        assert key in data, key
    assert data["round_cap"] <= 50, "example 自己就该在硬上限内，别教坏使用者"
    assert data["interval_seconds"][0] >= 5, "间隔低于 5 秒就是往风控上撞"


def test_招呼语example不含占位残留():
    text = (PKG / "assets" / "greeting.example.txt").read_text(encoding="utf-8")
    assert text.strip() and "TODO" not in text.upper()


def test_dom文档里每个选择器都能在生产实现里找到出处():
    """skill 里的 DOM 事实必须与本仓库真机核过的生产代码同源。

    比两个文件：打招呼/输入框/发送按钮的事实来自 greet_engine.py，会话条目
    `.friend-content` 只存在于 page_handler.py（回复侧），少一个就会误报。
    生产改了选择器而文档没跟着改时，测试就红 —— 以生产代码为准改文档。
    """
    doc = (PKG / "references" / "boss-dom.md").read_text(encoding="utf-8")
    src = "\n".join((ROOT / "boss_bot" / name).read_text(encoding="utf-8")
                    for name in ("greet_engine.py", "page_handler.py"))
    listed = [sel for line in doc.splitlines()
              if line.lstrip().startswith("- 选择器 ")
              for sel in re.findall(r"`([^`]+)`", line)]
    assert len(listed) >= 12, f"文档没按约定列选择器，只找到 {len(listed)} 条"
    for sel in listed:
        assert sel in src, f"boss-dom.md 写了 {sel}，生产代码里已经没有它了"
