# -*- coding: utf-8 -*-
"""HR 用斜杠隔字（"家/教""老师/助教"）绕过标题判据，这条不该放过。

2026-10-08 18:1x 扫会话存档时发现：「兼职·线上一对一家/教50-200元/时成都」这条
判据返回空——正常写「家教」是能命中的，中间插一个斜杠就看不见，于是它连补拒名单都没进。
BOSS 上这种写法是招聘方规避平台关键词审查的常规手法（同类的还有插不可见字符，
那种 `_tight` 已经处理了）。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.intent import _tight, title_veto_hit, veto_hit_anywhere  # noqa: E402
from boss_bot.unified_config import TITLE_VETO_KEYWORDS_DEFAULT, UnifiedConfig  # noqa: E402


class 斜杠隔字Test:
    @pytest.mark.parametrize("标题,词", [
        ("兼职·线上一对一家/教50-200元/时长沙查看职位", "家教"),
        ("兼职·英语老师/助教线上兼职40-60元/时", "老师"),
        ("兼职·日结/无需露脸/小白既可/线上居家120-150元/时", "露脸"),
        ("兼职·数据/策略运营50-60元/天泉州查看职位", ""),
    ])
    def test_抹掉斜杠后该命中的还得命中(self, 标题, 词):
        assert title_veto_hit(TITLE_VETO_KEYWORDS_DEFAULT, 标题) == 词, 标题

    def test_抹斜杠不改变否定式判断(self):
        """「不坐班/可远程」抹完还是"不坐班"挨着，不该被当成坐班岗。"""
        assert _tight("不坐班/可远程") == "不坐班可远程"

    def test_斜杠不误伤要的方向(self):
        """数据/分析/标注 这类标题里的斜杠只是并列，抹完仍是我们要的岗。"""
        词表 = list(UnifiedConfig.load().ai.custom_filter_keywords or [])
        标题 = "兼职·数据采集/标注专员（线上办公）150-250元/天"
        assert veto_hit_anywhere(TITLE_VETO_KEYWORDS_DEFAULT, 词表,
                                 title=标题, text="负责图片、语音数据的清洗与标注") == ""
