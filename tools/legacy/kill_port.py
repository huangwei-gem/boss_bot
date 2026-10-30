"""端口占用处理工具 — 强力终止占用指定端口的进程（跨平台）。

用法:
    python kill_port.py <端口>            # 终止占用那个端口的进程
    python kill_port.py <端口> --check    # 仅检查不终止
    python kill_port.py <端口> --find     # 从面板口往上找一个空口

面板默认口住在 boss_bot/unified_config.py 一处（10-28 起是 5001），这里不另写一遍数字。
另外提醒：`start.bat` / `start.sh` 从 10-23 起已经不再调这个脚本——它按端口强杀占用者，
分不清是面板还是别的程序（tests/test_one_click_launcher.py 有一条门盯着那种调用）。
"""

import sys
import os
import time
import socket
import subprocess


def _is_windows() -> bool:
    return os.name == "nt"


def is_port_in_use(port: int) -> bool:
    """检查端口是否被占用（跨平台）。"""
    if _is_windows():
        return _is_port_in_use_windows(port)
    else:
        return _is_port_in_use_unix(port)


def _is_port_in_use_windows(port: int) -> bool:
    """Windows: 使用 netstat 检测。"""
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if "LISTENING" not in line.upper():
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            local_addr = parts[1]
            if local_addr.endswith(f":{port}"):
                return True
    except Exception:
        pass
    return False


def _is_port_in_use_unix(port: int) -> bool:
    """Linux/Mac: 使用 lsof 或 ss 检测。"""
    # 尝试 lsof
    try:
        result = subprocess.run(
            ["lsof", "-i", f":{port}", "-sTCP:LISTEN"],
            capture_output=True, text=True, timeout=10
        )
        if result.stdout.strip():
            return True
    except Exception:
        pass
    # 尝试 ss
    try:
        result = subprocess.run(
            ["ss", "-tlnp"],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.splitlines():
            if f":{port} " in line:
                return True
    except Exception:
        pass
    return False


def get_pids_on_port(port: int) -> list:
    """获取占用指定端口的所有进程 PID（去重）。"""
    if _is_windows():
        return _get_pids_windows(port)
    else:
        return _get_pids_unix(port)


def _get_pids_windows(port: int) -> list:
    """Windows: netstat -ano 获取 PID。"""
    pids = set()
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if "LISTENING" not in line.upper():
                continue
            parts = line.split()
            if len(parts) < 5:
                continue
            local_addr = parts[1]
            if local_addr.endswith(f":{port}"):
                try:
                    pid = int(parts[4])
                    if pid > 0:
                        pids.add(pid)
                except ValueError:
                    pass
    except Exception:
        pass
    return sorted(pids)


def _get_pids_unix(port: int) -> list:
    """Linux/Mac: lsof 或 ss 获取 PID。"""
    pids = set()
    # 尝试 lsof
    try:
        result = subprocess.run(
            ["lsof", "-i", f":{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.isdigit():
                pids.add(int(line))
    except Exception:
        pass
    # 尝试 ss
    if not pids:
        try:
            result = subprocess.run(
                ["ss", "-tlnp"],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.splitlines():
                if f":{port} " in line:
                    # 提取 pid=NNN
                    import re
                    m = re.search(r"pid=(\d+)", line)
                    if m:
                        pids.add(int(m.group(1)))
        except Exception:
            pass
    return sorted(pids)


def kill_pid(pid: int) -> bool:
    """终止指定 PID 的进程及其子进程树。"""
    try:
        if _is_windows():
            # /F 强制终止, /T 终止进程树
            result = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, text=True, timeout=10
            )
            return result.returncode == 0
        else:
            # Unix: kill -9 终止进程
            os.kill(pid, 9)
            return True
    except Exception:
        return False


def free_port(port: int, max_retries: int = 3, wait_seconds: int = 3) -> bool:
    """尝试释放指定端口，支持多次重试。

    返回 True 表示端口已释放（或本来就没被占用）。
    """
    if not is_port_in_use(port):
        return True

    print(f"⚠ 端口 {port} 已被占用，正在自动终止旧进程...")

    for attempt in range(1, max_retries + 1):
        pids = get_pids_on_port(port)
        if not pids:
            # 没有找到监听进程，但端口仍被占用 — 可能是 TIME_WAIT 状态
            print(f"  第 {attempt} 次: 未找到监听进程，等待端口释放...")
        else:
            for pid in pids:
                success = kill_pid(pid)
                status = "已终止" if success else "终止失败"
                print(f"  第 {attempt} 次: {status} 进程 PID: {pid}")

        # 等待端口释放
        time.sleep(wait_seconds)

        if not is_port_in_use(port):
            print(f"✓ 旧进程已终止，端口 {port} 已释放")
            return True

    print(f"✗ 端口 {port} 仍被占用（已重试 {max_retries} 次）")
    return False


def find_free_port(start: int = None, end: int = None) -> int:
    """在指定范围内查找可用端口；不传就从面板那个口往上找 20 个。

    起点用 unified_config 那一份，别在这儿写死数字——写死了就出现"面板听 5001、
    找空口从 5000 起"，找回来的第一个口正好是别的项目占着的那个。
    0 是合法端口参数，所以判"没传"只认 None，不用 `or`。
    """
    if start is None or end is None:
        from boss_bot.unified_config import 面板端口
        起 = 面板端口()
        if start is None:
            start = 起
        if end is None:
            end = 起 + 20
    for port in range(start, end + 1):
        if not is_port_in_use(port):
            return port
    return None


def main():
    if len(sys.argv) < 2:
        print("用法: python kill_port.py <端口号> [--check] [--find]")
        sys.exit(1)

    port = int(sys.argv[1])

    if "--check" in sys.argv:
        # 仅检查
        in_use = is_port_in_use(port)
        if in_use:
            pids = get_pids_on_port(port)
            print(f"端口 {port} 被占用，PID: {pids}")
            sys.exit(1)
        else:
            print(f"端口 {port} 可用")
            sys.exit(0)

    if "--find" in sys.argv:
        # 查找可用端口
        free = find_free_port(port)
        if free:
            print(free)
            sys.exit(0)
        else:
            print(f"未找到可用端口 ({port}-{port+20})")
            sys.exit(1)

    # 默认: 尝试释放端口
    success = free_port(port)
    if success:
        sys.exit(0)
    else:
        # 端口无法释放，尝试找替代端口
        alt = find_free_port(port + 1, port + 20)
        if alt:
            print(f"→ 使用替代端口: {alt}")
            # 输出到文件供调用方读取
            with open(".alt_port", "w") as f:
                f.write(str(alt))
            sys.exit(0)
        else:
            print(f"✗ 无法释放端口 {port}，也未找到替代端口")
            sys.exit(1)


if __name__ == "__main__":
    main()
