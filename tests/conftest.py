"""测试会话级夹具 — 保证单元测试永远不碰用户的真实运行数据。

reply_records.json / greet_records.json 里是真实的打招呼与回复历史，
之前跑一次 pytest 就会往里追加记录，等于测试污染生产数据。
"""

import logging
import logging.handlers
import os
import tempfile
from pathlib import Path

import pytest

# ─────────────────────────────────────────────
# 文件日志重定向：测试一律不许写进仓库的 logs/
# ─────────────────────────────────────────────
# 2026-09-29 排查"主号今天为什么 0 投递"时，logs/boss_bot.log 里 [账号1] 的
# 120 行全是 pytest 留下的（"引擎初始化完成"×20、"🧪 演练模式 本应回复: 你好"、
# "已删除失效 Cookie 文件 a0.json"），真实现场被完全盖掉。
# 必须在这里改类的 __init__：handler 是 app.py / greet_engine.py 在 import 时挂的，
# 等到 fixture 再摘就已经来不及拦同一轮里的写入了。
REPO_LOG_DIR = (Path(__file__).resolve().parent.parent / "logs").resolve()
TEST_LOG_DIR = Path(tempfile.mkdtemp(prefix="boss_test_logs_"))

_orig_file_init = logging.FileHandler.__init__
_orig_timed_init = logging.handlers.TimedRotatingFileHandler.__init__


def _rerouted(filename):
    """仓库 logs/ 下的路径换到临时目录，其它路径原样保留"""
    f = os.path.normpath(str(filename))
    if os.path.dirname(f).lower() == str(REPO_LOG_DIR).lower():
        return str(TEST_LOG_DIR / os.path.basename(f))
    return filename


def _file_init(self, filename, *args, **kwargs):
    _orig_file_init(self, _rerouted(filename), *args, **kwargs)


def _timed_init(self, filename, *args, **kwargs):
    _orig_timed_init(self, _rerouted(filename), *args, **kwargs)


logging.FileHandler.__init__ = _file_init
logging.handlers.TimedRotatingFileHandler.__init__ = _timed_init


# ─────────────────────────────────────────────
# 配置文件重定向：测试一律不许写用户那份 bot_config.json
# ─────────────────────────────────────────────
# 2026-10-10 一天里这份配置被跑测试盖掉了三次（18:00、19:53、22:25 的
# bot_config.json.bak-* 成对出现，一对就是一次 pytest）。写盘的不是某个
# 粗心脚本，而是两条看起来人畜无害的回归：
#   tests/test_headless_switch.py:368/381 → mgr.switch_headless() →
#     apply_headless_config() → cfg.save()，而那个 cfg 是测试自己造的，
#     ai.providers 是空的；
#   面板 GET /api/config → _ensure_greeting_defaults() → cfg.save()，
#     用 TestClient 打这个接口同样落到仓库根目录。
# UnifiedConfig.save() 的路径取的是模块级 BOT_CONFIG_FILE，所以在 import
# 期就把这三个全局改指向临时目录，比在 fixture 里换更早——app.py / config.py
# 是 `from ... import BOT_CONFIG_FILE`，等 fixture 跑起来它们已经把真路径
# 抄进自己的全局了。内容先拷一份真的过去，测试读到的和以前一模一样，
# 只是写不再回仓库。
REPO_DIR = Path(__file__).resolve().parent.parent
TEST_CONFIG_DIR = Path(tempfile.mkdtemp(prefix="boss_test_config_"))
for _名字 in ("bot_config.json", "config_overrides.json", "user_profile.json"):
    _真的 = REPO_DIR / _名字
    if _真的.exists():
        (TEST_CONFIG_DIR / _名字).write_bytes(_真的.read_bytes())


def _isolate_config_paths():
    import boss_bot.unified_config as UC

    UC.BOT_CONFIG_FILE = TEST_CONFIG_DIR / "bot_config.json"
    UC.OVERRIDES_FILE = TEST_CONFIG_DIR / "config_overrides.json"
    UC.USER_PROFILE_FILE = TEST_CONFIG_DIR / "user_profile.json"
    try:
        import boss_bot.config as CFG
        CFG.CONFIG_FILE = str(UC.BOT_CONFIG_FILE)
    except Exception:
        pass
    # app.py 可能已经被先跑的测试 import 过了，它自己那份全局也要换
    import sys
    for 模块 in (sys.modules.get("app"), sys.modules.get("flask_app")):
        if not 模块:
            continue
        for 属性 in ("BOT_CONFIG_FILE", "USER_PROFILE_FILE"):
            现值 = getattr(模块, 属性, None)
            if 现值 is not None:
                setattr(模块, 属性, TEST_CONFIG_DIR / Path(str(现值)).name)


