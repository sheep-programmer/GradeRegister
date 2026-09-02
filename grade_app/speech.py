"""语音识别引擎：vosk（免费离线小模型，默认）与 faster-whisper（可选，更准）。

两者均为本地免费模型，录音数据不出本机。
接口约定（供 GUI 的录音线程调用）：
    begin()         开始新一轮识别
    feed(pcm)       feed 一个 16k 单声道 int16 音频块，返回部分识别文本(可空)
    finalize()      结束识别，返回最终文本
"""
from __future__ import annotations

import io
import importlib.util
import json
import os
import re
import sys
import threading
import urllib.request
import wave
import zipfile
from typing import List, Optional

import numpy as np

from . import paths

# ---------------------------------------------------------------------------
# 模型下载
# ---------------------------------------------------------------------------

VOSK_URL = "https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip"
VOSK_DIRNAME = "vosk-model-small-cn-0.22"


def model_dir_path(cfg: dict) -> str:
    """模型根目录：打包版跟着程序走，源码运行在项目目录下。

    只用于展示与整体定位。具体某个引擎的目录要用 engine_model_dir()，
    它会逐个引擎判断该读随包的那份还是用户目录里可写的那份。
    """
    md = cfg.get("model_dir", "models")
    if os.path.isabs(md):
        return md
    bundled = os.path.join(paths.resource_dir(), md)
    if os.path.isdir(bundled) or not paths.is_frozen():
        return bundled
    return os.path.join(paths.user_data_dir(), md)


def download_root(cfg: dict) -> str:
    """下载来的模型存到哪——也是之后读它的目录。

    配置里指定了就用指定的。没指定时按运行方式选一个可写的地方：源码运行
    放项目的 models/，打包版放用户数据目录，因为程序自身所在的位置是只读的
    （macOS 的 .app 包内、Windows 的 Program Files）。
    """
    custom = str(cfg.get("download_dir") or "").strip()
    if custom:
        return os.path.abspath(os.path.expanduser(custom))
    md = cfg.get("model_dir", "models")
    if os.path.isabs(md):
        return md
    base = paths.user_data_dir() if paths.is_frozen() else paths.project_root()
    return os.path.join(base, md)


def engine_model_dir(cfg: dict, subdir: str) -> str:
    """某个引擎的模型目录，读与写都用这一个。

    必须逐个引擎判断，看整个 models 目录存不存在是不够的：打包版只带了
    默认引擎时那个目录是存在的，于是别的引擎也被指向只读的程序包内，
    老师在设置里换引擎、点下载就会失败。

    已经存在的那份优先，所以改了下载目录不会让先前下好的几百 MB 变成孤儿。
    """
    md = cfg.get("model_dir", "models")
    if os.path.isabs(md):
        return os.path.join(md, subdir)
    bundled = os.path.join(paths.resource_dir(), md, subdir)
    if os.path.isdir(bundled):
        return bundled              # 随程序分发的那份
    return os.path.join(download_root(cfg), subdir)


def is_bundled_model(cfg: dict, subdir: str) -> bool:
    """这个引擎的模型是随程序分发的（只读、不用下载）。"""
    md = cfg.get("model_dir", "models")
    if os.path.isabs(md):
        return False
    return os.path.isdir(os.path.join(paths.resource_dir(), md, subdir))


def vosk_model_path(cfg: dict) -> str:
    return engine_model_dir(cfg, cfg.get("vosk_model", VOSK_DIRNAME))


def download_vosk_model(cfg: dict, progress=None) -> str:
    """下载并解压 vosk 中文小模型（约 42MB），返回模型目录。"""
    dest_dir = model_dir_path(cfg)
    os.makedirs(dest_dir, exist_ok=True)
    model_path = vosk_model_path(cfg)

    zip_path = os.path.join(dest_dir, os.path.basename(VOSK_URL))
    if not os.path.isdir(model_path):
        # 校验已有压缩包：下载中断留下的空/损坏文件会跳过下载导致解压失败
        if os.path.exists(zip_path) and not zipfile.is_zipfile(zip_path):
            os.remove(zip_path)
        if not os.path.exists(zip_path):
            def report(blocknum: int, bs: int, size: int):
                if progress and size:
                    progress(min(blocknum * bs, size), size)
            print(f"[download] {VOSK_URL}")
            try:
                urllib.request.urlretrieve(VOSK_URL, zip_path, reporthook=report)
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(f"模型下载失败: {e}\n请手动下载 {VOSK_URL} 解压到 {model_path}") from e
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(dest_dir)
        except zipfile.BadZipFile as e:
            os.remove(zip_path)
            raise RuntimeError(
                f"模型文件损坏，已删除，请重新下载 {VOSK_URL} 解压到 {model_path}") from e
        print(f"[download] 模型已解压到 {model_path}")
    return model_path


