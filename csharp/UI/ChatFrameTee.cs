// 聊天帧的"分流器"（ChatFrameTee）—— 常驻静态订阅者：只在"没人显示这个会话"时把帧收进
// `ChatFrameQueue`（关窗期间，以及正显示**别的**群 / 单聊时）。与 `ContactDuty` 同一种做法
// （静态 `Attach` + `UiEvent` 订阅，与面板实例解耦）。
//
// 为什么不在 WsClient（传输层）拦：`UiEvent` 是**多播**（ContactDuty 的未读红点等
// 关窗期间必须继续工作），在这层拦帧会连累它们。
// 为什么不用 ChatPresenter 实例字段：面板被销毁（`CloseViaManager` → `OnDestroy`）时
// presenter 一起销毁，队列跟着没 —— 而"关窗"正是要缓冲的场景。
//
// ── 分流（09-17 统一：一个 tee 服务两个世界）────────────────────────────────
//   · `room_id` 非空 ⇒ **群帧**：白名单 room_say / room_state / text_delta / step；
//     `AbChatPanel.IsShowingRoom(roomId)` 为真 ⇒ 不收（presenter 正演它）。
//   · `room_id` 空 + `npc_id` 非空 ⇒ **单聊帧**：白名单 text_delta / step；
//     ★ **与"谁在显示"无关，一律收**（09-17 二次改造，见下）。
//   · 例外：单聊 `npc_reply` —— 段闭合信号，无论有没有人显示都送队列。
//   · `step` 按 kind 过滤：tool_call / tool_result / think / text 收；sys / ctx 不收
//     （输入侧折叠"只实时不重放"，与历史重放口径一致）。
//
// ── 单聊为什么改成"始终记"（09-17 二次改造，勿改回）─────────────────────────
// 初版按"有人正显示这个 NPC ⇒ 不收（presenter 会演）"来分流。实机暴露两条**补不回来**的洞：
//   ① 玩家只能在窗口开着时发消息、工具与思考也大多在关窗之前 ⇒ 这些帧全被丢掉，
//      重开窗的"重演"里没有它们，`open_chat` 的重放又只含**已闭合**回合 ⇒
//      屏幕上是"后半截流式正文 + 没有我的输入 + 没有行动记录"（用户 09-17 实机报告）；
//   ② 那个判据本身还是个**静默丢帧**开关：`IsShowingNpc` 一旦因为面板没走关闭契约而残留为真，
//      帧就永远只进直播、不进队列，事后完全无迹可查。
// 现在的分工：**记录**归 tee（始终记，与显示无关），**谁负责画**归 `ChatPresenter` 的渲染闸
// （开窗请求发出前置位 ⇒ 直播不画 ⇒ 回执里整段重演 ⇒ 落位 ⇒ 之后直播续写）。
// 这样"关窗前的半截"能补回来，而同一帧也**不可能**被画两遍（闸门期间只有一个绘制方）。
// 群帧仍保留 `IsShowingRoom` 判据：群聊有 baseline + `room_say` 终稿覆盖两套去重，
// 且群窗重放读的是 `Room.log`（与单聊的账本口径不同），不在本次改造范围内。
//
// ★ 判据异常一律当"没显示"（收）：多收的帧由重放基线 / 段闭合兜住，而误判"在显示"
//   会让帧真正丢失 —— 与快捷键屏蔽（宁可漏屏蔽，不可永久屏蔽）的失败姿态**恰好相反**。
//
// 生命周期：`Attach` 在 ModMain 建连后调用（幂等；**WsClient 实例换了就换绑** —— Init
// 每次进世界都会 new 新实例，见 `Attach` 注释）；桶的清理在 `ChatPresenter`
// （消费完成 / 群解散 / 直播优先让位）与 `ModMain.OnIntoWorldEvent`（换存档，与
// `PortraitCache.ClearNameIndex` 并列）。断线重连**不清**（正是要缓冲的场景）——
// 重连后靠 `open_room` / `open_chat` 重放 + 基线 / 段判决收敛。

using System;
using Newtonsoft.Json.Linq;

namespace AgentLoopBridge
{
    internal static class ChatFrameTee
    {
        private static WsClient _ws;
        private static bool _attached;

        /// <summary>Python 连接建立后调用（重复调用安全）。
        ///
        /// ★ 同一实例重复 Attach 直接跳过（幂等）；**实例变了就换绑**：先从旧实例摘掉订阅、
        ///   再挂到新实例上。背景：ModMain.Init 每次进世界 / 读档都会重跑并 **new 一个新的
        ///   WsClient**，初版的 `if (_attached) return;` 会让本类永远订阅在**第一个已废弃的
        ///   实例**上 —— tee 整场失聪：`npc_reply` 的清段信号送不到（残留段在重开窗被整段
        ///   重演成重复的玩家气泡 + 卡死的「NPC 回应中」），关窗期间的帧也一帧都收不到。
        ///   2026-09-21 实机日志钉死（Init 跑了 3 次、已附着只出现 1 次、重演段里只有
        ///   C# 本地的 user_say）。与 `ContactDuty.Attach` 同规约（同一批修）。</summary>
        public static void Attach(WsClient ws)
        {
            if (ws == null || (_attached && ws == _ws)) return;
            if (_attached && _ws != null) _ws.UiEvent -= OnUiEvent;   // 换实例：先从旧的摘下来
            _ws = ws;
            _ws.UiEvent += OnUiEvent;
            _attached = true;
            ModMain.P("[ChatFrameTee] 已附着（关窗期间的群 / 单聊帧缓冲）");
        }

