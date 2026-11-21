// 气泡宽度策略核（BubbleWidthCore）的单元测试。
//
// 为什么要有这个工程：11-19 用户报「气泡全挤在左侧」，根因是气泡盒在 AB 预制体里是
// **死宽 450**（`NpcBubble/UserBubble` 的 `m_SizeDelta.x = 450`，而行模板 HLG 是
// `m_ChildControlWidth = 0` ⇒ 布局永不覆盖它）。改成"跟内容区走"之后，错的方向有两条，
// 而且**两条都不会报错**：
//   ① 上限算宽了 → 长气泡撑出窗口（穿模），玩家截图才会发现；
//   ② 上限算窄了 → 整屏气泡缩成一条线，比改之前更糟。
// 所以把算式钉在表里，而不是等实机"看着不对"。
//
// ⚠ 诚实边界：这里只测**算式**。"有没有接上、读的是不是那几个 rect"由 Python 侧源码
//   契约测试 tests/test_chat_bubble_layout_contract.py 锁在真实 ChatItemViews.cs /
//   ChatUiBuilder.cs 上 —— 单测全绿 + 一处没接 = 用户侧一点变化都没有。

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

        private static void Eq(string name, float got, float want, float eps = 0.01f) =>
            Check(name, Math.Abs(got - want) <= eps, "实际=" + got + " 期望=" + want);

        private static int Main()
        {
            Console.WriteLine("=== BubbleWidthCore 单元测试 ===");
            Section("常量（用户拍板的那两个数）", Constants);
            Section("上限：内容宽 − 头像 − 间距 − 安全边距", CapMath);
            Section("上限不许低于最小宽（内容区脏值时）", CapFloor);
            Section("单行所需宽（含左右内边距 + 富余）", NeededMath);
            Section("取小者：短句贴合、长句夹到上限", FitMath);
            Section("★写入闸：量不出文字宽就不写（11-20 满宽 bug）", WriteGate);
            Section("★硬闸：气泡不许超出内容区（11-20 群聊溢出）", ContentClamp);
            Section("去抖：变化 < 1.5 不重写", RewriteGate);
            Section("回退判据：内容宽 < 200 当没布局好", ContentGate);
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
            Eq("最小气泡宽", BubbleWidthCore.MinWidth, 96f);
            Eq("两侧安全边距合计", BubbleWidthCore.SafetyMargin, 24f);
            Eq("内容宽下限", BubbleWidthCore.MinContentWidth, 200f);
            Eq("全失败时的兜底内容宽", BubbleWidthCore.FallbackContentWidth, 800f);
            Eq("去抖阈值", BubbleWidthCore.RewriteEpsilon, 1.5f);
        }

        private static void CapMath()
        {
            // 真机量级：BG 1200、Scroll 1170、Content=Viewport ⇒ 内容区 ~1170；头像 80、间距 6
            Eq("1170 宽 + 头像 80 ⇒ 1060", BubbleWidthCore.Cap(1170f, 80f, 6f, 24f), 1060f);
            // 单聊头像被隐藏：兄弟宽 0、间隙 0 ⇒ 整宽都归气泡
            Eq("头像隐藏 ⇒ 只减边距", BubbleWidthCore.Cap(1170f, 0f, 0f, 24f), 1146f);
            // 老面板（代码版 520）也别算出负数
            Eq("520 宽 + 头像 80 ⇒ 410", BubbleWidthCore.Cap(520f, 80f, 6f, 24f), 410f);
        }

        private static void CapFloor()
        {
            // 内容区还是 0（开窗那一帧）→ 不许返回负数/极小值，否则气泡缩成一条线
            Eq("内容宽 0 ⇒ 抬到最小宽", BubbleWidthCore.Cap(0f, 80f, 6f, 24f), 96f);
            Eq("负数输入 ⇒ 抬到最小宽", BubbleWidthCore.Cap(-50f, 0f, 0f, 24f), 96f);
            Eq("刚好被头像吃掉 ⇒ 抬到最小宽", BubbleWidthCore.Cap(150f, 80f, 6f, 24f), 96f);
        }

        private static void NeededMath()
        {
            // 实测样本：40 字中文、fontSize 19 ⇒ 单行约 760；气泡 padding 左 40 右 14
            Eq("长句单行所需", BubbleWidthCore.Needed(760f, 40f, 14f), 816f);
            Eq("短句（2 字 ≈ 38）", BubbleWidthCore.Needed(38f, 40f, 14f), 96f);   // 抬到最小宽
            Eq("刚好过最小宽", BubbleWidthCore.Needed(120f, 40f, 14f), 176f);
            Eq("空文字也只给最小宽", BubbleWidthCore.Needed(0f, 0f, 0f), 96f);
        }

        private static void FitMath()
        {
            Eq("短句贴合自己", BubbleWidthCore.Fit(200f, 1060f), 200f);
            Eq("长句夹到上限", BubbleWidthCore.Fit(5000f, 1060f), 1060f);
            Eq("上限脏（< 最小宽）⇒ 先抬到最小宽再比", BubbleWidthCore.Fit(300f, 10f), 96f);
            Eq("正好等于上限", BubbleWidthCore.Fit(1060f, 1060f), 1060f);
        }

        /// <summary>
        /// 11-20 补：群聊溢出的**最后一道硬闸**。
        ///
        /// 用户的群聊截图里有一批气泡被右侧裁掉（`RectMask2D` 裁的）。
        /// `Cap()` 减头像/间距都依赖**读数**，回放/换档那一帧读到脏值就会算出偏大的 cap，
        /// `Fit()` 忠实照夹 ⇒ 溢出。所以再加一道不依赖任何兄弟节点读数的兜底：
        /// "气泡 + 两侧边距 ≤ 内容区宽"。
        /// </summary>
        private static void ContentClamp()
        {
            // 正常情形：气泡比内容区窄 ⇒ 原样放行
            Eq("窄气泡不受影响", BubbleWidthCore.ClampToContent(300f, 1170f, 24f), 300f);
            Eq("正好等于限值", BubbleWidthCore.ClampToContent(1146f, 1170f, 24f), 1146f);

            // ★ 越界必须夹回来 ★（1170-24=1146）
            // 1095 仍小于 1146 ⇒ 放行；它是"偏大但没越界"，由 Cap 那条管
            Eq("1095 未越界 ⇒ 放行", BubbleWidthCore.ClampToContent(1095f, 1170f, 24f), 1095f);
            Eq("越界 1200 ⇒ 夹到 1146", BubbleWidthCore.ClampToContent(1200f, 1170f, 24f), 1146f);
            Eq("越界很多 ⇒ 夹到 1146", BubbleWidthCore.ClampToContent(5000f, 1170f, 24f), 1146f);

            // 内容宽本身不可信（< 200）⇒ 不夹，交给前面的回退链
            Eq("内容宽脏(0) ⇒ 不夹", BubbleWidthCore.ClampToContent(900f, 0f, 24f), 900f);
            Eq("内容宽脏(50) ⇒ 不夹", BubbleWidthCore.ClampToContent(900f, 50f, 24f), 900f);

            // 限值不得低于最小宽
            Eq("内容区极窄 ⇒ 至少给最小宽", BubbleWidthCore.ClampToContent(1000f, 210f, 24f), 186f);
        }

        private static void RewriteGate()
        {
            Check("还没写过 ⇒ 一定写", BubbleWidthCore.ShouldRewrite(-1f, 500f));
            Check("变化 0.4 ⇒ 不写", !BubbleWidthCore.ShouldRewrite(500f, 500.4f));
            Check("变化 1.4 ⇒ 不写", !BubbleWidthCore.ShouldRewrite(500f, 501.4f));
            Check("变化 1.6 ⇒ 写", BubbleWidthCore.ShouldRewrite(500f, 501.6f));
            Check("变窄也照样判（取绝对值）", BubbleWidthCore.ShouldRewrite(500f, 400f));
        }

        /// <summary>
        /// 11-20 修「文字没到上限却撑满上限」的核心判据。
        ///
        /// 真机现场（用户截图）：一段 30 字的回话气泡横到天花板，关窗重开（回放）又正常。
        /// 根因不是算式错，是**测量时机**：新建气泡那一帧 `Text.preferredWidth` 读到 0
        /// （布局未结算），而旧代码 `w = need > 0 ? Fit(need, cap) : cap` 把它当成
        /// "这段文字不需要宽度"⇒ 直接撑满上限。气泡又只有一次测量机会（非流式），
        /// 于是那一帧的读 0 被永久固化。
        ///
        /// 判据：**量不出来就不写**（宁可停在预制体原宽一帧），等下一帧重测。
        /// </summary>
        private static void WriteGate()
        {
            // —— 主判据：量不出来一律不写 ——
            Check("文字量不出（=0）⇒ 不写", !BubbleWidthCore.ShouldWrite(0f, 1060f));
            Check("文字量不出（<0）⇒ 不写", !BubbleWidthCore.ShouldWrite(-1f, 1060f));
            // ★ 这一条就是那个 bug 的形状：旧行为在同样输入下写 1060（满宽）
            Check("★量不出且目标是上限 ⇒ 仍然不写★", !BubbleWidthCore.ShouldWrite(0f, 1060f));
            // 宽度本身没算出来也不写（任何一路返回 -1 的场合）
            Check("宽度没算出 ⇒ 不写", !BubbleWidthCore.ShouldWrite(300f, -1f));

            // —— 正常情形照写 ——
            Check("量到了 ⇒ 写（贴合）", BubbleWidthCore.ShouldWrite(131f, 187f));
            Check("量到了 ⇒ 写（夹上限）", BubbleWidthCore.ShouldWrite(5000f, 1060f));
            Check("量到了但结果是最小宽 ⇒ 写", BubbleWidthCore.ShouldWrite(1f, 96f));

            // —— 重测次数封顶：防"永远量不出"变成每帧循环 ——
            Check("重测上限有限且给够几帧", BubbleWidthCore.MaxRemeasureTries >= 1
                                           && BubbleWidthCore.MaxRemeasureTries <= 10);
        }

        private static void ContentGate()
        {
            Check("199 ⇒ 当没布局好", !BubbleWidthCore.ContentUsable(199f));
            Check("200 ⇒ 可用", BubbleWidthCore.ContentUsable(200f));
            Check("1170 ⇒ 可用", BubbleWidthCore.ContentUsable(1170f));
            Check("0 ⇒ 当没布局好", !BubbleWidthCore.ContentUsable(0f));
        }
    }
}
