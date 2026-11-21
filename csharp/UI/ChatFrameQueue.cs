// 聊天帧缓冲队列（ChatFrameQueue）—— "**当前这个会话还没落幕的那一段**"先存这里，重开窗、
// 重放落地之后再消费。09-17 由 `RoomFrameQueue` 统一而来：**一个类服务两个世界**。
//
//   · 群聊（键 = room_id）：按轮组织（`room_state` speak/end），baseline（重放 items 最大 seq）判决；
//   · 单聊（键 = npc_id）：整轮记录（`user_say` 开段、`npc_reply` 闭段），重开窗整段重演。
//
// ── 为什么需要（两个世界同一件事）────────────────────────────────────────────
// 玩家在群 / 单聊里说一句就关窗（去打架 / 换界面），Python 侧那一轮照跑；帧到达时窗口关着
// → 被 `!IsOpen` 守卫丢掉。重开窗的 `open_room` / `open_chat` 重放能把**说完的话**补上，
// 补不回来的是两样：① **正在说的那半截**（`text_delta`）；② **在跑什么 / 谁在说**
// （群聊的 speak 状态、两边的工具折叠与思考整段）。
// ★ 单聊 09-17 二次改造后连"**关窗之前**"的那部分也记（含玩家自己的输入）—— 见下。
//
// ── 群聊侧两条不可动摇的规则（原有，未动）──────────────────────────────────
// ① **按轮组织**：`room_state(phase="speak")` 开轮、`phase="end"` 闭轮；`text_delta` / `step`
//    没有 seq、无法被单独判决，所以它们**必须跟着它那一轮**同留同丢。否则会出现"delta 留下、
//    它的 end 被丢"，重放时 `AppendDelta` 会凭空建泡（`ChatWindow.AppendDelta` 的防御分支）
//    → 活跃泡永久泄漏 → 删除/压缩入口被 `HasActiveBubble` 守卫挡死（功能性事故，不只是观感）。
// ② **只裁 delta**：`room_say` / `room_state` / `step` 是低频关键帧，全保留 —— 丢 `end` 会让
//    忙碌态卡死；丢 `say` 会让内容缺口；丢 `step` 会让工具过程缺口。delta 裁掉无害
//    （终稿 `room_say` 会覆盖那半截）。
//
// ── 单聊侧：**整轮记录**（09-17 二次改造）──────────────────────────────────
// 段 = "**当前这一轮的全过程**"，从玩家开口（`user_say`）一直记到 `npc_reply`。为什么不再
// 只记"关窗期间"的帧（初版的做法）：
//   · 玩家只能在窗口**开着**时发消息、工具与思考也大多发生在关窗之前；初版按"窗口在显示
//     就不收"的规则把这些帧全丢了 ⇒ 重开窗后**玩家的输入和关窗前的工具行永远补不回来**
//     （实机：重开窗只剩后半截流式正文，用户报"没重放我的输入 / 行动记录也没了"）。
//   · 于是 tee 改成**始终记**（与是否显示无关），"重开窗那段谁来画"交给 `ChatPresenter`
//     的**渲染闸**：开窗时直播先不画，回执落地时整段重演，然后开闸续写 —— 队列是那段时间
//     唯一的帧来源 ⇒ 不存在"直播画过一遍、重演又画一遍"的重叠，也就不需要合并去重。
// 边界规则（一条都不许省）：
//   · `npc_reply` 到达 ⇒ 段作废清桶（账本已闭合，`open_chat` 重放必含该回合 ⇒ 留着只会
//     重演成重复；工具折叠不归 BubbleCommitPolicy 管，重复看得见），同时推进 `ClosedEpoch`；
//   · `step` 的账本回合号变大 ⇒ 旧段隐式作废（上一个回合早已结束、它的 `npc_reply` 该到过；
//     不清的话"npc_reply 丢帧"的死段会积到下一次开窗被重演成幽灵泡），同样推进代数；
//   · `user_say`（**C# 本地**产生的玩家发言帧）⇒ 段重置（玩家开口 = 新一段的第一帧）；
//   · delta **按 part 合并**（段内至多两条流式文本：body / think），不再逐帧堆 ——
//     逐帧列表一旦记满整轮，限流删的又是**最旧的** delta ⇒ 重开窗看到的是**被砍掉开头的**
//     正文；合并后无损且有界，重演时一条 `ApplyDelta(整段)` 与逐帧重演的终态逐字节相同。
//   · 段有帧数绝对上限（防无界），超了整段清掉。
//
// ── 消费侧（`ChatPresenter`）──────────────────────────────────────────────
// 群：`DrainRoomFrames`（`ApplyOpenRoom` 里、`ReplaceHistory` 与 `SetBusy(false)` 之后）。
// 单聊：`DrainSoloFrames`（`ChatPresenter.SendOpenChatReplay` 回执里、`ReplaceHistory` 之后）；
// "直播优先"的欠账分支不消费并 `ForgetSolo` 丢段（段已被直播续写或已收口，留着 = 过期段）。
// ★ 单聊还有一道**渲染闸**（`ChatPresenter._replayGate`）：开窗请求发出前置位，回执里
//   消费完才落位。闸门期间直播不画帧 —— 单聊帧现在"始终记"，不掐掉直播就会一帧两画。
//   闸门只吞**会进段的那几类**帧，判据与 tee 的收帧白名单同源（`ChatFrameQueue.IsSoloBuffered`）：
//   队列不收的（`stats_update` / `compact_result` / `step(sys|ctx)`）照常走直播 ——
//   否则那一帧两边都没有（既不直播也不重演），且 `compact_result` 还是"正在压缩中"那行
//   唯一的收尾信号。详见 `IsSoloBuffered` 的注释。
//
// 本文件刻意保持**零 Unity / 零 Newtonsoft 依赖**（帧是解构后的强类型 struct）：
// 由 `csharp/tests/ChatFrameQueueTests` 链接同一份源码直接跑断言（照 BubbleCommitPolicy 的先例）。
// JObject → ChatFrame 的解构在 `ChatFrameTee.cs`（那个文件依赖 Unity 侧类型，不进单测工程）。

