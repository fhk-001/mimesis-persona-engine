@echo off
chcp 65001 >nul
setlocal

rem 双击运行会自动找 Python，并把参数转给 run.py
rem 例：run.cmd selftest
rem     run.cmd chat --persona personas/demo-linxiaoman --mock --fast
rem     run.cmd profile --chat "D:\聊天记录.txt" --me 张三

set "PYEXE="
if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE for /f "delims=" %%i in ('where python 2^>nul') do if not defined PYEXE set "PYEXE=%%i"
if not defined PYEXE for /f "delims=" %%i in ('where py 2^>nul') do if not defined PYEXE set "PYEXE=%%i"

if not defined PYEXE (
    echo.
    echo 没有找到 Python，请先安装 Python 3.9 或更高版本。
    echo.
    pause
    exit /b 1
)

if "%~1"=="" (
    echo 当前使用的 Python：%PYEXE%
    echo.
    "%PYEXE%" "%~dp0run.py" --help
    echo.
    echo 常用命令：
    echo    run.cmd selftest
    echo    run.cmd chat --persona personas/demo-linxiaoman --mock --fast
    echo    run.cmd profile --chat "聊天记录路径" --me 昵称
    echo.
    pause
    exit /b 0
)

"%PYEXE%" "%~dp0run.py" %*
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
    echo.
    echo 命令执行失败，退出码 %CODE%
    pause
)
exit /b %CODE%
