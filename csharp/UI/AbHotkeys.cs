/// <summary>
/// 常驻帧回调（世界输入失效事故修复的配套件）。
///
/// 背景：通讯录/配置两个常驻宿主的**根节点在关闭态是 OFF 的**（防游戏把"UI 层顶部有一个
/// 打开中的界面"当成模态 → 屏蔽世界输入：角色不能动、点不开 NPC）。代价是宿主上的
/// `ContactPresenter.Update` / `ConfigPresenter.Update` 在关闭时不再运行——原来挂在它们
/// 里面的 Python 重启 tick 会失效，故统一搬到本类，由 ModMain 的 `g.timer.Frame`
/// 每帧回调驱动（常驻、且不依赖任何面板的激活状态）。
///
/// ：**全部 F 键热键已删除**
/// 删除原因不是"键位不好"，而是它们**根本按不动**——本类注册的帧回调会被挂两次
/// （见下方「双重注册」注释），于是同一次 F11 按下触发两次 `Toggle()`，开→关，
/// 看起来完全没反应。三个面板现在都只走界面入口：
///   · 配置面板 → 对话窗右上角 ⚙（`ChatPresenter.ConfigClicked` / `AbChatPanel`）
///   · 通讯录   → 大地图 HUD 的「传」按钮（`MapMainContactButton`）
///   · 对话窗   → NPC 面板「AI 对话」/ 通讯录行点击 / 剧情 AI 选项（`ChatLauncher`）
/// 抢救工具（清引导 / 关隐形模态 / 点确认按钮）的方法体全部**保留**，只是不再绑键位——
/// 需要时在 ModMain 里挂一个调用即可，不必重写。
///
/// 晚：**诊断快照全部退役**（清理"确认完没删"的探针）
/// 删掉 `WorldInputSnapshot` / `TipUiPresent` / `PendingTipCount` / `DumpUiTree` / `DumpNode` /
/// `DumpLayers` / `DescribeTopUi`（合计 ~220 行）——它们服务的 09-10/09-11 事故都已定案，
/// 且唯一调用方（`AbPanelProber` 的周期诊断）也一并退役，留着只是纯死重。
/// 现在本类只剩 `OnFrame`（活的）+ 四个**抢救工具**（见下，无调用方但保留作现场手册）。
///
/// 【双重注册】`Destroy()` 里没有任何"注销帧回调"的代码（原注释的判断是"帧回调随场景销毁"），
/// 而 `g.timer` 是**游戏全局**对象、不随场景销毁 ⇒「回主界面 → 重进世界」一次就多挂一份。
/// 上一局日志实证：`UI 已初始化` ×2、`Destroy ok` ×1，且所有注册项都 ×2。
/// 症状有选择性——纯打印型的键（F12/F2/F8 的快照）只是打两份、看起来正常，
/// 只有 **Toggle 型（F9/F10/F11）会自己抵消**，这就是"按了没反应"的全部原因。
/// </summary>
#if AB_UI
using System;
using UnityEngine;

namespace AgentLoopBridge
{
    public static class AbHotkeys
    {
        /// <summary>Frame 每帧回调（主线程）：配置面板的重启状态机 + 快捷键闸门的诊断心跳。</summary>
        public static void OnFrame()
        {
            // Python 重启状态机必须继续推进：宿主根在关闭态是 OFF 的，
            // ConfigPresenter.Update 不再运行，故在此承接。**这不是热键，勿删。**
            try { AbConfigPanel.Tick(); }
            catch (Exception e) { ModMain.P("[AbHotkeys] tick: " + e.Message); }

            // 晚 `MapWorldMgr.isEnableMap` 的逐帧采样（游戏自己那道"大地图是否可用"的闸）。
            // **必须无条件每帧跑**：要抓的正是"过剧情/进战斗时游戏自己把这道闸关掉"那一刻，
            // 而那时我们的界面是**关着**的，挂在"面板没开就不干活"的诊断里永远看不到。
            // 内部自带 10 帧节流 + 仅在值翻转时打一行，常态零日志；它是这条修复的长期金丝雀。
            try { FastKeyGate.Sample(); }
            catch { }
        }

