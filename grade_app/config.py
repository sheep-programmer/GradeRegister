"""配置模块：默认配置 + config.json 覆盖合并。"""
from __future__ import annotations

import json
import os
from typing import Any, Dict

from .paths import user_data_dir

DEFAULT_CONFIG: Dict[str, Any] = {
    # ---- 表格结构（-1 表示自动识别）----
    "name_col": -1,                 # 姓名列（列号，从0开始；-1自动）
    "total_col": -1,                # 总分列（-1自动）
    "check_header": "核对",         # 核对列表头文字（总分列后空列自动创建）

    # ---- 分数解析 ----
    "strip_prefix": True,           # 剥离"第X题"前缀（老师念"第一题18分"时只取18）
    "strip_suffix": True,           # 剥离"分"后缀（"18分"取18）
    "score_cutoff": 0.55,           # 姓名模糊匹配阈值（0~1，越大越严格）

    # ---- 总分 ----
    "write_formula": True,          # 总分列写 =SUM() 公式；False 则写计算好的数值
    "write_checked": True,          # 一致时核对列写"✓"；False 则不写
    "auto_save": True,              # 兼容旧配置：False 等同 auto_save_mode="manual"
    # score=每填一个分数存 | student=每录完一人存 | checked=核对通过才存 | manual=只手动存
    "auto_save_mode": "checked",

    # ---- 语音 ----
    "engine": "sense-voice",        # sense-voice（整句解码，最准，推荐）| paraformer（短句更快）| sherpa（边说边出字）| vosk | faster-whisper
    "model_dir": "models",
    # 下载来的模型存到哪（也是之后读取的目录）。留空＝自动：源码运行放项目
    # 的 models/，打包版放用户数据目录，因为程序自身所在的位置是只读的
    "download_dir": "",
    "vosk_model": "vosk-model-small-cn-0.22",
    "whisper_model": "small",       # tiny/base/small
    "device": None,                 # 麦克风设备索引（None = 每次启动自动挑选）
    "sample_rate": 16000,

    # ---- 交互 ----
    "hold_to_talk": False,          # 按住说话（False 为点击一次开/关）
    "sound_enabled": False,         # 提示音（默认关，安静；开了才有填分/核对/报错的声音）
    "continuous": True,             # 连续听写：一直监听，说完一句停顿即自动执行
    "segment_gap": 0.7,             # 连续听写切句：说完一句停顿超过该秒数就识别
    "auto_next": False,             # 录完一位（核对一致）后自动切到下一位未录学生
    "last_file": "",                # 上次打开的表格路径（启动时自动重新打开）
    "window_geometry": "",          # 上次退出时的窗口位置与大小（如 1200x800+50+50）
}


def config_path() -> str:
    """配置文件位置：打包后写用户目录，程序目录是只读的。"""
    return os.path.join(user_data_dir(), "config.json")


def load_config() -> Dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    path = config_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                user = json.load(f)
            cfg.update({k: v for k, v in user.items() if k in DEFAULT_CONFIG})
        except (json.JSONDecodeError, OSError):
            pass
    return cfg


def save_config(cfg: Dict[str, Any]) -> str:
    """合并写盘：以磁盘现有配置为基础覆盖内存值。

    防止旧版本程序（内存里没有新增字段）把新配置项整体覆盖丢失。
    """
    path = config_path()
    merged: Dict[str, Any] = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                merged = json.load(f)
        except (json.JSONDecodeError, OSError):
            merged = {}
    merged.update(cfg)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    return path