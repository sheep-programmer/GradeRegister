"""录音与断句：把麦克风音频喂给语音引擎，按事件回调向界面汇报。

不依赖 tkinter，可用假引擎单独测试。事件通过 emit(kind, payload) 回调发出：

    partial      流式识别的中间文本
    final        一句话的最终文本
    final_empty  录完没识别出内容，payload 为本次最大音量
    level        当前音量（0~1）
    thinking     正在识别上一句
    silent_warn  长时间没听到声音
    error        录音或识别出错
"""
from __future__ import annotations

import threading
import time
from typing import Callable, List, Optional

import numpy as np

Emit = Callable[[str, object], None]

# 判定「听到人声」的音量阈值
SPEECH_LEVEL = 0.0045
# 判定「这次完全没录到声音」的音量阈值
SILENT_LEVEL = 0.01
# 多久没听到声音就提醒一次（秒）；只提醒，绝不自动切断录音
SILENT_WARN_AFTER = 4.0


def block_rms(block: np.ndarray) -> float:
    """一块归一化音频（0~1 量纲）的 RMS 音量；空块返回 0。"""
    return float(np.sqrt(np.mean(np.square(block)))) if block.size else 0.0


class AutoGain:
    """软件自动增益。所有音量一律用 0~1 归一化量纲。

    裸录音没有系统级自动增益，笔记本内置麦音量偏小。开场测一下底噪定基准
    增益：环境越安静给得越多，上限受削顶风险约束。之后只做平滑微调——
    接近削顶就快速降，持续偏轻就缓慢升，说话过程中增益保持稳定不丢字。
    """

    # 开场校准所需的音频块数，约 0.6 秒
    CALIBRATION_BLOCKS = 3
    MIN_GAIN = 1.0
    MAX_GAIN = 8.0
    BASE_GAIN = 3.0
    # 校准基准底噪：比这更安静的环境按比例上调增益，更吵则维持基准
    BASE_NOISE = 0.004
    # 校准阶段听到明显声音就立即定标，不等满三块，避免漏掉第一句
    SPEECH_PEAK = 0.05
    # 放大后峰值超过此值视为接近削顶
    CLIP_PEAK = 0.75
    # 放大后仍然偏轻、且原始信号确实很小时，缓慢提升
    QUIET_PEAK = 0.08
    QUIET_RAW = 0.02

    def __init__(self) -> None:
        self.gain = self.BASE_GAIN
        self.calibrated = False
        self._noise: List[float] = []

    def update(self, peak: float) -> float:
        """根据当前块的峰值（0~1）调整增益并返回新增益。"""
        if not self.calibrated:
            self._noise.append(peak)
            if len(self._noise) >= self.CALIBRATION_BLOCKS or peak > self.SPEECH_PEAK:
                noise = max(max(self._noise), 1e-6)
                scaled = self.BASE_GAIN * (self.BASE_NOISE / noise)
                self.gain = min(self.MAX_GAIN, max(self.BASE_GAIN, scaled))
                self.calibrated = True
            return self.gain
        amplified = peak * self.gain
        if amplified > self.CLIP_PEAK:
            self.gain = max(self.MIN_GAIN, self.gain * 0.88)
        elif amplified < self.QUIET_PEAK and peak < self.QUIET_RAW:
            self.gain = min(self.MAX_GAIN, self.gain * 1.01)
        return self.gain

    def apply(self, block: np.ndarray) -> np.ndarray:
        """放大一块 0~1 归一化音频，返回可直接喂给识别引擎的 int16 数据。"""
        peak = float(np.max(np.abs(block))) if block.size else 0.0
        self.update(peak)
        return np.clip(block * self.gain * 32768.0,
                       -32768, 32767).astype(np.int16)


