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


def test_失败码文档与state的枚举完全一致():
    """文档里有代码没的码 → agent 记进去没人认得；代码有文档没的码 → 使用者查不到处置。"""
    doc = (PKG / "references" / "failure-codes.md").read_text(encoding="utf-8")
    doc_codes = set(re.findall(r"^\|\s*`([a-z_]+)`", doc, flags=re.M))
    src = (PKG / "scripts" / "state.py").read_text(encoding="utf-8")
    m = re.search(r"FAILURE_CODES = \(([^)]*)\)", src, flags=re.M)
    assert m, "state.py 里没声明 FAILURE_CODES 枚举"
    codes = {x.value for x in ast.parse(f"X=({m.group(1)})").body[0].value.elts}
    assert codes, "FAILURE_CODES 是空的"
    assert doc_codes == codes, f"文档多集 {doc_codes - codes} / 代码多集 {codes - doc_codes}"
    for code in sorted(codes):
        rows = [l for l in doc.splitlines() if l.strip().startswith(f"| `{code}`")]
        assert len(rows) == 1, f"{code} 在表里出现 {len(rows)} 次"
        cells = [c.strip() for c in rows[0].strip().strip("|").split("|")]
        assert len(cells) == 3 and all(len(c) > 3 for c in cells), \
            f"{code} 那一行三格没写满：{cells}"
