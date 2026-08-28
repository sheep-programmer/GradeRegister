"""采样率协商：Windows 上很多设备不支持 16kHz，硬开就是一路静音。

运行：python -m unittest tests.test_samplerate -v
"""
from __future__ import annotations

import unittest

import numpy as np

from grade_app.recorder import pick_samplerate, resample_to


class FakeSD:
    """假 sounddevice：只接受 supported 里列出的采样率。"""

    class PortAudioError(Exception):
        pass

    def __init__(self, supported, native=48000.0):
        self.supported = set(supported)
        self.native = native
        self.checked = []

    def check_input_settings(self, device=None, samplerate=None,
                             channels=1, dtype="int16"):
        self.checked.append(samplerate)
        if samplerate not in self.supported:
            raise self.PortAudioError(f"{samplerate} not supported")

    def query_devices(self, idx=None):
        return {"default_samplerate": self.native, "max_input_channels": 1}

    class default:
        device = (0, 1)


class TestPickSamplerate(unittest.TestCase):
    def test_prefers_16k_when_supported(self):
        sd = FakeSD({16000, 48000})
        self.assertEqual(pick_samplerate(sd, None, 16000), 16000)

    def test_falls_back_to_device_native(self):
        """16k 开不了就用设备原生的，而不是硬开一个静音流。"""
        sd = FakeSD({48000}, native=48000.0)
        self.assertEqual(pick_samplerate(sd, None, 16000), 48000)

    def test_falls_back_to_common_rates(self):
        sd = FakeSD({44100}, native=0)
        self.assertEqual(pick_samplerate(sd, None, 16000), 44100)

    def test_tries_16k_first(self):
        sd = FakeSD({16000, 48000})
        pick_samplerate(sd, None, 16000)
        self.assertEqual(sd.checked[0], 16000)

    def test_returns_wanted_when_nothing_works(self):
        """全都开不了时返回原值，让上层照常报错，不要静默吞掉。"""
        sd = FakeSD(set(), native=0)
        self.assertEqual(pick_samplerate(sd, None, 16000), 16000)

    def test_survives_query_failure(self):
        class Broken(FakeSD):
            def query_devices(self, idx=None):
                raise RuntimeError("no device")
        self.assertEqual(pick_samplerate(Broken({16000}), None, 16000), 16000)


class TestResample(unittest.TestCase):
    def test_same_rate_is_passthrough(self):
        a = np.arange(100, dtype=np.float32)
        self.assertIs(resample_to(a, 16000, 16000), a)

    def test_downsample_length(self):
        a = np.zeros(48000, dtype=np.float32)
        self.assertAlmostEqual(len(resample_to(a, 48000, 16000)), 16000,
                               delta=2)

    def test_odd_ratio_length(self):
        a = np.zeros(44100, dtype=np.float32)
        self.assertAlmostEqual(len(resample_to(a, 44100, 16000)), 16000,
                               delta=50)

    def test_preserves_tone(self):
        """440Hz 正弦降采样后还得是 440Hz，别把语音降成噪声。"""
        sr, dst = 48000, 16000
        t = np.arange(sr, dtype=np.float32) / sr
        a = np.sin(2 * np.pi * 440 * t).astype(np.float32)
        out = resample_to(a, sr, dst)
        freqs = np.fft.rfftfreq(len(out), 1 / dst)
        peak = freqs[int(np.argmax(np.abs(np.fft.rfft(out))))]
        self.assertAlmostEqual(peak, 440, delta=15)

    def test_keeps_amplitude(self):
        a = (np.sin(np.linspace(0, 200, 48000)) * 0.5).astype(np.float32)
        out = resample_to(a, 48000, 16000)
        self.assertAlmostEqual(float(np.abs(out).max()), 0.5, delta=0.1)

    def test_empty_input(self):
        self.assertEqual(len(resample_to(np.zeros(0, dtype=np.float32),
                                         48000, 16000)), 0)


if __name__ == "__main__":
    unittest.main()
