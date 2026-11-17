---
name: hot-path-transplant
description: Decide whether one slow function in a JavaScript/TypeScript, Python, Ruby or Go program is worth moving into native code (Rust, C/C++, Zig, WebAssembly), then do it with proof - a profiled hotspot card, an Amdahl ceiling that includes boundary-crossing cost, recorded real behaviour the new body must reproduce case for case, a noise-aware benchmark verdict, and a switch-guarded rollout with a shadow run. Use when someone says "rewrite this part in Rust", "make this native", "port the hot loop", "is a native extension worth it", or when a profile shows one function dominating.
---

# Hot Path Transplant

## What this is

A surgical procedure for performance work. You do not rewrite the app. You find the one organ that is
actually slow, check that the patient would really be healthier without it, grow a faster replacement
that behaves *identically* to the old one on recorded real inputs, and attach it behind a switch so it
can be pulled out in seconds.

## The problem it solves

"Rewrite it in Rust" is a popular instinct, and it fails in four quiet ways:

1. **Wrong organ.** People move the function that *looks* slow. The profile says the time is elsewhere.
2. **The ceiling was low.** If the function is 20 % of the run, making it infinitely fast gives at most a
   1.25x speed-up. Nobody computed that before starting.
3. **The border eats the win.** Every call from the host language into native code costs conversion
   and a crossing. A function called two million times with tiny arguments can get *slower* after
   being moved, even though its body is ten times faster.
4. **"It passes the tests" is not "it behaves the same".** Unit tests cover what someone thought of.
   Real inputs carry NaN, negative zero, emoji split mid-pair, `None`, key order that callers rely on.
   The new body silently disagrees and nobody finds out until a customer does.

This skill turns each failure into a written check with a verdict.

## When to use

- A profile (or a strong hunch you are willing to test) points at one function, module or loop.
- Someone proposes a native extension: napi-rs / Node-API, PyO3 / maturin, Cython, cgo, Ruby
  `magnus`/FFI, a WebAssembly module, or a C library behind ctypes.
- You already wrote a native version and need to know whether it is safe and worth shipping.

**Not for:** whole-application rewrites, moving to a new OS/CPU/runtime, or code whose cost is network
or disk wait (native code does not make the database answer faster).

## The procedure

Work through the six stations in order. Each one ends in a verdict; stop as soon as a verdict says stop.

### Station 1 - Freeze the workload

Write a **workload card** before measuring anything:

- the exact command or request that represents real use (`node bench.mjs big.csv`, a replayed request
  log, a pytest benchmark), with input size;
- what "faster" means for the user: wall time, p95 latency, throughput, CPU seconds, memory;
- the machine (CPU model, cores, power mode) and runtime version;
- the minimum whole-run gain worth a second language in the codebase (default 15 %).

If you cannot name a representative workload, stop: there is nothing honest to optimise yet.

### Station 2 - Profile and fill the hotspot card

Profile the frozen workload with a sampling profiler (Node `--cpu-prof` or `0x`, Python `py-spy record`
or `cProfile`, Ruby `stackprof`, Go `pprof`, Linux `perf`). Read *self* and *inclusive* time; prefer a
flame graph. For the top candidate record:

| field | meaning |
|---|---|
| `total_ms` | wall time of the whole run |
| `self_ms` | time inside the candidate plus callees that would move with it |
| `calls` | how many times the host enters it per run |
| `args_shape` | what crosses the border: numbers, short strings, big buffers, nested objects |
| `cross_us` | **measured** cost of one empty round-trip with that argument shape |
| `purity` | pure / reads globals / does I/O / calls back into host code |

Measure `cross_us` with a stub native function that does nothing but accept and return the same
shapes. Never guess it; string and object conversion often dominate.

### Station 3 - Compute the ceiling and pick a verdict

```bash
python3 scripts/transplant.py ceiling --total-ms 412 --self-ms 290 \
    --calls 1200 --cross-us 3 --kernel-speedup 6 --min-gain 0.15
```

It returns the best-case speed-up (Amdahl: the run with the function free), the crossing cost, the
expected whole-run speed-up, and one verdict:

- **LEAVE** - even a free function would not reach the minimum gain. Look elsewhere.
- **FIX-IN-PLACE** - the honest kernel gain is too small. Fix the algorithm, allocation pattern or data
  structure in the host language first (an O(n^2) scan beats any rewrite when made O(n)).
- **BATCH-FIRST** - crossings eat the win. Change the interface so one call carries a whole batch
  (an array, a buffer, a file path) and move the *loop*, not the call. Re-run the ceiling.
- **TRANSPLANT** - go on.

Use a modest `kernel_speedup` (3-8x for typical interpreted-to-native work on string/number loops; less
for code that already spends its time in C-backed library calls such as NumPy or regex engines).

### Station 4 - Record the behaviour to reproduce

Before writing native code, capture what the current function really does:

1. Wrap the function so each call during the frozen workload (and during the existing test suite)
   appends one JSON line: `{"id": ..., "in": [...], "out": ...}` or `{"id": ..., "in": [...], "error": "..."}`.
