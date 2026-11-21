/// <summary>
/// AB 三面板宿主的**创建闸门**（静态；ModMain / UiComposer / 三个 Ab*Panel 共用）。
///
/// 闸门只有三条**世界就绪**判据（09-21 定稿；原来的第四条「剧情窗」已删，见下）：
///   ① `CurrentGameDay() != -1`（能取到日历）
///   ② `g.world.playerUnit != null`（存档世界已加载）
///   ③ `g.ui.GetUI(UIType.MapMain) != null`（世界 HUD 已建 = 世界真正就绪）
/// 命中原因由 `GateReason()` 以**准确措辞**回传（"游戏日历尚未就绪 / 存档世界尚未加载 /
/// 世界界面尚未就绪"），不再用"玩家在不在存档"冒充失败原因。
///
/// ①②③ 的依据（09-10 事故，实锤）：`playerUnit` 在 `WorldMgr:EnterWorldFixData` /
/// `OpenMapDrama` 阶段就已非 null，但世界 UI 还没建好；此刻调 `g.ui.OpenUI` 会撞上
/// 游戏 UIMgr 正在重建实例 Canvas+RT。
///
/// ★ 原 ④「剧情窗正显示」已删除（09-21）★
/// ---------------------------------------------------------------------------------
/// 它声称防的是"OpenUI 撞上剧情窗展示"，而这条**正是 09-10 事后复盘里第 2 号已证伪假说**
/// （`docs/archive/2026-09-10-世界输入冻结.md:172`：「闸门后仍复现，证伪」——
/// 加了这道闸，输入冻结照样发生；定案双根因是 UI 生命周期违约与立绘 API 误用）。
/// 也就是说它在面板闸门上从来没有保护过任何东西。
///
/// 它之所以还活着：09-21 修 ⑤（`OpenMapDrama` 后 300 帧静置，被读档期的任务批量扫描误触发，
/// 害玩家每次进世界点地图「传」都看到"剧情/界面切换中"）时，让 ④ 改用
/// `NpcInitiativeMonitor.DramaWindowName()` 这条共用实现。**合并的是判据代码，
/// 顺手把 `BusyReason` 的结论也搬了过来** —— 可这两个调用方问的不是一件事：
///   · `BusyReason`：玩家正忙吗，能不能**打断**他（弹确认窗／发起行动）→ 剧情中不该打断，成立；
///   · `GateReason`：能不能**建一个面板宿主** → 调用方全是玩家亲手点开的
///     （NPC 面板「AI 对话」/ 通讯录行 / 剧情窗「对话」/ 对话窗 ⚙），没有"打断"可言，不成立。
/// 后果：剧情窗上那颗「对话」按钮被 ④ 当场拦死（点它的那一刻剧情窗必然在显示），
/// 玩家连点四次四条"当前不宜创建（剧情窗开着（DramaDialogue））"。
///
/// 删完之后仍然需要"剧情中别打扰玩家"的那一方，自己把判据拿回去：
///   · `NpcInitiativeMonitor.BusyReason` —— 一个字没动（它本来就在用 `DramaWindowName()`）；
///   · `BrainLink` 的失败横幅 —— 那是系统自己弹的东西，语义属 BusyReason 那一类，
///     已改成它自己调 `NpcInitiativeMonitor.IsDramaWindowShowing()`，不再冒充"世界就绪"。
///
/// 自动创建已废除（09-10 定案）
/// 面板一律**按需创建**：`ContactPanelOpener`（地图「传」按钮）/ `ConfigPanelOpener`（对话窗 ⚙）/
/// `AbChatPanel.EnsureResident`（NPC 面板「AI 对话」）。
/// ⇒ `OnFrame` **不再创建任何东西**，只服务两个诊断哨兵（`_diag_no_panels` / `_diag_lazy_panels`）。
///
/// ★ ③ 的「世界曾就绪」粘滞位（09-23 实机修复）★
/// ---------------------------------------------------------------------------------
/// 症状：邀约（赏花）进场景后，双方在场页上那颗「对话」按钮点了没反应 ——
///   `Player.log` 三行：`[AbChatPanel] 当前不宜创建（世界界面尚未就绪）` →
///   `[ChatLauncher] 对话宿主不可用（OpenUI 失败）` → `[DramaAiOption] 打开对话面板失败`。
/// 机理：邀约的"赏花"触发的是**独立战斗场景**（`SceneBattle` 走 `LoadSceneAsync`，
///   `Player.log`："进入战斗  耗时：1869毫秒"）—— 该场景**没有世界 HUD（MapMain）**，
///   而对话宿主随场景切换被销毁（`AbChatPanel.OnDestroy` 置空 `_resident`），
///   重建时被旧 ③ 当场拦死 ⇒ **整个场景期间都开不了对话窗**（不是偶发）。
/// 判据修正：③ 的语义是"世界**曾经**就绪过"，而不是"此刻 MapMain 实例在"。
///   `_worldHudSeen` 粘滞位：见过 MapMain 一次即置位（幂等）；此后 MapMain 缺失
///   （场景切换 / 战斗场景）也放行；**从未见过则原样拦截** —— 09-10 那个事故场景
///   （进世界早期 `EnterWorldFixData` / `OpenMapDrama`）正是"从未见过"，防线原样保留。
/// 置位点（三处，幂等）：① `GateReason` 见到 MapMain；② `MapMainContactButton` HUD 按钮
///   注入成功（世界 HUD 建成的最强外部信号，每次世界加载必发生）；③ 对话宿主装配成功。
/// 复位点：`ModMain.OnIntoWorldEvent`（读档 / 换档，与其余"静态表活过世界重载"同钩子）——
///   **战斗场景加载不走它**，所以粘滞位能正确跨场景留存。
/// </summary>
#if AB_UI
using System;
using UnityEngine;

