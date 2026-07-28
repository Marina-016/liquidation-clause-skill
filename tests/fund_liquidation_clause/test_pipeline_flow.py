import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import csrc_api
import datayes_api
import gen_excel
import pipeline


class PipelineFlowTests(unittest.TestCase):
    def test_stage2_retries_contract_before_alternative_sources(self):
        funds = [
            ("000001", "基金A", "管理人", "一级", "二级"),
            ("000002", "基金B", "管理人", "一级", "二级"),
        ]
        result_a = {
            "code": "000001",
            "name": "基金A",
            "mgr": "管理人",
            "type1": "一级",
            "type2": "二级",
            "clauseType": "类型1: 备案",
            "clauseText": "条款A",
            "source": "基金合同(宽松复判)",
            "stage": 2,
        }
        result_b = {
            "code": "000002",
            "name": "基金B",
            "mgr": "管理人",
            "type1": "一级",
            "type2": "二级",
            "clauseType": "类型3: 自动触发终止",
            "clauseText": "条款B",
            "source": "CSRC证监会(基金合同)",
            "stage": 3,
        }
        datayes_calls = []

        def fake_datayes(input_funds, out_dir, **kwargs):
            datayes_calls.append((list(input_funds), dict(kwargs)))
            callback = kwargs["on_result"]
            if kwargs["stage"] == 1:
                for fund in input_funds:
                    callback(
                        {
                            "code": fund[0],
                            "reason": "严格规则未命中",
                            "clauseType": None,
                            "source": "基金合同",
                        }
                    )
                return [], list(input_funds)
            if kwargs.get("document_stage") == 1:
                callback(result_a)
                callback(
                    {
                        "code": "000002",
                        "reason": "宽松规则未命中",
                        "clauseType": None,
                        "source": "基金合同(宽松复判)",
                    }
                )
                return [result_a], [funds[1]]
            callback(
                {
                    "code": "000002",
                    "reason": "替代源未命中",
                    "clauseType": None,
                    "source": "招募说明书",
                }
            )
            return [], [funds[1]]

        def fake_csrc(input_funds, out_dir, **kwargs):
            kwargs["on_result"](result_b)
            return [result_b], []

        with tempfile.TemporaryDirectory() as temp_dir:
            argv = [
                "pipeline.py",
                str(Path(temp_dir) / "input.xlsx"),
                "--work-dir",
                temp_dir,
                "--output",
                str(Path(temp_dir) / "output.xlsx"),
            ]
            with (
                patch.dict(os.environ, {"DATAYES_TOKEN": "test-token"}),
                patch.object(sys, "argv", argv),
                patch.object(pipeline, "read_fund_list", return_value=funds),
                patch.object(pipeline, "save_results_cache"),
                patch.object(datayes_api, "run_stage", side_effect=fake_datayes),
                patch.object(csrc_api, "run_stage", side_effect=fake_csrc),
                patch.object(gen_excel, "generate"),
            ):
                pipeline.main()

        self.assertEqual(3, len(datayes_calls))
        self.assertEqual(1, datayes_calls[1][1]["document_stage"])
        self.assertEqual(2, datayes_calls[2][1]["document_stage"])
        self.assertEqual(["000002"], [fund[0] for fund in datayes_calls[2][0]])


if __name__ == "__main__":
    unittest.main()