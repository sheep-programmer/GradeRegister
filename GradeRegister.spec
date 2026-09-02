# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置，macOS 与 Windows 通用。

    pyinstaller GradeRegister.spec --noconfirm

语音模型（sense-voice 约 228MB + paraformer 约 217MB）随程序打包，
装好即用不联网。vosk / faster-whisper / sherpa 流式模型都不打进去：
用不到，且体积是它的数倍。
"""
import os
import sys

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

PROJECT = os.path.abspath(os.getcwd())
IS_MAC = sys.platform == "darwin"

# ---------------------------------------------------------------- 随包资源
MODEL_FILES = ("model.int8.onnx", "tokens.txt")

# 内置两个整句解码引擎：sense-voice（默认）与 paraformer（短句更快）
# 目录名与 grade_app/speech.py 的 paraformer_dir() 保持一致
bundled_models = ("sense-voice", "paraformer-zh")
missing = []
datas = []
for sub in bundled_models:
    model_src = os.path.join(PROJECT, "models", sub)
    missing += [os.path.join(sub, f) for f in MODEL_FILES
                if not os.path.isfile(os.path.join(model_src, f))]
    datas += [(os.path.join(model_src, f), os.path.join("models", sub))
              for f in MODEL_FILES]
if missing:
    raise SystemExit(
        f"语音模型缺失: {missing}\n"
        f"请先运行 python download_model.py 把模型下到 models/ 下")

# ---------------------------------------------------------------- 原生依赖
# sherpa-onnx 与 onnxruntime 的动态库不会被自动收全，显式收集
binaries = []
for pkg in ("sherpa_onnx", "onnxruntime", "_sounddevice_data"):
    try:
        binaries += collect_dynamic_libs(pkg)
    except Exception:
        pass

hiddenimports = (
    collect_submodules("sherpa_onnx")
    + collect_submodules("tksheet")
    + ["sounddevice", "_sounddevice", "cffi", "openpyxl", "pypinyin"]
)
if sys.platform == "win32":
    # 请 Excel 交出被占用的表格要走 COM
    hiddenimports += ["win32com.client", "pythoncom", "pywintypes"]

# 体积大头，全都用不上：识别只走内置的 sense-voice
excludes = [
    "vosk", "faster_whisper", "torch", "torchaudio", "transformers",
    "matplotlib", "scipy", "pandas", "PIL", "PyQt5", "PySide2",
    "IPython", "jupyter", "pytest", "setuptools", "pip",
]

a = Analysis(
    ["main.py"],
    pathex=[PROJECT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

# 单目录模式：启动快（单文件每次要把 228MB 模型解压到临时目录，慢得多）
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="成绩登记" if IS_MAC else "GradeRegister",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # 不弹黑窗
    disable_windowed_traceback=False,
    argv_emulation=IS_MAC,  # 让 .app 支持把 xlsx 拖到图标上打开
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="GradeRegister",
)

if IS_MAC:
    app = BUNDLE(
        coll,
        name="成绩登记.app",
        icon=None,
        bundle_identifier="com.graderegister.app",
        info_plist={
            # 缺这一条，macOS 会直接拒绝录音且不弹任何提示
            "NSMicrophoneUsageDescription":
                "需要使用麦克风，才能把你念的学生姓名和分数录入成绩表。",
            "CFBundleName": "成绩登记",
            "CFBundleDisplayName": "成绩登记",
            "CFBundleShortVersionString": "1.0.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "Excel 成绩表",
                "CFBundleTypeRole": "Editor",
                "LSItemContentTypes": ["org.openxmlformats.spreadsheetml.sheet"],
            }],
        },
    )
