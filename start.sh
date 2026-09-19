#!/usr/bin/env bash
# Boss直聘智能投递助手 - 一键启动脚本
# 使用方法:
#   1. 赋予执行权限: chmod +x start.sh
#   2. 运行: ./start.sh
set -e

echo "============================================"
echo "  Boss直聘智能投递助手 - 一键启动"
echo "============================================"
echo

cd "$(dirname "$0")"

echo "[1/6] 检查 Python 环境..."
if ! command -v python3 &>/dev/null; then
    echo "✗ 未找到 Python3，请先安装 Python 3.10+"
    echo "  Ubuntu/Debian: sudo apt install python3 python3-pip"
    echo "  macOS: brew install python3"
    exit 1
fi
python3 --version

echo
echo "[2/6] 检查依赖包..."
if ! python3 -c "import flask, flask_socketio, DrissionPage, openai, openpyxl" 2>/dev/null; then
    echo "⚠ 缺少依赖包，正在安装..."
    if ! pip3 install -r requirements.txt; then
        echo "✗ 依赖安装失败，请手动运行: pip3 install -r requirements.txt"
        exit 1
    fi
fi
echo "✓ 依赖包完整"

echo
echo "[3/6] 初始化配置..."
if [ ! -f "bot_config.json" ]; then
    echo "⚠ 首次运行，创建默认配置..."
    python3 -c "from boss_bot.unified_config import UnifiedConfig; UnifiedConfig.load().save()"
fi
if [ ! -f "user_profile.json" ]; then
    echo "⚠ 创建默认用户画像..."
    python3 -c "import json; json.dump({}, open('user_profile.json','w',encoding='utf-8'))"
fi
echo "✓ 配置就绪"

echo
echo "[4/6] 检查端口占用..."
python3 kill_port.py 5000
if [ $? -ne 0 ]; then
    echo "✗ 端口 5000 无法释放，请手动关闭后重试"
    exit 1
fi
echo "✓ 端口 5000 可用"

echo
echo "[5/6] 检查项目结构..."
if [ ! -f "flask-version/app.py" ]; then
    echo "✗ 未找到 flask-version/app.py，项目结构不完整"
    echo "  请确保 flask-version 目录存在且包含 app.py"
    exit 1
fi
echo "✓ 项目结构完整"

echo
echo "[6/6] 启动 Web 界面..."
echo
echo "============================================"
echo "  访问地址: http://localhost:5000"
echo "  按 Ctrl+C 停止服务"
echo "============================================"
echo

# 延迟 3 秒后自动打开浏览器
(
    sleep 3
    if command -v xdg-open &>/dev/null; then
        xdg-open http://localhost:5000
    elif command -v open &>/dev/null; then
        open http://localhost:5000
    elif command -v sensible-browser &>/dev/null; then
        sensible-browser http://localhost:5000
    fi
) &

cd flask-version
python3 app.py
