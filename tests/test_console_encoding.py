"""控制台编码测试：中文输出在 Windows 上不能崩。

运行：python -m unittest tests.test_console_encoding -v
"""
from __future__ import annotations

import io
import sys
import unittest

from grade_app import platform_support

CHINESE = "当前语音引擎: sense-voice（约 228MB）"


class LegacyConsole:
    """把标准输出换成 Windows 传统代码页，模拟 CI 与中文版命令提示符。"""

    def __init__(self, encoding: str = "cp1252"):
        self.encoding = encoding
        self.buffer = io.BytesIO()

    def __enter__(self):
        self._real_out, self._real_err = sys.stdout, sys.stderr
        self.stream = io.TextIOWrapper(self.buffer, encoding=self.encoding,
                                       errors="strict", line_buffering=True)
        sys.stdout = sys.stderr = self.stream
        return self

    def __exit__(self, *exc):
        try:
            self.stream.flush()
        except Exception:      # noqa: BLE001
            pass
        sys.stdout, sys.stderr = self._real_out, self._real_err
        return False

    def text(self) -> str:
        return self.buffer.getvalue().decode("utf-8", errors="replace")


class TestLegacyConsoleReproduces(unittest.TestCase):
    def test_chinese_breaks_without_the_fix(self):
        """先证明这个坑是真的：不处理就是 CI 上那个 UnicodeEncodeError。"""
        with LegacyConsole() as console:
            with self.assertRaises(UnicodeEncodeError):
                print(CHINESE)
            del console


class TestEnsureUtf8Output(unittest.TestCase):
    def test_chinese_prints_after_fix(self):
        with LegacyConsole() as console:
            platform_support.ensure_utf8_output()
            print(CHINESE)
        self.assertIn("sense-voice", console.text())
        self.assertIn("当前语音引擎", console.text())

    def test_works_for_gbk_console_too(self):
        """中文版 Windows 命令提示符是 cp936，同样要能打印。"""
        with LegacyConsole(encoding="cp936") as console:
            platform_support.ensure_utf8_output()
            print(CHINESE)
        self.assertIn("当前语音引擎", console.text())

    def test_survives_missing_stdout(self):
        """打包成无控制台窗口的程序时 stdout 可能是 None，不能因此崩掉。"""
        real_out, real_err = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = None
        try:
            platform_support.ensure_utf8_output()      # 不抛异常即可
        finally:
            sys.stdout, sys.stderr = real_out, real_err

    def test_survives_stream_without_reconfigure(self):
        """有些替身流（测试桩、日志捕获）没有 reconfigure，要静默跳过。"""
        real_out = sys.stdout
        sys.stdout = io.StringIO()
        try:
            platform_support.ensure_utf8_output()
            print(CHINESE)
            self.assertIn("当前语音引擎", sys.stdout.getvalue())
        finally:
            sys.stdout = real_out

    def test_idempotent(self):
        with LegacyConsole() as console:
            platform_support.ensure_utf8_output()
            platform_support.ensure_utf8_output()
            print(CHINESE)
        self.assertIn("当前语音引擎", console.text())


class TestEntryPointsCallIt(unittest.TestCase):
    """两个命令行入口都必须先把编码理顺，否则第一句中文就崩。"""

    def test_main_entry_calls_it(self):
        import main
        with open(main.__file__, encoding="utf-8") as f:
            source = f.read()
        self.assertIn("ensure_utf8_output()", source)

    def test_download_model_entry_calls_it(self):
        import os
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "download_model.py")
        with open(path, encoding="utf-8") as f:
            source = f.read()
        self.assertIn("ensure_utf8_output()", source)


if __name__ == "__main__":
    unittest.main()
