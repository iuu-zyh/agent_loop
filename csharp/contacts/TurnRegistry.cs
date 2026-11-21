/// <summary>
/// 回合登记表（TurnRegistry）—— **「这个 NPC 现在有没有一个回合在跑」的唯一事实来源**。
/// 本文件是 Unity 侧的**薄壳**：注入真实时钟与日志，逻辑全在 `TurnRegistryCore`（纯逻辑、可单测）。
/// 完整动机、键为什么是 NPC 名、以及下面那个陷阱，见 `TurnRegistryCore.cs` 的文件头。
///
/// ── ★ 陷阱：`End` 必须挂在"能看到所有帧"的那一层 ★ ────────────────────────────
/// `ChatPresenter.OnUiEvent` 有一条 npc 准入过滤：
///
///     if (npcId != _window.CurrentNpcId) { DiagDrop(...); return; }   // ← A 的收口帧在此丢弃
///
/// 一旦玩家把窗口切到 B，**A 的 `npc_reply` 根本进不了 `ChatPresenter`**（真机日志实锤：
/// `★帧被丢弃(npc不匹配 payload=姜萌 面板=江宇文) event=npc_reply npc=姜萌`）。
/// 若把 `End` 写在 `ChatPresenter.OnReplyEvent` 里，A 会**永远停在"在跑"** ——
/// 切回去看到永久忙标。
///
/// 所以 `End` 挂在 `ContactDuty.OnUiEvent`（在一切早退之前无条件看到每一帧 `npc_reply`，
/// 且是常驻职责、与面板实例解耦）。**改动这一条之前请先读那段注释。**
///
/// ── 与 `ChatFrameQueue` 的分工（别把两者混起来）────────────────────────────────
/// 队列的"未闭合段"看起来也像"回合在跑"，但**它答不了首 token 之前那段空窗**：
/// 段是被第一条 `step`/`text_delta` **懒开**的，而模型 ttft 有 4~15 秒 —— 那段正是
/// 玩家最需要看到「正在斟酌」的时候，队列里却一条帧都没有。故必须**显式** `Begin`，
/// 不能拿段的非空当判据。
/// </summary>
using System;

namespace AgentLoopBridge
{
    internal static class TurnRegistry
    {
        private static readonly TurnRegistryCore _core =
            new TurnRegistryCore(() => DateTime.UtcNow.Ticks, Log);

        private static void Log(string msg)
        {
            try { ModMain.P("[TurnRegistry] " + msg); } catch { }
        }

        /// <summary>登记"这个 NPC 的回合开始了"（起点 = 玩家发消息 / 自主交互玩家点了同意）。</summary>
        internal static void Begin(string npc, string why = "")
        {
            if (_core.Begin(npc))
                Log("回合开始 npc=" + npc + (why.Length > 0 ? " ← " + why : ""));
        }

        /// <summary>登记"这个 NPC 的回合结束了"（终点 = 收口帧 `npc_reply`）。
        /// ★ 调用点在 `ContactDuty`，理由见本文件头的"陷阱"一节。★</summary>
        internal static void End(string npc, string why = "")
        {
            if (_core.End(npc))
                Log("回合结束 npc=" + npc + (why.Length > 0 ? " ← " + why : ""));
        }

        /// <summary>这个 NPC 此刻有没有回合在跑（UI 忙标的**唯一**判据）。</summary>
        internal static bool IsRunning(string npc) { return _core.IsRunning(npc); }

        /// <summary>该 NPC 本回合的阶段文案（空 = 用默认「对方正在斟酌…」）。</summary>
        internal static string PhaseOf(string npc) { return _core.PhaseOf(npc); }

        /// <summary>写阶段文案（心跳帧 `tool_pending` 带过来的"在等什么"）。空串 = 清掉。</summary>
        internal static void SetPhase(string npc, string phase)
        {
            if (_core.SetPhase(npc, phase))
                Log("阶段 npc=" + npc + " phase=" + (phase ?? ""));
        }

        /// <summary>当前登记的回合数（只给日志/自测用）。</summary>
        internal static int Count { get { return _core.Count; } }

        /// <summary>换存档 / 清场（与 `ChatFrameQueue.ClearAll` / `UnitIdRegistry.ResetForWorld`
        /// 同一个钩子、同一个理由：那些回合属于上一个世界，它们的收口帧永远不会再来）。</summary>
        internal static void ClearAll(string why = "")
        {
            int n = _core.ClearAll();
            if (n > 0) Log("全部清空（" + n + " 个）" + (why.Length > 0 ? " ← " + why : ""));
        }
    }
}
