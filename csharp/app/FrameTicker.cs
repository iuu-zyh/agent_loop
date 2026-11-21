/// <summary>
/// FrameTicker —— 常驻帧回调的**句柄表**（11-19 重做，替换原来那个 `_tickRegistered` 布尔）。
///
/// 【它修的是什么】
/// 真机 11-19 01:09：NPC 传功被排进「此间事了」队列，随后队列再也不执行 —— 玩家一直催，
/// 模型一直按「已受理、等待中」答复。根因不是排队逻辑，是**驱动队列的那条每帧回调已经没了**：
/// `g.timer.Frame(...)` 的登记在世界重载（回主菜单 / 读档 / 换档）时会被清掉，
/// 而我们原来用 `_tickRegistered` 这个**全进程一次**的布尔去防重复登记 ⇒ 重载后仍然"记得挂过"
/// ⇒ 再也不会重新挂 ⇒ 从那一刻起整条动作通道静默瘫痪（队列非空 ⇒ 后续动作全被排进同一队列）。
///
/// 【为什么布尔必然错，句柄才对】
/// 布尔只能回答"我有没有调用过登记"，答不了"那条登记现在还在吗"。而 `g.timer.Frame` 是**有返回值**的
/// （`TimerCoroutine`），官方模组模板与参考实现都存它、在 `Destroy` 里 `g.timer.Stop(cor)` 撤掉。
/// 我们历史上十几处全部"调用完就扔"，才被迫用布尔代替 —— 替身不会跟着本体一起失效，于是重载即翻车。
///
/// 【本类的三层保证】
///   ① **至多一份**：回调入口先问 <see cref="Once"/>（本帧已跑过就退）。即便真被登记了两份，
///      也只有一份真正干活 —— 这是当年 F9/F10/F11「同一次按键触发两次开关、开→关自己抵消」的防线。
///   ② **至少一份**：<see cref="Ensure"/> 不只看句柄在不在，还看它**最近有没有真的跑起来**
///      （`_lastRun`）。句柄在、但三十帧没动静 ⇒ 判定停摆，撤旧挂新并留一行日志。
///      于是任何"用到它"的路径（入队 / 弹确认窗 / Init）都能顺手把驱动救活。
///   ③ **主动收摊**：<see cref="StopAll"/> 在 `ModMain.Destroy` 里无条件调用 —— 不再指望
///      "帧回调随场景销毁"。官方模板的 `Destroy` 正是 `g.timer.Stop(corUpdate)` 一句。
///
/// 【为什么用帧号而不是秒】
/// 要回答的是"每帧都跑吗"，帧号天然对齐。而且**游戏暂停 / 加载屏时帧号不前进**，
/// 玩家在加载屏待半分钟不会被误判成"心跳死亡"；用秒数就会误判并反复重挂。
/// </summary>
using System;
using System.Collections.Generic;
using UnityEngine;

namespace AgentLoopBridge
{
    internal static class FrameTicker
    {
        // ---------- 键（一个键 = 一条常驻帧回调；新增持久 tick 时在这里登记） ----------
        internal const int KDeferQ = 1;          // DeferredActionQueue（「此间事了」队列）
        internal const int KBattleFlow = 2;      // BattleFlowGate（战前 / 战中两态采样）
        internal const int KDramaGate = 3;       // DramaGate（槽位过期 / 心跳检查）
        internal const int KBattleOutcome = 4;   // BattleOutcome（战斗收口轮询）
        internal const int KSegRec = 5;          // DramaSegmentRecorder（剧情段采样）
        internal const int KSegProbe = 6;        // DramaSegmentProbe（处置页探针）
        internal const int KTeamJoin = 7;        // TeamJoinGate（决斗/寻仇「要不要带队友一起上」采样）
        internal const int KBusyWatch = 8;       // BusyWatch（「玩家忙 / 战斗中」判据的现场记录）

