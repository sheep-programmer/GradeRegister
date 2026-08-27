"""语音引擎选择与识别结果清洗。

真正的模型推理不在这里跑（依赖 200MB+ 模型文件），只覆盖不需要模型的部分。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from grade_app import speech
from grade_app.config import DEFAULT_CONFIG


class TestResultCleanup(unittest.TestCase):
    def test_strips_language_tags(self):
        self.assertEqual(
            speech._strip_tags("<|zh|><|NEUTRAL|><|Speech|><|woitn|>第一题十五分"),
            "第一题十五分")

    def test_strips_pinyin_fragments(self):
        """中英混词表偶尔在汉字之间吐出拼音碎片。"""
        self.assertEqual(speech._strip_tags("张ang三"), "张三")
        self.assertEqual(speech._strip_tags("<|zh|>赵leiE雷"), "赵雷")

    def test_keeps_pure_ascii_result(self):
        """整句都是字母时不做删除，否则会把结果清空。"""
        self.assertEqual(speech._strip_tags("<|en|>hello"), "hello")

    def test_strips_trailing_punctuation(self):
        self.assertEqual(speech._strip_tags("总分六十分。"), "总分六十分")

    def test_handles_empty(self):
        self.assertEqual(speech._strip_tags(""), "")
        self.assertEqual(speech._strip_tags(None), "")


class TestModelPaths(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = dict(DEFAULT_CONFIG, model_dir=self.tmp)

    def test_not_ready_without_files(self):
        self.assertFalse(speech.sense_voice_ready(self.cfg))

    def test_ready_when_both_files_present(self):
        d = speech.sense_voice_dir(self.cfg)
        os.makedirs(d)
        for name, _url, _size in speech.SENSE_VOICE_FILES:
            open(os.path.join(d, name), "w").close()
        self.assertTrue(speech.sense_voice_ready(self.cfg))

    def test_download_skips_existing_files(self):
        d = speech.sense_voice_dir(self.cfg)
        os.makedirs(d)
        for name, _url, _size in speech.SENSE_VOICE_FILES:
            open(os.path.join(d, name), "w").close()
        with mock.patch("urllib.request.urlretrieve") as fetch:
            speech.download_sense_voice(self.cfg)
        fetch.assert_not_called()

    def test_ready_check_does_not_download(self):
        """启动脚本靠它判断要不要下模型，本身绝不能触发下载。"""
        with mock.patch("urllib.request.urlretrieve") as fetch:
            self.assertFalse(speech.engine_model_ready(self.cfg))
        fetch.assert_not_called()

    def test_progress_counts_all_files_as_one_total(self):
        """两个文件要合成一条进度，不能各自从 0% 走一遍。"""
        seen = []

        def fake_fetch(url, dst, reporthook=None):
            open(dst, "w").close()
            size = next(s for _n, u, s in speech.SENSE_VOICE_FILES if u == url)
            reporthook(1, size // 2, size)
            reporthook(2, size // 2, size)

        with mock.patch("urllib.request.urlretrieve", fake_fetch):
            speech.download_sense_voice(self.cfg,
                                        progress=lambda d, t: seen.append((d, t)))
        totals = {t for _d, t in seen}
        self.assertEqual(len(totals), 1)            # 全程同一个分母
        self.assertEqual([d for d, _t in seen], sorted(d for d, _t in seen))
        self.assertEqual(seen[-1][0], seen[-1][1])  # 收尾走满 100%

    def test_partial_file_removed_on_failure(self):
        """下载断了要清掉残片，否则几百 MB 垃圾留在盘上。"""
        def boom(url, dst, reporthook=None):
            with open(dst, "w") as f:
                f.write("half")
            raise OSError("断网")

        with mock.patch("urllib.request.urlretrieve", boom):
            with self.assertRaises(OSError):
                speech.download_sense_voice(self.cfg)
        d = speech.sense_voice_dir(self.cfg)
        self.assertEqual([n for n in os.listdir(d) if n.endswith(".part")], [])

    def test_status_text_mentions_size_when_missing(self):
        self.assertIn("228MB", speech.engine_model_status(self.cfg, "sense-voice"))
        self.assertIn("42MB", speech.engine_model_status(self.cfg, "vosk"))

    def test_status_text_when_ready(self):
        d = speech.sense_voice_dir(self.cfg)
        os.makedirs(d)
        for name, _url, _size in speech.SENSE_VOICE_FILES:
            open(os.path.join(d, name), "w").close()
        self.assertEqual(speech.engine_model_status(self.cfg, "sense-voice"),
                         "模型已就绪")

    def test_model_dir_is_absolute(self):
        """从任意工作目录启动都要找得到模型。"""
        cfg = dict(DEFAULT_CONFIG)
        self.assertTrue(os.path.isabs(speech.sense_voice_dir(cfg)))


class TestEngineSelection(unittest.TestCase):
    def test_default_engine_is_sense_voice(self):
        self.assertEqual(DEFAULT_CONFIG["engine"], "sense-voice")

    def test_falls_back_when_model_missing(self):
        """模型没下好时不能崩，退到下一个引擎。"""
        cfg = dict(DEFAULT_CONFIG, model_dir=tempfile.mkdtemp())
        with mock.patch.object(speech, "SherpaEngine",
                               side_effect=FileNotFoundError("没模型")), \
             mock.patch.object(speech, "VoskEngine") as vosk, \
             mock.patch("importlib.util.find_spec", return_value=None):
            speech.create_engine(cfg)
        vosk.assert_called_once()

    def test_offline_engine_declares_itself_non_streaming(self):
        """录音线程据此选择切句策略。"""
        self.assertFalse(speech.SenseVoiceEngine.streaming)
        self.assertFalse(speech.FasterWhisperEngine.streaming)
        self.assertTrue(getattr(speech.VoskEngine, "streaming", True))


if __name__ == "__main__":
    unittest.main()
