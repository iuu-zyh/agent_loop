/// <summary>
/// NPC 面板「AI 对话」注入 —— Harmony Postfix on UINPCInfo.InitData。
///
/// 目标（用户需求）：在 NPC 面板操作列表里新增「AI 对话」，点击打开 AI 对话 UI 并绑定当前 NPC。
///
/// 结构依据（dump + 截图实证，路径固化）：
///   面板体 = NPCInfo 顶层 Image 中含 Group:UnitInfo 子节点的那个；
///   操作列 = 面板体/Group:UnitInfo/LanguageGroup/G:goGrid1（直接子级即操作行 G:goButtonN(Clone)，
///   文本“交谈”恒在）。**用户截图（21:49）证实页签带 + × 全在面板体内，× 紧贴页签带最右。**
///
/// 点击为何点不响：UIOperationGroup.UpdateHandleInput 按 operationItems 逐帧
/// 接管 G:goGrid1 区域输入；**且操作行模板的 Image 默认 `raycastTarget=false`（行内点击由组按条目
/// 接管，Image 自身不接）** → 克隆后 Image 同样不接 raycast → Unity EventSystem 跳过 → 透到下层
/// 页签/× 触发别的动作（截图实证：把按钮放 × 位置点不动，但点穿到 人物道心 切了类目）。
/// 解决 = **视觉与点击分离**：视觉行照旧克隆进操作列（位置/样式现成、用户认可），**另在面板体顶层
/// 挂一个透明点击代理**：透明 Image（`raycastTarget=true`，alpha=0 仍可被 GraphicRaycaster 命中）
/// + Button；用 `GetWorldCorners` + `InverseTransformPoint` 精确盖住视觉行的世界矩形——
/// 面板体层无组机制（截图/旧版假×克隆在该层都能点）。
///
/// 已否决方案（勿回退）：
///   ① 克隆 btnClose / “树内任意首 Button”当模板 → × 盖 ×（关闭失灵+按钮隐形）；
///   ② 按钮放右上角 × 左侧（CreateBarButton，按 × anchor 推坐标）→ 28c76a1d 实证：
///      坐标算到页签带尾部、压到 人物道心 之后、覆盖 ×；且 Image 不接 raycast → 点不响。
///
/// 实现要点：UINPCInfo.InitData Postfix → 操作行晚于 InitData 出现（约 1.5s 内）按 0.3/0.8/1.5/3.0s
/// 轮询 → 注入成功才记去重（轮询 guard 与登记同集合同时序，勿先登记后尝试）。
///
/// 切页签自愈（重做，勿回退）：
///   症状 = 点右侧页签（人物属性等）后我们两个按钮消失，只能关掉面板重开。
///   根因 = 克隆行挂在页签内容容器（Group:UnitInfo）里，切页签时游戏重建该页面组 → 原生行按模板重建、
///          我们的克隆行被销毁；而 09-11 挂的 `Method_Private_Void_Toggle_0` postfix **从未被调用**
///          （两个完整会话 Player.log 命中 0 次）→ 没人补注。
///   实证 = UINPCInfoBase 有 `tglTitle1..7` 七个 Toggle、UINPCInfo 有 `_Init_b__23_1..7` 七个 (bool)
///          lambda —— 页签切换走的是这些 Toggle 的 onValueChanged。
///   修复 = ①`WireTabToggles` 在 Unity 层直接给七个页签 Toggle 挂 onValueChanged 监听（不依赖游戏
///          私有方法名）；②`OnPageSwitched` 幂等补注两个按钮 + 0.4/1.0/2.0s 三道**有界**验证；
///          ③补注目标改为 `ResolveOperationGrid`＝**当前激活页签**的操作列（按钮跟着页签一直可见，
///          其它页签没有「交谈」行时退化为克隆该网格第一条带文字的行）。
///   仍守用户定调：**无常驻轮询**，全部动作由真实事件驱动、重试次数封顶。
/// </summary>
using System;
using System.Collections.Generic;
using HarmonyLib;
using UnityEngine;
using UnityEngine.Events;
using UnityEngine.UI;

namespace AgentLoopBridge
{
    [HarmonyPatch(typeof(UINPCInfo), nameof(UINPCInfo.InitData))]
    internal static class NpcPanelButton
    {
        // internal：NpcPanelToggleHook / NpcPanelAddContact 复用（行名与注入集合需跨类读取）
        internal const string ButtonName = "AgentLoopChatButton";
        private const string JiaoTanText = "交谈";

        // 固化路径段（自 UINPCInfo 根向下逐级“直接子级按名”）
        private const string SegBodyImage = "Image";          // 顶层有多个 Image，取含 Group:UnitInfo 者
        private const string SegUnitInfo = "Group:UnitInfo";
        private const string SegLanguage = "LanguageGroup";
        private const string SegGrid = "G:goGrid1";

        /// <summary>首轮失败后的轮询间隔（s）。操作行晚建约 1.5s 内。</summary>
        private static readonly float[] RetryDelays = { 0.3f, 0.8f, 1.5f, 3.0f };

        // 同一面板实例只注入一次；注入成功才入集（面板随关闭销毁，新面板是新实例）。
        // internal：NpcPanelToggleHook 用它判断"只管注入成功过的面板"
        internal static readonly HashSet<UINPCInfo> Injected = new HashSet<UINPCInfo>();

        /// <summary>最后一次成功注入的面板（F8 诊断用；面板销毁后访问会抛，调用处 try 包死）。</summary>
        private static UINPCInfo _lastPanel;

        /// <summary>已挂页签监听的实例（去重）。</summary>
        private static readonly HashSet<UINPCInfo> TabWired = new HashSet<UINPCInfo>();

        /// <summary>页签回调委托引用（UnityEvent 只持原生侧引用，托管侧不留引用会被 GC 掉）。</summary>
        private static readonly List<UnityAction<bool>> TabCallbacks = new List<UnityAction<bool>>();

        /// <summary>页签切换事件计数（从"只打首次"改成**计数**）：
        /// 真机日志里出现 32 次「行缺失 → 已注入 → 又行缺失」的等距循环（28 行/周期，占全会话日志 23%），
        /// 而唯一的驱动入口 `OnPageSwitched` 只打一行"首次"——**重复次数被这个一次性开关藏住了**，
        /// 于是分不清是"游戏反复重置页签 Toggle"还是"我们自己重入"。计数一行就能分辨。</summary>
        private static int _pageSwitchCount;