# ---------------------------------------------------------------------------
# vosk 引擎
# ---------------------------------------------------------------------------

class VoskEngine:
    """vosk 离线小模型引擎（中文，~42MB，识别快，适合单次短句）。"""

    def __init__(self, model_path: str, sample_rate: int = 16000):
        from vosk import Model  # 延迟 import，缺依赖时给出清晰报错
        if not os.path.isdir(model_path):
            raise FileNotFoundError(
                f"未找到 vosk 模型: {model_path}\n请先运行: python download_model.py")
        self._model = Model(model_path)
        self._kaldi = None
        self.sample_rate = sample_rate
        self._grammar = None  # 识别词表（JSON 列表）：限定候选词可大幅提升人名准确率
        # vosk 识别器非线程安全：录音线程的 begin/feed/finalize 必须串行，
        # 否则快速连续录音（旧线程还在识别，新线程已 begin）会并发访问原生库导致段错误
        self._lock = threading.RLock()

    def set_grammar(self, words) -> None:
        """设置识别词表（只在这些词里挑，念人名不再瞎猜）；传 None/空 恢复通用听写。"""
        with self._lock:
            if words:
                self._grammar = json.dumps(list(words), ensure_ascii=False)
            else:
                self._grammar = None
            if self._kaldi is not None:
                self._apply_grammar()

    def _apply_grammar(self) -> None:
        with self._lock:
            if self._kaldi is None or not self._grammar:
                return
            try:
                self._kaldi.SetGrammar(self._grammar)
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] SetGrammar 失败（退回通用听写）: {exc}", flush=True)
                self._grammar = None

    def begin(self) -> None:
        from vosk import KaldiRecognizer
        with self._lock:
            self._kaldi = KaldiRecognizer(self._model, self.sample_rate)
            self._kaldi.SetWords(False)
            self._apply_grammar()

    def feed(self, pcm: bytes) -> str:
        with self._lock:
            if self._kaldi is None:
                return ""
            if self._kaldi.AcceptWaveform(pcm):
                self._kaldi.Result()  # 丢弃已消化结果，partial 会即时反映
            return self.partial()

    def partial(self) -> str:
        if self._kaldi is None:
            return ""
        try:
            with self._lock:
                return json.loads(self._kaldi.PartialResult()).get("partial", "")
        except Exception:  # noqa: BLE001
            return ""

    def finalize(self) -> str:
        if self._kaldi is None:
            return ""
        with self._lock:
            return json.loads(self._kaldi.FinalResult()).get("text", "")


# ---------------------------------------------------------------------------
# faster-whisper 引擎（可选）
# ---------------------------------------------------------------------------

class FasterWhisperEngine:
    """faster-whisper 引擎（模型较大，按需下载；识别更准确，支持长句）。"""

    streaming = False

    def __init__(self, model_name: str = "small", language: str = "zh"):
        from faster_whisper import WhisperModel  # 延迟 import
        # 模型缓存固定到项目 models/hf 下：位置可预测、便于清理与备份
        hf_home = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "models", "hf")
        os.environ.setdefault("HF_HOME", hf_home)
        if not os.path.isdir(hf_home) or not os.listdir(hf_home):
            print("[voice] 首次使用 faster-whisper：正在下载识别模型（约 460MB，"
                  "仅此一次，请保持联网耐心等待几分钟）…", flush=True)
        self._model = WhisperModel(model_name, device="cpu", compute_type="int8")
        print("[voice] faster-whisper 引擎就绪", flush=True)
        self.language = language
        self._frames: List[np.ndarray] = []
        # 与 vosk 同理：转写调用不是线程安全的，串行化保护
        self._lock = threading.RLock()

    def begin(self) -> None:
        with self._lock:
            self._frames = []

    def set_grammar(self, words) -> None:
        """faster-whisper 不支持词表约束，保留接口以便统一调用（no-op）。"""
        return None

    def feed(self, pcm: bytes) -> str:
        # faster-whisper 非流式，录音期间不返回部分结果
        with self._lock:
            arr = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            self._frames.append(arr)
        return ""

    def finalize(self) -> str:
        with self._lock:
            if not self._frames:
                return ""
            audio = np.concatenate(self._frames) if len(self._frames) > 1 else self._frames[0]
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes((audio * 32768.0).astype(np.int16).tobytes())
        buf.seek(0)
        # 防幻觉参数：不延续上文、低置信度/疑似无语音的结果直接丢弃。
        # 注意不要用 initial_prompt：whisper 会把提示词本身幻听进输出
        # （实测蹦出「同学们,请用简体中文。」），繁简问题由拼音纠错兜底
        segments, _info = self._model.transcribe(
            buf, language=self.language, vad_filter=True,
            beam_size=1,  # 贪心解码：短句速度约快一倍，准确率几乎不降
            condition_on_previous_text=False,
            no_speech_threshold=0.6, log_prob_threshold=-1.0)
        return "".join(seg.text for seg in segments).strip()


