/// <summary>
/// BusyWatchCore —— 「玩家忙 / 战斗中」观测的**纯逻辑核**（11-19，用户报障驱动）。
///
/// 【报障原话】"有检测会频繁显示自己正在忙或者正在处于战斗当中，但是实际不是。"
///
/// 【为什么判据要单独成一个核】
/// 本仓既有约定（`DeferredActionCore` / `BattleFlowCore` / `FrameTickerCore` …）：**判据归核、接线归层**。
/// 这里每条规则错在真机上都是**静默的**：
///   · 边沿判错 ⇒ 每秒打一行"进忙"⇒ 日志被淹，真故障反而看不见；
///   · "持续忙"补报的时间窗算错 ⇒ 要么刷屏，要么永不补（那正好丢掉"卡住不放"这个形态）；
///   · 统计窗口不清零 ⇒ "近 60s 进忙 3 次"永远是 3，用户说的"频繁"被抹平；
///   · 时间不前进时照常累计 ⇒ 游戏暂停/加载屏恢复那一瞬误报一堆边沿。
/// 四种都没法靠"读日志"回头发现，所以必须有断言锁住。
///
/// 【它回答什么】
/// 只看三个输入：当前时刻、本次采样判定"忙不忙"、原因串。输出一个**事件**
/// （进忙 / 出忙 / 原因变化 / 持续忙补报 / 抖动统计 / 无话可说）。
/// 时间与判据全由调用方给 —— 核不读时钟、不碰 Unity、不碰 `BusyReason`，于是单测能精确构造
/// "1 分钟里进忙 12 次""忙了 31 秒该不该补一行""暂停导致时间不前进"这些边界。
///
/// 【为什么用"事件"而不是让核直接打日志】
/// 打日志属于接线（要走 `ModMain.P`、要拼原始电平）。核只回答"现在该不该说、说哪一类"，
/// 接线层负责文案与电平。这样核里零字符串拼接、零 IO，单测只需比对事件序列。
///
/// 【一次采样最多产生一行】
/// 实质事件（进忙/出忙/原因变化/补报）与统计（近 60s）可能同拍到点。
/// 本核**优先返回实质事件**，统计留到下一拍 —— 否则那一行统计会被实质事件的现场淹掉，
/// 而它恰恰是量化"频繁"的唯一一行。
/// </summary>
using System;

namespace AgentLoopBridge
{
    /// <summary>一次采样得出的观测事件类型。</summary>
    internal enum BusyEvent
    {
        /// <summary>没有要说的话（常态：状态没变、也没到补报时间）。</summary>
        None = 0,
        /// <summary>由闲转忙。`Detail` = 原因串。</summary>
        EnterBusy = 1,
        /// <summary>由忙转闲。`Detail` = 上一段的原因串。</summary>
        LeaveBusy = 2,
        /// <summary>一直忙，但原因换了（例：从"战斗中"变成"Mask 层有模态遮罩"）。`Detail` = 新原因。</summary>
        ReasonChanged = 3,
        /// <summary>持续忙，到了补报间隔（抓"卡住不放"）。`Detail` = 原因串。</summary>
        StillBusy = 4,
        /// <summary>抖动统计窗口到点。`Enters`/`BusySeconds`/`Span` 有值。</summary>
        Stat = 5,
        /// <summary>两次采样之间隔了太久 ⇒ **这段时间我们没在看**，无法判断忙闲。
        /// `SegSeconds` = 缺口时长。见 <see cref="BusyWatchCore.MaxDtSeconds"/>。</summary>
        SampleGap = 6,
    }

    /// <summary>一个事件 + 它的现场数据（接线层据此拼文案）。</summary>
    internal struct BusyTickResult
    {
        public BusyEvent Kind;
        /// <summary>本事件的说明串：新原因 / 或上一段原因。</summary>
        public string Detail;
        /// <summary>上一段（忙或闲）持续了多久（秒）。边沿类事件有意义。</summary>
        public double SegSeconds;
        /// <summary>统计窗口内的进忙次数（仅 `Stat`）。</summary>
        public int Enters;
        /// <summary>统计窗口内累计忙的秒数（仅 `Stat`）。</summary>
        public double BusySeconds;
        /// <summary>统计窗口长度（仅 `Stat`）。</summary>
        public double Span;
    }

