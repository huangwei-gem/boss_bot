# -*- coding: utf-8 -*-
"""AI 自定义筛选的四层（2026-10-28「我输入我的文件，你就能匹配对应的岗位」）。

① 文档：上传/粘贴的要求要存得住、读得回、空文件和扫描版 PDF 要当场说读不出，
   不能存进去冒充"已上传"；超长截断要留痕。
② 提示词：判分时要真把这份要求喂进去，换了文件缓存键要跟着变（不然改了不生效）。
③ 判分写回：结论要盖章那是"按哪份文件判的"；接口异常那次不许盖章（否则一行
   错误结论会永远冒充"已经看过他的要求了"）。
④ 重判线程：跳过本地已否的和已按这份文件判过的；接口连着不通要自己停手。
"""

import io
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from boss_bot import ai_screen_doc            # noqa: E402
from boss_bot import screen_runner            # noqa: E402
from boss_bot.greet_engine import AIAnalyzerChain   # noqa: E402
from boss_bot.screen_judge import 判分写回     # noqa: E402


@pytest.fixture
def 目录(tmp_path, monkeypatch):
    """文档落在 tmp：不碰他真攒的那份 data/ai_screen_doc.json。"""
    monkeypatch.setattr(ai_screen_doc, "resolve_path", lambda p: tmp_path)
    yield tmp_path


class 文档存取Test:
    def test_存了要能从盘上读回来(self, 目录):
        d = ai_screen_doc.存("我的要求.txt", "只要长沙的数据分析岗，不要外包")
        assert d["hash"] and d["chars"] == len("只要长沙的数据分析岗，不要外包")
        assert d["truncated"] is False
        读 = ai_screen_doc.读()
        assert 读["text"] == "只要长沙的数据分析岗，不要外包"
        assert 读["name"] == "我的要求.txt"

    def test_空文档拒收(self, 目录):
        with pytest.raises(ValueError) as e:
            ai_screen_doc.存("空.txt", "   \n  ")
        assert "没读出文字" in str(e.value)
        assert ai_screen_doc.读()["hash"] == "", "拒收就不许在盘上留下半份"

    def test_超长截断并留痕(self, 目录):
        d = ai_screen_doc.存("长.txt", "要" * (ai_screen_doc.最大字数 + 50))
        assert d["chars"] == ai_screen_doc.最大字数
        assert d["truncated"] is True

    def test_清空之后就是没有要求(self, 目录):
        ai_screen_doc.存("有.txt", "数据分析")
        assert ai_screen_doc.当前指纹()
        ai_screen_doc.清空()
        assert ai_screen_doc.当前()["hash"] == ""

    def test_换文本指纹就要变(self, 目录):
        a = ai_screen_doc.存("a.txt", "只要长沙")
        b = ai_screen_doc.存("a.txt", "只要上海")
        assert a["hash"] != b["hash"]
        assert ai_screen_doc.当前指纹() == b["hash"], "面板换完文件，判分线程下一单就要按新的判"


class 解析上传Test:
    def test_utf8文本(self):
        assert ai_screen_doc.解析上传("要求.txt", "只投数据分析".encode("utf-8")) == "只投数据分析"

    def test_gbk文本也认得(self):
        文 = ai_screen_doc.解析上传("要求.txt", "只投数据分析".encode("gb18030"))
        assert 文 == "只投数据分析"

    def test_空文件说没文字(self):
        with pytest.raises(ValueError) as e:
            ai_screen_doc.解析上传("空.txt", b"   ")
        assert "没有文字" in str(e.value)

    def test_老doc直接说读不了(self):
        with pytest.raises(ValueError) as e:
            ai_screen_doc.解析上传("要求.doc", b"\xd0\xcf\x11\xe0")
        assert ".docx" in str(e.value)

    def test_docx连表格一起读(self):
        docx = pytest.importorskip("docx")
        文档 = docx.Document()
        文档.add_paragraph("只要长沙、上海")
        表 = 文档.add_table(rows=1, cols=2)
        表.rows[0].cells[0].text = "不要"
        表.rows[0].cells[1].text = "外包派遣"
        缓冲 = io.BytesIO()
        文档.save(缓冲)
        文 = ai_screen_doc.解析上传("要求.docx", 缓冲.getvalue())
        assert "只要长沙" in 文 and "外包派遣" in 文

    def test_读不出字的pdf说读不出(self):
        pypdf = pytest.importorskip("pypdf")
        写 = pypdf.PdfWriter()
        写.add_blank_page(width=200, height=200)
        缓冲 = io.BytesIO()
        写.write(缓冲)
        with pytest.raises(ValueError) as e:
            ai_screen_doc.解析上传("扫描件.pdf", 缓冲.getvalue())
        assert "读不出文字" in str(e.value), "扫描版别回空串——那会被当成'文件是空的'"