using System.Collections.Generic;

namespace AgentLoopBridge
{
    /// <summary>一帧聊天事件的**强类型快照**（由 `ChatFrameTee` 从 JObject 解构而来）。</summary>
    internal struct ChatFrame
    {
        /// <summary>"room_say" / "room_state" / "text_delta" / "step" / "npc_reply"（仅作单聊段闭合信号）
        /// / "user_say"（**C# 本地**产生的玩家发言帧，单聊专用：Python 侧单聊不回回声帧）。</summary>
        public string Event;
        /// <summary>`room_say.seq` / `room_state.last_seq`；delta / step 恒 -1（它们本来就没有 seq）。</summary>
        public int Seq;
        /// <summary>所属轮（0 = 轮外散帧）。判决与裁剪都以它为单位。单聊不用。</summary>
        public int Round;
        public string Uid;
        public string Speaker;
        /// <summary>`room_say.speaker_id` —— 说话人的**精确 unitID**（Python 从
        /// `Room.member_unit_ids` 查出来的）。有它，这一行的头像直接取图，不必按名解析
        /// （重名时按名会画上别人，且**不报错、不缺字段**）。空 = 老 Python / 身份未知，
        /// 消费侧回落 `PortraitCache.IdByName`。</summary>
        public string SpeakerId;
        /// <summary>`room_say.narration` —— 这条是**旁白**（第三人称叙述行，如切磋那条
        /// 「（与唐炎切磋了一场 —— …）」），不是谁开口说的话：渲染成「行动记录」行，
        /// **不画气泡**。老 Python 不发 ⇒ false（行为与今天一致）。
        /// 机理见 `Room.Utterance.narration`：它进 `Room.log`（成员经 `deliver` 读到）
        /// 也进群窗，但以"工具返回结果"那个形态出现 —— 那条旁白里夹着玩家的原话，
        /// 画成某人的气泡挂谁名下都不对（09-22 用户报）。</summary>
        public bool Narration;
        /// <summary>`room_say.echo_text` —— 玩家发言的**原文**（含 `@名字`）。
        ///
        /// 为什么需要：`text` 是 Python 剥掉 `@名字` 之后的正文，而 C# 本地那条气泡
        /// 画的是玩家敲进去的原文 ⇒ 拿 `text` 对号**永远对不上**，同一条消息会在群窗
        /// 出现两遍（11-21 用户截图：`@姜萌 你在干什么` + `你在干什么`）。
        /// 空 = 老 Python / 非玩家发言 / 原文与正文相同。
        /// ★ 这条对"关窗期间缓冲、重开时重放"同样关键 ★：不存进帧里，
        ///   重放那条回声就又会画一遍（本地气泡在关窗前已经画过）。
        /// </summary>
        public string EchoText;
        /// <summary>`room_say` 正文 / `user_say` 的玩家原话（含时间戳与 `[图片：xx]` 占位 ——
        /// 存的是**账本串**；上屏时由渲染口 `ChatWindow.AppendUserMessage` 统一剥行首戳
        /// （10-22），重演与实时、与回放因此三者观感一致）/ `step` 的正文。
        ///
        /// ★ `text_delta` 这条路**两个阶段**，别混：
        ///   · `ChatFrameTee.Decode` 装进来的是 Python 刚发的那**一小段增量**（逐 token，
        ///     `ws_channel._on_token` → `send_event("text_delta", text=tok)`）；
        ///   · 进段时由 `AppendDeltaText` 按 part 合并成"**到目前为止的全文**"（累计）。
        ///   所以"取出来当增量逐条 append"只在合并前成立；段里取出来的一条就是整段全文，
        ///   消费侧一条 `ApplyDelta` 画完（`ChatWindow.AppendDelta` 也是 append 语义 ——
        ///   把累计全文当增量再 append 一次就会翻倍）。</summary>
        public string Text;
        /// <summary>`room_state` 专用：speak / end。</summary>
        public string Phase;
        /// <summary>`room_state(phase="speak")` 的说话人名单（消费只用第一个）。</summary>
        public string[] Speaking;
        /// <summary>`room_state(phase="speak")` 的说话人 unitID 名单（δ 与 `Speaking` 同序同长；
        /// 老 Python 不发 ⇒ null ⇒ 消费侧回落按名）。见 `SpeakerId`。</summary>
        public string[] SpeakingIds;
        /// <summary>`room_state` 终帧的提示文案（"这一轮为什么没产出"）。</summary>
        public string Note;
        /// <summary>`text_delta` 专用：body / think。</summary>
        public string Part;
        // ---- step 帧（群与单聊共用；单聊还拿 Turn 做"段回合号"/ 隐式闭合）----
        /// <summary>`step.turn`（账本回合号，单调递增）。</summary>
        public int Turn;
        /// <summary>`step.kind`：tool_call / tool_result / think / text（sys/ctx 在 tee 就被滤掉）。</summary>
        public string Kind;
        /// <summary>`tool_call.name`（工具名）。</summary>
        public string Name;
        /// <summary>`tool_call.args`（JObject.ToString()，与直播路径逐字一致）。</summary>
        public string Args;
        /// <summary>`tool_result.isError`（Python 目前不发这个键 ⇒ 恒 false；保真转发，将来补上即自动生效）。</summary>
        public bool IsError;
    }

