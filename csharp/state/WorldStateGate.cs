/// <summary>
/// WorldStateGate —— 状态判据的**接线层**（10-25）。
///
/// 三层分工，别越界：
///   ① `WorldStateFacts` —— 只读世界电平（唯一碰 `g.world` / `g.ui` 的地方，零判断）；
///   ② `WorldStateCore`  —— 纯逻辑，把快照投影成四个视角（零 Unity，有单测）；
///   ③ **本文件**        —— 组装快照 + 注入剧情标记 + 对外给四个入口。
///
/// ## 为什么"剧情标记"要注入，而不是在这里直接读 `DramaGate`
/// `state/` 是**状态**模块，`drama/` 是**剧情**业务。让状态去 import 剧情就成环
/// （drama 的台账/分段器都要问状态），而环在这类 mod 里的表现是"初始化顺序敏感、
/// 偶发不生效"——最难查的那种。所以这里只持有两个 `Func<bool>`，由 `ModMain.Init`
/// 注入（与 `DeferredActionQueue.WaitBlocker` 的既有套路完全一致）。
///
/// **未注入时一律当 false**，这与 `DramaGate` 那两个位的空态**恰好等价**
/// （没有挂起动作 / 没有收尾占位）⇒ 行为与改造前逐字一致，不会静默变严或变松。
/// 注入这件事由 `tests/test_state_module_boundary.py` 与
/// `tests/test_state_gate_wiring.py` 一起盯住（漏注入会让"我方挂起动作"这一条永远不成立）。
///
/// ## 四个入口分别给谁
/// | 入口 | 调用方 | 语义 |
/// |---|---|---|
/// | `SpeechBlockReason` | 本仓主动互动的日节拍/派发（经 `NpcInitiativeMonitor` 转发）| 能不能开口 |
/// | `BusyReason` | `ContactDuty` / `ActionWatcher` / `NoticePanel` / `SendInitiative` 的 busy 帧 | 能不能发起动作 |
/// | `YieldReason` | 动作排队器（让路）| 弹原生窗会不会抢界面 |
/// | `ExecuteBlocker` | 动作排队器（能不能落刀）| 游戏能不能接住这个原生窗 |
///
/// ## 快照是"全读"而不是"懒读"
/// 旧实现是短路求值（命中第一条就返回），本实现一次读完 12 个字段。代价是
/// **每次调用多读几个 GetUI**（量级：几十微秒）。可以接受，因为这三个入口都只在
/// **粗粒度时刻**被调（派发一次 / 排队器 0.25s 采样一次），不是每帧；
/// 而每帧的那几个（`DramaWindowReason` / `BattlePreShowing` / `ActionShowing`）
/// **刻意留在 `WorldStateFacts` 里直读**，没走快照 —— 那是 10-21 卡顿事故的敏感面，
/// 不要让它们多读任何一个位。
/// </summary>
using System;

namespace AgentLoopBridge
{
    internal static class WorldStateGate
    {
        /// <summary>剧情链收尾占位（`DramaGate.ChainHold`）。未注入 = false（等价于没有收尾）。</summary>
        internal static Func<bool> DramaChainHold;

        /// <summary>我方挂起动作（`DramaGate.HasPending`）。未注入 = false（等价于没有挂起）。</summary>
        internal static Func<bool> DramaPendingAction;

        // 两个委托各记一个"已吼过"标志：共用一个的话，第一个缺失会把第二个的警告也吃掉
        // （结果是"HasPending 未注入"这件事永远不出现在日志里，而它恰好是"我方挂起动作"那一条）。
        private static bool _warnedNoChainHold;
        private static bool _warnedNoPending;

        /// <summary>接线（`ModMain.Init` 调一次）。只记两个委托，不碰任何游戏对象。</summary>
        internal static void Attach(Func<bool> chainHold, Func<bool> pendingAction)
        {
            DramaChainHold = chainHold;
            DramaPendingAction = pendingAction;
            _warnedNoChainHold = false;
            _warnedNoPending = false;
        }

