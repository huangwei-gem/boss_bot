# -*- coding: utf-8 -*-
"""面板图标与配色的回归闸门。

浏览器里改一次容易，下次改动悄悄退回去就看不出来了：这里把
「不依赖外网」「图标名都能解析」「两套主题文字都读得清」三件事钉住。
"""
import io
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "flask-version" / "templates" / "index.html"
SPRITE = ROOT / "flask-version" / "static" / "icons" / "ui-sprite.svg"
SHIM = ROOT / "flask-version" / "static" / "icons" / "icons.js"


def tpl():
    return io.open(str(TPL), encoding="utf-8").read()


def sprite():
    return io.open(str(SPRITE), encoding="utf-8").read()


def channel(hex_color):
    c = hex_color.lstrip("#")
    r, g, b = (int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda v: v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a, b):
    x, y = channel(a), channel(b)
    return (max(x, y) + 0.05) / (min(x, y) + 0.05)


def on_bg(value, bg):
    """rgba 半透明文字要按叠在主题底色上的实际颜色算，不然通过色会被高估。"""
    m = re.fullmatch(r"rgba?\(([^)]+)\)", value.strip())
    if not m:
        return value
    parts = [float(x) for x in m.group(1).replace("/", ",").split(",")]
    r, g, b, a = parts[0], parts[1], parts[2], (parts[3] if len(parts) > 3 else 1.0)
    base = bg.lstrip("#")
    br, bg_, bb = (int(base[i:i + 2], 16) for i in (0, 2, 4))
    mix = lambda c0, c1: int(round(a * c0 + (1 - a) * c1))
    return "#%02x%02x%02x" % (mix(r, br), mix(g, bg_), mix(b, bb))


def theme_tokens(theme_selector):
    """从模板里抠出某个主题的 token 表。"""
    start = tpl().index(theme_selector)
    block = tpl()[start:tpl().index("\n}", start)]
    tok = dict(re.findall(r"(--[a-z0-9-]+):\s*([^;]+);", block))
    assert tok, "找不到主题块 %s，token 结构变了" % theme_selector
    return tok


class TestNoExternalAssets:
    def test_面板不许挂外部资源(self):
        # 内网/断网/CDN 改版都会让面板缺图标缺字体，而且 @latest 这种版本号本身就在漂
        html = tpl()
        bad = re.findall(r'<script[^>]+src="(https?://[^"]+)"', html)
        bad += re.findall(r'<link[^>]+rel="(?:stylesheet|preconnect)"[^>]+href="(https?://[^"]+)"', html)
        bad += re.findall(r'<link[^>]+href="(https?://[^"]+)"[^>]+rel="stylesheet"', html)
        assert not bad, "模板里还有外部资源：%s" % bad[:3]

    def test_图标脚本和雪碧图都在本地(self):
        assert SHIM.exists() and SPRITE.exists()
        assert "KIcon" in io.open(str(SHIM), encoding="utf-8").read()


class TestIconContract:
    def test_用到的图标名雪碧图里必须有(self):
        used = set(re.findall(r'data-ki="([a-z0-9-]+)"', tpl()))
        have = set(re.findall(r'<symbol id="i-([a-z0-9-]+)"', sprite()))
        # JS 里拼接的名字（TOAST_ICONS 那类）单独核一遍
        assert used <= have, "雪碧图缺图标：%s" % sorted(used - have)
        assert used, "一个图标都没用上，说明替换被改坏了"

    def test_雪碧图合法且全部跟随主题色(self):
        root = ET.fromstring(sprite())
        symbols = root.findall(".//{*}symbol")
        assert len(symbols) > 40
        for s in symbols:
            blob = ET.tostring(s, encoding="unicode")
            assert "currentColor" in blob, "%s 没走 currentColor，暗色主题会瞎" % s.get("id")
            assert "width=" not in blob.split(">")[0], "%s 带死尺寸，CSS 控不住" % s.get("id")

    def test_折叠箭头用rotate而不是transform(self):
        # transform 加在 <svg> 上不生效，箭头会一直指着同一个方向
        assert ".adv-toggle.open .arrow{rotate:90deg}" in tpl()
        assert ".notif-center-header.open .arrow{rotate:90deg}" in tpl()


class TestThemeContrast:
    TEXT_TOKENS = ("--fg", "--fg-2nd", "--fg-3rd", "--fg-4th", "--side-label",
                   "--accent", "--success", "--warning", "--danger", "--info")

    @pytest.mark.parametrize("theme", ['[data-theme="light"], :root', '[data-theme="dark"]'])
    def test_正文级文字对比度达标(self, theme):
        tok = theme_tokens(theme)
        bg = tok["--bg"].strip()
        # 自检：这个判据必须能抓出改之前的值，否则测试是空的
        assert contrast("#8b90a3", "#eef1fb") < 4.5
        for name in self.TEXT_TOKENS:
            assert name in tok, "%s 少了 %s" % (theme, name)
            ratio = contrast(on_bg(tok[name], bg), bg)
            assert ratio >= 4.5, "%s %s=%s 对 %s 只有 %.2f:1（要 ≥4.5）" % (
                theme, name, tok[name].strip(), bg, ratio)
