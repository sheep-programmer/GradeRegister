"""界面冒烟测试：窗口能否正常搭建、刷新与响应动作。

需要图形环境，没有显示器时自动跳过（如无头 CI）。
运行：python -m unittest tests.test_gui_smoke -v
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest import mock

from openpyxl import Workbook


class TestMainWindow(unittest.TestCase):
    # macOS 的 Tk 在一个进程里创建第二个根窗口会崩溃，所以不做探测式的
    # 显示环境检查，直接尝试建窗口、失败再跳过。

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "成绩表.xlsx")
        wb = Workbook()
        ws = wb.active
        ws.append(["姓名", "第一题", "第二题", "第三题", "总分"])
        for name in ("张三", "李四", "张三", "王五"):
            ws.append([name, "", "", "", ""])
        wb.save(self.path)

        self.cfg = {"engine": "sense-voice", "auto_save": False, "continuous": True,
                    "strip_prefix": True, "strip_suffix": True,
                    "write_formula": True, "write_checked": True,
                    "score_cutoff": 0.55, "device": None, "last_file": ""}

        # 不启动真正的语音引擎，界面本身不该依赖它
        patcher = mock.patch(
            "grade_app.ui.main_window.GradeApp._ensure_engine")
        patcher.start()
        self.addCleanup(patcher.stop)

        from grade_app.ui.main_window import GradeApp
        try:
            self.app = GradeApp(self.cfg, open_path=self.path)
        except tk.TclError as e:
            self.skipTest(f"没有图形环境: {e}")
        self.app.withdraw()             # 不真的弹到屏幕上
        self.app.update_idletasks()
        self.app._auto_open_initial()   # 立刻加载，不等 after 回调
        self.app.update_idletasks()

    def tearDown(self):
        try:
            self.app.destroy()
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------------- 搭建 ----------------
    def test_table_loaded(self):
        self.assertIsNotNone(self.app.state.model)
        self.assertEqual(len(self.app.state.model.students), 4)
        self.assertEqual(len(self.app.sheet.get_sheet_data()), 4)

    def test_widgets_present(self):
        for name in ("btn_talk", "meter", "message", "progress", "paned",
                     "lbl_student", "lbl_status", "sheet", "heard"):
            self.assertTrue(hasattr(self.app, name), name)

    def test_table_pane_takes_the_space(self):
        """表格是主体：分隔条可拖，窗口的增量归表格。"""
        self.app.update_idletasks()
        panes = self.app.paned.panes()
        self.assertEqual(len(panes), 2)
        self.assertEqual(self.app.paned.pane(panes[0], "weight"), 1)
        self.assertEqual(self.app.paned.pane(panes[1], "weight"), 0)

    def test_talk_button_disabled_before_engine_ready(self):
        self.assertFalse(self.app.engine_ready)
        self.assertFalse(self.app._start_recording())

    # ---------------- 交互 ----------------
    def test_select_student_updates_header(self):
        self.app._on_text("李四")
        self.app.update_idletasks()
        self.assertEqual(self.app.state.current.name, "李四")
        self.assertEqual(self.app.lbl_student.cget("text"), "李四")
        self.assertIn("0/3 题", self.app.lbl_progress.cget("text"))

    def test_scoring_flow_updates_progress(self):
        self.app._on_text("李四")
        self.app._on_text("第一题十八分")
        self.app.update_idletasks()
        self.assertEqual(self.app.state.current.scores, {1: 18.0})
        self.assertIn("1/3 题", self.app.lbl_progress.cget("text"))

    def test_total_check_marks_row(self):
        self.app._on_text("李四")
        self.app._on_text("第一题十分 第二题十分 第三题十分")
        self.app._on_text("总分三十")
        self.app.update_idletasks()
        self.assertIs(self.app.state.current.checked, True)
        row = self.app.state.model.students.index(self.app.state.current)
        check_col = self.app.state.model.check_col
        self.assertEqual(self.app.sheet.get_sheet_data()[row][check_col], "✓")

    def test_duplicate_name_opens_dialog(self):
        with mock.patch.object(self.app, "_show_choice_dialog") as dlg:
            self.app._on_text("张三")
        dlg.assert_called_once()
        self.assertEqual(len(dlg.call_args.args[0]), 2)   # 两个张三

    def test_message_bar_switches_color(self):
        from grade_app.ui import theme
        self.app._notify("出错了", "error")
        self.app.update_idletasks()
        self.assertEqual(self.app.message.cget("bg"), theme.ERROR_SOFT)
        self.app._notify("好了", "success")
        self.assertEqual(self.app.message.cget("bg"), theme.SUCCESS_SOFT)

    def test_undo_restores_and_refreshes(self):
        self.app._on_text("李四")
        self.app._on_text("第二题十二分")
        self.app.undo()
        self.app.update_idletasks()
        self.assertEqual(self.app.state.current.scores, {})

    def test_clear_row_via_menu(self):
        self.app._on_text("李四")
        self.app._on_text("第一题十八分 第二题十二分")
        self.app._menu_row = self.app.state.model.students.index(
            self.app.state.current)
        self.app._menu_col = 1
        self.app._menu_clear_row()
        self.app.update_idletasks()
        self.assertEqual(self.app.state.current.scores, {})

    def test_clear_single_cell_via_menu(self):
        """只清右键那一格，同一行的其他分数留着。"""
        self.app._on_text("李四")
        self.app._on_text("第一题十八分 第二题十二分")
        self.app._menu_row = self.app.state.model.students.index(
            self.app.state.current)
        self.app._menu_col = 1
        self.app._menu_clear_cell()
        self.app.update_idletasks()
        self.assertEqual(self.app.state.current.scores, {2: 12.0})

    def test_clear_cell_is_undoable_in_one_step(self):
        self.app._on_text("李四")
        self.app._on_text("第一题十八分")
        self.app._menu_row = self.app.state.model.students.index(
            self.app.state.current)
        self.app._menu_col = 1
        self.app._menu_clear_cell()
        self.app.undo()
        self.app.update_idletasks()
        self.assertEqual(self.app.state.current.scores, {1: 18.0})

    def test_clear_column_asks_before_wiping_class(self):
        """清整列会动全班的分数，必须先确认；点「否」则一个都不动。"""
        self.app._on_text("李四")
        self.app._on_text("第一题十八分")
        self.app._menu_row = 0
        self.app._menu_col = 1
        with mock.patch("grade_app.ui.main_window.messagebox.askyesno",
                        return_value=False) as ask:
            self.app._menu_clear_column()
        ask.assert_called()
        self.assertEqual(self.app.state.current.scores, {1: 18.0})

    def test_clear_column_wipes_whole_class_when_confirmed(self):
        # 用不重名的两位，念重名的会弹选择框把测试挂住
        self.app._on_text("李四")
        self.app._on_text("第一题十八分")
        self.app._on_text("王五")
        self.app._on_text("第一题二十分")
        self.assertEqual(
            [s.scores.get(1) for s in self.app.state.model.students],
            [None, 18.0, None, 20.0])
        self.app._menu_row = 0
        self.app._menu_col = 1
        with mock.patch("grade_app.ui.main_window.messagebox.askyesno",
                        return_value=True):
            self.app._menu_clear_column()
        self.app.update_idletasks()
        self.assertEqual(
            [s.scores.get(1) for s in self.app.state.model.students],
            [None] * len(self.app.state.model.students))

    def test_menu_disables_cell_clear_off_score_columns(self):
        """右键点在姓名列上时，「清除此格」不该是可点的。"""
        self.app._menu_row, self.app._menu_col = 0, 0
        self.app._build_row_menu()
        labels = [self.app.row_menu.entrycget(i, "label")
                  for i in range(self.app.row_menu.index("end") + 1)
                  if self.app.row_menu.type(i) == "command"]
        idx = labels.index("清除此格")
        entry = [i for i in range(self.app.row_menu.index("end") + 1)
                 if self.app.row_menu.type(i) == "command"][idx]
        self.assertEqual(str(self.app.row_menu.entrycget(entry, "state")),
                         "disabled")

    def test_heard_log_shows_corrected_name(self):
        """听成「五米」而名单里有「吴敏」时，听到区只显示纠正后的名字。"""
        self.app.state.model.students[0].name = "吴敏"
        self.app._apply_vocab()
        self.app._on_text("五米")
        self.app.update_idletasks()
        dump = self.app.heard.dump()
        self.assertIn("吴敏", dump)
        self.assertNotIn("五米", dump)

    def test_manual_cell_edit_writes_back(self):
        self.app._on_text("李四")
        row = self.app.state.model.students.index(self.app.state.current)
        event = mock.Mock(row=row, column=1, value="17")
        self.app._on_sheet_edit(event)
        self.assertEqual(self.app.state.current.scores, {1: 17.0})

    def test_manual_cell_edit_rejects_garbage(self):
        self.app._on_text("李四")
        row = self.app.state.model.students.index(self.app.state.current)
        self.app._on_sheet_edit(mock.Mock(row=row, column=1, value="不是数字"))
        self.assertEqual(self.app.state.current.scores, {})

    # ---------------- 识别记录 ----------------
    def test_heard_log_keeps_every_final(self):
        """识别到的每一句都留在记录里，后面的句子不会把前面的顶掉。"""
        self.app._handle_event("final", "李四")
        self.app._handle_event("final", "第一题十八分")
        self.app.update_idletasks()
        dump = self.app.heard.dump()
        self.assertIn("李四", dump)
        self.assertIn("第一题十八分", dump)
        self.assertEqual(len(dump.splitlines()), 2)

    def test_heard_log_partial_replaced_by_final(self):
        """半句实时显示，确认后被正式结果取代，不留重复行。"""
        self.app._handle_event("partial", "第一题十")
        self.app._handle_event("partial", "第一题十八")
        self.app.update_idletasks()
        self.assertEqual(self.app.heard.dump().count("第一题十"), 1)

        self.app._handle_event("final", "第一题十八分")
        self.app.update_idletasks()
        dump = self.app.heard.dump()
        self.assertEqual(len(dump.splitlines()), 1)
        self.assertIn("第一题十八分", dump)
        self.assertNotIn("…", dump)

    def test_heard_log_records_empty_result(self):
        """没听清也要留一条，否则老师分不清是没听到还是程序卡了。"""
        self.app._handle_event("final_empty", 0.3)
        self.app.update_idletasks()
        self.assertIn("没听清", self.app.heard.dump())

    def test_heard_log_trims_to_limit(self):
        from grade_app.ui.widgets import HeardLog
        for i in range(HeardLog.MAX_LINES + 20):
            self.app.heard.append(f"第{i}句")
        self.app.update_idletasks()
        lines = self.app.heard.dump().splitlines()
        self.assertLessEqual(len(lines), HeardLog.MAX_LINES)
        self.assertIn(f"第{HeardLog.MAX_LINES + 19}句", lines[-1])

    # ---------------- 表格交互 ----------------
    def test_left_click_selects_student(self):
        """不调 enable_bindings 的话，单击、滚轮、方向键全都不响应。"""
        self.app.deiconify()
        self.app.update()
        self.app.sheet.MT.event_generate("<ButtonPress-1>", x=60, y=75)
        self.app.sheet.MT.event_generate("<ButtonRelease-1>", x=60, y=75)
        self.app.update()
        self.app.withdraw()
        self.assertIsNotNone(self.app.state.current)
        self.assertEqual(self.app.state.current.row, 3)   # 第 3 行数据

    def test_mouse_wheel_scrolls_table(self):
        self.app.deiconify()
        self.app.update()          # 先让窗口真正映射，否则表格高度是 0
        # 灌满一屏放不下的行数，只为验证滚轮能滚动视口
        self.app.sheet.set_sheet_data(
            [[f"学生{i}", "", "", "", ""] for i in range(80)])
        self.app.sheet.redraw()
        self.app.update()
        top_before = self.app.sheet.MT.yview()[0]
        for _ in range(6):
            self.app.sheet.MT.event_generate("<MouseWheel>", delta=-40)
        self.app.update()
        top_after = self.app.sheet.MT.yview()[0]
        self.app.withdraw()
        self.assertGreater(top_after, top_before)

    def test_row_index_shows_excel_row_numbers(self):
        index = [self.app.sheet.MT._row_index[i] for i in range(4)]
        self.assertEqual(index, [2, 3, 4, 5])

    # ---------------- 平台 ----------------
    def test_right_click_pops_row_menu(self):
        """右键事件号在 macOS 与 Windows 上不同，这里验证本平台真能弹出菜单。"""
        from grade_app import platform_support
        self.app.deiconify()
        self.app.update()
        with mock.patch.object(self.app.row_menu, "tk_popup") as popup:
            self.app.sheet.MT.event_generate(
                platform_support.RIGHT_CLICK_EVENTS[0], x=60, y=75)
            self.app.update()
        self.app.withdraw()
        popup.assert_called()
        self.assertEqual(self.app._menu_row, 2)   # 菜单认准点中的那一行

    # ---------------- 设置窗口 ----------------
    def _open_settings(self):
        from grade_app.ui.dialogs import SettingsDialog
        saved = {}
        dlg = SettingsDialog(self.app, self.app.fonts, self.cfg, saved.update)
        with mock.patch("grade_app.ui.dialogs._center_on"):
            dlg.show()
        self._settings_dialog = dlg
        self.app.update_idletasks()
        top = [w for w in self.app.winfo_children()
               if w.winfo_class() == "Toplevel"][-1]
        return top, saved

    def _option_rows(self, widget, kind=None):
        from grade_app.ui.widgets import OptionRow
        found = []
        for child in widget.winfo_children():
            if isinstance(child, OptionRow) and kind in (None, child._kind):
                found.append(child)
            found.extend(self._option_rows(child, kind))
        return found

    def _gap_combo(self, widget):
        """按候选项内容找「断句停顿」下拉框（界面里有多个 Combobox）。"""
        from tkinter import ttk
        for child in widget.winfo_children():
            if isinstance(child, ttk.Combobox) and any(
                    "秒（" in str(v) for v in child.cget("values")):
                return child
            found = self._gap_combo(child)
            if found is not None:
                return found
        return None

    def _click_save(self, top):
        for btn in top.winfo_children()[0].winfo_children()[-1].winfo_children():
            if btn.cget("text") == "保存":
                btn.invoke()
                return
        self.fail("没找到「保存」按钮")

    def test_settings_shows_current_segment_gap(self):
        self.cfg["segment_gap"] = 1.0
        top, _saved = self._open_settings()
        combo = self._gap_combo(top)
        label = combo.get() if combo is not None else ""
        top.destroy()
        self.assertIn("1.0", label)

    def test_settings_writes_back_segment_gap(self):
        self.cfg["segment_gap"] = 0.7
        top, saved = self._open_settings()
        combo = self._gap_combo(top)
        self.assertIsNotNone(combo, "没找到断句停顿下拉框")
        combo.set("1.5 秒（中间要想一下）")
        self._click_save(top)
        self.assertEqual(saved.get("segment_gap"), 1.5)

    def test_settings_keeps_gap_when_untouched(self):
        self.cfg["segment_gap"] = 1.0
        top, saved = self._open_settings()
        self._click_save(top)
        self.assertEqual(saved.get("segment_gap"), 1.0)

    def test_settings_reflects_current_config(self):
        self.cfg.update({"strip_prefix": True, "write_checked": False,
                         "auto_save_mode": "student"})
        top, _saved = self._open_settings()
        checks = {r._label.cget("text"): r.get()
                  for r in self._option_rows(top, "check")}
        top.destroy()
        self.assertTrue(checks["剥离题号前缀"])
        self.assertFalse(checks["核对一致时写 ✓"])

    def test_save_mode_is_single_choice(self):
        self.cfg["auto_save_mode"] = "student"
        top, saved = self._open_settings()
        radios = self._option_rows(top, "radio")
        self.assertEqual(len(radios), 3)
        self.assertEqual([r.get() for r in radios], [False, True, False])

        radios[2]._on_click(None)          # 改选「只在点保存时写盘」
        self.assertEqual([r.get() for r in radios], [False, False, True])
        self.app.update_idletasks()
        top.destroy()

    def test_settings_writes_back_save_mode(self):
        self.cfg["auto_save_mode"] = "score"
        top, saved = self._open_settings()
        self._option_rows(top, "radio")[1]._on_click(None)
        # 「保存」按钮在弹窗最下面一组里
        for btn in top.winfo_children()[0].winfo_children()[-1].winfo_children():
            if btn.cget("text") == "保存":
                btn.invoke()
                break
        self.assertEqual(saved.get("auto_save_mode"), "student")
        self.assertTrue(saved.get("auto_save"))

    def test_settings_shows_model_ready(self):
        with mock.patch("grade_app.speech.engine_model_ready", return_value=True):
            top, _saved = self._open_settings()
            self.app.update_idletasks()
            text = self._settings_dialog._lbl_model.cget("text")
            has_button = bool(self._settings_dialog._btn_dl.winfo_manager())
            top.destroy()
        self.assertIn("已就绪", text)
        self.assertFalse(has_button)      # 已就绪就不该有下载按钮

    def test_settings_offers_download_when_model_missing(self):
        with mock.patch("grade_app.speech.engine_model_ready", return_value=False):
            top, _saved = self._open_settings()
            self.app.update_idletasks()
            text = self._settings_dialog._lbl_model.cget("text")
            has_button = bool(self._settings_dialog._btn_dl.winfo_manager())
            top.destroy()
        self.assertIn("未下载", text)
        self.assertIn("MB", text)
        self.assertTrue(has_button)

    def test_settings_download_shows_progress_then_ready(self):
        gate = threading.Event()
        with mock.patch("grade_app.speech.engine_model_ready", return_value=False):
            top, _saved = self._open_settings()
            dlg = self._settings_dialog

            def fake_dl(cfg, engine=None, progress=None):
                progress(50 * 1048576, 228 * 1048576)
                gate.wait(3)          # 卡在半途，好让主线程看到中间进度

            with mock.patch("grade_app.speech.download_engine_model", fake_dl):
                dlg._download_model()
                for _ in range(40):
                    self.app.update()
                    if dlg._dl_bar.cget("value"):
                        break
                    time.sleep(0.02)
                value = dlg._dl_bar.cget("value")
                label = dlg._lbl_model.cget("text")
                gate.set()
                self.app.update()
        self.assertEqual(value, 21)               # 50 / 228
        self.assertIn("50MB / 228MB", label)

        # 下完之后状态自己翻成已就绪
        with mock.patch("grade_app.speech.engine_model_ready", return_value=True):
            dlg._refresh_model_status()
        self.assertIn("已就绪", dlg._lbl_model.cget("text"))
        top.destroy()

    def test_download_progress_bar_appears_and_hides(self):
        self.assertFalse(self.app.dl_bar.winfo_manager())
        self.app._handle_event("dl_progress", (23 * 1048576, 228 * 1048576))
        self.app.update_idletasks()
        self.assertTrue(self.app.dl_bar.winfo_manager())
        self.assertEqual(self.app.dl_bar.cget("value"), 10)
        self.assertIn("23MB / 228MB", self.app.lbl_status.cget("text"))

        self.app._handle_event("engine_ready", "")
        self.app.update_idletasks()
        self.assertFalse(self.app.dl_bar.winfo_manager())

    def test_talk_button_stays_locked_until_engine_built(self):
        """模型没下完不能让老师开始说话。"""
        self.assertFalse(self.app.engine_ready)
        self.app._handle_event("dl_progress", (10, 100))
        self.app.update_idletasks()
        self.assertFalse(self.app._start_recording())

        self.app._handle_event("engine_ready", "")
        self.app.update_idletasks()
        self.assertTrue(self.app.engine_ready)

    def test_settings_never_persists_auto_picked_mic(self):
        """自动挑选的麦克风只在本次运行生效，保存设置不能把它写进配置。"""
        self.cfg["device"] = 3          # 模拟启动时自动挑中的设备
        with mock.patch("grade_app.ui.main_window.save_config") as saved:
            self.app._apply_settings({"engine": "sherpa", "device": None,
                                      "auto_save": True})
        self.assertIsNone(saved.call_args.args[0]["device"])


    def test_unclear_name_does_not_block_with_a_modal(self):
        """没听清时只提示+高亮，绝不能弹模态窗——那会把后面的语音全堵住。"""
        with mock.patch.object(self.app, "_show_choice_dialog") as modal:
            self.app._on_text("旺财")
        modal.assert_not_called()
        self.assertTrue(self.app.state._pending_choices)

    def test_duplicate_name_still_uses_modal(self):
        """重名必须挑一个，仍然走模态窗。"""
        with mock.patch.object(self.app, "_show_choice_dialog") as modal:
            self.app._on_text("张三")          # fixture 里张三重名
        modal.assert_called()

    def test_voice_picks_a_candidate(self):
        self.app._on_text("旺财")
        picks = self.app.state._pending_choices
        self.assertTrue(picks)
        self.app._on_text("第一个")
        self.assertEqual(self.app.state.current.name, picks[0][1])


if __name__ == "__main__":
    unittest.main()
