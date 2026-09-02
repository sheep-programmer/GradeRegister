#!/usr/bin/env python3
"""下载当前配置所用的中文语音识别模型（免费、离线、本地识别）。"""
from __future__ import annotations

import sys

from grade_app import speech
from grade_app.config import load_config
from grade_app.platform_support import ensure_utf8_output

SIZES = {"sense-voice": "约 228MB", "paraformer": "约 217MB",
         "vosk": "约 42MB", "faster-whisper": "约 460MB",
         "sherpa": "随项目自带"}


def progress(done: int, total: int) -> None:
    pct = done * 100 // total if total else 0
    sys.stdout.write(f"\r  下载中… {done // 1024 // 1024}MB / "
                     f"{total // 1024 // 1024}MB ({pct}%)")
    sys.stdout.flush()
    if done >= total:
        sys.stdout.write("\n")
        sys.stdout.flush()


def main() -> int:
    ensure_utf8_output()        # 先理顺编码，否则 Windows 上第一句中文就崩
    cfg = load_config()
    engine = cfg.get("engine", "sense-voice")
    print(f"当前语音引擎: {engine}（{SIZES.get(engine, '未知体积')}）")
    print(f"模型目录: {speech.model_dir_path(cfg)}")
    try:
        speech.auto_ensure_engine(cfg, progress=progress)
    except Exception as e:  # noqa: BLE001
        print(f"\n下载失败: {e}", file=sys.stderr)
        return 1
    print("完成 ✔ 现在可以运行: python main.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
