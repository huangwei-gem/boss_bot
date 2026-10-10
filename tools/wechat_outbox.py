"""把该汇报给本人的事打成一条文本，并推到 IM（飞书优先，退回微信）。

    python tools/wechat_outbox.py                 # 打印这段时间没报过的（默认 6 小时）
    python tools/wechat_outbox.py --since 24      # 往前找 24 小时
    python tools/wechat_outbox.py --send          # 推出去（配了 data/feishu_push.json 就走飞书）
    python tools/wechat_outbox.py --clip          # 同时把文本塞进剪贴板，省得转义
    python tools/wechat_outbox.py --mark          # 记成已汇报（推成功后 --send 会自动带做）

两条出口都在，按凭据文件挑：boss_bot/feishu_push（飞书群机器人 webhook，不要 token、
不要扫码、100 次/分钟）优先，没配就用 boss_bot/wechat_push（公众号测试号模板消息）。
两边都不碰客户端、不抢桌面焦点；--clip 留着当接口挂掉时的手动后备。
"""
import argparse
import os
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

    具体怎么落到剪贴板（Windows 的 PowerShell + UTF-8 临时文件、mac 的 pbcopy）
    收在 platform_compat.写剪贴板 里，那条命令行引号的坑也写在那边的注释里。
    这条路是备用出口（推送接口挂了才手动粘），失败只报不拦。
    """
    from boss_bot.platform_compat import 写剪贴板
    try:
        return 写剪贴板(text)
    except Exception as e:
        detail = getattr(e, "stderr", b"") or b""
        print(f"剪贴板写入失败：{e} {detail[:200]!r}", file=sys.stderr)
        return False


def 挑出口(有没有=os.path.exists):
    """两条通道都留着，按有没有配过凭据挑：飞书那边只要一个群机器人 webhook 就通，
    微信那边还得多一步人在网页上建模板（见 wechat_push 的说明）。"""
    from boss_bot import feishu_push, wechat_push
    if 有没有(feishu_push.CREDS_PATH):
        return feishu_push.send_text, "飞书"
    return wechat_push.send_text, "微信"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=float, default=6.0, help="往前找多少小时（默认 6）")
    ap.add_argument("--clip", action="store_true", help="把文本塞进剪贴板")
    ap.add_argument("--send", action="store_true",
                    help="推送汇报（配了飞书群机器人走飞书，否则走微信模板消息），推成功后自动记账")
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
        送, 渠道 = 挑出口()
        try:
            果 = 送(文, title="BOSS 汇报 " + datetime.now().strftime("%m-%d %H:%M"))
        except Exception as e:
            print(f"[推送失败] {e}", file=sys.stderr)
            return 1
        全成 = bool(果) and all(r["ok"] for r in 果)
        print(f"[已推{渠道} {sum(1 for r in 果 if r['ok'])}/{len(果)} 条]"
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
