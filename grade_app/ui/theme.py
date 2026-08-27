"""界面设计令牌：颜色、字号、间距，以及 ttk 样式的统一配置。

界面代码只引用这里的常量，不再散落硬编码色值与字体名。
"""
from __future__ import annotations

from tkinter import ttk
from typing import Tuple

from .. import platform_support

# ---------------------------------------------------------------------------
# 颜色
# ---------------------------------------------------------------------------

# 中性色阶
BG = "#f1f3f5"           # 窗口背景
SURFACE = "#ffffff"      # 卡片
SURFACE_ALT = "#f8f9fa"  # 表格斑马纹的浅行
BORDER = "#e2e5e9"       # 分隔线与卡片描边
BORDER_STRONG = "#cdd2d8"

TEXT = "#1c1e21"         # 主文字
TEXT_MUTED = "#6b7280"   # 次要文字
TEXT_FAINT = "#9ca3af"   # 占位与提示

# 主色
PRIMARY = "#2563eb"
PRIMARY_HOVER = "#1d4ed8"
PRIMARY_ACTIVE = "#1e40af"
PRIMARY_SOFT = "#e8f0fe"  # 当前行底色
PRIMARY_DISABLED = "#a8c3f5"

# 语义色：文字色 / 浅底色
INFO = "#1d4ed8"
INFO_SOFT = "#eef4ff"
SUCCESS = "#15803d"
SUCCESS_SOFT = "#ecfdf3"
WARN = "#b45309"
WARN_SOFT = "#fff8eb"
ERROR = "#b91c1c"
ERROR_SOFT = "#fef2f2"

# 录音态
RECORDING = "#dc2626"
RECORDING_HOVER = "#b91c1c"

# 表格
PENDING_CELL = "#fff8db"   # 下一个待填的题
EDITED_CELL = "#2563eb"    # 刚填/刚改的格子

MESSAGE_COLORS = {
    "info": (INFO, INFO_SOFT),
    "success": (SUCCESS, SUCCESS_SOFT),
    "warn": (WARN, WARN_SOFT),
    "error": (ERROR, ERROR_SOFT),
}

MESSAGE_ICONS = {
    "info": "›",
    "success": "✓",
    "warn": "!",
    "error": "✕",
}

# ---------------------------------------------------------------------------
# 字号与间距
# ---------------------------------------------------------------------------

SIZE_DISPLAY = 20    # 当前学生等主视觉
SIZE_TITLE = 15      # 卡片标题
SIZE_BODY = 13       # 正文
SIZE_SMALL = 12      # 表格与辅助信息
SIZE_TINY = 11       # 状态栏

SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 12
SPACE_LG = 16
SPACE_XL = 24

RADIUS = 10          # 圆角按钮的半径


class Fonts:
    """按平台选定字体族后派生出的字号阶梯。"""

    def __init__(self) -> None:
        family = platform_support.ui_font_family()
        mono = platform_support.mono_font_family()
        self.family = family
        self.display = (family, SIZE_DISPLAY, "bold")
        self.title = (family, SIZE_TITLE, "bold")
        self.body = (family, SIZE_BODY)
        self.body_bold = (family, SIZE_BODY, "bold")
        self.small = (family, SIZE_SMALL)
        self.small_bold = (family, SIZE_SMALL, "bold")
        self.tiny = (family, SIZE_TINY)
        self.button = (family, SIZE_BODY, "bold")
        self.mono = (mono, SIZE_SMALL)