        // ---- 11-19 全仓守卫扫描补入：原本是 `ModMain.Init` 里的裸注册（返回值丢弃） ----
        //    裸注册 = 每进一次世界多一份、上一份还活着、`Destroy` 一条都撤不掉。
        //    真机日志量到：它们从第 3 段世界起恒为 2.00 次/帧（`deferq` 恒为 1.00）。
        internal const int KDispatcher = 9;      // MainThreadDispatcher（主线程任务队列）
        internal const int KNpcMonitor = 10;     // NpcInitiativeMonitor（日节拍兜底）
        internal const int KBrainLink = 11;      // BrainLink（Python 存活监测）
        internal const int KHotkeys = 12;        // AbHotkeys（热键 + 面板状态机 + 读盘泵）
        internal const int KDiag = 13;           // DiagSwitches（运行期诊断开关复查）
        internal const int KProber = 14;         // AbPanelProber（进世界诊断哨兵）
        internal const int KMapBtn = 15;         // MapMainContactButton（「传」钮未读角标）
        internal const int KBanner = 16;         // UnreadBanner（未读横幅自动隐藏）
        /// <summary>一次性：延后约 1s 打第二次「在册情况」（让 idle 有真数值）。打完自己撤掉。</summary>
        internal const int KDiagReport = 17;
        /// <summary>NpcDuel 的诊断节拍（读 `_diag_npc_duel*.txt`；内部自带 120 帧节流）。</summary>
        internal const int KNpcDuelDiag = 18;
        /// <summary>UiKit 的**建树自检**（延迟 2 帧等布局结算；有活才挂、干完自己撤）。
        ///
        /// ★11-20 收编★ 它原先叫 `UiKit.AuditKey`、值 19、**定义在 `UiKit.cs` 里**，
        /// 注释写"与 `app/FrameTicker.cs` 的常量段并列"—— 并列是并列了，但号段因此分裂成两处。
        ///
        /// 为什么必须收进来：本类五张表全以键为索引，而 `Attach` 里是 `_cor[k] = c`、
        /// `Once` 按 `_lastRun[k]` 去重。两个组件撞号会**静默**出三件事：
        ///   ① `_name[k]` 被覆盖 ⇒ 日志显示 A 的名字、跑的可能是 B；
        ///   ② `_cor[k]` 被覆盖 ⇒ 先挂那条仍是幽灵（游戏每帧调它、我们永无句柄、
        ///      `StopAll` 停不掉、闭包对象永不释放）；
        ///   ③ 后注册那条**永远不执行**（`Once` 见"本帧已跑过"直接退）。
        /// 而日志里 `idle=0` 照旧，看不出异常 —— 本仓已因同形状栽过三次
        /// （`_tickRegistered` / `_ticking` / `_installed`），所以号段必须集中一处。
        ///
        /// 唯一性由 `tests/test_resident_tick_contract.py` 的**全仓扫描**守（不只扫本文件）。</summary>
        internal const int KUIAudit = 19;

        /// <summary>气泡宽度的**延迟重测**（11-20 修「文字没到上限却撑满上限」。
        /// 新建气泡那一帧 `Text.preferredWidth` 读到 0 = 布局未结算，而气泡只有一次测量机会，
        /// 旧代码"量不出就按上限给"把那一帧的读 0 永久固化成满宽。详见 `BubbleWidthPump`）。</summary>
        internal const int KBubbleWidth = 20;

        /// <summary>历史回放的**分帧构建**（11-20 修「开窗卡住 1.1 秒」）。
        ///
        /// 575 行塞进一帧 ⇒ `[MFrame][FrameStall] ms=770`，期间连 ESC 都不响应。
        /// 改成每帧建一批、建完自己撤。判据在 `HistoryBuildCore`，
        /// 接线在 `ChatWindow.BuildHistoryBatch`（`ReplaceHistory` 超过门槛时启用）。</summary>
        internal const int KHistoryBuild = 21;

        // 判定用的宽限在 `FrameTickerCore.AliveGraceFrames`（判据归核、接线归层，见两者头注释）。

