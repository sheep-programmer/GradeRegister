"""应用状态机：把语音/键盘输入流转为对成绩表的操作。

阶段（phase）:
    idle    —— 等待念学生姓名
    scoring —— 已选中学生，等待念各题分数（数字自动往后填）
    total   —— 等待念总分，与软件计算值核对

任何阶段念到其他学生的名字（非数字文本匹配成功）都会切回对应学生。
"""
from __future__ import annotations

import re

from dataclasses import dataclass, field
from typing import List, Optional

from . import parser
from .excel_io import SheetModel, StudentRecord

# 单题分数的上限。识别偶尔会把一串数字连成「99999999999999999999」，
# 填进去会毁掉总分和整张表的可读性，而老师未必留意到多了十几位
MAX_SCORE = 1000.0


@dataclass
class ActionResult:
    """一次输入的处理结果，供 UI 展示/弹出对话框。"""
    message: str = ""
    new_phase: str = "idle"
    select_choices: Optional[List[tuple]] = None   # [(row, name)] 重名/候选选择
    ok: bool = True
    heard_text: Optional[str] = None   # 纠正后实际采用的文本，供界面显示
    sound: Optional[str] = None        # 指定提示音；None 表示按 ok 取默认


@dataclass
class AppState:
    model: Optional[SheetModel] = None
    cfg: dict = field(default_factory=dict)
    phase: str = "idle"                  # idle | scoring | total
    current: Optional[StudentRecord] = None
    # 一次操作一组，整组一起撤销：[[(stu, col, old_val|None), ...], ...]
    undo_stack: List[List[tuple]] = field(default_factory=list)
    _last_edit_col: Optional[int] = field(default=None, repr=False)  # 最近填/改的题列
    _pending_q: Optional[float] = field(default=None, repr=False)    # 念了题号还没念分
    # 听不清是谁时列出的候选，等老师用一句「第一个」来定
    _pending_choices: Optional[List[tuple]] = field(default=None, repr=False)
    # 同名多位、等老师选人时暂存的整句，选完照它把分数与总分补上
    _pending_line: Optional[str] = field(default=None, repr=False)
    # 最近一次自动保存的失败原因，供界面提示；成功则回到 None
    save_error: Optional[str] = field(default=None, repr=False)

    # ---------------- 加载 ----------------
    def load(self, model: SheetModel) -> None:
        self.model = model
        self.phase = "idle"
        self.current = None
        self.undo_stack.clear()
        self.save_error = None
        # 换表比换学生更彻底，逐项残留都会让新表出现莫名其妙的行为：
        # 悬着的题号会把下一个分数填到错的那一题，旧的编辑列会在新表上
        # 亮起一个从没填过的格子
        self._clear_pending()
        # 待补整句是「选完人就接着填」的跨激活传递，不属于切学生要清的那组，
        # 但换表必须清——它指的是旧表里的学生
        self._pending_line = None

    def _clear_pending(self) -> None:
        """清掉只对「当前这一位学生」有意义的临时状态。

        切学生与切表格都要清同一组字段。分散在两处写迟早会漏一个，
        所以收在这里。
        """
        self._pending_choices = None
        self._pending_q = None
        self._last_edit_col = None

    def student_no(self, stu: StudentRecord) -> int:
        """学生在名单里的第几位（1 起）。

        界面与语音一律用这个序号，不用 Excel 行号：老师念「第五个」指的
        是名单第五位，表头行、空行让两者错开，对不上就会点错人。
        """
        if self.model is None:
            return 0
        for i, s in enumerate(self.model.students, start=1):
            if s.row == stu.row:
                return i
        return 0

    def _score_headers(self) -> List[str]:
        """各分数列的表头文字，顺序与 score_cols 一致。"""
        if self.model is None:
            return []
        return [self.model.header[c] if c < len(self.model.header) else ""
                for c in self.model.score_cols]

    def _score_kw(self) -> dict:
        """分数解析参数：剥离开关 + 表头名（念表头等同于念题号）。"""
        return dict(strip_prefix=self.cfg.get("strip_prefix", True),
                    strip_suffix=self.cfg.get("strip_suffix", True),
                    headers=self._score_headers())

    def _unfinished_names(self) -> set:
        """还没录完的学生姓名，供拼音裁决时优先。"""
        if self.model is None:
            return set()
        return {s.name for s in self.model.students
                if self.model.filled_count(s) < self.model.score_count}

    # ---------------- 按号码选择 ----------------
    def select_by_id(self, id_text: str) -> ActionResult:
        """按学号或名单序号选择学生。

        表里有学号列就比对学号原文，没有就当成名单里的第几位。
        号码不存在时明确报错——念了 20 号而班上只有 12 人是笔误，
        退回去猜名字只会填到别人头上。
        """
        assert self.model is not None
        num = parser.any_number_to_float(id_text)
        if num is None:
            return ActionResult(message=f"没听清号码「{id_text}」", ok=False)
        digits = str(int(num))
        if self.model.id_col >= 0:
            hits = [s for s in self.model.students
                    if s.sid and s.sid.lstrip("0") == digits.lstrip("0")]
            if len(hits) == 1:
                return self._activate(hits[0].name, hits[0].row, exact=True)
            if len(hits) > 1:
                return ActionResult(
                    message=f"学号 {digits} 对应多位学生，请点选",
                    select_choices=[(s.row, s.name) for s in hits],
                    new_phase="idle")
            return ActionResult(message=f"名单里没有学号 {digits}", ok=False)
        idx = int(num) - 1        # 没有学号列：号码就是名单里的第几位
        if not (0 <= idx < len(self.model.students)):
            return ActionResult(
                message=f"名单里只有 {len(self.model.students)} 位学生，"
                        f"没有 {digits} 号",
                ok=False)
        stu = self.model.students[idx]
        return self._activate(stu.name, stu.row, exact=True)

    # ---------------- 下一位学生 ----------------
    def _next_unfinished(self, after: Optional[StudentRecord] = None):
        """after 之后第一个还没录完的学生（绕回名单开头找）；没有返回 None。"""
        if self.model is None or not self.model.students:
            return None
        students = self.model.students
        start = 0
        if after is not None:
            cur = next((i for i, s in enumerate(students) if s.row == after.row),
                       -1)
            start = cur + 1
        for off in range(len(students)):
            stu = students[(start + off) % len(students)]
            if self.model.filled_count(stu) < self.model.score_count:
                return stu
        return None

    def next_student(self) -> ActionResult:
        """切到名单里下一位还没录完的学生；全录完给出提示。"""
        if self.model is None:
            return ActionResult(message="还没有打开表格", ok=False)
        stu = self._next_unfinished(after=self.current)
        if stu is None:
            return ActionResult(
                message="全班都已录完，可以点「保存」或直接关闭程序", ok=False)
        return self._activate(stu.name, stu.row, exact=True)

    # ---------------- 学生选择 ----------------
    def select_student(self, text: str) -> ActionResult:
        """按名字选择学生；返回可能的重名/候选选择项。

        纯数字文本（如「十三」）绝不当作人名——防止共享「三」字误切学生。
        """
        assert self.model is not None
        if parser.is_number_text(text):
            return ActionResult(
                message="没听清：请念「第X题」＋「XX分」，例如「第三题十三分」"
                        "（分数后面要带「分」字）",
                ok=False)
        rows = parser.find_student_rows(
            text, [(s.row, s.name) for s in self.model.students],
            cutoff=self.cfg.get("score_cutoff", 0.55))
        if not rows:
            # 与其让老师重念，不如把发音最像的两位列出来点一下——
            # 实测「没听清」的情形里，正确答案七成就在这两个里面
            cands = parser.pinyin_candidates(
                text, [s.name for s in self.model.students])
            if cands:
                picks = [(s.row, s.name) for s in self.model.students
                         if s.name in cands]
                picks.sort(key=lambda rn: cands.index(rn[1]))
                self._pending_choices = picks
                listed = "  ".join(f"{i + 1} {n}"
                                   for i, (_r, n) in enumerate(picks))
                return ActionResult(
                    message=f"没太听清，是这几位吗：{listed}"
                            "（说「第一个」或直接点一下）",
                    select_choices=picks, new_phase="idle", ok=False)
            return ActionResult(
                message=f"没找到学生「{parser.clean_name_text(text)}」",
                ok=False)
        unique_names = sorted({name for _, name in rows})
        if len(unique_names) == 1 and len(rows) == 1:
            return self._activate(rows[0][1], rows[0][0], exact=True)
        # 重名（同名多行）或单名但多行 -> 弹窗选择
        return ActionResult(
            message="请选择学生",
            select_choices=rows,
            new_phase="idle",
        )

    def activate_row(self, row: int) -> ActionResult:
        """弹窗确认后激活指定行；有暂存的整句就顺势补完。"""
        assert self.model is not None
        for s in self.model.students:
            if s.row == row:
                act = self._activate(s.name, s.row, exact=False)
                line, self._pending_line = self._pending_line, None
                if line and act.ok:
                    done = self._apply_scored_line(line)
                    done.message = f"{act.message}；{done.message}"
                    return done
                return act
        return ActionResult(message="选择无效", ok=False)

    def _activate(self, name: str, row: int, exact: bool) -> ActionResult:
        assert self.model is not None
        for s in self.model.students:
            if s.row == row:
                self.current = s
                self.phase = "scoring"
                # 已经定了人：旧候选作废、旧格子高亮清掉、
                # 上一位没念完的题号不带过来
                self._clear_pending()
                rest = self.model.score_count - self.model.filled_count(s)
                # 行号跟 Excel 对得上，序号是念「第几个」用的，两个都写出来
                msg = (f"已选中：{name}（第{row + 1}行 · 第{self.student_no(s)}个）"
                       + (f"，还有 {rest} 题未填，请念分数" if rest > 0
                          else "，各题已填完，可核对总分或念下一位"))
                return ActionResult(message=msg, new_phase="scoring")
        return ActionResult(message=f"找不到行 {row}", ok=False)

    # ---------------- 分数 ----------------
    def add_scores(self, text: str) -> ActionResult:
        """把识别文本中的分数依次填入当前学生未填的题。

        连续听写常见念法「张三 十八 十二 十五」一句话带名字：
        先切换到该生，再把名字后面的分数直接填上。
        """
        assert self.model is not None
        # 「第三题」这类只有题号的句子不能进姓名匹配，
        # 否则「三」会把当前学生切成「张三」
        items = parser.extract_score_items(text, **self._score_kw())
        if items and all(v is None for _q, v in items):
            if self.current is None:
                return ActionResult(
                    message="还没选中学生，先念名字或号码（例如「张三」「5号」）",
                    ok=False)
            return self._fill_scores(text)

        # 整句本身能匹配到学生名字（如"李四"里的"四"会被当数字）-> 优先按名字处理。
        # 但纯数字文本（如「十三」）绝不能当名字，直接按分数处理
        names = [s.name for s in self.model.students]
        if not parser.is_number_text(text):
            rows = parser.find_student_rows(
                text, [(s.row, s.name) for s in self.model.students],
                cutoff=self.cfg.get("score_cutoff", 0.55))
        else:
            rows = []
        if rows:
            # 剔除姓名后的剩余文本若还有分数，说明是「名字+分数」连说
            rest = parser.remove_matched_names(text, names)
            has_scores = bool(parser.extract_scores(
                rest,
                strip_prefix=self.cfg.get("strip_prefix", True),
                strip_suffix=self.cfg.get("strip_suffix", True)))
            if not has_scores:
                return self.select_student(text)  # 纯名字：只切换学生
            act = self.select_student(text)
            if act.select_choices or not act.ok:
                return act  # 重名弹窗/没找到：先不填分
            fill = self._fill_scores(rest)
            act.message += f"；{fill.message}"
            act.ok = fill.ok
            return act

        if self.current is None:
            # 既没匹配到名字，也还没有当前学生：当作在念名字，由它给出提示
            return self.select_student(text)
        return self._fill_scores(text)

    def _fill_scores(self, text: str) -> ActionResult:
        """纯分数填充（不含学生切换）。

        「第三题九分」定向填第三题，已有分则覆盖；「九分」这类不带题号的
        分数顺序填入下一道空题；各题都填满后再念不带题号的分数无处可放，
        提示补念题号，绝不猜位置。
        只念了「第三题」还没念分数时记住题号，下一个分数补到那一题上，
        中间隔一句也算。
        """
        items = parser.extract_score_items(text, **self._score_kw())
        scores = [v for _q, v in items if v is not None]
        if not scores:
            heads = [q for q, v in items if v is None and q is not None]
            if heads:
                # 只念了题号：记下来，等下一句的分数
                self._pending_q = heads[-1]
                col = self._col_of_question(heads[-1])
                label = (self._question_label(col) if col is not None
                         else f"第{int(heads[-1])}题")
                return ActionResult(
                    message=f"{self.current.name} 记下{label}，请念这一题的分数",
                    new_phase="scoring")
            # 先试是否在念下一个学生的名字
            act = self.select_student(text)
            if act.ok or act.select_choices:
                return act
            if re.search(r"[零一二两三四五六七八九十百\d]", text):
                return ActionResult(
                    message="没听清：请念「第X题」＋「XX分」，或连念「3分4分5分」"
                            "（可用「加」分隔，如「10加5加5」；分数要带「分」字）",
                    ok=False)
            return act
        stu = self.current

        filled, fixed_cnt, skipped = [], 0, 0
        pending_q = self._pending_q      # 上一句只念了题号，分数在这一句
        self._pending_q = None
        dropped_no_q = 0   # 没带题号、且已无空题可填而被丢弃的分数
        changes: List[tuple] = []        # 这一句的全部改动，整组一次撤销
        absurd = 0     # 大到不可能是分数、只能是听错的值
        for q, v in items:
            if v is None:
                pending_q = q   # 只有题头：记住，等下一个分数补上
                continue
            if v > MAX_SCORE:
                # 识别把一串数字连成了「99999999999」这种。填进去会把总分
                # 和整张表搞得没法看，而且老师未必留意到多了几位
                absurd += 1
                continue
            if q is None and pending_q is not None:
                q, pending_q = pending_q, None
            if q is not None:
                target = self._col_of_question(q)
                if target is None:
                    skipped += 1    # 题号超出表格范围（如念了第八题但只有4题）
                    continue
            else:
                # 无题号但带「分」（或用「加」分隔）：顺序填下一个空题
                blanks = [c for c in self.model.score_cols if c not in stu.scores]
                if not blanks:
                    dropped_no_q += 1
                    continue
                target = blanks[0]
            old = stu.scores.get(target)
            if old is not None and abs(old - float(v)) < 1e-9:
                continue          # 重念了同一个分数，什么都没变
            if old is None:
                changes.append((stu, target, None))
                filled.append(target)
            else:
                # 重说某题 = 修正并覆盖（可撤销）
                changes.append((stu, target, old))
                fixed_cnt += 1
                print(f"[voice] 修正：{stu.name} {self._question_label(target)} "
                      f"{old:g} -> {v:g}", flush=True)
            stu.scores[target] = float(v)
            self._last_edit_col = target   # 供界面把该格框起来

        if changes:
            self.undo_stack.append(changes)
        if filled or fixed_cnt:
            stu.checked = None   # 分数变了，上一轮的核对结论作废
            stu.spoken_total = None
        stu.total = self.model.calc_total(stu)
        blanks_left = [c for c in self.model.score_cols if c not in stu.scores]

        # 没带题号的分数无处可填（各题已满）：提示补念题号覆盖，绝不乱填
        if not filled and not fixed_cnt and dropped_no_q and pending_q is None:
            self.phase = "scoring"
            # 举例用这张表真实的表头，表头叫「程序题」时不该教老师念「第三题」
            example = (self._question_label(self.model.score_cols[-1])
                       if self.model.score_cols else "第三题")
            return ActionResult(
                message=f"{stu.name} 各题都已填过，要改分请带上题号，"
                        f"例如「{example}九分」",
                ok=False)

        if pending_q is not None and blanks_left:
            self.phase = "scoring"
            self._pending_q = pending_q      # 分数下一句才来
            extra = (f"；另有 {dropped_no_q} 个分数没地方填（各题已满）"
                     if dropped_no_q else "")
            col = self._col_of_question(pending_q)
            label = (self._question_label(col) if col is not None
                     else f"第{int(pending_q)}题")
            if changes:
                self._maybe_autosave("score")
            return ActionResult(
                message=f"{stu.name} 已填 {len(filled)} 题，还剩 {len(blanks_left)} 题"
                        f"（「{label}」的分数还没听到，请继续念）{extra}",
                new_phase=self.phase)

        parts = [f"已填 {len(filled)} 题"]
        if fixed_cnt:
            parts.append(f"已修正 {fixed_cnt} 题")
        if blanks_left:
            parts.append(f"还剩 {len(blanks_left)} 题，请继续念")
        else:
            parts.append(f"已全部填完，总分 = {stu.total:g}，"
                         f"可念「总分 {stu.total:g}」核对，或直接念下一位学生")
        if skipped:
            parts.append(f"{skipped} 个分数的题号超出范围，已忽略")
        if absurd:
            parts.append(f"{absurd} 个数字大于 {MAX_SCORE:g}，不像分数，已忽略")
        if dropped_no_q:
            parts.append(f"{dropped_no_q} 个分数没地方填（各题已满），"
                         "要改分请带上题号")
        self._maybe_autosave("student" if not blanks_left else "score")
        return ActionResult(message=f"{stu.name} " + "；".join(parts),
                            new_phase="scoring")

    # ---------------- 总分核对 ----------------
    def check_total(self, text: str) -> ActionResult:
        """听到的总分与软件计算值对比；不一致记录到核对列。"""
        assert self.model is not None
        if self.current is None:
            return self.select_student(text)
        kw = dict(strip_prefix=self.cfg.get("strip_prefix", True),
                  strip_suffix=self.cfg.get("strip_suffix", True))
        # 只在关键词之后找数字：「一共92分」的「一」不能被当成总分
        _head, tail = parser.split_at_total(
            text, [s.name for s in self.model.students])
        scores = parser.extract_scores(tail, **kw) if tail else []
        if not scores:
            scores = parser.extract_scores(text, **kw)
        if not scores:
            return ActionResult(message="没听清总分，请再说一次（例如：总分 62）", ok=False)

        stu = self.current
        spoken = scores[-1]          # 取最后一个数字作为总分
        expected = stu.total if stu.total is not None else self.model.calc_total(stu)
        same = abs(spoken - expected) < 1e-9
        stu.checked = same
        if same:
            stu.spoken_total = None
            msg = f"✓ 一致：{stu.name} 总分 {expected:g}，核对完成"
            self.phase = "idle"
            # 设置里开了「录完自动切下一位」：直接激活名单里下一位未录完的
            if self.cfg.get("auto_next"):
                nxt = self._next_unfinished(after=stu)
                if nxt is not None:
                    self._activate(nxt.name, nxt.row, exact=True)
                    msg += f"；已自动切到下一位：{nxt.name}"
            sound, ok = "success", True
        else:
            # 报的数留在记录里，核对列会连差值一起写出来
            stu.spoken_total = spoken
            diff = spoken - expected
            msg = (f"✗ 不一致：你说的 {spoken:g}，软件计算 {expected:g}"
                   f"（差 {diff:+g}），已在该行「核对」列标红，可再核实")
            self.phase = "idle"
            sound, ok = "error", False
        # 核对通过是最靠后的时机；不一致时按「一位学生录完」算，
        # checked 模式下就不写盘——老师要的是只把对得上的结果同步出去
        self._maybe_autosave("checked" if same else "student")
        return ActionResult(message=msg, new_phase=self.phase, ok=ok,
                            sound=sound)

    # ---------------- 整句归属别的学生 ----------------
    def _other_student_in(self, head: str) -> bool:
        """总分关键词之前的那段，念的是不是当前学生以外的某个人。"""
        if self.model is None or not head.strip():
            return False
        if parser.is_number_text(head):
            return False
        rows = parser.find_student_rows(
            head, [(s.row, s.name) for s in self.model.students],
            cutoff=self.cfg.get("score_cutoff", 0.55))
        if not rows:
            return False
        cur_row = self.current.row if self.current else None
        return any(row != cur_row for row, _name in rows)

    def _scored_line_for_other(self, text: str, head: str) -> ActionResult:
        """处理「别人的名字 + 各题分数 + 总分」这一整句。"""
        act = self.select_student(head)
        if act.select_choices or not act.ok:
            # 同名多位或没听清：先让老师定人，这一句留着，选完再照它补
            self._pending_line = text
            act.message += "（选定后会自动补上这一句的分数与总分）"
            return act
        return self._apply_scored_line(text)

    def _apply_scored_line(self, text: str) -> ActionResult:
        """当前学生已定，把这一句的各题分数填上再核对总分。"""
        assert self.model is not None
        names = [s.name for s in self.model.students]
        head, _tail = parser.split_at_total(text, names)
        rest = parser.remove_matched_names(head, names)
        kw = dict(strip_prefix=self.cfg.get("strip_prefix", True),
                  strip_suffix=self.cfg.get("strip_suffix", True))
        parts = []
        if rest.strip() and parser.extract_scores(rest, **kw):
            parts.append(self._fill_scores(rest).message)
        act = self.check_total(text)
        if parts:
            act.message = "；".join(parts + [act.message])
        return act

    def pending_col(self) -> Optional[int]:
        """已念题号、还等着分数的那一列，供界面把格子标出来。"""
        if self._pending_q is None:
            return None
        return self._col_of_question(self._pending_q)

    def _question_no(self, col: int) -> int:
        """题号列的列索引转成老师看得懂的题号（第几题）。"""
        if self.model is not None and col in self.model.score_cols:
            return self.model.score_cols.index(col) + 1
        return col + 1

    def _question_label(self, col: int) -> str:
        """题目在提示里的叫法：表头有文字就用表头，否则「第X题」。"""
        if self.model is not None and 0 <= col < len(self.model.header):
            head = self.model.header[col].strip()
            if head:
                return head
        return f"第{self._question_no(col)}题"

    def _col_of_question(self, q: float) -> Optional[int]:
        """1-based 题号转列索引，超出表格范围返回 None。"""
        if self.model is None:
            return None
        idx = int(q) - 1
        if 0 <= idx < len(self.model.score_cols):
            return self.model.score_cols[idx]
        return None

    # ---------------- 清除 ----------------
    def clear_cells(self, targets: List[tuple]) -> ActionResult:
        """清除若干 (学生, 题号列) 的分数，整批算一次操作、一次撤销。"""
        if self.model is None:
            return ActionResult(message="还没有打开表格", ok=False)
        changes: List[tuple] = []
        touched = []
        for stu, col in targets:
            if col not in self.model.score_cols:
                continue          # 姓名/总分/核对列不是分数，不清
            old = stu.scores.get(col)
            if old is None:
                continue
            changes.append((stu, col, old))
            stu.scores.pop(col, None)
            if stu not in touched:
                touched.append(stu)
        if not changes:
            return ActionResult(message="选中的格子里没有分数可清除", ok=False)
        for stu in touched:
            stu.total = self.model.calc_total(stu)
            stu.checked = None    # 分数变了，上一轮的核对结论作废
            stu.spoken_total = None
        self.undo_stack.append(changes)
        self._last_edit_col = changes[-1][1]
        self._maybe_autosave("score")
        if len(changes) == 1:
            stu, col, _old = changes[0]
            msg = f"已清除 {stu.name} 的{self._question_label(col)}（可以按撤销找回）"
        elif len(touched) == 1:
            msg = (f"已清除 {touched[0].name} 的 {len(changes)} 个分数"
                   "（可以按撤销找回）")
        else:
            msg = (f"已清除 {len(touched)} 位学生共 {len(changes)} 个分数"
                   "（可以按撤销找回）")
        return ActionResult(message=msg, new_phase=self.phase)

    # ---------------- 撤销 / 保存 ----------------
    def undo(self) -> ActionResult:
        if not self.undo_stack:
            return ActionResult(message="没有可撤销的操作", ok=False)
        group = self.undo_stack.pop()
        touched = []
        for stu, col, old in reversed(group):
            if old is None:
                stu.scores.pop(col, None)
            else:
                stu.scores[col] = old
            if stu not in touched:
                touched.append(stu)
        for stu in touched:
            stu.total = self.model.calc_total(stu) if self.model else None
            stu.checked = None   # 分数被改回去了，核对结论同样作废
            stu.spoken_total = None
        self._last_edit_col = group[-1][1]
        if len(group) == 1:
            stu, col, _old = group[0]
            msg = f"已撤销 {stu.name} 的{self._question_label(col)}"
        elif len(touched) == 1:
            msg = f"已撤销 {touched[0].name} 的 {len(group)} 处改动"
        else:
            msg = f"已撤销 {len(touched)} 位学生共 {len(group)} 处改动"
        return ActionResult(message=msg, new_phase="scoring")

    # 写盘时机的严格程度。事件的级别 ≥ 模式要求的级别才落盘，
    # 所以「核对通过」这种最靠后的事件在任何非手动模式下都会写
    _SAVE_LEVELS = {"score": 0, "student": 1, "checked": 2}

    def auto_save_mode(self) -> str:
        """score=每填一个分数存 | student=每录完一位存 | checked=核对通过才存
        | manual=只手动存。"""
        mode = self.cfg.get("auto_save_mode")
        if mode in ("score", "student", "checked", "manual"):
            return mode
        return "score" if self.cfg.get("auto_save", True) else "manual"

    def _maybe_autosave(self, event: str = "score") -> None:
        """event 是这次触发的时机：score=填了分数，student=一位学生录完，
        checked=总分核对通过。"""
        mode = self.auto_save_mode()
        if mode == "manual" or self.model is None:
            return
        if self._SAVE_LEVELS.get(event, 0) < self._SAVE_LEVELS.get(mode, 0):
            return
        try:
            from .excel_io import save_sheet
            save_sheet(self.model, self.cfg,
                       write_formula=self.cfg.get("write_formula", True))
            self.save_error = None
        except PermissionError:
            # Excel 开着同一份表时会锁住文件。只打日志的话老师毫无察觉，
            # 一节课的分数全留在内存里，关掉程序就没了
            self.save_error = ("自动保存失败：表格正被 Excel 占用。"
                               "请关掉 Excel，或先点「保存」确认能写入")
            print(f"[warn] {self.save_error}", flush=True)
        except Exception as e:  # noqa: BLE001
            self.save_error = f"自动保存失败：{e}"
            print(f"[warn] {self.save_error}", flush=True)

    # ---------------- 统一输入入口 ----------------
    def handle_text(self, text: str) -> ActionResult:
        """按当前阶段处理一段识别文本（供语音与键盘共用）。"""
        text = (text or "").strip()
        if not text:
            return ActionResult(message="没听清，请再说一次", ok=False)
        # 短音频常被吐成「李四李四李四」，先折叠再匹配，否则整串拼音跟谁都不像
        text = parser.collapse_repeats(text)
        # 上一句留下的待补内容只对「紧接着选人」有效。老师改口说了别的，
        # 那句就作废——否则等会儿随手点一行会莫名其妙被填上一堆旧分数
        self._pending_line = None

        names = [s.name for s in self.model.students] if self.model else []

        # 上一句给了候选，这一句若是「第一个」就照它选人。
        # 必须赶在号码定位之前——「第一个」同时也是选号码的说法
        if self._pending_choices:
            idx = parser.parse_choice_index(text)
            choices = self._pending_choices
            if idx is not None:
                if 0 <= idx < len(choices):
                    self._pending_choices = None
                    act = self.activate_row(choices[idx][0])
                    act.heard_text = text
                    return act
                # 说了「第三个」而只有两个候选：候选留着让老师重说一次。
                # 放行下去会被后面的号码定位当成「名单第 3 位」，
                # 一句说岔的话就把当前学生换成了毫不相干的人
                listed = "  ".join(f"{i + 1} {n}"
                                   for i, (_r, n) in enumerate(choices))
                return ActionResult(
                    message=f"只有 {len(choices)} 个候选：{listed}"
                            f"（请说「第一个」到「第{len(choices)}个」）",
                    select_choices=choices, new_phase="idle", ok=False,
                    heard_text=text)
            self._pending_choices = None

        # 「下一个/下一位/继续」：切到下一位未录完的学生。
        # 指令词不含数字，与号码定位不冲突；名单里有同音姓名时优先当名字
        if parser.is_next_student_cmd(text, names):
            act = self.next_student()
            act.heard_text = text
            return act

        # 念了号码（「5号」「第五个」）就按号码定位，剩下的文本当分数
        if self.model is not None:
            id_text, rest = parser.extract_student_id(text)
            if id_text is not None:
                act = self.select_by_id(id_text)
                if act.heard_text is None:
                    act.heard_text = text
                if not act.ok or act.select_choices or not rest.strip():
                    return act
                fill = self._fill_scores(rest)
                act.message += f"；{fill.message}"
                act.ok = fill.ok
                return act

        # 任何阶段提到"总分"且当前有学生 -> 进入总分核对。
        # 但老师常一句话连念「…七分 总分 92」：先把"总"之前的分数填上，
        # 再核对总分，否则整句进核对、前面的分数全被扔掉。
        # 这一判断必须赶在姓名纠错之前：听岔的「钟分」会被纠错改写成名单里
        # 的同音姓名，改完就再也认不出这是在念总分了
        if self.model is not None and parser.find_total_keyword(text, names):
            head, _tail = parser.split_at_total(text, names)
            kw = dict(strip_prefix=self.cfg.get("strip_prefix", True),
                      strip_suffix=self.cfg.get("strip_suffix", True))
            # 句首念的是别人的名字：整句「张三…总分90」是那位学生的一条完整
            # 记录，绝不能拿这个 90 去核对当前学生——上一位刚核对过的结论
            # 会被改成不一致，而分数一个都没落到张三头上。
            # 这一判断不能要求「已经选中了谁」：一节课的第一句往往就是
            # 「孙丽第一题10分…总分100」，那时还没有当前学生，跳过这里的话
            # 总分会被当成分数塞进最后一题
            if self._other_student_in(head):
                return self._scored_line_for_other(text, head)
            if (self.current is not None and head and self.phase == "scoring"
                    and parser.extract_scores(head, **kw)
                    and [c for c in self.model.score_cols
                         if c not in self.current.scores]):
                act = self.add_scores(head)          # head 不含关键词，不会递归
                act2 = self.check_total(text)
                act.message += f"；{act2.message}"
                # 这一句的结论由核对决定：前半段填分成功不能盖掉核对不一致
                act.ok = act2.ok
                act.sound = act2.sound
                return act
            if self.current is not None:
                return self.check_total(text)

        # 拼音级纠错：把「掌声」「成序题」这类同音误听纠正回名单/表头里的原词。
        # 表头先纠：它是固定的几个词，比姓名更容易判准，纠完剩下的才当人名猜
        if self.model is not None:
            corrected, fixed = parser.correct_headers_in_text(
                text, self._score_headers(), names)
            if fixed:
                print(f"[voice] 表头纠正: {fixed}", flush=True)
                text = corrected
            corrected, fixed = parser.correct_names_in_text(
                text, names, self._unfinished_names())
            if fixed:
                print(f"[voice] 姓名纠正: {fixed}", flush=True)
                text = corrected

        if self.phase == "idle":
            # 走 add_scores 而不是 select_student：老师常一句话连着念
            # 「赵磊第一题十分第二题十二分」，只认名字会把分数全丢掉
            result = self.add_scores(text)
        elif self.phase == "scoring":
            result = self.add_scores(text)
        elif self.phase == "total":
            result = self.check_total(text)
        else:
            result = self.select_student(text)
        # 界面「听到」区显示纠正后的文本，不再留着听错的原词
        if result.heard_text is None:
            result.heard_text = text
        return result