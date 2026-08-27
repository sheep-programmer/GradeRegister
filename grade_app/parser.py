"""文本解析模块：中文数字转换、分数提取、学生姓名匹配。

语音识别（vosk/whisper）输出的中文文本需要解析成结构化数据：
- 名字：识别文本 → 匹配学生列表（支持重名、模糊候选）
- 分数：识别文本 → 数字列表，自动剥离可配置的前缀（第X题）与后缀（分）
"""
from __future__ import annotations

import difflib
import re
from typing import Iterable, List, Optional

# ---------------------------------------------------------------------------
# 中文数字
# ---------------------------------------------------------------------------
CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_UNITS = {"十": 10, "百": 100}

_CN_NUM_RE = re.compile(r"[零一二两三四五六七八九十百]+")
_ARAB_RE = re.compile(r"\d+(?:\.\d+)?")


def cn_int_to_value(s: str) -> Optional[int]:
    """把中文整数（如 十八/二十/二十五/一百零五）转成 int，失败返回 None。"""
    if not s:
        return None
    total, section, num = 0, 0, 0
    for ch in s:
        if ch in CN_DIGITS:
            num = CN_DIGITS[ch]
        elif ch in CN_UNITS:
            unit = CN_UNITS[ch]
            # "十二" 这类十开头的写法：十 = 1*10
            if unit == 10 and num == 0 and total == 0 and section == 0:
                num = 1
            section += num * unit
            num = 0
        else:
            return None
    return total + section + num


def cn_number_to_float(s: str) -> Optional[float]:
    """中文数字（支持小数，如 十二点五 / 十八 / 一点五）转 float，失败返回 None。"""
    s = s.strip()
    if not s:
        return None
    if "点" in s:
        int_part, _, frac_part = s.partition("点")
        if not frac_part:                      # "十三点" 没有小数位 -> 无效
            return None
        iv = cn_int_to_value(int_part) if int_part else 0
        if iv is None:
            return None
        fv = 0.0
        for i, ch in enumerate(frac_part, start=1):
            if ch not in CN_DIGITS:
                return None
            fv += CN_DIGITS[ch] / (10 ** i)
        return iv + fv
    return cn_int_to_value(s)


def any_number_to_float(s: str) -> Optional[float]:
    """同时支持阿拉伯数字与中文数字的字符串转 float。"""
    s = s.strip()
    if not s:
        return None
    if _ARAB_RE.fullmatch(s):
        return float(s)
    return cn_number_to_float(s)


# ---------------------------------------------------------------------------
# 分数提取
# ---------------------------------------------------------------------------
_PREFIX_RE = re.compile(r"第?[零一二两三四五六七八九十百\d]+[题次体]")  # 「第三题」「第一次」「第一体」都剥离
_SUFFIX_RE = re.compile(r"分+")
_WHITESPACE_RE = re.compile(r"\s+")


def extract_scores(text: str, strip_prefix: bool = True, strip_suffix: bool = True) -> List[float]:
    """从识别文本中提取分数列表。

    例子（默认开启前后缀剥离）:
        "十八分"                    -> [18.0]
        "第一题十八分"              -> [18.0]
        "十八分十二分十五分十七分"  -> [18.0, 12.0, 15.0, 17.0]
        "第1题18.5"                 -> [18.5]
        "第一题十八分 第二题十二分" -> [18.0, 12.0]
    若关闭剥离，则 "第一题十八分" -> [1.0, 18.0]（把题号也算进去了）。
    """
    # 同音误听纠正（时→十 等），再做词切分
    text = (text or "").translate(_MISHEAR_TO_NUM)
    tokens = text.split() or [""]
    raw: List[tuple] = []  # (token序号, 数值, token是否带题头)
    for i, tok in enumerate(tokens):
        seg = _WHITESPACE_RE.sub("", tok)
        if strip_prefix:
            seg, n_prefix = _PREFIX_RE.subn(" ", seg)
        else:
            n_prefix = 0
        had_prefix = n_prefix > 0   # 带「第X题」头的值是独立分数，不参与相邻合并
        if strip_suffix:
            seg = _SUFFIX_RE.sub(" ", seg)
        for m in _ARAB_RE.finditer(seg):
            raw.append((i, float(m.group()), had_prefix))
        # 中文数字：整段匹配（已剥离前缀/后缀后剩下的连续中文数字串）
        for m in _CN_NUM_RE.finditer(seg):
            v = cn_number_to_float(m.group())
            if v is not None:
                raw.append((i, v, had_prefix))
    # 相邻合并：分词把「十八」拆成「十」「八」时（前整十 + 后个位、词相邻、
    # 且前一个词不带题头——带题头的「第二题 三分」是两个独立分数）
    results: List[float] = []
    prev_idx = None
    prev_had_prefix = True
    for idx, v, had_prefix in raw:
        if (results and prev_idx is not None and idx == prev_idx + 1
                and not prev_had_prefix
                and v < 10 and results[-1] >= 10 and results[-1] % 10 == 0):
            results[-1] += v
        else:
            results.append(v)
        prev_idx, prev_had_prefix = idx, had_prefix
    return results


