"""交班合单领域逻辑的单元测试：python3 -m unittest discover -s tests -v"""
import itertools
import random
import unittest

from src.domain import merge
from src.domain.page import HEADER_BYTES, SLOT_BYTES, SlottedPage


def order(*operations):
    return {"operations": [dict(op, **({"note": note} if note is not None else {}))
                           for op, note in operations]}


def insert(key, note):
    return ({"op": "insert", "key": key}, note)


def update(key, note):
    return ({"op": "update", "key": key}, note)


def delete(key):
    return ({"op": "delete", "key": key}, None)


def compact():
    return ({"op": "compact"}, None)


def replay_sequential(first, second, capacity):
    """把两张单首尾相接顺序重放，不做任何交错。"""
    page = SlottedPage(capacity)
    for op in (*first["operations"], *second["operations"]):
        if op["op"] == "insert":
            page.insert(op["key"], op["note"].encode())
        elif op["op"] == "update":
            page.update(op["key"], op["note"].encode())
        elif op["op"] == "delete":
            page.delete(op["key"])


def trace(result):
    return {row["履历"]: row for row in result["rows"] if row["操作"] == "追溯地址"}


class SuccessfulOpsTest(unittest.TestCase):
    def test_keeps_only_effective_writes_in_order(self):
        payload = order(
            insert("A", "甲"),
            insert("A", "重复录入被拒"),
            update("B", "不存在的履历被拒"),
            delete("B"),
            update("A", "甲改"),
            compact(),
            delete("A"),
        )
        kept = merge.successful_ops(payload, 128)
        self.assertEqual([op["op"] for op in kept], ["insert", "update", "delete"])
        self.assertEqual([op["step"] for op in kept], [1, 5, 7])
        self.assertEqual(kept[1]["note"], "甲改")

    def test_all_denied_order_contributes_nothing(self):
        payload = order(insert("A", "x" * 500), delete("NONE"))
        self.assertEqual(merge.successful_ops(payload, 64), [])


class ConflictTest(unittest.TestCase):
    def test_keys_touched_by_both_sides(self):
        ops_a = merge.successful_ops(order(insert("A", "1"), insert("B", "2"), delete("B")), 128)
        ops_b = merge.successful_ops(order(insert("B", "3"), insert("C", "4")), 128)
        self.assertEqual(merge.conflict_keys(ops_a, ops_b), ["B"])

    def test_apply_choices_drops_losing_side_only(self):
        ops_a = merge.successful_ops(order(insert("K", "白班"), insert("A", "甲")), 128)
        ops_b = merge.successful_ops(order(insert("K", "夜班"), insert("B", "乙")), 128)
        kept_a, kept_b = merge.apply_choices(ops_a, ops_b, {"K": "a"})
        self.assertEqual([op["key"] for op in kept_a], ["K", "A"])
        self.assertEqual([op["key"] for op in kept_b], ["B"])
        kept_a, kept_b = merge.apply_choices(ops_a, ops_b, {"K": "b"})
        self.assertEqual([op["key"] for op in kept_a], ["A"])
        self.assertEqual([op["key"] for op in kept_b], ["K", "B"])


