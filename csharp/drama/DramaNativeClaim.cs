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
        /// <summary>单位 unitID（跨文件调用点多；**保留旧语义：单位为空给 null**，别改成空串）。</summary>
        public static string UidOf(WorldUnitBase u)
        {
            return u == null ? null : UnitIdentity.Id(u);
        }

        /// <summary>凭证有效期（秒）—— 兜底用：正常路径槽位 `Resolve` 时经 `OnResolved` 自动吊销，
        /// 比它长（90s 超时 + 余量），订阅失败时才靠它兜底回收。</summary>
        private const float ClaimTTL = 120f;

        /// <summary>会话（同一剧情窗实例）认领的兜底有效期（秒）—— 防"窗不销毁被复用"或指针复用。</summary>
        private const float SessionTTL = 120f;

        private sealed class Claim
        {
            public string UnitID;
            /// <summary>发起这个动作的**工具 op**（按钮/不投的政策查表用；"" = 关系类自制确认窗那一路，
            /// 维持现状）。09-23：认领不再等于"该静音" —— 内容会不会随工具结果送达，全看这个 op。</summary>
            public string Op = "";
            public float Deadline;
            /// <summary>**首次**登记的时刻 —— 工具结果的"区间读数"锚点就用它：凭证一定早于
            /// `CreateAction`/`ActionStart`，所以区间覆盖得到首窗（如切磋的 21204 提问页、
            /// 论道的 22821 开窗页）。续期（`ClaimFor` 重复调用）**不改**它。</summary>
            public float CreatedAt;
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

        /// <summary>登记一张凭证：本工具**即将发起**一个要等玩家操作的动作，目标是这个 NPC。
        /// 幂等（同 NPC 重复登记只续期）。工具真挂起失败时应立刻 `Release`（见 ReleaseUnlessPending）。</summary>
        public static void ClaimFor(string unitID)
        {
            ClaimFor(unitID, "", false);
        }

        /// <summary>`op` 版：把发起动作的工具名一起记下 —— 认领页的**按钮/不投政策**按它查表
        /// （见 `ContentViaResultOf` / `InjectOnClaimedOf`）。幂等：重复登记续期并**更新** op。</summary>
        public static void ClaimFor(string unitID, string op)
        {
            ClaimFor(unitID, op, false);
        }

        /// <summary>
        /// `newChain` 版（**链开始时用 true**：`UnitActionPending.Register`、`StartToUnitAiActionDeferred`、
        /// `Claimed`）：同 NPC 若还挂着上一场的旧凭证，把**区间锚点**重置到此刻。
        /// 为什么需要：动作工具被 busy 键串行化 ⇒ 新链开始就意味着旧链已经结束；不重置的话
        /// （旧凭证尚未被 Sweep 清掉的 120s 内）区间读数会把**上一场**的台词/选择也读进来。
        /// 周期续期（`BattleOutcome` 每 5s 的 `ClaimFor`）走 false，绝不重置。
        /// </summary>
        public static void ClaimFor(string unitID, string op, bool newChain)
        {
            if (string.IsNullOrEmpty(unitID)) return;
            float now = Time.unscaledTime;
            Sweep(now);
            for (int i = 0; i < _claims.Count; i++)
            {
                if (_claims[i].UnitID != unitID) continue;
                _claims[i].Deadline = now + ClaimTTL;   // 续期
                if (!string.IsNullOrEmpty(op)) _claims[i].Op = op;
                if (newChain) _claims[i].CreatedAt = now;   // 新链：锚点重置（见本方法注释）
                return;
            }
            _claims.Add(new Claim
            {
                UnitID = unitID,
                Op = op ?? "",
                Deadline = now + ClaimTTL,
                CreatedAt = now,      // 区间读数的锚点（首次登记时刻；续期不改）
            });
        }

        /// <summary>
        /// 区间读数的**锚点** = 这条链的凭证首次登记时刻（早于 `CreateAction`/`ActionStart`，
        /// 所以能覆盖首窗）。返回 &lt;0 = 这个 NPC 当前没有凭证 ⇒ 调用方回落自己的锚点。
        /// </summary>
        public static float AnchorOf(string unitID)
        {
            if (string.IsNullOrEmpty(unitID)) return -1f;
            for (int i = 0; i < _claims.Count; i++)
                if (_claims[i].UnitID == unitID) return _claims[i].CreatedAt;
            return -1f;
        }

        // ------------------------------------------------------------------
        // 页面政策（09-23 用户定：按钮注入与"不投"由**内容会不会随工具结果送达**决定，
        // 不再由"是不是认领页"决定）
        // ------------------------------------------------------------------

        /// <summary>
        /// **内容会随工具结果送达**的链 —— 它们的页面正文"照记不投"（内容已当 tool result
        /// 给过模型），认领页也**不注入**「对话」按钮。
        ///   · 论道/双修/切磋/攻击：结果 = 段区间读数（页 + 选项）；
        /// 反例（返回 false：结果是 accepted/invite_text 这类语义字段，原文不在结果里）：
        ///   · 邀约/赠送/讨要/传功 —— 内容只能走段，页正文**可投**。
        /// 未列出的（关系类自制确认窗那一路，op=""）**维持现状**（照记不投、不注入）。
        /// 一张表只写一处：要加减行为改这里。
        /// </summary>
        public static bool ContentViaResultOf(string op)
        {
            switch (op)
            {
                case "lun_dao":
                case "shuang_xiu":
                case "spar":
                case "attack":
                    return true;
                case "yao_yue":
                case "give_item":
                case "ask_for":
                case "chuan_gong":
                    return false;
                default:
                    return true;
            }
        }

        /// <summary>认领页要不要**注入**「对话」按钮。目前只开**场景类交互**（邀约：进场景后
        /// 双方在场的每一页都要能点对话 —— 用户 09-23 明确要求）；短决策窗（赠送/讨要/传功）
        /// 与内容链（论道/双修/切磋/攻击）都不注入（结果已表达语义 / 内容即结果）。
        /// 注：注入与否**不影响记录** —— 页与选项一律入段。</summary>
        public static bool InjectOnClaimedOf(string op)
        {
            return op == "yao_yue";
        }

        /// <summary>该 NPC 当前凭证的 op（查不到返回 ""）。</summary>
        public static string OpOf(string unitID)
        {
            if (string.IsNullOrEmpty(unitID)) return "";
            for (int i = 0; i < _claims.Count; i++)
                if (_claims[i].UnitID == unitID) return _claims[i].Op ?? "";
            return "";
        }

        /// <summary>
        /// 「当前动作链的目标 uid」——**无归属页**（`FindNpcOf` 判不出，如攻击逃跑页 20303）
        /// 借用它入段（09-23 用户定：这类页不能丢）。
        ///
        /// 只有**恰好一张**凭证时才给（多张 = 判不出，判不出就闭嘴）；没有则返回 ""
        /// （那一路维持"不记 + 清上下文"）。动作工具本来就被 busy 键串行化，正常至多一张。
        /// </summary>
        public static string CurrentChainUid()
        {
            if (_claims.Count != 1) return "";
            return _claims[0].UnitID ?? "";
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

        /// <summary>
        /// 放行"同一场剧情的后续翻页"这条会话（**战斗链收口时调**）。
        ///
        /// 为什么要它（09-22 实测）：会话**不随凭证吊销**（那是给 marry 那类"Resolve 之后游戏才开下一层"
        /// 用的，见类头【多翻页怎么办】）—— 但对**战斗链**来说，收口意味着这场戏的结局已经给过模型了，
        /// 之后同窗翻出来的页若还被会话认领，就会**三处都没有它**：不注入按钮（玩家聊不到）、
        /// 段里标"照记不投"、而台账已收口也不进 tool result ⇒ 玩家正盯着的那句话，模型永远看不到。
        /// （今晚尾页 20413 是**新窗实例**、侥幸没被认领；同窗翻页的支 —— 如切磋的 21401→21402 —— 就会丢。）
        ///
        /// 只给 `BattleOutcome` 用（它收口即"这场戏定案了"）：marry/结拜那类**不走**战斗台账，
        /// 会话照旧护着它们的第二层翻页。
        /// </summary>
        public static void ReleaseSession(string unitID)
        {
            if (string.IsNullOrEmpty(unitID)) return;
            if (_sessionPtr == IntPtr.Zero) return;
            if (_sessionUnit != unitID) return;      // 不是这个人的会话 ⇒ 不碰
            _sessionPtr = IntPtr.Zero;
            _sessionUnit = "";
            _sessionDeadline = 0f;
            try { ModMain.P("[DramaNativeClaim] 会话已放行（战斗链收口）unit=" + unitID + " —— 之后同窗翻页照常注入/可投"); }
            catch { }
        }

        /// <summary>过期凭证清理（每次读写顺手做，不挂常驻定时器）。</summary>
        private static void Sweep(float now)
        {
            for (int i = _claims.Count - 1; i >= 0; i--)
                if (now > _claims[i].Deadline) _claims.RemoveAt(i);
        }
    }
}