    internal static class ChatFrameQueue
    {
        // 上限都是"防无界"的兜底（正常一轮远小于此；实机若频繁触发会有日志）。
        internal const int MaxClosedRounds = 4;      // 群：保留最后 N 个**已闭合**轮（超出丢最旧整轮）
        internal const int MaxOpenDeltas = 400;      // 群：未闭合轮里的 delta 上限（只裁 delta，不碰 speak/say/step）
        internal const int MaxLooseFrames = 200;     // 群：轮外散帧上限（玩家回声等）
        internal const int MaxTotalFrames = 2000;    // 群：单桶绝对上限：超了整个桶清掉（最后的安全阀）
        internal const int MaxSoloTextChars = 200000; // 单聊：段内流式文本合并后的字符上限（防无界）
        internal const int MaxSoloFrames = 2000;      // 单聊：段绝对上限：超了整段清掉（最后的安全阀）

        // ==================================================================
        // 群聊桶（键 = room_id）
        // ==================================================================

        private sealed class Bucket
        {
            public readonly List<ChatFrame> Frames = new List<ChatFrame>();
            public int NextRound = 1;
            public int OpenRound;                                            // 0 = 没有未闭合轮
            public readonly Dictionary<int, int> EndSeq = new Dictionary<int, int>();
            public readonly List<int> ClosedOrder = new List<int>();
        }