class MergeRunTest(unittest.TestCase):
    def test_conflict_resolved_by_chosen_side(self):
        day = order(insert("GM-071", "白班首件"), insert("GM-072", "白班空跑"), delete("GM-072"))
        night = order(insert("GM-071", "夜班复录"), insert("GM-075", "夜班巡检"))
        for side, note in (("a", "白班首件"), ("b", "夜班复录")):
            result = merge.run_merge(day, night, 192, {"GM-071": side})
            self.assertTrue(result["fitted"])
            final = trace(result)
            self.assertEqual(final["GM-071"]["结果"], note)
            self.assertEqual(final["GM-071"]["来源"], "白班" if side == "a" else "夜班")
            self.assertIn("GM-075", final)
            self.assertNotIn("GM-072", final)
            self.assertEqual(result["metrics"]["拒绝操作"], 0)
            self.assertEqual(result["conflicts"][0]["履历"], "GM-071")

    def test_per_side_order_is_preserved(self):
        day = order(insert("A", "1"), update("A", "22"), delete("A"), insert("B", "3"))
        night = order(insert("C", "4"), update("C", "55"), insert("D", "6"))
        result = merge.run_merge(day, night, 192, {})
        self.assertTrue(result["fitted"])
        for label in ("白班", "夜班"):
            steps = [row["原步骤"] for row in result["rows"]
                     if row["来源"] == label and row["步骤"] is not None]
            self.assertEqual(steps, sorted(steps))
            self.assertTrue(steps)

    def test_interleaving_beats_any_single_side_first(self):
        # 两边各有一笔中段才撤销的大记录，最终各留两笔小记录：
        # 先跑完任何一边，另一方的大记录都撞不上空档，只有交错能全部写入。
        med, big = "x" * 30, "x" * 60
        day = order(insert("KA", med), insert("JA", big), delete("JA"), insert("LA", med))
        night = order(insert("KB", med), insert("JB", big), delete("JB"), insert("LB", med))
        capacity = HEADER_BYTES + 6 * SLOT_BYTES + 4 * 30  # 184，刚好容下最终态
        with self.assertRaises(ValueError):
            replay_sequential(day, night, capacity)
        with self.assertRaises(ValueError):
            replay_sequential(night, day, capacity)
        result = merge.run_merge(day, night, capacity, {})
        self.assertTrue(result["fitted"])
        self.assertEqual(set(trace(result)), {"KA", "LA", "KB", "LB"})

    def test_stuck_records_are_reported_when_nothing_fits(self):
        big, small = "x" * 60, "x" * 30
        day = order(insert("A1", big), insert("A2", big))
        night = order(insert("B1", small))
        capacity = HEADER_BYTES + 4 * SLOT_BYTES + 2 * 60 + 4  # 容不下三方
        result = merge.run_merge(day, night, capacity, {})
        self.assertFalse(result["fitted"])
        self.assertEqual(result["rows"], [])
        # A1 到处都放得下，不算卡住；A2 与 B1 互相顶牛。
        self.assertEqual({entry["履历"] for entry in result["stuck"]}, {"A2", "B1"})
        self.assertIn("原凭据可照常查询", result["summary"])
        for entry in result["stuck"]:
            self.assertGreater(entry["需要字节"], entry["最多可腾"])

    def test_delete_frees_room_for_the_other_side(self):
        big = "x" * 60
        day = order(insert("A", big), delete("A"))
        night = order(insert("B", big))
        capacity = HEADER_BYTES + 2 * SLOT_BYTES + 60
        result = merge.run_merge(day, night, capacity, {})
        self.assertTrue(result["fitted"])
        self.assertEqual(set(trace(result)), {"B"})

    def test_exact_fit_boundary(self):
        note = "x" * 40
        day = order(insert("A", note))
        night = order(insert("B", note))
        capacity = HEADER_BYTES + 2 * SLOT_BYTES + 2 * 40  # 分毫不差
        result = merge.run_merge(day, night, capacity, {})
        self.assertTrue(result["fitted"])
        self.assertEqual(result["metrics"]["剩余字节"], 0)

    def test_update_growth_accounting_matches_page(self):
        # 补记扩长只需要「空闲 + 旧记录」容得下新记录，不要求额外整块空闲。
        day = order(insert("A", "x" * 30), update("A", "x" * 50))
        night = order(insert("B", "y" * 30))
        capacity = HEADER_BYTES + 2 * SLOT_BYTES + 80
        result = merge.run_merge(day, night, capacity, {})
        self.assertTrue(result["fitted"])
        self.assertEqual(trace(result)["A"]["记录字节"], 50)

    def test_choice_validation(self):
        day = order(insert("K", "白"))
        night = order(insert("K", "夜"))
        with self.assertRaisesRegex(ValueError, "尚未取舍"):
            merge.run_merge(day, night, 128, {})
        with self.assertRaisesRegex(ValueError, "未冲突"):
            merge.run_merge(day, night, 128, {"K": "a", "X": "a"})
        with self.assertRaisesRegex(ValueError, "白班或夜班"):
            merge.run_merge(day, night, 128, {"K": "c"})

    def test_no_conflict_merges_without_choices(self):
        day = order(insert("A", "甲"))
        night = order(insert("B", "乙"))
        result = merge.run_merge(day, night, 128, {})
        self.assertTrue(result["fitted"])
        self.assertEqual(result["metrics"]["冲突履历"], 0)


