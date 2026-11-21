// 常驻帧回调生命周期判据（FrameTickerCore）单元测试。
//
// 真机现场（11-19 01:09，用户报障）：NPC 在同一轮提交了「收取灵果」与「向你传功」两个调用，
// 前者先开了确认窗，后者的传功因"已有交互占用"被排进「此间事了」队列并回了一句
// "已受理、不要重复调用"。此后队列**再也没有执行过** —— 玩家一直催，模型一直按"传功还在等待"答复。
//
// 根因不在排队逻辑：驱动队列的那条每帧回调已经没了。`g.timer.Frame(...)` 的登记在
// 世界重载（回主菜单 / 读档 / 换档）时会被清掉，而老实现用 `_tickRegistered` 这个
// **全进程一次**的布尔去防重复登记 ⇒ 重载后仍"记得挂过" ⇒ 永不重挂 ⇒ 整条动作通道静默瘫痪
// （队列非空 ⇒ 后续每个动作都被排进同一队列，一个都不执行）。
//
// 覆盖（每条都对应一个会静默出事的分支）：
//   ① 没有句柄 ⇒ Attach（首次 / Destroy 之后）；
//   ② 有句柄且在跑 ⇒ Alive（常态，不许重复挂）；
//   ③ 有句柄但停摆超宽限 ⇒ Revive（**这次故障的解药**，也是最容易被判宽的一条）；
//   ④ 刚挂上、还没跑第一帧 ⇒ Alive（判错会形成"挂上就撤、撤了再挂"的自激循环）；
//   ⑤ 宽限边界：正好 30 帧仍算活着，31 帧才重挂（差一帧的取舍要写死）；
//   ⑥ 帧号读不到 ⇒ Unknown（不重挂：宁可退化也不能每帧挂一条）；
//   ⑦ 同一帧只跑一份（历史事故：F9/F10/F11 同一次按键触发两次开关、开→关自己抵消）；
//   ⑧ 停摆帧数只为日志服务：没跑过返回 -1（别把"刚挂上"报成"停摆 -1 帧"）。

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

        private const int Grace = FrameTickerCore.AliveGraceFrames;

        private static FrameTickVerdict Decide(bool has, int frame, int lastRun, int attached) =>
            FrameTickerCore.Decide(has, frame, lastRun, attached);

        private static int Main()
        {
            Console.WriteLine("FrameTickerCore 单元测试（宽限 " + Grace + " 帧）");

            TestNoHandleAttaches();
            TestAliveIsQuiet();
            TestStalledRevives();
            TestJustAttachedIsNotStall();
            TestGraceBoundary();
            TestUnknownFrameDoesNotRevive();
            TestOncePerFrame();
            TestIdleFrames();
            TestIdleLabel();
            TestEffectiveWorkers();

            Console.WriteLine();
            if (_fails.Count == 0)
            {
                Console.WriteLine("全部通过（" + _pass + " 断言）");
                return 0;
            }
            Console.WriteLine("失败 " + _fails.Count + " 条 / 共 " + (_pass + _fails.Count) + " 断言");
            foreach (var f in _fails) Console.WriteLine("  - " + f);
            return 1;
        }

        // ---------- ① 没有句柄 ----------

        private static void TestNoHandleAttaches()
        {
            // 首次接线，以及 Destroy → StopAll 之后的重新 Init。
            Eq("没有句柄 ⇒ Attach（首次）", Decide(false, 1000, -1, -1), FrameTickVerdict.Attach);
            // ★这条最关键★ 重载后句柄被我们 StopAll 清掉 ⇒ 必须回到 Attach，
            //   而不是因为"以前挂过"就跳过（那正是老布尔的错）。
            Eq("没有句柄 ⇒ Attach（重建之后，哪怕帧号很大）",
               Decide(false, 99999, 99000, 99000), FrameTickVerdict.Attach);
            // 有句柄但为空（Stop 过 / 句柄丢失）也当没有 —— 上层传 has=false 同理，
            // 这里只确认"帧号很大"不会把它判成 Alive。
            Console.WriteLine("✓ ① 没有句柄必挂 通过");
        }

        // ---------- ② 活着就不打扰 ----------

        private static void TestAliveIsQuiet()
        {
            Eq("上一帧跑过 ⇒ Alive", Decide(true, 1000, 999, 500), FrameTickVerdict.Alive);
            Eq("同一帧跑过 ⇒ Alive", Decide(true, 1000, 1000, 500), FrameTickVerdict.Alive);
            Eq("宽限内没跑 ⇒ Alive", Decide(true, 1000, 1000 - Grace, 500), FrameTickVerdict.Alive);
            Console.WriteLine("✓ ② 活着不重复挂 通过");
        }

        // ---------- ③ 停摆 ⇒ 重挂（本次故障的解药） ----------

        private static void TestStalledRevives()
        {
            // 世界重载把游戏那边的登记清掉：句柄还在（我们没 Stop 过），但它再没跑过。
            Eq("停摆宽限+1 帧 ⇒ Revive", Decide(true, 1000, 1000 - Grace - 1, 500),
               FrameTickVerdict.Revive);
            // 真机那一局的形状：最后一次体检表在 3098 行，之后整段没有采样。
            Eq("停摆上千帧 ⇒ Revive", Decide(true, 50000, 30000, 1000), FrameTickVerdict.Revive);
            Console.WriteLine("✓ ③ 停摆自愈重挂 通过");
        }

        // ---------- ④ 刚挂上不算停摆 ----------

        private static void TestJustAttachedIsNotStall()
        {
            // 回调最快下一帧才跑，所以"刚挂上那一两帧"必须是 Alive。
            // 判错会形成自激：挂上 → 判停摆 → 撤 → 再挂 → 又判停摆 …
            Eq("刚挂上（同帧） ⇒ Alive", Decide(true, 1000, -1, 1000), FrameTickVerdict.Alive);
            Eq("刚挂上（+1 帧） ⇒ Alive", Decide(true, 1001, -1, 1000), FrameTickVerdict.Alive);
            // 但"挂了很久一直没跑过"是真停摆（挂载时就抛异常那种），必须能救。
            Eq("挂上后一直没跑过 ⇒ Revive", Decide(true, 1000 + Grace + 1, -1, 1000),
               FrameTickVerdict.Revive);
            // 连挂载时刻都不知道（-1）：无法判断，按不重挂处理，避免抖。
            Eq("挂载时刻未知且没跑过 ⇒ Alive（不做判断）", Decide(true, 1000, -1, -1),
               FrameTickVerdict.Alive);
            Console.WriteLine("✓ ④ 刚挂上不误判 通过");
        }

        // ---------- ⑤ 宽限边界 ----------

        private static void TestGraceBoundary()
        {
            // 差一帧的取舍写死在这里：正好 Grace 帧仍视为活着，Grace+1 才重挂。
            // 理由：多等一帧的代价远小于"频繁撤旧挂新"（后者会让队列稳定窗永远走不完）。
            Eq("正好 " + Grace + " 帧 ⇒ Alive", Decide(true, 1000, 1000 - Grace, 1),
               FrameTickVerdict.Alive);
            Eq("第 " + (Grace + 1) + " 帧 ⇒ Revive", Decide(true, 1000, 1000 - Grace - 1, 1),
               FrameTickVerdict.Revive);
            // 宽限本身要是个"够小"的值 —— 队列稳定窗是 0.5~1.5s，发现驱动死了不该比它慢太多。
            Check("宽限不超过 60 帧（≈1s@60fps），否则自愈太慢", Grace <= 60,
                  "实际=" + Grace);
            Check("宽限至少 10 帧，否则单帧长卡就误判", Grace >= 10, "实际=" + Grace);
            Console.WriteLine("✓ ⑤ 宽限边界 通过");
        }

        // ---------- ⑥ 帧号读不到 ----------

        private static void TestUnknownFrameDoesNotRevive()
        {
            // `Time.frameCount` 读不到时给 -1。这时**不重挂**：宁可退化成旧行为，
            // 也不能每帧撤旧挂新（那会把计时器表和队列状态机一起搞坏）。
            Eq("帧号=-1 且有句柄 ⇒ Unknown", Decide(true, -1, 900, 800), FrameTickVerdict.Unknown);
            Eq("帧号=-1 且无句柄 ⇒ 仍要挂", Decide(false, -1, -1, -1), FrameTickVerdict.Attach);
            Console.WriteLine("✓ ⑥ 帧号读不到不重挂 通过");
        }

        // ---------- ⑦ 同一帧只跑一份 ----------

        private static void TestOncePerFrame()
        {
            // 历史事故形状：热键的帧回调被重复注册 ⇒ 同一次按键触发两次开关、开→关自己抵消。
            Check("同一帧第二次调用 ⇒ 不跑", !FrameTickerCore.ShouldRunThisFrame(500, 500));
            Check("换一帧 ⇒ 跑", FrameTickerCore.ShouldRunThisFrame(501, 500));
            Check("第一次（从未跑过） ⇒ 跑", FrameTickerCore.ShouldRunThisFrame(500, -1));
            // 读不到帧号时不去重（拦住会让回调彻底不工作）
            Check("帧号=-1 ⇒ 放行", FrameTickerCore.ShouldRunThisFrame(-1, 123));
            Console.WriteLine("✓ ⑦ 同帧去重 通过");
        }

        // ---------- ⑧ 停摆帧数（只为日志） ----------

        private static void TestIdleFrames()
        {
            Eq("正常：差值", FrameTickerCore.IdleFrames(1000, 995), 5);
            Eq("从没跑过 ⇒ -1（别把'刚挂上'报成'停摆 -1 帧'）",
               FrameTickerCore.IdleFrames(1000, -1), -1);
            Eq("帧号读不到 ⇒ -1", FrameTickerCore.IdleFrames(-1, 995), -1);
            Console.WriteLine("✓ ⑧ 停摆帧数 通过");
        }

        // ---------- ⑨ idle 显示口径（11-19 新增） ----------

        /// <summary>
        /// 老实现一律打 `idle=?`，而 `Init` 末尾打这行时回调**刚交出去、游戏还没进下一帧**，
        /// 必然没跑过 ⇒ 这一行在**最需要它的时刻**（每次刚进世界）永远没有信息。
        /// 而"刚挂上"与"挂了但一直没跑"在排查上是相反结论，都显示成 `?` 就分不开。
        /// </summary>
        private static void TestIdleLabel()
        {
            // 跑过：直接给数字
            Eq("跑过 5 帧前 ⇒ 5", FrameTickerCore.IdleLabel(1005, 1000, 990), "5");
            Eq("同一帧刚跑过 ⇒ 0", FrameTickerCore.IdleLabel(1000, 1000, 990), "0");

            // 没跑过、仍在宽限内 ⇒ ~N（正常，还没轮到）
            Eq("刚挂上 0 帧 ⇒ ~0", FrameTickerCore.IdleLabel(1000, -1, 1000), "~0");
            Eq("刚挂上 3 帧 ⇒ ~3", FrameTickerCore.IdleLabel(1003, -1, 1000), "~3");
            Eq("正好到宽限 ⇒ 仍是 ~（还没越过判据）",
               FrameTickerCore.IdleLabel(1000 + Grace, -1, 1000), "~" + Grace);

            // 没跑过、已超宽限 ⇒ !N（真故障：挂了 N 帧一次都没跑）
            Eq("超宽限 1 帧 ⇒ !N", FrameTickerCore.IdleLabel(1000 + Grace + 1, -1, 1000),
               "!" + (Grace + 1));
            Eq("挂了 90 帧没跑 ⇒ !90", FrameTickerCore.IdleLabel(1090, -1, 1000), "!90");

            // 读不到帧号 / 不知道何时挂的
            Eq("帧号读不到 ⇒ ?", FrameTickerCore.IdleLabel(-1, 995, 990), "?");
            Eq("不知道挂载时刻 ⇒ ~", FrameTickerCore.IdleLabel(1000, -1, -1), "~");

            // ★与 Decide 的口径必须一致★ 否则会出现"日志说没问题、Decide 判停摆"这种自相矛盾
            int over = 1000 + Grace + 1;
            Check("!N 的那一刻，Decide 也判 Revive",
                  Decide(true, over, -1, 1000) == FrameTickVerdict.Revive);
            int at = 1000 + Grace;
            Check("~N 的那一刻，Decide 不判 Revive",
                  Decide(true, at, -1, 1000) != FrameTickVerdict.Revive);

            Console.WriteLine("✓ ⑨ idle 显示口径 通过");
        }

        // ---------- ⑩ 叠注册的"实际干活份数"（11-19 新增） ----------

        /// <summary>
        /// 裸 `g.timer.Frame(体, 1, true)` 每进一次世界叠一份，真机日志里那些键从第 3 段起
        /// 恒为 2.00 次/帧。但 `Attach` 的包装第一句是 `Once` ⇒ 干活的永远只有一份。
        /// 这个数值决定日志怎么说话：不能让它被读成"副作用也翻倍了"。
        /// </summary>
        private static void TestEffectiveWorkers()
        {
            Eq("没登记 ⇒ 0 份干活", FrameTickerCore.EffectiveWorkers(0), 0);
            Eq("负数是脏输入 ⇒ 当 0", FrameTickerCore.EffectiveWorkers(-3), 0);
            Eq("正常登记 1 份 ⇒ 1 份干活", FrameTickerCore.EffectiveWorkers(1), 1);
            Eq("叠了 2 份 ⇒ 仍只 1 份干活（Once 去重）", FrameTickerCore.EffectiveWorkers(2), 1);
            Eq("叠了 5 份 ⇒ 仍只 1 份干活", FrameTickerCore.EffectiveWorkers(5), 1);
            Console.WriteLine("✓ ⑩ 实际干活份数 通过");
        }
    }
}