def _链():
    return AIAnalyzerChain(providers=[{"name": "假", "api_key": "k",
                                       "api_base": "http://x/v1", "model": "m"}])


JOB = {"job_name": "线上数据整理", "company": "某公司", "city": "长沙·岳麓区",
       "experience": "1-3年", "education": "本科",
       "description": "整理销售数据", "requirements": "会 Excel"}


class 提示词注入Test:
    def test_要求原文进系统提示词(self, monkeypatch):
        要求 = {"text": "只要长沙的岗位，不要外包", "hash": "h1"}
        monkeypatch.setattr(ai_screen_doc, "当前", lambda: 要求)
        消息 = _链()._build_prompt(JOB)[0]["content"]
        assert "【用户提供的筛选要求】" in 消息
        assert "只要长沙的岗位，不要外包" in 消息

    def test_没有要求文件就不出现这一段(self, monkeypatch):
        monkeypatch.setattr(ai_screen_doc, "当前", lambda: {"text": "", "hash": ""})
        消息 = _链()._build_prompt(JOB)[0]["content"]
        assert "【用户提供的筛选要求】" not in 消息

    def test_岗位的城市经验学历也进提示词(self, monkeypatch):
        monkeypatch.setattr(ai_screen_doc, "当前", lambda: {"text": "", "hash": ""})
        消息 = _链()._build_prompt(JOB)[1]["content"]
        assert "城市/地点：长沙" in 消息
        assert "经验要求：1-3年" in 消息 and "学历要求：本科" in 消息

    def test_换了要求文件缓存键要跟着变(self, monkeypatch):
        当前 = {"v": "h1"}
        monkeypatch.setattr(ai_screen_doc, "当前指纹", lambda: 当前["v"])
        链 = _链()
        键1 = 链._make_cache_key("https://job/2", "简历哈希")
        当前["v"] = "h2"
        assert 链._make_cache_key("https://job/2", "简历哈希") != 键1, \
            "指纹不进键＝同一岗位命中旧文件下判出来的旧结论"


class 假池:
    def __init__(self, 行s):
        self.行s = 行s
        self.改过 = []

    def 列表(self, **kw):
        # 重判线程按「新抓的在前」拿队列；这里只回原序，挑哪几条由它自己的判据决定
        return list(self.行s)

    def 改(self, 编号, **字段):
        for r in self.行s:
            if r["id"] == 编号:
                r.update(字段)
                self.改过.append((编号, 字段))
                return True, "ok"
        return False, "没有这一条"


class 假引擎:
    def __init__(self, 结果):
        self.结果 = 结果
        self.看过 = []

    def _analyze_job_with_ai(self, job):
        self.看过.append(job)
        return self.结果(job)


def _行(i=1, **over):
    r = {"id": "id%d" % i, "job_name": "数据分析%d" % i, "company": "公司%d" % i,
         "salary": "8-10K", "city": "长沙", "url": "https://zhipin/job/%d" % i,
         "status": "new", "gate": {}, "ai": {}}
    r.update(over)
    return r


class 判分写回Test:
    文档 = {"text": "只要长沙", "hash": "doc-h1"}

    def test_正常结论盖上这份文件的指纹(self):
        池 = 假池([_行()])
        引擎 = 假引擎(lambda job: ({"score": 82, "is_match": True, "reason": "对口",
                                   "model": "假", "veto_hit": ""}, 0.1))
        话, 写 = 判分写回(池, 引擎, 池.行s[0], 文档=self.文档)
        assert "82" in 话 and 写["合"] is True
        assert 写["doc"] == "doc-h1"
        assert 池.行s[0]["ai"]["doc"] == "doc-h1", "必须真写回池子"

    def test_接口异常那次不盖指纹(self):
        池 = 假池([_行()])
        引擎 = 假引擎(lambda job: ({"score": 0, "is_match": False, "reason": "超时",
                                   "model": "假", "ai_error": True}, 0.1))
        话, 写 = 判分写回(池, 引擎, 池.行s[0], 文档=self.文档)
        assert 写["接口异常"] is True and "doc" not in 写
        assert "没判成" in 话
        assert 池.行s[0]["ai"].get("doc") in (None, ""), "没判成不能盖章成'按这份文件判过'"

    def test_没有可用接口就不写不盖(self):
        池 = 假池([_行()])
        引擎 = 假引擎(lambda job: (None, 0.1))
        话, 写 = 判分写回(池, 引擎, 池.行s[0], 文档=self.文档)
        assert 写 is None and "暂无可用 AI 接口" in 话
        assert 池.改过 == []

    def test_判了不合也是真结论要落盘(self):
        """打招呼腿靠 None 不投，但"不合"必须写进池子，否则下一轮又问一遍。"""
        池 = 假池([_行()])

        def 判(job):
            job["_ai_result"] = {"score": 35, "is_match": False, "reason": "方向不符",
                                 "veto_hit": "外包"}
            job["_ai_meta"] = {"model": "假"}
            return None, 0.1
        引擎 = 假引擎(判)
        话, 写 = 判分写回(池, 引擎, 池.行s[0], 文档=self.文档)
        assert 写["合"] is False and 写["doc"] == "doc-h1"
        assert "不合" in 话 and 池.行s[0]["ai"]["否决"] == "外包"

    def test_判分抛异常不写(self):
        池 = 假池([_行()])

        def 炸(job):
            raise RuntimeError("炸了")
        引擎 = 假引擎(炸)
        话, 写 = 判分写回(池, 引擎, 池.行s[0], 文档=self.文档)
        assert 写 is None and "炸了" in 话 and 池.改过 == []