def apply(root) -> Fonts:
    """套用主题到根窗口，返回字体集。需在根窗口创建之后调用。"""
    fonts = Fonts()
    root.configure(bg=BG)

    style = ttk.Style(root)
    style.theme_use("clam")   # clam 才能可靠地铺实背景色

    style.configure(".", background=BG, foreground=TEXT, font=fonts.body)
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("TSeparator", background=BORDER)

    # 表格与识别记录之间的分隔条：拖得动就要看得见
    style.configure("TPanedwindow", background=BG)
    style.configure("Sash", sashthickness=8, gripcount=0, background=BORDER)

    # 次要按钮：浅底描边
    style.configure("TButton", background=SURFACE, foreground=TEXT,
                    bordercolor=BORDER_STRONG, borderwidth=1,
                    focusthickness=0, focuscolor=SURFACE,
                    padding=(SPACE_MD, SPACE_SM), font=fonts.body)
    style.map("TButton",
              background=[("pressed", "#e9ecef"), ("active", SURFACE_ALT),
                          ("disabled", SURFACE_ALT)],
              bordercolor=[("active", BORDER_STRONG)],
              foreground=[("disabled", TEXT_FAINT)])

    # 主按钮：实心主色
    style.configure("Accent.TButton", background=PRIMARY, foreground="white",
                    bordercolor=PRIMARY, borderwidth=0,
                    focusthickness=0, focuscolor=PRIMARY,
                    padding=(SPACE_MD, SPACE_SM), font=fonts.button)
    style.map("Accent.TButton",
              background=[("pressed", PRIMARY_ACTIVE), ("active", PRIMARY_HOVER),
                          ("disabled", PRIMARY_DISABLED)],
              foreground=[("disabled", "white")])

    style.configure("Dialog.TFrame", background=SURFACE)
    style.configure("Dialog.TLabel", background=SURFACE, foreground=TEXT)
    style.configure("Dialog.TCheckbutton", background=SURFACE, foreground=TEXT)
    style.map("Dialog.TCheckbutton", background=[("active", SURFACE)])

    style.configure("TCheckbutton", background=SURFACE, foreground=TEXT)
    style.map("TCheckbutton", background=[("active", SURFACE)])
    style.configure("TCombobox", fieldbackground=SURFACE, background=SURFACE,
                    bordercolor=BORDER_STRONG, arrowcolor=TEXT_MUTED)

    style.configure("TProgressbar", background=PRIMARY, troughcolor=BORDER,
                    bordercolor=BORDER, lightcolor=PRIMARY, darkcolor=PRIMARY,
                    thickness=6)

    return fonts


def sheet_options(fonts: Fonts, row_height: int) -> dict:
    """tksheet 的外观参数，跟整体主题保持一致。"""
    return dict(
        font=(fonts.family, SIZE_SMALL, "normal"),
        header_font=(fonts.family, SIZE_SMALL, "bold"),
        index_font=(fonts.family, SIZE_TINY, "normal"),
        header_height=row_height + 6,
        row_height=row_height,
        header_bg=SURFACE_ALT, header_fg=TEXT_MUTED,
        header_grid_fg=BORDER,
        index_bg=SURFACE_ALT, index_fg=TEXT_FAINT,
        index_grid_fg=BORDER,
        table_bg=SURFACE, table_fg=TEXT,
        table_grid_fg=BORDER,
        table_selected_cells_border_fg=PRIMARY,
        table_selected_cells_bg=PRIMARY_SOFT,
        table_selected_cells_fg=TEXT,
        table_selected_rows_bg=PRIMARY_SOFT,
        table_selected_rows_fg=TEXT,
        table_selected_columns_bg=PRIMARY_SOFT,
        table_selected_columns_fg=TEXT,
        outline_thickness=0,
        show_horizontal_grid=True,
        show_vertical_grid=True,
        empty_horizontal=0, empty_vertical=0,
    )


def scaled(value: int, factor: float) -> int:
    """按显示缩放调整像素尺寸。"""
    return max(1, int(round(value * factor)))


def hex_mix(color_a: str, color_b: str, ratio: float) -> str:
    """在两个 #rrggbb 之间线性插值，用于生成按下态等中间色。"""
    ratio = min(1.0, max(0.0, ratio))

    def parts(c: str) -> Tuple[int, int, int]:
        c = c.lstrip("#")
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)

    ar, ag, ab = parts(color_a)
    br, bg_, bb = parts(color_b)
    return "#%02x%02x%02x" % (
        round(ar + (br - ar) * ratio),
        round(ag + (bg_ - ag) * ratio),
        round(ab + (bb - ab) * ratio),
    )
