#!/usr/bin/env python3
"""变异验证 —— 聊天帧缓冲队列（群 + 单聊）的两层测试（09-17 统一 / 原 room 版 09-16）。

**为什么必须有这个脚本**：本仓库的验收标准不是"测试跑绿了"，而是"**把修复逐个回退，
对应的测试真的变红**"（吃过恒真断言的亏）。队列的失效形态全是**静默**的：
重复气泡、忙碌态卡死、"正在说的那半截"不出现、单聊幽灵泡 —— 真机上偶发且无日志，
所以这里把每个关键分支逐个回退，确认两层测试各自能抓住自己的那半。

两层各锁一半：
  · C# 单测（`csharp/tests/ChatFrameQueueTests`）—— 判决规则（按轮过滤 / 只裁 delta /
    无归属拒收 / 单聊段闭合），喂帧序列断言"该演哪些帧"。
  · Python 源码契约（`tests/test_chat_frame_queue_contract.py`）—— 接线（消费时序
    在 ReplaceHistory 之后 / 收帧判据与分流 / 生命周期清理）。规则对了但接线被改回去，
    真机上一样是坏的。

用法：
    python3 scripts/dev/mutate_check_chat_frame_queue.py        # 两层都跑（默认）
    python3 scripts/dev/mutate_check_chat_frame_queue.py cs     # 只跑 C# 侧
    python3 scripts/dev/mutate_check_chat_frame_queue.py py     # 只跑 Python 侧

注意：会**临时改源码再还原**（备份在系统临时目录），跑完请确认末行是"全部变异都被捕获"。
"""
from __future__ import annotations
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
import _mutate_lib  # noqa: E402  （同进程跑测层，见该文件顶部说明）


import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QUEUE = ROOT / "csharp/UI/ChatFrameQueue.cs"
PRESENTER = ROOT / "csharp/UI/ChatPresenter.cs"
AB_PANEL = ROOT / "csharp/UI/AbChatPanel.cs"
TEE = ROOT / "csharp/UI/ChatFrameTee.cs"
WINDOW = ROOT / "csharp/UI/ChatWindow.cs"
ROOM_FLOW = ROOT / "room" / "room_flow.py"
CONTRACT = "tests/test_chat_frame_queue_contract.py"
RUNNER = ROOT / "scripts/dev/run_csharp_tests.py"

# ---------------------------------------------------------------------------
# C# 侧：回退队列规则，期望对应单测断言变红
# ---------------------------------------------------------------------------

