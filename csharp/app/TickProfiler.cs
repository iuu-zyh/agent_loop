/// <summary>
/// 主线程 tick 耗时采样器 —— 「大地图行走隔几秒一卡」定位用（09-28 用户拍板方案 A）。
///
/// 【用法】每个常驻帧回调（或换日扫描这类低频重活）在最外层包一对：
///     long t = TickProfiler.Begin();
///     try { ...原有逻辑... } finally { TickProfiler.End("名字", t); }
///   `End` 内部只做一次减法 + 累加（纳秒级、无托管分配），**不改变被测目标的行为**；
///   采样本身引入的偏差量级远小于它要找的尖峰（毫秒级）。
///
/// 【输出】每 <see cref="ReportSeconds"/> 秒向 Player.log 打一行汇总（按 max 降序）：
///     [TickProfiler] 5.0s 帧=+300 gc0=+3 mem=812.4MB(+12.1)
///         | hotkeys n=300 avg=0.02ms max=1.31ms@14:02:11.482 | segrec n=300 avg=0.05ms ...
///   命名约定：短名是父项（如 hotkeys），带点的是子项（如 hotkeys.pump）——父项 max 含子项。
///
/// 【判读】
///   · 顿感 = 单次尖峰 → 看 max 与它旁边的墙钟时刻（`@HH:mm:ss.fff`），可与游戏里卡的那一下对表；
///   · 周期性 → 某类项 max 远高于 avg（如每换日一次的重扫），或 n 很小但 max 很大；
///   · `gc0` 每 5s 增长 ≥3 次说明分配压力大（每帧分配 → GC 停顿是"短促顿感"的经典来源）；
///     `mem` 增量同步给出分配速率。
///
/// 【开销】每 tick 一次 Stopwatch 调用；每 5s 一次字符串拼接 + 一行日志。常态可忽略。
/// 【关闭】`Enabled = false`（或用 `_diag_no_tickprofiler.txt`，见 <see cref="Enabled"/>）；
///   定位收工后本文件连同各调用点一起可删——它是临时诊断件，不是功能件。
/// </summary>
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Text;
using UnityEngine;

namespace AgentLoopBridge
{
    internal static class TickProfiler
    {
        /// <summary>总开关。定位期默认开；收工后把这里改 false 即全链路归零（调用点留着不伤）。</summary>
        internal static bool Enabled = true;

        /// <summary>汇总间隔（秒）。</summary>
        private const float ReportSeconds = 5f;

        /// <summary>单行最多列出的项数（防极端场景刷屏）。</summary>
        private const int MaxRows = 24;

        private sealed class Acc
        {
            public long N;          // 采样次数
            public double SumMs;    // 累计耗时
            public double MaxMs;    // 单次最大耗时
            public string MaxAt;    // 最大耗时发生的墙钟（HH:mm:ss.fff）
        }

        private static readonly Dictionary<string, Acc> _acc = new Dictionary<string, Acc>(32);
        private static long _lastReportTs = Stopwatch.GetTimestamp();
        private static int _lastFrameCount = -1;
        private static int _lastGc0 = -1;
        private static long _lastMemBytes = -1;
        private static string _reportLogHeader;

        /// <summary>开始一次采样（无分配）。</summary>
        internal static long Begin()
        {
            return Enabled ? Stopwatch.GetTimestamp() : 0L;
        }

        /// <summary>从 <see cref="Begin"/> 的时刻算到现在经过了多少毫秒。
        ///
        /// 给"想把这数字直接打进日志"的调用点用（`End` 只做累计与周期汇总，
        /// 低频调用点靠它看不到单次值）。`Enabled=false` / 未 Begin 时返回 0。</summary>
        internal static double ElapsedMs(long t0)
        {
            if (!Enabled || t0 == 0L) return 0.0;
            try { return (Stopwatch.GetTimestamp() - t0) * 1000.0 / Stopwatch.Frequency; }
            catch { return 0.0; }
        }

        /// <summary>按**已经算好的毫秒数**记一笔（不配套 Begin/End 计时）。
        ///
        /// 给"耗时是在别处量的、但想并进同一张体检表"的调用点用 —— 例如
        /// `WsClient` 的排队/执行分段：它对同一个请求量了两段，两次 `Begin` 会互相覆盖，
        /// 所以先各自算出毫秒再回填。`Enabled=false` 时什么都不做。</summary>
        internal static void AddMs(string name, double ms)
        {
            if (!Enabled) return;
            try
            {
                Acc a;
                if (!_acc.TryGetValue(name, out a)) { a = new Acc(); _acc[name] = a; }
                a.N++;
                a.SumMs += ms;
                if (ms > a.MaxMs)
                {
                    a.MaxMs = ms;
                    try { a.MaxAt = DateTime.Now.ToString("HH:mm:ss.fff"); } catch { a.MaxAt = ""; }
                }
                // 汇总节奏与 `End` 一致：低频调用点靠它看不到单次值，所以调用方通常还会自己打一行。
                long now = Stopwatch.GetTimestamp();
                if ((now - _lastReportTs) / (double)Stopwatch.Frequency >= ReportSeconds)
                    Report(now);
            }
            catch { /* 采样器自己绝不抛给主循环 */ }
        }

