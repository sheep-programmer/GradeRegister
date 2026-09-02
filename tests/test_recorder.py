"""录音逻辑测试：自动增益与断句判定。运行：python -m unittest tests.test_recorder -v"""
from __future__ import annotations

import sys
import threading
import time
import types
import unittest

import numpy as np

from grade_app.recorder import (SPEECH_LEVEL, AutoGain, Recorder, SegmentTimer,
                                block_rms, remove_dc)


class TestAutoGain(unittest.TestCase):
    def test_quiet_room_gets_more_gain(self):
        gain = AutoGain()
        for _ in range(AutoGain.CALIBRATION_BLOCKS):
            gain.update(AutoGain.BASE_NOISE / 4)
        self.assertTrue(gain.calibrated)
        self.assertGreater(gain.gain, AutoGain.BASE_GAIN)
        self.assertLessEqual(gain.gain, AutoGain.MAX_GAIN)

    def test_noisy_room_stays_at_base_gain(self):
        gain = AutoGain()
        for _ in range(AutoGain.CALIBRATION_BLOCKS):
            gain.update(AutoGain.BASE_NOISE * 10)
        self.assertEqual(gain.gain, AutoGain.BASE_GAIN)

    def test_loud_input_calibrates_immediately(self):
        """一开口就说话时不等满三块，立刻定标，避免漏掉第一句。"""
        gain = AutoGain()
        gain.update(0.2)
        self.assertTrue(gain.calibrated)

    def test_gain_always_within_bounds(self):
        for peak in (0.0, 1e-9, 0.001, 0.5, 1.0):
            gain = AutoGain()
            for _ in range(AutoGain.CALIBRATION_BLOCKS):
                gain.update(peak)
            self.assertGreaterEqual(gain.gain, AutoGain.MIN_GAIN, peak)
            self.assertLessEqual(gain.gain, AutoGain.MAX_GAIN, peak)

    def test_backs_off_when_clipping(self):
        gain = AutoGain()
        gain.calibrated = True
        gain.gain = 8.0
        gain.update(1.0)              # 放大后远超削顶阈值
        self.assertLess(gain.gain, 8.0)

    def test_creeps_up_when_persistently_quiet(self):
        gain = AutoGain()
        gain.calibrated = True
        gain.gain = 3.0
        gain.update(0.001)
        self.assertGreater(gain.gain, 3.0)

    def test_speech_level_does_not_move_gain(self):
        """正常人声不该触发增减，否则说话过程中增益抖动会丢字。"""
        gain = AutoGain()
        gain.calibrated = True
        gain.gain = 3.0
        gain.update(0.1)
        self.assertEqual(gain.gain, 3.0)

    def test_apply_returns_int16_without_overflow(self):
        gain = AutoGain()
        gain.calibrated = True
        gain.gain = AutoGain.MAX_GAIN
        out = gain.apply(np.array([[0.9], [-0.9]], dtype=np.float32))
        self.assertEqual(out.dtype, np.int16)
        self.assertTrue(np.all(np.abs(out) <= 32767))

    def test_apply_scales_normalized_input(self):
        gain = AutoGain()
        gain.calibrated = True
        gain.gain = 2.0
        out = gain.apply(np.array([[0.1]], dtype=np.float32))
        self.assertAlmostEqual(int(out[0][0]), int(0.1 * 2.0 * 32768), delta=1)


