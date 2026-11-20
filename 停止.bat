@echo off
chcp 65001 >nul
echo 正在停止 镜 MirrorOS（端口 8765）...
powershell -NoProfile -Command "$c = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue; if ($c) { Stop-Process -Id $c.OwningProcess -Force; Write-Host '已停止。' } else { Write-Host '没有在运行。' }"
timeout /t 2 >nul
