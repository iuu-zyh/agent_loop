/// <summary>
/// AB 三面板宿主的**创建闸门**（静态；ModMain / UiComposer / 三个 Ab*Panel 共用）。
///
/// 世界输入失效事故（勿回退，勿放宽闸门）
/// 症状：进存档后能动几下 → 随即角色不能移动/点不开 NPC（游戏自带 UI 仍可点）+ 明显卡顿。
/// 日志铁证：Player.log 在**面板创建/重建那一刻**起，出现恒定每帧的
///   `RenderTexture.Create failed: width & height must be larger than 0`
///   （栈 = PortraitModel:CreateTextureInModelData，25 万~43 万次），世界逻辑日志几乎静止——
///   即某个**剧情窗**的立绘槽位布局被破坏（0 尺寸）后每帧重试，剧情窗因此显示/推进不了，
///   世界输入被"剧情展示中"状态挡住。
/// 定案：`g.ui.OpenUI` 在**世界进入期 / 地图剧情（OpenMapDrama）正在开窗时**执行，
///   会打乱游戏 UIMgr 对该 UI 层的排序/布局（UIMgr 会重建实例 Canvas+RT），
///   连带把同一时刻正在创建的剧情窗布局搞坏。三次运行均复现"重建面板 → 立刻刷屏"。
///
/// 自动创建已废除（定案后）
/// 面板一律**按需创建**：`ContactPanelOpener`（地图「传」按钮）/ `ConfigPanelOpener`（对话窗 ⚙）/
/// `AbChatPanel.EnsureResident`（NPC 面板「AI 对话」）。实证链：①面板只要经 `g.ui.OpenUI` 登记进
/// 游戏 UIMgr，我们的"关闭/销毁"都无法让游戏把它从"打开中的 UI"登记里摘掉 → 世界输入被永久门控；
/// ②`CloseAllUI` 能清空该登记表、每次都能恢复输入；③重读档会重建登记表。
/// ⇒ `OnFrame` **不再创建任何东西**，只服务两个诊断哨兵（`_diag_no_panels` / `_diag_lazy_panels`）。
///
/// 晚：清探针时发现并修掉的两个问题
///   ① `_created` / `_stable` / `_dramaQuiet` 三个字段**只写不读**（自"自动创建废除"起就是死状态），
///      连同三个只声明、无人使用的常量（`StableFramesNeeded` / `RetryCooldownFrames` /
///      `AliveCheckIntervalFrames`）一并删除。
///   ② **更要紧的**：`DramaActivityHook`（`WorldSystemMgr.OpenMapDrama` 的 postfix）**是个空操作**
///      —— 它唯一的作用是调 `NotifyDramaOpening()` 去清零那两个没人读的计数器。
///      也就是说，原设计的"⑤ 地图剧情期间静置"这道闸**从来没生效过**。
///      现在接成真的：`NotifyDramaOpening()` 记下帧号，`EvalGate` 在随后 `DramaQuietFramesNeeded`
///      帧内一律判"剧情中"。这比只按 UI 名判可靠——地图剧情未必叫 `UICustomDramaDyn`
///      （正是 `DramaActivityHook` 文件头点出的顾虑）。
/// </summary>
#if AB_UI
using System;
using UnityEngine;

namespace AgentLoopBridge
{
    public static class AbPanelProber
    {
        /// <summary>上次"剧情开启"的帧号（`DramaActivityHook` 经 `WorldSystemMgr.OpenMapDrama` 写入）。
        /// 初值取 `-DramaQuietFramesNeeded` ⇒ 静置窗天然失效，且不会与 `Time.frameCount` 相减溢出。</summary>
        private static int _dramaOpenedFrame = -DramaQuietFramesNeeded;

        /// <summary>地图剧情开启后要静置多久才算"世界稳了"（≈5~10s，见类头 ⑤）。</summary>
        private const int DramaQuietFramesNeeded = 300;

        /// <summary>剧情窗（游戏通用剧情 UI；NpcInitiativeMonitor 同款判定）</summary>
        private const string DramaUiName = "UICustomDramaDyn";

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

        /// <summary>用户主动触发路径的即时闸门（地图「传」按钮 / 对话窗 ⚙ / NPC 面板「AI 对话」）。</summary>
        public static bool CanCreateNow()
        {
            bool dramaOpen;
            return EvalGate(out dramaOpen);
        }

        /// <summary>剧情刚开启（`DramaActivityHook` 由 `WorldSystemMgr.OpenMapDrama` 回调）：
        /// 记下帧号，随后 `DramaQuietFramesNeeded` 帧内 `EvalGate` 一律判"剧情中"。</summary>
        public static void NotifyDramaOpening()
        {
            try { _dramaOpenedFrame = Time.frameCount; } catch { }
        }

        /// <summary>
        /// 闸门判定（全程 try 包裹防原生层异常）：
        ///   ① `CurrentGameDay() != -1`（能取到日历）
        ///   ② `g.world.playerUnit != null`（存档世界已加载）
        ///   ③ `g.ui.GetUI(UIType.MapMain) != null`（世界 HUD 已建 = 世界真正就绪）
        ///   ④ 当前没有剧情窗（`UICustomDramaDyn` 不存在）
        ///   ⑤ 距上次"地图剧情开启"≥ `DramaQuietFramesNeeded` 帧
        /// 任一条不满足就不 OpenUI。`dramaOpen` 单独回传。
        /// </summary>
        private static bool EvalGate(out bool dramaOpen)
        {
            dramaOpen = false;
            bool dayOk = false, unitOk = false, mapOk = false;
            try
            {
                dayOk = NpcInitiativeMonitor.CurrentGameDay() != -1;
                if (dayOk) unitOk = g.world.playerUnit != null;
            }
            catch { }
            bool gate = false;
            if (dayOk && unitOk)
            {
                // 世界 HUD 已建 = 世界真正就绪（09-10：光有 playerUnit 不够，
                // 世界进入期（WorldMgr:EnterWorldFixData/OpenMapDrama 阶段）即已非 null）
                try { mapOk = g.ui.GetUI(UIType.MapMain) != null; } catch { }
                if (mapOk)
                {
                    // ④ 剧情窗展示中 → 绝不 OpenUI（事故根因）
                    try
                    {
                        if (g.ui.GetUI(new UIType.UITypeBase(DramaUiName, (UILayer)0)) != null)
                            dramaOpen = true;
                    }
                    catch { }
                    // ⑤ 地图剧情刚开启的静置窗（见类头）：
                    //    地图剧情未必叫 UICustomDramaDyn，故用 OpenMapDrama 钩子兜住这 300 帧。
                    try
                    {
                        if (Time.frameCount - _dramaOpenedFrame < DramaQuietFramesNeeded)
                            dramaOpen = true;
                    }
                    catch { }
                    gate = !dramaOpen;
                }
            }
            return gate;
        }

        /// <summary>ModMain.Destroy 复位：重新 Init 后重新探测（宿主随场景销毁，
        /// 面板内验活检查会自动重建）。</summary>
        public static void Reset()
        {
            _dramaOpenedFrame = -DramaQuietFramesNeeded;
            _lazyCleanupDone = false;
        }
    }
}
#endif // AB_UI
