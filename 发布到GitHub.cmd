@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 把本地仓库推送到 GitHub。双击运行即可。
rem 说明：脚本会自己去找 git.exe —— 双击打开的窗口有时读不到最新的环境变量，
rem 光靠 PATH 找 git 会误报"不是 git 仓库"。找不到 git 和不是仓库是两回事，这里分开报。
rem 注意：不要在 if 代码块里写半角右括号，那会把代码块提前闭合、导致脚本直接退出。

echo ============================================================
echo 当前文件夹：%CD%
echo ============================================================

rem ---------- 第 1 步：定位 git ----------
set "GITEXE="
git --version >nul 2>&1
if not errorlevel 1 set "GITEXE=git"

if not defined GITEXE if exist "D:\APP\Git\cmd\git.exe" set "GITEXE=D:\APP\Git\cmd\git.exe"
if not defined GITEXE if exist "C:\Program Files\Git\cmd\git.exe" set "GITEXE=C:\Program Files\Git\cmd\git.exe"
if not defined GITEXE if exist "C:\Program Files (x86)\Git\cmd\git.exe" set "GITEXE=C:\Program Files (x86)\Git\cmd\git.exe"
if not defined GITEXE if exist "%LOCALAPPDATA%\Programs\Git\cmd\git.exe" set "GITEXE=%LOCALAPPDATA%\Programs\Git\cmd\git.exe"

if not defined GITEXE goto nogit

echo git 位置：%GITEXE%
"%GITEXE%" --version

rem ---------- 第 2 步：确认这里是不是仓库 ----------
rem 带上 safe.directory：这个文件夹的所有者可能不是当前登录账号
rem （比如由别的工具或账号创建），git 默认会因此拒绝操作。
rem 这样写只影响本脚本这次的调用，不动你的任何全局配置。
set GITSAFE=-c "safe.directory=%CD%"

echo.
echo ============================================================
echo 第 1 步：检查这个文件夹是不是 git 仓库
echo ============================================================
"%GITEXE%" %GITSAFE% rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 goto noproj

echo 是 git 仓库，本地提交如下：
"%GITEXE%" %GITSAFE% log --oneline -3

rem ---------- 第 3 步：确认远程地址 ----------
echo.
echo ============================================================
echo 第 2 步：检查有没有配置远程仓库地址
echo ============================================================
"%GITEXE%" %GITSAFE% remote get-url origin >nul 2>&1
if errorlevel 1 goto askurl

echo 当前远程地址：
"%GITEXE%" %GITSAFE% remote get-url origin
goto push

:askurl
echo 还没有设置远程地址。
echo.
echo 请先在浏览器打开 https://github.com/new 建一个"空"仓库
echo 建好后把仓库地址复制过来，形如 https://github.com/用户名/仓库名.git
echo.
set /p REPOURL=粘贴仓库地址后按回车: 
if "%REPOURL%"=="" goto emptyurl
"%GITEXE%" %GITSAFE% remote add origin "%REPOURL%"
if errorlevel 1 goto addfail
echo 已设置远程地址：%REPOURL%

rem ---------- 第 4 步：推送 ----------
:push
echo.
echo ============================================================
echo 第 3 步：推送到 GitHub（这一步会要求你登录）
echo ============================================================
"%GITEXE%" %GITSAFE% push -u origin main
if errorlevel 1 goto pushfail

echo.
echo ------------------------------------------------------------
echo 推送成功！打开你的 GitHub 仓库页面就能看到了。
echo ------------------------------------------------------------
pause
exit /b 0

:nogit
echo.
echo ------------------------------------------------------------
echo 找不到 git.exe。
echo 通常是因为 git 刚装完、环境变量还没刷新。
echo 办法：注销一次或重启电脑，然后再双击本脚本。
echo 如果重启后还是这样，把上面这几行发给我。
echo ------------------------------------------------------------
pause
exit /b 1

:noproj
echo.
echo ------------------------------------------------------------
echo 这个文件夹里没有 git 仓库（找不到 .git 目录）。
echo 当前文件夹是：%CD%
echo.
echo --- 下面是 git 的原话，方便定位 ---
"%GITEXE%" %GITSAFE% rev-parse --is-inside-work-tree
echo ------------------------------------
echo.
echo 请在文件资源管理器里翻到项目文件夹，确认那里确实有一个 .git 文件夹。
echo 如果当前文件夹不是上面的路径，说明你打开的是别的副本。
echo 把上面这几行发给我，我来判断。
echo ------------------------------------------------------------
pause
exit /b 1

:emptyurl
echo.
echo 没有输入仓库地址，已取消。重新双击本脚本即可再来一次。
pause
exit /b 1

:addfail
echo.
echo 设置远程地址失败，多半是地址写错了。
echo 正确格式：https://github.com/你的用户名/仓库名.git
pause
exit /b 1

:pushfail
echo.
echo ------------------------------------------------------------
echo 推送失败，常见原因：
echo   1. 仓库还没创建 —— 先打开 https://github.com/new 建一个空仓库
echo   2. 没登录 GitHub，或登录的账号没有这个仓库的权限
echo   3. 远程仓库不是空的（建仓库时勾了 Add a README），先拉取再推送
echo 把上面最后几行提示发给我，我帮你看。
echo ------------------------------------------------------------
pause
exit /b 1
