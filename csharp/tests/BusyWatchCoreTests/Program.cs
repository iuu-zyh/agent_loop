// 「玩家忙 / 战斗中」观测状态机（BusyWatchCore）单元测试。
//
// 报障原话（11-19）："有用户反映检测会频繁显示自己正在忙或者正在处于战斗当中，但是实际不是。"
//
// 这条报障最麻烦的地方是**它没有任何报错**：判据照常返回一个非空原因串，只是那个串与玩家
// 看到的画面对不上。要定位它，日志必须回答三个问题，本核就对应这三件事：
//   ① **抖不抖**（进忙/出忙的边沿次数）—— "频繁"是可数的，不是感觉；
//   ② **卡不卡**（同一原因持续多久）—— "一直忙"与"每秒忙一下"是两种病；
//   ③ **是哪条判据在说忙**（原因串 + 当时的原始电平，后者在接线层拼）。
//
// 覆盖（每条都对应一个会静默出事的规则）：
//   ① 首拍立基准：开局不报"进忙"（加载中判定为忙很正常，打出来只是噪音）；
//   ② 进忙 / 出忙 / 原因变化三种边沿各报一次，且带上"上一段持续了多久"；
//   ③ 持续忙到 30 秒补一行（抓"卡住不放"），不到就不打（否则刷屏）；
//   ④ 抖动统计：60 秒窗口到点报一次；窗口**必须清零**（不清零会把"频繁"抹平）；
//   ⑤ 统计与实质事件同拍：实质事件优先，统计留到下一拍（一行日志只装一件事）；
//   ⑥ 时间不前进（游戏暂停/加载屏）不判边沿、不累计；
//   ⑦ 世界重载 Reset 后统计归零、下一拍不报"出忙"。

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
                  "实际=" + got + " 期望=" + want);

        private static BusyWatchCore New() => new BusyWatchCore();

        /// <summary>喂一拍（模拟 1 秒采样间隔）。</summary>
        private static BusyTickResult At(BusyWatchCore c, double t, bool busy, string reason = "") =>
            c.Tick(t, busy, busy ? (string.IsNullOrEmpty(reason) ? "战斗中" : reason) : "");

        private static int Main()
        {
            Console.WriteLine("BusyWatchCore 单元测试（持续补报 " + BusyWatchCore.StuckLogSeconds
                              + "s / 统计窗 " + BusyWatchCore.StatSeconds + "s / 抖动阈 "
                              + BusyWatchCore.JitterEnters + " 次）");

            TestFirstTickIsBaseline();
            TestEdges();
            TestStillBusyCadence();
            TestStatWindowResets();
            TestStatYieldsToRealEvent();
            TestFrozenClock();
            TestSampleGap();
            TestResetOnWorldChange();

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

        // ---------- ① 首拍立基准 ----------

        private static void TestFirstTickIsBaseline()
        {
            var c = New();
            Eq("首拍（忙）不报事件", At(c, 0, true).Kind, BusyEvent.None);
            Check("首拍之后状态已是忙", c.Busy);
            // 关键的静默风险：首拍若报"进忙"，每次进游戏都会有一行，真故障被淹。
            var c2 = New();
            Eq("首拍（闲）也不报事件", At(c2, 0, false).Kind, BusyEvent.None);
            Console.WriteLine("✓ ① 首拍只立基准 通过");
        }

        // ---------- ② 三种边沿 ----------

        private static void TestEdges()
        {
            var c = New();
            At(c, 0, false);

            var on = At(c, 5, true, "战斗中");
            Eq("闲→忙 ⇒ EnterBusy", on.Kind, BusyEvent.EnterBusy);
            Eq("EnterBusy 带上原因", on.Detail, "战斗中");
            Check("EnterBusy 带上一段时长（5s）", Math.Abs(on.SegSeconds - 5) < 0.01,
                  "实际=" + on.SegSeconds);

            var chg = At(c, 8, true, "Mask 层有模态遮罩：UIConfirmDrama");
            Eq("忙→忙但原因变了 ⇒ ReasonChanged", chg.Kind, BusyEvent.ReasonChanged);
            Eq("ReasonChanged 带新原因", chg.Detail, "Mask 层有模态遮罩：UIConfirmDrama");
            Check("ReasonChanged 带同段已忙时长（3s）", Math.Abs(chg.SegSeconds - 3) < 0.01,
                  "实际=" + chg.SegSeconds);

            var off = At(c, 10, false);
            Eq("忙→闲 ⇒ LeaveBusy", off.Kind, BusyEvent.LeaveBusy);
            Eq("LeaveBusy 带上一段的原因（出忙后要能知道刚才为什么忙）",
               off.Detail, "Mask 层有模态遮罩：UIConfirmDrama");
            Check("LeaveBusy 带忙了多久（2s）", Math.Abs(off.SegSeconds - 2) < 0.01,
                  "实际=" + off.SegSeconds);

            Eq("状态没变 ⇒ None", At(c, 11, false).Kind, BusyEvent.None);
            Console.WriteLine("✓ ② 进忙/原因变化/出忙 通过");
        }

        // ---------- ③ 持续忙补报 ----------

        private static void TestStillBusyCadence()
        {
            var c = New();
            At(c, 0, true);
            // 29 秒：不该补报（否则等于每秒一行，把日志变噪音）
            bool reported = false;
            for (int t = 1; t <= 29; t++)
                if (At(c, t, true).Kind == BusyEvent.StillBusy) reported = true;
            Check("忙 29s 不补报（不到间隔）", !reported);

            // 30 秒：补一行
            var r = At(c, 30, true);
            Eq("忙满 30s ⇒ StillBusy", r.Kind, BusyEvent.StillBusy);
            Eq("StillBusy 带原因", r.Detail, "战斗中");
            Check("StillBusy 带已忙时长（30s）", Math.Abs(r.SegSeconds - 30) < 0.01,
                  "实际=" + r.SegSeconds);

            // 补报之后要重新计时：31 秒不该再补
            Eq("补报后下一拍不重复补", At(c, 31, true).Kind, BusyEvent.None);
            // 再满 30s 又补一行（逐秒推进，保持真实采样节奏 —— 一次跳过 30s 会
            // 被当成"采样缺口"，那是另一条规则，见 ⑧）
            bool again = false;
            for (int t = 32; t <= 60; t++)
                if (At(c, t, true).Kind == BusyEvent.StillBusy) again = true;
            Check("再满 30s 又补一行", again);

            // 阈值本身要落在一个"能抓卡死、又不刷屏"的区间
            Check("补报间隔在 10~120s 之间", BusyWatchCore.StuckLogSeconds >= 10
                  && BusyWatchCore.StuckLogSeconds <= 120, "实际=" + BusyWatchCore.StuckLogSeconds);
            Console.WriteLine("✓ ③ 持续忙补报节奏 通过");
        }

        // ---------- ④ 抖动统计 ----------

        private static void TestStatWindowResets()
        {
            var c = New();
            At(c, 0, false);
            // 60 秒里进出 12 次（模拟抖动）
            double t = 0;
            int enters = 0;
            for (int i = 0; i < 12; i++)
            {
                t += 2; At(c, t, true); enters++;
                t += 3; At(c, t, false);
            }
            // 现在 t = 60，窗口到点 —— 下一拍应给 Stat
            var stat = At(c, t + 0.5, false);
            Eq("窗口到点 ⇒ Stat", stat.Kind, BusyEvent.Stat);
            Eq("Stat 进忙次数正确", stat.Enters, enters);
            Check("Stat 累计忙时长 > 0", stat.BusySeconds > 0, "实际=" + stat.BusySeconds);
            Check("进忙次数达到抖动阈值（应能被日志标注）",
                  stat.Enters >= BusyWatchCore.JitterEnters, "实际=" + stat.Enters);

            // ★清零★ 不清零的话下一窗口会把上一窗口的次数带上，永远居高不下
            double t2 = t + 0.5;
            At(c, t2 + 1, false);
            // 逐拍推进到下一个窗口边界（一次跳 60s 会被当成采样缺口）
            BusyTickResult stat2 = default(BusyTickResult);
            bool got2 = false;
            for (double u = t2 + 2; u <= t2 + BusyWatchCore.StatSeconds + 1; u += 1)
            {
                var rr = At(c, u, false);
                if (rr.Kind == BusyEvent.Stat) { stat2 = rr; got2 = true; break; }
            }
            Check("下一窗口统计归零后的 Stat", got2);
            Eq("下一窗口内没有进忙 ⇒ 0 次", stat2.Enters, 0);

            Check("抖动阈值在 3~30 之间", BusyWatchCore.JitterEnters >= 3
                  && BusyWatchCore.JitterEnters <= 30, "实际=" + BusyWatchCore.JitterEnters);
            Console.WriteLine("✓ ④ 抖动统计窗口与清零 通过");
        }

        // ---------- ⑤ 统计让位于实质事件 ----------

        private static void TestStatYieldsToRealEvent()
        {
            var c = New();
            At(c, 0, false);
            // 逐拍走到窗口边界，最后一拍同时发生"进忙"：实质事件必须先出
            for (double u = 1; u < BusyWatchCore.StatSeconds; u += 1) At(c, u, false);
            var r = At(c, BusyWatchCore.StatSeconds, true);
            Eq("窗口边界 + 进忙 ⇒ 先报 EnterBusy", r.Kind, BusyEvent.EnterBusy);
            // 下一拍（状态不变）才轮到 Stat —— 那一行"近 60s 进忙 N 次"不能被现场淹没
            var r2 = At(c, BusyWatchCore.StatSeconds + 1, true);
            Eq("下一拍补上 Stat", r2.Kind, BusyEvent.Stat);
            Console.WriteLine("✓ ⑤ 实质事件优先于统计 通过");
        }

        // ---------- ⑥ 时间不前进 ----------

        private static void TestFrozenClock()
        {
            var c = New();
            At(c, 10, false);
            At(c, 11, true);
            // 游戏暂停：时刻不前进。不判边沿、不累计、不补报。
            Eq("时刻不前进 ⇒ 不判", At(c, 11, false).Kind, BusyEvent.None);
            Eq("仍保持忙（暂停不是出忙）", At(c, 11, true).Kind, BusyEvent.None);
            Check("暂停期间状态仍是忙", c.Busy);

            // 时钟回退（极端情况）也不判
            Eq("时刻回退 ⇒ 不判", At(c, 5, false).Kind, BusyEvent.None);
            // 恢复前进后能正常判出边沿（小步推进，避免被判成采样缺口）
            // 回退到 5 之后，下一拍不能一次跳超过 MaxDtSeconds，否则会先撞上"采样缺口"
            Eq("恢复前进后能判出边沿", At(c, 9.5, false).Kind, BusyEvent.LeaveBusy);
            Console.WriteLine("✓ ⑥ 时间不前进时不判 通过");
        }

        // ---------- ⑧ 采样缺口（帧回调丢了的旁证） ----------

        private static void TestSampleGap()
        {
            // 正常 1s 采样。突然隔很久才来一拍 ⇒ 这段"我们没在看"：
            //   ① 加载屏/暂停（时间停住后跳一段）；
            //   ② 帧回调死了（11-19 那次故障的形状）。
            // 两者都必须报一行，否则这段空白永远无法解释。
            var c = New();
            At(c, 0, false);
            At(c, 1, true);
            var gap = At(c, 1 + BusyWatchCore.MaxDtSeconds + 10, true);
            Eq("隔太久 ⇒ SampleGap", gap.Kind, BusyEvent.SampleGap);
            Check("缺口时长要报出来", gap.SegSeconds > BusyWatchCore.MaxDtSeconds,
                  "实际=" + gap.SegSeconds);
            Eq("缺口带中断前的原因", gap.Detail, "战斗中");

            // ★缺口之后必须重新立基准★ 缺口里的忙闲不可知，拿它当"上一段"会算出假时长。
            // 首拍不报边沿这条规则同样适用。
            Eq("缺口后首拍不报边沿", At(c, 1 + BusyWatchCore.MaxDtSeconds + 11, false).Kind,
               BusyEvent.None);
            // 边界：正好等于 MaxDtSeconds 不算缺口（正常的长卡），大于才算
            var c2 = New();
            At(c2, 0, false);
            Eq("正好等于上限不算缺口", At(c2, BusyWatchCore.MaxDtSeconds, false).Kind, BusyEvent.None);
            var c3 = New();
            At(c3, 0, false);
            Eq("超过上限算缺口", At(c3, BusyWatchCore.MaxDtSeconds + 0.1, false).Kind, BusyEvent.SampleGap);
            Console.WriteLine("✓ ⑧ 采样缺口留证 通过");
        }

        // ---------- ⑦ 世界重载 ----------

        private static void TestResetOnWorldChange()
        {
            var c = New();
            At(c, 0, false);
            At(c, 10, true);
            At(c, 40, true);                 // 触发补报，且累计了忙时长
            Check("重载前是忙", c.Busy);

            c.Reset();
            Check("Reset 后不再是忙", !c.Busy);
            Eq("Reset 后原因为空", c.Reason, "");

            // ★关键★ 重载后第一拍只立基准，绝不报"出忙" ——
            //   否则每次读档都会有一行"出忙（忙了 900s）"这种假时长。
            Eq("重载后首拍不报事件", At(c, 100, false).Kind, BusyEvent.None);

            // 统计也归零：新世界从零开始数（逐拍推进到窗口边界）
            BusyTickResult stat = default(BusyTickResult);
            bool got = false;
            for (double u = 101; u <= 100 + BusyWatchCore.StatSeconds + 1; u += 1)
            {
                var rr = At(c, u, false);
                if (rr.Kind == BusyEvent.Stat) { stat = rr; got = true; break; }
            }
            Check("重载后首个统计窗口给 Stat", got);
            Eq("新世界窗口内进忙 0 次", stat.Enters, 0);
            Console.WriteLine("✓ ⑦ 世界重载归零 通过");
        }
    }
}
