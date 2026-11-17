[English](README.md) | **简体中文**

# hot-path-transplant（热路径移植）

一个专门应对 **“这部分我们用 Rust 重写吧”** 这句话的智能体技能。

它先判断 JavaScript/TypeScript、Python、Ruby 或 Go 程序里的某一个慢函数，是否真的值得搬进原生代码（Rust、C/C++、Zig、WebAssembly）；如果值得，再带着证据完成搬迁：新实现必须逐条复现录制下来的真实行为，必须在考虑噪声的基准测试中确实更快，并且挂在开关后面上线，可选先做影子运行。

## 为什么需要它

把“慢的那部分”改写成原生代码，结果常常令人失望，原因却很平常：时间根本不在那个函数里；它在总耗时中的占比本身就封死了收益上限；每次调用跨越语言边界的成本把收益吃掉了；或者新代码在没人测过的输入上（NaN、`-0`、emoji、`None`、键顺序）和旧代码结果不一样。这个技能把每一种情况都变成一道写下来的检查并给出判定，所以答案也可以是 **“别做”**，而这往往是成本最低的结果。

## 功能特点

- **工作负载卡**：测量之前先冻结“更快”的含义。
- **热点卡**：剖析得到的占比、调用次数、参数形态，以及**实测**的单次跨边界成本。
- **含边界成本的上限计算**：Amdahl 最好情况 + 扣除跨边界后的预期收益，判定为 `LEAVE` / `FIX-IN-PLACE` / `BATCH-FIRST` / `TRANSPLANT`。
- **行为录制**：从真实运行中抓取 JSONL 用例并补充边界用例集，用标签表示 NaN、无穷大和负零，容差规则在新代码出现**之前**写好。
- **严格对比**：NaN = NaN，`-0` ≠ `0`，布尔值 ≠ 数字，两边都报错算一致，缺失算不一致。
- **考虑噪声的基准测试**：中位数比值 + bootstrap 95% 区间 + 噪声保护，判定为 `FASTER` / `MARGINAL` / `SAME` / `SLOWER` / `NOISY`。
- **上线闸门**：`SHIP` / `SHIP-BEHIND-SWITCH` / `HOLD-MISMATCH` / `HOLD-NOISY` / `DROP`。
- 给团队看的**一分钟移植报告**。
- 零依赖辅助脚本 `scripts/transplant.py`，输出 JSON，退出码适合 CI；附 12 个测试。

## 安装

把整个文件夹复制到智能体的技能目录：

| 智能体 | 位置 |
|---|---|
| Claude Code | `~/.claude/skills/hot-path-transplant/`（或项目内的 `.claude/skills/`） |
| Codex | `~/.codex/skills/hot-path-transplant/` |
| Cursor | 项目内的 `.cursor/skills/hot-path-transplant/`，或在规则中引用 `SKILL.md` |
| Gemini CLI / 其他 | 放在任意位置，让智能体读取 `SKILL.md` |

```bash
git clone https://github.com/fangdididi/hot-path-transplant ~/.claude/skills/hot-path-transplant
```

中文用户可以让智能体读取 `SKILL.zh-CN.md`。

## 使用方法

可以这样对智能体说：

- “剖析一下 `npm run import`，告诉我把 `normalizeRow` 搬到 Rust 值不值得。”
- “我们写了一个 PyO3 版本的 `score()`，证明它行为一致，再决定要不要上线。”
- “把边界成本算进去之后，我们 diff 函数的 WebAssembly 版本真的更快吗？”

也可以自己运行辅助脚本：

```bash
# 1. 值得吗？
python3 scripts/transplant.py ceiling --total-ms 412 --self-ms 290 --calls 1200 --cross-us 3 --kernel-speedup 6
# 2. 新实现行为一致吗？
python3 scripts/transplant.py diff examples/old.jsonl examples/new.jsonl > diff.json
# 3. 真的更快吗？
python3 scripts/transplant.py bench examples/base_ms.txt examples/cand_ms.txt > bench.json
# 4. 能上线吗？
python3 scripts/transplant.py gate diff.json bench.json
```

## 示例

自带的示例演示了一个很真实的问题：新实现按字节计算字符串长度，所以录制用例里的 `naïve42` 返回了 `8` 而不是 `7`：

```json
{ "cases": 61, "agree": 60, "mismatch": 1,
  "first_mismatches": [{ "id": "17", "old": {"slug": "naïve42", "len": 7},
                                       "new": {"slug": "naïve42", "len": 8} }],
  "verdict": "MISMATCH" }
```

只看基准测试会得出 `FASTER`（2.75 倍，区间 2.71～2.80）；闸门给出的结论是 `HOLD-MISMATCH`。

## 文件结构

```
hot-path-transplant/
├── SKILL.md              # 技能说明（英文）
├── SKILL.zh-CN.md        # 技能说明（中文）
├── README.md
├── README.zh-CN.md
├── LICENSE
├── scripts/
│   └── transplant.py     # ceiling | diff | bench | gate
├── examples/
│   ├── old.jsonl         # 原实现的行为录制
│   ├── new.jsonl         # 新实现的回放结果（埋了一个 bug）
│   ├── base_ms.txt       # 原实现计时
│   └── cand_ms.txt       # 新实现计时
└── tests/
    └── test_transplant.py
```

运行测试：`python3 -m unittest discover -s tests`。

## 许可证

MIT © fangdididi
