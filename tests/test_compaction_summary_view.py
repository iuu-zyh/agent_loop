"""压缩纪要的读取 / 可编辑判据 / 改账 / 历史投影（10-31）

这份功能的四条硬约束（改之前先读 `docs/plan-compaction-view.md` §2）：

  ① **账本只追加**：修改 = 再追加一条 `user/message` + `replace{start=end=旧seq}`，
     绝不原地改旧事件；存档才落账（与所有对话增量同一套）。
  ② **compactionId 跨编辑不变**：删除逻辑的覆盖闭包按它查 `shadowedSeqs`；
     换 id 会让改过的纪要丢掉"我代表哪段原文"。
  ③ **可编辑窗口 = 这条纪要还没被任何一次请求用过**：之后没有 `turn/start`、
     也没有 `assistant/message`。后者专治"自动压缩发生在回合内部"。
  ④ **历史投影只吐一条**（当前生效那版），位置在**原始那一格**，`turn=0` / `scene=""`。

本文件只测**纯逻辑**（不碰 WS / 不碰 agent）；RPC 与推送事件见
`tests/test_compaction_edit_rpc.py`，C# 源码契约见 `tests/test_compaction_ui_contract.py`。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

_PARENT = str(Path(__file__).resolve().parents[2])
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from agent_loop.compaction import Compressor, summary
from agent_loop.compaction.compress import COMPACT_PLUGIN, SUMMARY_OPEN, SUMMARY_CLOSE
from agent_loop.session.history import project_ui_history
from agent_loop.session.history_ops import plan_delete
from agent_loop.session.session import Session

NPC = "林婉清"
SUMMARY_BODY = "## 自身\n- 你外冷内热，说话简省。"


class _StubSummaryLlm:
    """摘要桩：只回一条固定纪要（够短、够稳定）。"""

    def __init__(self, body: str = SUMMARY_BODY):
        self.body = body
        self.calls = 0

    async def generate(self, system, messages, tools, **kw):
        self.calls += 1
        return SimpleNamespace(
            text=f"{SUMMARY_OPEN}\n{self.body}\n{SUMMARY_CLOSE}",
            tool_calls=[], usage={})


def _seed(sess: Session, turns: int = 40) -> Session:
    """造一段够长的历史（40 轮），每轮 user + assistant + 闭合。"""
    for i in range(turns):
        sess.append("turn/start", {"turn": i + 1, "scene": ""})
        sess.append("user/message", {
            "role": "user", "id": f"u{i}",
            "content": [{"type": "text", "text": f"玩家第{i}句话，问你最近有没有去过白帝城，路上可还太平"}],
        }, {"surfaceOp": "append"})
        sess.append("assistant/message", {
            "message": {"role": "assistant", "id": f"a{i}",
                        "content": [{"type": "text", "text": f"你第{i}次回答：去过，边关风沙大，小心为上。"}]},
        }, {"surfaceOp": "append"})
        sess.append("turn/end", {"turn": i + 1, "reason": {"kind": "completed"}})
    return sess


def _compact(sess: Session, body: str = SUMMARY_BODY):
    comp = Compressor(llm=_StubSummaryLlm(body), ctx_window=1200,
                      retain_ratio=0.16, cool_down=0.0)
    return asyncio.run(comp.compact_now(sess, sess.id))


def _compaction_items(sess: Session, scene: str = "") -> list:
    out = project_ui_history(sess, max_turns=0, scene=scene)
    return [it for it in out["items"] if it.get("kind") == "compaction"]


# ---------------------------------------------------------------- 常量与识别

def test_plugin_constants_match_producer():
    """`summary.py` 的 plugin 名与标记必须与生产者（compress.py）逐字同值。

    为什么值得一条断言：summary.py **刻意不 import** compress（只读投影不该被压缩实现
    牵着走），两处字面量一旦漂移，表现是"UI 永远看不到纪要"且零报错。
    """
    assert summary.SUMMARY_PLUGIN == COMPACT_PLUGIN
    assert summary.SUMMARY_OPEN == SUMMARY_OPEN
    assert summary.SUMMARY_CLOSE == SUMMARY_CLOSE
    print("✓ plugin 名与纪要标记与生产者同值")


def test_no_compaction_yields_nothing():
    sess = _seed(Session(id=NPC, header={"id": NPC}), turns=2)
    assert summary.latest(sess) is None
    assert _compaction_items(sess) == []
    print("✓ 没压缩过：latest=None，投影没有 compaction 条目")


# ---------------------------------------------------------------- 读

def test_compaction_report_and_latest():
    """压缩报告要带正文与新节点 seq；`latest` 读的是 surface 里活着那条。"""
    sess = _seed(Session(id=NPC, header={"id": NPC}))
    rep = _compact(sess)
    assert rep is not None
    assert set(rep) >= {"range", "compactionId", "summary", "summarySeq"}
    assert rep["summary"] == SUMMARY_BODY.strip()
    node = sess.log[rep["summarySeq"]]
    assert summary.is_summary(node), "summarySeq 必须指向那条 plugin user/message"

    info = summary.latest(sess)
    assert info is not None
    assert info["seq"] == rep["summarySeq"] and info["anchor"] == rep["summarySeq"]
    assert info["compaction_id"] == rep["compactionId"]
    assert info["text"] == SUMMARY_BODY.strip()
    assert info["chars"] == len(SUMMARY_BODY.strip())
    assert info["editable"] is True and info["lock_reason"] == ""
    assert info["edited"] is False and info["edits"] == 0
    print(f"✓ 压缩报告/最新纪要：{info['chars']} 字，可编辑={info['editable']}")


def test_latest_follows_surface_not_log():
    """被后一次压缩折走的旧纪要不在 surface ⇒ `latest` 只认新的那条。"""
    sess = _seed(Session(id=NPC, header={"id": NPC}))
    _compact(sess, "## 自身\n- 第一次压缩的纪要。")
    # 再压一次：这次会把上一次的纪要（以及它的保留尾）一起折进新纪要
    _compact(sess, "## 自身\n- 第二次压缩的纪要。")
    info = summary.latest(sess)
    assert info is not None and info["text"] == "## 自身\n- 第二次压缩的纪要。"
    items = _compaction_items(sess)
    assert len(items) == 1 and items[0]["text"] == info["text"], "投影只许出现当前生效那一版"
    print("✓ 连续两次压缩：只认最新纪要，投影不重复")


# ---------------------------------------------------------------- 写

def test_edit_replaces_in_place_and_context_follows():
    """改纪要 = 追加一条 replace(start==end)；模型可见正文立刻变；投影只有一行。"""
    sess = _seed(Session(id=NPC, header={"id": NPC}))
    rep = _compact(sess)
    info = summary.latest(sess)
    n_log = len(sess.log)
    new_text = "## 自身\n- 你改过的纪要：说话更冷一点。"

    out = summary.apply_edit(sess, info["compaction_id"], new_text)
    assert out["text"] == new_text and out["edited"] is True and out["edits"] == 1
    assert out["anchor"] == info["anchor"], "编辑不搬动时间线位置"
    assert out["seq"] != info["seq"] and out["seq"] >= n_log

    # ── 账本形状：先审计、后替换；replace 的 start == end == 旧 seq
    audit = sess.log[n_log]
    assert audit["type"] == summary.EDIT_TYPE
    assert audit["data"]["compactionId"] == rep["compactionId"]
    assert audit["data"]["fromSeq"] == info["seq"]
    assert audit["data"]["charsAfter"] == len(new_text)

    ev = sess.log[n_log + 1]
    assert ev["type"] == "user/message"
    assert ev["surfaceOp"] == {"op": "replace", "start": info["seq"], "end": info["seq"]}
    src = ev["data"]["source"]
    assert src["compactionId"] == rep["compactionId"], "compactionId 跨编辑不变（删除覆盖闭包靠它）"
    assert src["anchorSeq"] == info["anchor"] and src["edited"] is True and src["edits"] == 1

    # ── 模型可见：derive_messages 里那一格已经是新正文（不是"看起来改了"）
    assert new_text in sess.derive_messages()[0]["content"][0]["text"]
    assert info["text"] not in sess.derive_messages()[0]["content"][0]["text"]

    # ── 投影：一条，正文/字数都是新的，位置仍是原 anchor
    items = _compaction_items(sess)
    assert len(items) == 1
    assert items[0]["text"] == new_text and items[0]["chars"] == len(new_text)
    assert items[0]["seq"] == out["seq"], "投影给的是当前节点 seq（删除判断要用它）"
    assert items[0]["editable"] is True
    print("✓ 修改落账：replace(start==end)、上下文与投影同步更新、compactionId 不变")


def test_multiple_edits_keep_single_row_and_anchor():
    sess = _seed(Session(id=NPC, header={"id": NPC}))
    _compact(sess)
    info = summary.latest(sess)
    for i in range(3):
        info = summary.apply_edit(sess, info["compaction_id"], f"## 自身\n- 第 {i + 1} 次修改。")
    assert info["edits"] == 3 and info["anchor"] == summary.latest(sess)["anchor"]
    items = _compaction_items(sess)
    assert len(items) == 1 and items[0]["text"].endswith("第 3 次修改。")
    print("✓ 连改三次：仍只有一行、锚点不漂、edits 计数正确")


def test_turn_start_locks_edit():
    """压缩后一旦开启新一轮对话 ⇒ 不可改（用户拍板的那条窗口）。"""
    sess = _seed(Session(id=NPC, header={"id": NPC}))
    _compact(sess)
    info = summary.latest(sess)
    sess.append("turn/start", {"turn": 41, "scene": ""})

    locked = summary.latest(sess)
    assert locked["editable"] is False
    assert "新一轮对话" in locked["lock_reason"]
    try:
        summary.apply_edit(sess, info["compaction_id"], "改不动")
        raise AssertionError("锁定后必须拒绝")
    except summary.SummaryEditError as e:
        assert "新一轮对话" in str(e)
    n_before = len(sess.log)
    assert len(sess.log) == n_before, "拒绝时账本一字不动"
    assert _compaction_items(sess)[0]["editable"] is False
    print("✓ 开启新回合后锁定：RPC 拒绝、账本不动、投影如实标不可编辑")


def test_summary_inside_turn_is_never_editable():
    """自动压缩的形态：纪要在 `turn/start` **之后**落账 ⇒ 永远不可改。

    这就是"只看之后有没有 turn/start"会误判的那一格（自动压缩的 turn/start 在它**前面**）。
    判据是"它落在回合内部"，与回合是否已经闭合无关 —— 闭合后模型也早已读过它。
    """
    sess = _seed(Session(id=NPC, header={"id": NPC}), turns=3)
    sess.append("turn/start", {"turn": 4, "scene": ""})
    folded = list(sess.surface.nodes)
    sess.append("compaction/summary", {
        "shadowedRange": {"start": folded[0], "end": folded[-1]},
        "shadowedSeqs": folded, "compactionId": "auto1",
    })
    sess.append("user/message", {
        "role": "user", "id": "s1",
        "content": [{"type": "text", "text": f"{SUMMARY_OPEN}\n自动压缩的纪要\n{SUMMARY_CLOSE}"}],
        "source": {"kind": "plugin", "plugin": "compact", "compactionId": "auto1"},
    }, {"surfaceOp": {"op": "replace", "start": folded[0], "end": folded[-1]}})

    info = summary.latest(sess)
    assert info["editable"] is False, "自动压缩的纪要落在回合内 ⇒ 立刻锁定"
    assert "本轮对话已经开始" in info["lock_reason"]
    try:
        summary.apply_edit(sess, "auto1", "想改")
        raise AssertionError("回合内产出的纪要必须拒绝修改")
    except summary.SummaryEditError as e:
        assert "本轮对话已经开始" in str(e)

    # 回合跑完、模型也回过了：仍然不可改（不是因为"回合开着"，是因为它就在回合里）
    sess.append("assistant/message", {
        "message": {"role": "assistant", "id": "a4",
                    "content": [{"type": "text", "text": "你回了一句。"}]},
    }, {"surfaceOp": "append"})
    sess.append("turn/end", {"turn": 4, "reason": {"kind": "completed"}})
    assert summary.latest(sess)["editable"] is False
    print("✓ 自动压缩落在回合内：自始至终不可改（不依赖回合是否闭合）")


def test_assistant_message_after_summary_locks_even_outside_turn():
    """防御性判据：纪要**不在**回合内、但之后出现了 assistant 消息 ⇒ 仍然锁定。

    现实路径里回合必以 `turn/start` 开头（已被上一条覆盖），这一条是给"将来若有别的
    路径直接落 assistant"留的保险 —— 它必须独立成立，而不是靠上一条顺带兜住。
    """
    sess = _seed(Session(id=NPC, header={"id": NPC}), turns=3)
    # 手工在**回合外**折一条纪要（形状同手动压缩）
    folded = list(sess.surface.nodes)
    sess.append("compaction/summary", {
        "shadowedRange": {"start": folded[0], "end": folded[-1]},
        "shadowedSeqs": folded, "compactionId": "manual-ish",
    })
    sess.append("user/message", {
        "role": "user", "id": "s1",
        "content": [{"type": "text", "text": f"{SUMMARY_OPEN}\n回合外的纪要\n{SUMMARY_CLOSE}"}],
        "source": {"kind": "plugin", "plugin": "compact", "compactionId": "manual-ish"},
    }, {"surfaceOp": {"op": "replace", "start": folded[0], "end": folded[-1]}})
    assert summary.latest(sess)["editable"] is True
    sess.append("assistant/message", {
        "message": {"role": "assistant", "id": "a9",
                    "content": [{"type": "text", "text": "凭空回了一句。"}]},
    }, {"surfaceOp": "append"})
    info = summary.latest(sess)
    assert info["editable"] is False and "说过话" in info["lock_reason"]
    print("✓ 回合外的纪要：之后出现 assistant 消息也锁定（防御判据独立成立）")


def test_new_compaction_supersedes_old_id():
    """面板开着时又压了一次：旧 compactionId 必须被拒，且原因要说人话。"""
    sess = _seed(Session(id=NPC, header={"id": NPC}))
    first = _compact(sess, "## 自身\n- 旧纪要。")
    _compact(sess, "## 自身\n- 新纪要。")
    try:
        summary.apply_edit(sess, first["compactionId"], "改旧的那条")
        raise AssertionError("被取代的纪要必须拒绝")
    except summary.SummaryEditError as e:
        assert "取代" in str(e)
    assert summary.latest(sess)["text"] == "## 自身\n- 新纪要。"
    print("✓ 被新压缩取代后拒绝修改旧纪要")


def test_edit_input_validation():
    sess = _seed(Session(id=NPC, header={"id": NPC}))
    _compact(sess)
    cid = summary.latest(sess)["compaction_id"]
    for bad, why in (("", "空"), ("   \n\t ", "全空白")):
        try:
            summary.apply_edit(sess, cid, bad)
            raise AssertionError(f"{why}正文必须拒绝")
        except summary.SummaryEditError as e:
            assert "不能为空" in str(e)
    try:
        summary.apply_edit(sess, cid, "字" * (summary.MAX_CHARS + 1))
        raise AssertionError("超长必须拒绝")
    except summary.SummaryEditError as e:
        assert "过长" in str(e)
    # 防御：误把带标记的整段粘进来 → 剥掉标记后照收（不是报错）
    out = summary.apply_edit(sess, cid, f"{SUMMARY_OPEN}\n正文\n{SUMMARY_CLOSE}")
    assert out["text"] == "正文"
    print("✓ 输入校验：空/全空白/超长拒绝，误粘标记自动剥掉")


# ---------------------------------------------------------------- 与删除逻辑的接缝

def test_delete_coverage_still_works_after_edit():
    """改过之后，'删光原文 ⇒ 纪要一起忘'仍然成立（compactionId 不变的直接收益）。"""
    sess = _seed(Session(id=NPC, header={"id": NPC}))
    _compact(sess)
    info = summary.latest(sess)
    summary.apply_edit(sess, info["compaction_id"], "## 自身\n- 改过的纪要。")

    turns = [ev["data"]["turn"] for ev in sess.log if ev["type"] == "turn/start"]
    plan = plan_delete(sess, turns)
    assert plan["ok"] is True
    assert plan["dropped_summaries"], "原文全覆盖时必须把纪要一起删掉（哪怕它被编辑过）"
    assert summary.latest(sess) is not None, "plan 是纯函数，还没落账"
    print(f"✓ 编辑后删除覆盖闭包仍成立（将删纪要 {len(plan['dropped_summaries'])} 条）")


# ---------------------------------------------------------------- 投影细节

def test_projection_place_and_scene_rules():
    """位置在 anchor 之前、`turn=0`、`scene` 恒空（群聊回合里压缩也不该在 1:1 窗消失）。"""
    sess = _seed(Session(id=NPC, header={"id": NPC}))
    _compact(sess)
    # 压缩之后再来一回合（会锁定，但投影照常）
    sess.append("turn/start", {"turn": 41, "scene": ""})
    sess.append("user/message", {"role": "user", "id": "u41",
                                 "content": [{"type": "text", "text": "后来的一句话"}]},
                {"surfaceOp": "append"})
    sess.append("turn/end", {"turn": 41, "reason": {"kind": "completed"}})

    items = project_ui_history(sess, max_turns=0, scene="")["items"]
    idx = [i for i, it in enumerate(items) if it.get("kind") == "compaction"]
    assert len(idx) == 1
    assert items[idx[0]]["turn"] == 0, "纪要不属于任何回合（否则会被回合删除连坐）"
    assert items[idx[0]]["scene"] == ""
    assert idx[0] < [i for i, it in enumerate(items) if it.get("kind") == "user"][-1], \
        "纪要必须排在压缩之后那一回合的内容之前（按 anchor 定位）"

    # 群聊回合里发生的压缩：投影仍按单聊场合出（否则 1:1 窗回放会"记忆凭空消失"）
    sess2 = _seed(Session(id=NPC + "2", header={"id": NPC + "2"}), turns=6)
    sess2.append("turn/start", {"turn": 7, "scene": "room:g1"})
    folded = list(sess2.surface.nodes)
    sess2.append("compaction/summary", {"shadowedRange": {"start": folded[0], "end": folded[-1]},
                                        "shadowedSeqs": folded, "compactionId": "r1"})
    sess2.append("user/message", {
        "role": "user", "id": "s2",
        "content": [{"type": "text", "text": f"{SUMMARY_OPEN}\n群聊里压出来的纪要\n{SUMMARY_CLOSE}"}],
        "source": {"kind": "plugin", "plugin": "compact", "compactionId": "r1"},
    }, {"surfaceOp": {"op": "replace", "start": folded[0], "end": folded[-1]}})
    # 回合必须闭合：投影只收已闭合回合（半截回合不入历史，那是直播/重放不交叠的前提）
    sess2.append("turn/end", {"turn": 7, "reason": {"kind": "completed"}})
    # ★ 已知限制（不是"应该如此"，是"当前如此"，见 `docs/plan-compaction-view.md` §7）：
    #   投影的场合过滤是**按回合**做的（`closed = [x for x in closed if x["scene"] == scene]`），
    #   而纪要在账本位置上落在那个群聊回合里 ⇒ 回合被整段滤掉时，这一行也跟着没了。
    #   要修就得把投影改成**按条目**过滤（会动到删除/截断的既有语义），本功能刻意不碰。
    #   影响面：群聊回合里发生的自动压缩，在 1:1 窗重开时看不到这一行；
    #   下一次压缩产出新纪要时会出现（新纪元的 anchor 在更后面）。数据/记忆本身不受影响。
    solo_items = [it for it in project_ui_history(sess2, max_turns=0, scene="")["items"]
                  if it.get("kind") == "compaction"]
    assert solo_items == [], "当前实现：群聊回合里的纪要不进 1:1 回放（已知限制，改了要同步改这里）"
    all_items = [it for it in project_ui_history(sess2, max_turns=0, scene=None)["items"]
                 if it.get("kind") == "compaction"]
    assert len(all_items) == 1 and all_items[0]["scene"] == "", \
        "不过滤场合时它还在，且 scene 标的是单聊（不是继承群聊）"
    print("✓ 投影规则：位置在 anchor、turn=0、scene 恒空；群聊回合里的压缩是已知盲区")
