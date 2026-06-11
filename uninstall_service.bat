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

echo [1/2] 停止服务...
"%~dp0venv\Scripts\python.exe" "%~dp0rdp_auto_ban.py" stop
if %errorlevel% neq 0 (
    echo [提示] 服务可能已在停止状态，继续卸载...
)

echo.
echo [2/2] 卸载服务...
"%~dp0venv\Scripts\python.exe" "%~dp0rdp_auto_ban.py" remove
if %errorlevel% neq 0 (
    echo [错误] 服务卸载失败
    pause
    exit /b 1
)

echo.
echo ============================================
echo   卸载完成！
echo   注意: 防火墙中的封禁规则未被自动清理。
echo   如需清理，请在"高级安全 Windows Defender 防火墙"
echo   中删除以 "RDP-Auto-Ban-" 开头的规则。
echo ============================================
pause
