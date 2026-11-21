# -*- coding: utf-8 -*-
"""prompt_lab.replay_sessions — 拿真实对话记录回放"这一步该不该调工具"。

和 scenarios_tool 的分工：
    scenarios_tool —— 手工夹具，判据写死，覆盖面广、可回归；
    本模块 —— 玩家真说过的那句话 + 当时真的系统提示 + 当时真的上下文长度与历史，
             原样再打一次，看模型这次开不开口要工具。

只读：读 `~/.sessions/worlds/<存档>/<NPC>.jsonl`，只发请求，不改写任何会话文件。

用法：
    # 列出某个 NPC 的每个玩家回合、当时真调了哪些工具
    python scripts/dev/prompt_lab/replay_sessions.py --npc 巩易 --list

    # 回放指定回合（turn 号见 --list），同一回合打 3 次看稳定性
    python scripts/dev/prompt_lab/replay_sessions.py --npc 巩易 --turns 12 --repeat 3 \
        --config F:/.agent_loop_build/prompt_lab/game_config.json

    # 多步：模型调了工具就把记录里备好的真实结果喂回去，看它收尾收得对不对
    ... --multi

    # 只回放在"玩家开口要东西/问局势"这类该调工具的回合
    ... --grep 灵石 --grep 宗门
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lab_core import Lab, validate_args  # noqa: E402

SURFACE = {"user/message", "assistant/message", "tool/result"}


# ---------------------------------------------------------------- 会话读取

def sessions_root() -> Path:
    return Path(os.path.expanduser("~/.sessions/worlds"))


def find_session(npc: str, world: str = "") -> Path:
    root = sessions_root()
    # 终端传中文容易花掉，所以也接受已经百分号编码过的名字
    names = [urllib.parse.quote(npc, safe="") + ".jsonl"]
    if "%" in npc:
        names.insert(0, npc + ".jsonl" if not npc.endswith(".jsonl") else npc)
    cands = []
    for w in sorted(os.listdir(root)):
        if world and w != world:
            continue
        for fn in names:
            p = root / w / fn
            if p.is_file():
                cands.append(p)
    if not cands:
        raise SystemExit(f"找不到 {npc} 的会话记录（查 {root}）")
    if len(cands) > 1:
        cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        print(f"[warn] {npc} 在多个存档里都有记录，用最近修改的 {cands[0].parent.name}", file=sys.stderr)
    return cands[0]


def load_events(path: Path) -> List[Dict[str, Any]]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type"):
            out.append(ev)
    return out


def derive(ev: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """surface 事件 → canonical 消息（与 session 派生逻辑同形）。"""
    t = ev.get("type")
    d = ev.get("data") or {}
    if t == "user/message":
        return d
    if t == "assistant/message":
        msg = d.get("message") or {}
        # 只留文本或工具调用块；空壳（无 content）不进上下文
        return msg if msg.get("content") else None
    if t == "tool/result":
        return d.get("message")
    return None


@dataclass
class StepRec:
    turn: int
    step: int
    boundary: int                      # 该步模型回复落账的 seq，之前的消息都是它的输入
    system: str = ""
    system_from_log: bool = False
    tools: List[Dict[str, Any]] = field(default_factory=list)
    messages: List[Dict[str, Any]] = field(default_factory=list)
    player_text: str = ""              # 该回合玩家原话（去掉日期戳）
    real_calls: List[Dict[str, Any]] = field(default_factory=list)
    real_results: Dict[str, str] = field(default_factory=dict)
    real_reasoning_len: int = 0
    real_final: str = ""


def index_steps(evs: List[Dict[str, Any]]) -> List[StepRec]:
    by_seq = {e["seq"]: e for e in evs if e.get("seq") is not None}
    max_seq = max(by_seq) if by_seq else 0

    # 每个 seq 时刻生效的系统提示（request/header 只在提示词变化时落账）
    sys_at: Dict[int, Tuple[str, List[Dict[str, Any]]]] = {}
    last: Optional[Tuple[str, List[Dict[str, Any]]]] = None
    for s in range(max_seq + 1):
        ev = by_seq.get(s)
        if ev and ev["type"] == "request/header":
            h = (ev["data"] or {}).get("header") or {}
            last = (h.get("system") or "", h.get("tools") or [])
        if last:
            sys_at[s] = last

    tool_calls: Dict[Tuple[int, int], List[Dict[str, Any]]] = {}
    results: Dict[str, str] = {}
    for ev in evs:
        d = ev.get("data") or {}
        if ev["type"] == "tool/call":
            key = (d.get("turn"), d.get("step"))
            call = {"name": d.get("name"), "arguments": d.get("arguments"), "id": d.get("callId")}
            tool_calls.setdefault(key, []).append(call)
            res = by_seq.get(ev["seq"] + 1)
            if res and res.get("type") == "tool/result":
                msg = (res.get("data") or {}).get("message") or {}
                for blk in msg.get("content") or []:
                    if isinstance(blk, dict) and blk.get("type") == "tool-result":
                        results[call["id"]] = "".join(
                            b.get("text", "") for b in blk.get("content") or [] if isinstance(b, dict))

    surface = [e for e in evs if e.get("type") in SURFACE and e.get("surfaceOp") == "append"]
    out: List[StepRec] = []
    for ev in evs:
        if ev["type"] != "assistant/message":
            continue
        d = ev.get("data") or {}
        seq = ev["seq"]
        rec = StepRec(turn=d.get("turn") or 0, step=d.get("step") or 0, boundary=seq)
        rec.messages = [m for m in (derive(x) for x in surface if x["seq"] < seq) if m]
        got = sys_at.get(seq)
        if got:
            rec.system, rec.tools = got[0], got[1]
            rec.system_from_log = True
        key = (rec.turn, rec.step)
        rec.real_calls = tool_calls.get(key) or []
        rec.real_results = {c["id"]: results.get(c["id"], "") for c in rec.real_calls if c.get("id")}
        rec.real_reasoning_len = len((d.get("reasoning") or "").strip())
        content = (d.get("message") or {}).get("content") or []
        rec.real_final = "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
        txt = []
        for m in reversed(rec.messages):
            if m.get("role") != "user":
                continue
            for blk in m.get("content") or []:
                if isinstance(blk, dict) and blk.get("type") == "text":
                    t = blk.get("text") or ""
                    if t.startswith("Current runtime context") or t.startswith("（系统："):
                        continue
                    txt.append(t)
            if txt:
                break
        rec.player_text = " / ".join(txt[-2:])
        out.append(rec)
    return out


# ---------------------------------------------------------------- 回放

def _norm_args(a: Any) -> Any:
    if isinstance(a, str):
        try:
            a = json.loads(a)
        except Exception:
            pass
    return a


class Fixture:
    """把整份记录里出现过的工具结果当夹具，按"参数全等 → 关键键相同 → 同名"三档复用。"""

    def __init__(self, steps: List[StepRec]):
        self.exact: Dict[Tuple[str, str], str] = {}
        self.by_name: Dict[str, List[Tuple[str, str]]] = {}
        for st in steps:
            for call in st.real_calls:
                out = st.real_results.get(call.get("id"), "")
                if not out:
                    continue
                args = _norm_args(call.get("arguments"))
                k = (call["name"], json.dumps(args, ensure_ascii=False, sort_keys=True))
                self.exact.setdefault(k, out)
                self.by_name.setdefault(call["name"], []).append(k)

    def get(self, name: str, args: Any) -> Tuple[str, str]:
        args = _norm_args(args) or {}
        k = (name, json.dumps(args, ensure_ascii=False, sort_keys=True))
        if k in self.exact:
            return self.exact[k], "exact"
        for kk in self.by_name.get(name, []):
            have = json.loads(kk[1])
            if all(have.get(f) == args.get(f) for f in ("target", "topic", "op", "seller", "buyer")):
                return self.exact[kk], "near"
        for kk in self.by_name.get(name, []):
            return self.exact[kk], "same-tool"
        return json.dumps({"error": "回放夹具里没有该工具的结果"}, ensure_ascii=False), "missing"


async def replay(lab: Lab, client, st: StepRec, fixture: Fixture, multi: bool,
                 max_steps: int, use_logged_system: bool,
                 system_override: str = "") -> Dict[str, Any]:
    system = system_override or (st.system if (use_logged_system and st.system) else lab.system_prompt())
    messages = [json.loads(json.dumps(m, ensure_ascii=False)) for m in st.messages]
    tools = st.tools or lab.tools
    rec: Dict[str, Any] = {
        "turn": st.turn, "step": st.step, "player": st.player_text,
        "system_chars": len(system), "logged_system": bool(st.system) and use_logged_system,
        "system_differs_from_log": bool(st.system) and system != st.system,
        "real_calls": [{"name": c["name"], "arguments": c["arguments"]} for c in st.real_calls],
        "real_reasoning_len": st.real_reasoning_len,
        "calls": [], "steps": [], "final_text": "", "error": None,
        "fixture_hits": [],
    }
    t0 = time.monotonic()
    try:
        for _ in range(max_steps if multi else 1):
            r = await client.generate(system, messages, tools)
            text = (getattr(r, "text", "") or "").strip()
            calls = list(getattr(r, "tool_calls", None) or [])
            reasoning = (getattr(r, "reasoning", "") or "").strip()
            usage = getattr(r, "usage", None) or {}
            rec["steps"].append({"text": text, "reasoning_len": len(reasoning),
                                 "n_calls": len(calls),
                                 "prompt_tokens": usage.get("prompt_tokens"),
                                 "reasoning_tokens": (usage.get("prompt_tokens_details") or {}).get("reasoning_tokens")})
            for c in calls:
                args = _norm_args(c.get("arguments"))
                rec["calls"].append({"name": c.get("name"), "arguments": args,
                                     "schema_problems": validate_args(
                                         {t["function"]["name"]: t for t in tools}.get(c.get("name"), {}), args)})
            if not calls:
                rec["final_text"] = text
                break
            blocks = [{"type": "text", "text": text}] if text else []
            for c in calls:
                blocks.append({"type": "tool-call", "id": c.get("id"),
                               "name": c.get("name"), "arguments": c.get("arguments")})
            messages.append({"role": "assistant", "content": blocks})
            for c in calls:
                out, how = fixture.get(c.get("name"), c.get("arguments"))
                rec["fixture_hits"].append(how)
                messages.append({"role": "user",
                                 "content": [{"type": "tool-result", "toolCallId": c.get("id"),
                                              "content": [{"type": "text", "text": out}]}],
                                 "source": {"kind": "tool", "callId": c.get("id"), "toolName": c.get("name")}})
        else:
            rec["hit_step_limit"] = True
    except Exception as e:  # noqa: BLE001
        rec["error"] = f"{type(e).__name__}: {e}"
    rec["seconds"] = round(time.monotonic() - t0, 2)
    return rec


# ---------------------------------------------------------------- 判定

def verdict(st: StepRec, rec: Dict[str, Any]) -> str:
    """记录里真调了工具 = 这一步"该调"（当时同一套配置同一句提示词下模型自己选择了调）。"""
    real = bool(st.real_calls)
    got = bool(rec["calls"])
    if rec["error"]:
        return "ERR"
    if real and got:
        rn = {c["name"] for c in st.real_calls}
        gn = {c["name"] for c in rec["calls"]}
        return "OK" if rn == gn else "WRONG_TOOL"
    if real and not got:
        return "MISSED"          # 该调没调 —— 玩家口中的"很呆"
    if not real and got:
        return "EXTRA"           # 当时没调，现在调了（不一定是坏事，但要看见）
    return "NONE"


def verdict_expected(got_names: List[str], expect: List[str], err: str = "") -> str:
    """自拟台词用：expect = 期望的工具名列表，["none"] = 期望别调工具。"""
    if err:
        return "ERR"
    gn, want = set(got_names), set(expect)
    if "none" in want:
        return "OK" if not gn else "EXTRA"
    if not gn:
        return "MISSED"
    return "OK" if gn & want else ("WRONG_TOOL" if gn - want else "MISSED")


def say_case(st: StepRec, text: str, system: str, reminder: str = "") -> StepRec:
    """把一条自拟玩家台词接到真实历史后面 —— 真上下文 + 想测的说法。"""
    import copy
    msgs = [copy.deepcopy(m) for m in st.messages] + [{
        "role": "user",
        "content": [{"type": "text", "text": text}],
        "id": f"probe-say-{len(st.messages)}"}]
    if reminder:
        # 生产里 L1 各段就落在玩家话之后、模型开口之前 —— 提醒放这儿才有"最近一句"的分量
        msgs.append({"role": "user",
                     "content": [{"type": "text", "text": reminder}],
                     "id": "probe-reminder",
                     "source": {"kind": "plugin", "plugin": "@python-harness/system-prompt"}})
    rec = StepRec(turn=st.turn, step=0, boundary=st.boundary, system=system,
                  system_from_log=st.system_from_log, tools=list(st.tools), messages=msgs)
    rec.player_text = text
    return rec


# ---------------------------------------------------------------- CLI

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--npc", required=True)
    ap.add_argument("--world", default="")
    ap.add_argument("--config", default=os.environ.get("AGENT_LOOP_LAB_CONFIG", ""))
    ap.add_argument("--turns", default="", help="逗号分隔的 turn 号；缺省 = 所有玩家回合")
    ap.add_argument("--grep", action="append", default=[], help="只回放玩家话里含这些词的回合")
    ap.add_argument("--say", action="append", default=[],
                    help='接在真实历史后面的自拟台词，形如 "你宗门有何动静=>query_world"；'
                         '期望留空或 none = 期望不调工具')
    ap.add_argument("--say-file", default="",
                    help="每行一条 `台词=>期望工具`，UTF-8（中文走命令行容易花，建议用这个）")
    ap.add_argument("--reminder", default="",
                    help="在玩家话之后插一条 plugin 提醒消息（模拟新增的每轮 L1 段），只作用于 --say")
    ap.add_argument("--ctx", type=int, default=0, help="--say 用哪个 turn 的真实上下文（缺省 = 最后一回合）")
    ap.add_argument("--list", action="store_true", help="只列回合，不打模型")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--multi", action="store_true", help="调了工具就把真实结果喂回去，看收尾")
    ap.add_argument("--max-steps", type=int, default=4)
    ap.add_argument("--logged-system", dest="logged_system", action="store_true", default=True)
    ap.add_argument("--current-system", dest="logged_system", action="store_false",
                    help="用当前源码树装配的系统提示替换记录里那份")
    ap.add_argument("--system-file", default="",
                    help="用这份文件里的系统提示原文替换（上下文/台词不动）—— 单独隔离提示词这一个变量")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if args.say_file:
        for ln in Path(args.say_file).read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                args.say.append(ln)

    args.system_text = (Path(args.system_file).read_text(encoding="utf-8")
                        if args.system_file else "")
    if args.reminder.startswith("@"):
        args.reminder = Path(args.reminder[1:]).read_text(encoding="utf-8").strip()
    path = find_session(args.npc, args.world)
    evs = load_events(path)
    steps = index_steps(evs)
    first_of_turn: Dict[int, StepRec] = {}
    for st in steps:
        first_of_turn.setdefault(st.turn, st)
    targets = sorted(first_of_turn.values(), key=lambda s: s.turn)

    if args.say and not args.turns:
        targets = [first_of_turn[max(first_of_turn)] if not args.ctx
                   else first_of_turn.get(args.ctx) or targets[-1]]
    if args.turns:
        want = {int(x) for x in args.turns.replace(" ", ",").split(",") if x.strip()}
        targets = [s for s in targets if s.turn in want]
    if args.grep:
        targets = [s for s in targets if any(k in s.player_text for k in args.grep)]

    if args.list:
        print(f"{path}")
        for s in targets:
            called = "、".join(f"{c['name']}" for c in s.real_calls) or "—（没调工具）"
            print(f"  turn {s.turn:>3} sys{s.system_chars if hasattr(s,'system_chars') else len(s.system):>6}字 "
                  f"思考{s.real_reasoning_len:>4}字 | 玩家: {s.player_text[:38]!r} → {called}")
        return 0
    if not targets:
        print("没有匹配的回合")
        return 1

    lab = Lab(npc_id=urllib.parse.unquote(args.npc), config_path=args.config or None)
    client = lab.client()
    fixture = Fixture(steps)
    print(f"模型 {json.dumps(lab.model_info(), ensure_ascii=False)}")
    print(f"会话 {path.name}  回合 {len(targets)} × {args.repeat} 次  "
          f"系统提示={'记录原文' if args.logged_system else '当前装配'}  多步={args.multi}")

    results: List[Dict[str, Any]] = []

    # 真实回合 + 自拟台词（都挂在真实历史后面）
    cases: List[Tuple[StepRec, Optional[List[str]]]] = [(st, None) for st in targets]
    say_ctx = first_of_turn[max(first_of_turn)] if not args.ctx else first_of_turn.get(args.ctx) or targets[-1]
    sys_for_say = (args.system_text or
                   (say_ctx.system if (args.logged_system and say_ctx.system) else lab.system_prompt()))
    for spec in args.say:
        text, _, exp = spec.partition("=>")
        expect = [x.strip() for x in (exp or "none").split(",") if x.strip()]
        cases.append((say_case(say_ctx, text.strip(), sys_for_say, args.reminder), expect))

    async def run() -> None:
        for st, expect in cases:
            for i in range(max(1, args.repeat)):
                rec = await replay(lab, client, st, fixture, args.multi, args.max_steps,
                                   args.logged_system, args.system_text)
                rec["run"] = i + 1
                rec["expect"] = expect
                rec["verdict"] = (verdict(st, rec) if expect is None else
                                  verdict_expected([c["name"] for c in rec["calls"]], expect, rec["error"] or ""))
                results.append(rec)
                rc = "、".join(c["name"] for c in st.real_calls) or ("期望 " + "、".join(expect) if expect else "无")
                gc = "、".join(f"{c['name']}" for c in rec["calls"]) or "无"
                print(f"  turn {st.turn:>3} #{i+1} [{rec['verdict']:<10}] {rc:<26} → 现在调: {gc:<26} "
                      f"{rec['seconds']}s" + (f"  ERR {rec['error'][:60]}" if rec["error"] else ""))
                print(f"        玩家: {st.player_text[:70]!r}")
                if rec["final_text"]:
                    print(f"        回复: {rec['final_text'][:90]!r}")

    asyncio.run(run())

    tally: Dict[str, int] = {}
    for r in results:
        tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
    print(f"\n汇总：{tally}")
    missed = [r for r in results if r["verdict"] == "MISSED"]
    print(f"「当时调了工具、现在不调」的回合 {len(missed)}/{len(results)}")

    out = args.out or f"F:/.agent_loop_build/prompt_lab/replay_{args.npc}_{time.strftime('%m%d_%H%M%S')}.json"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps({"model": lab.model_info(), "session": str(path),
                                     "tally": tally, "results": results},
                                    ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"明细 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