        private static readonly Dictionary<string, Bucket> _rooms = new Dictionary<string, Bucket>();

        /// <summary>收帧（幂等安全；不识别的 event / 空 roomId 返回 false）。</summary>
        internal static bool OfferRoom(string roomId, ChatFrame f)
        {
            if (string.IsNullOrEmpty(roomId)) return false;
            if (f.Event != "room_say" && f.Event != "room_state" && f.Event != "text_delta"
                && f.Event != "step") return false;

            Bucket b;
            if (!_rooms.TryGetValue(roomId, out b))
            {
                b = new Bucket();
                _rooms[roomId] = b;
            }

            if (f.Event == "text_delta" || f.Event == "step")
            {
                // ★ 没有归属轮的 delta / step 一律不收：重放它只会在屏幕上留一条没人收口的气泡 /
                //   一个没有回合的折叠（连它的 speak 都没收到，说明这条时序已经不可复原）。
                if (b.OpenRound == 0) return false;
                f.Round = b.OpenRound;
                b.Frames.Add(f);
                TrimOpenDeltas(b);
            }
            else if (f.Event == "room_state" && f.Phase == "speak")
            {
                // 上一个 speak 没等到 end（异常/丢帧）：按"隐式闭合"处理 —— EndSeq 记 0，
                // 消费时 `0 <= baseline` 恒真 ⇒ 整轮被丢。保守但安全：没有终帧的轮重放出来
                // 会把忙碌态卡住（那正是 `end` 的职责）。
                CloseOpenRound(b, 0);
                f.Round = b.NextRound++;
                b.OpenRound = f.Round;
                b.Frames.Add(f);
            }
            else if (f.Event == "room_state" && f.Phase == "end")
            {
                if (b.OpenRound != 0)
                {
                    f.Round = b.OpenRound;
                    b.Frames.Add(f);
                    b.EndSeq[f.Round] = f.Seq;
                    b.ClosedOrder.Add(f.Round);
                    b.OpenRound = 0;
                }
                else
                {
                    b.Frames.Add(f);      // 散帧（无对应 speak）：按 Seq 判决
                }
            }
            else // room_say / 其它 phase 的 room_state
            {
                if (f.Event == "room_say" && b.OpenRound != 0) f.Round = b.OpenRound;
                b.Frames.Add(f);
            }

            TrimClosedRounds(b);
            TrimLoose(b);
            if (b.Frames.Count > MaxTotalFrames)
            {
                // 最后的安全阀：正常永远到不了（每轮帧数是两位数、delta 已单独限流）。
                b.Frames.Clear();
                b.EndSeq.Clear();
                b.ClosedOrder.Clear();
                b.OpenRound = 0;
                b.NextRound = 1;
            }
            return true;
        }