CS_MUTATIONS = [
    (
        "① 连关键帧一起裁（speak / room_say / step 跟着 delta 一起丢）",
        QUEUE,
        'b.Frames[i].Event == "text_delta"',
        "true",
        "[裁剪：只裁 delta]",
    ),
    (
        "② 已闭合轮判决反向（把没被重放覆盖的丢掉、已覆盖的留下）",
        QUEUE,
        "out endSeq) && endSeq <= baselineSeq) continue;",
        "out endSeq) && endSeq > baselineSeq) continue;",
        "[已闭合轮",
    ),
    (
        "③ 无归属 delta / step 也收（重放会凭空建泡、活跃泡泄漏）",
        QUEUE,
        "if (b.OpenRound == 0) return false;",
        "if (b.OpenRound == 0) { b.OpenRound = b.NextRound++; }",
        "[无归属 delta]",
    ),
    (
        "④ 单聊 npc_reply 不清段（段永不闭合 ⇒ 重开窗把已收口的回合又演一遍）",
        QUEUE,
        "if (_solos.TryGetValue(npcId, out sb)) InvalidateSegment(sb);",
        "if (_solos.TryGetValue(npcId, out sb)) { }",
        "[npc_reply",
    ),
    (
        "⑤ 单聊隐式闭合失效（回合号推进不清旧段 ⇒ 死段演成幽灵泡）",
        QUEUE,
        "if (b.SegmentTurn > 0 && f.Turn > b.SegmentTurn) InvalidateSegment(b);",
        "if (b.SegmentTurn > 0 && f.Turn > b.SegmentTurn) { }",
        "[隐式闭合：step 回合号推进]",
    ),
    (
        "⑥ delta 合并丢了'写回表里'（struct 改的是副本 ⇒ 重开窗正文只剩第一个字）",
        QUEUE,
        "                    b.Segment[b.Segment.Count - 1] = last;   // struct：写回",
        "",
        "[delta 按 part 合并",
    ),
    (
        "⑦ user_say 不重置段（把上一轮的残留当成本轮内容 ⇒ 时间线错乱）",
        QUEUE,
        "                ClearSegment(b0);\n                b0.Segment.Add(f);",
        "                b0.Segment.Add(f);",
        "[user_say",
    ),
    (
        "⑧ 段闭合代数不推进（快照过期兜底失效 ⇒ 开窗期间收口的回合凭空消失）",
        QUEUE,
        "            b.ClosedEpoch++;",
        "            b.ClosedEpoch += 0;",
        "[段闭合代数",
    ),
    (
        "⑨ 跨 part 也合并（think 与正文串成一条 ⇒ 内心思量与台词混在一起）",
        QUEUE,
        '                if (last.Event == "text_delta" && lastPart == part)',
        '                if (last.Event == "text_delta")',
        "[delta 按 part 合并",
    ),
    (
        "⑩ 未闭合段里**已落账**的 room_say 不再整段丢（重开窗同一条画两遍）",
        QUEUE,
        'f.Event == "room_say" && f.Seq > 0 && f.Seq <= baselineSeq',
        "false",
        "[未闭合轮",
    ),
    (
        "⑪ step 的 kind 白名单失效（sys/ctx 也当成'会进段' ⇒ 闸门吞掉它们 = 两边都没有）",
        QUEUE,
        'if (ev == "step") return IsPlayableStepKind(kind);',
        'if (ev == "step") return true;',
        "[收帧白名单",
    ),
    (
        "⑫ 白名单放行未知事件（stats_update / compact_result 也当成'会进段' ⇒ 统计条 / 压缩提示行没人收尾）",
        QUEUE,
        '            if (ev == "step") return IsPlayableStepKind(kind);\n            return false;',
        '            if (ev == "step") return IsPlayableStepKind(kind);\n            return true;',
        "[收帧白名单",
    ),
]

# ---------------------------------------------------------------------------
# Python 侧：回退真实接线，期望对应契约测试变红
# ---------------------------------------------------------------------------

