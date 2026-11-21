// 聊天帧缓冲队列（ChatFrameQueue）单元测试。
//
// 为什么要有这个工程：队列的存在意义是"关窗期间不丢帧、重开不重复"，而这两条都由
// **纯判决规则**决定 —— 规则错了在真机上表现是"偶发重复气泡 / 忙碌态卡死 / 幽灵泡"，
// 与 BubbleCommitPolicy 那批事故同一形态（无日志、偶发、难复现）。所以规则必须能在
// 开发机上直接跑：这里把真正的队列源码链接进来，喂帧序列，断言"该演哪些帧"。
//
// 覆盖（每条都对应一个会静默出事的分支）：
//   群聊（按轮 + baseline，原 RoomFrameQueueTests 的 9 条原样保留）：
//     ① 分桶隔离；② 已闭合轮 baseline 判决；③ 未闭合轮恒保留；④ 轮外散帧按 seq；
//     ⑤ 无归属 delta 不收；⑥ 只裁 delta；⑦ 闭合轮裁剪；⑧ 无 end 的轮隐式闭合；
//     ⑨ ForgetRoom / ClearAll。
//   群聊 09-17 新增（step 帧纳入）：
//     ⑩ step 归轮（无归属不收 / 未闭合轮跟着重演）；⑪ step 不参与 delta 限流。
//   单聊（段 = 当前这一轮的**全过程**）：
//     ⑫ 段收集与保序（user_say / delta / step 混排）；⑬ npc_reply 清段（幂等 / 空桶安全）；
//     ⑭ step 回合号推进 → 旧段隐式作废；⑮ **delta 按 part 合并**（body/think 各一条，保序）；
//     ⑯ **user_say 段重置**（玩家开口 = 新一段，且不被新回合的 step 连带清掉）；
//     ⑰ **段闭合代数**（npc_reply / 隐式闭合推进，user_say 不推进）；⑱ 段的帧数安全阀；
//     ⑲ **收帧白名单**（`IsSoloBuffered`：会进段 vs 闸门必须放行的 —— 两边同源，见下）；
//     ⑳ ForgetSolo / CountOfSolo / ClearAll；㉑ 空键 / 未知键安全。
//
// 接线（谁在什么时候收帧、什么时候消费）由 Python 侧源码契约测试
// tests/test_chat_frame_queue_contract.py 锁在真实 ChatPresenter.cs / ChatFrameTee.cs /
// AbChatPanel.cs / ModMain.cs 上 —— 两边合起来才完整：这里测"规则对不对"，
// 那边测"接线有没有被改回去"。

using System;
using System.Collections.Generic;
using AgentLoopBridge;

namespace AgentLoopBridge.Tests
{
    internal static class Program
    {
        private static int _pass;
        private static readonly List<string> _fails = new List<string>();

        private static void Check(string name, bool ok, string detail = "")
        {
            if (ok) { _pass++; return; }
            _fails.Add(name + (detail.Length > 0 ? "  ｜ " + detail : ""));
            Console.WriteLine("  ✗ " + name + (detail.Length > 0 ? "  ｜ " + detail : ""));
        }

        private static void Eq<T>(string name, T got, T want) =>
            Check(name, EqualityComparer<T>.Default.Equals(got, want),
                  "实际=" + Show(got) + " 期望=" + Show(want));

        private static string Show<T>(T v) => v == null ? "<null>" : "\"" + v + "\"";

        // ---- 帧构造（与 ChatFrameTee.Decode 的字段一一对应） ----

        private static ChatFrame Say(int seq, string text = "话", string speaker = "林婉清") =>
            new ChatFrame { Event = "room_say", Seq = seq, Uid = "g#" + seq, Speaker = speaker, Text = text };

        private static ChatFrame Speak(string who = "林婉清") =>
            new ChatFrame { Event = "room_state", Phase = "speak", Seq = -1, Speaking = new[] { who } };

        private static ChatFrame End(int lastSeq) =>
            new ChatFrame { Event = "room_state", Phase = "end", Seq = lastSeq };