        /// <summary>取该群桶里**该重演的帧**（保持原始顺序；只读，不删）。
        ///
        /// `baselineSeq` = 重放历史 items 的最大 seq。判决：
        ///   · 已闭合轮：`endSeq <= baselineSeq` ⇒ 整轮丢（屏幕已由重放呈现）；
        ///   · 未闭合轮：恒保留（"正在说的"就是它）；
        ///   · 散帧：`seq > 0 && seq <= baselineSeq` ⇒ 丢（含玩家回声）。</summary>
        internal static List<ChatFrame> TakeRoom(string roomId, int baselineSeq)
        {
            var outp = new List<ChatFrame>();
            Bucket b;
            if (string.IsNullOrEmpty(roomId) || !_rooms.TryGetValue(roomId, out b)) return outp;
            // ★ 未闭合段里"**已经落进日志**的 room_say" ⇒ 整段都算被回放覆盖（09-17 复核修）。
            //
            //   背景：未闭合的段不受基线约束是**有意为之** —— 关窗时正在流的那半截必须补上。
            //   但同一段里可能已经有一条 `room_say` 落了账（说话人说完了、这一轮还在往下跑：
            //   `say` 是"落账 + 广播"，而终帧 `end` 要等整轮结束），而回放（`ReplaceHistory`）
            //   读的正是 `Room.log` ⇒ 那条已经画在屏幕上了。整段照搬就会**同一条出现两遍**。
            //   判据：段内存在 `0 < seq <= baselineSeq` 的 room_say ⇒ 这一段的正文在日志里，
            //   丢掉整段（含它的 speak/delta，否则会留下一条**永远等不到定稿**的半截泡）。
            //   流式中的段（还没有 room_say）照旧保留。
            var loggedRounds = new HashSet<int>();
            foreach (var f in b.Frames)
            {
                if (f.Round != 0 && f.Event == "room_say" && f.Seq > 0 && f.Seq <= baselineSeq)
                    loggedRounds.Add(f.Round);
            }
            foreach (var f in b.Frames)
            {
                if (f.Round != 0)
                {
                    int endSeq;
                    if (b.EndSeq.TryGetValue(f.Round, out endSeq) && endSeq <= baselineSeq) continue;
                    if (loggedRounds.Contains(f.Round)) continue;
                    outp.Add(f);
                }
                else
                {
                    if (f.Seq > 0 && f.Seq <= baselineSeq) continue;
                    outp.Add(f);
                }
            }
            return outp;
        }

        /// <summary>丢弃该群的桶（消费完成 / 群解散时调）。</summary>
        internal static void ForgetRoom(string roomId)
        {
            if (!string.IsNullOrEmpty(roomId)) _rooms.Remove(roomId);
        }

        /// <summary>群桶内帧数（日志 / 单测用）。</summary>
        internal static int CountOfRoom(string roomId)
        {
            Bucket b;
            if (string.IsNullOrEmpty(roomId) || !_rooms.TryGetValue(roomId, out b)) return 0;
            return b.Frames.Count;
        }

        // ==================================================================
        // 单聊桶（键 = npc_id）：整轮记录（段 = 当前这一轮的全过程）
        // ==================================================================

        private sealed class SoloBucket
        {
            public readonly List<ChatFrame> Segment = new List<ChatFrame>();
            /// <summary>段里见过的最大账本回合号（0 = 还没见过带 turn 的 step）。</summary>
            public int SegmentTurn;
            /// <summary>段闭合代数：**每有一次回合边界把段作废就 +1**（npc_reply / 隐式闭合 /
            /// 安全阀清段）。用途见 `SoloEpoch`。</summary>
            public int ClosedEpoch;
        }

        private static readonly Dictionary<string, SoloBucket> _solos = new Dictionary<string, SoloBucket>();

