/// <summary>
/// FrameTickerCore —— 「这条常驻帧回调该不该重挂」的**纯逻辑核**（11-19）。
///
/// 【为什么单独一个核】
/// 本仓既有约定（`DeferredActionCore` / `BattleFlowCore` / `DramaSegmentCore` …）：**判据归核、接线归层**。
/// 这里每条规则错在真机上**全是静默的**：
///   · 判"活着"判宽了 ⇒ 世界重载后回调已死、"活着"结论照旧 ⇒ 又回到 11-19 那个
///     传功排进队列却永不执行、日志一个字都没有的现场；
///   · 判"死了"判宽了 ⇒ 每帧撤旧挂新 ⇒ 队列状态机被反复重置（稳定窗永远走不完），
///     而且计时器表里堆垃圾。
/// 两种都没法靠"看日志"发现，所以必须有断言锁住。
///
/// 【它回答什么】
/// 只看三个数：当前帧号、上次**真的跑起来**的帧号、以及上次挂载的帧号。
/// 不读时钟、不碰 Unity、不碰队列 —— 时间轴完全由调用方给，于是单测能精确构造
/// "刚挂上还没跑第一帧""停摆了 31 帧""同一帧被调用三次"这些边界。
///
/// 【一个刻意的设计：读不到帧号时就"假设活着"】
/// 返回 <see cref="Verdict.Unknown"/>，接线层据此**不重挂**。理由：`Time.frameCount`
/// 在这种游戏里极少失败，而一旦失败，宁可"少挂一次"（退化成 11-19 之前的旧行为）
/// 也不要"每帧挂一次"（那会把计时器表和队列状态机一起搞坏）。方向安全优先。
/// </summary>
using System;

namespace AgentLoopBridge
{
    /// <summary>一条常驻帧回调此刻的处置。</summary>
    internal enum FrameTickVerdict
    {
        /// <summary>没有句柄 ⇒ 挂一条（首次 / `StopAll` 之后）。</summary>
        Attach = 0,
        /// <summary>有句柄、最近真跑过 ⇒ 什么都不做（常态）。</summary>
        Alive = 1,
        /// <summary>有句柄、已经很久没跑 ⇒ 撤旧挂新。</summary>
        Revive = 2,
        /// <summary>帧号读不到 ⇒ 不做判断，不重挂（见类头最后一段）。</summary>
        Unknown = 3,
    }

    internal static class FrameTickerCore
    {
        /// <summary>判定"停摆"的宽限（帧）。回调每帧都跑 ⇒ 正常情况 `当前帧 - 上次运行帧` 只有 0~1。
        ///
        /// 为什么给到 30 而不是 2：`Ensure` 与"回调跑起来"之间存在一帧级的时序差，而且单帧长卡
        /// （真机体检表里见过 311ms 的尖峰）会让帧号跳跃。给 30 帧（≈0.5s@60fps）既不会误判，
        /// 相对队列 0.5~1.5s 的稳定窗也完全够快 —— 晚半秒发现驱动死了，代价可忽略。
        ///
        /// 为什么用帧号不用秒：游戏暂停 / 加载屏时帧号**不前进**。玩家在加载屏待半分钟，
        /// 用秒数会误判成"心跳死亡"并反复重挂；用帧号天然免疫。</summary>
        public const int AliveGraceFrames = 30;

        /// <summary>
        /// 要不要重挂。`hasHandle` = 手上有没有句柄；`frame` = 当前帧号（小于 0 = 读不到）；
        /// `lastRunFrame` = 回调上次真的跑起来的帧号（-1 = 还没跑过）；
        /// `attachedFrame` = 上次挂载时的帧号（-1 = 不知道）。
        /// </summary>
        public static FrameTickVerdict Decide(bool hasHandle, int frame, int lastRunFrame, int attachedFrame)
        {
            if (!hasHandle) return FrameTickVerdict.Attach;
            if (frame < 0) return FrameTickVerdict.Unknown;          // 读不到帧号 ⇒ 不判断

            if (lastRunFrame >= 0)
                return frame - lastRunFrame <= AliveGraceFrames
                    ? FrameTickVerdict.Alive
                    : FrameTickVerdict.Revive;

            // 还没跑过第一帧：只能看"挂了多久"。
            // 这条分支是**第一次挂上之后的头几帧**会走到的（回调最快下一帧才跑）；
            // 把它判成 Alive 是为了避免"刚挂上就撤掉再挂"的自激循环。
            if (attachedFrame >= 0 && frame - attachedFrame > AliveGraceFrames)
                return FrameTickVerdict.Revive;
            return FrameTickVerdict.Alive;
        }

