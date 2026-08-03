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

    def test_type1_with_standard_csrc_report_word_order(self):
        text = (
            "连续20个工作日基金份额持有人少于200人或者基金资产净值低于"
            "5000万元。连续60个工作日出现前述情形，基金管理人应当"
            "向中国证监会报告并提出解决方案，召开基金份额持有人大会。"
        )
        self.assert_type(TYPE_1, text)

    def test_type1_with_full_regulator_name(self):
        text = (
            "连续20个工作日基金份额持有人少于200人或者基金资产净值低于"
            "5000万元。连续60个工作日出现前述情形，基金管理人应当"
            "向中国证券监督管理委员会报告并提出解决方案，召开基金份额持有人大会。"
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

    def test_unrelated_six_month_phrase_does_not_create_type2(self):
        text = (
            "连续20个工作日基金份额持有人少于200人或者基金资产净值低于"
            "5000万元。连续60个工作日出现前述情形。"
            "本基金成立6个月内完成其他登记事项。"
        )
        self.assert_type(None, text)

    def test_ten_day_report_without_six_month_meeting_is_not_type2(self):
        text = (
            "连续20个工作日基金份额持有人少于200人或者基金资产净值低于"
            "5000万元。连续60个工作日出现前述情形，基金管理人应当在"
            "10个工作日内向中国证监会报告。"
        )
        self.assert_type(None, text)

    def test_unrelated_no_meeting_phrase_does_not_create_type3(self):
        text = (
            "连续20个工作日基金份额持有人少于200人或者基金资产净值低于"
            "5000万元。连续60个工作日出现前述情形。"
            "调整管理费无需召开基金份额持有人大会。"
        )
        self.assert_type(None, text)

    def test_stage1_rejects_chinese_number_relaxation(self):
        text = (
            "连续二十个工作日基金份额持有人少于二百人或者基金资产净值低于"
            "五千万元。连续六十个工作日出现前述情形，基金管理人应当在"
            "十个工作日内向中国证监会报告，并在六个月内召集基金份额持有人大会。"
        )
        self.assert_type(None, text, stage=1)

    def test_stage2_accepts_chinese_numbers(self):
        text = (
            "连续二十个工作日基金份额持有人少于二百人或者基金资产净值低于"
            "五千万元。连续六十个工作日出现前述情形，基金管理人应当在"
            "十个工作日内向中国证监会报告，并在六个月内召集基金份额持有人大会。"
        )
        self.assert_type(TYPE_2, text, stage=2)

    def test_stage1_accepts_chinese_deadlines_with_arabic_base(self):
        text = (
            "连续20个工作日基金份额持有人少于200人或者基金资产净值低于"
            "5000万元。连续60个工作日出现前述情形，基金管理人应当在"
            "十个工作日内向中国证监会报告，并在六个月内召集基金份额持有人大会。"
        )
        self.assert_type(TYPE_2, text, stage=1)
    def test_stage3_tolerates_ocr_separators(self):
        text = (
            "连·续·二·十·个·工·作·日基金份额持有人少于二·百·人或者"
            "基金资产净值低于五·千·万元。连续六·十·个·工·作·日出现前述情形，"
            "基金管理人应当在十·个·工·作·日内向中国证监会报告，并在"
            "六·个·月·内召集基金份额持有人大会。"
        )
        self.assert_type(None, text, stage=2)
        self.assert_type(TYPE_2, text, stage=3)

    def test_missing_twenty_day_threshold_is_not_classified(self):
        text = (
            "基金份额持有人少于200人或者基金资产净值低于5000万元。"
            "连续60个工作日出现前述情形，基金管理人应当在10个工作日内"
            "向中国证监会报告，并在6个月内召集基金份额持有人大会。"
        )
        self.assert_type(None, text)

    def test_fifty_day_no_meeting_without_termination_is_not_type3(self):
        text = (
            "连续20个工作日基金份额持有人少于200人或者基金资产净值低于"
            "5000万元。连续50个工作日出现前述情形，基金管理人进行风险提示。"
            "调整管理费无需召开基金份额持有人大会。"
        )
        self.assert_type(None, text)

    def test_sixty_day_termination_wording_is_type3(self):
        text = (
            "连续20个工作日基金份额持有人少于200人或者基金资产净值低于"
            "5000万元。连续60个工作日出现前述情形，基金合同自动终止，"
            "无需召开基金份额持有人大会。"
        )
        self.assert_type(TYPE_3, text)

    def test_later_valid_amount_anchor_is_evaluated(self):
        unrelated = "募集规模涉及200人和5000万元。" + "其他事项。" * 200
        valid_clause = (
            "连续20个工作日基金份额持有人少于200人或者基金资产净值低于"
            "5000万元。连续60个工作日出现前述情形，基金管理人应当"
            "向中国证监会报告并提出解决方案，召开基金份额持有人大会。"
        )
        self.assert_type(TYPE_1, unrelated + valid_clause)

    def test_stage3_rejects_calendar_day_phrases(self):
        text = (
            "连续20日基金份额持有人少于200人或者基金资产净值低于5000万元。"
            "连续60日出现前述情形，基金管理人应当在10日内向中国证监会报告，"
            "并在6个月内召集基金份额持有人大会。"
        )
        self.assert_type(None, text, stage=3)

    def test_type2_features_cannot_cross_sentence_boundary(self):
        text = (
            "连续20个工作日基金份额持有人少于200人或者基金资产净值低于"
            "5000万元。连续60个工作日出现前述情形，基金管理人应当在"
            "10个工作日内向中国证监会报告。另行安排在6个月内召集"
            "基金份额持有人大会。"
        )
        self.assert_type(None, text)

    def test_type3_features_cannot_cross_sentence_boundary(self):
        text = (
            "连续20个工作日基金份额持有人少于200人或者基金资产净值低于"
            "5000万元。连续50个工作日出现前述情形，基金合同自动终止。"
            "调整管理费无需召开基金份额持有人大会。"
        )
        self.assert_type(None, text)

    def test_base_threshold_features_must_share_one_sentence(self):
        text = (
            "连续20个工作日基金份额持有人少于200人。"
            "基金资产净值低于5000万元。连续60个工作日出现前述情形，"
            "基金管理人应当在10个工作日内向中国证监会报告，并在6个月内"
            "召集基金份额持有人大会。"
        )
        self.assert_type(None, text)

    def test_type3_accepts_contract_shall_terminate_wording(self):
        text = (
            "连续20个工作日基金份额持有人少于200人或者基金资产净值低于"
            "5000万元。连续50个工作日出现前述情形，基金合同应当终止，"
            "无需召开基金份额持有人大会。"
        )
        self.assert_type(TYPE_3, text)

    def test_type3_accepts_liquidation_then_termination(self):
        text = (
            "连续20个工作日基金份额持有人少于200人或者基金资产净值低于"
            "5000万元。连续50个工作日出现前述情形，本基金进行基金财产清算并终止，"
            "无需召开基金份额持有人大会。"
        )
        self.assert_type(TYPE_3, text)

    def test_type3_accepts_no_meeting_before_sixty_day_conditions(self):
        text = (
            "连续20个工作日基金份额持有人少于200人或者基金资产净值低于"
            "5000万元。出现前述情形时基金合同应当终止并清算，无需召开"
            "基金份额持有人大会：连续60个工作日基金资产净值低于5000万元。"
        )
        self.assert_type(TYPE_3, text)

    def test_type3_accepts_continuation_sentence(self):
        text = (
            "连续20个工作日基金份额持有人少于200人或者基金资产净值低于"
            "5000万元。连续60个工作日出现前述情形，本基金清算并终止。"
            "由上述情形导致基金合同终止，无需召开基金份额持有人大会。"
        )
        self.assert_type(TYPE_3, text)

    def test_type1_accepts_explanation_and_submitted_solution(self):
        text = (
            "连续二十个工作日基金份额持有人少于二百人或者基金资产净值低于"
            "五千万元。连续六十个工作日出现前述情形，基金管理人应当向"
            "中国证监会说明原因并报送解决方案，并召开基金份额持有人大会。"
        )
        self.assert_type(TYPE_1, text, stage=2)

    def test_type1_when_ten_day_report_has_no_six_month_meeting(self):
        text = (
            "连续20个工作日基金份额持有人少于200人或者基金资产净值低于"
            "5000万元。连续60个工作日出现前述情形，基金管理人应当在"
            "10个工作日内向中国证监会报告并提出解决方案。"
        )
        self.assert_type(TYPE_1, text, stage=2)

    def test_type2_tolerates_pdf_header_inside_holder_phrase(self):
        text = (
            "连续20个工作日基金份额持有人少于200人或者基金资产净值低于"
            "5000万元。连续60个工作日出现前述情形，基金管理人应当在"
            "10个工作日内向中国证监会报告，并在6个月内召集基金份额持有"
            "某基金基金合同13人大会。"
        )
        self.assert_type(TYPE_2, text, stage=2)
    def test_type2_accepts_fifty_day_deadline_chain(self):
        text = (
            "连续20个工作日基金份额持有人少于200人或者基金资产净值低于"
            "5000万元。连续50个工作日出现前述情形，基金管理人应当在"
            "10个工作日内向中国证监会报告并提出解决方案，并在6个月内"
            "召开基金份额持有人大会。"
        )
        self.assert_type(TYPE_2, text, stage=2)

    def test_stage3_accepts_legacy_hundred_holder_direct_termination(self):
        text = (
            "基金份额持有人数量连续20个工作日达不到100人，或连续20个工作日"
            "基金资产净值低于5000万元，基金管理人应当向中国证监会报告并提出"
            "解决方案。基金份额持有人数量连续60个工作日达不到100人，或连续"
            "60个工作日基金资产净值低于5000万元，本基金应当终止，并报中国"
            "证监会备案。"
        )
        self.assert_type(None, text, stage=2)
        self.assert_type(TYPE_3, text, stage=3)

    def test_stage3_accepts_legacy_twenty_day_report_and_solution(self):
        text = (
            "基金份额持有人数量不满200人或者基金资产净值低于5000万元，"
            "基金管理人应当及时报告中国证监会；连续20个工作日出现前述情形，"
            "基金管理人应当向中国证监会说明原因并报送解决方案。"
        )
        self.assert_type(TYPE_1, text, stage=3)

    def test_amount_5000_does_not_create_false_fifty_day_trigger(self):
        text = (
            "基金份额持有人数量不满200人或者基金资产净值低于5000万元，"
            "基金管理人应当及时报告中国证监会；连续20个工作日出现前述情形，"
            "基金管理人应当向中国证监会说明原因并报送解决方案。"
        )
        actual, _, detail = classify(text, stage=3)
        self.assertEqual(TYPE_1, actual)
        self.assertFalse(detail["has_50"])
    def test_type3_accepts_thirty_day_auto_termination(self):
        """30日+1亿元自动终止变体(南方基金系列)"""
        text = (
            "连续20个工作日出现基金份额持有人数量不满200人或者基金资产净值低于"
            "5000万元情形的，基金管理人应当在定期报告中予以披露；连续30个工作日"
            "出现基金份额持有人数量不满200人或者基金资产净值低于1亿元情形的，"
            "基金合同应当终止，无需召开基金份额持有人大会。"
        )
        self.assert_type(TYPE_3, text, stage=3)

    def test_type3_accepts_thirty_day_with_chinese_numbers(self):
        """30日中文数字+1亿元自动终止"""
        text = (
            "连续二十个工作日出现基金份额持有人数量不满二百人或者基金资产净值低于"
            "五千万元情形的，基金管理人应当在定期报告中予以披露；连续三十个工作日"
            "出现前述情形的，基金合同终止，不需召开基金份额持有人大会。"
        )
        self.assert_type(TYPE_3, text, stage=3)

    def test_thirty_day_variant_classified_at_all_stages(self):
        """30日变体使用阿拉伯数字，所有阶段均可分类为类型3"""
        text = (
            "连续20个工作日出现基金份额持有人数量不满200人或者基金资产净值低于"
            "5000万元情形的，基金管理人应当在定期报告中予以披露；连续30个工作日"
            "出现基金份额持有人数量不满200人或者基金资产净值低于1亿元情形的，"
            "基金合同应当终止，无需召开基金份额持有人大会。"
        )
        self.assert_type(TYPE_3, text, stage=1)
        self.assert_type(TYPE_3, text, stage=2)
        self.assert_type(TYPE_3, text, stage=3)

    def test_old_contract_hundred_holder_manager_right_to_terminate(self):
        """旧合同100人+证监会批准后有权宣布终止(银河收益151002)"""
        text = (
            "在本系列基金合同生效后的存续期内，若连续20个工作日某个基金的持有人"
            "数量达不到100人，或连续20个工作日该基金的基金资产净值低于5000万元"
            "人民币，基金管理人应当及时向中国证监会报告，说明出现上述情况的原因"
            "以及解决方案。若连续60个工作日某个基金的持有人数量达不到100人，或"
            "连续60个工作日该基金的基金资产净值低于5000万元人民币，则基金管理人"
            "在经中国证监会批准后有权宣布该基金终止。"
        )
        self.assert_type(None, text, stage=2)
        self.assert_type(TYPE_3, text, stage=3)

    def test_invalid_stage_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "stage must be 1, 2, or 3"):
            classify("任意文本", stage=0)


if __name__ == "__main__":
    unittest.main()
