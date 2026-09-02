"""平台差异集中处理：macOS / Windows / Linux。

界面与业务代码一律通过本模块访问平台相关能力，不直接调用 `open`、
`os.startfile` 或写死字体名。
"""
from __future__ import annotations

import os
import re
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


# 右键菜单事件。macOS 上两种编号都可能出现：Tk 8 把右键报成 Button-2，
# Tk 9 起改成与其他平台一致的 Button-3。运行期拿得到 Tk 版本就只绑对应的
# 那个，拿不到就两个都绑——少绑一个的后果是右键菜单完全点不出来
RIGHT_CLICK_EVENTS: Tuple[str, ...] = (
    ("<Button-2>", "<Button-3>", "<Control-Button-1>") if IS_MAC
    else ("<Button-3>",)
)


def right_click_events(widget=None) -> Tuple[str, ...]:
    """本平台上代表「右键」的事件串，能判定 Tk 版本时排除中键。"""
    if not IS_MAC or widget is None:
        return RIGHT_CLICK_EVENTS
    try:
        major = int(str(widget.tk.call("info", "patchlevel")).split(".")[0])
    except Exception:  # noqa: BLE001
        return RIGHT_CLICK_EVENTS
    button = "<Button-3>" if major >= 9 else "<Button-2>"
    return (button, "<Control-Button-1>")


def bind_right_click(widget, handler) -> None:
    """绑定右键菜单事件（覆盖各平台与各 Tk 版本的按键编号差异）。"""
    for event in right_click_events(widget):
        widget.bind(event, handler)


def modifier_label() -> str:
    """快捷键修饰键的显示名。"""
    return "⌘" if IS_MAC else "Ctrl"


def accelerator(key: str) -> Tuple[str, str]:
    """返回 (菜单加速键显示文本, 事件绑定串)，如 ("⌘S", "<Command-s>")。"""
    if IS_MAC:
        return f"⌘{key.upper()}", f"<Command-{key.lower()}>"
    return f"Ctrl+{key.upper()}", f"<Control-{key.lower()}>"


# ---------------------------------------------------------------------------
# Excel 占用检测与让行
# ---------------------------------------------------------------------------

def excel_lock_path(path: str) -> str:
    """Excel 打开工作簿时在同目录建的占用标记文件（~$名字.xlsx）。"""
    folder, name = os.path.split(os.path.abspath(path))
    return os.path.join(folder, "~$" + name)


def excel_holds_file(path: str) -> bool:
    """这份表格现在是不是正被 Excel 打开着。

    两条线索：Excel 会在同目录留一个 ~$ 开头的占用标记；Windows 上它还会
    独占锁住文件本身，连以读写方式打开都会被拒。
    """
    try:
        if os.path.exists(excel_lock_path(path)):
            return True
    except OSError:
        pass
    if IS_WINDOWS and os.path.exists(path):
        try:
            with open(path, "r+b"):
                return False
        except PermissionError:
            return True
        except OSError:
            return False
    return False


def file_is_writable(path: str) -> bool:
    """现在能不能真的写这个文件——判断占用是否只是残留标记的依据。"""
    try:
        with open(path, "r+b"):
            return True
    except OSError:
        return False


def close_excel_workbook(path: str) -> Tuple[bool, str]:
    """请 Excel 关掉这一份工作簿，返回 (是否已关掉, 说明)。

    只动这一个工作簿，Excel 里打开的别的表格一概不碰——整个进程杀掉会
    连老师正在编辑的其他文件一起丢。老师在这份表里的改动先存盘再关，
    这样接下来加载到的就是他最新的内容。
    """
    if not os.path.exists(path):
        return False, "文件不存在"
    if IS_WINDOWS:
        return _close_workbook_windows(path)
    if IS_MAC:
        return _close_workbook_mac(path)
    return False, "本平台不支持自动关闭 Excel"


def _close_workbook_windows(path: str) -> Tuple[bool, str]:
    """走 COM 让 Excel 自己关。缺 pywin32 或 Excel 没在跑就返回 False。"""
    try:
        import win32com.client        # pywin32，Windows 上才有
        import pythoncom
    except ImportError:
        return False, "未安装 pywin32，无法自动关闭 Excel"
    try:
        pythoncom.CoInitialize()
    except Exception:  # noqa: BLE001
        pass
    try:
        app = win32com.client.GetActiveObject("Excel.Application")
    except Exception:  # noqa: BLE001
        return False, "Excel 没有在运行"
    target = os.path.normcase(os.path.abspath(path))
    try:
        for book in list(app.Workbooks):
            try:
                full = os.path.normcase(os.path.abspath(str(book.FullName)))
            except Exception:  # noqa: BLE001
                continue
            if full != target:
                continue
            if not book.Saved:
                book.Save()      # 先留住老师在 Excel 里改的东西
            book.Close(SaveChanges=False)
            return True, "已关闭 Excel 里的这份表格"
    except Exception as exc:  # noqa: BLE001
        return False, f"关闭 Excel 失败：{exc}"
    return False, "Excel 里没有打开这份表格"


