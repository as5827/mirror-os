@echo off
chcp 65001 >nul
title 镜 · 开启手机访问
net session >nul 2>&1
if %errorLevel% neq 0 (
  echo 需要管理员权限，正在请求提权...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
echo.
echo 正在为 8765 端口添加入站规则（仅「专用/域」网络，公共 WiFi 不放行）...
netsh advfirewall firewall delete rule name="MirrorOS 8765" >nul 2>&1
netsh advfirewall firewall add rule name="MirrorOS 8765" dir=in action=allow protocol=TCP localport=8765 profile=private,domain
echo.
if %errorLevel%==0 (
  echo [OK] 已放行。
  echo.
  echo 手机连同一个 WiFi，浏览器打开下面这个地址：
  for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
    for /f "tokens=* delims= " %%b in ("%%a") do echo      http://%%b:8765
  )
  echo.
  echo 注意：电脑上的「镜」必须保持运行，手机才能访问。
) else (
  echo [X] 添加失败。
)
echo.
pause
