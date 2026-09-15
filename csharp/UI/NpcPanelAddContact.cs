/// <summary>
/// NPC 面板「加好友/移除好友」按钮注入 —— Harmony Postfix on UINPCInfo.InitData。
///
/// 功能：通讯录支持手动加好友（好友语义 = 社会关系 ∪ 手动添加，双向名录）。
/// 本按钮是切换语义：当前不在手动名单 → 「加好友」；已在 → 「移除好友」。
/// 名单真相在 Python 侧 contacts.json（经 ContactPresenter.ToggleManualContact 走
/// add/remove_contact RPC）；按钮文案按 ContactPresenter 内存镜像渲染。
///
/// 位置与 NpcPanelButton 同（操作列表），策略=视觉+点击分离：视觉行克隆进 goGrid1（与「交谈」
/// 同款，row 默认 `raycastTarget=false` 故视觉层不接点击），**在视觉行内部塞一个铺满整行的透明
/// 点击捕捉层**（alpha=0 的 Image `raycastTarget=true` + Button，行内子节点、随行移动缩放，
/// 无需坐标换算）承担点击。两按钮按注入先后排入操作列（AI 对话 = 0 号位，加好友紧随其后）。
///
/// 历史教训（截图+日志实锤，勿回退）：
///   ① 旧版克隆“opGroupRoot 下任意首 Button”= 顶层 G:btnClose（×） → 克隆 × 盖住真 ×。
///   ② 旧版用“× anchor 推坐标放右上角”= 28c76a1d 实证：压到页签带尾部、覆盖 ×，且 Image
///      `raycastTarget=false` → 点不响、透到 人物道心 切类目。
///   ③ 旧版“克隆入网格”= 321a8f43：可见但点不响（UIOperationGroup 接管网格输入，克隆行不在
///      operationItems）；同样 Image 不接 raycast。
///   ④ 旧版“面板体顶层坐标代理”（GetWorldCorners 量测盖住行）= 30f2b6db：该游戏 Canvas 缩放下
///      量测恒得 0 尺寸，代理从未建起 → 依旧点不响。
///   正确方案 = 视觉行入网格（占位靠布局）+ **行内透明点击捕捉层**（Image raycastTarget=true +
///   Button，alpha=0，铺满整行、SetAsLastSibling，随行移动）——不依赖任何坐标换算。
///
/// 实现要点：UINPCInfo.InitData Postfix，注入成功才记去重（轮询 0.3/0...0s 补注）；
/// 回调时现读 unit、点击后按镜像翻文案（Text+TextMeshPro 双轨）。
/// </summary>
using System;
using System.Collections.Generic;
using HarmonyLib;
using UnityEngine;
using UnityEngine.UI;

namespace AgentLoopBridge
{
    [HarmonyPatch(typeof(UINPCInfo), nameof(UINPCInfo.InitData))]
    internal static class NpcPanelAddContact
    {
        // internal：NpcPanelToggleHook / NpcPanelButton.EnsureRowFor 链路复用
        internal const string ButtonName = "AgentLoopAddContactButton";

        /// <summary>首轮失败后的轮询间隔（s）。</summary>
        private static readonly float[] RetryDelays = { 0.3f, 0.8f, 1.5f, 3.0f };

        // 同一面板实例只注入一次；成功才入集（轮询 guard 与登记同集合同时序）
        private static readonly HashSet<UINPCInfo> Injected = new HashSet<UINPCInfo>();

