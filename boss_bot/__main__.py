"""
BOSS Bot 统一入口

支持以下运行模式：
    python -m boss_bot              # 启动统一主循环（打招呼+回复）
    python -m boss_bot --greet      # 仅启动打招呼引擎
    python -m boss_bot --reply      # 仅启动回复引擎
    python -m boss_bot --web        # 启动 Flask Web 界面
"""

import argparse
import sys


def main():
    """统一入口主函数"""
    parser = argparse.ArgumentParser(description="BOSS 直聘自动化机器人")
    parser.add_argument("--greet", action="store_true", help="仅启动打招呼/投递引擎")
    parser.add_argument("--reply", action="store_true", help="仅启动自动回复引擎")
    parser.add_argument("--web", action="store_true", help="启动 Flask Web 管理界面")
    parser.add_argument("--config", type=str, default=None, help="指定配置文件路径")
    args = parser.parse_args()

    if args.web:
        # 启动 Flask Web 界面
        from flask_version.app import create_app
        app = create_app()
        app.run(host="0.0.0.0", port=5000)
    elif args.greet:
        # 仅打招呼引擎
        from boss_bot.greet_engine import GreetEngine
        engine = GreetEngine()
        engine.run()
    elif args.reply:
        # 仅回复引擎
        from boss_bot.reply_engine import ReplyEngine
        engine = ReplyEngine()
        engine.run()
    else:
        # 默认：统一主循环
        from boss_bot.main_loop import UnifiedMainLoop
        loop = UnifiedMainLoop()
        loop.run()


if __name__ == "__main__":
    main()