"""跨平台适配层测试。运行：python -m unittest tests.test_platform -v"""
from __future__ import annotations

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

    def test_mac_uses_button_2(self):
        # macOS 的 Tk 把右键报成 Button-2，绑 Button-3 会点不出菜单
        expected = ("<Button-2>", "<Control-Button-1>") if ps.IS_MAC else ("<Button-3>",)
        self.assertEqual(ps.RIGHT_CLICK_EVENTS, expected)


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


if __name__ == "__main__":
    unittest.main()
