$ErrorActionPreference = "Stop"
$root = "C:\Users\35796\orca\boss_bot"
$out = Join-Path $root "data\panel_stdout.log"
$err = Join-Path $root "data\panel_stderr.log"
$env:PYTHONUTF8 = "1"
$p = Start-Process -FilePath "python" -ArgumentList "flask-version/app.py" `
  -WorkingDirectory $root -WindowStyle Hidden `
  -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
Write-Output ("PID=" + $p.Id)