        // --------------------------------------------------------------------------------------------
        // 补注风暴的抑制与存活判据
        //
        // 真机现象：一次开面板触发 32 次「行缺失 → 已注入」，等距 28 行/周期，占全会话日志 23%
        // （≈900 行）。逐条对齐后的事实：
        //   · 每次检查时**另一个行总是不在**，且刚注入完的那一行在**下一次检查**（0..0/2.0s 档）
        //     就已经被游戏清掉了 —— 即游戏的建场会连着吃掉好几轮，不是我们重入；
        //   · 每轮 6 行日志（行缺失/操作行命中/去挂件×2/视觉行就绪/捕捉层/已注入）把这个放大成灾。
        // 对策两条：
        //   ① `BurstInjections` 计数：同一次"行重新出现"之间的连续补注，**第二次起静音**——
        //      只留「行缺失（带存活帧数）」与一行「已注入」，6 行压到 2 行；
        //   ② `LastInjectFrame`：把"上次注入到现在活了几帧"打进日志。1~2 帧 = 游戏每帧重建网格；
        //      几十帧 = 周期性重建。**这是判定该不该换个挂点（挂出网格）的唯一判据**，比猜准。
        // --------------------------------------------------------------------------------------------

        /// <summary>本轮连续补注次数（检查到行又在 → 归零）</summary>
        private static int _burstInjections;

        /// <summary>第二次起的补注是否静音（由 `_burstInjections` 驱动）</summary>
        private static bool _quietInject;

        /// <summary>上次注入成功的帧号（-1=从未）——"存活 N 帧"判据</summary>
        private static int _lastInjectFrame = -1;

        /// <summary>静音期内的日志闸（`NpcPanelAddContact` 共用同一个注入器，故也认这个标记）</summary>
        internal static bool QuietInject => _quietInject;

        /// <summary>上次注入至今活了多少帧（判据文本）</summary>
        internal static string SurvivedFrames()
        {
            if (_lastInjectFrame < 0) return "从未";
            return (Time.frameCount - _lastInjectFrame) + " 帧前";
        }

        /// <summary>注入成功后记账（`TryInjectPanel` 与 `NpcPanelAddContact` 都调）</summary>
        internal static void NoteInjected()
        {
            _lastInjectFrame = Time.frameCount;
            _quietInject = true;
        }

        /// <summary>补注前的记账（两模块共用同一场风暴）：返回"本轮第几次"；第二次起静音。
        ///
        /// ⚠ 09-13 自审修：**复位不能挂在"自己的行在"上**——两模块的检查是交替的，而真机现象恰恰是
        /// "一行的在、另一行不在"（`现状=[10] 有AI对话 无加好友` ↔ `[9] 两个都不在`），
        /// 于是每个检查都把自己的行看到 → 复位 → 计数恒为 0 → **静音永远不生效**。
        /// 改成**按时间**判定：距上次注入超过 ~5 秒即视为新一场风暴（页签切换是秒级的，够用），
        /// 与"谁的行在"解耦。</summary>
        internal static int BeginInject()
        {
            if (_lastInjectFrame >= 0 && Time.frameCount - _lastInjectFrame > StormGapFrames)
            {
                _burstInjections = 0;
                _quietInject = false;
            }
            _quietInject = _burstInjections > 0;
            _burstInjections++;
            return _burstInjections;
        }

        /// <summary>两场风暴的间隔判据（帧）：60fps 下约 5 秒</summary>
        private const int StormGapFrames = 300;

        private static void Postfix(UINPCInfo __instance, WorldUnitBase unit, bool isOption)
        {
            try
            {
                if (__instance == null) return;
                string unitName = "?";
                try
                {
                    if (unit != null && unit.data != null && unit.data.unitData != null
                        && unit.data.unitData.propertyData != null)
                        unitName = unit.data.unitData.propertyData.GetName();
                }
                catch { }
                ModMain.P("[NpcPanelButton] InitData（unit=" + unitName + "，isOption=" + isOption + "）");
                if (!isOption)
                {
                    // 纯展示面板（无操作列）不注入 —— 但从剧情窗「查看」进来就是这个模式，
                    // 用户会问"为什么这里没有 AI 对话/加好友"。早退前留一份结构快照：
                    // 有没有操作列、列里有没有行、整面板有哪些可见带字的 Button（= 潜在模板）。
                    SnapshotDisplayPanel(__instance, unitName);
                    return;
                }
                if (Injected.Contains(__instance)) return;

                // 复核修 新面板首注走的是这条路（不经过 `BeginInject`），会**继承上一场的静音**：
                // 面板 A 的补注风暴把 `_quietInject` 置真后 5 秒内开关面板 B，B 的首次注入就没有
                // 「操作行命中/视觉行就绪/点击捕捉层/去挂件」四行（"已注入"/"行缺失"无闸仍在）。
                // 不是永久静音，但是"以后看不到注入日志"的一个真实窗口 —— 新面板一律复位。
                _burstInjections = 0;
                _quietInject = false;

                if (TryInjectPanel(__instance))
                {
                    Injected.Add(__instance);
                    // 首注成功也排一轮**有界**验证：兼作"页签监听补挂"重试（Toggle 可能晚于 InitData 就绪）
                    ScheduleVerify(__instance);
                    return;
                }
                ModMain.P("[NpcPanelButton] 首轮未命中（操作行晚建），0.3/0.8/1.5/3.0s 轮询补注...");
                ScheduleRetry(__instance, 0);
            }
            catch (Exception e)
            {
                ModMain.P("[NpcPanelButton] inject: " + e);
            }
        }

        /// <summary>轮询补注：注入成功/面板销毁/档位耗尽即停。</summary>
        private static void ScheduleRetry(UINPCInfo panel, int idx)
        {
            try
            {
                if (idx < 0 || idx >= RetryDelays.Length) return;
                float delay = RetryDelays[idx];
                g.timer.Time(new Action(() =>
                {
                    try
                    {
                        if (panel == null || panel.transform == null) return;
                        if (Injected.Contains(panel)) return;
                        ModMain.P("[NpcPanelButton] 轮询补注#" + (idx + 1) + "（+" + delay + "s）...");
                        if (TryInjectPanel(panel)) Injected.Add(panel);
                        else ScheduleRetry(panel, idx + 1);
                    }
                    catch (Exception e)
                    {
                        ModMain.P("[NpcPanelButton] 轮询终止: " + e.Message);
                    }
                }), delay, false);
            }
            catch (Exception e) { ModMain.P("[NpcPanelButton] 排轮询: " + e.Message); }
        }

