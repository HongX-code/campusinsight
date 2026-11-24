@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo.
echo   高校智讯 CampusInsight - 本地一键启动
echo   ----------------------------------------
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
  echo   或查看：  python scripts/codearts_tools.py doctor
  echo.
  pause
  exit /b 1
)

echo   解释器：%PYEXE%
%PYEXE% run.py --check
if errorlevel 1 (
  echo.
  echo   [体检未通过] 请按上面 FAIL 项处理后重试。
  pause
  exit /b 1
)

echo.
echo   即将启动并自动打开浏览器；按 Ctrl+C 或关闭本窗口即停止服务。
echo   浏览器地址固定为 http://127.0.0.1:8000/ ，请勿访问 0.0.0.0。
echo.
%PYEXE% run.py --open
echo.
echo   服务已停止。
pause
