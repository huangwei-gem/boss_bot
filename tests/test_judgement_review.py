# -*- coding: utf-8 -*-
"""判分复盘的聚合与建议（追问原因 → 可采纳的建议）。

用户口径（2026-09-29）："不符合的原因可以用于 AI 的自进化"，并且选了
"只出建议，人工点采纳才写配置"。所以这里产出的每一条建议都必须带
auto_applied:false —— 和自进化引擎原本的契约一致：只记录与评估，
不替用户改写他写的话术和规则。见 memory 里的 ai-chain-contract。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.judgement_review import apply_suggestion, build_review  # noqa: E402
from tests.test_config_hot_reload import flask_app  # noqa: F401,E402  跨文件复用夹具


def rec(score=63, blocker="要求 3 年以上 SQL 取数经验", evidence="简历没写 SQL 项目",
        fixable=True, account=0, when=None, probed=True):
    """造一条"AI 判不匹配 + 有追问结果"的记录（dict 形态，贴近落库后的样子）。"""
    r = {"ai_score": score, "ai_is_match": False, "account_index": account,
         "timestamp": (when or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"),
         "job_name": "数据分析师", "company": "某公司"}
    if probed:
        r["ai_probe"] = {"blocking_requirement": blocker, "evidence_missing": evidence,
                         "fixable_by_resume": fixable, "score_if_fixed": 78}
    return r


class BuildReviewTest:
    def test_只统计有追问结果的不匹配(self):
        got = build_review([rec(), rec(probed=False),
                            {"ai_score": 88, "ai_is_match": True, "account_index": 0,
                             "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}])
        assert got["probed"] == 1, got

    def test_按账号过滤不串号(self):
        """记录是共表存的，靠 account_index 分家；主账号 0 不能被账号2 的原因带偏。"""
        got = build_review([rec(account=0), rec(account=1, blocker="要能接受驻场外派")],
                           account_index=0)
        assert got["probed"] == 1
        assert [t["requirement"] for t in got["top_blockers"]] == ["要求 3 年以上 SQL 取数经验"]

    def test_同一硬性要求归并计数并排序(self):
        rows = [rec(blocker="要求 3 年以上 SQL 取数经验"),
                rec(blocker="要求 3 年以上 SQL 取数经验"),
                rec(blocker="需要硕士学历")]
        got = build_review(rows)
        assert got["top_blockers"][0]["requirement"] == "要求 3 年以上 SQL 取数经验"
        assert got["top_blockers"][0]["count"] == 2
        assert got["top_blockers"][0]["score_max"] == 63

    def test_超出天数窗口的不算进来(self):
        old = rec(when=datetime.now() - timedelta(days=30))
        got = build_review([old, rec()], days=7)
        assert got["probed"] == 1

    def test_能改简历的条数单独列(self):
        """这批的结论是"该改简历"，和"岗位确实不合适"混在一起会把建议方向带偏。"""
        got = build_review([rec(fixable=True), rec(fixable=False, blocker="要应届生身份除外"),
                            rec(fixable=True)])
        assert got["fixable_by_resume"] == 2
        assert got["not_fixable"] == 1

    def test_建议一律不带自动生效(self):
        got = build_review([rec(), rec()])
        assert got["suggestions"], "两条同类原因就该出建议"
        for s in got["suggestions"]:
            assert s["auto_applied"] is False, s

    def test_今日条数单独给(self):
        """AI 卡片上那句「今日追问 N 条」要的是今天，不是 7 天窗口。"""
        got = build_review([rec(), rec(when=datetime.now() - timedelta(days=3))])
        assert got["probed"] == 2
        assert got["today_probed"] == 1

    def test_模型说匹配但分数没到阈值的也要算进来(self):
        """2026-09-30 真机跑出来的一条：模型回 is_match=true + score=65，
        判定按 score>=阈值 走的是不匹配分支，记录里存的却是模型原话。
        复盘要是按 ai_is_match 过滤，这种"分数不够被拦下"的正好被漏掉。"""
        rows = [{"ai_score": 65, "ai_is_match": True, "account_index": 0,
                 "skip_reason": "AI判定不匹配: 经验缺口",
                 "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 "ai_probe": {"blocking_requirement": "供应链领域经验",
                              "evidence_missing": "没写库存/物流项目",
                              "fixable_by_resume": False, "score_if_fixed": 70}}]
        got = build_review(rows)
        assert got["probed"] == 1, got
        assert got["top_blockers"][0]["requirement"] == "供应链领域经验"

    def test_没有真判过AI的记录不算(self):
        rows = [rec(), {"ai_score": 50, "ai_is_match": True, "ai_error": True,
                        "account_index": 0,
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "ai_probe": {"blocking_requirement": "接口挂了也留了追问"}}]
        assert build_review(rows)["probed"] == 1


class SuggestionTest:
    def test_反复出现的硬性要求出成打分规则建议(self):
        rows = [rec(blocker="要求 3 年以上 SQL 取数经验") for _ in range(3)]
        got = build_review(rows)
        kinds = {s["kind"] for s in got["suggestions"]}
        assert "add_scoring_rule" in kinds, kinds
        rule = next(s for s in got["suggestions"] if s["kind"] == "add_scoring_rule")
        assert rule["target"] == "ai.custom_scoring_prompt"
        assert "SQL" in rule["value"]

    def test_说得出缺哪条证据的出成简历补充建议(self):
        got = build_review([rec(evidence="没写 SQL 项目经历"), rec(evidence="没写 SQL 项目经历")])
        s = next((x for x in got["suggestions"] if x["kind"] == "add_resume_evidence"), None)
        assert s and s["target"] == "resume.skills", s
        assert "SQL" in s["value"]

    def test_确实不具备的能力不出补简历建议(self):
        """fixable_by_resume=false 说明是真不具备，往简历里塞东西等于造假。"""
        got = build_review([rec(fixable=False, evidence="没有券商实习")] * 3)
        assert not [s for s in got["suggestions"] if s["kind"] == "add_resume_evidence"]


class ApplySuggestionTest:
    def _cfg(self):
        import boss_bot.unified_config as UC
        return UC.UnifiedConfig()

    def test_采纳打分规则建议只动那一个字段(self):
        cfg = self._cfg()
        before = (cfg.resume.school, cfg.ai.match_threshold, cfg.ai.custom_filter_keywords)
        s = {"kind": "add_scoring_rule", "target": "ai.custom_scoring_prompt",
             "value": "SQL 取数经验按 10 分计"}
        ok, msg = apply_suggestion(cfg, s)
        assert ok, msg
        assert s["value"] in cfg.ai.custom_scoring_prompt
        assert (cfg.resume.school, cfg.ai.match_threshold,
                cfg.ai.custom_filter_keywords) == before, "顺手改到别的字段就是越权"

    def test_同一条建议重复采纳不写两遍(self):
        cfg = self._cfg()
        s = {"kind": "add_scoring_rule", "target": "ai.custom_scoring_prompt", "value": "规则A"}
        apply_suggestion(cfg, s)
        apply_suggestion(cfg, s)
        assert cfg.ai.custom_scoring_prompt.count("规则A") == 1

    def test_补简历证据写进技能列表(self):
        cfg = self._cfg()
        cfg.resume.skills = ["Excel"]
        ok, _ = apply_suggestion(cfg, {"kind": "add_resume_evidence",
                                       "target": "resume.skills", "value": "SQL 取数"})
        assert ok
        assert cfg.resume.skills == ["Excel", "SQL 取数"]

    def test_否决词建议追加不覆盖(self):
        cfg = self._cfg()
        cfg.ai.custom_filter_keywords = ["外包"]
        ok, _ = apply_suggestion(cfg, {"kind": "add_veto_keyword",
                                       "target": "ai.custom_filter_keywords",
                                       "value": "驻场"})
        assert ok and cfg.ai.custom_filter_keywords == ["外包", "驻场"]

    def test_未知类型不动配置(self):
        cfg = self._cfg()
        ok, msg = apply_suggestion(cfg, {"kind": "wat", "target": "ai.api_key",
                                         "value": "sk-xxxx"})
        assert ok is False and msg
        assert cfg.ai.api_key == "", "没登记过的 target 一律不许写"

    def test_空值建议不动配置(self):
        cfg = self._cfg()
        ok, msg = apply_suggestion(cfg, {"kind": "add_scoring_rule",
                                         "target": "ai.custom_scoring_prompt", "value": "  "})
        assert ok is False and msg


class JudgementReviewApiTest:
    """面板端点：复盘数据按账号取，建议只有点采纳才落配置。"""

    def _seed_records(self, *rows):
        from boss_bot.reply_record import GreetRecord, _get_greet_store
        store = _get_greet_store()
        for r in rows:
            store.add(GreetRecord(
                job_name=r.get("job_name", "数据分析师"),
                company=r.get("company", "某公司"),
                ai_score=r.get("score", 63), ai_is_match=False,
                is_skipped=True, skip_reason="AI判定不匹配: x",
                account_index=r.get("account", 0), ai_probe=r.get("probe")))

    def _probe(self, blocker="要求 3 年以上 SQL 取数经验", evidence="没写 SQL 项目经历",
               fixable=True):
        return {"blocking_requirement": blocker, "evidence_missing": evidence,
                "fixable_by_resume": fixable, "score_if_fixed": 78}

    def test_GET按账号出复盘(self, flask_app):
        import json, os
        app, tmp = flask_app
        (tmp / "bot_config.json").write_text(json.dumps({
            "ai": {"enabled": True, "providers": []},
            "accounts": [{"name": "主账号", "jobs": []}, {"name": "账号2", "jobs": []}],
        }, ensure_ascii=False), encoding="utf-8")
        os.utime(tmp / "bot_config.json")
        self._seed_records({"account": 0, "probe": self._probe()},
                           {"account": 0, "probe": self._probe()},
                           {"account": 1, "probe": self._probe("要能驻场")})
        c = app.app.test_client()
        got = c.get("/api/ai/judgement_review?account=0").get_json()
        assert got["status"] == "ok", got
        assert got["probed"] == 2, got
        assert got["top_blockers"][0]["requirement"] == "要求 3 年以上 SQL 取数经验"
        assert all(s["auto_applied"] is False for s in got["suggestions"])
        other = c.get("/api/ai/judgement_review?account=1").get_json()
        assert other["probed"] == 1, "两个号的复盘串了"

    def test_采纳打分规则建议写进该账号覆盖(self, flask_app):
        import json, os
        app, tmp = flask_app
        (tmp / "bot_config.json").write_text(json.dumps({
            "ai": {"enabled": True, "custom_scoring_prompt": "全局基准", "providers": []},
            "accounts": [{"name": "主账号", "jobs": []},
                         {"name": "账号2", "jobs": [], "settings": {}}],
        }, ensure_ascii=False), encoding="utf-8")
        os.utime(tmp / "bot_config.json")
        c = app.app.test_client()
        s = {"kind": "add_scoring_rule", "target": "ai.custom_scoring_prompt",
             "value": "SQL 取数经验写明即视为满足"}
        r = c.post("/api/ai/judgement_review/adopt", json={"suggestion": s, "account": 1})
        assert r.get_json()["status"] == "ok", r.get_json()
        disk = json.loads((tmp / "bot_config.json").read_text(encoding="utf-8"))
        assert disk["ai"]["custom_scoring_prompt"] == "全局基准", \
            "点账号2 的采纳却改了全局基准"
        assert "SQL 取数经验写明即视为满足" in \
            disk["accounts"][1]["settings"]["ai"]["custom_scoring_prompt"]

    def test_第二次采纳不会抹掉第一次的覆盖(self, flask_app):
        """真机实测点出来的：连点两条「采纳」，第一条静默消失。

        端点是在全局段上追加再折回账号，第二次进来时盘上的覆盖没被读进生效值，
        折出来的 diff 就成了空 —— settings 被整个替换掉，用户点了两次只留最后一次。
        """
        import json, os
        app, tmp = flask_app
        (tmp / "bot_config.json").write_text(json.dumps({
            "ai": {"enabled": True, "custom_scoring_prompt": "全局基准", "providers": []},
            "resume": {"skills": ["Excel"]},
            "accounts": [{"name": "主账号", "jobs": []},
                         {"name": "账号2", "jobs": [], "settings": {}}],
        }, ensure_ascii=False), encoding="utf-8")
        os.utime(tmp / "bot_config.json")
        c = app.app.test_client()
        first = {"kind": "add_scoring_rule", "target": "ai.custom_scoring_prompt",
                 "value": "第一条：坐班不作为硬性项"}
        second = {"kind": "add_resume_evidence", "target": "resume.skills", "value": "SQL 取数"}
        assert c.post("/api/ai/judgement_review/adopt",
                      json={"suggestion": first, "account": 1}).get_json()["status"] == "ok"
        assert c.post("/api/ai/judgement_review/adopt",
                      json={"suggestion": second, "account": 1}).get_json()["status"] == "ok"
        disk = json.loads((tmp / "bot_config.json").read_text(encoding="utf-8"))
        assert "第一条：坐班不作为硬性项" in \
            disk["accounts"][1]["settings"]["ai"]["custom_scoring_prompt"], \
            "第二次采纳把第一次的账号覆盖冲掉了"
        assert disk["resume"]["skills"] == ["Excel", "SQL 取数"]
        assert disk["ai"]["custom_scoring_prompt"] == "全局基准"

    def test_补简历建议只能写全局(self, flask_app):
        """简历按号覆盖会让两个号对同一个 HR 说出互相矛盾的介绍，所以不折进账号。"""
        import json, os
        app, tmp = flask_app
        (tmp / "bot_config.json").write_text(json.dumps({
            "ai": {"enabled": True, "providers": []},
            "resume": {"skills": ["Excel"]},
            "accounts": [{"name": "主账号", "jobs": []}, {"name": "账号2", "jobs": []}],
        }, ensure_ascii=False), encoding="utf-8")
        os.utime(tmp / "bot_config.json")
        c = app.app.test_client()
        s = {"kind": "add_resume_evidence", "target": "resume.skills", "value": "SQL 取数"}
        r = c.post("/api/ai/judgement_review/adopt", json={"suggestion": s, "account": 1})
        assert r.get_json()["status"] == "ok", r.get_json()
        disk = json.loads((tmp / "bot_config.json").read_text(encoding="utf-8"))
        assert disk["resume"]["skills"] == ["Excel", "SQL 取数"]
        assert not (disk["accounts"][1].get("settings") or {}), "简历被写进账号覆盖了"

    def test_没登记的建议类型拒绝写入(self, flask_app):
        import json, os
        app, tmp = flask_app
        (tmp / "bot_config.json").write_text(json.dumps({
            "ai": {"enabled": True, "api_key": "sk-real", "providers": []},
            "accounts": [{"name": "主账号", "jobs": []}],
        }, ensure_ascii=False), encoding="utf-8")
        os.utime(tmp / "bot_config.json")
        c = app.app.test_client()
        before = (tmp / "bot_config.json").read_text(encoding="utf-8")
        r = c.post("/api/ai/judgement_review/adopt",
                   json={"suggestion": {"kind": "wat", "target": "ai.api_key",
                                        "value": "sk-evil"}})
        assert r.status_code in (200, 400)
        assert r.get_json()["status"] != "ok", r.get_json()
        assert (tmp / "bot_config.json").read_text(encoding="utf-8") == before, \
            "被拒的采纳请求也动了配置文件"


class FrontendReviewTest:
    """界面上要看得见：AI 卡片一行 + 记录原因可展开，且跟着数据范围切。"""

    def _html(self):
        from pathlib import Path
        return (Path(__file__).resolve().parent.parent / "flask-version" /
                "templates" / "index.html").read_text(encoding="utf-8")

    def test_AI卡片有复盘那一行(self):
        html = self._html()
        assert 'id="aiReviewLine"' in html
        assert "/api/ai/judgement_review" in html

    def test_采纳按钮打的是adopt端点且带账号(self):
        html = self._html()
        start = html.index("function loadAiReview")
        block = html[start:html.index("function renderAiHealthSummary", start)]
        assert "/api/ai/judgement_review/adopt" in block
        assert "body.account" in block, "不带账号就会把建议写进全局基准"
        assert "btn.disabled = true" in block, "重复点采纳会写两遍，按钮要锁住"

    def test_复盘行随数据范围一起刷新(self):
        html = self._html()
        i = html.index("loadAiQuality();")
        assert "loadAiReview();" in html[i - 400:i + 400], \
            "切了账号只重读判分质量，复盘还是上一个号的"

    def test_开机就填一次而不是等用户切范围(self):
        """applyScopeToView 只在切数据范围/增删账号时跑；不在启动序列里调一次，
        页面刚打开那行永远是"未统计"（2026-09-30 真机实测就是这个）。"""
        html = self._html()
        # 找顶层那一次（文件里 loadAiHealth 还有函数内的调用）
        i = html.rindex("loadAiHealth();")
        head = html[i - 300:i + 600]
        assert "loadAiReview();" in head and "loadAiQuality();" in head, head[-200:]

    def test_记录原因列可展开追问(self):
        html = self._html()
        assert "ai_probe" in html, "记录行没读追问结果"
        block = html[html.index("function toGreetRow"):]
        block = block[:block.index("\nfunction ")]
        assert "ai_probe" in block, "历史行归一化时把追问丢了，只有推送行有"

    def test_推送行也带追问(self):
        """实时推送与历史记录都过 toGreetRow，追问才不会只有刷新后才出现。"""
        html = self._html()
        i = html.index("socket.on('greet_record'")
        seg = html[i:i + 400]
        assert "addGreetRecord(" in seg, "推送那一支没接进统一入口"
        j = html.index("function addGreetRecord")
        body = html[j:html.index("\nfunction ", j + 10)]
        assert "toGreetRow(" in body, "addGreetRecord 绕过了归一化，追问会在半路丢掉"


class ProbeQuotaInputTest:
    """上限必须有输入框且双向接好——按"改了必须生效"，输入框没接线就是 bug。"""

    def _html(self):
        from pathlib import Path
        return (Path(__file__).resolve().parent.parent / "flask-version" /
                "templates" / "index.html").read_text(encoding="utf-8")

    def test_输入框存在(self):
        html = self._html()
        assert 'id="aiProbeMaxPerRound"' in html

    def test_读配置时回填(self):
        html = self._html()
        assert "setVal('aiProbeMaxPerRound'" in html, "界面永远显示默认值，看不出改过没有"

    def test_改动时写回配置(self):
        html = self._html()
        block = html[html.index("function onAiChange"):]
        block = block[:block.index("\nfunction ")]
        assert "config.ai.probe_max_per_round" in block, "改了输入框不会保存"
