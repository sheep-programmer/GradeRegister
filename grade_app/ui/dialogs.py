"""弹窗：选择学生、设置、使用说明。统一套用主题令牌。"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable, List, Tuple

from .. import platform_support, speech
from ..recorder import SegmentTimer
from . import theme
from .widgets import OptionGroup, OptionRow


def _center_on(dlg: tk.Toplevel, parent: tk.Misc) -> None:
    """把弹窗摆到父窗口中央。"""
    dlg.update_idletasks()
    w, h = dlg.winfo_width(), dlg.winfo_height()
    x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - h) // 3
    dlg.geometry(f"+{max(0, x)}+{max(0, y)}")


def _make_dialog(parent: tk.Misc, title: str,
                 resizable: bool = False) -> Tuple[tk.Toplevel, tk.Frame]:
    dlg = tk.Toplevel(parent)
    dlg.title(title)
    dlg.transient(parent)
    dlg.configure(bg=theme.SURFACE)
    dlg.resizable(resizable, resizable)
    body = tk.Frame(dlg, bg=theme.SURFACE)
    body.pack(fill="both", expand=True, padx=theme.SPACE_XL, pady=theme.SPACE_LG)
    return dlg, body


class StudentChoiceDialog:
    """重名或候选确认：列出候选行让老师点选。

    模态等待，选择期间后续识别结果排队，避免弹窗与连续听写互相打架。
    """

    def __init__(self, parent: tk.Misc, fonts: theme.Fonts,
                 choices: List[Tuple[int, str]],
                 on_pick: Callable[[int], None]):
        self.parent = parent
        self.fonts = fonts
        self.choices = choices
        self.on_pick = on_pick

    def show(self) -> None:
        dlg, body = _make_dialog(self.parent, "请选择学生")
        dlg.attributes("-topmost", True)
        dlg.grab_set()

        tk.Label(body, text="听到的名字对应多行，请点选正确的一位",
                 bg=theme.SURFACE, fg=theme.TEXT, font=self.fonts.title,
                 anchor="w").pack(fill="x")
        tk.Label(body, text="重名，或者名字没听清时都会出现这个窗口",
                 bg=theme.SURFACE, fg=theme.TEXT_MUTED, font=self.fonts.small,
                 anchor="w").pack(fill="x", pady=(2, theme.SPACE_MD))

        def pick(row: int) -> None:
            dlg.destroy()
            self.on_pick(row)

        for row, name in self.choices:
            item = tk.Frame(body, bg=theme.SURFACE_ALT, highlightthickness=1,
                            highlightbackground=theme.BORDER, cursor="hand2")
            item.pack(fill="x", pady=theme.SPACE_XS // 2)
            tk.Label(item, text=name, bg=theme.SURFACE_ALT, fg=theme.TEXT,
                     font=self.fonts.body_bold).pack(
                side="left", padx=theme.SPACE_MD, pady=theme.SPACE_SM)
            tk.Label(item, text=f"第 {row + 1} 行", bg=theme.SURFACE_ALT,
                     fg=theme.TEXT_MUTED, font=self.fonts.small).pack(
                side="right", padx=theme.SPACE_MD)
            for widget in (item, *item.winfo_children()):
                widget.bind("<Button-1>", lambda _e, r=row: pick(r))
                widget.bind("<Enter>",
                            lambda _e, f=item: f.configure(bg=theme.PRIMARY_SOFT))
                widget.bind("<Leave>",
                            lambda _e, f=item: f.configure(bg=theme.SURFACE_ALT))

        ttk.Button(body, text="都不是，取消", command=dlg.destroy).pack(
            pady=(theme.SPACE_MD, 0))
        _center_on(dlg, self.parent)
        self.parent.wait_window(dlg)


class SettingsDialog:
    """设置窗口：语音、麦克风、解析与保存选项。"""

    def __init__(self, parent: tk.Misc, fonts: theme.Fonts, cfg: dict,
                 on_saved: Callable[[dict], None]):
        self.parent = parent
        self.fonts = fonts
        self.cfg = cfg
        self.on_saved = on_saved

    CHECKS = (
        ("语音", (
            ("hold_to_talk", "按住说话", "关闭则点一下开始、再点一下结束"),
        )),
        ("录入", (
            ("strip_prefix", "剥离题号前缀", "念「第一题18分」时只取 18"),
            ("strip_suffix", "剥离「分」后缀", "念「18分」时取 18"),
        )),
        ("写回 Excel", (
            ("write_formula", "总分写 =SUM() 公式", "关闭则写算好的数值"),
            ("write_checked", "核对一致时写 ✓", "关闭则只标不一致"),
        )),
    )
    SAVE_MODES = (
        ("score", "每填一个分数就保存", "最稳妥，中途退出也不丢"),
        ("student", "每录完一名学生保存", "整行填完或核对完才写盘"),
        ("manual", "只在点「保存」时写盘", "自己掌握保存时机"),
    )
    # 断句停顿档位：(秒, 显示文字)
    GAP_CHOICES = (
        (0.4, "0.4 秒（说完马上识别）"),
        (0.7, "0.7 秒（标准）"),
        (1.0, "1.0 秒（念得慢一些）"),
        (1.5, "1.5 秒（中间要想一下）"),
    )

    def _gap_label(self) -> str:
        """当前 segment_gap 对应的档位文字，取最接近的一档。"""
        current = SegmentTimer._resolve_gap(
            self.cfg.get("segment_gap"), streaming=False)
        return min(self.GAP_CHOICES,
                   key=lambda c: abs(c[0] - current))[1]

    def show(self) -> None:
        dlg, body = _make_dialog(self.parent, "设置")
        dlg.grab_set()

        engine_var = tk.StringVar(value=self.cfg.get("engine", "sense-voice"))
        mics = speech.list_microphones()
        mic_values = ["自动选择"] + [f"{d['index']}: {d['name']}" for d in mics]
        current = self.cfg.get("device")
        mic_var = tk.StringVar(value=next(
            (v for v in mic_values[1:] if v.startswith(f"{current}:")),
            "自动选择"))
        gap_var = tk.StringVar(value=self._gap_label())

        rows: dict = {}
        for title, items in self.CHECKS:
            group = self._section(body, title)
            if title == "语音":
                combo = self._field(group, "识别引擎", engine_var,
                                    ["sense-voice", "sherpa", "vosk",
                                     "faster-whisper"],
                                    "sense-voice 最准，说完一句才出字；"
                                    "sherpa 边说边出字；vosk 模型小")
                self._build_model_row(group, dlg, engine_var)
                combo.bind("<<ComboboxSelected>>",
                           lambda _e: self._refresh_model_status())
                self._field(group, "麦克风", mic_var, mic_values,
                            "自动选择会在每次启动时挑一个收得到声音的设备"
                            if mics else "没有检测到麦克风")
                self._field(group, "断句停顿", gap_var,
                            [label for _sec, label in self.GAP_CHOICES],
                            "连续听写时，说完一句停顿多久就自动识别")
            for key, label, hint in items:
                row = OptionRow(group, label, hint, self.fonts.body,
                                self.fonts.small)
                row.set(bool(self.cfg.get(key, True)))
                row.pack(fill="x", pady=1)
                rows[key] = row

        save_group = self._section(body, "自动保存")
        modes = OptionGroup(save_group, self.SAVE_MODES, self.fonts.body,
                            self.fonts.small)
        modes.set(self._current_mode())

        def apply() -> None:
            new_cfg = {
                "engine": engine_var.get(),
                # 「自动选择」写回 None：不能把本次自动挑中的设备号固化下来，
                # 蓝牙耳机连上/断开后设备索引会变
                "device": (None if mic_var.get() == "自动选择"
                           else int(mic_var.get().split(":")[0])),
                "auto_save_mode": modes.get() or "score",
                "segment_gap": next(
                    (sec for sec, label in self.GAP_CHOICES
                     if label == gap_var.get()),
                    self.cfg.get("segment_gap", 0.7)),
            }
            new_cfg["auto_save"] = new_cfg["auto_save_mode"] != "manual"
            new_cfg.update({key: row.get() for key, row in rows.items()})
            dlg.destroy()
            self.on_saved(new_cfg)

        buttons = tk.Frame(body, bg=theme.SURFACE)
        buttons.pack(fill="x", pady=(theme.SPACE_LG, 0))
        ttk.Button(buttons, text="取消", command=dlg.destroy).pack(side="right")
        ttk.Button(buttons, text="保存", command=apply,
                   style="Accent.TButton").pack(side="right",
                                                padx=(0, theme.SPACE_SM))
        _center_on(dlg, self.parent)

    # ---------------- 模型状态 ----------------
    def _build_model_row(self, parent: tk.Frame, dlg: tk.Toplevel,
                         engine_var: tk.StringVar) -> None:
        self._dlg = dlg
        self._engine_var = engine_var
        row = tk.Frame(parent, bg=theme.SURFACE)
        row.pack(fill="x", padx=(theme.SPACE_XL + theme.SPACE_MD, 0))
        self._lbl_model = tk.Label(row, text="", bg=theme.SURFACE,
                                   font=self.fonts.small, anchor="w")
        self._lbl_model.pack(side="left")
        self._btn_dl = ttk.Button(row, text="下载模型",
                                  command=self._download_model)
        self._dl_bar = ttk.Progressbar(row, length=170, maximum=100)
        self._refresh_model_status()

    def _refresh_model_status(self) -> None:
        engine = self._engine_var.get()
        ready = speech.engine_model_ready(self.cfg, engine)
        self._lbl_model.config(
            text=("✓ " if ready else "") + speech.engine_model_status(
                self.cfg, engine),
            fg=theme.SUCCESS if ready else theme.WARN)
        self._dl_bar.pack_forget()
        if ready or engine == "faster-whisper":
            self._btn_dl.pack_forget()
        else:
            self._btn_dl.pack(side="left", padx=(theme.SPACE_SM, 0))

    def _download_model(self) -> None:
        engine = self._engine_var.get()
        self._btn_dl.state(["disabled"])
        self._dl_bar.configure(value=0)
        self._dl_bar.pack(side="left", padx=(theme.SPACE_SM, 0))
        # 下载在后台线程跑，控件只能主线程碰，所以走队列
        events: "queue.Queue" = queue.Queue()

        def work() -> None:
            try:
                speech.download_engine_model(
                    self.cfg, engine,
                    progress=lambda d, t: events.put(("progress", (d, t))))
                events.put(("done", None))
            except Exception as e:  # noqa: BLE001
                events.put(("failed", e))

        threading.Thread(target=work, daemon=True).start()
        self._poll_download(events)

    def _poll_download(self, events) -> None:
        if not self._dlg.winfo_exists():
            return          # 窗口关了，后台还在下，但没人看进度了
        try:
            while True:
                kind, payload = events.get_nowait()
                if kind == "progress":
                    self._set_progress(*payload)
                    continue
                self._btn_dl.state(["!disabled"])
                if kind == "done":
                    self._refresh_model_status()
                else:
                    self._lbl_model.config(text=f"下载失败：{payload}",
                                           fg=theme.ERROR)
                    self._dl_bar.pack_forget()
                return
        except queue.Empty:
            pass
        self._dlg.after(100, lambda: self._poll_download(events))

    def _set_progress(self, done: int, total: int) -> None:
        pct = int(done * 100 / total) if total else 0
        self._dl_bar.configure(value=pct)
        self._lbl_model.config(
            text=f"正在下载 {done / 1048576:.0f}MB / {total / 1048576:.0f}MB"
                 f"（{pct}%）", fg=theme.TEXT_MUTED)

    def _current_mode(self) -> str:
        mode = self.cfg.get("auto_save_mode")
        if mode in ("score", "student", "manual"):
            return mode
        return "score" if self.cfg.get("auto_save", True) else "manual"

    # ---------------- 组件 ----------------
    def _section(self, parent: tk.Frame, title: str) -> tk.Frame:
        head = tk.Frame(parent, bg=theme.SURFACE)
        head.pack(fill="x", pady=(theme.SPACE_MD, theme.SPACE_XS))
        tk.Label(head, text=title, bg=theme.SURFACE, fg=theme.TEXT_MUTED,
                 font=self.fonts.small_bold).pack(side="left")
        line = tk.Frame(head, bg=theme.BORDER, height=1)
        line.pack(side="left", fill="x", expand=True, padx=(theme.SPACE_SM, 0),
                  pady=(8, 0))
        group = tk.Frame(parent, bg=theme.SURFACE)
        group.pack(fill="x")
        return group

    def _field(self, parent: tk.Frame, label: str, var: tk.StringVar,
               values: List[str], hint: str = "") -> ttk.Combobox:
        row = tk.Frame(parent, bg=theme.SURFACE)
        row.pack(fill="x", pady=theme.SPACE_XS)
        tk.Label(row, text=label, bg=theme.SURFACE, fg=theme.TEXT,
                 font=self.fonts.body, width=8, anchor="w").pack(side="left")
        combo = ttk.Combobox(row, textvariable=var, values=values,
                             state="readonly", width=24, font=self.fonts.body)
        combo.pack(side="left")
        if hint:
            tk.Label(row, text=hint, bg=theme.SURFACE, fg=theme.TEXT_FAINT,
                     font=self.fonts.small).pack(side="left",
                                                 padx=theme.SPACE_SM)
        return combo


HELP_TEXT = """打开表格
    点顶部「打开表格」选一份 .xlsx。表头需要有姓名列、若干道题的列和总分列，
    程序会自动识别，并在总分列右边补一列「核对」。

