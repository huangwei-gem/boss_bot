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