        private static void OnUiEvent(JObject payload)
        {
            try
            {
                if (payload == null) return;
                string ev = (string)payload["event"] ?? "";
                string roomId = (string)payload["room_id"] ?? "";
                if (roomId.Length > 0)
                {
                    // ---- 群帧：有 room_id ----
                    if (ev != "room_say" && ev != "room_state" && ev != "text_delta" && ev != "step") return;
                    if (ev == "step" && !ChatFrameQueue.IsPlayableStepKind((string)payload["kind"] ?? "")) return;
                    if (IsShowingRoom(roomId)) return;    // 有人正显示这个群 ⇒ presenter 会演，别缓冲
                    ChatFrameQueue.OfferRoom(roomId, Decode(ev, payload));
                    return;
                }
                // ---- 单聊帧：无 room_id（单聊帧天然不带它）----
                string npcId = (string)payload["npc_id"] ?? "";
                if (npcId.Length == 0) return;
                if (ev == "npc_reply")
                {
                    // 段闭合信号：与"谁在显示"无关（见文件头"例外"）。显示中到达时
                    // presenter 会正常收口，队列这一侧只需把段同步清掉。
                    ChatFrameQueue.OfferSolo(npcId, new ChatFrame { Event = ev });
                    return;
                }
                if (ev != "text_delta" && ev != "step") return;
                // step 的 kind 白名单（sys/ctx 只实时不重放）与"谁在显示"无关，判据同源：
                // `ChatFrameQueue.IsSoloBuffered` 同时被 `ChatPresenter` 的渲染闸拿来决定
                // "闸门期间吞不吞这一帧"（两处必须字面同源，见那个方法的注释）。
                if (!ChatFrameQueue.IsSoloBuffered(ev, (string)payload["kind"] ?? "")) return;
                // ★ 这里**故意没有**"有人正显示 ⇒ 不收"的判据（09-17 二次改造，见文件头）：
                //   单聊帧一律记，"谁负责画"由 `ChatPresenter` 的渲染闸决定。
                ChatFrameQueue.OfferSolo(npcId, Decode(ev, payload));
            }
            catch (Exception e)
            {
                try { ModMain.P("[ChatFrameTee] 收帧失败（已忽略）：" + e.Message); } catch { }
            }
        }

        /// <summary>JObject → 强类型帧（字段名与 Python 侧 `_emit_say` / `_emit_state` /
        /// `_run_turn` 的 step 钩子一一对应 —— 跨语言漂移是**完全静默**的，有契约测试钉）。</summary>
        private static ChatFrame Decode(string ev, JObject p)
        {
            var f = new ChatFrame { Event = ev, Seq = -1, Turn = -1 };
            if (ev == "room_say")
            {
                f.Seq = p["seq"]?.Value<int?>() ?? -1;
                f.Uid = (string)p["uid"] ?? "";
                f.Speaker = (string)p["speaker"] ?? "";
                f.SpeakerId = (string)p["speaker_id"] ?? "";
                f.Text = (string)p["text"] ?? "";
                // 旁白（第三人称叙述行）：渲染成「行动记录」行而不是气泡，见 ChatFrame.Narration
                f.Narration = p["narration"]?.Value<bool>() ?? false;
                // 玩家原文（含 @名字）：回声销账要用它，见 ChatFrame.EchoText
                f.EchoText = (string)p["echo_text"] ?? "";
            }
            else if (ev == "room_state")
            {
                f.Seq = p["last_seq"]?.Value<int?>() ?? -1;
                f.Phase = (string)p["phase"] ?? "";
                f.Note = (string)p["text"] ?? "";
                var sp = p["speaking"] as JArray;
                if (sp != null && sp.Count > 0)
                {
                    var arr = new string[sp.Count];
                    for (int i = 0; i < sp.Count; i++) arr[i] = (string)sp[i] ?? "";
                    f.Speaking = arr;
                }
                // 与 `speaking` 同序同长的 id 名单（老 Python 不发 ⇒ 留 null）
                var si = p["speaking_ids"] as JArray;
                if (si != null && si.Count > 0)
                {
                    var arr = new string[si.Count];
                    for (int i = 0; i < si.Count; i++) arr[i] = (string)si[i] ?? "";
                    f.SpeakingIds = arr;
                }
            }
            else if (ev == "step")
            {
                f.Turn = p["turn"]?.Value<int?>() ?? -1;
                f.Kind = (string)p["kind"] ?? "";
                f.Name = (string)p["name"] ?? "";                  // tool_call 的工具名
                f.Args = p["args"]?.ToString() ?? "";              // 与直播路径 `payload["args"]?.ToString()` 逐字一致
                f.Text = p["text"]?.ToString() ?? "";
                f.IsError = p["isError"]?.Value<bool>() ?? false;  // Python 现无此键 → 恒 false（保真转发）
            }
            else // text_delta
            {
                f.Text = p["text"]?.ToString() ?? "";
                f.Part = p["part"]?.ToString() ?? "body";
            }
            return f;
        }

        /// <summary>这个群是否**正被显示**（见文件头"分流"）。</summary>
        private static bool IsShowingRoom(string roomId)
        {
#if AB_UI
            return AbChatPanel.IsShowingRoom(roomId);
#else
            try
            {
                var w = ModMain.ChatWindowInstance;      // 代码模式的窗口（AB 模式下恒 null）
                return w != null && w.IsOpen && w.IsRoomMode && w.CurrentRoomId == roomId;
            }
            catch { return false; }
#endif
        }

    }
}
