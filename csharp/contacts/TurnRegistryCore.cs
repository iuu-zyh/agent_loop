/// <summary>
/// 回合登记表的**纯逻辑核**（TurnRegistryCore）—— 「这个 NPC 此刻有没有回合在跑」。
///
/// 与 `TurnRegistry.cs` 的分工（照 `UnitIdRegistryCore` / `UnitIdRegistry` 的先例）：
///   · 本文件**零 Unity、零 ModMain 依赖**，时钟与日志都从外面注入 ⇒
///     `csharp/tests/TurnRegistryTests` 能直接链接本源码跑断言（不需要 Unity、不需要游戏）；
///   · `TurnRegistry.cs` 是 Unity 侧的薄壳（注入 `DateTime` + `ModMain.P`）。
///   主工程把两份都编进去（静态门面 + 核），测试工程只链这一份。
///
/// ── 它存在的理由（用户 10-17 拍板"改成单一事实来源"）────────────────────────
/// 「对方正在斟酌…」忙标此前是**谁最后碰它谁说了算**：全工程 15 处 `SetBusy` 各写各的，
/// 判断标准互不相同（玩家动作 / 历史内容 / 超时猜测 / UI 重建 / 群聊阶段）。真机事故：
///
///     点「同意」→ 开窗点亮忙标 → **几百毫秒后历史回放落地** → `ResetContent` 灭掉它
///     → 模型还要跑 15 秒，屏幕上什么都没显示（忙标只活了 2 秒）。
///
/// 根因不是某一行写错，而是**没有单一事实来源** —— `ChatWindow.ResetContent` 把
/// 「内容被重建」当成了「回合已结束」，而这两件事毫无关系。
///
/// ⇒ 本核把"回合在跑"抽成一个**按 NPC 记的显式状态**，边界只有两个（都是协议事实）：
///   **起点** = 玩家发消息 / 自主交互回合真正开始（玩家点了同意）；
///   **终点** = 收口帧 `npc_reply`。UI 只**读**，不再**决定**。
///
/// ── 键为什么是 NPC 名，不是"当前窗口"（用户明确要求）──────────────────────────
/// 同时可能有好几个 NPC 在跑回合（姜萌在答、江宇文也在答）。UI 物理上只能显示一个，
/// 但**状态必须各自留着**，否则切回去就丢了。用户确认的期望流程：
///
///     看 A（A 在跑，忙标 ON）→ 切 B（B 空闲，忙标 OFF）→ 切回 A ⇒ **忙标要恢复成 ON**
///
/// 所以本核按 npc 名存（与 `UnreadStore` 同款键），UI 渲染时查**当前窗口绑定的那个 NPC**。
///
/// ── 线程 ──────────────────────────────────────────────────────────────────
/// 无锁，也不做线程安全承诺：调用方全在 Unity 主线程（`WsClient.UiEvent` 回调、
/// UI 事件、`NpcInitiativeMonitor` 的帧回调）。这一点与 `UnreadStore` 同款约定。
/// </summary>
using System;
using System.Collections.Generic;

namespace AgentLoopBridge
{
    internal sealed class TurnRegistryCore
    {
        /// <summary>npc → 本回合开始时刻（ticks）。</summary>
        private readonly Dictionary<string, long> _since = new Dictionary<string, long>();

        /// <summary>npc → 阶段文案（可空）。回合卡在"等一场战斗"这类长等时，
        /// 忙标该说「战斗进行中…」而不是「对方正在斟酌…」（见 `ChatWindow.SetBusyPhase`）。</summary>
        private readonly Dictionary<string, string> _phase = new Dictionary<string, string>();

        /// <summary>取"现在"的 ticks。测试注入可控时钟 —— TTL 那类逻辑不该靠 sleep 去测。</summary>
        private readonly Func<long> _now;

        /// <summary>日志（可空 = 静默）。只在**状态真变化**时被调用，避免每帧刷屏。</summary>
        private readonly Action<string> _log;

        /// <summary>安全阀：超过这个秒数一律视为"这个回合早就死了"（见 <see cref="TtlSeconds"/> 注释）。</summary>
        private readonly double _ttlSeconds;

