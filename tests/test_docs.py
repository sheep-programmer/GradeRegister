"""文档与代码是否自洽。

README 与设置面板、默认配置、打包清单很容易各自漂移——这一轮就撞到过两次：
面板的保存模式早已从三档变四档而 README 还写三档，`make_sample.py` 删掉了
README 还在引用。写错的文档比简陋的文档更坏，所以让测试盯着。
运行：python -m unittest tests.test_docs -v
"""
from __future__ import annotations

import os
import pathlib
import re
import unittest

from grade_app import excel_io, speech
from grade_app.config import DEFAULT_CONFIG
from grade_app.ui.dialogs import SettingsDialog

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")

# README 里出现但不该当成仓库文件检查的：运行期生成的、举例用的
NOT_IN_REPO = {"config.json", "hotwords.txt", "成绩表.xlsx"}


class TestReadmeReferences(unittest.TestCase):
    def test_referenced_files_exist(self):
        missing = []
        for raw in re.findall(
                r"`([^`]*\.(?:py|xlsx|md|bat|command|spec|json|txt))`", README):
            for part in raw.split(" / "):
                part = part.strip()
                if part.startswith("python "):
                    part = part[len("python "):].strip()
                if not part or part in NOT_IN_REPO or part.startswith("."):
                    continue
                # 结构图里写的是模块名，文件在 grade_app/ 或 grade_app/ui/ 下
                if any((ROOT / c).exists() for c in
                       (part, f"grade_app/{part}", f"grade_app/ui/{part}")):
                    continue
                missing.append(part)
        self.assertEqual(missing, [], f"README 提到但仓库里没有: {missing}")

    def test_no_licence_claim_without_a_licence_file(self):
        if not (ROOT / "LICENSE").exists():
            self.assertNotRegex(README, r"(?m)^## 许可",
                                "声称了许可但没有 LICENSE 文件")


class TestReadmeMatchesSettings(unittest.TestCase):
    def test_every_toggle_is_documented_verbatim(self):
        """设置表要用面板上的原话，改了文案两边一起改。"""
        for _title, items in SettingsDialog.CHECKS:
            for _key, label, _hint in items:
                self.assertIn(label, README, f"设置表缺开关「{label}」")

    def test_save_mode_count_and_default(self):
        modes = SettingsDialog.SAVE_MODES
        self.assertIn(f"{'四' if len(modes) == 4 else len(modes)}选一", README,
                      f"保存模式实际 {len(modes)} 档，README 说的不是这个数")
        self.assertEqual(modes[0][0], DEFAULT_CONFIG["auto_save_mode"],
                         "面板第一档应当就是默认档")

    def test_every_engine_is_documented(self):
        for engine in ("sense-voice", "paraformer", "sherpa", "vosk",
                       "faster-whisper"):
            self.assertIn(f"`{engine}`", README, f"README 没提 {engine}")

    def test_backup_count_matches_the_code(self):
        self.assertIn(f"{excel_io.BACKUP_KEEP} 份", README,
                      f"代码里 BACKUP_KEEP={excel_io.BACKUP_KEEP}")


class TestReadmeMatchesColumnRules(unittest.TestCase):
    """列识别的关键词，代码改了 README 也得改，否则老师照着排表会失败。"""

    GROUPS = {
        "姓名": ("姓名", "名字", "学生"),
        "总分": ("总分", "总成绩", "总计", "合计"),
        "核对": ("核对", "核查", "复核", "检查", "确认"),
        "学号": ("学号", "座号", "编号", "考号", "序号"),
    }

    def test_keywords_appear_in_both(self):
        src = (ROOT / "grade_app" / "excel_io.py").read_text(encoding="utf-8")
        for group, words in self.GROUPS.items():
            for word in words:
                self.assertIn(word, src, f"{group}列关键词 {word} 不在代码里")
                self.assertIn(word, README, f"{group}列关键词 {word} 没写进 README")


class TestPackagingIsConsistent(unittest.TestCase):
    def test_spec_and_downloader_bundle_the_same_engines(self):
        """spec 少一个模型就拒绝打包，所以 --all 的清单必须和它一致。"""
        import download_model
        spec = (ROOT / "GradeRegister.spec").read_text(encoding="utf-8")
        found = re.search(r"bundled_models = \(([^)]*)\)", spec)
        self.assertIsNotNone(found)
        in_spec = {part.strip().strip("\"'")
                   for part in found.group(1).split(",") if part.strip()}
        dirname = {"sense-voice": "sense-voice", "paraformer": "paraformer-zh"}
        will_download = {dirname[name] for name, _fn in download_model.BUNDLED}
        self.assertEqual(in_spec, will_download)

    def test_only_one_workflow_reacts_to_version_tags(self):
        """两条流水线都盯 v* 的话，一个 tag 会跑两遍。"""
        folder = ROOT / ".github" / "workflows"
        reacting = [p.name for p in folder.glob("*.yml")
                    if 'tags: ["v*"]' in p.read_text(encoding="utf-8")]
        self.assertEqual(len(reacting), 1, f"都盯着 v* 的流水线: {reacting}")


class TestNoFakeSettings(unittest.TestCase):
    """面板上摆出来的每一项都得真的起作用。"""

    def test_every_config_key_is_read_somewhere(self):
        src = "\n".join(p.read_text(encoding="utf-8")
                        for p in (ROOT / "grade_app").rglob("*.py"))
        for key in DEFAULT_CONFIG:
            self.assertRegex(
                src, rf"""(?:get|cfg)\(\s*["']{re.escape(key)}["']""",
                f"配置键 {key} 从来没被读取过")

    def test_every_toggle_has_a_config_default(self):
        for _title, items in SettingsDialog.CHECKS:
            for key, label, _hint in items:
                self.assertIn(key, DEFAULT_CONFIG,
                              f"「{label}」的键 {key} 不在 DEFAULT_CONFIG")

    def test_native_endpoint_list_matches_the_engines(self):
        cases = {"sherpa": speech.SherpaEngine,
                 "sense-voice": speech.SenseVoiceEngine,
                 "paraformer": speech.ParaformerEngine,
                 "vosk": speech.VoskEngine}
        for name, cls in cases.items():
            self.assertEqual(hasattr(cls, "check_endpoint"),
                             name in speech.NATIVE_ENDPOINT_ENGINES, name)

    def test_notification_sounds_go_through_one_gate(self):
        """绕过 _beep 直接播的地方关不掉提示音。"""
        src = (ROOT / "grade_app" / "ui" / "main_window.py").read_text(
            encoding="utf-8")
        direct = re.findall(r"platform_support\.play_sound\(", src)
        self.assertEqual(len(direct), 1,
                         "只允许 _beep 里那一处直接调 play_sound")


if __name__ == "__main__":
    unittest.main()
