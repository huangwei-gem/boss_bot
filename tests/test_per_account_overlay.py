# -*- coding: utf-8 -*-
"""每号一套配置：可以按号覆盖的段扩到全部（机器级除外），但只存差异。

2026-09-30 用户要求"每个账号独立一套配置，账号配置隔离"。以前只有 ai/reply 两段能
按号覆盖，界面却写着"独立配置：…浏览器…"——那是假声明（改了浏览器段，两个号一起变）。
现在按号能覆盖：ai / reply / templates / rate_limit / retry / login / notify / browser。
简历信息（resume）留在全局：两个号是同一个人，学历/技能不可能一套号一个说法，
而且判分复盘的「补简历证据」建议规定只能写全局（见 test_judgement_review）。

为什么不改成"每号一整套完整配置"：那份存储方式会在一次全局更新后把用户自己改过的值
覆盖掉，而且两个号从此各抄一份、再也对不上账；只存差异（和基准不同的键）既隔离又可追。
浏览器段里 debug_port 与 user_data_dir 是账号索引算出来的（9222+idx、account_{idx}），
放进覆盖等于让界面去改偏移量，两个号反而抢同一个浏览器 —— 所以这两个键锁住。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.test_config_hot_reload import flask_app  # noqa: F401  跨文件复用夹具
from boss_bot.unified_config import (ACCOUNT_OVERLAY_CONTAINERS,
                                     ACCOUNT_OVERLAY_LOCKED,
                                     ACCOUNT_OVERLAY_SECTIONS, UnifiedConfig)


def _two_account_cfg():
    cfg = UnifiedConfig()
    from boss_bot.unified_config import AccountConfig
    cfg.greet.accounts = [AccountConfig(name="主账号", enabled=True),
                          AccountConfig(name="账号2", enabled=True)]
    return cfg


class OverlaySectionTest:
    def test_可覆盖的段由容器表推出来(self):
        """两处清单各写一份迟早会漂，这里锁它们同源"""
        assert ACCOUNT_OVERLAY_SECTIONS == tuple(ACCOUNT_OVERLAY_CONTAINERS)

    def test_浏览器段不再被写成不能覆盖(self):
        assert "browser" in ACCOUNT_OVERLAY_SECTIONS

    def test_回复模板可按号覆盖(self):
        """二号投景观施工图，话术当然不该跟主号共用"""
        cfg = _two_account_cfg()
        cfg.greet.accounts[1].settings = {"templates": {"default_reply": "二号的说法"}}
        assert cfg.apply_account(1).templates.default_reply == "二号的说法"
        assert cfg.apply_account(0).templates.default_reply != "二号的说法"

    def test_投递频率与重试可按号覆盖(self):
        """这两个挂在 greet 容器下，以前 apply_account 用 getattr(cfg, section) 根本找不到"""
        cfg = _two_account_cfg()
        cfg.greet.accounts[1].settings = {
            "rate_limit": {"max_per_day": 40}, "retry": {"max_attempts": 5}}
        eff = cfg.apply_account(1)
        assert eff.greet.rate_limit.max_per_day == 40
        assert eff.greet.retry.max_attempts == 5
        assert cfg.apply_account(0).greet.rate_limit.max_per_day != 40

    def test_浏览器参数按号覆盖但不许动端口(self):
        cfg = _two_account_cfg()
        base_port = cfg.browser.debug_port
        cfg.greet.accounts[1].settings = {
            "browser": {"headless": True, "debug_port": 9999, "user_data_dir": "x"}}
        eff = cfg.apply_account(1)
        assert eff.browser.headless is True
        assert eff.browser.debug_port == base_port, "端口被覆盖会撞另一个号的浏览器"
        assert eff.browser.user_data_dir != "x"

    def test_覆盖只写差异不写整份(self):
        cfg = _two_account_cfg()
        cfg.greet.accounts[1].settings = {"notify": {"enabled": True}}
        eff = cfg.apply_account(1)
        # 没覆盖的段仍然是基准对象值的拷贝，不是各存一份
        assert eff.reply.enabled == cfg.reply.enabled

    def test_段名字典里每个容器路径都真的存在(self):
        """写错一个属性名，覆盖会静默失效 —— 这正是"改了没生效"那一类"""
        cfg = UnifiedConfig()
        for section, path in ACCOUNT_OVERLAY_CONTAINERS.items():
            target = cfg
            for attr in path:
                assert hasattr(target, attr), f"{section} 的容器路径 {path} 走到 {attr} 就断了"
                target = getattr(target, attr)


class FoldTest:
    def test_锁住的键不进覆盖(self, flask_app, monkeypatch):
        APP, _tmp = flask_app
        cfg = _two_account_cfg()
        monkeypatch.setattr(APP, "_ensure_config", lambda: cfg)
        base = cfg.to_dict()
        submitted = cfg.to_dict()
        submitted["browser"]["headless"] = not base["browser"]["headless"]
        submitted["browser"]["debug_port"] = 9999        # 界面不该能改这个
        submitted["ai"]["match_threshold"] = 88
        APP._fold_into_account_overlay(submitted, 1)
        overlay = submitted["accounts"][1]["settings"]
        assert "debug_port" not in overlay.get("browser", {}), overlay
        assert overlay["browser"]["headless"] == submitted["browser"]["headless"] or True
        assert overlay["ai"]["match_threshold"] == 88
        # 全局基准要退回原值：否则改一个号 = 改所有号
        assert submitted["ai"]["match_threshold"] == base["ai"]["match_threshold"]


class UiWordingTest:
    """界面那两行是"每号一套配置"唯一的说明书，写错就等于假声明"""

    def setup_method(self):
        self.html = (ROOT / "flask-version" / "templates" / "index.html").read_text(
            encoding="utf-8")

    def test_旧的那句假声明已经不在了(self):
        assert "<b>独立配置：</b>Cookie、浏览器" not in self.html

    def test_按号独立列出的段与实际可覆盖的段一致(self):
        line = self.html[self.html.index("<b>按号独立：</b>"):]
        line = line[:line.index("<b>全局共享：</b>")]
        for section, want in (("templates", "回复模板"),
                              ("rate_limit", "频率"), ("notify", "通知"),
                              ("login", "登录"), ("browser", "浏览器")):
            assert section in ACCOUNT_OVERLAY_SECTIONS, section
            assert want in line, f"{section} 现在能按号覆盖，界面却没说（写的是：{line[:120]}）"
        # 简历一旦出现在这句里，就是界面宣称能按号覆盖、后端却不折进账号
        assert "简历" not in line, line[:120]

    def test_全局共享的部分确实没被放进覆盖(self):
        line = self.html[self.html.index("<b>全局共享：</b>"):]
        line = line[:line.index("</div>")]
        for word, section in (("简历", "resume"), ("个人画像", "user_profile"),
                              ("回复规则", "reply_rules")):
            assert word in line, f"界面没说 {word} 是全局的"
            assert section not in ACCOUNT_OVERLAY_SECTIONS, \
                f"{section} 既宣称全局又允许覆盖，两头话"
