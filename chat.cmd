@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 双击本文件就开始试聊（用 personas\default 里的人设）
rem 想临时换个人设：chat.cmd --persona personas\demo-linxiaoman

call "%~dp0run.cmd" chat %*
