@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 双击后在项目目录里打开一个命令行窗口，方便自己敲 git 命令。
rem 顺带把这个文件夹加进 git 的信任名单——否则手动敲 git 会因为
rem 「仓库属于别的账号」被拒绝（脚本里是用 -c 参数绕过的，手动敲没有）。
rem 注意：不要在 if 代码块里写半角右括号，那会把代码块提前闭合。

echo ============================================================
echo 项目目录：%CD%
echo ============================================================
echo.

set "GITEXE="
git --version >nul 2>&1
if not errorlevel 1 set "GITEXE=git"
if not defined GITEXE if exist "D:\APP\Git\cmd\git.exe" set "GITEXE=D:\APP\Git\cmd\git.exe"
if not defined GITEXE if exist "C:\Program Files\Git\cmd\git.exe" set "GITEXE=C:\Program Files\Git\cmd\git.exe"
if not defined GITEXE if exist "C:\Program Files (x86)\Git\cmd\git.exe" set "GITEXE=C:\Program Files (x86)\Git\cmd\git.exe"
if not defined GITEXE if exist "%LOCALAPPDATA%\Programs\Git\cmd\git.exe" set "GITEXE=%LOCALAPPDATA%\Programs\Git\cmd\git.exe"
if not defined GITEXE goto nogit

echo git：%GITEXE%
"%GITEXE%" --version

rem 已经配过就不重复加
set "TRUSTED="
for /f "delims=" %%i in ('"%GITEXE%" config --global --get-all safe.directory 2^>nul') do if /i "%%i"=="%CD%" set "TRUSTED=1"
if defined TRUSTED goto trusted

echo.
echo 正在把这个文件夹加入 git 信任名单（只影响这一个文件夹）……
"%GITEXE%" config --global --add safe.directory "%CD%"
if errorlevel 1 goto trustfail
echo 完成。
goto ready

:trustfail
echo.
echo 没能写入信任名单。手动敲 git 可能会报 dubious ownership。
echo 那就每次自己带上这句：
echo    git -c "safe.directory=%CD%" status
goto ready

:trusted
echo 这个文件夹已经在 git 信任名单里。

:ready
echo.
echo ------------------------------------------------------------
echo 接下来会打开一个命令行窗口，里面可以直接敲 git 命令。
echo 常用四条（按顺序）：
echo.
echo    git status                 看看有哪些改动
echo    git add -A                 把改动放进待提交
echo    git commit -m "改了啥"      提交
echo    git push                   推到 GitHub
echo ------------------------------------------------------------
echo.
echo 按任意键打开命令行窗口……
pause >nul
start "" powershell -NoExit -Command "Set-Location -LiteralPath '%CD%'"
exit /b 0

:nogit
echo.
echo 找不到 git.exe。先确认 git 装好了（重启一次电脑再试）。
pause
exit /b 1
