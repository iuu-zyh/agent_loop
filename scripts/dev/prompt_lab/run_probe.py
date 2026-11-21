"""prompt_lab.run_probe — 跑探针并出报告。

用法（从源码树任意位置）：
    python3 scripts/dev/prompt_lab/run_probe.py --suite tool
    python3 scripts/dev/prompt_lab/run_probe.py --suite compact --only G1
    python3 scripts/dev/prompt_lab/run_probe.py --suite all --concurrency 3 --repeat 2

产物落在 `--out`（默认 F:\\.agent_loop_build\\prompt_lab\\<时间戳>）：
    report.md      —— 人读：判据红绿 + 每次调用 + 模型原话
    results.json   —— 机读：全量记录（含系统提示原文与完整消息流）

只读探针：不改任何产品文件、不写 prompts/、不碰存档。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lab_core import Lab, ScenarioRun, Step, TREE_ROOT, validate_args  # noqa: E402

def _default_out() -> Path:
    """输出根：优先 F 盘构建目录（Windows 侧看得到），其次源码树内（非 Windows 时）。"""
    win = Path("F:/.agent_loop_build/prompt_lab")
    if Path("/mnt/f/.agent_loop_build").is_dir():
        return Path("/mnt/f/.agent_loop_build/prompt_lab")
    if win.parent.parent.exists():
        return win
    return TREE_ROOT / ".prompt_lab_out"


DEFAULT_OUT = _default_out()


def _fmt_args(args: Any) -> str:
    return json.dumps(args, ensure_ascii=False, sort_keys=False) if args is not None else "{}"


def _truncate(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + f"…（共 {len(text)} 字）"


async def run_tool_suite(args, out_dir: Path) -> Dict[str, Any]:
    from scenarios_tool import scenarios

    scens = scenarios()
    if args.only:
        keys = [k.strip() for k in args.only.split(",") if k.strip()]
        scens = [s for s in scens if any(k in s["id"] for k in keys)]
    lab = Lab(npc_id=args.npc, config_path=args.config or None)
    # 串行装配（persona 是进程单例里的屉，必须一个场景一个场景地换）
    prepared: List[Dict[str, Any]] = []
    for s in scens:
        lab.reset_persona()
        if s.get("persona"):
            lab.set_persona(s["persona"])
        lab.sp.set_player_persona(s.get("player_persona"))
        prepared.append({**s, "_system": lab.system_prompt()})
    lab.reset_persona()
    lab.sp.set_player_persona(None)

    client = lab.client()
    sem = asyncio.Semaphore(max(1, args.concurrency))

    async def _one(sc: Dict[str, Any]) -> List[ScenarioRun]:
        runs = []
        for i in range(max(1, args.repeat)):
            async with sem:
                r = await lab.run_scenario(client, sc, system=sc["_system"])
            runs.append(r)
        return runs

    t0 = time.monotonic()
    groups = await asyncio.gather(*[_one(sc) for sc in prepared])
    seconds = time.monotonic() - t0

    results: List[Dict[str, Any]] = []
    for sc, runs in zip(prepared, groups):
        checks = []
        for label, fn in sc.get("checks", []):
            per_run = []
            for r in runs:
                try:
                    per_run.append(bool(fn(r)))
                except Exception as e:  # noqa: BLE001
                    per_run.append(False)
                    r.meta.setdefault("check_errors", []).append(f"{label}: {type(e).__name__}: {e}")
            checks.append({"label": label, "passed": all(per_run), "per_run": per_run})
        results.append({
            "id": sc["id"], "note": sc.get("note", ""),
            "checks": checks,
            "runs": [{
                "final_text": r.final_text, "seconds": r.seconds, "error": r.error,
                "calls": r.calls, "steps": [{
                    "index": s.index, "text": s.text, "reasoning": _truncate(s.reasoning, 800),
                    "calls": [{"name": c.get("name"), "arguments": c.get("arguments")} for c in s.calls],
                    "usage": s.usage, "seconds": s.seconds,
                } for s in r.steps],
                "meta": {k: v for k, v in r.meta.items() if k != "messages"},
            } for r in runs],
        })
    return {"suite": "tool", "model": lab.model_info(), "seconds": seconds,
            "results": results,
            "system_chars": len(prepared[0]["_system"]) if prepared else 0,
            "tools_chars": len(json.dumps(lab.tools, ensure_ascii=False))}


async def run_compact_suite(args, out_dir: Path) -> Dict[str, Any]:
    from scenarios_compact import build_cases

    cases = build_cases()
    if args.only:
        keys = [k.strip() for k in args.only.split(",") if k.strip()]
        cases = [c for c in cases if any(k in c["id"] for k in keys)]
    lab = Lab(npc_id=args.npc, config_path=args.config or None)
    client = lab.client()
    system_prompt_text = lab.system_prompt()

    # 用**真的** Compressor 渲染指令与 transcript：探针不许自己对模板另做一套解释
    from agent_loop.compaction.compress import Compressor
    comp = Compressor(llm=None, prompts_root=TREE_ROOT / "agent_loop" / "prompts" / "compaction")

    sem = asyncio.Semaphore(max(1, args.concurrency))

    async def _one(case: Dict[str, Any]) -> Dict[str, Any]:
        class _FakeSession:
            def __init__(self, msgs): self._m = msgs
            def derive_messages(self): return self._m

        vars_ = {"npc_name": case.get("npc", args.npc), "player_name": case.get("player", "缪嘉歆")}
        instruction = comp._load_instruction(case.get("npc", args.npc))
        system = comp._render_template(instruction, vars_)
        if case.get("with_persona", True):
            system = f"{system}\n\n---\n{system_prompt_text}"
        transcript = comp._render_transcript(_FakeSession(case["messages"]), vars_)
        run = ScenarioRun(sid=case["id"], note=case.get("note", ""))
        run.meta["player_name"] = case.get("player") or "缪嘉歆"
        run.meta["system"] = system
        run.meta["transcript"] = transcript
        run.meta["instruction"] = instruction
        async with sem:
            t = time.monotonic()
            try:
                res = await client.generate(
                    system,
                    [{"role": "user", "content": [{"type": "text",
                      "text": f"<transcript>\n{transcript}\n</transcript>"}]}],
                    [],
                )
                run.final_text = (res.text or "").strip()
                run.steps = [Step(index=0, text=run.final_text,
                                  reasoning=_truncate(getattr(res, "reasoning", "") or "", 800),
                                  usage=getattr(res, "usage", None))]
            except Exception as e:  # noqa: BLE001
                run.error = f"{type(e).__name__}: {e}"
            run.seconds = time.monotonic() - t
        checks = []
        for label, fn in case.get("checks", []):
            try:
                ok = bool(fn(run))
            except Exception as e:  # noqa: BLE001
                ok = False
                run.meta.setdefault("check_errors", []).append(f"{label}: {type(e).__name__}: {e}")
            checks.append({"label": label, "passed": ok, "per_run": [ok]})
        return {"id": case["id"], "note": case.get("note", ""), "checks": checks,
                "runs": [{"final_text": run.final_text, "seconds": run.seconds, "error": run.error,
                          "meta": run.meta, "steps": [{"index": 0, "text": run.final_text,
                                                       "reasoning": _truncate(run.steps[0].reasoning, 2000) if run.steps else "",
                                                       "usage": run.steps[0].usage if run.steps else None}]}]}

    t0 = time.monotonic()
    results = await asyncio.gather(*[_one(c) for c in cases])
    return {"suite": "compact", "model": lab.model_info(), "seconds": time.monotonic() - t0,
            "results": list(results)}


async def run_prune_suite(args, out_dir: Path) -> Dict[str, Any]:
    """工具结果摘要（compaction/pruner.TOOL_PRUNE_INSTRUCTION）：指令内置在代码里，不经 prompts/。"""
    from scenarios_compact import build_prune_cases
    from agent_loop.compaction.pruner import TOOL_PRUNE_INSTRUCTION

    cases = build_prune_cases()
    if args.only:
        keys = [k.strip() for k in args.only.split(",") if k.strip()]
        cases = [c for c in cases if any(k in c["id"] for k in keys)]
    lab = Lab(npc_id=args.npc, config_path=args.config or None)
    client = lab.client()
    sem = asyncio.Semaphore(max(1, args.concurrency))

    async def _one(case: Dict[str, Any]) -> Dict[str, Any]:
        run = ScenarioRun(sid=case["id"], note=case.get("note", ""))
        run.raw = case["raw"]
        run.meta["instruction"] = TOOL_PRUNE_INSTRUCTION
        run.meta["raw"] = case["raw"]
        async with sem:
            t = time.monotonic()
            try:
                res = await client.generate(
                    TOOL_PRUNE_INSTRUCTION,
                    [{"role": "user", "content": [{"type": "text",
                      "text": f"<tool-result-raw>\n{case['raw']}\n</tool-result-raw>"}]}],
                    [],
                )
                run.final_text = (res.text or "").strip()
            except Exception as e:  # noqa: BLE001
                run.error = f"{type(e).__name__}: {e}"
            run.seconds = time.monotonic() - t
        checks = []
        for label, fn in case.get("checks", []):
            try:
                ok = bool(fn(run))
            except Exception as e:  # noqa: BLE001
                ok = False
                run.meta.setdefault("check_errors", []).append(f"{label}: {type(e).__name__}: {e}")
            checks.append({"label": label, "passed": ok, "per_run": [ok]})
        return {"id": case["id"], "note": case.get("note", ""), "checks": checks,
                "runs": [{"final_text": run.final_text, "seconds": run.seconds, "error": run.error,
                          "meta": run.meta, "raw_chars": len(case["raw"]),
                          "steps": [{"index": 0, "text": run.final_text, "usage": None}]}]}

    t0 = time.monotonic()
    results = await asyncio.gather(*[_one(c) for c in cases])
    return {"suite": "prune", "model": lab.model_info(), "seconds": time.monotonic() - t0,
            "results": list(results)}


# ---------------------------------------------------------------- 报告

def write_report(payload: Dict[str, Any], out_dir: Path) -> Path:
    lines: List[str] = []
    name = {"tool": "工具调用", "compact": "历史压缩", "prune": "工具结果压缩"}.get(payload["suite"], payload["suite"])
    lines.append(f"# 提示词探针报告 · {name}")
    lines.append("")
    lines.append(f"- 模型：`{payload['model'].get('model')}` @ `{payload['model'].get('base_url')}`"
                 f"（protocol={payload['model'].get('protocol')}）")
    lines.append(f"- 用时：{payload['seconds']:.0f}s")
    if payload["suite"] == "tool":
        lines.append(f"- 系统提示 {payload['system_chars']} 字 / 工具表 {payload['tools_chars']} 字")
    total = len(payload["results"])
    n_fail = sum(1 for r in payload["results"] if not all(c["passed"] for c in r["checks"]))
    lines.append(f"- 场景 {total} 个：**通过 {total - n_fail} / 失败 {n_fail}**")
    lines.append("")

    lines.append("## 一览")
    lines.append("")
    lines.append("| 场景 | 判据 | 调用 | 结果 |")
    lines.append("|---|---|---|---|")
    for r in payload["results"]:
        n_ok = sum(1 for c in r["checks"] if c["passed"])
        calls = ", ".join(f"`{c['name']}`" for c in (r["runs"][0].get("calls") or [])) or "—"
        mark = "✓" if n_ok == len(r["checks"]) else f"✗ {n_ok}/{len(r['checks'])}"
        lines.append(f"| `{r['id']}` | {n_ok}/{len(r['checks'])} | {calls} | {mark} |")
    lines.append("")

    for r in payload["results"]:
        bad = [c for c in r["checks"] if not c["passed"]]
        head = "✓" if not bad else "✗"
        lines.append(f"## {head} `{r['id']}`")
        lines.append("")
        if r.get("note"):
            lines.append(f"{r['note']}")
            lines.append("")
        for c in r["checks"]:
            lines.append(f"- {'✓' if c['passed'] else '✗'} {c['label']}")
        lines.append("")
        for i, run in enumerate(r["runs"]):
            if len(r["runs"]) > 1:
                lines.append(f"### 第 {i + 1} 次")
            if run.get("error"):
                lines.append(f"- ⚠️ 运行异常：{run['error']}")
            if payload["suite"] == "tool":
                for c in run.get("calls") or []:
                    prob = c.get("schema_problems") or []
                    suffix = f"　⚠️ schema：{'; '.join(prob)}" if prob else ""
                    lines.append(f"- 调用 `{c['name']}` {_fmt_args(c['arguments'])}{suffix}")
                if not run.get("calls"):
                    lines.append("- 未调用任何工具")
            if payload["suite"] == "prune" and run.get("raw_chars"):
                got = len(run.get("final_text") or "")
                lines.append(f"- 原文 {run['raw_chars']} 字 → 摘要 {got} 字（{got * 100 // max(1, run['raw_chars'])}%）")
                lines.append("")
            text = _truncate(run.get("final_text") or "", 900)
            lines.append("")
            lines.append(f"> {text.replace(chr(10), chr(10) + '> ') if text else '（空）'}")
            lines.append("")
        lines.append("")

    out = out_dir / "report.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default="tool", choices=["tool", "compact", "prune", "all"])
    ap.add_argument("--only", default="")
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--npc", default="云含")
    ap.add_argument("--config", default="", help="改用这份 config.json（例：游戏 ModAssets 那份）")
    ap.add_argument("--out", default="")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    stamp = time.strftime("%m%d_%H%M%S")
    out_dir = Path(args.out) if args.out else (DEFAULT_OUT / f"{stamp}{('_' + args.tag) if args.tag else ''}")
    out_dir.mkdir(parents=True, exist_ok=True)

    payloads = []
    if args.suite in ("tool", "all"):
        payloads.append(await run_tool_suite(args, out_dir))
    if args.suite in ("compact", "all"):
        payloads.append(await run_compact_suite(args, out_dir))
    if args.suite in ("prune", "all"):
        payloads.append(await run_prune_suite(args, out_dir))

    reports = []
    for p in payloads:
        sub = out_dir if len(payloads) == 1 else out_dir / p["suite"]
        sub.mkdir(parents=True, exist_ok=True)
        rep = write_report(p, sub)
        (sub / "results.json").write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
        reports.append(rep)
        n_fail = sum(1 for r in p["results"] if not all(c["passed"] for c in r["checks"]))
        print(f"[{p['suite']}] 场景 {len(p['results'])}：失败 {n_fail} → {rep}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
