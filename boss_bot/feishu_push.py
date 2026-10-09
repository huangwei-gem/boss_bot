# -*- coding: utf-8 -*-
"""把汇报推到飞书：走群里的「自定义机器人」webhook。

为什么不是微信（2026-10-09 一路查/测下来的结论）：

* 个人微信客户端没有对外接口。wcferry 那类要往微信进程注 DLL，且只支持 3.9.x，
  本机装的是 4.1.13 的 Weixin.exe；wxauto 那类读界面控件，客户端一升级就断。
* 腾讯 2026 年确实开了官方个人 Bot 通道（iLink / ClawBot，WorkBuddy 那类宿主扫码
  接的就是它），但协议规定**每人 24 小时最多 10 条主动推送**，超了接口直接 429，
  还要每天给助手发一条心跳维持 context_token——用来做"一天几条摘要"勉强够，
  用来做程序化出口太脆。
* 公众号测试号模板消息不用人工建模板这一步走不通（template_id 只能在测试号网页上点）——
  见 boss_bot/wechat_push.py，那条代码也在，配好就能用。
* 飞书群机器人只要一个 webhook URL：不要 token、不要扫码、不要维持会话，
  配额 100 次/分钟、5 次/秒，请求体上限 20 KB。

凭据放在 data/feishu_push.json（data/ 已在 .gitignore 里，不进仓库）：
    {"webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/xxxx",
     "secret": "开启了签名校验才填，否则留空"}
"""
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request

from .wechat_push import chunk_text as _按行切块

CREDS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "feishu_push.json")

# 一条 text 消息的正文上限（字）。飞书限的是请求体 20 KB，中文一个字 UTF-8 占 3 字节，
# 1000 字连头带尾 3 KB 出头，离红线还远；再大就分条——分条比截断有用，
# 汇报是按岗位分组的，截了就读不出前后关系。
CHUNK_CHARS = 1000

# 飞书对单个机器人的频次是 5 次/秒，切多条时贴着这个限走，别赌它不丢
SEND_GAP_SECONDS = 0.3


def load_creds(path=CREDS_PATH):
    """读凭据；缺哪个就说缺哪个，不要抛 KeyError 让人猜。"""
    if not os.path.exists(path):
        raise RuntimeError(f"飞书推送未配置：找不到 {path}，至少要有 webhook")
    with open(path, encoding="utf-8") as f:
        creds = json.load(f) or {}
    if not creds.get("webhook"):
        raise RuntimeError(f"飞书推送未配置：{path} 里缺 webhook")
    return creds


def sign(timestamp, secret):
    """飞书的签名：待签的 key 是 "时间戳\\n密钥"，被签内容是一个字节都没有，结果 Base64。

    写成常见 HMAC 那种"把报文拼进去签"会一直 19021 sign match fail，
    这个坑在飞书文档的示例代码里写得很直白。
    """
    key = f"{timestamp}\n{secret}".encode("utf-8")
    return base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode()


def payload(text, timestamp=None, 密钥=""):
    """拼一条飞书 text 报文；群机器人开了签名校验才带 timestamp/sign。"""
    体 = {"msg_type": "text", "content": {"text": text}}
    if 密钥:
        ts = str(timestamp if timestamp is not None else int(time.time()))
        体["timestamp"] = ts
        体["sign"] = sign(ts, 密钥)
    return 体


def _post(url, payload_dict):
    body = json.dumps(payload_dict, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"code": e.code, "msg": e.read().decode("utf-8", "replace")}
    except Exception as e:
        return {"code": -1, "msg": f"{type(e).__name__}: {e}"}


def 回包成功(回):
    """HTTP 200 不代表发出去了，一切看飞书回的状态码；新老两种回包都在用。"""
    if not isinstance(回, dict):
        return False
    for 键 in ("code", "StatusCode"):
        if 键 in 回:
            try:
                return int(回[键]) == 0
            except (TypeError, ValueError):
                return False
    return False


def send_text(text, title="BOSS 汇报", creds=None, _post=_post, _sleep=time.sleep):
    """把一段文本推过去，过长自动切几条。返回每条的结果列表。"""
    creds = creds or load_creds()
    密钥 = creds.get("secret") or ""
    正文 = str(text or "").strip() or "（这一轮没有内容）"
    块 = _按行切块(正文, CHUNK_CHARS)
    果 = []
    for idx, part in enumerate(块, 1):
        头 = title if len(块) == 1 else f"{title}（{idx}/{len(块)}）"
        回 = _post(creds["webhook"], payload(f"{头}\n{part}", 密钥=密钥))
        果.append({"part": idx, "ok": 回包成功(回),
                   "code": 回.get("code", 回.get("StatusCode")),
                   "msg": 回.get("msg", 回.get("StatusMessage"))})
        if idx < len(块):
            _sleep(SEND_GAP_SECONDS)
    return 果


def selftest():
    """发一条自检消息，把飞书回的原话带回来。"""
    return send_text("链路自检：这条到了就说明 BOSS 汇报能推到这个飞书群。",
                     title="BOSS 机器人")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="飞书推送（群机器人 webhook）")
    ap.add_argument("--text", help="要发的文本")
    ap.add_argument("--text-file", help="从文件读要发的文本")
    ap.add_argument("--selftest", action="store_true", help="发一条自检消息")
    args = ap.parse_args()
    try:
        if args.selftest:
            print(json.dumps(selftest(), ensure_ascii=False, indent=1))
        else:
            body = (open(args.text_file, encoding="utf-8").read()
                    if args.text_file else args.text)
            if not body:
                ap.error("要发东西就给 --text 或 --text-file，自检用 --selftest")
            print(json.dumps(send_text(body), ensure_ascii=False, indent=1))
    except Exception as e:
        print("发送失败：" + str(e))