        /// <summary>完整注入：视觉行克隆进网格首位（不可点击） + 行内透明点击捕捉层（可点击）。
        /// 网格 = **当前激活页签**的操作列（ResolveOperationGrid）——切页签后跟着走。</summary>
        private static bool TryInjectPanel(UINPCInfo panel)
        {
            try
            {
                if (panel == null || panel.transform == null) return false;
                Transform grid = ResolveOperationGrid(panel);
                if (grid == null)
                {
                    ModMain.P("[NpcPanelButton] 操作列未就绪（激活页签内未找到 G:goGrid1）");
                    return false;
                }
                // 幂等：行还在就不再注——切页签钩子/验证会重复调用本方法
                if (RowExists(grid, ButtonName) != null) { WireTabToggles(panel); return true; }
                GameObject visual = CreateVisualRow(panel.transform, ButtonName, "AI 对话", 0, grid);
                if (visual == null) return false;
                System.Action onClick = () => OpenChat(panel);
                AddClickCatcher(visual, onClick, ButtonName + "_Catcher");
                _lastPanel = panel;
                WireTabToggles(panel);   // 页签监听在首注成功时一次性挂上（幂等）
                ModMain.P("[NpcPanelButton] AI 对话视觉行已注入（父=" +
                          (visual.transform.parent != null ? visual.transform.parent.name : "?") +
                          "，网格路径=" + GetPath(grid) + "）");
                NoteInjected();   // 记账：存活帧数判据 + 后续补注静音
                return true;
            }
            catch (Exception e)
            {
                ModMain.P("[NpcPanelButton] 管线: " + e);
                return false;
            }
        }

        /// <summary>
        /// 行级幂等补注：行在 → true（无事）；行缺失 → 补注并返回注入结果；
        /// 网格未就绪/结构重建中 → false（调用方交验证兜底）。
        /// "面板是否归我们管"由调用方（NpcPanelToggleHook，用 Injected 集合）把关，此处不重复判。
        /// </summary>
        internal static bool EnsureRowFor(UINPCInfo panel)
        {
            try
            {
                if (panel == null) return true;
                Transform grid = ResolveOperationGrid(panel);
                if (grid == null) return false;
                if (RowExists(grid, ButtonName) != null)
                {
                    WireTabToggles(panel);
                    return true;   // 验证轮兼作监听补挂（风暴计数由 BeginInject 按时间复位，勿在此清）
                }
                int n = BeginInject();
                ModMain.P("[NpcPanelButton] AI 对话行缺失（网格=" + GetPath(grid) +
                          "，网格ID=" + GridId(grid) +
                          "，现状=" + DumpGrid(grid) + "），补注…（本轮第 " + n +
                          " 次，上次注入=" + SurvivedFrames() + "）");
                return TryInjectPanel(panel);
            }
            catch (Exception e)
            {
                ModMain.P("[NpcPanelButton] EnsureRowFor: " + e.Message);
                return false;
            }
        }

        /// <summary>
        /// 解析"当前该注入哪个操作列"（切页签自愈的关键）：
        /// ① 全树收集名为 G:goGrid1 的网格，取**在层级中激活**者（= 当前页签的操作列，优先有
        ///    LanguageGroup 祖先的老形态）；② 都不激活/找不到 → 回退老固化路径
        ///    （面板体/Group:UnitInfo/LanguageGroup/G:goGrid1）。找不到返回 null。
        /// 备注：不依赖页面容器名（Group:Property 等未实证），符合"结构变了也能活"。
        /// </summary>
        internal static Transform ResolveOperationGrid(UINPCInfo panel)
        {
            try
            {
                if (panel == null || panel.transform == null) return null;
                List<Transform> grids = new List<Transform>();
                CollectByName(panel.transform, SegGrid, grids, 0, 8);
                Transform active = null;
                for (int i = 0; i < grids.Count; i++)
                {
                    Transform g = grids[i];
                    if (g == null) continue;
                    bool on = false;
                    try { on = g.gameObject.activeInHierarchy; } catch { continue; }
                    if (!on) continue;
                    if (active == null) active = g;
                    if (HasAncestorNamed(g, SegLanguage)) { active = g; break; }   // 老形态优先
                }
                if (active != null) return active;
                return LocateGrid(panel.transform);
            }
            catch (Exception e)
            {
                ModMain.P("[NpcPanelButton] 定位操作列: " + e.Message);
                return null;
            }
        }

        /// <summary>递归收集名为 name 的后代（含未激活；深度上限防意外深树）。</summary>
        internal static void CollectByName(Transform root, string name, List<Transform> outList, int depth, int maxDepth)
        {
            try
            {
                if (root == null || outList == null || depth > maxDepth) return;
                int n = 0;
                try { n = root.childCount; } catch { return; }
                for (int i = 0; i < n; i++)
                {
                    Transform ch = null;
                    try { ch = root.GetChild(i); } catch { continue; }
                    if (ch == null) continue;
                    if (ch.name == name) outList.Add(ch);
                    CollectByName(ch, name, outList, depth + 1, maxDepth);
                }
            }
            catch { }
        }

        /// <summary>祖先链上有没有叫 name 的节点（限 8 层）。</summary>
        private static bool HasAncestorNamed(Transform t, string name)
        {
            try
            {
                Transform cur = t != null ? t.parent : null;
                int depth = 0;
                while (cur != null && depth < 8)
                {
                    if (cur.name == name) return true;
                    cur = cur.parent;
                    depth++;
                }
            }
            catch { }
            return false;
        }

        /// <summary>网格直接子级按名找行（含未激活——页面可能只是隐藏）。null=行不存在（被销毁）。</summary>
        internal static GameObject RowExists(Transform grid, string rowName)
        {
            try
            {
                if (grid == null) return null;
                int n = grid.childCount;
                for (int i = 0; i < n; i++)
                {
                    Transform ch = null;
                    try { ch = grid.GetChild(i); } catch { continue; }
                    if (ch != null && ch.name == rowName) return ch.gameObject;
                }
            }
            catch { }
            return null;
        }

        /// <summary>网格现状（名字「文本」一行竖列），行缺失时打进日志作实证（游戏到底怎么重建的）。</summary>
        internal static string DumpGrid(Transform grid)
        {
            try
            {
                if (grid == null) return "(无网格)";
                List<string> parts = new List<string>();
                int n = grid.childCount;                for (int i = 0; i < n; i++)
                {
                    Transform ch = null;
                    try { ch = grid.GetChild(i); } catch { continue; }
                    if (ch == null) continue;
                    string label = null;
                    try { var tx = ch.GetComponentInChildren<Text>(true); if (tx != null) label = tx.text; } catch { }
                    if (string.IsNullOrEmpty(label))
                    {
                        try { var tm = ch.GetComponentInChildren<TMPro.TextMeshProUGUI>(true); if (tm != null) label = tm.text; } catch { }
                    }
                    parts.Add(ch.name + (string.IsNullOrEmpty(label) ? "" : "「" + label + "」"));
                }
                return "[" + n + "] " + string.Join(" | ", parts.ToArray());
            }
            catch (Exception e) { return "(dump失败:" + e.Message + ")"; }
        }

