"""群聊里切磋的**公共叙述行**（`tools/room_narrate.py`）契约。

**为什么单独钉这个文件**：`docs/archive/2026-09-17-群聊编排/设计定稿.md:421-437` 早就设计过"动作类 → 派生一条第三人称事件行
广播进 `Room.log`"，但指定文件从未落地 —— 实际只有 4 种提案 + 关系解除会写公开行，
`world_ai_action` 在 `judge_proposal` 里直接判 `pass`。于是"有人被邀去切磋、还打了一整场"
这件事在全群的账本里**一个字都没有**；而切磋现在要占掉一整场战斗的时间
（`_run_round` 持 `room_lock` 串行跑成员），不广播一句，别的成员只会以为这一轮卡死了。

"结果全局可见"只能走发言行，不能共享 `tool/result`：成员的 tool 结果要与他看到的 tool_call
成对，插别人的 = 孤儿 tool 消息，网关直接拒。这条约束也钉在这里。

**09-22 追加：这行是**旁白**，画「行动记录」行、不画气泡。** 用户报"不该以 NPC 气泡发"——实机里它成了
发起人的气泡，而旁白中又引用了**玩家的原话**（`唐炎：「厉害，我输了。」` 出现在姜萌的泡里）。
用户拍板的形态是**工具返回结果那种行**：直播 `narration:true` → `AppendActionRow`，
重开窗走 `project_room_history` 的 `tool_result` 条目（所以重开窗也还在）。
**行为测试在 `tests/test_room.py::test_spar_narration_is_ledger_only_not_a_bubble`**
（本文件只测纯函数措辞，编排层那三条不变量归那边）。提案/应下/解除那几条叙述行**不在此列**。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CS = ROOT / "csharp"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _line(res):
    """按仓库既有写法导入（包名 = 目录名 agent_loop，父目录进 sys.path）。"""
    import importlib
    import sys
    parent = str(ROOT.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    mod = importlib.import_module(f"{ROOT.name}.tools.room_narrate")
    return mod.battle_line("端木茗雪", res)


# ---------- 各态文案 ----------

def test_result_with_lines_quotes_the_game():
    """打完且抓到战后台词 → 一行里带上**原话 + 归属**（原文一字不改，不判胜负）。"""
    t = _line({"success": True, "data": {
        "op": "spar", "target": "端木茗雪", "player": "唐炎", "accepted": True,
        "battle_seen": True, "battle_finished": True,
        "lines": [{"who": "player", "name": "唐炎", "text": "厉害，我输了。"},
                  {"who": "npc", "name": "端木茗雪", "text": "看来是我赢了！"}]}})
    assert t and "与唐炎切磋了一场" in t, t
    assert "唐炎：「厉害，我输了。」" in t and "端木茗雪：「看来是我赢了！」" in t, t
    assert "胜" not in t.replace("是我赢", ""), f"不该由我们下胜负结论：{t}"


def test_unknown_speaker_not_guessed():
    """归属判不出 → 写「有人」，绝不猜（猜反了人头，胜负就安错了）。"""
    t = _line({"success": True, "data": {
        "op": "spar", "target": "端木茗雪", "player": "唐炎", "accepted": True,
        "battle_finished": True, "lines": [{"who": "", "name": "", "text": "承让。"}]}})
    assert "有人：「承让。」" in t, t


def test_player_choice_in_stream_is_rendered_as_a_choice():
    """★09-23 单流★：`kind=option` 是玩家选择 —— 写「X选择了：「…」」，不是「有人：「…」」。"""
    t = _line({"success": True, "data": {
        "op": "spar", "target": "端木茗雪", "player": "唐炎", "accepted": True,
        "battle_seen": True, "battle_finished": True,
        "lines": [{"kind": "option", "who": "choice", "name": "唐炎",
                   "text": "好，就让我和你切磋一下"},
                  {"who": "player", "name": "唐炎", "text": "承让！"}]}})
    assert "唐炎选择了：「好，就让我和你切磋一下」" in t, t
    assert "有人：「好，就让我和你切磋一下」" not in t, t


def test_finished_without_lines_says_outcome_unknown():
    t = _line({"success": True, "data": {"op": "spar", "target": "端木茗雪",
                                         "accepted": True, "battle_finished": True}})
    assert t and "胜负未明" in t, t


def test_no_battle_result_is_not_a_loss():
    """进了战斗却没等到结果 ≠ 谁输了。"""
    t = _line({"success": True, "data": {"op": "spar", "target": "端木茗雪",
                                         "accepted": True, "battle_seen": True}})
    assert t and "结局没看清" in t and "输" not in t, t


def test_refusal_and_pending_are_distinct():
    no = _line({"success": True, "data": {"op": "spar", "target": "端木茗雪",
                                          "player": "唐炎", "accepted": False}})
    pend = _line({"success": True, "data": {"op": "spar", "target": "端木茗雪",
                                            "player": "唐炎", "pending": True}})
    assert no and "没接招" in no, no
    assert pend and "等他回应" in pend, pend


# ---------- 不许虚报 ----------

def test_failure_writes_nothing():
    """工具失败（含 C# 判"异地"回绝）不进公共流水 —— 没发生的事不是既成事实。"""
    assert _line({"success": False, "error": "切磋需同格，当前异地"}) is None
    assert _line(None) is None


def test_no_answer_writes_nothing():
    """玩家没选就关窗（`accepted` 缺失）→ 不替他向全群宣告。"""
    assert _line({"success": True, "data": {"op": "spar", "target": "端木茗雪"}}) is None


# ---------- 接线 ----------

def test_room_flow_wires_battle_settle():
    """闸门必须给**战斗类**（spar + attack）挂上事后收尾，且**发起时不写行**（那时还没过 C# 的同格闸）。"""
    src = _read(ROOT / "room" / "room_flow.py")
    assert re.search(r'name == "world_ai_action" and str\(args\.get\("op"\) or ""\) in \("spar", "attack"\)', src), \
        "闸门里没有战斗分支 —— 群里没人知道有人被邀了切磋/挨了打"
    assert "_make_battle_settle" in src, "缺少战斗收尾闭包"
    body = src.split("_make_battle_settle", 1)[1]
    assert "battle_line(" in body and "_say_and_emit(" in body, "收尾闭包没有把行写进 Room.log"
