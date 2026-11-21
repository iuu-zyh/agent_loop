/// <summary>
/// WorldStateCore —— 「此刻能不能做事」的**纯逻辑核**（10-25）。
///
/// ## 为什么"忙"不能只有一个答案（这是本文件存在的全部理由）
/// 同一个"忙"字，在本仓有**四个真实不同的视角**，它们的**成员不同、顺序不同**，
/// 而且差异是**刻意的、有代价考量的**：
///
/// | 视角 | 谁问 | 成员（按判定顺序） | 为什么是这个集合 |
/// |---|---|---|---|
/// | **SpeechBlock** | 主动互动的日节拍/派发 | 未进世界 · 玩家死亡复活中 | 只拦"根本不该开口"；**战斗中不在此列**——战斗中允许传音（顶部横幅、非模态、不挡操作）|
/// | **Busy** | 发给 Python 的 `busy` 帧 | 战斗中 · 我方挂起动作 · 剧情窗 · 战斗界面 · 对话窗 · Mask · Window | 玩家正忙 ⇒ 本回合只许言语+只读（执行层 `speech_only_turn` 兜底）|
/// | **Yield** | 动作排队器（让路）| 剧情窗 · Mask · Window | **刻意不含对话窗**：直发路径就是在对话窗开着时弹确认窗的（模型在聊天里发起动作＝正常语义）；算进去会把执行一直拖到玩家关窗，而他可能聊很久 |
/// | **Execute** | 动作排队器（能不能落刀）| 链收尾 · 战斗中 · 我方挂起 · Yield · 不在主世界 | 比 Busy 多"链收尾"和"不在主世界"、少"对话窗"——排队等的是"游戏能接住这个弹窗"，不是"玩家有没有空说话" |
///
/// **合并这四者会立刻产生 bug**：把 Yield 并进 Busy，`movement`/`economy_item` 的直发路径
/// 会在对话窗开着时永远排不上队（用户 09-24 拍板过一次，别再翻案）；把 Busy 并进 Yield，
/// 战斗中传音会静默丢弃。
///
/// 所以本类的形态是：**四个视角、同一批输入**。输入是 `Snapshot`（纯数据，无 Unity），
/// 由 `WorldStateGate` 组装。这样：
///   · 读数只有一份（`WorldStateFacts`）；
///   · 每个视角的组合只有一份（本文件），且**在同一个屏幕上并排可读**；
///   · 差异是显式的、有注释的、**可单测的**（`csharp/tests/WorldStateCoreTests`）。
///
/// ## 零 Unity / 零 Newtonsoft
/// 同 `DeferredActionCore` / `BattleFlowCore`：这里每条规则错在真机上都是静默的
/// （该拦没拦 = 弹出撞窗；不该拦却拦 = NPC 永久哑掉），必须能在开发机上跑断言。
/// </summary>
using System;

namespace AgentLoopBridge
{
    /// <summary>玩家单位读数。**三态**，因为"没有玩家单位"与"读的时候抛了异常"
    /// 在原实现里给的是**两句不同的话**（`playerUnit=null（未进世界）` / `playerUnit 读取异常`）
    /// —— 合成一个 bool 就把排障线索丢了。
    ///
    /// 定义在**核**这一侧（而不是 `WorldStateFacts`）：核是零依赖的纯逻辑，
    /// 测试工程只链接 `WorldStateCore.cs` 一个文件；反过来依赖 Facts 会让 97 项断言编不过。</summary>
    internal enum PlayerUnitRead
    {
        /// <summary>有玩家单位（= 在存档世界里）。</summary>
        Present = 0,
        /// <summary>`g.world.playerUnit == null` ⇒ 还没进世界。</summary>
        Absent = 1,
        /// <summary>读的时候抛了异常（世界正在重建/卸载）。</summary>
        Error = 2,
    }

