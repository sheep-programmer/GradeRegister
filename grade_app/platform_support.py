"""平台差异集中处理：macOS / Windows / Linux。

界面与业务代码一律通过本模块访问平台相关能力，不直接调用 `open`、
`os.startfile` 或写死字体名。
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Optional, Sequence, Tuple

IS_MAC = sys.platform == "darwin"
IS_WINDOWS = os.name == "nt"
IS_LINUX = not IS_MAC and not IS_WINDOWS


def ensure_utf8_output() -> None:
    """把标准输出重设为 UTF-8，让中文提示在任何控制台都打得出来。

    Windows 的控制台按本地代码页解码（简体中文版是 cp936，英文版与 CI
    环境是 cp1252），print 中文会直接抛 UnicodeEncodeError 把程序打断。
    无法表示的字符降级替换，宁可显示成问号也不能中断整个流程。
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue          # 没有控制台（打包成窗口程序）或流是替身
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass              # 流已关闭或不支持重配，保持原样即可


def platform_name() -> str:
    if IS_MAC:
        return "macOS"
    if IS_WINDOWS:
        return "Windows"
    return "Linux"


# ---------------------------------------------------------------------------
# 打开文件 / 目录
# ---------------------------------------------------------------------------

def open_in_default_app(path: str) -> None:
    """用系统默认程序打开文件或目录；失败时抛出异常由调用方提示。"""
    if IS_WINDOWS:
        os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
        return
    cmd = ["open", path] if IS_MAC else ["xdg-open", path]
    subprocess.Popen(cmd)


def open_microphone_settings() -> bool:
    """跳转到系统的麦克风权限设置页；无法跳转时返回 False。"""
    try:
        if IS_MAC:
            subprocess.Popen([
                "open",
                "x-apple.systempreferences:"
                "com.apple.preference.security?Privacy_Microphone"])
            return True
        if IS_WINDOWS:
            os.startfile("ms-settings:privacy-microphone")  # type: ignore[attr-defined]
            return True
    except Exception:  # noqa: BLE001
        return False
    return False


def microphone_permission_hint() -> str:
    """收不到声音时展示给用户的排查步骤（按平台措辞）。"""
    if IS_MAC:
        return (
            "1. 在「系统设置 → 隐私与安全性 → 麦克风」里，打开本程序（或终端）的开关\n"
            "2. 改完权限后必须完全退出本程序再重新打开，权限才会生效\n"
            "3. 仍然不行就点「设置」换一个麦克风设备")
    if IS_WINDOWS:
        return (
            "1. 在「设置 → 隐私和安全性 → 麦克风」里，打开「允许桌面应用访问你的麦克风」\n"
            "2. 确认任务栏音量图标里选中的输入设备是你要用的那一个\n"
            "3. 仍然不行就点「设置」换一个麦克风设备")
    return (
        "1. 确认系统声音设置里选中了正确的输入设备\n"
        "2. 确认当前用户有音频设备访问权限（如属于 audio 用户组）\n"
        "3. 仍然不行就点「设置」换一个麦克风设备")


# ---------------------------------------------------------------------------
# 界面
# ---------------------------------------------------------------------------

# 中文界面字体候选，按平台优先级排列
_FONT_CANDIDATES: Tuple[str, ...] = (
    ("PingFang SC", "Hiragino Sans GB", "Helvetica Neue", "Helvetica")
    if IS_MAC else
    ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "SimHei")
    if IS_WINDOWS else
    ("Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Micro Hei", "DejaVu Sans")
)

_MONO_CANDIDATES: Tuple[str, ...] = (
    ("SF Mono", "Menlo", "Monaco") if IS_MAC else
    ("Cascadia Mono", "Consolas", "Courier New") if IS_WINDOWS else
    ("DejaVu Sans Mono", "Liberation Mono")
)


def _first_available(candidates: Sequence[str], fallback: str) -> str:
    """返回候选里系统实际装有的第一个字体族。"""
    try:
        from tkinter import font as tkfont
        available = {name.lower() for name in tkfont.families()}
    except Exception:  # noqa: BLE001
        return candidates[0] if candidates else fallback
    for name in candidates:
        if name.lower() in available:
            return name
    return fallback


def ui_font_family() -> str:
    """界面正文字体族（需在 Tk 根窗口创建之后调用）。"""
    return _first_available(_FONT_CANDIDATES, "TkDefaultFont")


def mono_font_family() -> str:
    """等宽字体族，用于分数等需要对齐的数字。"""
    return _first_available(_MONO_CANDIDATES, "TkFixedFont")


def enable_dpi_awareness() -> None:
    """Windows 高分屏下开启 DPI 感知，避免界面被系统拉伸成模糊位图。

    必须在创建 Tk 根窗口之前调用；其他平台为空操作。
    """
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        # 优先 per-monitor v2（Win 10 1703+），失败退回 system aware（Win 8.1+）
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
        except Exception:  # noqa: BLE001
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        pass


def dpi_scaling(root) -> float:
    """当前显示缩放比例（1.0 = 96 DPI），用于按屏幕密度调整行高等尺寸。"""
    try:
        return max(1.0, root.winfo_fpixels("1i") / 96.0)
    except Exception:  # noqa: BLE001
        return 1.0


# 右键菜单事件：macOS 的 Tk 把右键报成 Button-2，其余平台是 Button-3
RIGHT_CLICK_EVENTS: Tuple[str, ...] = (
    ("<Button-2>", "<Control-Button-1>") if IS_MAC else ("<Button-3>",)
)


def bind_right_click(widget, handler) -> None:
    """绑定右键菜单事件（覆盖各平台的按键编号差异）。"""
    for event in RIGHT_CLICK_EVENTS:
        widget.bind(event, handler)


def modifier_label() -> str:
    """快捷键修饰键的显示名。"""
    return "⌘" if IS_MAC else "Ctrl"


def accelerator(key: str) -> Tuple[str, str]:
    """返回 (菜单加速键显示文本, 事件绑定串)，如 ("⌘S", "<Command-s>")。"""
    if IS_MAC:
        return f"⌘{key.upper()}", f"<Command-{key.lower()}>"
    return f"Ctrl+{key.upper()}", f"<Control-{key.lower()}>"


def default_excel_hint() -> Optional[str]:
    """选表格对话框里给出的平台提示，无提示时返回 None。"""
    if IS_WINDOWS:
        return "若表格正被 Excel 打开，请先关闭再选择，否则无法写入"
    return None
