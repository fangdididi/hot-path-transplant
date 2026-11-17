import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import transplant as t


def write(lines):
    f = tempfile.NamedTemporaryFile("w", delete=False, suffix=".jsonl")
    f.write("\n".join(json.dumps(x) for x in lines)); f.close()
    return f.name


class Ceiling(unittest.TestCase):
    def test_small_share_is_leave(self):
        r = t.ceiling(1000, 80, 10, 1, 10, 0.15)
        self.assertEqual(r["verdict"], "LEAVE")
        self.assertLess(r["best_case_speedup"], 1.15)

    def test_chatty_boundary_is_batch_first(self):
        # 600 ms inside, but 2 million tiny calls at 0.5 us each = 1000 ms crossing
        r = t.ceiling(1000, 600, 2_000_000, 0.5, 10, 0.15)
        self.assertEqual(r["verdict"], "BATCH-FIRST")

    def test_good_candidate_is_transplant(self):
        r = t.ceiling(1000, 700, 100, 2, 8, 0.15)
        self.assertEqual(r["verdict"], "TRANSPLANT")
        self.assertGreater(r["expected_speedup"], 2)

    def test_weak_kernel_is_fix_in_place(self):
        r = t.ceiling(1000, 400, 10, 1, 1.2, 0.15)
        self.assertEqual(r["verdict"], "FIX-IN-PLACE")

    def test_bad_input_rejected(self):
        with self.assertRaises(ValueError):
            t.ceiling(100, 200, 1, 1, 2, 0.1)


class Diff(unittest.TestCase):
    def test_nan_and_both_errors_agree(self):
        old = write([{"id": 1, "out": {"$f": "nan"}}, {"id": 2, "error": "TypeError: x"}])
        new = write([{"id": 1, "out": {"$f": "nan"}}, {"id": 2, "error": "panic: y"}])
        self.assertEqual(t.diff(old, new)["verdict"], "MATCH")

    def test_signed_zero_and_missing_case(self):
        old = write([{"id": 1, "out": {"$f": "-0"}}, {"id": 2, "out": 1}])
        new = write([{"id": 1, "out": 0}])
        r = t.diff(old, new)
        self.assertEqual(r["verdict"], "MISMATCH")
        self.assertEqual(r["missing_in_new"], ["2"])
        self.assertEqual(r["mismatch"], 1)
        self.assertEqual(t.diff(old, new, signed_zero=False)["mismatch"], 0)

    def test_tolerance_and_key_order(self):
        old = write([{"id": "a", "out": {"x": 1.0, "y": 2}}])
        new = write([{"id": "a", "out": {"y": 2, "x": 1.0000001}}])
        self.assertEqual(t.diff(old, new)["verdict"], "MISMATCH")
        self.assertEqual(t.diff(old, new, rel_tol=1e-6)["verdict"], "MATCH")
        self.assertEqual(t.diff(old, new, rel_tol=1e-6, key_order=True)["verdict"], "MISMATCH")

    def test_bool_is_not_number(self):
        old = write([{"id": 1, "out": True}]); new = write([{"id": 1, "out": 1}])
        self.assertEqual(t.diff(old, new)["verdict"], "MISMATCH")


class Bench(unittest.TestCase):
    def test_clear_win(self):
        base = [100, 101, 99, 100, 102, 98, 100, 101]
        cand = [40, 41, 39, 40, 40, 41, 39, 40]
        r = t.bench(base, cand)
        self.assertEqual(r["verdict"], "FASTER")
        self.assertGreater(r["ci95"][0], 2)

    def test_slower_and_noisy(self):
        base = [50, 51, 49, 50, 50, 51]
        self.assertEqual(t.bench(base, [60, 61, 59, 60, 60, 61])["verdict"], "SLOWER")
        self.assertEqual(t.bench(base, [20, 90, 15, 80, 30, 70])["verdict"], "NOISY")


class Gate(unittest.TestCase):
    def test_decisions(self):
        ok = {"verdict": "MATCH", "cases": 500}
        fast = {"verdict": "FASTER"}
        self.assertEqual(t.gate(ok, fast, {"calls": 9000, "disagree": 0})["decision"], "SHIP")
        self.assertEqual(t.gate(ok, fast)["decision"], "SHIP-BEHIND-SWITCH")
        self.assertEqual(t.gate(ok, fast, {"calls": 9000, "disagree": 3})["decision"], "HOLD-MISMATCH")
        self.assertEqual(t.gate({"verdict": "MISMATCH"}, fast)["decision"], "HOLD-MISMATCH")
        self.assertEqual(t.gate(ok, {"verdict": "SLOWER"})["decision"], "DROP")
        self.assertEqual(t.gate(ok, {"verdict": "SAME"})["decision"], "DROP")


if __name__ == "__main__":
    unittest.main()
