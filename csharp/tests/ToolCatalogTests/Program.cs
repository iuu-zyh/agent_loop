// 工具侧边栏判定核（ToolCatalogCore）单元测试。
//
// 为什么要有这个工程：侧边栏每行点下去只有两种结局 —— 直接发（send）/ 填进输入框等玩家补（fill）。
// 后者的判据是"台词里那个全角空格 U+3000 在哪、光标该落在哪"。这类错在实机上**完全静默**：
// 找不到空位 → 光标落在末尾 → 玩家得自己找位置删字；认错字符 → 光标停在一个无关的字中间。
// 两者都不报错、都没有日志，只有"手感怪"。所以判据必须在这里被锁住。
//
// 覆盖：
//   ① FirstBlank / CaretAt：全角空格才认；半角空格、制表符、其他空白符都不算（认错的后果见上）；
//   ② IsFill：**以台词为准**，`mode` 只是参考 —— Python 侧若把 mode 写错，台词里真有空位仍按 fill 处理；
//   ③ 边界：null / 空串 / 多个空位（取第一个）/ 空位在首尾；
//   ④ BeforeSend：**一字不改**。这条钉的是"我们不去猜玩家的意思"——
//      玩家跳过了空位就发，模型追问"哪件"才是正确行为，改写成任何别的字符都只是伪装。
//
// 真实建面板/发消息的接线在 UI/ToolPanel.cs，由 tests/test_tool_sidebar_contract.py 锁。

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

        private static string Show(object o)
        {
            if (o == null) return "<null>";
            var s = o.ToString();
            var sb = new System.Text.StringBuilder();
            foreach (char c in s)
            {
                if (c == ToolCatalogCore.Blank) sb.Append("[BLANK]");
                else if (c == ' ') sb.Append("[sp]");
                else if (c == '\t') sb.Append("[tab]");
                else sb.Append(c);
            }
            return sb.ToString();
        }

        private static void Main()
        {
            Console.WriteLine("ToolCatalogCore 单元测试");

            // ── ① 空位只在全角空格上 ───────────────────────────────
            string fill = "你那儿的那" + ToolCatalogCore.Blank + "，可否匀我一件？";
            Eq("全角空格被认出", ToolCatalogCore.FirstBlank(fill), "你那儿的那".Length);
            Eq("光标落在空位上", ToolCatalogCore.CaretAt(fill), "你那儿的那".Length);

            Eq("半角空格不算空位", ToolCatalogCore.FirstBlank("你那儿的那 ，可否匀我一件？"), -1);
            Eq("制表符不算空位", ToolCatalogCore.FirstBlank("你那儿的那\t，可否匀我一件？"), -1);
            Eq("普通中文空格（U+3000 之外的空白）不算",
               ToolCatalogCore.FirstBlank("你那儿的那\u00A0，可否"), -1);
            Eq("全角逗号不算空位", ToolCatalogCore.FirstBlank("你那儿的那，可否"), -1);

            // ── ② IsFill 以台词为准 ────────────────────────────────
            Check("mode=send 但台词有空位 → 仍判 fill",
                  ToolCatalogCore.IsFill(ToolCatalogCore.ModeSend, fill));
            Check("mode=fill 但台词没空位 → 仍判 fill（mode 优先于 -1 的空位）",
                  ToolCatalogCore.IsFill(ToolCatalogCore.ModeFill, "没有空位的一句"));
            Check("mode=send 且没空位 → 不是 fill",
                  !ToolCatalogCore.IsFill(ToolCatalogCore.ModeSend, "你囊中都有些什么好东西？"));
            Check("mode 大小写不敏感", ToolCatalogCore.IsFill("FILL", "x"));
            Check("mode 为 null 不炸", !ToolCatalogCore.IsFill(null, "没空位"));
            Check("null 台词不是 fill", !ToolCatalogCore.IsFill(null, null));

            // ── ③ 边界 ─────────────────────────────────────────────
            Eq("null → -1", ToolCatalogCore.FirstBlank(null), -1);
            Eq("空串 → -1", ToolCatalogCore.FirstBlank(""), -1);
            Eq("null → 光标 0", ToolCatalogCore.CaretAt(null), 0);
            Eq("空串 → 光标 0", ToolCatalogCore.CaretAt(""), 0);
            Eq("没有空位 → 光标在串尾", ToolCatalogCore.CaretAt("一二三"), 3);

            string twoBlanks = "甲" + ToolCatalogCore.Blank + "乙" + ToolCatalogCore.Blank + "丙";
            Eq("多个空位取第一个", ToolCatalogCore.FirstBlank(twoBlanks), 1);
            Eq("多个空位光标也在第一个", ToolCatalogCore.CaretAt(twoBlanks), 1);

            Eq("空位在首位", ToolCatalogCore.FirstBlank(ToolCatalogCore.Blank + "开头"), 0);
            Eq("空位在末位", ToolCatalogCore.FirstBlank("结尾" + ToolCatalogCore.Blank), 2);

            // ── ④ 发送前一字不改 ───────────────────────────────────
            Eq("BeforeSend 原样返回（带空位）", ToolCatalogCore.BeforeSend(fill), fill);
            Eq("BeforeSend 连空位码位都不换", (int)ToolCatalogCore.BeforeSend(fill)[5], (int)ToolCatalogCore.Blank);
            Eq("BeforeSend 对 null 给空串", ToolCatalogCore.BeforeSend(null), "");
            Check("BeforeSend 不删空位", ToolCatalogCore.BeforeSend(fill).IndexOf(ToolCatalogCore.Blank) >= 0);

            // ── 判定核不依赖 Unity ──────────────────────────────────
            // 编译期就能证明（本工程只链接 ToolCatalogCore.cs，无 Unity 引用）。
            // 运行时再钉一条：类型所在程序集里不该出现 UnityEngine 的类型名。
            bool unityFree = true;
            foreach (var t in typeof(ToolCatalogCore).Assembly.GetTypes())
            {
                if (t.FullName != null && t.FullName.StartsWith("UnityEngine", StringComparison.Ordinal))
                { unityFree = false; break; }
            }
            Check("判定核所在程序集不含 UnityEngine 类型", unityFree);

            Console.WriteLine();
            if (_fails.Count == 0)
            {
                Console.WriteLine("全部通过：" + _pass + " 项断言");
                Environment.Exit(0);
            }
            Console.WriteLine("失败 " + _fails.Count + " 项（通过 " + _pass + " 项）：");
            foreach (var f in _fails) Console.WriteLine("  · " + f);
            Environment.Exit(1);
        }
    }
}
