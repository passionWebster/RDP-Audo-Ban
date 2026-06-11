@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   RDP Auto-Ban - Uninstall Windows Service
echo ============================================
echo.

rem Check admin
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Please run as Administrator.
    echo Right-click uninstall_service.bat - Run as Administrator.
    pause
    exit /b 1
)

rem Check nssm
set NSSM=%~dp0nssm.exe
if not exist "%NSSM%" (
    echo [WARN] nssm.exe not found, trying pywin32 fallback...
    if exist "%~dp0.venv\Scripts\python.exe" (
        "%~dp0.venv\Scripts\python.exe" "%~dp0rdp_auto_ban.py" stop 2>nul
        "%~dp0.venv\Scripts\python.exe" "%~dp0rdp_auto_ban.py" remove
    ) else (
        echo [ERROR] Cannot uninstall without nssm.exe or venv.
    )
    pause
    exit /b
)

rem Stop
echo [1/3] Stopping service...
"%NSSM%" stop RDP-Auto-Ban 2>nul >nul

rem Remove
echo [2/3] Removing service...
"%NSSM%" remove RDP-Auto-Ban confirm
if !errorlevel! neq 0 (
    echo [WARN] NSSM remove failed, trying sc delete...
    sc delete RDP-Auto-Ban 2>nul >nul
)

rem Cleanup pywin32 residue
echo [3/3] Cleaning up pywin32 residue...
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0rdp_auto_ban.py" remove 2>nul >nul
)

echo.
echo ============================================
echo   Uninstall complete.
echo.
echo   NOTE: Firewall rules (RDP-Auto-Ban-*) are NOT removed.
echo   Manually delete them in Windows Defender Firewall
echo   if needed.
echo ============================================
pause
