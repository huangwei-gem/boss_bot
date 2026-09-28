"""测试会话级夹具 — 保证单元测试永远不碰用户的真实运行数据。

reply_records.json / greet_records.json 里是真实的打招呼与回复历史，
之前跑一次 pytest 就会往里追加记录，等于测试污染生产数据。
"""

import pytest


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

    yield

    reply_record._reply_store = None
    reply_record._greet_store = None
    metrics.METRICS_FILE = original_file
    metrics._store = None