        /// <summary>收一帧单聊帧。三种来路（返回是否真的进段，日志 / 单测用）：
        ///   · `npc_reply` —— 段闭合信号：作废整段（自身不入段，账本已闭合 ⇒ 重放必含该回合）；
        ///   · `user_say`  —— 玩家发言（**C# 本地产生**）：段重置，它是新一段的第一帧；
        ///   · `text_delta` / `step` —— 进段（delta 按 part 合并，见 `AppendDeltaText`）。
        ///
        /// ★ 幂等 / 空桶安全：清一个不存在或已空的段同样合法（显示中的 `npc_reply` 也走
        ///   这里 —— 段该清就得清，与"谁在显示"无关）。</summary>
        internal static bool OfferSolo(string npcId, ChatFrame f)
        {
            if (string.IsNullOrEmpty(npcId)) return false;
            if (f.Event == "npc_reply")
            {
                // 段闭合：npc_reply 发出 ⇒ 账本已闭合（`_run_turn` 先落账后发帧）⇒ `open_chat`
                // 重放必含该回合 ⇒ 这段没有重演价值（重演 = 重复，工具折叠不受去重管辖）。
                SoloBucket sb;
                if (_solos.TryGetValue(npcId, out sb)) InvalidateSegment(sb);
                return false;
            }
            if (f.Event == "user_say")
            {
                // 玩家开口 ⇒ 上一段（若有）无论收口没收到 `npc_reply` 都不再属于这一轮。
                // ★ 同时把 `SegmentTurn` 归零：否则新回合第一条 step（账本回合号必然变大）
                //   会被"隐式闭合"判成旧段推进，把刚记下的这句话连带清掉 —— 那种丢法只会在
                //   "上一轮 npc_reply 丢帧"时出现，静默且必现。
                SoloBucket b0;
                if (!_solos.TryGetValue(npcId, out b0))
                {
                    b0 = new SoloBucket();
                    _solos[npcId] = b0;
                }
                ClearSegment(b0);
                b0.Segment.Add(f);
                TrimSolo(b0);
                return true;
            }
            if (f.Event != "text_delta" && f.Event != "step") return false;

            SoloBucket b;
            if (!_solos.TryGetValue(npcId, out b))
            {
                b = new SoloBucket();
                _solos[npcId] = b;
            }

            if (f.Event == "step" && f.Turn > 0)
            {
                // 隐式闭合：账本回合号**变大** ⇒ 旧段属于上一个回合（它早已结束、npc_reply 该到过；
                // 没到 = 丢帧）⇒ 旧段不可能再续。不清的话"死段"会积到下一次开窗被重演成幽灵泡。
                if (b.SegmentTurn > 0 && f.Turn > b.SegmentTurn) InvalidateSegment(b);
                if (f.Turn > b.SegmentTurn) b.SegmentTurn = f.Turn;
            }
            if (f.Event == "text_delta") { AppendDeltaText(b, f); return true; }
            b.Segment.Add(f);
            TrimSolo(b);
            return true;
        }

        /// <summary>delta 合并进段：段尾就是**同一个 part** 的 delta ⇒ 追加到那一条的文本上
        /// （段内至多两条流式文本：body / think），否则新开一条。
        ///
        /// 为什么要合并：段落改成"整轮记录"之后，逐帧列表会被 `TrimSolo` 从**最旧**那头裁
        /// （初版只记关窗期间的几十帧，看不出来）⇒ 重开窗看到的是**被砍掉开头**的正文。
        /// 合并后无损且有界；重演时一条 `ApplyDelta(整段)` 与逐帧重演的终态逐字节相同。
        /// `ChatFrame` 是 struct —— 改完必须写回表里（否则改的是副本，这是本文件最容易踩的坑）。</summary>
        private static void AppendDeltaText(SoloBucket b, ChatFrame f)
        {
            string part = string.IsNullOrEmpty(f.Part) ? "body" : f.Part;
            string add = f.Text ?? "";
            if (b.Segment.Count > 0)
            {
                ChatFrame last = b.Segment[b.Segment.Count - 1];
                string lastPart = string.IsNullOrEmpty(last.Part) ? "body" : last.Part;
                if (last.Event == "text_delta" && lastPart == part)
                {
                    string merged = (last.Text ?? "") + add;
                    if (merged.Length > MaxSoloTextChars) merged = merged.Substring(0, MaxSoloTextChars);
                    last.Text = merged;
                    last.Part = part;
                    b.Segment[b.Segment.Count - 1] = last;   // struct：写回
                    return;
                }
            }
            if (add.Length > MaxSoloTextChars) f.Text = add.Substring(0, MaxSoloTextChars);
            f.Part = part;
            b.Segment.Add(f);
        }

