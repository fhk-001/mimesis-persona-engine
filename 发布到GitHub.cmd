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
set "PUSHLOG=%TEMP%\mimesis_push.log"
"%GITEXE%" %GITSAFE% push -u origin main >"%PUSHLOG%" 2>&1
set "PUSHRC=%ERRORLEVEL%"
type "%PUSHLOG%"
if not "%PUSHRC%"=="0" goto pushfail

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
echo 如果当前文件夹不是项目文件夹，说明打开的是别的副本。
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
echo 推送没有成功。上面那段就是 git 的原话。
echo ------------------------------------------------------------
findstr /C:"Repository not found" /C:"repository not found" "%PUSHLOG%" >nul 2>&1
if not errorlevel 1 goto case_notfound
findstr /C:"Authentication failed" /C:"could not read Username" /C:"Permission denied" /C:"403" "%PUSHLOG%" >nul 2>&1
if not errorlevel 1 goto case_auth
findstr /C:"rejected" /C:"non-fast-forward" /C:"fetch first" /C:"remote contains work" /C:"behind" "%PUSHLOG%" >nul 2>&1
if not errorlevel 1 goto case_notempty
goto case_other

:case_notempty
echo 【判断】远程仓库里已经有内容了（多半是建仓库时勾了 Add a README）。
echo.
echo   要覆盖远程那几个文件，直接用你本地的 46 个文件吗？
echo.
echo     输入 Y 再回车 = 覆盖（远程自动生成的那个 README 会被替换掉）
echo     直接回车     = 不覆盖，安全退出
echo       （不覆盖也可以：去 GitHub 把那个仓库删掉，重新建一个空的）
echo.
set /p FORCEANS=你的选择: 
if /i not "%FORCEANS%"=="Y" goto cancel
echo.
echo 正在覆盖远程……
"%GITEXE%" %GITSAFE% push -u --force origin main
if errorlevel 1 goto case_other
echo.
echo ------------------------------------------------------------
echo 覆盖成功！打开你的 GitHub 仓库页面就能看到了。
echo ------------------------------------------------------------
pause
exit /b 0

:case_notfound
echo 【判断】GitHub 上找不到这个仓库。
echo 请用浏览器打开这个地址确认一下：
echo   https://github.com/fhk-001/mimesis-persona-engine
echo 如果是 404，说明仓库还没建，或者名字/用户名不一样。
echo 把正确的地址告诉我，我改一下就行。
pause
exit /b 1

:case_auth
echo 【判断】登录/权限问题：没登录 GitHub，或者登录的账号不是 fhk-001。
echo 办法：在 Windows 搜索"凭据管理器" - 打开 - Windows 凭据 -
echo       找到 github.com 那条删掉，然后重新双击本脚本，再登录一次。
pause
exit /b 1

:case_other
echo 【判断】不是上面几种常见情况，需要看一下 git 的原话。
echo 把窗口里从「第 3 步」到这里的文字全部复制发给我。
pause
exit /b 1

:cancel
echo.
echo 已取消，远程没有做任何改动。
pause
exit /b 1
