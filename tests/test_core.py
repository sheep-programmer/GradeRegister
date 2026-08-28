"""核心逻辑测试：parser / excel_io / state。运行：python -m unittest tests.test_core -v"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from grade_app import parser
from grade_app.excel_io import load_sheet, save_sheet
from grade_app.state import AppState
from openpyxl import Workbook

CFG = {
    "strip_prefix": True, "strip_suffix": True, "score_cutoff": 0.55,
    "write_formula": True, "write_checked": True, "auto_save": False,
}


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------
class TestCnNumber(unittest.TestCase):
    def test_basic(self):
        cases = {
            "十八": 18, "十二": 12, "二十": 20, "二十五": 25,
            "一百零五": 105, "一十八": 18, "两": 2, "零": 0,
            "十": 10, "三十": 30, "九十九": 99, "一百": 100,
        }
        for s, want in cases.items():
            self.assertEqual(parser.cn_int_to_value(s), want, s)

    def test_float(self):
        self.assertEqual(parser.cn_number_to_float("十二点五"), 12.5)
        self.assertEqual(parser.cn_number_to_float("一点五"), 1.5)
        self.assertEqual(parser.any_number_to_float("18.5"), 18.5)
        self.assertEqual(parser.any_number_to_float("十八"), 18)

    def test_invalid(self):
        self.assertIsNone(parser.cn_int_to_value("abc"))
        self.assertIsNone(parser.cn_number_to_float("十三点"))


class TestExtractScores(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(parser.extract_scores("十八分"), [18.0])
        self.assertEqual(parser.extract_scores("18分"), [18.0])
        self.assertEqual(parser.extract_scores("12"), [12.0])
        self.assertEqual(parser.extract_scores("18.5"), [18.5])

    def test_sequence(self):
        got = parser.extract_scores("十八分十二分十五分十七分")
        self.assertEqual(got, [18.0, 12.0, 15.0, 17.0])
        got2 = parser.extract_scores("18分、12分、15分、17分")
        self.assertEqual(got2, [18.0, 12.0, 15.0, 17.0])

    def test_prefix_stripped(self):
        self.assertEqual(parser.extract_scores("第一题十八分"), [18.0])
        self.assertEqual(parser.extract_scores("第一题18分"), [18.0])
        self.assertEqual(parser.extract_scores("第1题18.5"), [18.5])
        self.assertEqual(parser.extract_scores("第六题六分"), [6.0])
        # 关闭剥离时，题号也会被当成分数
        self.assertEqual(parser.extract_scores("第一题十八分", strip_prefix=False),
                         [1.0, 18.0])

    def test_suffix_off(self):
        self.assertEqual(parser.extract_scores("第一题十八分", strip_suffix=False),
                         [18.0])  # 前缀剥离后"分"字保留但无数字，不影响
        self.assertEqual(parser.extract_scores("十八分", strip_suffix=False), [18.0])

    def test_mixed_text(self):
        got = parser.extract_scores("第一题十八分 第二题十二分")
        self.assertEqual(got, [18.0, 12.0])


class TestExtractScoreItems(unittest.TestCase):
    def test_single_question(self):
        self.assertEqual(parser.extract_score_items("第三题九分"), [(3, 9.0)])

    def test_no_question_number(self):
        self.assertEqual(parser.extract_score_items("十八分"), [(None, 18.0)])

    def test_question_head_alone_is_placeholder(self):
        self.assertEqual(parser.extract_score_items("第三题"), [(3, None)])

    def test_all_questions_in_one_breath(self):
        """一口气念完四道题：每道题各自的题号都要认出来，不能全归到第一题。"""
        got = parser.extract_score_items(
            "第一题十分第二题二十分第三题三十分第四题四十分")
        self.assertEqual(got, [(1, 10.0), (2, 20.0), (3, 30.0), (4, 40.0)])

    def test_multiple_heads_with_spaces(self):
        got = parser.extract_score_items("第一题十八分 第二题十二分")
        self.assertEqual(got, [(1, 18.0), (2, 12.0)])

    def test_head_after_leading_scores(self):
        """题号前先念了一个不带题号的分数，两者都要保留。"""
        got = parser.extract_score_items("十八分第二题十二分")
        self.assertEqual(got, [(None, 18.0), (2, 12.0)])

    def test_separator_words_split_scores(self):
        self.assertEqual(parser.extract_score_items("10加5加5加3"),
                         [(None, 10.0), (None, 5.0), (None, 5.0), (None, 3.0)])

    def test_one_fen_settles_one_score(self):
        """「第一题十五分」被听成「七月十五分」时只认紧挨着「分」的 15。"""
        self.assertEqual(parser.extract_score_items("七月十五分"), [(None, 15.0)])

    def test_bare_number_without_fen_ignored(self):
        self.assertEqual(parser.extract_score_items("十八"), [])

    def test_inner_head_needs_the_di_character(self):
        """句中的题号必须带「第」字，否则「再说一次十分」里的「一次」会被当题号。"""
        self.assertEqual(parser.extract_score_items("再说一次十分"),
                         [(None, 10.0)])

    def test_leading_head_without_di_still_works(self):
        """句首题号识别时常丢掉「第」字，这里仍要认出来。"""
        self.assertEqual(parser.extract_score_items("三题九分"), [(3, 9.0)])


class TestHeaderScoreItems(unittest.TestCase):
    """按表头名念分数：表头不一定是「第X题」，也可能是「程序题」。"""

    HEADERS = ["程序题", "选择题", "填空题"]
    NUMBERED = ["第一题", "第二题", "第三题", "第四题"]

    def test_exact_header_name(self):
        self.assertEqual(
            parser.extract_score_items("程序题72分", headers=self.HEADERS),
            [(1, 72.0)])

    def test_later_header_name(self):
        self.assertEqual(
            parser.extract_score_items("选择题85分", headers=self.HEADERS),
            [(2, 85.0)])

    def test_several_headers_in_one_breath(self):
        got = parser.extract_score_items("程序题72分选择题85分填空题60分",
                                         headers=self.HEADERS)
        self.assertEqual(got, [(1, 72.0), (2, 85.0), (3, 60.0)])

    def test_question_number_still_works_with_named_headers(self):
        """念序号照旧生效，按位置命中第一个分数列。"""
        self.assertEqual(
            parser.extract_score_items("第一题78分", headers=self.HEADERS),
            [(1, 78.0)])

    def test_numbered_headers_go_through_index_path(self):
        self.assertEqual(
            parser.extract_score_items("第二题二十分", headers=self.NUMBERED),
            [(2, 20.0)])

    def test_header_alone_is_placeholder(self):
        self.assertEqual(
            parser.extract_score_items("程序题", headers=self.HEADERS),
            [(1, None)])

    def test_no_headers_argument_keeps_old_behaviour(self):
        self.assertEqual(parser.extract_score_items("第三题九分"), [(3, 9.0)])


class TestHeaderPinyinCorrection(unittest.TestCase):
    def test_misheard_header_corrected(self):
        text, fixed = parser.correct_headers_in_text("成序题七十二分",
                                                     ["程序题", "选择题"])
        self.assertIn("程序题", text)
        self.assertTrue(fixed)

    def test_numbered_headers_never_pinyin_matched(self):
        """「第一题」「第二题」发音太近，模糊匹配会把分数填到错的题上。"""
        text, fixed = parser.correct_headers_in_text("第二题二十分",
                                                     ["第一题", "第二题"])
        self.assertEqual(fixed, [])
        self.assertEqual(text, "第二题二十分")

    def test_student_name_not_mistaken_for_header(self):
        _text, fixed = parser.correct_headers_in_text(
            "刘洋", ["程序题", "选择题"], names=["刘洋"])
        self.assertEqual(fixed, [])

    def test_exact_header_left_alone(self):
        text, fixed = parser.correct_headers_in_text("程序题72分", ["程序题"])
        self.assertIn("程序题", text)
        self.assertEqual(fixed, [])

    def test_corrected_header_feeds_score_items(self):
        """纠正后的文本要能被分数提取认出来，端到端连上。"""
        headers = ["程序题", "选择题"]
        text, _fixed = parser.correct_headers_in_text("成序题七十二分", headers)
        self.assertEqual(parser.extract_score_items(text, headers=headers),
                         [(1, 72.0)])


class TestStudentIdExtraction(unittest.TestCase):
    """号码必须带标记词，裸数字一律当分数。"""

    def test_plain_number_with_hao(self):
        num, rest = parser.extract_student_id("5号")
        self.assertEqual(parser.any_number_to_float(num), 5)
        self.assertEqual(rest.strip(), "")

    def test_chinese_number_with_hao(self):
        num, _rest = parser.extract_student_id("五号")
        self.assertEqual(parser.any_number_to_float(num), 5)

    def test_xuehao_prefix(self):
        for text in ("学号5", "座号5", "编号五"):
            num, _rest = parser.extract_student_id(text)
            self.assertEqual(parser.any_number_to_float(num), 5, text)

    def test_nth_form(self):
        num, _rest = parser.extract_student_id("第五个")
        self.assertEqual(parser.any_number_to_float(num), 5)

    def test_id_with_scores_keeps_rest(self):
        num, rest = parser.extract_student_id("5号78分85分")
        self.assertEqual(parser.any_number_to_float(num), 5)
        self.assertEqual(parser.extract_scores(rest), [78.0, 85.0])

    def test_score_is_not_an_id(self):
        self.assertIsNone(parser.extract_student_id("5分")[0])

    def test_question_head_is_not_an_id(self):
        self.assertIsNone(parser.extract_student_id("第三题")[0])
        self.assertIsNone(parser.extract_student_id("第三题九分")[0])

    def test_bare_number_is_not_an_id(self):
        self.assertIsNone(parser.extract_student_id("78")[0])

    def test_name_is_not_an_id(self):
        self.assertIsNone(parser.extract_student_id("刘洋")[0])


class TestCollapseRepeats(unittest.TestCase):
    def test_whole_text_repeat_collapsed(self):
        self.assertEqual(parser.collapse_repeats("李四李四李四"), "李四")
        self.assertEqual(parser.collapse_repeats("张三张三"), "张三")

    def test_different_names_left_alone(self):
        self.assertEqual(parser.collapse_repeats("张三张三丰"), "张三张三丰")

    def test_non_repeat_left_alone(self):
        for t in ("刘洋", "第一题十分", "", "程序题72分"):
            self.assertEqual(parser.collapse_repeats(t), t)

    def test_single_char_repeat_left_alone(self):
        """「三三」可能是名字的一部分，单字重复不折叠。"""
        self.assertEqual(parser.collapse_repeats("三三"), "三三")


class TestAmbiguousNameCorrection(unittest.TestCase):
    """班上同时有刘洋和刘阳时，别闷头猜一个。"""

    BOTH = ["刘洋", "刘阳", "王小明"]
    ONLY_ONE = ["刘洋", "王小明"]

    def test_ambiguous_pair_not_rewritten(self):
        text, fixed = parser.correct_names_in_text("刘扬", self.BOTH)
        self.assertEqual(fixed, [])
        self.assertEqual(text, "刘扬")

    def test_clear_winner_still_rewritten(self):
        text, fixed = parser.correct_names_in_text("刘扬", self.ONLY_ONE)
        self.assertEqual(text, "刘洋")
        self.assertTrue(fixed)

    def test_ambiguous_text_yields_multiple_candidates(self):
        """不改写之后，模糊匹配要能给出两个候选供弹窗选择。"""
        got = parser.match_student_names("刘扬", self.BOTH)
        self.assertIn("刘洋", got)
        self.assertIn("刘阳", got)

    def test_prefer_set_breaks_the_tie(self):
        """分数接近时优先还没录完的学生。"""
        best, _r = parser.best_name_by_pinyin("刘扬", self.BOTH,
                                              prefer={"刘阳"})
        self.assertEqual(best, "刘阳")

    def test_prefer_does_not_override_clear_winner(self):
        """差距明显时该谁是谁，回头改分照样点得到人。"""
        best, _r = parser.best_name_by_pinyin(
            "王小明", self.BOTH, prefer={"刘阳"})
        self.assertEqual(best, "王小明")


class TestMatchNames(unittest.TestCase):
    NAMES = ["张三", "李四", "王五", "张三丰", "赵六"]

    def test_exact(self):
        self.assertEqual(parser.match_student_names("张三", self.NAMES), ["张三"])

    def test_contained(self):
        self.assertEqual(parser.match_student_names("张三。", self.NAMES), ["张三"])
        self.assertEqual(parser.match_student_names("张三丰", self.NAMES), ["张三丰"])

    def test_fuzzy(self):
        got = parser.match_student_names("章三", self.NAMES)
        self.assertIn("张三", got)

    def test_find_rows_duplicate(self):
        students = [(1, "张三"), (2, "李四"), (4, "张三")]
        rows = parser.find_student_rows("张三", students)
        self.assertEqual(len(rows), 2)


# ---------------------------------------------------------------------------
# excel_io
# ---------------------------------------------------------------------------
class TestExcel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "test.xlsx")
        wb = Workbook()
        ws = wb.active
        ws.append(["姓名", "第一题", "第二题", "第三题", "总分"])
        ws.append(["张三", "", "", "", ""])
        ws.append(["李四", "", "", "", ""])
        ws.append(["张三", "", "", "", ""])
        wb.save(self.path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_load(self):
        m = load_sheet(self.path, CFG, backup=False)
        self.assertEqual(m.name_col, 0)
        self.assertEqual(m.score_cols, [1, 2, 3])
        self.assertEqual(m.total_col, 4)
        self.assertEqual(m.check_col, 5)
        self.assertEqual(len(m.students), 3)
        self.assertEqual([s.name for s in m.students], ["张三", "李四", "张三"])

    def test_save_formula_and_check(self):
        m = load_sheet(self.path, CFG, backup=False)
        s0 = m.students[0]
        s0.scores = {1: 18.0, 2: 12.0, 3: 15.0}
        s0.total = m.calc_total(s0)
        s0.checked = False  # 不一致 -> 标红
        s1 = m.students[1]
        s1.scores = {1: 20.0, 2: 20.0, 3: 20.0}
        s1.total = m.calc_total(s1)
        s1.checked = True   # 一致 -> ✓
        save_sheet(m, CFG, write_formula=True)

        # 读回验证：分数与核对标记
        m2 = load_sheet(self.path, CFG, backup=False)
        self.assertEqual(m2.students[0].scores, {1: 18.0, 2: 12.0, 3: 15.0})
        self.assertEqual(m2.students[1].scores, {1: 20.0, 2: 20.0, 3: 20.0})
        # 核对结果随表格一起还原，重开表不会丢
        self.assertIs(m2.students[0].checked, False)
        self.assertIs(m2.students[1].checked, True)
        self.assertIs(m2.students[2].checked, None)   # 没核对过的行保持未核对
        # 另一个文件验证：总分写数值 + 核对列内容
        path2 = os.path.join(self.tmp, "num.xlsx")
        wb = Workbook()
        ws = wb.active
        ws.append(["姓名", "第一题", "第二题", "第三题", "总分"])
        ws.append(["张三", "", "", "", ""])
        wb.save(path2)
        m3 = load_sheet(path2, CFG, backup=False)
        s = m3.students[0]
        s.scores = {1: 18.0, 2: 12.0, 3: 15.0}
        s.total = m3.calc_total(s)   # 45
        s.checked = False
        save_sheet(m3, CFG, write_formula=False)
        from openpyxl import load_workbook as lw
        ws2 = lw(path2).active
        self.assertEqual(ws2.cell(row=2, column=5).value, 45.0)   # 总分数值
        self.assertEqual(ws2.cell(row=2, column=6).value, "不一致")  # 核对列
        self.assertIsNotNone(ws2.cell(row=2, column=6).fill.start_color.rgb)

    def test_blank_rows_keep_empty_total(self):
        """一分没录的学生不写总分：否则 =SUM(空白) 会把全班总分刷成 0。"""
        from openpyxl import load_workbook as lw
        m = load_sheet(self.path, CFG, backup=False)
        s0 = m.students[0]
        s0.scores = {1: 18.0}
        s0.total = m.calc_total(s0)
        save_sheet(m, CFG, write_formula=True)

        ws = lw(self.path).active
        self.assertEqual(ws.cell(row=2, column=5).value, "=SUM(B2:D2)")
        self.assertIsNone(ws.cell(row=3, column=5).value)   # 李四没录分
        self.assertIsNone(ws.cell(row=4, column=5).value)

    def test_manual_check_note_preserved(self):
        """老师自己在核对列写的批注不属于本程序的标记，保存时不能被抹掉。"""
        from openpyxl import load_workbook as lw
        wb = lw(self.path)
        wb.active.cell(row=2, column=6, value="家长已签字")
        wb.save(self.path)

        m = load_sheet(self.path, CFG, backup=False)
        self.assertIsNone(m.students[0].checked)     # 不是可识别的标记
        save_sheet(m, CFG, write_formula=True)
        self.assertEqual(lw(self.path).active.cell(row=2, column=6).value,
                         "家长已签字")

    def test_existing_total_preserved_without_scores(self):
        """老师手填过总分但没填分题得分的行，保存时不能被清掉。"""
        from openpyxl import load_workbook as lw
        wb = lw(self.path)
        wb.active.cell(row=3, column=5, value=88)     # 李四只有总分
        wb.save(self.path)

        m = load_sheet(self.path, CFG, backup=False)
        self.assertEqual(m.students[1].total, 88.0)
        self.assertEqual(m.students[1].scores, {})
        save_sheet(m, CFG, write_formula=True)
        self.assertEqual(lw(self.path).active.cell(row=3, column=5).value, 88)

    def test_header_covers_check_column(self):
        """原表没有核对列时，内存表头要补齐，否则按列索引访问会越界。"""
        m = load_sheet(self.path, CFG, backup=False)
        self.assertGreater(len(m.header), m.check_col)
        self.assertEqual(m.header[m.check_col], "核对")

    def test_existing_check_header_not_renamed(self):
        path = os.path.join(self.tmp, "hascheck.xlsx")
        wb = Workbook()
        ws = wb.active
        ws.append(["姓名", "第一题", "总分", "复核"])
        ws.append(["张三", "", "", ""])
        wb.save(path)

        m = load_sheet(path, CFG, backup=False)
        self.assertEqual(m.check_col, 3)
        self.assertEqual(m.header[3], "复核")

    def test_save_without_score_columns(self):
        """姓名列紧邻总分列（识别不出题号列）时保存不能崩。"""
        path = os.path.join(self.tmp, "noscore.xlsx")
        wb = Workbook()
        ws = wb.active
        ws.append(["姓名", "总分"])
        ws.append(["张三", ""])
        wb.save(path)

        m = load_sheet(path, CFG, backup=False)
        self.assertEqual(m.score_cols, [])
        save_sheet(m, CFG, write_formula=True)       # 不抛 IndexError

    def test_removed_score_cleared_from_file(self):
        """内存里删掉的分数要同步从表格抹掉，否则 =SUM() 还在算旧值。"""
        from openpyxl import load_workbook as lw
        m = load_sheet(self.path, CFG, backup=False)
        s0 = m.students[0]
        s0.scores = {1: 18.0, 2: 12.0}
        s0.total = m.calc_total(s0)
        save_sheet(m, CFG, write_formula=True)
        self.assertEqual(lw(self.path).active.cell(row=2, column=3).value, 12)

        del s0.scores[2]                       # 撤销第二题
        s0.total = m.calc_total(s0)
        save_sheet(m, CFG, write_formula=True)
        ws = lw(self.path).active
        self.assertIsNone(ws.cell(row=2, column=3).value)
        self.assertEqual(ws.cell(row=2, column=2).value, 18)

    def test_all_scores_removed_clears_total(self):
        """分数被清光后总分格也要清掉，留着 =SUM() 会算成 0。"""
        from openpyxl import load_workbook as lw
        m = load_sheet(self.path, CFG, backup=False)
        s0 = m.students[0]
        s0.scores = {1: 18.0}
        s0.total = m.calc_total(s0)
        save_sheet(m, CFG, write_formula=True)

        s0.scores.clear()
        s0.total = m.calc_total(s0)
        save_sheet(m, CFG, write_formula=True)
        ws = lw(self.path).active
        self.assertIsNone(ws.cell(row=2, column=2).value)
        self.assertIsNone(ws.cell(row=2, column=5).value)

    def test_text_score_cell_preserved(self):
        """老师手写的「缺考」这类文字不算分数，保存时不能被当成残留值清掉。"""
        from openpyxl import load_workbook as lw
        wb = lw(self.path)
        wb.active.cell(row=2, column=2, value="缺考")
        wb.save(self.path)

        m = load_sheet(self.path, CFG, backup=False)
        self.assertEqual(m.students[0].scores, {})
        save_sheet(m, CFG, write_formula=True)
        self.assertEqual(lw(self.path).active.cell(row=2, column=2).value, "缺考")

    def test_backup_created(self):
        from grade_app.excel_io import _make_backup
        dst = _make_backup(self.path)
        self.assertTrue(os.path.exists(dst))
        self.assertTrue(os.path.basename(dst).startswith("test-"))

    def test_backup_pruned_keeps_newest(self):
        """备份数量有上限，且留下的是时间戳最新的几个。"""
        from grade_app.excel_io import _prune_backups
        backup_dir = os.path.join(self.tmp, "backups")
        os.makedirs(backup_dir)
        stamps = [f"2026082{d}-100000" for d in range(6)]
        for ts in stamps:
            open(os.path.join(backup_dir, f"test-{ts}.xlsx"), "w").close()
        open(os.path.join(backup_dir, "别的表-20260101-100000.xlsx"), "w").close()

        _prune_backups(backup_dir, "test", ".xlsx", keep=3)

        kept = sorted(os.listdir(backup_dir))
        self.assertEqual([n for n in kept if n.startswith("test-")],
                         [f"test-{ts}.xlsx" for ts in stamps[-3:]])
        self.assertIn("别的表-20260101-100000.xlsx", kept)   # 其他表的备份不受影响


class TestReadChecked(unittest.TestCase):
    def test_marks(self):
        from grade_app.excel_io import _read_checked
        self.assertIs(_read_checked("✓"), True)
        self.assertIs(_read_checked(" 一致 "), True)
        self.assertIs(_read_checked("不一致"), False)
        self.assertIs(_read_checked("✗"), False)
        self.assertIsNone(_read_checked(None))
        self.assertIsNone(_read_checked(""))
        self.assertIsNone(_read_checked("待复核"))


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------
class TestState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "test.xlsx")
        wb = Workbook()
        ws = wb.active
        ws.append(["姓名", "第一题", "第二题", "第三题", "总分"])
        ws.append(["张三", "", "", "", ""])
        ws.append(["李四", "", "", "", ""])
        ws.append(["张三", "", "", "", ""])
        wb.save(self.path)
        self.state = AppState(cfg=CFG)
        self.state.load(load_sheet(self.path, CFG, backup=False))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_duplicate_ask_choice(self):
        r = self.state.select_student("张三")
        self.assertIsNotNone(r.select_choices)
        self.assertEqual(len(r.select_choices), 2)
        # 选择第 4 行（第二个张三）
        r2 = self.state.activate_row(3)
        self.assertEqual(self.state.current.name, "张三")
        self.assertEqual(self.state.current.row, 3)
        self.assertEqual(self.state.phase, "scoring")

    def test_scores_flow(self):
        self.state.select_student("李四")
        r = self.state.add_scores("第一题十八分 第二题十二分 第三题十五分")
        self.assertEqual(self.state.current.scores, {1: 18.0, 2: 12.0, 3: 15.0})
        self.assertEqual(self.state.current.total, 45.0)
        # 再补一个分数（已满 -> 温和提示，不覆盖已有分数）
        r2 = self.state.add_scores("第四题五分")
        self.assertTrue(r2.ok)
        self.assertIn("忽略", r2.message)
        self.assertEqual(self.state.current.scores, {1: 18.0, 2: 12.0, 3: 15.0})
        # 各题已满、又不带题号 -> 不猜位置，提示补念题号
        r3 = self.state.add_scores("五分")
        self.assertFalse(r3.ok)
        self.assertIn("要改分请带上题号", r3.message)
        self.assertEqual(self.state.current.scores, {1: 18.0, 2: 12.0, 3: 15.0})

    def test_scores_then_next_student(self):
        self.state.select_student("张三")
        self.state.activate_row(1)
        self.state.add_scores("第一题18分 第二题12分 第三题15分")
        # 念下一个学生名字（无数字）-> 切到新学生
        r = self.state.add_scores("王五")
        self.assertNotIn("王五", [s.name for s in self.state.model.students])
        # 用李四验证切换
        r = self.state.add_scores("李四")
        self.assertEqual(self.state.current.name, "李四")
        self.assertEqual(self.state.phase, "scoring")

    def test_total_check(self):
        self.state.select_student("张三")
        self.state.activate_row(1)
        self.state.add_scores("第一题18分 第二题12分 第三题15分")   # 总分 45
        r = self.state.check_total("总分45")
        self.assertTrue(r.ok)
        self.assertIs(self.state.current.checked, True)
        # 不一致的情况
        self.state.select_student("张三")
        self.state.activate_row(3)
        self.state.add_scores("第一题18分 第二题12分 第三题15分")
        r2 = self.state.check_total("总分46")
        self.assertIs(self.state.current.checked, False)

    def test_undo(self):
        self.state.select_student("李四")
        self.state.add_scores("第一题18分")
        self.state.undo()
        self.assertEqual(self.state.current.scores, {})

    def test_total_keyword_switches_phase(self):
        self.state.select_student("李四")
        self.state.add_scores("第一题18分 第二题12分 第三题15分")
        r = self.state.handle_text("总分 45")
        self.assertIs(self.state.current.checked, True)

    def test_check_reset_after_score_change(self):
        """核对完又改分：旧的核对结论必须作废，否则错分挂着 ✓。"""
        self.state.select_student("李四")
        self.state.add_scores("第一题18分 第二题12分 第三题15分")
        self.state.check_total("总分45")
        self.assertIs(self.state.current.checked, True)

        self.state.add_scores("第二题20分")          # 改分
        self.assertIsNone(self.state.current.checked)
        self.assertEqual(self.state.current.total, 53.0)

    def test_check_reset_after_undo(self):
        self.state.select_student("李四")
        self.state.add_scores("第一题18分 第二题12分 第三题15分")
        self.state.check_total("总分45")
        self.state.undo()
        self.assertIsNone(self.state.current.checked)

    def test_last_edit_col_tracked(self):
        """语音填分也要标出刚填的格子，界面据此高亮。"""
        self.state.select_student("李四")
        self.state.add_scores("第二题12分")
        self.assertEqual(self.state._last_edit_col, 2)
        self.state.select_student("张三")            # 切学生时清掉旧高亮
        self.state.activate_row(1)
        self.assertIsNone(self.state._last_edit_col)

    def test_undo_message_uses_header_text(self):
        """提示里说的是表头原文，不是内部列号。"""
        self.state.select_student("李四")
        self.state.add_scores("第三题15分")
        self.assertIn("第三题", self.state.undo().message)

    def test_no_question_score_message_matches_result(self):
        """带题号的分数照填，只有真正没地方放的那个才提示补题号。"""
        self.state.select_student("李四")
        r = self.state.add_scores("第一题18分 第二题12分 第三题18分 20分")
        self.assertEqual(self.state.current.scores, {1: 18.0, 2: 12.0, 3: 18.0})
        self.assertIn("已填 3 题", r.message)
        self.assertIn("要改分请带上题号", r.message)
        self.assertNotIn("没带题号已忽略", r.message)

    def test_total_keyword_yigong(self):
        """「一共92分」：92 是总分，「一」不能被当成题分。"""
        self.state.select_student("李四")
        self.state.add_scores("第一题18分 第二题12分 第三题18分")
        r = self.state.handle_text("一共92分")
        self.assertEqual(self.state.current.scores, {1: 18.0, 2: 12.0, 3: 18.0})
        self.assertIn("92", r.message)
        self.assertIs(self.state.current.checked, False)   # 48 ≠ 92

    def test_only_one_score_per_fen(self):
        """「第一题十五分」听成「七月十五分」时只认 15，7 不是分数。"""
        self.state.select_student("李四")
        self.state.add_scores("七月十五分")
        self.assertEqual(self.state.current.scores, {1: 15.0})

    def test_question_head_alone_is_remembered(self):
        """只念了「第三题」：记下题号等分数，不能被当成人名「张三」。"""
        self.state.select_student("李四")
        r = self.state.handle_text("第三题")
        self.assertEqual(self.state.current.name, "李四")
        self.assertEqual(self.state.pending_col(), 3)
        self.assertIn("第三题", r.message)

        self.state.handle_text("九分")
        self.assertEqual(self.state.current.scores, {3: 9.0})
        self.assertIsNone(self.state.pending_col())

    def test_pending_question_dropped_on_student_switch(self):
        self.state.select_student("李四")
        self.state.handle_text("第三题")
        self.state.activate_row(1)       # 换到张三
        self.assertIsNone(self.state.pending_col())

    def test_misheard_total_does_not_fill_scores(self):
        """「总分」听成「钟分」时走核对，不能把总分当题分填进空题。"""
        self.state.select_student("李四")
        self.state.add_scores("第一题18分 第二题12分")
        r = self.state.handle_text("钟分三十分")
        self.assertEqual(self.state.current.scores, {1: 18.0, 2: 12.0})
        self.assertIs(self.state.current.checked, True)
        self.assertIn("一致", r.message)

    def test_scores_then_total_in_one_sentence(self):
        """「…十八分 一共 48」：前半填分、后半核对，分数不能被吞掉。"""
        self.state.select_student("李四")
        self.state.add_scores("第一题18分 第二题12分")
        r = self.state.handle_text("第三题18分 一共 48")
        self.assertEqual(self.state.current.scores, {1: 18.0, 2: 12.0, 3: 18.0})
        self.assertIs(self.state.current.checked, True)
        self.assertIn("一致", r.message)


class TestNamedHeaderState(unittest.TestCase):
    """表头是「程序题」这类名称时，念表头名就能定向填分。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "named.xlsx")
        wb = Workbook()
        ws = wb.active
        ws.append(["姓名", "程序题", "选择题", "填空题", "总分"])
        ws.append(["张三", "", "", "", ""])
        ws.append(["吴敏", "", "", "", ""])
        wb.save(self.path)
        self.state = AppState(cfg=CFG)
        self.state.load(load_sheet(self.path, CFG, backup=False))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fill_by_header_name(self):
        self.state.select_student("张三")
        self.state.handle_text("程序题72分")
        self.assertEqual(self.state.current.scores, {1: 72.0})

    def test_fill_several_headers_in_one_breath(self):
        self.state.select_student("张三")
        self.state.handle_text("程序题72分选择题85分填空题60分")
        self.assertEqual(self.state.current.scores,
                         {1: 72.0, 2: 85.0, 3: 60.0})

    def test_question_number_still_fills(self):
        """念序号照旧按位置填。"""
        self.state.select_student("张三")
        self.state.handle_text("第二题85分")
        self.assertEqual(self.state.current.scores, {2: 85.0})

    def test_misheard_header_corrected_and_filled(self):
        self.state.select_student("张三")
        r = self.state.handle_text("成序题七十二分")
        self.assertEqual(self.state.current.scores, {1: 72.0})
        self.assertIn("程序题", r.heard_text or "")

    def test_message_uses_header_text(self):
        self.state.select_student("张三")
        r = self.state.handle_text("程序题")
        self.assertIn("程序题", r.message)


