/// <summary>
/// DramaNativeClaim —— 「闸 1.5」：原生剧情窗的**归因凭证**。
///
/// 【要解决什么】`DramaAiOption` 要在原生剧情窗上注入「AI 对话」按钮，但有两类窗**不该**注入：
/// 模型刚用工具发起的那批（tool result 已在它上下文里，回喂会复读/语义错位）。
///
/// 【闸 1 为什么不够】`DramaGate.SuppressAiOption` 只在 `ToolExecutor.Execute` 执行期内为真。
/// 而工具发起的动作是**异步延续**的：`Execute` 里只同步弹出第一层，玩家点完之后，**游戏的
/// 回调**才在之后的某一帧弹下一层——那时闸 1 早已落回 false。实证：
///   · 邀约：81002 随 `CreateAction` 同步弹（闸 1 挡得住）→ 玩家点「接受」→ 游戏 lambda
///     `UnitActionRoleInvite.b__11_2` 弹 **81012**（闸 1 够不着）；
///   · 求婚：`ShowConfirm` 弹自建确认窗 → 玩家点「我愿意」→ `onOk` 里 `OpenDrama(22201)`，
///     而 `ShowConfirm` 的 `onOkCb` 是「`onOk()` 之后**紧接着** `DramaGate.Resolve`」——
///     22201 还开着，动作就算结束了。
/// 所以判据不能只看"时间窗"，得给动作发一张**认人**的凭证。
///
/// 【凭证记什么】只记 **目标 NPC 的 unitID**，不记 dramaId —— **认人不认 ID**。
/// 这正是它比"按 dramaId 黑名单"好的地方：既挡得住我方的 22201，又放得过**游戏本体自己**
/// 发起的 1031 双修（黑名单会把后者一起误挡）。
///
/// 【吊销：按动作结束，不是"命中即消费"】用户 09-13 拍板：一个动作可能牵出**多重**剧情
/// （多窗口/多翻页），"命中即消费"会让第二重漏出来。故凭证活到：
///   · `UnitActionPending.Complete/Remove`（原生动作 `OnEnd`，覆盖邀约/传功/讨要/赠予原生路）—— 精确；
///   · `DramaGate` 槽位 `Resolve`（点确定/拒绝、或超时）—— 覆盖 marry / 结拜 / 解除关系 / 赠予自制窗；
///   · 或 `ClaimTTL` 到期（兜底：窗开了但玩家一直不动，槽位始终不 Resolve）。
///
/// 【Resolve 那条腿怎么接的】`ShowConfirm` 类工具不走 `UnitActionPending`，只能靠槽位。接法是
/// **订阅 `DramaGate` 已有的公开事件 `OnResolved`**（`DramaGate.cs:42`）——不改它一行。
/// `Claimed()` 拿到 pending 标记后把凭证绑到 `res["__slot__"]`；槽位一 Resolve 就自动吊销。
/// 时序正好：`onOkCb` 里 `onOk()`（marry 在其中同步 `OpenDrama(22201)`）**先**跑，
/// `DramaGate.Resolve` **后**跑 —— 释放凭证时 22201 已被会话接管（见下），翻页照样挡得住。
///
/// 【多翻页怎么办】同一个剧情窗实例翻页会反复 `InitData`（每页都走注入判定）。若只靠凭证，
/// marry 的 22201 第二页就漏了（凭证已随 Resolve 吊销）。所以另记一条**会话指针**：
///   窗的 **native 指针**（`ui.Pointer`，不是托管引用——Unhollower 不保证同一 native 对象
///   每次给同一个代理实例）+ 认领时的 unitID + `SessionTTL`。
/// 命中会话 → 直接算我方，**与凭证生死无关**。指针复用/窗不销毁的兜底就是 `SessionTTL`。
///
/// 【为什么 `ReleaseUnlessPending` 要跳过"已绑槽位"的券】同一 NPC 的第二个工具调用若撞窗失败，
/// 会走到 `ReleaseUnlessPending`。此时表里那张券是**上一个真挂起中**动作的，替它收回就等于
/// 把它的后续窗（如 22201）放出来。判据：券绑了**未 Resolve** 的槽位 = 真挂起中 → 不收回。
///
/// 线程：全部主线程读写（`ToolExecutor.Execute` / `InitData` postfix / `OnEnd` postfix 都是主线程）。
/// </summary>
using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace AgentLoopBridge
{
    internal static class DramaNativeClaim
    {
        /// <summary>凭证有效期（秒）—— 与 `DramaGate` 的 120s 挂起超时同步。</summary>
        private const float ClaimTTL = 120f;

        /// <summary>会话（同一剧情窗实例）认领的兜底有效期（秒）—— 防"窗不销毁被复用"或指针复用。</summary>
        private const float SessionTTL = 120f;

        private sealed class Claim
        {
            public string UnitID;
            public float Deadline;
            /// <summary>绑定的 `DramaGate` 槽位。0 = 未绑（原生动作路径）；>0 = 真挂起中，
            /// 该槽 `Resolve` 即吊销（见 `OnSlotResolved`）。</summary>
            public int Slot;
        }

        private static readonly List<Claim> _claims = new List<Claim>();

        /// <summary>已认领的剧情窗：**native 指针**（不是托管引用——Unhollower 不保证同一 native
        /// 对象每次给出同一代理实例，用 `ReferenceEquals` 会误判）。同一场剧情全部翻页共用它。</summary>
        private static IntPtr _sessionPtr = IntPtr.Zero;
        private static string _sessionUnit = "";
        private static float _sessionDeadline;

        /// <summary>`DramaGate.OnResolved` 是否已订阅（幂等标志）。</summary>
        private static bool _hooked;

        // ------------------------------------------------------------------
        // 工具侧 API
        // ------------------------------------------------------------------

        /// <summary>取单位的 unitID（异常安全；拿不到返回 null）。工具调用处用它喂 ClaimFor。</summary>
        public static string UidOf(WorldUnitBase u)
        {
            try { return u != null ? u.data.unitData.unitID : null; } catch { return null; }
        }

        /// <summary>登记一张凭证：本工具**即将发起**一个要等玩家操作的动作，目标是这个 NPC。
        /// 幂等（同 NPC 重复登记只续期）。工具真挂起失败时应立刻 `Release`（见 ReleaseUnlessPending）。</summary>
        public static void ClaimFor(string unitID)
        {
            if (string.IsNullOrEmpty(unitID)) return;
            float now = Time.unscaledTime;
            Sweep(now);
            for (int i = 0; i < _claims.Count; i++)
            {
                if (_claims[i].UnitID != unitID) continue;
                _claims[i].Deadline = now + ClaimTTL;   // 续期
                return;
            }
            _claims.Add(new Claim { UnitID = unitID, Deadline = now + ClaimTTL });
        }

        /// <summary>吊销该 NPC 的凭证（动作真正结束：`OnEnd` / 开窗失败回滚）。</summary>
        public static void Release(string unitID)
        {
            if (string.IsNullOrEmpty(unitID)) return;
            for (int i = _claims.Count - 1; i >= 0; i--)
                if (_claims[i].UnitID == unitID) _claims.RemoveAt(i);
        }

        /// <summary>开窗结果不是 pending 标记（撞窗 busy / 开窗失败）→ 立刻收回凭证，
        /// 免得留一张空券在 `ClaimTTL` 内误挡玩家自己的窗。
        ///
        /// **例外**：表里那张券若已绑在**未 Resolve** 的槽位上，它属于另一个真挂起中的动作，
        /// 不能替它收回（否则那个动作的后续窗——如 marry 的 22201——会被放出来注入按钮）。</summary>
        public static void ReleaseUnlessPending(JObject res, string unitID)
        {
            if (res != null && res["__pending__"]?.Value<bool>() == true) return;
            if (string.IsNullOrEmpty(unitID)) return;
            for (int i = 0; i < _claims.Count; i++)
                if (_claims[i].UnitID == unitID && _claims[i].Slot != 0) return;
            Release(unitID);
        }

        /// <summary>
        /// 把凭证绑到 `DramaGate` 槽位上（`Claimed()` 在拿到 pending 标记后调用）。
        /// 该槽一 `Resolve`（玩家点确定/拒绝、或 120s 超时）→ 凭证自动吊销。
        ///
        /// 订阅的是 `DramaGate` **已有**的公开事件（`OnResolved`），`DramaGate` 零改动。
        /// 多播安全：`WsClient` 已订阅同一个事件（补发 response），互不影响。
        /// </summary>
        public static void BindSlot(string unitID, int slot)
        {
            if (string.IsNullOrEmpty(unitID) || slot <= 0) return;
            EnsureHooked();
            for (int i = 0; i < _claims.Count; i++)
            {
                if (_claims[i].UnitID != unitID) continue;
                _claims[i].Slot = slot;
                return;
            }
            // 走到这里说明券已被 Sweep 清掉（`ClaimFor` 刚调过，理论上不会）→ 无事可做
        }

        /// <summary>槽位结束 → 吊销绑在它上面的凭证。主线程（`onOkCb` / 超时 `Tick`）。</summary>
        private static void OnSlotResolved(int slot, JObject result)
        {
            for (int i = _claims.Count - 1; i >= 0; i--)
            {
                if (_claims[i].Slot != slot) continue;
                try { ModMain.P("[DramaNativeClaim] 槽位结束→吊销凭证 unit=" + _claims[i].UnitID + " slot=" + slot); }
                catch { }
                _claims.RemoveAt(i);
            }
        }

        /// <summary>订阅 `DramaGate.OnResolved`（幂等，只订一次）。</summary>
        private static void EnsureHooked()
        {
            if (_hooked) return;
            _hooked = true;
            try { DramaGate.OnResolved += OnSlotResolved; }
            catch (Exception e)
            {
                _hooked = false;
                try { ModMain.P("[DramaNativeClaim] 订阅 OnResolved 失败（凭证将只靠 TTL）: " + e.Message); } catch { }
            }
        }

        // ------------------------------------------------------------------
        // 认领侧 API（DramaAiOption 调用）
        // ------------------------------------------------------------------

        /// <summary>
        /// 认领判定：true = 这个剧情窗是我方动作的后续 → **不要注入**按钮。
        ///
        /// 判定顺序：
        ///   ① 会话命中：窗 native 指针 + unitID 都相同且在 `SessionTTL` 内 → 同一场剧情的后续翻页；
        ///   ② 凭证命中：该 NPC 有未过期的凭证 → 本场剧情的**首次**命中，顺便开一个会话。
        /// 两侧都不中 → 这是玩家/游戏本体自己的剧情，正常注入。
        /// </summary>
        public static bool TryClaim(string unitID, UIDramaBase ui)
        {
            float now = Time.unscaledTime;
            IntPtr ptr = IntPtr.Zero;
            try { ptr = ui != null ? ui.Pointer : IntPtr.Zero; } catch { }

            // ① 同一场剧情的后续翻页
            if (_sessionPtr != IntPtr.Zero && now < _sessionDeadline)
            {
                if (ptr != IntPtr.Zero && ptr == _sessionPtr && _sessionUnit == (unitID ?? ""))
                    return true;
            }
            else
            {
                _sessionPtr = IntPtr.Zero;   // 超时 → 忘掉旧会话
                _sessionUnit = "";
            }

            // ② 凭证命中（首次）
            Sweep(now);
            if (string.IsNullOrEmpty(unitID)) return false;
            for (int i = 0; i < _claims.Count; i++)
            {
                if (_claims[i].UnitID != unitID) continue;
                _sessionPtr = ptr;
                _sessionUnit = unitID;
                _sessionDeadline = now + SessionTTL;
                return true;
            }
            return false;
        }

        /// <summary>过期凭证清理（每次读写顺手做，不挂常驻定时器）。</summary>
        private static void Sweep(float now)
        {
            for (int i = _claims.Count - 1; i >= 0; i--)
                if (now > _claims[i].Deadline) _claims.RemoveAt(i);
        }
    }
}