_CN_DIGITS = "零一二三四五六七八九"


def _cn_num(n: int) -> str:
    """整数转中文读法（0-999），供词表生成；格式与 parser 解析规则一致。"""
    if n < 10:
        return _CN_DIGITS[n]
    if n == 10:
        return "十"
    if n < 20:
        return "十" + _CN_DIGITS[n % 10]
    if n < 100:
        d, r = divmod(n, 10)
        s = _CN_DIGITS[d] + "十"
        return s + _CN_DIGITS[r] if r else s
    h, rest = divmod(n, 100)
    s = _CN_DIGITS[h] + "百"
    if rest == 0:
        return s
    if rest < 10:
        return s + "零" + _CN_DIGITS[rest]
    # 百位后面的十位要带「一」：110 读作「一百一十」而不是「一百十」。
    # 直接递归会得到后者，而它不是标准读法——错的写法进了识别词表会让
    # 识别器偏向产出它，解析时那个「十」被吞掉，110 变成 100
    tens, ones = divmod(rest, 10)
    out = s + _CN_DIGITS[tens] + "十"
    return out + _CN_DIGITS[ones] if ones else out


def build_grade_vocab(names, headers=()) -> List[str]:
    """构建成绩登记场景的识别词表：学生姓名 + 表头名 + 中文数字 + 常用关键词。

    注入 vosk SetGrammar 后，识别只在这些候选词里挑，
    念人名不再被听成无关的日常词语，准确率大幅提升。
    表头名（「程序题」）同样要进词表——老师直接念表头定位题目。
    """
    vocab = {str(n).strip() for n in names if str(n).strip()}
    vocab.update(str(h).strip() for h in headers if str(h).strip())
    for i in range(0, 121):
        vocab.add(_cn_num(i))
    vocab.update(["分", "点", "总分", "题", "第一题", "好了", "取消", "重来",
                  "删除", "核对", "零点五"])
    return sorted(vocab)


# ---------------------------------------------------------------------------
# sherpa-onnx 流式识别引擎（默认）：k2-fsa 开源，加载<1秒，真流式边说边出字
# ---------------------------------------------------------------------------

def sherpa_model_dir(cfg: dict) -> str:
    """定位 sherpa 流式模型目录：优先大版（更准），small 版兜底。

    路径基于项目目录解析，从任意工作目录启动都能找到模型。
    """
    base = os.path.join(model_dir_path(cfg), "sherpa")
    if os.path.isdir(base):
        found_small = None
        for name in sorted(os.listdir(base)):
            p = os.path.join(base, name)
            if name.startswith("sherpa-onnx-streaming") and os.path.isfile(
                    os.path.join(p, "tokens.txt")):
                if "small" not in name:
                    return p        # 大版优先（准确率更高，加载仍为秒级）
                found_small = p
        if found_small:
            return found_small
    raise FileNotFoundError(
        f"未找到 sherpa 流式模型（{base} 下应有 tokens.txt），"
        "模型目录 models/sherpa 缺失或损坏")


def hotwords_path(cfg: dict) -> str:
    """热词文件位置：模型目录在打包版里是只读的，热词写用户数据目录。"""
    return os.path.join(paths.user_data_dir(), "hotwords.txt")


