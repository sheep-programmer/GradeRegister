#!/usr/bin/env python3
"""下载中文语音识别模型（免费、离线、本地识别）。

    python download_model.py          下载当前配置所用的引擎
    python download_model.py --all    下载打包需要的全部内置引擎
"""
from __future__ import annotations

import argparse
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


# 打包版随程序分发的引擎，与 GradeRegister.spec 的 bundled_models 一致。
# 少一个 spec 就会拒绝打包，所以 --all 必须把它们全下齐
BUNDLED = (
    ("sense-voice", speech.download_sense_voice),
    ("paraformer", speech.download_paraformer),
)


def main(argv=None) -> int:
    ensure_utf8_output()        # 先理顺编码，否则 Windows 上第一句中文就崩
    ap = argparse.ArgumentParser(description="下载中文语音识别模型")
    ap.add_argument("--all", action="store_true",
                    help="下载打包需要的全部内置引擎（供打包与 CI 用）")
    args = ap.parse_args(argv)

    cfg = load_config()
    print(f"模型目录: {speech.model_dir_path(cfg)}")
    if args.all:
        for name, download in BUNDLED:
            print(f"[{name}] {SIZES.get(name, '未知体积')}")
            try:
                download(cfg, progress=progress)
            except Exception as e:  # noqa: BLE001
                print(f"\n{name} 下载失败: {e}", file=sys.stderr)
                return 1
        print("完成 ✔ 可以打包了: pyinstaller GradeRegister.spec --noconfirm")
        return 0

    engine = cfg.get("engine", "sense-voice")
    print(f"当前语音引擎: {engine}（{SIZES.get(engine, '未知体积')}）")
    try:
        speech.auto_ensure_engine(cfg, progress=progress)
    except Exception as e:  # noqa: BLE001
        print(f"\n下载失败: {e}", file=sys.stderr)
        return 1
    print("完成 ✔ 现在可以运行: python main.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