        private static ChatFrame EndNote(int lastSeq, string note) =>
            new ChatFrame { Event = "room_state", Phase = "end", Seq = lastSeq, Note = note };

        private static ChatFrame Delta(string tok = "半", string part = "body") =>
            new ChatFrame { Event = "text_delta", Seq = -1, Text = tok, Part = part };

        /// <summary>玩家发言帧（**C# 本地产生**：单聊没有回声帧，见 ChatFrameQueue 文件头）。</summary>
        private static ChatFrame UserSay(string text = "你好") =>
            new ChatFrame { Event = "user_say", Seq = -1, Turn = -1, Text = text };

        private static ChatFrame Step(int turn, string kind = "tool_call") =>
            new ChatFrame { Event = "step", Seq = -1, Turn = turn, Kind = kind, Text = "T",
                            Name = "inspect_unit", Args = "{}", IsError = false };

        private static ChatFrame Reply() =>
            new ChatFrame { Event = "npc_reply" };

        private static int Main()
        {
            Console.WriteLine("=== ChatFrameQueue 单元测试 ===");
            Console.WriteLine("[群聊：按轮 + baseline]");
            Buckets();
            RoundFilter();
            OpenRound();
            LooseFrames();
            DeltaWithoutRound();
            StepInRound();
            TrimDeltaOnly();
            TrimClosedRounds();
            ImplicitClose();
            Lifecycle();
            Console.WriteLine("[单聊：整轮记录（段 = 当前这一轮的全过程）]");
            SoloCollect();
            SoloReplyClears();
            SoloTurnRollover();
            SoloDeltaMerges();
            SoloUserSayResets();
            SoloEpoch();
            SoloFrameCap();
            SoloBufferedWhitelist();
            SoloLifecycle();
            Console.WriteLine();
            if (_fails.Count == 0)
            {
                Console.WriteLine("全部通过：" + _pass + " 项断言");
                return 0;
            }
            Console.WriteLine("失败 " + _fails.Count + " 项 / 通过 " + _pass + " 项");
            return 1;
        }

        // ==================================================================
        // 群聊
        // ==================================================================

        private static void Buckets()
        {
            Console.WriteLine("[分桶]");
            ChatFrameQueue.ClearAll();
            ChatFrameQueue.OfferRoom("gA", Speak());
            ChatFrameQueue.OfferRoom("gA", Delta());
            ChatFrameQueue.OfferRoom("gA", Say(1));
            ChatFrameQueue.OfferRoom("gA", End(1));
            ChatFrameQueue.OfferRoom("gB", Say(5));
            Eq("A 桶只装 A 的帧", ChatFrameQueue.CountOfRoom("gA"), 4);
            Eq("B 桶只装 B 的帧", ChatFrameQueue.CountOfRoom("gB"), 1);
            Eq("未知桶为空", ChatFrameQueue.CountOfRoom("gX"), 0);
            Eq("空 roomId 不收", ChatFrameQueue.OfferRoom("", Say(1)), false);
            Eq("非群聊帧不收", ChatFrameQueue.OfferRoom("gA", new ChatFrame { Event = "npc_reply" }), false);
            Eq("群 / 单聊两个世界互不串", ChatFrameQueue.CountOfSolo("gA"), 0);
        }

        private static void RoundFilter()
        {
            Console.WriteLine("[已闭合轮：baseline 判决]");
            ChatFrameQueue.ClearAll();
            ChatFrameQueue.OfferRoom("g1", Speak("甲"));
            ChatFrameQueue.OfferRoom("g1", Delta("半"));
            ChatFrameQueue.OfferRoom("g1", Say(7));
            ChatFrameQueue.OfferRoom("g1", End(7));

            var covered = ChatFrameQueue.TakeRoom("g1", 7);
            Eq("基线覆盖 endSeq → 整轮丢（重开不重复）", covered.Count, 0);

            var kept = ChatFrameQueue.TakeRoom("g1", 6);
            Eq("基线未覆盖 → 整轮保留", kept.Count, 4);
            Eq("原序第一帧 speak", kept[0].Phase, "speak");
            Eq("原序第二帧 delta", kept[1].Event, "text_delta");
            Eq("原序第三帧 room_say", kept[2].Event, "room_say");
            Eq("原序末帧 end", kept[3].Phase, "end");
            Eq("TakeRoom 只读不删", ChatFrameQueue.CountOfRoom("g1"), 4);
        }

