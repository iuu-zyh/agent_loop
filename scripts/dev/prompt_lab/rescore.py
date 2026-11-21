"""prompt_lab.rescore — 拿**同一套判据**重判旧结果（baseline 与 after 必须同尺）。

为什么需要它：
    判据本身也是会写错的（第一轮探针里就有四条是我的判据过严：模型写「三载五月十二日」
    「三千二百四十」被判成"没答对日期/没答出灵石数"）。改完判据如果只重跑新结果，
    就变成"用新尺量新衣" —— 与基线的对比失效。
    本脚本把落盘的 results.json 读回来，按**当前**的 checks 重算一遍，让两次运行同尺可比。

用法：
    python3 scripts/dev/prompt_lab/rescore.py <results.json> [--suite tool|compact|prune]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))


@dataclass
class ReRun:
    """够 checks 用的最小 ScenarioRun 替身（判据只读 final_text / meta / calls）。"""
    final_text: str = ""
    error: Any = None
    meta: Dict[str, Any] = field(default_factory=dict)
    calls: List[Dict[str, Any]] = field(default_factory=list)
    raw: str = ""
    steps: List[Any] = field(default_factory=list)
    sid: str = ""
    note: str = ""
    seconds: float = 0.0


def _rerun(payload: Dict[str, Any]) -> Dict[str, Any]:
    suite = payload["suite"]
    checks_of: Dict[str, Any] = {}
    if suite == "tool":
        from scenarios_tool import scenarios
        checks_of = {s["id"]: s.get("checks", []) for s in scenarios()}
    elif suite == "compact":
        from scenarios_compact import build_cases
        checks_of = {c["id"]: c.get("checks", []) for c in build_cases()}
    elif suite == "prune":
        from scenarios_compact import build_prune_cases
        checks_of = {c["id"]: c.get("checks", []) for c in build_prune_cases()}

    out = []
    for r in payload["results"]:
        defs = checks_of.get(r["id"], [])
        per_run_checks = []
        for run in r["runs"]:
            rr = ReRun(final_text=run.get("final_text") or "",
                       error=run.get("error"),
                       meta={k: v for k, v in (run.get("meta") or {}).items()},
                       calls=run.get("calls") or [],
                       raw=run.get("meta", {}).get("raw", ""))
            rr.meta.setdefault("transcript", rr.meta.get("transcript", ""))
            got = []
            for label, fn in defs:
                try:
                    got.append(bool(fn(rr)))
                except Exception as e:  # noqa: BLE001
                    got.append(False)
            per_run_checks.append(got)
        checks = []
        for i, (label, _fn) in enumerate(defs):
            vals = [g[i] for g in per_run_checks] if per_run_checks else [False]
            checks.append({"label": label, "passed": all(vals), "per_run": vals})
        out.append({"id": r["id"], "note": r.get("note", ""), "checks": checks,
                    "runs": r["runs"]})
    return {"suite": suite, "model": payload.get("model"), "seconds": payload.get("seconds"),
            "results": out}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--suite", default="")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()
    payload = json.loads(Path(args.path).read_text(encoding="utf-8"))
    if args.suite:
        payload["suite"] = args.suite
    rescored = _rerun(payload)
    n_fail = 0
    for r in rescored["results"]:
        bad = [c["label"] for c in r["checks"] if not c["passed"]]
        if bad:
            n_fail += 1
        print(f"{r['id']:34s} {'OK' if not bad else '✗ ' + str(sum(1 for c in r['checks'] if c['passed'])) + '/' + str(len(r['checks']))}"
              + ("" if not bad else "  ← " + "；".join(bad)))
    total = len(rescored["results"])
    print(f"\n[{rescored['suite']}] 场景 {total}：失败 {n_fail}")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(rescored, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
