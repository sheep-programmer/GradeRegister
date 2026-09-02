"""主窗口：顶栏 + 语音卡片 + 成绩表格 + 状态栏。

交互：
    点「开始说话」进入连续听写，说完一句稍停即自动执行（也可在设置里
    改成按住说话）；识别文本交给状态机流转：名字选中学生、分数填入题号列、
    「总分XX」触发核对。重名或没听清时弹窗让老师点选。
    表格支持单击选中学生、双击改分、右键清空。
"""
from __future__ import annotations

import importlib.util
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import List, Optional

from .. import parser, platform_support, speech
from ..config import load_config, save_config
from ..excel_io import check_mark_text, load_sheet, save_sheet
from ..recorder import SILENT_LEVEL, Recorder
from ..state import AppState
from . import dialogs, theme
from .widgets import (HeardLog, LevelMeter, MessageBar, ProgressBadge,
                      RoundButton)


class GradeApp(tk.Tk):
    def __init__(self, cfg: dict, open_path: Optional[str] = None):
        super().__init__()
        self.cfg = cfg
        self.state = AppState(cfg=cfg)
        self.engine_ready = False
        self._auto_open = open_path
        self._rec_queue: "queue.Queue" = queue.Queue()
        self.recorder = Recorder(cfg, self._emit)
        self._space_owns_rec = False   # 本次录音是否由空格启动
        self._btn_owns_rec = False     # 本次录音是否由按住按钮启动
        self._save_error_shown: Optional[str] = None   # 已提示过的保存错误

        self.title("成绩登记")
        geom = cfg.get("window_geometry") or ""
        if geom:
            # 恢复上次窗口位置与大小；外接屏拔掉后位置可能飘到屏外，
            # 交给 sanitize_geometry 校验，不合规就回落到默认尺寸
            geom = platform_support.sanitize_geometry(
                geom, self.winfo_screenwidth(), self.winfo_screenheight())
        self.geometry(geom or "1200x800")
        self.minsize(1000, 660)
        self.fonts = theme.apply(self)
        self.scale = platform_support.dpi_scaling(self)

        self._build_menu()
        self._build_header()
        self._build_voice_bar()
        self._build_statusbar()
        self._build_body()

        self.bind_all("<KeyPress-space>", self._on_space_press)
        self.bind_all("<KeyRelease-space>", self._on_space_release)
        for key, command in (("s", self.save_now), ("o", self.open_excel),
                             ("z", self.undo)):
            _, sequence = platform_support.accelerator(key)
            self.bind_all(sequence, lambda _e, c=command: c())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.after(120, self._poll_queue)
        self.after(150, self._auto_open_initial)
        self.after(500, self._ensure_engine)

    # ================================================================= 布局
    def _build_menu(self) -> None:
        menubar = tk.Menu(self)
        open_label, open_seq = platform_support.accelerator("o")
        save_label, save_seq = platform_support.accelerator("s")
        undo_label, undo_seq = platform_support.accelerator("z")

        m_file = tk.Menu(menubar, tearoff=0)
        m_file.add_command(label="打开表格…", accelerator=open_label,
                           command=self.open_excel)
        m_file.add_command(label="保存", accelerator=save_label,
                           command=self.save_now)
        m_file.add_command(label="在 Excel 中打开", command=self.open_in_excel)
        m_file.add_separator()
        m_file.add_command(label="退出", command=self._on_close)
        menubar.add_cascade(label="文件", menu=m_file)

        m_edit = tk.Menu(menubar, tearoff=0)
        m_edit.add_command(label="撤销上一步", accelerator=undo_label,
                           command=self.undo)
        m_edit.add_command(label="核对当前学生总分", command=self.ask_total)
        m_edit.add_separator()
        m_edit.add_command(label="设置…", command=self.open_settings)
        menubar.add_cascade(label="操作", menu=m_edit)

        m_help = tk.Menu(menubar, tearoff=0)
        m_help.add_command(label="使用说明", command=self.show_help)
        m_help.add_command(label="麦克风没声音？", command=self.show_mic_help)
        m_help.add_command(label="打开日志文件夹", command=self.open_log_folder)
        menubar.add_cascade(label="帮助", menu=m_help)
        self.config(menu=menubar)

    def _build_header(self) -> None:
        header = tk.Frame(self, bg=theme.SURFACE)
        header.pack(fill="x", side="top")
        tk.Frame(self, bg=theme.BORDER, height=1).pack(fill="x", side="top")

        left = tk.Frame(header, bg=theme.SURFACE)
        left.pack(side="left", padx=theme.SPACE_XL, pady=theme.SPACE_SM)
        tk.Label(left, text="成绩登记", bg=theme.SURFACE, fg=theme.TEXT,
                 font=self.fonts.title).pack(side="left")
        self.lbl_file = tk.Label(left, text="尚未打开表格", bg=theme.SURFACE,
                                 fg=theme.TEXT_MUTED, font=self.fonts.small)
        self.lbl_file.pack(side="left", padx=(theme.SPACE_MD, 0), pady=(4, 0))

        right = tk.Frame(header, bg=theme.SURFACE)
        right.pack(side="right", padx=theme.SPACE_XL, pady=theme.SPACE_XS)
        for text, command, style in (
                ("设置", self.open_settings, "TButton"),
                ("在 Excel 中打开", self.open_in_excel, "TButton"),
                ("撤销", self.undo, "TButton"),
                ("核对总分", self.ask_total, "TButton"),
                ("保存", self.save_now, "TButton"),
                ("打开表格", self.open_excel, "Accent.TButton")):
            ttk.Button(right, text=text, command=command, style=style).pack(
                side="right", padx=(theme.SPACE_SM, 0))

    def _build_voice_bar(self) -> None:
        """操作条：按钮 + 当前学生 + 进度 + 电平 + 实时识别，只占一行。"""
        bar = tk.Frame(self, bg=theme.SURFACE)
        bar.pack(fill="x", side="top")
        inner = tk.Frame(bar, bg=theme.SURFACE)
        inner.pack(fill="x", padx=theme.SPACE_XL, pady=theme.SPACE_XS)

        # 按四种可能文字里最宽的那个定宽：否则切到「按住说话（或按住空格）」
        # 时按钮会先撑开、一开始录音又缩回去，用起来一跳一跳
        self.btn_talk = RoundButton(
            inner, text=self._talk_idle_text(), command=self._on_talk_click,
            on_press=self._on_talk_press, on_release=self._on_talk_release,
            font=self.fonts.body_bold, height=40,
            width=self._talk_button_width())
        self.btn_talk.pack(side="left")
        self.btn_talk.set_enabled(False)

        self.meter = LevelMeter(inner, height=22)
        self.meter.pack(side="right", padx=(theme.SPACE_MD, 0))

        who = tk.Frame(inner, bg=theme.SURFACE)
        who.pack(side="left", padx=(theme.SPACE_LG, 0))
        self.lbl_student = tk.Label(who, text="还没有选中学生",
                                    bg=theme.SURFACE, fg=theme.TEXT_FAINT,
                                    font=self.fonts.display)
        self.lbl_student.pack(side="left")
        self.lbl_progress = tk.Label(who, text="", bg=theme.SURFACE,
                                     fg=theme.TEXT_MUTED, font=self.fonts.small)
        self.lbl_progress.pack(side="left", padx=(theme.SPACE_SM, 0), pady=(6, 0))
        self.progress = ProgressBadge(who)
        self.progress.pack(side="left", padx=(theme.SPACE_SM, 0), pady=(6, 0))

        self.lbl_partial = tk.Label(inner, text="", bg=theme.SURFACE,
                                    fg=theme.TEXT_MUTED, font=self.fonts.small,
                                    anchor="w")
        self.lbl_partial.pack(side="left", fill="x", expand=True,
                              padx=(theme.SPACE_LG, 0))

        self.message = MessageBar(bar, self.fonts.body, self.fonts.body_bold)
        self.message.pack(fill="x", padx=theme.SPACE_XL,
                          pady=(0, theme.SPACE_SM))
        tk.Frame(self, bg=theme.BORDER, height=1).pack(fill="x", side="top")
        self.bind("<Configure>", self._on_window_resize)

    def _build_statusbar(self) -> None:
        tk.Frame(self, bg=theme.BORDER, height=1).pack(fill="x", side="bottom")
        bar = tk.Frame(self, bg=theme.SURFACE)
        bar.pack(fill="x", side="bottom")
        self.lbl_status = tk.Label(bar, text="语音引擎：正在启动…",
                                   bg=theme.SURFACE, fg=theme.TEXT_MUTED,
                                   font=self.fonts.tiny, anchor="w")
        self.lbl_status.pack(side="left", padx=theme.SPACE_XL,
                             pady=theme.SPACE_XS)
        # 下载模型时才现身
        self.dl_bar = ttk.Progressbar(bar, length=180, mode="determinate",
                                      maximum=100)
        tk.Label(bar, text=platform_support.platform_name(), bg=theme.SURFACE,
                 fg=theme.TEXT_FAINT, font=self.fonts.tiny).pack(
            side="right", padx=theme.SPACE_XL)

    def _build_body(self) -> None:
        """表格是主体，识别记录挂在下方；中间的分隔条可以随手拖。"""
        paned = ttk.PanedWindow(self, orient="vertical")
        paned.pack(fill="both", expand=True, padx=theme.SPACE_XL,
                   pady=(theme.SPACE_SM, theme.SPACE_MD))
        self.paned = paned

        table_pane = tk.Frame(paned, bg=theme.BG)
        paned.add(table_pane, weight=1)     # 窗口变大时增量全给表格
        self._build_table(table_pane)

        log_pane = tk.Frame(paned, bg=theme.BG)
        paned.add(log_pane, weight=0)
        self.heard = HeardLog(log_pane, self.fonts.small, self.fonts.small,
                              lines=2)
        self.heard.pack(fill="both", expand=True, pady=(theme.SPACE_SM, 0))

    def _build_table(self, parent) -> None:
        import tksheet

        shell = tk.Frame(parent, bg=theme.SURFACE, highlightthickness=1,
                         highlightbackground=theme.BORDER)
        shell.pack(fill="both", expand=True)

        row_height = theme.scaled(32, self.scale)
        self.sheet = tksheet.Sheet(
            shell, column_width=theme.scaled(100, self.scale),
            editable=True, empty_display=" ",
            **theme.sheet_options(self.fonts, row_height))
        self.sheet.pack(fill="both", expand=True, padx=1, pady=1)
        self.sheet.set_index_width(theme.scaled(60, self.scale))
        self.sheet.table_align("center")
        self.sheet.header_align("center")

        # 不开这些，单击选格、滚轮、方向键全都不响应。
        # 插入/删除行、排序、粘贴不能开：会打乱行与表格模型的对应关系
        # select_all 同时管三件事：左上角全选三角是否显示、点它是否全选、
        # Cmd/Ctrl+A 是否可用。不开的话左上角连三角都画不出来
        self.sheet.enable_bindings(
            "single_select", "drag_select", "row_select", "column_select",
            "arrowkeys", "copy", "edit_cell", "rc_select", "select_all",
            "column_width_resize", "double_click_column_resize",
            "row_height_resize")

        self._menu_row: Optional[int] = None
        self._menu_col: Optional[int] = None
        self._menu_area = "cell"      # 右键点的是 cell / row / column / all
        self.sheet.extra_bindings([
            ("cell_select", self._on_cell_select),
            ("shift_cell_select", self._on_cell_select),
            ("end_edit_cell", self._on_sheet_edit),
        ])
        platform_support.bind_right_click(self.sheet, self._on_sheet_right)
        # 键盘焦点会落在被点的那块画布上：点行号在 RI、点列头在 CH、
        # 点左上角全选在 TL。只绑 MT 的话，全选之后按 Delete 没人接
        for canvas in (self.sheet.MT, self.sheet.RI, self.sheet.CH,
                       self.sheet.TL):
            for seq in ("<Delete>", "<BackSpace>"):
                canvas.bind(seq, self._on_delete_key)

        self.row_menu = tk.Menu(self, tearoff=0)

    def _on_window_resize(self, event) -> None:
        # 建窗过程中会先收到一个极小的宽度，照着它折行会把提示条撑成三行
        if event.widget is self and event.width > 400:
            self.message.set_wraplength(event.width - theme.SPACE_XL * 4)

    # ================================================================= 引擎
    def _emit(self, kind: str, payload) -> None:
        """录音线程的事件入口：一律排队，由主线程消费。"""
        self._rec_queue.put((kind, payload))

    def _ensure_engine(self) -> None:
        engine_name = self.cfg.get("engine", "sense-voice")
        need_download = not speech.engine_model_ready(self.cfg)
        if need_download:
            size = speech.ENGINE_SIZE_MB.get(engine_name, 0)
            self._notify(f"首次使用要先下载中文语音模型（约 {size}MB），"
                         "下载完才能开始说话；这期间可以先双击单元格手动录入", "info")
            self.btn_talk.configure_look(text="正在下载模型…")
        elif engine_name == "faster-whisper":
            self.lbl_status.config(
                text="语音引擎：启动中（faster-whisper 首次加载约 10~30 秒）…")
        else:
            self.lbl_status.config(text="语音引擎：启动中…")

        def work():
            try:
                self._check_engine_deps()
                self._auto_pick_mic()
                speech.auto_ensure_engine(
                    self.cfg,
                    progress=lambda done, total: self._emit(
                        "dl_progress", (done, total)))
                # 模型齐了才建引擎，建好了「开始说话」按钮才亮
                self.recorder.engine = speech.create_engine(self.cfg)
                self._apply_vocab()
                self._emit("engine_ready", "")
            except Exception as e:  # noqa: BLE001
                self._emit("engine_error", str(e))

        threading.Thread(target=work, daemon=True).start()

    def _check_engine_deps(self) -> None:
        """缺录音/识别依赖时提前报错，避免白白下载模型。"""
        engine = self.cfg.get("engine", "sense-voice")
        need = ["sounddevice"]
        need.append({"faster-whisper": "faster_whisper",
                     "sherpa": "sherpa_onnx",
                     "sense-voice": "sherpa_onnx"}.get(engine, "vosk"))
        missing = [m for m in need if importlib.util.find_spec(m) is None]
        if missing:
            raise RuntimeError("缺少依赖: " + ", ".join(missing))

    def _apply_vocab(self) -> None:
        """把学生名单与数字用语注入识别词表（支持词表约束的引擎才生效）。"""
        engine = self.recorder.engine
        model = self.state.model
        if engine is None or model is None or not model.students:
            return
        names = [s.name for s in model.students]
        headers = self.state._score_headers()
        try:
            engine.set_grammar(speech.build_grade_vocab(names, headers))
            print(f"[voice] 已注入识别词表：{len(names)} 个姓名、"
                  f"{len(headers)} 个题目表头", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 词表注入失败（不影响使用）: {e}", flush=True)

    def _auto_pick_mic(self) -> None:
        """挑一个真正收得到声音的麦克风。

        结果只用于本次运行、不写回配置——蓝牙耳机连上或断开时设备索引会变，
        固定下来反而会失效。手动指定的设备若已拔掉/收不到音，也回退到自动挑选。
        """
        try:
            import sounddevice as sd
            if not any(d.get("max_input_channels", 0) > 0
                       for d in sd.query_devices()):
                return
            manual = self.cfg.get("device")
            if manual is not None:
                # 尊重手动选择，但先验证它还收得到声音；失效就回退自动挑
                if speech.device_has_sound(int(manual)):
                    return
                print(f"[mic] 手动设备 {manual} 收不到声音，回退自动挑选", flush=True)
            idx = speech.pick_mic_device()
            if idx is None:
                self._emit("mic_silent", "")
                return
            # 只记在录音器上，不写回 cfg：写回去的话，老师之后随手在设置里
            # 点一次「保存」，这个本次挑中的编号就被当成他的选择固化了
            self.recorder.device_override = idx
            self._emit("mic_picked", sd.query_devices(idx).get("name", f"设备{idx}"))
        except Exception as exc:  # noqa: BLE001
            # 回退到系统默认设备继续跑，但要留下痕迹：
            # Windows 上录不到音时，这行往往是唯一的线索
            print(f"[warn] 自动挑选麦克风失败，改用系统默认: {exc}", flush=True)

    def _on_engine_error(self, err: str) -> None:
        """区分缺依赖与模型未就绪，给出可操作的指引。"""
        self._hide_download()
        engine = self.cfg.get("engine", "sense-voice")
        missing = [name for mod, name in (("sounddevice", "sounddevice（录音）"),
                                          ("numpy", "numpy"))
                   if importlib.util.find_spec(mod) is None]
        if missing:
            self.lbl_status.config(text="语音引擎不可用（缺少依赖）")
            self._notify("语音引擎不可用，但手动输入不受影响", "warn")
            messagebox.showwarning(
                "语音引擎不可用",
                "缺少依赖：" + "、".join(missing) + "\n\n"
                "请关闭程序后运行：\n    pip install -r requirements.txt\n\n"
                "装好后重启程序即可使用语音。现在仍可双击单元格手动录入。")
            return
        sizes = {"vosk": "约 42MB", "sense-voice": "约 228MB"}
        if engine in sizes:
            self.lbl_status.config(text="语音引擎不可用（模型未就绪）")
            if messagebox.askyesno(
                    "语音模型未就绪",
                    f"中文语音模型还没准备好：\n{err}\n\n"
                    f"是否现在下载（{sizes[engine]}，下载完自动启用）？"):
                self._start_model_download(sizes[engine])
            return
        self.lbl_status.config(text="语音引擎不可用")
        self._notify(f"语音引擎启动失败：{err}；仍可双击单元格手动录入", "error")

    def _start_model_download(self, size: str = "") -> None:
        self._notify(f"正在下载语音模型（{size}）…", "info")
        self.lbl_status.config(text="正在下载语音模型 0% …")
        self.btn_talk.set_enabled(False)
        self.btn_talk.configure_look(text="正在下载模型…")

        def work():
            try:
                speech.auto_ensure_engine(
                    self.cfg,
                    progress=lambda done, total: self._emit(
                        "dl_progress", (done, total)))
                self.recorder.engine = speech.create_engine(self.cfg)
                self._apply_vocab()
                self._emit("dl_done", "")
            except Exception as e:  # noqa: BLE001
                self._emit("engine_error", str(e))

        threading.Thread(target=work, daemon=True).start()

    # ================================================================= 表格
    def _auto_open_initial(self) -> None:
        path = self._auto_open or (self.cfg.get("last_file") or "")
        if path and os.path.exists(path):
            self.open_excel(path, quiet=True)
        else:
            self._notify("点右上角「打开表格」选一份成绩表，然后念第一个学生的名字",
                         "info")

    def open_excel(self, path: Optional[str] = None, quiet: bool = False) -> None:
        if path is None:
            path = filedialog.askopenfilename(
                title="选择成绩表（.xlsx）",
                filetypes=[("Excel 工作簿", "*.xlsx"), ("所有文件", "*.*")])
        if not path:
            return
        if not self._release_from_excel(path, quiet=quiet):
            return
        print(f"[open] 加载表格: {path}", flush=True)
        try:
            model = load_sheet(path, self.cfg, backup=True)
        except Exception as e:  # noqa: BLE001
            print(f"[open] 加载失败: {e}", flush=True)
            hint = platform_support.default_excel_hint()
            detail = f"{e}\n\n{hint}" if hint else str(e)
            if quiet:
                self._notify(f"自动打开失败：{e}", "error")
            else:
                messagebox.showerror("打开失败", detail)
            return
        self.state.load(model)
        self._build_columns()
        self._refresh_table()
        self.lbl_file.config(text=self._shorten_path(model.path))
        self._apply_vocab()
        self._remember_last_file(path)
        self._after_action()
        self._notify(
            f"已加载 {len(model.students)} 名学生、{model.score_count} 道题，"
            "念第一个学生的名字开始", "success")

    def _release_from_excel(self, path: str, quiet: bool = False) -> bool:
        """表格正被 Excel 开着就先让它交出来；返回 False 表示别继续加载。

        两边同时开同一份表是纯粹的坏事：Windows 上本程序一次也写不进去，
        macOS 上写得进去但 Excel 不重读盘，它一保存就把分数全盖掉。
        只关这一个工作簿，Excel 里别的表格不动；老师在这份表里没保存的
        改动会先存下来，接着加载到的就是他最新的内容。
        """
        while platform_support.excel_holds_file(path):
            ok, why = platform_support.close_excel_workbook(path)
            if ok:
                self._notify(f"{why}（避免两边同时改同一份表格）", "info")
                return True
            if platform_support.file_is_writable(path):
                # ~$ 标记还在但文件写得动：Excel 上次崩溃留下的残留，不用管
                print(f"[open] 忽略残留的占用标记: {why}", flush=True)
                return True
            if quiet:
                self._notify(
                    f"表格正被 Excel 占用（{why}），请在 Excel 里关掉它，"
                    "再点「打开表格」", "error")
                return False
            if not messagebox.askretrycancel(
                    "表格正被 Excel 打开",
                    f"{why}。\n\n"
                    "同时开着两边会互相覆盖，请先在 Excel 里关掉这份表格，"
                    "然后点「重试」。",
                    icon="warning", parent=self):
                return False
        return True

    def _remember_last_file(self, path: str) -> None:
        """只把打开过的文件写回配置，不带上本次自动挑选的麦克风等运行期状态。"""
        try:
            self.cfg["last_file"] = os.path.abspath(path)
            disk = load_config()
            disk["last_file"] = self.cfg["last_file"]
            save_config(disk)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _shorten_path(path: str, keep: int = 46) -> str:
        if len(path) <= keep:
            return path
        return "…" + path[-keep:]

    def _build_columns(self) -> None:
        m = self.state.model
        assert m is not None
        headers = [h if h else f"列{i + 1}" for i, h in enumerate(m.header)]
        self.sheet.headers(headers)
        for i in range(len(headers)):
            self.sheet.column_width(column=i, width=self._column_floor(i))
        self._autofit_columns()
        self.sheet.redraw()

    def _column_floor(self, col: int) -> int:
        """这一列至少多宽。窄表格不至于挤成一条，宽内容再由自适应加长。"""
        m = self.state.model
        name_col = m.name_col if m is not None else 0
        return theme.scaled(140 if col == name_col else 100, self.scale)

    def _autofit_columns(self) -> None:
        """列宽取「放得下内容」与下限之间的较大者。

        核对列不一致时写的是「不一致（报18 算19）」，比表头长一大截；
        固定列宽要么把它截掉，要么让每一列都白留一截空白。
        分数只有一两位，短于下限，所以填分过程中列宽不会来回跳。
        set_sheet_data 会把列宽重置回默认，所以每次刷完数据都要重来一遍。
        """
        m = self.state.model
        if m is None:
            return
        current = self.sheet.get_column_widths()
        for col in range(len(m.header)):
            need = max(self.sheet.get_column_text_width(col),
                       self._column_floor(col))
            if col >= len(current) or round(current[col]) != round(need):
                self.sheet.column_width(column=col, width=need, redraw=False)

    def _refresh_table(self) -> None:
        m = self.state.model
        if m is None:
            return
        current_row = self.state.current.row if self.state.current else None
        row_to_idx = {stu.row: i for i, stu in enumerate(m.students)}

        data, index = [], []
        for stu in m.students:
            values = [""] * len(m.header)
            values[m.name_col] = stu.name
            for col, val in stu.scores.items():
                values[col] = f"{val:g}"
            if stu.total is not None:
                values[m.total_col] = f"{stu.total:g}"
            if stu.checked is not None:
                values[m.check_col] = ("✓" if stu.checked
                                       else check_mark_text(stu))
            data.append(values)
            index.append(stu.row + 1)      # Excel 里的实际行号，跟 Excel 对得上

        # set_sheet_data 会把列宽重置回默认，自适应必须排在它后面
        self.sheet.set_sheet_data(data, redraw=False)
        self.sheet.row_index(index)
        self._autofit_columns()
        self.sheet.dehighlight_all()
        self._paint_rows(m, row_to_idx, current_row)
        if current_row is not None and current_row in row_to_idx:
            self.sheet.see(row_to_idx[current_row])
        self.sheet.redraw()

    def _paint_rows(self, m, row_to_idx: dict, current_row: Optional[int]) -> None:
        """斑马纹、核对标记、当前行与待填格的高亮。"""
        for stu in m.students:
            idx = row_to_idx[stu.row]
            if idx % 2 == 1:
                self.sheet.highlight_rows(rows=[idx], bg=theme.SURFACE_ALT,
                                          redraw=False)
            if stu.checked is not None:
                bg, fg = ((theme.SUCCESS_SOFT, theme.SUCCESS) if stu.checked
                          else (theme.ERROR_SOFT, theme.ERROR))
                self.sheet.highlight_cells(row=idx, column=m.check_col,
                                           bg=bg, fg=fg, redraw=False)
        if current_row is None or current_row not in row_to_idx:
            return
        cur_idx = row_to_idx[current_row]
        # 十字定位照 Excel 的做法：整行高亮的只有当前学生自己那一行，
        # 「在填哪一题」靠列表头指示——把整列铺黄会连别人的分数一起染上，
        # 而且斑马纹行的行高亮会压过列高亮，那一列黄一格灰一格更难看
        self.sheet.highlight_rows(rows=[cur_idx], bg=theme.CURRENT_ROW,
                                  redraw=False)
        self.sheet.highlight_cells(row=cur_idx, canvas="index",
                                   bg=theme.CROSS_CELL,
                                   fg=theme.CROSS_CELL_FG, redraw=False)
        if self.state.phase != "scoring" or self.state.current is None:
            return
        # 念过题号的话就指着那一题，否则指下一个空题
        blanks = [c for c in m.score_cols if c not in self.state.current.scores]
        target = self.state.pending_col()
        if target is None and blanks:
            target = blanks[0]
        if target is not None:
            self.sheet.highlight_cells(column=target, canvas="header",
                                       bg=theme.CROSS_CELL,
                                       fg=theme.CROSS_CELL_FG, redraw=False)
            # 单元格高亮盖过行高亮，交叉那一格单独上深色
            self.sheet.highlight_cells(row=cur_idx, column=target,
                                       bg=theme.CROSS_CELL,
                                       fg=theme.CROSS_CELL_FG, redraw=False)
        for row, _name in (self.state._pending_choices or []):
            if row in row_to_idx:
                self.sheet.highlight_rows(rows=[row_to_idx[row]],
                                          bg=theme.PENDING_CELL, redraw=False)
        last = self.state._last_edit_col
        if last is not None:
            self.sheet.highlight_cells(row=cur_idx, column=last,
                                       bg=theme.EDITED_CELL, fg="white",
                                       redraw=False)

    # ================================================================= 动作
    def _on_text(self, text: str) -> None:
        result = self.state.handle_text(text)
        # 显示纠正后的文本：听成「五米」而名单里是「吴敏」时只显示「吴敏」
        heard = result.heard_text or text
        self.lbl_partial.config(text=f"听到：{heard}")
        self.heard.append(heard)
        if result.select_choices and result.ok:
            # 重名：必须挑一个才能往下走，用模态窗
            self._beep("pick")
            self._show_choice_dialog(result.select_choices)
        elif result.select_choices:
            # 只是没太听清：不弹窗挡住语音流，把候选高亮在表里，
            # 老师说一句「第一个」就能定，也可以直接点那一行
            self._beep("pick")
            self._notify(result.message, "warn")
        elif result.message:
            # 状态机指定了音效就照它来（核对通过/不一致各有各的声音），
            # 没指定才退回按成败取默认
            self._beep(result.sound or ("ok" if result.ok else "warn"))
            level = ("success" if result.ok
                     else "error" if result.sound == "error" else "warn")
            self._notify(result.message, level)
        self._report_save_error()
        self._after_action()

    def _save_before_exit(self) -> bool:
        """退出前落盘。返回 False 表示老师选择留在程序里，先别退。

        Windows 上 Excel 会独占锁住表格，这时写盘必然失败。原来只往日志里
        记一行就把窗口销毁了，一节课的分数跟着没——现在必须问过老师。
        """
        while True:
            try:
                save_sheet(self.state.model, self.cfg,
                           write_formula=self.cfg.get("write_formula", True))
                return True
            except Exception as e:  # noqa: BLE001
                print(f"[warn] 退出前保存失败: {e}", flush=True)
                reason = ("表格正被 Excel 或其他程序占用，写不进去。"
                          if isinstance(e, PermissionError) else f"{e}")
                answer = messagebox.askyesnocancel(
                    "退出前没能保存",
                    f"{reason}\n\n"
                    "「是」  关掉占用它的程序后重试保存\n"
                    "「否」  放弃这些分数直接退出\n"
                    "「取消」留在本程序里，稍后自己点「保存」",
                    icon="warning", default="cancel", parent=self)
                if answer is None:
                    return False        # 留下
                if answer is False:
                    return True         # 放弃改动，照旧退出
                # answer is True：老师说已经关掉了，回到循环再试一次

    def _beep(self, kind: str) -> None:
        """提示音的唯一出口，默认关着。

        一节课要念几百句，每句都响一声很吵；办公室或办公桌相邻时更不合适。
        想要声音反馈的老师在设置里自己打开。
        """
        if self.cfg.get("sound_enabled", False):
            platform_support.play_sound(kind)

    def _report_save_error(self) -> None:
        """自动保存写不进去时必须让老师看见，别让分数只留在内存里。"""
        err = self.state.save_error
        if err and err != self._save_error_shown:
            self._save_error_shown = err
            self._beep("error")
            self._notify(err, "error")
            self.heard.append(f"（{err}）", "note")
        elif not err:
            self._save_error_shown = None

    def _after_action(self) -> None:
        m = self.state.model
        if m is None:
            return
        cur = self.state.current
        if cur is None:
            self.lbl_student.config(text="还没有选中学生", fg=theme.TEXT_FAINT)
            self.lbl_progress.config(text="")
            self.progress.set_progress(0, 0)
        else:
            filled = m.filled_count(cur)
            self.lbl_student.config(text=cur.name, fg=theme.TEXT)
            self.lbl_progress.config(
                text=f"第 {cur.row + 1} 行 · 第 {self.state.student_no(cur)} 个"
                     f" · {filled}/{m.score_count} 题")
            self.progress.set_progress(filled, m.score_count)
        self._refresh_table()

    def ask_total(self) -> None:
        if self.state.current is None:
            self._notify("还没有选中学生，先念一个名字", "warn")
            return
        self.state.phase = "total"
        self._after_action()
        self._notify(f"请念 {self.state.current.name} 的总分，例如「总分 62」", "info")

    def open_in_excel(self) -> None:
        path = self.state.model.path if self.state.model else self.cfg.get("last_file")
        if not path or not os.path.exists(path):
            self._notify("还没有打开表格", "warn")
            return
        warn = platform_support.excel_open_warning()
        if warn and self.state.auto_save_mode() != "manual":
            if not messagebox.askokcancel("要现在用 Excel 打开吗", warn,
                                          icon="warning", parent=self):
                return
        try:
            platform_support.open_in_default_app(path)
            note = "；期间自动保存会失败" if warn else ""
            self._notify(
                f"已用默认程序打开 {os.path.basename(path)}{note}",
                "warn" if warn else "success")
        except Exception as e:  # noqa: BLE001
            self._notify(f"打开失败：{e}", "error")

    def undo(self) -> None:
        result = self.state.undo()
        self._after_action()
        self._notify(result.message, "info" if result.ok else "warn")

    def save_now(self) -> None:
        if self.state.model is None:
            self._notify("还没有打开表格", "warn")
            return
        try:
            save_sheet(self.state.model, self.cfg,
                       write_formula=self.cfg.get("write_formula", True))
            self._notify("已保存", "success")
        except PermissionError:
            self._notify("保存失败：文件正被其他程序占用，请先关闭 Excel 再试", "error")
        except Exception as e:  # noqa: BLE001
            self._notify(f"保存失败：{e}", "error")

    # ================================================================= 录音
    def _is_hold(self) -> bool:
        return bool(self.cfg.get("hold_to_talk", False))

    def _is_continuous(self) -> bool:
        return not self._is_hold() and bool(self.cfg.get("continuous", True))

    # 说话按钮在各模式下的全部文字，宽度按最宽的那个定
    TALK_LABELS = ("开始说话", "开始录音", "按住说话（或按住空格）",
                   "正在听…点此结束", "正在听…松开结束")

    def _talk_button_width(self) -> int:
        from tkinter import font as tkfont
        try:
            measure = tkfont.Font(root=self, font=self.fonts.body_bold).measure
        except Exception:  # noqa: BLE001
            return theme.scaled(220, self.scale)
        return max(measure(t) for t in self.TALK_LABELS) + 32

    def _talk_idle_text(self) -> str:
        if self._is_hold():
            return "按住说话（或按住空格）"
        if self._is_continuous():
            return "开始说话"
        return "开始录音"

    def _talk_recording_text(self) -> str:
        if self._is_hold():
            return "正在听…松开结束"
        return "正在听…点此结束"

    def _set_talk_idle(self) -> None:
        self.btn_talk.configure_look(text=self._talk_idle_text(),
                                     fill=theme.PRIMARY,
                                     hover=theme.PRIMARY_HOVER,
                                     disabled=theme.PRIMARY_DISABLED)
        self.btn_talk.set_enabled(self.engine_ready)

    def _set_talk_recording(self) -> None:
        self.btn_talk.configure_look(text=self._talk_recording_text(),
                                     fill=theme.RECORDING,
                                     hover=theme.RECORDING_HOVER)
        self.btn_talk.set_enabled(True)

    def _on_talk_press(self) -> None:
        """按住说话模式下，按下按钮就开录。

        RoundButton 只在松开时触发 command，所以不接这个回调的话，
        「按住说话」按钮怎么按都不会开始录音。
        """
        if not self._is_hold() or self.recorder.recording:
            return
        if self._start_recording():
            self._btn_owns_rec = True

    def _on_talk_release(self) -> bool:
        """松开按钮。返回 True 表示这次是长按，别再当成一次点击。"""
        if not self._btn_owns_rec:
            return False
        self._btn_owns_rec = False
        self._stop_recording()
        return True

    def _on_talk_click(self) -> None:
        if self._is_hold():
            if self.recorder.recording:
                self._stop_recording()
            else:
                self._notify("请按住按钮或空格键说话，松手自动识别", "info")
            return
        if self.recorder.recording:
            was_continuous = self._is_continuous()
            self._stop_recording()
            self._set_talk_idle()
            self._notify("已结束" if was_continuous else "录音结束，正在识别…", "info")
        else:
            self._start_recording()

    def _start_recording(self) -> bool:
        if not self.engine_ready:
            self._notify("语音引擎还在启动，等左下角显示「已就绪」再点", "warn")
            return False
        if self.recorder.busy:
            # 引擎不是线程安全的，上一次还在收尾就不能开新的
            self._notify("上一次识别还在收尾，稍等半秒再试", "warn")
            return False
        if not self.recorder.start(continuous=self._is_continuous()):
            return False
        self._set_talk_recording()
        self.lbl_partial.config(
            text="正在听，说完一句稍停即自动识别" if self._is_continuous()
            else "正在录音…")
        self.heard.append("（开始听）", "note")
        return True

    def _stop_recording(self) -> None:
        self.recorder.stop()
        self._space_owns_rec = False

    def _on_space_press(self, _event) -> None:
        # 空格只在「按住说话」模式下管录音。点击说话模式里不拦的话，
        # 随手碰一下空格就是「开录又立刻停」，白丢一句
        if not self._is_hold():
            return
        if isinstance(self.focus_get(), (tk.Entry, tk.Text, ttk.Entry)):
            return
        # 只有空格自己开的录音才由空格结束，避免误碰空格停掉按钮开的录音
        if not self.recorder.recording and self._start_recording():
            self._space_owns_rec = True

    def _on_space_release(self, _event) -> None:
        if self._space_owns_rec:
            self._stop_recording()

    # ================================================================= 队列
    def _poll_queue(self) -> None:
        """把录音线程发来的事件搬到主线程处理。

        音量是个仪表，一轮里只画最后一个值：会大量堆积的只有它，而每条
        都重绘画布，积压时能把界面冻住一秒以上。识别结果一类事件绝不设
        上限——漏处理一条就是丢一句话。
        """
        level = None
        try:
            while True:
                kind, payload = self._rec_queue.get_nowait()
                if kind == "level":
                    level = payload
                    continue
                self._handle_event(kind, payload)
        except queue.Empty:
            pass
        if level is not None:
            self._handle_event("level", level)
        self.after(120, self._poll_queue)

    def _handle_event(self, kind: str, payload) -> None:
        if kind == "partial":
            self.lbl_partial.config(text=f"识别中：{payload}".strip())
            self.heard.set_partial(payload)
        elif kind == "final":
            self._settle_button()
            self.meter.reset()
            self._on_text(payload)
        elif kind == "final_empty":
            self._beep("warn")
            self.lbl_partial.config(text="没有听到内容")
            self.heard.append("（这一句没听清）", "note")
            self._settle_button()
            self.meter.reset()
            if payload < SILENT_LEVEL:
                self._notify("这次完全没录到声音：靠近麦克风正常音量说话；"
                             "反复出现就在设置里换个麦克风", "warn")
            else:
                self._notify("没听清，请再说一次（念慢一点、离麦克风近一点）", "warn")
        elif kind == "thinking":
            self.lbl_partial.config(text="正在识别上一句…（可以继续念）")
        elif kind == "level":
            self.meter.set_level(min(1.0, float(payload) * 1.8))
        elif kind == "silent_warn":
            self._notify(self._silent_hint(), "warn")
        elif kind == "error":
            self.recorder.stop()
            self._space_owns_rec = False
            self._set_talk_idle()
            self.heard.append(f"（录音中断：{payload}）", "note")
            self._notify(str(payload), "error")
        elif kind == "engine_ready":
            self.engine_ready = True
            self._hide_download()
            self._set_talk_idle()
            self.lbl_status.config(
                text=f"语音引擎：{self.cfg.get('engine')} 已就绪 · "
                     f"麦克风：{self._current_mic_name()}")
        elif kind == "mic_picked":
            self.lbl_status.config(text=f"语音引擎：加载中 · 麦克风：{payload}")
        elif kind == "mic_silent":
            self.lbl_partial.config(text="没检测到麦克风声音：先检查系统麦克风权限")
            self.heard.append("（没检测到麦克风声音）", "note")
            self._notify("没检测到麦克风声音：请在「系统设置 → 隐私与安全性 → "
                         "麦克风」里打开本程序（或终端）的开关，再点一次「开始说话」",
                         "warn")
        elif kind == "engine_error":
            self._set_talk_idle()
            self._on_engine_error(str(payload))
        elif kind == "dl_progress":
            self._show_download(*payload)
        elif kind == "dl_done":
            self.engine_ready = True
            self._hide_download()
            self._set_talk_idle()
            self.lbl_status.config(
                text=f"语音引擎：{self.cfg.get('engine')} 已就绪（模型下载完成）")
            self._notify("语音模型下载完成，可以开始说话了", "success")

    def _show_download(self, done: int, total: int) -> None:
        if not self.dl_bar.winfo_manager():
            self.dl_bar.pack(side="left", padx=(0, theme.SPACE_MD))
        pct = int(done * 100 / total) if total else 0
        self.dl_bar.configure(value=pct)
        self.lbl_status.config(
            text=f"正在下载语音模型 {done / 1048576:.0f}MB / "
                 f"{total / 1048576:.0f}MB（{pct}%）")

    def _hide_download(self) -> None:
        if self.dl_bar.winfo_manager():
            self.dl_bar.pack_forget()

    def _settle_button(self) -> None:
        """连续听写时一句话结束仍在继续听，按钮保持录音态。"""
        if not (self.recorder.recording and self._is_continuous()):
            self._set_talk_idle()

    def _silent_hint(self) -> str:
        if self._is_hold():
            return "还没听到声音：对着麦克风说话，说完松开按钮"
        if self._is_continuous():
            return "还没听到声音：对着麦克风正常说话，说完一句稍停就会自动识别"
        return "还没听到声音：录音不会自动结束，说完再点一次按钮"

    def _current_mic_name(self) -> str:
        try:
            import sounddevice as sd
            device = self.recorder.active_device()
            if device is None:
                device = sd.default.device[0]
            if device is not None and int(device) >= 0:
                return sd.query_devices(int(device)).get("name", f"设备{device}")
        except Exception:  # noqa: BLE001
            pass
        return "系统默认"

    # ================================================================= 弹窗
    def _notify(self, text: str, kind: str = "info") -> None:
        self.message.show(text, kind)

    def _show_choice_dialog(self, choices: List[tuple]) -> None:
        def pick(row: int) -> None:
            act = self.state.activate_row(row)
            self._after_action()
            self._notify(act.message, "success" if act.ok else "warn")

        # 选人期间先停下听写：弹窗要等老师点，这段时间他常会自言自语
        # （「第一个…不对…第三个」），那些话照旧进状态机就会跳到别处去
        was_listening = self.recorder.recording
        if was_listening:
            self._stop_recording()
            self._set_talk_idle()
            self.lbl_partial.config(text="选人期间暂停听写…")
        self._notify("听到多个候选，请在弹窗里选择学生（听写已暂停）", "warn")
        dialogs.StudentChoiceDialog(self, self.fonts, choices, pick).show()
        if was_listening:
            self._resume_after_choice()

    def _resume_after_choice(self) -> None:
        """弹窗关掉后接着听。上一轮识别可能还在收尾，等它退出再开。"""
        if self.recorder.recording:
            return
        if self.recorder.busy:
            self.after(120, self._resume_after_choice)
            return
        if self._start_recording():
            self.heard.append("（选人完毕，继续听）", "note")

    def open_settings(self) -> None:
        dialogs.SettingsDialog(self, self.fonts, self.cfg, self._apply_settings).show()

    def _apply_settings(self, new_cfg: dict) -> None:
        # 必须先看键在不在：只传了部分设置时，缺 engine 会被当成「改成 None」，
        # 白白重启一次引擎，按钮跟着禁用几秒
        engine_changed = ("engine" in new_cfg
                          and new_cfg["engine"] != self.cfg.get("engine"))
        if "device" in new_cfg and new_cfg["device"] != self.cfg.get("device"):
            # 老师亲手换了麦克风：清掉本次自动挑中的，让新选择立刻生效
            self.recorder.device_override = None
        self.cfg.update(new_cfg)
        self.state.cfg = self.cfg
        save_config(new_cfg)      # 只写用户改过的项，不固化运行期状态
        if engine_changed:
            self._restart_engine()
        self._set_talk_idle()
        self._notify("设置已保存" + ("，正在切换语音引擎…" if engine_changed else ""),
                     "success")

    def _restart_engine(self) -> None:
        """切换识别引擎：不用重启程序，停掉旧引擎后后台加载新引擎。

        旧引擎对象直接丢弃（连同它的热词文件），引擎未就绪期间
        「开始说话」按钮保持禁用。
        """
        self.recorder.stop()
        self.recorder.engine = None
        self.engine_ready = False
        self._set_talk_idle()
        self._ensure_engine()

    def show_help(self) -> None:
        dialogs.HelpDialog(self, self.fonts).show()

    def show_mic_help(self) -> None:
        dialogs.MicrophoneHelpDialog(self, self.fonts).show()

    # ================================================================= 表格交互
    def _on_cell_select(self, event) -> None:
        """单击任意单元格即选中该行学生。"""
        # 选中类事件的 event.row 恒为 None，行号在 event.selected 里
        selected = getattr(event, "selected", None)
        row = getattr(selected, "row", None) if selected else None
        if row is None:
            row = getattr(event, "row", None)
        if row is None or self.state.model is None:
            return
        self._menu_row = int(row)
        self._activate_index(int(row), announce=True)

    def _activate_index(self, index: int, announce: bool) -> None:
        students = self.state.model.students if self.state.model else []
        if not (0 <= index < len(students)):
            return
        stu = students[index]
        if self.state.current is not None and self.state.current.row == stu.row:
            return
        self.state.activate_row(stu.row)
        self._after_action()
        current = self.state.current
        if announce and current is not None:
            self._notify(f"已选中 {current.name}（第 {current.row + 1} 行 · "
                         f"第 {self.state.student_no(current)} 个），请念分数",
                         "success")

    def _menu_zone(self, event) -> str:
        """右键点在表格的哪一块：cell / row / column / all。

        照 Excel 的分法给不同的菜单。只看 row/col 是否为 None 分不开
        列头和左上角——两者都没有行号。
        """
        widget = getattr(event, "widget", None)
        if widget is self.sheet.RI:
            return "row"
        if widget is self.sheet.CH:
            return "column"
        if widget is self.sheet.TL:
            return "all"
        return "cell"

    def _on_sheet_right(self, event) -> None:
        row = self.sheet.identify_row(event, allow_end=False)
        col = self.sheet.identify_column(event, allow_end=False)
        # 列头上右键没有行、行号列上右键没有列，两者各自记各自的：
        # 把列号的赋值挂在「有行」里面，列头右键就永远拿不到列，
        # 「清除整列」会一直是灰的
        self._menu_row = int(row) if row is not None else None
        self._menu_col = int(col) if col is not None else None
        self._menu_area = self._menu_zone(event)
        # 照 Excel：右键先把作用范围选出来，让菜单要动谁一目了然
        if self._menu_area == "all":
            self.sheet.select_all()
        elif self._menu_area == "row" and self._menu_row is not None:
            self.sheet.select_row(self._menu_row)
        elif self._menu_area == "column" and self._menu_col is not None:
            self.sheet.select_column(self._menu_col)
        elif row is not None:
            # 右键落在已选区域内就保留整片选区，否则改选这一格
            if (self._menu_col is None
                    or (self._menu_row, self._menu_col) not in self._selected_cells()):
                self.sheet.select_cell(int(row), int(col or 0))
        self._build_row_menu()
        self.row_menu.tk_popup(event.x_root, event.y_root)

    # ---------------- 清除 ----------------
    def _selected_cells(self) -> set:
        """当前选中的格子集合（拖选、整行选、整列选都归一成格子）。"""
        try:
            return {(int(r), int(c))
                    for r, c in self.sheet.get_selected_cells(
                        get_rows=True, get_columns=True)}
        except Exception:  # noqa: BLE001
            return set()

    def _score_targets(self, cells) -> list:
        """把格子坐标翻译成 [(学生, 列)]，只保留分数列。"""
        m = self.state.model
        if m is None:
            return []
        out = []
        for row, col in sorted(cells):
            if 0 <= row < len(m.students) and col in m.score_cols:
                out.append((m.students[row], col))
        return out

    def _build_row_menu(self) -> None:
        """按右键位置重建菜单，条目与排布尽量贴合 Excel。

        插入/删除行列一概不给：那会打乱表格行与名单的对应关系。
        够不着的目标置灰而不是隐藏，位置稳定，不用每次重新找。
        """
        menu = self.row_menu
        menu.delete(0, "end")
        m = self.state.model
        copy_key, _seq = platform_support.accelerator("c")
        area = getattr(self, "_menu_area", "cell")

        # 左上角只给一条：这里唯一说得通的操作就是清空全班
        if area == "all":
            menu.add_command(
                label="清除全部分数（全班所有题）", command=self._menu_clear_all,
                state="normal" if m is not None else "disabled")
            return

        menu.add_command(label="复制", accelerator=copy_key,
                         command=self._menu_copy)
        if m is None:
            return
        on_row = self._menu_row is not None
        on_score = self._menu_col is not None and self._menu_col in m.score_cols

        if area == "column":
            menu.add_command(
                label="清除整列分数（全班这一题）", command=self._menu_clear_column,
                state="normal" if on_score else "disabled")
            return

        if area == "row":
            menu.add_command(
                label="清除整行分数（该生所有题）", command=self._menu_clear_row,
                state="normal" if on_row else "disabled")
            menu.add_separator()
            menu.add_command(
                label="设为当前学生", command=self._menu_activate,
                state="normal" if on_row else "disabled")
            return

        menu.add_command(
            label="清除内容", accelerator="Delete",
            command=self._menu_clear_cell,
            state="normal" if on_score and on_row else "disabled")
        menu.add_separator()
        menu.add_command(
            label="清除整行分数（该生所有题）", command=self._menu_clear_row,
            state="normal" if on_row else "disabled")
        menu.add_command(
            label="清除整列分数（全班这一题）", command=self._menu_clear_column,
            state="normal" if on_score else "disabled")
        menu.add_separator()
        menu.add_command(
            label="设为当前学生", command=self._menu_activate,
            state="normal" if on_row else "disabled")

    def _menu_copy(self) -> None:
        """把选区交给 tksheet 自己的复制，跟 Excel 一样进系统剪贴板。"""
        try:
            self.sheet.MT.ctrl_c()
        except Exception as e:  # noqa: BLE001
            self._notify(f"复制失败：{e}", "warn")

    def _menu_clear_all(self) -> None:
        """清空全班所有题的分数，整批算一次操作、一次撤销。"""
        m = self.state.model
        if m is None:
            return
        targets = [(stu, col) for stu in m.students for col in m.score_cols]
        if not any(col in stu.scores for stu, col in targets):
            self._notify("表里还没有分数可清除", "warn")
            return
        if not messagebox.askyesno(
                "清除全部分数",
                f"要清空全班 {len(m.students)} 位学生共 {m.score_count} 道题的"
                "分数吗？\n\n可以按「撤销」一次找回。",
                icon="warning", parent=self):
            return
        self._apply_clear(self._score_targets(
            {(i, col) for i in range(len(m.students)) for col in m.score_cols}))

    def _apply_clear(self, targets: list) -> None:
        if not targets:
            self._notify("选中的格子里没有分数可清除", "warn")
            return
        result = self.state.clear_cells(targets)
        self._notify(result.message, "info" if result.ok else "warn")
        self._after_action()

    def _menu_clear_cell(self) -> None:
        """有选区就清整片选区，否则只清右键那一格。"""
        cells = self._selected_cells()
        if len(cells) <= 1 and self._menu_row is not None and self._menu_col is not None:
            cells = {(self._menu_row, self._menu_col)}
        self._apply_clear(self._score_targets(cells))

    def _menu_clear_row(self) -> None:
        m = self.state.model
        if m is None:
            return
        rows = {r for r, _c in self._selected_cells()}
        if not rows and self._menu_row is not None:
            rows = {self._menu_row}
        self._apply_clear(self._score_targets(
            {(r, c) for r in rows for c in m.score_cols}))

    def _menu_clear_column(self) -> None:
        m = self.state.model
        if m is None:
            return
        cols = {c for _r, c in self._selected_cells() if c in m.score_cols}
        if not cols and self._menu_col is not None:
            cols = {self._menu_col}
        cols = {c for c in cols if c in m.score_cols}
        if not cols:
            return
        names = "、".join(m.header[c] for c in sorted(cols)
                          if c < len(m.header))
        if not messagebox.askyesno(
                "清除整列分数",
                f"要清除全班「{names}」的分数吗？\n"
                f"共 {len(m.students)} 位学生，清除后可以按撤销找回。",
                parent=self):
            return
        self._apply_clear(self._score_targets(
            {(r, c) for r in range(len(m.students)) for c in cols}))

    def _on_delete_key(self, _event=None):
        """Delete / BackSpace：清除选中区域里的分数。"""
        cells = self._selected_cells()
        if cells:
            self._apply_clear(self._score_targets(cells))
        return "break"

    def _on_sheet_edit(self, event) -> None:
        """手动编辑：姓名列与题号列写回模型，总分与核对列由程序算。"""
        m = self.state.model
        if m is None:
            return
        row, col = int(event.row), int(event.column)
        if not (0 <= row < len(m.students)):
            return
        stu = m.students[row]
        value = str(getattr(event, "value", "") or "").strip()
        if col == m.name_col:
            if value and value != stu.name:
                stu.name = value
                self._apply_vocab()
        elif col in m.score_cols:
            number = parser.any_number_to_float(value) if value else None
            if value and number is None:
                self._notify(f"「{value}」不是有效分数，已还原", "warn")
            else:
                if number is None:
                    stu.scores.pop(col, None)     # 清空格子 = 删掉该题分数
                else:
                    stu.scores[col] = float(number)
                stu.total = m.calc_total(stu)
                stu.checked = None                # 改了分，之前的核对结论作废
                self.state._last_edit_col = col
        else:
            self._notify("总分与核对列由程序自动计算，不用手填", "warn")
        self._refresh_table()

    def _menu_activate(self) -> None:
        if self._menu_row is not None:
            self._activate_index(self._menu_row, announce=True)

    # ================================================================= 关闭
    def open_log_folder(self) -> None:
        """打开日志所在目录。打包版没有控制台，出问题只能靠这个文件。"""
        from .. import paths
        folder = paths.user_data_dir()
        log = paths.log_path()
        if not os.path.exists(log):
            self._notify(f"还没有日志文件（源码运行时日志直接打在终端里）：{folder}",
                         "info")
        try:
            platform_support.open_in_default_app(folder)
        except Exception as e:  # noqa: BLE001
            self._notify(f"打不开文件夹：{e}\n路径：{folder}", "warn")

    def _on_close(self) -> None:
        self.recorder.stop()
        if self.state.model is not None and not self._save_before_exit():
            return          # 没存成、老师选择留下：不退出

        # 记住窗口位置与大小，下次启动原样恢复
        try:
            save_config({"window_geometry": self.geometry()})
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 保存窗口位置失败: {e}", flush=True)
        self.destroy()


def run(cfg: Optional[dict] = None, open_path: Optional[str] = None) -> None:
    platform_support.enable_dpi_awareness()   # 必须在建根窗口之前
    GradeApp(cfg or load_config(), open_path=open_path).mainloop()