        private static void OpenRound()
        {
            Console.WriteLine("[未闭合轮：恒保留]");
            ChatFrameQueue.ClearAll();
            ChatFrameQueue.OfferRoom("g1", Speak("甲"));
            ChatFrameQueue.OfferRoom("g1", Delta("半"));
            ChatFrameQueue.OfferRoom("g1", Say(3));
            var t = ChatFrameQueue.TakeRoom("g1", 999);   // 基线很高也不丢：这轮"正在说"
            // ★ 但"段内已有**落进日志**的 room_say"是例外：那一条已经由 `ReplaceHistory` 画过了
            //   （回放读的就是 Room.log），整段照搬会让**同一条出现两遍**。见 `TakeRoom` 的注释。
            //   这里 Say(3) 的 seq=3 ≤ 基线 ⇒ 它是"已落账"的那一条 ⇒ 整段丢。
            Eq("未闭合段里已落账的 room_say：整段丢（否则重开窗画两遍）", t.Count, 0);

            ChatFrameQueue.ClearAll();
            ChatFrameQueue.OfferRoom("g1", Speak("甲"));
            ChatFrameQueue.OfferRoom("g1", Delta("半"));
            ChatFrameQueue.OfferRoom("g1", Say(9));
            var t2 = ChatFrameQueue.TakeRoom("g1", 9 - 1);   // say 还没进日志 ⇒ 正在流
            Eq("未闭合轮里还没落账的：照旧留", t2.Count, 3);
            Eq("delta 跟着未闭合轮一起留（否则会凭空建泡）", t2[1].Event, "text_delta");
        }

        private static void LooseFrames()
        {
            Console.WriteLine("[轮外散帧：按 seq]");
            ChatFrameQueue.ClearAll();
            ChatFrameQueue.OfferRoom("g1", Say(3, "我这句话", "player"));
            Eq("散帧 seq > 基线 → 留", ChatFrameQueue.TakeRoom("g1", 2).Count, 1);
            Eq("散帧 seq <= 基线 → 丢（玩家回声不重复）", ChatFrameQueue.TakeRoom("g1", 3).Count, 0);
            // 无 speak 的 end（异常时序）：作散帧、按 seq 判
            ChatFrameQueue.ClearAll();
            ChatFrameQueue.OfferRoom("g1", End(9));
            Eq("无配轮的 end：seq <= 基线 → 丢", ChatFrameQueue.TakeRoom("g1", 9).Count, 0);
            Eq("无配轮的 end：seq > 基线 → 留（复位忙碌态）", ChatFrameQueue.TakeRoom("g1", 8).Count, 1);
            // 终帧的提示文案（"这一轮为什么没产出"）也必须原样带过 —— 丢了玩家就看不到说明
            ChatFrameQueue.ClearAll();
            ChatFrameQueue.OfferRoom("g1", EndNote(9, "群聊不提供手动压缩"));
            var tn = ChatFrameQueue.TakeRoom("g1", 8);
            Eq("终帧提示文案原样带过", tn.Count == 1 ? tn[0].Note : "", "群聊不提供手动压缩");
        }

        private static void DeltaWithoutRound()
        {
            Console.WriteLine("[无归属 delta]");
            ChatFrameQueue.ClearAll();
            Eq("没有 speak 的 delta 不收", ChatFrameQueue.OfferRoom("g1", Delta()), false);
            Eq("桶仍是空的", ChatFrameQueue.CountOfRoom("g1"), 0);
        }

