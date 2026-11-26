"""演示数据与交班合单的端到端口径：python3 -m unittest discover -s tests -v"""
import unittest

from src.domain import merge, scenario
from src.domain.fixtures import FIXTURES, MERGE_FIXTURE


class DemoFixtureTest(unittest.TestCase):
    def test_merge_fixture_pair_merges_after_choices(self):
        payloads = {fixture["code"]: fixture["payload"] for fixture in FIXTURES}
        day = payloads[MERGE_FIXTURE["day"]]
        night = payloads[MERGE_FIXTURE["night"]]
        parameter = MERGE_FIXTURE["parameter"]
        # 两张单各自在原容量下完工。
        for payload in (day, night):
            self.assertIsNotNone(scenario.run(payload, parameter, None))
        ops_a = merge.successful_ops(day, parameter)
        ops_b = merge.successful_ops(night, parameter)
        # GM-071 两边都动过，是演示里的冲突履历。
        self.assertEqual(merge.conflict_keys(ops_a, ops_b), ["GM-071"])
        # 无论质保选哪边，交错后都能装回原容量。
        for side in ("a", "b"):
            result = merge.run_merge(day, night, parameter, {"GM-071": side})
            self.assertTrue(result["fitted"], side)
            self.assertEqual(result["metrics"]["冲突履历"], 1)
            final = {row["履历"]: row for row in result["rows"] if row["操作"] == "追溯地址"}
            self.assertIn("GM-071", final)
            self.assertIn("GM-075", final)


if __name__ == "__main__":
    unittest.main()