namespace AgentLoopBridge
{
    public static class AbPanelProber
    {
        /// <summary>懒创建模式的一次性清理标记（诊断哨兵 `_diag_lazy_panels.txt`）。</summary>
        private static bool _lazyCleanupDone;

        /// <summary>Frame 每帧回调（主线程）：**本类已不再创建任何东西**，只服务两个诊断哨兵。</summary>
        public static void OnFrame()
        {
            // 诊断（因果对照用）：mod 被整体停用后，本回调不再做任何实质工作
            if (ModMain.Suspended) return;
            // 诊断哨兵：禁用面板时销毁已有宿主——用户删掉文件即恢复
            if (DiagSwitches.NoPanels)
            {
                if (AbContactPanel.IsAlive || AbConfigPanel.IsAlive)
                {
                    AbContactPanel.DestroyResident();
                    AbConfigPanel.DestroyResident();
                    ModMain.P("[AbPanelProber] 诊断开关 noPanels：已销毁通讯录/配置宿主");
                }
                return;
            }
            // 诊断哨兵（lazyPanels）：禁止自动创建，只在用户主动用时才建
            // （与对话面板同款时机，用于验证"创建时机"假说）
            if (DiagSwitches.LazyPanels)
            {
                if (!_lazyCleanupDone)
                {
                    _lazyCleanupDone = true;
                    AbContactPanel.DestroyResident();
                    AbConfigPanel.DestroyResident();
                    ModMain.P("[AbPanelProber] 懒创建模式：已销毁自动创建的宿主（之后只在用户主动打开时创建）");
                }
                return;
            }
            if (_lazyCleanupDone) _lazyCleanupDone = false;   // 关闭懒创建后复位
        }

        /// <summary>用户主动触发路径的即时闸门（地图「传」按钮 / 对话窗 ⚙ / NPC 面板「AI 对话」）。
        /// 通过返回 true；被拦时 `reason` 给出**是哪道闸门**的人话原因（给玩家看的，不许再用笼统措辞）。</summary>
        public static bool CanCreateNow() { return GateReason().Length == 0; }

        // ------------------------------------------------------------------
        // ③ 的「世界曾就绪」粘滞位（09-23 实机修复；机理见文件头）
        // ------------------------------------------------------------------

        /// <summary>世界 HUD **曾经**就绪（粘滞位）。见文件头：战斗场景（SceneBattle）没有
        /// MapMain，但世界早已就绪过 —— ③ 不该把这种情况当成"世界没就绪"（否则邀约场景里
        /// 对话宿主一重建就被拦死）。</summary>
        private static bool _worldHudSeen;

        /// <summary>放行/置位日志节流（战斗场景期间每次建面板都会走 GateReason）。</summary>
        private static float _hudSeenLogAt;

