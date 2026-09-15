"""chat_cli.py — 命令行对话 I/O（验证链路的最小壳）

两种模式：
  python scripts/chat_cli.py --npc 林婉清              # 默认：Stub 内存桥，脱离 C# 直接驱动 Agent
  python scripts/chat_cli.py --ws --npc 林婉清         # WS 模式：连 server.py 的 WS 通道，模拟 C# 客户端

WS 模式交互（验证「C#→Python 全双工通道 + 流式」）：
  输入一行 → 发 player_message → 实时打印 step / text_delta（逐字）→ npc_reply（收尾）
  输入 /init <npc> <intent> [缘由] → 模拟 C# 端 NPC 主动开口（npc_initiative 事件）
  输入  /quit 退出
"""

from __future__ import annotations

import sys
from pathlib import Path

# ---- 导入引导：把「含 agent_loop 包的那一层」放进 sys.path（与 scripts/server.py 同一判据）。
# 旧实现写死 parent.parent.parent，只在「目录恰好叫 agent_loop」的开发机布局下成立。
# 同时认 .py/.pyc（发行包可能只发字节码，见 server.py 同名函数注释）。
def _has_agent_loop_pkg(cand: Path) -> bool:
    pkg = cand / "agent_loop"
    if not pkg.is_dir():
        return False
    if (pkg / "__init__.py").is_file() or (pkg / "__init__.pyc").is_file():
        return True
    return any(pkg.glob("__pycache__/__init__.*.pyc"))


def _bootstrap_sys_path() -> None:
    # PyInstaller 设 `sys.frozen`；**Nuitka 不设**（它注入的是 `__compiled__`）。
    # 只认 frozen 会让本守卫在 Nuitka 产物里静默失效（详见 scripts/server.py 同名函数注释）。
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return
    here = Path(__file__).resolve().parent
    for cand in (here, here.parent, here.parent.parent, here.parent.parent.parent):
        if _has_agent_loop_pkg(cand):
            if str(cand) not in sys.path:
                sys.path.insert(0, str(cand))
            return


_bootstrap_sys_path()
del _bootstrap_sys_path, _has_agent_loop_pkg

import argparse  # noqa: E402
import asyncio  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
from typing import List  # noqa: E402


async def run_stub(npc_id: str):
    """默认模式：Stub 内存桥 + Stub llm（离线自闭环，不依赖任何通道/LLM）。"""
    from agent_loop.agent_loop import AgentLoop
    from agent_loop.bridge import StubGameBridge
    from agent_loop.llm.stub_client import StubLlmClient

    bridge = StubGameBridge()
    bridge.seed_unit(npc_id, {"realm": "金丹", "sect": "化神殿", "pos": "永宁州", "mood": 72, "power": 8900})
    calls = {"n": 0}

    def _llm_fn(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"text": "", "tool_calls": [{"id": "t1", "name": "inspect_unit", "arguments": {"target": "张三"}}]}
        return {"text": "道友安好，我乃林婉清，金丹后期修士，栖居永宁州。", "tool_calls": []}

    loop = AgentLoop(bridge=bridge, llm=StubLlmClient(fn=_llm_fn))
    agent = loop.create(npc_id)
    print(f"[cli/stub] 与 [{npc_id}] 开始对话（Stub 内存桥，输入 /quit 退出）")

    while True:
        try:
            text = input("> ")
        except (EOFError, KeyboardInterrupt):
            print("\n[cli] 退出")
            break
        if text.strip().lower() in ("/quit", "/exit", "quit", "exit"):
            print("[cli] 退出")
            break
        if not text.strip():
            continue
        agent.send(text)
        await agent.run_until_idle()
        for t in _last_assistant_texts(agent):
            print(f"[{npc_id}]: {t}")