class SherpaEngine:
    """sherpa-onnx 流式 Zipformer（中英双语）：毫秒级加载、真流式 partial。

    支持热词（hotwords）：把学生名单写进热词文件，解码时给这些词加分，
    念姓名不再被听成无关的日常词。名单在打开表格后才确定，所以热词在
    下一次录音开始时重建识别器生效（重建约几百毫秒，发生在录音之前）。
    """

    def __init__(self, cfg: dict, sample_rate: int = 16000):
        self.cfg = cfg
        self.sample_rate = sample_rate
        self._model_dir = sherpa_model_dir(cfg)
        self._hotwords_file = ""
        self._hotwords_dirty = False
        self._rec = self._create_recognizer("")
        self._stream = None
        self._finished = False
        self._lock = threading.RLock()

    def _create_recognizer(self, hotwords_file: str):
        import sherpa_onnx
        d = self._model_dir
        pick = lambda name: os.path.join(  # noqa: E731
            d, name) if os.path.exists(os.path.join(d, name)) else os.path.join(
            d, name.replace(".onnx", ".int8.onnx"))
        # 热词要求 modified_beam_search 解码（sherpa_onnx 对 greedy_search
        # + hotwords_file 直接抛错）；没热词时保持 greedy_search，更快
        decoding_method = ("modified_beam_search" if hotwords_file
                           else "greedy_search")
        return sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=os.path.join(d, "tokens.txt"),
            encoder=pick("encoder-epoch-99-avg-1.onnx"),
            decoder=pick("decoder-epoch-99-avg-1.onnx"),
            joiner=pick("joiner-epoch-99-avg-1.onnx"),
            num_threads=2, sample_rate=self.sample_rate, feature_dim=80,
            decoding_method=decoding_method,
            # 实时对讲式端点检测：说完一句（尾部静音）自动断句，
            # reset 只重置解码状态、音频流无缝继续——不会丢字。
            # 静音阈值不宜太长：老师说完要等近 2 秒才出结果，太煎熬
            enable_endpoint_detection=True,
            rule1_min_trailing_silence=1.2,
            rule2_min_trailing_silence=0.6,
            rule3_min_utterance_length=20,
            # 热词加分：名单里的姓名/表头在解码时获得额外偏置
            hotwords_file=hotwords_file,
            hotwords_score=1.5,
        )

    def begin(self) -> None:
        with self._lock:
            if self._hotwords_dirty:
                self._rec = self._create_recognizer(self._hotwords_file)
                self._hotwords_dirty = False
            self._stream = self._rec.create_stream()
            self._finished = False

    def set_grammar(self, words) -> None:
        """把学生名单与常用语写入热词文件，下一次录音开始时生效。

        传 None/空 则清除热词（重建为无热词的识别器）。
        """
        with self._lock:
            path = ""
            if words:
                cleaned = sorted({str(w).strip() for w in words
                                  if str(w).strip()})
                try:
                    path = hotwords_path(self.cfg)
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "w", encoding="utf-8") as f:
                        f.write("\n".join(cleaned))
                except OSError as e:
                    print(f"[warn] 热词文件写入失败（退回无热词）: {e}",
                          flush=True)
                    path = ""
            if path != self._hotwords_file:
                self._hotwords_file = path
                self._hotwords_dirty = True

    def feed(self, pcm: bytes) -> str:
        with self._lock:
            if self._stream is None:
                self.begin()
            arr = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            self._stream.accept_waveform(self.sample_rate, arr)
            while self._rec.is_ready(self._stream):
                self._rec.decode_stream(self._stream)
            return self._rec.get_result(self._stream)

    def check_endpoint(self):
        """实时对讲式断句：引擎检测到句尾端点时，取整句并重置解码继续听。

        reset 只清解码状态、不销毁音频流——与「finalize+重建流」不同，
        断句前后衔接的音频一个字都不会丢。返回 (是否断句, 该句文本)。
        """
        with self._lock:
            if self._stream is None:
                return False, ""
            if not self._rec.is_endpoint(self._stream):
                return False, ""
            text = self._rec.get_result(self._stream)
            self._rec.reset(self._stream)
            return True, text

    def finalize(self) -> str:
        with self._lock:
            if self._stream is None:
                return ""
            # 短句（如单独念「张三」两三个字）音频太短，流式解码器凑不够
            # 上下文会输出空白——句尾补 0.5 秒静音帮它凑足解码窗口（实测有效）
            self._stream.accept_waveform(
                self.sample_rate,
                np.zeros(int(self.sample_rate * 0.5), dtype=np.float32))
            while self._rec.is_ready(self._stream):
                self._rec.decode_stream(self._stream)
            if not self._finished:
                self._stream.input_finished()
                self._finished = True
            while self._rec.is_ready(self._stream):
                self._rec.decode_stream(self._stream)
            return self._rec.get_result(self._stream)


# ---------------------------------------------------------------------------
# 非流式中文识别（默认）：整句一次解码，短句与嘈杂环境下比流式稳得多
# ---------------------------------------------------------------------------

# 2025-09-09 版。实测在这套短人名场景下明显优于 2024-07-17 版：
# 安静环境点名可用率 90%→98%，且体积几乎没变
_SENSE_VOICE_BASE = ("https://huggingface.co/csukuangfj/"
                     "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09/"
                     "resolve/main/")
# (文件名, 地址, 大致字节数)，字节数只用来算总进度
SENSE_VOICE_FILES = (
    ("model.int8.onnx", _SENSE_VOICE_BASE + "model.int8.onnx", 237_000_000),
    ("tokens.txt", _SENSE_VOICE_BASE + "tokens.txt", 316_000),
)

