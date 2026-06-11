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

:: Check for nssm
set NSSM=%~dp0nssm.exe
if not exist "%NSSM%" (
    echo [错误] 未找到 nssm.exe，请先下载:
    echo    curl -L -o nssm.zip https://nssm.cc/release/nssm-2.24.zip
    echo   解压后把 win64\nssm.exe 放到本目录
    pause
    exit /b 1
)

:: Check for venv
set PYTHON=%~dp0.venv\Scripts\python.exe
if not exist "%PYTHON%" (
    echo [错误] 未找到虚拟环境: %PYTHON%
    echo 请先运行: python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

:: Check for config
if not exist "%~dp0config.yaml" (
    echo [错误] 未找到 config.yaml 配置文件
    pause
    exit /b 1
)

:: Stop and remove any existing instance
echo [1/4] 清理旧服务实例...
"%NSSM%" stop RDP-Auto-Ban >nul 2>&1
"%NSSM%" remove RDP-Auto-Ban confirm >nul 2>&1

:: Also clean up any leftover pywin32 service
"%~dp0.venv\Scripts\python.exe" "%~dp0rdp_auto_ban.py" remove >nul 2>&1

:: Install service via NSSM
echo [2/4] 安装服务...
"%NSSM%" install RDP-Auto-Ban "%PYTHON%" "%~dp0rdp_auto_ban.py --console"
if %errorlevel% neq 0 (
    echo [错误] 服务安装失败
    pause
    exit /b 1
)

:: Configure auto-start
echo [3/4] 配置自启动 ^& 崩溃恢复...
"%NSSM%" set RDP-Auto-Ban Start SERVICE_AUTO_START
"%NSSM%" set RDP-Auto-Ban AppExit Default Restart
"%NSSM%" set RDP-Auto-Ban AppRestartDelay 60000

:: Redirect output to log files
set LOGDIR=%~dp0logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
"%NSSM%" set RDP-Auto-Ban AppStdout "%LOGDIR%\nssm_stdout.log"
"%NSSM%" set RDP-Auto-Ban AppStderr "%LOGDIR%\nssm_stderr.log"
"%NSSM%" set RDP-Auto-Ban AppRotateFiles 1
"%NSSM%" set RDP-Auto-Ban AppRotateBytes 10485760

:: Start service
echo [4/4] 启动服务...
"%NSSM%" start RDP-Auto-Ban
if %errorlevel% neq 0 (
    echo [错误] 服务启动失败
    "%NSSM%" status RDP-Auto-Ban
    pause
    exit /b 1
)

echo.
echo ============================================
echo   安装完成！服务已在后台运行。
echo.
echo   查看状态:  services.msc  →  "RDP Auto Ban Service"
echo              nssm status RDP-Auto-Ban
echo   查看日志:  logs\rdp_auto_ban.log
echo   配置文件:  config.yaml
echo.
echo   特性: 开机自启 | 崩溃自动重启 ^(60s后^)
echo ============================================
pause
