@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 启动 QQ 机器人：先自检能不能连上 NapCat，通了就直接开始收消息。
rem 注意：不要在 if 代码块里写半角右括号，那会把代码块提前闭合、导致脚本直接退出。

echo ============================================================
echo 第 1 步：检查能不能连上 NapCat
echo ============================================================
call "%~dp0run.cmd" qq --check
if errorlevel 1 goto failed

echo.
echo 自检通过，正在启动 QQ 机器人……
echo.
echo   - 现在是演练模式：会收到消息、打印准备回的内容，但不会真的发出去
echo   - 想真的发出去：把 config.json 里的 dry_run 改成 false
echo   - 想停止：按 Ctrl+C，或者直接关掉这个窗口
echo.
call "%~dp0run.cmd" qq
echo.
echo 机器人已停止。
pause
exit /b 0

:failed
echo.
echo 自检没通过，请对照检查：
echo   - NapCat 是否已经启动
echo   - NapCat 的 HTTP 服务是否开启，端口填 3000
echo   - QQ 小号是否已经扫码登录成功
echo.
echo 详细报错见上面几行文字。
pause
exit /b 1