PY_MUTATIONS = [
    (
        "① 群消费提前到 ReplaceHistory 之前（会被重放整个擦掉）",
        PRESENTER,
        [
            (
                '            _window.ReplaceHistory(data["items"] as JArray);\n'
                '            _roomSpeaking = "";',
                '            DrainRoomFrames(roomId, data["items"] as JArray);\n'
                '            _window.ReplaceHistory(data["items"] as JArray);\n'
                '            _roomSpeaking = "";',
            ),
            (
                "            DrainRoomFrames(roomId, data[\"items\"] as JArray);\n"
                "            // 群删入口流程",
                "            // 群删入口流程",
            ),
        ],
        "test_drain_room_is_after_replace_history_and_after_busy_reset",
    ),
    (
        "② 单聊消费提前到 ReplaceHistory 之前（会被重放整个擦掉）",
        PRESENTER,
        [
            (
                "                _window.ReplaceHistory(items ?? new JArray());",
                "                DrainSoloFrames(npc);\n"
                "                _window.ReplaceHistory(items ?? new JArray());",
            ),
            (
                "                DrainSoloFrames(npc);\n"
                "                // 消费完才落位：之后到达的帧照常走直播（闸门期间它们都在队列里，一条不丢）\n",
                "                // 消费完才落位：之后到达的帧照常走直播（闸门期间它们都在队列里，一条不丢）\n",
            ),
        ],
        "test_drain_solo_is_after_replace_history_and_before_selecting",
    ),
    (
        "③ 单聊欠账分支不丢段（过期段会在未来安静重放里演成幽灵泡）",
        PRESENTER,
        [("                    ChatFrameQueue.ForgetSolo(npc);\n", "")],
        "test_drain_solo_is_after_replace_history_and_before_selecting",
    ),
    (
        "④ 单聊收帧回潮成'有人正显示就不收'（关窗前的工具/思考行再也补不回来）",
        TEE,
        [("                ChatFrameQueue.OfferSolo(npcId, Decode(ev, payload));",
          "                if (AbChatPanel.IsShowingNpc(npcId)) return;\n"
          "                ChatFrameQueue.OfferSolo(npcId, Decode(ev, payload));")],
        "test_tee_routes_room_and_solo_frames",
    ),
    (
        "⑤ tee 的 npc_reply 分支不送队列（显示中的残留段没人清）",
        TEE,
        [(
            "ChatFrameQueue.OfferSolo(npcId, new ChatFrame { Event = ev });",
            "return;",
        )],
        "test_tee_routes_room_and_solo_frames",
    ),
    (
        "⑥ 玩家发言帧不记（重开窗永远缺'我说过什么'）",
        PRESENTER,
        [("            ChatFrameQueue.OfferSolo(npc, new ChatFrame { Event = \"user_say\", "
          "Seq = -1, Turn = -1, Text = clean });\n", "")],
        "test_solo_frames_are_recorded_even_while_showing",
    ),
    (
        "⑦ 渲染闸判据被掏空（回执到达前直播照画 ⇒ 同一帧被画两遍）",
        PRESENTER,
        [("if (!_window.IsRoomMode && _replayGate.Length > 0 && _replayGate == npcId",
          "if (false")],
        "test_replay_gate_is_armed_before_request_and_cleared_on_every_exit",
    ),
    (
        "⑧ 成功路径不落位闸门（该窗口直播从此静默：帧只进队列不出画）",
        PRESENTER,
        [('                ClearReplayGate("重放已落地");\n', "")],
        "test_replay_gate_is_armed_before_request_and_cleared_on_every_exit",
    ),
    (
        "⑨ 早退出口漏落位（回执失败后直播永久静默）",
        PRESENTER,
        [('                    ClearReplayGate("回执失败");\n', "")],
        "test_replay_gate_is_armed_before_request_and_cleared_on_every_exit",
    ),
    (
        "⑩ 断线时照样置位闸门（请求没发出去 ⇒ 没有回执 ⇒ 该窗口永久静默）",
        PRESENTER,
        [("            if (_ws == null || !_ws.IsConnected)", "            if (false)")],
        "test_replay_gate_is_armed_before_request_and_cleared_on_every_exit",
    ),
    (
        "⑪ 整块重建不进钉底保持期（重开窗后视图停在顶部，要自己往下翻）",
        WINDOW,
        [("            _pinHoldFrames = 6;", "            _pinHoldFrames = 0;")],
        "test_bulk_rebuild_holds_the_scroll_at_bottom",
    ),
    (
        "⑫ 整段重演后不钉底（内容回来了但停在顶部）",
        PRESENTER,
        [("            if (_window != null) _window.HoldScrollAtBottom();\n", "")],
        "test_bulk_rebuild_holds_the_scroll_at_bottom",
    ),
    (
        "⑬ 群解散的清桶挪到早退守卫之后（解散的往往不是当前群 ⇒ 桶清不掉）",
        PRESENTER,
        [(
            "            ChatFrameQueue.ForgetRoom(roomId);\n"
            "            if (!_window.AcceptsRoomFrame(roomId)) return;",
            "            if (!_window.AcceptsRoomFrame(roomId)) return;\n"
            "            ChatFrameQueue.ForgetRoom(roomId);",
        )],
        "test_lifecycle_cleanups_are_wired",
    ),    (
        "⑭ 弃权不发 silent 帧（speak 开的空气泡永久留在群窗上）",
        ROOM_FLOW,
        [('await self._emit_state(room, phase="silent", speaking=[npc_id], spoken=spoken)',
          "pass")],
        "test_r3_silence_does_not_write_log_but_advances_cursor",
    ),
    (
        "⑮ C# 不认识 silent 帧（撤泡那一支落空）",
        PRESENTER,
        [('else if (phase == "silent")', "else if (false)")],
        "test_silent_speaker_drops_the_empty_bubble",
    ),
    (
        "⑯ speak / end 两道空气泡兜底被拆（silent 帧丢了就留下空气泡）",
        PRESENTER,
        [("if (_window.ActiveBubbleHasNoBody) _window.CancelActiveBubble();",
          "if (false) _window.CancelActiveBubble();")],
        "test_silent_speaker_drops_the_empty_bubble",
    ),
    (
        "⑰ 空气泡判据被掏空（恒假 ⇒ 空气泡撤不掉）",
        WINDOW,
        [("public bool ActiveBubbleHasNoBody => _deltaBubble != null && _delta.Length <= _deltaPrefixLen;",
          "public bool ActiveBubbleHasNoBody => false;")],
        "test_silent_speaker_drops_the_empty_bubble",
    ),
    (
        "⑱ 玩家回声不与待销账对号（每条群消息画两遍）",
        PRESENTER,
        [("if (_roomEchoPending.Count > 0 && _roomEchoPending.Peek() == text)", "if (false)")],
        "test_player_echo_is_not_drawn_twice",
    ),
    (
        "⑲ 闸门吞掉一切（队列不收的 stats_update / compact_result / step(sys|ctx) 也吞 ⇒ 那一帧两边都没有）",
        PRESENTER,
        [(
            '            if (!_window.IsRoomMode && _replayGate.Length > 0 && _replayGate == npcId\n'
            '                && ChatFrameQueue.IsSoloBuffered(ev0, payload["kind"]?.ToString() ?? "")) return;',
            '            if (!_window.IsRoomMode && _replayGate.Length > 0 && _replayGate == npcId) return;',
        )],
        "test_gate_swallows_only_buffered_frames",
    ),
    (
        "⑳ tee 的收帧白名单不再与闸门同源（各写一份 ⇒ 迟早漂移成'两边都没有'）",
        TEE,
        [('                if (!ChatFrameQueue.IsSoloBuffered(ev, (string)payload["kind"] ?? "")) return;',
          '                if ((string)payload["kind"] == "sys") return;')],
        "test_gate_swallows_only_buffered_frames",
    ),
    # ---- 09-17 群聊四件事（用户拍板）：异常不静默 / 点名不预开泡 / 隐藏输入侧折叠 / 折叠带说话人 ----
    (
        "㉑ 成员异常又变成静默（不发 error 帧 ⇒ 空气泡挂到下一人开口，玩家零解释）",
        ROOM_FLOW,
        [('phase="error", speaking=[npc_id]', 'phase="silent", speaking=[npc_id]')],
        "test_room_member_error_speaks_up_and_gate_is_quiet",
    ),
    (
        "㉒ C# 的 error 分支不给玩家任何解释（撤了泡但什么都不说）",
        PRESENTER,
        [('                string why = string.IsNullOrEmpty(note) ? "回合异常" : note;\n'
          '                _window.AppendSystemNotice(who.Length > 0\n'
          '                    ? who + " 这次没能开口：" + why + "，本轮跳过"\n'
          '                    : "有一位成员这次没能开口：" + why + "，本轮跳过");\n',
          '                string why = string.IsNullOrEmpty(note) ? "回合异常" : note;\n')],
        "test_room_member_error_speaks_up_and_gate_is_quiet",
    ),
    (
        "㉓ 点名又预开气泡（弃权/出错时留下一条要等别人说话才消失的幽灵泡）",
        PRESENTER,
        [('                SetBusy(true, "群点名 " + who);',
          '                SetBusy(true, "群点名 " + who);\n'
          '                if (who.Length > 0) _window.BeginNpcBubble(who + "：", false, who, whoId);')],
        "test_room_member_error_speaks_up_and_gate_is_quiet",
    ),
    (
        "㉔ 群聊又渲染「系统组装 / 运行时上下文」（用户明确要求隐藏）",
        PRESENTER,
        [('                if (_window.IsRoomMode) return;\n                AppendInputParts(',
          '                AppendInputParts(')],
        "test_room_member_error_speaks_up_and_gate_is_quiet",
    ),
    (
        "㉕ NoteTurn 挪到群聊早退之后（回合号丢了 ⇒ 群聊同段正文会成两次泡）",
        PRESENTER,
        [('                _window.NoteTurn(payload["turn"]?.Value<int>() ?? -1);\n', ''),
         ('                AppendInputParts(payload, kind, kind == "sys" ? "系统组装" : "运行时上下文");',
          '                _window.NoteTurn(payload["turn"]?.Value<int>() ?? -1);\n'
          '                AppendInputParts(payload, kind, kind == "sys" ? "系统组装" : "运行时上下文");')],
        "test_room_member_error_speaks_up_and_gate_is_quiet",
    ),
    (
        "㉖ 「行动记录」标题不带说话人（群里多人各一块，分不清是谁在做）",
        WINDOW,
        [('NewStepGroup(SpeakerLabel("行动记录"))', 'NewStepGroup("行动记录")')],
        "test_room_member_error_speaks_up_and_gate_is_quiet",
    ),
    (
        "㉗ 「内心思量」标题不带说话人（思考期间那是唯一的归属线索）",
        WINDOW,
        [('new ThinkGroup(_r.Content, SpeakerLabel("内心思量"))', 'new ThinkGroup(_r.Content)')],
        "test_room_member_error_speaks_up_and_gate_is_quiet",
    ),
    (
        "㉘ step(text) 在群聊里不补说话人前缀（气泡现建 ⇒ 屏幕上一条没有归属的台词）",
        PRESENTER,
        [('? _roomSpeaking + "：" + text : text', '? text : text')],
        "test_room_member_error_speaks_up_and_gate_is_quiet",
    ),
    (
        "㉙ 全员出错也不写终帧 note（屏幕上一个字都没有，玩家不知道该等还是该重说）",
        ROOM_FLOW,
        [('if not spoken and errs:', 'if False:')],
        "test_room_member_error_speaks_up_and_gate_is_quiet",
    ),
    (
        "㉚ 异常原因直接上内部类名（玩家看到 'TimeoutError'）",
        ROOM_FLOW,
        [('    if isinstance(exc, TimeoutError):        # py3.11 起 = asyncio.TimeoutError\n'
          '        return "模型超时（未在时限内完成）"',
          '    if isinstance(exc, TimeoutError):\n        return type(exc).__name__')],
        "test_room_member_error_speaks_up_and_gate_is_quiet",
    ),
    (
        "㉛ 群聊成员边界不关「内心思量」折叠（下一个成员的思考并进上一位那块，标题只写第一个人的名字）",
        PRESENTER,
        [('                _window.FinishThink();      // 他的回合结束了：思考折叠就此定稿（下一位另起一块）\n', '')],
        "test_room_member_error_speaks_up_and_gate_is_quiet",
    ),
]


