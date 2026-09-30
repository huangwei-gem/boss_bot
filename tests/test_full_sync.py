"""采集口径：启动后全量同步一次侧栏，之后回复轮只看未读。

用户口径（2026-09-30）：「每次启动才要全量采集，在投递的过程中只要采集未读的」
—— 启动先同步最新消息保证上下文干净，运行中不再每轮全量点开。
"""
from unittest.mock import MagicMock, patch

import pytest


def _make_loop():
    from boss_bot.unified_config import UnifiedConfig, AccountConfig, JobConfig
    cfg = UnifiedConfig()
    cfg.greet.accounts = [
        AccountConfig(name="主账号", enabled=True, cookie_file="a0.json",
                      jobs=[JobConfig(query="数据分析", city="长沙", enabled=True)]),
    ]
    cfg.browser.debug_port = 9222

    class FakeManager:
        def __init__(self, config=None, account_index=0, port=None, user_data_dir=None):
            self.instance = MagicMock()
            self.instance.url = "https://www.zhipin.com/web/geek/chat"
            self.instance.run_js.return_value = '{"boxes":[],"words":[]}'
            self.search = MagicMock()
            self.search.url = "https://www.zhipin.com/web/geek/chat"
            self.search.run_js.return_value = "ok"

        def get_instance(self):
            return self.instance

        def get_search_page(self):
            return self.search

        def get_chat_page(self):
            return self.instance

        def get_reply_tab_id(self):
            return None

    with patch("boss_bot.main_loop.BrowserManager", FakeManager):
        from boss_bot.main_loop import MultiAccountManager
        mgr = MultiAccountManager(config=cfg)
    loop = mgr._loops[0]
    loop._log = lambda *a, **k: None
    return loop


class GetAllChatsTest:
    def test_侧栏全部会话不依赖未读标记(self):
        import inspect
        from boss_bot.page_handler import BossChatHandler
        src = inspect.getsource(BossChatHandler.get_all_chats)
        assert ".friend-content" in src, "全量快照要扫侧栏全部 .friend-content 行"
        assert "notice-badge" not in src, "全量同步不能只挑带红点的行"

    def test_未读列表仍在用红点判据(self):
        import inspect
        from boss_bot.page_handler import BossChatHandler
        src = inspect.getsource(BossChatHandler.get_unread_chats)
        assert "notice-badge" in src


class FullSyncTest:
    def test_全量同步逐个点开只存档不回复(self):
        loop = _make_loop()
        loop._running = True
        loop._on_captcha_page = lambda: False
        chats = [
            {"index": 0, "name": "陈女士", "company": "小智时代", "preview": "你好"},
            {"index": 1, "name": "王女士", "company": "艾秒广告", "preview": "在吗"},
        ]
        loop._chat_handler = MagicMock()
        loop._chat_handler.get_all_chats.return_value = chats
        loop._chat_handler.enter_chat.return_value = True
        loop._chat_handler.read_all_messages.return_value = [
            {"text": "你好", "is_mine": False, "time": "10:00"},
        ]
        loop._chat_handler.read_selected_row.return_value = {"name": "陈女士", "company": "小智时代"}
        loop._chat_handler.get_job_name.return_value = "数据分析"
        loop._msg_store = MagicMock()
        loop._reply_engine = MagicMock()

        loop._full_sync_chats()

        assert loop._chat_handler.enter_chat.call_count == 2
        assert loop._msg_store.merge_messages.call_count == 2
        # 全量同步绝不触发回复
        loop._reply_engine.generate_reply.assert_not_called()

    def test_切换校验失败不中断整体同步(self):
        loop = _make_loop()
        loop._running = True
        loop._on_captcha_page = lambda: False
        loop._chat_handler = MagicMock()
        loop._chat_handler.get_all_chats.return_value = [
            {"index": 0, "name": "甲", "company": "", "preview": ""},
            {"index": 1, "name": "乙", "company": "", "preview": ""},
        ]
        loop._chat_handler.enter_chat.side_effect = [False, True]
        loop._chat_handler.read_all_messages.return_value = [
            {"text": "hi", "is_mine": False, "time": "10:00"}]
        loop._chat_handler.read_selected_row.return_value = {"name": "乙", "company": ""}
        loop._chat_handler.get_job_name.return_value = ""
        loop._msg_store = MagicMock()
        loop._reply_engine = MagicMock()

        loop._full_sync_chats()

        assert loop._msg_store.merge_messages.call_count == 1

    def test_中途出现验证页立即中止(self):
        loop = _make_loop()
        loop._running = True
        loop._on_captcha_page = lambda: True
        loop._chat_handler = MagicMock()
        loop._chat_handler.get_all_chats.return_value = [
            {"index": 0, "name": "甲", "company": "", "preview": ""},
        ]
        loop._msg_store = MagicMock()

        loop._full_sync_chats()

        loop._chat_handler.enter_chat.assert_not_called()

    def test_回复线程只在启动后同步一次(self):
        import inspect
        from boss_bot.main_loop import UnifiedBotLoop
        src = inspect.getsource(UnifiedBotLoop)
        assert "_full_sync_chats" in src, "回复线程启动后要调用一次全量同步"
        assert "_full_sync_done" in src, "全量同步必须有一次性标记，不能每轮都全量"
