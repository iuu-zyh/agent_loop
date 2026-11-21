// 历史回放分帧构建策略核（HistoryBuildCore）的单元测试。
//
// 为什么要有这个工程：11-20 单聊窗 575 条历史回放**卡住 1.13 秒**
// （`[MFrame][FrameStall] ms=770` + 紧接着 `ms=358`，期间连 ESC 都不响应）。
// 根因不是哪一步特别慢，是**575 行全塞进同一帧**（克隆节点树 + 挂按钮 + 文字网格）。
//
// 修法是把构建摊到多帧。但"摊多少"错的方向有两条，而且**两条都不报错**：
//   ① 摊太多（每帧几行）→ 575 行拖成几百帧，屏幕十几秒画不全，比卡一下更糟；
//   ② 摊太少（每帧几十行）→ 单帧仍然超预算，等于没修。
// 所以把判据钉在表里，而不是等实机"看着不卡"。
//
// ⚠ 诚实边界：这里只测**判据**。"有没有接上、是否真的每帧只建这么多"由 Python 侧
//   源码契约测试 tests/test_history_build_contract.py 锁在真实 ChatWindow.cs 上 ——
//   单测全绿 + 一处没接 = 用户侧一点变化都没有。

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

        private static void Eq(string name, int got, int want) =>
            Check(name, got == want, "实际=" + got + " 期望=" + want);

        private static void True(string name, bool got) => Check(name, got, "实际=false 期望=true");
        private static void False(string name, bool got) => Check(name, !got, "实际=true 期望=false");

        private static int Main()
        {
            Console.WriteLine("=== HistoryBuildCore 单元测试 ===");
            Section("常量（阈值就是权衡本身）", Constants);
            Section("分帧门槛：小回放保持同步", DeferGate);
            Section("★保底推进：任何一帧都不许原地踏步", AlwaysProgress);
            Section("封顶：便宜的行也不许一口气全建完", FrameCap);
            Section("预算：超了就收手", BudgetStop);
            Section("★575 行的真实场景：帧数必须落在合理区间", Realistic575);
            Section("边界：0 条 / 负数不崩", EdgeCases);
            Console.WriteLine();
            if (_fails.Count == 0)
            {
                Console.WriteLine("全部通过：" + _pass + " 项断言");
                return 0;
            }
            Console.WriteLine("失败 " + _fails.Count + " 项 / 通过 " + _pass + " 项");
            return 1;
        }

        private static void Section(string name, Action body)
        {
            try { body(); }
            catch (Exception e)
            {
                // 一节抛了也要如实记成"这一节失败"并继续跑后面 —— 崩在第一行会让后面全没跑，
                // 看起来像"测试通过"（ToolFlowPolicyTests 踩过这个坑）
                Check("小节「" + name + "」不应抛异常", false, e.GetType().Name + ": " + e.Message);
            }
        }

        // ---------------------------------------------------------------- 各节

        private static void Constants()
        {
            // 这几个数是"卡不卡"的全部权衡，改它们等于改行为，必须显式确认过一次
            Check("单帧预算 8ms（60fps 的一半）",
                  Math.Abs(HistoryBuildCore.FrameBudgetMs - 8.0) < 1e-9,
                  "实际=" + HistoryBuildCore.FrameBudgetMs);
            Eq("同步门槛 60 条", HistoryBuildCore.SyncTotalLimit, 60);
            Eq("每帧保底 6 行", HistoryBuildCore.MinRowsPerFrame, 6);
            Eq("每帧封顶 40 行", HistoryBuildCore.MaxRowsPerFrame, 40);
        }

        private static void DeferGate()
        {
            // 小回放同步：分帧会让"历史还没画全"这个中间态暴露出来，而省下的耗时是零
            False("0 条不分帧", HistoryBuildCore.ShouldDefer(0));
            False("1 条不分帧", HistoryBuildCore.ShouldDefer(1));
            False("18 条（本次群聊）不分帧", HistoryBuildCore.ShouldDefer(18));
            False("60 条（正好门槛）不分帧", HistoryBuildCore.ShouldDefer(60));
            // 超过就分：100 条 ≈ 134ms，人已经能察觉到一顿
            True("61 条（刚过门槛）分帧", HistoryBuildCore.ShouldDefer(61));
            True("575 条（本次单聊）分帧", HistoryBuildCore.ShouldDefer(575));
            True("1000 条（窗口上限）分帧", HistoryBuildCore.ShouldDefer(1000));
        }

        private static void AlwaysProgress()
        {
            // ★ 这一节防的是"修完变慢"★
            // 若判据允许"一行都没建就收手"，调用方按帧轮询就永远推不动；
            // 若允许"第一条就超预算就收手"，575 行会分摊到 575 帧（≈10 秒）。
            False("一行没建时不许收手", HistoryBuildCore.ShouldStopFrame(0, 100, 999.0));
            False("建了 1 行、预算已爆也不许收手", HistoryBuildCore.ShouldStopFrame(1, 100, 999.0));
            False("建了 5 行（未到保底 6）、预算已爆也不许收手",
                  HistoryBuildCore.ShouldStopFrame(5, 100, 999.0));
            // 到保底且预算爆了 ⇒ 可以收手
            True("建满保底 6 行且预算已爆 ⇒ 收手",
                 HistoryBuildCore.ShouldStopFrame(6, 100, 999.0));
        }

        private static void FrameCap()
        {
            // 便宜的行（比如 0.1ms/行）会让"只看预算"一口气建完 40 条以上 ⇒ 退化成同步构建
            True("建满封顶 40 行、预算还剩 ⇒ 也要收手",
                 HistoryBuildCore.ShouldStopFrame(40, 100, 0.0));
            True("建了 41 行 ⇒ 收手", HistoryBuildCore.ShouldStopFrame(41, 100, 0.0));
            False("建了 39 行、预算还剩 ⇒ 继续",
                  HistoryBuildCore.ShouldStopFrame(39, 100, 0.0));
        }

        private static void BudgetStop()
        {
            // 富文本行 ~5ms/行：建到第 2 行就已经 10ms，超过 8ms 预算
            False("建 2 行花 10ms —— 但未到保底 6 行，必须继续",
                  HistoryBuildCore.ShouldStopFrame(2, 100, 10.0));
            True("建 10 行花 10ms —— 到保底且超预算 ⇒ 收手",
                 HistoryBuildCore.ShouldStopFrame(10, 100, 10.0));
            False("建 10 行花 7ms —— 预算还剩 ⇒ 继续",
                  HistoryBuildCore.ShouldStopFrame(10, 100, 7.0));
            False("建 10 行花 8ms —— 正好到预算，判据是 >= 但这里取 7.9",
                  HistoryBuildCore.ShouldStopFrame(10, 100, 7.9));
            True("建 10 行花 8ms —— 正好到预算 ⇒ 收手",
                 HistoryBuildCore.ShouldStopFrame(10, 100, 8.0));
        }

        private static void Realistic575()
        {
            // 575 条是本 bug 的真实规模。按 1.34ms/行 + 8ms 预算 + 保底 6 行：
            // 每帧约 6 行 ⇒ ~96 帧 ≈ 1.6 秒 @60fps。
            // 关键是**没有一帧是卡的**（每帧 ≤8ms 构建），总时长略长但体感完全不同。
            int frames = HistoryBuildCore.EstimateFrames(575);
            Check("575 条的帧数估算落在 60~200 帧之间（过少=没分，过多=拖沓）",
                  frames >= 60 && frames <= 200, "实际=" + frames + " 帧");

            // 模拟跑一遍：确认真的能建完、且每帧都推进了
            int built = 0, frameCount = 0;
            while (built < 575 && frameCount < 5000)
            {
                frameCount++;
                int before = built;
                while (!HistoryBuildCore.ShouldStopFrame(built - before, 575 - built, (built - before) * 1.34))
                {
                    built++;
                    if (built >= 575) break;
                }
                Check("第 " + frameCount + " 帧必须有推进", built > before);
                if (built <= before) break;   // 死循环防线：不推进就直接退出，让上面那条断言说话
            }
            Eq("模拟跑完必须正好建 575 条", built, 575);
            // 每帧不超过封顶
            Check("总帧数不低于保底估算（575/40 = 15 帧）", frameCount >= 15, "实际=" + frameCount);
        }

        private static void EdgeCases()
        {
            True("remaining=0 ⇒ 收手（建完了）", HistoryBuildCore.ShouldStopFrame(0, 0, 0.0));
            True("remaining 为负 ⇒ 收手（不崩）", HistoryBuildCore.ShouldStopFrame(3, -5, 0.0));
            Eq("EstimateFrames(0) = 0", HistoryBuildCore.EstimateFrames(0), 0);
            Eq("EstimateFrames(负数) = 0", HistoryBuildCore.EstimateFrames(-10), 0);
            Eq("EstimateFrames(60) = 1 帧（同步）", HistoryBuildCore.EstimateFrames(60), 1);
        }
    }
}