        private static readonly Dictionary<int, TimerCoroutine> _cor = new Dictionary<int, TimerCoroutine>();
        private static readonly Dictionary<int, int> _lastRun = new Dictionary<int, int>();
        private static readonly Dictionary<int, int> _attachedAt = new Dictionary<int, int>();
        private static readonly Dictionary<int, string> _name = new Dictionary<int, string>();
        /// <summary>该键当前挂着的回调**绑在哪个对象上**（`Ensure` 的 owner 参数）。见 `Ensure` 第 ③ 条。</summary>
        private static readonly Dictionary<int, object> _owner = new Dictionary<int, object>();

        /// <summary>当前在册的条数（`Destroy` 后应为 0；`Init` 之后应等于已接线的键数）。</summary>
        internal static int Count { get { return _cor.Count; } }

        /// <summary>
        /// 每帧去重。**回调体的第一句必须调它**，返回 false 表示"本帧已经有一份跑过了，直接退"。
        ///
        /// 它同时承担第二个职责：把 `_lastRun[key]` 更新成当前帧 —— 那是
        /// <see cref="Ensure"/> 判断"还活着吗"的唯一依据。两个职责共用一次调用，不额外记账。
        /// </summary>
        internal static bool Once(int key)
        {
            int f = SafeFrame();
            int last;
            if (!_lastRun.TryGetValue(key, out last)) last = -1;
            if (!FrameTickerCore.ShouldRunThisFrame(f, last)) return false;
            if (f >= 0) _lastRun[key] = f;
            return true;
        }

        /// <summary>
        /// 确保这条常驻回调在跑。**幂等、可高频调用**（入队 / 弹确认窗路径每秒可能几十上百次）。
        ///
        /// 判断顺序（每一步都有理由）：
        ///   ① 没有句柄 ⇒ 直接挂（首次 / `Destroy` 之后）；
        ///   ② 该键的回调**换了一个宿主对象** ⇒ 撤旧挂新（见下面第 ③ 条）；
        ///   ③ 有句柄且最近跑过 ⇒ 什么都不做（常态，零成本）；
        ///   ④ 有句柄但三十帧没动静 ⇒ **撤旧挂新**。这一步就是"重载之后没人重挂"的解药：
        ///      重载把游戏那边的登记清掉了，句柄还在（我们没 Stop 过），于是老实现会一直以为"挂过了"；
        ///      现在发现它不跳就换一条，不依赖"重载时我一定会被叫到"这个假设。
        /// </summary>
        /// <param name="key">键（见常量区）。同一键同一时刻至多一份在册。</param>
        /// <param name="name">日志名（`Describe()` 与停摆日志用）。</param>
        /// <param name="body">每帧要跑的委托（已由调用方绑好实例）。</param>
        /// <param name="owner">
        /// 可选：这条回调**依赖的那个对象**。不传 = 不判。
        ///
        /// ★为什么必须有这个参数（11-19 全仓扫描发现，差点踩进去）★
        /// 有些常驻回调是"实例方法"：`ModMain.Init` 每次进世界都 `new MainThreadDispatcher()`、
        /// `new NpcInitiativeMonitor(_wsClient)`。若只按句柄判活，`Init` 重跑时会看到
        /// "句柄在、还活着"⇒ 什么都不做 ⇒ **帧回调永远跑在被 `Destroy` 里 `Dispose()`
        /// 掉的那个旧实例上**（`_dispatcher.OnUpdate` 第一句就是 `if (_disposed) return`），
        /// 而新实例一次都不会被调用。
        ///
        /// 后果比"少跑点东西"严重得多：主线程调度器停摆 = **Python 的每一次工具调用都超时**
        /// （它们全部经 `_dispatcher.Enqueue` 派到主线程）。而 `Ensure` 的日志会显示"一切正常"。
        /// 这正是本仓最忌的形态：坏了、且日志说没事。
        ///
        /// 判据是引用相等（`ReferenceEquals`）：同一个实例重复 `Init` 不重挂（幂等），
        /// 换了实例才重挂。静态方法传 `null`（或不传）。
        /// </param>
        internal static void Ensure(int key, string name, Action body, object owner = null)
        {
            if (body == null) return;
            _name[key] = name;
            int f = SafeFrame();

            TimerCoroutine old;
            bool has = _cor.TryGetValue(key, out old) && old != null;

            // ② 宿主换了 ⇒ 旧那份跑的已经是死对象，直接撤（这一条优先于 Decide 的"活着"结论）
            if (has && RebindNeeded(key, owner))
            {
                try { g.timer.Stop(old); } catch { }
                has = false;
            }
            if (!has)
            {
                _cor.Remove(key);
                _lastRun.Remove(key);
                _attachedAt.Remove(key);
                _owner.Remove(key);
            }

            int lastRun, attachedAt;
            if (!_lastRun.TryGetValue(key, out lastRun)) lastRun = -1;
            if (!_attachedAt.TryGetValue(key, out attachedAt)) attachedAt = -1;

            switch (FrameTickerCore.Decide(has, f, lastRun, attachedAt))
            {
                case FrameTickVerdict.Alive:
                case FrameTickVerdict.Unknown:      // 读不到帧号 ⇒ 不重挂（见核头注释）
                    return;

                case FrameTickVerdict.Revive:
                    int idle = FrameTickerCore.IdleFrames(f, lastRun);
                    try { g.timer.Stop(old); } catch { }
                    _cor.Remove(key);
                    _lastRun.Remove(key);
                    _attachedAt.Remove(key);
                    // 只有"确实跑过、然后停了"才是真停摆；`idle < 0`（从没跑过）不打 —— 那是刚挂上。
                    if (idle > FrameTickerCore.AliveGraceFrames)
                    {
                        try
                        {
                            ModMain.P("[FrameTicker] " + name + " 停摆 " + idle + " 帧（≈"
                                      + (idle / 60f).ToString("0.0") + "s）→ 已重挂"
                                      + "（世界重载会清掉帧登记，这里自愈；见 FrameTicker 头注释）");
                        }
                        catch { }
                    }
                    break;
            }

            Attach(key, body, f, owner);
        }