class TestHeardText(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "heard.xlsx")
        wb = Workbook()
        ws = wb.active
        ws.append(["姓名", "第一题", "第二题", "总分"])
        ws.append(["吴敏", "", "", ""])
        ws.append(["刘洋", "", "", ""])
        wb.save(self.path)
        self.state = AppState(cfg=CFG)
        self.state.load(load_sheet(self.path, CFG, backup=False))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_corrected_name_is_reported(self):
        """听成「五米」而名单里是「吴敏」时，界面要显示纠正后的名字。"""
        r = self.state.handle_text("五米")
        self.assertEqual(self.state.current.name, "吴敏")
        self.assertEqual(r.heard_text, "吴敏")

    def test_unmatched_text_keeps_original(self):
        """没有任何名字发音接近时保留原文，否则不知道到底听到了什么。"""
        r = self.state.handle_text("今天天气不错")
        self.assertIn(r.heard_text or "今天天气不错", "今天天气不错")

    def test_exact_name_needs_no_correction(self):
        r = self.state.handle_text("刘洋")
        self.assertEqual(r.heard_text or "刘洋", "刘洋")


class TestUndoGrouping(unittest.TestCase):
    """一次操作算一组：填一句话、清一整列，都只需撤销一次。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "undo.xlsx")
        wb = Workbook()
        ws = wb.active
        ws.append(["姓名", "第一题", "第二题", "第三题", "第四题", "总分"])
        ws.append(["张三", "", "", "", "", ""])
        ws.append(["李四", "", "", "", "", ""])
        wb.save(self.path)
        self.state = AppState(cfg=CFG)
        self.state.load(load_sheet(self.path, CFG, backup=False))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_one_sentence_undoes_in_one_step(self):
        self.state.select_student("张三")
        self.state.handle_text("第一题十分第二题二十分第三题三十分第四题四十分")
        self.assertEqual(len(self.state.current.scores), 4)
        r = self.state.undo()
        self.assertTrue(r.ok)
        self.assertEqual(self.state.current.scores, {})

    def test_clear_cells_undoes_in_one_step(self):
        m = self.state.model
        self.state.select_student("张三")
        self.state.handle_text("第一题十分第二题二十分")
        targets = [(m.students[0], c) for c in (1, 2)]
        r = self.state.clear_cells(targets)
        self.assertTrue(r.ok)
        self.assertEqual(m.students[0].scores, {})
        self.state.undo()
        self.assertEqual(m.students[0].scores, {1: 10.0, 2: 20.0})

    def test_clear_whole_column_across_students(self):
        m = self.state.model
        for name in ("张三", "李四"):
            self.state.select_student(name)
            self.state.handle_text("第一题十分")
        targets = [(s, 1) for s in m.students]
        self.state.clear_cells(targets)
        self.assertEqual([s.scores.get(1) for s in m.students], [None, None])
        self.state.undo()
        self.assertEqual([s.scores.get(1) for s in m.students], [10.0, 10.0])

    def test_clear_empty_cells_reports_nothing_to_do(self):
        m = self.state.model
        r = self.state.clear_cells([(m.students[0], 1)])
        self.assertFalse(r.ok)

    def test_clearing_resets_total_and_check(self):
        m = self.state.model
        self.state.select_student("张三")
        self.state.handle_text("第一题十分")
        stu = m.students[0]
        stu.checked = True
        self.state.clear_cells([(stu, 1)])
        self.assertIsNone(stu.checked)
        self.assertEqual(stu.total, 0.0)


class TestIdColumn(unittest.TestCase):
    """有学号列就用真学号，没有就用名单序号。"""

    def _build(self, header, rows):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = os.path.join(tmp, "t.xlsx")
        wb = Workbook()
        ws = wb.active
        ws.append(header)
        for r in rows:
            ws.append(r)
        wb.save(path)
        st = AppState(cfg=CFG)
        st.load(load_sheet(path, CFG, backup=False))
        return st

    def test_id_column_detected(self):
        st = self._build(["学号", "姓名", "第一题", "总分"],
                         [["1001", "张三", "", ""], ["1002", "李四", "", ""]])
        self.assertEqual(st.model.id_col, 0)
        self.assertEqual([s.sid for s in st.model.students], ["1001", "1002"])
        self.assertEqual(st.model.name_col, 1)
        self.assertEqual(st.model.score_cols, [2])

    def test_no_id_column(self):
        st = self._build(["姓名", "第一题", "总分"],
                         [["张三", "", ""], ["李四", "", ""]])
        self.assertEqual(st.model.id_col, -1)

    def test_select_by_real_student_id(self):
        st = self._build(["学号", "姓名", "第一题", "总分"],
                         [["1001", "张三", "", ""], ["1002", "李四", "", ""]])
        r = st.handle_text("1002号")
        self.assertTrue(r.ok, r.message)
        self.assertEqual(st.current.name, "李四")

    def test_select_by_list_position_without_id_column(self):
        st = self._build(["姓名", "第一题", "总分"],
                         [["张三", "", ""], ["李四", "", ""], ["王五", "", ""]])
        st.handle_text("3号")
        self.assertEqual(st.current.name, "王五")

    def test_id_and_scores_in_one_breath(self):
        st = self._build(["姓名", "第一题", "第二题", "总分"],
                         [["张三", "", "", ""], ["李四", "", "", ""]])
        r = st.handle_text("2号78分85分")
        self.assertEqual(st.current.name, "李四")
        self.assertEqual(st.current.scores, {1: 78.0, 2: 85.0})
        self.assertTrue(r.ok, r.message)

    def test_nth_form_selects_by_position(self):
        st = self._build(["姓名", "第一题", "总分"],
                         [["张三", "", ""], ["李四", "", ""], ["王五", "", ""]])
        st.handle_text("第二个")
        self.assertEqual(st.current.name, "李四")

    def test_out_of_range_id_reports_error(self):
        """念了 20 号而班上只有 3 人：明确报错，不去猜名字填错人。"""
        st = self._build(["姓名", "第一题", "总分"],
                         [["张三", "", ""], ["李四", "", ""], ["王五", "", ""]])
        r = st.handle_text("20号")
        self.assertFalse(r.ok)
        self.assertIsNone(st.current)

    def test_score_not_treated_as_id(self):
        st = self._build(["姓名", "第一题", "第二题", "总分"],
                         [["张三", "", "", ""], ["李四", "", "", ""]])
        st.handle_text("张三")
        st.handle_text("5分")
        self.assertEqual(st.current.name, "张三")
        self.assertEqual(st.current.scores, {1: 5.0})


class TestRepeatAndAmbiguityInState(unittest.TestCase):
    def _build(self, names):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = os.path.join(tmp, "t.xlsx")
        wb = Workbook()
        ws = wb.active
        ws.append(["姓名", "第一题", "第二题", "总分"])
        for n in names:
            ws.append([n, "", "", ""])
        wb.save(path)
        st = AppState(cfg=CFG)
        st.load(load_sheet(path, CFG, backup=False))
        return st

    def test_repeated_name_collapsed(self):
        st = self._build(["张三", "李四"])
        r = st.handle_text("李四李四李四")
        self.assertTrue(r.ok, r.message)
        self.assertEqual(st.current.name, "李四")

    def test_repeated_misheard_name_still_matches(self):
        """听错又重复三遍时，不折叠的话整串拼音跟谁都不像，会匹配失败。"""
        st = self._build(["吴敏", "张三"])
        r = st.handle_text("五米五米五米")
        self.assertTrue(r.ok, r.message)
        self.assertEqual(st.current.name, "吴敏")
        self.assertEqual(r.heard_text, "吴敏")

    def test_ambiguous_name_asks_instead_of_guessing(self):
        st = self._build(["刘洋", "刘阳", "王小明"])
        r = st.handle_text("刘扬")
        self.assertIsNotNone(r.select_choices)
        names = {n for _row, n in r.select_choices}
        self.assertEqual(names, {"刘洋", "刘阳"})
        self.assertIsNone(st.current)

    def test_unambiguous_name_still_direct(self):
        st = self._build(["刘洋", "王小明"])
        r = st.handle_text("刘扬")
        self.assertIsNone(r.select_choices)
        self.assertEqual(st.current.name, "刘洋")


class TestAutoSaveMode(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "test.xlsx")
        wb = Workbook()
        ws = wb.active
        ws.append(["姓名", "第一题", "第二题", "总分"])
        ws.append(["李四", "", "", ""])
        wb.save(self.path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _state(self, **over):
        cfg = dict(CFG)
        cfg["auto_save"] = True
        cfg.update(over)
        st = AppState(cfg=cfg)
        st.load(load_sheet(self.path, cfg, backup=False))
        st.select_student("李四")
        return st

    def _saved_scores(self):
        from openpyxl import load_workbook as lw
        ws = lw(self.path).active
        return [ws.cell(row=2, column=c).value for c in (2, 3)]

    def test_mode_score_writes_every_time(self):
        st = self._state(auto_save_mode="score")
        st.add_scores("第一题18分")
        self.assertEqual(self._saved_scores(), [18, None])

    def test_mode_student_waits_for_the_last_question(self):
        st = self._state(auto_save_mode="student")
        st.add_scores("第一题18分")
        self.assertEqual(self._saved_scores(), [None, None])   # 还没录完
        st.add_scores("第二题12分")
        self.assertEqual(self._saved_scores(), [18, 12])

    def test_mode_manual_never_writes(self):
        st = self._state(auto_save_mode="manual")
        st.add_scores("第一题18分 第二题12分")
        st.check_total("总分30")
        self.assertEqual(self._saved_scores(), [None, None])

    def test_legacy_auto_save_flag_maps_to_manual(self):
        st = self._state(auto_save=False, auto_save_mode=None)
        self.assertEqual(st.auto_save_mode(), "manual")
        st.add_scores("第一题18分")
        self.assertEqual(self._saved_scores(), [None, None])


class TestSplitAtTotal(unittest.TestCase):
    def test_markers(self):
        self.assertEqual(parser.split_at_total("第一题18分 一共 48"),
                         ("第一题18分 ", " 48"))
        self.assertEqual(parser.split_at_total("总分45"), ("", "45"))
        self.assertEqual(parser.split_at_total("总成绩 45"), ("", " 45"))
        self.assertEqual(parser.split_at_total("第一题18分"), ("第一题18分", ""))

    def test_misheard_total_by_pinyin(self):
        """「总分」被听成同音词时照样切得出来，否则总分会被当成题分填掉。"""
        for text in ("钟分六十分", "充分六十分", "钟晨六十分", "中分六十分",
                     "钟分 六十"):
            with self.subTest(text=text):
                self.assertTrue(parser.find_total_keyword(text), text)
                _head, tail = parser.split_at_total(text)
                self.assertEqual(parser.extract_scores(tail), [60.0])

    def test_pinyin_guess_needs_a_number(self):
        """没有数字的句子不做同音猜测，避免把姓名当成总分。"""
        self.assertFalse(parser.find_total_keyword("钟分"))
        self.assertFalse(parser.find_total_keyword("充分"))

    def test_pinyin_guess_skips_roster_names(self):
        """班里真有同音姓名时，念名字不能被当成念总分。"""
        roster = ["张三", "钟芳", "宋芬"]
        self.assertFalse(parser.find_total_keyword("宋芬十八分", roster))
        self.assertTrue(parser.find_total_keyword("宋芬十八分", ["张三"]))

    def test_score_sentences_not_mistaken_for_total(self):
        for text in ("第一题十八分", "十八分十二分", "满分", "三分", "得分"):
            with self.subTest(text=text):
                self.assertFalse(parser.find_total_keyword(text), text)


if __name__ == "__main__":
    unittest.main()

class TestNameAndScoresInOneBreath(unittest.TestCase):
    """「赵磊第一题十分第二题十二分」这类连念，在任何阶段都要填得进去。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "t.xlsx")
        wb = Workbook()
        ws = wb.active
        ws.append(["姓名", "第一题", "第二题", "第三题", "第四题", "总分"])
        for n in ("赵磊", "张三", "李四"):
            ws.append([n, "", "", "", "", ""])
        wb.save(self.path)
        self.state = AppState(cfg=CFG)
        self.state.load(load_sheet(self.path, CFG, backup=False))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _scores(self):
        m = self.state.model
        return [self.state.current.scores.get(c) for c in m.score_cols]

    def test_fills_from_idle_phase(self):
        """刚启动（idle）就连名带分念一整句。"""
        self.assertEqual(self.state.phase, "idle")
        self.state.handle_text("赵磊第一题十分第二题十二分")
        self.assertEqual(self.state.current.name, "赵磊")
        self.assertEqual(self._scores(), [10.0, 12.0, None, None])

    def test_fills_from_scoring_phase(self):
        self.state.handle_text("张三")
        self.state.handle_text("赵磊第一题十分第二题十二分")
        self.assertEqual(self.state.current.name, "赵磊")
        self.assertEqual(self._scores(), [10.0, 12.0, None, None])

    def test_real_world_misheard_sentence(self):
        """实际录到的一句：「是跟」是「十分」的误听，题号超范围的那个忽略掉。"""
        r = self.state.handle_text(
            "赵磊第一题是跟第二题十二分第三题七分第八题七十五分")
        self.assertEqual(self.state.current.name, "赵磊")
        self.assertEqual(self._scores()[:3], [10.0, 12.0, 7.0])
        self.assertIn("超出范围", r.message)

    def test_plain_name_still_only_switches(self):
        """只念名字不能凭空填分。"""
        self.state.handle_text("赵磊")
        self.assertEqual(self.state.current.name, "赵磊")
        self.assertEqual(self._scores(), [None] * 4)

    def test_scores_without_name_need_a_current_student(self):
        """idle 阶段只念分数、又没有当前学生时，要说清楚而不是崩。"""
        r = self.state.handle_text("第一题十分")
        self.assertFalse(r.ok)
        self.assertIsNone(self.state.current)


