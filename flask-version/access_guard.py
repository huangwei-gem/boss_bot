# -*- coding: utf-8 -*-
"""面板的来源判定：谁能不带凭据就控制这台机器。

原来 app.py 里只看 request.remote_addr 是不是回环。隧道/反向代理（cloudflared、
nginx 都算）永远从 127.0.0.1 连源站，真实访客的地址在 CF-Connecting-IP /
X-Forwarded-For 这类头里 —— 于是"把面板暴露到公网"这一步会把 /api/stop、
/api/cookies/delete 这些也一起敞开。这里把规则写成纯函数，app.py 只负责调用。
"""

# 回环地址的几种写法（IPv6 映射那一种 Flask 也会给出来）
LOCAL_ADDRESSES = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}

# 隧道/反代写真实访客地址的头，按可信度排序
代理头 = ("CF-Connecting-IP", "X-Real-IP", "X-Forwarded-For")

# 手机浏览器上输一次口令之后走 cookie，省得前端每个 fetch 都改一遍
COOKIE名 = "boss_panel_token"


def _取(头, 名):
    """按名字取值，大小写无关。

    WSGI 会把 CF-Connecting-IP 规范成 Cf-Connecting-Ip（environ 里是
    HTTP_CF_CONNECTING_IP，还原时按首字母大写），精确匹配会漏掉隧道头，
    那条洞就等于没堵上。
    """
    小 = str(名).lower()
    for k, v in (头 or {}).items():
        if str(k).lower() == 小:
            return v
    return ""


def 来源(头, 直连IP):
    """返回 (真实来源IP, 是不是经了代理)。

    经没经代理是关键：经了就必须认头里的地址，哪怕那个值写着 127.0.0.1——
    访客自己也能塞一个 X-Forwarded-For: 127.0.0.1 进来洗白。
    """
    for h in 代理头:
        值 = str(_取(头, h) or "").split(",")[0].strip()
        if 值:
            return 值, True
    return (直连IP or ""), False


def 放行(头, 直连IP, token="", 免鉴权=False):
    """这个请求能不能处理。

    - 免鉴权的路径（首页、静态文件：页面本身没有数据）一律放行
    - 本机直连（没经代理、地址是回环）照旧免凭据，日常使用不受影响
    - 其余一律要凭据：X-API-Token 头，或手机浏览器上输过一次之后的 cookie
    - 没设 api_token 时，非本机就是拒绝——想要公网访问，先把 token 设上
    """
    if 免鉴权:
        return True
    真实, 经代理 = 来源(头, 直连IP)
    if not 经代理 and 真实 in LOCAL_ADDRESSES:
        return True
    if not token:
        return False
    if str(_取(头, "X-API-Token") or "") == token:
        return True
    cookie = str(_取(头, "Cookie") or "")
    for 段 in cookie.split(";"):
        名, _, 值 = 段.strip().partition("=")
        if 名 == COOKIE名 and 值 == token:
            return True
    return False
