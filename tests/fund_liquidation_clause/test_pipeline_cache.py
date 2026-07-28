import json
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from pipeline import (
    CACHE_SCHEMA_VERSION,
    PIPELINE_VERSION,
    filter_unclassified_funds,
    load_cached_results,
    save_results_cache,
)


class PipelineCacheTests(unittest.TestCase):
    def setUp(self):
        self.funds = [
            ("000001", "新名称A", "新管理人A", "一级A", "二级A"),
            ("000002", "基金B", "管理人B", "一级B", "二级B"),
        ]

    def test_resume_reuses_only_successful_results_and_refreshes_metadata(self):
        cached = [
            {
                "code": "000001",
                "name": "旧名称",
                "mgr": "旧管理人",
                "type1": "旧一级",
                "type2": "旧二级",
                "clauseType": "类型1: 备案",
                "clauseText": "条款",
                "stage": 1,
            },
            {
                "code": "000002",
                "name": "基金B",
                "clauseType": None,
                "reason": "上次失败",
            },
        ]
        with tempfile.TemporaryDirectory(dir=SKILL_ROOT) as temp_dir:
            cache_path = str(Path(temp_dir) / "results_cache.json")
            save_results_cache(cache_path, cached)
            reused, pending = load_cached_results(cache_path, self.funds)

            with open(cache_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)

        self.assertEqual(CACHE_SCHEMA_VERSION, payload["schemaVersion"])
        self.assertEqual(PIPELINE_VERSION, payload["pipelineVersion"])
        self.assertEqual(["000001"], [result["code"] for result in reused])
        self.assertEqual("新名称A", reused[0]["name"])
        self.assertEqual("新管理人A", reused[0]["mgr"])
        self.assertEqual([self.funds[1]], pending)

    def test_legacy_list_cache_is_not_reused(self):
        with tempfile.TemporaryDirectory(dir=SKILL_ROOT) as temp_dir:
            cache_path = Path(temp_dir) / "results_cache.json"
            cache_path.write_text(
                json.dumps([{"code": "000001", "clauseType": "类型1: 备案"}]),
                encoding="utf-8",
            )
            reused, pending = load_cached_results(str(cache_path), self.funds)

        self.assertEqual([], reused)
        self.assertEqual(self.funds, pending)

    def test_filter_unclassified_funds_excludes_any_successful_code(self):
        results = [{"code": "000001", "clauseType": "类型1: 备案"}]
        candidates = filter_unclassified_funds(self.funds, results)
        self.assertEqual([self.funds[1]], candidates)


if __name__ == "__main__":
    unittest.main()