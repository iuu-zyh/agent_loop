// 「此刻发不出去的动作」排队器纯逻辑核（DeferredActionCore）单元测试。
//
// 背景（09-24 用户拍板 B 方案）：玩家在战斗 / 剧情链收尾期间请求的动作，游戏此刻**发不出去**
// （原生窗沉进 WorldSystemMgr 系统队列不出队 → 90 秒静默超时）。核把这件事变成
// "立即受理 → 此间事了自动执行 → 办完主动告知"。
//
// 覆盖（每条都对应一个会**静默**出事的分支）：
//   ① 入队 / 队列上限（满则拒，不悄悄丢） / Describe 人话；
//   ② 被挡（让步集非空）时绝不执行；条件满足后还要**静默一段**才执行（防"战斗位刚假、
//      处置页/尾页还没登场"就抢跑 —— DramaGate 收尾占位与 BattleOutcome 的同一类教训）；
//   ③ 静默**分两档**：无边沿证据 1.5s（只靠轮询）/ 有边沿证据 0.5s（BattleEnd 事件、收尾占位被撤）；
//   ④ 被挡期间静默窗**清零**（用户 09-24 拍板"有竞争就等待，续期时间置为零"）——
//      包括"边沿已到、刚起算 0.2 秒又来了竞争"这种；
//   ⑤ 执行时又忙 → RequeueHead 退回等待（不判失败），静默窗重计；
//   ⑥ 等玩家（MarkSlot）期间 Poll 不推进；IsHeadSlot 只认队首那张票；
//   ⑦ 队首出队后**下一个**重新计静默窗（不能因为前一个等完就直接抢跑）；
//   ⑧ 硬顶（600s）必然收口 —— 连"正在等玩家"的条目也能被收掉（票据万一丢了没有别的出口）；
//   ⑨ 告知：能说才发（被挡时取不出来）/ FIFO 顺序 / TTL 过期作废（半分钟后凭空开口更糟）；
//   ⑩ 世界重载 Clear：条目与告知一律作废。

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

        /// <summary>固定起点（核不读时钟，全部时间由测试构造）。</summary>
        private static readonly DateTime T0 = new DateTime(2026, 9, 24, 12, 0, 0, DateTimeKind.Utc);

        private static DeferredActionCore New() => new DeferredActionCore();

        private static DeferredActionCore.Step Poll(DeferredActionCore c, double sec, bool blocked, bool edge = false)
            => c.Poll(T0.AddSeconds(sec), blocked, edge);

        private static int Main()
        {
            Console.WriteLine("DeferredActionCore 单元测试");

            TestEnqueueAndCap();
            TestBlockedAndQuietWindow();
            TestEdgeShortensQuietWindow();
            TestCompetitionResetsQuietWindow();
            TestRequeueAfterBusyAgain();
            TestSlotWaitAndNextHead();
            TestHardCeiling();
            TestNotices();
            TestWorldChangedClears();
            TestEnqueueDefaults();

            Console.WriteLine();
            if (_fails.Count == 0)
            {
                Console.WriteLine("全部通过（" + _pass + " 断言）");
                return 0;
            }
            Console.WriteLine("失败 " + _fails.Count + " 条 / 共 " + (_pass + _fails.Count) + " 断言");
            foreach (var f in _fails) Console.WriteLine("  - " + f);
            return 1;
        }

        // ---------- ① 入队 / 上限 / Describe ----------

        private static void TestEnqueueAndCap()
        {
            var c = New();
            Eq("空队 Count=0", c.Count, 0);
            Eq("空队 Head=null", c.Head == null, true);
            Eq("空队 Describe=空串", c.Describe(T0), "");

            Check("入队 1 成功", c.TryEnqueue("姜萌", "world_ai_action", "邀约姜萌", T0, null));
            Eq("Count=1", c.Count, 1);
            Eq("Head.Summary", c.Head.Summary, "邀约姜萌");
            Eq("Head.NpcId", c.Head.NpcId, "姜萌");
            Eq("Head.Slot 初始 -1", c.Head.Slot, -1);
            Eq("Describe 带摘要与排队秒数", c.Describe(T0.AddSeconds(3)), "邀约姜萌（已排队 3s）");

            for (int i = 2; i <= DeferredActionCore.MaxWaiting; i++)
                Check("入队 " + i + " 成功", c.TryEnqueue("姜萌", "economy_item", "赠送 " + i, T0, null));
            Eq("上限即满", c.Count, DeferredActionCore.MaxWaiting);
            Eq("IsFull", c.IsFull, true);
            Eq("第 " + (DeferredActionCore.MaxWaiting + 1) + " 个被拒（不悄悄丢）",
               c.TryEnqueue("姜萌", "economy_item", "赠送溢出", T0, null), false);
            Eq("被拒后 Count 不变", c.Count, DeferredActionCore.MaxWaiting);

            Eq("多条目 Describe 报「另有 N 个」",
               c.Describe(T0.AddSeconds(5)), "邀约姜萌（已排队 5s），另有 7 个在排队");
            Console.WriteLine("✓ ① 入队 / 上限拒收 / Describe 通过");
        }

        // ---------- ② 被挡 + 静默窗（轮询档 1.5s） ----------

        private static void TestBlockedAndQuietWindow()
        {
            var c = New();
            c.TryEnqueue("姜萌", "world_ai_action", "邀约姜萌", T0, null);

            Eq("被挡 → Wait（哪怕已过很久）", Poll(c, 100, true), DeferredActionCore.Step.Wait);
            Eq("被挡 → Wait（再问一次）", Poll(c, 101, true), DeferredActionCore.Step.Wait);

            Eq("条件刚满足 → Wait（起点帧）", Poll(c, 102, false), DeferredActionCore.Step.Wait);
            Eq("差 0.1 秒不执行", Poll(c, 103.4, false), DeferredActionCore.Step.Wait);
            Eq("满 1.5 秒 → Execute", Poll(c, 103.5, false), DeferredActionCore.Step.Execute);
            Eq("常量与意图一致（轮询档 = 1.5）", DeferredActionCore.StableSeconds, 1.5);
            Console.WriteLine("✓ ② 被挡不执行 + 轮询档静默 1.5s 通过");
        }

        // ---------- ③ 边沿档 0.5s ----------

        private static void TestEdgeShortensQuietWindow()
        {
            var c = New();
            c.TryEnqueue("姜萌", "world_ai_action", "邀约姜萌", T0, null);

            Eq("有边沿：起点帧仍 Wait", Poll(c, 10, false, true), DeferredActionCore.Step.Wait);
            Eq("有边沿：0.3 秒还不执行", Poll(c, 10.3, false, true), DeferredActionCore.Step.Wait);
            Eq("有边沿：满 0.5 秒 → Execute", Poll(c, 10.5, false, true), DeferredActionCore.Step.Execute);
            Eq("常量与意图一致（边沿档 = 0.5）", DeferredActionCore.StableEdgeSeconds, 0.5);

            // 同一个时刻、**没有**边沿证据时不该执行（证明边沿确实在起区分作用）
            var c2 = New();
            c2.TryEnqueue("姜萌", "world_ai_action", "邀约姜萌", T0, null);
            Poll(c2, 10, false, false);
            Eq("无边沿：0.5 秒时仍是 Wait（要等满 1.5）", Poll(c2, 10.5, false, false),
               DeferredActionCore.Step.Wait);
            Eq("无边沿：1.5 秒到点才 Execute", Poll(c2, 11.5, false, false),
               DeferredActionCore.Step.Execute);
            Console.WriteLine("✓ ③ 边沿档 0.5s / 轮询档 1.5s 分档通过");
        }

        // ---------- ④ 竞争出现 → 静默窗清零（含边沿档） ----------

        private static void TestCompetitionResetsQuietWindow()
        {
            var c = New();
            c.TryEnqueue("姜萌", "world_ai_action", "邀约姜萌", T0, null);

            // 轮询档：差一点点到点，又被挡（玩家开了界面）→ 归零
            Eq("起点帧", Poll(c, 0, false), DeferredActionCore.Step.Wait);
            Eq("差 0.5 秒到点又被挡 → Wait", Poll(c, 1.0, true), DeferredActionCore.Step.Wait);
            Eq("恢复后**重新起算**（起点帧）", Poll(c, 1.1, false), DeferredActionCore.Step.Wait);
            Eq("重新起算：差 0.1 秒不执行", Poll(c, 2.5, false), DeferredActionCore.Step.Wait);
            Eq("重新算满 1.5 秒 → Execute", Poll(c, 2.6, false), DeferredActionCore.Step.Execute);

            // 边沿档同样：竞争一出就归零（用户原话："有竞争就等待，续期时间置为零"）
            var c2 = New();
            c2.TryEnqueue("姜萌", "world_ai_action", "邀约姜萌", T0, null);
            Eq("边沿档起点帧", Poll(c2, 0, false, true), DeferredActionCore.Step.Wait);
            Eq("刚起算 0.2 秒就撞上竞争 → Wait", Poll(c2, 0.2, true, true), DeferredActionCore.Step.Wait);
            Eq("竞争过去 → 重新起算（起点帧）", Poll(c2, 0.3, false, true), DeferredActionCore.Step.Wait);
            Eq("差 0.1 秒不执行", Poll(c2, 0.7, false, true), DeferredActionCore.Step.Wait);
            Eq("重新算满 0.5 秒 → Execute", Poll(c2, 0.8, false, true), DeferredActionCore.Step.Execute);
            Console.WriteLine("✓ ④ 竞争 → 静默窗清零（两档都验）通过");
        }

        // ---------- ⑤ 执行时又忙 → 退回等待 ----------

        private static void TestRequeueAfterBusyAgain()
        {
            var c = New();
            c.TryEnqueue("姜萌", "world_ai_action", "邀约姜萌", T0, null);
            Poll(c, 0, false);
            Eq("到期可执行", Poll(c, 1.5, false), DeferredActionCore.Step.Execute);

            // 接线层执行体返回"还不能"（又忙了）⇒ 退回等待，**不判失败**
            c.RequeueHead(T0.AddSeconds(1.5));
            Eq("退回后条目还在", c.Count, 1);
            Eq("退回后重新计静默窗（起点帧 Wait）", Poll(c, 1.5, false), DeferredActionCore.Step.Wait);
            Eq("不满 1.5 秒不执行", Poll(c, 2.9, false), DeferredActionCore.Step.Wait);
            Eq("满 1.5 秒再执行", Poll(c, 3.0, false), DeferredActionCore.Step.Execute);
            Console.WriteLine("✓ ⑤ 执行时又忙 → 退回等待（不失败）通过");
        }

        // ---------- ⑥⑦ 等玩家 + 下一个重新计静默窗 ----------

        private static void TestSlotWaitAndNextHead()
        {
            var c = New();
            c.TryEnqueue("姜萌", "world_ai_action", "邀约姜萌", T0, null);
            c.TryEnqueue("云含", "economy_item", "向云含赠送物品", T0, null);
            Poll(c, 0, false);
            Eq("队首到期可执行", Poll(c, 1.5, false), DeferredActionCore.Step.Execute);

            c.MarkSlot(42);
            Eq("等玩家期间不推进（哪怕早已过静默窗）",
               Poll(c, 30, false), DeferredActionCore.Step.Wait);
            Eq("IsHeadSlot 认这张票", c.IsHeadSlot(42), true);
            Eq("不是这张票就不认", c.IsHeadSlot(43), false);

            // 结局回来 → 出队并排一条告知
            c.CompleteHead(T0.AddSeconds(31), "邀约姜萌：已完成");
            Eq("队首已出队", c.Count, 1);
            Eq("新队首是第二个动作", c.Head.Summary, "向云含赠送物品");
            Eq("新队首的票不作数", c.IsHeadSlot(42), false);
            Eq("新队首重新计静默窗（起点帧 Wait）", Poll(c, 32, false), DeferredActionCore.Step.Wait);
            Eq("新队首满 1.5 秒才执行", Poll(c, 33.5, false), DeferredActionCore.Step.Execute);
            Console.WriteLine("✓ ⑥⑦ 等玩家不推进 + 队首出队后下一个重新计静默窗通过");
        }

        // ---------- ⑧ 硬顶 ----------

        private static void TestHardCeiling()
        {
            var c = New();
            c.TryEnqueue("姜萌", "world_ai_action", "邀约姜萌", T0, null);
            Eq("差 1 秒未到硬顶", Poll(c, DeferredActionCore.HardCeilingSeconds - 1, true),
               DeferredActionCore.Step.Wait);
            Eq("到硬顶 → Ceiling（被挡也收口）",
               Poll(c, DeferredActionCore.HardCeilingSeconds + 1, true),
               DeferredActionCore.Step.Ceiling);

            var c2 = New();
            c2.TryEnqueue("姜萌", "world_ai_action", "邀约姜萌", T0, null);
            c2.MarkSlot(7);
            Eq("正在等玩家的条目也受硬顶（票据丢了没有别的出口）",
               Poll(c2, DeferredActionCore.HardCeilingSeconds + 1, false),
               DeferredActionCore.Step.Ceiling);
            Console.WriteLine("✓ ⑧ 硬顶 600 秒必然收口通过");
        }

        // ---------- ⑨ 告知 ----------

        private static void TestNotices()
        {
            var c = New();
            // 两条告知要两个条目（CompleteHead 的语义是"队首**出队**并排一条告知"）
            c.TryEnqueue("姜萌", "world_ai_action", "邀约姜萌", T0, null);
            c.TryEnqueue("云含", "economy_item", "赠送", T0, null);
            c.CompleteHead(T0, "邀约姜萌：已完成");
            c.CompleteHead(T0.AddSeconds(1), "赠送：未能完成（玩家拒收）");

            string npc, text;
            Eq("被挡时取不出来（玩家还在忙 → 先攒着）",
               c.TryTakeNotice(T0.AddSeconds(2), true, out npc, out text), false);
            Eq("取不出时不消耗", c.NoticeCount, 2);

            Eq("能说了 → 取第一条", c.TryTakeNotice(T0.AddSeconds(2), false, out npc, out text), true);
            Eq("FIFO：先入先出（npc）", npc, "姜萌");
            Eq("FIFO：先入先出（正文）", text, "邀约姜萌：已完成");
            Eq("还剩一条", c.NoticeCount, 1);

            // TTL：过期一律作废（"半分钟后凭空开口"比不说更糟）
            var gone = c.ExpireNotices(T0.AddSeconds(1 + DeferredActionCore.NoticeTtlSeconds + 1));
            Eq("过期一条被作废", gone != null ? gone.Count : 0, 1);
            Eq("作废内容原样上报（留痕）", gone != null ? gone[0] : "",
               "赠送：未能完成（玩家拒收）");
            Eq("告知清空", c.NoticeCount, 0);
            Eq("没有可说的了", c.TryTakeNotice(T0.AddSeconds(200), false, out npc, out text), false);

            // 空文本 = 不告知（接线层某些结局不值得开口）
            var c2 = New();
            c2.TryEnqueue("姜萌", "world_ai_action", "邀约姜萌", T0, null);
            c2.CompleteHead(T0, "");
            Eq("空文本不排告知", c2.NoticeCount, 0);
            Console.WriteLine("✓ ⑨ 告知：能说才发 / FIFO / TTL 作废通过");
        }

        // ---------- ⑩ 世界重载 ----------

        private static void TestWorldChangedClears()
        {
            var c = New();
            c.TryEnqueue("姜萌", "world_ai_action", "邀约姜萌", T0, null);
            c.TryEnqueue("云含", "economy_item", "赠送", T0, null);
            c.CompleteHead(T0, "一条待发告知");      // 队首出队 → 排一条告知
            Eq("清掉剩下的 1 个条目", c.Clear(), 1);
            Eq("条目清空", c.Count, 0);
            Eq("告知一并作废", c.NoticeCount, 0);
            Eq("清空后 Poll=Idle", Poll(c, 10, false), DeferredActionCore.Step.Idle);

            var c2 = New();
            Eq("空队 Clear 返回 0", c2.Clear(), 0);
            Console.WriteLine("✓ ⑩ 世界重载 Clear 作废通过");
        }

        // ---------- ⑪ 入队参数 ----------

        private static void TestEnqueueDefaults()
        {
            var c = New();
            c.TryEnqueue("姜萌", "world_ai_action", null, T0, null);
            Eq("摘要缺省回落 op（不能空着——提示/日志都要用）", c.Head.Summary, "world_ai_action");
            Console.WriteLine("✓ ⑪ 摘要缺省兜底通过");
        }
    }
}
