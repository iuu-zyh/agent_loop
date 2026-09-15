"""dump_initiative_prompt — 打印一次「NPC 主动开口」回合喂给 LLM 的**完整提示词**

用途：自主交互（含 game_drama / busy 变体）用的是与被动对话**完全同一套**提示词装配，
只多一条"伪 user 意图"。本脚本用真实 SystemPrompt / prompts 目录 + Stub 桥，把那一回合的
`system` 与 `messages` 原样打出来，便于核对（不连游戏、不调真 LLM）。

用法：
    cd F:\\agent_loop && python3 scripts/dev/dump_initiative_prompt.py [npc] [intent] [缘由]

默认：郦安 / missing / "念及与你多日情谊，特来以神识传音寻你"
环境：AGENT_LOOP_ROOT 可覆盖仓库根（默认脚本上两级）。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

ROOT = os.environ.get("AGENT_LOOP_ROOT") or os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.dirname(ROOT))
os.chdir(ROOT)

from agent_loop.agent_loop import AgentLoop            # noqa: E402
from agent_loop.bridge import StubGameBridge           # noqa: E402
from agent_loop.llm.stub_client import StubLlmClient   # noqa: E402
from agent_loop.ws_channel import WsServer, ChatHub    # noqa: E402

NPC = sys.argv[1] if len(sys.argv) > 1 else "郦安"
INTENT = sys.argv[2] if len(sys.argv) > 2 else "missing"
REASON = sys.argv[3] if len(sys.argv) > 3 else "念及与你多日情谊，特来以神识传音寻你"

captured = []


def _fn(req):
    captured.append(req)
    return {"text": "（示例回复：脚本只为抓提示词，不产生真实内容）", "tool_calls": []}


def main() -> None:
    root = os.path.join(tempfile.gettempdir(), "agent_loop_dump_prompt")
    bridge = StubGameBridge()
    bridge.seed_unit(NPC, {
        "realm": "元婴", "sect": "七星阁", "pos": "永宁州", "mood": 80, "power": 22000,
        "player": {"name": "缪嘉歆", "realm": "筑基", "relation": "道侣", "intim": 180, "same_grid": True},
        "recent": "上月在雷泽觅得一件异宝",
    })
    AgentLoop.reset_for_test(storage_root=root, bridge=bridge, llm=StubLlmClient(fn=_fn))
    loop = AgentLoop()
    hub = ChatHub(loop, WsServer(port=0))
    asyncio.run(hub.handle_initiative(NPC, INTENT, REASON, consented=True))

    if not captured:
        print("!! 没有捕获到 LLM 请求（回合未跑到 LLM？）")
        return
    req = captured[0]
    print("=" * 100)
    print(f"# 场景：NPC={NPC} intent={INTENT} 缘由={REASON!r}（consented 变体）")
    print(f"# 请求条数={len(captured)}（首条即本回合第一次 LLM 调用）")
    print("=" * 100)
    print("\n########## [1] system（SystemPrompt.assemble 产物，按段落拼接）##########\n")
    print(req["system"])
    print("\n########## [2] messages（账本投影 → 模型消息；真实请求还会在最前面加一条 role=system）##########\n")
    print(json.dumps(req["messages"], ensure_ascii=False, indent=2))
    print("\n########## [3] tools（恒定 8 个，忙碌变体也不动它）##########\n")
    names = [t.get("function", {}).get("name") or t.get("name") for t in (req.get("tools") or [])]
    print(json.dumps(names, ensure_ascii=False))
    print("\n（提示：真实线上请求的 system 段还会带 tools 的 JSON schema，见 llm/openai_client.py:canonical_to_openai）")


if __name__ == "__main__":
    main()
