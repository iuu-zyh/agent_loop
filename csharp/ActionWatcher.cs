/// <summary>
/// 动作完成观察者 —— 「自主交互回合里动了手 → 动作完成那一刻把对话 UI 推到我眼前」。
///
/// 用户原话（三次收敛）：
///   第一版：「现在是这些ai直接调用动作了，如果同格的话调用了动作，然后动作完成之后我需要自动打开对话UI」；
///   第二版：「只要是主动交互的话调用了动作工具之后，动作完成那一刻就自动打开对话UI，
///   **不需要考虑是否同格**」——距离判据已删（只留作日志信息）；
///   第三版（**现行规则**）：「同格互动无论是对话还是动作都应该**立即弹确认窗**，点击确认后
///   立即弹出对话UI进行后续逻辑，**就不需要后续的根据工具结果立即打开对话UI了**，异地动作依然保留这个机制」。
///
/// 现行判据（第三版）= **这一回合有没有因为"当面确认窗 + 玩家点同意"而已经开过窗**，而不是"同格/异地"四个字：
///   - 同格+闲（`FireOne` 的 `sameGrid && !isBusy` 分支）→ 确认窗 → 点同意 → 窗口当场打开（「对方正在斟酌…」），
///     本类**不再补开**（否则玩家中途关窗还会被动作弹回来）；
///   - 异地+闲 → 没有确认窗这一步 → 本类保留"动作完成即开窗"（既成事实必须让玩家看见）；
///   - 同格+忙 → Python 侧 `BUSY_NOTE` + 执行层忙碌闸（`dialogue_agent.py`）把 5 个动作工具全部拦下，
///     根本不会发 `call_tool` 帧过来 → **本类收不到这种回合的动作事件**，无需讨论。
///
/// 真机现场（00:21 云含 intent=recent，`Player.log` + `agent_loop.log` 双向对证）：
///   ① `forced trigger … sameGrid=False` —— **触发那一刻异地**；
///   ② 按 09-13 拍板 (b)（分流以触发时判定为准）→ 不弹确认窗，只发传音；
///   ③ 但**动作闸对"异地+玩家空闲"是放行的**（README 附录二 G.5 的表）→ 模型推理
///      "既然我们异地相隔，可以用 world_ai_action，op=lun_dao（论道），这是言语往来，可远程"
///      （`WsClient step(think)` 原文）→ 动作**真的执行了**（23.6s）；
///   ④ 回复抵达时 `现在同格=True`（这 65s 里玩家已经走到它旁边），分流③ → 只登记未读 + 横幅。
/// 结果：NPC 就在玩家旁边"论道"了一场，玩家屏幕上什么都没发生。
///
/// 本类补的**不是征求同意**（那是确认窗的职责，仍严格按触发时判定，不受本类影响），
/// 而是**既成事实的通报**：动作工具**真的执行成功**之后，只要这一回合是**自主开口**回合、
/// 且没有任何会打断玩家的界面（战斗 / 我们的确认窗 / 别的对话窗 / 剧情窗 / 模态遮罩 /
/// 游戏窗口——与"能弹确认窗"**同一把尺子** `BusyReasonText()`），就自动打开对话 UI，
/// 玩家当场看到"它做了什么 + 它说的话"。回复随后抵达 → `ContactDuty` 分流①（该 NPC 窗开着）
/// → 直播已读，不会重复登记未读，也不会二次开窗。
///
/// 边界：
///   - **只对自主开口回合生效**：`NoteInitiativeTurn` 标记的 NPC 才自动开窗——玩家自己发起的
///     对话窗本来就在，而且玩家中途手动关窗后不该被动作弹回来。标记在玩家发起新回合时撤销、
///     在回复抵达时收尾，另有 TTL 兜底（动作可能很慢 + 有排队）；
///   - **不看远近**（用户第二版拍板）：异地动作照样开窗——动作是既成事实，玩家必须看得见；
///     "异地只亮红点+横幅"仍适用于**纯说话**的传音（那条走 `ContactDuty` 分流③，与本类无关）。
///
/// 线程：仅主线程（工具执行在 `MainThreadDispatcher` 上；本类只被 `WsClient` 与
/// `NpcInitiativeMonitor` 调用，两者都在主线程）。
/// </summary>
using System;
using System.Collections.Generic;
using UnityEngine;

namespace AgentLoopBridge
{
    internal static class ActionWatcher
    {
        /// <summary>
        /// **动作类工具**（会改变世界/关系/物品/位置的那 5 个）——必须与 Python 侧
        /// `tools/schemas.py` 的动作/只读分组一致：只读三件套（inspect_unit / search_units /
        /// query_world）**不算动作**，它们不改变任何东西，不值得打断玩家。
        /// </summary>
        private static readonly HashSet<string> ActionTools = new HashSet<string>(StringComparer.Ordinal)
        {
            "world_ai_action", "social_relation", "economy_item", "movement", "item_acquire",
        };

        /// <summary>自主开口回合标记的有效期（s）。动作本身可能跑 20~60s，再叠加工具往返与排队。</summary>
        private const float InitiativeTtl = 900f;

        /// <summary>npc → 标记失效时刻（`Time.unscaledTime`）。</summary>
        private static readonly Dictionary<string, float> _initiativeTurns = new Dictionary<string, float>();

        /// <summary>npc → 本回合的对话窗是否**已经**因"当面确认窗 + 玩家点同意"打开过（第三版判据）。
        /// true = 同格当面路径：窗口在玩家点同意那一刻就开了，动作完成不再补开。</summary>
        private static readonly Dictionary<string, bool> _openedByConsent = new Dictionary<string, bool>();