class TestSegmentTimer(unittest.TestCase):
    def test_no_cut_before_any_speech(self):
        timer = SegmentTimer(now=0.0)
        self.assertFalse(timer.should_cut(now=100.0))

    def test_cuts_when_text_stops_changing(self):
        timer = SegmentTimer(now=0.0)
        timer.note_level(0.5, now=0.0)
        timer.note_partial("张三", now=0.0)
        self.assertFalse(timer.should_cut(now=0.5))
        self.assertTrue(timer.should_cut(now=1.0))

    def test_still_talking_keeps_segment_open(self):
        """中间换气但文字还在变，不能切句。"""
        timer = SegmentTimer(now=0.0)
        timer.note_level(0.5, now=0.0)
        timer.note_partial("第一题", now=0.0)
        timer.note_partial("第一题十八", now=0.7)
        self.assertFalse(timer.should_cut(now=1.0))

    def test_cuts_on_long_silence_even_if_text_unchanged(self):
        timer = SegmentTimer(now=0.0)
        timer.note_level(0.5, now=0.0)
        timer.note_partial("张三", now=0.0)
        self.assertTrue(timer.should_cut(now=4.0))

    def test_quiet_input_is_not_speech(self):
        timer = SegmentTimer(now=0.0)
        timer.note_level(0.0001, now=0.0)
        self.assertFalse(timer.heard_speech)

    def test_reset_clears_state(self):
        timer = SegmentTimer(now=0.0)
        timer.note_level(0.5, now=0.0)
        timer.note_partial("张三", now=0.0)
        timer.reset(now=5.0)
        self.assertFalse(timer.heard_speech)
        self.assertEqual(timer.last_partial_text, "")
        self.assertFalse(timer.should_cut(now=9.0))

    def test_offline_engine_cuts_on_short_silence(self):
        """整句解码的引擎没有中间文字，只能听静音，且不能等满 3 秒。"""
        timer = SegmentTimer(now=0.0, streaming=False)
        timer.note_level(0.5, now=0.0)
        self.assertFalse(timer.should_cut(now=0.5))
        self.assertTrue(timer.should_cut(now=0.9))

    def test_offline_engine_keeps_listening_while_talking(self):
        """一直有声音就不切，长句不会被拦腰截断。"""
        timer = SegmentTimer(now=0.0, streaming=False)
        for t in (0.0, 0.4, 0.8, 1.2, 1.6):
            timer.note_level(0.5, now=t)
            self.assertFalse(timer.should_cut(now=t))
        self.assertTrue(timer.should_cut(now=2.4))

    def test_streaming_engine_would_cut_too_early_without_text(self):
        """同样的静音时长，流式判定靠文字稳定，两条路径互不干扰。"""
        offline = SegmentTimer(now=0.0, streaming=False)
        streaming = SegmentTimer(now=0.0, streaming=True)
        for timer in (offline, streaming):
            timer.note_level(0.5, now=0.0)
        self.assertTrue(offline.should_cut(now=0.9))
        self.assertTrue(streaming.should_cut(now=0.9))   # 文字一直没变
        streaming.note_partial("第一题", now=0.85)
        self.assertFalse(streaming.should_cut(now=1.0))


class TestSegmentGapConfig(unittest.TestCase):
    """config 里的 segment_gap 要真的决定断句快慢。"""

    def test_default_keeps_builtin_gaps(self):
        self.assertEqual(SegmentTimer(streaming=False).gap,
                         SegmentTimer.QUIET_GAP)
        self.assertEqual(SegmentTimer(streaming=True).gap,
                         SegmentTimer.STABLE_GAP)

    def test_configured_gap_is_used_offline(self):
        timer = SegmentTimer(now=0.0, streaming=False, gap=0.4)
        timer.note_level(0.5, now=0.0)
        self.assertFalse(timer.should_cut(now=0.3))
        self.assertTrue(timer.should_cut(now=0.5))

    def test_configured_gap_is_used_streaming(self):
        timer = SegmentTimer(now=0.0, streaming=True, gap=0.4)
        timer.note_level(0.5, now=0.0)
        timer.note_partial("张三", now=0.0)
        self.assertFalse(timer.should_cut(now=0.3))
        self.assertTrue(timer.should_cut(now=0.5))

    def test_longer_gap_waits_longer(self):
        timer = SegmentTimer(now=0.0, streaming=False, gap=2.0)
        timer.note_level(0.5, now=0.0)
        self.assertFalse(timer.should_cut(now=1.5))
        self.assertTrue(timer.should_cut(now=2.5))

    def test_gap_clamped_to_sane_range(self):
        """0.05 秒会把一句话切碎，10 秒等得难受，都要夹到合理区间。"""
        self.assertEqual(SegmentTimer(streaming=False, gap=0.05).gap,
                         SegmentTimer.MIN_GAP)
        self.assertEqual(SegmentTimer(streaming=False, gap=10).gap,
                         SegmentTimer.MAX_GAP)

    def test_invalid_gap_falls_back_to_default(self):
        for bad in ("", "abc", None, [], float("nan")):
            self.assertEqual(SegmentTimer(streaming=False, gap=bad).gap,
                             SegmentTimer.QUIET_GAP, repr(bad))

    def test_silence_gap_still_forces_a_cut_when_nothing_heard(self):
        """流式下文字一直不变又完全没声音时，兜底强制切句不受 gap 影响。"""
        timer = SegmentTimer(now=0.0, streaming=True, gap=2.0)
        timer.note_level(0.5, now=0.0)
        timer.note_partial("张三", now=1.9)
        self.assertTrue(timer.should_cut(now=SegmentTimer.SILENCE_GAP + 0.1))

    def test_config_default_matches_builtin_offline_gap(self):
        """config 默认值要和内置行为一致，修好配置不该顺带改变默认体验。"""
        from grade_app.config import DEFAULT_CONFIG
        self.assertEqual(DEFAULT_CONFIG["segment_gap"], SegmentTimer.QUIET_GAP)


