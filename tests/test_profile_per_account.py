# -*- coding: utf-8 -*-
"""求职意向按号各一份（用户 2026-11-03："每个好一份吧，然后你把现在有的分配一下"）。

改之前的形状：user_profile.json 里只有一条 position
「AI产品 / AI运营 / 数据分析师（长沙 · 全职）」，两个号共用。
于是一号做 AI 产品、二号做数据分析这两条方向，喂给 AI 的背景却是同一句合并写法——
对着数据分析的 HR 说"我也想投 AI 产品"，这正是 he 一直在问的"怎么老是被拒绝"里
能自己归因的那一半。

画像其余字段（学历/技能/经历）仍然全局一份：两个号是同一个人，
劈开等于对两个 HR 说互相矛盾的话（见 unified_config.ACCOUNT_GLOBAL_ONLY）。
"""
import json

import pytest

import boss_bot.prompts as P
from boss_bot.unified_config import UnifiedConfig


基准 = {
    "name": "黄维",
    "education": "本科在读（长沙理工大学 · 风景园林，2023-2027）",
    "position": "AI产品 / AI运营 / 数据分析师（长沙 · 全职）",
    "position_by_account": {
        "0": "AI产品经理 / AI运营（长沙 · 全职）",
        "1": "数据分析师（长沙 · 全职）",
    },
    "skills": ["Excel", "SQL"],
    "experience": "做过数据清洗与看板",
    "salary_expectation": "8-12K",
    "available_interview_time": "工作日下午",
    "contact": "",
    "highlights": [],
}


