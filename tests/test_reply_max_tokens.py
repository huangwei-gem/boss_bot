# -*- coding: utf-8 -*-
"""回复侧的 max_tokens 要够思考型模型把话说完，而且前端改了要真生效。

现算（data/reply_records.json + 今日 logs/boss_bot.log）：
- "接口没有回复正文（content 为空，思考过程写了 N 字）" 今天 160 条，
  N 的中位 318、p95 896、最大 962——这是被 200 的预算截断后的数字，
  不是思考本身只要 300 字。
- 真正要发出去的回复中位 33 字、p95 63 字、最长 75 字。
预算 200 的时候思考吃掉全部额度，正文一个字都不剩，四个接口连着同一个坑，
那条 HR 消息就当没说过（17 点这一小时全链失败 30 次）。
岗位判分那条链早就为同样的问题把预算放到 analyze_max_tokens=1600，
回复这条一直没跟上，而且 200 这个数在面板上根本没有入口。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.unified_config import UnifiedConfig  # noqa: E402

TEMPLATE = (ROOT / "flask-version" / "templates" / "index.html").read_text(encoding="utf-8")


def _write_cfg(tmp_path, ai):
    p = tmp_path / "bot_config.json"
    p.write_text(json.dumps({"ai": ai}, ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_默认预算够思考型模型把正文留出来(tmp_path):
    cfg = UnifiedConfig.load(_write_cfg(tmp_path, {}))
    assert cfg.ai.max_tokens >= 1200, \
        f"回复预算 {cfg.ai.max_tokens}：思考 p95 就要 896 字，正文必然被截没"


def test_配置文件里改了就读得到(tmp_path):
    cfg = UnifiedConfig.load(_write_cfg(tmp_path, {"max_tokens": 900}))
    assert cfg.ai.max_tokens == 900, "读不到就是面板改了没生效"


def test_保存能写回文件(tmp_path):
    path = _write_cfg(tmp_path, {"max_tokens": 900})
    cfg = UnifiedConfig.load(path)
    cfg.ai.max_tokens = 1500
    cfg.save(path)
    assert json.loads(Path(path).read_text(encoding="utf-8"))["ai"]["max_tokens"] == 1500
    assert UnifiedConfig.load(path).ai.max_tokens == 1500


def test_回复引擎每次调用都取当前预算():
    import inspect
    from boss_bot.main_loop import UnifiedBotLoop
    src = inspect.getsource(UnifiedBotLoop._hot_reload_config)
    assert "self._reply_engine._ai_max_tokens = self.config.ai.max_tokens" in src, \
        "热重载里没有这一句，改了要重启才生效"


def test_面板上有回复预算的输入框而且两头都接上():
    assert 'id="aiMaxTokens"' in TEMPLATE
    assert "setVal('aiMaxTokens'" in TEMPLATE, "没有回填：打开设置显示的是空框"
    assert "config.ai.max_tokens = parseInt(document.getElementById('aiMaxTokens')" in TEMPLATE, \
        "没有收集：输入框只是个摆设"