        /// <summary>
        /// 切页签后的两道无条件验证（0.8s / 2.0s，**有界、无常驻轮询**——用户定调不预支开销）：
        /// 实证 InitData 路径交谈行先消失 ~0.3s 后才重建 → 游戏的清场可能晚于我们的即时补注，
        /// 这两道验证把"补上的行又被异步清掉"的窗口兜住。每次切页签固定 2 次检查，行缺失才补注。
        /// </summary>
        internal static void ScheduleVerify(UINPCInfo panel)
        {
            try
            {
                // ：切页签后新页签的操作列可能晚于回调出现（游戏异步重建页面组），
                // 三道**有界**验证兜住；仍是事件驱动、次数封顶，无常驻轮询。
                float[] delays = { 0.4f, 1.0f, 2.0f };
                foreach (float d in delays)
                {
                    g.timer.Time(new Action(() =>
                    {
                        try
                        {
                            if (panel == null || panel.transform == null) return;
                            EnsureRowFor(panel);
                        }
                        catch (Exception e) { ModMain.P("[NpcPanelButton] 验证: " + e.Message); }
                    }), d, false);
                }
            }
            catch (Exception e) { ModMain.P("[NpcPanelButton] 排验证: " + e.Message); }
        }

        /// <summary>
        /// 视觉行注入器（NpcPanelButton「AI 对话」与 NpcPanelAddContact「加好友」共用）：
        /// 克隆网格里的操作行 → 挂到 insertIndex → 去原行挂件（MarkItem/LED1）→ 深层清组机制组件
        /// → 文案双轨改写（Text + TextMeshPro）。返回已就绪行 GO（**视觉层不可点击——操作行模板
        /// Image 默认 raycastTarget=false，点击由透明代理承担**）。
        ///
        /// 模板行：优先老行为「交谈」行；其它页签没有交谈 → 退化为该网格里**第一条带文字的行**
        /// （不同页签的操作列样式各异，用本页签自己的行当模板才能保证样式一致）。
        /// </summary>
        internal static GameObject CreateVisualRow(Transform panelRoot, string buttonName, string label, int insertIndex)
        {
            return CreateVisualRow(panelRoot, buttonName, label, insertIndex, null);
        }

        /// <summary>同上，可指定目标网格（null = 按老固化路径定位）。</summary>
        internal static GameObject CreateVisualRow(Transform panelRoot, string buttonName, string label, int insertIndex, Transform grid)
        {
            try
            {
                if (panelRoot == null && grid == null) return null;
                Transform g = grid != null ? grid : LocateGrid(panelRoot);
                if (g == null)
                {
                    ModMain.P("[NpcPanelButton] 操作列未就绪（缺 Group:UnitInfo/LanguageGroup/G:goGrid1 之一）");
                    return null;
                }
                GameObject hitRow = FindTemplateRow(g);
                if (hitRow == null)
                {
                    ModMain.P("[NpcPanelButton] 操作列在但没有可克隆的行（网格=" + GetPath(g) +
                              "，现状=" + DumpGrid(g) + "）");
                    return null;
                }
                if (!QuietInject)
                    ModMain.P("[NpcPanelButton] 操作行命中（行=" + hitRow.name + "，路径=" + GetPath(hitRow.transform) + "）");

                GameObject gc = UnityEngine.Object.Instantiate(hitRow);
                gc.name = buttonName;
                var grt = gc.GetComponent<RectTransform>();
                if (grt == null) { ModMain.P("[NpcPanelButton] 行无 RectTransform，放弃"); return null; }
                grt.SetParent(g, false);
                gc.transform.SetSiblingIndex(insertIndex);
                grt.localScale = Vector3.one;
                RemoveRowExtras(gc);
                DeepStrip(gc);
                Text labelText = gc.GetComponentInChildren<Text>(true);
                if (labelText != null) labelText.text = label;
                var tmpLabel = gc.GetComponentInChildren<TMPro.TextMeshProUGUI>(true);
                if (tmpLabel != null) tmpLabel.text = label;
                bool hasLabel = labelText != null || tmpLabel != null;
                if (!QuietInject)
                    ModMain.P("[NpcPanelButton] 视觉行就绪（名=" + buttonName + "，文案=" + label +
                              "，插入位=" + insertIndex + "，有字=" + hasLabel + "，网格=" + GetPath(g) + "）");
                return gc;
            }
            catch (Exception e)
            {
                ModMain.P("[NpcPanelButton] 视觉行注入器: " + e);
                return null;
            }
        }

        /// <summary>模板行：①「交谈」行（老行为）②网格内第一条带文字、非我方注入的行。</summary>
        private static GameObject FindTemplateRow(Transform grid)
        {
            GameObject row = FindRowByTextDirect(grid, JiaoTanText);
            if (row != null) return row;
            try
            {
                int n = grid.childCount;
                for (int i = 0; i < n; i++)
                {
                    Transform ch = null;
                    try { ch = grid.GetChild(i); } catch { continue; }
                    if (ch == null) continue;
                    if (ch.name != null && ch.name.StartsWith("AgentLoop", StringComparison.Ordinal)) continue;
                    if (ch.GetComponent<RectTransform>() == null) continue;
                    if (HasLabel(ch.gameObject)) return ch.gameObject;
                }
            }
            catch (Exception e) { ModMain.P("[NpcPanelButton] 找模板行: " + e.Message); }
            return null;
        }

        /// <summary>行里有没有文字（Text 或 TextMeshPro，任意深度）。</summary>
        private static bool HasLabel(GameObject go)
        {
            try
            {
                if (go == null) return false;
                if (go.GetComponentInChildren<Text>(true) != null) return true;
                if (go.GetComponentInChildren<TMPro.TextMeshProUGUI>(true) != null) return true;
            }
            catch { }
            return false;
        }

