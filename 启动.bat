@echo off
rem 备用启动方式（推荐用桌面快捷方式：直接指向 python.exe，不经过 shell）
rem 本文件必须保存为 CRLF 换行，否则 CMD 会吞掉字符。
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set MIRROR_CONSOLE=1

rem 依次尝试：项目内虚拟环境 -> py 启动器 -> PATH 里的 python
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m app.server
  goto :end
)
where py >nul 2>&1
if %errorlevel%==0 (
  py -3 -m app.server
  goto :end
)
where python >nul 2>&1
if %errorlevel%==0 (
  python -m app.server
  goto :end
)
echo.
echo [X] 找不到 Python。请先安装 Python 3.9+ 并勾选 Add to PATH。
echo     下载: https://www.python.org/downloads/
echo.
pause

:end
