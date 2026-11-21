// 「须知」面板判定核（NoticeCore）单测。
//
// 为什么要有这个工程（用户 10-23 需求：第一次自动弹一次，看过就不再弹，版本更新后再弹）：
//   判据写偏的症状全都不报错——
//   ① 该弹不弹（水位判反）→ 玩家永远不知道杀毒把 exe 隔离了，只会说"这 mod 连不上"；
//   ② 每次进游戏都弹（水位没前进）→ 第一天就被骂"天天弹窗"；
//   ③ 水位回写时下调（降级过 mod 又升级）→ 同一条公告弹两次；
//   ④ 弹错页（更新时弹「使用须知」）→ 老玩家被迫重读一遍入门，看不到改了什么；
//   ⑤ 判据依赖版本字符串 → `1.0.10` 被判成旧版，或者"改了内容忘改版本号"时**静默不再弹**。
// 这五类在实机上都要"正好跨版本/正好重装"才复现，所以钉在判定表里。
//
// ⚠ 诚实边界：这里只测**规则**。"面板有没有真的读水位、有没有把文案写在面板里"
//   由 Python 侧 tests/test_notice_panel_contract.py 锁在真实源码上。

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
            Check(name, EqualityComparer<T>.Default.Equals(got, want),
                  "实际=" + Show(got) + " 期望=" + Show(want));
        }

        private static string Show<T>(T v) { return v == null ? "<null>" : v.ToString(); }

        private static int Main()
        {
            Console.WriteLine("=== NoticeCore 单元测试 ===");

            // ---------------- ① 该不该自动弹 ----------------
            Console.WriteLine("[1] ShouldAutoShow");
            // 首次安装：没看过（0），最新是 3 ⇒ 弹
            Check("首次安装（0 < 3）⇒ 弹", NoticeCore.ShouldAutoShow(0, 3));
            // 都看过：3 == 3 ⇒ 不弹（用户最在意的"同版本不再弹"）
            Check("已看最新（3 == 3）⇒ 不弹", !NoticeCore.ShouldAutoShow(3, 3));
            // 版本更新：看过 2，最新 3 ⇒ 弹
            Check("版本更新（2 < 3）⇒ 弹", NoticeCore.ShouldAutoShow(2, 3));
            // 玩家没点开过、但水位被别的途径写成很大（降级后重新升级的边界）
            Check("水位超前（9 > 3）⇒ 不弹（降级回退不重弹）", !NoticeCore.ShouldAutoShow(9, 3));
            // 内容表为空：没东西可看，不许打扰
            Check("内容为空（maxSeq=0）⇒ 不弹", !NoticeCore.ShouldAutoShow(0, 0));
            Check("内容为空且水位超前 ⇒ 不弹", !NoticeCore.ShouldAutoShow(5, 0));
            // 诊断强制：忽略水位（调版面/截图用）
            Check("force=true ⇒ 无条件弹", NoticeCore.ShouldAutoShow(3, 3, true));
            Check("force=true 且内容为空 ⇒ 仍不弹（没东西可看）",
                  !NoticeCore.ShouldAutoShow(3, 0, true));

            // ---------------- ② 弹哪一页 ----------------
            Console.WriteLine("[2] TabFor");
            Eq("从没看过（seq=0）⇒ 使用须知", NoticeCore.TabFor(0), NoticeCore.TabGuide);
            Check("从没看过 ⇒ 不是更新公告", NoticeCore.TabFor(0) != NoticeCore.TabUpdates);
            Eq("看过旧版（seq=2）⇒ 更新公告", NoticeCore.TabFor(2), NoticeCore.TabUpdates);
            Eq("已看最新（seq=3）⇒ 更新公告（手动打开时也落在公告页）",
               NoticeCore.TabFor(3), NoticeCore.TabUpdates);
            Check("两个页签下标不相同", NoticeCore.TabGuide != NoticeCore.TabUpdates);

            // ---------------- ③ 哪条算「新」 ----------------
            Console.WriteLine("[3] IsNew");
            Check("看过 2，条目 3 ⇒ 新", NoticeCore.IsNew(3, 2));
            Check("看过 2，条目 2 ⇒ 不新", !NoticeCore.IsNew(2, 2));
            Check("看过 0（没看过）⇒ 全部算新", NoticeCore.IsNew(2, 0));
            Check("看过 5（超前），条目 3 ⇒ 不复古标新", !NoticeCore.IsNew(3, 5));

            // ---------------- ④ 水位只前进，绝不后退 ----------------
            Console.WriteLine("[4] NextSeen / NeedsWrite");
            Eq("前进：2 → 3", NoticeCore.NextSeen(2, 3), 3);
            Eq("持平：3 → 3", NoticeCore.NextSeen(3, 3), 3);
            Eq("不后退：9 遇 3 仍为 9（降级过 mod 再升级不重弹）", NoticeCore.NextSeen(9, 3), 9);
            Check("前进需要写盘", NoticeCore.NeedsWrite(2, 3));
            Check("持平不写盘（反复开关面板零 IO）", !NoticeCore.NeedsWrite(3, 3));
            Check("不后退也不写盘", !NoticeCore.NeedsWrite(9, 3));
            Check("从没看过也要写盘（0 → 3）", NoticeCore.NeedsWrite(0, 3));

            // ---------------- ⑤ 页脚文案 ----------------
            Console.WriteLine("[5] 页脚");
            Eq("没看过 ⇒ 第一次打开", NoticeCore.FooterSeen(0, ""), "第一次打开");
            Eq("看过但版本空 ⇒ 第一次打开（宁可少说）", NoticeCore.FooterSeen(3, ""), "第一次打开");
            Eq("看过 v1.0.0", NoticeCore.FooterSeen(2, "1.0.0"), "上次看过 v1.0.0");
            Eq("已是最新", NoticeCore.FooterUnread(3, 3), "已是最新");
            Eq("还剩 1 条", NoticeCore.FooterUnread(2, 3), "有 1 条新公告");
            Eq("还剩 3 条", NoticeCore.FooterUnread(0, 3), "有 3 条新公告");
            Eq("水位超前 ⇒ 已是最新（不显示负数）", NoticeCore.FooterUnread(9, 3), "已是最新");

            // ---------------- ⑥ 诊断哨兵裁决 ----------------
            Console.WriteLine("[6] AutoShowAllowed");
            Check("无哨兵 ⇒ 允许", NoticeCore.AutoShowAllowed(false, false));
            Check("always 存在 ⇒ 允许（由 force 决定）", NoticeCore.AutoShowAllowed(false, true));
            Check("no_notice 存在 ⇒ 禁止", !NoticeCore.AutoShowAllowed(true, false));
            Check("两个都在 ⇒ 以「永不弹」为准（一个文件就能止血）",
                  !NoticeCore.AutoShowAllowed(true, true));

            // ---------------- ⑦ 水位结构默认值 ----------------
            Console.WriteLine("[7] NoticeSeen.None");
            var none = NoticeSeen.None;
            Eq("默认 seq = 0", none.Seq, 0);
            Eq("默认没有文件", none.HasFile, false);
            Eq("默认版本为空", none.Version ?? "", "");
            Eq("文件名固定", NoticeCore.FileName, "notice_seen.json");

            // ---------------- 汇总 ----------------
            Console.WriteLine();
            if (_fails.Count == 0)
            {
                Console.WriteLine("=== 全部通过：" + _pass + " 项 ===");
                return 0;
            }
            Console.WriteLine("=== 失败 " + _fails.Count + " 项 / 通过 " + _pass + " 项 ===");
            foreach (var f in _fails) Console.WriteLine("  ✗ " + f);
            return 1;
        }
    }
}
