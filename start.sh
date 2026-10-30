#!/usr/bin/env bash
# Boss直聘智能投递助手 - 一键启动
# 使用方法:  ./start.sh        （首次先 chmod +x start.sh）
#
# 判断和提示全部在 tools/start_accounts.py 里（--launch），这里只负责找到 Python。
# 为什么不在 shell 脚本里写流程：Windows 那端 start.bat 必须保持纯 ASCII
# （cmd 读带中文的 UTF-8 批处理会走偏，10-23 实测过），逻辑放一份 Python 里
# 两端才是同一套口径，不会出现"mac 上能用、Windows 上少了那一步"。
set -e
cd "$(dirname "$0")"
export PYTHONUTF8=1     # 子进程打印中文用 UTF-8，终端 locale 是 C 时也不会炸

PY=python3
command -v python3 &>/dev/null || PY=python
if ! command -v "$PY" &>/dev/null; then
    echo "✗ 未找到 Python3，请先安装 Python 3.10+"
    echo "  Ubuntu/Debian: sudo apt install python3 python3-pip"
    echo "  macOS: brew install python3"
    exit 1
fi

# 这里也绝不 kill_port：它会强杀占用面板口的那个进程；面板自己有单实例守卫，
# 起号那一步只判断端口上有没有人应答。
exec "$PY" tools/start_accounts.py --launch
