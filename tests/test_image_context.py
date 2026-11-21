"""图片进上下文：落盘 / 引用 / token 估算 / 删除与压缩的回收（10-18）

背景：09-13 的实现是「图片只进当回合、绝不进历史」（内存侧表 `_turn_images`），
结果是玩家发过的图在下一回合就只剩 `[图片：xx]` 占位文本，模型失忆；图片的 token
也只在那一轮实测 `usage` 里出现过，估算/压缩阈值完全看不见它。

10-18 重做（设计见 `docs/archive/2026-10-18-图片进上下文.md`）：
  · 图像字节落 `<sessions_root>/images/<npc>/<sha1>.<ext>`；
  · `session.log` 的 user 消息 content 里只记 `{"type":"image","ref":..,"tokens":..}`；
  · 每轮拼请求时 `ref` 投影成 data URI（`DialogueAgent._inline_image_refs`）；
  · 删除/压缩把消息从 surface 剔掉之后，`storage.images.gc_session` 回收文件。

本文件钉住「静默出错」型的不变量（每条都能被一次无意的重构悄悄打破）：
  1. 落盘 + 去重 + ref 形状校验（不许 `../` 穿越）；
  2. token 估算在**收图时**记进账本块，`meter` 用的是同一个数；
  3. 删回合 / 群窗删消息 / 压缩 之后**文件真的没了**，但账本仍只追加；
  4. **待投递筐**里的图不会被误回收（压缩发生在回合边界，那条消息还没落面）；
  5. 文件缺失 / 模型不支持图片 / 老账本 —— 三条降级路径都**不崩**。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from typing import Any, Dict, List

from agent_loop.agent_loop import AgentLoop
from agent_loop.compaction import meter
from agent_loop.compaction.compress import Compressor
from agent_loop.llm.echo_client import EchoLlmClient
from agent_loop.session import history_ops
from agent_loop.storage.images import ImageStore, gc_session, parse_data_uri

NPC = "云含"
FAKE_URL = "data:image/png;base64,AAECAwQFBgcICQ=="
FAKE_URL_B = "data:image/png;base64,CQkJCQkJCQkJCQ=="
# 一张 1024x576 的"假图"元信息：token 估算只认 w/h，不需要真像素
IMG_META = {"w": 1024, "h": 576, "kb": 120}


class SpyLlm:
    """记录每次 generate 收到的 messages（深拷贝，防事后被改）。"""

    def __init__(self, supports_image: Any = None) -> None:
        self.calls: List[List[Dict[str, Any]]] = []
        self._echo = EchoLlmClient()
        if supports_image is not None:
            self.supports_image = supports_image

    async def generate(self, system, messages, tools, on_token=None, on_reasoning=None):
        self.calls.append(json.loads(json.dumps(messages, ensure_ascii=False, default=str)))
        return await self._echo.generate(system, messages, tools, on_token, on_reasoning)

    def images(self, call_index: int = -1) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for m in self.calls[call_index]:
            for b in (m.get("content") or []):
                if isinstance(b, dict) and b.get("type") == "image":
                    out.append(b)
        return out


def _rig(llm=None, compactor=None):
    """临时存档根 + 一个 loop（fixture-free：`_mutate_lib` 的直调路径认不出 tmp_path）。"""
    root = tempfile.mkdtemp(prefix="agent_loop_imgctx_")
    loop = AgentLoop.reset_for_test(storage_root=root, llm=llm, compactor=compactor)
    return root, loop


def _cleanup(root: str) -> None:
    shutil.rmtree(root, ignore_errors=True)


def _image_inputs(url: str, name: str = "a.png") -> List[Dict[str, Any]]:
    return [{"name": name, "url": url, **IMG_META}]


def _files(store: ImageStore) -> List[str]:
    try:
        return sorted(os.listdir(store.base))
    except OSError:
        return []


# ══════════════════════════════════════════════════════════════════════
# 1. 落盘 + 去重 + ref 形状
# ══════════════════════════════════════════════════════════════════════

def test_store_put_dedupes_by_content_and_validates_ref():
    root, loop = _rig()
    try:
        agent = loop.create(NPC)
        store = agent.session.image_store
        assert store is not None and store.base.startswith(root), "图片仓没挂到存档根下"

        _mime, data = parse_data_uri(FAKE_URL)
        ref1 = store.put(data, "image/png")
        ref2 = store.put(data, "image/png")
        assert ref1 == ref2, "同内容应得到同一个 ref（去重免费）"
        assert _files(store) == [ref1.rsplit("/", 1)[-1]], f"应只有一份文件：{_files(store)}"
        assert ref1.startswith("images/") and ref1.endswith(".png"), ref1

        # ref 形状是回收与读取的唯一凭据：`../` 之类必须拒绝。
        # ★ 造一个**真实存在的**越界目标再试（否则"被拒绝"可能只是因为文件不存在 ——
        #   那样把校验整个删掉测试也照样绿，变异验证会抓不到）。
        sentinel = os.path.join(root, "sentinel.txt")
        with open(sentinel, "w", encoding="utf-8") as f:
            f.write("secret")
        assert store.data_uri("images/../sentinel.txt") is None, "越界 ref 必须拒绝（哪怕目标真实存在）"
        assert store.data_uri("images/云含/notahash.png") is None, "非十六进制名必须拒绝"
        assert store.data_uri("images/云含/" + "a" * 40 + ".png") is None, "不存在的文件应返回 None"
        print("✓ 落盘/去重/ref 校验")
    finally:
        _cleanup(root)


# ══════════════════════════════════════════════════════════════════════
# 2. token 估算：收图时记进块里，meter 用同一个数
# ══════════════════════════════════════════════════════════════════════

def test_image_tokens_recorded_and_counted_by_meter():
    root, loop = _rig(llm=SpyLlm())
    try:
        agent = loop.create(NPC)
        agent.send("看图\n[图片：a.png]", images=_image_inputs(FAKE_URL))
        asyncio.run(agent.run_until_idle())

        blocks = [b for m in agent.session.derive_messages()
                  for b in (m.get("content") or [])
                  if isinstance(b, dict) and b.get("type") == "image"]
        assert len(blocks) == 1, f"账本里应有 1 个 image 块：{blocks}"
        blk = blocks[0]
        expect = meter.estimate_image_tokens(w=IMG_META["w"], h=IMG_META["h"])
        assert blk.get("tokens") == expect, f"块里的 tokens 应为 {expect}，实际 {blk.get('tokens')}"
        assert blk.get("w") == IMG_META["w"] and blk.get("h") == IMG_META["h"]

        # meter 认图：同一条消息，带图比不带图多出 ~一张图的量
        # （取**带图的那条 user 消息**，别拿 derive_messages()[-1] —— 那是 NPC 的回复）
        target_msg = next(m for m in agent.session.derive_messages()
                          if any(isinstance(b, dict) and b.get("type") == "image"
                                 for b in (m.get("content") or [])))
        with_image = meter.estimate_message(target_msg)
        no_image = meter.estimate_message({"role": "user", "content": [
            {"type": "text", "text": "看图\n[图片：a.png]"}]})
        delta = with_image - no_image
        assert abs(delta - (expect + meter.BLOCK_OVERHEAD)) <= 1, \
            f"估算差额应等于图片 tokens+块开销，实际 {delta}（expect {expect}）"

        # data URI 块（没有 tokens 字段的老形态）不许被估成几万 token
        huge = meter.estimate_blocks([{"type": "image",
                                       "url": "data:image/png;base64," + "A" * 1_400_000}])
        assert huge < 5000, f"data URI 块被估成 {huge} token（旧实现 dump 整块的地雷）"
        print(f"✓ token 估算：块内 {expect}，meter 计入，data URI 块 {huge}")
    finally:
        _cleanup(root)


# ══════════════════════════════════════════════════════════════════════
# 3. 删回合 → 文件回收
# ══════════════════════════════════════════════════════════════════════

def test_delete_turn_reclaims_image_file():
    root, loop = _rig(llm=SpyLlm())
    try:
        agent = loop.create(NPC)
        agent.send("这人是谁\n[图片：a.png]", images=_image_inputs(FAKE_URL))
        asyncio.run(agent.run_until_idle())
        store = agent.session.image_store
        assert len(_files(store)) == 1, "收图后应有 1 份文件"

        plan = history_ops.plan_delete(agent.session, [1])
        assert plan["ok"], f"删除方案不合法：{plan.get('reason')}"
        history_ops.append_delete(agent.session, plan)

        assert _files(store) == [], f"删回合之后图片文件应被回收：{_files(store)}"
        # 账本仍是只追加（不是把事件改掉了）
        assert any(ev.get("type") == "history/delete" for ev in agent.session.log)
        # 模型上下文里也不再有图
        agent.send("还看得见吗")
        asyncio.run(agent.run_until_idle())
        assert agent  # 仅表达"没抛"
        print("✓ 删回合 → 图片文件回收")
    finally:
        _cleanup(root)


# ══════════════════════════════════════════════════════════════════════
# 4. 群窗删消息（消息级删除）→ 文件回收
# ══════════════════════════════════════════════════════════════════════

def test_message_delete_reclaims_image_file():
    root, loop = _rig(llm=SpyLlm())
    try:
        agent = loop.create(NPC)
        agent.send("一\n[图片：a.png]", images=_image_inputs(FAKE_URL))
        asyncio.run(agent.run_until_idle())
        agent.send("二\n[图片：b.png]", images=_image_inputs(FAKE_URL_B, "b.png"))
        asyncio.run(agent.run_until_idle())
        store = agent.session.image_store
        assert len(_files(store)) == 2, f"两张不同图应有 2 份文件：{_files(store)}"

        # 找到第一条带图消息的节点 seq（群窗删除走的就是"节点集合"这条）
        target = None
        for seq in agent.session.surface.nodes:
            msg = agent.session.log[seq].get("data") or {}
            if any(isinstance(b, dict) and b.get("type") == "image"
                   for b in (msg.get("content") or [])):
                target = seq
                break
        assert target is not None, "surface 里找不到带图消息"

        plan = history_ops.plan_message_delete(agent.session, [target])
        assert plan["ok"], f"消息级删除方案不合法：{plan.get('reason')}"
        history_ops.append_delete(agent.session, plan, reason="room_delete")

        left = _files(store)
        assert len(left) == 1, f"只该回收被删那条消息的图，剩 {left}"
        print("✓ 群窗删消息 → 只回收对应那张图")
    finally:
        _cleanup(root)


# ══════════════════════════════════════════════════════════════════════
# 5. 压缩 → 文件回收
# ══════════════════════════════════════════════════════════════════════

def test_compaction_reclaims_image_file():
    llm = SpyLlm()
    comp = Compressor(llm=llm, ctx_window=200_000, retain_ratio=0.16)
    root, loop = _rig(llm=llm, compactor=comp)
    try:
        agent = loop.create(NPC)
        agent.send("这人是谁\n[图片：a.png]", images=_image_inputs(FAKE_URL))
        asyncio.run(agent.run_until_idle())
        # 多聊几轮：`_find_cut` 的保留尾预算是 total × retain_ratio，太短的会话会被
        # "保留尾吃掉全部"而 fail-closed 不压（那是**对的**，不是本测试要验的东西）
        for i in range(8):
            agent.send(f"第 {i + 2} 轮")
            asyncio.run(agent.run_until_idle())
        store = agent.session.image_store
        assert len(_files(store)) == 1, "压缩前应还有这张图"

        report = asyncio.run(agent.compact_now())
        assert report, "压缩没执行（compact_now 返回空）"

        assert _files(store) == [], f"被折进纪要的图应被回收：{_files(store)}"
        # 纪要仍在（压缩不是"删光"）
        assert any((ev.get("data") or {}).get("shadowedRange") for ev in agent.session.log
                   if ev.get("type") == "compaction/summary"), "没看到 compression/summary 事件"
        print("✓ 压缩折走 → 图片文件回收，纪要保留")
    finally:
        _cleanup(root)


# ══════════════════════════════════════════════════════════════════════
# 6. 待投递筐的图不能被误回收
# ══════════════════════════════════════════════════════════════════════

def test_pending_inbox_image_is_not_garbage_collected():
    """压缩/删除发生在"玩家刚发、还没落面"的窗口期时，那张图的 ref 只在筐里。

    只看 surface 会把它删掉 ⇒ 这条消息静默丢图（玩家发出去的图看不见了）。
    """
    root, loop = _rig(llm=SpyLlm())
    try:
        agent = loop.create(NPC)
        agent.send("排队中\n[图片：a.png]", images=_image_inputs(FAKE_URL), wake=False)
        store = agent.session.image_store
        assert len(_files(store)) == 1, "收图（进筐）时就应该落盘"

        removed = gc_session(agent.session, why="test")
        assert removed == [], f"待投递消息的图被误回收了：{removed}"
        assert len(_files(store)) == 1, "文件不该被删"

        asyncio.run(agent.run_until_idle())
        print("✓ 待投递筐里的图不被 GC 误删")
    finally:
        _cleanup(root)


# ══════════════════════════════════════════════════════════════════════
# 7. 降级：文件缺失 / 模型不支持图 / 老账本
# ══════════════════════════════════════════════════════════════════════

def test_missing_file_degrades_to_placeholder():
    llm = SpyLlm()
    root, loop = _rig(llm=llm)
    try:
        agent = loop.create(NPC)
        agent.send("看图\n[图片：a.png]", images=_image_inputs(FAKE_URL))
        asyncio.run(agent.run_until_idle())
        assert len(llm.images(-1)) == 1, "正常路径应看到图"
        loop.flush_all()
        agent.dispose()

        # 新进程等价物：缓存空 + 文件被删
        base = agent.session.image_store.base
        for name in os.listdir(base):
            os.unlink(os.path.join(base, name))

        llm2 = SpyLlm()
        loop2 = AgentLoop.reset_for_test(storage_root=root, llm=llm2)
        agent2 = loop2.create(NPC)
        assert agent2.session.log, "重启后账本应能读回"
        agent2.send("还看得见吗")
        asyncio.run(agent2.run_until_idle())

        assert llm2.images(-1) == [], "文件缺失时应丢图"
        assert "a.png" in json.dumps(llm2.calls[-1], ensure_ascii=False), \
            "占位文本应仍在上下文里（模型好歹知道当时发过图）"
        assert '"ref"' in json.dumps(agent2.session.log, ensure_ascii=False), \
            "账本不该因为文件缺失而被改写"
        print("✓ 文件缺失 → 丢图 + 占位留存，不崩")
    finally:
        _cleanup(root)


def test_text_only_model_drops_images_without_error():
    """图片进历史之后，`supports_image=False` 绝不能变成"每一回合都报错"。"""
    llm = SpyLlm(supports_image=False)
    root, loop = _rig(llm=llm)
    try:
        agent = loop.create(NPC)
        agent.send("看图\n[图片：a.png]", images=_image_inputs(FAKE_URL))
        asyncio.run(agent.run_until_idle())
        assert len(llm.calls) == 1, "回合应正常跑完"
        assert llm.images(-1) == [], "纯文本模型不该收到图片块"
        # 图仍然落盘（账本是真相，与模型能力无关）
        assert len(_files(agent.session.image_store)) == 1, "落盘与模型能力无关"
        print("✓ 纯文本模型：丢图不报错，账本照记")
    finally:
        _cleanup(root)


def test_legacy_ledger_without_image_blocks_still_works():
    """老账本（09-13~10-18 之间写的）：只有 `[图片：xx]` 占位文本，没有 image 块。"""
    llm = SpyLlm()
    root, loop = _rig(llm=llm)
    try:
        from agent_loop.session.session import Session
        from agent_loop.storage.persistence import save_session

        sess = Session(id=NPC, header={"id": NPC})
        sess.append("user/message", {"role": "user", "id": "m1", "content": [
            {"type": "text", "text": "[1年1月] 这人是谁\n[图片：a.png]"}]}, {"surfaceOp": "append"})
        sess.append("assistant/message", {"message": {"role": "assistant", "id": "m2", "content": [
            {"type": "text", "text": "看不出来。"}]}}, {"surfaceOp": "append"})
        save_session(sess, root)

        agent = loop.create(NPC)
        agent.send("还记得那张图吗")
        asyncio.run(agent.run_until_idle())
        assert llm.images(-1) == [], "老账本里没有 image 块，就不该凭空有图"
        assert "a.png" in json.dumps(llm.calls[-1], ensure_ascii=False), "占位文本应照常进上下文"
        print("✓ 老账本（只有占位文本）零迁移")
    finally:
        _cleanup(root)


# ══════════════════════════════════════════════════════════════════════
# 8. 账本只记 ref：字节绝不进 session.log
# ══════════════════════════════════════════════════════════════════════

def test_ledger_holds_ref_only_never_bytes():
    root, loop = _rig(llm=SpyLlm())
    try:
        agent = loop.create(NPC)
        agent.send("看图\n[图片：a.png]", images=_image_inputs(FAKE_URL))
        asyncio.run(agent.run_until_idle())
        dump = json.dumps(agent.session.log, ensure_ascii=False)
        assert FAKE_URL not in dump, "图像字节泄漏进了 session.log"
        assert "data:image/" not in dump, "session.log 里出现了 data URI"
        assert '"ref"' in dump, "账本里应记 ref"
        # 落盘文件（JSONL）同样干净
        from agent_loop.storage.persistence import session_path_for
        loop.flush_all()
        path = str(session_path_for(NPC, root))
        assert os.path.isfile(path), f"flush_all 应写出 JSONL：{path}"
        with open(path, encoding="utf-8") as f:
            text = f.read()
        assert "data:image/" not in text and FAKE_URL not in text, "JSONL 里出现了图片字节"
        print("✓ 账本/JSONL 只有 ref，没有字节")
    finally:
        _cleanup(root)


# ══════════════════════════════════════════════════════════════════════
# 9. 压缩材料：图片只出现一次（正文占位是唯一来源）
# ══════════════════════════════════════════════════════════════════════

def test_transcript_material_does_not_duplicate_image_marker():
    """压缩材料里那次"发过图"只该出现一次。

    玩家消息的正文里 C# 已经拼好 `[图片：文件名]`（唯一来源），材料再补一行 `[图片]`
    会让摘要以为这条消息附了**两张**图（纪要是永久节点，错了会一直被喂回来）。
    没有占位的消息（程序化调用 / 工具结果带的图）才补 `[图片]`。
    """
    from agent_loop.compaction.compress import Compressor
    from agent_loop.session.session import Session

    comp = Compressor(llm=None)
    sess = Session(id=NPC, header={"id": NPC})
    sess.append("user/message", {"role": "user", "id": "m1", "content": [
        {"type": "text", "text": "[1年1月] 这人是谁\n[图片：a.png]"},
        {"type": "image", "ref": "images/x/abc.png", "tokens": 787},
    ]}, {"surfaceOp": "append"})
    text = comp._render_transcript(sess, {"npc_name": NPC, "player_name": "玩家"})
    assert "[图片：a.png]" in text, f"占位文本应进材料：{text!r}"
    assert text.count("[图片") == 1, f"材料里图片标记重复了：{text!r}"

    sess2 = Session(id=NPC, header={"id": NPC})
    sess2.append("user/message", {"role": "user", "id": "m1", "content": [
        {"type": "text", "text": "看看"},
        {"type": "image", "ref": "images/x/abc.png", "tokens": 787},
    ]}, {"surfaceOp": "append"})
    text2 = comp._render_transcript(sess2, {"npc_name": NPC, "player_name": "玩家"})
    assert "[图片]" in text2, f"没有占位时应补一行 [图片]：{text2!r}"
    print("✓ 压缩材料：图片标记不重复")


# ══════════════════════════════════════════════════════════════════════
# 10. 回收的 fail-closed：keep-set 拼不出来 ⇒ **放弃回收**（不是"当它没引用"）
#     —— 10-18 独立复核指出：这条阻塞级防线当时**没有任何测试**（注释写了"绝不返回空集"，
#        但改坏了没有一条会红）。下面两条分别覆盖 surface 与待投递筐两条失败路径。
# ══════════════════════════════════════════════════════════════════════

def test_gc_aborts_when_surface_is_corrupt():
    root, loop = _rig(llm=SpyLlm())
    try:
        agent = loop.create(NPC)
        agent.send("看图\n[图片：a.png]", images=_image_inputs(FAKE_URL))
        asyncio.run(agent.run_until_idle())
        store = agent.session.image_store
        assert len(_files(store)) == 1

        # 账本读残的等价物：surface 里出现越界 seq（`log_events[seq]` 会 IndexError）
        good = list(agent.session.surface.nodes)
        agent.session.surface.nodes = good + [len(agent.session.log) + 50]
        try:
            assert gc_session(agent.session, why="corrupt") == [], "拼不出 keep-set 时必须放弃回收"
            assert len(_files(store)) == 1, "文件被误删了（宁可留孤儿，不许误删在用的图）"
        finally:
            agent.session.surface.nodes = good
        print("✓ surface 读残 ⇒ 放弃回收")
    finally:
        _cleanup(root)


def test_gc_aborts_when_inbox_replay_fails():
    """待投递筐重放失败时**绝不能返回空集**（空集 = 把筐里的图全删）。

    构造：筐里有一条带图消息（只被 `agent/inbox/spliced` 引用、还没落 surface），
    再塞一条坏掉的 spliced 事件让重放抛错 —— 此时正确的行为是"放弃回收"。
    """
    root, loop = _rig(llm=SpyLlm())
    try:
        agent = loop.create(NPC)
        agent.send("排队中\n[图片：a.png]", images=_image_inputs(FAKE_URL), wake=False)
        store = agent.session.image_store
        assert len(_files(store)) == 1, "收图（进筐）时就该落盘"

        before = len(agent.session.log)
        agent.session.log.append({"seq": before, "type": "agent/inbox/spliced", "data": None})
        try:
            assert gc_session(agent.session, why="corrupt") == [], "筐重放失败时必须放弃回收"
            assert len(_files(store)) == 1, "筐里的图被误删了（静默丢图）"
        finally:
            del agent.session.log[before:]
        print("✓ 筐重放失败 ⇒ 放弃回收")
    finally:
        _cleanup(root)


# ══════════════════════════════════════════════════════════════════════
# 11. 一条消息多张图（C# 侧上限 3 张）—— 复核指出：只投影/只收第一张的变异当时抓不到
# ══════════════════════════════════════════════════════════════════════

URL_C = "data:image/png;base64,AwMDAwMDAwMDAw=="


def test_multiple_images_in_one_message():
    llm = SpyLlm()
    root, loop = _rig(llm=llm)
    try:
        agent = loop.create(NPC)
        agent.send("三张图\n[图片：a][图片：b][图片：c]", images=[
            {"name": "a.png", "url": FAKE_URL, **IMG_META},
            {"name": "b.png", "url": FAKE_URL_B, **IMG_META},
            {"name": "c.png", "url": URL_C, **IMG_META},
        ])
        asyncio.run(agent.run_until_idle())

        seen = [b.get("url") for b in llm.images(-1)]
        assert seen == [FAKE_URL, FAKE_URL_B, URL_C], f"三张图都要发给模型，实际 {seen}"
        blocks = [b for m in agent.session.derive_messages()
                  for b in (m.get("content") or [])
                  if isinstance(b, dict) and b.get("type") == "image"]
        assert len(blocks) == 3, f"账本里应有 3 个 image 块，实际 {len(blocks)}"
        assert all(b.get("ref") and b.get("tokens") for b in blocks)
        assert len(_files(agent.session.image_store)) == 3, _files(agent.session.image_store)
        print("✓ 一条消息 3 张图：全部落盘、全部进账本、全部发给模型")
    finally:
        _cleanup(root)


# ══════════════════════════════════════════════════════════════════════
# 12. 图片仓目录名的长度与隔离（复核抓到的真 bug：长中文名 NPC 的图全废）
# ══════════════════════════════════════════════════════════════════════

def test_image_store_scope_is_bounded_and_isolated():
    llm = SpyLlm()
    root, loop = _rig(llm=llm)
    try:
        # 8 个汉字 → 百分号编码 72 字符，旧的"直接 quote"实现会撑破 _REF_RE(64) ⇒ 每张图都落不了盘
        long_name = "八个字的中文名字"
        a = loop.create(long_name)
        a.send("看图\n[图片：a.png]", images=_image_inputs(FAKE_URL))
        asyncio.run(a.run_until_idle())
        store_a = a.session.image_store
        assert len(store_a.scope) <= 64 and store_a.scope not in (".", ".."), store_a.scope
        assert len(_files(store_a)) == 1, f"长名字 NPC 的图也必须落盘：{store_a.scope}"
        assert [b.get("url") for b in llm.images(-1)] == [FAKE_URL], "长名字 NPC 的图也必须发给模型"

        # 仓按 NPC 隔离：共用目录 = 一方删历史会删掉另一方还在引用的图
        b = loop.create("司马长风")
        b.send("看图\n[图片：a.png]", images=_image_inputs(FAKE_URL))
        asyncio.run(b.run_until_idle())
        store_b = b.session.image_store
        assert store_a.base != store_b.base, "两个 NPC 共用图片仓（会互相删图）"

        plan = history_ops.plan_delete(a.session, [1])
        assert plan["ok"], plan.get("reason")
        history_ops.append_delete(a.session, plan)
        assert _files(store_a) == [], "A 删历史后 A 的图应被回收"
        assert len(_files(store_b)) == 1, "B 的图被连坐删掉了（仓没隔离）"
        print("✓ 图片仓目录名定长安全、按 NPC 隔离")
    finally:
        _cleanup(root)


# ══════════════════════════════════════════════════════════════════════
# 13. token 兜底优先级（复核指出：没有 tokens 字段 / 没有宽高的图当时测不到）
# ══════════════════════════════════════════════════════════════════════

def test_image_token_fallbacks():
    import base64 as _b64
    assert meter.estimate_image_tokens(w=320, h=200) == 255, "有宽高按像素/切片算"
    assert meter.estimate_image_tokens(kb=200) == 200, "没有宽高时按体积算（不是恒 85）"
    big_uri = "data:image/png;base64," + "A" * 140_000
    assert meter.estimate_image_tokens(url=big_uri) > 90, "只有 data URI 时按 base64 体积反推"
    assert meter.estimate_image_tokens(tokens=5000, w=1, h=1) == 5000, "记录值优先于宽高"

    root, loop = _rig(llm=SpyLlm())
    try:
        agent = loop.create(NPC)
        payload = "data:image/png;base64," + _b64.b64encode(b"x" * 100_000).decode()
        agent.send("看图\n[图片：big.png]", images=[{"name": "big.png", "url": payload}])
        asyncio.run(agent.run_until_idle())
        blk = [b for m in agent.session.derive_messages() for b in (m.get("content") or [])
               if isinstance(b, dict) and b.get("type") == "image"][0]
        assert blk["tokens"] >= 90, f"没有宽高时应按体积估，实际 {blk['tokens']}"
        print(f"✓ token 兜底：宽高 / 体积 / data URI / 记录值（本例 {blk['tokens']}）")
    finally:
        _cleanup(root)


# ══════════════════════════════════════════════════════════════════════
# 14. 显式声明 supports_image=True 的模型必须收到图（复核：当时没有任何正向用例）
# ══════════════════════════════════════════════════════════════════════

def test_explicitly_supported_model_still_receives_images():
    llm = SpyLlm(supports_image=True)
    root, loop = _rig(llm=llm)
    try:
        agent = loop.create(NPC)
        agent.send("看图\n[图片：a.png]", images=_image_inputs(FAKE_URL))
        asyncio.run(agent.run_until_idle())
        assert [b.get("url") for b in llm.images(-1)] == [FAKE_URL], "显式支持图片的模型不该被丢图"
        print("✓ supports_image=True：照发")
    finally:
        _cleanup(root)


# ══════════════════════════════════════════════════════════════════════
# 15. 关窗（cancel）时筐里的孤儿图回收
# ══════════════════════════════════════════════════════════════════════

def test_cancel_reclaims_images_of_dropped_inbox():
    """`inbox.clear()` 丢掉的带图消息，其图片从此无人引用 ⇒ 取消时应顺手回收。

    否则回收点只有"删除/压缩"，玩家不删不压就永久滞留（复核指出的缓慢泄漏）。
    """
    root, loop = _rig(llm=SpyLlm())
    try:
        agent = loop.create(NPC)
        agent.send("排队中\n[图片：a.png]", images=_image_inputs(FAKE_URL), wake=False)
        store = agent.session.image_store
        assert len(_files(store)) == 1
        agent.dispose()                       # → cancel() → 筐清空
        assert _files(store) == [], "筐被丢掉之后那张图不再需要，应被回收"
        print("✓ 关窗/取消 ⇒ 筐里的孤儿图被回收")
    finally:
        _cleanup(root)


# ══════════════════════════════════════════════════════════════════════
# 16. tool-result 里内嵌的图片块绝不 dump 进压缩材料（形状防御）
# ══════════════════════════════════════════════════════════════════════

def test_tool_result_image_is_not_dumped_into_material():
    from agent_loop.compaction.compress import Compressor
    from agent_loop.session.session import Session

    sess = Session(id=NPC, header={"id": NPC})
    sess.append("tool/result", {"message": {"role": "tool", "id": "t1", "content": [
        {"type": "tool-result", "content": [
            {"type": "text", "text": "找到 3 处"},
            {"type": "image", "ref": "images/x/abc.png", "tokens": 787},
        ]},
    ]}}, {"surfaceOp": "append"})
    text = Compressor(llm=None)._render_transcript(sess, {"npc_name": NPC, "player_name": "玩家"})
    assert "找到 3 处" in text, f"工具返回的正文应保留：{text!r}"
    assert "[图片]" in text, f"图片应留标记：{text!r}"
    assert "abc.png" not in text, f"图片块被 dump 进材料了（几万字符的噪音）：{text!r}"
    assert '{"type"' not in text, f"材料里出现了 JSON 块：{text!r}"
    print("✓ tool-result 内嵌图片只留 [图片] 标记，不 dump")
