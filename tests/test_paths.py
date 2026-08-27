"""打包路径测试：只读资源与用户数据要分开。

运行：python -m unittest tests.test_paths -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock

from grade_app import paths


class FrozenContext:
    """临时把进程伪装成打包后的独立程序。"""

    def __init__(self, meipass: str, platform: str = "darwin"):
        self.meipass = meipass
        self.platform = platform
        self._patchers = []

    def __enter__(self):
        self._patchers = [
            mock.patch.object(sys, "frozen", True, create=True),
            mock.patch.object(sys, "_MEIPASS", self.meipass, create=True),
            mock.patch.object(sys, "platform", self.platform),
        ]
        for p in self._patchers:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patchers):
            p.stop()
        return False


class TestSourceRun(unittest.TestCase):
    """源码运行时一切照旧落在项目目录，开发体验不变。"""

    def test_not_frozen(self):
        self.assertFalse(paths.is_frozen())

    def test_resource_and_data_both_project_root(self):
        self.assertEqual(paths.resource_dir(), paths.project_root())
        self.assertEqual(paths.user_data_dir(), paths.project_root())

    def test_config_stays_in_project(self):
        from grade_app import config
        self.assertEqual(os.path.dirname(config.config_path()),
                         paths.project_root())


class TestFrozenRun(unittest.TestCase):
    """打包后程序目录只读：模型跟着程序走，配置写用户目录。"""

    def test_resource_dir_follows_bundle(self):
        with tempfile.TemporaryDirectory() as tmp, FrozenContext(tmp):
            self.assertEqual(paths.resource_dir(), tmp)

    def test_user_data_dir_is_outside_bundle(self):
        with tempfile.TemporaryDirectory() as tmp, FrozenContext(tmp):
            data = paths.user_data_dir()
            self.assertNotEqual(data, tmp)
            self.assertFalse(data.startswith(tmp))
            self.assertTrue(os.path.isdir(data))
            self.assertTrue(os.access(data, os.W_OK))

    def test_user_data_dir_per_platform(self):
        cases = {
            "darwin": os.path.expanduser("~/Library/Application Support"),
            "win32": None,          # 走 APPDATA，下面单独测
            "linux": None,
        }
        with tempfile.TemporaryDirectory() as tmp:
            for platform, expected in cases.items():
                if expected is None:
                    continue
                with FrozenContext(tmp, platform=platform):
                    self.assertTrue(
                        paths.user_data_dir().startswith(expected), platform)

    def test_windows_uses_appdata(self):
        with tempfile.TemporaryDirectory() as tmp, \
                tempfile.TemporaryDirectory() as appdata, \
                FrozenContext(tmp, platform="win32"), \
                mock.patch.dict(os.environ, {"APPDATA": appdata}):
            self.assertTrue(paths.user_data_dir().startswith(appdata))

    def test_config_written_to_user_dir(self):
        from grade_app import config
        with tempfile.TemporaryDirectory() as tmp, FrozenContext(tmp):
            self.assertTrue(
                config.config_path().startswith(paths.user_data_dir()))

    def test_bundled_model_found_next_to_program(self):
        from grade_app import speech
        with tempfile.TemporaryDirectory() as tmp, FrozenContext(tmp):
            os.makedirs(os.path.join(tmp, "models", "sense-voice"))
            self.assertEqual(speech.model_dir_path({"model_dir": "models"}),
                             os.path.join(tmp, "models"))

    def test_absolute_model_dir_still_honoured(self):
        from grade_app import speech
        with tempfile.TemporaryDirectory() as tmp, FrozenContext(tmp):
            self.assertEqual(
                speech.model_dir_path({"model_dir": "/opt/models"}),
                "/opt/models")


if __name__ == "__main__":
    unittest.main()
