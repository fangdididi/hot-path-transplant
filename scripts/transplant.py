#!/usr/bin/env python3
"""hot-path-transplant helper: ceiling, diff, bench, gate.

Standard library only. Every command prints JSON and sets an exit code
so an agent (or CI) can act on the result without parsing prose.
"""
import argparse
import json
import math
import random
import statistics
import sys

# ---------------------------------------------------------------- ceiling

def ceiling(total_ms, self_ms, calls, cross_us, kernel_speedup, min_gain):
    """Decide whether moving one hot function to native code can pay off.

    total_ms        wall time of the whole representative run
    self_ms         time spent inside the candidate (inclusive of its callees
                    that would move with it)
    calls           how many times the candidate is entered during the run
    cross_us        measured cost of one crossing of the language boundary,
                    including argument/result conversion, in microseconds
    kernel_speedup  honest guess of how much faster the moved body runs
    min_gain        smallest whole-run gain worth the extra build (e.g. 0.15)
    """
    if total_ms <= 0 or self_ms < 0 or self_ms > total_ms:
        raise ValueError("need 0 <= self_ms <= total_ms and total_ms > 0")
    share = self_ms / total_ms
    rest = total_ms - self_ms
    # Amdahl: even if the function became free, the run cannot beat this.
    best_case = total_ms / rest if rest > 0 else math.inf
    crossing_ms = calls * cross_us / 1000.0
    moved_ms = self_ms / kernel_speedup + crossing_ms
    new_total = rest + moved_ms
    expected = total_ms / new_total
    crossing_share = crossing_ms / moved_ms if moved_ms > 0 else 0.0
    if best_case < 1 + min_gain:
        verdict, why = "LEAVE", "even a free function would not reach the minimum gain"
    elif expected < 1 + min_gain and crossing_share >= 0.5:
        verdict, why = "BATCH-FIRST", "boundary crossings eat the win; move a loop, not a call"
    elif expected < 1 + min_gain:
        verdict, why = "FIX-IN-PLACE", "kernel gain too small after crossings; fix the algorithm where it lives"
    else:
        verdict, why = "TRANSPLANT", "expected whole-run gain clears the bar"
    return {
        "share": round(share, 4),
        "best_case_speedup": None if math.isinf(best_case) else round(best_case, 3),
        "crossing_ms": round(crossing_ms, 3),
        "crossing_share_of_moved": round(crossing_share, 3),
        "expected_speedup": round(expected, 3),
        "min_gain": min_gain,
        "verdict": verdict,
        "why": why,
    }

# ---------------------------------------------------------------- diff