        /// <summary>
        /// 【抢救工具 · 无调用方，保留作现场手册】清空"待处理新提示"
        /// （`g.data.world.newTip.allNewTip`）并关闭 StartGameTip 界面。
        /// 背景：游戏的引导/提示以"待处理列表"形式存在存档世界数据里（DataWorld.World.NewTipData
        /// .allNewTip，分组 → GroupData.openNewTip）。只要不被"完成"，每次进世界都会重弹并全屏
        /// 挡住输入（症状=角色不能动、ESC 无效）。清空该表后不再弹；如需彻底生效请随后存盘。
        /// </summary>
        public static void ClearPendingTips()
        {
            try
            {
                int before = -1, after = -1;
                try
                {
                    var nt = g.data.world.newTip;
                    if (nt != null)
                    {
                        var dict = nt.allNewTip;
                        if (dict != null)
                        {
                            before = dict.Count;
                            dict.Clear();
                            after = dict.Count;
                        }
                        else ModMain.P("[Repair] newTip.allNewTip 为 null");
                    }
                    else ModMain.P("[Repair] g.data.world.newTip 为 null");
                }
                catch (Exception e1) { ModMain.P("[Repair] 清 allNewTip 失败: " + e1.Message); }

                // 关掉已经弹出的引导界面（数据清了，界面也要收掉）
                try { g.ui.CloseUI(new UIType.UITypeBase(StartTipUiName, (UILayer)0), true); } catch { }
                // 关键：清数据 + 关界面 都**不足以**解除"世界暂停/等待确认"（实测清完仍不能动）。
                // 再走一次 CloseAllUI（保留地图）——游戏是在"没有其它 UI 开着"时才恢复世界输入。
                bool unblocked = CloseAllButMapSafe();

                ModMain.P("[Repair] 已清理待处理引导：allNewTip " + before + " → " + after +
                          "，关闭 " + StartTipUiName + "，解除流程 ok=" + unblocked +
                          "。请试走动；能动就立刻存档");
            }
            catch (Exception e) { ModMain.P("[Repair] ClearPendingTips: " + e.Message); }
        }

        /// <summary>开局引导界面（游戏自带 UIStartGameTip；实测卡在最上层全屏挡输入）</summary>
        public const string StartTipUiName = "StartGameTip";

        /// <summary>
        /// 【抢救工具 · 无调用方，保留作现场手册】
        /// 替玩家点掉指定 UI 里的确认按钮（优先文字含"知道/确定/下一步/开始/完成/关闭"的按钮）。
        /// 关键：**关闭界面 ≠ 完成引导**——只有真正触发按钮回调，游戏才会记录"引导已完成"，
        /// 否则下次行动会再次弹出（这就是"动一下又卡"的循环成因）。
        /// </summary>
        public static void ClickUiConfirmButton(string uiName)
        {
            try
            {
                var ui = g.ui.GetUI(new UIType.UITypeBase(uiName, (UILayer)0));
                if (ui == null) { ModMain.P("[Repair] 未找到 UI：" + uiName); return; }
                _findBest = null; _findBestPath = null; _findBestPrefer = false;
                FindConfirmButton(ui.transform, 0);
                if (_findBest == null)
                {
                    ModMain.P("[Repair] " + uiName + " 内未找到可点的确认按钮，请先按 F4 看结构");
                    return;
                }
                ModMain.P("[Repair] 点击 " + uiName + " 的按钮：" + _findBestPath);
                _findBest.onClick.Invoke();
                ModMain.P("[Repair] 已触发点击（该按钮的回调应会把引导标记为已完成）");
            }
            catch (Exception e) { ModMain.P("[Repair] ClickUiConfirmButton: " + e.Message); }
        }