        /// <summary>
        /// 行内点击捕捉层（替代 30f2b6db 的顶层坐标代理——世界坐标量测在该游戏 Canvas 缩放下
        /// 恒得 0 尺寸，代理从未建起，日志实证）：
        /// 在视觉行内部塞一个铺满整行的子节点：透明 Image（`raycastTarget=true`，alpha=0 仍可被
        /// GraphicRaycaster 命中）+ Button + 我们的 onClick。子节点跟随视觉行移动缩放，无需任何
        /// 坐标换算；行内点击原模板 `raycastTarget=false`（点不响根因），由捕捉层接收。
        /// </summary>
        internal static void AddClickCatcher(GameObject visualRow, System.Action onClick, string catcherName)
        {
            try
            {
                if (visualRow == null || onClick == null) return;
                RectTransform rowRt = visualRow.GetComponent<RectTransform>();
                if (rowRt == null) return;

                GameObject cc = new GameObject(catcherName);
                RectTransform crt = cc.AddComponent<RectTransform>();
                crt.SetParent(rowRt, false);
                crt.anchorMin = Vector2.zero;
                crt.anchorMax = Vector2.one;
                crt.offsetMin = new Vector2(-4f, -4f);   // 略外扩，防止行内子元素边缘漏点
                crt.offsetMax = new Vector2(4f, 4f);
                crt.localScale = Vector3.one;
                crt.SetAsLastSibling();                  // 盖住行内原有子元素（文字/背景）之上
                Image img = cc.AddComponent<Image>();
                img.color = new Color(1f, 1f, 1f, 0f);   // 全透明仍可被 GraphicRaycaster 命中
                img.raycastTarget = true;
                Button btn = cc.AddComponent<Button>();
                ClickUtils.Attach(btn, onClick);
                if (!QuietInject)
                    ModMain.P("[NpcPanelButton] 行内点击捕捉层已挂（名=" + catcherName +
                          "，行 rect=(" + rowRt.rect.width + "," + rowRt.rect.height + ")）");
            }
            catch (Exception e)
            {
                ModMain.P("[NpcPanelButton] 捕捉层: " + e.Message);
            }
        }

        /// <summary>去掉整行克隆体上原行的挂件（MarkItem/LED1：原交谈行的耗费图标与指示灯）</summary>
        private static void RemoveRowExtras(GameObject row)
        {
            try
            {
                if (row == null) return;
                string[] extras = { "MarkItem", "LED1" };
                Transform t = row.transform;
                int n = 0;
                try { n = t.childCount; } catch { return; }
                for (int i = n - 1; i >= 0; i--)
                {
                    Transform ch = null;
                    try { ch = t.GetChild(i); } catch { continue; }
                    if (ch == null) continue;
                    for (int k = 0; k < extras.Length; k++)
                    {
                        if (ch.name == extras[k])
                        {
                            if (!QuietInject)
                            ModMain.P("[NpcPanelButton] 去挂件：" + ch.name);
                            UnityEngine.Object.Destroy(ch.gameObject);
                            break;
                        }
                    }
                }
            }
            catch (Exception e) { ModMain.P("[NpcPanelButton] 去挂件: " + e.Message); }
        }

        /// <summary>深层 Strip：复数版 GetComponents 在此包疑似返回空，逐节点单点清才保险</summary>
        private static void DeepStrip(GameObject go)
        {
            try
            {
                if (go == null) return;
                StripOperationItem(go);
                Transform t = go.transform;
                int n = 0;
                try { n = t.childCount; } catch { return; }
                for (int i = 0; i < n; i++)
                {
                    Transform ch = null;
                    try { ch = t.GetChild(i); } catch { continue; }
                    if (ch == null) continue;
                    DeepStrip(ch.gameObject);
                }
            }
            catch { }
        }

        /// <summary>对外入口（`DramaAiOption` / `MapMainContactButton` 的克隆体都调它）</summary>
        internal static void StripOperationItemPublic(GameObject go) => StripOperationItem(go);

        /// <summary>移除 UIOperationItem 等组机制组件（保留 Button/Image/Text 视觉）。
        ///
        /// 复核修 旧写法用 `c.GetType.Name` 判类型，而它在 IL2CPP 下**恒为声明类型 `Component`**
        /// （`GetComponents&lt;Component&gt;()` 返回的是按 T 包装的代理）——于是那个 if **永不成立、
        /// 整个 foreach 是死代码**：本方法自诞生起就没删掉过任何东西。改成 `GetIl2CppType().Name`
        /// （`MapMainContactButton.TypeName` 同款）。**这是行为变更**：现在会真的执行删除，
        /// 若出现"可见但点不响"，第一嫌疑就是这里——保留字符串重载作兜底，并**删一条打一行**留痕，
        /// 免得又变成"做了什么没人知道"。</summary>
        private static void StripOperationItem(GameObject go)
        {
            try
            {
                var comps = go.GetComponents<Component>();
                for (int i = 0; i < comps.Length; i++)   // 索引循环（与其余文件统一，不 foreach 互操作数组）
                {
                    var c = comps[i];
                    if (c == null) continue;
                    string n = RealTypeName(c);
                    if (n == "UIOperationItem" || n.Contains("OperationItem") || n == "UIEventListener")
                    {
                        ModMain.P("[NpcPanelButton] 去组机制组件：" + n + "（" + go.name + "）");
                        UnityEngine.Object.DestroyImmediate(c);
                    }
                }
                var opItem = go.GetComponent("UIOperationItem");
                if (opItem != null) UnityEngine.Object.DestroyImmediate(opItem);
                var listener = go.GetComponent("UIEventListener");
                if (listener != null) UnityEngine.Object.DestroyImmediate(listener);
            }
            catch (Exception e) { ModMain.P("[NpcPanelButton] strip: " + e.Message); }
        }

        /// <summary>运行时类型名（`GetIl2CppType().Name` 优先；不可用时退声明类型名）</summary>
        private static string RealTypeName(Component c)
        {
            try
            {
                var t = c.GetIl2CppType();
                if (t != null && !string.IsNullOrEmpty(t.Name)) return t.Name;
            }
            catch { }
            try { return c.GetType().Name; } catch { return "?"; }
        }

        /// <summary>在网格直接子级里找文本精确匹配的行（行文本可能藏在子孙里，用单数 GetComponentInChildren）</summary>
        private static GameObject FindRowByTextDirect(Transform grid, string text)
        {
            try
            {
                if (grid == null) return null;
                int n = grid.childCount;
                for (int i = 0; i < n; i++)
                {
                    Transform ch = null;
                    try { ch = grid.GetChild(i); } catch { continue; }
                    if (ch == null) continue;
                    string rowText = null;
                    try
                    {
                        var tx = ch.GetComponentInChildren<Text>(true);
                        if (tx != null) rowText = tx.text;
                        if (rowText == null)
                        {
                            var tm = ch.GetComponentInChildren<TMPro.TextMeshProUGUI>(true);
                            if (tm != null) rowText = tm.text;
                        }
                    }
                    catch { }
                    if (rowText == text) return ch.gameObject;
                }
            }
            catch (Exception e) { ModMain.P("[NpcPanelButton] 找行: " + e.Message); }
            return null;
        }