        /// <summary>接线自检（启动日志用；返回一行摘要，让"没接上"不可能被静默忽略）。</summary>
        internal static string InstallReport()
        {
            return "chainHold=" + (DramaChainHold != null ? "已注入" : "★未注入★")
                 + ";pending=" + (DramaPendingAction != null ? "已注入" : "★未注入★");
        }

        // ==================================================================
        // 快照组装
        // ==================================================================

        /// <summary>
        /// 读一次世界 + 注入两个剧情标记 → 快照。
        /// 每个字段独立吞异常（`Facts` 里已经各自兜住了），这里不再重复 try。
        /// </summary>
        internal static WorldStateSnapshot Snapshot()
        {
            var sn = new WorldStateSnapshot
            {
                PlayerUnit = WorldStateFacts.ReadPlayerUnit(),
                DeathWindowVisible = WorldStateFacts.DeathWindowVisible(),
                IsBattleLevel = WorldStateFacts.IsBattleLevel(),
                BattleUiVisible = WorldStateFacts.BattleUiVisible(),
                OwnDramaWindowVisible = WorldStateFacts.OwnDramaWindowVisible(),
                UiLayerTopName = WorldStateFacts.UiLayerTopName(),
                ChatWindowVisible = WorldStateFacts.ChatWindowVisible(),
                MaskLayerName = WorldStateFacts.MaskLayerName(),
                WindowLayerName = WorldStateFacts.WindowLayerName(),
                MainWorldReady = WorldStateFacts.MainWorldReady(),
                DramaChainHold = ReadInjected(DramaChainHold, "ChainHold", ref _warnedNoChainHold),
                DramaPendingAction = ReadInjected(DramaPendingAction, "HasPending", ref _warnedNoPending),
            };
            return sn;
        }

        /// <summary>读注入的委托。**取不到一律当 false**（= `DramaGate` 的空态，行为同改造前）。
        /// 只在第一次缺注入时吼一行 —— 每帧吼会把日志淹掉，而不吼就等于静默退化。</summary>
        private static bool ReadInjected(Func<bool> f, string what, ref bool warned)
        {
            if (f == null)
            {
                if (!warned)
                {
                    warned = true;
                    try
                    {
                        ModMain.P("[WorldState] ⚠ " + what + " 未注入 → 该条判据恒 false"
                                  + "（若见本行，查 ModMain 里 WorldStateGate.Attach 是否漏调）");
                    }
                    catch { }
                }
                return false;
            }
            try { return f(); } catch { return false; }
        }

        // ==================================================================
        // 四个入口（形状与旧实现逐字一致，调用方一行都不用改）
        // ==================================================================

        /// <summary>能不能开口（非空 = 本次主动互动触发丢弃）。</summary>
        internal static string SpeechBlockReason()
        {
            var sn = Snapshot();
            return WorldStateCore.SpeechBlock(ref sn);
        }

        /// <summary>能不能发起动作（非空 = 玩家忙，本回合只许言语 + 只读）。</summary>
        internal static string BusyReason()
        {
            var sn = Snapshot();
            return WorldStateCore.BusyReason(ref sn);
        }

        /// <summary>`BusyReason` 的人话（`ContactDuty` 收尾分流日志用）。</summary>
        internal static string BusyReasonText()
        {
            return BusyReason();
        }

        /// <summary>别弹窗打扰（能说闸 ∪ 能做动作闸）。</summary>
        internal static bool IsPlayerBusy()
        {
            var sn = Snapshot();
            return WorldStateCore.IsPlayerBusy(ref sn);
        }

        /// <summary>排队器要不要让路（非空 = 让路）。</summary>
        internal static string YieldReason()
        {
            var sn = Snapshot();
            return WorldStateCore.YieldReason(ref sn);
        }

        /// <summary>排队器能不能落刀（非空 = 还不能执行）。</summary>
        internal static string ExecuteBlocker()
        {
            var sn = Snapshot();
            return WorldStateCore.ExecuteBlocker(ref sn);
        }

        /// <summary>四视角并排的一行诊断（排障日志用）。</summary>
        internal static string Describe()
        {
            var sn = Snapshot();
            return WorldStateCore.Describe(ref sn);
        }
    }
}