    internal sealed class BusyWatchCore
    {
        /// <summary>持续忙碌时每隔多久补一行。30 秒的理由：短于它，"卡住"与"正常打一场仗"
        /// 还分不清；长于它，用户报"一直忙"时日志里那段空白会让人怀疑是没记录。
        /// 真机战斗通常 1~3 分钟，30 秒间隔既能看到"还在忙"的连续性，又不至于刷屏。</summary>
        public const double StuckLogSeconds = 30.0;

        /// <summary>抖动统计的汇总周期（秒）。60 秒的理由：用户说的是"频繁"，
        /// 而"频繁"要有个可数的口径 —— 60 秒里进忙几次，跨用户、跨机器都能直接比。</summary>
        public const double StatSeconds = 60.0;

        /// <summary>抖动告警阈值（每窗口进忙次数）。超过就在日志里标注"抖动"：
        /// 正常游玩里一分钟内进出"忙"状态十次以上，基本只能是判据在抖，
        /// 而不是真的打仗/开窗十次。</summary>
        public const int JitterEnters = 10;

        /// <summary>单次累计的最大时间步（秒）。接线层按 1s 采样，所以正常 `dt≈1`。
        /// 出现远大于它的 `dt` 只有一种解释：**中间有一段时间压根没被采样**（加载屏整段没有帧回调）。
        /// 那段"不知道发生了什么"的时间不该算进"忙了多久" —— 按采样间隔封顶，
        /// 宁可少算几秒，也不要在恢复瞬间报出一段凭空的"忙了 87s"。</summary>
        public const double MaxDtSeconds = 5.0;

        private bool _busy;
        private string _reason = "";
        private double _sinceAt;        // 本段（忙或闲）起点
        private double _lastStillAt;    // 上次补报时刻
        private double _windowAt;       // 统计窗口起点
        private double _lastNow;        // 上次采样的时刻（用于算 dt）
        private int _enters;
        private double _busySeconds;
        private bool _init;

        /// <summary>当前是否判定为忙（诊断读用）。</summary>
        public bool Busy { get { return _busy; } }

        /// <summary>最近一次的原因串。</summary>
        public string Reason { get { return _reason; } }

        /// <summary>世界重载/初始化：状态与统计一起归零。换世界后"上一个世界的进忙次数"
        /// 与新世界无关，混在一起会让抖动统计失去意义。</summary>
        public void Reset()
        {
            _busy = false;
            _reason = "";
            _sinceAt = _windowAt = _lastStillAt = _lastNow = 0;
            _enters = 0;
            _busySeconds = 0;
            _init = false;
        }