class SegmentTimer:
    """没有原生端点检测的引擎用的切句判定。

    边说边出字的引擎：文字还在变说明还在念，哪怕中间换气也不切；
    文字停住加上短暂静音才算这句说完。
    整句解码的引擎录音期间不出任何文字，只能听静音，所以阈值要短一些，
    否则老师说完得干等着。
    """

    # 识别文本停止变化多久算说完（秒）
    STABLE_GAP = 0.8
    # 完全没有声音多久强制切句（秒）——与 gap 无关的兜底
    SILENCE_GAP = 3.0
    # 整句解码的引擎：静音多久算说完（秒）
    QUIET_GAP = 0.7
    # 可配置断句间隔的合理区间：再短会把一句话切碎，再长等着难受
    MIN_GAP = 0.3
    MAX_GAP = 3.0

    def __init__(self, now: Optional[float] = None,
                 streaming: bool = True, gap=None) -> None:
        t = time.time() if now is None else now
        self.streaming = streaming
        self.gap = self._resolve_gap(gap, streaming)
        self.last_partial_text = ""
        self.last_change = t
        self.last_sound = t
        self.heard_speech = False

    @classmethod
    def _resolve_gap(cls, gap, streaming: bool) -> float:
        """把配置值夹到合理区间；缺失或不是数字就用内置默认。"""
        default = cls.STABLE_GAP if streaming else cls.QUIET_GAP
        try:
            value = float(gap)
        except (TypeError, ValueError):
            return default
        if value != value:        # NaN
            return default
        return min(cls.MAX_GAP, max(cls.MIN_GAP, value))

    def note_partial(self, text: str, now: Optional[float] = None) -> None:
        if text and text != self.last_partial_text:
            self.last_partial_text = text
            self.last_change = time.time() if now is None else now

    def note_level(self, level: float, now: Optional[float] = None) -> None:
        if level >= SPEECH_LEVEL:
            self.last_sound = time.time() if now is None else now
            self.heard_speech = True

    def should_cut(self, now: Optional[float] = None) -> bool:
        if not self.heard_speech:
            return False
        t = time.time() if now is None else now
        if not self.streaming:
            return t - self.last_sound > self.gap
        return (t - self.last_change > self.gap
                or t - self.last_sound > self.SILENCE_GAP)

    def reset(self, now: Optional[float] = None) -> None:
        self.heard_speech = False
        self.last_partial_text = ""
        self.last_change = time.time() if now is None else now

    def silent_for(self, now: Optional[float] = None) -> float:
        return (time.time() if now is None else now) - self.last_sound


