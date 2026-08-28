"""人声判定与最长句兜底。

固定阈值在嘈杂环境下会把底噪当人声，说完话永远等不到停顿，
几句话被连成一整段——这是真机上「非得再出点动静才切换」的成因。
运行：python -m unittest tests.test_speech_gate -v
"""
from __future__ import annotations

import unittest

from grade_app.recorder import SPEECH_LEVEL, SegmentTimer, SpeechGate


class TestSpeechGate(unittest.TestCase):
    def test_quiet_room_uses_floor(self):
        gate = SpeechGate()
        for _ in range(30):
            gate.feed(0.0005)
        self.assertAlmostEqual(gate.threshold(), SPEECH_LEVEL, delta=1e-6)

    def test_noisy_room_raises_threshold(self):
        """底噪 0.01 的环境里，阈值必须抬上去，否则底噪就是人声。"""
        gate = SpeechGate()
        for _ in range(200):
            gate.feed(0.01)
        self.assertGreater(gate.threshold(), 0.01)

    def test_noise_is_not_speech_after_adapting(self):
        gate = SpeechGate()
        for _ in range(200):
            gate.feed(0.012)
        self.assertFalse(gate.feed(0.012))

    def test_speech_still_detected_in_noisy_room(self):
        gate = SpeechGate()
        for _ in range(200):
            gate.feed(0.012)
        self.assertTrue(gate.feed(0.08))

    def test_soft_speech_over_quiet_noise(self):
        """轻声说话也要认出来，不能因为自适应把门槛抬得太高。"""
        gate = SpeechGate()
        for _ in range(100):
            gate.feed(0.002)
        self.assertTrue(gate.feed(0.02))

    def test_speech_does_not_lift_the_floor(self):
        """一直说话时底噪估计不能被人声带上去，否则越说越听不见。"""
        gate = SpeechGate()
        for _ in range(50):
            gate.feed(0.001)
        quiet = gate.threshold()
        for _ in range(200):
            gate.feed(0.09)
        self.assertLess(gate.threshold(), quiet * 3)

    def test_follows_noise_down_quickly(self):
        """从吵的地方换到安静处，门槛要跟着降回来。"""
        gate = SpeechGate()
        for _ in range(200):
            gate.feed(0.02)
        for _ in range(60):
            gate.feed(0.0005)
        self.assertAlmostEqual(gate.threshold(), SPEECH_LEVEL, delta=1e-3)


class TestMaxSegment(unittest.TestCase):
    """兜底：无论判定怎么出错，一句话不能无限长下去。"""

    def test_forces_a_cut_when_sound_never_stops(self):
        timer = SegmentTimer(now=0.0, streaming=False)
        t = 0.0
        while t < SegmentTimer.MAX_SEGMENT + 1.0:
            timer.note_level(0.5, now=t)     # 一直有声，永远等不到停顿
            t += 0.2
        self.assertTrue(timer.should_cut(now=t),
                        "连续有声超过最长句长，必须强制断句")

    def test_normal_sentence_not_cut_early(self):
        timer = SegmentTimer(now=0.0, streaming=False)
        t = 0.0
        while t < 4.0:
            timer.note_level(0.5, now=t)
            t += 0.2
        self.assertFalse(timer.should_cut(now=t),
                         "正常长度的句子不该被拦腰截断")

    def test_reset_clears_segment_start(self):
        timer = SegmentTimer(now=0.0, streaming=False)
        t = 0.0
        while t < SegmentTimer.MAX_SEGMENT + 1.0:
            timer.note_level(0.5, now=t)
            t += 0.2
        timer.reset(now=t)
        timer.note_level(0.5, now=t)
        self.assertFalse(timer.should_cut(now=t + 0.1))


if __name__ == "__main__":
    unittest.main()