        /// <summary>只扫“直接子级”的按名查找（无递归）</summary>
        private static Transform DirectChildByName(Transform parent, string name)
        {
            try
            {
                if (parent == null || string.IsNullOrEmpty(name)) return null;
                int n = parent.childCount;
                for (int i = 0; i < n; i++)
                {
                    Transform c = null;
                    try { c = parent.GetChild(i); } catch { continue; }
                    if (c != null && c.name == name) return c;
                }
            }
            catch { }
            return null;
        }

        /// <summary>定位面板体：顶层多个 Image，取“含 Group:UnitInfo 子节点”的那个。</summary>
        internal static Transform LocateBody(Transform root)
        {
            try
            {
                if (root == null) return null;
                int topN = root.childCount;
                for (int i = 0; i < topN; i++)
                {
                    Transform c = null;
                    try { c = root.GetChild(i); } catch { continue; }
                    if (c == null || c.name != SegBodyImage) continue;
                    if (DirectChildByName(c, SegUnitInfo) != null) return c;
                }
            }
            catch { }
            return null;
        }

        /// <summary>固化路径定位操作列：面板体 → Group:UnitInfo → LanguageGroup → G:goGrid1。</summary>
        internal static Transform LocateGrid(Transform root)
        {
            try
            {
                Transform body = LocateBody(root);
                if (body == null) return null;
                Transform ui = DirectChildByName(body, SegUnitInfo);
                if (ui == null) return null;
                Transform lg = DirectChildByName(ui, SegLanguage);
                if (lg == null) return null;
                return DirectChildByName(lg, SegGrid);
            }
            catch { return null; }
        }

        /// <summary>层级全路径（供日志与截图对照定位容器）</summary>
        /// <summary>网格的**实例 ID**（诊断用）——判定"网格被整个重建过"还是"只有我们的行被销毁"。
        ///
        /// 为什么需要它：`GetPath()` 打的是**路径**，而网格重建后路径**一模一样**，
        /// 光看路径永远分不出这两种情况。这正是当前"两个按钮从未同时存在"风暴的关键分叉：
        ///   · **ID 变了** → 游戏重建了整个网格（我们的克隆行被连带清掉）→
        ///     那么"往网格里塞克隆行"这个方案**先天不成立**，因为每次塞都会被下一次重建抹掉；
        ///   · **ID 没变** → 网格还是同一个对象，那是**有人单独销毁了我们的行**，得另找凶手。
        /// 判出来才知道该修哪边——不加这一行就只能在两种解释之间猜。</summary>
        internal static int GridId(Transform grid)
        {
            try { return grid != null ? grid.GetInstanceID() : 0; } catch { return 0; }
        }

        internal static string GetPath(Transform t)
        {
            try
            {
                if (t == null) return "-";
                string p = t.name;
                Transform cur = null;
                try { cur = t.parent; } catch { return p; }
                int depth = 0;
                while (cur != null && depth < 12)
                {
                    p = cur.name + "/" + p;
                    try { cur = cur.parent; } catch { break; }
                    depth++;
                }
                return p;
            }
            catch { return "?"; }
        }

        // ============================================================================================
        // 页签切换自愈（重做）——上一版挂在 UINPCInfo.Method_Private_Void_Toggle_0 上，
        // 但两个完整会话的 Player.log 里该 postfix **命中 0 次**（实证：页签点击根本不走它）。
        // interop 实证：UINPCInfoBase 有 tglTitle1..7 七个 Toggle 属性，UINPCInfo 有
        // _Init_b__23_1..7 七个 (bool) lambda —— 页签切换走的是这七个 onValueChanged 回调。
        // 本版改为**在 Unity 层直接监听七个页签 Toggle 的 onValueChanged**（不依赖游戏私有方法名，
        // 事件驱动、无常驻轮询），命中后把两个按钮补注到**当前激活页签**的操作列。
        // ============================================================================================

        /// <summary>页签 Toggle 全部接线（幂等；注入成功时调用）。</summary>
        internal static void WireTabToggles(UINPCInfo panel)
        {
            try
            {
                if (panel == null) return;
                if (TabWired.Contains(panel)) return;
                List<Toggle> toggles = TabTogglesOf(panel);
                if (toggles.Count == 0)
                {
                    ModMain.P("[NpcPanelButton] 页签 Toggle 未找到（监听未挂，切页签只能靠轮询补注兜底）");
                    return;
                }
                int ok = 0;
                for (int i = 0; i < toggles.Count; i++)
                {
                    Toggle t = toggles[i];
                    if (t == null) continue;
                    try
                    {
                        UINPCInfo captured = panel;
                        System.Action<bool> a = (bool isOn) =>
                        {
                            try
                            {
                                if (!isOn) return;          // 只在"变为选中"时补注（同组其它 Toggle 置 false 时也回调）
                                OnPageSwitched(captured, "页签 onValueChanged");
                            }
                            catch (Exception e) { ModMain.P("[NpcPanelButton] 页签回调: " + e.Message); }
                        };
                        UnityAction<bool> cb = a;           // MELONLOADER：隐式 op_Implicit 转 UnityAction<bool>
                        t.onValueChanged.AddListener(cb);
                        TabCallbacks.Add(cb);               // 防 GC：UnityEvent 只持原生侧引用
                        ok++;
                    }
                    catch (Exception e) { ModMain.P("[NpcPanelButton] 挂页签监听: " + e.Message); }
                }
                if (ok > 0)
                {
                    TabWired.Add(panel);
                    ModMain.P("[NpcPanelButton] 页签切换监听已挂（" + ok + " 个 Toggle）");
                }
            }
            catch (Exception e) { ModMain.P("[NpcPanelButton] WireTabToggles: " + e.Message); }
        }

