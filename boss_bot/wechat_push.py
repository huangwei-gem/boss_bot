# -*- coding: utf-8 -*-
"""把汇报推到微信：走微信公众平台「测试号」的模板消息接口。

为什么是这条通道（2026-10-09 把能用的路子都查了一遍、逐个实测过）：

* 个人微信客户端没有对外接口。wcferry 那一类要往微信进程里注 DLL，而且只支持
  3.9.x 老客户端（本机装的是 4.0 的 Weixin.exe，官方仓库明确写着不支持 4.0）；
  wxautox4 靠读界面控件，客户端一升级就断，还要作者那边发授权。
* 公众号测试号是官方接口：不碰微信客户端、不碰微信账号，零封号风险，模板消息
  每日上限 10 万次。代价是只能单向推给关注了这个测试号的人 —— 而"把汇报发给
  我自己看"正好就是单向。
* 同一个测试号上另两条能发消息的路实测都堵着：客服接口 message/custom/send 回
  45015（要求用户 48 小时内先给公众号发过话，无人值守撑不住），群发接口
  message/mass/send 回 48001（未认证不开放）。所以长期能自动跑的只有模板消息。

凭据放在 data/wechat_push.json（data/ 已在 .gitignore 里，不进仓库）：
    {"appid": "...", "secret": "...", "openid": "...", "template_id": "..."}

template_id 只能在测试号网页上点「新增测试模板」拿到：接口侧 api_add_tpl 对测试号
返回 40066，正式的 api_add_template 又要求先设行业（返回 40102/48011）。这一步必须
人在浏览器里做一次，做完就一劳永逸——之后推送不再需要登录测试号后台。
"""
import json
import os
import time
import urllib.request
import urllib.error

CREDS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "wechat_push.json")
TOKEN_CACHE = os.path.join(os.path.dirname(CREDS_PATH), "wechat_token.json")
API = "https://api.weixin.qq.com/cgi-bin"

# 一条模板消息正文的上限（字）。微信没写死这个数，实测过长会被整条丢掉，
# 而汇报本身按分组能到几千字，所以宁可切几条也不能赌它不截。
# 500 字是留了余量的取法：一条手机上一屏能看完，切完 6 条也就是一屏多。
CHUNK_CHARS = 500


def load_creds(path=CREDS_PATH):
    """读凭据；缺哪个就说缺哪个，不要抛 KeyError 让人猜。"""
    if not os.path.exists(path):
        raise RuntimeError(f"微信推送未配置：找不到 {path}")
    with open(path, encoding="utf-8") as f:
        creds = json.load(f) or {}
    缺 = [k for k in ("appid", "secret", "openid", "template_id") if not creds.get(k)]
    if 缺:
        raise RuntimeError("微信推送未配置完整，还缺：" + "、".join(缺)
                           + ("（template_id 要在测试号网页上「新增测试模板」里拿）"
                              if "template_id" in 缺 else ""))
    return creds


def get_token(appid, secret, cache_path=TOKEN_CACHE, now=None):
    """取 access_token，命中缓存就不重复要。

    测试号的 token 每日上限 2000 次、有效 7200 秒。每次发送都新取一个，
    一是慢，二是会把上限烧在取 token 上；提前 5 分钟过期是为了避开
    "发的时候刚好过期"这种半路失败。
    """
    now = time.time() if now is None else now
    try:
        with open(cache_path, encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("token") and cached.get("expires_at", 0) > now + 300:
            return cached["token"]
    except Exception:
        pass
    url = (f"{API}/token?grant_type=client_credential"
           f"&appid={appid}&secret={secret}")
    data = _get(url)
    if "access_token" not in data:
        raise RuntimeError(f"取 access_token 失败：{data}")
    token = data["access_token"]
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"token": token,
                       "expires_at": now + int(data.get("expires_in", 7200))}, f)
    except Exception:
        pass
    return token


def _get(url):
    with urllib.request.urlopen(url, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post(url, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"errcode": e.code, "errmsg": e.read().decode("utf-8", "replace")}


def chunk_text(text, limit=CHUNK_CHARS):
    """按行切块，每块不超过 limit 字；单行超长再硬切。

    先按行切是为了保住汇报的分块结构（【面试邀请】这种标题不会被劈成两半）。
    """
    lines = [ln for ln in str(text or "").splitlines()]
    out, cur = [], ""
    for ln in lines:
        if len(ln) > limit:
            if cur:
                out.append(cur)
                cur = ""
            for i in range(0, len(ln), limit):
                piece = ln[i:i + limit]
                if len(piece) == limit:
                    out.append(piece)
                else:
                    cur = piece
            continue
        if cur and len(cur) + len(ln) + 1 > limit:
            out.append(cur)
            cur = ln
        else:
            cur = (cur + "\n" + ln) if cur else ln
    if cur:
        out.append(cur)
    return [c for c in out if c.strip()]


def send_text(text, title="BOSS 汇报", creds=None):
    """把一段文本推过去，过长自动切几条。返回每条的结果列表。"""
    creds = creds or load_creds()
    token = get_token(creds["appid"], creds["secret"])
    parts = chunk_text(text)
    results = []
    for idx, part in enumerate(parts, 1):
        头 = title if len(parts) == 1 else f"{title}（{idx}/{len(parts)}）"
        resp = _post(f"{API}/message/template/send?access_token={token}", {
            "touser": creds["openid"],
            "template_id": creds["template_id"],
            "data": {"first": {"value": 头}, "remark": {"value": part}},
        })
        results.append({"part": idx, "ok": resp.get("errcode") == 0,
                        "errcode": resp.get("errcode"), "errmsg": resp.get("errmsg"),
                        "msgid": resp.get("msgid")})
        # 微信侧对同一用户的模板消息有频率保护，连着甩容易整条丢
        if idx < len(parts):
            time.sleep(1.0)
    return results


def selftest():
    """发一条自检消息，把微信回的原话带回来。"""
    return send_text("链路自检：这条到了就说明 BOSS 汇报能推到你微信了。",
                     title="BOSS 机器人")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="微信推送（公众号测试号模板消息）")
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