        /// <summary>该键当前挂着的回调是否绑在**另一个**对象上（`owner` 为 null = 不做判断）。</summary>
        private static bool RebindNeeded(int key, object owner)
        {
            if (owner == null) return false;
            object cur;
            if (!_owner.TryGetValue(key, out cur)) return false;   // 老记录没存过 ⇒ 无从判，交给 Decide
            return !ReferenceEquals(cur, owner);
        }

        /// <summary>
        /// 撤掉全部常驻回调。**只在 `ModMain.Destroy`（回主界面）里调**。
        ///
        /// 为什么要我们主动撤，而不是等场景销毁：老实现十几处一条都没 Stop 过，全靠"帧回调随场景销毁"——
        /// 真机日志里 `Destroy ok` 之后体检表还在继续打，说明这个假设压根没验证过。
        /// 官方模板与参考实现都在 `Destroy` 里 `g.timer.Stop(...)`，本类回到同一种写法：
        /// 谁清的、什么时候清的，都在自己手里，于是"句柄在册 = 确实在跑"这句话才成立。
        /// </summary>
        internal static void StopAll(string why)
        {
            if (_cor.Count == 0) return;
            var keys = new List<int>(_cor.Keys);
            int n = 0;
            for (int i = 0; i < keys.Count; i++)
            {
                TimerCoroutine c;
                if (!_cor.TryGetValue(keys[i], out c)) continue;
                try { if (c != null) { g.timer.Stop(c); n++; } } catch { }
            }
            _cor.Clear();
            _lastRun.Clear();
            _attachedAt.Clear();
            _owner.Clear();
            try { ModMain.P("[FrameTicker] 已停止 " + n + " 个常驻帧回调（" + why + "）"); } catch { }
        }