class TestPinyinSimilarity(unittest.TestCase):
    """按字比声母韵母：整串比会把「村你/孙丽」这种模糊音结构丢掉。"""

    def test_fuzzy_initials_and_finals(self):
        """c↔s、n↔l 是普通话最常混的几组，韵母完全相同时要给高分。"""
        self.assertGreater(parser.pinyin_similarity("村你", "孙丽"), 0.7)

    def test_identical_pronunciation_scores_one(self):
        for a, b in (("正好", "郑浩"), ("例四", "李四"), ("沉静", "陈静")):
            self.assertAlmostEqual(parser.pinyin_similarity(a, b), 1.0,
                                   delta=0.01, msg=f"{a}/{b}")

    def test_different_people_score_low(self):
        """不同的人必须拉开距离，否则会把分数填到别人那行。"""
        for a, b in (("张三", "李四"), ("刘洋", "陈静"),
                     ("王小明", "赵磊"), ("吴敏", "周杰")):
            self.assertLess(parser.pinyin_similarity(a, b), 0.35,
                            msg=f"{a}/{b}")

    def test_length_mismatch_penalised(self):
        """「张三」不能蹭上「张三丰」，否则三个字的名字永远选不中。"""
        self.assertLess(parser.pinyin_similarity("张三", "张三丰"), 0.8)

    def test_handles_empty_and_non_chinese(self):
        for a, b in (("", "张三"), ("abc", "张三"), ("张三", "")):
            v = parser.pinyin_similarity(a, b)
            self.assertGreaterEqual(v, 0.0)
            self.assertLessEqual(v, 1.0)

    def test_threshold_separates_real_cases(self):
        """实测样本：该纠的过阈值，不该纠的不过。"""
        th = parser._NAME_FIX_THRESHOLD
        for got, want in (("村你", "孙丽"), ("正好", "郑浩"), ("五米", "吴敏")):
            self.assertGreaterEqual(parser.pinyin_similarity(got, want), th,
                                    msg=f"{got}->{want} 应该能纠回来")
        for got, want in (("张三", "李四"), ("刘洋", "陈静")):
            self.assertLess(parser.pinyin_similarity(got, want), th,
                            msg=f"{got}->{want} 不该被纠")
