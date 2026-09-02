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

from grade_app import platform_support
from grade_app.ui import dialogs


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

    def _menu_items(self, area="cell", row=0, col=0):
        """按右键分区建菜单，返回 {标签: 状态}。"""
        self.app._menu_area = area
        self.app._menu_row, self.app._menu_col = row, col
        self.app._build_row_menu()
        menu = self.app.row_menu
        return {menu.entrycget(i, "label"): str(menu.entrycget(i, "state"))
                for i in range(menu.index("end") + 1)
                if menu.type(i) == "command"}

    def test_menu_disables_cell_clear_off_score_columns(self):
        """右键点在姓名列上时，「清除内容」不该是可点的。"""
        items = self._menu_items("cell", row=0, col=0)
        self.assertEqual(items["清除内容"], "disabled")

    def test_corner_menu_offers_only_clear_all(self):
        """左上角除了「清空全班」没有说得通的操作，别的一条都不给。"""
        items = self._menu_items("all", row=None, col=None)
        self.assertEqual(list(items), ["清除全部分数（全班所有题）"])

    def test_cell_menu_is_excel_shaped(self):
        m = self.app.state.model
        items = self._menu_items("cell", row=0, col=m.score_cols[0])
        self.assertEqual(list(items), ["复制", "清除内容",
                                       "清除整行分数（该生所有题）",
                                       "清除整列分数（全班这一题）",
                                       "设为当前学生"])
        self.assertEqual(items["清除内容"], "normal")

    def test_row_header_menu_has_no_column_action(self):
        """行号上右键谈不上「这一列」，Excel 也不给。"""
        items = self._menu_items("row", row=1, col=None)
        self.assertNotIn("清除整列分数（全班这一题）", items)
        self.assertEqual(items["清除整行分数（该生所有题）"], "normal")
        self.assertEqual(items["设为当前学生"], "normal")

    def test_column_header_menu_has_only_the_column_action(self):
        m = self.app.state.model
        items = self._menu_items("column", row=None, col=m.score_cols[0])
        self.assertEqual(list(items), ["复制", "清除整列分数（全班这一题）"])

    def test_clear_all_wipes_every_score(self):
        m = self.app.state.model
        for stu in m.students:
            for col in m.score_cols:
                stu.scores[col] = 7.0
        with mock.patch("grade_app.ui.main_window.messagebox.askyesno",
                        return_value=True):
            self.app._menu_clear_all()
        self.assertEqual([dict(s.scores) for s in m.students],
                         [{} for _ in m.students])

    def test_clear_all_is_one_undo(self):
        """整批清空算一次操作，按一次撤销全回来。"""
        m = self.app.state.model
        for stu in m.students:
            for col in m.score_cols:
                stu.scores[col] = 7.0
        before = [dict(s.scores) for s in m.students]
        with mock.patch("grade_app.ui.main_window.messagebox.askyesno",
                        return_value=True):
            self.app._menu_clear_all()
        self.app.state.undo()
        self.assertEqual([dict(s.scores) for s in m.students], before)

    def test_clear_all_asks_first(self):
        m = self.app.state.model
        m.students[0].scores[m.score_cols[0]] = 5.0
        with mock.patch("grade_app.ui.main_window.messagebox.askyesno",
                        return_value=False) as ask:
            self.app._menu_clear_all()
        ask.assert_called_once()
        self.assertEqual(m.students[0].scores, {m.score_cols[0]: 5.0})

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
        """左侧序号跟 Excel 的行号一致，方便对着 Excel 核。"""
        index = [self.app.sheet.MT._row_index[i] for i in range(4)]
        self.assertEqual(index, [2, 3, 4, 5])

    def test_status_line_shows_both_row_and_ordinal(self):
        """行号对 Excel，序号对「念第几个」，两个都得写出来才不混。"""
        m = self.app.state.model
        self.app.state.activate_row(m.students[2].row)
        self.app._after_action()
        text = self.app.lbl_progress.cget("text")
        self.assertIn(f"第 {m.students[2].row + 1} 行", text)
        self.assertIn("第 3 个", text)

    # ---------------- 平台 ----------------
    def _right_click(self, canvas, x, y):
        """在指定画布上发一次本机真实的右键事件。"""
        from grade_app import platform_support
        for event in platform_support.right_click_events(self.app.sheet):
            if event.startswith("<Button-"):
                canvas.event_generate(event, x=x, y=y)
                return event
        self.fail("没有可用的右键事件")

    def test_right_click_pops_row_menu(self):
        """右键键号随平台与 Tk 版本变，这里按运行时实际绑定的那个键验证。

        写死 Button-2 的话，Tk 9 的 macOS 上右键其实是 Button-3，
        测试照样绿而真机点不出菜单。
        """
        self.app.deiconify()
        self.app.update()
        with mock.patch.object(self.app.row_menu, "tk_popup") as popup:
            self._right_click(self.app.sheet.MT, 60, 75)
            self.app.update()
        self.app.withdraw()
        popup.assert_called()
        self.assertEqual(self.app._menu_row, 2)   # 菜单认准点中的那一行

    def test_right_click_on_column_header_records_the_column(self):
        """列头右键没有行号，列号仍要记下来，否则「清除整列」永远是灰的。"""
        self.app.deiconify()
        self.app.update()
        with mock.patch.object(self.app.row_menu, "tk_popup"):
            self._right_click(self.app.sheet.CH, 180, 10)
            self.app.update()
        self.app.withdraw()
        self.assertIsNone(self.app._menu_row)
        self.assertIsNotNone(self.app._menu_col)
        labels = {self.app.row_menu.entrycget(i, "label"):
                  str(self.app.row_menu.entrycget(i, "state"))
                  for i in range(self.app.row_menu.index("end") + 1)
                  if self.app.row_menu.type(i) == "command"}
        self.assertEqual(labels["清除整列分数（全班这一题）"], "normal")
        # 列头菜单照 Excel 的样子，不放跟整列无关的项
        self.assertNotIn("设为当前学生", labels)

    # ---------------- 十字定位高亮 ----------------
    def _cell_bg(self, row, col):
        h = self.app.sheet.MT.get_cell_kwargs(row, col, key="highlight")
        return h.bg if h else None

    # ---------------- 列宽自适应 ----------------
    def test_refreshing_data_keeps_the_column_widths(self):
        """set_sheet_data 会把列宽重置回默认，刷完数据必须重新量一遍。"""
        m = self.app.state.model
        before = self.app.sheet.get_column_widths()[m.name_col]
        self.app._refresh_table()
        self.assertEqual(self.app.sheet.get_column_widths()[m.name_col], before)

    def test_check_column_grows_to_fit_the_mismatch_text(self):
        """「不一致（报18 算19）」比表头长得多，列宽得跟上，不能截断。"""
        m = self.app.state.model
        narrow = self.app.sheet.get_column_widths()[m.check_col]
        stu = m.students[0]
        stu.scores = {c: 5.0 for c in m.score_cols}
        stu.total = 5.0 * len(m.score_cols)
        stu.checked = False
        stu.spoken_total = 999.0
        self.app._refresh_table()
        widened = self.app.sheet.get_column_widths()[m.check_col]
        self.assertGreater(widened, narrow)
        self.assertGreaterEqual(
            widened, self.app.sheet.get_column_text_width(m.check_col))

    def test_long_name_widens_the_name_column(self):
        m = self.app.state.model
        before = self.app.sheet.get_column_widths()[m.name_col]
        m.students[0].name = "欧阳娜娜实验班借读生"
        self.app._refresh_table()
        self.assertGreater(self.app.sheet.get_column_widths()[m.name_col],
                           before)

    def test_filling_scores_does_not_jitter_the_widths(self):
        """分数只有一两位，短于下限，填分过程中列宽不该来回跳。"""
        m = self.app.state.model
        self.app.state.activate_row(m.students[0].row)
        self.app._refresh_table()
        before = list(self.app.sheet.get_column_widths())
        for col in m.score_cols:
            m.students[0].scores[col] = 88.0
            self.app._refresh_table()
            self.assertEqual(list(self.app.sheet.get_column_widths()), before)

    def test_width_follows_the_content_back_down(self):
        """核对标记清掉后内容变短，列宽跟着收回下限，不留一截空白。"""
        m = self.app.state.model
        stu = m.students[0]
        stu.total = 1.0
        stu.checked = False
        stu.spoken_total = 999.0
        self.app._refresh_table()
        wide = self.app.sheet.get_column_widths()[m.check_col]
        stu.checked = None
        stu.spoken_total = None
        self.app._refresh_table()
        narrow = self.app.sheet.get_column_widths()[m.check_col]
        self.assertLess(narrow, wide)
        self.assertEqual(narrow, self.app._column_floor(m.check_col))

    def test_table_is_centre_aligned(self):
        self.assertEqual(self.app.sheet.table_align(), "n")   # n = 居中

    def test_current_student_row_is_highlighted(self):
        from grade_app.ui import theme
        self.app.state.activate_row(self.app.state.model.students[1].row)
        self.app._refresh_table()
        self.assertEqual(self._cell_bg(1, 0), theme.CURRENT_ROW)
        self.assertIsNone(self._cell_bg(0, 0))    # 别的学生不跟着黄

    def test_active_question_is_shown_on_the_column_header(self):
        """「在填哪一题」由列表头指示，照 Excel 的做法。"""
        from grade_app.ui import theme
        m = self.app.state.model
        self.app.state.activate_row(m.students[1].row)
        self.app._refresh_table()
        h = self.app.sheet.CH.get_cell_kwargs(m.score_cols[0], key="highlight")
        self.assertEqual(h.bg, theme.CROSS_CELL)

    def test_current_row_number_is_marked(self):
        from grade_app.ui import theme
        self.app.state.activate_row(self.app.state.model.students[1].row)
        self.app._refresh_table()
        h = self.app.sheet.RI.get_cell_kwargs(1, key="highlight")
        self.assertEqual(h.bg, theme.CROSS_CELL)

    def test_other_students_cells_are_never_tinted(self):
        """整列铺黄会把别人的分数一起染上，看着像他们也被选中了。"""
        m = self.app.state.model
        self.app.state.activate_row(m.students[1].row)
        self.app._refresh_table()
        col = m.score_cols[0]
        for row in range(len(m.students)):
            if row == 1:
                continue
            self.assertNotIn(self._cell_bg(row, col),
                             ("#fff3c4", "#ffd43b"),
                             f"第 {row} 行不是当前学生，不该被高亮")

    def test_crossing_cell_is_the_darkest(self):
        """行与列交叉的那一格要压过两条浅黄，一眼看出在填谁的哪一题。"""
        from grade_app.ui import theme
        m = self.app.state.model
        self.app.state.activate_row(m.students[1].row)
        self.app._refresh_table()
        self.assertEqual(self._cell_bg(1, m.score_cols[0]), theme.CROSS_CELL)

    def test_cross_follows_the_question_being_scored(self):
        """填完第一题，十字与列表头都要跟着挪到第二题。"""
        from grade_app.ui import theme
        m = self.app.state.model
        self.app.state.activate_row(m.students[1].row)
        self.app._on_text("第一题10分")
        self.app.update_idletasks()
        second = m.score_cols[1]
        self.assertEqual(self._cell_bg(1, second), theme.CROSS_CELL)
        h = self.app.sheet.CH.get_cell_kwargs(second, key="highlight")
        self.assertEqual(h.bg, theme.CROSS_CELL)
        first = m.score_cols[0]
        self.assertFalse(self.app.sheet.CH.get_cell_kwargs(first,
                                                           key="highlight"))

    # ---------------- 点击说话 / 按住说话 ----------------
    class _TalkRecorder:
        """记录 start/stop 的录音器替身。"""

        def __init__(self):
            self.recording = False
            self.busy = False
            self.engine = object()
            self.log = []

        def start(self, continuous):
            self.log.append("start")
            self.recording = True
            return True

        def stop(self):
            self.log.append("stop")
            self.recording = False

    def _talk_mode(self, hold):
        """切到指定模式，装上替身录音器，返回它。"""
        self.app.cfg["hold_to_talk"] = hold
        self.app.engine_ready = True
        rec = self._TalkRecorder()
        self.app.recorder = rec
        self.app._set_talk_idle()
        self.app._space_owns_rec = False
        self.app._btn_owns_rec = False
        return rec

    def _tap_button(self, press=True, release=True):
        if press:
            self.app.btn_talk.event_generate("<ButtonPress-1>", x=10, y=10)
            self.app.update_idletasks()
        if release:
            self.app.btn_talk.event_generate("<ButtonRelease-1>", x=10, y=10)
            self.app.update_idletasks()

    def test_hold_mode_starts_on_button_press(self):
        """按住说话按钮必须按下就录。

        RoundButton 原来只在松开时触发 command，「按住说话」怎么按都不录音。
        """
        rec = self._talk_mode(hold=True)
        self._tap_button(release=False)
        self.assertEqual(rec.log, ["start"])

    def test_hold_mode_stops_on_button_release(self):
        rec = self._talk_mode(hold=True)
        self._tap_button()
        self.assertEqual(rec.log, ["start", "stop"])

    def test_hold_release_is_not_also_a_click(self):
        """长按松手别再被当成一次点击，否则松手后又开一轮。"""
        rec = self._talk_mode(hold=True)
        self._tap_button()
        self.assertFalse(rec.recording)
        self.assertEqual(rec.log.count("start"), 1)

    def test_click_mode_ignores_button_press(self):
        """点击说话模式按下不该录，松手才录。"""
        rec = self._talk_mode(hold=False)
        self._tap_button(release=False)
        self.assertEqual(rec.log, [])
        self._tap_button(press=False)
        self.assertEqual(rec.log, ["start"])

    def test_space_is_bound(self):
        """按下/松开空格都得有人接，否则「按住空格」整条路都不通。"""
        self.assertTrue(self.app.bind_all("<KeyPress-space>"))
        self.assertTrue(self.app.bind_all("<KeyRelease-space>"))

    def _hold_space(self, repeats=1):
        """直接调处理器：窗口是 withdrawn 的，合成键盘事件送不到。"""
        for _ in range(repeats):
            self.app._on_space_press(None)
        self.app._on_space_release(None)

    def test_hold_mode_space_works(self):
        rec = self._talk_mode(hold=True)
        self._hold_space()
        self.assertEqual(rec.log, ["start", "stop"])

    def test_click_mode_space_does_nothing(self):
        """点击说话模式下随手碰空格不该「开录又立刻停」，白丢一句。"""
        rec = self._talk_mode(hold=False)
        self._hold_space()
        self.assertEqual(rec.log, [])

    def test_held_space_autorepeat_starts_once(self):
        """按住空格系统会连发 KeyPress，不能每次都开一轮。"""
        rec = self._talk_mode(hold=True)
        self._hold_space(repeats=4)
        self.assertEqual(rec.log, ["start", "stop"])

    def test_button_label_follows_the_mode(self):
        self.app.cfg["hold_to_talk"] = True
        self.assertIn("按住", self.app._talk_idle_text())
        self.app.cfg["hold_to_talk"] = False
        self.assertNotIn("按住", self.app._talk_idle_text())

    def test_partial_settings_do_not_restart_the_engine(self):
        """只传部分设置时，缺 engine 不该被当成「改成 None」而白重启引擎。"""
        self.app.engine_ready = True
        with mock.patch.object(self.app, "_restart_engine") as restart:
            self.app._apply_settings({"hold_to_talk": True})
        restart.assert_not_called()
        self.assertTrue(self.app.engine_ready)

    def test_changing_the_engine_still_restarts(self):
        with mock.patch.object(self.app, "_restart_engine") as restart:
            self.app._apply_settings({"engine": "paraformer"})
        restart.assert_called_once()

    # ---------------- 事件队列 ----------------
    def test_level_events_are_coalesced(self):
        """音量是个仪表，一轮里只该画最后一个值。

        每条都重绘画布的话，积压时能把界面冻住一秒以上。
        """
        drawn = []
        with mock.patch.object(self.app.meter, "set_level",
                               side_effect=lambda v: drawn.append(v)):
            for i in range(500):
                self.app._emit("level", i / 500)
            self.app._poll_queue()
        self.assertEqual(len(drawn), 1, f"画了 {len(drawn)} 次")

    def test_a_flood_of_levels_never_drops_a_result(self):
        """识别结果绝不能因为音量事件堆积而漏处理——漏一条就是丢一句话。

        名字要挑名单里唯一的那个：这份表里有两个张三，念它会弹模态选人窗，
        测试会卡在 wait_window 上。
        """
        m = self.app.state.model
        names = [s.name for s in m.students]
        unique = next(n for n in names if names.count(n) == 1)
        target = next(s for s in m.students if s.name == unique)
        self.app.engine_ready = True
        for i in range(5000):
            self.app._emit("level", i / 5000)
        self.app._emit("final", unique)
        for i in range(5000):
            self.app._emit("level", i / 5000)
        self.app._emit("final", "第一题10分")
        self.app._poll_queue()
        self.assertTrue(self.app._rec_queue.empty())
        self.assertEqual(getattr(self.app.state.current, "name", None), unique)
        self.assertEqual(target.scores.get(m.score_cols[0]), 10.0)

    def test_queue_is_fully_drained_each_tick(self):
        """不设处理上限：留在队列里的事件下一轮才处理，识别就慢一拍。"""
        for i in range(300):
            self.app._emit("partial", f"第{i}")
        self.app._poll_queue()
        self.assertTrue(self.app._rec_queue.empty())

    # ---------------- 说话按钮的尺寸与默认模式 ----------------
    def test_click_to_talk_is_the_default(self):
        """默认点一下开始、再点一下结束；按住说话是设置里可选的。"""
        from grade_app.config import DEFAULT_CONFIG
        self.assertFalse(DEFAULT_CONFIG["hold_to_talk"])

    def test_button_fits_every_label_it_can_show(self):
        """按钮放不下文字时画布只会裁掉，不会报错——只能自己量。"""
        from tkinter import font as tkfont
        measure = tkfont.Font(root=self.app,
                              font=self.app.fonts.body_bold).measure
        width = int(self.app.btn_talk.cget("width"))
        for label in self.app.TALK_LABELS:
            self.assertGreater(width, measure(label),
                               f"「{label}」放不下，会被切掉")

    def test_button_width_does_not_jump_between_states(self):
        """待机与录音中的文字长度不同，按钮宽度不能跟着一跳一跳。"""
        for hold in (False, True):
            self.app.cfg["hold_to_talk"] = hold
            self.app._set_talk_idle()
            idle = int(self.app.btn_talk.cget("width"))
            self.app._set_talk_recording()
            busy = int(self.app.btn_talk.cget("width"))
            self.assertEqual(idle, busy, f"hold_to_talk={hold} 时宽度会变")

    def test_all_talk_labels_are_declared(self):
        """TALK_LABELS 要盖住两种模式下真会显示的每一句，漏了就量不到它。"""
        shown = set()
        for hold in (False, True):
            self.app.cfg["hold_to_talk"] = hold
            shown.add(self.app._talk_idle_text())
            shown.add(self.app._talk_recording_text())
        missing = shown - set(self.app.TALK_LABELS)
        self.assertFalse(missing, f"这些文字没登记进 TALK_LABELS: {missing}")

    # ---------------- 提示音 ----------------
    def _beeps(self, enabled, kind="ok"):
        self.app.cfg["sound_enabled"] = enabled
        with mock.patch.object(platform_support, "play_sound") as snd:
            self.app._beep(kind)
        return snd.call_args_list

    def test_sounds_are_off_by_default(self):
        """一节课几百句，每句都响很吵；想要的人自己去设置里开。"""
        from grade_app.config import DEFAULT_CONFIG
        self.assertFalse(DEFAULT_CONFIG["sound_enabled"])

    def test_no_beep_when_disabled(self):
        self.assertEqual(self._beeps(False), [])

    def test_beeps_when_enabled(self):
        calls = self._beeps(True, "success")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[0], "success")

    def test_missing_key_means_silent(self):
        """配置里没有这一项时按「关」处理，不能默认吵。"""
        self.app.cfg.pop("sound_enabled", None)
        with mock.patch.object(platform_support, "play_sound") as snd:
            self.app._beep("error")
        snd.assert_not_called()

    def test_every_beep_goes_through_the_gate(self):
        """所有播放都必须走 _beep，绕过去的地方关不掉。"""
        import inspect
        import re
        from grade_app.ui import main_window
        src = inspect.getsource(main_window)
        direct = re.findall(r"platform_support\.play_sound\(", src)
        self.assertEqual(len(direct), 1,
                         "只允许 _beep 里那一处直接调 play_sound")
        gate = inspect.getsource(main_window.GradeApp._beep)
        self.assertIn("platform_support.play_sound(", gate)

    def test_recognised_text_is_silent_when_disabled(self):
        """端到端：关着的时候念一句，一声都不该响。"""
        self.app.cfg["sound_enabled"] = False
        m = self.app.state.model
        self.app.state.activate_row(m.students[0].row)
        with mock.patch.object(platform_support, "play_sound") as snd:
            self.app._on_text("第一题10分")
            self.app.update_idletasks()
        snd.assert_not_called()

    def test_recognised_text_beeps_when_enabled(self):
        self.app.cfg["sound_enabled"] = True
        m = self.app.state.model
        self.app.state.activate_row(m.students[0].row)
        with mock.patch.object(platform_support, "play_sound") as snd:
            self.app._on_text("第一题10分")
            self.app.update_idletasks()
        snd.assert_called()

    # ---------------- 设置的真实性 ----------------
    def test_auto_picked_mic_never_reaches_the_config(self):
        """本次自动挑中的设备号不能写回 cfg。

        写回去的话，老师之后随手在设置里点一次「保存」，这个临时挑中的
        编号就被当成他的选择固化了；蓝牙耳机一插拔编号就变，下次必哑。
        """
        self.app.cfg["device"] = None
        with mock.patch("grade_app.speech.pick_mic_device", return_value=2), \
             mock.patch("grade_app.speech.device_has_sound", return_value=True):
            self.app._auto_pick_mic()
        self.assertIsNone(self.app.cfg.get("device"))
        self.assertEqual(self.app.recorder.device_override, 2)
        self.assertEqual(self.app.recorder.active_device(), 2)

    def test_manual_mic_choice_clears_the_override(self):
        self.app.recorder.device_override = 2
        self.app._apply_settings({"device": 0})
        self.assertIsNone(self.app.recorder.device_override)
        self.assertEqual(self.app.recorder.active_device(), 0)

    def test_manual_mic_is_respected_without_override(self):
        self.app.cfg["device"] = 1
        self.app.recorder.device_override = None
        self.assertEqual(self.app.recorder.active_device(), 1)

    # ---------------- 打开时避开 Excel ----------------
    def test_open_proceeds_when_excel_is_not_holding_it(self):
        with mock.patch.object(platform_support, "excel_holds_file",
                               return_value=False), \
             mock.patch.object(platform_support, "close_excel_workbook") as cl:
            self.assertTrue(self.app._release_from_excel(self.path))
        cl.assert_not_called()

    def test_open_closes_the_workbook_excel_is_holding(self):
        """两边同开必然互相覆盖，先让 Excel 交出这一份。"""
        held = [True]
        with mock.patch.object(platform_support, "excel_holds_file",
                               side_effect=lambda _p: held[0]), \
             mock.patch.object(
                 platform_support, "close_excel_workbook",
                 side_effect=lambda _p: (held.pop(0) and (True, "已关闭"))) as cl:
            self.assertTrue(self.app._release_from_excel(self.path))
        cl.assert_called_once()

    def test_stale_marker_does_not_block_opening(self):
        """Excel 崩溃后残留的 ~$ 标记：文件写得动就照常打开。"""
        with mock.patch.object(platform_support, "excel_holds_file",
                               return_value=True), \
             mock.patch.object(platform_support, "close_excel_workbook",
                               return_value=(False, "Excel 没有在运行")), \
             mock.patch.object(platform_support, "file_is_writable",
                               return_value=True):
            self.assertTrue(self.app._release_from_excel(self.path))

    def test_asks_the_teacher_when_it_cannot_close_excel(self):
        """关不掉又写不动（Windows 缺 pywin32）：让老师自己关，别硬来。"""
        with mock.patch.object(platform_support, "excel_holds_file",
                               return_value=True), \
             mock.patch.object(platform_support, "close_excel_workbook",
                               return_value=(False, "未安装 pywin32")), \
             mock.patch.object(platform_support, "file_is_writable",
                               return_value=False), \
             mock.patch("grade_app.ui.main_window.messagebox.askretrycancel",
                        return_value=False) as ask:
            self.assertFalse(self.app._release_from_excel(self.path))
        ask.assert_called_once()

    def test_quiet_open_never_pops_a_dialog(self):
        """启动时自动打开上次的文件，不该弹窗挡在前面。"""
        with mock.patch.object(platform_support, "excel_holds_file",
                               return_value=True), \
             mock.patch.object(platform_support, "close_excel_workbook",
                               return_value=(False, "未安装 pywin32")), \
             mock.patch.object(platform_support, "file_is_writable",
                               return_value=False), \
             mock.patch("grade_app.ui.main_window.messagebox.askretrycancel"
                        ) as ask:
            self.assertFalse(self.app._release_from_excel(self.path,
                                                          quiet=True))
        ask.assert_not_called()

    def test_open_excel_aborts_when_the_file_stays_locked(self):
        """让不出来就别加载，否则填一节课全写不进去。"""
        with mock.patch.object(self.app, "_release_from_excel",
                               return_value=False), \
             mock.patch("grade_app.ui.main_window.load_sheet") as load:
            self.app.open_excel(self.path)
        load.assert_not_called()

    # ---------------- 退出前保存 ----------------
    def test_exit_saves_and_closes(self):
        with mock.patch("grade_app.ui.main_window.save_sheet") as sv:
            self.assertTrue(self.app._save_before_exit())
        sv.assert_called_once()

    def test_exit_asks_when_the_file_is_locked(self):
        """Windows 上 Excel 锁着表格时，不能一声不响地把分数丢掉。"""
        with mock.patch("grade_app.ui.main_window.save_sheet",
                        side_effect=PermissionError(13, "被占用")), \
             mock.patch("grade_app.ui.main_window.messagebox.askyesnocancel",
                        return_value=None) as ask:
            stay = self.app._save_before_exit()
        self.assertFalse(stay)          # 留在程序里，不退出
        ask.assert_called_once()
        self.assertIn("Excel", ask.call_args.args[1])

    def test_exit_retries_after_the_teacher_frees_the_file(self):
        """选「是」= 已关掉 Excel，要真的再试一次。"""
        attempts = []

        def save(*_a, **_k):
            attempts.append(1)
            if len(attempts) == 1:
                raise PermissionError(13, "被占用")

        with mock.patch("grade_app.ui.main_window.save_sheet", save), \
             mock.patch("grade_app.ui.main_window.messagebox.askyesnocancel",
                        return_value=True):
            self.assertTrue(self.app._save_before_exit())
        self.assertEqual(len(attempts), 2)

    def test_exit_can_discard_on_purpose(self):
        """选「否」= 明确放弃这些分数，照旧退出。"""
        with mock.patch("grade_app.ui.main_window.save_sheet",
                        side_effect=PermissionError(13, "被占用")), \
             mock.patch("grade_app.ui.main_window.messagebox.askyesnocancel",
                        return_value=False):
            self.assertTrue(self.app._save_before_exit())

    def test_close_keeps_the_window_when_the_teacher_stays(self):
        with mock.patch("grade_app.ui.main_window.save_sheet",
                        side_effect=PermissionError(13, "被占用")), \
             mock.patch("grade_app.ui.main_window.messagebox.askyesnocancel",
                        return_value=None), \
             mock.patch.object(self.app, "destroy") as bye:
            self.app._on_close()
        bye.assert_not_called()

    # ---------------- 选人期间暂停听写 ----------------
    class _FakeRecorder:
        """只记录 start/stop 的录音器替身。

        真 Recorder 的 recording/busy 是类上的 property，改它会连原来的
        property 一起丢掉，污染同进程里别的测试。
        """

        def __init__(self, listening):
            self.recording = listening
            self.busy = False
            self.engine = object()
            self.calls = []

        def start(self, continuous):
            self.calls.append("start")
            self.recording = True
            return True

        def stop(self):
            self.calls.append("stop")
            self.recording = False

    def _choose_with(self, listening):
        fake = self._FakeRecorder(listening)
        real, self.app.recorder = self.app.recorder, fake
        self.app.engine_ready = True
        try:
            with mock.patch.object(dialogs.StudentChoiceDialog, "show"):
                self.app._show_choice_dialog([(1, "张三"), (5, "张三")])
        finally:
            self.app.recorder = real
        return fake.calls

    def test_choice_dialog_pauses_and_resumes_listening(self):
        """弹窗要等老师点，这段时间的自言自语不该进状态机。"""
        self.assertEqual(self._choose_with(listening=True), ["stop", "start"])

    def test_choice_dialog_does_not_start_listening_if_it_was_idle(self):
        """本来没在听写，选完人不该自己开始录音。"""
        self.assertEqual(self._choose_with(listening=False), [])

    def test_select_all_corner_is_enabled(self):
        """左上角全选三角靠这个开关决定画不画，不开就既看不见也点不着。"""
        self.assertTrue(self.app.sheet.MT.select_all_enabled)

    def test_delete_works_from_every_canvas(self):
        """点左上角/行号/列头之后键盘焦点不在主表格上，Delete 也要能清分。"""
        sheet = self.app.sheet
        for canvas in (sheet.MT, sheet.RI, sheet.CH, sheet.TL):
            self.assertTrue(canvas.bind("<Delete>"),
                            f"{canvas.__class__.__name__} 没绑 Delete")
            self.assertTrue(canvas.bind("<BackSpace>"),
                            f"{canvas.__class__.__name__} 没绑 BackSpace")

    def test_select_all_then_delete_clears_every_score(self):
        """全选之后按 Delete 要清空全班分数。

        不合成窗口事件：那样得先 deiconify 等窗口真正映射，在 macOS 上
        时灵时不灵。「焦点落在哪块画布上都收得到 Delete」由
        test_delete_works_from_every_canvas 覆盖，这里只验清除本身。
        """
        m = self.app.state.model
        for stu in m.students:
            for col in m.score_cols:
                stu.scores[col] = 5.0
        self.app.sheet.select_all()
        self.app.update_idletasks()
        self.app._on_delete_key()
        self.assertEqual([dict(s.scores) for s in m.students],
                         [{} for _ in m.students])

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
        from grade_app.ui.dialogs import SettingsDialog
        modes = [key for key, _label, _hint in SettingsDialog.SAVE_MODES]
        self.cfg["auto_save_mode"] = "student"
        top, saved = self._open_settings()
        radios = self._option_rows(top, "radio")
        self.assertEqual(len(radios), len(modes))
        picked = modes.index("student")
        self.assertEqual([r.get() for r in radios],
                         [i == picked for i in range(len(modes))])

        other = modes.index("manual")
        radios[other]._on_click(None)
        self.assertEqual([r.get() for r in radios],
                         [i == other for i in range(len(modes))])
        self.app.update_idletasks()
        top.destroy()

    def test_settings_writes_back_save_mode(self):
        from grade_app.ui.dialogs import SettingsDialog
        modes = [key for key, _label, _hint in SettingsDialog.SAVE_MODES]
        self.cfg["auto_save_mode"] = "score"
        top, saved = self._open_settings()
        self._option_rows(top, "radio")[modes.index("student")]._on_click(None)
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
