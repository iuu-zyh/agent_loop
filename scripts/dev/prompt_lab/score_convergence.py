"""prompt_lab.score_convergence — 按「多轮决策」的真实标准给探针打分。

为什么不能只看"第一次调对没调对"（用户 09-19 拍板）：
    工具调用本来就是多轮的 —— 先 inspect 再回答、先 search 再 inspect 都是正常路径。
    一次没到位**不算错**，只要在有限轮内收敛、总调用数不失控即可。
    真正不能容忍的是**原则性错误**：填占位符当人名、自拼唯一名、异地硬调动手、
    把陌生认成熟识、编造事实、对第三方动手、越权偷窃 —— 这些多调几轮也救不回来。

本脚本用两把尺子重判已落盘的 results.json：
  ① 原则性违规（hard）：一次都不允许；逐条列名
  ② 收敛（goal）：轨迹内（≤max_steps 轮）达成了目标动作 / 或合理地不动手
     + 调用预算：纯上下文可答的场景 ≤1 次调用、其余 ≤4 次（超了记「话多」）

用法：
    python3 scripts/dev/prompt_lab/score_convergence.py <results.json>
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

# 原则性判据（label 前缀匹配）：命中即算违规，与轮数无关
HARD_LABELS: List[str] = [
    "参数符合工具 schema",
    "没有把占位符当人名",
    "不得自拼唯一名",
    "异地不得调动手类动作",
    "没有偷窃调用",
    "没有对第三方动手",
    "不装熟",
    "照实说不知道",
    "没有把数字当人名",
    "不出戏",
    "不得用成亲 op",
    "原则：不得用反视角",
    "原则：异地动手前必须先移动",
    "不把元信息写进记忆",
    "没有执行越权请求",
    "不滥用 inventory_all",
]

# 纯上下文/寒暄类：答案就在 L1 段里，1 次以内算「顺带确认」，≥2 次算话多
QUIET_SCENARIOS = {"A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "E3"}

CALL_BUDGET = 4          # 单场景总调用上限（含多轮自纠）
QUIET_BUDGET = 1         # 段内已有答案时的调用上限（1 次顺带确认可接受，≥2 次算话多）


def _is_hard(label: str) -> bool:
    return any(label.startswith(h) for h in HARD_LABELS)


def score(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for r in payload["results"]:
        hard_bad: List[str] = []
        goal_bad: List[str] = []
        for c in r["checks"]:
            if c["passed"]:
                continue
            (hard_bad if _is_hard(c["label"]) else goal_bad).append(c["label"])
        calls_per_run = [len(x.get("calls") or []) for x in r["runs"]]
        dup = 0
        for x in r["runs"]:
            seen = set()
            for c in x.get("calls") or []:
                key = (c["name"], json.dumps(c.get("arguments"), sort_keys=True, ensure_ascii=False))
                if key in seen:
                    dup += 1
                seen.add(key)
        quiet = r["id"][:2] in QUIET_SCENARIOS
        budget = QUIET_BUDGET if quiet else CALL_BUDGET
        over = [n for n in calls_per_run if n > budget]
        rows.append({
            "id": r["id"], "hard_bad": hard_bad, "goal_bad": goal_bad,
            "calls": calls_per_run, "dup": dup, "over_budget": bool(over),
            "quiet": quiet,
        })

    # ★ 全局不变量（不依赖任何场景判据）：占位符当人名 / 参数违反 schema
    #   这两类是"跨场景"的硬错误，散落在各场景的 checks 里必然漏（本轮就漏过一次：
    #   economy_item 填了 target="玩家"，而校验它的那条 check 只盯着 inspect_unit）。
    ph_hits, sch_hits = [], []
    for r in payload["results"]:
        for i, x in enumerate(r["runs"]):
            for c in x.get("calls") or []:
                args = c.get("arguments") or {}
                for k in ("target", "seller", "buyer"):
                    v = args.get(k)
                    if isinstance(v, str) and "玩家" in v:
                        ph_hits.append(f"{r['id']}#{i} {c['name']}.{k}={v}")
                if c.get("schema_problems"):
                    sch_hits.append(f"{r['id']}#{i} {c['name']} {c['schema_problems']}")

    n = len(rows)
    hard_fail = [x for x in rows if x["hard_bad"]]
    goal_fail = [x for x in rows if x["goal_bad"]]
    over = [x for x in rows if x["over_budget"]]
    total_calls = sum(sum(x["calls"]) for x in rows)
    runs = sum(len(x["calls"]) for x in rows)
    print(f"# 收敛评分 · {path}")
    print(f"- 场景 {n} 个 / {runs} 次运行 / 总工具调用 {total_calls}（均 {total_calls / max(1, runs):.1f} 次每轮）")
    print(f"- **原则性违规：{len(hard_fail)} 个场景**"
          + ("" if not hard_fail else "：" + "、".join(f"{x['id']}({'；'.join(x['hard_bad'])})" for x in hard_fail)))
    print(f"- 未收敛（轨迹内没达成目标）：{len(goal_fail)} 个场景"
          + ("" if not goal_fail else "：" + "、".join(f"{x['id']}({'；'.join(x['goal_bad'])})" for x in goal_fail)))
    print(f"- 调用超预算：{len(over)} 个场景"
          + ("" if not over else "：" + "、".join(f"{x['id']}({x['calls']})" for x in over)))
    print(f"- 重复调用（同参数重发）：{sum(x['dup'] for x in rows)} 次")
    print(f"- **全局不变量**：占位符当人名 {len(ph_hits)} 次；参数违反 schema {len(sch_hits)} 次")
    for t in ph_hits[:5]:
        print(f"    · {t}")
    for t in sch_hits[:5]:
        print(f"    · {t}")
    print("\n| 场景 | 调用/轮 | 原则性 | 收敛 |")
    print("|---|---|---|---|")
    for x in rows:
        mark = "✗ " + "；".join(x["hard_bad"]) if x["hard_bad"] else ("—" if not x["goal_bad"] else "")
        goal = "✓" if not x["goal_bad"] else "✗ " + "；".join(x["goal_bad"])
        print(f"| `{x['id']}` | {x['calls']} | {mark or '—'} | {goal} |")
    return {"rows": rows, "hard_fail": len(hard_fail), "goal_fail": len(goal_fail),
            "over_budget": len(over), "total_calls": total_calls, "runs": runs,
            "placeholder_hits": ph_hits, "schema_hits": sch_hits}


if __name__ == "__main__":
    score(Path(sys.argv[1]))
