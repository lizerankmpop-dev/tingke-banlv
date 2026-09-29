@echo off
chcp 65001 >nul
echo 启动听课伴侣...

set "DESKTOP=%USERPROFILE%\Desktop"
set "PROJECT_DIR=%DESKTOP%\听课伴侣\项目源代码\tingke-banlv"
set "INSTALL_DIR=%DESKTOP%\听课伴侣\安装空间"

echo 启动后端服务...
start "听课伴侣-后端" cmd /c "cd /d "%PROJECT_DIR%\backend" && "%INSTALL_DIR%\venv\Scripts\python.exe" main.py"

timeout /t 3 /nobreak >nul

echo 启动前端服务...
start "听课伴侣-前端" cmd /c "cd /d "%PROJECT_DIR%\frontend" && npm run dev"

timeout /t 3 /nobreak >nul

echo 正在打开浏览器...
start http://localhost:5173

echo.
echo 听课伴侣已启动！
echo 关闭此窗口不影响程序运行。
pause
