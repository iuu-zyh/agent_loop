"""prompt_lab.aggregate — 把多轮探针结果**汇总成比率**（单轮有方差，别拿一轮下结论）。

背景：同一套提示词连跑 4 轮，同一场景的判据会红绿抖动（模型是随机采样）。
按用户 09-19 的口径：**单轮失误可接受，只要不是原则性的、且多轮里能收敛**。
所以结论要用"多少次运行里犯了几次"来说，而不是"这一轮通过没通过"。

用法：
    python3 scripts/dev/prompt_lab/aggregate.py base=<results.json>[,<results.json>...] \
                                               after=<results.json>[,...]
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from score_convergence import HARD_LABELS, QUIET_SCENARIOS, QUIET_BUDGET, CALL_BUDGET  # noqa: E402

# 判据 label → 人话类别（同一类只算一次，避免一个场景多条 check 重复计数）
KIND = {
    "不得用成亲 op": "错 op（道侣→成亲）",
    "不得自拼唯一名": "自拼唯一名",
    "原则：不得用反视角": "反视角（拜师/收徒填反）",
    "原则：异地动手前必须先移动": "异地裸调动手",
    "不装熟": "对陌生人认亲",
    "照实说不知道": "不知道却编",
    "没有把占位符当人名": "占位符当人名",
    "没有把数字当人名": "数字当人名",
    "参数符合工具 schema": "参数违反 schema",
    "不滥用 inventory_all": "滥用全量裁剪",
    "没有偷窃调用": "越权偷窃",
    "没有对第三方动手": "对第三方动手",
    "不出戏": "出戏/暴露来历",
}


def _kind(label: str) -> str:
    for k, v in KIND.items():
        if label.startswith(k):
            return v
    return label


def collect(files):
    runs = 0
    hard = Counter()          # 类别 → 次数（按"运行"计）
    hard_scen = defaultdict(set)
    unconverged = Counter()
    over = 0
    calls = 0
    placeholder = 0
    schema_bad = 0
    quiet_over = 0
    per_scen_calls = defaultdict(list)
    for f in files:
        payload = json.loads(Path(f).read_text(encoding="utf-8"))
        for r in payload["results"]:
            for run in r["runs"]:
                runs += 1
                n = len(run.get("calls") or [])
                calls += n
                per_scen_calls[r["id"]].append(n)
                quiet = r["id"][:2] in QUIET_SCENARIOS
                if n > (QUIET_BUDGET if quiet else CALL_BUDGET):
                    over += 1
                    if quiet:
                        quiet_over += 1
                for c in run.get("calls") or []:
                    args = c.get("arguments") or {}
                    if any(isinstance(args.get(k), str) and "玩家" in args[k] for k in ("target", "seller", "buyer")):
                        placeholder += 1
                    if c.get("schema_problems"):
                        schema_bad += 1
                # 判据：逐条 check 的 per_run 已丢失 → 用整场景 passed 计（保守：一轮红算整场景红）
            for c in r["checks"]:
                if c["passed"]:
                    continue
                k = _kind(c["label"])
                if any(c["label"].startswith(h) for h in HARD_LABELS):
                    hard[k] += 1
                    hard_scen[k].add(r["id"])
                else:
                    unconverged[k] += 1
    return dict(runs=runs, hard=hard, hard_scen=hard_scen, unconverged=unconverged,
                over=over, quiet_over=quiet_over, calls=calls, placeholder=placeholder,
                schema_bad=schema_bad, per_scen_calls=per_scen_calls)


def show(tag: str, st: dict):
    print(f"### {tag}")
    print(f"- 运行 {st['runs']} 次 / 总工具调用 {st['calls']}（均 {st['calls'] / max(1, st['runs']):.2f} 次每轮）")
    print(f"- **原则性违规 {sum(st['hard'].values())} 条**"
          + ("：" + "、".join(f"{k} ×{v}（{len(st['hard_scen'][k])} 场景）" for k, v in st["hard"].most_common())
             if st["hard"] else "（无）"))
    print(f"- 未收敛判据 {sum(st['unconverged'].values())} 条"
          + ("：" + "、".join(f"{k} ×{v}" for k, v in st["unconverged"].most_common()) if st["unconverged"] else ""))
    print(f"- 调用超预算 {st['over']} 次运行（其中纯上下文场景 {st['quiet_over']} 次）")
    print(f"- 全局不变量：占位符当人名 {st['placeholder']} 次；参数违反 schema {st['schema_bad']} 次")
    top = sorted(st["per_scen_calls"].items(), key=lambda kv: -sum(kv[1]))[:6]
    print("- 调用最多的场景：" + "、".join(f"`{k}` {v}" for k, v in top))
    print()


if __name__ == "__main__":
    groups = {}
    for a in sys.argv[1:]:
        tag, _, files = a.partition("=")
        groups[tag] = [x for x in files.split(",") if x]
    for tag, files in groups.items():
        show(tag, collect(files))
