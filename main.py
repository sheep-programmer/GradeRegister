#!/usr/bin/env python3
"""成绩登记系统入口。

用法:
    python main.py                  # 启动图形界面
    python main.py --check          # 只做环境自检（依赖/模型/麦克风）
"""
from __future__ import annotations

import os
import sys


def check_environment() -> int:
    """环境自检：依赖、语音模型、麦克风。返回 0 表示通过。"""
    from grade_app import config, platform_support, speech

    cfg = config.load_config()
    engine = cfg.get("engine", "sense-voice")
    print("== 成绩登记系统 环境自检 ==")
    print(f"  运行平台: {platform_support.platform_name()}"
          f"  Python {sys.version.split()[0]}")
    print(f"  当前语音引擎: {engine}")
    ok = True

    required = [("openpyxl", "openpyxl (Excel读写)"),
                ("numpy", "numpy"),
                ("tkinter", "tkinter (图形界面)"),
                ("sounddevice", "sounddevice (录音)")]
    if engine in ("sense-voice", "sherpa"):
        required.append(("sherpa_onnx", "sherpa-onnx (语音识别)"))
    elif engine == "vosk":
        required.append(("vosk", "vosk (语音识别)"))
    else:
        required.append(("faster_whisper", "faster-whisper (语音识别)"))

    for mod, name in required:
        try:
            __import__(mod)
            print(f"  [OK] {name}")
        except ImportError:
            print(f"  [缺] {name} —— 请运行: pip install -r requirements.txt")
            ok = False

    ok = _check_model(engine, cfg, speech) and ok

    mics = speech.list_microphones()
    if mics:
        print(f"  [OK] 检测到 {len(mics)} 个麦克风:")
        for d in mics:
            print(f"        {d['index']}: {d['name']}")
    else:
        print("  [警告] 未检测到麦克风，请检查权限：")
        for line in platform_support.microphone_permission_hint().splitlines():
            print(f"        {line}")

    print("== 自检" + ("通过 ✔" if ok else "有缺失，请按提示安装") + " ==")
    return 0 if ok else 1


def _check_model(engine: str, cfg: dict, speech) -> bool:
    """检查当前引擎所需的模型是否就位。"""
    if engine == "sense-voice":
        if speech.sense_voice_ready(cfg):
            print(f"  [OK] 语音模型: {speech.sense_voice_dir(cfg)}")
            return True
        print("  [缺] 语音模型未下载（约 228MB）—— 请运行: python download_model.py")
        return False
    if engine == "sherpa":
        try:
            print(f"  [OK] sherpa 模型: {speech.sherpa_model_dir(cfg)}")
            return True
        except FileNotFoundError as e:
            print(f"  [缺] {e}")
            return False
    if engine == "vosk":
        model_path = speech.vosk_model_path(cfg)
        if os.path.isdir(model_path):
            print(f"  [OK] vosk 模型: {model_path}")
            return True
        print("  [缺] vosk 模型未下载（约42MB）—— 请运行: python download_model.py")
        return False
    print("  [提示] faster-whisper 模型在首次使用时自动下载（约 460MB）")
    return True


def main() -> int:
    args = sys.argv[1:]
    if "--check" in args:
        return check_environment()

    # 支持直接打开表格：python main.py 实例成绩表.xlsx
    open_path = None
    for a in args:
        if not a.startswith("-") and os.path.exists(a):
            open_path = os.path.abspath(a)
            break

    from grade_app.gui import run
    run(open_path=open_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())