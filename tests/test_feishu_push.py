# -*- coding: utf-8 -*-
"""汇报推飞书：走飞书群里的自定义机器人 webhook。

为什么是这条（2026-10-09）：微信侧个人号没有对外接口，公众号测试号要在网页上人工
建模板、iLink/ClawBot 那条官方个人 Bot 通道每人 24 小时只有 10 条主动推送；飞书群
机器人只要一个 webhook URL，不要 token、不要扫码、不要维持心跳，100 次/分钟 5 次/秒
的配额对"一天几条汇报"绰绰有余。
"""
import base64
import hashlib
import hmac
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from boss_bot import feishu_push as fp  # noqa: E402


class FakePost:
    def __init__(self, 回=None):
        self.发的 = []
        self.回 = 回 or {"code": 0, "msg": "success"}

    def __call__(self, url, payload):
        self.发的.append((url, payload))
        return dict(self.回)


def test_签名用官方那套_待签名串是时间戳换行密钥_消息为空():
    """飞书的 sign 不是"把内容拼进去签"，而是 key=timestamp\\n密钥、内容为空，
    结果 Base64。这一条写错就是 19021 签名校验不通过。"""
    ts, 密钥 = 1700000000, "sec-demo"
    期 = base64.b64encode(hmac.new(f"{ts}\n{密钥}".encode(), b"",
                                   hashlib.sha256).digest()).decode()
    assert fp.sign(ts, 密钥) == 期
    assert fp.sign(ts, 密钥) != fp.sign(ts, "另一个密钥")


def test_配了密钥才带签名字段():
    p = fp.payload("正文", timestamp=1700000000, 密钥="sec-demo")
    assert p["timestamp"] == "1700000000" and p["sign"]
    p2 = fp.payload("正文", timestamp=1700000000, 密钥="")
    assert "sign" not in p2 and "timestamp" not in p2


def test_报文形状是飞书文本消息():
    p = fp.payload("今天投了 30 个", 密钥="")
    assert p == {"msg_type": "text", "content": {"text": "今天投了 30 个"}}


def test_没配webhook就直说缺什么(tmp_path):
    with pytest.raises(RuntimeError) as e:
        fp.load_creds(str(tmp_path / "没有.json"))
    assert "飞书推送未配置" in str(e.value) and "webhook" in str(e.value)


def test_成功与否看飞书回的code不看http(tmp_path):
    creds = {"webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/abc"}
    发 = FakePost({"code": 19021, "msg": "sign match fail"})
    果 = fp.send_text("正文", creds=creds, _post=发, _sleep=lambda s: None)
    assert 果[0]["ok"] is False
    assert 果[0]["code"] == 19021


def test_老格式回包也认(tmp_path):
    """飞书这接口新老两种回包都在用：StatusCode=0 和 code=0"""
    creds = {"webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/abc"}
    果 = fp.send_text("正文", creds=creds, _post=FakePost({"StatusCode": 0}),
                      _sleep=lambda s: None)
    assert 果[0]["ok"] is True


def test_过长切几条且每条之间歇一下():
    """5 次/秒 是硬限，连着甩会被丢；汇报能到几千字，一条 20 KB 也顶不住"""
    creds = {"webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/abc"}
    发 = FakePost()
    睡 = []
    正文 = "\n".join(f"【第{i}条】这一条是一个岗位的汇报内容" for i in range(200))
    果 = fp.send_text(正文, creds=creds, _post=发, _sleep=睡.append)
    assert len(果) > 1 and len(发.发的) == len(果)
    assert len(睡) == len(果) - 1
    for _, p in 发.发的:
        assert len(json.dumps(p, ensure_ascii=False).encode("utf-8")) <= 20 * 1024
    assert "（1/" in 发.发的[0][1]["content"]["text"]
    assert "（2/" in 发.发的[1][1]["content"]["text"]


def test_一条时标题不带序号():
    creds = {"webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/abc"}
    发 = FakePost()
    fp.send_text("短的一行", title="BOSS 汇报", creds=creds, _post=发,
                 _sleep=lambda s: None)
    assert 发.发的[0][1]["content"]["text"].startswith("BOSS 汇报")
    assert "/1" not in 发.发的[0][1]["content"]["text"]


def test_汇报出口优先飞书有了凭据就用它():
    """两条通道都留着：飞书要一个 webhook 就通，微信那条要人在测试号网页上建模板"""
    import tools.wechat_outbox as outbox
    有 = {fp.CREDS_PATH}
    送, 渠道 = outbox.挑出口(有没有=lambda p: p in 有)
    assert 渠道 == "飞书"
    assert 送 is fp.send_text


def test_没配飞书就退回微信那条():
    import tools.wechat_outbox as outbox
    from boss_bot import wechat_push
    送, 渠道 = outbox.挑出口(有没有=lambda p: False)
    assert 渠道 == "微信" and 送 is wechat_push.send_text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
