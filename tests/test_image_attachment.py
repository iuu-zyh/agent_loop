"""图片附件：运输契约 + 「只进当回合、不进历史」不变量（09-13）

背景：Python 侧图片能力早已贯通（`tests/test_image_input.py` 覆盖 adapter 层），
缺的是「C# → Python」的运输与生命周期。本次新增：
  · C# `ImageInput` 把图片路径编码成 data URL，文本里留 `[图片：xx]` 占位；
  · `WsClient.SendPlayerMessage(npc, text, images)` 带图发帧；
  · `ChatHub.handle_message(npc, text, images)` 收图；
  · `DialogueAgent.send(images=)` 落内存侧表，`_attach_turn_images` 仅在拼请求时挂图。

本文件钉住四条不变量（都是「静默出错」型，必须靠断言守）：
  1. 模型**看得到**图（当回合请求里真有 image 块）；
  2. 账本**看不到**图（session.log / derive_messages 里没有 image 块、没有 base64）；
  3. 同回合多步都看得到（不是只第一步）；
  4. 下一回合、以及任何重放，都**不再**携带图片字节。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from agent_loop.agent_loop import AgentLoop
from agent_loop.llm.base import LlmResult
from agent_loop.llm.echo_client import EchoLlmClient

ROOT = "/tmp/agent_loop_test_image_attach"
NPC = "云含"

# 一眼能认出的假 data URL：真出现「泄漏」时断言消息里能直接看到它
FAKE_URL = "data:image/png;base64,AAECAwQFBgcICQ=="
FAKE_URL_B = "data:image/png;base64,CQkJCQkJCQkJCQ=="


class SpyLlm:
    """记录每次 generate 收到的 messages（深拷贝，防事后被改）。"""

    def __init__(self) -> None:
        self.calls: List[List[Dict[str, Any]]] = []
        self._echo = EchoLlmClient()

    async def generate(self, system, messages, tools, on_token=None, on_reasoning=None):
        self.calls.append(json.loads(json.dumps(messages, ensure_ascii=False, default=str)))
        return await self._echo.generate(system, messages, tools, on_token, on_reasoning)

    def images_seen(self, call_index: int = -1) -> List[str]:
        """第 N 次调用里，全部消息的 image 块 url。"""
        out: List[str] = []
        for m in self.calls[call_index]:
            for b in (m.get("content") or []):
                if isinstance(b, dict) and b.get("type") == "image":
                    out.append(b.get("url"))
        return out


class ToolCallingLlm(SpyLlm):
    """占位：本文件不再靠工具循环逼多步（改用直接验 _attach_turn_images 的纯函数性）。"""


def _new_loop(llm) -> AgentLoop:
    return AgentLoop.reset_for_test(storage_root=ROOT, llm=llm)


# ----------------------------------------------------------------------
# 1 + 2：模型看得到，账本看不到
# ----------------------------------------------------------------------

def test_image_reaches_model_but_not_ledger():
    llm = SpyLlm()
    agent = _new_loop(llm).create(NPC)
    agent.send("这张图里的人是谁\n[图片：屏幕截图 2026-09-13 002402.png]",
               images=[{"name": "屏幕截图 2026-09-13 002402.png", "url": FAKE_URL}])
    asyncio.run(agent.run_until_idle())

    # ① 模型看得到
    assert llm.images_seen() == [FAKE_URL], f"当回合请求应含 1 张图，实际={llm.images_seen()}"

    # ② 账本看不到：session.log 里不得出现 image 块或 base64 本体
    dump = json.dumps(agent.session.log, ensure_ascii=False)
    assert FAKE_URL not in dump, "图像字节泄漏进了 session.log（图片必须只存内存侧表）"
    assert '"type": "image"' not in dump and '"type":"image"' not in dump, \
        "账本里出现了 image 块，违反「图片不进历史」"

    # ③ 账本投影（= 后续回合与 UI 历史的输入）同样干净，且占位文本还在
    msgs = agent.session.derive_messages()
    assert not any(b.get("type") == "image" for m in msgs for b in (m.get("content") or [])
                   if isinstance(b, dict)), "derive_messages 里仍有 image 块"
    assert any("[图片：屏幕截图 2026-09-13 002402.png]" in json.dumps(m, ensure_ascii=False)
               for m in msgs), "占位文本应留在历史里（否则后续回合看不出当时发过图）"

    # ④ 侧表已随回合收口清空
    assert agent._turn_images == {}, "回合结束后图片侧表应清空"
    print("✓ 图片：模型可见 / 账本不可见 / 占位留存 / 侧表清空")


# ----------------------------------------------------------------------
# 3：同回合多步都看得到（不是只第一步）
# ----------------------------------------------------------------------

def test_attach_is_pure_and_repeatable():
    """同回合每一步都会重新挂图，且挂图**绝不改账本本体**。

    直接验 `_attach_turn_images`（而不是靠工具循环逼出真实两步）：本测试要钉的是
    「浅拷贝 + 每次调用都挂上」这两条 —— 原地改 `derive_messages()` 返回的对象，就等于把
    base64 永久写进 session.log，正是「图片不进历史」要防的事故；两步循环只是它的表现形式。
    """
    llm = SpyLlm()
    agent = _new_loop(llm).create(NPC)
    agent.send("看图\n[图片：a.png]", images=[{"name": "a.png", "url": FAKE_URL}])
    asyncio.run(agent.run_until_idle())

    base = agent.session.derive_messages()
    ledger_snapshot = json.dumps(agent.session.log, ensure_ascii=False)
    # 手工再挂一次（模拟同回合的后续步）——回合已结束侧表已清，先用副本重灌
    agent._turn_images = {m["id"]: [FAKE_URL] for m in base if m.get("role") == "user"}
    for _ in range(3):
        got = agent._attach_turn_images(base)
        assert any(b.get("type") == "image" for m in got for b in (m.get("content") or [])
                   if isinstance(b, dict)), "重复挂图应每次都生效"
    # 账本本体一字未动
    assert json.dumps(agent.session.log, ensure_ascii=False) == ledger_snapshot, \
        "_attach_turn_images 改动了账本本体（必须浅拷贝）"
    assert not any(b.get("type") == "image" for m in base for b in (m.get("content") or [])
                   if isinstance(b, dict)), "原消息对象被原地挂上了 image 块"
    # 原 content 列表也没被 append 污染
    for m in base:
        for b in (m.get("content") or []):
            assert isinstance(b, dict) and b.get("type") != "image"
    agent._turn_images.clear()
    print("✓ 挂图是纯操作：可重复、每次生效、账本本体零改动")


# ----------------------------------------------------------------------
# 4：下一回合不再携带
# ----------------------------------------------------------------------

def test_image_not_carried_into_next_turn():
    llm = SpyLlm()
    agent = _new_loop(llm).create(NPC)
    agent.send("第一问\n[图片：a.png]", images=[{"name": "a.png", "url": FAKE_URL}])
    asyncio.run(agent.run_until_idle())
    assert llm.images_seen(-1) == [FAKE_URL]

    agent.send("第二问")            # 无图
    asyncio.run(agent.run_until_idle())
    assert llm.images_seen(-1) == [], \
        "第二回合仍看到了图片字节（寿命应为「本回合」）"
    # 历史里的占位还在，模型知道当时发过图
    assert "a.png" in json.dumps(llm.calls[-1], ensure_ascii=False), "占位文本应仍在上下文中"
    print("✓ 图片寿命 = 当回合：下一回合只剩占位文本")


def test_queued_second_image_survives_first_turn():
    """抢在回合开始前连发两条带图消息：第二条的图不能被第一条的回合收口顺手清掉。

    一次 `_pre_step` 只吃一条 next-turn，所以第二条会等下一回合；若收口用
    `_turn_images.clear()` 全清，第二条就**静默丢图**。这里钉住「只清本回合领到的」。"""
    llm = SpyLlm()
    agent = _new_loop(llm).create(NPC)
    agent.send("第一问\n[图片：a.png]", images=[{"name": "a.png", "url": FAKE_URL}])
    agent.send("第二问\n[图片：b.png]", images=[{"name": "b.png", "url": FAKE_URL_B}])
    asyncio.run(agent.run_until_idle())

    seen = [llm.images_seen(i) for i in range(len(llm.calls))]
    assert [FAKE_URL] in seen, f"第一回合没看到 A 的图：{seen}"
    assert [FAKE_URL_B] in seen, f"第二条消息的图被前一个回合清掉了（静默丢图）：{seen}"
    assert agent._turn_images == {}, "两回合都收口后侧表应清空"
    print(f"✓ 排队带图消息各自成回合、各自带图（{len(llm.calls)} 次调用）")


# ----------------------------------------------------------------------
# 5：image 块形状能被 adapter 认出来（端到端形状对得上）
# ----------------------------------------------------------------------

def test_attached_block_shape_is_adapter_compatible():
    from agent_loop import llm_adapter
    llm = SpyLlm()
    agent = _new_loop(llm).create(NPC)
    agent.send("看图\n[图片：a.png]", images=[{"name": "a.png", "url": FAKE_URL}])
    asyncio.run(agent.run_until_idle())

    oai, _ = llm_adapter.canonical_to_openai("sys", llm.calls[-1], None)
    user = [m for m in oai if m.get("role") == "user"][-1]
    assert isinstance(user["content"], list), "带图消息应转成多-part content"
    urls = [c["image_url"]["url"] for c in user["content"] if c.get("type") == "image_url"]
    assert urls == [FAKE_URL], f"adapter 未把 image 块转成 image_url：{user['content']}"
    print("✓ 挂上的 image 块形状能被 canonical_to_openai 正确转成 image_url")


# ----------------------------------------------------------------------
# 6：纯图（无正文）不被静默丢弃
# ----------------------------------------------------------------------

class _StubWs:
    """ChatHub 只用到 send_event 推流；测试不需要真服务器。"""

    def __init__(self) -> None:
        self.events: List[tuple] = []

    async def send_event(self, event: str, **fields: Any) -> bool:
        self.events.append((event, fields))
        return True


def _hub(loop: AgentLoop) -> Any:
    from agent_loop.ws_channel import ChatHub
    return ChatHub(loop=loop, ws=_StubWs(), max_concurrent_turns=4)


def test_image_only_message_is_not_dropped():
    """纯图（正文为空、只有图）必须进回合 —— 用户明确要求允许「只发图不发字」。"""
    llm = SpyLlm()
    loop = _new_loop(llm)
    hub = _hub(loop)

    asyncio.run(hub.handle_message(NPC, "", images=[{"name": "a.png", "url": FAKE_URL}]))

    assert llm.calls, "纯图消息被静默丢弃了（handle_message 的判据没放行）"
    assert llm.images_seen(-1) == [FAKE_URL], f"模型没看到图：{llm.images_seen(-1)}"
    print("✓ 纯图消息（text 为空）正常进入回合，未被丢弃")


def test_empty_message_still_dropped():
    """反向锁：既没字也没图，仍然必须丢弃（别把判据放宽成「什么都收」）。"""
    llm = SpyLlm()
    loop = _new_loop(llm)
    hub = _hub(loop)

    asyncio.run(hub.handle_message(NPC, "", images=None))
    asyncio.run(hub.handle_message(NPC, "", images=[]))

    assert not llm.calls, "空消息（无字无图）不该触发 LLM 回合"
    assert loop.get(NPC) is None, "空消息不该建活体"
    print("✓ 空消息（无字无图）仍被丢弃，且没建活体")