# 2024-03-09 版 paraformer-large。短句场景实测比 sense-voice 略准且更快，
# 对「姓名+分数」连读（如「李四十八分」）不丢字
_PARA_BASE = ("https://huggingface.co/csukuangfj/"
              "sherpa-onnx-paraformer-zh-2024-03-09/resolve/main/")
PARAFORMER_FILES = (
    ("model.int8.onnx", _PARA_BASE + "model.int8.onnx", 217_000_000),
    ("tokens.txt", _PARA_BASE + "tokens.txt", 74_000),
)


_HF_HOST = "https://huggingface.co/"
# huggingface.co 在国内常年慢或不通，这个镜像的路径与官方一一对应，
# 换掉域名即可。环境变量 HF_ENDPOINT 优先，方便自带镜像的用户覆盖
_HF_MIRRORS = ("https://hf-mirror.com/",)


def download_urls(url: str) -> List[str]:
    """一个下载地址的候选列表，按先后顺序尝试。

    官方地址在前、镜像在后：能连上官方就用官方，连不上再退镜像，
    而不是反过来——镜像的更新有滞后。
    """
    if not url.startswith(_HF_HOST):
        return [url]
    tail = url[len(_HF_HOST):]
    env = os.environ.get("HF_ENDPOINT", "").strip().rstrip("/")
    heads = [_HF_HOST]
    if env:
        heads.insert(0, env + "/")     # 用户指定的镜像最优先
    heads += list(_HF_MIRRORS)
    out = []
    for head in heads:
        candidate = head + tail
        if candidate not in out:
            out.append(candidate)
    return out


def _files_present(model_dir: str, files) -> bool:
    return all(os.path.isfile(os.path.join(model_dir, name))
               for name, _url, _size in files)


def _download_files(model_dir: str, files, progress=None) -> str:
    """下载一组模型文件，已存在的跳过。progress(已下载字节, 总字节)。

    进度按所有待下文件的总字节算，不是每个文件各走一遍 0~100%。
    先写 .part 再改名：中断留下的残片不会被当成下好的模型。
    """
    os.makedirs(model_dir, exist_ok=True)
    todo = [(n, u, s) for n, u, s in files
            if not os.path.isfile(os.path.join(model_dir, n))]
    if not todo:
        return model_dir
    total_all = sum(s for _n, _u, s in todo)
    done_before = 0
    for name, url, approx in todo:
        dst = os.path.join(model_dir, name)
        tmp = dst + ".part"
        print(f"[voice] 正在下载识别模型 {name} …", flush=True)

        def hook(blocks, block_size, total, _base=done_before, _approx=approx):
            if progress:
                got = min(blocks * block_size, total if total > 0 else _approx)
                progress(min(_base + got, total_all), total_all)

        last_error = None
        for candidate in download_urls(url):
            try:
                urllib.request.urlretrieve(candidate, tmp, reporthook=hook)
                last_error = None
                break
            except BaseException as exc:      # noqa: BLE001
                # 断掉的残片留着会占几百 MB，而且下次重下要重来，直接清掉
                if os.path.exists(tmp):
                    os.remove(tmp)
                if isinstance(exc, KeyboardInterrupt):
                    raise
                last_error = exc
                print(f"[voice] {candidate} 下载失败（{exc}），换下一个地址",
                      flush=True)
        if last_error is not None:
            raise last_error
        os.replace(tmp, dst)
        done_before += approx
    if progress:
        progress(total_all, total_all)
    return model_dir


def paraformer_dir(cfg: dict) -> str:
    """模型目录，路径基于项目目录解析，从任意工作目录启动都找得到。"""
    return engine_model_dir(cfg, "paraformer-zh")


def paraformer_ready(cfg: dict) -> bool:
    return _files_present(paraformer_dir(cfg), PARAFORMER_FILES)


def download_paraformer(cfg: dict, progress=None) -> str:
    return _download_files(paraformer_dir(cfg), PARAFORMER_FILES, progress)


def sense_voice_dir(cfg: dict) -> str:
    """模型目录，路径基于项目目录解析，从任意工作目录启动都找得到。"""
    return engine_model_dir(cfg, "sense-voice")


def sense_voice_ready(cfg: dict) -> bool:
    return _files_present(sense_voice_dir(cfg), SENSE_VOICE_FILES)


def download_sense_voice(cfg: dict, progress=None) -> str:
    return _download_files(sense_voice_dir(cfg), SENSE_VOICE_FILES, progress)


