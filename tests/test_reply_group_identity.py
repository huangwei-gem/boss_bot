# -*- coding: utf-8 -*-
"""回复记录归到哪一路会话：原话比岗位名更硬。

2026-10-28 实测：蒋旭冉 挂着两家公司的会话（"阳春市随上优选农" 与
"兼职·居家电商客服70-100元/天南宁"），两路岗位名几乎一样，回复记录只带
姓名+岗位 → 按岗位挑中了不相干的那一路，点开只剩一段没有「拒绝交换联系方式」
角标的对话（真机上筛「拒绝交换」1 个会话、角标 0 个）。
回复正文在存档里只出现在其中一路，这是唯一一路的硬证据，必须优先于岗位名。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "flask-version"))

import app as 面板  # noqa: E402


class 假记录:
    def __init__(self, chat_name, job_name, reply_content="", account_index=0):
        self.chat_name = chat_name
        self.job_name = job_name
        self.reply_content = reply_content
        self.account_index = account_index


class 假消息库:
    def chat_id(self, name, company="", job_name=""):
        return f"{name}#{job_name or '拼接'}"


def 会话(chat_id, job_name, *texts):
    return {"chat_id": chat_id, "job_name": job_name,
            "messages": [{"content": t} for t in texts]}


class Test按原话归路:
    def test_原话只出现在一路时认那一路(self):
        甲 = 会话("蒋旭冉#阳春市随上优选农", "兼职·居家电商客服70-100元/天南宁",
                 "您好，看到您的招聘信息", "[已拒绝交换联系方式]")
        乙 = 会话("蒋旭冉#兼职_居家电商客服", "兼职·居家电商客服70-100元/天南宁查看职位",
                 "您好，看到您的招聘信息")
        记录 = 假记录("蒋旭冉", "兼职·居家电商客服70-100元/天南宁查看职位",
                      "[已拒绝交换联系方式]")
        索引 = {("蒋旭冉", 0): [乙, 甲]}   # 岗位名更贴合甲的那一路排在前面
        assert 面板._chat_id_for_record(索引, 假消息库(), 记录) == "蒋旭冉#阳春市随上优选农"

    def test_两路都有同一句时退回岗位名(self):
        甲 = 会话("A#1", "数据分析师", "抱歉这个岗位我不考虑了")
        乙 = 会话("A#2", "运营实习生", "抱歉这个岗位我不考虑了")
        记录 = 假记录("A", "运营实习生", "抱歉这个岗位我不考虑了")
        索引 = {("A", 0): [甲, 乙]}
        assert 面板._chat_id_for_record(索引, 假消息库(), 记录) == "A#2"

    def test_只有一路会话时不看原话(self):
        甲 = 会话("B#1", "别的岗位", "另一句话")
        记录 = 假记录("B", "岗位", "原话不在存档里")
        索引 = {("B", 0): [甲]}
        assert 面板._chat_id_for_record(索引, 假消息库(), 记录) == "B#1"

    def test_取不到原话时仍按岗位名(self):
        甲 = 会话("C#1", "数据分析师", "x")
        乙 = 会话("C#2", "运营实习生", "y")
        记录 = 假记录("C", "运营实习生")
        索引 = {("C", 0): [甲, 乙]}
        assert 面板._chat_id_for_record(索引, 假消息库(), 记录) == "C#2"

    def test_原话对不上任何一路时退回拼接身份(self):
        甲 = 会话("D#1", "数据分析师", "x")
        乙 = 会话("D#2", "运营实习生", "y")
        记录 = 假记录("D", "第三个岗位", "谁都没有的这句话")
        索引 = {("D", 0): [甲, 乙]}
        assert 面板._chat_id_for_record(索引, 假消息库(), 记录) == "D#第三个岗位"