    /// <summary>某一刻的世界状态**快照**（纯数据）。字段顺序无关，但**名字要能一眼对上话**。
    /// 由 `WorldStateGate` 填：世界电平来自 `WorldStateFacts`，两个剧情标记由主工程注入
    /// （`state/` 刻意不依赖 `drama/`，见 `tests/test_state_module_boundary.py`）。</summary>
    internal struct WorldStateSnapshot
    {
        /// <summary>玩家单位读数（三态：有 / 没有 / 读异常）。</summary>
        public PlayerUnitRead PlayerUnit;
        /// <summary>玩家死亡/复活选择窗可见。</summary>
        public bool DeathWindowVisible;
        /// <summary>`g.world.battle.isBattle`（战斗位的一半，**不**含战斗界面）。</summary>
        public bool IsBattleLevel;
        /// <summary>`UIBattleInfo` 可见（战斗位的另一半）。</summary>
        public bool BattleUiVisible;
        /// <summary>我方挂起动作（`DramaGate.HasPending`）—— 主工程注入。</summary>
        public bool DramaPendingAction;
        /// <summary>剧情链收尾占位（`DramaGate.ChainHold`）—— 主工程注入。</summary>
        public bool DramaChainHold;
        /// <summary>我方自制确认窗可见。</summary>
        public bool OwnDramaWindowVisible;
        /// <summary>UI 层层顶名字（"" = 没有）。</summary>
        public string UiLayerTopName;
        /// <summary>对话窗（`UIChatAi`）可见。</summary>
        public bool ChatWindowVisible;
        /// <summary>Mask 层层顶名（"" = 没有可见层顶）。</summary>
        public string MaskLayerName;
        /// <summary>Window 层层顶名（"" = 没有可见层顶）。</summary>
        public string WindowLayerName;
        /// <summary>主世界 HUD（`MapMain`）已就绪。</summary>
        public bool MainWorldReady;
    }

    internal static class WorldStateCore
    {
        // ------------------------------------------------------------------
        // 共用的最小组合（两个视角都要"剧情窗"，所以抽出来 —— 唯一实现）
        // ------------------------------------------------------------------

        /// <summary>有没有剧情窗**正显示**；非空 = 人话原因。
        ///
        /// 两个来源缺一不可：
        ///   ① 我方自制确认窗（判**可见**，不判存在 —— `CloseUI` 不一定销毁实例，
        ///      判存在会把"刚关掉的窗"也算剧情中，本局第一次同格互动极易命中）；
        ///   ② 游戏自己的剧情窗家族（层顶名字含 `Drama`：`UIDramaDialogue` / `UIDramaBase` …）。
        ///
        /// ★这是唯一实现★ 历史事故：面板闸门与忙判据各写一套（一个判"存在+单一硬编码名"、
        /// 一个判"可见+名字家族"），于是同一时刻一边说"剧情窗挡着"、一边说"玩家不忙"。
        /// </summary>
        public static string DramaWindowReason(bool ownDramaWindowVisible, string uiLayerTopName)
        {
            if (ownDramaWindowVisible) return "我方确认窗 UICustomDramaDyn 可见";
            if (!string.IsNullOrEmpty(uiLayerTopName) &&
                uiLayerTopName.IndexOf("Drama", StringComparison.OrdinalIgnoreCase) >= 0)
                return "剧情窗开着（" + uiLayerTopName + "）";
            return "";
        }

        /// <summary>"玩家正被**游戏**占着"（剧情窗 / Mask / Window）—— `YieldReason` 与
        /// `ExecuteBlocker` 共用。**刻意不含对话窗**（理由见类头那张表）。</summary>
        private static string GameHoldingReason(ref WorldStateSnapshot sn)
        {
            string d = DramaWindowReason(sn.OwnDramaWindowVisible, sn.UiLayerTopName);
            if (d.Length > 0) return d;
            if (!string.IsNullOrEmpty(sn.MaskLayerName))
                return "Mask 层有模态遮罩：" + sn.MaskLayerName;
            if (!string.IsNullOrEmpty(sn.WindowLayerName))
                return "Window 层有窗口：" + sn.WindowLayerName;
            return "";
        }

        // ------------------------------------------------------------------
        // 视角一：能不能开口
        // ------------------------------------------------------------------

