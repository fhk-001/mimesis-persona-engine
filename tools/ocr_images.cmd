@echo off
chcp 65001 >nul

rem 把微信聊天截图转成文字。
rem 用法：把图片放进一个文件夹，双击本文件，把文件夹拖进来回车即可。

if "%~1"=="" (
    echo.
    echo 把微信聊天截图放到一个文件夹里，然后把文件夹拖到这个窗口，回车。
    echo.
    set /p FOLDER=图片文件夹路径：
) else (
    set "FOLDER=%~1"
)

if "%FOLDER%"=="" (
    echo 没有输入路径，已退出。
    pause
    exit /b 1
)

set "MODE=0"
if "%~2"=="" (
    echo.
    echo 截图是哪种？
    echo   1 = 手机截图（带状态栏、标题栏、输入框。推荐）
    echo   2 = 电脑截图（已经自己裁好，或者识别结果里没有多余界面）
    set /p MODE=输入 1 或 2，直接回车 = 1：
)

set "EXTRA="
if "%MODE%"=="0" (
    set EXTRA=%2 %3 %4 %5 %6 %7 %8 %9
) else (
    if not "%MODE%"=="2" set "EXTRA=-PhoneLayout"
)

echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0ocr_chat_images.ps1" -Folder "%FOLDER%" %EXTRA%
echo.
pause
