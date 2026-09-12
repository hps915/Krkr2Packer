@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem Krkr2Packer launcher: prefer pythonw (no console window)
where pythonw >nul 2>nul
if not errorlevel 1 (
    start "Krkr2Packer" pythonw main.py
    exit /b 0
)

where python >nul 2>nul
if errorlevel 1 (
    echo [Krkr2Packer] Python not found. Install Python 3.10+ first.
    pause
    exit /b 1
)

start "Krkr2Packer" python main.py

