@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   RDP Auto-Ban - Install Windows Service
echo ============================================
echo.

rem Check admin
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Please run as Administrator.
    echo Right-click install_service.bat - Run as Administrator.
    pause
    exit /b 1
)

rem Check nssm
set NSSM=%~dp0nssm.exe
if not exist "%NSSM%" (
    echo [ERROR] nssm.exe not found.
    echo Download: curl -L -o nssm.zip https://nssm.cc/release/nssm-2.24.zip
    echo Extract win64\nssm.exe to this directory.
    pause
    exit /b 1
)

rem Check venv
set PYTHON=%~dp0.venv\Scripts\python.exe
if not exist "%PYTHON%" (
    echo [ERROR] Virtual environment not found: .venv
    echo Run: python -m venv .venv
    echo      .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

rem Check config
if not exist "%~dp0config.yaml" (
    echo [ERROR] config.yaml not found.
    pause
    exit /b 1
)

rem Clean old instances
echo [1/4] Cleaning old service instances...
"%NSSM%" stop RDP-Auto-Ban 2>nul >nul
"%NSSM%" remove RDP-Auto-Ban confirm 2>nul >nul
sc delete RDP-Auto-Ban 2>nul >nul

rem Install
echo [2/4] Installing service...
"%NSSM%" install RDP-Auto-Ban "%PYTHON%" "%~dp0rdp_auto_ban.py --console"
if !errorlevel! neq 0 (
    echo [ERROR] Service install failed.
    pause
    exit /b 1
)

rem Configure
echo [3/4] Configuring auto-start and crash recovery...
"%NSSM%" set RDP-Auto-Ban Start SERVICE_AUTO_START
"%NSSM%" set RDP-Auto-Ban AppExit Default Restart
"%NSSM%" set RDP-Auto-Ban AppRestartDelay 60000

rem Logging
set LOGDIR=%~dp0logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
"%NSSM%" set RDP-Auto-Ban AppStdout "%LOGDIR%\nssm_stdout.log"
"%NSSM%" set RDP-Auto-Ban AppStderr "%LOGDIR%\nssm_stderr.log"
"%NSSM%" set RDP-Auto-Ban AppRotateFiles 1
"%NSSM%" set RDP-Auto-Ban AppRotateBytes 10485760

rem Start
echo [4/4] Starting service...
"%NSSM%" start RDP-Auto-Ban
if !errorlevel! neq 0 (
    echo [ERROR] Service start failed.
    "%NSSM%" status RDP-Auto-Ban
    pause
    exit /b 1
)

echo.
echo ============================================
echo   Install complete.
echo.
echo   Status:   services.msc or nssm status RDP-Auto-Ban
echo   Logs:     logs\rdp_auto_ban.log
echo   Config:   config.yaml
echo.
echo   Features: auto-start on boot, auto-restart on crash (60s)
echo ============================================
pause
