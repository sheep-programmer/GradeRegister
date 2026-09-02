"""跨平台适配层测试。运行：python -m unittest tests.test_platform -v"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from unittest import mock

from grade_app import platform_support as ps


class TestPlatformFlags(unittest.TestCase):
    def test_exactly_one_platform(self):
        self.assertEqual(sum([ps.IS_MAC, ps.IS_WINDOWS, ps.IS_LINUX]), 1)

    def test_platform_name(self):
        self.assertIn(ps.platform_name(), ("macOS", "Windows", "Linux"))


class TestOpenInDefaultApp(unittest.TestCase):
    def test_windows_uses_startfile(self):
        fake_startfile = mock.Mock()
        with mock.patch.object(ps, "IS_WINDOWS", True), \
                mock.patch.object(ps, "IS_MAC", False), \
                mock.patch("os.startfile", fake_startfile, create=True):
            ps.open_in_default_app("表格.xlsx")
        fake_startfile.assert_called_once_with("表格.xlsx")

    def test_mac_uses_open_command(self):
        with mock.patch.object(ps, "IS_WINDOWS", False), \
                mock.patch.object(ps, "IS_MAC", True), \
                mock.patch("subprocess.Popen") as popen:
            ps.open_in_default_app("表格.xlsx")
        popen.assert_called_once_with(["open", "表格.xlsx"])

    def test_linux_uses_xdg_open(self):
        with mock.patch.object(ps, "IS_WINDOWS", False), \
                mock.patch.object(ps, "IS_MAC", False), \
                mock.patch("subprocess.Popen") as popen:
            ps.open_in_default_app("表格.xlsx")
        popen.assert_called_once_with(["xdg-open", "表格.xlsx"])


class TestMicrophoneHint(unittest.TestCase):
    def test_hint_mentions_platform_path(self):
        with mock.patch.object(ps, "IS_MAC", True), \
                mock.patch.object(ps, "IS_WINDOWS", False):
            self.assertIn("隐私与安全性", ps.microphone_permission_hint())
        with mock.patch.object(ps, "IS_MAC", False), \
                mock.patch.object(ps, "IS_WINDOWS", True):
            self.assertIn("隐私和安全性", ps.microphone_permission_hint())

    def test_open_settings_reports_failure_instead_of_raising(self):
        with mock.patch.object(ps, "IS_MAC", True), \
                mock.patch("subprocess.Popen", side_effect=OSError("boom")):
            self.assertFalse(ps.open_microphone_settings())

    def test_open_settings_succeeds_on_mac(self):
        with mock.patch.object(ps, "IS_MAC", True), \
                mock.patch("subprocess.Popen") as popen:
            self.assertTrue(ps.open_microphone_settings())
        self.assertIn("Privacy_Microphone", popen.call_args.args[0][1])


class TestFonts(unittest.TestCase):
    def test_falls_back_when_no_candidate_installed(self):
        with mock.patch("tkinter.font.families", return_value=("Arial",)):
            self.assertEqual(ps._first_available(("不存在的字体",), "TkDefaultFont"),
                             "TkDefaultFont")

    def test_picks_first_installed_candidate(self):
        with mock.patch("tkinter.font.families",
                        return_value=("Arial", "Microsoft YaHei UI")):
            self.assertEqual(
                ps._first_available(("PingFang SC", "Microsoft YaHei UI"), "X"),
                "Microsoft YaHei UI")

    def test_survives_without_tk_root(self):
        with mock.patch("tkinter.font.families", side_effect=RuntimeError):
            self.assertEqual(ps._first_available(("甲", "乙"), "回落"), "甲")


class TestRightClick(unittest.TestCase):
    def test_binds_every_platform_event(self):
        widget = mock.Mock()
        handler = object()
        ps.bind_right_click(widget, handler)
        bound = {call.args[0] for call in widget.bind.call_args_list}
        self.assertEqual(bound, set(ps.RIGHT_CLICK_EVENTS))
        self.assertTrue(bound)

    def test_mac_covers_both_button_numbers(self):
        """Tk 8 把 macOS 右键报成 Button-2，Tk 9 起改成 Button-3，都要覆盖。"""
        if ps.IS_MAC:
            self.assertIn("<Button-2>", ps.RIGHT_CLICK_EVENTS)
            self.assertIn("<Button-3>", ps.RIGHT_CLICK_EVENTS)
            self.assertIn("<Control-Button-1>", ps.RIGHT_CLICK_EVENTS)
        else:
            self.assertEqual(ps.RIGHT_CLICK_EVENTS, ("<Button-3>",))

    def test_mac_picks_button_by_tk_version(self):
        """能问出 Tk 版本时只绑对应的那个键，不误伤中键。"""
        if not ps.IS_MAC:
            self.skipTest("仅 macOS 存在两套编号")

        def widget_with_tk(patchlevel):
            w = mock.Mock()
            w.tk.call.return_value = patchlevel
            return w

        self.assertEqual(ps.right_click_events(widget_with_tk("9.0.4")),
                         ("<Button-3>", "<Control-Button-1>"))
        self.assertEqual(ps.right_click_events(widget_with_tk("8.6.13")),
                         ("<Button-2>", "<Control-Button-1>"))

    def test_falls_back_to_all_candidates_when_version_unknown(self):
        """问不出 Tk 版本时宁可多绑一个：少绑的后果是右键菜单完全打不开。"""
        w = mock.Mock()
        w.tk.call.side_effect = RuntimeError("没有解释器")
        self.assertEqual(ps.right_click_events(w), ps.RIGHT_CLICK_EVENTS)


class TestSettingsAreReal(unittest.TestCase):
    """设置面板不许有假开关：显示的状态要和代码实际采用的一致。"""

    def test_toggle_defaults_match_the_config_defaults(self):
        """面板缺键时的兜底值必须取自 DEFAULT_CONFIG。

        写死 True 的话，配置里缺 hold_to_talk / auto_next 时面板显示「开」
        而代码按「关」跑——看着是开着的功能其实没生效。
        """
        from grade_app.config import DEFAULT_CONFIG
        from grade_app.ui.dialogs import SettingsDialog
        for _title, items in SettingsDialog.CHECKS:
            for key, label, _hint in items:
                self.assertIn(key, DEFAULT_CONFIG,
                              f"「{label}」对应的 {key} 不在 DEFAULT_CONFIG 里")

    def test_native_endpoint_list_matches_the_engines(self):
        """自带端点检测的名单要跟引擎真实能力一致。

        名单少一个，那个引擎下的「断句停顿」就是个能点但没用的假设置；
        多一个，本来有效的设置被白白置灰。
        """
        from grade_app import speech
        cases = {"sherpa": speech.SherpaEngine,
                 "sense-voice": speech.SenseVoiceEngine,
                 "paraformer": speech.ParaformerEngine,
                 "vosk": speech.VoskEngine}
        for name, cls in cases.items():
            self.assertEqual(
                hasattr(cls, "check_endpoint"),
                name in speech.NATIVE_ENDPOINT_ENGINES,
                f"{name} 的 check_endpoint 能力与 NATIVE_ENDPOINT_ENGINES 不符")


class TestWindowsCloseWorkbook(unittest.TestCase):
    """Windows 走 COM 关 Excel。真机在 macOS 上跑不了 COM，这里用假的
    win32com 把决策逻辑验出来：认对工作簿、先存后关、别碰其他表格。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "成绩表.xlsx")
        with open(self.path, "wb") as f:
            f.write(b"x")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    class _Book:
        def __init__(self, full_name, saved=True):
            self.FullName = full_name
            self.Saved = saved
            self.calls = []

        def Save(self):
            self.calls.append("Save")
            self.Saved = True

        def Close(self, SaveChanges=None):
            self.calls.append(f"Close(SaveChanges={SaveChanges})")

    def _run(self, books, get_active=None):
        """装上假的 win32com / pythoncom，跑 Windows 分支。"""
        app = mock.Mock()
        app.Workbooks = books
        client = mock.Mock()
        client.GetActiveObject = (get_active if get_active
                                  else mock.Mock(return_value=app))
        win32com = mock.Mock(client=client)
        mods = {"win32com": win32com, "win32com.client": client,
                "pythoncom": mock.Mock()}
        with mock.patch.dict("sys.modules", mods), \
             mock.patch.object(ps, "IS_WINDOWS", True), \
             mock.patch.object(ps, "IS_MAC", False):
            return ps.close_excel_workbook(self.path)

    def test_missing_pywin32_says_so(self):
        with mock.patch.dict("sys.modules", {"win32com.client": None}), \
             mock.patch.object(ps, "IS_WINDOWS", True), \
             mock.patch.object(ps, "IS_MAC", False):
            ok, why = ps.close_excel_workbook(self.path)
        self.assertFalse(ok)
        self.assertIn("pywin32", why)

    def test_excel_not_running(self):
        boom = mock.Mock(side_effect=OSError("没在跑"))
        ok, why = self._run([], get_active=boom)
        self.assertFalse(ok)
        self.assertIn("没有在运行", why)

    def test_closes_the_matching_workbook(self):
        book = self._Book(self.path)
        ok, _why = self._run([book])
        self.assertTrue(ok)
        self.assertEqual(book.calls, ["Close(SaveChanges=False)"])

    def test_saves_unsaved_edits_before_closing(self):
        """老师在 Excel 里改了没存：先存下来，别把他的改动丢了。"""
        book = self._Book(self.path, saved=False)
        ok, _why = self._run([book])
        self.assertTrue(ok)
        self.assertEqual(book.calls, ["Save", "Close(SaveChanges=False)"])

    def test_leaves_other_workbooks_alone(self):
        """整个进程杀掉会连老师正在编辑的别的文件一起丢，只能关这一个。"""
        other_a = self._Book(os.path.join(self.tmp, "别的A.xlsx"), saved=False)
        target = self._Book(self.path)
        other_b = self._Book(os.path.join(self.tmp, "别的B.xlsx"), saved=False)
        ok, _why = self._run([other_a, target, other_b])
        self.assertTrue(ok)
        self.assertEqual(target.calls, ["Close(SaveChanges=False)"])
        self.assertEqual(other_a.calls, [])
        self.assertEqual(other_b.calls, [])

    def test_reports_when_that_book_is_not_open(self):
        other = self._Book(os.path.join(self.tmp, "别的.xlsx"))
        ok, why = self._run([other])
        self.assertFalse(ok)
        self.assertIn("没有打开", why)
        self.assertEqual(other.calls, [])

    def test_a_workbook_with_an_unreadable_name_is_skipped(self):
        """取不到 FullName 的工作簿跳过就好，不能让整个流程崩掉。"""
        bad = mock.Mock()
        type(bad).FullName = mock.PropertyMock(side_effect=OSError("读不到"))
        target = self._Book(self.path)
        ok, _why = self._run([bad, target])
        self.assertTrue(ok)
        self.assertEqual(target.calls, ["Close(SaveChanges=False)"])