        /// <summary>取七个页签 Toggle：优先 UINPCInfoBase.tglTitle1..7 属性（interop 实证），失败退化按名搜索。</summary>
        private static List<Toggle> TabTogglesOf(UINPCInfo panel)
        {
            List<Toggle> res = new List<Toggle>();
            try
            {
                UINPCInfoBase b = null;
                try { b = panel as UINPCInfoBase; } catch { }
                if (b != null)
                {
                    Toggle[] ts = new Toggle[]
                    {
                        SafeGet(() => b.tglTitle1), SafeGet(() => b.tglTitle2), SafeGet(() => b.tglTitle3),
                        SafeGet(() => b.tglTitle4), SafeGet(() => b.tglTitle5), SafeGet(() => b.tglTitle6),
                        SafeGet(() => b.tglTitle7),
                    };
                    for (int i = 0; i < ts.Length; i++) if (ts[i] != null) res.Add(ts[i]);
                }
            }
            catch (Exception e) { ModMain.P("[NpcPanelButton] 读页签属性: " + e.Message); }
            if (res.Count > 0) return res;

            // 兜底：面板子树里名字以 tglTitle 开头的 Toggle
            try
            {
                var all = panel.GetComponentsInChildren<Toggle>(true);
                if (all != null)
                {
                    for (int i = 0; i < all.Length; i++)
                    {
                        var t = all[i];
                        if (t == null) continue;
                        string n = null;
                        try { n = t.gameObject.name; } catch { }
                        if (!string.IsNullOrEmpty(n) && n.StartsWith("tglTitle", StringComparison.Ordinal))
                            res.Add(t);
                    }
                }
            }
            catch (Exception e) { ModMain.P("[NpcPanelButton] 搜页签 Toggle: " + e.Message); }
            return res;
        }

        private static Toggle SafeGet(Func<Toggle> f)
        {
            try { return f(); } catch { return null; }
        }

        /// <summary>页签切换统一入口（Toggle 监听 / 老 postfix / 以后的其它钩子都汇到这里）：
        /// 只管注入成功过的面板；幂等补注两个按钮 + 三道有界验证。</summary>
        internal static void OnPageSwitched(UINPCInfo panel, string source)
        {
            try
            {
                if (panel == null) return;
                if (!Injected.Contains(panel)) return;      // 纯展示面板 / 没归我们管 → 无责
                // 每次调用都计数（前 5 次全打，之后每 20 次打一次，防真出病态循环时刷屏）
                _pageSwitchCount++;
                if (_pageSwitchCount <= 5 || _pageSwitchCount % 20 == 0)
                    ModMain.P("[NpcPanelButton] 页签切换事件 #" + _pageSwitchCount + "（来源=" + source + "）");
                bool chat = EnsureRowFor(panel);
                bool add = NpcPanelAddContact.EnsureRowFor(panel);
                if (!chat || !add)
                    ModMain.P("[NpcPanelButton] 切页签补注未全成（chat=" + chat + "，add=" + add +
                              "，来源=" + source + "），交有界验证兜底");
                ScheduleVerify(panel);
                NpcPanelAddContact.ScheduleVerify(panel);
            }
            catch (Exception e) { ModMain.P("[NpcPanelButton] OnPageSwitched: " + e.Message); }
        }

        // --------------------------------------------------------------------------------------------
        // F8 只读诊断：把 NPC 面板"页面容器 / 各操作列 / 我们两个行 / 七个页签状态"打进 Player.log。
        // 用途：切页签自愈若在某页签不灵，凭这份快照即可定位（哪个网格激活、行在不在、模板长什么样）。
        // 只读、不碰游戏状态；用户定调"只读诊断优先"。
        // --------------------------------------------------------------------------------------------
        internal static void DumpPanelForDiag()
        {
            try
            {
                UINPCInfo panel = _lastPanel;
                if (panel == null) { ModMain.P("[NpcPanelButton-Diag] 尚无已注入面板（先打开一次 NPC 面板）"); return; }
                Transform root = null;
                try { root = panel.transform; } catch { }
                if (root == null) { ModMain.P("[NpcPanelButton-Diag] 面板已销毁（GC 僵尸）"); return; }

                ModMain.P("[NpcPanelButton-Diag] ===== NPC 面板结构快照 =====");
                Transform body = LocateBody(root);
                ModMain.P("[NpcPanelButton-Diag] 面板体=" + (body == null ? "(未命中)" : GetPath(body)));
                if (body != null)
                {
                    int n = body.childCount;
                    for (int i = 0; i < n; i++)
                    {
                        Transform c = null;
                        try { c = body.GetChild(i); } catch { continue; }
                        if (c == null) continue;
                        ModMain.P("[NpcPanelButton-Diag]   子级[" + i + "] " + DescribeNode(c));
                    }
                }

                List<Transform> grids = new List<Transform>();
                CollectByName(root, SegGrid, grids, 0, 8);
                ModMain.P("[NpcPanelButton-Diag] 全树 G:goGrid1 命中 " + grids.Count + " 个：");
                for (int i = 0; i < grids.Count; i++)
                {
                    Transform gr = grids[i];
                    if (gr == null) continue;
                    ModMain.P("[NpcPanelButton-Diag]   [" + i + "] " + GetPath(gr) + " " + DescribeNode(gr) +
                              " 行数=" + SafeChildCount(gr) + " 行清单=" + DumpGrid(gr));
                }

                Transform active = ResolveOperationGrid(panel);
                ModMain.P("[NpcPanelButton-Diag] 解析出的目标操作列=" + (active == null ? "(无)" : GetPath(active)));
                ModMain.P("[NpcPanelButton-Diag] 我方行：AI 对话=" + DescribeOurRow(active, ButtonName) +
                          " ｜ 加好友=" + DescribeOurRow(active, NpcPanelAddContact.ButtonName));

                List<Toggle> toggles = TabTogglesOf(panel);
                var sb = new System.Text.StringBuilder();
                for (int i = 0; i < toggles.Count; i++)
                {
                    var t = toggles[i];
                    if (t == null) continue;
                    string nm = "?"; bool on = false;
                    try { nm = t.gameObject.name; } catch { }
                    try { on = t.isOn; } catch { }
                    sb.Append(nm).Append("=").Append(on ? "on" : "off").Append("  ");
                }
                ModMain.P("[NpcPanelButton-Diag] 页签 Toggle(" + toggles.Count + ")： " + sb);
                ModMain.P("[NpcPanelButton-Diag] ===== 快照结束 =====");
            }
            catch (Exception e) { ModMain.P("[NpcPanelButton-Diag] dump: " + e.Message); }
        }

        private static string DescribeOurRow(Transform grid, string rowName)
        {
            try
            {
                if (grid == null) return "(无网格)";
                GameObject go = RowExists(grid, rowName);
                if (go == null) return "缺失";
                string state = "?";
                try { state = go.activeInHierarchy ? "在(激活)" : "在(未激活)"; } catch { }
                return state + "@" + GetPath(go.transform);
            }
            catch { return "?"; }
        }

