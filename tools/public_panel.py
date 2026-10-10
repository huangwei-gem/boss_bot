# -*- coding: utf-8 -*-
"""把本机 5000 面板甩到一个公网 https 地址，断了自动重连。

python tools/public_panel.py                      # 默认 cloudflared
python tools/public_panel.py --backend localhost-run
python tools/public_panel.py --check              # 只起一次，拿到地址就退出

地址每次重连都可能换，所以只往 logs/public_url.txt 里写当前那一个；
隧道断了就把文件删掉，免得照着旧链接点开一个 404。

两条路都在这台机器上实测过（2026-10-09 23:00 前后）：
* cloudflared（他点名要的免费 Cloudflare）起不来 —— Clash Verge 现在是系统代理
  而不是虚拟网卡，cloudflared 不读 HTTP(S)_PROXY（带代理跑三次都是
  `Post "https://api.trycloudflare.com/tunnel": context deadline exceeded`，
  2026.7.3 这版也没有 --http-proxy 这类旗标），而 region1.cfargotunnel.com 的
  7844/443 直连 TCP 都不通。在 Clash Verge 里打开「虚拟网卡(TUN)」后这条路就通了。
* localhost.run 走 ssh 22 端口，连得上、也发了真地址，但地址打过去是 nginx 的
  `502 no tunnel`（会话活着的时候连着打了 4 次都一样），所以它现在只能当备用。
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "logs", "public_panel.log")
URL_FILE = os.path.join(ROOT, "logs", "public_url.txt")
本地端口 = 5000
隧道目标 = "nokey@localhost.run"

# 只认带 scheme 的：localhost.run 的横幅里既有裸域名 f30d...lhr.life，也有它自己的
# 文档站 https://localhost.run/docs/，后者当成地址给出去就是假链接
_地址 = re.compile(r"https://[a-z0-9-]+\.(?:lhr\.life|trycloudflare\.com)(?![\w./-])")


def 挑出地址(文本):
    匹 = _地址.search(文本 or "")
    return 匹.group(0) if 匹 else ""


def _cloudflared():
    """cloudflared 在哪：先问 PATH（mac 上 brew 装的就是裸名字），再退回 Windows 装机路径。"""
    找到 = shutil.which("cloudflared")
    if 找到:
        return 找到
    win = shutil.which("cloudflared.exe") or r"C:\Program Files (x86)\cloudflared\cloudflared.exe"
    return win if os.path.isfile(win) else ""


def 隧道命令(端口=本地端口, 后端="cloudflared"):
    if 后端 == "cloudflared":
        程序 = _cloudflared()
        if not 程序:
            raise FileNotFoundError("cloudflared 没装或不在 PATH（mac：brew install cloudflared）")
        return [程序, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{端口}"]
    # BatchMode=yes：无人值守的重连循环里一旦弹密码/确认提示就永远起不来
    return ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
            "-o", "ServerAliveInterval=30", "-o", "ExitOnForwardFailure=yes",
            "-R", f"80:localhost:{端口}", 隧道目标]


def 写地址(文件, 地址):
    """把当前地址写出去；传空串就是撤下。"""
    if 地址:
        with open(文件, "w", encoding="utf-8") as f:
            f.write(地址 + "\n")
    elif os.path.exists(文件):
        os.remove(文件)


def run(后端="cloudflared", once=False, url_file=URL_FILE):
    日志 = open(LOG, "a", encoding="utf-8", buffering=1)
    while True:
        日志.write(f"[{time.strftime('%F %T')}] 起隧道（{后端}）\n")
        try:
            proc = subprocess.Popen(隧道命令(本地端口, 后端), stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True,
                                    encoding="utf-8", errors="replace")
        except (OSError, FileNotFoundError) as e:
            日志.write(f"起不来：{e}\n")
            return 2
        地址 = ""
        for 行 in proc.stdout:
            日志.write(行)
            新 = 挑出地址(行)
            if 新 and 新 != 地址:
                地址 = 新
                写地址(url_file, 地址)
                print(地址, flush=True)
                if once:
                    proc.terminate()
                    return 0
        proc.wait()
        写地址(url_file, "")
        日志.write(f"[{time.strftime('%F %T')}] 隧道断了（exit {proc.returncode}），5 秒后重连\n")
        if once:
            return 1
        time.sleep(5)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="cloudflared", choices=["cloudflared", "localhost-run"])
    ap.add_argument("--check", action="store_true", help="只起一次，拿到地址就退出")
    a = ap.parse_args()
    sys.exit(run(后端=a.backend, once=a.check))