def _offline_model_files(model_dir: str) -> tuple:
    """整句解码模型的 (模型文件, 词表文件)，缺一个就报错。"""
    model = os.path.join(model_dir, "model.int8.onnx")
    tokens = os.path.join(model_dir, "tokens.txt")
    if not (os.path.isfile(model) and os.path.isfile(tokens)):
        raise FileNotFoundError(
            f"未找到识别模型（{model_dir} 下缺 model.int8.onnx 或 tokens.txt）")
    return model, tokens


class _OfflineEngine:
    """整句解码引擎的共用部分：录音期间只攒音频，停顿断句后一次性识别。"""

    streaming = False

    def __init__(self, recognizer, sample_rate: int = 16000):
        self._rec = recognizer
        self.sample_rate = sample_rate
        self._frames: List[np.ndarray] = []
        # 解码调用不是线程安全的，串行化保护
        self._lock = threading.RLock()

    def begin(self) -> None:
        with self._lock:
            self._frames = []

    def set_grammar(self, words) -> None:
        """不支持词表约束，人名靠拼音纠错兜底。"""
        return None

    def feed(self, pcm: bytes) -> str:
        with self._lock:
            self._frames.append(
                np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0)
        return ""

    def finalize(self) -> str:
        with self._lock:
            if not self._frames:
                return ""
            audio = np.concatenate(self._frames)
            self._frames = []
        if len(audio) < self.sample_rate * 0.15:
            return ""            # 不足 0.15 秒，多半是误触
        stream = self._rec.create_stream()
        stream.accept_waveform(self.sample_rate, audio)
        self._rec.decode_stream(stream)
        return _strip_tags(stream.result.text)


class SenseVoiceEngine(_OfflineEngine):
    """整句解码：录音期间不出中间结果，停顿断句后一次性识别。"""

    def __init__(self, cfg: dict, sample_rate: int = 16000):
        import sherpa_onnx
        model, tokens = _offline_model_files(sense_voice_dir(cfg))
        super().__init__(sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=model, tokens=tokens, num_threads=2,
            # 锁定中文：这是个中英日韩粤多语种模型，不指定就自动判语种，
            # 「八分」「加三分」这类半秒短音频经常被判成英文，吐出
            # tin / baten / smerton 这种拼音式英文，整句作废
            language="zh",
            # 不做数字规范化：它会把「第三题九分」并成「第39分」，
            # 中文数字交给解析器处理更稳
            use_itn=False), sample_rate)


class ParaformerEngine(_OfflineEngine):
    """整句解码，与 sense-voice 同接口：录音期间不出中间结果，停顿后一次性识别。"""

    def __init__(self, cfg: dict, sample_rate: int = 16000):
        import sherpa_onnx
        model, tokens = _offline_model_files(paraformer_dir(cfg))
        super().__init__(sherpa_onnx.OfflineRecognizer.from_paraformer(
            paraformer=model, tokens=tokens, num_threads=2), sample_rate)


_TAG_RE = re.compile(r"<\|[^|]*\|>")
_LATIN_RE = re.compile(r"[A-Za-z]+")
_CJK_RE = re.compile(r"[一-鿿]")


def _strip_tags(text: str) -> str:
    """去掉结果里的语种/情绪标记、拼音碎片与首尾标点。"""
    text = _TAG_RE.sub("", text or "").strip()
    if _CJK_RE.search(text):
        # 中英混词表偶尔在汉字之间吐出拼音片段：「张ang三」要还原成「张三」
        text = _LATIN_RE.sub("", text)
    return text.strip().strip("。，！？、 ")


ALL_ENGINES = ("sense-voice", "paraformer", "sherpa", "vosk", "faster-whisper")


def available_engines(cfg: dict) -> List[str]:
    """当前这份程序里真正能用起来的引擎。

    打包版只随程序带了 sense-voice 与 paraformer：sherpa 的模型要跟着
    仓库分发、faster-whisper 与 vosk 的依赖没打进去。把它们照旧列在
    设置里，老师切过去只会撞上「找不到 tokens.txt」而且点下载也没反应。
    """
    out = ["sense-voice", "paraformer"]   # 内置，缺了也能自动下载
    try:
        sherpa_model_dir(cfg)
        out.append("sherpa")
    except FileNotFoundError:
        pass
    if importlib.util.find_spec("vosk") is not None:
        out.append("vosk")
    if importlib.util.find_spec("faster_whisper") is not None:
        out.append("faster-whisper")
    return out


# 自带端点检测的引擎：断句由引擎自己判，segment_gap 那档设置对它无效
NATIVE_ENDPOINT_ENGINES = frozenset({"sherpa"})


