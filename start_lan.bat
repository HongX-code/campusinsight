@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo.
echo   高校智讯 CampusInsight - 局域网一键启动（手机 / 平板 / 鸿蒙）
echo   ------------------------------------------------------------
echo.

set "PYEXE="
py -3.12 -c "import fastapi" >nul 2>&1
if %errorlevel%==0 set "PYEXE=py -3.12"
if defined PYEXE goto :found
python -c "import fastapi" >nul 2>&1
if %errorlevel%==0 set "PYEXE=python"

:found
if not defined PYEXE (
  echo   [错误] 没有找到装好依赖的 Python 3.10+ 解释器。
  echo   请先执行：py -3.12 -m pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)

echo   解释器：%PYEXE%
echo.
echo   手机与电脑需连接同一 WiFi；Windows 防火墙需放行 8000 端口。
echo   启动后请复制屏幕上打印的 http://192.168.x.x:8000/ 地址到手机浏览器。
echo   注意：0.0.0.0 只是监听地址，不能填进浏览器。
echo.
%PYEXE% run.py --lan --open
echo.
echo   服务已停止。
pause
