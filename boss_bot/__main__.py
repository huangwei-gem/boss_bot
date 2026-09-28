"""
BOSS Bot 统一入口

支持以下运行模式：
    python -m boss_bot              # 多账号统一主循环（打招呼+回复），Ctrl+C 停止
    python -m boss_bot --greet      # 仅运行打招呼/投递引擎
    python -m boss_bot --reply      # 仅运行自动回复引擎
    python -m boss_bot --web        # 启动 Flask 管理界面（http://localhost:5000）
    python -m boss_bot --account 1  # 仅运行指定账号
"""

import argparse
import os
import sys


def main():
    """统一入口主函数

    注意：命令行开关通过环境变量下传给 UnifiedConfig，因为主循环每轮都会
    重新 load() 配置；只改内存对象会在第一次热重载时被配置文件覆盖。
    因此这些设置必须发生在导入 boss_bot 子模块之前（config.py 在导入时
    就会 load 一次配置）。
    """
    parser = argparse.ArgumentParser(description="BOSS 直聘自动化机器人")
    parser.add_argument("--greet", action="store_true", help="仅运行打招呼/投递引擎")
    parser.add_argument("--reply", action="store_true", help="仅运行自动回复引擎")
    parser.add_argument("--web", action="store_true", help="启动 Flask 管理界面")
    parser.add_argument("--account", type=int, default=None, help="仅运行指定账号索引（0 起）")
    parser.add_argument("--headless", action="store_true", help="浏览器无头模式")
    parser.add_argument("--dry-run", action="store_true",
                        help="演练模式：照常搜索/AI判分/生成回复，但不真的点发送")
    parser.add_argument("--config", type=str, default=None, help="指定 bot_config.json 路径")
    args = parser.parse_args()

    if args.greet and args.reply:
        parser.error("--greet 与 --reply 互斥，同时指定等于两个都开")

    if args.config:
        os.environ["BOSS_BOT_CONFIG"] = args.config
    if args.headless:
        os.environ["BOSS_BOT_HEADLESS"] = "1"
    if args.dry_run:
        os.environ["BOSS_BOT_DRY_RUN"] = "1"
    if args.greet:
        os.environ["BOSS_BOT_ONLY_ENGINE"] = "greet"
    elif args.reply:
        os.environ["BOSS_BOT_ONLY_ENGINE"] = "reply"

    if args.web:
        _run_web()
        return

    _run_bot(account=args.account)


def _run_web():
    """启动 Flask 管理界面。

    目录名 flask-version 带连字符，无法作为包导入，只能用 runpy 按文件执行。
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app_py = os.path.join(root, "flask-version", "app.py")
    if not os.path.exists(app_py):
        print(f"未找到 {app_py}", file=sys.stderr)
        sys.exit(1)
    import runpy
    sys.argv = [app_py]
    runpy.run_path(app_py, run_name="__main__")


def _run_bot(account=None):
    """命令行运行机器人（阻塞至 Ctrl+C）。"""
    import time
    from boss_bot.main_loop import MultiAccountManager
    from boss_bot.unified_config import UnifiedConfig

    cfg = UnifiedConfig.load()
    manager = MultiAccountManager(config=cfg, log_callback=print)

    if account is not None and account not in manager._loops:
        print(f"账号 {account} 不存在或未启用，可用: {sorted(manager._loops)}")
        sys.exit(1)

    enabled = [name for name, on in (("打招呼", cfg.greet.enabled), ("回复", cfg.reply.enabled)) if on]
    mode = " + ".join(enabled) if enabled else "无（打招呼与回复都被关掉了）"

    print("=" * 52)
    print("  BOSS 直聘自动化机器人（命令行模式）"
          + ("  [演练模式]" if cfg.dry_run else ""))
    print(f"  账号: {len(manager._loops)} 个 | 启用: {mode}")
    print(f"  每日上限: {cfg.greet.rate_limit.max_per_day} | AI 筛岗: {'开' if cfg.ai.enabled else '关'}")
    print("  Ctrl+C 停止")
    print("=" * 52)

    if account is None:
        manager.start()
    else:
        manager.start_account(account)
    try:
        while True:
            time.sleep(30)
    except KeyboardInterrupt:
        print("\n收到中断信号，正在停止…")
    finally:
        manager.stop()
        print("已停止")


if __name__ == "__main__":
    main()
