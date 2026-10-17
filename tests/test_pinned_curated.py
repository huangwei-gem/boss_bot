# -*- coding: utf-8 -*-
"""他置顶的那几路 = 严选正例，要能把判分和方向闸往这个方向收。

用户口径（2026-10-17）：「我置顶的都是我严选的，可以朝着这方向去努力。」

现状是这条信号压根没有入口：`pinned` 这个字段在 greet_engine / self_evolve /
judgement_review 里零引用，判分提示词只有 `custom_scoring_prompt` + 否决词，
正向方向闸只吃配置里那 13 个词。也就是说他亲手挑出来正在聊的那一类活，
机器既不知道、也不会往那儿多投——只有否决词在往外砍。

这一档要接通的是：存档里 pinned 的会话 → 岗位标题清洗 →
①判分提示词里作为"他自己认可的方向" ②方向闸的词表里作为补集。
否决词仍然优先：严选不等于放行，标题写着到岗的照样拦。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.greet_engine import (AIAnalyzerChain, GreetEngine,  # noqa: E402
                                   jd_gate, 严选方向词)
from tests.test_reply_backfill_wiring import _make_loop  # noqa: E402


def _conv(name, company, job_name, pinned=False, account_index=0):
    c = {"chat_name": name, "company": company, "job_name": job_name,
         "account_index": account_index, "messages": [],
         "updated_at": (datetime.now() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")}
    if pinned:
        c["pinned"] = True
    return c


class 标题清洗Test:
    def test_剥掉薪资城市查看职位和兼职前缀(self):
        """实测盘上 2505 个会话的 job_name 100% 带「查看职位」，48% 带「兼职·」前缀。"""
        assert 严选方向词("兼职·数据分析专员100-300元/天东莞查看职位") == "数据分析专员"

    def test_K制薪资也剥得掉(self):
        assert 严选方向词("数据分析专员4-6K长沙查看职位") == "数据分析专员"

    def test_没有薪资时只剥前缀(self):
        assert 严选方向词("兼职·excel数据处理专员") == "excel数据处理专员"

    def test_空标题给空串(self):
        assert 严选方向词("") == "" and 严选方向词(None) == ""

    def test_城市不许被切成一条方向词(self):
        """把标题分词会把"青岛""银川"当成词喂进方向闸——任何青岛的岗都能过闸。

        所以一条严选会话只产出一条词，就是清洗后的整条岗位名，不分词。
        """
        词 = 严选方向词("兼职·ai数据标注师（青岛）方言转写 数据标注10-30元/时银川查看职位")
        assert 词 == "ai数据标注师（青岛）方言转写 数据标注", 词


class 严选名册Test:
    def _循环(self, tmp_path, monkeypatch, 存档, 号=0):
        loop = _make_loop(tmp_path, monkeypatch)
        loop.account_index = 号
        读盘 = []

        class _Store:
            def get_all_chats_detail(self):
                读盘.append(1)
                return 存档

        loop._msg_store = _Store()
        loop._log = lambda *a, **k: None
        return loop, 读盘

    def test_只取本号置顶会话的岗位(self, tmp_path, monkeypatch):
        存档 = [_conv("唐女士", "东莞市万昭贸易", "兼职·数据分析专员100-300元/天东莞查看职位",
                     pinned=True),
                _conv("王女士", "长沙数据标注", "兼职·数据标注20-40元/时长沙查看职位"),
                _conv("潘女士", "学洲教育", "兼职·课程顾问", pinned=True, account_index=1)]
        loop, _ = self._循环(tmp_path, monkeypatch, 存档)
        assert loop._严选岗位名() == ["数据分析专员"], loop._严选岗位名()

    def test_没有置顶时给空表不硬造(self, tmp_path, monkeypatch):
        loop, _ = self._循环(tmp_path, monkeypatch,
                             [_conv("王女士", "长沙数据标注", "兼职·数据标注")])
        assert loop._严选岗位名() == []

    def test_一轮之内不许反复翻两千个存档文件(self, tmp_path, monkeypatch):
        存档 = [_conv("唐女士", "东莞市万昭贸易", "兼职·数据分析专员100-300元/天东莞查看职位",
                     pinned=True)]
        loop, 读盘 = self._循环(tmp_path, monkeypatch, 存档)
        for _ in range(5):
            loop._严选岗位名()
        assert len(读盘) == 1, f"严选名册要带缓存，实测读了 {len(读盘)} 遍"


class 判分提示词带严选Test:
    JOB = {"job_name": "线上数据整理专员", "url": "https://job/2",
           "description": "销售数据、客户数据整理，定期输出报表", "requirements": "会 Excel"}

    def _链(self, 严选=None):
        return AIAnalyzerChain(providers=[{"name": "假", "api_key": "k",
                                           "api_base": "http://x/v1", "model": "m"}],
                               pinned_job_examples=严选)

    def test_严选方向要出现在系统提示词里(self):
        消息 = self._链(["数据分析专员", "excel数据处理专员"])._build_prompt(self.JOB)[0]
        assert "严选" in 消息["content"], 消息["content"][:400]
        assert "数据分析专员" in 消息["content"]

    def test_没有严选时这一整段不出现(self):
        消息 = self._链([])._build_prompt(self.JOB)[0]
        assert "严选" not in 消息["content"], "空表就别硬塞一段废话进提示词"

    def test_严选变了缓存键要跟着变(self):
        """口径不进键就等于改了不生效（2026-10-04 一轮 62 次判分 54 次是缓存）。"""
        链 = self._链(["数据分析专员"])
        键1 = 链._make_cache_key("https://job/2", "简历哈希")
        链.pinned_job_examples = ["飞书文档助理"]
        assert 链._make_cache_key("https://job/2", "简历哈希") != 键1


class 方向闸吃严选词Test:
    def _引擎(self, 配置词, 严选):
        cfg = MagicMock()
        cfg.ai.enabled = False
        eng = GreetEngine(MagicMock(), cfg, account_index=0, pinned_jobs_cb=lambda: 严选)
        eng._ai_target_job_keywords = list(配置词)
        eng._log = lambda *a, **k: None
        return eng

    def test_严选词并进方向表且不重复配置里已有的(self):
        eng = self._引擎(["数据分析"], ["数据分析专员", "excel数据处理专员"])
        assert eng.方向词表() == ["数据分析", "excel数据处理专员"], eng.方向词表()

    def test_方向闸用合并表_严选那种岗不再被当成跑偏(self):
        """配置只写"数据分析"时，「excel数据处理专员」标题一条方向词都不沾，被闸拦死。"""
        eng = self._引擎(["数据分析"], ["excel数据处理专员"])
        岗 = {"job_name": "excel数据处理专员", "jd_description": "按模板整理数据"}
        assert eng.方向词表() and jd_gate([], 岗, target_keywords=eng.方向词表()) == "", \
            jd_gate([], 岗, target_keywords=eng.方向词表())

    def test_没并严选时这一单照样被拦(self):
        """反向哨兵：门必须能真的红——把严选去掉就该回到拦。"""
        eng = self._引擎(["数据分析"], [])
        岗 = {"job_name": "excel数据处理专员", "jd_description": "按模板整理数据"}
        assert "不是要投的方向" in jd_gate([], 岗, target_keywords=eng.方向词表())

    def test_否决词仍然优先于严选(self):
        eng = self._引擎(["数据分析"], ["数据标注员"])
        岗 = {"job_name": "数据标注员（需到公司）", "jd_description": "标注"}
        assert jd_gate([], 岗, target_keywords=eng.方向词表()), "到岗的岗不能因为严选就放行"


class 接线Test:
    def test_主循环把严选提供器交给打招呼引擎(self):
        源 = (ROOT / "boss_bot" / "main_loop.py").read_text(encoding="utf-8")
        assert "pinned_jobs_cb=self._严选岗位名" in 源, \
            "GreetEngine 拿不到 MessageStore，只能由主循环注入这一条路"

    def test_方向闸只从一处取词表(self):
        """合并逻辑散在多处就会两头不一致（否决词那三处副本对齐过一次）。"""
        import inspect
        源 = inspect.getsource(GreetEngine)
        assert 源.count("self.方向词表()") >= 1
        assert "target_keywords=self._ai_target_job_keywords" not in 源, \
            "闸门要读合并后的那张表，不许再直接吃配置那份"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