        private static void StepInRound()
        {
            Console.WriteLine("[step 归轮（09-17 统一新增）]");
            ChatFrameQueue.ClearAll();
            Eq("没有 speak 的 step 不收（会变成没有回合的折叠）",
               ChatFrameQueue.OfferRoom("g1", Step(1, "tool_call")), false);
            Eq("桶仍是空的", ChatFrameQueue.CountOfRoom("g1"), 0);

            ChatFrameQueue.OfferRoom("g1", Speak("甲"));
            Eq("未闭合轮里的 step 收下", ChatFrameQueue.OfferRoom("g1", Step(1, "tool_call")), true);
            ChatFrameQueue.OfferRoom("g1", Say(1));
            ChatFrameQueue.OfferRoom("g1", End(1));

            var t = ChatFrameQueue.TakeRoom("g1", 0);   // 基线 0 ⇒ 整轮保留
            Eq("step 跟着轮一起重演（共 4 帧）", t.Count, 4);
            Eq("step 保序（第 2 帧）", t[1].Kind, "tool_call");
            Eq("step 带回合号过桥（隐式闭合/去重都用它）", t[1].Turn, 1);
            Eq("step 的 Round 已归轮（不是散帧）", t[1].Round, 1);
        }

        private static void TrimDeltaOnly()
        {
            Console.WriteLine("[裁剪：只裁 delta（speak / room_say / step 永远保留）]");
            ChatFrameQueue.ClearAll();
            ChatFrameQueue.OfferRoom("g1", Speak());
            ChatFrameQueue.OfferRoom("g1", Step(1, "tool_call"));
            ChatFrameQueue.OfferRoom("g1", Say(1));
            for (int i = 0; i < ChatFrameQueue.MaxOpenDeltas + 50; i++)
                ChatFrameQueue.OfferRoom("g1", Delta("d" + i));

            var t = ChatFrameQueue.TakeRoom("g1", 0);
            int deltas = 0; bool hasSpeak = false, hasSay = false, hasStep = false;
            string firstDelta = null;
            foreach (var f in t)
            {
                if (f.Event == "text_delta") { deltas++; if (firstDelta == null) firstDelta = f.Text; }
                if (f.Phase == "speak") hasSpeak = true;
                if (f.Event == "room_say") hasSay = true;
                if (f.Event == "step") hasStep = true;
            }
            Check("speak 永远保留", hasSpeak);
            Check("room_say 永远保留", hasSay);
            Check("step 永远保留", hasStep);
            Check("delta 数被限流（≤ MaxOpenDeltas）", deltas <= ChatFrameQueue.MaxOpenDeltas,
                  "实际=" + deltas);
            Eq("裁掉的是最旧 delta（d50 成了第一条）", firstDelta, "d50");
        }

        private static void TrimClosedRounds()
        {
            Console.WriteLine("[裁剪：闭合轮只留最后 N 个]");
            ChatFrameQueue.ClearAll();
            int n = ChatFrameQueue.MaxClosedRounds + 2;
            for (int r = 1; r <= n; r++)
            {
                ChatFrameQueue.OfferRoom("g1", Speak("甲"));
                ChatFrameQueue.OfferRoom("g1", Say(r));
                ChatFrameQueue.OfferRoom("g1", End(r));
            }
            Eq("只留最后 N 轮（丢最旧整轮，不撕结构）",
               ChatFrameQueue.CountOfRoom("g1"), ChatFrameQueue.MaxClosedRounds * 3);

            bool oldestGone = true;
            foreach (var f in ChatFrameQueue.TakeRoom("g1", 0))
                if (f.Event == "room_say" && f.Seq <= 2) oldestGone = false;
            Check("最旧的整轮被删", oldestGone);
        }

