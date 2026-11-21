// TurnRegistryCore（回合登记表纯逻辑核）单元测试。
//
// 被测的是什么：「这个 NPC 此刻有没有回合在跑」这一件事 —— Begin / End / IsRunning / TTL / 清场。
//
// 为什么值得单独钉：它是**忙标（「对方正在斟酌…」）的唯一事实来源**，而这层出错的症状
// 全是静默的、且**双向**都疼：
//   · 该忙却不忙 → 玩家点完同意后盯着一个毫无提示的窗口等 15 秒（10-17 真机事故正是它）；
//   · 不忙却忙着 → 删除/压缩入口被 `_busy` 守卫挡死并提示"回合进行中"，只有重启游戏才能恢复。
//
// 而这次改造的核心承诺是**用户明确要求的一条行为**，只有在这一层才测得干净：
//     看 A（A 在跑，忙标 ON）→ 切 B（B 空闲，忙标 OFF）→ 切回 A ⇒ **忙标要恢复成 ON**
// 它成立的前提就是"状态按 NPC 名各自留着，不随窗口走" —— 这正是下面「多 NPC 各自独立」那节。
//
// ⚠ 诚实边界：这里只测**核**。真实时钟、`ModMain.P` 日志、以及"`End` 必须挂在
//   `ContactDuty`（唯一能看到所有收口帧的地方）"那条接线，由 Python 侧的源码契约测试
//   `tests/test_busy_label_contract.py` 兜住 —— 那部分要碰 Unity 类型，WSL 里跑不起来。

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
            Console.WriteLine("=== TurnRegistryCore（回合登记表纯逻辑）单元测试 ===");
            Section("空表语义", EmptySemantics);
            Section("起止配对", BeginEndPairing);
            Section("★多 NPC 各自独立（切走不丢）", MultipleNpcsAreIndependent);
            Section("★TTL 安全阀", TtlSafetyValve);
            Section("重复 Begin 只算一次", RepeatBeginKeepsOneTurn);
            Section("End 幂等且未知名字安全", EndIsIdempotent);
            Section("阶段文案随回合走", PhaseFollowsTheTurn);
            Section("换存档整表清空", ClearAllResetsEverything);
            Section("脏输入容错", GarbageTolerated);
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
                Check("[" + name + "] 抛异常", false, e.GetType().Name + ": " + e.Message);
            }
        }

        /// <summary>可控时钟：测试自己推进"现在"，不靠 sleep（TTL 是 900s，睡不起）。</summary>
        private sealed class Clock
        {
            public long Ticks;
            public Clock() { Ticks = new DateTime(2026, 10, 17, 12, 0, 0, DateTimeKind.Utc).Ticks; }
            public void Advance(double seconds) { Ticks += (long)(seconds * TimeSpan.TicksPerSecond); }
            public long Now() { return Ticks; }
        }

        private static TurnRegistryCore Make(Clock c, List<string> log = null)
        {
            return new TurnRegistryCore(c.Now, log == null ? (Action<string>)null : log.Add);
        }

        // ---------------------------------------------------------------- 用例

        private static void EmptySemantics()
        {
            var c = new Clock();
            var r = Make(c);
            Check("未登记的 NPC 不忙（未知=false：宁可只亮红点）", !r.IsRunning("张三"));
            Eq("空表 Count = 0", r.Count, 0);
            Eq("未登记的阶段文案为空串", r.PhaseOf("张三"), "");
            // 空参不许写进去："不知道"不许变成"忙"
            Check("空名 Begin 不登记", !r.Begin(""));
            Check("null Begin 不登记", !r.Begin(null));
            Eq("空参调用后表仍为空", r.Count, 0);
            Check("空名 IsRunning = false", !r.IsRunning(""));
            Check("空名 End 安全", !r.End(""));
        }

        private static void BeginEndPairing()
        {
            var c = new Clock();
            var r = Make(c);
            Check("Begin 首次 = 状态真的变了（调用方据此打日志）", r.Begin("姜萌"));
            Check("Begin 之后在跑", r.IsRunning("姜萌"));
            Eq("Count = 1", r.Count, 1);
            Check("End 真的清掉了", r.End("姜萌"));
            Check("End 之后不在跑", !r.IsRunning("姜萌"));
            Eq("Count 归零", r.Count, 0);
        }

        /// <summary>★ 用户明确要求的那条行为 ★
        /// 看 A（A 在跑）→ 切 B → 切回 A，A 的忙态**必须原样恢复**。
        /// 这就是"键是 NPC 名而不是当前窗口"的意义 —— 若状态住在窗口里，切走即丢。</summary>
        private static void MultipleNpcsAreIndependent()
        {
            var c = new Clock();
            var r = Make(c);
            r.Begin("姜萌");                       // A 开始跑
            Check("A 在跑", r.IsRunning("姜萌"));
            Check("此时 B 不忙（切到 B 窗口 ⇒ 忙标灭）", !r.IsRunning("江宇文"));

            r.Begin("江宇文");                     // B 也开始跑（两人同时有回合）
            Check("A 仍在跑（多 NPC 各自独立）", r.IsRunning("姜萌"));
            Check("B 也在跑", r.IsRunning("江宇文"));
            Eq("Count = 2", r.Count, 2);

            r.End("江宇文");                       // B 先收口
            Check("B 结束后不忙", !r.IsRunning("江宇文"));
            Check("★ A 不受影响（切回 A ⇒ 忙标要能恢复成 ON）", r.IsRunning("姜萌"));

            r.End("姜萌");
            Check("A 结束后不忙（切回 A ⇒ 忙标灭）", !r.IsRunning("姜萌"));
            Eq("两人都收口后 Count = 0", r.Count, 0);
        }

        /// <summary>TTL 是"收口帧永远不到"时的最后一道闸（Python 崩了 / 帧被吞）。
        /// 没有它，被切走的那个 NPC 会把忙标永久点亮 —— 恰是本类要根治的病。</summary>
        private static void TtlSafetyValve()
        {
            var c = new Clock();
            var log = new List<string>();
            var r = Make(c, log);

            r.Begin("姜萌");
            c.Advance(TurnRegistryCore.TtlSeconds - 1);      // 差 1 秒到期
            Check("未到期 ⇒ 仍在跑（长回合不许被误杀）", r.IsRunning("姜萌"));

            c.Advance(2);                                    // 越过 TTL
            Check("过期 ⇒ 视为没在跑（安全阀放行）", !r.IsRunning("姜萌"));
            Check("过期留了一行日志（出现它说明收口帧丢了）",
                  log.Exists(s => s.Contains("过期清理")));
            Eq("过期顺手清掉，不留残条目", r.Count, 0);
            Eq("阶段文案也一并清掉", r.PhaseOf("姜萌"), "");
        }

        private static void RepeatBeginKeepsOneTurn()
        {
            var c = new Clock();
            var r = Make(c);
            r.Begin("姜萌");
            c.Advance(30);
            Check("第二次 Begin 不算状态变化（不刷日志）", !r.Begin("姜萌"));
            Eq("仍然只有一个回合", r.Count, 1);
            // 重复 Begin 会**刷新开始时刻**：多步工具往返、玩家连发消息都属同一回合，
            // 不该因为"起点较早"而被 TTL 误杀。
            c.Advance(TurnRegistryCore.TtlSeconds - 1);
            Check("刷新后仍活着（TTL 从最近一次 Begin 起算）", r.IsRunning("姜萌"));
        }

        private static void EndIsIdempotent()
        {
            var c = new Clock();
            var r = Make(c);
            r.Begin("姜萌");
            Check("首次 End = 真的有东西可清", r.End("姜萌"));
            Check("重复 End 安全（重复收口 / 失败兜底帧）", !r.End("姜萌"));
            Check("从未登记的名字 End 安全", !r.End("查无此人"));
            Check("end 之后 IsRunning 仍为 false", !r.IsRunning("姜萌"));
        }

        private static void PhaseFollowsTheTurn()
        {
            var c = new Clock();
            var r = Make(c);
            r.Begin("姜萌");
            Check("写阶段 = 变化", r.SetPhase("姜萌", "战斗进行中…"));
            Eq("读得到阶段", r.PhaseOf("姜萌"), "战斗进行中…");
            Check("同值重写不算变化", !r.SetPhase("姜萌", "战斗进行中…"));
            Check("空串 = 清除", r.SetPhase("姜萌", ""));
            Eq("清除后回落默认（空串）", r.PhaseOf("姜萌"), "");
            // ★ 阶段必须随回合一起走：不忙时若还留着上一回合的"战斗进行中…"，
            //   下一次忙会把它当成通用文案显示（`SetBusyPhase("")` 的语义）。
            r.SetPhase("姜萌", "战斗进行中…");
            r.End("姜萌");
            Eq("回合结束后阶段被一并清掉", r.PhaseOf("姜萌"), "");
        }

        private static void ClearAllResetsEverything()
        {
            var c = new Clock();
            var r = Make(c);
            r.Begin("姜萌");
            r.Begin("江宇文");
            r.SetPhase("江宇文", "战斗进行中…");
            Eq("清空前 Count = 2", r.Count, 2);
            Eq("ClearAll 返回清掉的条目数", r.ClearAll(), 2);
            Check("清空后谁都不忙", !r.IsRunning("姜萌") && !r.IsRunning("江宇文"));
            Eq("清空后 Count = 0", r.Count, 0);
            Eq("空表再清返回 0（不刷日志）", r.ClearAll(), 0);
            Eq("阶段也清了", r.PhaseOf("江宇文"), "");
        }

        private static void GarbageTolerated()
        {
            var c = new Clock();
            var r = Make(c);
            // 空/空白名：这些都不是 NPC，不许进表（进了就会有一个查不到的"幽灵忙标"）
            Check("空白名 Begin 不登记", !r.Begin("   ".Trim()));
            Eq("表仍为空", r.Count, 0);
            // 阶段写到一个没登记的 NPC 上：允许（拿到帧的顺序不保证），但读不到忙
            r.SetPhase("路人", "x");
            Check("未登记的 NPC 依然不忙", !r.IsRunning("路人"));
            // 日志回调抛异常不许影响状态机（真实实现里那是 ModMain.P，Unity 侧可能炸）
            var r2 = new TurnRegistryCore(c.Now, _ => { throw new InvalidOperationException("日志炸了"); });
            Check("日志抛异常不影响 Begin", r2.Begin("姜萌"));
            Check("日志抛异常不影响 IsRunning", r2.IsRunning("姜萌"));
            Check("日志抛异常不影响 End", r2.End("姜萌"));
        }
    }
}
