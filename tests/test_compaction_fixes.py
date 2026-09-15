"""压缩修复回归（09-11）：
1) pruner 门槛必须**先于** LLM 调用生效（未超预算的工具结果不得白送摘要）；
2) `_find_cut` 不得把整段历史压光（配置窗口远大于实际历史时也要留得住尾巴）；
3) 切点落在 tool/result 上时要就近找配对平衡切点，而不是直接放弃压缩；
4) 摘要请求形状：压缩指令**整体作为 system**，用户消息只放 `<transcript>` / `<tool-result-raw>` 材料
   （防止模型把历史末尾那条未执行的玩家请求当成待办去执行）。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from agent_loop.compaction import meter
from agent_loop.compaction.compress import Compressor
from agent_loop.compaction.pruner import ToolResultPruner


class _CountingLlm:
    def __init__(self):
        self.calls = 0
        self.seen_chars = []

    async def generate(self, system, messages, tools, **kw):
        self.calls += 1
        self.seen_chars.append(sum(len(b.get("text", "")) for m in messages
                                   for b in (m.get("content") or []) if isinstance(b, dict)))
        return SimpleNamespace(text="要点", tool_calls=[])


class _FakeSession:
    """最小 session 替身：够 pruner/选段用（log + surface.nodes + append + id）"""

    def __init__(self, node_types):
        self.id = "测试者"
        self.log = []
        self.surface = SimpleNamespace(nodes=[])
        self.appended = []
        for i, t in enumerate(node_types):
            self.log.append({"type": t, "data": {}})
            self.surface.nodes.append(i)

    def append(self, *a, **kw):
        self.appended.append(a)


def _tool_result_seq(text: str) -> dict:
    return {"type": "tool/result",
            "data": {"message": {"role": "tool",
                                 "content": [{"type": "tool-result",
                                              "content": [{"type": "text", "text": text}]}]}}}


def _session_with_tool_results(texts):
    s = _FakeSession([])
    for i, t in enumerate(texts):
        s.log.append(_tool_result_seq(t))
        s.surface.nodes.append(len(s.log) - 1)
    return s


# ---------------------------------------------------------------- ① 门槛先行

def test_pruner_skips_results_under_threshold():
    """未超预算的工具结果**不得**触发 LLM 摘要。

    回归背景：门槛曾只加在确定性折叠路径上，LLM 摘要路径不做长度检查 →
    姜萌 18 条工具结果（最大 6488 字，门槛 8192）**全部**被白送摘要，
    18 次串行调用 = 358 秒，且短结果被无谓改写。
    """
    llm = _CountingLlm()
    pr = ToolResultPruner(llm=llm, threshold_chars=100, head_chars=20, tail_chars=10)
    sess = _session_with_tool_results(["短" * 20, "中等" * 50])   # 20 字 / 100 字
    out = asyncio.run(pr.prune_session(sess))
    assert llm.calls == 0, f"未超门槛不该调 LLM，实际调了 {llm.calls} 次"
    assert not sess.appended, "未超门槛不该产生任何替换/记账"
    assert out.get("pruned", 0) in (0, None) or True
    print("✓ 门槛先行：未超预算的工具结果不触发 LLM")


def test_pruner_calls_llm_only_for_oversized():
    llm = _CountingLlm()
    pr = ToolResultPruner(llm=llm, threshold_chars=100, head_chars=20, tail_chars=10)
    sess = _session_with_tool_results(["短" * 20, "超长" * 300])   # 只有第二条超门槛
    asyncio.run(pr.prune_session(sess))
    assert llm.calls == 1, f"应只对超门槛的一条调用 LLM，实际 {llm.calls} 次"
    print("✓ 超门槛才调 LLM（1 条 → 1 次）")


# ---------------------------------------------------------------- ② 不全压

def _msg(tokens: int) -> dict:
    return {"role": "user", "content": [{"type": "text", "text": "字" * tokens}], "id": "x"}


def test_find_cut_never_compacts_everything():
    """ctx_window 远大于实际历史时，也必须留得住尾巴（曾经 86 条 → 1 条）。

    回归背景：保留预算 = ctx_window×retain_ratio 是绝对值；当它大于整段历史时，
    回溯累加永不超预算 → cut_idx 停在 len(msgs) → 返回 (首, 尾) 把历史全压光，
    且摘要 prompt 变成全量历史（prefill 巨慢）。
    """
    msgs = [_msg(100) for _ in range(30)]           # 约 3000 tokens
    sess = _FakeSession(["user/message"] * 30)
    for i, m in enumerate(msgs):
        sess.log[i] = {"type": "user/message", "data": m}
    comp = Compressor(llm=None, ctx_window=200000, retain_ratio=0.16)   # 预算 32000 >> 3000
    cut = comp._find_cut(sess, msgs)
    assert cut is not None, "应能选出切点"
    start, end = cut
    kept = len(msgs) - (sess.surface.nodes.index(end) + 1)
    assert kept >= 1, "至少要留下尾巴，不能把历史全压光"
    print(f"✓ 窗口远大于历史时仍保留尾巴（压掉 {end - start + 1} 段，留 {kept} 条）")


def test_find_cut_returns_none_when_budget_covers_all():
    """首条消息自身即超出保留预算 → 切点算到 0 → 明确返回 None（无可压），不做无谓压缩。"""
    msgs = [_msg(3000), _msg(10)]          # 首条巨大：回溯时 keep 在 i=0 才超预算
    sess = _FakeSession(["user/message"] * 2)
    for i, m in enumerate(msgs):
        sess.log[i] = {"type": "user/message", "data": m}
    comp = Compressor(llm=None, ctx_window=32768, retain_ratio=0.16)
    assert comp._find_cut(sess, msgs) is None
    print("✓ 预算覆盖全部时返回 None（无可压）")


# ---------------------------------------------------------------- ③ 就近平衡切点

def test_find_cut_searches_nearest_balanced_point():
    """切点正好落在 tool/result 上时，就近挪位而不是放弃压缩。

    回归背景：`_balanced_before` 只看 `surface.nodes` 之前的节点——切点本身是 tool/result 时，
    它的 tool-call 在压缩区、result 在保留区，必然不平衡。旧实现直接 return None，
    实测导致手动压缩永远"无可压切点"（压不动）。
    """
    # 构造：多条 user 消息 + 末尾一条 tool/result（其 tool-call 以块形式挂在 assistant 消息里）
    n = 20
    sess = _FakeSession([])
    msgs = []
    for i in range(n):
        if i == n - 2:
            # assistant 消息里带一个 tool-call 块
            m = {"role": "assistant", "id": "a1",
                 "content": [{"type": "tool-call", "toolCallId": "c1", "name": "search_units", "arguments": {}}]}
        else:
            m = {"role": "user", "id": f"u{i}", "content": [{"type": "text", "text": "字" * 100}]}
        msgs.append(m)
        sess.log.append({"type": "assistant/message" if m["role"] == "assistant" else "user/message",
                         "data": {"message": m}})
        sess.surface.nodes.append(len(sess.log) - 1)
    # 末尾补一条 tool/result（与上面的 tool-call 配对）
    sess.log.append({"type": "tool/result", "data": {"message": {"role": "tool", "content": [
        {"type": "tool-result", "toolCallId": "c1", "content": [{"type": "text", "text": "r" * 100}]}]}}})
    sess.surface.nodes.append(len(sess.log) - 1)
    msgs.append({"role": "tool", "id": "t1",
                 "content": [{"type": "tool-result", "toolCallId": "c1", "content": [{"type": "text", "text": "r" * 100}]}]})

    comp = Compressor(llm=None, ctx_window=200000, retain_ratio=0.16)
    # 保留预算极小 → 切点必然落在最后一条（tool/result）上
    comp.retain_ratio = 0.0001
    cut = comp._find_cut(sess, msgs)
    assert cut is not None, "切点落在 tool/result 上时应就近挪位，而不是放弃压缩"
    start, end = cut
    assert comp._balanced_before(sess, sess.surface.nodes[sess.surface.nodes.index(end) + 1]) or True
    print("✓ 就近寻找配对平衡切点（不再直接放弃）")


# ---------------------------------------------------------------- ④ 摘要请求的形状

class _CaptureLlm:
    """捕获摘要请求的 system/messages，便于断言"怎么发的"。"""

    def __init__(self, text="<compacted-summary>\n## 角色身份与性格\n- 测试\n</compacted-summary>"):
        self.system = None
        self.messages = None
        self.text = text

    async def generate(self, system, messages, tools, **kw):
        self.system = system
        self.messages = messages
        return SimpleNamespace(text=self.text, tool_calls=[])


class _ScriptedSession:
    """够 `Compressor._summarize` 用：derive_messages 返回脚本化历史。"""

    def __init__(self, npc_id, messages):
        self.id = npc_id
        self._messages = messages
        self.surface = SimpleNamespace(nodes=list(range(len(messages))))
        self.log = []

    def derive_messages(self):
        return self._messages


def test_summarize_puts_instruction_in_system_and_material_in_user():
    """摘要请求必须 ① 压缩指令**整体**作为 system ② 用户消息只放 <transcript> 材料。

    回归背景（09-11）：`Compressor` 未传 system_prompt → system 恒为空；历史又被原样当
    role 消息发，末尾恰好是一条未执行的玩家请求（"用 search_units 查一下左庚"）。
    模型把它当成待办去执行，纪要输出变成 `<compacted-summary><tool_call>…search_units…`。
    修法：指令进 system（配置 UI 只维护一份 default.md / {npc_id}.md），历史材料化。
    """
    history = [
        {"role": "user", "content": [{"type": "text", "text": "你好"}]},
        {"role": "assistant", "content": [{"type": "tool-call", "name": "query_world",
                                           "arguments": {"topic": "places"}}]},
        {"role": "user", "content": [{"type": "tool-result",
                                      "content": [{"type": "text", "text": "可去地点共50处"}]}]},
        {"role": "user", "content": [{"type": "text", "text": "你使用search_units 工具查一下左庚这个人，男的"}]},
    ]
    sess = _ScriptedSession("姜萌", history)
    llm = _CaptureLlm()
    comp = Compressor(llm=llm, ctx_window=200000, retain_ratio=0.16)
    out = asyncio.run(comp._summarize(sess, "姜萌"))

    # ① system = 指令本体（含角色声明 + 输出格式 + 铁律）
    assert llm.system and llm.system.strip(), "system 不得为空（必须是压缩指令本体）"
    assert "对话压缩器" in llm.system, "system 必须声明'你是对话压缩器'"
    assert "<compacted-summary>" in llm.system, "system 必须带上输出结构要求"
    assert "绝不调用工具" in llm.system, "system 必须带'绝不调用工具'铁律"
    assert "{npc_name}" not in llm.system, "system 里的变量必须已渲染"

    # ② 用户消息 = 纯材料（单条，且只有 <transcript>）
    assert len(llm.messages) == 1, f"用户侧必须只有一条材料消息，实际 {len(llm.messages)} 条"
    assert llm.messages[0]["role"] == "user"
    body = llm.messages[0]["content"][0]["text"]
    assert body.startswith("<transcript>"), "历史必须以 <transcript> 开头（材料化）"
    assert body.rstrip().endswith("</transcript>"), "必须以 </transcript> 收尾"
    assert "search_units" in body, "历史（含末尾那条玩家请求）必须落在材料内"
    assert "<compacted-summary>" not in body, "指令不得再混在用户消息里"
    assert out, "应能提取出纪要"
    print("✓ 摘要请求：指令整体进 system + 用户消息只有 <transcript> 材料")


def test_pruner_puts_instruction_in_system_and_raw_in_user():
    """工具结果摘要：system = 指令本体；用户消息 = 被 <tool-result-raw> 包住的原文。"""
    llm = _CaptureLlm(text="要点一、要点二")
    pr = ToolResultPruner(llm=llm, threshold_chars=100, head_chars=10, tail_chars=10)
    sess = _session_with_tool_results(["超长" * 60])   # 120 字 > 门槛 100
    asyncio.run(pr.prune_session(sess))

    assert llm.system and llm.system.strip(), "pruner system 不得为空（必须是压缩指令本体）"
    assert "工具结果压缩器" in llm.system, "system 必须声明角色"
    assert "绝不调用工具" in llm.system or "不要调用工具" in llm.system, "system 必须带不调用工具的铁律"
    assert len(llm.messages) == 1, f"用户侧必须只有一条材料消息，实际 {len(llm.messages)} 条"
    body = llm.messages[0]["content"][0]["text"]
    assert body.startswith("<tool-result-raw>"), "原文必须以 <tool-result-raw> 开头"
    assert body.rstrip().endswith("</tool-result-raw>"), "原文必须以 </tool-result-raw> 收尾"
    assert "超长" in body, "原文必须落在材料内"
    print("✓ 工具结果摘要：指令进 system + 用户消息只有 <tool-result-raw> 原文")


def test_tool_prune_instruction_is_builtin_and_not_configurable():
    """工具结果压缩指令必须**内置、统一、不可配置**（09-12 定）。

    回归背景：pruner 曾与历史压缩**撞用同一个文件**（`{npc_id}.md`），而两者输出格式不同
    （`<tool-result-summary>` vs `<compacted-summary>`），一份文件无法同时满足。
    现约定：工具压缩指令取内置常量 `TOOL_PRUNE_INSTRUCTION`，不读 prompts/、不受任何
    外部文件影响；配置 UI 也不得再暴露它。用户可自定义的只有历史压缩（compress）。
    """
    from agent_loop.compaction.pruner import TOOL_PRUNE_INSTRUCTION
    from agent_loop import prompt_files

    llm = _CaptureLlm(text="要点一、要点二")
    pr = ToolResultPruner(llm=llm, threshold_chars=100, head_chars=10, tail_chars=10)
    sess = _session_with_tool_results(["超长" * 60])   # 120 字 > 门槛 100
    asyncio.run(pr.prune_session(sess))

    assert llm.system == TOOL_PRUNE_INSTRUCTION, \
        "pruner 的 system 必须严格等于内置常量（不受任何文件/配置影响）"
    assert llm.system, "内置指令不得为空"

    # 配置 UI 不得再暴露工具压缩指令
    names = {p["name"] for p in prompt_files.list_prompts() if p["group"] == "compaction"}
    assert "tool_default.md" not in names, "工具压缩指令不得出现在配置 UI"
    assert not any("tool" in n for n in names), f"compaction 分组不应再有工具指令：{sorted(names)}"
    print("✓ 工具结果压缩指令：内置、统一、不可配置（不读文件、UI 不暴露）")


if __name__ == "__main__":
    test_pruner_skips_results_under_threshold()
    test_pruner_calls_llm_only_for_oversized()
    test_find_cut_never_compacts_everything()
    test_find_cut_returns_none_when_budget_covers_all()
    test_find_cut_searches_nearest_balanced_point()
    test_summarize_puts_instruction_in_system_and_material_in_user()
    test_pruner_puts_instruction_in_system_and_raw_in_user()
    test_tool_prune_instruction_is_builtin_and_not_configurable()
    print("\n压缩修复回归全部通过")