def resolve_engine(cfg: dict) -> str:
    """把配置里的引擎名收敛到一个真能用的。

    老配置里可能存着现在用不了的引擎（换过版本、或换到打包版），
    直接拿去建引擎会一路报错到界面上。
    """
    engine = cfg.get("engine", "sense-voice")
    usable = available_engines(cfg)
    return engine if engine in usable else usable[0]


def create_engine(cfg: dict):
    """按配置创建语音引擎；缺模型/依赖时自动回退。

    回退链：sense-voice → paraformer → sherpa → faster-whisper（若已装）→ vosk。
    """
    engine_name = resolve_engine(cfg)
    if engine_name != cfg.get("engine", "sense-voice"):
        print(f"[warn] 引擎 {cfg.get('engine')} 在这份程序里不可用，"
              f"改用 {engine_name}", file=sys.stderr)
    sr = int(cfg.get("sample_rate", 16000))
    if engine_name == "sense-voice":
        try:
            import sherpa_onnx  # noqa: F401
            return SenseVoiceEngine(cfg, sample_rate=sr)
        except FileNotFoundError as e:
            print(f"[warn] {e}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 识别引擎初始化失败: {e}", file=sys.stderr)
        print("[warn] 自动回退到备用引擎。", file=sys.stderr)
        engine_name = "paraformer"
    if engine_name == "paraformer":
        try:
            import sherpa_onnx  # noqa: F401
            return ParaformerEngine(cfg, sample_rate=sr)
        except FileNotFoundError as e:
            print(f"[warn] {e}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] paraformer 引擎初始化失败: {e}", file=sys.stderr)
        print("[warn] 自动回退到备用引擎。", file=sys.stderr)
        engine_name = "sherpa"
    if engine_name == "sherpa":
        try:
            import sherpa_onnx  # noqa: F401
            return SherpaEngine(cfg, sample_rate=sr)
        except FileNotFoundError as e:
            print(f"[warn] {e}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] sherpa 引擎初始化失败: {e}", file=sys.stderr)
        print("[warn] 自动回退到备用引擎。", file=sys.stderr)
        engine_name = "faster-whisper" if importlib.util.find_spec(
            "faster_whisper") else "vosk"
    if engine_name == "faster-whisper":
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            print("[warn] 未安装 faster-whisper，自动回退 vosk 引擎。"
                  "如需更准识别，运行: pip install faster-whisper", file=sys.stderr)
            engine_name = "vosk"
    if engine_name == "faster-whisper":
        return FasterWhisperEngine(cfg.get("whisper_model", "small"))
    return VoskEngine(vosk_model_path(cfg), sample_rate=sr)


ENGINE_SIZE_MB = {"sense-voice": 228, "paraformer": 217, "vosk": 42,
                  "faster-whisper": 460, "sherpa": 0}


def engine_model_ready(cfg: dict, engine: Optional[str] = None) -> bool:
    """指定引擎（默认取配置里的）的模型是否已就位——只检查，不下载。"""
    engine = engine or cfg.get("engine", "sense-voice")
    if engine == "sense-voice":
        return sense_voice_ready(cfg)
    if engine == "paraformer":
        return paraformer_ready(cfg)
    if engine == "vosk":
        return os.path.isdir(vosk_model_path(cfg))
    if engine == "sherpa":
        try:
            sherpa_model_dir(cfg)
        except FileNotFoundError:
            return False
    return True     # faster-whisper 首次使用时自己下


# 各引擎的模型目录名，供界面显示「存到哪」
ENGINE_SUBDIR = {
    "sense-voice": "sense-voice",
    "paraformer": "paraformer-zh",
    "vosk": VOSK_DIRNAME,
}


def engine_model_location(cfg: dict, engine: Optional[str] = None) -> str:
    """这个引擎的模型在哪（未下载时就是将要下到哪）。"""
    engine = engine or cfg.get("engine", "sense-voice")
    subdir = ENGINE_SUBDIR.get(engine)
    if subdir is None:
        return ""            # sherpa 随项目自带、faster-whisper 自己管缓存
    if engine == "vosk":
        return vosk_model_path(cfg)
    return engine_model_dir(cfg, subdir)


def engine_model_status(cfg: dict, engine: Optional[str] = None) -> str:
    """给界面看的一句话状态。"""
    engine = engine or cfg.get("engine", "sense-voice")
    if engine_model_ready(cfg, engine):
        return "模型已就绪"
    size = ENGINE_SIZE_MB.get(engine, 0)
    if engine == "faster-whisper":
        return f"模型在首次使用时自动下载（约 {size}MB）"
    return f"模型未下载（约 {size}MB）"