        /// <summary>**能说闸**：只拦"根本不该开口"的状态 —— 未进世界、玩家死亡/复活选择中。
        /// 非空 = 本次主动互动触发丢弃。
        ///
        /// **战斗中不在此列**：战斗中允许传音（只弹顶部横幅，非模态、不挡操作）。</summary>
        public static string SpeechBlock(ref WorldStateSnapshot sn)
        {
            switch (sn.PlayerUnit)
            {
                case PlayerUnitRead.Absent: return "playerUnit=null（未进世界）";
                case PlayerUnitRead.Error: return "playerUnit 读取异常";
            }
            if (sn.DeathWindowVisible) return "UIMapDie 可见（玩家死亡/复活中）";
            return "";
        }

        // ------------------------------------------------------------------
        // 视角二：能不能发起动作（发给 Python 的 busy 帧）
        // ------------------------------------------------------------------

        /// <summary>**能做动作闸**：非空 = 玩家忙，本回合只许言语 + 只读。
        ///
        /// 判定顺序**照搬旧实现**（`NpcInitiativeMonitor.BusyReason` 2026-09 以来的形态），
        /// 别重排：这个串会进日志（`ContactDuty` 收尾分流会打"（原因）"），
        /// 排障时靠它认现场；而且第一条（`isBattle`）是最常见的命中，放前面省钱。
        ///
        /// 注意第一条**只读 `isBattle`**、第二条战斗界面**单列**（不是取或）——
        /// 这与 `BattleActive`（取或）**刻意不同**：这里要的是"哪一位亮的"，
        /// 那里要的是"战斗位整体亮没亮"。</summary>
        public static string BusyReason(ref WorldStateSnapshot sn)
        {
            if (sn.IsBattleLevel) return "world.battle.isBattle（战斗中）";
            if (sn.DramaPendingAction) return "DramaGate.HasPending";

            string d = DramaWindowReason(sn.OwnDramaWindowVisible, sn.UiLayerTopName);
            if (d.Length > 0) return d;

            // 战斗 UI 真的显示着（结算期 isBattle 可能已落，故保留 —— 单列，不是取或）
            if (sn.BattleUiVisible) return "UIBattleInfo 可见";

            // ★ 用户 09-2x 的规则原话 ★"我的对话 UI 打开的时候必须要判忙，否则我在和别的 NPC
            //   对话呢，你给我直接弹出窗口或者动作打断了，这很不好；其余的不算忙（看 NPCInfo、
            //   地图之类），但是 DramaDialogue 需要算忙。"
            //   所以：**不按层判 UI(0)**（那会把所有游戏面板一网打尽），只按**具体界面**判两个：
            //     ① 我方对话窗 UIChatAi —— 正在与某个 NPC 对话。判忙的后果恰好是用户要的：
            //        同格不弹确认窗、动作工具被禁、回来的传音走未读红点 + 顶部横幅（不抢焦点）；
            //     ② 剧情窗（名字含 Drama 的家族）—— 已在上面判过。
            //   其余 UI(0) 面板（NPCInfo/PlayerInfo/PlayerTask/FateFeature/GetReward/MinMap…）
            //   **不算忙**：看资料时希望 NPC 照常来互动（照常弹确认窗、照常调行动）。
            if (sn.ChatWindowVisible) return "对话窗开着（UIChatAi）：正在与 NPC 对话，不弹窗不行动";

            // 游戏自己的 UI 分层判据：
            //   UILayer.Mask = 全屏输入遮罩层（模态确认/剧情/结算在挡输入），
            //   UILayer.Window = 窗口层（游戏自带界面）。
            // 两者任一有**可见**的层顶 UI = 玩家正被界面占着 → 本回合只许言语。
            // 只判 Mask/Window：**绝不判 UITop**（HUD/地图常驻 UITop，判了就永远拦）。
            if (!string.IsNullOrEmpty(sn.MaskLayerName))
                return "Mask 层有模态遮罩：" + sn.MaskLayerName;
            if (!string.IsNullOrEmpty(sn.WindowLayerName))
                return "Window 层有窗口：" + sn.WindowLayerName;
            return "";
        }

