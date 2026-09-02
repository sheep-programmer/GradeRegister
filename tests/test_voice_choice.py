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


class TestRankingReuseIsEquivalent(unittest.TestCase):
    """纠错里名次只排一次，结论必须和分开排两次完全一样。

    is_name_ambiguous 只看前两位的相似度，而 prefer 只在同分时重排顺序，
    所以前两位的分数与 prefer 无关——这条等价性是「只排一次」的前提，
    它一旦不成立，性能优化就会静默改掉纠错结果。
    """

    NAMES = ["张三", "李四", "王五", "赵磊", "刘洋", "刘阳", "纪旭", "钟芳",
             "孙丽", "陈明", "周杰", "吴敏", "欧阳娜", "司马光"]

    def _two_passes(self, token, prefer):
        if parser.is_name_ambiguous(token, self.NAMES):
            return None
        return parser.best_name_by_pinyin(token, self.NAMES, prefer)

    def _one_pass(self, token, prefer):
        ranked = parser.rank_names_by_pinyin(token, self.NAMES, prefer)
        if parser._ranking_is_ambiguous(ranked):
            return None
        return parser._best_from_ranking(ranked, prefer)

    def test_equivalent_on_real_mishearings(self):
        prefer = set(self.NAMES[:7])
        for token in ("掌声", "五米", "刘扬", "村你", "又雷", "陈净", "正好",
                      "州杰", "孙立", "雷斯", "第第", "滴滴", "欧阳那"):
            self.assertEqual(self._two_passes(token, prefer),
                             self._one_pass(token, prefer), token)

    def test_equivalent_across_random_tokens_and_preferences(self):
        import random
        chars = "张李王赵刘纪钟孙陈周吴郑欧阳娜司马光三四五磊洋旭芳丽明杰敏"
        random.seed(99)
        for _ in range(3000):
            token = "".join(random.choice(chars)
                            for _ in range(random.randint(2, 4)))
            prefer = set(random.sample(self.NAMES,
                                       random.randint(0, len(self.NAMES))))
            self.assertEqual(self._two_passes(token, prefer),
                             self._one_pass(token, prefer),
                             f"{token!r} prefer={sorted(prefer)}")

    def test_top_two_scores_ignore_the_preference(self):
        """等价性的根据：前两位的分数与 prefer 无关。"""
        for token in ("掌声", "刘扬", "村你", "欧阳那"):
            plain = parser.rank_names_by_pinyin(token, self.NAMES)
            biased = parser.rank_names_by_pinyin(token, self.NAMES,
                                                 self.NAMES[:5])
            self.assertEqual([r for _n, r in plain[:2]],
                             [r for _n, r in biased[:2]], token)


class TestRepeatedCharNoise(unittest.TestCase):
    """叠字噪声不许被纠成姓名。

    噪声与口吃经 collapse_repeats 折叠后就是叠字（「第第第第」→「第第」）。
    叠字两个音节完全相同，跟任何「声母不同、韵母相同」的姓名都算得很像
    （didi vs lisi 有 0.65），而真实误听最低只有 0.58——两段区间重叠，
    调阈值分不开，只能按结构挡。
    """

    NAMES = ["张三", "李四", "王五", "赵磊", "刘洋", "陈静",
             "孙丽", "周杰", "吴敏", "郑浩"]
    NOISE = ["第第", "第第第第", "滴滴", "地地", "题题", "你你", "米米",
             "西西", "气气", "是是", "一一", "三三", "四四", "嘀嘀", "喂喂"]

    def test_noise_is_never_rewritten_to_a_name(self):
        for word in self.NOISE:
            folded = parser.collapse_repeats(word)
            out, fixed = parser.correct_names_in_text(folded, self.NAMES)
            self.assertEqual(fixed, [], f"{word!r} 被改写成了 {out!r}")

    def test_real_mishearings_still_get_fixed(self):
        for got, want in (("掌声", "张三"), ("五米", "吴敏"), ("刘扬", "刘洋"),
                          ("村你", "孙丽"), ("又雷", "赵磊"), ("陈净", "陈静"),
                          ("正好", "郑浩"), ("州杰", "周杰")):
            out, _fixed = parser.correct_names_in_text(got, self.NAMES)
            self.assertEqual(out, want, f"{got} 应纠成 {want}，实际 {out}")

    def test_a_roster_with_a_doubled_name_can_still_correct(self):
        """名单里真有叠字姓名（莉莉）时，这条限制要放开。"""
        names = ["莉莉", "张三", "李四"]
        out, fixed = parser.correct_names_in_text("丽丽", names)
        self.assertEqual(out, "莉莉")
        self.assertTrue(fixed)

    def test_a_doubled_name_matches_exactly_without_correction(self):
        """就算不纠错，念对了也能精确命中——所以挡掉纠错不会漏人。"""
        self.assertIn("丽丽", parser.match_student_names("丽丽",
                                                        ["丽丽", "张三"]))

    def test_noise_does_not_switch_the_current_student(self):
        """端到端：正在录张三时说一句叠字噪声，不能把学生换掉。"""
        import os
        import shutil
        import tempfile
        from openpyxl import Workbook
        from grade_app.excel_io import load_sheet
        from grade_app.state import AppState
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "t.xlsx")
            wb = Workbook()
            ws = wb.active
            ws.append(["姓名", "第一题", "总分"])
            for n in ("张三", "李四", "王五"):
                ws.append([n, None, None])
            wb.save(path)
            cfg = {"auto_save_mode": "manual", "score_cutoff": 0.55}
            st = AppState(cfg=cfg)
            st.load(load_sheet(path, cfg, backup=False))
            st.handle_text("张三")
            self.assertEqual(st.current.name, "张三")
            for word in ("第第第第", "滴滴", "你你", "是是"):
                st.handle_text(word)
                self.assertEqual(st.current.name, "张三",
                                 f"说了 {word!r} 之后学生被换成了 "
                                 f"{st.current.name}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


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
