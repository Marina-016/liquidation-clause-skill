import sys
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from classifier import classify


TYPE_1 = "类型1: 备案"
TYPE_2 = "类型2: 备案+6个月大会"
TYPE_3 = "类型3: 自动触发终止"


class ClassifierRegressionTests(unittest.TestCase):
    def assert_type(self, expected, text, stage=1):
        actual, _, _ = classify(text, stage=stage)
        self.assertEqual(expected, actual)

    def test_type1_with_report_before_csrc(self):
        text = (
            "连续20个工作日基金份额持有人少于200人或者基金资产净值低于"
            "5000万元，应当在定期报告中披露。连续60个工作日出现前述情形，"
            "基金管理人应当报告中国证监会并提出解决方案，召开基金份额持有人大会。"
        )
        self.assert_type(TYPE_1, text)

    def test_type2_with_complete_deadline_chain(self):
        text = (
            "连续20个工作日基金份额持有人少于200人或者基金资产净值低于"
            "5000万元。连续60个工作日出现前述情形，基金管理人应当在"
            "10个工作日内向中国证监会报告，并在6个月内召集基金份额持有人大会。"
        )
        self.assert_type(TYPE_2, text)

    def test_type3_with_automatic_termination(self):
        text = (
            "连续20个工作日基金份额持有人少于200人或者基金资产净值低于"
            "5000万元。连续50个工作日出现前述情形，基金合同自动终止，"
            "无需召开基金份额持有人大会。"
        )
        self.assert_type(TYPE_3, text)

    def test_non_liquidation_text_is_not_classified(self):
        self.assert_type(
            None,
            "本基金投资于依法发行的证券，基金管理人按照基金合同约定履行职责。",
        )

    @unittest.expectedFailure
    def test_type1_with_standard_csrc_report_word_order(self):
        text = (
            "连续20个工作日基金份额持有人少于200人或者基金资产净值低于"
            "5000万元。连续60个工作日出现前述情形，基金管理人应当"
            "向中国证监会报告并提出解决方案，召开基金份额持有人大会。"
        )
        self.assert_type(TYPE_1, text)

    @unittest.expectedFailure
    def test_unrelated_six_month_phrase_does_not_create_type2(self):
        text = (
            "连续20个工作日基金份额持有人少于200人或者基金资产净值低于"
            "5000万元。连续60个工作日出现前述情形。"
            "本基金成立6个月内完成其他登记事项。"
        )
        self.assert_type(None, text)

    @unittest.expectedFailure
    def test_ten_day_report_without_six_month_meeting_is_not_type2(self):
        text = (
            "连续20个工作日基金份额持有人少于200人或者基金资产净值低于"
            "5000万元。连续60个工作日出现前述情形，基金管理人应当在"
            "10个工作日内向中国证监会报告。"
        )
        self.assert_type(None, text)

    @unittest.expectedFailure
    def test_unrelated_no_meeting_phrase_does_not_create_type3(self):
        text = (
            "连续20个工作日基金份额持有人少于200人或者基金资产净值低于"
            "5000万元。连续60个工作日出现前述情形。"
            "调整管理费无需召开基金份额持有人大会。"
        )
        self.assert_type(None, text)

    @unittest.expectedFailure
    def test_stage1_rejects_chinese_number_relaxation(self):
        text = (
            "连续二十个工作日基金份额持有人少于二百人或者基金资产净值低于"
            "五千万元。连续六十个工作日出现前述情形，基金管理人应当在"
            "十个工作日内向中国证监会报告，并在六个月内召集基金份额持有人大会。"
        )
        self.assert_type(None, text, stage=1)


if __name__ == "__main__":
    unittest.main()
