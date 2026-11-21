// 时间分隔行判档（TimeSepCore.Label）与行首戳解析（BubbleTextCore.TryParseLeadStamp）单测。
//
// 为什么要有这个工程（用户 10-22 需求：气泡不带日期，改微信式小字分隔行，粒度按游戏历）：
//   判档一旦写偏，症状全都不报错——
//   ① 同月换日没打 → 玩家看不见"过天了"（功能等于没上）；
//   ② 同一天每句都打 → 满屏灰字，比日期骑在气泡里更破坏沉浸感；
//   ③ 档位选错（跨年只打"6日"）→ 玩家以为是**同一年**的 6 日，比不显示更误导；
//   ④ 退化形（日历读不到日，戳只有 `[N年M月]`）若照"日"档打 → 打出 `0日` 这种鬼话。
// 这四类在实机上都要"正好等到换月/跨年/读不到日历"才复现，所以钉在判定表里。
//
// ⚠ 诚实边界：这里只测**规则**。接线（分隔行只由玩家行渲染口驱动、重放清零重算）由
//   Python 侧 tests/test_time_separator_contract.py 锁在真实 ChatWindow.cs 上。

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

        private static string Show<T>(T v) => v == null ? "<null>" : v.ToString();

        private static int Main()
        {
            Console.WriteLine("=== TimeSepCore 单元测试 ===");
            Section("判档：变化才打、按档收窄", LabelRules);
            Section("退化形（无日）：绝不编造日", DegradedNoDay);
            Section("解析：TryParseLeadStamp", ParseRules);
            Section("剥与认同一把尺（MatchLeadStamp 单源回归）", StripParseAgree);
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
                Check("小节「" + name + "」不应抛异常", false, e.GetType().Name + ": " + e.Message);
            }
        }

        // ------------------------------------------------------------------
        // ① 判档（用户口径：同年同月只显示日；不同月显示月日；都不同全部显示）
        // ------------------------------------------------------------------
        private static void LabelRules()
        {
            // 会话第一条：没有"上次"可比 → 全档（微信同款：首条也打时间）
            Eq("第一条 → 全档", TimeSepCore.Label(false, 0, 0, 0, 1, 1, 3), "1年1月3日");
            // 同一天：不打（每句都打 = 满屏灰字）
            Eq("同天不打", TimeSepCore.Label(true, 1, 1, 5, 1, 1, 5), null);
            // 同月换日：只到"日"
            Eq("同月换日 → 日档", TimeSepCore.Label(true, 1, 1, 5, 1, 1, 6), "6日");
            Eq("同月跨十天也只看日", TimeSepCore.Label(true, 1, 1, 2, 1, 1, 29), "29日");
            // 跨年：全档（哪怕月日相同——只打"6日"会误导成同一年）
            Eq("跨年 → 全档", TimeSepCore.Label(true, 1, 12, 30, 2, 1, 1), "2年1月1日");
            Eq("跨年同月同日 → 全档", TimeSepCore.Label(true, 1, 3, 6, 2, 3, 6), "2年3月6日");
            // 同月/跨年判定用**年份数值**，不是"变没变月"
            Eq("回拨到旧年也按跨年", TimeSepCore.Label(true, 3, 5, 5, 2, 5, 6), "2年5月6日");
        }

        // ------------------------------------------------------------------
        // ② 退化形：日历读不到日 → 戳是 `[N年M月]`（d=0），任何档位都不许带"日"
        // ------------------------------------------------------------------
        private static void DegradedNoDay()
        {
            Eq("第一条无日 → 年月", TimeSepCore.Label(false, 0, 0, 0, 1, 1, 0), "1年1月");
            Eq("新戳无日、同月 → 不打（判不出日变化）",
               TimeSepCore.Label(true, 1, 1, 5, 1, 1, 0), null);
            Eq("旧档无日、新戳有日、同月 → 不打（拿不准就不打扰）",
               TimeSepCore.Label(true, 1, 1, 0, 1, 1, 5), null);
            Eq("跨月且无日 → 只到月档", TimeSepCore.Label(true, 1, 1, 5, 1, 3, 0), "3月");
            Eq("跨年且无日 → 年月档", TimeSepCore.Label(true, 1, 12, 5, 2, 1, 0), "2年1月");
            Eq("跨月有日 → 月日档", TimeSepCore.Label(true, 1, 1, 30, 1, 2, 1), "2月1日");
        }

        // ------------------------------------------------------------------
        // ③ 解析（行首戳 → 年月日数字；与 StripLeadStamp 同一把尺）
        // ------------------------------------------------------------------
        private static void ParseRules()
        {
            int y, m, d;
            Check("年月日全形命中", BubbleTextCore.TryParseLeadStamp("[1年1月3日] 你好", out y, out m, out d));
            Eq("  y", y, 1); Eq("  m", m, 1); Eq("  d", d, 3);

            Check("无日退化形命中且 d=0", BubbleTextCore.TryParseLeadStamp("[12年10月] 甲", out y, out m, out d));
            Eq("  y", y, 12); Eq("  m", m, 10); Eq("  d", d, 0);

            Check("多位数", BubbleTextCore.TryParseLeadStamp("[123年10月29日] 甲", out y, out m, out d));
            Eq("  y", y, 123); Eq("  m", m, 10); Eq("  d", d, 29);

            Check("戳后无换行也命中", BubbleTextCore.TryParseLeadStamp("[1年1月]你好", out y, out m, out d));
            Check("零值如实解析（不判合法性，交给判档）",
                  BubbleTextCore.TryParseLeadStamp("[0年0月0日] 甲", out y, out m, out d)
                  && y == 0 && m == 0 && d == 0);

            // 不是一切的形状
            Check("非行首不认", !BubbleTextCore.TryParseLeadStamp("你好[1年1月]", out y, out m, out d));
            Check("缺日字不认（[1年1月3]）", !BubbleTextCore.TryParseLeadStamp("[1年1月3] 甲", out y, out m, out d));
            Check("无月不认", !BubbleTextCore.TryParseLeadStamp("[1年] 甲", out y, out m, out d));
            Check("非数字不认", !BubbleTextCore.TryParseLeadStamp("[x年y月] 甲", out y, out m, out d));
            Check("超长数字不认（防回绕）", !BubbleTextCore.TryParseLeadStamp("[12345678901年1月] 甲", out y, out m, out d));
            Check("null/空不认", !BubbleTextCore.TryParseLeadStamp(null, out y, out m, out d)
                                 && !BubbleTextCore.TryParseLeadStamp("", out y, out m, out d));
        }

        // ------------------------------------------------------------------
        // ④ "剥的"与"认的"必须同一个形状：命中 ⇔ 剥得动；不认 ⇔ 剥不动
        // ------------------------------------------------------------------
        private static void StripParseAgree()
        {
            foreach (var s in new[] { "[1年1月3日] 你好", "[12年10月] 甲", "[1年1月]你好",
                                      "你好[1年1月]", "[1年1月3] 甲", "[1年] 甲", "道友请了", "" })
            {
                int y, m, d;
                bool parsed = BubbleTextCore.TryParseLeadStamp(s, out y, out m, out d);
                bool stripped = BubbleTextCore.StripLeadStamp(s) != s;
                Eq("剥/认同形：" + Show(s), stripped, parsed);
            }
        }
    }
}
