# -*- coding: utf-8 -*-
"""异常文本为空的那一类日志不许留下一句没有原因的话。

DrissionPage 与 CDP 抛出来的异常有一族 `str(e) == ""`：日志写成 `f"...: {e}"`
就留下一条「解析卡片 3 失败: 」，10-24 半小时念了 52 条、10-23 一天念了
312 条「侧栏跳到 4900px 失败」与 370 条「获取全部会话列表失败」，
全是这种没有下文的红字——看着像页面坏了，其实连"是断开还是 JS 报错"都分不出来。
"""
import logging
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class _空消息异常(Exception):
    def __str__(self):          # 真实现场：str() 是空串
        return ""


class _页:
    def run_js(self, *a, **k):
        raise _空消息异常()


def test_侧栏跳行失败要带得出是哪种异常(caplog):
    from boss_bot.page_handler import BossChatHandler

    句柄 = BossChatHandler.__new__(BossChatHandler)
    句柄.page = _页()
    with caplog.at_level(logging.DEBUG):
        句柄._跳到(4900)
    行 = [r.getMessage() for r in caplog.records if "侧栏跳到" in r.getMessage()]
    assert 行, "跳行失败一个字都没记"
    assert "空消息异常" in 行[0], 行[0]
    assert not 行[0].rstrip().endswith("失败: "), 行[0]


def _扫(文件, 句子们):
    源 = (ROOT / 文件).read_text(encoding="utf-8")
    坏 = []
    for 句 in 句子们:
        for m in re.finditer(re.escape(句), 源):
            尾 = 源[m.start():m.start() + 200]
            if "type(e)" not in 尾:
                坏.append(句)
                break
    return 坏


def test_热路径上那三句空原因日志都改了写法():
    """这条是防"改回来"的：不是行为门，是写法门——这三处每一轮都要念，
    少一处就又回到"日志里只有失败两个字"。"""
    坏 = _扫("boss_bot/greet_engine.py", ['"解析卡片'])
    坏 += _扫("boss_bot/page_handler.py", ['"获取全部会话列表失败'])
    坏 += _扫("boss_bot/main_loop.py", ['"访问会话页异常'])
    assert not 坏, "这几处又回到只打 {e} 了：%s" % 坏