        /// <summary>
        /// 喂一次采样。返回"这一刻该说什么"。
        ///
        /// `now` 应单调递增（`Time.unscaledTime`）。**时间不前进时不做任何判断也不累计**：
        /// 游戏暂停/加载屏会让时间停住，那时若照常累计，恢复瞬间会误报一堆边沿。
        /// </summary>
        public BusyTickResult Tick(double now, bool busy, string reason)
        {
            reason = reason ?? "";

            if (!_init)
            {
                // 第一拍只立基准：开局不该有一行"进忙"。那时玩家正在加载、判定为忙很正常，
                // 打出来只是噪音，还会让人以为"刚进游戏就误判"。
                _init = true;
                _busy = busy;
                _reason = reason;
                _sinceAt = _windowAt = _lastStillAt = _lastNow = now;
                return new BusyTickResult { Kind = BusyEvent.None };
            }

            double dt = now - _lastNow;
            if (dt < 0) { _lastNow = now; return new BusyTickResult { Kind = BusyEvent.None }; }   // 时钟回退：不判
            _lastNow = now;

            // ★时间没前进 ⇒ 一律不判★
            //   调用方传的是 `Time.unscaledTime`，它在**游戏暂停 / 加载屏**时是停住的。
            //   那段时间既不是"忙"也不是"闲" —— 是"我们没在看"。此时若照常判边沿：
            //     · 暂停那一刻调用方可能给出 busy=false（帧回调没跑）⇒ 误报"出忙"；
            //     · 恢复后第一拍又给出 busy=true ⇒ 再误报一次"进忙"。
            //   两次都是假的，而且每次暂停都会各来一遍 ⇒ 正是用户说的"频繁"的形状之一。
            //   所以 `dt <= 0` 直接返回 None，连"状态是否一致"都不判。
            if (dt <= 0) return new BusyTickResult { Kind = BusyEvent.None };

            if (busy) _busySeconds += (dt > MaxDtSeconds ? MaxDtSeconds : dt);   // 封顶见 MaxDtSeconds

            // ★采样缺口★ 正常 `dt≈采样间隔`。突然来一个很大的 dt 只有两种解释：
            //   ① 游戏加载屏 / 暂停（时间本身停住了，恢复后跳一段）；
            //   ② **帧回调死了**（11-19 那次故障的形状：世界重载把登记清掉，队列永久停摆）。
            // 两者都必须留一行 —— 尤其 ②，否则"这段时间判据说什么"永远是个空白，
            // 而用户报的"频繁判忙"很可能就发生在缺口两侧。
            // 这一行同时也是 `FrameTicker` 那条链的旁证：如果它频繁出现，说明帧回调在反复丢。
            if (dt > MaxDtSeconds)
            {
                double gap = dt;
                _init = false;      // 缺口之后重新立基准：缺口里的忙闲不可知，别拿它当"上一段"
                _lastNow = now;
                return new BusyTickResult
                {
                    Kind = BusyEvent.SampleGap,
                    SegSeconds = gap,
                    Detail = _reason,
                };
            }

            double seg = now - _sinceAt;

            // ---- 状态没变 ----
            if (busy == _busy && reason == _reason)
            {
                if (busy && now - _lastStillAt >= StuckLogSeconds)
                {
                    _lastStillAt = now;
                    var still = new BusyTickResult
                    {
                        Kind = BusyEvent.StillBusy, Detail = reason, SegSeconds = seg,
                    };
                    return CollapseStat(still, now);
                }
                return CollapseStat(new BusyTickResult { Kind = BusyEvent.None }, now);
            }

            // ---- 边沿 ----
            BusyTickResult r;
            if (busy)
            {
                if (_busy)
                {
                    r = new BusyTickResult { Kind = BusyEvent.ReasonChanged, Detail = reason, SegSeconds = seg };
                }
                else
                {
                    r = new BusyTickResult { Kind = BusyEvent.EnterBusy, Detail = reason, SegSeconds = seg };
                    _enters++;
                }
                _lastStillAt = now;
            }
            else
            {
                r = new BusyTickResult { Kind = BusyEvent.LeaveBusy, Detail = _reason, SegSeconds = seg };
            }

            _busy = busy;
            _reason = reason;
            _sinceAt = now;
            return CollapseStat(r, now);
        }

        /// <summary>统计窗口到点时怎么处理：有实质事件就让实质事件先出去（见类头"一次采样最多一行"）。</summary>
        private BusyTickResult CollapseStat(BusyTickResult primary, double now)
        {
            if (now - _windowAt < StatSeconds) return primary;
            if (primary.Kind != BusyEvent.None) return primary;

            var st = new BusyTickResult
            {
                Kind = BusyEvent.Stat,
                Enters = _enters,
                BusySeconds = _busySeconds,
                Span = now - _windowAt,
            };
            _windowAt = now;
            _enters = 0;
            _busySeconds = 0;
            return st;
        }
    }
}