def _backup(p: Path) -> Path:
    tmp = Path(tempfile.gettempdir()) / ("mutate_" + p.name)
    shutil.copy2(p, tmp)
    return tmp


def _run(cmd) -> int:
    r = _mutate_lib.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode


def _check(name: str, path: Path, repls, expect_hint: str, cmds) -> bool:
    bak = _backup(path)
    try:
        src = path.read_text(encoding="utf-8")
        for old, new in repls:
            if old not in src:
                print("  ✗ %s：待替换片段不存在（脚本要更新）：%r" % (name, old[:60]))
                return False
            src = src.replace(old, new)
        path.write_text(src, encoding="utf-8")
        code = 0
        for cmd in cmds:
            code = _run(cmd)
            if code != 0:
                break
        if code == 0:
            print("  ✗ %s：所有测试仍然通过 —— 这条修复没有被任何测试覆盖" % name)
            return False
        print("  ✓ %s：测试如实变红" % name)
        return True
    finally:
        shutil.copy2(bak, path)


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    cs_cmd = [sys.executable, "-X", "utf8", str(RUNNER), "--only", "ChatFrameQueueTests", "--quiet"]
    py_cmd = [sys.executable, "-X", "utf8", "-m", "pytest", CONTRACT, "-q"]

    _mutate_lib.guard(sorted({m[1] for m in (CS_MUTATIONS + PY_MUTATIONS)}))   # 被中断也要还原
    ok = True
    if only in ("", "cs"):
        print("[C# 单测 · ChatFrameQueueTests]")
        for name, path, old, new, _hint in CS_MUTATIONS:
            ok &= _check(name, path, [(old, new)], "", [cs_cmd])
    if only in ("", "py"):
        print("[Python 契约 · test_chat_frame_queue_contract.py]")
        for name, path, repls, hint in PY_MUTATIONS:
            ok &= _check(name, path, repls, hint, [py_cmd])

    print()
    print("全部变异都被捕获" if ok else "★ 有变异未被捕获 —— 补测试或修实现")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
