@echo off
chcp 65001 >nul
title 成绩登记 - 打包 Windows 版
cd /d "%~dp0"

echo ============================================
echo   成绩登记 —— 打包成免安装的 Windows 程序
echo ============================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [错误] 没有找到 Python。
    echo        请先到 https://www.python.org/downloads/ 安装 Python 3.10 以上版本，
    echo        安装时务必勾选 "Add Python to PATH"。
    pause
    exit /b 1
)

if not exist ".venv\" (
    echo [1/4] 创建虚拟环境...
    python -m venv .venv || goto :failed
) else (
    echo [1/4] 虚拟环境已存在，跳过
)

echo [2/4] 安装依赖（首次约需几分钟）...
call .venv\Scripts\python.exe -m pip install --upgrade pip -q
call .venv\Scripts\python.exe -m pip install -r requirements.txt -q || goto :failed
call .venv\Scripts\python.exe -m pip install pyinstaller -q || goto :failed

echo [3/4] 检查语音模型...
if not exist "models\sense-voice\model.int8.onnx" (
    echo       模型不在本地，开始下载（约 228MB，只需一次）...
    call .venv\Scripts\python.exe download_model.py || goto :failed
) else (
    echo       模型已就位
)

echo [4/4] 打包中（约 2-5 分钟，请勿关闭窗口）...
call .venv\Scripts\pyinstaller.exe GradeRegister.spec --noconfirm || goto :failed

echo.
echo ============================================
echo   打包完成
echo ============================================
echo   程序位置: dist\GradeRegister\GradeRegister.exe
echo.
echo   整个 dist\GradeRegister 文件夹就是完整程序，
echo   拷到任何一台 Windows 电脑都能直接双击运行，
echo   对方不需要装 Python，也不需要联网。
echo.
pause
exit /b 0

:failed
echo.
echo [失败] 上一步出错了，请把上面的红色错误信息发给开发者。
pause
exit /b 1
