"""只读取证：那两张页此刻还能不能回 CDP 消息。

2026-10-16 12:49 起两条投递腿都停在"点了沟通之后"那一段，整整一个钟头没有
一条日志——分不清是"我们的线程卡在一个不返回的调用上"还是"页面本身已经不
答话"。这两种的修法完全不同：前者要给这一单加总时长闸门，后者要在超时后
把这一族判死并换掉浏览器。

这个脚本只发 `Runtime.evaluate`：document.readyState / title / 是否存在聊天
输入框，不点、不输入、不导航，也不动我们进程里的任何对象。每个请求都带自己
的墙钟超时，超时就是超时，如实报出来。

用法：python tools/probe_cdp_reply.py [端口 ...]
"""
import json
import sys
from urllib.request import urlopen

from websocket import create_connection

PORTS = (9222, 9223)
PROBES = (
    ("document.readyState", "document.readyState"),
    ("title", "document.title"),
    ("url", "location.href.slice(0, 80)"),
    ("聊天输入框", "!!document.querySelector('#chat-input, .chat-input, .input-area')"),
)
WAIT_SEC = 6


def 探一条(ws_url, 表达式):
    ws = None
    起 = 0.0
    try:
        import time
        起 = time.perf_counter()
        ws = create_connection(ws_url, enable_multithread=True, suppress_origin=True,
                               timeout=WAIT_SEC)
        ws.send(json.dumps({"id": 2, "method": "Runtime.evaluate",
                            "params": {"expression": 表达式, "returnByValue": True}}))
        回 = json.loads(ws.recv())
        return time.perf_counter() - 起, 回.get("result", {}).get("result", {}).get("value", 回)
    except Exception as e:
        import time
        return time.perf_counter() - 起, "%s: %s" % (type(e).__name__, e)
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


def main():
    for port in (int(a) for a in (sys.argv[1:] or PORTS)):
        print("=== 端口 %d ===" % port)
        try:
            with urlopen("http://127.0.0.1:%d/json" % port, timeout=5) as r:
                tabs = [t for t in json.loads(r.read().decode()) if t.get("type") == "page"]
        except Exception as e:
            print("  拿不到标签页列表:", e)
            continue
        for t in tabs:
            url = (t.get("url") or "")[:70]
            print("  页: %s" % url)
            for 名, 表达式 in PROBES:
                秒, 值 = 探一条(t["webSocketDebuggerUrl"], 表达式)
                值 = str(值)[:70]
                print("    %-10s %6.2fs  %s" % (名, 秒, 值))


if __name__ == "__main__":
    main()
