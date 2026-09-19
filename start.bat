@echo off
chcp 65001 >nul
title Boss直聘智能投递助手

echo ============================================
echo   Boss直聘智能投递助手 - 一键启动
echo ============================================
echo.

cd /d "%~dp0"

echo [1/6] 检查 Python 环境...
python --version >nul 2>&1
if errorlevel 1 (
    echo ✗ 未找到 Python，请先安装 Python 3.10+
    echo   下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)
python --version

echo.
echo [2/6] 检查依赖包...
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
echo [3/6] 初始化配置...
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
echo [4/6] 检查端口占用...
netstat -ano | findstr ":5000 " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo ⚠ 端口 5000 已被占用，正在自动终止旧进程...
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000 " ^| findstr "LISTENING"') do (
        taskkill /F /PID %%a >nul 2>&1
        echo   已终止进程 PID: %%a
    )
    timeout /t 2 /nobreak >nul
    netstat -ano | findstr ":5000 " | findstr "LISTENING" >nul 2>&1
    if not errorlevel 1 (
        echo ✗ 端口仍被占用，无法自动终止，请手动关闭后重试
        pause
        exit /b 1
    )
    echo ✓ 旧进程已终止，端口已释放
)
echo ✓ 端口 5000 可用

echo.
echo [5/6] 检查项目结构...
if not exist "flask-version\app.py" (
    echo ✗ 未找到 flask-version\app.py，项目结构不完整
    echo   请确保 flask-version 目录存在且包含 app.py
    pause
    exit /b 1
)
echo ✓ 项目结构完整

echo.
echo [6/6] 启动 Web 界面...
echo.
echo ============================================
echo   访问地址: http://localhost:5000
echo   按 Ctrl+C 停止服务
echo ============================================
echo.

REM 延迟3秒后自动打开浏览器（使用ping模拟延迟，兼容性更好）
start "" cmd /c "ping -n 4 127.0.0.1 >nul & start http://localhost:5000"

cd flask-version
python app.py

if errorlevel 1 (
    echo.
    echo ✗ 应用启动失败，请检查错误信息
    pause
)

pause
