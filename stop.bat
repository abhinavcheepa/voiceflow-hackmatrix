@echo off
setlocal
cd /d "%~dp0"
echo Stopping all VoiceFlow AI services...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" -Stop
echo Done.
