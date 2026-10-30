# -*- coding: utf-8 -*-
"""面板的来源判定：谁能不带凭据就控制这台机器。

原来 app.py 里只看 request.remote_addr 是不是回环。隧道/反向代理（cloudflared、
nginx 都算）永远从 127.0.0.1 连源站，真实访客的地址在 CF-Connecting-IP /
X-Forwarded-For 这类头里 —— 于是"把面板暴露到公网"这一步会把 /api/stop、
/api/cookies/delete 这些也一起敞开。这里把规则写成纯函数，app.py 只负责调用。
"""

import json

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


# ===================== 这台机器上是不是已经有一个面板 =====================

面板主机 = "127.0.0.1"
# 面板用哪个口不在这里写死——住在 boss_bot/unified_config.py 一处（10-28 起是 5001，
# 因为这台机器上 5000 长期被另一个项目的 app.py 占着，面板一起来就绑不上，
# "看一眼前端统计"变成"得先去关别的程序"）。这里只把那个数连同 BOSS_PANEL_PORT
# 的覆盖取过来，给 app.py 和单测用；启动器与实测脚本读的是同一份。
# 自己把路径补上：app.py 有 sys.path 处理，但单测/脚本可能只把 flask-version 加进路径。
import sys as _sys
from pathlib import Path as _Path

_仓库根 = str(_Path(__file__).resolve().parents[1])
if _仓库根 not in _sys.path:
    _sys.path.insert(0, _仓库根)
from boss_bot.unified_config import 面板端口 as _面板端口  # noqa: E402

默认面板端口 = _面板端口()
# 这个口一定要在"读数据"的接口上：/ 会返回 index.html，任何 web 服务都返 200，
# 光看"有没有人应答"就把对面认领成面板，等于把端口上别的服务当成第二个副本、
# 于是永远起不来（而真面板可能压根没跑）。accounts 这个键只有本面板有。
面板探路 = "/api/status"


def 已有面板在跑(取, 主机=面板主机, 端口=默认面板端口):
    """探测本机端口，返回 (是不是面板在跑, 一句人话)。

    探测动作由调用方给（app.py 用 urllib，单测用假函数），这样判据能测、
    启动路径也能测，而且不会为了自检真去占一个端口。

    三档必须分开，它们的处置完全不同：
    - 连不上：没人跑，正常起。
    - 应答了、回音里有 accounts：就是面板，第二个副本必须退出——两套
      MultiAccountManager 会把同一个岗位点两遍、记录写两份、浏览器树各 ~400 MB。
    - 应答了但看不出是面板：别关机也别认领，只说"端口被别的东西占着"，
      起不起得来交给 socketio 的绑定错误去判。
    带 token 的面板对无凭据的请求回 401，那也是面板在跑（应答的就是它）。
    """
    谁 = "%s:%s" % (主机, 端口)
    try:
        状态, 正文 = 取("http://%s%s" % (谁, 面板探路))
    except Exception:
        return False, ""

    if 状态 in (401, 403):
        return True, ("%s 上已经有一个面板在跑（%s，读内容要凭据，但应答的就是它）" % (谁, 状态))
    if 状态 != 200:
        return False, ("%s 上有东西应答了 HTTP %s，不像面板" % (谁, 状态))

    try:
        数据 = json.loads(正文 or "")
    except Exception:
        数据 = None
    if isinstance(数据, dict) and "accounts" in 数据:
        return True, ("%s 上已经有一个面板在跑（/api/status 回音里有 accounts）" % 谁)
    return False, ("%s 被别的东西占着：应答 200 但回音里没有面板的 accounts 字段" % 谁)
