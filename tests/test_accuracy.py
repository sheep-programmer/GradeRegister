"""端到端准确率语料：一句真实念法进去，看落到表里的结果对不对。

每条用例是「老师会怎么念」＋「表里该变成什么」，覆盖选人、填分、核对、
切换、同音误听与噪声。加新用例只需往 CASES 里追加一行。
运行：python -m unittest tests.test_accuracy -v
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from openpyxl import Workbook

from grade_app.excel_io import load_sheet
from grade_app.state import AppState

NAMES = ["王小明", "李四", "张伟", "刘洋", "陈静", "张三", "赵磊", "孙丽",
         "周杰", "吴敏", "郑浩", "刘阳"]
QUESTIONS = 4


def _current(st):
    return st.current.name if st.current else None


def _scores(st, name):
    stu = next((s for s in st.model.students if s.name == name), None)
    if stu is None:
        return None
    return [stu.scores.get(c) for c in st.model.score_cols]


def _checked(st, name):
    return next(s for s in st.model.students if s.name == name).checked


# (分类, 依次念的话, 期望)
CASES = [
    # ---- 选人 ----
    ("选人", ["王小明"], lambda st: _current(st) == "王小明"),
    ("选人", ["李四"], lambda st: _current(st) == "李四"),
    ("选人·唯一名直接选中", ["张三"], lambda st: _current(st) == "张三"),
    ("选人·按序号", ["第三个"], lambda st: _current(st) == "张伟"),
    ("选人·按号码", ["5号"], lambda st: _current(st) == "陈静"),
    # 同音误听要能纠回来
    ("同音·雷斯→李四", ["雷斯"], lambda st: _current(st) == "李四"),
    ("同音·五米→吴敏", ["五米"], lambda st: _current(st) == "吴敏"),
    ("同音·正好→郑浩", ["正好"], lambda st: _current(st) == "郑浩"),
    ("同音·陈净→陈静", ["陈净"], lambda st: _current(st) == "陈静"),
    ("同音·州杰→周杰", ["州杰"], lambda st: _current(st) == "周杰"),
    ("同音·孙立→孙丽", ["孙立"], lambda st: _current(st) == "孙丽"),
    # 分不清是谁时要求确认，不替老师猜
    ("歧义·刘扬（刘洋/刘阳）", ["刘扬"], lambda st: _current(st) is None),
    ("歧义·掌声（张三/张伟）", ["掌声"], lambda st: _current(st) is None),
    # ---- 填分 ----
    ("填分·带题号", ["王小明", "第一题18分"],
     lambda st: _scores(st, "王小明") == [18, None, None, None]),
    ("填分·连念四题", ["王小明", "第一题10分第二题20分第三题30分第四题40分"],
     lambda st: _scores(st, "王小明") == [10, 20, 30, 40]),
    ("填分·不带题号顺序填", ["王小明", "18分", "12分"],
     lambda st: _scores(st, "王小明") == [18, 12, None, None]),
    ("填分·加号分隔", ["王小明", "10加5加5"],
     lambda st: _scores(st, "王小明") == [10, 5, 5, None]),
    ("填分·中文数字", ["王小明", "第一题二十五分"],
     lambda st: _scores(st, "王小明") == [25, None, None, None]),
    ("填分·中文小数", ["王小明", "第一题十二点五分"],
     lambda st: _scores(st, "王小明") == [12.5, None, None, None]),
    ("填分·零分", ["王小明", "第一题零分"],
     lambda st: _scores(st, "王小明") == [0, None, None, None]),
    ("填分·一百分", ["王小明", "第一题一百分"],
     lambda st: _scores(st, "王小明") == [100, None, None, None]),
    ("填分·题号在上一句", ["王小明", "第三题", "九分"],
     lambda st: _scores(st, "王小明") == [None, None, 9, None]),
    ("填分·重念覆盖", ["王小明", "第一题10分", "第一题20分"],
     lambda st: _scores(st, "王小明") == [20, None, None, None]),
    ("同音·「时八分」→18", ["王小明", "第一题时八分"],
     lambda st: _scores(st, "王小明") == [18, None, None, None]),
    ("同音·「气氛」→7分", ["王小明", "第一题气氛"],
     lambda st: _scores(st, "王小明") == [7, None, None, None]),
    ("同音·「三粉」→3分", ["王小明", "第一题三粉"],
     lambda st: _scores(st, "王小明") == [3, None, None, None]),
    ("同音·「第一体」→第一题", ["王小明", "第一体18分"],
     lambda st: _scores(st, "王小明") == [18, None, None, None]),
    # ---- 名字与分数连说 ----
    ("连说·名字加一题", ["赵磊第一题15分"],
     lambda st: _current(st) == "赵磊"
     and _scores(st, "赵磊") == [15, None, None, None]),
    ("连说·名字加整行", ["孙丽第一题10分第二题20分第三题30分第四题40分"],
     lambda st: _scores(st, "孙丽") == [10, 20, 30, 40]),
    # 一节课的第一句往往就是这种：那时还没有当前学生
    ("连说·整行加总分（开场第一句）",
     ["孙丽第一题10分第二题20分第三题30分第四题40分总分100"],
     lambda st: _scores(st, "孙丽") == [10, 20, 30, 40]
     and _checked(st, "孙丽") is True),
    ("连说·换人不牵连上一位",
     ["王小明", "第一题10分第二题20分", "总分30",
      "孙丽第一题10分第二题20分第三题30分第四题40分总分100"],
     lambda st: _checked(st, "王小明") is True
     and _checked(st, "孙丽") is True),
    # ---- 核对 ----
    ("核对·一致", ["王小明", "第一题10分第二题20分", "总分30"],
     lambda st: _checked(st, "王小明") is True),
    ("核对·不一致", ["王小明", "第一题10分第二题20分", "总分35"],
     lambda st: _checked(st, "王小明") is False),
    ("核对·「一共」", ["王小明", "第一题10分第二题20分", "一共30分"],
     lambda st: _checked(st, "王小明") is True),
    ("核对·同音「钟分」", ["王小明", "第一题10分第二题20分", "钟分30"],
     lambda st: _checked(st, "王小明") is True),
    ("核对·同一句填分再核对", ["王小明", "第一题10分第二题20分总分30"],
     lambda st: _checked(st, "王小明") is True),
    # ---- 切下一位 ----
    ("切换·下一个", ["王小明", "下一个"], lambda st: _current(st) == "李四"),
    ("切换·下一位", ["王小明", "下一位"], lambda st: _current(st) == "李四"),
    ("切换·继续", ["王小明", "继续"], lambda st: _current(st) == "李四"),
    # ---- 噪声不许动当前状态 ----
    ("噪声·叠字", ["王小明", "第第第第"], lambda st: _current(st) == "王小明"),
    ("噪声·闲话", ["王小明", "嗯这个不太对"],
     lambda st: _current(st) == "王小明"),
    ("噪声·空句", ["王小明", ""], lambda st: _current(st) == "王小明"),
    ("噪声·纯标点", ["王小明", "。，！"], lambda st: _current(st) == "王小明"),
]


class TestAccuracyCorpus(unittest.TestCase):
    """整份语料必须全过。掉一条就是准确率退步，不是「可以接受的波动」。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "成绩表.xlsx")
        wb = Workbook()
        ws = wb.active
        ws.append(["姓名"] + [f"第{i}题" for i in range(1, QUESTIONS + 1)]
                  + ["总分"])
        for name in NAMES:
            ws.append([name] + [None] * (QUESTIONS + 1))
        wb.save(self.path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fresh(self):
        cfg = {"auto_save_mode": "manual", "score_cutoff": 0.55,
               "strip_prefix": True, "strip_suffix": True}
        st = AppState(cfg=cfg)
        st.load(load_sheet(self.path, cfg, backup=False))
        return st

    def test_corpus(self):
        for label, utterances, expect in CASES:
            with self.subTest(label):
                st = self._fresh()
                for text in utterances:
                    st.handle_text(text)
                state = [(s.name, [s.scores.get(c) for c in st.model.score_cols],
                          s.checked)
                         for s in st.model.students
                         if s.scores or s.checked is not None]
                self.assertTrue(
                    expect(st),
                    f"念 {utterances}\n  当前学生={_current(st)}\n  表内={state}")

    def test_corpus_covers_every_area(self):
        """语料别退化成只测一个方向。"""
        areas = {label.split("·")[0] for label, _u, _e in CASES}
        for need in ("选人", "填分", "同音", "核对", "连说", "切换", "噪声",
                     "歧义"):
            self.assertIn(need, areas, f"语料缺了「{need}」这一类")


if __name__ == "__main__":
    unittest.main()
