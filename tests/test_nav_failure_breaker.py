# -*- coding: utf-8 -*-
"""稳定性那一路：「页面加载失败」是整条投递腿一起坏，不是一个岗位抽风。

判据来自全量记录的形状（tools/audit_nav_failure_shape.py 现算）：
492 条页面加载失败只聚成 8 波，最大一波 10-10 10:56→11:15 用 19 分钟把 150 条
失败摊到 50 个岗位 URL 上，这段时间一单都没投进去。既然坏的是腿，
按岗位 URL 放一放（抽屉那道门的形式）拦不住它——每个 URL 都才第一次撞；
只有账号层面的时间窗熔断能在第 5 次就收手，而不是烧完 150 个再说。

同时这条提示不许狼来了：窗口里明明投进去过单（10-09 22:52 那波 20 条失败夹着
7 单成功），就不能说人家额度用完，那是页面正常在抖。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from boss_bot.greet_engine import (NAV_FAIL_WINDOW_LIMIT, NAV_FAIL_WINDOW_SEC,
                                   GreetEngine)


def 引擎():
    e = GreetEngine.__new__(GreetEngine)
    e._no_drawer_recent = []
    e._applied_recent = []
    e._nav_fail_recent = []
    e._greet_cooldown_until = 0.0
    e._greet_cooldown_reason = ""
    e._log = lambda *a, **k: None
    return e


class 熔断那一条Test:
    def test_窗口内够数且没投进过单就冷却整条腿(self):
        e = 引擎()
        now = time.time()
        reason = ""
        for i in range(NAV_FAIL_WINDOW_LIMIT):
            reason = e._note_nav_fail(now=now + i * 20)
        assert reason, "5 次导航失败必须熔断"
        assert e.greet_cooldown_left(now=now + 60) > 0
        assert "页面加载失败" in e._greet_cooldown_reason
        # 这条不是额度：写成"疑似当日沟通额度用完"会把他支去等额度，
        # 真该做的是别在这一刻反复重启浏览器
        assert "额度" not in e._greet_cooldown_reason

    def test_差一次不熔断(self):
        e = 引擎()
        now = time.time()
        for i in range(NAV_FAIL_WINDOW_LIMIT - 1):
            assert e._note_nav_fail(now=now + i * 20) == ""
        assert e.greet_cooldown_left(now=now) == 0.0

    def test_摊在几小时里不算一波(self):
        """一天里零星几个岗位打不开是常态，熔断只认扎堆。"""
        e = 引擎()
        now = time.time()
        隔 = NAV_FAIL_WINDOW_SEC + 60
        for i in range(NAV_FAIL_WINDOW_LIMIT + 2):
            assert e._note_nav_fail(now=now + i * 隔) == ""
        assert e.greet_cooldown_left(now=now) == 0.0

    def test_这段时间投进过单就不熔断(self):
        """腿还在出货的时候不能定罪——10-09 22:52 那波 20 条失败夹着 7 单成功。"""
        e = 引擎()
        now = time.time()
        e._note_applied(now=now)
        for i in range(NAV_FAIL_WINDOW_LIMIT):
            assert e._note_nav_fail(now=now + 10 + i * 20) == "", "窗口里有成功就不该熔断"
        assert e.greet_cooldown_left(now=now + 200) == 0.0

    def test_冷却会自己醒(self):
        """机器暂停必须会自己醒——这条挂上去之后不能等人来点恢复。"""
        from boss_bot.greet_engine import NAV_FAIL_COOLDOWN_SEC
        e = 引擎()
        now = time.time()
        for i in range(NAV_FAIL_WINDOW_LIMIT):
            e._note_nav_fail(now=now)
        assert e.greet_cooldown_left(now=now) > 0
        assert e.greet_cooldown_left(now=now + NAV_FAIL_COOLDOWN_SEC + 1) == 0.0


class 冷却理由要说真话Test:
    def test_取理由函数没冷却时给空(self):
        e = 引擎()
        assert e.greet_cooldown_reason() == ""

    def test_冷却中的理由就是挂上时那句(self):
        e = 引擎()
        for _ in range(NAV_FAIL_WINDOW_LIMIT):
            e._note_nav_fail(now=time.time())
        assert e.greet_cooldown_reason() == e._greet_cooldown_reason
