#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成面板用的图标雪碧图 flask-version/static/icons/ui-sprite.svg。

图标取自 Koboyo Icons（原样单文件放进我们的 UI，许可允许；不要把整库或索引提交进仓库）。
每个图标包成 <symbol id="i-{名}">，页面里用 <svg class="ki"><use href="/static/icons/ui-sprite.svg#i-play"/></svg>
引用；图标本体一律 currentColor，所以颜色永远跟着按钮/文字走，暗色模式不用另存一套。
"""

import io
import os
import re
import sys
import urllib.request

BASE = "https://koboyo.com/icons/svg/%s.svg"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"

# 面板语义 -> Koboyo 文件名（Original 画风，一套用到底，不混风格）
ICONS = {
    # 顶栏 / 侧栏分组
    "account": "user",
    "settings": "settings",
    "ai": "robot-face",
    "jobs": "list-ordered",
    "preview": "eye",
    "bell": "bell",
    "log": "terminal",
    "moon": "moon",
    "sun": "sun",
    "link": "link",
    "chevron": "chevron-down",
    "cookie": "cookie",
    "chevron-open": "chevron-down-opening-row",
    # 四个漏斗指标
    "sent-total": "paper-plane",
    "sent-today": "calendar",
    "resume-in": "recruiter-cv",
    "interview": "handshake",
    # 运行控制
    "start": "play",
    "stop": "square-stop",
    "save": "save",
    "export": "table",
    "greet": "hand-waving",
    "reply": "speech-bubble",
    "download": "download",
    "history": "history-clock-arrow",
    # 侧栏动作
    "add": "plus",
    "plus": "plus",
    "zap": "zap",
    "delete": "trash",
    "upload": "upload",
    "regenerate": "pencil-sparkles",
    "healthcheck": "stethoscope",
    "edit": "pencil",
    "edit-resume": "file-user",
    "edit-prompt": "wand-sparkles",
    "edit-rules": "shield",
    "edit-template": "message-square",
    "test-ai": "beaker",
    "evolve": "dna-double-helix",
    "advanced": "set-sliders",
    # 记录区
    "filter": "filter",
    "reset": "refresh-circular-arrow",
    "clear": "eraser",
    "copy": "copy",
    "probe": "search",
    "gauge": "gauge",
    "warn": "warning",
    "power": "power",
    "timer": "timer",
    "inbox": "inbox",
    "archive": "archive",
    "key": "key",
    "volume": "volume",
    "mute": "mute-speaker",
    "check": "check",
    "cross": "close-cross",
    "sparkle": "sparkle",
    "shield": "shield",
    "clock": "countdown-timer",
    "trophy": "trophy",
    # 空状态
    "empty-greet": "empty-state-for-activity",
    "empty-reply": "empty-state-for-chat",
    "empty-log": "hourglass",
    "empty-chat": "empty-state-for-chat",
    # 面板里原本用 Lucide 名字的那批：换成同语义的 Koboyo 图标，标记不用重写两遍
    "alert-triangle": "warning",
    "check-circle": "circle-check",
    "chevron-down": "chevron-down",
    "chevron-right": "chevron-right",
    "clipboard-list": "clipboard-list",
    "clock": "clock",
    "file-text": "file-text",
    "info": "info",
    "lightbulb": "lightbulb",
    "loader": "loader-circle",
    "menu": "menu",
    "message-circle": "message-circle",
    "wifi-off": "wifi-off",
    "x-circle": "circle-cross-for-failed",
}

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "flask-version", "static", "icons", "ui-sprite.svg")


def fetch(name):
    req = urllib.request.Request(BASE % name, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def to_symbol(icon_id, source):
    """把独立 SVG 拆成 <symbol>：根标签上的 fill/stroke/viewBox 搬到 symbol 上。"""
    root = re.search(r"<svg\b[^>]*>", source)
    head = root.group(0)
    attrs = {}
    for k in ("fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "viewBox"):
        found = re.search(r'\b%s="([^"]*)"' % k, head)
        if found:
            attrs[k] = found.group(1)
    inner = source[root.end(): source.rfind("</svg>")].strip()
    # 注释和 aria-label 对 <use> 里的图标没意义，去掉能省下几十到几百字节
    inner = re.sub(r"<!--.*?-->", "", inner, flags=re.S)
    inner = re.sub(r'\s(?:aria-label|role|title)="[^"]*"', "", inner)
    viewBox = attrs.pop("viewBox", "0 0 24 24")
    rest = "".join(' %s="%s"' % kv for kv in attrs.items())
    return '  <symbol id="i-%s" viewBox="%s"%s>\n    %s\n  </symbol>' % (
        icon_id, viewBox, rest, inner)


def build():
    symbols, missing = [], []
    cache_dir = os.path.join(os.path.dirname(OUT), "src")
    os.makedirs(cache_dir, exist_ok=True)
    for icon_id, name in sorted(ICONS.items()):
        path = os.path.join(cache_dir, name + ".svg")
        if os.path.exists(path):
            source = io.open(path, encoding="utf-8").read()
        else:
            try:
                source = fetch(name)
            except Exception as e:
                missing.append("%s->%s (%s)" % (icon_id, name, e))
                continue
            with io.open(path, "w", encoding="utf-8") as f:
                f.write(source)
        symbols.append(to_symbol(icon_id, source))
    text = ('<svg xmlns="http://www.w3.org/2000/svg" style="display:none">\n'
            '  <!-- 由 tools/build_ui_icons.py 从 koboyo.com/icons 生成，画风 Original，'
            '图标一律 currentColor -->\n'
            + "\n".join(symbols) + "\n</svg>\n")
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write(text)
    print("写入 %s：%d 个图标，%d KB" % (OUT, len(symbols), len(text) // 1024))
    if missing:
        print("缺失：\n  " + "\n  ".join(missing))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(build())
