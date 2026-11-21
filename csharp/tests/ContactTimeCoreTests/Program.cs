// 通讯录「最近」行时间判档（ContactTimeCore.Format）单测。
//
// 判档偏了的症状都不报错：
//   ① 跨天判成"天前"边界错（昨天显示成"1天前"或反过来）→ 玩家对列表的信任感崩；
//   ② HH:mm 用了 UTC → 与玩家手表差八小时（国内 -8），比不打更误导；
//   ③ 时钟倒挂（改系统时间/跨存档）若硬算 → 负数"−3天前"；
//   ④ 无记录打"0天"→ 每个新好友都顶着个假时间。
// 全部用**固定相对偏移**构造，任何机器任何时刻跑结果一致。
//
// ⚠ 诚实边界：只测规则；接线由 tests/test_contact_recent_time_contract.py 锁。

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
                  "实际=" + (got == null ? "<null>" : got.ToString()) + " 期望=" + (want == null ? "<null>" : want.ToString()));

        private static int Main()
        {
            Console.WriteLine("=== ContactTimeCore 单元测试 ===");

            // 锚点：本地今天正午（LocalDateTime 往返自洽，机器时区无关）
            long noon = new DateTimeOffset(DateTime.Today.AddHours(12)).ToUnixTimeSeconds();
            const long H = 3600, D = 24 * 3600;

            Eq("无记录 → 空", ContactTimeCore.Format(noon, 0), "");
            Eq("负数 → 空", ContactTimeCore.Format(noon, -5), "");
            Eq("时钟倒挂 → 空", ContactTimeCore.Format(noon, noon + 10), "");
            Eq("59 秒前 → 刚刚", ContactTimeCore.Format(noon, noon - 59), "刚刚");
            Eq("60 秒前 → 时分", ContactTimeCore.Format(noon, noon - 60), "11:59");
            Eq("今天 3 小时前 → 时分", ContactTimeCore.Format(noon, noon - 3 * H), "09:00");
            Eq("昨天同一钟点 → 昨天", ContactTimeCore.Format(noon, noon - D), "昨天");
            Eq("昨天再多一小时仍是昨天", ContactTimeCore.Format(noon, noon - D - H), "昨天");
            Eq("前天 → 2天前", ContactTimeCore.Format(noon, noon - 2 * D), "2天前");
            Eq("6 天前 → 6天前", ContactTimeCore.Format(noon, noon - 6 * D), "6天前");

            // 7 天起改日期形：期望值按**本地日历**现算（测的是"选哪种格式"，不是日历算术）
            var then10 = DateTime.Today.AddDays(-10).AddHours(12);
            string want10 = then10.Year == DateTime.Today.Year
                ? then10.Month + "月" + then10.Day + "日"
                : then10.Year + "年" + then10.Month + "月" + then10.Day + "日";
            Eq("10 天前 → 日期形", ContactTimeCore.Format(noon, noon - 10 * D), want10);

            var then400 = DateTime.Today.AddDays(-400).AddHours(12);
            Eq("400 天前 → 带年", ContactTimeCore.Format(noon, noon - 400 * D),
               then400.Year + "年" + then400.Month + "月" + then400.Day + "日");

            // 午夜边界：现在 00:30，对方昨天 23:50 —— 40 分钟前，但**日历日**是昨天
            long midnight = new DateTimeOffset(DateTime.Today.AddMinutes(30)).ToUnixTimeSeconds();
            Eq("跨午夜 40 分钟 → 昨天（日历日优先，微信同款）",
               ContactTimeCore.Format(midnight, midnight - 40 * 60), "昨天");

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
