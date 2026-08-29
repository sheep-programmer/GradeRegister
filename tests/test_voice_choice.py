"""拿不准就列候选 + 用语音选人。

实测：听不清时正确答案有七成落在发音最像的前两名。与其丢掉让老师重念，
不如列出来让他一句话选——软件不猜，所以不会把分数填到别人那行。
运行：python -m unittest tests.test_voice_choice -v
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from grade_app import parser
from grade_app.excel_io import load_sheet
from grade_app.state import AppState
from openpyxl import Workbook

CFG = {"strip_prefix": True, "strip_suffix": True, "score_cutoff": 0.55,
       "write_formula": True, "write_checked": True, "auto_save": False}


class TestPinyinCandidates(unittest.TestCase):
    NAMES = ["王小明", "李四", "张伟", "刘洋", "陈静", "张三",
             "赵磊", "孙丽", "周杰", "吴敏", "郑浩"]

    def test_lists_the_right_person_first(self):
        for got, want in (("村你", "孙丽"), ("又雷", "赵磊"), ("雷飞", "李四")):
            cands = parser.pinyin_candidates(got, self.NAMES)
            self.assertIn(want, cands, f"{got} 的候选里应该有 {want}：{cands}")

    def test_limited_length(self):
        cands = parser.pinyin_candidates("村你", self.NAMES, limit=2)
        self.assertLessEqual(len(cands), 2)

    def test_garbage_yields_nothing(self):
        """识别成完全不沾边的东西时，别硬凑候选去烦老师。"""
        self.assertEqual(parser.pinyin_candidates("今天天气不错", self.NAMES), [])

    def test_ordered_by_similarity(self):
        cands = parser.pinyin_candidates("村你", self.NAMES, limit=3)
        sims = [parser.pinyin_similarity("村你", c) for c in cands]
        self.assertEqual(sims, sorted(sims, reverse=True))

    def test_empty_inputs(self):
        self.assertEqual(parser.pinyin_candidates("", self.NAMES), [])
        self.assertEqual(parser.pinyin_candidates("村你", []), [])


class TestChoiceByVoice(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "t.xlsx")
        wb = Workbook()
        ws = wb.active
        ws.append(["姓名", "第一题", "第二题", "总分"])
        for n in ("吴敏", "王五", "张三"):
            ws.append([n, "", "", ""])
        wb.save(self.path)
        self.state = AppState(cfg=CFG)
        self.state.load(load_sheet(self.path, CFG, backup=False))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unclear_name_offers_choices(self):
        r = self.state.handle_text("午门儿")
        self.assertTrue(r.select_choices, "听不清时应该给候选而不是直接放弃")
        self.assertIsNone(self.state.current)

    def test_pick_first_by_voice(self):
        r = self.state.handle_text("午门儿")
        first = r.select_choices[0][1]
        self.state.handle_text("第一个")
        self.assertEqual(self.state.current.name, first)

    def test_pick_second_by_voice(self):
        r = self.state.handle_text("午门儿")
        if len(r.select_choices) < 2:
            self.skipTest("这条音频只有一个候选")
        second = r.select_choices[1][1]
        self.state.handle_text("第二个")
        self.assertEqual(self.state.current.name, second)

    def test_plain_number_also_picks(self):
        r = self.state.handle_text("午门儿")
        first = r.select_choices[0][1]
        self.state.handle_text("一")
        self.assertEqual(self.state.current.name, first)

    def test_without_pending_it_means_the_nth_student(self):
        """没有候选在等时，「第一个」回归号码语义，指名单第一位。"""
        self.state.handle_text("第一个")
        self.assertEqual(self.state.current.name,
                         self.state.model.students[0].name)

    def test_saying_a_name_instead_cancels_choices(self):
        """老师直接改念另一个名字，就按名字走，别还惦记着候选。"""
        self.state.handle_text("午门儿")
        self.state.handle_text("张三")
        self.assertEqual(self.state.current.name, "张三")

    def test_choice_is_consumed_once(self):
        """候选用掉就作废，不会被后面的话重复消费。"""
        r = self.state.handle_text("午门儿")
        self.assertTrue(r.select_choices)
        self.state.handle_text("第一个")
        self.assertIsNone(self.state._pending_choices)

    def test_choices_cleared_when_name_recognised(self):
        """候选还挂着时直接念对了名字，候选要一并清掉。"""
        self.state.handle_text("午门儿")
        self.assertIsNotNone(self.state._pending_choices)
        self.state.handle_text("张三")
        self.assertEqual(self.state.current.name, "张三")
        self.assertIsNone(self.state._pending_choices)

    def test_scores_still_work_after_choosing(self):
        self.state.handle_text("午门儿")
        self.state.handle_text("第一个")
        self.state.handle_text("第一题十分")
        self.assertEqual(self.state.current.scores.get(1), 10.0)


if __name__ == "__main__":
    unittest.main()