def download_engine_model(cfg: dict, engine: Optional[str] = None,
                          progress=None) -> None:
    """下载指定引擎的模型。progress(已下载字节, 总字节)。"""
    engine = engine or cfg.get("engine", "sense-voice")
    if engine == "sense-voice":
        download_sense_voice(cfg, progress=progress)
    elif engine == "paraformer":
        download_paraformer(cfg, progress=progress)
    elif engine == "vosk":
        download_vosk_model(cfg, progress=progress)
    elif engine == "sherpa":
        sherpa_model_dir(cfg)   # 随项目自带，缺了只能重新拉仓库


def auto_ensure_engine(cfg: dict, progress=None) -> None:
    """确保配置的引擎可用：缺模型的先下载，whisper 首次使用时自己下。"""
    download_engine_model(cfg, progress=progress)


def list_microphones() -> List[dict]:
    """列出可用麦克风设备（供设置界面选择）。"""
    try:
        import sounddevice as sd
        info = sd.query_devices()
        devices = []
        for i, d in enumerate(info):
            if d.get("max_input_channels", 0) > 0:
                devices.append({"index": i, "name": d.get("name", f"设备{i}"),
                                "channels": d.get("max_input_channels")})
        return devices
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 无法枚举麦克风: {exc}", file=sys.stderr)
        return []


def measure_mic_rms(idx: int) -> float:
    """对指定设备短录 0.5 秒，返回 RMS 音量；失败返回 -1。"""
    import time
    try:
        import sounddevice as sd
    except Exception:  # noqa: BLE001
        return -1.0
    try:
        d = sd.query_devices(idx)
        sr = int(d.get("default_samplerate", 16000) or 16000)
        frames: List[np.ndarray] = []

        def cb(indata, _n, _t, _s) -> None:
            frames.append(indata.copy())

        with sd.InputStream(device=idx, samplerate=sr, channels=1,
                            blocksize=int(sr * 0.1), callback=cb):
            time.sleep(0.5)
    except Exception:  # noqa: BLE001
        return -1.0
    if not frames:
        return -1.0
    arr = np.concatenate(frames)
    return float(np.sqrt(np.mean(arr ** 2)))


# 「这个设备真的在收音」的 RMS 下限：正常底噪与人声都在此之上。
# 蓝牙耳麦"假连接"时录到的是 0.0000x 的电噪声，绝不能选中——
# 否则老师说什么都被当噪音忽略
MIN_LIVE_RMS = 5e-4


def device_has_sound(idx: int) -> bool:
    """指定设备现在能否录到像样的声音。

    用于校验手动配置的设备是否仍有效：蓝牙耳麦"假连接"、设备已拔掉或
    收不到音时都会低于阈值，应回退到自动挑选。
    """
    return measure_mic_rms(idx) >= MIN_LIVE_RMS


def pick_mic_device() -> Optional[int]:
    """自动挑选真正有声音输入的麦克风。

    优先测【系统当前默认输入设备】（老师在系统设置里选的就是想用的），
    有声音就直接用它；否则逐个设备短录 0.5 秒测音量，返回信号最强的；
    所有设备都录不到声音时返回 None（调用方提示检查麦克风权限）。
    """
    try:
        import sounddevice as sd
        info = sd.query_devices()
    except Exception:  # noqa: BLE001
        return None

    # 1) 先试系统默认输入设备：录到像样的信号才直接采用
    try:
        def_idx = sd.default.device[0]
        if def_idx is not None and int(def_idx) >= 0:
            name = sd.query_devices(def_idx).get("name", f"设备{def_idx}")
            rms = measure_mic_rms(int(def_idx))
            ok = rms >= MIN_LIVE_RMS
            print(f"[mic] 默认输入 设备{def_idx} [{name}]: RMS={rms:.6f}"
                  f"{' (选中)' if ok else ' (几乎无声，跳过)'}", flush=True)
            if ok:
                return int(def_idx)
    except Exception:  # noqa: BLE001
        pass

    # 2) 默认输入无声（未连接/虚拟设备），再逐个实测所有输入设备
    best_idx: Optional[int] = None
    best_rms = 0.0
    for idx, d in enumerate(info):
        if d.get("max_input_channels", 0) <= 0:
            continue
        name = d.get("name", "")
        rms = measure_mic_rms(idx)
        if rms < 0:
            print(f"[mic] 设备{idx} [{name}]: 无法录音", flush=True)
            continue
        print(f"[mic] 设备{idx} [{name}]: RMS={rms:.6f}"
              f"{' (有声)' if rms > 1e-3 else ' (静音)'}", flush=True)
        if rms > best_rms:
            best_rms, best_idx = rms, idx
    return best_idx if best_rms >= MIN_LIVE_RMS else None