@echo off
chcp 936 >nul
setlocal enabledelayedexpansion

set "DESKTOP=%USERPROFILE%\Desktop"
set "PROJECT_DIR=%DESKTOP%\听课伴侣\项目源代码\tingke-banlv"
set "INSTALL_DIR=%DESKTOP%\听课伴侣\安装空间"
set "VENV_PYTHON=%INSTALL_DIR%\venv\Scripts\python.exe"

echo =========================================
echo   Tingke Banlv - Diagnostic Startup
echo =========================================
echo.

:: Check Python
echo [1] Checking Python...
python --version 2>nul
if errorlevel 1 (
    echo   [ERROR] Python not found!
    echo   Please install Python 3.10+ from:
    echo   https://www.python.org/downloads/
    echo   IMPORTANT: Check "Add Python to PATH" during install
    pause
    exit /b 1
)
echo   [OK] Python found
echo.

:: Check virtual environment
echo [2] Checking virtual environment...
if not exist "%VENV_PYTHON%" (
    echo   [WARNING] Virtual env not found, using system Python
    set "PYTHON_CMD=python"
    echo   Installing dependencies to system Python...
    pip install -r "%PROJECT_DIR%\backend\requirements.txt" 2>&1
) else (
    echo   [OK] Virtual env found
    set "PYTHON_CMD=%VENV_PYTHON%"
)
echo.

:: Check backend imports
echo [3] Checking backend dependencies...
cd /d "%PROJECT_DIR%\backend"
%PYTHON_CMD% -c "import fastapi; print('  fastapi OK')" 2>&1
%PYTHON_CMD% -c "import faster_whisper; print('  faster_whisper OK')" 2>&1
%PYTHON_CMD% -c "import pyaudio; print('  pyaudio OK')" 2>&1
echo.

:: Test start backend in current window
echo [4] Starting backend...
echo =========================================
echo   Backend output (screenshot if errors):
echo =========================================
echo.

cd /d "%PROJECT_DIR%\backend"
%PYTHON_CMD% main.py 2>&1

echo.
echo =========================================
echo   Backend exited
echo   If there are errors above, screenshot
echo =========================================
pause