        private static void Postfix(UINPCInfo __instance, WorldUnitBase unit, bool isOption)
        {
            try
            {
                if (__instance == null) return;
                if (!isOption) return;   // 纯展示面板不注入
                if (Injected.Contains(__instance)) return;

                if (TryInject(__instance)) { Injected.Add(__instance); return; }
                ModMain.P("[NpcPanelAddContact] 首轮未命中（操作行晚建），0.3/0.8/1.5/3.0s 轮询补注...");
                ScheduleRetry(__instance, 0);
            }
            catch (Exception e)
            {
                ModMain.P("[NpcPanelAddContact] inject: " + e);
            }
        }

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
                        ModMain.P("[NpcPanelAddContact] 轮询补注#" + (idx + 1) + "（+" + delay + "s）...");
                        if (TryInject(panel)) Injected.Add(panel);
                        else ScheduleRetry(panel, idx + 1);
                    }
                    catch (Exception e)
                    {
                        ModMain.P("[NpcPanelAddContact] 轮询终止: " + e.Message);
                    }
                }), delay, false);
            }
            catch (Exception e) { ModMain.P("[NpcPanelAddContact] 排轮询: " + e.Message); }
        }

        /// <summary>视觉行 + 行内点击捕捉层：插在所有 AgentLoop 视觉行之后（AI 对话占 0 号位）。
        /// 网格 = **当前激活页签**的操作列（NpcPanelButton.ResolveOperationGrid）——切页签后跟着走。</summary>
        private static bool TryInject(UINPCInfo panel)
        {
            try
            {
                if (panel == null || panel.transform == null) return false;
                Transform grid = NpcPanelButton.ResolveOperationGrid(panel);
                if (grid == null)
                {
                    ModMain.P("[NpcPanelAddContact] 操作列未就绪（激活页签内未找到 G:goGrid1）");
                    return false;
                }
                // 幂等：行还在就不再注——切页签钩子/验证会重复调用本方法
                if (NpcPanelButton.RowExists(grid, ButtonName) != null) { NpcPanelButton.WireTabToggles(panel); return true; }
                int insertIndex = CountAgentLoopRows(grid);   // 跟在已注入的 AI 对话等行后
                GameObject visual = NpcPanelButton.CreateVisualRow(panel.transform, ButtonName, LabelFor(panel), insertIndex, grid);
                if (visual == null) return false;

                // 回调里顺手刷新视觉行文案（点击后镜像翻），并挂行内捕捉层
                System.Action onClick = () =>
                {
                    ToggleContact(panel, visual);
                    ScheduleRefreshVisualLabel(visual);
                };
                NpcPanelButton.AddClickCatcher(visual, onClick, ButtonName + "_Catcher");
                NpcPanelButton.WireTabToggles(panel);   // 首注成功即挂页签监听（幂等；本类可能先于 NpcPanelButton 注成）
                ModMain.P("[NpcPanelAddContact] 加好友视觉行已注入（父=" +
                          (visual.transform.parent != null ? visual.transform.parent.name : "?") +
                          "，插入位=" + insertIndex + "，网格=" + NpcPanelButton.GetPath(grid) + "）");
                NpcPanelButton.NoteInjected();   // 记账：存活帧数判据 + 后续补注静音
                return true;
            }
            catch (Exception e)
            {
                ModMain.P("[NpcPanelAddContact] 注入: " + e);
                return false;
            }
        }

        /// <summary>
        /// 行级幂等补注：行在 → true（无事）；行缺失 → 补注并返回注入结果；
        /// 网格未就绪 → false（调用方交验证兜底）。与 NpcPanelButton.EnsureRowFor 同构。
        /// </summary>
        internal static bool EnsureRowFor(UINPCInfo panel)
        {
            try
            {
                if (panel == null) return true;
                Transform grid = NpcPanelButton.ResolveOperationGrid(panel);
                if (grid == null) return false;
                if (NpcPanelButton.RowExists(grid, ButtonName) != null)
                {
                    NpcPanelButton.WireTabToggles(panel);   // 验证轮兼作监听补挂（风暴计数按时间复位，勿在此清）
                    return true;
                }
                int n = NpcPanelButton.BeginInject();
                ModMain.P("[NpcPanelAddContact] 加好友行缺失（网格=" + NpcPanelButton.GetPath(grid) +
                          "，网格ID=" + NpcPanelButton.GridId(grid) +
                          "，现状=" + NpcPanelButton.DumpGrid(grid) + "），补注…（本轮第 " + n +
                          " 次，上次注入=" + NpcPanelButton.SurvivedFrames() + "）");
                return TryInject(panel);
            }
            catch (Exception e)
            {
                ModMain.P("[NpcPanelAddContact] EnsureRowFor: " + e.Message);
                return false;
            }
        }

        /// <summary>切页签后的三道无条件验证（0..0/2.0s，有界、无常驻轮询）——见 NpcPanelButton.ScheduleVerify。</summary>
        internal static void ScheduleVerify(UINPCInfo panel)
        {
            try
            {
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
                        catch (Exception e) { ModMain.P("[NpcPanelAddContact] 验证: " + e.Message); }
                    }), d, false);
                }
            }
            catch (Exception e) { ModMain.P("[NpcPanelAddContact] 排验证: " + e.Message); }
        }

        /// <summary>数出该网格里所有 AgentLoop 前缀的视觉行个数（用于加好友插在 AI 之后）。</summary>
        private static int CountAgentLoopRows(Transform grid)
        {
            try
            {
                if (grid == null) return 0;
                int n = grid.childCount;
                int count = 0;
                for (int i = 0; i < n; i++)
                {
                    Transform c = null;
                    try { c = grid.GetChild(i); } catch { continue; }
                    if (c == null || c.name == null) continue;
                    if (c.name.StartsWith("AgentLoop", StringComparison.Ordinal)) count++;
                }
                return count;
            }
            catch { return 0; }
        }

        private static string LabelFor(UINPCInfo panel)
        {
            string name = UnitNameOf(panel);
            bool manual = ContactStore.IsManualContact(name);   // 静态镜像：与面板实例解耦（09-10 定案）
            return manual ? "移除好友" : "加好友";
        }

        private static void ToggleContact(UINPCInfo panel, GameObject visual)
        {
            try
            {
                string name = UnitNameOf(panel);
                if (string.IsNullOrEmpty(name))
                {
                    ModMain.P("[NpcPanelAddContact] 未获取到 NPC 名");
                    return;
                }
                ModMain.P("[NpcPanelAddContact] 加好友按钮被点击（unit=" + name + "）");
                // 定案：好友镜像与 RPC 全在常驻层（ContactStore/ContactDuty），
                // **不再要求通讯录面板在场**——加好友与面板生命周期彻底解耦。
                bool adding = !ContactStore.IsManualContact(name);
                ContactDuty.SetManualContact(name, adding);
                ContactDuty.ToggleManualContact(name, adding);
            }
            catch (Exception e)
            {
                ModMain.P("[NpcPanelAddContact] toggle: " + e);
            }
        }

        /// <summary>点击后延迟 0.2s 刷新视觉行文案（RPC 回包后镜像通常已翻；按 LabelFor 重新读）</summary>
        private static void ScheduleRefreshVisualLabel(GameObject visual)
        {
            try
            {
                if (visual == null) return;
                UINPCInfo panel = null;
                try
                {
                    Transform t = visual.transform;
                    while (t != null)
                    {
                        var ni = t.GetComponent<UINPCInfo>();
                        if (ni != null) { panel = ni; break; }
                        t = t.parent;
                    }
                }
                catch { }
                if (panel == null) return;
                string newLabel = LabelFor(panel);
                g.timer.Time(new Action(() =>
                {
                    try
                    {
                        if (visual == null) return;
                        Text t = visual.GetComponentInChildren<Text>(true);
                        if (t != null) t.text = newLabel;
                        var tm = visual.GetComponentInChildren<TMPro.TextMeshProUGUI>(true);
                        if (tm != null) tm.text = newLabel;
                    }
                    catch { }
                }), 0.2f, false);
            }
            catch { }
        }

        private static string UnitNameOf(UINPCInfo panel)
        {
            try
            {
                WorldUnitBase unit = panel != null ? panel.unit : null;
                if (unit == null) return null;
                string name = null;
                try { name = unit.data.unitData.propertyData.GetName(); } catch { }
                if (string.IsNullOrEmpty(name)) name = unit.data.unitData.unitID;
                return name;
            }
            catch { return null; }
        }
    }
}
