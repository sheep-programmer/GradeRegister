@echo off
chcp 65001 >nul
rem 成绩登记软件 - 双击启动（Windows）
rem 自动使用 .venv 环境，自动补齐依赖和语音模型

cd /d "%~dp0"

echo ========================================
echo   成绩登记软件 启动中…
echo ========================================

set PY=.venv\Scripts\python.exe

rem 1. 虚拟环境：没有就现场建
if not exist "%PY%" (
    echo [1/3] 首次运行，正在创建运行环境…
    python -m venv .venv
    if errorlevel 1 (
        echo 创建环境失败：请先从 python.org 安装 Python 3.9 或更高版本
        pause
        exit /b 1
    )
)

rem 2. 依赖：缺少就现场装
"%PY%" -c "import openpyxl, sounddevice, tksheet, sherpa_onnx" 2>nul
if errorlevel 1 (
    echo [2/3] 正在安装语音识别组件（只需一次）…
    "%PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo 安装失败，请检查网络后重试
        pause
        exit /b 1
    )
)

rem 3. 语音模型：当前引擎缺模型就补齐
"%PY%" -c "import sys; from grade_app import speech; from grade_app.config import load_config; sys.exit(0 if speech.engine_model_ready(load_config()) else 1)" 2>nul
if errorlevel 1 (
    echo [3/3] 正在准备中文语音模型（只需一次）…
    "%PY%" download_model.py
    if errorlevel 1 (
        echo 模型下载失败，请检查网络后重试
        pause
        exit /b 1
    )
)

echo 环境就绪，正在打开成绩登记软件…
rem 麦克风由程序启动时自动挑选，只对本次运行生效
"%PY%" -X faulthandler -u main.py >> gui_runtime.log 2>&1
if errorlevel 1 (
    echo 程序异常退出，详情见 gui_runtime.log
    pause
)
