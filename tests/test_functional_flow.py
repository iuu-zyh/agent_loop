"""功能回归测试：真实 LLM / Stub 桥全链路（新建 → 屉 → 纯消息 → 工具多 step → 重建续档）

本测试面向「平台流程正确性 + 功能正常」，可与真实 LLM 配置（config.json，键 base_url/api_key/model）
一起运行；也可用 StubLlmClient 离线跑以校验结构。

覆盖（对照需求的流程）：
  新建 агент (session/屉) → 首轮纯消息 → 工具触发多 step → Dispose 后重建续档
"""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile

from agent_loop.agent_loop import AgentLoop
from agent_loop.bridge import StubGameBridge
from agent_loop.llm.stub_client import StubLlmClient
from agent_loop.system_prompt import SystemPrompt
from agent_loop.persistence import save_session, load_session

ROOT = os.path.join(tempfile.gettempdir(), "agent_loop_functional")
NPC = "林婉清_流程测试"
REPLY_TOOLS = "张三"


def _reset(bridge=None, llm=None):
    AgentLoop.reset_for_test(storage_root=ROOT, bridge=bridge, llm=llm)
    if os.path.isdir(ROOT):
        shutil.rmtree(ROOT, ignore_errors=True)


def test_create_session_and_drawer():
    """新建：session 建立，屉有 default-based persona 层，npc_name 变量就位。"""
    _reset()
    loop = AgentLoop()
    agent = loop.create(NPC)
    assert agent.session.id == NPC
    assert agent.session.header.get("id") == NPC
    sp = SystemPrompt.instance()
    scoped = sp.scoped.get(NPC)
    assert scoped is not None, "抽屉应已为 NPC 建立"
    assert "deployment:persona" in scoped.sections, "抽屉应有 persona 张纸"
    assert scoped.variables.get("npc_name") == NPC
    print("✓ 新建：session/屉建立，persona 层 + npc_name 就位")
    return loop


def test_first_chat_structure():
    """纯消息：log 事件结构完整，出现 assistant 文本回复。"""
    stub = StubLlmClient(fn=lambda req: {"text": "道友安好，我乃林婉清。", "tool_calls": []})
    _reset(llm=stub)
    loop = AgentLoop()
    agent = loop.create(NPC)

    async def _go(a):
        a.send("你好，你是谁？")
        await a.run_until_idle()

    asyncio.run(_go(agent))
    types = [ev["type"] for ev in agent.session.log]
    for t in ("turn/start", "user/message", "request/header", "step/start", "assistant/message", "step/end", "turn/end"):
        assert t in types, f"缺少事件 {t}, 实际={types}"
    texts = [
        b["text"] for ev in agent.session.log if ev.get("type") == "assistant/message"
        for b in (ev.get("data", {}).get("message", {}) or {}).get("content", [])
        if isinstance(b, dict) and b.get("type") == "text" and b.get("text")
    ]
    assert texts, "应有 assistant 文本回复"
    print(f"✓ 纯消息：log 结构完整（{len(types)} 事件），回复 {texts[0][:20]!r}...")
    return agent


def test_tool_multi_step():
    """工具多 step：LLM 产 tool_calls → Stub 执行 → tool/result → 最终答复。"""
    bridge = StubGameBridge()
    bridge.seed_unit("林婉清", {"realm": "金丹", "sect": "化神殿", "pos": "永宁州", "mood": 72, "power": 8900})
    bridge.seed_unit(REPLY_TOOLS, {"realm": "筑基", "sect": "七星阁", "pos": "永宁州", "mood": 60, "power": 5000})

    # Stub LLM：第一调用返回 tool_calls，第二调用返回文本（模拟"查完再答"）
    calls = {"n": 0}
    def _llm_fn(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"text": "", "tool_calls": [{"id": "t1", "name": "inspect_unit", "arguments": {"target": REPLY_TOOLS}}]}
        return {"text": "张三道友是筑基中期修士，实力约五千。", "tool_calls": []}

    # 必须经 reset_for_test 一次性注入 bridge+llm（单例 __init__ 仅在首次执行，后续 create 不会覆盖）
    _reset(bridge=bridge, llm=StubLlmClient(fn=_llm_fn))
    loop = AgentLoop()
    agent = loop.create(NPC)

    async def _go(a):
        a.send("查一下张三的境界")
        await a.run_until_idle()

    asyncio.run(_go(agent))
    types = [ev["type"] for ev in agent.session.log]
    assert "tool/call" in types and "tool/result" in types, f"应发生工具调用, types={types}"
    assert calls["n"] >= 2, "应多 step（至少两次 LLM 调用：工具前 + 工具后）"
    text = [
        b["text"] for ev in agent.session.log if ev.get("type") == "assistant/message"
        for b in (ev.get("data", {}).get("message", {}) or {}).get("content", [])
        if isinstance(b, dict) and b.get("type") == "text" and b.get("text")
    ]
    assert len(text) >= 1, "工具之后应有最终文本答复"
    print(f"✓ 工具多 step：tool/call + tool/result + 两轮 LLM（n={calls['n']}），最终答复 {text[-1][:20]!r}...")
    return agent


def test_rebuild_resume(agent=None):
    """重建续档：保存 → 清空 store → 重建同一 NPC → 历史保留。

    pytest 直跑时 agent 缺省自建（纯消息一轮）；`__main__` 链式跑时接上文 tool_multi_step 的 agent。
    """
    if agent is None:
        _reset(llm=StubLlmClient(fn=lambda req: {"text": "道友安好，我乃林婉清。", "tool_calls": []}))
        agent = AgentLoop().create(NPC)

        async def _speak(a):
            a.send("你好，你是谁？")
            await a.run_until_idle()

        asyncio.run(_speak(agent))
    npc = agent.session.id
    save_session(agent.session, ROOT)
    # 模拟"关进程再开"：清空 store + 保留存储文件
    loop = AgentLoop()
    loop.store.clear()
    try:
        agent.dispose()
    except Exception:
        pass
    loop.storage_root = ROOT
    loaded = load_session(npc, ROOT)
    assert loaded is not None
    agent2 = loop.create(npc)  # 会 resume 续档
    t2 = [ev["type"] for ev in agent2.session.log]
    assert "user/message" in t2, "续档后应保留历史 user/message"
    print(f"✓ 重建续档：历史保留（{len(t2)} 事件）")


if __name__ == "__main__":
    loop = test_create_session_and_drawer()
    agent = test_first_chat_structure()
    agent2 = test_tool_multi_step()
    test_rebuild_resume(agent2)
    print("\n功能回归全部通过")