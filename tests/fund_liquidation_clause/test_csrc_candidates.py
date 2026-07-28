import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import csrc_api


class CsrcCandidateTests(unittest.TestCase):
    def test_search_payload_covers_legacy_contracts(self):
        payload = json.loads(
            csrc_api._build_search_payload("001115", "FA020010", 0)
        )
        values = {item["name"]: item["value"] for item in payload}

        self.assertEqual("2000-01-01", values["startUploadDate"])
        self.assertEqual(str(csrc_api.CSRC_PAGE_SIZE), values["iDisplayLength"])

    def test_search_candidates_paginates_deduplicates_and_ranks_contracts(self):
        first_page = {
            "iTotalRecords": 3,
            "aaData": [
                {
                    "uploadInfoId": 1,
                    "reportName": "关于降低费率并修改基金合同的公告",
                    "reportSendDate": "2025-01-01",
                },
                {
                    "uploadInfoId": 2,
                    "reportName": "示例基金基金合同",
                    "reportSendDate": "2020-01-01",
                },
            ],
        }
        second_page = {
            "iTotalRecords": 3,
            "aaData": [
                {
                    "uploadInfoId": 2,
                    "reportName": "示例基金基金合同",
                    "reportSendDate": "2020-01-01",
                },
                {
                    "uploadInfoId": 3,
                    "reportName": "示例基金基金合同（修订版）",
                    "reportSendDate": "2024-01-01",
                },
            ],
        }
        with (
            patch.object(csrc_api, "CSRC_PAGE_SIZE", 2),
            patch.object(
                csrc_api,
                "_fetch_search_page",
                side_effect=[first_page, second_page],
            ),
        ):
            candidates = csrc_api.search_csrc_candidates(
                "000001",
                "FA020010",
            )

        self.assertEqual([3, 2, 1], [item["uploadId"] for item in candidates])

    def test_process_tries_next_contract_after_rule_miss(self):
        candidates = [
            {"uploadId": 1, "name": "旧合同", "rt": "FA020010"},
            {"uploadId": 2, "name": "新合同", "rt": "FA020010"},
        ]

        def fake_search(code, report_type):
            return candidates if report_type == "FA020010" else []

        with (
            patch.object(
                csrc_api,
                "search_csrc_candidates",
                side_effect=fake_search,
            ),
            patch.object(csrc_api, "download_csrc_pdf", return_value="x.pdf"),
            patch.object(
                csrc_api,
                "parse_csrc_pdf",
                side_effect=["基金合同旧正文", "基金合同新正文"],
            ),
            patch.object(
                csrc_api,
                "classify",
                side_effect=[
                    (None, "", {"anchor": True}),
                    ("类型3: 自动触发终止", "命中条款", {}),
                ],
            ),
        ):
            result = csrc_api.process_fund_csrc(
                ("000001", "基金A", "管理人", "一级", "二级"),
                ".",
                verbose=False,
            )

        self.assertEqual("类型3: 自动触发终止", result["clauseType"])
        self.assertIn("instanceid=2", result["s3Url"])
        self.assertEqual(
            "CSRC_RULE_NO_MATCH",
            result["candidateAttempts"][0]["status"],
        )
        self.assertEqual("CSRC_CLASSIFIED", result["candidateAttempts"][1]["status"])

    def test_series_fund_uses_known_csrc_index_alias(self):
        def fake_search(code, report_type):
            if code == "151001" and report_type == "FA020010":
                return [
                    {
                        "uploadId": 9,
                        "name": "银河银联系列证券投资基金基金合同",
                        "rt": report_type,
                    }
                ]
            return []

        with (
            patch.object(
                csrc_api,
                "search_csrc_candidates",
                side_effect=fake_search,
            ),
            patch.object(csrc_api, "download_csrc_pdf", return_value="x.pdf"),
            patch.object(csrc_api, "parse_csrc_pdf", return_value="基金合同正文"),
            patch.object(
                csrc_api,
                "classify",
                return_value=("类型3: 自动触发终止", "命中条款", {}),
            ),
        ):
            result = csrc_api.process_fund_csrc(
                ("151002", "银河收益", "管理人", "一级", "二级"),
                ".",
                verbose=False,
            )

        self.assertEqual("类型3: 自动触发终止", result["clauseType"])
        self.assertIn("instanceid=9", result["s3Url"])

    def test_process_keeps_structured_reason_when_no_records(self):
        with patch.object(csrc_api, "search_csrc_candidates", return_value=[]):
            result = csrc_api.process_fund_csrc(
                ("000001", "基金A", "管理人", "一级", "二级"),
                ".",
                verbose=False,
            )

        self.assertIsNone(result["clauseType"])
        self.assertIn("CSRC_NO_RECORDS=2", result["reason"])
        self.assertEqual(2, len(result["candidateAttempts"]))


if __name__ == "__main__":
    unittest.main()