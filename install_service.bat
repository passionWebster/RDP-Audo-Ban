@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   RDP Auto-Ban — 安装 Windows 服务
echo ============================================
echo.

:: Check for admin privileges
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 请以管理员身份运行此脚本！
    echo 右键点击 install_service.bat → 以管理员身份运行
    pause
    exit /b 1
)

echo [1/2] 安装服务...
"%~dp0venv\Scripts\python.exe" "%~dp0rdp_auto_ban.py" install
if %errorlevel% neq 0 (
    echo [错误] 服务安装失败，请检查:
    echo   1. 虚拟环境是否存在: venv\Scripts\python.exe
    echo   2. pywin32 是否已安装: venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

echo.
echo [2/2] 启动服务...
"%~dp0venv\Scripts\python.exe" "%~dp0rdp_auto_ban.py" start
if %errorlevel% neq 0 (
    echo [错误] 服务启动失败，请检查日志: logs\rdp_auto_ban.log
    pause
    exit /b 1
)

echo.
echo ============================================
echo   安装完成！服务已在后台运行。
echo.
echo   查看状态:  services.msc  →  "RDP Auto Ban Service"
echo   查看日志:  logs\rdp_auto_ban.log
echo   配置文件:  config.yaml
echo ============================================
pause