2. Add an **edge corpus**: empty, very long, Unicode outside the BMP, combining marks, negative zero,
   NaN and infinities, maximum integers, `null`/`None`, duplicated keys, unsorted input.
3. Encode values JSON cannot hold as tags: `{"$f": "nan"}`, `{"$f": "inf"}`, `{"$f": "-inf"}`, `{"$f": "-0"}`.
4. Write down which differences are allowed (float tolerance? error text? key order?) **now**, before
   the new code exists. A rule written after a mismatch is an excuse.

Aim for at least a few hundred cases; the gate warns below 50.

### Station 5 - Build the new body and diff it

Write the native version with the interface chosen in station 3. Keep the binding layer thin and the
core in plain safe code that can be tested without the host runtime. Catch native panics/exceptions
at the border and turn them into host errors. Then replay the recorded inputs through it and run:

```bash
python3 scripts/transplant.py diff recorded.jsonl replayed.jsonl --rel-tol 1e-12
```

Rules the diff applies: NaN equals NaN; `-0` and `0` differ unless `--ignore-signed-zero`; booleans never
equal numbers; both sides raising counts as agreement (add `--compare-errors` if the message is part of
the contract); a missing case is a mismatch. Fix every mismatch at the root and re-run until **MATCH**.

Typical root causes: string length counted in bytes vs characters vs UTF-16 units; integer overflow
where the host had big integers; different rounding; sort stability; hash-map iteration order.

### Station 6 - Benchmark, then ship behind a switch

Time the frozen workload at least 10 times each way on a quiet machine (same build flags you will
ship, release mode, warm-up runs discarded). One number per line in a file, then:

```bash
python3 scripts/transplant.py bench base_ms.txt cand_ms.txt --min-gain 0.10
```

Verdicts: **FASTER** (95 % bootstrap interval of the median ratio is above the bar), **MARGINAL**,
**SAME**, **SLOWER**, **NOISY** (coefficient of variation above 10 % - fix the measurement, not the code).

Ship with a runtime switch (environment variable or config flag) that falls back to the original.
Optionally run a **shadow** period: production calls the original, also calls the new body, compares,
logs disagreements, and always returns the original result. Then combine the evidence:

```bash
python3 scripts/transplant.py gate diff.json bench.json --shadow-json shadow.json
```

Decisions: **SHIP**, **SHIP-BEHIND-SWITCH** (good, but small sample or no shadow yet),
**HOLD-MISMATCH**, **HOLD-NOISY**, **DROP** (keep the original; delete the native code).

## Output: the transplant report

Finish with a short report the team can read in one minute:

```
Workload   : csv-import 50k rows, wall time, M2 Pro, Node 22
Hotspot    : normalizeRow()  70 % self, 1 200 calls, 3 us/crossing
Ceiling    : best 3.38x, expected 2.37x -> TRANSPLANT
Behaviour  : 612 recorded cases, MATCH (rel-tol 1e-12, errors not compared)
Benchmark  : 410 ms -> 149 ms, 2.75x [2.71-2.80], cv 2.4 % -> FASTER
Shadow     : 3 days, 41 k calls, 0 disagreements
Decision   : SHIP  (switch: CSV_NATIVE=0 restores the JS path)
Not done   : parseDates() still JS - 9 % share, LEAVE
```

## Examples

**"Our Python report job takes 40 minutes; rewrite it in Rust."** Profile shows 70 % in `pandas.read_sql`
waiting on the database and 6 % in a pure-Python `clean_name()` -> ceiling for `clean_name` is 1.06x ->
LEAVE. The report recommends a database index instead. No Rust written.

**"Port `tokenize()` from TypeScript to Rust with napi-rs."** 55 % share but 3 million calls per run with
short strings at 0.4 us each -> BATCH-FIRST. Interface changed to `tokenizeAll(lines: string[])`;
re-run ceiling -> TRANSPLANT; diff finds UTF-16 vs UTF-8 offsets on emoji, fixed; bench 2.1x -> SHIP-BEHIND-SWITCH.

**"I already wrote a Go -> C version of our checksum."** Diff MATCH on 2 000 cases, bench interval
[0.97, 1.04] -> SAME -> DROP. The C code is removed; cgo build complexity is not free.

## Tips

- Measure the border with a do-nothing native function first; it settles most debates in five minutes.
- Profile in release/production mode. Debug builds of either side lie.
- Move leaves first: a native function that calls back into the host pays the border twice.
- If the hot code already sits inside C-backed libraries (NumPy, regex, JSON parsers), native rewrites
  gain little; vectorise or batch instead.
- Keep the switch for at least one release after shipping; delete the old path only after the shadow
  log is clean.
- Record the run that made the decision (workload card + JSON outputs) next to the code, so the next
  person can re-run it when the runtime gets faster.

## Helper

`scripts/transplant.py` (Python 3.8+, standard library only): `ceiling`, `diff`, `bench`, `gate`.
Each prints JSON and exits non-zero for a stop verdict, so it can run in CI.