        /// <summary>撤掉**单独一条**常驻回调（`Ensure` 的反操作）。用于一次性 tick 打完后自行收摊。
        /// 不存在时静默返回（可重复调用）。</summary>
        internal static void Stop(int key)
        {
            TimerCoroutine c;
            if (_cor.TryGetValue(key, out c))
            {
                try { if (c != null) g.timer.Stop(c); } catch { }
            }
            _cor.Remove(key);
            _lastRun.Remove(key);
            _attachedAt.Remove(key);
            _owner.Remove(key);
            _name.Remove(key);
        }

        /// <summary>从挂上到现在过了多少帧（-1 = 没挂或读不到帧号）。给"延后打日志"这类用法。
        /// 与 `IdleFrames` 不同：它量的是"挂了多久"，不是"上次跑到现在多久"。</summary>
        internal static int FramesSinceAttach(int key)
        {
            int att;
            if (!_attachedAt.TryGetValue(key, out att)) return -1;
            int f = SafeFrame();
            if (f < 0 || att < 0) return -1;
            return f - att;
        }

        /// <summary>一行诊断（写日志用）：在册的键名与"最近多久没跑"，排障时一眼看出谁哑了。
        ///
        /// `idle` 的三种前缀（判据在 `FrameTickerCore.IdleLabel`，这里只搬结论）：
        ///   · `3`    跑了，3 帧前跑的 —— 正常；
        ///   · `~3`   刚挂上 3 帧，还没轮到第一帧 —— 正常（`Init` 末尾打的必然是这个或 `~0`）；
        ///   · `!90`  挂了 90 帧一次都没跑 —— **真故障**，`g.timer` 那边没在驱动它。
        /// 老实现把后两种都打成 `?`，而 `Init` 恰好只在"刚挂上"那一刻打，于是这一行
        /// 在最需要它的位置永远没有信息。</summary>
        internal static string Describe()
        {
            if (_cor.Count == 0) return "在册 0 条";
            int f = SafeFrame();
            var sb = new System.Text.StringBuilder();
            sb.Append("在册 ").Append(_cor.Count).Append(" 条：");
            bool first = true;
            foreach (var kv in _cor)
            {
                if (!first) sb.Append(" ");
                first = false;
                string nm;
                if (!_name.TryGetValue(kv.Key, out nm)) nm = ("#" + kv.Key);
                int last, att;
                if (!_lastRun.TryGetValue(kv.Key, out last)) last = -1;
                if (!_attachedAt.TryGetValue(kv.Key, out att)) att = -1;
                sb.Append(nm).Append("(idle=").Append(FrameTickerCore.IdleLabel(f, last, att)).Append(")");
            }
            return sb.ToString();
        }

        // ------------------------------------------------------------------ 内部

        private static void Attach(int key, Action body, int f, object owner)
        {
            int k = key;                            // 闭包捕获副本，别捕 key
            Action wrapped = () =>
            {
                if (!Once(k)) return;               // ① 至多一份：本帧已有副本跑过
                try { body(); } catch { }           // 单条回调出错不许拖垮计时器
            };
            try
            {
                TimerCoroutine c = g.timer.Frame(wrapped, 1, true);
                if (c != null)
                {
                    _cor[k] = c;
                    _attachedAt[k] = f;
                    if (owner != null) _owner[k] = owner; else _owner.Remove(k);
                }
            }
            catch
            {
                // 挂不上（`g.timer` 未就绪等）：不写句柄 ⇒ 下次 Ensure 会再试。
                // 这里刻意不抛也不记 —— Init 早期确实可能还没就绪，而"每次失败都打一行"会刷屏。
            }
        }

        /// <summary>`Time.frameCount` 读不到时返回 -1（调用方据此放弃"停摆"判断，而不是误判）。
        /// 加载屏 / 暂停时帧号**不前进**，所以帧号做判据天然免疫"玩家挂着不动"。</summary>
        private static int SafeFrame()
        {
            try { return Time.frameCount; } catch { return -1; }
        }
    }
}
