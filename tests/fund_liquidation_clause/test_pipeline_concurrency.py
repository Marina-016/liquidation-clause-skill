import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


SKILL_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import csrc_api

try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    sys.modules["requests"] = MagicMock()

import datayes_api


class DatayesPipelineTests(unittest.TestCase):
    def test_streaming_pipeline_deduplicates_urls_and_preserves_fund_order(self):
        funds = [
            ("000001", "基金A", "管理人", "一级", "二级"),
            ("000002", "基金B", "管理人", "一级", "二级"),
            ("000003", "基金C", "管理人", "一级", "二级"),
        ]
        urls = {
            "000001": "https://bigdata-s3.wmcloud.com/shared.pdf",
            "000002": "https://bigdata-s3.wmcloud.com/shared.pdf",
            "000003": "https://bigdata-s3.wmcloud.com/unique.pdf",
        }
        completed = []

        def fake_find_contract(code, stage):
            return {"s3Url": urls[code], "source": "基金合同"}

        def fake_download(url, out_dir):
            return str(Path(out_dir) / Path(url).name)

        with tempfile.TemporaryDirectory(dir=SKILL_ROOT) as temp_dir:
            with (
                patch.object(
                    datayes_api,
                    "find_contract",
                    side_effect=fake_find_contract,
                ),
                patch.object(
                    datayes_api,
                    "download_pdf",
                    side_effect=fake_download,
                ) as download_mock,
                patch.object(
                    datayes_api,
                    "parse_pdf",
                    return_value=("合同正文", None),
                ) as parse_mock,
                patch.object(
                    datayes_api,
                    "classify",
                    return_value=("类型1: 备案", "命中条款", {}),
                ),
            ):
                classified, not_found = datayes_api.run_stage(
                    funds,
                    temp_dir,
                    stage=1,
                    verbose=False,
                    on_result=completed.append,
                )

        self.assertEqual([], not_found)
        self.assertEqual([fund[0] for fund in funds], [r["code"] for r in classified])
        self.assertEqual(2, download_mock.call_count)
        self.assertEqual(2, parse_mock.call_count)
        self.assertEqual(3, len(completed))

    def test_complete_contract_is_preferred_over_fee_change_notice(self):
        response = {
            "code": 1,
            "data": {
                "list": [
                    {
                        "title": "关于降低管理费率并修改基金合同的公告",
                        "classifyName": "基金合同",
                        "classifyId": "1",
                        "s3Url": "https://bigdata-s3.wmcloud.com/fee.pdf",
                    },
                    {
                        "title": "示例基金基金合同（修订版）",
                        "classifyName": "基金合同",
                        "classifyId": "1",
                        "s3Url": "https://bigdata-s3.wmcloud.com/contract.pdf",
                    },
                ]
            },
        }
        with patch.object(datayes_api, "api_get", return_value=response):
            contract = datayes_api.find_contract("000001", stage=1)

        self.assertEqual(
            "https://bigdata-s3.wmcloud.com/contract.pdf",
            contract["s3Url"],
        )

    def test_relaxed_retry_uses_contract_lookup_and_stage2_rules(self):
        fund = ("000001", "基金A", "管理人", "一级", "二级")

        with tempfile.TemporaryDirectory(dir=SKILL_ROOT) as temp_dir:
            with (
                patch.object(
                    datayes_api,
                    "find_contract",
                    return_value={
                        "s3Url": "https://bigdata-s3.wmcloud.com/contract.pdf",
                        "source": "基金合同",
                    },
                ) as find_mock,
                patch.object(
                    datayes_api,
                    "download_pdf",
                    return_value=str(Path(temp_dir) / "contract.pdf"),
                ),
                patch.object(
                    datayes_api,
                    "parse_pdf",
                    return_value=("合同正文", None),
                ) as parse_mock,
                patch.object(
                    datayes_api,
                    "classify",
                    return_value=("类型1: 备案", "命中条款", {}),
                ),
            ):
                classified, not_found = datayes_api.run_stage(
                    [fund],
                    temp_dir,
                    stage=2,
                    document_stage=1,
                    source_override="基金合同(宽松复判)",
                    verbose=False,
                )

        self.assertEqual([], not_found)
        find_mock.assert_called_once_with("000001", 1)
        parse_mock.assert_called_once_with(
            str(Path(temp_dir) / "contract.pdf"),
            2,
        )
        self.assertEqual("基金合同(宽松复判)", classified[0]["source"])
    def test_worker_limits_are_increased_by_pipeline_stage(self):
        self.assertEqual(16, datayes_api.API_WORKERS)
        self.assertEqual(16, datayes_api.DL_WORKERS)
        self.assertEqual(8, datayes_api.PARSE_WORKERS)
        self.assertEqual(8, csrc_api.CSRC_WORKERS)


if __name__ == "__main__":
    unittest.main()