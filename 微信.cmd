@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 接入微信前的自检：检查 wxauto 是否装好、能不能连上你的微信

call "%~dp0run.cmd" wechat --check %*
echo.
echo ----------------------------------------
echo 说明：
echo   1) wxauto 只支持【微信电脑版 3.9.X】，新版 4.x 客户端不支持
echo   2) 建议用在"小号"上：小号登录 3.9 微信，机器人在电脑上自动回复，
echo      你在自己微信里点小号头像就能聊天
echo   3) 安装命令：python -m pip install git+https://github.com/cluic/wxauto.git
echo ----------------------------------------
pause