class FakeEngine:
    """满足 begin/feed/finalize 协议的假引擎（不支持端点检测）。"""

    def __init__(self, final=""):
        self.final = final
        self.begun = 0

    def begin(self):
        self.begun += 1

    def feed(self, pcm):
        return ""

    def finalize(self):
        return self.final


class FakeEndpointEngine(FakeEngine):
    """额外实现 check_endpoint 的引擎（对应 sherpa）。"""

    def __init__(self, endpoint=(False, ""), final=""):
        super().__init__(final=final)
        self.endpoint = endpoint      # (是否断句, 该句文本)

    def check_endpoint(self):
        return self.endpoint


class TestRecorderSegmentation(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.rec = Recorder({}, lambda kind, payload: self.events.append((kind, payload)))

    def test_endpoint_cut_emits_final(self):
        engine = FakeEndpointEngine(endpoint=(True, "张三"))
        timer = SegmentTimer(now=0.0)
        self.assertTrue(self.rec._cut_by_endpoint(engine, timer))
        self.assertIn(("final", "张三"), self.events)
        self.assertFalse(timer.heard_speech)

    def test_endpoint_not_hit_does_nothing(self):
        engine = FakeEndpointEngine(endpoint=(False, ""))
        timer = SegmentTimer(now=0.0)
        timer.heard_speech = True
        self.assertFalse(self.rec._cut_by_endpoint(engine, timer))
        self.assertEqual(self.events, [])
        self.assertTrue(timer.heard_speech)

    def test_timer_cut_restarts_engine(self):
        engine = FakeEngine(final="第一题十八分")
        timer = SegmentTimer(now=0.0)
        self.assertTrue(self.rec._cut_by_timer(engine, timer))
        self.assertIn(("final", "第一题十八分"), self.events)
        self.assertEqual(engine.begun, 1)      # 重置识别器继续听下一句

    def test_timer_cut_falls_back_to_partial_text(self):
        """引擎最终结果为空时，用流式过程中已出的文字兜底，不丢整句。"""
        engine = FakeEngine(final="")
        timer = SegmentTimer(now=0.0)
        timer.note_partial("李四", now=0.0)
        self.rec._cut_by_timer(engine, timer)
        self.assertIn(("final", "李四"), self.events)

    def test_timer_cut_reports_engine_failure(self):
        class Broken(FakeEngine):
            def finalize(self):
                raise RuntimeError("模型炸了")

        timer = SegmentTimer(now=0.0)
        self.assertFalse(self.rec._cut_by_timer(Broken(), timer))
        self.assertEqual(self.events[-1][0], "error")


def noise_block(rms: float, size: int = 3200, seed: int = 0) -> np.ndarray:
    """一块归一化的高斯噪声/人声替身，指定 RMS 音量。"""
    rng = np.random.default_rng(seed)
    return np.clip(rng.normal(0, rms, size), -1.0, 1.0
                   ).astype(np.float32).reshape(-1, 1)


class TestGainLevelDimension(unittest.TestCase):
    """增益与断句阈值的量纲：SPEECH_LEVEL 是原始音量的阈值。

    放大后的音量只能用来画音量条，拿它判断「还在说话」会把底噪当人声。
    """

    def test_amplified_room_noise_passes_speech_level(self):
        gain = AutoGain()
        noise = noise_block(0.002)
        for _ in range(AutoGain.CALIBRATION_BLOCKS):
            gain.apply(noise)
        amplified = gain.apply(noise).astype(np.float32) / 32768.0
        self.assertLess(block_rms(noise), SPEECH_LEVEL)
        self.assertGreater(block_rms(amplified), SPEECH_LEVEL)

    def test_raw_speech_still_passes_speech_level(self):
        self.assertGreater(block_rms(noise_block(0.02)), SPEECH_LEVEL)

    def test_block_rms_handles_empty(self):
        self.assertEqual(block_rms(np.zeros((0, 1), dtype=np.float32)), 0.0)


class FakeInputStream:
    """假麦克风：先送 1 秒人声，之后一直送室内底噪。

    底噪音量取 0.002 —— 经 AutoGain 放大后会超过 SPEECH_LEVEL，
    正是「说完话却永远不自动断句」的现场条件。
    """

    SPEECH_SECONDS = 1.0

    def __init__(self, samplerate=16000, channels=1, dtype="int16",
                 device=None, blocksize=3200, callback=None):
        self.samplerate = samplerate
        self.blocksize = blocksize
        self._callback = callback
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        self._stop.set()
        self._thread.join(timeout=2.0)
        return False

    def _run(self):
        started = time.time()
        while not self._stop.is_set():
            speaking = time.time() - started < self.SPEECH_SECONDS
            block = noise_block(0.05 if speaking else 0.002, self.blocksize,
                                seed=int((time.time() - started) * 100))
            try:
                self._callback((block * 32768.0).astype(np.int16),
                               self.blocksize, None, None)
            except Exception:      # noqa: BLE001  CallbackStop
                return
            time.sleep(self.blocksize / self.samplerate)


class FakeSounddevice(types.ModuleType):
    CallbackStop = type("CallbackStop", (Exception,), {})
    InputStream = FakeInputStream

    def __init__(self):
        super().__init__("sounddevice")


class TestContinuousAutoCut(unittest.TestCase):
    """连续听写必须在说完一句停顿后自动出结果，不能非等手动停止。"""

    def setUp(self):
        self._real_sd = sys.modules.get("sounddevice")
        sys.modules["sounddevice"] = FakeSounddevice()

    def tearDown(self):
        if self._real_sd is None:
            sys.modules.pop("sounddevice", None)
        else:
            sys.modules["sounddevice"] = self._real_sd

    def test_long_segment_gap_delays_the_cut(self):
        """把 segment_gap 调大，说完话后要多等一会才断句——证明配置真被读到。"""
        got_final = threading.Event()

        class OfflineEngine(FakeEngine):
            streaming = False

        rec = Recorder({"sample_rate": 16000, "segment_gap": 2.5},
                       lambda kind, _p: kind == "final" and got_final.set())
        rec.engine = OfflineEngine(final="第一题十分")
        self.assertTrue(rec.start(continuous=True))
        try:
            # 说话 1 秒后停，gap=2.5 -> 到 2.2 秒时还不该出结果
            early = got_final.wait(timeout=2.2)
            late = got_final.wait(timeout=3.0)
        finally:
            rec.stop()
            if rec._thread:
                rec._thread.join(timeout=3.0)
        self.assertFalse(early, "segment_gap=2.5 却提前断句了，配置没生效")
        self.assertTrue(late, "等够 segment_gap 之后仍未断句")

    def test_offline_engine_cuts_without_manual_stop(self):
        """必须在 stop() 之前就收到 final —— stop() 之后的 final 是手动停止的结果。"""
        finals = []
        got_final = threading.Event()

        def emit(kind, payload):
            if kind == "final":
                finals.append(payload)
                got_final.set()

        class OfflineEngine(FakeEngine):
            streaming = False

        rec = Recorder({"sample_rate": 16000}, emit)
        rec.engine = OfflineEngine(final="第一题十分")
        self.assertTrue(rec.start(continuous=True))
        try:
            cut_while_recording = got_final.wait(timeout=5.0)
        finally:
            rec.stop()
            if rec._thread:
                rec._thread.join(timeout=3.0)
        self.assertTrue(
            cut_while_recording,
            "说完一句停顿 4 秒后仍未自动断句，只能靠手动停止才出结果")
        self.assertEqual(finals[0], "第一题十分")


class TestRemoveDc(unittest.TestCase):
    """录音块去直流：偏置声卡录出来的波形要回到零基线再喂给引擎。"""

    def test_constant_offset_removed(self):
        x = np.full((800, 1), 0.3, dtype=np.float32)
        out = remove_dc(x)
        self.assertAlmostEqual(float(out.mean()), 0.0, places=6)

    def test_sine_keeps_waveform(self):
        """去直流只消偏置，人声波形本身不能一起被抹掉。"""
        t = np.linspace(0, 20, 3200)
        x = (np.sin(t) + 0.5).astype(np.float32).reshape(-1, 1)
        out = remove_dc(x)
        self.assertAlmostEqual(float(out.mean()), 0.0, places=6)
        self.assertGreater(float(np.abs(out).max()), 0.5)

    def test_empty_block_passthrough(self):
        empty = np.zeros((0, 1), dtype=np.float32)
        self.assertIs(remove_dc(empty), empty)

    def test_shape_preserved(self):
        x = np.random.default_rng(1).normal(0, 0.1, (3200, 1)).astype(np.float32)
        self.assertEqual(remove_dc(x).shape, (3200, 1))


class TestRecorderGuards(unittest.TestCase):
    def test_start_refused_without_engine(self):
        rec = Recorder({}, lambda *a: None)
        self.assertFalse(rec.start(continuous=True))
        self.assertFalse(rec.recording)

    def test_start_refused_while_recording(self):
        rec = Recorder({}, lambda *a: None)
        rec.engine = FakeEngine()
        rec._recording = True
        self.assertFalse(rec.start(continuous=True))


if __name__ == "__main__":
    unittest.main()


class TestClipGuard(unittest.TestCase):
    """放大不能把波形削平：削顶是硬失真，识别率掉得比音量不足更狠。"""

    def test_loud_block_is_not_clipped(self):
        gain = AutoGain()
        gain.calibrated = True
        gain.gain = AutoGain.MAX_GAIN
        block = (np.sin(np.linspace(0, 20, 3200)) * 0.63).astype(
            np.float32).reshape(-1, 1)
        out = gain.apply(block).astype(np.float32) / 32768.0
        self.assertEqual(int((np.abs(out) >= 0.999).sum()), 0)

    def test_quiet_block_still_amplified(self):
        """防削顶不能顺手把正常放大也压掉。"""
        gain = AutoGain()
        gain.calibrated = True
        gain.gain = 4.0
        block = np.full((3200, 1), 0.02, dtype=np.float32)
        out = gain.apply(block).astype(np.float32) / 32768.0
        self.assertAlmostEqual(float(np.max(out)), 0.08, delta=0.005)

    def test_silence_does_not_divide_by_zero(self):
        gain = AutoGain()
        gain.calibrated = True
        out = gain.apply(np.zeros((3200, 1), dtype=np.float32))
        self.assertEqual(int(np.abs(out).max()), 0)


class FakeInputStream48k(FakeInputStream):
    """只在 48kHz 下工作的设备——Windows 上最常见的情形。"""

    RATE = 48000

    def __init__(self, samplerate=48000, **kw):
        if samplerate != self.RATE:
            raise RuntimeError(f"{samplerate} not supported")
        super().__init__(samplerate=samplerate, **kw)


class FakeSounddevice48k(types.ModuleType):
    CallbackStop = type("CallbackStop", (Exception,), {})
    InputStream = FakeInputStream48k

    def __init__(self):
        super().__init__("sounddevice")

    class default:
        device = (0, 1)

    @staticmethod
    def check_input_settings(device=None, samplerate=None, channels=1,
                             dtype="int16"):
        if samplerate != FakeInputStream48k.RATE:
            raise RuntimeError("unsupported")

    @staticmethod
    def query_devices(idx=None):
        return {"default_samplerate": 48000.0, "max_input_channels": 1}


class TestRecordsOnDeviceThatRejects16k(unittest.TestCase):
    """设备只认 48k 时也要能录到、能断句——这是 Windows 上没声音的根因。"""

    def setUp(self):
        self._real = sys.modules.get("sounddevice")
        sys.modules["sounddevice"] = FakeSounddevice48k()

    def tearDown(self):
        if self._real is None:
            sys.modules.pop("sounddevice", None)
        else:
            sys.modules["sounddevice"] = self._real

    def test_still_hears_speech_and_cuts(self):
        got = threading.Event()
        heard = []

        class Offline(FakeEngine):
            streaming = False

            def feed(self, pcm):
                heard.append(len(pcm))
                return ""

        rec = Recorder({"sample_rate": 16000},
                       lambda k, _p: k == "final" and got.set())
        rec.engine = Offline(final="第一题十分")
        self.assertTrue(rec.start(continuous=True))
        try:
            cut = got.wait(timeout=5.0)
        finally:
            rec.stop()
            if rec._thread:
                rec._thread.join(timeout=3.0)
        self.assertTrue(cut, "设备只支持 48k 时录不到声音/不断句")
        self.assertTrue(heard, "引擎没有收到任何音频")

    def test_audio_is_downsampled_to_engine_rate(self):
        """喂给引擎的必须已经是 16k，否则识别出来是一堆乱码。"""
        sizes = []

        class Offline(FakeEngine):
            streaming = False

            def feed(self, pcm):
                sizes.append(len(pcm))
                return ""

        rec = Recorder({"sample_rate": 16000}, lambda *a: None)
        rec.engine = Offline()
        rec.start(continuous=False)
        time.sleep(1.2)
        rec.stop()
        if rec._thread:
            rec._thread.join(timeout=3.0)
        total_samples = sum(sizes) // 2          # int16
        # 1.2 秒左右的音频，按 16k 算约 19200 个采样点；
        # 若没降采样会是 48k 的三倍，差距远超容差
        self.assertLess(total_samples, 16000 * 2.2,
                        f"喂进去 {total_samples} 个采样点，像是没降采样")