        private static readonly string[] ConfirmWords =
            { "知道", "确定", "下一步", "开始", "完成", "关闭", "明白", "好的", "继续" };

        private static UnityEngine.UI.Button _findBest;
        private static string _findBestPath;
        private static bool _findBestPrefer;

        private static void FindConfirmButton(Transform t, int depth)
        {
            if (t == null || depth > 8) return;
            bool active = false;
            try { active = t.gameObject.activeInHierarchy; } catch { }
            if (active)
            {
                try
                {
                    var btn = t.GetComponent<UnityEngine.UI.Button>();
                    if (btn != null && btn.interactable)
                    {
                        string label = "";
                        try
                        {
                            var txt = t.GetComponentInChildren<UnityEngine.UI.Text>(true);
                            if (txt != null) label = txt.text ?? "";
                        }
                        catch { }
                        bool prefer = false;
                        for (int i = 0; i < ConfirmWords.Length; i++)
                            if (label.IndexOf(ConfirmWords[i], StringComparison.Ordinal) >= 0) { prefer = true; break; }
                        // 关键字命中的按钮优先；否则记住第一个找到的
                        if (_findBest == null || (prefer && !_findBestPrefer))
                        {
                            _findBest = btn;
                            _findBestPath = PathOf(t) + (prefer ? " (文字:" + label + ")" : " (首个可点按钮)");
                            _findBestPrefer = prefer;
                        }
                    }
                }
                catch { }
            }
            int n = 0;
            try { n = t.childCount; } catch { return; }
            for (int i = 0; i < n; i++)
            {
                Transform c = null;
                try { c = t.GetChild(i); } catch { continue; }
                FindConfirmButton(c, depth + 1);
            }
        }

        private static string PathOf(Transform t)
        {
            try
            {
                string p = t.name;
                Transform cur = t.parent;
                for (int i = 0; i < 6 && cur != null; i++) { p = cur.name + "/" + p; cur = cur.parent; }
                return p;
            }
            catch { return "?"; }
        }

        /// <summary>
        /// 【抢救工具 · 无调用方，保留作现场手册】
        /// 强制关闭除地图主界面外的所有 UI（含隐形/卡死的模态窗）——用于抢救"读档后角色不能动、
        /// ESC 也无效"的存档（该状态= 某界面卡在最上层吃掉了输入；实测元凶常是游戏自己的
        /// 菜单界面 GameMemu 残留为"打开中"）。
        /// 关完请立刻手动存一次盘，把干净状态写回去。
        /// </summary>
        public static void CloseAllButMap()
        {
            try
            {
                bool ok = CloseAllButMapSafe();
                if (!ok)
                {
                    // 兜底：连 MapMain 也一起关（极端情况），随后游戏会自建 HUD
                    try { g.ui.CloseAllUI(null, true, false); ok = true; }
                    catch (Exception e2) { ModMain.P("[Repair] CloseAllUI 兜底也失败: " + e2.Message); }
                }
                ModMain.P("[Repair] 已执行 CloseAllUI（保留 MapMain）ok=" + ok + " ——请试走动；能动就立刻存档");
            }
            catch (Exception e) { ModMain.P("[Repair] CloseAllButMap: " + e.Message); }
        }

        /// <summary>只走"保留 MapMain"这一条路（失败不兜底，避免误关地图 HUD）——自动清理用。
        /// 【抢救工具 · 无调用方（`ClearPendingTips` 会调），保留作现场手册】</summary>
        public static bool CloseAllButMapSafe()
        {
            try
            {
                var keep = new UnhollowerBaseLib.Il2CppReferenceArray<UIType.UITypeBase>(1);
                keep[0] = UIType.MapMain;
                g.ui.CloseAllUI(keep, true, false);
                return true;
            }
            catch (Exception e)
            {
                ModMain.P("[Repair] CloseAllUI(保留 MapMain) 失败: " + e.Message);
                return false;
            }
        }

    }
}
#endif // AB_UI

