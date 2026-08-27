"""自绘控件：圆角按钮、音量电平表、提示条、识别记录、进度点。

tkinter 原生控件做不出圆角与色块层次，这里用 Canvas 画。
"""
from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk
from typing import Callable, List, Optional

from . import theme


def rounded_rect(canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int,
                 radius: int, **kwargs) -> int:
    """在画布上画一个圆角矩形，返回 item id。"""
    radius = max(0, min(radius, (x2 - x1) // 2, (y2 - y1) // 2))
    points = [
        x1 + radius, y1, x2 - radius, y1, x2, y1,
        x2, y1 + radius, x2, y2 - radius, x2, y2,
        x2 - radius, y2, x1 + radius, y2, x1, y2,
        x1, y2 - radius, x1, y1 + radius, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class RoundButton(tk.Canvas):
    """圆角实心按钮，支持悬停、按下与禁用三态。"""

    def __init__(self, parent, text: str, command: Callable[[], None],
                 font, fill: str = theme.PRIMARY,
                 hover: str = theme.PRIMARY_HOVER,
                 disabled: str = theme.PRIMARY_DISABLED,
                 fg: str = "white", width: int = 220, height: int = 52,
                 bg: str = theme.SURFACE, radius: int = theme.RADIUS):
        super().__init__(parent, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._command = command
        self._fill = fill
        self._hover = hover
        self._disabled_fill = disabled
        self._radius = radius
        self._enabled = True
        self._pressed = False

        self._shape = rounded_rect(self, 1, 1, width - 1, height - 1,
                                   radius, fill=fill, outline="")
        self._label = self.create_text(width // 2, height // 2, text=text,
                                       fill=fg, font=font)

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Configure>", self._on_resize)

    # ---------------- 外观 ----------------
    def configure_look(self, text: Optional[str] = None,
                       fill: Optional[str] = None,
                       hover: Optional[str] = None,
                       disabled: Optional[str] = None) -> None:
        if text is not None:
            self.itemconfigure(self._label, text=text)
        if fill is not None:
            self._fill = fill
        if hover is not None:
            self._hover = hover
        if disabled is not None:
            self._disabled_fill = disabled
        self._repaint()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._repaint()

    def _repaint(self, hovering: bool = False) -> None:
        if not self._enabled:
            color = self._disabled_fill
        elif self._pressed:
            color = theme.hex_mix(self._hover, "#000000", 0.12)
        elif hovering:
            color = self._hover
        else:
            color = self._fill
        self.itemconfigure(self._shape, fill=color)

    def _on_resize(self, event) -> None:
        self.coords(self._label, event.width // 2, event.height // 2)
        self.delete(self._shape)
        self._shape = rounded_rect(self, 1, 1, event.width - 1, event.height - 1,
                                   self._radius, fill=self._fill, outline="")
        self.tag_lower(self._shape, self._label)
        self._repaint()

    # ---------------- 事件 ----------------
    def _on_enter(self, _event) -> None:
        if self._enabled:
            self._repaint(hovering=True)

    def _on_leave(self, _event) -> None:
        self._pressed = False
        self._repaint()

    def _on_press(self, _event) -> None:
        if self._enabled:
            self._pressed = True
            self._repaint()

    def _on_release(self, _event) -> None:
        was_pressed = self._pressed
        self._pressed = False
        self._repaint(hovering=True)
        if self._enabled and was_pressed:
            self._command()


class LevelMeter(tk.Canvas):
    """音量电平表：一排小方块，音量越大点亮越多。"""

    BARS = 22
    BAR_W = 5
    BAR_GAP = 3

    def __init__(self, parent, height: int = 26, bg: str = theme.SURFACE):
        width = self.BARS * (self.BAR_W + self.BAR_GAP)
        super().__init__(parent, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0)
        self._height = height
        self._bars: List[int] = []
        self._level = 0.0
        self._build()

    def _build(self) -> None:
        self.delete("all")
        self._bars.clear()
        for i in range(self.BARS):
            x = i * (self.BAR_W + self.BAR_GAP)
            # 越靠右的格子越高，形成渐进的电平条外观
            h = 6 + int((self._height - 10) * (i / max(1, self.BARS - 1)))
            y2 = self._height - 2
            self._bars.append(
                rounded_rect(self, x, y2 - h, x + self.BAR_W, y2, 2,
                             fill=theme.BORDER, outline=""))

    def set_level(self, level: float) -> None:
        """level 为 0~1 的归一化音量。"""
        self._level = max(0.0, min(1.0, level))
        lit = int(round(self._level * self.BARS))
        for i, bar in enumerate(self._bars):
            if i >= lit:
                color = theme.BORDER
            elif i > self.BARS * 0.85:
                color = theme.ERROR        # 快削顶了
            elif i > self.BARS * 0.65:
                color = theme.WARN
            else:
                color = theme.SUCCESS
            self.itemconfigure(bar, fill=color)

    def reset(self) -> None:
        self.set_level(0.0)


class MessageBar(tk.Frame):
    """反馈提示条：左侧色条 + 图标 + 文字，四种语义配色。"""

    def __init__(self, parent, font, icon_font, wraplength: int = 900):
        super().__init__(parent, bg=theme.INFO_SOFT, bd=0, highlightthickness=0)
        self._accent = tk.Frame(self, bg=theme.INFO, width=4)
        self._accent.pack(side="left", fill="y")
        inner = tk.Frame(self, bg=theme.INFO_SOFT)
        inner.pack(side="left", fill="both", expand=True,
                   padx=theme.SPACE_MD, pady=theme.SPACE_XS)
        self._inner = inner
        self._icon = tk.Label(inner, text="", bg=theme.INFO_SOFT,
                              fg=theme.INFO, font=icon_font)
        self._icon.pack(side="left", padx=(0, theme.SPACE_SM))
        self._text = tk.Label(inner, text="", bg=theme.INFO_SOFT, fg=theme.INFO,
                              font=font, justify="left", anchor="w",
                              wraplength=wraplength)
        self._text.pack(side="left", fill="x", expand=True)

    def show(self, text: str, kind: str = "info") -> None:
        fg, bg = theme.MESSAGE_COLORS.get(kind, theme.MESSAGE_COLORS["info"])
        icon = theme.MESSAGE_ICONS.get(kind, "›")
        self.configure(bg=bg)
        self._accent.configure(bg=fg)
        self._inner.configure(bg=bg)
        self._icon.configure(text=icon, bg=bg, fg=fg)
        self._text.configure(text=text, bg=bg, fg=fg)

    def set_wraplength(self, width: int) -> None:
        self._text.configure(wraplength=max(200, width))


class OptionRow(tk.Frame):
    """一行可点的选项：标记 + 标题 + 灰色说明，整行都是点击热区。

    kind="check" 画方框打勾，kind="radio" 画圆点。ttk 在 clam 主题下
    只把方框涂成实色，勾没勾看不出来。
    """

    BOX = 18

    def __init__(self, parent, text: str, hint: str, font, small_font,
                 kind: str = "check", command: Optional[Callable] = None):
        super().__init__(parent, bg=theme.SURFACE, cursor="hand2")
        self._kind = kind
        self._command = command
        self._checked = False
        self._bg = theme.SURFACE
        self._box = tk.Canvas(self, width=self.BOX, height=self.BOX,
                              bg=theme.SURFACE, highlightthickness=0, bd=0)
        self._box.pack(side="left", pady=3)
        self._label = tk.Label(self, text=text, bg=theme.SURFACE, fg=theme.TEXT,
                               font=font)
        self._label.pack(side="left", padx=(theme.SPACE_SM, 0))
        self._hint = tk.Label(self, text=hint, bg=theme.SURFACE,
                              fg=theme.TEXT_FAINT, font=small_font)
        self._hint.pack(side="left", padx=(theme.SPACE_SM, 0))
        for w in (self, self._box, self._label, self._hint):
            w.bind("<Button-1>", self._on_click)
            w.bind("<Enter>", lambda _e: self._set_bg(theme.SURFACE_ALT))
            w.bind("<Leave>", lambda _e: self._set_bg(theme.SURFACE))
        self._draw()

    def set(self, checked: bool) -> None:
        self._checked = bool(checked)
        self._draw()

    def get(self) -> bool:
        return self._checked

    def _on_click(self, _event) -> None:
        self.set(True if self._kind == "radio" else not self._checked)
        if self._command is not None:
            self._command(self)

    def _set_bg(self, color: str) -> None:
        self._bg = color
        for w in (self, self._box, self._label, self._hint):
            w.configure(bg=color)
        self._draw()

    def _draw(self) -> None:
        c = self._box
        c.delete("all")
        n = self.BOX - 2
        if self._kind == "radio":
            c.create_oval(2, 2, n, n, width=2, fill=self._bg,
                          outline=theme.PRIMARY if self._checked
                          else theme.BORDER_STRONG)
            if self._checked:
                c.create_oval(6, 6, n - 4, n - 4, fill=theme.PRIMARY, outline="")
        elif self._checked:
            rounded_rect(c, 2, 2, n, n, 4, fill=theme.PRIMARY, outline="")
            c.create_line(5, n // 2, n // 2, n - 4, n - 3, 5, fill="white",
                          width=2, capstyle="round", joinstyle="round")
        else:
            rounded_rect(c, 2, 2, n, n, 4, fill=self._bg, width=1,
                         outline=theme.BORDER_STRONG)


class OptionGroup:
    """一组互斥的 OptionRow，同时只有一项选中。"""

    def __init__(self, parent, items, font, small_font):
        self.rows = {}
        for key, text, hint in items:
            row = OptionRow(parent, text, hint, font, small_font,
                            kind="radio", command=self._picked)
            row.pack(fill="x", pady=1)
            self.rows[key] = row

    def _picked(self, chosen: OptionRow) -> None:
        for row in self.rows.values():
            if row is not chosen:
                row.set(False)

    def set(self, key: str) -> None:
        for k, row in self.rows.items():
            row.set(k == key)

    def get(self) -> Optional[str]:
        for k, row in self.rows.items():
            if row.get():
                return k
        return None


class HeardLog(tk.Frame):
    """识别记录：每句识别结果带时间戳留在面板里，可往回翻。

    正在识别的半句以灰字跟在末尾，确认后被正式结果替换。
    识别文本只在提示条上一闪而过的话，没人说话时界面完全静止，
    老师无从判断程序还在不在听。
    """

    MAX_LINES = 300

    def __init__(self, parent, font, small_font, lines: int = 4):
        super().__init__(parent, bg=theme.SURFACE)
        head = tk.Frame(self, bg=theme.SURFACE)
        head.pack(fill="x")
        tk.Label(head, text="识别记录", bg=theme.SURFACE, fg=theme.TEXT_MUTED,
                 font=small_font).pack(side="left")
        self._count = tk.Label(head, text="还没有识别过", bg=theme.SURFACE,
                               fg=theme.TEXT_FAINT, font=small_font)
        self._count.pack(side="right")

        box = tk.Frame(self, bg=theme.SURFACE_ALT, highlightthickness=1,
                       highlightbackground=theme.BORDER)
        box.pack(fill="both", expand=True, pady=(theme.SPACE_XS, 0))
        self._text = tk.Text(box, height=lines, font=font, wrap="word",
                             bg=theme.SURFACE_ALT, fg=theme.TEXT, bd=0,
                             highlightthickness=0, cursor="arrow",
                             padx=theme.SPACE_SM, pady=theme.SPACE_XS,
                             state="disabled")
        scroll = ttk.Scrollbar(box, orient="vertical", command=self._text.yview)
        self._text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self._text.pack(side="left", fill="both", expand=True)

        self._text.tag_configure("stamp", foreground=theme.TEXT_FAINT)
        self._text.tag_configure("heard", foreground=theme.TEXT)
        self._text.tag_configure("note", foreground=theme.TEXT_FAINT)
        self._text.tag_configure("partial", foreground=theme.TEXT_MUTED)
        self._count_n = 0

    # ---------------- 写入 ----------------
    def append(self, text: str, kind: str = "heard") -> None:
        """追加一条确认的识别结果。"""
        follow = self._at_bottom()
        self._text.configure(state="normal")
        self._drop_partial()
        if self._text.index("end-1c") != "1.0":
            self._text.insert("end", "\n")
        self._text.insert("end", f"{time.strftime('%H:%M:%S')}  ", "stamp")
        self._text.insert("end", (text or "").strip() or "（空）", kind)
        self._trim()
        self._text.configure(state="disabled")
        if follow:
            self._text.see("end")
        if kind == "heard":
            self._count_n += 1
            self._count.config(text=f"已识别 {self._count_n} 句")

    def set_partial(self, text: str) -> None:
        """刷新末尾那条正在识别的半句。"""
        text = (text or "").strip()
        follow = self._at_bottom()
        self._text.configure(state="normal")
        self._drop_partial()
        if text:
            head = "" if self._text.index("end-1c") == "1.0" else "\n"
            self._text.insert("end", f"{head}…  {text}", "partial")
        self._text.configure(state="disabled")
        if follow:
            self._text.see("end")

    def dump(self) -> str:
        """当前面板里的全部文本，供测试与排查用。"""
        return self._text.get("1.0", "end-1c")

    # ---------------- 内部 ----------------
    def _drop_partial(self) -> None:
        rng = self._text.tag_ranges("partial")
        while rng:
            self._text.delete(rng[0], rng[1])
            rng = self._text.tag_ranges("partial")

    def _at_bottom(self) -> bool:
        """老师往回翻记录时不要把视口硬拽回末尾。"""
        try:
            return self._text.yview()[1] >= 0.999
        except Exception:  # noqa: BLE001
            return True

    def _trim(self) -> None:
        extra = int(self._text.index("end-1c").split(".")[0]) - self.MAX_LINES
        if extra > 0:
            self._text.delete("1.0", f"{extra + 1}.0")


class ProgressBadge(tk.Canvas):
    """当前学生的填写进度：小圆点，填一题亮一颗。"""

    DOT = 9
    GAP = 6

    def __init__(self, parent, bg: str = theme.SURFACE, max_dots: int = 12):
        self._max_dots = max_dots
        super().__init__(parent, height=self.DOT + 4,
                         width=max_dots * (self.DOT + self.GAP),
                         bg=bg, highlightthickness=0, bd=0)
        self._bg = bg

    def set_progress(self, filled: int, total: int) -> None:
        self.delete("all")
        if total <= 0:
            self.configure(width=1)
            return
        shown = min(total, self._max_dots)
        self.configure(width=shown * (self.DOT + self.GAP))
        y = (self.DOT + 4) // 2
        for i in range(shown):
            x = i * (self.DOT + self.GAP)
            done = i < min(filled, shown)
            self.create_oval(x, y - self.DOT // 2, x + self.DOT, y + self.DOT // 2,
                             fill=theme.PRIMARY if done else theme.BORDER,
                             outline="")
        if total > self._max_dots:
            self.create_text(shown * (self.DOT + self.GAP), y, text="…",
                             anchor="w", fill=theme.TEXT_FAINT)