_TOTAL_MARKERS = ("总分", "总成绩", "一共", "总")
# 按字数分组：三字窗口只跟三字词比，否则「钟分六」会挨上「总分」
_TOTAL_PINYIN = {2: ("zongfen", "yigong"), 3: ("zongchengji",)}
_TOTAL_PINYIN_MIN = 0.75   # 数字词与总分的拼音相似度都在 0.4 以下，留足余量


def find_total_marker(text: str, names: Iterable[str] = ()) -> tuple:
    """定位总分关键词，返回它在原文里的 (起点, 终点)；没有则 (-1, -1)。"""
    text = text or ""
    hits = [(text.find(m), m) for m in _TOTAL_MARKERS]
    hits = [(p, m) for p, m in hits if p >= 0]
    if hits:
        # 位置相同时取更长的关键词：「总分45」要切在「分」后面而不是「总」后面
        pos, marker = min(hits, key=lambda pm: (pm[0], -len(pm[1])))
        return pos, pos + len(marker)
    return _find_total_by_pinyin(text, names)


def _find_total_by_pinyin(text: str, names: Iterable[str]) -> tuple:
    """按发音找总分关键词：「总分」常被听成「钟分」「充分」「钟晨」。

    只在句中有数字时才猜，且与名单里姓名同音的词不算——班上真有
    「钟芳」时，念名字不能被当成念总分。
    """
    if _lazy_pinyin is None or not extract_scores(text):
        return -1, -1
    name_set = {str(n).strip() for n in names if str(n).strip()}
    keep = [i for i, ch in enumerate(text) if not ch.isspace()]
    stripped = "".join(text[i] for i in keep)
    for start in range(len(stripped)):
        # 先试两字：「钟晨六」整段听上去也接近「总成绩」，会把「六」吃掉
        for size in (2, 3):
            win = stripped[start:start + size]
            if len(win) < size or win in name_set or is_number_text(win):
                continue
            py = _pinyin(win)
            if py and max(difflib.SequenceMatcher(None, py, t).ratio()
                          for t in _TOTAL_PINYIN[size]) >= _TOTAL_PINYIN_MIN:
                return keep[start], keep[start + size - 1] + 1
    return -1, -1


def find_total_keyword(text: str, names: Iterable[str] = ()) -> bool:
    """识别文本里是否提到总分。

    「总分」常被听成「总跟/总 份」等，故含「总」且有数字也视为总分；
    中文数字里没有「总」字，不会误伤纯分数句。
    """
    start, end = find_total_marker(text, names)
    if start < 0:
        return False
    marker = (text or "")[start:end]
    if marker == "一共":
        return "分" in text
    if marker == "总":
        return bool(extract_scores(text))
    return True


def split_at_total(text: str, names: Iterable[str] = ()) -> tuple:
    """按总分关键词把句子切成 (关键词之前, 关键词之后)。

    老师常一句话连念「…七分 一共 92」：前半是各题分数、后半才是总分。
    关键词不止「总」一个，「一共」同样要切——否则「一共92分」里的
    「一」会被当成题分，92 也会被误填进空题。
    """
    text = text or ""
    start, end = find_total_marker(text, names)
    if start < 0:
        return text, ""
    return text[:start], text[end:]