_isolate_config_paths()


@pytest.fixture(autouse=True, scope="session")
def isolate_runtime_records(tmp_path_factory):
    """把记录存储的全局单例指向临时目录。"""
    from boss_bot import reply_record

    data_dir = tmp_path_factory.mktemp("runtime_records")
    reply_record._reply_store = reply_record.ReplyRecordStore(
        path=str(data_dir / "reply_records.json"))
    reply_record._greet_store = reply_record.GreetRecordStore(
        path=str(data_dir / "greet_records.json"))

    # 指标文件也要隔离：UnifiedBotLoop 构造时会拿真实路径的 metrics 单例，
    # 一旦它在测试里执行回填，就会把生产环境的累计值标记成"已回填"而永久停在 0
    from boss_bot import metrics
    original_file = metrics.METRICS_FILE
    metrics.METRICS_FILE = data_dir / "metrics.json"
    metrics._store = None

    # 聊天会话文件也要隔离：MessageStore 不传 base_dir 时默认落在仓库的 messages/，
    # 测试里一次 mark_read 就会改写真实对话记录（把 HR 消息标成已读）
    from boss_bot import config as CFG
    original_base = CFG.BASE_DIR
    CFG.BASE_DIR = data_dir / "runtime_base"

    yield

    reply_record._reply_store = None
    reply_record._greet_store = None
    metrics.METRICS_FILE = original_file
    metrics._store = None
    CFG.BASE_DIR = original_base


@pytest.fixture(autouse=True, scope="session")
def 仓库里的进化数据一条都不许写():
    """SelfEvolveEngine 的状态文件属于用户：测试里不传 data_file 的那种构造，
    默认路径要整个换到临时目录。

    2026-10-19 撞上的：引擎内部那份"质量数据"路径过去只跟着文件名换、不跟着目录换，
    于是 `_save_quality_data()` 会写进仓库的 data/self_evolve_data.json，
    一条测试就把 4 条真规则调整顶成了 1 条 fixture。目录那半已在 self_evolve.py
    改对；这里再把默认路径本身换掉，测试从结构上就够不着仓库那份文件。

    为什么不是"跑完比对一下有没有被动过"：线上面板随时会合法地写这两个文件
    （实测 08:53:07 它就写了一次），那种比法只会天天报假红。
    """
    import boss_bot.self_evolve as SE

    数据目录 = Path(tempfile.mkdtemp(prefix="boss_test_evolution_"))
    原默认 = SE._EVOLUTION_DATA_FILE
    for 名字 in ("evolution_data.json", "self_evolve_data.json"):
        真的 = REPO_DIR / "data" / 名字
        if 真的.exists():
            (数据目录 / 名字).write_bytes(真的.read_bytes())
    SE._EVOLUTION_DATA_FILE = 数据目录 / "evolution_data.json"
    try:
        yield
        assert str(SE._EVOLUTION_DATA_FILE).startswith(str(数据目录)), (
            "进化数据的默认路径又指回仓库了：测试一跑就写用户的真记录")
    finally:
        SE._EVOLUTION_DATA_FILE = 原默认


@pytest.fixture(autouse=True, scope="session")
def 仓库里的登录Cookie一条都不许动():
    """2026-10-17 16:16 一条新写的反向哨兵测试把主账号的 zhipin_cookies.json 搬进了
    data/stale_cookies/：harness 把 `_cookie_file()` 直接 patch 成了仓库里那个真文件名，
    而 `_handle_login()` 判成登录墙的第一件事就是归档 Cookie。文件没丢（只归档不删除），
    但它要是没人管，下一次重启就变成"要人工重新登录"。

    这道门只看一件事：跑完一轮测试，仓库里那些 Cookie 文件必须还在原处。
    """
    原名 = sorted(p.name for p in REPO_DIR.glob("zhipin*.json"))
    yield
    还在 = {p.name for p in REPO_DIR.glob("zhipin*.json")}
    少了 = [n for n in 原名 if n not in 还在]
    assert not 少了, (
        f"测试把用户的登录 Cookie 文件弄走了：{少了}。"
        f"去 data/stale_cookies/ 按时间戳拷回来，再把那条测试的 _cookie_file 指到 tmp")