        /// <summary>清段（不动代数）：`user_say` 开新段时用 —— 那只是"这一轮重新开始"，
        /// 不是"有一个回合收口了"，消费侧不该因此补请求。</summary>
        private static void ClearSegment(SoloBucket b)
        {
            b.Segment.Clear();
            b.SegmentTurn = 0;
        }

        /// <summary>作废段（**推进代数**）：回合边界（`npc_reply` / 隐式闭合）与安全阀清段走这条。
        /// 代数变了 = "自你上次问账本以来，有回合收口了" —— 见 `SoloEpoch`。</summary>
        private static void InvalidateSegment(SoloBucket b)
        {
            ClearSegment(b);
            b.ClosedEpoch++;
        }

        /// <summary>取该单聊桶的**段闭合代数**。用途只有一个：`open_chat` 回的是**请求时刻**的
        /// 账本快照 —— 若某一轮恰好在"请求发出 ~ 回执到达"之间收口，那份快照里没有它，而段
        /// 又被 `npc_reply` 刚清掉 ⇒ 两边都够不着（重开窗缺一整轮，且没有任何日志痕迹）。
        /// 消费侧在发请求前记一次代数、回执到达时再读一次，**变了就补一次请求**。
        ///
        /// 桶不存在返回 0（等价于"还没有任何回合收口过"）。</summary>
        internal static int SoloEpoch(string npcId)
        {
            SoloBucket b;
            if (string.IsNullOrEmpty(npcId) || !_solos.TryGetValue(npcId, out b)) return 0;
            return b.ClosedEpoch;
        }

        /// <summary>取该单聊桶里"还在说"的那一段（保持原始顺序；只读，不删）。
        /// 段不存在返回空表 —— 调用方按"没有要重演的"处理。</summary>
        internal static List<ChatFrame> TakeSolo(string npcId)
        {
            var outp = new List<ChatFrame>();
            SoloBucket b;
            if (string.IsNullOrEmpty(npcId) || !_solos.TryGetValue(npcId, out b)) return outp;
            outp.AddRange(b.Segment);
            return outp;
        }

        /// <summary>丢弃该单聊桶（消费完成 / "直播优先"让位时调 —— 见文件头收帧规则）。</summary>
        internal static void ForgetSolo(string npcId)
        {
            if (!string.IsNullOrEmpty(npcId)) _solos.Remove(npcId);
        }

        /// <summary>单聊桶内帧数（日志 / 单测用）。</summary>
        internal static int CountOfSolo(string npcId)
        {
            SoloBucket b;
            if (string.IsNullOrEmpty(npcId) || !_solos.TryGetValue(npcId, out b)) return 0;
            return b.Segment.Count;
        }

        /// <summary>`step` 帧里**可重放**的 kind（输入侧折叠 sys/ctx 只实时不重放 ——
        /// 与 `project_ui_history` 不投影它们同口径）。</summary>
        internal static bool IsPlayableStepKind(string kind)
        {
            return kind == "tool_call" || kind == "tool_result" || kind == "think" || kind == "text";
        }