# ---------------------------------------------------------------------------
# 学号 / 序号定位
# ---------------------------------------------------------------------------
_NUM = r"[零一二两三四五六七八九十百\d]+"
# 号码必须带标记词才算：裸数字一律当分数，否则「78」是学号还是分数分不清。
# 「第X个」也算，老师常说「第五个」指名单第五位；「第X题」不在此列
_ID_PATTERNS = (
    re.compile(rf"(?:学号|座号|编号|考号|序号)\s*({_NUM})\s*号?"),
    re.compile(rf"第\s*({_NUM})\s*个"),
    re.compile(rf"({_NUM})\s*号"),
)


def extract_student_id(text: str) -> tuple:
    """从识别文本里取出学号/序号，返回 (号码文字或None, 去掉号码后的剩余文本)。

    「5号78分85分」-> ("5", "78分85分")：先定位学生，剩下的当分数。
    「5分」「第三题」「78」都不是号码——不带「号」或「第X个」标记就不认。
    """
    text = (text or "").translate(_MISHEAR_TO_NUM)
    for pat in _ID_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        if any_number_to_float(m.group(1)) is None:
            continue
        rest = (text[:m.start()] + " " + text[m.end():]).strip()
        return m.group(1), rest
    return None, text


def collapse_repeats(text: str) -> str:
    """整段是同一个词重复多遍时折叠成一遍。

    识别引擎对短音频常把一个词吐好几遍：「李四李四李四」->「李四」。
    只在整段严格重复时折叠，「张三张三丰」不动；单字重复（「三三」）
    可能是名字本身，也不动。
    """
    t = (text or "").strip()
    n = len(t)
    if n < 4:
        return text
    for size in range(2, n // 2 + 1):
        if n % size == 0 and t[:size] * (n // size) == t:
            return t[:size]
    return text


# ---------------------------------------------------------------------------
# 姓名匹配
# ---------------------------------------------------------------------------
def clean_name_text(text: str) -> str:
    """清理识别文本中的标点与空白，用于匹配姓名。"""
    return re.sub(r"[，。、！？；：,.!?;:\s'\"]+", "", text or "").strip()


def remove_matched_names(text: str, names: Iterable[str]) -> str:
    """从识别文本中剔除所有出现的姓名（长名优先），返回剩余文本。

    用于「张三 十八 十二」这类名字+分数连念的句子：
    先去掉名字，「三/五」等数字字就不会再被误当成分数。
    """
    result = text or ""
    for name in sorted({str(n).strip() for n in names if str(n).strip()},
                       key=len, reverse=True):
        if name:
            result = result.replace(name, " ")
    return result


def is_number_text(text: str) -> bool:
    """判断清理后的文本是否纯数字（含中文数字）——纯数字绝不能当人名。

    防止「十三」因共享「三」字被模糊匹配成「张三」。
    """
    t = clean_name_text(text)
    return bool(t) and all(ch in "零一二两三四五六七八九十百点．.0123456789"
                           for ch in t)


def match_student_names(text: str, names: Iterable[str], cutoff: float = 0.45) -> List[str]:
    """按优先级匹配姓名：精确 > 包含 > 模糊相似 > 单字重叠兜底。

    返回有序候选列表（可能为空）。名字相同但出现在多行的情况由调用方
    用 find_student_rows 处理重名选择。
    """
    t = clean_name_text(text)
    if not t:
        return []
    name_list = list(dict.fromkeys(names))  # 去重保序

    # 1) 精确匹配
    exact = [n for n in name_list if n == t]
    if exact:
        return exact
    # 2) 包含匹配（识别文本包含名字，或名字包含识别文本，取较短的包含关系）
    contained = []
    for n in name_list:
        if not n:
            continue
        if n in t or t in n:
            if len(n) >= 2 and len(t) >= 1:  # 至少2字的名字，避免单字误中
                contained.append(n)
    if contained:
        return contained
    # 3) 模糊匹配（difflib，如 章三 -> 张三）
    fuzzy = difflib.get_close_matches(t, name_list, n=5, cutoff=cutoff)
    if fuzzy:
        return fuzzy
    # 4) 兜底：长度相近且至少共享一个汉字（如 张叁 -> 张三）
    overlap = []
    tset = set(t)
    for n in name_list:
        if len(n) >= 2 and len(t) >= 2 and abs(len(n) - len(t)) <= 1 \
                and (set(n) & tset):
            overlap.append(n)
    return overlap


def _numbers_in_order(seg: str) -> List[float]:
    """一段文本里的数字，按出现位置排序（阿拉伯与中文数字混排）。"""
    found = []
    for m in _ARAB_RE.finditer(seg):
        found.append((m.start(), float(m.group())))
    for m in _CN_NUM_RE.finditer(seg):
        v = cn_number_to_float(m.group())
        if v is not None:
            found.append((m.start(), v))
    return [v for _pos, v in sorted(found, key=lambda pv: pv[0])]


# 句首的题号允许丢掉「第」字（识别常吞掉它）；句中的必须带「第」，
# 否则「再说一次十分」里的「一次」会被当成题号
_HEAD_RE = re.compile(r"第?[零一二两三四五六七八九十百\d]+[题次]")
_INNER_HEAD_RE = re.compile(r"第[零一二两三四五六七八九十百\d]+[题次]")


def is_numbered_header(header: str) -> bool:
    """表头本身就是「第X题」「三次」这类序号写法。

    这类表头走序号解析，不参与表头名的拼音模糊匹配——「第一题」与
    「第二题」发音太近，模糊匹配会把分数填到错的题上。
    """
    return bool(_HEAD_RE.fullmatch(clean_name_text(header or "")))


def named_header_map(headers: Iterable[str]) -> dict:
    """{表头文字: 1-based 序号}，只收名称型表头（程序题/作文这类）。"""
    out = {}
    for i, h in enumerate(headers, start=1):
        h = clean_name_text(h)
        if h and not is_numbered_header(h) and h not in out:
            out[h] = i
    return out


def _head_matches(seg: str, header_map: dict) -> List[tuple]:
    """段内所有题头的 (起点, 终点, 序号)，按位置排序、互不重叠。

    三条来源：段首的「第X题」（识别常吞掉「第」字）、段中的「第X题」、
    以及表头名原文出现的位置。
    """
    found: List[tuple] = []
    lead = _HEAD_RE.match(seg)
    if lead:
        found.append((lead.start(), lead.end(), _head_number(lead.group(0))))
    for m in _INNER_HEAD_RE.finditer(seg):
        if m.start() >= (lead.end() if lead else 0):
            found.append((m.start(), m.end(), _head_number(m.group(0))))
    # 长表头优先：表头同时有「选择题」「选择题二」时不能只认前缀
    for name in sorted(header_map, key=len, reverse=True):
        start = seg.find(name)
        while start >= 0:
            end = start + len(name)
            if not any(s < end and start < e for s, e, _q in found):
                found.append((start, end, float(header_map[name])))
            start = seg.find(name, end)
    return sorted(found, key=lambda t: t[0])


def _head_number(head: str) -> Optional[float]:
    raw = head.rstrip("题次").lstrip("第")
    return float(raw) if raw.isdigit() else cn_number_to_float(raw)


def _split_by_head(seg: str, header_map: Optional[dict] = None) -> List[tuple]:
    """按段内出现的每一个题头切分，返回 [(题号或None, 该题头后的文本)]。

    老师常一口气念完整行「第一题十分第二题二十分…」，识别结果不带空格，
    题头只认开头一个就会把后面所有分数都算到第一题上。
    表头名（「程序题」）与序号（「第一题」）都算题头。
    """
    heads = _head_matches(seg, header_map or {})
    if not heads:
        return [(None, seg)]
    out: List[tuple] = []
    if heads[0][0] > 0:
        out.append((None, seg[:heads[0][0]]))
    for i, (_start, end, q) in enumerate(heads):
        stop = heads[i + 1][0] if i + 1 < len(heads) else len(seg)
        out.append((q, seg[end:stop]))
    return out


def extract_score_items(text: str, strip_prefix: bool = True,
                        strip_suffix: bool = True,
                        headers: Iterable[str] = ()) -> List[tuple]:
    """提取 [(题号或None, 分值)]。

    「第三题九分」-> (3, 9.0)：题号明确，定向填到第三题；
    「十八」-> (None, 18.0)：没带题号，顺序填下一个空题。
    「第三题」单独出现（分数在下一句）-> (3, None)：占位，等待补分。
    一句话里念多道题 -> 每个题头各自成段，分数跟着自己的题号。
    headers 给出各分数列的表头，念表头名（「程序题72分」）等同于念题号。
    分隔词「加/和/与」当作切分点：「10加5加5加3」-> 四个独立分数。
    一个「分」字只结算一个分数，取紧挨着它的那个数：「第一题十五分」被
    听成「七月十五分」时只认 15，不能把 7 也当成一道题的分数。
    """
    text = (text or "").translate(_MISHEAR_TO_NUM)
    header_map = named_header_map(headers)
    items: List[tuple] = []
    # 「加/和/与」是明确分隔：切出的每段都视为有效分数（如 10加5加5加3）
    parts = re.split(r"[加和与]", text)
    split_by_sep = len(parts) > 1
    for part in parts:
        for tok in (part.split() or [""]):
            tok = _WHITESPACE_RE.sub("", tok)
            groups = (_split_by_head(tok, header_map) if strip_prefix
                      else [(None, tok)])
            for q, seg in groups:
                # 分数须带「分」字，或用分隔词隔开；防「10和5」被粘连误读成 15
                has_fen = "分" in seg or split_by_sep
                if strip_suffix:
                    seg = _SUFFIX_RE.sub(" ", seg)
                seg_vals: List[float] = []
                for chunk in (seg.split() or [seg]):
                    vals = _numbers_in_order(chunk)
                    if vals:
                        seg_vals.append(vals[-1])   # 每个「分」只结算最后一个数
                if not seg_vals:
                    if q is not None:
                        items.append((q, None))   # 只有题头：占位等补分
                    continue
                if q is None and not has_fen:
                    continue   # 无题号且没带「分」、也没用分隔词：不当作分数
                for v in seg_vals:
                    items.append((q, v))
    return items


def find_student_rows(text: str, students: List[tuple], cutoff: float = 0.45) -> List[tuple]:
    """在 [(row_idx, name), ...] 中查找匹配行。

    返回所有匹配行（重名时返回多行，由 UI 弹窗让用户选择）。
    """
    names = [name for _, name in students]
    candidates = match_student_names(text, names, cutoff=cutoff)
    if not candidates:
        return []
    cand_set = set(candidates)
    return [(row, name) for row, name in students if name in cand_set]


# ---------------------------------------------------------------------------
# 拼音级姓名纠错（macOS 版 vosk 不支持 SetGrammar 词表约束，
# 同音误听如「掌声」->「张三」只能靠后处理纠正）
# ---------------------------------------------------------------------------
try:
    from pypinyin import lazy_pinyin as _lazy_pinyin
except Exception:  # noqa: BLE001
    _lazy_pinyin = None


def _pinyin(s: str) -> str:
    if _lazy_pinyin is None:
        return ""
    try:
        return "".join(_lazy_pinyin(s))
    except Exception:  # noqa: BLE001
        return ""


_NAME_FIX_THRESHOLD = 0.62   # 拼音相似度达标即认定为念了该学生的名字
# 最佳与次佳差距小于此值就算听不出区别（班上同时有「刘洋」「刘阳」），
# 这时不擅自改写文本，留给候选框让老师点
_NAME_AMBIGUOUS_GAP = 0.08


def rank_names_by_pinyin(token: str, names: Iterable[str],
                         prefer: Iterable[str] = ()) -> List[tuple]:
    """按发音接近程度给学生姓名排序，返回 [(姓名, 相似度), ...] 从高到低。

    prefer（一般是还没录完的学生）只在相似度几乎相同时用作裁决：
    老师很少回头重录，但确实会回头改分，所以不做排除、只做裁决。
    """
    t_py = _pinyin(token)
    if not t_py or len(token) < 2:
        return []
    prefer_set = {str(p).strip() for p in prefer}
    scored = []
    for n in names:
        n = str(n).strip()
        if len(n) < 2:
            continue
        r = difflib.SequenceMatcher(None, t_py, _pinyin(n)).ratio()
        scored.append((n, r))
    # 同分时未录完的排前面：sorted 稳定，先按偏好分桶再按相似度排
    scored.sort(key=lambda nr: (-nr[1], nr[0] not in prefer_set))
    return scored


def best_name_by_pinyin(token: str, names: Iterable[str],
                        prefer: Iterable[str] = ()) -> tuple:
    """返回 (最佳姓名, 相似度)：token 与哪个学生姓名发音最接近。"""
    ranked = rank_names_by_pinyin(token, names, prefer)
    if not ranked:
        return "", 0.0
    # 头名与次名难分时，让 prefer 里的那个胜出
    if len(ranked) >= 2 and ranked[0][1] - ranked[1][1] < _NAME_AMBIGUOUS_GAP:
        prefer_set = {str(p).strip() for p in prefer}
        close = [nr for nr in ranked
                 if ranked[0][1] - nr[1] < _NAME_AMBIGUOUS_GAP]
        for name, r in close:
            if name in prefer_set:
                return name, r
    return ranked[0]


def is_name_ambiguous(token: str, names: Iterable[str]) -> bool:
    """发音上分不出是哪位学生——该弹候选框而不是替老师猜。"""
    ranked = rank_names_by_pinyin(token, names)
    if len(ranked) < 2 or ranked[0][1] < _NAME_FIX_THRESHOLD:
        return False
    return ranked[0][1] - ranked[1][1] < _NAME_AMBIGUOUS_GAP


def correct_names_in_text(text: str, names: Iterable[str],
                          prefer: Iterable[str] = ()) -> tuple:
    """把句中发音接近学生姓名的词替换为真实姓名。

    例：「掌声 十八 十二」->「张三 十八 十二」（张三在名单里时）。
    发音上分不出是哪位学生时保持原样不改写——改写会让后续匹配变成精确
    命中，候选框就再也没机会弹出来，等于替老师闷头猜了一个。
    返回 (纠正后的文本, 被纠正的姓名列表)。
    """
    if _lazy_pinyin is None or not text:
        return text, []
    name_list = [str(n).strip() for n in names if str(n).strip()]
    if not name_list:
        return text, []
    fixed: List[str] = []
    out_tokens = []
    for tok in text.split():
        t = clean_name_text(tok)
        if (t and len(t) >= 2 and not is_number_text(t)
                and not is_name_ambiguous(t, name_list)):
            best, r = best_name_by_pinyin(t, name_list, prefer)
            if r >= _NAME_FIX_THRESHOLD:
                out_tokens.append(best)
                if best != t:
                    fixed.append(f"{t}→{best}")
                continue
        out_tokens.append(tok)
    return " ".join(out_tokens), fixed


_HEADER_FIX_THRESHOLD = 0.62   # 拼音相似度达标即认定念的是该表头


def correct_headers_in_text(text: str, headers: Iterable[str],
                            names: Iterable[str] = ()) -> tuple:
    """把句中发音接近表头名的片段替换成表头原文。

    例：「成序题七十二分」->「程序题七十二分」（表头有「程序题」时）。
    替换后不插空格：表头名靠精确查找定位，分开反而把题头与分数拆成两段。
    只处理名称型表头；序号型（「第一题」）发音互相太近，一律不猜。
    与学生姓名同音的片段跳过——班上真有「程雪」时念名字不能被当成念表头。
    返回 (纠正后的文本, 被纠正的表头列表)。
    """
    header_map = named_header_map(headers)
    if _lazy_pinyin is None or not text or not header_map:
        return text, []
    name_set = {clean_name_text(n) for n in names if clean_name_text(n)}
    sizes = sorted({len(h) for h in header_map})
    fixed: List[str] = []
    out = text
    for name in sorted(header_map, key=len, reverse=True):
        if name in out:
            continue          # 已经念对了，不用猜
        target = _pinyin(name)
        if not target:
            continue
        best_win, best_r, best_pos = "", 0.0, -1
        for size in sizes:
            for start in range(len(out) - size + 1):
                win = out[start:start + size]
                if (win in name_set or is_number_text(win)
                        or win in header_map or win.strip() != win):
                    continue
                r = difflib.SequenceMatcher(None, _pinyin(win), target).ratio()
                if r > best_r:
                    best_win, best_r, best_pos = win, r, start
        if best_r >= _HEADER_FIX_THRESHOLD and best_pos >= 0:
            out = out[:best_pos] + name + out[best_pos + len(best_win):]
            fixed.append(f"{best_win}→{name}")
    return (out, fixed) if fixed else (text, [])


# 同音误听纠正：小模型常把「十」听成「时/是/石」，「四」听成「斯/丝」。
# 仅用于分数提取前的文本规范化（此时姓名已剔除，不会伤到人名）
_MISHEAR_TO_NUM = str.maketrans({
    "时": "十", "是": "十", "石": "十", "识": "十", "拾": "十",
    "斯": "四", "丝": "四", "私": "四",
})
