@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   RDP Auto-Ban — 卸载 Windows 服务
echo ============================================
echo.

:: Check for admin privileges
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 请以管理员身份运行此脚本！
    echo 右键点击 uninstall_service.bat → 以管理员身份运行
    pause
    exit /b 1
)

:: Check for nssm
set NSSM=%~dp0nssm.exe
if not exist "%NSSM%" (
    echo [提示] 未找到 nssm.exe，尝试用 pywin32 方式卸载...
    if exist "%~dp0.venv\Scripts\python.exe" (
        "%~dp0.venv\Scripts\python.exe" "%~dp0rdp_auto_ban.py" stop >nul 2>&1
        "%~dp0.venv\Scripts\python.exe" "%~dp0rdp_auto_ban.py" remove
    )
    pause
    exit /b
)

echo [1/3] 停止服务...
"%NSSM%" stop RDP-Auto-Ban >nul 2>&1

echo [2/3] 卸载服务...
"%NSSM%" remove RDP-Auto-Ban confirm
if %errorlevel% neq 0 (
    echo [提示] NSSM 卸载返回非零，尝试强制清理...
    sc delete RDP-Auto-Ban >nul 2>&1
)

echo [3/3] 清理 pywin32 残留...
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0rdp_auto_ban.py" remove >nul 2>&1
)

echo.
echo ============================================
echo   卸载完成！
echo   注意: 防火墙中的封禁规则未被自动清理。
echo   如需清理，在"高级安全 Windows Defender 防火墙"
echo   中删除以 "RDP-Auto-Ban-" 开头的入站规则。
echo ============================================
pause
