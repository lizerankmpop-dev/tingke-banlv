@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo =========================================
echo   听课伴侣 - Windows 环境安装
echo =========================================
echo.

set "DESKTOP=%USERPROFILE%\Desktop"
set "PROJECT_DIR=%DESKTOP%\听课伴侣\项目源代码\tingke-banlv"
set "INSTALL_DIR=%DESKTOP%\听课伴侣\安装空间"

echo 项目目录: %PROJECT_DIR%
echo 安装目录: %INSTALL_DIR%
echo.

:: 1. 创建目录
echo [1/6] 创建目录结构...
if not exist "%INSTALL_DIR%\venv" mkdir "%INSTALL_DIR%\venv"
if not exist "%INSTALL_DIR%\data" mkdir "%INSTALL_DIR%\data"
if not exist "%INSTALL_DIR%\whisper_models" mkdir "%INSTALL_DIR%\whisper_models"
if not exist "%INSTALL_DIR%\ecdict" mkdir "%INSTALL_DIR%\ecdict"
echo   [OK] 目录创建完成

:: 2. Python 虚拟环境
echo.
echo [2/6] 创建 Python 虚拟环境...
if exist "%INSTALL_DIR%\venv\Scripts\python.exe" (
    echo   [OK] 虚拟环境已存在
) else (
    python -m venv "%INSTALL_DIR%\venv"
    echo   [OK] 虚拟环境创建完成
)

:: 3. 安装 Python 依赖
echo.
echo [3/6] 安装 Python 依赖...
call "%INSTALL_DIR%\venv\Scripts\activate.bat"
pip install --upgrade pip -q
pip install -r "%PROJECT_DIR%\backend\requirements.txt"
echo   [OK] Python 依赖安装完成

:: 4. 下载词典
echo.
echo [4/6] 下载 ECDICT 词典（可能需要几分钟）...
cd /d "%PROJECT_DIR%\backend"
python -c "from dictionary.local_dict import LocalDictionary; LocalDictionary(); print('ECDICT OK')"
echo   [OK] 词典就绪

:: 5. 前端依赖
echo.
echo [5/6] 安装前端依赖...
cd /d "%PROJECT_DIR%\frontend"
call npm install
echo   [OK] 前端依赖安装完成

:: 6. Ollama
echo.
echo [6/6] 检查 Ollama...
where ollama >nul 2>&1
if %errorlevel% equ 0 (
    echo   [OK] Ollama 已安装
    echo   推荐模型: ollama pull qwen2.5:7b
) else (
    echo   [WARN] Ollama 未安装（AI术语提取需要）
    echo   访问 https://ollama.com 安装
)

echo.
echo =========================================
echo   安装完成！
echo.
echo   启动后端（终端1）:
echo     cd "%PROJECT_DIR%\backend"
echo     "%INSTALL_DIR%\venv\Scripts\python.exe" main.py
echo.
echo   启动前端（终端2）:
echo     cd "%PROJECT_DIR%\frontend"
echo     npm run dev
echo.
echo   浏览器访问: http://localhost:5173
echo =========================================
pause