录入
    点「开始说话」后程序一直听，说完一句稍停就会自动执行：
      念「张三」          跳到该生所在行，重名时弹窗确认
      念「第一题十八分」  填到第一题
      念「10加5加5加3」   依次填入接下来几道题
      念「总分 62」       与程序算出的总分核对，不一致会在核对列标红

    念错了就重念一遍同一题，会直接覆盖；也可以点「撤销」退回上一步。

手动修改
    双击任意单元格可以直接改分数或姓名，回车确认。
    右键某一行可以设为当前学生，或清空该生已填的分数。

保存
    默认每填一个分数就写回 Excel，可以在设置里改成每录完一名学生保存，
    或者只在点「保存」时写盘。首次打开时原文件会备份到 backups/。"""


class HelpDialog:
    def __init__(self, parent: tk.Misc, fonts: theme.Fonts):
        self.parent = parent
        self.fonts = fonts

    def show(self) -> None:
        dlg, body = _make_dialog(self.parent, "使用说明", resizable=True)
        tk.Label(body, text="使用说明", bg=theme.SURFACE, fg=theme.TEXT,
                 font=self.fonts.title, anchor="w").pack(fill="x",
                                                         pady=(0, theme.SPACE_SM))
        text = tk.Text(body, bg=theme.SURFACE_ALT, fg=theme.TEXT,
                       font=self.fonts.body, relief="flat", wrap="word",
                       width=52, height=22, padx=theme.SPACE_MD,
                       pady=theme.SPACE_MD, highlightthickness=1,
                       highlightbackground=theme.BORDER)
        text.insert("1.0", HELP_TEXT)
        text.configure(state="disabled")
        text.pack(fill="both", expand=True)
        ttk.Button(body, text="知道了", command=dlg.destroy,
                   style="Accent.TButton").pack(pady=(theme.SPACE_MD, 0))
        _center_on(dlg, self.parent)


class MicrophoneHelpDialog:
    """收不到声音时的排查指引，按平台给出不同步骤。"""

    def __init__(self, parent: tk.Misc, fonts: theme.Fonts):
        self.parent = parent
        self.fonts = fonts

    def show(self) -> None:
        dlg, body = _make_dialog(self.parent, "没有听到声音")
        dlg.grab_set()
        tk.Label(body, text="程序收不到麦克风声音", bg=theme.SURFACE,
                 fg=theme.TEXT, font=self.fonts.title, anchor="w").pack(fill="x")
        tk.Label(body, text=platform_support.microphone_permission_hint(),
                 bg=theme.SURFACE, fg=theme.TEXT_MUTED, font=self.fonts.body,
                 justify="left", anchor="w").pack(fill="x",
                                                  pady=(theme.SPACE_SM, 0))
        buttons = tk.Frame(body, bg=theme.SURFACE)
        buttons.pack(fill="x", pady=(theme.SPACE_LG, 0))
        ttk.Button(buttons, text="知道了", command=dlg.destroy).pack(side="right")
        ttk.Button(buttons, text="打开系统设置",
                   command=lambda: (platform_support.open_microphone_settings(),
                                    dlg.destroy()),
                   style="Accent.TButton").pack(side="right",
                                                padx=(0, theme.SPACE_SM))
        _center_on(dlg, self.parent)