class TestNotificationSounds(unittest.TestCase):
    """五种提示音在两个平台上都得齐全且互不相同——撞车就分辨不出来。"""

    KINDS = ("ok", "pick", "success", "warn", "error")

    def test_mac_has_a_distinct_sound_for_each(self):
        got = [ps._MAC_SOUNDS.get(k) for k in self.KINDS]
        self.assertTrue(all(got), f"有用途没配音: {dict(zip(self.KINDS, got))}")
        self.assertEqual(len(set(got)), len(self.KINDS), "有两种用途共用一个音")

    def test_windows_has_a_distinct_beep_for_each(self):
        import pathlib
        import re
        src = pathlib.Path(ps.__file__).read_text(encoding="utf-8")
        mapping = dict(re.findall(r'"(\w+)": winsound\.(MB_\w+)', src))
        for k in self.KINDS:
            self.assertIn(k, mapping, f"Windows 上 {k} 没配音")
        beeps = [mapping[k] for k in self.KINDS]
        self.assertEqual(len(set(beeps)), len(self.KINDS),
                         f"有两种用途共用一个 MessageBeep: {mapping}")


class TestExcelOccupation(unittest.TestCase):
    """检测表格是否正被 Excel 占用。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "成绩表.xlsx")
        with open(self.path, "wb") as f:
            f.write(b"x")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_lock_path_sits_next_to_the_file(self):
        self.assertEqual(ps.excel_lock_path(self.path),
                         os.path.join(self.tmp, "~$成绩表.xlsx"))

    def test_not_held_when_there_is_no_marker(self):
        self.assertFalse(ps.excel_holds_file(self.path))

    def test_held_when_excel_left_its_marker(self):
        open(ps.excel_lock_path(self.path), "wb").close()
        self.assertTrue(ps.excel_holds_file(self.path))

    def test_writable_file_reports_writable(self):
        self.assertTrue(ps.file_is_writable(self.path))

    def test_missing_file_is_not_writable(self):
        self.assertFalse(ps.file_is_writable(self.path + ".nope"))

    def test_closing_a_missing_file_is_refused(self):
        ok, why = ps.close_excel_workbook(self.path + ".nope")
        self.assertFalse(ok)
        self.assertIn("不存在", why)

    def test_mac_close_is_a_noop_when_excel_is_not_running(self):
        """Excel 没在跑时脚本必须什么都不做，尤其不能把它启动起来。"""
        if not ps.IS_MAC:
            self.skipTest("仅 macOS")
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="notrunning",
                                         stderr="")
            ok, why = ps.close_excel_workbook(self.path)
        self.assertFalse(ok)
        self.assertIn("没有在运行", why)

    def test_mac_close_reports_success(self):
        if not ps.IS_MAC:
            self.skipTest("仅 macOS")
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="1", stderr="")
            ok, _why = ps.close_excel_workbook(self.path)
        self.assertTrue(ok)

    def test_mac_close_reports_when_that_book_is_not_open(self):
        if not ps.IS_MAC:
            self.skipTest("仅 macOS")
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="0", stderr="")
            ok, why = ps.close_excel_workbook(self.path)
        self.assertFalse(ok)
        self.assertIn("没有打开", why)

    def test_mac_script_saves_before_closing(self):
        """老师在 Excel 里没保存的改动必须先存下来，不能丢。"""
        if not ps.IS_MAC:
            self.skipTest("仅 macOS")
        self.assertIn("save workbook i", ps._MAC_CLOSE_SCRIPT)
        self.assertIn("is not running", ps._MAC_CLOSE_SCRIPT)

    def test_windows_close_needs_pywin32(self):
        """缺 pywin32 时如实说明，不要假装关掉了。"""
        if not ps.IS_WINDOWS:
            self.skipTest("仅 Windows")
        with mock.patch.dict("sys.modules", {"win32com.client": None}):
            ok, _why = ps.close_excel_workbook(self.path)
        self.assertFalse(ok)


class TestAccelerator(unittest.TestCase):
    def test_mac(self):
        with mock.patch.object(ps, "IS_MAC", True):
            self.assertEqual(ps.accelerator("s"), ("⌘S", "<Command-s>"))

    def test_windows(self):
        with mock.patch.object(ps, "IS_MAC", False):
            self.assertEqual(ps.accelerator("s"), ("Ctrl+S", "<Control-s>"))


class TestDpi(unittest.TestCase):
    def test_enable_is_noop_off_windows(self):
        with mock.patch.object(ps, "IS_WINDOWS", False):
            ps.enable_dpi_awareness()   # 不抛异常即可

    def test_scaling_falls_back_to_one(self):
        root = mock.Mock()
        root.winfo_fpixels.side_effect = RuntimeError
        self.assertEqual(ps.dpi_scaling(root), 1.0)

    def test_scaling_from_dpi(self):
        root = mock.Mock()
        root.winfo_fpixels.return_value = 144.0
        self.assertAlmostEqual(ps.dpi_scaling(root), 1.5)


class TestSanitizeGeometry(unittest.TestCase):
    """恢复窗口几何时的安全校验：尺寸不缩成缝、位置不飘出屏。"""

    SW, SH = 1920, 1080

    def test_valid_geometry_passes_through(self):
        got = ps.sanitize_geometry("1400x900+100+50", self.SW, self.SH)
        self.assertEqual(got, "1400x900+100+50")

    def test_missing_or_garbage_falls_back(self):
        self.assertEqual(ps.sanitize_geometry("", self.SW, self.SH), "1000x660")
        self.assertEqual(ps.sanitize_geometry("oops", self.SW, self.SH), "1000x660")
        self.assertEqual(ps.sanitize_geometry("1400x900", self.SW, self.SH), "1000x660")

    def test_small_size_clamped_up(self):
        self.assertEqual(
            ps.sanitize_geometry("800x400+10+10", self.SW, self.SH),
            "1000x660+10+10")

    def test_oversized_size_clamped_to_screen(self):
        self.assertEqual(
            ps.sanitize_geometry("4000x3000+0+0", self.SW, self.SH),
            "1920x1080+0+0")

    def test_off_screen_position_falls_back(self):
        # 位置在屏幕右下方之外，回来后整块看不到
        self.assertEqual(
            ps.sanitize_geometry("1400x900+3000+2000", self.SW, self.SH),
            "1000x660")

    def test_negative_position_kept_when_visible(self):
        # 部分在窗外但露出一角，保留即可
        self.assertEqual(
            ps.sanitize_geometry("1400x900-100+50", self.SW, self.SH),
            "1400x900-100+50")


if __name__ == "__main__":
    unittest.main()