class Recorder:
    """管理录音线程：一次录一段，或连续听写。

    engine 需实现 begin/feed/finalize；实现了 check_endpoint 的引擎
    （sherpa）走原生端点检测，断句时音频流不中断，衔接处不丢字。
    """

    def __init__(self, cfg: dict, emit: Emit) -> None:
        self.cfg = cfg
        self.emit = emit
        self.engine = None
        self._recording = False
        self._thread: Optional[threading.Thread] = None

    # ---------------- 状态 ----------------
    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def busy(self) -> bool:
        """上一次录音的线程是否还在收尾（识别未完成）。"""
        return self._thread is not None and self._thread.is_alive()

    # ---------------- 控制 ----------------
    def start(self, continuous: bool) -> bool:
        """启动录音线程；已在录音或上一次还没收尾时返回 False。

        引擎不是线程安全的，必须等上一个线程退出再开新的，否则并发调用
        原生库会崩溃。
        """
        if self._recording or self.busy or self.engine is None:
            return False
        self._recording = True
        self._thread = threading.Thread(
            target=self._loop, args=(continuous,), daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._recording = False

    # ---------------- 录音线程 ----------------
    def _loop(self, continuous: bool) -> None:
        import sounddevice as sd

        engine = self.engine
        engine.begin()
        frames: List[bytes] = []
        levels: List[float] = []
        raw_levels: List[float] = []
        gain = AutoGain()
        timer = SegmentTimer(streaming=getattr(engine, "streaming", True),
                             gap=self.cfg.get("segment_gap"))
        warned_silent = False
        max_peak = 0.0
        # sherpa 自带端点检测：断句时只重置解码状态，音频流继续，不丢字
        use_endpoint = continuous and hasattr(engine, "check_endpoint")

        def callback(indata, _frames, _time_info, _status):
            if not self._recording:
                raise sd.CallbackStop
            block = indata.astype(np.float32) / 32768.0   # 统一归一化到 0~1
            amplified = gain.apply(block)
            frames.append(amplified.tobytes())
            # 两种音量各有用途：放大后的给界面音量条（反映引擎实际听到的强度），
            # 未放大的给断句判定——SPEECH_LEVEL 是原始音量的阈值，拿放大 3~8 倍
            # 的值去比，室内底噪就会一直被当成人声，永远等不到停顿
            levels.append(block_rms(amplified.astype(np.float32) / 32768.0))
            raw_levels.append(block_rms(block))

        try:
            with sd.InputStream(
                    samplerate=int(self.cfg.get("sample_rate", 16000)),
                    channels=1, dtype="int16",
                    device=self.cfg.get("device"),
                    blocksize=3200, callback=callback):
                while self._recording:
                    time.sleep(0.05)
                    if frames:
                        chunk = b"".join(frames)
                        frames.clear()
                        try:
                            part = engine.feed(chunk)
                        except Exception:  # noqa: BLE001
                            part = ""
                        if part:
                            timer.note_partial(part)
                            self.emit("partial", part)
                        if levels:
                            peak = max(levels)
                            max_peak = max(max_peak, peak)
                            levels.clear()
                            self.emit("level", peak)
                        if raw_levels:
                            timer.note_level(max(raw_levels))
                            raw_levels.clear()

                    if use_endpoint and timer.heard_speech:
                        if self._cut_by_endpoint(engine, timer):
                            continue
                    elif continuous and timer.should_cut():
                        if not self._cut_by_timer(engine, timer):
                            return

                    if not warned_silent and timer.silent_for() > SILENT_WARN_AFTER:
                        warned_silent = True
                        self.emit("silent_warn", "")
        except Exception as e:  # noqa: BLE001
            self._recording = False
            self.emit("error", f"麦克风出错：{e}\n请在「设置」里换一个麦克风设备")
            print(f"[voice] 麦克风出错: {e}", flush=True)
            return

        print(f"[voice] 本次录音结束: 最大音量={max_peak:.4f}", flush=True)
        try:
            final = engine.finalize()
        except Exception as e:  # noqa: BLE001
            self.emit("error", f"识别失败：{e}")
            return
        if final:
            print(f"[voice] 识别结果: {final}", flush=True)
            self.emit("final", final)
        elif max_peak < SILENT_LEVEL:
            print(f"[voice] 识别为空 (峰值={max_peak:.4f})", flush=True)
            self.emit("final_empty", max_peak)

    def _cut_by_endpoint(self, engine, timer: SegmentTimer) -> bool:
        """引擎原生端点检测断句；返回是否切了一句。"""
        hit, text = engine.check_endpoint()
        if not hit:
            return False
        if text:
            print(f"[voice] 识别结果: {text}", flush=True)
            self.emit("final", text)
        timer.reset()
        self.emit("partial", " ")
        return True

    def _cut_by_timer(self, engine, timer: SegmentTimer) -> bool:
        """按停顿切句（无端点检测的引擎）；返回 False 表示出错需退出线程。"""
        self.emit("thinking", "")
        fallback = timer.last_partial_text
        try:
            text = engine.finalize()
            if not text and fallback:
                # 引擎给空白时用流式过程中已出的文字兜底，绝不丢句
                text = fallback
                print(f"[voice] 流式兜底: {text!r}", flush=True)
            engine.begin()
        except Exception as e:  # noqa: BLE001
            self.emit("error", f"识别失败：{e}")
            return False
        if text:
            print(f"[voice] 识别结果: {text}", flush=True)
            self.emit("final", text)
        else:
            print("[voice] 本句无内容", flush=True)
        timer.reset()
        return True
