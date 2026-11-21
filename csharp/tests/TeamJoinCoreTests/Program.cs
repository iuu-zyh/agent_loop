// 「决斗要不要带队友」状态机的单元测试（10-25）。
//
// 为什么值得单独一套：这套判据错在真机上**全是静默的**。
//   · 该问没问 ⇒ 玩家看不到确认窗、队友不来，日志一切正常；
//   · 重复问   ⇒ 应战窗期间连弹两次，第二次把刚做的选择顶掉；
//   · 不作废   ⇒ 上一场点过"一起应战"，很久以后某一场没问就自己带人上场；
//   · 乱作废   ⇒ 玩家点了同意，却在注入之前被清掉（队友照样不来，而且没人报错）。
//
// 九组：
//   ① 应战窗没亮 ⇒ 从不问
//   ② 没有队友 / 判不出对手 ⇒ 不问（后者还要"不反复重试"）
//   ③ 正常时机 ⇒ 问一次；同一场再问 ⇒ 不问
//   ④ 玩家答"同意"/"我一个人" ⇒ Take 取走一次，第二次拿到 None
//   ⑤ 进 InBattle 时答案必须**留着**（注入层要用）
//   ⑥ 战后回 Idle ⇒ 表态作废
//   ⑦ 战前流程结束（没打成）⇒ 表态作废
//   ⑧ 新的战前 ⇒ 可以再问（"已问"复位）
//   ⑨ Reset（换世界）⇒ 全清，连"已问"一起

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

        private static int Main(string[] args)
        {
            Console.WriteLine("TeamJoinCoreTests —— 决斗参战状态机");

            Case1_NeverAskWithoutPreWindow();
            Case2_SkipWhenNoTeamOrNoAttacker();
            Case3_AskOncePerEpisode();
            Case4_AnswerAndTake();
            Case5_KeepAnswerIntoBattle();
            Case6_DropAfterBattle();
            Case7_DropWhenPreBattleEndsWithoutBattle();
            Case8_NewEpisodeAsksAgain();
            Case9_ResetClearsEverything();
            Case10_NoSecondAskAfterAnswer();

            Console.WriteLine();
            if (_fails.Count == 0)
            {
                Console.WriteLine("✅ 全部通过：" + _pass + " 项");
                return 0;
            }
            Console.WriteLine("❌ 失败 " + _fails.Count + " 项 / 通过 " + _pass + " 项：");
            foreach (var f in _fails) Console.WriteLine("   · " + f);
            return 1;
        }

        // ---------------------------------------------------------------- ①

        private static void Case1_NeverAskWithoutPreWindow()
        {
            Console.WriteLine("\n① 应战窗没亮 ⇒ 从不问");
            var c = new TeamJoinCore();
            string why;
            Check("不亮不问", !c.ShouldAsk(false, true, true, false, out why));
            Check("不亮时 why 为空（不必刷日志）", why == "", "why=" + why);
            Check("不亮也不算'问过'", !c.Asked);
        }

        // ---------------------------------------------------------------- ②

        private static void Case2_SkipWhenNoTeamOrNoAttacker()
        {
            Console.WriteLine("\n② 没有队友 / 判不出对手 ⇒ 不问");
            var c = new TeamJoinCore();
            string why;

            Check("没队友不问", !c.ShouldAsk(true, false, true, false, out why));
            Check("没队友时 why 有说明", why.Contains("队友"), "why=" + why);

            Check("判不出对手不问", !c.ShouldAsk(true, true, false, false, out why));
            Check("判不出对手时 why 有说明", why.Contains("对手"), "why=" + why);

            // ★ 判不出对手要**标记已问** —— 否则每 0.25s 试一次，日志会被刷满
            Check("判不出对手后不再重复尝试", !c.ShouldAsk(true, true, false, false, out why));
            Check("重复尝试时 why=本场已问过", why.Contains("已问过"), "why=" + why);

            // 我方自己的窗开着 ⇒ 不重复弹
            var c2 = new TeamJoinCore();
            Check("我方窗开着不问", !c2.ShouldAsk(true, true, true, true, out why));
            Check("我方窗开着时 why 有说明", why.Contains("窗口"), "why=" + why);
        }

        // ---------------------------------------------------------------- ③

        private static void Case3_AskOncePerEpisode()
        {
            Console.WriteLine("\n③ 正常时机 ⇒ 问一次；同一场再问 ⇒ 不问");
            var c = new TeamJoinCore();
            string why;

            Check("第一次问", c.ShouldAsk(true, true, true, false, out why));
            Check("问了之后 AskCount=1", c.AskCount == 1, "asks=" + c.AskCount);
            Check("同一场第二次不问", !c.ShouldAsk(true, true, true, false, out why));
            Check("第二次 why=已问过", why.Contains("已问过"), "why=" + why);
            Check("AskCount 仍是 1", c.AskCount == 1, "asks=" + c.AskCount);
        }

        // ---------------------------------------------------------------- ④

        private static void Case4_AnswerAndTake()
        {
            Console.WriteLine("\n④ 表态的存取是一次性的");

            var yes = new TeamJoinCore();
            string why;
            yes.ShouldAsk(true, true, true, false, out why);
            yes.SetAnswer(true);
            Check("同意后 HasAnswer", yes.HasAnswer);
            Check("Take 拿到 Yes", yes.Take() == JoinAnswer.Yes);
            Check("Take 之后清空", !yes.HasAnswer);
            Check("再 Take 拿到 None", yes.Take() == JoinAnswer.None);
            Check("TakeCount=1", yes.TakeCount == 1, "takes=" + yes.TakeCount);

            var no = new TeamJoinCore();
            no.ShouldAsk(true, true, true, false, out why);
            no.SetAnswer(false);
            Check("拒绝后 Take 拿到 No", no.Take() == JoinAnswer.No);

            // 没答就取 ⇒ None（"没点 = 没同意"这条的机器保证）
            var none = new TeamJoinCore();
            none.ShouldAsk(true, true, true, false, out why);
            Check("没答就取 ⇒ None", none.Take() == JoinAnswer.None);
        }

        // ---------------------------------------------------------------- ⑤

        private static void Case5_KeepAnswerIntoBattle()
        {
            Console.WriteLine("\n⑤ 进 InBattle 时答案必须留着（注入层马上要用）");
            var c = new TeamJoinCore();
            string why;
            c.ShouldAsk(true, true, true, false, out why);
            c.SetAnswer(true);

            c.OnFlowStateChanged(BattleFlowState.PreBattle, BattleFlowState.InBattle, out why);
            Check("进战斗不作废", c.HasAnswer);
            Check("进战斗 why 为空", why == "", "why=" + why);
            Check("进战斗后 Take 仍拿到 Yes", c.Take() == JoinAnswer.Yes);
            Check("DroppedCount 仍是 0", c.DroppedCount == 0, "drops=" + c.DroppedCount);
        }

        // ---------------------------------------------------------------- ⑥

        private static void Case6_DropAfterBattle()
        {
            Console.WriteLine("\n⑥ 战后回 Idle ⇒ 表态作废");
            var c = new TeamJoinCore();
            string why;
            c.ShouldAsk(true, true, true, false, out why);
            c.SetAnswer(true);
            c.OnFlowStateChanged(BattleFlowState.PreBattle, BattleFlowState.InBattle, out why);

            // 注入没取走（比如这场是副本，不是决斗）⇒ 战后必须清
            c.OnFlowStateChanged(BattleFlowState.InBattle, BattleFlowState.Idle, out why);
            Check("战后清空", !c.HasAnswer);
            Check("战后 why 说明是'打完了'", why.Contains("战斗结束"), "why=" + why);
            Check("DroppedCount=1", c.DroppedCount == 1, "drops=" + c.DroppedCount);
            Check("战后 Take 拿到 None", c.Take() == JoinAnswer.None);
        }

        // ---------------------------------------------------------------- ⑦

        private static void Case7_DropWhenPreBattleEndsWithoutBattle()
        {
            Console.WriteLine("\n⑦ 战前流程结束（取消应战 / 没打成）⇒ 表态作废");
            var c = new TeamJoinCore();
            string why;
            c.ShouldAsk(true, true, true, false, out why);
            c.SetAnswer(false);
            c.OnFlowStateChanged(BattleFlowState.PreBattle, BattleFlowState.Idle, out why);
            Check("作废", !c.HasAnswer);
            Check("why 说明没打成", why.Contains("没打成"), "why=" + why);
            Check("DroppedCount=1", c.DroppedCount == 1, "drops=" + c.DroppedCount);
        }

        // ---------------------------------------------------------------- ⑧

        private static void Case8_NewEpisodeAsksAgain()
        {
            Console.WriteLine("\n⑧ 新的战前 ⇒ 可以再问");
            var c = new TeamJoinCore();
            string why;
            c.ShouldAsk(true, true, true, false, out why);
            c.SetAnswer(true);
            c.OnFlowStateChanged(BattleFlowState.PreBattle, BattleFlowState.Idle, out why);   // 没打成

            c.OnFlowStateChanged(BattleFlowState.Idle, BattleFlowState.PreBattle, out why);
            Check("新战前可以再问", c.ShouldAsk(true, true, true, false, out why));
            Check("AskCount=2", c.AskCount == 2, "asks=" + c.AskCount);

            // 上一次的答案绝不能带进新一场
            Check("新一场没有残留答案", !c.HasAnswer);
        }

        // ---------------------------------------------------------------- ⑨

        private static void Case9_ResetClearsEverything()
        {
            Console.WriteLine("\n⑨ Reset（换世界）⇒ 全清");
            var c = new TeamJoinCore();
            string why;
            c.ShouldAsk(true, true, true, false, out why);
            c.SetAnswer(true);
            c.Reset();
            Check("Reset 后无答案", !c.HasAnswer);
            Check("Reset 后未问", !c.Asked);
            Check("Reset 后不在 episode", !c.Episode);
            Check("Reset 后仍能问", c.ShouldAsk(true, true, true, false, out why));
        }

        // ---------------------------------------------------------------- ⑩

        /// <summary>
        /// 表态过之后，即使流程重进一次战前，也**不许再问**。
        ///
        /// 为什么这个形态是真的（10-26 复核）：我们的确认窗开在应战窗**之上**，
        /// 层顶换人 ⇒ `BattlePreShowing()` 变假 ⇒ `BattleFlowGate` 可能先退一次战前；
        /// 玩家点完我们的窗后应战窗又回到层顶 ⇒ 流程再进一次 `PreBattle`（那里复位 `_asked`）。
        /// 没有这条守卫就是**第二次弹窗**，而第二次的选择会把第一次的顶掉
        /// （玩家点了"一起应战"，再被问一次点了"我一个人" ⇒ 队友就是不来）。
        /// </summary>
        private static void Case10_NoSecondAskAfterAnswer()
        {
            Console.WriteLine("\n⑩ 已表态 ⇒ 重进战前也不再问，答案保住");
            var c = new TeamJoinCore();
            string why;
            Check("第一次问", c.ShouldAsk(true, true, true, false, out why));
            c.SetAnswer(true);

            c.OnFlowStateChanged(BattleFlowState.Idle, BattleFlowState.PreBattle, out why);   // 窗盖住又回来
            bool asked = c.ShouldAsk(true, true, true, false, out why);
            Check("重进战前不再问", !asked, "why=" + why);
            Check("why 说明已表态", !asked && why.Contains("已表态"), "why=" + why);
            Check("AskCount 仍是 1", c.AskCount == 1, "asks=" + c.AskCount);
            Check("答案仍是 Yes", c.Answer == JoinAnswer.Yes, "answer=" + c.Answer);
            Check("Take 仍拿到 Yes", c.Take() == JoinAnswer.Yes);
        }
    }
}
