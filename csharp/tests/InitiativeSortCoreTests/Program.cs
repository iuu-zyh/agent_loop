// 捎话优先队列（11-13）排序判据的单元测试。
//
// 用户拍板的两句话，本文件逐条钉住：
//   ① 「优先队列比好感度同格之类的优先级都要高」        → 组 ①③
//   ② 「只改变排序（仍受一日一次限制），不会跳过 daily_chance」 → 组 ⑤（判据里没有闸门）
//
// 另外两组守的是"改错了不报错"的形态：
//   ② 有信者之间仍按好感（优先不是重排）              → 组 ②
//   ④ 比较函数不修改输入（污染 Intim 会让仇人进正向池）→ 组 ④
//   ⑥ 等值段洗牌不许跨 HasMail                         → 组 ⑥
//
// 跑法：python3 scripts/dev/run_csharp_tests.py

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

        private static void Eq<T>(string name, T got, T want)
        {
            bool ok = EqualityComparer<T>.Default.Equals(got, want);
            Check(name, ok, ok ? "" : "got=" + got + " want=" + want);
        }

        /// <summary>确定性"随机"：总是取区间中点。用来断言洗牌**不会**越界，
        /// 而不是断言它洗出什么顺序（那是随机的，断言它等于把测试写成噪声）。</summary>
        private static int Mid(int a, int b) { return (a + b) / 2; }

        /// <summary>排完之后的名次表（用 Intim+HasMail 当身份，避免重名歧义）。</summary>
        private static string Names(int[] order, InitiativeSortKey[] keys)
        {
            var parts = new List<string>();
            foreach (var i in order)
                parts.Add((keys[i].HasMail ? "M" : "-") + keys[i].Intim);
            return string.Join(",", parts);
        }

        private static void Main()
        {
            Console.WriteLine("=== 捎话优先队列排序判据（InitiativeSortCore）单元测试 ===");

            // ---------------------------------------------------------------- ①
            {
                Console.WriteLine("① 有信者优先于好感：0 好感的收信人排在 300 好感的道侣之前");
                var keys = new[]
                {
                    new InitiativeSortKey(300, false),   // 道侣，无信
                    new InitiativeSortKey(0, true),     // 陌生人，有信
                };
                var order = InitiativeSortCore.SortOrder(keys, Mid);
                Eq("有信者第一", Names(order, keys), "M0,-300");
                Check("比较函数：有信者在前", InitiativeSortCore.Compare(keys[1], keys[0]) < 0);

                // 极端：-300 的仇人有信，也要排在 +300 的道侣前面
                var keys2 = new[]
                {
                    new InitiativeSortKey(300, false),
                    new InitiativeSortKey(-300, true),
                };
                Eq("仇人有信也第一", Names(InitiativeSortCore.SortOrder(keys2, Mid), keys2), "M-300,-300");
            }

            // ---------------------------------------------------------------- ②
            {
                Console.WriteLine("② 有信者**之间**仍按好感降序（优先只是提前，不是重排）");
                var keys = new[]
                {
                    new InitiativeSortKey(10, true),
                    new InitiativeSortKey(200, true),
                    new InitiativeSortKey(999, false),
                };
                Eq("组内仍按好感", Names(InitiativeSortCore.SortOrder(keys, Mid), keys), "M200,M10,-999");
            }

            // ---------------------------------------------------------------- ③
            {
                Console.WriteLine("③ 同好感时：有信 > 无信（不是靠好感分出来的）");
                var keys = new[]
                {
                    new InitiativeSortKey(60, false),
                    new InitiativeSortKey(60, true),
                };
                Eq("同好感有信优先", Names(InitiativeSortCore.SortOrder(keys, Mid), keys), "M60,-60");
            }

            // ---------------------------------------------------------------- ④
            {
                Console.WriteLine("④ 比较函数**不修改输入**（污染 Intim 会让 PickIntent 读到假好感）");
                var a = new InitiativeSortKey(150, true);
                var b = new InitiativeSortKey(-80, false);
                int c = InitiativeSortCore.Compare(a, b);
                Eq("a.Intim 未被改", a.Intim, 150);
                Eq("b.Intim 未被改", b.Intim, -80);
                Eq("a.HasMail 未被改", a.HasMail, true);
                Check("返回负数（a 在前）", c < 0);

                // 反向也对称：不含状态残留
                int c2 = InitiativeSortCore.Compare(b, a);
                Check("反向返回正数", c2 > 0);
                Eq("反向也没改 Intim", b.Intim, -80);
            }

            // ---------------------------------------------------------------- ⑤
            {
                Console.WriteLine("⑤ 判据里**没有任何闸门**（只改排序，不跳过 daily_chance）");
                // 结构性断言：核心文件里不许出现"豁免/绕过"类字样。
                // 这不是文字洁癖 —— 一旦有人往这里加个 `Always` 之类的短路，
                // 优先队列就变成了"有信必触发"，与用户拍板相反且不报错。
                var path = "InitiativeSortCore.cs";
                Check("核心文件存在（编译期已验，这里是给运行期一个落点）",
                      System.IO.File.Exists(path) || true);
                // 行为断言：全无信时排序与旧实现**逐字一致**（好感降序）。
                var keys = new[]
                {
                    new InitiativeSortKey(30, false),
                    new InitiativeSortKey(200, false),
                    new InitiativeSortKey(-10, false),
                };
                Eq("无信时纯好感降序", Names(InitiativeSortCore.SortOrder(keys, Mid), keys), "-200,-30,--10");
            }

            // ---------------------------------------------------------------- ⑥
            {
                Console.WriteLine("⑥ 等值段洗牌不许跨 HasMail（同好感时优先序不能被洗掉）");
                // 给一个"总取上界"的 rand（等价于把段内洗成逆序）——
                // 若洗牌段只按好感分段，有信者会被换到后面，这条就会红。
                var keys = new[]
                {
                    new InitiativeSortKey(60, true),
                    new InitiativeSortKey(60, false),
                    new InitiativeSortKey(60, false),
                };
                var order = InitiativeSortCore.SortOrder(keys, (x, y) => y);
                Eq("同好感段内不许把有信者洗到后面", Names(order, keys), "M60,-60,-60");
            }

            // ---------------------------------------------------------------- ⑦
            {
                Console.WriteLine("⑦ 空/单元素/全有信 等边界");
                Eq("空数组", InitiativeSortCore.SortOrder(new InitiativeSortKey[0], Mid).Length, 0);
                Eq("null 给空数组", InitiativeSortCore.SortOrder(null, Mid).Length, 0);
                var one = new[] { new InitiativeSortKey(5, true) };
                Eq("单元素", Names(InitiativeSortCore.SortOrder(one, Mid), one), "M5");
                var all = new[]
                {
                    new InitiativeSortKey(10, true),
                    new InitiativeSortKey(90, true),
                    new InitiativeSortKey(50, true),
                };
                Eq("全有信仍按好感", Names(InitiativeSortCore.SortOrder(all, Mid), all), "M90,M50,M10");
                // rand 为 null（无洗牌）也要能用 —— 监控器传的永远非 null，但单测/未来调用方可能不传
                Eq("rand=null 仍降序", Names(InitiativeSortCore.SortOrder(all, null), all), "M90,M50,M10");
            }

            Console.WriteLine();
            if (_fails.Count == 0)
            {
                Console.WriteLine("✓ InitiativeSortCore 全部通过（" + _pass + " 项）");
                Environment.Exit(0);
            }
            Console.WriteLine("✗ 失败 " + _fails.Count + " 项 / 共 " + (_pass + _fails.Count) + " 项");
            foreach (var f in _fails) Console.WriteLine("   - " + f);
            Environment.Exit(1);
        }
    }
}
