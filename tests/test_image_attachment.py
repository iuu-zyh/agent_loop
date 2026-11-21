"""图片附件：运输契约 + 「图片进上下文」不变量（09-13 建立，10-18 重做）

背景：09-13 的实现是「图片只进当回合、绝不进历史」——图像字节存在内存侧表
`_turn_images`，仅在拼请求时浅挂，回合收口清空；账本里只有 C# 拼好的 `[图片：xx]` 占位。
后果是模型下一回合就"失忆"，且图片的 token 从不出现在估算里。

10-18 起（设计见 `docs/archive/2026-10-18-图片进上下文.md`）图片**是会话内容的一部分**：
字节落存档图片仓、账本里只记 `ref`、每轮拼请求时投影成 data URI；删除/压缩之后回收。
本文件钉住运输与投影这一层的不变量（仓/GC/token 的细节在 `test_image_context.py`）：

  1. 模型**看得到**图（当回合就有）；
  2. 账本**看不到字节**（session.log 里只有 ref，没有 base64/data URI）；
  3. 发前投影是**纯操作**：可重复调用、绝不原地改账本本体；
  4. **下一回合仍然携带**（旧契约「寿命 = 当回合」已反转）；
  5. 排队中的第二条带图消息不会被前一个回合顺手清掉；
  6. image 块形状能被 adapter 转成 `image_url`；
  7. 「只发图不发字」照常进回合，「无字无图」仍被丢弃。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from typing import Any, Dict, List

from agent_loop.agent_loop import AgentLoop
from agent_loop.llm.echo_client import EchoLlmClient

NPC = "云含"

# 一眼能认出的假 data URL：真出现「泄漏」时断言消息里能直接看到它
FAKE_URL = "data:image/png;base64,AAECAwQFBgcICQ=="
FAKE_URL_B = "data:image/png;base64,CQkJCQkJCQkJCQ=="
IMG_META = {"w": 320, "h": 200, "kb": 8}


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


def _new_loop(llm) -> AgentLoop:
    """临时存档根 + 一个 loop（fixture-free：`_mutate_lib` 的直调路径认不出 tmp_path）。"""
    root = tempfile.mkdtemp(prefix="agent_loop_imgattach_")
    loop = AgentLoop.reset_for_test(storage_root=root, llm=llm)
    loop._test_root = root  # type: ignore[attr-defined]
    return loop


def _cleanup(loop: AgentLoop) -> None:
    shutil.rmtree(getattr(loop, "_test_root", ""), ignore_errors=True)


# ----------------------------------------------------------------------
# 1 + 2：模型看得到，账本看不到字节
# ----------------------------------------------------------------------

def test_image_reaches_model_but_not_ledger():
    llm = SpyLlm()
    loop = _new_loop(llm)
    try:
        agent = loop.create(NPC)
        agent.send("这张图里的人是谁\n[图片：屏幕截图 2026-09-13 002402.png]",
                   images=[{"name": "屏幕截图 2026-09-13 002402.png", "url": FAKE_URL, **IMG_META}])
        asyncio.run(agent.run_until_idle())

        # ① 模型看得到（投影把 ref 还原成 data URI，逐字节等于原图）
        assert llm.images_seen() == [FAKE_URL], f"当回合请求应含 1 张图，实际={llm.images_seen()}"

        # ② 账本看不到字节：session.log 里不得出现 base64 本体或 data URI
        dump = json.dumps(agent.session.log, ensure_ascii=False)
        assert FAKE_URL not in dump, "图像字节泄漏进了 session.log（图片必须只存引用）"
        assert "data:image/" not in dump, "session.log 里出现了 data URI"
        assert "base64" not in dump, "session.log 里出现了 base64"

        # ③ 账本投影（= 后续回合与 UI 历史的输入）里是**引用**，占位文本也还在
        msgs = agent.session.derive_messages()
        blocks = [b for m in msgs for b in (m.get("content") or [])
                  if isinstance(b, dict) and b.get("type") == "image"]
        assert len(blocks) == 1 and blocks[0].get("ref"), f"derive_messages 里的 image 块应带 ref：{blocks}"
        assert "url" not in blocks[0], "账本里的 image 块不许带 url（那是发给模型的那一份才有的）"
        assert any("[图片：屏幕截图 2026-09-13 002402.png]" in json.dumps(m, ensure_ascii=False)
                   for m in msgs), "占位文本应留在历史里（C# 拼的那一串是唯一来源）"

        # ④ 字节真的落盘了（否则下一回合就解析不出来）
        store = agent.session.image_store
        assert blocks[0]["ref"].rsplit("/", 1)[-1] in os.listdir(store.base), \
            "账本有 ref 但文件没落盘"
        print("✓ 图片：模型可见 / 账本只有 ref / 占位留存 / 字节落盘")
    finally:
        _cleanup(loop)


# ----------------------------------------------------------------------
# 3：投影是纯操作（浅拷贝纪律）
# ----------------------------------------------------------------------

def test_inline_projection_is_pure_and_repeatable():
    """`_inline_image_refs` 每次调用都要重新投影，且**绝不改账本本体**。

    原地改 `derive_messages()` 返回的对象 = 把 base64 永久写进 session.log ——
    旧实现（`_attach_turn_images`）防的就是这件事，新实现必须同样防住。
    """
    llm = SpyLlm()
    loop = _new_loop(llm)
    try:
        agent = loop.create(NPC)
        agent.send("看图\n[图片：a.png]", images=[{"name": "a.png", "url": FAKE_URL, **IMG_META}])
        asyncio.run(agent.run_until_idle())

        base = agent.session.derive_messages()
        ledger_snapshot = json.dumps(agent.session.log, ensure_ascii=False)
        for _ in range(3):
            got = agent._inline_image_refs(base)
            urls = [b.get("url") for m in got for b in (m.get("content") or [])
                    if isinstance(b, dict) and b.get("type") == "image"]
            assert urls == [FAKE_URL], f"重复投影应每次都生效，实际 {urls}"
        # 账本本体一字未动
        assert json.dumps(agent.session.log, ensure_ascii=False) == ledger_snapshot, \
            "_inline_image_refs 改动了账本本体（必须浅拷贝）"
        # 原消息对象仍是 ref 形态、没有被挂上 url
        for m in base:
            for b in (m.get("content") or []):
                if isinstance(b, dict) and b.get("type") == "image":
                    assert b.get("ref") and "url" not in b, f"原对象被原地投影了：{b}"
        print("✓ 投影是纯操作：可重复、每次生效、账本本体零改动")
    finally:
        _cleanup(loop)


# ----------------------------------------------------------------------
# 4：下一回合仍然携带（10-18 契约反转）
# ----------------------------------------------------------------------

def test_image_is_carried_into_next_turn():
    llm = SpyLlm()
    loop = _new_loop(llm)
    try:
        agent = loop.create(NPC)
        agent.send("第一问\n[图片：a.png]", images=[{"name": "a.png", "url": FAKE_URL, **IMG_META}])
        asyncio.run(agent.run_until_idle())
        assert llm.images_seen(-1) == [FAKE_URL]

        agent.send("第二问：刚才那张图里是谁？")     # 无图
        asyncio.run(agent.run_until_idle())
        assert llm.images_seen(-1) == [FAKE_URL], \
            "第二回合仍应带着历史里的那张图（图片进上下文）"
        # 占位文本也还在（模型两边都能对上文件名）
        assert "a.png" in json.dumps(llm.calls[-1], ensure_ascii=False), "占位文本应仍在上下文中"
        print("✓ 图片寿命 = 上下文：下一回合仍携带")
    finally:
        _cleanup(loop)


def test_image_survives_restart():
    """重启（新进程等价物：新 loop + 空缓存）之后，历史里的图仍然解析得出来。"""
    llm = SpyLlm()
    loop = _new_loop(llm)
    root = loop._test_root  # type: ignore[attr-defined]
    try:
        agent = loop.create(NPC)
        agent.send("看图\n[图片：a.png]", images=[{"name": "a.png", "url": FAKE_URL, **IMG_META}])
        asyncio.run(agent.run_until_idle())
        loop.flush_all()
        agent.dispose()

        llm2 = SpyLlm()
        loop2 = AgentLoop.reset_for_test(storage_root=root, llm=llm2)
        agent2 = loop2.create(NPC)
        agent2.send("还记得那张图吗")
        asyncio.run(agent2.run_until_idle())
        assert llm2.images_seen(-1) == [FAKE_URL], "重启后历史里的图应仍能投影出来"
        print("✓ 重启后图片仍可投影（靠存档里的文件 + 账本里的 ref）")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ----------------------------------------------------------------------
# 5：排队带图消息各自成回合、各自带图
# ----------------------------------------------------------------------

def test_queued_second_image_survives_first_turn():
    """抢在回合开始前连发两条带图消息：第二条的图必须留到它自己的回合。

    （10-18 之前靠 `_turn_images` 的"只清本回合领到的"保证；现在图在账本里，
    天然不会丢 —— 这条断言留作回归。）
    """
    llm = SpyLlm()
    loop = _new_loop(llm)
    try:
        agent = loop.create(NPC)
        agent.send("第一问\n[图片：a.png]", images=[{"name": "a.png", "url": FAKE_URL, **IMG_META}])
        agent.send("第二问\n[图片：b.png]", images=[{"name": "b.png", "url": FAKE_URL_B, **IMG_META}])
        asyncio.run(agent.run_until_idle())

        seen = [llm.images_seen(i) for i in range(len(llm.calls))]
        assert [FAKE_URL] in seen, f"第一回合没看到 A 的图：{seen}"
        assert [FAKE_URL_B] in seen or [FAKE_URL, FAKE_URL_B] in seen, \
            f"第二条消息的图丢了：{seen}"
        print(f"✓ 排队带图消息各自成回合、各自带图（{len(llm.calls)} 次调用）")
    finally:
        _cleanup(loop)


# ----------------------------------------------------------------------
# 6：image 块形状能被 adapter 认出来（端到端形状对得上）
# ----------------------------------------------------------------------

def test_projected_block_shape_is_adapter_compatible():
    from agent_loop.llm import llm_adapter
    llm = SpyLlm()
    loop = _new_loop(llm)
    try:
        agent = loop.create(NPC)
        agent.send("看图\n[图片：a.png]", images=[{"name": "a.png", "url": FAKE_URL, **IMG_META}])
        asyncio.run(agent.run_until_idle())

        oai, _ = llm_adapter.canonical_to_openai("sys", llm.calls[-1], None)
        user = [m for m in oai if m.get("role") == "user"][-1]
        assert isinstance(user["content"], list), "带图消息应转成多-part content"
        urls = [c["image_url"]["url"] for c in user["content"] if c.get("type") == "image_url"]
        assert urls == [FAKE_URL], f"adapter 未把 image 块转成 image_url：{user['content']}"
        print("✓ 投影出的 image 块形状能被 canonical_to_openai 正确转成 image_url")
    finally:
        _cleanup(loop)


# ----------------------------------------------------------------------
# 7：纯图（无正文）不被静默丢弃 / 空消息仍丢弃
# ----------------------------------------------------------------------

class _StubWs:
    """ChatHub 只用到 send_event 推流；测试不需要真服务器。"""

    def __init__(self) -> None:
        self.events: List[tuple] = []

    async def send_event(self, event: str, **fields: Any) -> bool:
        self.events.append((event, fields))
        return True


def _hub(loop: AgentLoop) -> Any:
    from agent_loop.transport.ws_channel import ChatHub
    return ChatHub(loop=loop, ws=_StubWs(), max_concurrent_turns=4)


def test_image_only_message_is_not_dropped():
    """纯图（正文为空、只有图）必须进回合 —— 用户明确要求允许「只发图不发字」。"""
    llm = SpyLlm()
    loop = _new_loop(llm)
    try:
        hub = _hub(loop)

        asyncio.run(hub.handle_message(NPC, "", images=[{"name": "a.png", "url": FAKE_URL, **IMG_META}]))

        assert llm.calls, "纯图消息被静默丢弃了（handle_message 的判据没放行）"
        assert llm.images_seen(-1) == [FAKE_URL], f"模型没看到图：{llm.images_seen(-1)}"
        print("✓ 纯图消息（text 为空）正常进入回合，未被丢弃")
    finally:
        _cleanup(loop)


def test_empty_message_still_dropped():
    """反向锁：既没字也没图，仍然必须丢弃（别把判据放宽成「什么都收」）。"""
    llm = SpyLlm()
    loop = _new_loop(llm)
    try:
        hub = _hub(loop)

        asyncio.run(hub.handle_message(NPC, "", images=None))
        asyncio.run(hub.handle_message(NPC, "", images=[]))

        assert not llm.calls, "空消息（无字无图）不该触发 LLM 回合"
        assert loop.get(NPC) is None, "空消息不该建活体"
        print("✓ 空消息（无字无图）仍被丢弃，且没建活体")
    finally:
        _cleanup(loop)


# ----------------------------------------------------------------------
# 8：text=None + 有图（判据放宽之后的可达路径）
# ----------------------------------------------------------------------

def test_none_text_with_image_still_works():
    """`handle_message(npc, None, images=[...])` 必须正常进回合。

    判据是 `not text and not images`，所以 `text=None` + 有图会**通过**判据；此后任何
    `len(text)` 都会炸（被兜底 except 接住 ⇒ 玩家收到"回合失败"、图也白发）。
    WS 收帧永远是 str，但本方法是公开入口（脚本/新调用点会传 None）。
    """
    llm = SpyLlm()
    loop = _new_loop(llm)
    try:
        hub = _hub(loop)
        asyncio.run(hub.handle_message(NPC, None, images=[{"name": "a.png", "url": FAKE_URL, **IMG_META}]))
        assert llm.calls, "text=None + 有图应正常进回合（不该崩在 len(text)）"
        assert llm.images_seen(-1) == [FAKE_URL], f"模型没看到图：{llm.images_seen(-1)}"
        print("✓ text=None + 有图：正常进回合")
    finally:
        _cleanup(loop)
