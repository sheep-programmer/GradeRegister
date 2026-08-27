#!/bin/bash
# 成绩登记软件 - 双击启动（macOS）
# 自动使用 .venv 环境，自动补齐依赖和语音模型

cd "$(dirname "$0")" || exit 1

echo "========================================"
echo "  成绩登记软件 启动中…"
echo "========================================"

# 1. 虚拟环境：没有就现场建
if [ ! -x ".venv/bin/python" ]; then
    echo "[1/3] 首次运行，正在创建运行环境…"
    python3 -m venv .venv || { echo "创建环境失败：请确认已安装 Python 3（python3）"; read -r -p "按回车键退出"; exit 1; }
fi

# 2. 依赖：缺少就现场装
if ! .venv/bin/python -c "import openpyxl, sounddevice, tksheet, sherpa_onnx" 2>/dev/null; then
    echo "[2/3] 正在安装语音识别组件（只需一次）…"
    .venv/bin/python -m pip install -r requirements.txt || { echo "安装失败，请检查网络后重试"; read -r -p "按回车键退出"; exit 1; }
fi

# 3. 语音模型：当前引擎缺模型就补齐
if ! .venv/bin/python -c "import sys; from grade_app import speech; from grade_app.config import load_config; sys.exit(0 if speech.engine_model_ready(load_config()) else 1)" 2>/dev/null; then
    echo "[3/3] 正在准备中文语音模型（只需一次）…"
    .venv/bin/python download_model.py || { echo "模型下载失败，请检查网络后重试"; read -r -p "按回车键退出"; exit 1; }
fi

echo "环境就绪，正在打开成绩登记软件…" | tee -a gui_runtime.log
# 麦克风由程序启动时自动挑选，只对本次运行生效——蓝牙耳机连上或断开后
# 设备编号会变，写死进配置反而会选到收不到声音的设备。
# 运行日志写入 gui_runtime.log，便于排查麦克风音量与识别结果。
exec .venv/bin/python -X faulthandler -u main.py 2>&1 | tee -a gui_runtime.log
