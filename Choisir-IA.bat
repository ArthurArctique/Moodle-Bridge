@echo off
rem Moodle Bridge : choisir l IA (Claude Code, Codex, autre commande ou aucune) et son modele.
rem Double-clic dans l Explorateur. Meme chose dans un terminal : py scripts\moodle_bridge.py ai
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -I scripts\moodle_bridge.py ai %*
) else (
    python -I scripts\moodle_bridge.py ai %*
)
echo.
pause
