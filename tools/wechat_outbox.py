"""把该汇报给本人的事打成一条微信文本，并推到微信。

    python tools/wechat_outbox.py                 # 打印这段时间没报过的（默认 6 小时）
    python tools/wechat_outbox.py --since 24      # 往前找 24 小时
    python tools/wechat_outbox.py --send          # 直接推到微信（公众号测试号模板消息）
    python tools/wechat_outbox.py --clip          # 同时把文本塞进剪贴板，省得转义
    python tools/wechat_outbox.py --mark          # 记成已汇报（推成功后 --send 会自动带做）

发送走 boss_bot/wechat_push：那是微信公众平台的官方接口，不碰微信客户端、
不碰桌面焦点。--clip 这条路留着当备用（接口挂了还能手动贴）。
"""
import argparse
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.message_store import MessageStore                # noqa: E402
from boss_bot.report_outbox import (commit_seen, digest_text,  # noqa: E402
                                    new_reports, scan_interviews)


def _greet_rows():
    """台账/汇报要岗位薪资城市这些字段，吃得话记录补齐；读不到就少几列，不影响判事。"""
    try:
        import json
        d = json.loads((ROOT / "data" / "greet_records.json").read_text(encoding="utf-8"))
        return d.get("records") if isinstance(d, dict) else d
    except Exception:
        return []


def collect(since_hours: float = 6.0):
    截止 = (datetime.now() - timedelta(hours=since_hours)).strftime("%Y-%m-%d %H:%M:%S")
    chats = MessageStore().get_all_chats_detail()
    报, _ = new_reports(chats, _greet_rows(), scan_interviews(chats))
    return [r for r in 报 if str(r.get("last_activity") or "") >= 截止]


def _to_clipboard(text: str) -> bool:
    """走剪贴板而不是逐字符敲：微信里回车就是发送，多行文本只能粘。

    先落一个 UTF-8 临时文件再让 PowerShell 读进去：把文本直接塞进命令行会被
    引号/换行吃掉，覆盖 env 又会让 powershell 找不到自己的路径。
    """
    import os
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".txt", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-Content -LiteralPath '{tmp}' -Raw -Encoding UTF8 | Set-Clipboard"],
            check=True, capture_output=True)
        return True
    except Exception as e:
        detail = getattr(e, "stderr", b"") or b""
        print(f"剪贴板写入失败：{e} {detail[:200]!r}", file=sys.stderr)
        return False
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=float, default=6.0, help="往前找多少小时（默认 6）")
    ap.add_argument("--clip", action="store_true", help="把文本塞进剪贴板")
    ap.add_argument("--send", action="store_true",
                    help="推到微信（公众号测试号模板消息，推成功后自动记账）")
    ap.add_argument("--mark", action="store_true", help="记成已汇报（贴进微信发出后调）")
    args = ap.parse_args()

    报 = collect(args.since)
    文 = digest_text(报, stamp=datetime.now().strftime("%m-%d %H:%M"))
    if not 报:
        print("（这段时间没有要汇报的）")
        return 0
    print(文)
    if args.clip:
        print("\n[已放进剪贴板]" if _to_clipboard(文) else "\n[剪贴板写入失败]")
    if args.send:
        import json
        from boss_bot import wechat_push
        try:
            果 = wechat_push.send_text(
                文, title="BOSS 汇报 " + datetime.now().strftime("%m-%d %H:%M"))
        except Exception as e:
            print(f"[推送失败] {e}", file=sys.stderr)
            return 1
        全成 = bool(果) and all(r["ok"] for r in 果)
        print(f"[已推微信 {sum(1 for r in 果 if r['ok'])}/{len(果)} 条]"
              + ("" if 全成 else "｜失败详情：" + json.dumps(
                  [r for r in 果 if not r["ok"]], ensure_ascii=False)[:300]))
        if not 全成:
            return 1
        args.mark = True
    if args.mark:
        commit_seen([r["_k"] for r in 报 if r.get("_k")])
        print(f"[已记 {len(报)} 条为已汇报]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