        /// <summary>`ActionWatcher.InitiativeTtl` 同量级（900s）。
        ///
        /// 防的是"收口帧永远不到"（Python 崩了 / 帧被吞）：那种情况下本核会把忙标
        /// **永久钉在"在跑"**，而这恰恰是它要根治的病。真实回合最长也就一场战斗 + 工具往返
        /// （分钟级），15 分钟已远超"还活着"的合理解释。
        /// 判据用**开始时刻**而不是"最近一帧"：本核不该知道帧的存在（那是 UI 的事）。</summary>
        public const double TtlSeconds = 900.0;

        public TurnRegistryCore(Func<long> nowTicks, Action<string> log = null, double ttlSeconds = TtlSeconds)
        {
            _now = nowTicks ?? (() => DateTime.UtcNow.Ticks);
            _log = log;
            _ttlSeconds = ttlSeconds > 0 ? ttlSeconds : TtlSeconds;
        }

        /// <summary>登记"这个 NPC 的回合开始了"。**返回是否发生了状态变化**（false = 本来就已登记）。
        /// 可重复调用：重复 = 刷新开始时刻（玩家连发两条消息、多步工具往返都算同一回合）。</summary>
        public bool Begin(string npc)
        {
            if (string.IsNullOrEmpty(npc)) return false;
            bool was = _since.ContainsKey(npc);
            _since[npc] = _now();
            return !was;
        }

        /// <summary>登记"这个 NPC 的回合结束了"（收口帧到达）。**返回是否真的清掉了**（false = 本来就没登记）。
        /// 幂等：未知/已清的名字同样合法（失败兜底帧、重复收口都会走到）。
        /// ★ 调用点在 `ContactDuty`，不在 `ChatPresenter` —— 理由见 `TurnRegistry.cs` 文件头"陷阱"一节。★</summary>
        public bool End(string npc)
        {
            if (string.IsNullOrEmpty(npc)) return false;
            _phase.Remove(npc);
            return _since.Remove(npc);
        }

        /// <summary>这个 NPC 此刻有没有回合在跑（UI 忙标的**唯一**判据）。
        /// 顺带做过期清理 —— 过期即视为没在跑（并**顺手清掉**，免得下次还要重算）。</summary>
        public bool IsRunning(string npc)
        {
            if (string.IsNullOrEmpty(npc)) return false;
            long t;
            if (!_since.TryGetValue(npc, out t)) return false;
            if (_now() - t <= (long)(_ttlSeconds * TimeSpan.TicksPerSecond)) return true;
            _since.Remove(npc);
            _phase.Remove(npc);
            Log("★过期清理★ npc=" + npc + " 已超 " + (int)_ttlSeconds + "s 无收口帧 → 强制复位");
            return false;
        }

        /// <summary>该 NPC 本回合的阶段文案（空 = 用默认「对方正在斟酌…」）。</summary>
        public string PhaseOf(string npc)
        {
            if (string.IsNullOrEmpty(npc)) return "";
            string p;
            return _phase.TryGetValue(npc, out p) && p != null ? p : "";
        }

        /// <summary>写阶段文案（心跳帧 `tool_pending` 带过来的"在等什么"）。空串 = 清掉。</summary>
        public bool SetPhase(string npc, string phase)
        {
            if (string.IsNullOrEmpty(npc)) return false;
            if (string.IsNullOrEmpty(phase)) return _phase.Remove(npc);
            string old;
            if (_phase.TryGetValue(npc, out old) && old == phase) return false;
            _phase[npc] = phase;
            return true;
        }

        /// <summary>当前登记的回合数（只给日志/自测用）。</summary>
        public int Count { get { return _since.Count; } }

        /// <summary>换存档 / 清场：整表清空，**返回清掉的条目数**（0 = 本来就空）。
        /// 与 `ChatFrameQueue.ClearAll` / `UnitIdRegistry.ResetForWorld` 同一个钩子、同一个理由：
        /// 那些回合属于上一个世界，它们的收口帧永远不会再来。</summary>
        public int ClearAll()
        {
            int n = _since.Count;
            _since.Clear();
            _phase.Clear();
            return n;
        }

        private void Log(string msg)
        {
            if (_log == null) return;
            try { _log(msg); } catch { }
        }
    }
}
