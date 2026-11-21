/// <summary>
/// BusyWatch —— 「玩家忙 / 战斗中」判据的**接线层**（11-19 加，用户报障驱动）。
/// 状态机在 <see cref="BusyWatchCore"/>（纯逻辑、有单测），本类只负责：采样、拼文案、打日志。
///
/// 【它要解决的报障原话】
/// "有用户反映检测会频繁显示自己正在忙或者正在处于战斗当中，但是实际不是。"
///
/// 【为什么现有日志查不出这件事】
/// `BusyReason` 的结论只在**事件发生的那一刻**被顺带打出去（`ContactDuty` 的主动开口分流、
/// `ActionWatcher` 的动作收尾、`NoticePanel` 的自动弹出）。问题有三层：
///   ① **只记结论、不记原始电平** —— 看到"战斗中"三个字，没法知道是
///      `world.battle.isBattle` 真为真（游戏自己的位卡住）、还是 `UIBattleInfo` 可见
///      （结算期残留）、还是判据抛异常后被兜成 false。**这几种修法完全相反**；
///   ② **没有边沿概念** —— 用户说"频繁"，那是一个**抖动**现象；现有日志是一堆孤立时刻，
///      数不出"1 分钟里判了 40 次忙"；
///   ③ **没有持续时长** —— "一直忙"和"每秒忙一下"是两种完全不同的故障，看结论串分不出来。
///
/// 【采样为什么不能挂在调用点】
/// `BusyReason` 的调用点是**事件驱动**的（有主动开口、有动作收尾才调）。而"玩家明明不忙
/// 却被判忙"恰恰可能发生在一段**没有任何事件**的时间里 —— 挂在调用点就永远看不到它。
/// 所以要定时采样（1s 一次）。
///
/// 【开销】默认 1.0s 一次，每次一次 `BusyReason`（读 12 个字段，实测 avg 0.3~0.46ms）
/// ⇒ 约 0.04% CPU。**不逐帧**：那是 09-28 卡顿事故的形状（每帧两次判据 ⇒ 大地图行走隔几秒一顿），
/// 这里刻意慢两个数量级 —— 这个故障的时间尺度是秒级到分钟级，不需要帧级精度。
/// 想彻底关掉：数据根放一个 `_diag_no_busy_watch.txt`。
/// </summary>
using System;

namespace AgentLoopBridge
{
    internal static class BusyWatch
    {
        /// <summary>采样间隔（秒）。见类头"开销"——刻意不快，这个故障是秒级现象。</summary>
        private const double SampleSeconds = 1.0;

        /// <summary>状态机（纯逻辑核，有独立单测 `csharp/tests/BusyWatchCoreTests`）。</summary>
        private static readonly BusyWatchCore _core = new BusyWatchCore();

        /// <summary>日志出口（主工程注入 `ModMain.P`）。未注入 = 本类完全不工作（零副作用）。</summary>
        internal static Action<string> Logger;

        private static double _lastSampleAt = -1;

        /// <summary>上一次采样判定的"忙"（诊断读用）。</summary>
        internal static bool LastBusy { get { return _core.Busy; } }

