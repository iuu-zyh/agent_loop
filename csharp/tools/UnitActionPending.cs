/// <summary>
/// UnitActionPending —— 原生 UnitAction 完成挂起登记表（赠送/讨要/邀约/传功）。
///
/// 背景（用户纠偏 + 真机截图实证）：NPC 赠送道具会弹原生剧情窗
/// "…准备赠于你，你需要此物吗？"（选项：我收下了 / 我不需要这个）；讨要/邀约/传功
/// 同样有"同意/拒绝"剧情（Askfor 三件套字段、Invite.forceAgree 旁路、TeachSkill.PlayAskDrama
/// 反编实锤）。工具不能立即返回"已发起"，必须等玩家选择后按真实结局返回。
///
/// 机制：ToolExecutor 发动作前把「实例指针 → slot + 结果组装委托」登记到本表 →
/// Harmony postfix（UnitActionHooks，仅主工程）在原生 OnEnd() 后查表命中 →
/// 执行 BuildResult 读结局字段（receive/refuseProps、isAskforComplete、gainSkill…）
/// → DramaGate.Resolve(slot, 结果) → WsClient 补发 response → LLM 才收口。
///
/// 键 = il2cpp 实例指针（IntPtr）：动作存续期间游戏自身动作列表持有强引用，
/// postfix 内命中即移除条目，指针复用窗口可忽略。OnEnd 永不触发的兜底 =
/// DramaGate 120s 超时自动 Resolve"玩家长时间未响应"。
///
/// 两工程都编译（verify 无 0Harmony，Harmony 钩子单独放 UnitActionHooks.cs）。
/// 线程约定：Register/Remove/Complete 全在 Unity 主线程调用（工具执行/postfix）。
/// </summary>
using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;

namespace AgentLoopBridge
{
    internal static class UnitActionPending
    {
        internal sealed class Entry
        {
            public int Slot;
            public string Op;
            public string UnitID;               // 闸 1.5：本动作目标 NPC —— 动作结束时据此吊销归因凭证
            public Func<JObject> BuildResult;   // 主线程（postfix）内执行，返回 Ok/Fail 形态
        }

        private static readonly object _lock = new object();
        private static readonly Dictionary<IntPtr, Entry> _pending = new Dictionary<IntPtr, Entry>();

        public static void Register(IntPtr key, int slot, string op, Func<JObject> buildResult)
        {
            Register(key, slot, op, null, buildResult);
        }

        /// <summary>`unitID` = 本动作目标 NPC（可空）。登记时同步向 `DramaNativeClaim` 发一张归因
        /// 凭证，动作结束（Complete/Remove）时吊销 —— 使「Execute 返回之后才弹的原生剧情窗」
        /// 仍能被认出是我方动作的后续（闸 1.5，机理见 DramaNativeClaim 头注释）。</summary>
        public static void Register(IntPtr key, int slot, string op, string unitID, Func<JObject> buildResult)
        {
            lock (_lock) { _pending[key] = new Entry { Slot = slot, Op = op, UnitID = unitID, BuildResult = buildResult }; }
            // op 一起带进凭证：认领页的"按钮/不投"政策按它查表（见 DramaNativeClaim.ContentViaResultOf）。
            // `newChain: true` = 这是一条新链的开始 ⇒ 区间锚点重置到此刻（旧凭证若还挂着，
            // 不重置会把上一场的台词读进这次结果）。
            DramaNativeClaim.ClaimFor(unitID, op, true);
        }

        public static void Remove(IntPtr key)
        {
            Entry e;
            lock (_lock)
            {
                if (!_pending.TryGetValue(key, out e)) return;
                _pending.Remove(key);
            }
            DramaNativeClaim.Release(e.UnitID);   // 动作被撤销 → 凭证一并收回
        }

        /// <summary>把条目摘走但**保留归因凭证**（凭证此后由摘走方负责吊销）。
        /// 切磋应战路径用它把登记交给 `BattleOutcome`：留在表里，进战斗那一刻的 OnEnd 就会抢先收口
        /// （真机 Player.log 4896→4899：`IntoBattle` → `UnitActionRoleDrill:OnEnd()` → `OnEnd 挂起命中`）。</summary>
        public static Entry Take(IntPtr key)
        {
            lock (_lock)
            {
                Entry e;
                if (!_pending.TryGetValue(key, out e)) return null;
                _pending.Remove(key);
                return e;
            }
        }

        /// <summary>Harmony postfix 调用：登记表命中（本 mod 挂起的动作）→ 组结果并 Resolve；未命中（原生自身动作）零打扰。</summary>
        public static void Complete(IntPtr key)
        {
            Entry e;
            lock (_lock)
            {
                if (!_pending.TryGetValue(key, out e)) return;
                _pending.Remove(key);
            }
            // 动作真的走完了（原生 OnEnd）→ 吊销归因凭证：该动作牵出的剧情此时已全部弹完，
            // 之后同 NPC 的剧情窗（玩家自己开的、或游戏本体的）照常注入。
            // （切磋的应战路径到不了这里：登记已被 `DrillChoiceGate` 在 ClickOption **prefix** 里
            //   `Take` 走，紧接着进战斗那一刻的 OnEnd 查表落空 —— 收口与吊销都归 `BattleOutcome`。）
            DramaNativeClaim.Release(e.UnitID);
            JObject result;
            try { result = e.BuildResult != null ? e.BuildResult() : null; }
            catch (Exception ex)
            {
                try { UnityEngine.Debug.Log("[UnitAction] " + e.Op + " 结果组装异常: " + ex.Message); } catch { }
                result = new JObject { ["success"] = false, ["error"] = e.Op + " 已完成但结果读取失败: " + ex.Message };
            }
            try { UnityEngine.Debug.Log("[UnitAction] " + e.Op + " OnEnd 挂起命中 slot=" + e.Slot); } catch { }
            DramaGate.Resolve(e.Slot, result);
        }
    }
}