def _same(a, b, rel_tol, abs_tol, signed_zero, key_order):
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b if isinstance(a, bool) and isinstance(b, bool) else False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        fa, fb = float(a), float(b)
        if math.isnan(fa) or math.isnan(fb):
            return math.isnan(fa) and math.isnan(fb)
        if signed_zero and fa == 0.0 and fb == 0.0:
            return math.copysign(1, fa) == math.copysign(1, fb)
        if isinstance(a, int) and isinstance(b, int):
            return a == b
        return math.isclose(fa, fb, rel_tol=rel_tol, abs_tol=abs_tol)
    if isinstance(a, dict) and isinstance(b, dict):
        if key_order and list(a.keys()) != list(b.keys()):
            return False
        if set(a) != set(b):
            return False
        return all(_same(a[k], b[k], rel_tol, abs_tol, signed_zero, key_order) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(
            _same(x, y, rel_tol, abs_tol, signed_zero, key_order) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


def _decode(v):
    """Tagged specials survive JSON: {"$f": "nan"|"inf"|"-inf"|"-0"}."""
    if isinstance(v, dict):
        if set(v) == {"$f"}:
            return {"nan": math.nan, "inf": math.inf, "-inf": -math.inf, "-0": -0.0}[v["$f"]]
        return {k: _decode(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_decode(x) for x in v]
    return v


def load_cases(path):
    cases = {}
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            row = json.loads(line)
            if "id" not in row:
                raise ValueError(f"{path}:{n}: every case needs an id")
            cases[str(row["id"])] = row
    return cases


def diff(old_path, new_path, rel_tol=0.0, abs_tol=0.0, signed_zero=True,
         key_order=False, compare_errors=False, show=5):
    old, new = load_cases(old_path), load_cases(new_path)
    missing = sorted(set(old) - set(new))
    extra = sorted(set(new) - set(old))
    mismatches, agree = [], 0
    for cid in sorted(set(old) & set(new)):
        a, b = old[cid], new[cid]
        a_err, b_err = "error" in a, "error" in b
        if a_err or b_err:
            ok = a_err and b_err and (not compare_errors or a["error"] == b["error"])
        else:
            ok = _same(_decode(a.get("out")), _decode(b.get("out")),
                       rel_tol, abs_tol, signed_zero, key_order)
        if ok:
            agree += 1
        else:
            mismatches.append({"id": cid, "old": a.get("error", a.get("out")),
                               "new": b.get("error", b.get("out"))})
    verdict = "MATCH" if not mismatches and not missing else "MISMATCH"
    return {"cases": len(old), "agree": agree, "mismatch": len(mismatches),
            "missing_in_new": missing, "extra_in_new": extra,
            "first_mismatches": mismatches[:show], "verdict": verdict}

# ---------------------------------------------------------------- bench

def _read_times(path):
    with open(path, encoding="utf-8") as fh:
        vals = [float(x) for x in fh.read().split() if x and not x.startswith("#")]
    if len(vals) < 5:
        raise ValueError(f"{path}: need at least 5 timings, got {len(vals)}")
    return vals


def bench(base, cand, min_gain=0.10, max_cv=0.10, rounds=2000, seed=7):
    """Compare two timing samples (lower is better) with a bootstrap CI on
    the ratio of medians. Speedup > 1 means the candidate is faster."""
    rng = random.Random(seed)
    mb, mc = statistics.median(base), statistics.median(cand)
    ratios = []
    for _ in range(rounds):
        sb = statistics.median(rng.choices(base, k=len(base)))
        sc = statistics.median(rng.choices(cand, k=len(cand)))
        ratios.append(sb / sc)
    ratios.sort()
    lo, hi = ratios[int(0.025 * rounds)], ratios[int(0.975 * rounds) - 1]
    cv = max(statistics.pstdev(base) / statistics.mean(base),
             statistics.pstdev(cand) / statistics.mean(cand))
    if cv > max_cv:
        verdict = "NOISY"
    elif lo >= 1 + min_gain:
        verdict = "FASTER"
    elif hi < 1.0:
        verdict = "SLOWER"
    elif lo > 1.0:
        verdict = "MARGINAL"
    else:
        verdict = "SAME"
    return {"base_median": mb, "cand_median": mc, "speedup": round(mb / mc, 3),
            "ci95": [round(lo, 3), round(hi, 3)], "worst_cv": round(cv, 3),
            "min_gain": min_gain, "verdict": verdict}

# ---------------------------------------------------------------- gate

def gate(diff_res, bench_res, shadow=None):
    """Combine the evidence into one ship decision."""
    reasons = []
    if diff_res.get("verdict") != "MATCH":
        return {"decision": "HOLD-MISMATCH", "reasons": ["recorded behaviour differs"]}
    if diff_res.get("cases", 0) < 50:
        reasons.append("fewer than 50 recorded cases")
    bv = bench_res.get("verdict")
    if bv == "SLOWER":
        return {"decision": "DROP", "reasons": ["candidate is slower; keep the original"]}
    if bv in ("NOISY",):
        return {"decision": "HOLD-NOISY", "reasons": ["timings too noisy to judge; quiet the machine and re-run"]}
    if bv in ("SAME", "MARGINAL"):
        return {"decision": "DROP", "reasons": ["no gain worth a second language"]}
    if shadow is not None:
        if shadow.get("disagree", 0) > 0:
            return {"decision": "HOLD-MISMATCH", "reasons": ["shadow run disagreed on real traffic"]}
        if shadow.get("calls", 0) == 0:
            reasons.append("shadow run saw no traffic")
    else:
        reasons.append("no shadow run yet; ship behind a switch")
    return {"decision": "SHIP" if not reasons else "SHIP-BEHIND-SWITCH", "reasons": reasons}

# ---------------------------------------------------------------- cli

EXIT = {"TRANSPLANT": 0, "LEAVE": 3, "BATCH-FIRST": 4, "FIX-IN-PLACE": 5,
        "MATCH": 0, "MISMATCH": 1, "FASTER": 0, "SLOWER": 1, "NOISY": 2,
        "SAME": 3, "MARGINAL": 3, "SHIP": 0, "SHIP-BEHIND-SWITCH": 0,
        "HOLD-MISMATCH": 1, "HOLD-NOISY": 2, "DROP": 3}


def main(argv=None):
    p = argparse.ArgumentParser(prog="transplant.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("ceiling", help="is the hot function worth moving?")
    c.add_argument("--total-ms", type=float, required=True)
    c.add_argument("--self-ms", type=float, required=True)
    c.add_argument("--calls", type=int, required=True)
    c.add_argument("--cross-us", type=float, required=True)
    c.add_argument("--kernel-speedup", type=float, default=5.0)
    c.add_argument("--min-gain", type=float, default=0.15)
    d = sub.add_parser("diff", help="compare recorded old vs new outputs")
    d.add_argument("old"); d.add_argument("new")
    d.add_argument("--rel-tol", type=float, default=0.0)
    d.add_argument("--abs-tol", type=float, default=0.0)
    d.add_argument("--ignore-signed-zero", action="store_true")
    d.add_argument("--key-order", action="store_true")
    d.add_argument("--compare-errors", action="store_true")
    b = sub.add_parser("bench", help="ratio of medians with bootstrap CI")
    b.add_argument("base"); b.add_argument("cand")
    b.add_argument("--min-gain", type=float, default=0.10)
    b.add_argument("--max-cv", type=float, default=0.10)
    g = sub.add_parser("gate", help="one ship decision from diff+bench(+shadow) JSON")
    g.add_argument("diff_json"); g.add_argument("bench_json")
    g.add_argument("--shadow-json")
    a = p.parse_args(argv)
    if a.cmd == "ceiling":
        res = ceiling(a.total_ms, a.self_ms, a.calls, a.cross_us, a.kernel_speedup, a.min_gain)
        key = res["verdict"]
    elif a.cmd == "diff":
        res = diff(a.old, a.new, a.rel_tol, a.abs_tol, not a.ignore_signed_zero,
                   a.key_order, a.compare_errors)
        key = res["verdict"]
    elif a.cmd == "bench":
        res = bench(_read_times(a.base), _read_times(a.cand), a.min_gain, a.max_cv)
        key = res["verdict"]
    else:
        load = lambda f: json.load(open(f, encoding="utf-8"))
        res = gate(load(a.diff_json), load(a.bench_json),
                   load(a.shadow_json) if a.shadow_json else None)
        key = res["decision"]
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return EXIT.get(key, 9)


if __name__ == "__main__":
    sys.exit(main())