def _写在临时盘(tmp_path) -> str:
    文件 = tmp_path / "user_profile.json"
    文件.write_text(json.dumps(基准, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(文件)


def _加载(tmp_path) -> UnifiedConfig:
    """账号列表读仓库那份 bot_config（要 2 个号才测得出按号分配），画像读临时那份。"""
    return UnifiedConfig.load(profile_path=_写在临时盘(tmp_path),
                              overrides_path=str(tmp_path / "不存在.json"))


def _两份画像(tmp_path):
    cfg = _加载(tmp_path)
    assert len(cfg.greet.accounts) >= 2, "这份 bot_config 不足两个号，测不出按号分配"
    return cfg, [cfg.apply_account(i) for i in (0, 1)]


class Test按号各一份:
    def test_两个号拿到的是各自那句(self, tmp_path):
        cfg, 生效 = _两份画像(tmp_path)
        assert 生效[0].user_profile.position == \
            "AI产品经理 / AI运营（长沙 · 全职）"
        assert 生效[1].user_profile.position == "数据分析师（长沙 · 全职）"
        # 全局基准仍是那条合并写法（"全部账号"视图与没按号填的号都读它）
        assert cfg.user_profile.position == 基准["position"]

    def test_其余字段不跟着劈开(self, tmp_path):
        cfg, 生效 = _两份画像(tmp_path)
        for i in (0, 1):
            assert 生效[i].user_profile.education == cfg.user_profile.education
            assert 生效[i].user_profile.skills == cfg.user_profile.skills
            assert 生效[i].user_profile.salary_expectation == \
                cfg.user_profile.salary_expectation

    def test_没填那一号就回落全局(self, tmp_path):
        """只填了号0 的表，号1 不许读成空串——HR 问方向时机器不能没话。"""
        文件 = tmp_path / "user_profile.json"
        表 = dict(基准)
        表["position_by_account"] = {"0": "AI产品经理 / AI运营（长沙 · 全职）"}
        文件.write_text(json.dumps(表, ensure_ascii=False, indent=1), encoding="utf-8")
        cfg = UnifiedConfig.load(profile_path=str(文件),
                                 overrides_path=str(tmp_path / "不存在.json"))
        assert cfg.生效求职意向(1) == 基准["position"]
        assert cfg.生效求职意向(99) == 基准["position"], "越界账号也要有回落"

    def test_空表不报错(self, tmp_path):
        文件 = tmp_path / "user_profile.json"
        表 = dict(基准)
        表.pop("position_by_account")
        文件.write_text(json.dumps(表, ensure_ascii=False, indent=1), encoding="utf-8")
        cfg = UnifiedConfig.load(profile_path=str(文件),
                                 overrides_path=str(tmp_path / "不存在.json"))
        assert cfg.生效求职意向(0) == 基准["position"]


class Test提示词读这一号:
    def test_系统提示词里是这一号的方向(self, tmp_path, monkeypatch):
        文件 = tmp_path / "user_profile.json"
        文件.write_text(json.dumps(基准, ensure_ascii=False, indent=2), encoding="utf-8")
        monkeypatch.setattr(P, "USER_PROFILE_FILE", 文件)
        零 = P.build_system_prompt(account_index=0)
        壹 = P.build_system_prompt(account_index=1)
        assert "AI产品经理 / AI运营（长沙 · 全职）" in 零
        assert "AI产品经理" not in 壹, "二号还在报一号的方向"
        assert "数据分析师（长沙 · 全职）" in 壹
        # 不传就照旧读全局那句（现有调用点不该被这次改动打破）
        assert "AI产品 / AI运营 / 数据分析师" in P.build_system_prompt()

    def test_现读画像那条也认账号(self, tmp_path, monkeypatch):
        文件 = tmp_path / "user_profile.json"
        文件.write_text(json.dumps(基准, ensure_ascii=False, indent=2), encoding="utf-8")
        monkeypatch.setattr(P, "USER_PROFILE_FILE", 文件)
        assert P.current_profile(1)["position"] == "数据分析师（长沙 · 全职）"
        assert P.current_profile()["position"] == 基准["position"]
        # 返回的是新对象，改它不许把基准那份字典改了
        现 = P.current_profile(0)
        现["position"] = "被污染"
        assert P.current_profile(0)["position"] != "被污染"

    def test_换号后话术里的这个占位跟着走(self, tmp_path):
        """招呼语模板是加载时按画像渲染的，position 占位要用这一号那句。

        占位在第一次渲染就被吃掉了，所以按号那次必须拿原文重渲染——
        这条测的就是 templates_raw 有没有真在用。
        """
        文件 = tmp_path / "user_profile.json"
        表 = dict(基准)
        表["position"] = "数据分析"
        文件.write_text(json.dumps(表, ensure_ascii=False, indent=1), encoding="utf-8")
        cfg = UnifiedConfig.load(profile_path=str(文件),
                                 overrides_path=str(tmp_path / "不存在.json"))
        cfg.templates_raw["greeting_reply"] = "您好，我想找{position}的活儿。"
        cfg._render_templates()                       # 相当于加载时那一次渲染
        assert cfg.templates.greeting_reply == "您好，我想找数据分析的活儿。"
        一号 = cfg.apply_account(1)
        assert 一号.user_profile.position == "数据分析师（长沙 · 全职）"
        assert "数据分析师（长沙 · 全职）" in 一号.templates.greeting_reply, \
            "模板还是按全局那句渲染的，这一号发出去的方向和界面对不上"


class Test仓库里那份配置:
    def test_真实配置文件两号都有方向(self):
        """这条测的是盘上那份，不是临时造的样例：改了数据没配表，界面就还是同一句。"""
        盘 = json.loads(open("user_profile.json", encoding="utf-8").read())
        表 = 盘.get("position_by_account") or {}
        assert str(表.get("0") or "").strip(), "号0 的求职意向没填"
        assert str(表.get("1") or "").strip(), "号1 的求职意向没填"
        assert 表["0"] != 表["1"], "两份一模一样，等于没分配"

    def test_两号方向各自纯粹(self):
        """口径（2026-11-02）：「只保留纯粹的一个是数据分析，一个是AI产品或者AI运营」。"""
        盘 = json.loads(open("user_profile.json", encoding="utf-8").read())
        表 = 盘.get("position_by_account") or {}
        assert "数据分析" not in 表["0"], "号0 还挂着数据分析那条"
        assert "AI产品" not in 表["1"] and "AI运营" not in 表["1"], "号1 还挂着 AI 那条"
