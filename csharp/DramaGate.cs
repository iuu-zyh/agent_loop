/// <summary>
/// DramaGate —— 模态确认窗与 WS 延迟 response 之间的静态中间人。
///
/// 背景：工具弹 UICustomDramaDyn 确认窗后，LLM 回合必须挂起等玩家选择——
/// 但主线程绝不能阻塞（onClick 同为主线程 → 死锁）。正解：
///   ToolExecutor 弹窗后立即返回带 __pending__ 标记的 JObject → WsClient 憋住
///   response（暂存 reqId→slot）→ 玩家点击回调执行真动作 → Resolve(slot, 真实结果)
///   → WsClient 补发 response → Python future 被填上 → LLM 才收口。
/// Python 零改动（ws_channel 的 request 只认 req_id 配对，不在乎等多久）。
///
/// 线程约定：所有公共方法都在 Unity 主线程调用（工具执行/按钮回调/帧回调）。
///
/// 已知限制（有意为之）：
///   - 同一 dramaId 同时只允许一个待确认窗（模态 UI 语义）；撞窗时 TryDefer 返回
///     false，工具返回"已有确认窗待处理"让 LLM 稍后重试。
///   - 过期窗口不主动关闭（关窗 API 不明），但 TryClaim 保证迟到点击不会误执行动作。
/// </summary>
using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;

namespace AgentLoopBridge
{
    internal static class DramaGate
    {
        /// <summary>过期秒数：超时自动 Resolve 成"玩家未响应"，防 LLM 回合永久挂起、防窗位永久占用。</summary>
        private const int ExpireSeconds = 120;

        private sealed class Pending
        {
            public int Slot;
            public int DramaId;
            public DateTime CreatedAt;
        }

        private static readonly object _lock = new object();
        private static readonly Dictionary<int, Pending> _pending = new Dictionary<int, Pending>();
        private static int _slotSeq;
        private static bool _tickRegistered;

        /// <summary>slot → 最终结果（Ok/Fail 形态的 JObject）。WsClient 订阅：反查 reqId 补发 response。</summary>
        public static event Action<int, JObject> OnResolved;

        /// <summary>
        /// 「AI 对话」按钮抑制标志（ToolExecutor.Execute 期间置 true）。
        /// 放本类的原因：ToolExecutor 是 verify 工程链接编译的共享文件，而 DramaAiOption（读方）
        /// 仅存在于主工程——标志存这个两工程都编译的类里，两边都能过编译（UnitLookup CS0103 教训）。
        /// 仅主线程读写。
        /// </summary>
        public static bool SuppressAiOption;

        // 对话窗让位/自动恢复状态机已于 2026-09-09 整体退役：AbChatPanel 迁移到游戏 UI 管理器
        // 体系（g.ui.OpenUI + UIBase 继承，层级由游戏自动排"后开盖先开"），确认窗/论道弹窗
        // 后开自然盖住对话窗，无需手动让位/恢复。

        /// <summary>
        /// 登记一个待确认窗。同 dramaId 已有 pending 时返回 false（撞窗/busy）。
        /// 首次调用时注册帧回调做过期检查（复用 MainThreadDispatcher 的 g.timer.Frame 模式）。
        /// </summary>
        public static bool TryDefer(int dramaId, out int slot)
        {
            EnsureTick();
            lock (_lock)
            {
                foreach (var p in _pending.Values)
                {
                    if (p.DramaId == dramaId) { slot = 0; return false; }
                }
                slot = ++_slotSeq;
                _pending[slot] = new Pending { Slot = slot, DramaId = dramaId, CreatedAt = DateTime.UtcNow };
                return true;
            }
        }

        /// <summary>生成给 WsClient 的 pending 标记（HandleMessage 据此憋住 response）。</summary>
        public static JObject MakePendingMarker(int slot)
        {
            return new JObject { ["__pending__"] = true, ["__slot__"] = slot };
        }

        /// <summary>
        /// 原子领票：玩家点击后先 Claim 再执行动作，防止"超时已 Resolve 但玩家又点了"导致动作迟到误执行。
        /// 领票成功（返回 true）后调用方必须 Resolve(slot, result)。
        /// </summary>
        public static bool TryClaim(int slot)
        {
            lock (_lock)
            {
                return _pending.Remove(slot);
            }
        }

        /// <summary>是否有待确认窗（供 NpcInitiativeMonitor 状态闸）。</summary>
        public static bool HasPending
        {
            get { lock (_lock) return _pending.Count > 0; }
        }

        /// <summary>补发结果（领票成功后调用；也供过期 Tick / 动作完成回调用）。幂等：无人订阅/未知 slot 自然无事发生。
        ///
        /// 修复：**Resolve 同时释放票据**。Resolve 的语义是"结果已定，不再需要等玩家"，
        /// 票据使命即结束。原先只有 TryClaim（玩家点击）与过期 Tick 会 Remove，
        /// 于是所有"不走玩家点击"的路径都会把票据留在表里 →
        /// `HasPending` 假挂起 → 状态闸被多挡到 ExpireSeconds(120s) 过期为止。
        /// 真机实测（邀约）：`yao_yue OnEnd 挂起命中 slot=1` 之后又空了 2 分钟才恢复触发
        /// （Player.log 1651→1978 共 6 次 `状态闸命中（原因：DramaGate.HasPending）`）。
        /// 泄漏点当时有三处：UnitActionPending.Complete（OnEnd）、ToolExecutor 的 AI 行动完成回调、
        /// ShowDramaService 自动确认分支——在 Resolve 里统一释放，比逐个补 TryClaim 更不易再漏。
        /// 语义不变：过期 Tick 仍先 Remove 再 Resolve（超时后玩家迟到点击照样被 TryClaim 拒绝）。</summary>
        public static void Resolve(int slot, JObject result)
        {
            lock (_lock)
            {
                _pending.Remove(slot);
            }
            OnResolved?.Invoke(slot, result);
        }

        /// <summary>窗弹出失败时静默注销（不发事件——WsClient 还没登记过这个 slot）。</summary>
        public static void Cancel(int slot)
        {
            lock (_lock)
            {
                _pending.Remove(slot);
            }
        }

        /// <summary>帧回调：过期未响应 → 自动 Resolve 成超时结果（玩家侧窗口变僵尸但 TryClaim 防误执行）。</summary>
        private static void Tick()
        {
            List<KeyValuePair<int, JObject>> expired = null;
            lock (_lock)
            {
                if (_pending.Count == 0) return;
                DateTime now = DateTime.UtcNow;
                foreach (var kv in _pending)
                {
                    if ((now - kv.Value.CreatedAt).TotalSeconds >= ExpireSeconds)
                    {
                       expired = expired ?? new List<KeyValuePair<int, JObject>>();
                        expired.Add(new KeyValuePair<int, JObject>(kv.Key,
                            new JObject { ["success"] = false, ["error"] = "玩家长时间未响应，确认已超时" }));
                    }
                }
                if (expired != null)
                    foreach (var e in expired) _pending.Remove(e.Key);
            }
            if (expired != null)
                foreach (var e in expired) OnResolved?.Invoke(e.Key, e.Value);
        }

        private static void EnsureTick()
        {
            if (_tickRegistered) return;
            _tickRegistered = true;
            try { g.timer.Frame(new Action(Tick), 1, true); }
            catch { _tickRegistered = false; }
        }
    }
}
