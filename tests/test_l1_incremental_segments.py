"""L1「经历类」段增量差分的**行为**测试（`agent/l1_incremental.py`）。

## 为什么必须有这个文件
这两段原来是「取最近 N 条 → 全量成文 → 文本变了就整段重发」。而经历日志**只增不减**，
于是"新增一条 = 整段重发"。实测（三个存档 20 个会话、313 条段消息）：
「近况」占总字数 **31%**（均长 301 字）、「与玩家相关」占 **9%**（均长 208 字），合计约 40%
—— 是 L1 里最大的一块，而它们的**内容增量**通常只有 30~60 字。

改成增量之后，错法的表现**全是静默的**（和这个仓库里其他几次事故同一形状）：
  · 键取错（比如只按文本、不带月份）⇒ 同一个事件在不同月份发生会被当成"已发过" ⇒ **漏发**；
  · 没有新增时返回空串 ⇒ 每一步都判"段变了" ⇒ 交替 `set`/`remove`，UI 上反复"已移除"，
    而且段会从容器里消失（模型静默失去这段）；
  · 发件箱不按会话隔离 ⇒ 多个 NPC 共用一套"已发过"集合 ⇒ 某人的经历**永远缺几条**；
  · 压缩把段折走后不作废发件箱 ⇒ 之后只发增量 ⇒ 被折走那段**永久缺席**。

## 钉八件事
  ① 首次全量、之后只发新增
  ② 无新增 ⇒ 文本**不变**（差分不触发，不发 remove）
  ③ 月份前缀参与判等：同文本不同月 ⇒ 两条都发（用户口径：先按月份、再按文本）
  ④ 同月内同文本 ⇒ 只发一次（真正的重复）
  ⑤ 候选为空 ⇒ 保持上次文本（不清空、不 remove）
  ⑥ `reset()` ⇒ 下次重发全量（压缩折走/重开会话的唯一自救手段）
  ⑦ 作用域隔离（多 NPC 各有自己的发件箱）
  ⑧ 成文形态仍是「1. …；2. …」，且增量带"新增"提示
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _mod():
    sys.path.insert(0, str(ROOT.parent))
    from agent_loop.agent.l1_incremental import IncrementalRegistry, IncrementalSegment
    return IncrementalSegment, IncrementalRegistry


def _renderer(fresh):
    return "；".join(f"{i + 1}. {t}" for i, t in enumerate(fresh))


def _seg():
    Seg, _ = _mod()
    return Seg()


# ---------------------------------------------------------------- ① 首次全量

def test_first_render_is_full_and_unmarked():
    """首次渲染 = 全量、**不加**"新增"提示（那是初始状态声明，不是追加）。"""
    s = _seg()
    out = s.render(["(1年1月) 甲", "(1年2月) 乙"], _renderer, marker="（以下为新增）")
    assert out == "1. (1年1月) 甲；2. (1年2月) 乙", out
    assert "新增" not in out, "首次渲染不该标'新增'（会让人以为之前还有内容）"


def test_second_render_without_new_sends_nothing():
    """② 无新增 ⇒ 文本**一字不变**（差分判据据此不触发）。

    ★ 反面：返回空串会让每步都判"段变了" ⇒ 交替 set/remove，段还会从容器里消失。
    """
    s = _seg()
    a = s.render(["(1年1月) 甲", "(1年2月) 乙"], _renderer)
    b = s.render(["(1年1月) 甲", "(1年2月) 乙"], _renderer)
    assert a == b, f"无新增时文本变了：\n  第一次={a!r}\n  第二次={b!r}"


# ---------------------------------------------------------------- ② 只发新增

def test_only_new_entries_are_rendered():
    """有新增 ⇒ 只发新增那几条（不加旧条目）。"""
    s = _seg()
    s.render(["(1年1月) 甲"], _renderer)
    out = s.render(["(1年1月) 甲", "(1年2月) 乙", "(1年3月) 丙"], _renderer, marker="（以下为新增）")
    assert "甲" not in out, f"重发了旧条目：{out}"
    assert "乙" in out and "丙" in out, out
    assert out.startswith("（以下为新增）"), out
    # 编号从 1 重新开始（增量是独立阅读单元；全局序号会让模型以为中间丢了内容）
    assert "1. (1年2月) 乙" in out, out


def test_incremental_numbering_restarts():
    """③ 增量里的编号重新从 1 开始（不是接着全局第 37 条）。"""
    s = _seg()
    s.render([f"(1年{i}月) 第{i}条" for i in range(1, 6)], _renderer)
    out = s.render([f"(1年{i}月) 第{i}条" for i in range(1, 8)], _renderer, marker="（新增）")
    # 新增 2 条（第6、第7）⇒ 增量内部编号 1、2（而不是接着全局的 6、7）
    assert "1. (1年6月) 第6条" in out, out
    assert "2. (1年7月) 第7条" in out, out
    assert "6. " not in out.replace("(1年6月)", ""), f"编号沿用全局序号：{out}"


# ---------------------------------------------------------------- ③ 月份参与判等

def test_same_text_different_month_is_a_new_entry():
    """★ 用户口径：**先按月份**去重 ⇒ 同一个事件在不同月份发生是两条。

    C# 已经给每条带了 `(N年M月) ` 前缀，所以"月份+文本"的唯一键就是那条成品字符串。
    若只按文本去重，这里会被误判成"已发过" ⇒ **漏发**（静默）。
    """
    s = _seg()
    s.render(["(1年5月) 在客栈中休养了一段时日，自身状态恢复了！"], _renderer)
    out = s.render(["(1年5月) 在客栈中休养了一段时日，自身状态恢复了！",
                    "(1年6月) 在客栈中休养了一段时日，自身状态恢复了！"],
                   _renderer, marker="（新增）")
    assert "1年6月" in out, f"同文本不同月被当成重复 ⇒ 漏发：{out!r}"
    assert "1年5月" not in out, f"重发了 5 月那条：{out!r}"


def test_same_month_same_text_is_deduped():
    """③ 同月内同文本 ⇒ 只发一次（这才是真正的重复）。"""
    s = _seg()
    s.render(["(1年5月) 甲"], _renderer)
    out = s.render(["(1年5月) 甲", "(1年5月) 甲"], _renderer, marker="（新增）")
    assert out == "1. (1年5月) 甲", out


# ---------------------------------------------------------------- ④ 候选为空

def test_empty_candidates_keeps_last_text():
    """⑤ 候选为空 ⇒ **保持上次文本**（不清空）。

    反面：返回空串会被判成"段变了且 desired 为空" ⇒ emit `remove` ⇒
    UI 显示"已移除"、段从容器消失。而"这一轮 C# 没取到经历"不等于"经历没了"。
    """
    s = _seg()
    a = s.render(["(1年1月) 甲"], _renderer)
    b = s.render([], _renderer)
    assert b == a, f"候选为空时把段清掉了：{a!r} → {b!r}"


# ---------------------------------------------------------------- ⑤ reset

def test_reset_sends_everything_again():
    """⑥ `reset()` ⇒ 下次重发全量（压缩折走 / 重开会话的唯一自救手段）。

    ★ 这条是增量语义的**安全阀**：条目只在"发过的那条消息还在上下文里"时才算数。
    被压缩折走后模型看不见了，若发件箱还认为"发过"⇒ 这段**永久缺席**且无报错。
    """
    s = _seg()
    s.render(["(1年1月) 甲", "(1年2月) 乙"], _renderer)
    assert s.sent_count == 2
    s.reset()
    assert s.sent_count == 0
    out = s.render(["(1年1月) 甲", "(1年2月) 乙"], _renderer, marker="（新增）")
    assert "甲" in out and "乙" in out, f"reset 后没有重发全量：{out!r}"
    assert not out.startswith("（新增）"), "reset 后的全量不该标'新增'"


# ---------------------------------------------------------------- ⑥ 作用域

def test_registry_isolates_scopes():
    """⑦ 多个 NPC 各有自己的发件箱。

    ★ 共用一套集合的后果：A 发过的条目，到 B 那就是"已发过" ⇒ **B 的经历永远缺几条**，
    而且没有任何报错（B 的段只是"看起来内容少"）。
    """
    _, Reg = _mod()
    r = Reg()
    a = r.get("npcA", "recent")
    b = r.get("npcB", "recent")
    a.render(["(1年1月) 甲的往事"], _renderer)
    out_b = b.render(["(1年1月) 甲的往事"], _renderer, marker="（新增）")
    assert "甲的往事" in out_b, f"作用域串味：B 把 A 的条目当成已发过 → {out_b!r}"
    assert not out_b.startswith("（新增）"), "B 是首次渲染，不该标'新增'"


def test_registry_reset_scope_and_clear():
    """注册表的两个清理入口都要有效（换档 / 关窗时用）。"""
    _, Reg = _mod()
    r = Reg()
    r.get("npcA", "recent").render(["(1年1月) 甲"], _renderer)
    r.get("npcA", "recent_with_player").render(["(1年1月) 乙"], _renderer)
    r.get("npcB", "recent").render(["(1年1月) 丙"], _renderer)

    r.reset("npcA", "recent")
    assert r.get("npcA", "recent").sent_count == 0
    assert r.get("npcA", "recent_with_player").sent_count == 1, "reset 波及了别的段"

    r.reset_scope("npcA")
    assert r.get("npcA", "recent_with_player").sent_count == 0, "reset_scope 没清干净"
    assert r.get("npcB", "recent").sent_count == 1, "reset_scope 误伤别的会话"

    r.clear()
    assert r.get("npcB", "recent").sent_count == 0


# ---------------------------------------------------------------- ⑦ 接进 SystemPrompt

def _bridge_segs(raw, scope="测试甲", npc="测试甲"):
    sys.path.insert(0, str(ROOT.parent))
    from agent_loop.agent.system_prompt import SystemPrompt
    sp = SystemPrompt()
    return sp, sp.format_l1_context(npc, raw, scope=scope)


def _raw(recent, wp=None, power=7048):
    return {
        "npc_id": "测试甲",
        "self": {"name": "测试甲", "sex": "男", "age": 22, "life": 140,
                 "power": power, "health": 451, "talent": 61, "mood": 100},
        "recent": recent,
        "recent_with_player": wp or [],
    }


def test_system_prompt_recent_is_incremental():
    """§ 接进 L1 之后：近况只发新增（端到端，不是只测核）。"""
    sp, segs1 = _bridge_segs(_raw(["(1年1月) 甲"]))
    assert "甲" in segs1["recent"]
    _, segs2 = _bridge_segs(_raw(["(1年1月) 甲"]))
    # 同一 sp、同一 scope ⇒ 无新增 ⇒ 文本不变
    segs2 = sp.format_l1_context("测试甲", _raw(["(1年1月) 甲"]), scope="测试甲")
    assert segs2["recent"] == segs1["recent"], "无新增时近况文本变了（差分会被触发）"
    segs3 = sp.format_l1_context("测试甲", _raw(["(1年1月) 甲", "(1年2月) 乙"]), scope="测试甲")
    assert "甲" not in segs3["recent"] and "乙" in segs3["recent"], segs3["recent"]


def test_system_prompt_recent_with_player_is_incremental():
    """§ 「与玩家相关」同样增量（用户口径：和经历一样）。"""
    sp, segs1 = _bridge_segs(_raw([], wp=["(1年1月) 与玩家论道"]))
    assert "论道" in segs1["recent_with_player"]
    segs2 = sp.format_l1_context("测试甲", _raw([], wp=["(1年1月) 与玩家论道",
                                                       "(1年2月) 与玩家切磋"]),
                                 scope="测试甲")
    assert "论道" not in segs2["recent_with_player"], segs2["recent_with_player"]
    assert "切磋" in segs2["recent_with_player"], segs2["recent_with_player"]


def test_vital_segments_are_separate_from_main_body():
    """§ 状态段独立：战力变 ⇒ 只动 `npc_vital`，`self` 一字不变。

    这是这次拆分**唯一的存在理由** —— 若战力变化仍然让 `self` 变了，拆了等于没拆。
    """
    sp, a = _bridge_segs(_raw([], power=7048))
    b = sp.format_l1_context("测试甲", _raw([], power=6992), scope="测试甲")
    assert a["self"] == b["self"], f"战力变化污染了自身段：\n  {a['self']!r}\n  {b['self']!r}"
    assert "7048" in a["npc_vital"] and "6992" in b["npc_vital"]
    assert a["npc_vital"] != b["npc_vital"]


def test_vital_absent_when_no_data():
    """§ 旧 DLL（没有 power/health/talent/mood）⇒ 状态段为空、整段不渲染（不编造）。"""
    _, segs = _bridge_segs({"npc_id": "测试甲", "self": {"name": "测试甲", "sex": "男"}})
    assert segs.get("npc_vital", "") == "", segs.get("npc_vital")
    assert segs.get("player_vital", "") == "", segs.get("player_vital")

# ---------------------------------------------------------------- ⑧ 压缩自救（端到端）

def test_compaction_losses_invalidate_notebook():
    """压缩把段折走后，**发件箱必须一起作废** —— 否则那段经历永久缺席。

    这是增量语义的安全阀，也是最容易漏的一环：`_resync_runtime_ctx_baselines` 原本只作废
    "发过什么文本"（`_retained_ctx`），而增量段的"发过哪些条目"记在另一处（`_incr`）。
    只作废前者 ⇒ 之后仍只发增量 ⇒ 被折走的那段模型再也看不见，且**没有任何报错**。
    """
    sys.path.insert(0, str(ROOT.parent))
    from agent_loop.agent.system_prompt import SystemPrompt
    import inspect

    # ① 源码层面：reset 必须在处理 lost 段的那段代码里
    da = (ROOT / "agent" / "dialogue_agent.py").read_text(encoding="utf-8")
    i = da.index("def _resync_runtime_ctx_baselines")
    body = da[i: i + 4000]
    assert "_incr.reset(" in body, \
        "压缩/重开导致段丢失后没有重置发件箱 —— 那段经历会永久只发增量（静默缺席）"
    assert "for name in lost" in body, "没有按「被折走的段」逐个重置"

    # ② 行为层面：reset 之后重发全量（而不是只发新增）
    # SystemPrompt 是**进程单例**（`_initialized` 闸）：前面的测试已经用它发过东西了。
    # 所以这里必须用一个**全新的 scope**，否则读到的 `_last` 是别的测试留下的。
    sp = SystemPrompt()
    _scope = "压缩自救·独立作用域"
    raw = {"npc_id": _scope, "self": {"name": _scope, "sex": "男"},
           "recent": ["(1年1月) 甲", "(1年2月) 乙"]}
    a = sp.format_l1_context(_scope, raw, scope=_scope)["recent"]
    assert "甲" in a and "乙" in a, a
    sp._incr.reset(_scope, "recent")            # 模拟"这段被压缩折走了"
    b = sp.format_l1_context(_scope, dict(raw, recent=["(1年1月) 甲", "(1年2月) 乙"]),
                             scope=_scope)["recent"]
    assert "甲" in b and "乙" in b, f"作废发件箱后没有重发全量：{b!r}"