        /// <summary>结束一次采样（必须与 Begin 严格配对，异常路径也要走到）。</summary>
        internal static void End(string name, long t0)
        {
            if (!Enabled || t0 == 0L) return;
            try
            {
                long now = Stopwatch.GetTimestamp();
                double ms = (now - t0) * 1000.0 / Stopwatch.Frequency;

                Acc a;
                if (!_acc.TryGetValue(name, out a)) { a = new Acc(); _acc[name] = a; }
                a.N++;
                a.SumMs += ms;
                if (ms > a.MaxMs)
                {
                    a.MaxMs = ms;
                    try { a.MaxAt = DateTime.Now.ToString("HH:mm:ss.fff"); } catch { a.MaxAt = ""; }
                }

                if ((now - _lastReportTs) / (double)Stopwatch.Frequency >= ReportSeconds)
                    Report(now);
            }
            catch { /* 采样器自己绝不抛给主循环 */ }
        }

        /// <summary>打一行汇总并清零（只在 <see cref="End"/> 内到点触发，主线程）。</summary>
        private static void Report(long nowTs)
        {
            double span = (nowTs - _lastReportTs) / (double)Stopwatch.Frequency;
            _lastReportTs = nowTs;
            if (span <= 0.01) span = ReportSeconds;

            int frames = -1;
            try
            {
                int fc = Time.frameCount;
                if (_lastFrameCount >= 0) frames = fc - _lastFrameCount;
                _lastFrameCount = fc;
            }
            catch { }

            // GC / 内存（IL2CPP 上可能不支持 CollectionCount —— 取不到就显示 ?）
            string gc = "?", mem = "?";
            try
            {
                int g0 = GC.CollectionCount(0);
                gc = _lastGc0 >= 0 ? "+" + (g0 - _lastGc0) : g0.ToString();
                _lastGc0 = g0;
            }
            catch { }
            try
            {
                long b = GC.GetTotalMemory(false);
                mem = (b / 1048576.0).ToString("0.0") + "MB";
                if (_lastMemBytes >= 0)
                    mem += "(" + ((b - _lastMemBytes) / 1048576.0).ToString("+0.0;-0.0;0") + ")";
                _lastMemBytes = b;
            }
            catch { }

            var sb = new StringBuilder(768);
            sb.Append("[TickProfiler] ").Append(span.ToString("0.0")).Append("s");
            if (frames >= 0) sb.Append(" 帧=+").Append(frames);
            sb.Append(" gc0=").Append(gc).Append(" mem=").Append(mem);

            // 按 max 降序（尖峰优先）
            var rows = new List<KeyValuePair<string, Acc>>(_acc.Count);
            foreach (var kv in _acc) if (kv.Value.N > 0) rows.Add(kv);
            rows.Sort((x, y) => y.Value.MaxMs.CompareTo(x.Value.MaxMs));

            int shown = 0;
            foreach (var kv in rows)
            {
                if (shown++ >= MaxRows) { sb.Append(" | …共").Append(rows.Count).Append("项"); break; }
                var a = kv.Value;
                sb.Append(" | ").Append(kv.Key)
                  .Append(" n=").Append(a.N)
                  .Append(" avg=").Append((a.SumMs / a.N).ToString("0.00")).Append("ms")
                  .Append(" max=").Append(a.MaxMs.ToString("0.00")).Append("ms");
                if (!string.IsNullOrEmpty(a.MaxAt)) sb.Append('@').Append(a.MaxAt);
            }
            string line = sb.ToString();

            // 明细行 + 表头（表头只打一次，便于日志阅读器折叠）
            try { ModMain.P(line); } catch { }

            // 清零，进入下一个窗口
            foreach (var kv in _acc)
            {
                var a = kv.Value;
                a.N = 0; a.SumMs = 0; a.MaxMs = 0; a.MaxAt = null;
            }
        }

        /// <summary>可选：把"报告节奏"表头打一行（Init 时调一次，日志里好认）。</summary>
        internal static void AnnounceOnStart()
        {
            if (!Enabled || _reportLogHeader != null) return;
            _reportLogHeader = "on";
            try
            {
                ModMain.P("[TickProfiler] 采样开始（每 " + ReportSeconds.ToString("0") + "s 一行，按 max 降序；"
                          + "关掉：把 TickProfiler.Enabled 改 false 重新编译）");
            }
            catch { }
        }
    }
}
