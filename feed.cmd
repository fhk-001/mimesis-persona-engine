@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 投喂：把新截图自动变成语料并更新人设。
rem 用法一：把截图文件夹直接拖到本文件上
rem 用法二：双击本文件，按提示粘贴文件夹路径

if "%~1"=="" (
    call "%~dp0run.cmd" feed
) else (
    call "%~dp0run.cmd" feed "%~1"
)
if errorlevel 1 exit /b
echo.
pause
