#!/usr/bin/env bash
set -e

echo "============================================"
echo "  Boss直聘智能投递助手 - 一键启动"
echo "============================================"
echo

cd "$(dirname "$0")"

echo "[1/4] 检查 Python 环境..."
if ! command -v python3 &>/dev/null; then
    echo "✗ 未找到 Python3，请先安装 Python 3.10+"
    exit 1
fi
python3 --version

echo
echo "[2/4] 检查依赖包..."
if ! python3 -c "import flask, flask_socketio, DrissionPage, openai, openpyxl" 2>/dev/null; then
    echo "⚠ 缺少依赖包，正在安装..."
    pip3 install -r requirements.txt
fi
echo "✓ 依赖包完整"

echo
echo "[3/4] 初始化配置..."
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
echo "[4/4] 启动 Web 界面..."
echo
echo "============================================"
echo "  访问地址: http://localhost:5000"
echo "  按 Ctrl+C 停止服务"
echo "============================================"
echo

cd flask-version
python3 app.py