        private static void ImplicitClose()
        {
            Console.WriteLine("[隐式闭合：没有 end 的轮]");
            ChatFrameQueue.ClearAll();
            ChatFrameQueue.OfferRoom("g1", Speak("甲"));
            ChatFrameQueue.OfferRoom("g1", Delta());
            ChatFrameQueue.OfferRoom("g1", Say(1));
            ChatFrameQueue.OfferRoom("g1", Speak("乙"));   // 旧轮始终没有 end
            ChatFrameQueue.OfferRoom("g1", Delta());
            ChatFrameQueue.OfferRoom("g1", Say(2));
            ChatFrameQueue.OfferRoom("g1", End(2));

            var t = ChatFrameQueue.TakeRoom("g1", 0);
            // 乙轮 = speak + delta + room_say + end 共 4 帧；甲轮（EndSeq=0）整轮被丢
            Eq("无 end 的轮按'可丢'处理（重放不卡忙碌态）", t.Count, 4);
            Eq("留下的是乙轮", (t[0].Speaking != null && t[0].Speaking.Length > 0) ? t[0].Speaking[0] : "", "乙");
            Eq("末帧是乙轮的 end", t[3].Phase, "end");
        }

        private static void Lifecycle()
        {
            Console.WriteLine("[生命周期]");
            ChatFrameQueue.ClearAll();
            ChatFrameQueue.OfferRoom("g1", Say(1));
            ChatFrameQueue.ForgetRoom("g1");
            Eq("ForgetRoom 清桶", ChatFrameQueue.CountOfRoom("g1"), 0);
            Eq("ForgetRoom 未知桶是安全的", ChatFrameQueue.CountOfRoom("gX"), 0);

            ChatFrameQueue.OfferRoom("g1", Say(1));
            ChatFrameQueue.OfferRoom("g2", Say(2));
            ChatFrameQueue.ClearAll();
            Eq("ClearAll 清全部",
               ChatFrameQueue.CountOfRoom("g1") + ChatFrameQueue.CountOfRoom("g2"), 0);
        }

        // ==================================================================
        // 单聊
        // ==================================================================

        private static void SoloCollect()
        {
            Console.WriteLine("[段收集：delta / step 混排保序]");
            ChatFrameQueue.ClearAll();
            Eq("空 npcId 不收", ChatFrameQueue.OfferSolo("", Delta()), false);
            Eq("群帧（room_say）不进单聊桶", ChatFrameQueue.OfferSolo("林", Say(1)), false);
            Eq("段的第一帧可以是 delta（回合中关窗，speak 没人给）",
               ChatFrameQueue.OfferSolo("林", Delta("甲")), true);
            Eq("step 进同一段", ChatFrameQueue.OfferSolo("林", Step(3, "tool_call")), true);
            Eq("delta 继续进段", ChatFrameQueue.OfferSolo("林", Delta("乙")), true);
            Eq("段内共 3 帧", ChatFrameQueue.CountOfSolo("林"), 3);

            var seg = ChatFrameQueue.TakeSolo("林");
            Eq("保序：delta 开头", seg[0].Text, "甲");
            Eq("保序：step 在中间", seg[1].Kind, "tool_call");
            Eq("保序：delta 收尾", seg[2].Text, "乙");
            Eq("TakeSolo 只读不删", ChatFrameQueue.CountOfSolo("林"), 3);
            Eq("未知桶为空", ChatFrameQueue.CountOfSolo("王"), 0);
            Eq("未知桶 TakeSolo 为空表", ChatFrameQueue.TakeSolo("王").Count, 0);
        }

        private static void SoloReplyClears()
        {
            Console.WriteLine("[npc_reply：段闭合信号]");
            ChatFrameQueue.ClearAll();
            ChatFrameQueue.OfferSolo("林", Delta("半截"));
            ChatFrameQueue.OfferSolo("林", Step(2, "think"));
            Eq("清段前有 2 帧", ChatFrameQueue.CountOfSolo("林"), 2);
            Eq("npc_reply 自身不入段", ChatFrameQueue.OfferSolo("林", Reply()), false);
            Eq("npc_reply 把整段作废（重放必含该回合）", ChatFrameQueue.CountOfSolo("林"), 0);
            Eq("重复 npc_reply 幂等安全", ChatFrameQueue.OfferSolo("林", Reply()), false);
            Eq("清后可接着开新段", ChatFrameQueue.OfferSolo("林", Delta("新回合")), true);
            Eq("新段只有新帧", ChatFrameQueue.CountOfSolo("林"), 1);
        }

