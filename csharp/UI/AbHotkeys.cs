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
///
/// ★ 09-24 新增：通讯录快捷键 **C**（`TickContactHotkey`）★
/// 它不是"F 键复活"—— 键位、判据、帧去重、日志四样都齐：
///   · 键位 C：不在游戏 `DefaultKeys.json` 任何一条里（见方法注释的排查表）；
///   · 判据：`HotkeyCore.Decide` 四条（我们的界面 / 输入框 / 战斗中 / isEnableMap=False）；
///   · **帧去重**：正是对着上面那条教训写的（重复注册的回调里，同帧第二份必须跳过）；
///   · **只开不关**：走 `ContactPanelOpener.RequestOpen`（已开着时不响应，不误关）。
/// 判定核在 `UI/HotkeyCore.cs`（零 Unity 依赖、可单测：`csharp/tests/HotkeyCoreTests`）。
/// </summary>
#if AB_UI
using System;
using UnityEngine;

namespace AgentLoopBridge
{
    public static class AbHotkeys
    {
        /// <summary>Frame 每帧回调（主线程）：通讯录快捷键（C）+ 配置面板的重启状态机
        /// + 立绘读盘泵 + 快捷键闸门心跳。</summary>
        public static void OnFrame()
        {
            // 耗时采样（TickProfiler，09-28 卡顿定位）：本回调是"每帧 5 件事"的聚合体；
            // 子项各自带 `hotkeys.` 前缀单独统计，父项 `hotkeys` 的 max 含子项。
            long __tpAll = TickProfiler.Begin();
            try
            {
                // ★ 通讯录快捷键（C）（09-24 用户需求）★
                // 放在最前：输入类响应越早越跟手；未按下时内部一行 GetKeyDown 就早退（常态零开销）。
                long __tp = TickProfiler.Begin();
                try { TickContactHotkey(); }
                catch (Exception e) { ModMain.P("[Hotkey] tick: " + e.Message); }
                finally { TickProfiler.End("hotkeys.contact", __tp); }

                // Python 重启状态机必须继续推进：宿主根在关闭态是 OFF 的，
                // ConfigPresenter.Update 不再运行，故在此承接。**这不是热键，勿删。**
                __tp = TickProfiler.Begin();
                try { AbConfigPanel.Tick(); }
                catch (Exception e) { ModMain.P("[AbHotkeys] tick: " + e.Message); }
                finally { TickProfiler.End("hotkeys.cfg", __tp); }

                // ★ 待开重试（09-20「点传按钮没反应、要点好几遍」修复，勿删）★
                // 玩家点「传」时若闸门未开，那次点击会被**挂起**而不是丢弃；本回调每帧重试，
                // 闸门一开就自动弹出（5s 超时后如实告知原因）。
                // **必须常驻**——该意图的存在意义正是"面板还不存在"，任何挂在面板上的
                // tick（如 `ContactPresenter.Update`）在那一刻都跑不到。
                __tp = TickProfiler.Begin();
                try { ContactPanelOpener.TickPending(); }
                catch (Exception e) { ModMain.P("[AbHotkeys] TickPending: " + e.Message); }
                finally { TickProfiler.End("hotkeys.pending", __tp); }

                // ★ 立绘读盘泵（09-19 修复，勿删）★
                // `ContactPresenter.Update` 里也有一份，但**面板关闭时那行根本不跑**（宿主根 OFF，
                // 见类头）；而待读的 id 是**常驻层**（`ContactDuty` 的 list_contacts 回包 →
                // `OnStoreChanged`）塞进来的 —— 面板关着时照样在塞。泵不在常驻回调里跑，
                // 那些 id 就一直躺在 `_loadQueue` 里，直到玩家手动开面板才被处理。
                // 放在这里 = 与面板激活状态解耦（`RowAvatars` 已为对话窗记过同款教训：
                // "`PumpLoad` 原先只挂在 ContactPresenter.Update 上……头像一直不出现，且零报错"）。
                // 内部自带限流（每帧 2 张）与空队列早退，常态零开销。
                __tp = TickProfiler.Begin();
                try { PortraitCache.PumpLoad(); }
                catch { }
                finally { TickProfiler.End("hotkeys.pump", __tp); }

                // ★ 放大立绘的关闭键（10-18）★
                // 浮层/原生窗开着时判一次 ESC；**关着时 `IsOpen` 两次引用比较就早退**，
                // 不碰任何 Unity 输入 API（与本回调其它常驻项的"常态零开销"同规矩）。
                // 必须挂在**常驻**回调：浮层挂在游戏的根 Canvas 下，对话窗关掉后
                // `ChatPresenter.Update` 那条路根本不跑（`AbHotkeys` 类头记过两次同款教训）。
                __tp = TickProfiler.Begin();
                try { BigPortrait.Tick(); }
                catch { }
                finally { TickProfiler.End("hotkeys.bigportrait", __tp); }

                // 晚 `MapWorldMgr.isEnableMap` 的逐帧采样（游戏自己那道"大地图是否可用"的闸）。
                // **必须无条件每帧跑**：要抓的正是"过剧情/进战斗时游戏自己把这道闸关掉"那一刻，
                // 而那时我们的界面是**关着**的，挂在"面板没开就不干活"的诊断里永远看不到。
                // 内部自带 10 帧节流 + 仅在值翻转时打一行，常态零日志；它是这条修复的长期金丝雀。
                __tp = TickProfiler.Begin();
                try { FastKeyGate.Sample(); }
                catch { }
                finally { TickProfiler.End("hotkeys.fastkey", __tp); }

                // ★ 「须知」面板的 ESC 与自动弹出（10-23）★
                // 必须挂在这条**常驻**回调：须知是独立画布，不依赖任何面板的激活状态
                //（三个面板关闭时 `xxxPresenter.Update` 都不再运行，本类文件头记过两次同款教训）。
                // 常态开销：关着时两次 bool 比较 +（自动弹出未安排时）一个 return，零 GC。
                __tp = TickProfiler.Begin();
                try { if (!ModMain.Suspended) NoticePanel.Tick(); }
                catch (Exception e) { ModMain.P("[AbHotkeys] NoticePanel.Tick: " + e.Message); }
                finally { TickProfiler.End("hotkeys.notice", __tp); }
            }
            finally { TickProfiler.End("hotkeys", __tpAll); }
        }

