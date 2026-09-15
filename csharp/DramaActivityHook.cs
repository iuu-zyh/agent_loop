/// <summary>
/// 地图剧情开启钩子 —— Harmony Postfix on WorldSystemMgr.OpenMapDrama（手动挂载，见 ModMain）。
///
/// 世界输入失效事故：`g.ui.OpenUI` 若在**剧情窗设置期间**执行，会打乱游戏 UIMgr 对该
/// UI 层的排序/布局（UIMgr 会重建实例 Canvas+RT），把同时刻正在创建的剧情窗立绘槽位打成
/// 0 尺寸 → 剧情窗显示/推进不了（世界输入被"剧情展示中"挡住，角色不能动/点不开 NPC）
/// + 每帧 `RenderTexture.Create failed` 刷屏拖垮主线程。
///
/// 本钩子把"剧情刚开启"事件告诉 AbPanelProber（静置计时清零），保证面板创建/重建一定避开
/// 剧情窗口——比"按 UI 名猜剧情窗"可靠（地图剧情可能不走 UICustomDramaDyn）。
///
/// 只用 Postfix、不改游戏逻辑；挂载失败不影响其它补丁（ModMain 里单独 try/catch 手动挂）。
/// </summary>
#if AB_UI
using System;
using HarmonyLib;

namespace AgentLoopBridge
{
    internal static class DramaActivityHook
    {
        /// <summary>供 ModMain 手动 Harmony.Patch 用的 postfix（不参与 PatchAll，避免歧义异常
        /// 中断其它补丁）。签名与 OpenMapDrama 无关（只关心"被调用过"）。</summary>
        internal static void Postfix()
        {
            try { AbPanelProber.NotifyDramaOpening(); }
            catch { }
        }
    }
}
#endif // AB_UI