        private static void SoloTurnRollover()
        {
            Console.WriteLine("[隐式闭合：step 回合号推进]");
            ChatFrameQueue.ClearAll();
            ChatFrameQueue.OfferSolo("林", Step(5, "tool_call"));
            ChatFrameQueue.OfferSolo("林", Delta("上回合的尾巴"));
            Eq("同回合内推进不误清", ChatFrameQueue.CountOfSolo("林"), 2);
            ChatFrameQueue.OfferSolo("林", Delta("又来一个 delta"));
            Eq("新回合第一步若是 delta 也先进段（段尾同类 delta 合并 ⇒ 仍是 2 帧）",
               ChatFrameQueue.CountOfSolo("林"), 2);
            ChatFrameQueue.OfferSolo("林", Step(6, "tool_call"));   // 回合号变大 → 旧段隐式作废
            Eq("回合号推进 → 旧段清掉（死段不留）", ChatFrameQueue.CountOfSolo("林"), 1);
            var seg = ChatFrameQueue.TakeSolo("林");
            Eq("新段从这一帧开张", seg[0].Kind, "tool_call");
            Eq("段内存的是新回合号", seg[0].Turn, 6);
            ChatFrameQueue.OfferSolo("林", Step(-1, "think"));
            Eq("turn 未知（-1）的 step 不清段（保守）", ChatFrameQueue.CountOfSolo("林"), 2);
        }

        private static void SoloDeltaMerges()
        {
            Console.WriteLine("[delta 按 part 合并：段内至多两条流式文本]");
            ChatFrameQueue.ClearAll();
            ChatFrameQueue.OfferSolo("林", Delta("你"));
            ChatFrameQueue.OfferSolo("林", Delta("好"));
            ChatFrameQueue.OfferSolo("林", Delta("呀"));
            var seg = ChatFrameQueue.TakeSolo("林");
            Eq("相邻同类 delta 合并成一条", seg.Count, 1);
            Eq("合并后是累计全文（struct 必须写回表里）", seg[0].Text, "你好呀");
            Eq("Part 保持 body", seg[0].Part, "body");

            // 思考与正文是两个 part：交替出现时**不许**互相合并（顺序也要保持）
            ChatFrameQueue.ClearAll();
            ChatFrameQueue.OfferSolo("林", Delta("想甲", "think"));
            ChatFrameQueue.OfferSolo("林", Delta("说乙"));
            ChatFrameQueue.OfferSolo("林", Delta("想丙", "think"));
            ChatFrameQueue.OfferSolo("林", Delta("说丁"));
            var seg2 = ChatFrameQueue.TakeSolo("林");
            Eq("think / body 交替 ⇒ 4 条（不跨 part 合并）", seg2.Count, 4);
            Eq("顺序：think 第一条", seg2[0].Text, "想甲");
            Eq("顺序：body 第二条", seg2[1].Text, "说乙");
            Eq("顺序：think 第三条", seg2[2].Text, "想丙");
            Eq("顺序：body 第四条", seg2[3].Text, "说丁");

            // step 是天然的分隔点：同 part 的 delta 被 step 隔开就**不许**跨过去合并
            ChatFrameQueue.ClearAll();
            ChatFrameQueue.OfferSolo("林", Delta("前"));
            ChatFrameQueue.OfferSolo("林", Step(3, "tool_call"));
            ChatFrameQueue.OfferSolo("林", Delta("后"));
            var seg3 = ChatFrameQueue.TakeSolo("林");
            Eq("被 step 隔开的 delta 不合并（时间线不许抹平）", seg3.Count, 3);
            Eq("第一条仍是前半截", seg3[0].Text, "前");
            Eq("第三条是后半截", seg3[2].Text, "后");

            // 超长文本有字符上限（防无界）：截断而不是无限增长
            ChatFrameQueue.ClearAll();
            string big = new string('x', ChatFrameQueue.MaxSoloTextChars + 5000);
            ChatFrameQueue.OfferSolo("林", Delta(big));
            var seg4 = ChatFrameQueue.TakeSolo("林");
            Eq("单条超限即截到上限", seg4[0].Text.Length, ChatFrameQueue.MaxSoloTextChars);
        }

