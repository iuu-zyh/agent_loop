// 工具结果折叠判定（ToolFoldPolicy）单元测试。
//
// 为什么要有这个工程：折叠后屏幕上只剩一行，一旦摘要写错（把富文本标签截一半、字数报错、
// 或者"展开"那一路偷偷截断了原文），实机上只会看到"文字怪怪的"，没有任何异常日志。
// 而"点开之后视图跳不跳"这类手感问题，更要靠判定表锁住——不能靠每次进游戏点一遍。
//
// 覆盖四件事：
//   ① ShouldFold：值不值得折（单行 ≤ 40 字直出；多行/超长才折）——按**可读文本**判，不按原始长度
//      （工具结果里 <color=#004FCA> 这类标签很多，按原始长度判会把一行短结果误折）；
//   ② CollapsedLine：摘要必须**剥标签 + 压空白 + 截 40 字 + 报可读字数**，且总长有界（"折叠"要有意义）；
//   ③ ExpandedLine：**原文一字不改**（含标签/换行/前后空格）——这是"只折视图不改数据"的哨兵，
//      与 Python 侧 test_text_render.py::test_no_truncation_anywhere 是同一条原则的两端；
//   ④ RevealDelta（用户选定 B 档「不够就滚够」）：装得下就一动不动；装不下只滚差的那一点；
//      行比视口高就把行顶对齐视口顶；绝不往回滚、绝不超出可滚范围。
//
// ⚠ 诚实边界：真实的 ChatWindow 依赖 Unity，WSL 里跑不起来。这里测的是**规则**；
//   "接线有没有被改回去"由 Python 侧源码契约测试 tests/test_tool_fold_contract.py 锁在
//   真实 ChatWindow.cs / ChatItemViews.cs 上 —— 两边合起来才完整。

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

        private static int Main()
        {
            Console.WriteLine("=== ToolFoldPolicy 单元测试 ===");
            // 每个小节单独兜异常：一节里抛了（例如被测代码对短文本越界），
            // 也要如实记成"这一节失败"并继续跑后面的小节 —— 崩在第一行会让后面全都没跑，
            // 看起来像"测试通过"（变异验证踩过：截断变异触发 Substring 越界，进程直接 82 退出）
            Section("ShouldFold", ShouldFoldTable);
            Section("CollapsedLine", CollapsedTable);
            Section("ExpandedLine", ExpandedFidelity);
            Section("RevealDelta", RevealTable);
            Section("RevealInvariants", RevealInvariants);
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
                Check("★ 小节抛异常：" + name, false, e.GetType().Name + ": " + e.Message);
            }
        }

        // 真实观测到的最长一条结果（BOlQu6 账本：908 字 / 17 行）
        private const string LongResult =
            "unit id: FmTzJ5\n风剑流，男，炼气境散修；性格：内仁善·外豪侠；\n" +
            "气运：先天魂剑（开局自带一把噬魂魔剑。魔道值+200，幸运+10。）\n" +
            "（正常大宝贝，无特殊影响，大能看一眼也只会觉得是个普通的炼气散修）\n" +
            "<color=#004FCA>扬宣城、溪望镇、远望镇、石同镇</color>的坊市又进了奇珍异宝\n" +
            "宗门推荐信×1（单价 0，小计 0）—— 正在向天下广揽人才\n" +
            "石沉戒×1（单价 250，小计 250）—— 最低级的储物戒指";

        // ------------------------------------------------------------------
        // 一、ShouldFold：值不值得折
        // ------------------------------------------------------------------
        private static void ShouldFoldTable()
        {
            Console.WriteLine("[ShouldFold 判定表]");

            Eq("null → 不折", ToolFoldPolicy.ShouldFold(null), false);
            Eq("空串 → 不折", ToolFoldPolicy.ShouldFold(""), false);
            Eq("纯空白 → 不折", ToolFoldPolicy.ShouldFold("   \n  "), false);

            // 短结果直出：不让用户为一行字多点一次（"已向你发起切磋"这类）
            Eq("短单行(7字) → 不折", ToolFoldPolicy.ShouldFold("已向你发起切磋"), false);
            Eq("正好 40 字 → 不折", ToolFoldPolicy.ShouldFold(new string('甲', 40)), false);
            Eq("41 字 → 折", ToolFoldPolicy.ShouldFold(new string('甲', 41)), true);

            // 多行即折：三行短句直出也占三行，折起来更整齐
            Eq("两行短句 → 折", ToolFoldPolicy.ShouldFold("甲\n乙"), true);
            Eq("CRLF 两行 → 折", ToolFoldPolicy.ShouldFold("甲\r\n乙"), true);

            // ★ 按**可读长度**判：标签很多但可读文本很短 → 不折（屏幕上就一行）
            string taggedShort = "<color=#004FCA>扬宣城</color>的坊市进了<color=#DB0000>鸣蛇</color>";
            Check("标签多但可读文本短 → 不折（按可读长度判）",
                  !ToolFoldPolicy.ShouldFold(taggedShort),
                  "原始长=" + taggedShort.Length + " 可读长=" + ToolFoldPolicy.Visible(taggedShort).Length);

            Eq("真实长结果(908字/17行) → 折", ToolFoldPolicy.ShouldFold(LongResult), true);
        }

        // ------------------------------------------------------------------
        // 二、CollapsedLine：摘要必须短、必须干净、字数必须是真的
        // ------------------------------------------------------------------
        private static void CollapsedTable()
        {
            Console.WriteLine("[CollapsedLine 摘要]");

            string tagged = "<color=#004FCA>扬宣城</color>的<color=#057600>醉花林境</color>中出现了" +
                            "<color=#DB0000>树妖</color>，各路修仙者纷纷合力与其对抗，最终将其击败了。";

            string c = ToolFoldPolicy.CollapsedLine(tagged, false);

            Check("折叠态带 ▸ 箭头", c.StartsWith("▸ "), c);
            Check("折叠态带 ✓ 前缀（非错误）", c.StartsWith("▸ ✓ "), c);
            Check("★ 摘要已剥富文本标签（不出现 <color）", c.IndexOf("<color", StringComparison.Ordinal) < 0, c);
            Check("摘要不出现半个标签（不出现 <）", c.IndexOf('<') < 0, c);
            Check("摘要压平换行", c.IndexOf('\n') < 0 && c.IndexOf('\r') < 0, c);
            Check("摘要带「点击展开」提示", c.Contains("点击展开"), c);
            Check("摘要报了可读字数", c.Contains("（共 " + ToolFoldPolicy.Visible(tagged).Length + " 字"), c);

            // 截断规则：40 字以内原样，超出截 40 + 省略号
            string body = ToolFoldPolicy.CollapsedLine(LongResult, false);
            Check("长结果摘要被截断（40 字 + …）",
                  body.Contains(new string('…', 1)), body);
            Check("★ 折叠后整行有界（< 90 字符 = 屏幕上就一两行）", body.Length < 90,
                  "实际长度=" + body.Length);

            // 错误行：✗ 且默认展开
            string err = ToolFoldPolicy.CollapsedLine("钱不够", true);
            Check("错误行用 ✗ 前缀", err.StartsWith("▸ ✗ "), err);
            Eq("错误行默认展开", ToolFoldPolicy.ExpandByDefault(true), true);
            Eq("正常行默认折叠", ToolFoldPolicy.ExpandByDefault(false), false);

            // 极端长文本（4088 字单行，远超实测最长的 908 字）：折叠必须仍然有界，
            // 且展开态一字不少 —— 这条防的是"有人把摘要上限改大/改成不截断"
            string huge = new string('甲', 4000) + "\n" + new string('乙', 88);
            Eq("4088 字也折", ToolFoldPolicy.ShouldFold(huge), true);
            Check("★ 4088 字折叠后整行仍有界（< 90 字符）",
                  ToolFoldPolicy.CollapsedLine(huge, false).Length < 90,
                  "实际=" + ToolFoldPolicy.CollapsedLine(huge, false).Length);
            Eq("★ 4088 字展开态一字不少",
               ToolFoldPolicy.ExpandedLine(huge, false).Length, huge.Length + 4);

            // 换行文本压平后不丢字：三段拼起来应等于可见文本
            string three = "第一行\n第二行\n第三行";
            string flat = ToolFoldPolicy.CollapsedLine(three, false);
            Check("多行压平后三段都在", flat.Contains("第一行 第二行 第三行"), flat);
        }

        // ------------------------------------------------------------------
        // 三、ExpandedLine：原文一字不改（截断哨兵）
        // ------------------------------------------------------------------
        private static void ExpandedFidelity()
        {
            Console.WriteLine("[ExpandedLine 忠实性]");

            string[] cases =
            {
                LongResult,
                "<color=#004FCA>扬宣城</color>\n第二行\t带制表符",
                "  前后都有空格  ",
                "甲\n\n乙",
                "已向你发起切磋",
                "",
            };
            foreach (string raw in cases)
            {
                string got = ToolFoldPolicy.ExpandedLine(raw, false);
                Eq("展开态 = 「▾ ✓ 」+ 原文：" + Short(raw), got, "▾ ✓ " + raw);
            }
            Eq("展开态错误行 = 「▾ ✗ 」+ 原文",
               ToolFoldPolicy.ExpandedLine("失败：灵石不足", true), "▾ ✗ 失败：灵石不足");

            // 长度守恒：展开态只多 4 个字符（"▾ " 箭头 + "✓ " 标记），证明**没有**任何截断/改写
            string longRaw = LongResult;
            Eq("★ 展开态长度守恒（原文长度 + 4）",
               ToolFoldPolicy.ExpandedLine(longRaw, false).Length, longRaw.Length + 4);
            Eq("★ 展开态含全部 17 行（换行数不变）",
               CountChar(ToolFoldPolicy.ExpandedLine(longRaw, false), '\n'),
               CountChar(longRaw, '\n'));
            Check("★ 展开态保留富文本标签（屏幕上仍是彩色人名）",
                  ToolFoldPolicy.ExpandedLine(taggedOne, false).Contains("<color=#004FCA>"));
        }

        private const string taggedOne = "<color=#004FCA>扬宣城</color>的坊市";

        private static int CountChar(string s, char c)
        {
            int n = 0;
            for (int i = 0; i < s.Length; i++) if (s[i] == c) n++;
            return n;
        }

        private static string Short(string s)
        {
            if (s == null) return "<null>";
            string one = s.Replace("\n", "\\n");
            return one.Length <= 18 ? one : one.Substring(0, 18) + "…";
        }

        // ------------------------------------------------------------------
        // 四、RevealDelta：不够就滚够
        // ------------------------------------------------------------------
        private static void RevealTable()
        {
            Console.WriteLine("[RevealDelta 判定表] V=600 视口");

            Eq("已经装得下 → 一动不动", ToolFoldPolicy.RevealDelta(500f, 20f, 600f, 9999f), 0f);
            Eq("刚好装得下 → 一动不动", ToolFoldPolicy.RevealDelta(180f, 420f, 600f, 9999f), 0f);
            Eq("差 320 → 只滚 320", ToolFoldPolicy.RevealDelta(500f, 420f, 600f, 9999f), 320f);
            Eq("行比视口高(800) → 行顶对齐视口顶（滚 100）",
               ToolFoldPolicy.RevealDelta(100f, 800f, 600f, 9999f), 100f);
            Eq("行顶已在视口上方 + 行超出下沿 → 0（不折腾）",
               ToolFoldPolicy.RevealDelta(-100f, 900f, 600f, 9999f), 0f);
            Eq("可滚范围不够 → 夹到范围上限", ToolFoldPolicy.RevealDelta(500f, 420f, 600f, 50f), 50f);
            Eq("已经滚到底 → 0", ToolFoldPolicy.RevealDelta(500f, 420f, 600f, 0f), 0f);
            Eq("视口退化 0 高 → 0（不硬算）", ToolFoldPolicy.RevealDelta(500f, 420f, 0f, 9999f), 0f);
            Eq("行高为 0（布局还没生效）→ 0", ToolFoldPolicy.RevealDelta(500f, 0f, 600f, 9999f), 0f);
            Eq("负 room 当 0 处理", ToolFoldPolicy.RevealDelta(500f, 420f, 600f, -20f), 0f);
        }

        /// <summary>性质检查：对"行顶位置 × 行高"的整个矩阵，断言两条不变量——
        /// ① 绝不往回滚（delta ≥ 0）；② 应用后要么整行都在视口里，要么行顶已到视口顶
        /// （行比视口高时的极限）。这就是用户选的 B 档语义。</summary>
        private static void RevealInvariants()
        {
            Console.WriteLine("[RevealDelta 不变量矩阵]");
            const float V = 600f;
            int bad1 = 0, bad2 = 0, bad3 = 0, moved = 0, cases = 0;

            for (int top = -50; top <= 600; top += 25)
            {
                for (int h = 20; h <= 900; h += 40)
                {
                    cases++;
                    float d = ToolFoldPolicy.RevealDelta(top, h, V, 5000f);
                    if (d < 0f) bad1++;

                    float topAfter = top - d;          // 滚 d 像素 = 行相对视口上移 d
                    float bottomAfter = topAfter + h;
                    bool fullyVisible = bottomAfter <= V + 0.01f;
                    bool topAtTop = topAfter <= 0.01f; // 行顶贴到视口顶（高行滚不动更多）
                    if (!fullyVisible && !topAtTop) bad2++;
                    if (d > 0f) moved++;

                    // 装得下就不该动
                    if (top + h <= V + 0.01f && d > 0f) bad3++;
                }
            }
            Check("矩阵 " + cases + " 组：delta 恒非负", bad1 == 0, "违规=" + bad1);
            Check("矩阵 " + cases + " 组：滚完要么整行可见、要么行顶贴视口顶",
                  bad2 == 0, "违规=" + bad2);
            Check("矩阵 " + cases + " 组：装得下的一律不动（不够才滚够）",
                  bad3 == 0, "违规=" + bad3);
            Check("矩阵里确实有需要滚的场景（不是恒 0 的假绿）", moved > 20, "需要滚的组数=" + moved);
        }
    }
}
