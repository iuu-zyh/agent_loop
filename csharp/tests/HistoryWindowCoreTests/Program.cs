// HistoryWindowCore 单测：分页窗口切在哪 / 要不要分页 / 插入历史后滚动位置怎么补偿。
// 症状背景见 HistoryWindowCore.cs 文件头（用户实测"消息越多越卡"）。

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

        private static void Ieq(string name, int got, int want) =>
            Check(name, got == want, "实际=" + got + " 期望=" + want);

        private static void Feq(string name, float got, float want, float tol = 1e-4f) =>
            Check(name, Math.Abs(got - want) < tol, "实际=" + got + " 期望=" + want);

        private static int Main()
        {
            Console.WriteLine("=== HistoryWindowCore 单元测试 ===");

            // ---------------------------------------------------------------
            // ① StartIndex：窗口切在哪
            // ---------------------------------------------------------------
            Ieq("575 条 / 首屏 40 → 从 535 起（保留最后 40 条）", HistoryWindowCore.StartIndex(575, 40), 535);
            Ieq("正好 40 条 / 首屏 40 → 从 0（全放得下，不截断）", HistoryWindowCore.StartIndex(40, 40), 0);
            Ieq("39 条 / 首屏 40 → 从 0", HistoryWindowCore.StartIndex(39, 40), 0);
            Ieq("0 条 → 从 0（空历史不该算出负下标）", HistoryWindowCore.StartIndex(0, 40), 0);
            Ieq("负总数防御 → 从 0", HistoryWindowCore.StartIndex(-5, 40), 0);
            Ieq("页长 0 = 一条都不显示 → 从 total", HistoryWindowCore.StartIndex(100, 0), 100);
            Ieq("负页长同页长 0", HistoryWindowCore.StartIndex(100, -10), 100);
            Ieq("1 条 / 页 1 → 从 0", HistoryWindowCore.StartIndex(1, 1), 0);

            // 关键口径：**保留的是最后 N 条**（最近的），不是最前 N 条
            //   535 起 ⇒ 下标 535..574 共 40 条，正是"最近 40 条"
            Check("保留的是最后 N 条（最近的），不是最前 N 条",
                  HistoryWindowCore.StartIndex(575, 40) == 575 - 40);

            // ---------------------------------------------------------------
            // ② NeedsPaging：要不要「加载更早」入口
            // ---------------------------------------------------------------
            Check("575 > 40 → 需要分页", HistoryWindowCore.NeedsPaging(575, 40));
            Check("40 与 40 相等 → 不需要", !HistoryWindowCore.NeedsPaging(40, 40));
            Check("39 < 40 → 不需要", !HistoryWindowCore.NeedsPaging(39, 40));
            Check("0 条 → 不需要（空历史不该显示入口）", !HistoryWindowCore.NeedsPaging(0, 40));
            Check("负总数 → 不需要", !HistoryWindowCore.NeedsPaging(-1, 40));
            Check("页长 0 且有内容 → 需要", HistoryWindowCore.NeedsPaging(1, 0));

            // ---------------------------------------------------------------
            // ③ NextPage：每次加多少
            // ---------------------------------------------------------------
            Ieq("首次（0）→ 首屏 40", HistoryWindowCore.NextPage(0), HistoryWindowCore.FirstPageRows);
            Ieq("40 → 80", HistoryWindowCore.NextPage(40), 80);
            Ieq("80 → 120", HistoryWindowCore.NextPage(80), 120);
            Ieq("负值防御 → 首屏", HistoryWindowCore.NextPage(-3), HistoryWindowCore.FirstPageRows);
            Check("每页增量 = PageRows", HistoryWindowCore.NextPage(80) - HistoryWindowCore.NextPage(40)
                                         == HistoryWindowCore.PageRows);

            // ---------------------------------------------------------------
            // ④ MoreLabel：文案
            // ---------------------------------------------------------------
            Check("不需要分页 → 空串（调用方据此撤掉该行）",
                  HistoryWindowCore.MoreLabel(40, 40, false) == "");
            string lbl = HistoryWindowCore.MoreLabel(575, 40, false);
            Check("显示了 40 / 总 575 → 提示还剩 535", lbl.Contains("535"), lbl);
            Check("文案里说明是上划加载", lbl.Contains("上划"), lbl);
            Check("加载中 → 单独文案", HistoryWindowCore.MoreLabel(575, 40, true).Contains("正在加载"));
            // 边界：刚好全显示完 → 不该再有入口
            Check("shown == total → 无入口", HistoryWindowCore.MoreLabel(575, 575, false) == "");
            // 边界：脏数据（shown > total）也不许算出负数条
            string weirds = HistoryWindowCore.MoreLabel(100, 150, false);
            Check("shown 超过 total → 当成不需要入口", weirds == "");

            // ---------------------------------------------------------------
            // ⑤ ScrollAfterPrepend：插入历史后不让画面跳 —— **本轮最容易错的地方**
            // ---------------------------------------------------------------
            // 场景：视口 600，插入前内容 1000（可滚 400），已滚到 200（离底 200）。
            //       插入 10 行 × 50 = 500 ⇒ 内容变 1500（可滚 900）。
            //       要保持"看到同一处"，离底应变成 200 + 500 = 700。
            //       v = 1 - 700/900 = 0.2222...
            float v = HistoryWindowCore.ScrollAfterPrepend(200f, 500f, 600f, 1500f);
            Feq("插入后离底 = 旧离底 + 插入高度 ⇒ 比例 0.2222",
                 1f - v, 700f / 900f, 1e-3f);

            // 反向验证：**如果不补偿**（直接把旧比例 1-200/400=0.5 写回去），
            // 实际离底会变成 0.5*900=450 ⇒ 用户看到的位置往上跑了 250px。这就是 bug 的形态。
            float naive = 0.5f;
            Check("不补偿会让位置跑掉（差值 250px）",
                  Math.Abs((1f - naive) * 900f - 700f) > 200f,
                  "不补偿离底=" + ((1f - naive) * 900f) + " 正确=" + 700f);

            // ⚠ 参数 `prevScrollPx` 的语义 = **从顶部算的已滚距离**（scrollOffset），
            //   不是"离底部距离"。见实现里 `(1 - verticalNormalizedPosition) * range`：
            //   v=1(顶) → 0；v=0(底) → range(最大)。第一版测试把两个标签写反了。

            // 原本在**底**（scrollOffset = range = 400）：插入后仍在底
            float vBottom = HistoryWindowCore.ScrollAfterPrepend(400f, 500f, 600f, 1500f);
            Feq("原本在底，插入后仍在底", vBottom, 0f);

            // 原本在**顶**（scrollOffset = 0）：插入 500 后视口应停在"原内容"上，
            // 即 scrollOffset 变成 500 ⇒ v = 1 - 500/900。**不再在顶** —— 这是对的，
            // 新插入的历史出现在视图上方，用户要继续上滚才看得到（不打断当前阅读）。
            float vTop = HistoryWindowCore.ScrollAfterPrepend(0f, 500f, 600f, 1500f);
            Feq("原本在顶，插入后停在原内容（v=1-500/900）", vTop, 1f - 500f / 900f, 1e-3f);
            Check("原本在顶，插入后确实不在顶（新历史在上方，需继续上滚）", vTop < 0.99f);

            // 退化：内容还不够视口高 → 返回 1（顶）
            Feq("内容比视口矮 → 顶", HistoryWindowCore.ScrollAfterPrepend(0f, 100f, 600f, 500f), 1f);
            // 退化：读不到高度 → 1，不许抛
            Feq("contentH=0 → 顶", HistoryWindowCore.ScrollAfterPrepend(0f, 100f, 600f, 0f), 1f);
            Feq("viewportH=0 → 顶", HistoryWindowCore.ScrollAfterPrepend(0f, 100f, 0f, 1000f), 1f);

            // 越界夹紧：离底比可滚范围还大 → 夹到 0（底），不许算出负比例
            float vClamp = HistoryWindowCore.ScrollAfterPrepend(99999f, 0f, 600f, 1000f);
            Feq("离底超出范围 → 夹到底", vClamp, 0f);
            // 负离开量（脏数据）→ 夹到顶
            float vNeg = HistoryWindowCore.ScrollAfterPrepend(-50f, 0f, 600f, 1000f);
            Feq("负离底 → 夹到顶", vNeg, 1f);

            // 结果恒在 [0,1]（写进 verticalNormalizedPosition 必须合法）
            float[] cases = { 0f, 1f, 100f, 500f, 10000f };
            bool allInRange = true;
            foreach (float c in cases)
            {
                float r = HistoryWindowCore.ScrollAfterPrepend(c, 250f, 600f, 1200f);
                if (r < 0f || r > 1f) allInRange = false;
            }
            Check("结果恒在 [0,1]", allInRange);

            // ---------------------------------------------------------------
            // ⑥ 常量口径
            // ---------------------------------------------------------------
            Check("首屏 40（用户拍板）", HistoryWindowCore.FirstPageRows == 40);
            Check("每页 40（与首屏同量级）", HistoryWindowCore.PageRows == 40);
            Check("首屏 > 0（否则开窗空白）", HistoryWindowCore.FirstPageRows > 0);

            Console.WriteLine();
            if (_fails.Count == 0)
            {
                Console.WriteLine("全部通过：" + _pass + " 项断言");
                return 0;
            }
            Console.WriteLine("失败 " + _fails.Count + " 项 / 通过 " + _pass + " 项");
            return 1;
        }
    }
}