        private static void SoloUserSayResets()
        {
            Console.WriteLine("[user_say：玩家开口 = 新一段]");
            ChatFrameQueue.ClearAll();
            ChatFrameQueue.OfferSolo("林", Step(5, "tool_call"));
            ChatFrameQueue.OfferSolo("林", Delta("上一轮的半截"));
            Eq("重置前段里有东西", ChatFrameQueue.CountOfSolo("林"), 2);
            Eq("user_say 入段", ChatFrameQueue.OfferSolo("林", UserSay("我说的话")), true);
            var seg = ChatFrameQueue.TakeSolo("林");
            Eq("段被重置成 1 帧（上一轮的残留不进这一轮）", seg.Count, 1);
            Eq("留下的正是玩家那句", seg[0].Event, "user_say");
            Eq("原文逐字保留（含时间戳/图片占位）", seg[0].Text, "我说的话");

            // ★ 重置必须把 SegmentTurn 归零：否则新回合第一条 step（回合号必然变大）
            //   会被"隐式闭合"判成旧段推进，把刚记下的这句话连带清掉。
            ChatFrameQueue.OfferSolo("林", Step(6, "tool_call"));
            Eq("新回合的 step 之后，玩家那句仍在", ChatFrameQueue.TakeSolo("林").Count, 2);
            var seg2 = ChatFrameQueue.TakeSolo("林");
            Eq("顺序：玩家先说", seg2[0].Event, "user_say");
            Eq("顺序：工具在后", seg2[1].Kind, "tool_call");
            // 完整一轮的形态：user_say → think → tool_call → tool_result → think流 → 正文流
            ChatFrameQueue.ClearAll();
            ChatFrameQueue.OfferSolo("林", UserSay("查一下你的日志"));
            ChatFrameQueue.OfferSolo("林", Step(7, "think"));
            ChatFrameQueue.OfferSolo("林", Step(7, "tool_call"));
            ChatFrameQueue.OfferSolo("林", Step(7, "tool_result"));
            ChatFrameQueue.OfferSolo("林", Delta("让我看看", "think"));
            ChatFrameQueue.OfferSolo("林", Delta("这就是妾身近来经历", "body"));
            var full = ChatFrameQueue.TakeSolo("林");
            Eq("整轮 6 条（重开窗要一条不落）", full.Count, 6);
            Eq("末条是累计正文", full[5].Text, "这就是妾身近来经历");
        }

        private static void SoloEpoch()
        {
            Console.WriteLine("[段闭合代数：识别'快照过期']");
            ChatFrameQueue.ClearAll();
            Eq("没桶时读作 0", ChatFrameQueue.SoloEpoch("林"), 0);
            ChatFrameQueue.OfferSolo("林", UserSay("你好"));
            ChatFrameQueue.OfferSolo("林", Step(9, "think"));
            Eq("记帧不推进代数", ChatFrameQueue.SoloEpoch("林"), 0);
            ChatFrameQueue.OfferSolo("林", Reply());
            Eq("npc_reply 推进代数（这一段收口了）", ChatFrameQueue.SoloEpoch("林"), 1);
            ChatFrameQueue.OfferSolo("林", UserSay("再来一句"));
            Eq("user_say 只是开新段，不推进代数", ChatFrameQueue.SoloEpoch("林"), 1);
            ChatFrameQueue.OfferSolo("林", Step(10, "think"));
            ChatFrameQueue.OfferSolo("林", Step(11, "think"));   // 回合号推进 ⇒ 隐式闭合
            Eq("隐式闭合也推进代数（旧回合的 npc_reply 丢了）", ChatFrameQueue.SoloEpoch("林"), 2);
            ChatFrameQueue.ForgetSolo("林");
            Eq("桶没了读作 0（等价'没有回合收口过'）", ChatFrameQueue.SoloEpoch("林"), 0);
        }

