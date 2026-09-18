@echo off
chcp 65001 >nul
title Boss直聘智能投递助手

echo ============================================
echo   Boss直聘智能投递助手 - 一键启动
echo ============================================
echo.

cd /d "%~dp0"

echo [1/4] 检查 Python 环境...
python --version >nul 2>&1
if errorlevel 1 (
    echo ✗ 未找到 Python，请先安装 Python 3.10+
    pause
    exit /b 1
)
python --version

echo.
echo [2/4] 检查依赖包...
python -c "import flask, flask_socketio, DrissionPage, openai, openpyxl" 2>nul
if errorlevel 1 (
    echo ⚠ 缺少依赖包，正在安装...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo ✗ 依赖安装失败，请手动运行: pip install -r requirements.txt
        pause
        exit /b 1
    )
)
echo ✓ 依赖包完整

echo.
echo [3/4] 初始化配置...
if not exist "bot_config.json" (
    echo ⚠ 首次运行，创建默认配置...
    python -c "from boss_bot.unified_config import UnifiedConfig; UnifiedConfig.load().save()"
)
if not exist "user_profile.json" (
    echo ⚠ 创建默认用户画像...
    python -c "import json; json.dump({}, open('user_profile.json','w',encoding='utf-8'))"
)
echo ✓ 配置就绪

echo.
echo [4/4] 启动 Web 界面...
echo.
echo ============================================
echo   访问地址: http://localhost:5000
echo   按 Ctrl+C 停止服务
echo ============================================
echo.

cd flask-version
python app.py

pause