        // ------------------------------------------------------------------
        // 通讯录快捷键（C）—— 09-24 用户需求
        // ------------------------------------------------------------------

        /// <summary>通讯录快捷键键位：**C**（用户 09-24 拍板）。改键位只改这一处；
        /// `tests/test_contact_hotkey_contract.py` 锁着它（防"改了键位却没人知道"）。
        ///
        /// 为什么是 C（游戏 `DefaultKeys.json` 全表 34 条逐条解码后的排查表）：
        ///   · **已占用**：W/S/A/D（移动）、鼠标左/右键、空格、Q、E、R、F、左 Shift、
        ///     1/2/3/4/5、I 人物属性、X 技能、B 背包、L 任务、H 信件、N 大事件、
        ///     M 小地图、O 技艺、Z 跳过本月、J 逆天改命、F 神器、T 仙法 —— **C 不在其中**；
        ///   · 语义顺（Contacts / 传音簿）、左手区、单键无修饰（与游戏快捷键形态一致）。
        /// 不推荐：Tab / 方向键（可能被 UI 当焦点导航）、F12（Steam 截图）、数字 6-0（不如字母明朗）。
        /// </summary>
        private const KeyCode ContactHotkey = KeyCode.C;

        /// <summary>上次已处理的按键帧号（帧去重的状态；-1 = 从未处理过）。
        /// 必须初值 -1 而不是 0：`Time.frameCount` 从 0 开始，用 0 会把游戏第 0 帧的按下吃掉。</summary>
        private static int _lastHotkeyFrame = -1;

        /// <summary>上次"按下但未触发"的原因（日志节流：**同一原因只打一次**，
        /// 免得在输入框里打字（按 c 是正常输入）或战斗中连按把日志刷满；
        /// 触发过（Fire）后再被拦时原因必然变化，会重新打一行）。</summary>
        private static HotkeyCore.Reason _lastHotkeyBlock = HotkeyCore.Reason.Fire;

        /// <summary>
        /// 通讯录快捷键（C）的接线：读输入 → 喂判据核 → 触发/日志。
        /// 判定规则全在 `HotkeyCore.Decide`（纯逻辑、可单测）；这里只做 Unity/工程的接线。
        ///
        /// 需求原文（09-24）三条，对应关系：
        ///   ① 不与游戏快捷键重叠 → 键位 C（见上）；
        ///   ② 与游戏快捷键行为一致：战斗中不触发 → `BattleActive()` + `FastKeyGate.MapEnabled()`；
        ///   ③ 自己的对话窗/配置/传音簿开着时不触发 → `HotkeyGate.OurPanelOpen()`
        ///      （顺带覆盖输入框聚焦 —— 打字时的字母键属于输入，不是快捷键）。
        /// </summary>
        private static void TickContactHotkey()
        {
            bool pressed;
            try { pressed = Input.GetKeyDown(ContactHotkey); }
            catch { return; }
            if (!pressed) return;      // 常态早退：不查任何判据（每帧只这一次 GetKeyDown）

            bool uiOpen = false, focused = false, mapOk = true, battle = false;
            try { uiOpen = HotkeyGate.OurPanelOpen(); } catch { }
            try { focused = HotkeyGate.InputFocused(); } catch { }
            try { mapOk = FastKeyGate.MapEnabled(); } catch { }
            try { battle = NpcInitiativeMonitor.BattleActive(); } catch { }

            HotkeyCore.Decision d = HotkeyCore.Decide(Time.frameCount, _lastHotkeyFrame, true,
                                                      uiOpen, focused, mapOk, battle);
            _lastHotkeyFrame = d.NewLastFrame;

            // 日志带上两个现场值：真机第一次实验就是靠它核对"战斗里 isEnableMap 到底是不是 False"
            string tail = "（isEnableMap=" + (mapOk ? "True" : "False")
                        + " battle=" + (battle ? "True" : "False") + "）";

            if (d.Fire)
            {
                _lastHotkeyBlock = HotkeyCore.Reason.Fire;
                ModMain.P("[Hotkey] 传音簿快捷键 C 命中" + tail);
                try { ContactPanelOpener.RequestOpen(); }
                catch (Exception e) { ModMain.P("[Hotkey] 打开传音簿失败: " + e.Message); }
                return;
            }

            if (d.Why == _lastHotkeyBlock) return;     // 同一原因不重复打（见字段注释）
            _lastHotkeyBlock = d.Why;
            ModMain.P("[Hotkey] 传音簿快捷键 C 按下但未触发：" + HotkeyCore.Describe(d.Why) + tail);
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