        /// <summary>
        /// 采一次样。由 `FrameTicker` 的常驻回调每帧调（内部按 `SampleSeconds` 节流，
        /// 所以调用频率高也无害 —— 节流在 `Time.unscaledTime` 上做，游戏暂停时不会推进）。
        /// </summary>
        internal static void Sample(double now)
        {
            var log = Logger;
            if (log == null) return;
            if (Disabled()) return;

            // 节流。`unscaledTime` 在暂停时停住 ⇒ 暂停期间不采样（正是要的：
            // 暂停不是"玩家忙"，也不该把静止的时间算进持续时长）。
            if (_lastSampleAt >= 0 && now - _lastSampleAt < SampleSeconds) return;
            if (now < _lastSampleAt) _lastSampleAt = now - SampleSeconds;    // 时间回退：立刻采一次
            _lastSampleAt = now;

            // 判据与所有调用方**同一个**入口，绝不另写一套（本仓最忌"同一个忙字两套口径"）
            string reason = "";
            try { reason = WorldStateGate.BusyReason() ?? ""; } catch { }

            BusyTickResult r = _core.Tick(now, reason.Length > 0, reason);
            if (r.Kind == BusyEvent.None) return;

            string line;
            switch (r.Kind)
            {
                case BusyEvent.EnterBusy:
                    line = "[BusyWatch] ★进忙★（上一段闲了 " + r.SegSeconds.ToString("0.0") + "s）"
                         + "｜原因=" + r.Detail + "｜" + WorldStateFacts.BusyEvidenceRaw();
                    break;
                case BusyEvent.LeaveBusy:
                    line = "[BusyWatch] 出忙（忙了 " + r.SegSeconds.ToString("0.0") + "s）"
                         + "｜上一原因=" + (string.IsNullOrEmpty(r.Detail) ? "(无)" : r.Detail)
                         + "｜" + WorldStateFacts.BusyEvidenceRaw();
                    break;
                case BusyEvent.ReasonChanged:
                    line = "[BusyWatch] 忙碌原因变了（同段忙了 " + r.SegSeconds.ToString("0.0") + "s）"
                         + "｜新原因=" + r.Detail + "｜" + WorldStateFacts.BusyEvidenceRaw();
                    break;
                case BusyEvent.StillBusy:
                    line = "[BusyWatch] 持续判忙 " + r.SegSeconds.ToString("0") + "s（仍是同一原因）"
                         + "｜原因=" + r.Detail + "｜" + WorldStateFacts.BusyEvidenceRaw();
                    break;
                case BusyEvent.SampleGap:
                    // 这段时间"我们没在看"。两种解释（加载屏 / 帧回调丢了）都必须留证 ——
                    // 尤其后者：它是 11-19 那次"动作排了队永不执行"的同一形状，
                    // 而这一行是它的旁证（[FrameTicker] 的停摆日志只在 Ensure 时才触发）。
                    line = "[BusyWatch] 采样中断 " + r.SegSeconds.ToString("0.0") + "s"
                         + "（这段时间的忙闲不可知：可能是加载屏/暂停，也可能是帧回调丢了 —— "
                         + "若同时见 [FrameTicker] … 停摆，则是后者）"
                         + "｜中断前原因=" + (string.IsNullOrEmpty(r.Detail) ? "(不忙)" : r.Detail);
                    break;
                case BusyEvent.Stat:
                    line = "[BusyWatch] 近 " + r.Span.ToString("0") + "s：进忙 " + r.Enters
                         + " 次，累计忙 " + r.BusySeconds.ToString("0") + "s"
                         + (r.Enters >= BusyWatchCore.JitterEnters
                            ? "（★抖动：同一判据反复进出，查原始电平是否有边沿读错★）" : "");
                    break;
                default:
                    return;
            }
            try { log(line); } catch { }
        }

        /// <summary>世界重载：状态归零（旧世界"忙了多久"对新世界毫无意义，
        /// 留着会让出世界后第一行打出"忙了 900s"这种假时长）。
        /// 采样节流位一并复位，新世界立刻开始采样。</summary>
        internal static void OnWorldChanged()
        {
            _core.Reset();
            _lastSampleAt = -1;
        }

        /// <summary>关掉整个观测（空文件 `_diag_no_busy_watch.txt`）。每 5 秒才查一次文件存在性，
        /// 不逐帧 IO。
        ///
        /// 走 `ModPaths` 而不是 `DiagSwitches`：后者在 `#if AB_UI` 里，而本类属于 `state/`
        /// （该目录的边界契约见 `tests/test_state_module_boundary.py`）。
        /// </summary>
        private static bool Disabled()
        {
            if (_checkAt != 0 && Environment.TickCount - _checkAt < 5000) return _disabled;
            _checkAt = Environment.TickCount;
            try
            {
                _disabled = System.IO.File.Exists(
                    System.IO.Path.Combine(ModPaths.DiagDir, "_diag_no_busy_watch.txt"));
            }
            catch { _disabled = false; }
            return _disabled;
        }

        private static int _checkAt;
        private static bool _disabled;
    }
}