class SearchExhaustiveTest(unittest.TestCase):
    """小规模随机操作：网格搜索与暴力枚举全部交错顺序逐一核对。"""

    def random_ops(self, rng, count, keys):
        ops = []
        live = set()
        for _ in range(count):
            candidates = [key for key in keys if key not in live]
            actions = ["insert"] if not live else ["insert", "update", "delete"]
            action = rng.choice(actions)
            if action == "insert" and candidates:
                key = rng.choice(candidates)
                ops.append(insert(key, "x" * rng.randint(1, 30)))
                live.add(key)
            elif action == "update" and live:
                ops.append(update(rng.choice(sorted(live)), "x" * rng.randint(1, 30)))
            elif action == "delete" and live:
                key = rng.choice(sorted(live))
                ops.append(delete(key))
                live.discard(key)
        return order(*ops)

    def brute_force_fits(self, ops_a, ops_b, parameter):
        total = len(ops_a) + len(ops_b)
        for picks in itertools.combinations(range(total), len(ops_a)):
            chosen = set(picks)
            page = SlottedPage(parameter)
            ia = ib = 0
            ok = True
            for position in range(total):
                if position in chosen:
                    op, ia = ops_a[ia], ia + 1
                else:
                    op, ib = ops_b[ib], ib + 1
                try:
                    if op["op"] == "insert":
                        page.insert(op["key"], op["note"].encode())
                    elif op["op"] == "update":
                        page.update(op["key"], op["note"].encode())
                    else:
                        page.delete(op["key"])
                except ValueError:
                    ok = False
                    break
            if ok:
                return True
        return False

    def test_search_matches_brute_force(self):
        rng = random.Random(6106)
        for _ in range(300):
            keys = ["K1", "K2", "K3"]
            payload_a = self.random_ops(rng, rng.randint(1, 4), keys)
            payload_b = self.random_ops(rng, rng.randint(1, 4), keys)
            parameter = rng.randint(40, 200)
            ops_a = merge.successful_ops(payload_a, parameter)
            ops_b = merge.successful_ops(payload_b, parameter)
            conflicts = merge.conflict_keys(ops_a, ops_b)
            choices = {key: rng.choice(("a", "b")) for key in conflicts}
            kept_a, kept_b = merge.apply_choices(ops_a, ops_b, choices)
            sides, stuck = merge._search(kept_a, kept_b, parameter)
            self.assertEqual(sides is not None, self.brute_force_fits(kept_a, kept_b, parameter),
                             f"parameter={parameter} a={kept_a} b={kept_b}")
            if sides is not None:
                # 搜索给出的顺序必须真的全部落盘。
                page = SlottedPage(parameter)
                ia = ib = 0
                for side in sides:
                    if side == "a":
                        op, ia = kept_a[ia], ia + 1
                    else:
                        op, ib = kept_b[ib], ib + 1
                    if op["op"] == "insert":
                        page.insert(op["key"], op["note"].encode())
                    elif op["op"] == "update":
                        page.update(op["key"], op["note"].encode())
                    else:
                        page.delete(op["key"])
            else:
                self.assertTrue(stuck)


if __name__ == "__main__":
    unittest.main()
