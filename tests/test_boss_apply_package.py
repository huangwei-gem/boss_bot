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
