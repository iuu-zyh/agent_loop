"""prompt_lab.lab_core — 提示词探针底座：真提示词 + 真工具表 + 真模型。

一句话：把游戏里那一套（SystemPrompt 装配的系统提示、9 件工具 schema、L1 运行时上下文、
Canonical 消息形状）原样搬到命令行，用 config.json 里那套 base_url/api_key/model 打真请求，
把模型的选择（调不调工具、调哪件、参数是什么、说的话是什么）落成可判读的记录。

为什么要它（而不是读代码猜）：
    提示词是"给模型看的程序"，它的 bug 只能在**模型的实际输出**里看见。
    本仓库已经吃过"改完提示词只跑单测"的亏 —— 单测只能证明字面量没写错，
    证明不了模型在新措辞下还会不会把 id 填进 target、会不会异地硬调切磋。

设计约束：
- **不改动任何产品代码**：本模块只读 prompts/tools/config，绝不写回。
- **日志隔离**：把 AGENT_LOOP_DATA 指到 scratch 目录，别把探针的日志混进开发机 logs/。
  （prompts 会因此从 `<data>/prompts` 找不到而回退包目录那份 = 源码树里这份，正是要测的。）
- **只走 LlmClient.generate 一个口**：与 DialogueAgent 同一条路（含重试/看门狗/协议自适应），
  这样探针里看到的行为就是玩家看到的行为。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------- 包根 / 环境隔离

def _bootstrap() -> Path:
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "agent_loop" / "__init__.py").is_file():
            if str(p) not in sys.path:
                sys.path.insert(0, str(p))
            return p / "agent_loop"
    raise RuntimeError("找不到 agent_loop 包根（本脚本必须放在源码树内）")


PKG_DIR = _bootstrap()
TREE_ROOT = PKG_DIR.parent


def _default_scratch() -> Path:
    mount = Path("/mnt/f/.agent_loop_build")
    if mount.is_dir():
        return mount / "prompt_lab" / "_runtime"
    win = Path("F:/.agent_loop_build")
    if win.parent.exists():
        return win / "prompt_lab" / "_runtime"
    return TREE_ROOT / ".prompt_lab_data"


def isolate_runtime(scratch: Path) -> None:
    """把运行期数据根指到 scratch：日志/会话不落进开发机的 logs/ 与 ~/.sessions。

    必须在 import agent_loop.* 之前 / 或至少在 log_setup 首次取 logger 之前设置。
    prompts 根会因 `<scratch>/prompts` 不存在而回退到包目录（= 源码树这份），正是要测的。
    """
    scratch.mkdir(parents=True, exist_ok=True)
    os.environ["AGENT_LOOP_DATA"] = str(scratch)
    # 生产判定（require_world）在探针里无意义：不给 PPID，避免任何"游戏拉起"分支
    os.environ.pop("AGENT_LOOP_PPID", None)


# ---------------------------------------------------------------- 结果结构

@dataclass
class Step:
    index: int
    text: str = ""
    reasoning: str = ""
    calls: List[Dict[str, Any]] = field(default_factory=list)   # [{id,name,arguments}]
    usage: Any = None
    seconds: float = 0.0
    error: Optional[str] = None


@dataclass
class ScenarioRun:
    sid: str
    note: str = ""
    steps: List[Step] = field(default_factory=list)
    calls: List[Dict[str, Any]] = field(default_factory=list)   # 扁平化 [{step,name,arguments}]
    final_text: str = ""
    seconds: float = 0.0
    error: Optional[str] = None
    raw: str = ""                                               # prune 场景：待压缩的原始工具结果
    meta: Dict[str, Any] = field(default_factory=dict)          # 场景自报的中间量（如提示词长度）

    @property
    def tool_names(self) -> List[str]:
        return [c["name"] for c in self.calls]


# ---------------------------------------------------------------- JSON Schema 最小校验
# 只覆盖 tools/schemas.py 实际用到的关键字：type/enum/required/additionalProperties/
# minimum/maximum/minItems/items/properties。够用来抓"模型填错枚举/漏必填/多塞键"。

_TYPE_MAP = {
    "string": str, "integer": int, "number": (int, float), "boolean": bool,
    "array": list, "object": dict,
}


def _type_ok(value: Any, want: str) -> bool:
    if want == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if want == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if want == "boolean":
        return isinstance(value, bool)
    py = _TYPE_MAP.get(want)
    return isinstance(value, py) if py else True


def validate_args(schema: Dict[str, Any], args: Any) -> List[str]:
    """按工具 JSON Schema 校验一次调用的参数，返回问题列表（空 = 合法）。"""
    fn = (schema or {}).get("function", {})
    spec = fn.get("parameters") or {}
    name = fn.get("name", "?")
    problems: List[str] = []
    if not isinstance(args, dict):
        return [f"{name}: arguments 不是对象（{type(args).__name__}）"]
    props: Dict[str, Any] = spec.get("properties") or {}
    for key in spec.get("required", []) or []:
        if key not in args or args.get(key) in (None, ""):
            problems.append(f"{name}.{key}: 缺必填")
    if spec.get("additionalProperties") is False:
        for key in args:
            if key not in props:
                problems.append(f"{name}.{key}: 不是该工具的参数（additionalProperties=false）")
    for key, val in args.items():
        sub = props.get(key)
        if not isinstance(sub, dict) or val is None:
            continue
        want = sub.get("type")
        if want and not _type_ok(val, want):
            problems.append(f"{name}.{key}: 类型应为 {want}，实为 {type(val).__name__}")
            continue
        if "enum" in sub and val not in sub["enum"]:
            problems.append(f"{name}.{key}: 取值 {val!r} 不在 enum {sub['enum']}")
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            if "minimum" in sub and val < sub["minimum"]:
                problems.append(f"{name}.{key}: {val} < minimum {sub['minimum']}")
            if "maximum" in sub and val > sub["maximum"]:
                problems.append(f"{name}.{key}: {val} > maximum {sub['maximum']}")
        if isinstance(val, list) and "minItems" in sub and len(val) < sub["minItems"]:
            problems.append(f"{name}.{key}: 元素数 {len(val)} < minItems {sub['minItems']}")
        if isinstance(val, list) and isinstance(sub.get("items"), dict):
            item_spec = sub["items"]
            if "enum" in item_spec:
                for it in val:
                    if it not in item_spec["enum"]:
                        problems.append(f"{name}.{key}[]: 取值 {it!r} 不在 enum {item_spec['enum']}")
        if isinstance(val, dict) and isinstance(sub.get("properties"), dict):
            for k2, v2 in val.items():
                s2 = sub["properties"].get(k2)
                if s2 is None:
                    if sub.get("additionalProperties") is False:
                        problems.append(f"{name}.{key}.{k2}: 不是合法子键")
                    continue
                if s2.get("type") and not _type_ok(v2, s2["type"]):
                    problems.append(f"{name}.{key}.{k2}: 类型应为 {s2['type']}，实为 {type(v2).__name__}")
                if "enum" in s2 and v2 not in s2["enum"]:
                    problems.append(f"{name}.{key}.{k2}: 取值 {v2!r} 不在 enum {s2['enum']}")
    return problems


# ---------------------------------------------------------------- 探针主体

class Lab:
    """一屉一人设：装好系统提示与工具表，按场景跑多步对话。"""

    def __init__(
        self,
        npc_id: str = "云含",
        persona: Optional[str] = None,
        player_persona: Optional[str] = None,
        scratch: Optional[Path] = None,
        config_path: Optional[Path] = None,
    ):
        if scratch is None:
            scratch = _default_scratch()
        isolate_runtime(scratch)
        from agent_loop.agent.system_prompt import SystemPrompt
        from agent_loop.tools.schemas import ALL_TOOL_SCHEMAS, TOOL_ORDER

        self.npc_id = npc_id
        # 默认用源码树那份 config.json；--config / AGENT_LOOP_LAB_CONFIG 可换成游戏里
        # ModAssets 那份，用来复现"玩家实际用的模型 + 参数"下的行为。
        cfg = config_path or os.environ.get("AGENT_LOOP_LAB_CONFIG")
        self.config_path = Path(cfg).resolve() if cfg else (PKG_DIR / "config.json")
        self.sp = SystemPrompt.instance()
        self.sp.ensure_agent_layer(npc_id)
        self.reset_persona()
        if persona is not None:
            self.set_persona(persona)
        if player_persona is not None:
            self.sp.set_player_persona(player_persona)
        else:
            self.sp.set_player_persona(None)
        self.tools = ALL_TOOL_SCHEMAS
        self._tool_schema = {t["function"]["name"]: t for t in ALL_TOOL_SCHEMAS}
        self.tool_order = list(TOOL_ORDER)

    # ---- 人设（场景级覆盖，跑完要能还原） ----

    def set_persona(self, text: str) -> None:
        layer = self.sp.scoped.get(self.npc_id)
        if layer and "deployment:persona" in layer.sections:
            layer.sections["deployment:persona"]["text"] = text
            # 断开"文件源"跟踪，免得 _refresh_persona 把注入的覆盖回文件版
            self.sp._persona_file_cache.pop(self.npc_id, None)

    def reset_persona(self) -> None:
        """还原成 prompts/personas 里那份（含 _suffix）。"""
        try:
            self.set_persona(self.sp._load_persona(self.npc_id))
        except Exception:
            pass

    # ---- 装配 ----

    def system_prompt(self) -> str:
        asm = self.sp.assemble(scope=self.npc_id)
        return self.sp.render_prompt(asm)

    def l1_messages(self, raw: Dict[str, Any], roster: Any = None) -> List[Dict[str, Any]]:
        """L1 运行时上下文 → 逐段 plugin user 消息（与 DialogueAgent._pre_step 同形）。

        roster 必须像生产那样给（dialogue_agent:854 传 self.roster）：名单决定「在场/同席」
        段的有无。不给的话，`format_l1_context` 里那条 `roster is None` 兜底是**够不着的**
        （roster 在更上面已被归一成 []）→ C# 的 others 明细整包丢弃。
        """
        segs = self.sp.format_l1_context(self.npc_id, raw, roster=roster)
        out: List[Dict[str, Any]] = []
        for name, text in segs.items():
            rendered = self.sp.render_context_segment(name, text)
            if rendered:
                out.append({
                    "role": "user",
                    "content": [{"type": "text", "text": rendered}],
                    "id": f"ctx-{name}",
                    "source": {"kind": "plugin", "plugin": "@python-harness/system-prompt"},
                })
        return out

    # ---- 客户端 ----

    def client(self, **kw):
        from agent_loop.llm.factory import create_llm_client
        return create_llm_client(config_path=self.config_path, **kw)

    def model_info(self) -> Dict[str, Any]:
        from agent_loop.config.config_loader import load_config
        llm = (load_config(self.config_path) or {}).get("llm", {}) or {}
        return {"base_url": llm.get("base_url"), "model": llm.get("model"),
                "protocol": llm.get("protocol") or "auto"}

    # ---- 跑一个场景 ----

    async def run_scenario(
        self,
        client,
        scenario: Dict[str, Any],
        system: Optional[str] = None,
        max_steps: Optional[int] = None,
    ) -> ScenarioRun:
        """场景字段：
        - id/note
        - l1: dict（喂 format_l1_context 的 raw）
        - turns: [str, ...] 玩家消息（多条 = 连续追问；中间不插模型回合的地方用 beats 更细）
        - tools: {工具名: callable(args)->str}，未命中走 fallback(args)
        - fallback: callable(name, args)->str
        - max_steps: 上限（默认 3，防止模型无限自纠把探针跑爆）
        """
        sid = scenario["id"]
        run = ScenarioRun(sid=sid, note=scenario.get("note", ""))
        system = system if system is not None else self.system_prompt()
        max_steps = int(scenario.get("max_steps", max_steps or 4))
        execs: Dict[str, Callable[[Dict[str, Any]], str]] = scenario.get("tools") or {}
        fallback = scenario.get("fallback")

        roster = scenario.get("roster")
        if roster is None:
            others = (scenario.get("l1") or {}).get("others") or []
            roster = [str((o or {}).get("name") or (o or {}).get("id") or "") for o in others if isinstance(o, dict)]
            roster = [x for x in roster if x] or None
        messages: List[Dict[str, Any]] = list(self.l1_messages(scenario.get("l1") or {}, roster=roster))
        # 提案私信（群聊编排器排的队）：与 ws_channel.py:655 同形 —— plugin 消息、开跑前注入
        for _note in scenario.get("notices") or []:
            messages.append({
                "role": "user",
                "content": [{"type": "text", "text": _note}],
                "id": f"notice-{len(messages)}",
                "source": {"kind": "plugin", "plugin": "@python-harness/proposal"},
            })
        run.meta["system_chars"] = len(system)
        run.meta["tools_chars"] = len(json.dumps(self.tools, ensure_ascii=False))
        t0 = time.monotonic()
        try:
            for turn_text in scenario.get("turns") or []:
                messages.append({
                    "role": "user",
                    "content": [{"type": "text", "text": turn_text}],
                    "id": f"player-{len(messages)}",
                })
                for _ in range(max_steps):
                    idx = len(run.steps)
                    st = Step(index=idx)
                    run.steps.append(st)
                    _t = time.monotonic()
                    try:
                        res = await client.generate(system, messages, self.tools)
                    except Exception as e:  # noqa: BLE001 探针要把异常如实记下来
                        st.error = f"{type(e).__name__}: {e}"
                        st.seconds = time.monotonic() - _t
                        raise
                    st.seconds = time.monotonic() - _t
                    st.text = (getattr(res, "text", "") or "").strip()
                    st.reasoning = (getattr(res, "reasoning", "") or "").strip()
                    st.usage = getattr(res, "usage", None)
                    st.calls = list(getattr(res, "tool_calls", None) or [])
                    for c in st.calls:
                        run.calls.append({
                            "step": idx,
                            "name": c.get("name"),
                            "arguments": c.get("arguments"),
                            "schema_problems": validate_args(
                                self._tool_schema.get(c.get("name"), {}), c.get("arguments")),
                        })
                    if not st.calls:
                        run.final_text = st.text
                        break
                    # 回填 assistant 回合（含工具调用），再逐条把结果喂回去
                    blocks: List[Dict[str, Any]] = []
                    if st.text:
                        blocks.append({"type": "text", "text": st.text})
                    for c in st.calls:
                        blocks.append({"type": "tool-call", "id": c.get("id"),
                                       "name": c.get("name"), "arguments": c.get("arguments")})
                    messages.append({"role": "assistant", "content": blocks})
                    for c in st.calls:
                        name, args = c.get("name"), c.get("arguments")
                        try:
                            if name in execs:
                                out = execs[name](args)
                            elif fallback is not None:
                                out = fallback(name, args)
                            else:
                                out = json.dumps({"error": "该工具在本探针场景里没有准备结果"},
                                                 ensure_ascii=False)
                        except Exception as e:  # noqa: BLE001
                            out = json.dumps({"error": f"探针执行器异常：{e}"}, ensure_ascii=False)
                        messages.append({
                            "role": "user",
                            "content": [{"type": "tool-result", "toolCallId": c.get("id"),
                                         "content": [{"type": "text", "text": out}]}],
                            "source": {"kind": "tool", "callId": c.get("id"), "toolName": name},
                        })
                    # 工具结果已全部回填 → 进入下一步，看模型接着做什么
                else:
                    # 步数用尽仍在调工具：如实记下，别静默
                    run.meta["hit_step_limit"] = True
        except Exception as e:  # noqa: BLE001
            run.error = f"{type(e).__name__}: {e}"
        run.seconds = time.monotonic() - t0
        run.meta["system"] = system
        run.meta["messages"] = _slim_messages(messages)
        return run


def _slim_messages(messages: List[Dict[str, Any]], max_text: int = 1200) -> List[Dict[str, Any]]:
    """给报告留一份可读的消息流（长文本截断，避免报告几百 KB）。"""
    out = []
    for m in messages:
        blocks = []
        for b in m.get("content") or []:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "text":
                txt = b.get("text") or ""
                blocks.append({"type": "text", "text": txt if len(txt) <= max_text else txt[:max_text] + "…"})
            elif t == "tool-call":
                blocks.append({"type": "tool-call", "name": b.get("name"), "arguments": b.get("arguments")})
            elif t == "tool-result":
                inner = b.get("content") or []
                txt = "".join(c.get("text", "") for c in inner if isinstance(c, dict))
                blocks.append({"type": "tool-result", "text": txt if len(txt) <= max_text else txt[:max_text] + "…"})
        out.append({"role": m.get("role"), "kind": (m.get("source") or {}).get("kind"), "content": blocks})
    return out
