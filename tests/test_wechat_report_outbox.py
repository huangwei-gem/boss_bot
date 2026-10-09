# -*- coding: utf-8 -*-
"""该汇报的事要能被打成微信文本，而且同一件事只报一次。

用户 2026-10-08：「以后有面试邀请，或者给了什么联系方式都给我汇报一声，
汇报的内容包括 JD、姓名、岗位、公司等基本信息，以及末尾一句 AI 总结。」
这条一直没做，是因为通道没定：微信 PC 客户端没有对外接口，能稳定驱动它的
是 Qoder 的桌面自动化（computer-use）。所以拆成两半——
机器人这边只负责判事并产出文本，发送由 Qoder 侧贴进微信后回来 --mark。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.report_outbox import (format_report, new_reports,  # noqa: E402
                                    scan_interviews)

HR = {"is_mine": False, "isFriend": True, "text": "", "sender": "hr"}


def 会话(name="王女士", company="某数据科技", job="兼职·数据标注20-30元/时长沙查看职位",
         msgs=(), updated="2026-10-08 20:00:00", account=0):
    return {"account_index": account, "chat_name": name, "company": company,
            "job_name": job, "updated_at": updated, "messages": list(msgs)}


class 联系方式Test:
    def test_HR给了微信号要报一条(self):
        档 = [会话(msgs=[{"is_mine": False, "isFriend": True,
                          "text": "加我微信 abc_123，通过一下"}])]
        报, _键 = new_reports(档, [], [], seen=[])
        assert len(报) == 1 and 报[0]["event"] == "联系方式", 报
        assert "abc_123" in 报[0]["contacts"]

    def test_同一件事第二次不再报(self):
        档 = [会话(msgs=[{"is_mine": False, "isFriend": True, "text": "电话 13800001234"}])]
        报, 键 = new_reports(档, [], [], seen=[])
        assert len(报) == 1
        再报, _ = new_reports(档, [], [], seen=键)
        assert 再报 == [], "重复汇报就是骚扰他自己"

    def test_先给微信再给电话算两件事(self):
        第一条 = [会话(msgs=[{"is_mine": False, "isFriend": True, "text": "微信 abc_123"}])]
        报, 键 = new_reports(第一条, [], [], seen=[])
        第二条 = [会话(msgs=[{"is_mine": False, "isFriend": True, "text": "加我微信 abc_123"},
                             {"is_mine": False, "isFriend": True, "text": "电话 13800001234"}])]
        再报, _ = new_reports(第二条, [], [], seen=键)
        assert len(再报) == 1 and "13800001234" in 再报[0]["contacts"], 再报


class 面试邀请Test:
    def test_线上面试要报(self):
        档 = [会话(msgs=[{"is_mine": False, "isFriend": True,
                          "text": "安排个视频面试可以吗？"}])]
        报, _ = new_reports(档, [], scan_interviews(档), seen=[])
        assert any(r["event"] == "面试邀请" and r["kind"] == "online" for r in 报), 报

    def test_线下面试要报并且标出来(self):
        档 = [会话(msgs=[{"is_mine": False, "isFriend": True,
                          "text": "面试地点：岳麓区某某大厦13楼，明天上午十点现场面试"}])]
        报, _ = new_reports(档, [], scan_interviews(档), seen=[])
        面 = [r for r in 报 if r["event"] == "面试邀请"]
        assert len(面) == 1 and 面[0]["kind"] == "offline"

    def test_说不清的邀请不报(self):
        """只写"邀请您面试"——报错了是他自己的面试，不猜"""
        档 = [会话(msgs=[{"is_mine": False, "isFriend": True, "text": "邀请您面试，前往查看"}])]
        报, _ = new_reports(档, [], scan_interviews(档), seen=[])
        assert not [r for r in 报 if r["event"] == "面试邀请"], 报


class 文本内容Test:
    def test_汇报里要有姓名公司岗位联系方式(self):
        档 = [会话(msgs=[{"is_mine": False, "isFriend": True, "text": "加我微信 abc_123"}])]
        报, _ = new_reports(档, [], [], seen=[])
        文 = format_report(报[0])
        for 项 in ("王女士", "某数据科技", "数据标注", "abc_123"):
            assert 项 in 文, 文

    def test_不许出现内部键名(self):
        档 = [会话(msgs=[{"is_mine": False, "isFriend": True, "text": "加我微信 abc_123"}])]
        报, _ = new_reports(档, [], [], seen=[])
        文 = format_report(报[0])
        for 漏 in ("account_index", "chat_name", "hr_last_message", "None"):
            assert 漏 not in 文, 文