        /// <summary>
        /// 本帧是不是已经有一份跑过了（去重）。`frame &lt; 0` ⇒ 恒 true（读不到帧号就没有去重依据，
        /// 放行比拦住好：拦住会让回调彻底不工作）。
        ///
        /// 它防的是"同一帧跑两遍" —— 当年 F9/F10/F11「同一次按键触发两次开关、开→关自己抵消」
        /// 就是这个形状。现在即便真被登记了两份，也只有一份干活。
        /// </summary>
        public static bool ShouldRunThisFrame(int frame, int lastRunFrame)
        {
            if (frame < 0) return true;
            return lastRunFrame != frame;
        }

        /// <summary>"停摆了多少帧"（日志用；-1 = 还没有运行记录）。</summary>
        public static int IdleFrames(int frame, int lastRunFrame)
        {
            if (frame < 0 || lastRunFrame < 0) return -1;
            return frame - lastRunFrame;
        }

        /// <summary>
        /// 一次补登了 N 份同键回调之后，**实际会干活的是几份**（11-19，全仓守卫扫描的产物）。
        ///
        /// 【这个函数在回答什么】
        /// 裸 `g.timer.Frame(体, 1, true)` 的返回值从不保存 ⇒ 没有任何办法 Stop ⇒ 每进一次世界
        /// 就多一份，而上一份还活着。真机日志（20:25，5 段世界）把它量出来了：走 `FrameTicker`
        /// 的键（`deferq`/`battleflow`）在整个进程里恒为 1.00 次/帧，而裸注册的
        /// （`npcmon`/`hotkeys`/`diag`/`prober`）从第 3 段起恒为 **2.00** 次/帧，且不再回落。
        ///
        /// 【为什么不是"有几份就跑几份"】
        /// 因为 `FrameTicker.Attach` 的包装回调第一句是 `Once(k)` —— 它按**帧号**去重：
        /// 同一帧里第二份、第三份……都会看到"本帧已经跑过了"直接退出。所以冗余份数再多，
        /// 真正干活的永远只有一份。
        ///
        /// 【那为什么还要管它】
        /// 三件事都有代价，而且都是静默的：
        ///   · 每帧多 N 次 `Time.frameCount` 读 + 字典查 + 委托调用（热点路径上白烧）；
        ///   · 委托与闭包捕获的对象被游戏那边的计时器表**永久持有** ⇒ 世界重载后旧世界的
        ///     对象不可回收（本仓已有"静态表活过世界重载 ⇒ 僵尸条目"的两次事故）；
        ///   · `Destroy` 撤不掉它们 ⇒ "句柄在册 = 确实在跑"这句话不成立，诊断口径被污染。
        ///
        /// 所以值为 N（N &gt; 1）时，日志应当**明确说"只有 1 份在干活"**，别让读日志的人
        /// 误以为副作用也翻了 N 倍 —— 那是两个完全不同量级的结论。
        /// </summary>
        public static int EffectiveWorkers(int registeredCopies)
        {
            if (registeredCopies <= 0) return 0;
            return 1;   // `Once` 去重 ⇒ 无论登记了几份，干活的只有一份
        }

        /// <summary>
        /// `Describe()` 里的 `idle` 该怎么显示（11-19）。
        ///
        /// 【为什么不能让调用方自己判】
        /// `ModMain.Init` 末尾打这一行时，回调**刚被交出去、游戏还没进下一帧** ⇒
        /// "上次运行帧号"必然是空的。老实现一律打成 `idle=?`，于是那一行在**最需要它的时刻**
        /// （每次刚进世界）恰好没有信息 —— 而"刚挂上"与"挂了但一直没跑"在故障排查上
        /// 是完全相反的两个结论，显示成同一个 `?` 就分不开。
        ///
        /// 【判据】
        /// 没跑过时，看"挂了多久"（`frame - attachedFrame`）：
        ///   · 仍在宽限内 ⇒ `~N`：还没轮到第一帧，正常，等着就行；
        ///   · 已超宽限 ⇒ `!N`：挂了 N 帧一次都没跑 ⇒ **真故障**（`g.timer` 那边没在驱动），
        ///     与 `Ensure` 判 `Revive` 的口径完全一致（同一个 `AliveGraceFrames`）。
        /// 跑过 ⇒ 直接给数字。
        /// </summary>
        public static string IdleLabel(int frame, int lastRunFrame, int attachedFrame)
        {
            if (frame < 0) return "?";                       // 读不到帧号：无从判断
            if (lastRunFrame >= 0) return (frame - lastRunFrame).ToString();

            if (attachedFrame >= 0)
            {
                int since = frame - attachedFrame;
                if (since > AliveGraceFrames) return "!" + since;   // 超宽限仍未跑 ⇒ 可疑
                return "~" + since;                                 // 刚挂上，还没轮到
            }
            return "~";                                      // 不知道何时挂的
        }
    }
}
