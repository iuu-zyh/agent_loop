// 两段确认（TwoStepConfirm）单元测试。
//
// 为什么要有这个工程：删除文件 / 重置为默认都是**不可恢复**操作，全靠这段判定把关。
// 它一旦走偏，症状是两种极端：
//   ① 太松（没武装就直接执行 / 过期后仍执行）→ 用户一次误点就丢了手写的提示词；
//   ② 太紧（武装后永远判过期 / 执行后没解除武装）→ 按钮变成"点了没反应"或"点一次执行两次"。
// 这些边界（恰好 3.0 秒、过期后再点、执行后第三次点击、时钟倒退）在实机上都**没法稳定复现**，
// 所以钉在判定表里。
//
// ⚠ 诚实边界：真实的按钮接线（哪两个按钮用它、文案怎么切、换文件时解除）依赖 Unity，
//   WSL 里跑不起来。这里只测**规则**；"接线有没有被改回去"由 Python 侧源码契约测试
//   tests/test_prompt_reset_ui_contract.py 锁在真实 ConfigPresenter.cs 上 —— 两边合起来才完整。

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

        private static int Main()
        {
            Console.WriteLine("=== TwoStepConfirm 单元测试 ===");
            Section("基础两段", Basic);
            Section("窗口边界", WindowBoundary);
            Section("过期与重来", ExpireAndRestart);
            Section("解除武装", DisarmCases);
            Section("不变量", Invariants);
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
                // 一节里抛了也要如实记成"这一节失败"并继续跑后面的小节 —— 崩在第一行会让
                // 后面全都没跑，看起来像"测试通过"（ToolFoldPolicyTests 踩过这个坑）
                Check("★ 小节抛异常：" + name, false, e.GetType().Name + ": " + e.Message);
            }
        }

        // ------------------------------------------------------------------
        // 基础两段
        // ------------------------------------------------------------------

        private static void Basic()
        {
            var c = new TwoStepConfirm();
            Eq("默认窗口 3 秒（与删除流程既有值一致）", TwoStepConfirm.DefaultWindow, 3f);
            Eq("初始未武装", c.Armed, false);

            Eq("首击 = First（只武装）", c.Click(10f), TwoStepConfirm.Step.First);
            Eq("首击后 Armed=true", c.Armed, true);
            Eq("首击记录时刻", c.ArmedAt, 10f);

            Eq("窗口内再击 = Second（执行）", c.Click(11.5f), TwoStepConfirm.Step.Second);
            Eq("执行后自动解除武装", c.Armed, false);
            Eq("执行后时刻归零", c.ArmedAt, 0f);

            Eq("第三次点击重新武装（不会连着执行两次）", c.Click(12f), TwoStepConfirm.Step.First);
            Eq("于是第四次在窗口内 = Second", c.Click(12.5f), TwoStepConfirm.Step.Second);

            // 未武装时任何时刻点击都只能是 First —— "没武装却直接执行"是最危险的一种错
            var fresh = new TwoStepConfirm();
            Eq("未武装 + 巨量时间流逝 = First", fresh.Click(99999f), TwoStepConfirm.Step.First);
        }

        // ------------------------------------------------------------------
        // 窗口边界：判定是**严格大于**（恰好 3.0 秒仍算来得及）
        // ------------------------------------------------------------------

        private static void WindowBoundary()
        {
            var c = new TwoStepConfirm();
            c.Click(0f);
            Eq("恰好 3.000 秒（等于窗口）= Second", c.Click(3f), TwoStepConfirm.Step.Second);

            var c2 = new TwoStepConfirm();
            c2.Click(0f);
            Eq("刚过窗口 3.0001 秒 = Expired", c2.Click(3.0001f), TwoStepConfirm.Step.Expired);

            var c3 = new TwoStepConfirm();
            c3.Click(0f);
            Eq("差一点 2.9999 秒 = Second", c3.Click(2.9999f), TwoStepConfirm.Step.Second);

            // 自定义窗口
            var w = new TwoStepConfirm(1f);
            w.Click(0f);
            Eq("自定义窗口 1 秒：0.9 秒内 = Second", w.Click(0.9f), TwoStepConfirm.Step.Second);
            var w2 = new TwoStepConfirm(1f);
            w2.Click(0f);
            Eq("自定义窗口 1 秒：1.1 秒 = Expired", w2.Click(1.1f), TwoStepConfirm.Step.Expired);

            // 非法窗口（0 / 负数）回落默认：否则"确认"形同虚设（任何第二次点击都算执行）
            var z = new TwoStepConfirm(0f);
            z.Click(0f);
            Eq("窗口 0 → 回落默认：2.9 秒仍 = Second", z.Click(2.9f), TwoStepConfirm.Step.Second);
            var n = new TwoStepConfirm(-5f);
            n.Click(0f);
            Eq("窗口负数 → 回落默认：3.1 秒 = Expired", n.Click(3.1f), TwoStepConfirm.Step.Expired);
        }

        // ------------------------------------------------------------------
        // 过期与重来
        // ------------------------------------------------------------------

        private static void ExpireAndRestart()
        {
            var c = new TwoStepConfirm();
            c.Click(100f);
            Eq("隔了很久再点 = Expired（不是 Second）", c.Click(5000f), TwoStepConfirm.Step.Expired);
            Eq("过期后自动解除武装", c.Armed, false);
            Eq("过期后再点 = First（重新开始，不卡死）", c.Click(5001f), TwoStepConfirm.Step.First);
            Eq("于是又一次窗口内点击 = Second", c.Click(5002f), TwoStepConfirm.Step.Second);

            // 时钟倒退（Time.time 在场景重载后理论上可能变小）不误判为过期 ——
            // 与内联时代的 `Time.time - _deleteArmTime > 3f` 行为一致
            var b = new TwoStepConfirm();
            b.Click(100f);
            Eq("时钟倒退不算过期（= Second）", b.Click(50f), TwoStepConfirm.Step.Second);
        }

        // ------------------------------------------------------------------
        // 解除武装
        // ------------------------------------------------------------------

        private static void DisarmCases()
        {
            var c = new TwoStepConfirm();
            c.Click(0f);
            c.Disarm();
            Eq("Disarm 后 Armed=false", c.Armed, false);
            Eq("Disarm 后点击 = First（换目标文件即作废）", c.Click(0.5f), TwoStepConfirm.Step.First);

            var c2 = new TwoStepConfirm();
            c2.Disarm();
            Eq("未武装时 Disarm 是幂等空操作（不抛）", c2.Armed, false);
            Eq("未武装时 Disarm 后点击 = First", c2.Click(1f), TwoStepConfirm.Step.First);

            var c3 = new TwoStepConfirm();
            c3.Click(0f);
            c3.Disarm();
            c3.Disarm();
            Eq("连续 Disarm 幂等", c3.Armed, false);
        }

        // ------------------------------------------------------------------
        // 不变量（跨随机序列）
        // ------------------------------------------------------------------

        private static void Invariants()
        {
            // 序列模拟：arming → 各类间隔 → 检查"执行"永远发生在一次"武装"之后
            float[] gaps = { 0f, 0.001f, 1f, 2.9999f, 3f, 3.0001f, 10f };
            int executions = 0;
            for (int i = 0; i < gaps.Length; i++)
            {
                var c = new TwoStepConfirm();
                c.Click(1000f);
                var step = c.Click(1000f + gaps[i]);
                bool armedBefore = true;   // 上一行刚武装过
                if (step == TwoStepConfirm.Step.Second)
                {
                    executions++;
                    Check("间隔 " + gaps[i] + "s：Second 必在武装之后", armedBefore);
                    Check("间隔 " + gaps[i] + "s：Second 后不残留武装", !c.Armed);
                }
                else
                {
                    Check("间隔 " + gaps[i] + "s：只可能是 Expired", step == TwoStepConfirm.Step.Expired);
                    Check("间隔 " + gaps[i] + "s：Expired 后不残留武装", !c.Armed);
                    Check("间隔 " + gaps[i] + "s：过期后再点必是 First",
                          c.Click(2000f) == TwoStepConfirm.Step.First);
                }
            }
            Eq("判定表：≤3s 的 5 种间隔执行、>3s 的 2 种过期（合计 5 次执行）", executions, 5);
        }
    }
}