        /// <summary>这个单聊帧会不会**进段**（`ChatFrameTee` 的收帧白名单）。
        ///
        /// ★ 唯一的判据来源：tee 的"收哪些帧"与 `ChatPresenter._replayGate` 的"闸门吞哪些帧"
        ///   **共用本方法**，两处必须字面同源。理由是两边各自都只有一半信息：
        ///   · 闸门期间直播**不画**任何帧，前提是"队列会替它画回来"；
        ///   · 一旦闸门吞掉一个队列根本没收的帧（`stats_update` / `compact_result` /
        ///     `step(sys|ctx)`），那一帧就**两边都没有** —— 既不直播也不重演。代价不只是少一行：
        ///     `compact_result` 是"正在压缩中"那一行**唯一**的收尾信号，吞掉它 = 提示行一直
        ///     停在"已 Ns"直到看门狗误报"长时间无响应"（而压缩其实成功了），且零日志。
        ///   · 反过来，闸门**少吞**一个已记帧 = 同一帧画两遍（重演一次 + 直播一次）。
        ///   ⇒ 改这里就是同时改两处；加新帧类型时也只有这一个地方要动
        ///     （`ChatPresenter` 那条 `IsSoloBuffered` 判据 + 契约测试会一起红）。</summary>
        internal static bool IsSoloBuffered(string ev, string kind)
        {
            if (ev == "npc_reply" || ev == "text_delta") return true;
            if (ev == "step") return IsPlayableStepKind(kind);
            return false;
        }

        // ==================================================================
        // 生命周期
        // ==================================================================

        /// <summary>清空全部（换存档 / 读档时调：旧世界的帧不该灌进新档）。</summary>
        internal static void ClearAll()
        {
            _rooms.Clear();
            _solos.Clear();
        }

        // ------------------------------------------------------------------
        // 内部：轮闭合 / 裁剪
        // ------------------------------------------------------------------

        private static void CloseOpenRound(Bucket b, int endSeq)
        {
            if (b.OpenRound == 0) return;
            b.EndSeq[b.OpenRound] = endSeq;
            b.ClosedOrder.Add(b.OpenRound);
            b.OpenRound = 0;
        }

        private static void TrimClosedRounds(Bucket b)
        {
            while (b.ClosedOrder.Count > MaxClosedRounds)
            {
                int rid = b.ClosedOrder[0];
                b.ClosedOrder.RemoveAt(0);
                b.EndSeq.Remove(rid);
                b.Frames.RemoveAll(f => f.Round == rid);   // 整轮丢，不撕结构
            }
        }

        private static void TrimOpenDeltas(Bucket b)
        {
            int n = 0;
            for (int i = 0; i < b.Frames.Count; i++)
                if (b.Frames[i].Round == b.OpenRound && b.Frames[i].Event == "text_delta") n++;
            while (n > MaxOpenDeltas)
            {
                for (int i = 0; i < b.Frames.Count; i++)
                {
                    if (b.Frames[i].Round == b.OpenRound && b.Frames[i].Event == "text_delta")
                    {
                        b.Frames.RemoveAt(i);
                        n--;
                        break;
                    }
                }
            }
        }

        private static void TrimLoose(Bucket b)
        {
            int n = 0;
            for (int i = 0; i < b.Frames.Count; i++)
                if (b.Frames[i].Round == 0) n++;
            while (n > MaxLooseFrames)
            {
                for (int i = 0; i < b.Frames.Count; i++)
                {
                    if (b.Frames[i].Round == 0)
                    {
                        b.Frames.RemoveAt(i);
                        n--;
                        break;
                    }
                }
            }
        }

        /// <summary>单聊段裁剪：delta 已按 part 合并（段内至多两条流式文本），所以这里只剩
        /// **帧数安全阀** —— step 是低频关键帧、正常一轮几十条，永远到不了；真到了（结构性
        /// 异常）就整段清掉并按 `InvalidateSegment` 推进代数，让消费侧去补一次全量重放。
        /// （合并之前这里还负责"只裁 delta"，那条规则随逐帧 delta 一起删除了 —— 记整轮时它
        /// 删的是**最旧**的 delta，等于把正文砍掉开头。）</summary>
        private static void TrimSolo(SoloBucket b)
        {
            if (b.Segment.Count > MaxSoloFrames)
            {
                // 最后的安全阀：正常永远到不了。
                InvalidateSegment(b);
            }
        }
    }
}