async def run_ws(npc_id: str, ws_url: str):
    """WS 模式：以模拟 C# 客户端身份连 server.py 的 WS 通道，实时打印 step/text_delta/npc_reply。"""
    import websockets

    async with websockets.connect(ws_url) as ws:
        print(f"[cli/ws] 已连接 {ws_url}，与 [{npc_id}] 对话（输入 /quit 退出）")
        print(f"[cli/ws] 提示：先启动 `python scripts/server.py`；否则连接会失败\n")
        while True:
            try:
                text = input("> ")
            except (EOFError, KeyboardInterrupt):
                print("\n[cli/ws] 退出")
                break
            if text.strip().lower() in ("/quit", "/exit", "quit", "exit"):
                print("[cli/ws] 退出")
                break
            if not text.strip():
                continue
            # /init <npc> <intent> [reason]：模拟 C# 端 NPC 主动开口（NpcInitiativeMonitor 触发）
            if text.startswith("/init"):
                parts = text.split(maxsplit=3)
                if len(parts) < 3:
                    print("用法: /init <npc名> <intent> [缘由]\n  意图键: greet/smalltalk/courteous/life/recent/missing/affection/malice/vent/provocation/disdain")
                    continue
                init_npc = parts[1]
                init_intent = parts[2]
                init_reason = parts[3] if len(parts) > 3 else ""
                msg = {"type": "event", "event": "npc_initiative", "npc_id": init_npc, "intent": init_intent, "reason": init_reason}
            else:
                msg = {"type": "event", "event": "player_message", "npc_id": npc_id, "text": text}
                print(f"你: {text}")
            await ws.send(json.dumps(msg, ensure_ascii=False))
            initiated = text.startswith("/init")
            streamed = ""
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=60)
                msg = json.loads(raw)
                ev = msg.get("event")
                if ev == "step":
                    kind = msg.get("kind")
                    if kind == "tool_call":
                        print(f"  · [调用工具] {msg.get('name')} {msg.get('args')}")
                    elif kind == "tool_result":
                        print(f"  · [工具结果] {msg.get('text')}")
                    else:
                        print(f"  · [step:{kind}] {msg.get('text')}")
                elif ev == "text_delta":
                    streamed += msg.get("text") or ""
                    print(msg.get("text") or "", end="", flush=True)
                elif ev == "npc_reply":
                    if initiated:
                        print("（对方主动传音）", end="")
                    print()
                    break
            if not streamed:
                print(f"[{npc_id}]: （无回复）")


def _last_assistant_texts(agent) -> List[str]:
    out: List[str] = []
    for ev in agent.session.log:
        if ev.get("type") != "assistant/message":
            continue
        msg = (ev.get("data") or {}).get("message", {}) or {}
        for b in msg.get("content", []) or []:
            if isinstance(b, dict) and b.get("type") == "text":
                t = (b.get("text") or "").strip()
                if t:
                    out.append(t)
    return out


def main():
    parser = argparse.ArgumentParser(description="命令行对话 I/O")
    parser.add_argument("--npc", default="林婉清", help="NPC 身份 id")
    parser.add_argument("--ws", action="store_true", help="WS 模式：连 server.py 的通道，模拟 C# 客户端")
    parser.add_argument("--bridge", default="ws://127.0.0.1:8766", help="WS 通道地址（--ws 模式）")
    parser.add_argument("--log", metavar="SWITCH", help="日志总闸：off/on/debug/info/warning/error")
    parser.add_argument("--no-log", dest="no_log", action="store_true", help="等价于 --log off")
    parser.add_argument("--log-level", metavar="LEVEL", help="只改日志级别")
    args = parser.parse_args()

    # 日志装配（与 server.py 同规约：装配点只在入口，业务模块一律只取 logger）
    # 总闸优先级：CLI 参数 > 环境变量 AGENT_LOOP_LOG > config.json 的 logging 块
    from agent_loop import log_setup
    from agent_loop.config_loader import load_config

    switch = ("off" if args.no_log else (args.log or args.log_level
                                        or os.environ.get("AGENT_LOOP_LOG") or None))
    snap = log_setup.setup_logging(load_config(), switch=switch)
    log_setup.install_process_hooks()
    if snap.get("enabled"):
        log_setup.get_logger("agent_loop.chat_cli").info(
            "chat_cli 启动：npc=%s mode=%s level=%s file=%s",
            args.npc, "ws" if args.ws else "stub", snap.get("level"), snap.get("path") or "仅控制台")
    else:
        print(f"[chat_cli] 日志已关闭：{log_setup.describe_switch(switch)}", file=sys.stderr)
    try:
        if args.ws:
            asyncio.run(run_ws(args.npc, args.bridge))
        else:
            asyncio.run(run_stub(args.npc))
    finally:
        log_setup.shutdown_logging()


if __name__ == "__main__":
    main()
