"""每个账号一套配置（task #34 的最后一块）。

两个号投的是不同工种：主号投数据分析、账号2 投运营，判分标准不该共用一套
阈值和提示词。做法是给账号留一份"只存差异"的覆盖（accounts[i].settings），
循环启动和热重载时盖到基准配置上；界面选中某个账号时读写的就是这份生效值。

API 和前端都用真的：接口走 Flask test client 真读真写临时 bot_config.json，
前端函数抽出来交 node 真跑——字符串扫描证明不了"改了到底生效没有"。
"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import pytest

from boss_bot.unified_config import UnifiedConfig
from tests.test_config_hot_reload import flask_app  # noqa: F401  跨文件复用夹具
from tests.test_greet_record_realtime import extract_fn  # noqa: F401

INDEX_HTML = Path("flask-version/templates/index.html")


def _cfg():
    c = UnifiedConfig()
    c.ai.match_threshold = 70
    c.ai.custom_scoring_prompt = "全局基准提示词"
    c.greet.accounts[0].name = "主账号"
    if len(c.greet.accounts) > 1:
        c.greet.accounts[1].name = "账号2"
    return c


def run_js(fns, prologue, code):
    """把 index.html 里的若干函数交给 node 真跑，返回 out 字典。"""
    node = subprocess.run(["node", "--version"], capture_output=True, text=True)
    if node.returncode != 0:
        raise AssertionError("本机没有 node，无法执行前端行为测试")
    html = INDEX_HTML.read_text(encoding="utf-8")
    src = "\n".join(extract_fn(html, f) for f in fns)
    script = prologue + "\n" + src + "\n" + code
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as f:
        f.write(script)
        path = f.name
    r = subprocess.run(["node", path], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, f"node 报错：{r.stderr[:600]}"
    assert r.stdout.strip(), "脚本没有输出：末尾要 console.log(JSON.stringify(o))"
    return json.loads(r.stdout.strip().splitlines()[-1])


# ===================== 配置模型：账号覆盖 =====================

class AccountOverlayTest(unittest.TestCase):
    def test_覆盖只盖写过的字段(self):
        cfg = _cfg()
        cfg.greet.accounts[0].settings = {"ai": {"match_threshold": 88}}
        eff = cfg.apply_account(0)
        self.assertEqual(eff.ai.match_threshold, 88)
        self.assertEqual(eff.ai.custom_scoring_prompt, "全局基准提示词",
                         "没覆盖的字段不该被清空")

    def test_没有覆盖就是基准(self):
        cfg = _cfg()
        self.assertEqual(cfg.apply_account(0).ai.match_threshold, 70)

    def test_索引越界不动配置(self):
        cfg = _cfg()
        self.assertEqual(cfg.apply_account(99).ai.match_threshold, 70)

    def test_未知段落忽略(self):
        cfg = _cfg()
        cfg.greet.accounts[0].settings = {"nope": {"x": 1}, "resume": {"name": "改了也不生效"}}
        eff = cfg.apply_account(0)
        self.assertEqual(eff.ai.match_threshold, 70)

    def test_段内未知字段忽略(self):
        cfg = _cfg()
        cfg.greet.accounts[0].settings = {"ai": {"not_a_field": 1}}
        self.assertEqual(cfg.apply_account(0).ai.match_threshold, 70)

    def test_读得写得出(self):
        cfg = UnifiedConfig()
        cfg._apply_bot_config({"accounts": [
            {"name": "a", "settings": {"ai": {"match_threshold": 85}}},
            {"name": "b"},
        ]})
        self.assertEqual(cfg.greet.accounts[0].settings["ai"]["match_threshold"], 85)
        self.assertEqual(cfg.greet.accounts[1].settings, {})
        out = cfg.to_dict()["accounts"]
        self.assertEqual(out[0]["settings"], {"ai": {"match_threshold": 85}})


# ===================== 运行循环各持一份 =====================

class LoopOwnsItsConfigTest(unittest.TestCase):
    """循环之间必须各拿一份配置：共用一个对象的话 A 号的覆盖会串进 B 号。"""

    def _loop(self, cfg, idx):
        from unittest.mock import patch

        from boss_bot.main_loop import UnifiedBotLoop
        with patch("boss_bot.main_loop.BrowserManager"):
            return UnifiedBotLoop(config=cfg, account_index=idx)

    def test_两个循环不共用config(self):
        base = _cfg()
        base.greet.accounts[0].settings = {"ai": {"match_threshold": 91}}
        lp0 = self._loop(base, 0)
        lp1 = self._loop(base, 1)
        self.assertIsNot(lp0.config, base, "循环直接用了共享对象，覆盖会互相污染")
        self.assertEqual(lp0.config.ai.match_threshold, 91)
        self.assertEqual(lp1.config.ai.match_threshold, 70)
        self.assertEqual(base.ai.match_threshold, 70, "基准被就地改掉了")

    def test_热重载也按账号取(self):
        """每 10 秒的热重载如果只 self.config = load()，账号覆盖会被冲掉。"""
        from unittest.mock import patch

        from boss_bot.main_loop import UnifiedBotLoop
        disk = _cfg()
        disk.greet.accounts[0].settings = {"ai": {"match_threshold": 77}}
        with patch("boss_bot.main_loop.BrowserManager"):
            lp = UnifiedBotLoop(config=disk, account_index=0)
        lp._log = lambda *a: None
        lp._greet_engine = None
        lp._reply_engine = None
        lp._stats = type("S", (), {"snapshot": lambda self: {}})()
        self.assertEqual(lp.config.ai.match_threshold, 77)
        with patch("boss_bot.main_loop.UnifiedConfig") as uc:
            uc.load.return_value = _cfg()
            uc.load.return_value.greet.accounts[0].settings = {"ai": {"match_threshold": 77}}
            lp._hot_reload_config()
        self.assertEqual(lp.config.ai.match_threshold, 77,
                         "热重载把账号覆盖冲掉了")
        self.assertEqual(_cfg().ai.match_threshold, 70, "基准被就地改掉了")


# ===================== diff_against =====================

class DiffAgainstTest(unittest.TestCase):
    def test_只留下差异(self):
        from boss_bot.unified_config import diff_against
        base = {"a": 1, "b": "x", "c": [1, 2]}
        self.assertEqual(diff_against({"a": 2, "b": "x", "c": [1, 2], "d": 9}, base), {"a": 2})

    def test_全一样就是空覆盖(self):
        from boss_bot.unified_config import diff_against
        self.assertEqual(diff_against({"a": 1}, {"a": 1}), {})

    def test_嵌套段只留差异字段(self):
        from boss_bot.unified_config import diff_against
        self.assertEqual(
            diff_against({"ai": {"match_threshold": 88, "model": "m"},
                          "reply": {"check_interval": 8}},
                         {"ai": {"match_threshold": 70, "model": "m"},
                          "reply": {"check_interval": 8}}),
            {"ai": {"match_threshold": 88}})


# ===================== 接口：按账号读写 =====================

SEEDED = {
    "ai": {"enabled": True, "match_threshold": 70, "custom_scoring_prompt": "全局基准"},
    "accounts": [
        {"name": "主账号", "jobs": [{"city": "长沙", "query": "数据分析"}]},
        {"name": "账号2", "jobs": [{"city": "长沙", "query": "运营"}],
         "settings": {"ai": {"match_threshold": 88}}},
    ],
    "greet": {"enabled": True},
}


def _seed(tmp, data=None):
    (tmp / "bot_config.json").write_text(
        json.dumps(data or SEEDED, ensure_ascii=False), encoding="utf-8")


class ConfigApiAccountTest:
    def test_GET带account返回生效值(self, flask_app):
        app, tmp = flask_app
        _seed(tmp)
        c = app.app.test_client()
        eff = c.get("/api/config?account=1").get_json()["config"]
        assert eff["ai"]["match_threshold"] == 88, "没把账号覆盖合进去"
        assert eff["ai"]["custom_scoring_prompt"] == "全局基准", "没覆盖的字段要回落全局"
        assert c.get("/api/config").get_json()["config"]["ai"]["match_threshold"] == 70, \
            "不带 account 时必须还是全局基准"

    def test_POST带account写进覆盖且不动全局(self, flask_app):
        app, tmp = flask_app
        _seed(tmp)
        c = app.app.test_client()
        submitted = c.get("/api/config?account=1").get_json()["config"]
        submitted["ai"]["match_threshold"] = 95
        submitted["ai"]["custom_scoring_prompt"] = "账号2 只看运营岗"
        r = c.put("/api/config", json={"config": submitted, "account": 1})
        assert r.get_json()["status"] == "ok", r.get_json()

        disk = json.loads((tmp / "bot_config.json").read_text(encoding="utf-8"))
        assert disk["ai"]["match_threshold"] == 70, \
            "改账号2 的阈值顺手把全局基准也改了"
        assert disk["ai"]["custom_scoring_prompt"] == "全局基准"
        assert disk["accounts"][1]["settings"]["ai"] == {
            "match_threshold": 95, "custom_scoring_prompt": "账号2 只看运营岗"}
        assert disk["accounts"][0].get("settings", {}) == {}, "另一个账号被写脏"

    def test_POST不带account就是全局(self, flask_app):
        app, tmp = flask_app
        _seed(tmp)
        c = app.app.test_client()
        submitted = c.get("/api/config").get_json()["config"]
        submitted["ai"]["match_threshold"] = 60
        c.put("/api/config", json={"config": submitted})
        disk = json.loads((tmp / "bot_config.json").read_text(encoding="utf-8"))
        assert disk["ai"]["match_threshold"] == 60
        assert disk["accounts"][1].get("settings", {}) == {"ai": {"match_threshold": 88}}, \
            "保存全局时把已有覆盖抹掉了"

    def test_改回和全局一样就撤掉覆盖(self, flask_app):
        """用户把账号阈值改回全局值，应当变成"没有覆盖"而不是留一份同值僵尸。"""
        app, tmp = flask_app
        _seed(tmp)
        c = app.app.test_client()
        submitted = c.get("/api/config?account=1").get_json()["config"]
        submitted["ai"]["match_threshold"] = 70
        c.put("/api/config", json={"config": submitted, "account": 1})
        disk = json.loads((tmp / "bot_config.json").read_text(encoding="utf-8"))
        assert disk["accounts"][1]["settings"] == {}


# ===================== 前端：作用对象说清楚 + 保存带账号 =====================

SAVE_JS_PROLOGUE = r'''
let config = {ai:{enabled:true,match_threshold:95,analyze_max_tokens:1600,
                  providers:[],api_key:'',api_base:'x',model:'m'},
              accounts:[{name:'主账号'},{name:'账号2'}]};
let dataScope = '1';
const captured = {};
function addLog(){}
function busyBtn(){}
function toast(){}
function fetch(url, opts){ captured.url = url; captured.body = JSON.parse(opts.body);
  return Promise.resolve({ok:true, json:()=>Promise.resolve({status:'ok'})}); }
'''

NOTE_JS_PROLOGUE = r'''
let dataScope = '1';
let config = {accounts:[{name:'主账号'},{name:'账号2'}]};
const el = {};
var document = { getElementById: function(id){ el[id] = el[id] || {textContent:''}; return el[id]; } };
'''


class UiScopeBadgeTest:
    def test_AI面板标出当前作用对象(self):
        out = run_js(["updateAiScopeNote"], NOTE_JS_PROLOGUE,
                     r'''
const o = {};
updateAiScopeNote();
o.per_account = el['aiScopeNote'].textContent;
dataScope = 'all';
updateAiScopeNote();
o.all = el['aiScopeNote'].textContent;
console.log(JSON.stringify(o));
''')
        assert "账号2" in out["per_account"], out["per_account"]
        assert "全局" not in out["per_account"]
        assert "全局" in out["all"], out["all"]

    def test_保存带上账号(self):
        out = run_js(["saveConfig"], SAVE_JS_PROLOGUE, r'''
const o = {};
saveConfig().then(function(){
  o.account = captured.body.account;
  o.threshold = (captured.body.config.ai || {}).match_threshold;
  console.log(JSON.stringify(o));
});
''')
        assert out["account"] == 1, f"选中账号2 时保存不带账号：{out}"
        assert out["threshold"] == 95

    def test_全部账号时不带account(self):
        out = run_js(["saveConfig"], SAVE_JS_PROLOGUE.replace("let dataScope = '1';",
                                                              "let dataScope = 'all';"), r'''
const o = {};
saveConfig().then(function(){
  o.account = captured.body.account;
  console.log(JSON.stringify(o));
});
''')
        assert out["account"] in (None, "all"), f"'全部账号' 范围不该写进某个号：{out}"

    def test_切数据范围会重拉配置(self):
        """切了范围但输入框还是上一个号的值，就是"看着改了其实没生效"。"""
        html = INDEX_HTML.read_text(encoding="utf-8")
        body = extract_fn(html, "setDataScope")
        assert "loadConfig" in body, "切数据范围没重拉配置，AI 面板还停在上一个号"


if __name__ == "__main__":
    unittest.main()
