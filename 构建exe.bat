@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
title MusicDataManager - build exe
powershell -ExecutionPolicy Bypass -File "scripts\windows-build-exe.ps1"
echo.
echo Done. Press any key to close.
pause >nul
