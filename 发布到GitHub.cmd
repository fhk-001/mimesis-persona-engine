@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 把本地仓库推送到 GitHub。双击运行即可，中途会让你粘贴仓库地址、并登录 GitHub。
rem 注意：不要在 if 代码块里写半角右括号，那会把代码块提前闭合、导致脚本直接退出。

echo ============================================================
echo 第 1 步：检查这个文件夹是不是 git 仓库
echo ============================================================
git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 goto nogit

echo 已经是 git 仓库，本地提交如下：
git log --oneline -3

echo.
echo ============================================================
echo 第 2 步：检查有没有配置远程仓库地址
echo ============================================================
git remote get-url origin >nul 2>&1
if errorlevel 1 goto askurl

echo 当前远程地址：
git remote get-url origin
goto push

:askurl
echo 还没有设置远程地址。
echo.
echo 请先在浏览器打开 https://github.com/new 建一个"空"仓库
echo 建好后把仓库地址复制过来，形如 https://github.com/用户名/仓库名.git
echo.
set /p REPOURL=粘贴仓库地址后按回车: 
if "%REPOURL%"=="" goto emptyurl
git remote add origin "%REPOURL%"
if errorlevel 1 goto addfail
echo 已设置远程地址：%REPOURL%

:push
echo.
echo ============================================================
echo 第 3 步：推送到 GitHub（这一步会要求你登录）
echo ============================================================
git push -u origin main
if errorlevel 1 goto pushfail

echo.
echo ------------------------------------------------------------
echo 推送成功！打开你的 GitHub 仓库页面就能看到了。
echo ------------------------------------------------------------
pause
exit /b 0

:nogit
echo.
echo 这个文件夹不是 git 仓库，无法推送。请把这个窗口里的文字发给我。
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
echo   3. 远程仓库已经存在同名文件，先拉取再推送
echo 把上面最后几行提示发给我，我帮你看。
echo ------------------------------------------------------------
pause
exit /b 1