        /// <summary>
        /// 置位粘滞位（幂等）。三处调用：`GateReason` 见到 MapMain / HUD 传音簿按钮注入成功
        /// （世界 HUD 建成的最强外部信号）/ 对话宿主装配成功。`source` 只进首次那行日志。
        /// </summary>
        public static void NoteWorldHudAlive(string source)
        {
            if (_worldHudSeen) return;
            _worldHudSeen = true;
            ModMain.P("[AbPanelProber] 世界 HUD 已就绪（③ 粘滞位置位；来源=" + source + "）");
        }

        /// <summary>
        /// 世界重载（读档/换档）复位：新世界的 HUD 未必已建，粘滞位必须作废 —— 否则
        /// "上一世界曾就绪"会变成新世界进世界早期的通行证，把 09-10 那个 OpenUI 窗口
        /// 重新打开。与其余"静态表活过世界重载"的清理同钩子（`ModMain.OnIntoWorldEvent`）。
        /// **战斗场景加载不走该钩子**，所以粘滞位能正确跨场景留存（这正是修复要的）。
        /// </summary>
        public static void EnteringWorld()
        {
            _worldHudSeen = false;
        }

        /// <summary>
        /// 闸门判定 + **准确原因**（空串 = 全部通过，可以创建）。
        ///
        /// 为什么返回原因而不是布尔：09-21 实机发现旧实现的文案是
        /// `WorldIdentityAvailable ? "剧情/界面切换中" : "世界尚未加载完成"` ——
        /// `WorldIdentityAvailable` 只等价于"玩家在存档里"，于是**只要人进了存档，
        /// 五道闸门里任何一条失败都会被说成"剧情/界面切换中"**。玩家照着提示去找剧情窗，
        /// 场上却什么都没有（实机日志：被拦的那 ~350 帧里 `UICustomDramaDyn` 全程 0 次、
        /// 游戏剧情窗 `UIDramaDialogue` 一次也没出现）—— 原因与事实对不上，排障被带偏。
        /// </summary>
        public static string GateReason()
        {
            // ① 能取到日历（游戏日历未就绪 = 连世界的框架都还没建）
            try { if (NpcInitiativeMonitor.CurrentGameDay() == -1) return "游戏日历尚未就绪"; }
            catch { return "游戏日历尚未就绪"; }

            // ② 存档世界已加载
            try { if (g.world.playerUnit == null) return "存档世界尚未加载"; }
            catch { return "存档世界尚未加载"; }

            // ③ 世界 HUD 已建 = 世界真正就绪（09-10：光有 playerUnit 不够，
            //    世界进入期（WorldMgr:EnterWorldFixData/OpenMapDrama 阶段）即已非 null）
            //    ★ 09-23：MapMain 此刻不在、但**世界曾经就绪过**（战斗场景 SceneBattle
            //      没有世界 HUD）⇒ 放行，机理见文件头。从未见过 ⇒ 原样拦截。
            //    异常与 null 分开报：旧实现把两者压成同一句话，排障时分不清"没建"还是"查询炸了"。
            bool hudAlive;
            try { hudAlive = g.ui.GetUI(UIType.MapMain) != null; }
            catch (Exception e) { return "世界界面查询异常：" + e.Message; }
            if (hudAlive)
            {
                NoteWorldHudAlive("GateReason 见到 MapMain");
            }
            else if (!_worldHudSeen)
            {
                return "世界界面尚未就绪";
            }
            else
            {
                // 节流：战斗场景期间每次建面板都会走到这里（说清"为什么放行"，便于复核）
                try
                {
                    if (Time.unscaledTime - _hudSeenLogAt > 2f)
                    {
                        _hudSeenLogAt = Time.unscaledTime;
                        ModMain.P("[AbPanelProber] MapMain 不在但世界曾就绪（场景切换/战斗场景）⇒ 闸门放行");
                    }
                }
                catch { }
            }

            // （原 ④「剧情窗正显示」已删 —— 它防的那条已被 09-10 复盘证伪，且它把玩家
            //  亲手点的三枚面板按钮一起拦死了。为什么删、删完谁还需要它，见本类文件头。）

            return "";
        }

        /// <summary>ModMain.Destroy 复位：重新 Init 后重新探测（宿主随场景销毁，
        /// 面板内验活检查会自动重建）。**③ 粘滞位一并作废** —— 重新 Init 会重进世界，
        /// 那是一个新的"世界 HUD 尚未建立"窗口（与 `EnteringWorld` 同款理由）。</summary>
        public static void Reset()
        {
            _lazyCleanupDone = false;
            _worldHudSeen = false;
        }
    }
}
#endif // AB_UI