        /// <summary>该工具是否属于"动作类"（只读工具返回 false）。</summary>
        internal static bool IsActionTool(string name)
        {
            return !string.IsNullOrEmpty(name) && ActionTools.Contains(name);
        }

        /// <summary>自主开口触发时登记（`NpcInitiativeMonitor.FireOne` 调）。
        /// `windowOpenedByConsent` = 本次触发走了"同格当面确认窗"那条路（= `sameGrid && !isBusy`）——
        /// 玩家一点同意，`NpcInitiativeMonitor.TickPendingOpen` 就把对话窗打开了，本类不再补开（第三版）。
        /// 传 true 的场合只有这一种；异地/诊断强制（force_remote）一律 false → 保留"动作完成即开窗"。</summary>
        internal static void NoteInitiativeTurn(string npc, bool windowOpenedByConsent)
        {
            if (string.IsNullOrEmpty(npc)) return;
            try
            {
                _initiativeTurns[npc] = Time.unscaledTime + InitiativeTtl;
                _openedByConsent[npc] = windowOpenedByConsent;
            }
            catch { }
        }

        /// <summary>玩家自己发起的回合（`WsClient.SendPlayerMessage` 调）→ 撤销标记：
        /// 那种回合窗口本来就在玩家眼前，动作完成不该再抢一次焦点。</summary>
        internal static void NotePlayerTurn(string npc)
        {
            if (string.IsNullOrEmpty(npc)) return;
            try { _initiativeTurns.Remove(npc); _openedByConsent.Remove(npc); } catch { }
        }

        /// <summary>回复抵达（`ContactDuty` 分流收尾）→ 撤销标记，防长期挂着。</summary>
        internal static void ClearInitiativeTurn(string npc)
        {
            if (string.IsNullOrEmpty(npc)) return;
            try { _initiativeTurns.Remove(npc); _openedByConsent.Remove(npc); } catch { }
        }

        private static bool IsInitiativeTurn(string npc)
        {
            if (string.IsNullOrEmpty(npc)) return false;
            float until;
            if (!_initiativeTurns.TryGetValue(npc, out until)) return false;
            if (Time.unscaledTime > until) { _initiativeTurns.Remove(npc); return false; }   // TTL 过期
            return true;
        }

        /// <summary>
        /// **动作工具成功执行后**调用（主线程）——这是用户要的"动作完成之后"这一刻：
        /// C# 执行 `call_tool` 是同步等动作真的跑完的（`world_ai_action` 23.6s），
        /// 所以本方法被调到的时刻 = 动作已落地。
        /// </summary>
        internal static void OnActionDone(string npcId, string tool)
        {
            try
            {
                if (!IsActionTool(tool)) return;
                if (string.IsNullOrEmpty(npcId))
                {
                    ModMain.P("[ActionWatcher] 动作完成但帧里没带 npc_id（旧 Python？）→ 不自动开窗 tool=" + tool);
                    return;
                }
                if (!IsInitiativeTurn(npcId))
                {
                    // 玩家自己发起的回合（或标记已收尾）：窗本来就在/玩家主动关过，不抢
                    return;
                }
                // 第三版 同格当面路径（确认窗 → 玩家点同意 → 窗口当场已开）→ **不再补开**。
                // 用户原话："同格互动…点击确认后立即弹出对话UI进行后续逻辑，就不需要后续的
                // 根据工具结果立即打开对话UI了"。这里显式落规则：以前是靠下面 AnyChatOpen 顺带挡住的
                // （同格同意后窗必然开着），但玩家中途关窗就会失效——规则要写死在回合属性上，不靠现场。
                bool openedByConsent;
                if (_openedByConsent.TryGetValue(npcId, out openedByConsent) && openedByConsent)
                {
                    ModMain.P("[ActionWatcher] 动作完成，但本回合的对话窗在你点「同意」时就已经打开了" +
                              "（同格当面路径）→ 按 09-13 第三版规则不再补开 ｜ npc=" + npcId + " tool=" + tool);
                    return;
                }
                if (UnreadStore.AnyChatOpen)
                {
                    ModMain.P("[ActionWatcher] 动作完成但已有对话窗开着 → 不抢焦点 ｜ npc=" + npcId + " tool=" + tool);
                    return;
                }
                string busy = NpcInitiativeMonitor.BusyReasonText();
                if (!string.IsNullOrEmpty(busy))
                {
                    ModMain.P("[ActionWatcher] 动作完成但此刻不宜打扰（" + busy + "）→ 走未读+横幅 ｜ npc=" +
                              npcId + " tool=" + tool);
                    return;
                }
                var wub = UnitLookup.Resolve(npcId);
                if (wub == null)
                {
                    ModMain.P("[ActionWatcher] 动作完成但找不到该 NPC 单位 → 不开窗 ｜ npc=" + npcId);
                    return;
                }
                // 走到这里 = 本回合**没有**确认窗那一步（异地直发传音）→ 保留"动作完成即开窗"。
                // **仍然不看远近**（第二版拍板）：异地触发、动作跑完时人已经在旁边，也得让玩家看见。
                // 同格与否只作为现场信息记进日志（排查用），不参与判定。
                bool sameGrid = false;
                try
                {
                    var player = g.world.playerUnit;
                    sameGrid = player != null && UnitSnapshot.IsSameGrid(wub, player);
                }
                catch { }
                ChatLauncher.OpenForUnit(wub);
                ModMain.P("[ActionWatcher] 动作完成 → 异地（无确认窗的）自主开口回合自动开窗 ｜ npc=" + npcId +
                          " tool=" + tool +
                          "（同格=" + sameGrid + "，按 09-13 规则不看远近；回复抵达走分流①直播已读，不再登记未读）");
            }
            catch (Exception e)
            {
                ModMain.P("[ActionWatcher] OnActionDone: " + e.Message);
            }
        }
    }
}