class 重判线程Test:
    文档 = {"text": "只要长沙", "hash": "run-h1"}

    @pytest.fixture(autouse=True)
    def _预备(self, monkeypatch):
        monkeypatch.setattr(ai_screen_doc, "当前", lambda: self.文档)
        screen_runner.停()          # 上一个用例留下的停标记要清
        yield
        screen_runner.停()
        _等到停()

    def _起(self, monkeypatch, 行s, 结果, 账号们=(0,)):
        池 = 假池(行s)
        monkeypatch.setattr(screen_runner, "岗位池", lambda account_index=0: 池)
        引擎 = 假引擎(结果)
        成, 话 = screen_runner.起(list(账号们), lambda 号: 引擎)
        assert 成, 话
        _等到停()
        return 池, 引擎

    def test_没有要求文件不许开跑(self, monkeypatch):
        monkeypatch.setattr(ai_screen_doc, "当前", lambda: {"text": "", "hash": ""})
        成, 话 = screen_runner.起([0], lambda 号: 假引擎(lambda j: (None, 0)))
        assert not 成 and "要求" in 话

    def test_跳过本地已否和已按这份文件判过的(self, monkeypatch):
        行s = [_行(1), _行(2),
               _行(3, gate={"方向命中": False}),
               _行(4, gate={"否决命中": "外包"}),
               _行(5, ai={"合": True, "doc": "run-h1", "判于": time.time()}),
               _行(6, ai={"合": True, "doc": "旧文件", "判于": time.time()})]
        池, 引擎 = self._起(monkeypatch, 行s,
                             lambda job: ({"score": 80, "is_match": True, "reason": "ok",
                                           "model": "假", "veto_hit": ""}, 0.1))
        assert len(引擎.看过) == 3, "2 条待判 + 1 条旧文件下判的要按新文件重判；已否和已判的不许烧 AI"
        态 = screen_runner.状态()
        assert 态["total"] == 3 and 态["skipped"] == 2
        assert 态["judged"] == 3 and 态["matched"] == 3
        assert 池.行s[5]["ai"]["doc"] == "run-h1", "旧文件的结论要按新文件重判并盖新章"

    def test_接口一直不通连着十条就停手(self, monkeypatch):
        行s = [_行(i) for i in range(1, 13)]
        池, 引擎 = self._起(monkeypatch, 行s, lambda job: (None, 0.1))
        态 = screen_runner.状态()
        assert len(引擎.看过) == 10, "连着 10 条没判成要停手：不许拿着整池空转一遍"
        assert 态["failed"] == 10 and 态["judged"] == 0
        assert "没判成" in 态["error"]

    def test_点停止就在下一行之前停(self, monkeypatch):
        行s = [_行(i) for i in range(1, 5)]

        def 判(job):
            screen_runner.停()
            return {"score": 70, "is_match": True, "reason": "ok",
                    "model": "假", "veto_hit": ""}, 0.1
        self._起(monkeypatch, 行s, 判)
        态 = screen_runner.状态()
        assert 态["stopped"] is True and 态["judged"] == 1

    def test_引擎全起不来就照实说(self, monkeypatch):
        def 工厂(号):
            raise RuntimeError("AI 没启用")
        成, 话 = screen_runner.起([0], 工厂)
        assert 成
        _等到停()
        态 = screen_runner.状态()
        assert "AI 引擎起不来" in 态["error"], 态


def _等到停(超时=8.0):
    """等重判线程收口。超时就断言失败——静默超时会让下一个用例撞上"还在跑"。"""
    end = time.time() + 超时
    while time.time() < end:
        if not screen_runner.状态()["running"]:
            return
        time.sleep(0.02)
    raise AssertionError("重判线程 %.1f 秒还没停" % 超时)
