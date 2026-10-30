# 隐藏窗口起面板：没有控制台黑框，标准输出/错误落到 data/panel_*.log（面板被不明终止时就看这两份）。
# 与 start.bat 的分工：start.bat 是"双击给他看进度"的前台壳，这个是他要"静默在后台跑"的那条路。
$ErrorActionPreference = "Stop"
# 不再写死 C:\Users\...\boss_bot：这份脚本会跟着仓库走，按自己所在位置往上找一层。
$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $root "data\panel_stdout.log"
$err = Join-Path $root "data\panel_stderr.log"
$env:PYTHONUTF8 = "1"

# 起之前先问一眼面板口：已经在跑就**绝不**再 Start-Process。
# 原因是 -RedirectStandardOutput 一开就把 panel_stdout.log 截成空的——那个面板还活着、
# 句柄还按旧偏移往下写，日志会立刻变成一片 NUL 加半截新内容；而这份日志正是
# "面板被不明终止"（任务 #190）唯一的事实来源。面板自己有单实例守卫会退出，
# 但守卫拦不住已经发生的截断。
# 端口从 unified_config 那一份取，不在这里写数字：写死了就会"面板听 5001、这里还在问 5000"，
# 于是每次都要多起一个副本才被守卫拦下来，顺带把上面那个日志截断风险又招回来一次。
$面板口 = & python -c "from boss_bot.unified_config import 面板端口; print(面板端口())"
$探 = New-Object System.Net.Sockets.TcpClient
try {
    $探.Connect("127.0.0.1", [int]$面板口)
    $在家 = $true
} catch {
    $在家 = $false
} finally {
    $探.Close()
}

if ($在家) {
    Write-Output ("面板口 " + $面板口 + " 上已经有面板在跑，这次不起第二个、也不碰 data\panel_stdout.log；只把没跑的号补上。")
    & python (Join-Path $root "tools\start_accounts.py")
    exit $LASTEXITCODE
}

New-Item -ItemType Directory -Force -Path (Split-Path $out) | Out-Null
$p = Start-Process -FilePath "python" -ArgumentList "tools/start_accounts.py", "--launch" `
    -WorkingDirectory $root -WindowStyle Hidden `
    -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
# --launch 自己会等面板就绪再起号、回读 /api/status，所以这里不用再补一步等待；
# 它的 stdout 就是这个面板子进程的输出，两份日志合在一起看才是完整现场。
Write-Output ("PID=" + $p.Id + "  日志=" + $out)