        /// <summary>`BusyReason` 的布尔形态：玩家是否处于"别弹窗打扰"的状态。
        /// 收尾分流（`ContactDuty` 同格弹出前）用：能说闸与能做动作闸**任一**命中就不弹窗。</summary>
        public static bool IsPlayerBusy(ref WorldStateSnapshot sn)
        {
            return SpeechBlock(ref sn).Length > 0 || BusyReason(ref sn).Length > 0;
        }

        // ------------------------------------------------------------------
        // 视角三：排队器要不要让路
        // ------------------------------------------------------------------

        /// <summary>**让步判据**（09-24，动作排队器用）：此刻"弹一个原生窗会不会抢玩家的界面"。
        /// 返回非空 = 让路（等这一刻过去），空 = 可以弹。
        ///
        /// 与 `BusyReason` 的关系：**判据同源**（同一批原始件），但**有意是不同集合** ——
        ///   · **不含**"我们的对话窗（UIChatAi）"：直发路径就是在对话窗开着时弹确认窗的
        ///     （模型在聊天里发起动作 = 正常语义）；算进去会让排队器与直发语义分叉，
        ///     还会把执行一直拖到玩家关窗（他可能聊很久）；
        ///   · **不含**"我们自己的挂起动作"（那条由排队器自己的阻塞项管）。
        /// 含的三类都是"玩家正被**游戏**占着"：剧情窗（含我方确认窗）/ Mask 层模态遮罩 /
        /// Window 层窗口。判据异常一律当"没有"（fail-open：宁可弹一下，也别把队列永久卡死）。</summary>
        public static string YieldReason(ref WorldStateSnapshot sn)
        {
            return GameHoldingReason(ref sn);
        }

        // ------------------------------------------------------------------
        // 视角四：排队器能不能落刀
        // ------------------------------------------------------------------

        /// <summary>**排队器能不能执行**：非空 = 现在还不能（等），空 = 可以落刀。
        ///
        /// 五条，顺序即优先级（与旧实现逐字一致）：
        ///   ① 剧情链在收尾 —— 处置页收口那一刻起，链的尾页还要十几秒才来；这期间
        ///      `game.battle.isBattle` 仍为真，两条判据都算"发不出去"；
        ///   ② 战斗中（**取或**：逻辑战斗位 ∪ 战斗界面）；
        ///   ③ 另有一个动作正等玩家点选（再弹一个 = 撞窗）；
        ///   ④ 玩家正被游戏界面占着（= `YieldReason`，**不含**对话窗）；
        ///   ⑤ 不在主世界（场景 / 加载 / 主菜单里弹窗一样会沉进系统队列不出队）。
        ///
        /// 与 `BusyReason` 的差异正是"排队"这个动作的语义：它问的不是"玩家有没有空说话"，
        /// 而是"**游戏能不能接住这个原生窗**"。所以多"链收尾/不在主世界"、少"对话窗"。</summary>
        public static string ExecuteBlocker(ref WorldStateSnapshot sn)
        {
            if (sn.DramaChainHold) return "剧情链收尾中";
            if (sn.IsBattleLevel || sn.BattleUiVisible) return "战斗中";
            if (sn.DramaPendingAction) return "另有一个动作在等玩家点选";

            string y = GameHoldingReason(ref sn);
            if (y.Length > 0) return "玩家正被界面占着（" + y + "）";

            if (!sn.MainWorldReady) return "不在主世界（世界界面未就绪）";
            return "";
        }

        /// <summary>一行诊断（写日志用；四个视角的结论并排，排障时一眼看出"到底哪一条在拦"）。</summary>
        public static string Describe(ref WorldStateSnapshot sn)
        {
            return "说=" + Mark(SpeechBlock(ref sn))
                 + " 做=" + Mark(BusyReason(ref sn))
                 + " 让=" + Mark(YieldReason(ref sn))
                 + " 行=" + Mark(ExecuteBlocker(ref sn));
        }

        private static string Mark(string reason)
        {
            return string.IsNullOrEmpty(reason) ? "通" : reason;
        }
    }
}