        private static void SoloFrameCap()
        {
            Console.WriteLine("[安全阀：段帧数绝对上限]");
            ChatFrameQueue.ClearAll();
            for (int i = 0; i < ChatFrameQueue.MaxSoloFrames; i++)
                ChatFrameQueue.OfferSolo("林", Step(1, "tool_call"));
            Eq("上限之内不清", ChatFrameQueue.CountOfSolo("林"), ChatFrameQueue.MaxSoloFrames);
            ChatFrameQueue.OfferSolo("林", Step(1, "tool_call"));
            Eq("超绝对上限整段清掉", ChatFrameQueue.CountOfSolo("林"), 0);
            Eq("清段同时推进代数（消费侧据此补一次全量重放）", ChatFrameQueue.SoloEpoch("林"), 1);
        }

        private static void SoloBufferedWhitelist()
        {
            Console.WriteLine("[收帧白名单：tee 的'收进段'与渲染闸的'吞掉'同源]");
            // 会进段的（= 闸门期间允许被吞，因为队列会补画）
            Eq("step(tool_call) 进段", ChatFrameQueue.IsSoloBuffered("step", "tool_call"), true);
            Eq("step(tool_result) 进段", ChatFrameQueue.IsSoloBuffered("step", "tool_result"), true);
            Eq("step(think) 进段", ChatFrameQueue.IsSoloBuffered("step", "think"), true);
            Eq("step(text) 进段", ChatFrameQueue.IsSoloBuffered("step", "text"), true);
            Eq("text_delta 进段", ChatFrameQueue.IsSoloBuffered("text_delta", ""), true);
            Eq("npc_reply 进段（段闭合信号）", ChatFrameQueue.IsSoloBuffered("npc_reply", ""), true);
            // ★ 不进段的：闸门**不许**吞 —— 队列不补画，吞了就是"两边都没有"
            Eq("step(sys) 不进段（只实时不重放 ⇒ 闸门必须放行）",
               ChatFrameQueue.IsSoloBuffered("step", "sys"), false);
            Eq("step(ctx) 不进段", ChatFrameQueue.IsSoloBuffered("step", "ctx"), false);
            Eq("stats_update 不进段（吞了统计条就再不更新）",
               ChatFrameQueue.IsSoloBuffered("stats_update", ""), false);
            Eq("compact_result 不进段（吞了'正在压缩中'那行没人收尾）",
               ChatFrameQueue.IsSoloBuffered("compact_result", ""), false);
            Eq("群帧不进单聊段", ChatFrameQueue.IsSoloBuffered("room_say", ""), false);
            Eq("空 event 不进段", ChatFrameQueue.IsSoloBuffered("", ""), false);
            // 白名单内部只有这四种 kind（与 tee 的"只实时不重放"口径一致）
            Eq("kind 白名单：sys 不是可重放 kind", ChatFrameQueue.IsPlayableStepKind("sys"), false);
            Eq("kind 白名单：text 是", ChatFrameQueue.IsPlayableStepKind("text"), true);
        }

        private static void SoloLifecycle()
        {
            Console.WriteLine("[生命周期]");
            ChatFrameQueue.ClearAll();
            ChatFrameQueue.OfferSolo("林", Delta("半"));
            ChatFrameQueue.ForgetSolo("林");
            Eq("ForgetSolo 清桶", ChatFrameQueue.CountOfSolo("林"), 0);
            Eq("ForgetSolo 未知桶是安全的", ChatFrameQueue.CountOfSolo("王"), 0);

            ChatFrameQueue.OfferSolo("林", Delta("半"));
            ChatFrameQueue.OfferRoom("g1", Say(1));
            ChatFrameQueue.ClearAll();
            Eq("ClearAll 清两个世界",
               ChatFrameQueue.CountOfSolo("林") + ChatFrameQueue.CountOfRoom("g1"), 0);
        }
    }
}