        private static string DescribeNode(Transform t)
        {
            try
            {
                if (t == null) return "(null)";
                string s = t.name + " ";
                try { s += t.gameObject.activeSelf ? "[on]" : "[off]"; } catch { }
                try { s += t.gameObject.activeInHierarchy ? "↑on" : "↑off"; } catch { }
                var rt = UiRects.Of(t);   // ★09-13★ 旧写法 `t as RectTransform` 恒 null（IL2CPP 铁律）
                if (rt != null) { try { s += " rect=" + rt.rect.width + "x" + rt.rect.height; } catch { } }
                return s;
            }
            catch { return "?"; }
        }

        /// <summary>已快照过的展示模式面板（按 **native 指针** 记，不用托管引用 —— 见 README 附录 D.1）。</summary>
        private static readonly HashSet<IntPtr> DisplaySnapshotted = new HashSet<IntPtr>();
        private const int MaxScanButtons = 30;

        /// <summary>
        /// 展示模式（`isOption=false`）面板的**一次性结构快照**。
        ///
        /// 为什么要它：两个注入器（`NpcPanelButton` / `NpcPanelAddContact`）都在 `!isOption` 处早退，
        /// 而早退前**什么都没记** —— 于是"从剧情窗「查看」进来为什么没有按钮"这个问题
        /// 在日志里完全无迹可查（用户提的就是这个）。快照回答两件事：
        ///   ① 这个模式下**有没有操作列**（`G:goGrid1`）、列里**有没有行**（注入靠克隆一行，没行就没模板）；
        ///   ② 整个面板还有哪些 Button —— 若要在展示模式另行注入，模板只能从这里挑
        ///      （且仍受 D.3 铁律约束：绝不用树内任意首 Button，`G:btnClose` 就是这么踩雷的）。
        /// </summary>
        private static void SnapshotDisplayPanel(UINPCInfo panel, string unitName)
        {
            try
            {
                if (panel == null) return;
                IntPtr ptr = IntPtr.Zero;
                try { ptr = panel.Pointer; } catch { }
                if (ptr != IntPtr.Zero)
                {
                    if (DisplaySnapshotted.Contains(ptr)) return;
                    DisplaySnapshotted.Add(ptr);
                }
                var grid = ResolveOperationGrid(panel);
                string gridInfo = grid == null
                    ? "无"
                    : (DescribeNode(grid) + " 行数=" + SafeChildCount(grid));
                var sb = new System.Text.StringBuilder("[NpcPanelButton] 展示模式面板快照：unit=")
                         .Append(unitName).Append(" 操作列=").Append(gridInfo).Append(" ｜ Button 清单：");
                int n = ScanButtons(panel.transform, sb, 0);
                if (n == 0) sb.Append("（无）");
                ModMain.P(sb.ToString());
            }
            catch { }
        }

        /// <summary>递归收集 Button（名 + 激活 + 有无文本 + 尺寸），供快照用。上限防刷屏。</summary>
        private static int ScanButtons(Transform t, System.Text.StringBuilder sb, int found)
        {
            if (t == null || found >= MaxScanButtons) return found;
            Button b = null;
            try { b = t.GetComponent<Button>(); } catch { }
            if (b != null)
            {
                if (found > 0) sb.Append(" ｜ ");
                sb.Append(DescribeNode(t));
                try { sb.Append(HasLabel(t.gameObject) ? "/有字" : "/无字"); } catch { }
                found++;
            }
            int n = SafeChildCount(t);
            for (int i = 0; i < n; i++)
            {
                found = ScanButtons(t.GetChild(i), sb, found);
                if (found >= MaxScanButtons) return found;
            }
            return found;
        }

        private static int SafeChildCount(Transform t)
        {
            try { return t != null ? t.childCount : 0; } catch { return -1; }
        }

        /// <summary>点击时现读面板当前 unit 并打开对话 UI（打开逻辑统一走 ChatLauncher）。
        /// 入口打点：点了代理但此处无日志 = 代理没接上/被更高层遮挡。</summary>
        private static void OpenChat(UINPCInfo panel)
        {
            try
            {
                WorldUnitBase unit = panel.unit;
                string unitName = "?";
                try
                {
                    if (unit != null && unit.data != null && unit.data.unitData != null
                        && unit.data.unitData.propertyData != null)
                        unitName = unit.data.unitData.propertyData.GetName();
                }
                catch { }
                ModMain.P("[NpcPanelButton] AI 对话被点击（unit=" + unitName + "），转 ChatLauncher");
                if (unit == null)
                {
                    ModMain.P("[NpcPanelButton] 面板 unit 为空，无法打开对话 UI");
                    return;
                }
                ChatLauncher.OpenForUnit(unit);
            }
            catch (Exception e)
            {
                ModMain.P("[NpcPanelButton] open chat: " + e);
            }
        }
    }

    /// <summary>
    /// 页签切换钩子（初版，降级为**备用通道**）。
    ///
    /// 初版假设：页签切换走私有处理器 `UINPCInfo.Method_Private_Void_Toggle_0(Toggle)`（interop 名）。
    /// **09-12 实证推翻**：两个完整会话的 Player.log 里本 postfix 命中 **0 次** —— 页签点击不走它
    /// （它是操作列/道具页的另一个 Toggle 处理器）。页签真正走的是 `UINPCInfoBase.tglTitle1..7`
    /// 七个 Toggle 的 `onValueChanged`（对应 `UINPCInfo._Init_b__23_1..7` 七个 (bool) lambda）。
    ///
    /// 现役修复 = `NpcPanelButton.WireTabToggles`（Unity 层直接监听七个页签 Toggle）+ 补注到
    /// **当前激活页签**的操作列 + 0.4/1.0/2.0s 三道有界验证。本钩子保留作兜底（真被调用也无害），
    /// **无常驻轮询**（用户定调：不预支开销，全部动作由真实事件驱动）。
    /// </summary>
    [HarmonyPatch(typeof(UINPCInfo), "Method_Private_Void_Toggle_0")]
    internal static class NpcPanelToggleHook
    {
        private static bool _firstLogged;

        private static void Postfix(UINPCInfo __instance)
        {
            try
            {
                if (__instance == null) return;
                if (!NpcPanelButton.Injected.Contains(__instance)) return;   // 纯展示面板/未注入过 → 无责
                if (!_firstLogged)
                {
                    _firstLogged = true;
                    ModMain.P("[NpcPanelToggleHook] 首次命中（Method_Private_Void_Toggle_0 通道）");
                }
                NpcPanelButton.OnPageSwitched(__instance, "Method_Private_Void_Toggle_0");
            }
            catch (Exception e)
            {
                ModMain.P("[NpcPanelToggleHook] " + e.Message);
            }
        }
    }
}
