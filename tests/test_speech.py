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


class TestParaformerEngine(unittest.TestCase):
    """paraformer 引擎与 sense-voice 同接口，模型目录独立解析。"""

    def _cfg(self, model_dir):
        return {"model_dir": model_dir, "engine": "paraformer"}

    def test_dir_resolves_under_model_dir(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(
                speech.paraformer_dir(self._cfg(d)),
                os.path.join(d, "paraformer-zh"))

    def test_not_ready_without_files(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(speech.paraformer_ready(self._cfg(d)))

    def test_ready_when_both_files_present(self):
        with tempfile.TemporaryDirectory() as d:
            pd = speech.paraformer_dir(self._cfg(d))
            os.makedirs(pd)
            for name, _url, _size in speech.PARAFORMER_FILES:
                open(os.path.join(pd, name), "w").close()
            self.assertTrue(speech.paraformer_ready(self._cfg(d)))

    def test_download_skips_existing_files(self):
        with tempfile.TemporaryDirectory() as d:
            pd = speech.paraformer_dir(self._cfg(d))
            os.makedirs(pd)
            for name, _url, _size in speech.PARAFORMER_FILES:
                open(os.path.join(pd, name), "w").close()
            with mock.patch("urllib.request.urlretrieve") as rv:
                speech.download_paraformer(self._cfg(d))
            rv.assert_not_called()

    def test_engine_uses_from_paraformer(self):
        with tempfile.TemporaryDirectory() as d:
            pd = speech.paraformer_dir(self._cfg(d))
            os.makedirs(pd)
            for name, _url, _size in speech.PARAFORMER_FILES:
                open(os.path.join(pd, name), "w").close()
            with mock.patch(
                    "sherpa_onnx.OfflineRecognizer.from_paraformer") as fp:
                speech.ParaformerEngine(self._cfg(d))
            fp.assert_called_once()
            args = fp.call_args.kwargs
            self.assertTrue(args["paraformer"].endswith("model.int8.onnx"))
            self.assertTrue(args["tokens"].endswith("tokens.txt"))

    def test_create_engine_returns_paraformer(self):
        with tempfile.TemporaryDirectory() as d:
            pd = speech.paraformer_dir(self._cfg(d))
            os.makedirs(pd)
            for name, _url, _size in speech.PARAFORMER_FILES:
                open(os.path.join(pd, name), "w").close()
            with mock.patch.object(
                    speech, "ParaformerEngine",
                    return_value=object()) as pe:
                got = speech.create_engine(self._cfg(d))
            self.assertIs(got, pe.return_value)


if __name__ == "__main__":
    unittest.main()


class TestVocabNumbersRoundTrip(unittest.TestCase):
    """词表里的中文数字必须能被解析器原样读回来。

    喂给识别器的词表会让它偏向产出表里的写法。表里放了解析器读不懂的
    写法，就是静默的准确率损失——110 曾被写成「一百十」，解析回来是 100。
    """

    def test_every_generated_number_parses_back(self):
        from grade_app.parser import cn_number_to_float
        bad = [(n, speech._cn_num(n), cn_number_to_float(speech._cn_num(n)))
               for n in range(1000)
               if cn_number_to_float(speech._cn_num(n)) != n]
        self.assertEqual(bad, [], f"往返不一致 {len(bad)} 个，前几个：{bad[:5]}")

    def test_hundreds_use_the_standard_reading(self):
        """110 读作「一百一十」，不是「一百十」。"""
        self.assertEqual(speech._cn_num(110), "一百一十")
        self.assertEqual(speech._cn_num(115), "一百一十五")
        self.assertEqual(speech._cn_num(120), "一百二十")
        self.assertEqual(speech._cn_num(105), "一百零五")

    def test_parser_also_tolerates_the_colloquial_reading(self):
        """老师或识别器真吐出「一百十」时也不能算错。"""
        from grade_app.parser import cn_number_to_float
        self.assertEqual(cn_number_to_float("一百十"), 110)
        self.assertEqual(cn_number_to_float("一百十五"), 115)

    def test_vocab_contains_the_scores_teachers_say(self):
        vocab = set(speech.build_grade_vocab(["张三"], ["第一题"]))
        for n in (0, 5, 10, 18, 60, 100, 110, 120):
            self.assertIn(speech._cn_num(n), vocab, f"{n} 不在词表里")


class TestRecognizerLanguage(unittest.TestCase):
    """必须锁定中文：多语种自动判语种会把短句判成英文。"""

    def test_sense_voice_pins_chinese(self):
        import inspect
        from grade_app import speech
        source = inspect.getsource(speech.SenseVoiceEngine.__init__)
        self.assertIn('language="zh"', source)


class TestMishearNumbers(unittest.TestCase):
    """同音误听：「七分」常被听成「气氛」，「三分」听成「三粉」。"""

    def test_qifen_reads_as_seven(self):
        from grade_app import parser
        self.assertEqual(parser.extract_scores("气氛"), [7.0])

    def test_fen_homophones(self):
        from grade_app import parser
        self.assertEqual(parser.extract_scores("三粉"), [3.0])
        self.assertEqual(parser.extract_scores("十份"), [10.0])

    def test_common_name_char_not_rewritten(self):
        """「芬」是常见人名用字，不能当成「分」改掉，否则点名会出错。"""
        from grade_app import parser
        self.assertIn("李芬", parser.match_student_names("李芬", ["李芬", "张三"]))

    def test_extended_digit_homophones(self):
        """扩展的同音字映射：失/期/吧/肆/漆/伍/耳/酒/伞 归到对应数字。"""
        from grade_app import parser
        self.assertEqual(parser.extract_scores("失误"), [15.0])
        self.assertEqual(parser.extract_scores("时期"), [17.0])
        self.assertEqual(parser.extract_scores("是吧"), [18.0])
        self.assertEqual(parser.extract_scores("肆"), [4.0])
        self.assertEqual(parser.extract_scores("漆"), [7.0])
        self.assertEqual(parser.extract_scores("伍"), [5.0])
        self.assertEqual(parser.extract_scores("耳"), [2.0])
        self.assertEqual(parser.extract_scores("酒"), [9.0])
        self.assertEqual(parser.extract_scores("伞"), [3.0])


class TestAvailableEngines(unittest.TestCase):
    """只列真正能用起来的引擎——列出来又切不过去，比不列更糟。"""

    def _cfg(self, model_dir):
        return {"model_dir": model_dir, "engine": "sense-voice"}

    def test_sense_voice_always_listed(self):
        from grade_app import speech
        with tempfile.TemporaryDirectory() as d:
            self.assertIn("sense-voice", speech.available_engines(self._cfg(d)))

    def test_paraformer_always_listed(self):
        """paraformer 与 sense-voice 一样内置，缺了模型可自动下载。"""
        from grade_app import speech
        with tempfile.TemporaryDirectory() as d:
            self.assertIn("paraformer", speech.available_engines(self._cfg(d)))

    def test_sherpa_hidden_without_its_model(self):
        """打包版没带 sherpa 模型，列出来点了就报 tokens.txt 找不到。"""
        from grade_app import speech
        with tempfile.TemporaryDirectory() as d:
            self.assertNotIn("sherpa", speech.available_engines(self._cfg(d)))

    def test_sherpa_listed_when_model_present(self):
        from grade_app import speech
        with tempfile.TemporaryDirectory() as d:
            sd = os.path.join(d, "sherpa", "sherpa-onnx-streaming-x")
            os.makedirs(sd)
            open(os.path.join(sd, "tokens.txt"), "w").close()
            self.assertIn("sherpa", speech.available_engines(self._cfg(d)))

    def test_never_empty(self):
        from grade_app import speech
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(speech.available_engines(self._cfg(d)))

    def test_unavailable_engine_falls_back(self):
        """配置里存着 sherpa 但模型没了，要退回能用的，而不是一路崩到底。"""
        from grade_app import speech
        with tempfile.TemporaryDirectory() as d:
            cfg = {"model_dir": d, "engine": "sherpa"}
            self.assertEqual(speech.resolve_engine(cfg), "sense-voice")

    def test_available_engine_kept(self):
        from grade_app import speech
        with tempfile.TemporaryDirectory() as d:
            cfg = {"model_dir": d, "engine": "sense-voice"}
            self.assertEqual(speech.resolve_engine(cfg), "sense-voice")


class TestSherpaHotwords(unittest.TestCase):
    """热词文件：名单写进用户数据目录，下一次录音开始时重建识别器生效。"""

    def _engine(self):
        """构造不带真实模型的 SherpaEngine，记录识别器重建调用。"""
        calls = []

        def fake_create(_self, hotwords_file):
            calls.append(hotwords_file)
            return mock.Mock()

        # patch 要活到测试结束：begin() 里也会调 _create_recognizer
        self.enterContext(mock.patch.object(
            speech.SherpaEngine, "_create_recognizer", fake_create))
        self.enterContext(mock.patch.object(
            speech, "sherpa_model_dir", return_value="/tmp/model"))
        eng = speech.SherpaEngine({}, 16000)
        return eng, calls

    def test_hotwords_path_lives_in_user_data_dir(self):
        """打包版模型目录只读，热词必须落在用户数据目录。"""
        with mock.patch.object(speech.paths, "user_data_dir",
                               return_value="/tmp/ud"):
            self.assertEqual(speech.hotwords_path({}),
                             os.path.join("/tmp/ud", "hotwords.txt"))

    def test_set_grammar_writes_sorted_unique_words(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(speech.paths, "user_data_dir", return_value=d):
                eng, _calls = self._engine()
                eng.set_grammar(["李四", "张三", "张三", "  ", "王五"])
                with open(os.path.join(d, "hotwords.txt"),
                          encoding="utf-8") as f:
                    content = f.read().splitlines()
            self.assertEqual(content, ["张三", "李四", "王五"])
            self.assertEqual(eng._hotwords_file,
                             os.path.join(d, "hotwords.txt"))
            self.assertTrue(eng._hotwords_dirty)

    def test_empty_grammar_clears_hotwords(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(speech.paths, "user_data_dir", return_value=d):
                eng, _calls = self._engine()
                eng.set_grammar(["张三"])
                eng.set_grammar(None)
                self.assertEqual(eng._hotwords_file, "")
                self.assertTrue(eng._hotwords_dirty)

    def test_begin_rebuilds_only_when_dirty(self):
        """加了热词后下一次 begin 重建识别器；之后没变化就不重复重建。"""
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(speech.paths, "user_data_dir", return_value=d):
                eng, calls = self._engine()
                eng.set_grammar(["张三"])
                eng.begin()
                self.assertEqual(calls, ["", os.path.join(d, "hotwords.txt")])
                eng.begin()
                self.assertEqual(calls, ["", os.path.join(d, "hotwords.txt")])
                self.assertFalse(eng._hotwords_dirty)

    def test_write_failure_keeps_no_hotwords(self):
        """用户目录写不进热词时退回无热词识别器，不能把录音搞崩。"""
        with mock.patch.object(speech.paths, "user_data_dir",
                               side_effect=OSError("只读")):
            eng, _calls = self._engine()
            eng.set_grammar(["张三"])          # 不抛异常
            self.assertEqual(eng._hotwords_file, "")
            self.assertFalse(eng._hotwords_dirty)

    def test_write_failure_with_existing_hotwords_clears(self):
        """先成功写入再写失败：下次录音重建为无热词识别器。"""
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(speech.paths, "user_data_dir", return_value=d):
                eng, _calls = self._engine()
                eng.set_grammar(["张三"])
                with mock.patch.object(speech.paths, "user_data_dir",
                                       side_effect=OSError("只读")):
                    eng.set_grammar(["李四"])
                self.assertEqual(eng._hotwords_file, "")
                self.assertTrue(eng._hotwords_dirty)


class TestSherpaDecodingMethod(unittest.TestCase):
    """热词必须配 modified_beam_search：sherpa_onnx 对 greedy + 热词直接抛错。"""

    def _engine_and_calls(self):
        calls = []

        def fake_from_transducer(**kwargs):
            calls.append(kwargs.get("decoding_method"))
            return mock.Mock()

        # _create_recognizer 内部 import sherpa_onnx 后调用 from_transducer
        self.enterContext(mock.patch(
            "sherpa_onnx.OnlineRecognizer.from_transducer",
            side_effect=fake_from_transducer))
        self.enterContext(mock.patch.object(
            speech, "sherpa_model_dir", return_value="/tmp/model"))
        eng = speech.SherpaEngine({}, 16000)
        return eng, calls

    def test_no_hotwords_uses_greedy(self):
        eng, calls = self._engine_and_calls()
        self.assertEqual(calls, ["greedy_search"])
        eng.begin()
        self.assertEqual(calls, ["greedy_search"])

    def test_hotwords_switches_to_beam_search(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(speech.paths, "user_data_dir", return_value=d):
                eng, calls = self._engine_and_calls()
                eng.set_grammar(["张三"])
                eng.begin()          # 重建识别器
                self.assertEqual(calls, ["greedy_search", "modified_beam_search"])
                eng.set_grammar(None)
                eng.begin()          # 清空热词后回到 greedy
                self.assertEqual(calls, ["greedy_search", "modified_beam_search",
                                         "greedy_search"])


class TestMicMeasurement(unittest.TestCase):
    """measure_mic_rms / device_has_sound 的设备音量测量逻辑。"""

    def _patch_sd(self, input_cb=None):
        """替身 sounddevice：query_devices 与 InputStream 都由假实现接管。"""
        calls = {}

        def fake_query(idx):
            calls["query_idx"] = idx
            return {"default_samplerate": 16000}

        class FakeStream:
            def __init__(self, callback, **kw):
                self._cb = callback
                self._kw = kw
                calls["stream_kw"] = kw

            def __enter__(self):
                if input_cb is not None:
                    input_cb(self._cb)
                return self

            def __exit__(self, *exc):
                return False

        patchers = [
            mock.patch("sounddevice.query_devices", side_effect=fake_query),
            mock.patch("sounddevice.InputStream", FakeStream),
            mock.patch("time.sleep", return_value=None),
        ]
        for p in patchers:
            self.enterContext(p)
        return calls

    def test_no_frames_returns_negative(self):
        """InputStream 没回传数据时视为测量失败，返回 -1。"""
        self._patch_sd(input_cb=None)
        self.assertEqual(speech.measure_mic_rms(0), -1.0)

    def test_computes_rms_from_frames(self):
        """回调塞进固定幅度音频，RMS 应按公式算出。

        InputStream 默认回调数据类型是 float32（0~1 量纲），与真实录音一致。
        """
        import numpy as np

        def feed(cb):
            cb(np.full((1600, 1), 0.2, dtype=np.float32), None, None, None)

        self._patch_sd(input_cb=feed)
        rms = speech.measure_mic_rms(0)
        self.assertAlmostEqual(rms, 0.2, places=4)

    def test_device_has_sound_above_threshold(self):
        """RMS 达到底噪/人声水平（≥0.0005）才算有声设备。"""
        with mock.patch.object(speech, "measure_mic_rms", return_value=0.02):
            self.assertTrue(speech.device_has_sound(0))

    def test_device_has_sound_below_threshold(self):
        """电噪声级别（0.0000x）不算有声，避免选中"假连接"的蓝牙耳麦。"""
        with mock.patch.object(speech, "measure_mic_rms", return_value=2e-5):
            self.assertFalse(speech.device_has_sound(0))

    def test_measure_failure_counts_as_no_sound(self):
        """设备无法打开（返回 -1）时不能算有声。"""
        with mock.patch.object(speech, "measure_mic_rms", return_value=-1.0):
            self.assertFalse(speech.device_has_sound(0))