# 按下标倒着遍历：Excel 的 AppleScript 接口里 `repeat with wb in workbooks`
# 拿到的是引用，取 full name 会报参数错误(-50)；关闭会让后面的下标前移，
# 所以从后往前删
_MAC_CLOSE_SCRIPT = """
on run argv
    if application "Microsoft Excel" is not running then return "notrunning"
    set target to item 1 of argv
    tell application "Microsoft Excel"
        set hits to 0
        repeat with i from (count of workbooks) to 1 by -1
            if (full name of workbook i) is equal to target then
                if not (saved of workbook i) then save workbook i
                close workbook i saving no
                set hits to hits + 1
            end if
        end repeat
        return hits as text
    end tell
end run
"""


def _close_workbook_mac(path: str) -> Tuple[bool, str]:
    """走 AppleScript 让 Excel 自己关。Excel 没在跑就什么都不做。"""
    try:
        done = subprocess.run(
            ["osascript", "-", os.path.abspath(path)],
            input=_MAC_CLOSE_SCRIPT, capture_output=True, text=True,
            timeout=20, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"关闭 Excel 失败：{exc}"
    out = (done.stdout or "").strip()
    if done.returncode != 0:
        return False, f"关闭 Excel 失败：{(done.stderr or '').strip()}"
    if out == "notrunning":
        return False, "Excel 没有在运行"
    if out.isdigit() and int(out) > 0:
        return True, "已关闭 Excel 里的这份表格"
    return False, "Excel 里没有打开这份表格"


def excel_open_warning() -> Optional[str]:
    """录入过程中用 Excel 打开同一份表的后果提示；无需提示则 None。

    Windows 的 Excel 会独占锁住文件，打开期间本程序一次也写不进去。
    macOS 的 Excel 不加这道锁，写得进去，但它自己不会重新读盘，
    在它里面按一次保存就会用旧数据盖掉本程序写的内容。
    """
    if IS_WINDOWS:
        return ("Windows 的 Excel 会独占这份表格，打开期间本程序无法写入，"
                "自动保存会一直失败。\n\n"
                "建议录完再看。要现在打开的话，看完请关掉 Excel，"
                "再回来点一次「保存」。")
    if IS_MAC:
        return ("Excel 打开后不会自动重新读盘，看到的仍是打开那一刻的内容；"
                "而且在 Excel 里按保存会用旧数据盖掉本程序写入的分数。\n\n"
                "建议录完再看。")
    return None


def default_excel_hint() -> Optional[str]:
    """选表格对话框里给出的平台提示，无提示时返回 None。"""
    if IS_WINDOWS:
        return "若表格正被 Excel 打开，请先关闭再选择，否则无法写入"
    return None


# ---------------------------------------------------------------------------
# 提示音
# ---------------------------------------------------------------------------

# macOS 系统自带提示音；Windows 用 MessageBeep 的系统音，不依赖音频文件
_MAC_SOUNDS: dict = {
    "ok": "/System/Library/Sounds/Glass.aiff",
    "pick": "/System/Library/Sounds/Pop.aiff",
    "warn": "/System/Library/Sounds/Ping.aiff",
    "error": "/System/Library/Sounds/Basso.aiff",
    # 核对通过要跟「又填了一个分数」区别开：填分一节课响几十次，
    # 核对结论一位学生只响一次，听感必须不一样
    "success": "/System/Library/Sounds/Hero.aiff",
}


def sanitize_geometry(geometry: str, screen_w: int, screen_h: int,
                      min_w: int = 1000, min_h: int = 660) -> str:
    """校验上次保存的窗口几何，恢复时别把窗口放到屏幕外或缩成一条缝。

    拔掉外接显示器 / 分辨率改小后，窗口左上角可能落在屏幕外，记下来的
    尺寸也可能不满足最小窗口。样式必须是 ``WxH+X+Y``：宽度高度至少是
    min_w/min_h（但不超过屏幕），位置至少要露出一角在屏内；任一项不合
    规就退回一个只留尺寸的中性值，保证窗口始终可见可抓。
    """
    g = (geometry or "").strip()
    m = re.fullmatch(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", g)
    if not m:
        return f"{min_w}x{min_h}"
    w = min(max(int(m.group(1)), min_w), screen_w)
    h = min(max(int(m.group(2)), min_h), screen_h)
    x, y = int(m.group(3)), int(m.group(4))
    # 窗口 [x, x+w) × [y, y+h) 至少要露出一角在屏内，否则整块飘在外面
    visible = (x < screen_w and y < screen_h and x + w > 0 and y + h > 0)
    if not visible:
        return f"{min_w}x{min_h}"
    return f"{w}x{h}{x:+d}{y:+d}"


def play_sound(kind: str = "ok") -> None:
    """播放一条系统提示音，用于语音识别结果的即时反馈。

    全部异步、失败静默：提示音只是辅助，任何一步出错都不能打断主流程。
    """
    if IS_MAC:
        path = _MAC_SOUNDS.get(kind)
        if path and os.path.exists(path):
            try:
                subprocess.Popen(["afplay", path],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            except OSError:
                pass
    elif IS_WINDOWS:
        try:
            import winsound
            # MessageBeep 恰好有 5 个音，5 种用途各占一个，全都听得出区别。
            # pick 与 success 共用一个的话，「要选人」和「核对通过」一个声
            flags = {"ok": winsound.MB_OK,
                     "pick": winsound.MB_ICONQUESTION,
                     "success": winsound.MB_ICONASTERISK,
                     "warn": winsound.MB_ICONEXCLAMATION,
                     "error": winsound.MB_ICONHAND}.get(kind, winsound.MB_OK)
            winsound.MessageBeep(flags)
        except Exception:  # noqa: BLE001
            pass
