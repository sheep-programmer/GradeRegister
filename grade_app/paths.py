"""运行期路径：区分随程序分发的只读资源与用户可写数据。

源码运行时两者都是项目目录，开发时一切照旧。打包成独立程序后程序自身
所在的位置是只读的（macOS 的 .app 包内、Windows 的 Program Files），
语音模型跟着程序走，配置必须落到用户目录，否则保存设置会失败。
"""
from __future__ import annotations

import os
import sys

APP_NAME = "GradeRegister"


def is_frozen() -> bool:
    """是否运行在打包后的独立程序里。"""
    return bool(getattr(sys, "frozen", False))


def project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_dir() -> str:
    """随程序分发的只读资源目录（语音模型放这里）。

    单文件模式下 PyInstaller 把资源解压到 _MEIPASS；单目录模式下资源
    就在可执行文件旁边。
    """
    if is_frozen():
        return getattr(sys, "_MEIPASS", None) or os.path.dirname(
            os.path.abspath(sys.executable))
    return project_root()


def user_data_dir() -> str:
    """用户可写数据目录（配置）。源码运行时仍用项目目录，便于开发调试。"""
    if not is_frozen():
        return project_root()
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    elif sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = (os.environ.get("XDG_DATA_HOME")
                or os.path.expanduser("~/.local/share"))
    path = os.path.join(base, APP_NAME)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        return os.path.expanduser("~")
    return path
