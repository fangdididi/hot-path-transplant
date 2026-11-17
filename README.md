**English** | [简体中文](README.zh-CN.md)

# hot-path-transplant

An agent skill for the moment someone says **"let's rewrite this part in Rust"**.

It decides whether one slow function in a JavaScript/TypeScript, Python, Ruby or Go program is really
worth moving into native code (Rust, C/C++, Zig, WebAssembly) - and if it is, it walks the move with
proof: the new body must reproduce recorded real behaviour case for case, must be measurably faster
under a noise-aware benchmark, and ships behind a switch with an optional shadow run.

## Why

Native rewrites of "the slow part" often disappoint for boring reasons: the function was not where the
time went, its share of the run capped the possible gain, the cost of crossing the language border on
every call ate the win, or the new code disagreed with the old one on inputs nobody tested
(NaN, `-0`, emoji, `None`, key order). This skill makes each of those a written check with a verdict,
so the answer can also be **"don't"** - which is often the cheapest result of all.

## Features

- **Workload card** - freeze what "faster" means before measuring.
- **Hotspot card** - profiled share, call count, argument shape and a *measured* cost per crossing.
- **Ceiling with border cost** - Amdahl best case plus expected gain after crossings, with verdicts
  `LEAVE` / `FIX-IN-PLACE` / `BATCH-FIRST` / `TRANSPLANT`.
- **Behaviour recording** - JSONL cases captured from real runs plus an edge corpus, with tags for
  NaN, infinities and negative zero, and tolerance rules written *before* the new code exists.
- **Strict diff** - NaN = NaN, `-0` ≠ `0`, booleans ≠ numbers, both-errors = agree, missing = mismatch.
- **Noise-aware benchmark** - ratio of medians with a bootstrap 95 % interval and a noise guard:
  `FASTER` / `MARGINAL` / `SAME` / `SLOWER` / `NOISY`.
- **Ship gate** - `SHIP` / `SHIP-BEHIND-SWITCH` / `HOLD-MISMATCH` / `HOLD-NOISY` / `DROP`.
- **One-minute transplant report** for the team.
- Zero-dependency helper `scripts/transplant.py` with JSON output and CI-friendly exit codes; 12 tests.

## Installation

Copy the folder into your agent's skills directory:

| Agent | Location |
|---|---|
| Claude Code | `~/.claude/skills/hot-path-transplant/` (or `.claude/skills/` in a project) |
| Codex | `~/.codex/skills/hot-path-transplant/` |
| Cursor | `.cursor/skills/hot-path-transplant/` in the project, or reference `SKILL.md` from a rule |
| Gemini CLI / others | put the folder anywhere and point the agent at `SKILL.md` |

```bash
git clone https://github.com/fangdididi/hot-path-transplant ~/.claude/skills/hot-path-transplant
```

Chinese users can point the agent at `SKILL.zh-CN.md` instead.

## Usage

Ask your agent things like:

- "Profile `npm run import` and tell me whether moving `normalizeRow` to Rust is worth it."
- "We wrote a PyO3 version of `score()`; prove it behaves the same and decide whether to ship."
- "Is a WebAssembly version of our diff function faster once you count the boundary?"

Or run the helper yourself:

```bash
# 1. is it worth it?
python3 scripts/transplant.py ceiling --total-ms 412 --self-ms 290 --calls 1200 --cross-us 3 --kernel-speedup 6
# 2. does the new body behave the same?
python3 scripts/transplant.py diff examples/old.jsonl examples/new.jsonl > diff.json
# 3. is it actually faster?
python3 scripts/transplant.py bench examples/base_ms.txt examples/cand_ms.txt > bench.json
# 4. ship?
python3 scripts/transplant.py gate diff.json bench.json
```

## Example

The bundled example shows a realistic catch: the new body counts string length in bytes, so one
recorded case with `naïve42` returns `8` instead of `7`:

```json
{ "cases": 61, "agree": 60, "mismatch": 1,
  "first_mismatches": [{ "id": "17", "old": {"slug": "naïve42", "len": 7},
                                       "new": {"slug": "naïve42", "len": 8} }],
  "verdict": "MISMATCH" }
```

The benchmark alone would have said `FASTER` (2.75x, interval 2.71-2.80); the gate says `HOLD-MISMATCH`.

## File structure

```
hot-path-transplant/
├── SKILL.md              # skill instructions (English)
├── SKILL.zh-CN.md        # skill instructions (Chinese)
├── README.md
├── README.zh-CN.md
├── LICENSE
├── scripts/
│   └── transplant.py     # ceiling | diff | bench | gate
├── examples/
│   ├── old.jsonl         # recorded behaviour of the original
│   ├── new.jsonl         # replay through the new body (one planted bug)
│   ├── base_ms.txt       # timings, original
│   └── cand_ms.txt       # timings, candidate
└── tests/
    └── test_transplant.py
```

Run the tests with `python3 -m unittest discover -s tests`.

## License

MIT © fangdididi
