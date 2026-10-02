@echo off
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$p = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -match 'monitor\.py' }; if ($p) { $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Write-Host 'Monitor stopped.' } else { Write-Host 'Monitor is not running.' }"
pause
