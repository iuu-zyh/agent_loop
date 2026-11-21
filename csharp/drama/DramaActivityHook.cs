/// <summary>
/// `WorldSystemMgr.OpenMapDrama` 的 Harmony Postfix（手动挂载，见 ModMain）——
/// **09-21 起只做记录，不再参与任何闸门。**
///
/// 历史：这条钩子原本唯一的作用是给 `AbPanelProber` 的"剧情静置窗"记帧号
/// （"`OpenMapDrama` 被调 ⇒ 剧情窗要开了 ⇒ 300 帧内别 `OpenUI`"），而且曾长期是个空操作、
/// 09-20 清探针时才被"接成真的"。
///
/// 09-21 实机日志推翻了它的前提：`OpenMapDrama` **不是**"剧情窗要开了"，而是读档期的
/// 任务/条件**批量检查器** —— 每次进世界被调 **35 次**，栈上游戏自己打的全是
/// "检测破碎祭坛 / 处理夸父之忧任务 / 检测通关成就 / 检测主线剧情中断…"，被调 ≠ 有剧情窗要开。
/// 于是那条 300 帧静置窗**每次进世界都误拦**：玩家点「传」的横幅写着"剧情/界面切换中"，
/// 场上却一个剧情窗都没有（同段日志 `UICustomDramaDyn` 0 次、`UIDramaDialogue` 0 次）。
///
/// 修法（AbPanelProber 09-21 同批）：面板闸门的剧情窗判据改用
/// `NpcInitiativeMonitor.DramaWindowName()`（家族名 + 可见性，与忙判据共用同一份实现），
/// 静置窗整条删除 —— 本钩子随之失去闸门用途。
///
/// 保留它只为一件事：**把"被调次数"如实记下来**，供以后核对这条结论
/// （每 35 次打一行 = 每次进世界一行，几乎零日志成本）。另有诊断开关
/// `_diag_no_patch_drama.txt` 可单独撤掉它（DiagSwitches → ModMain.UnpatchDramaHook）。
///
/// 只用 Postfix、不改游戏逻辑；挂载失败不影响其它补丁（ModMain 里单独 try/catch 手动挂）。
/// </summary>
#if AB_UI
using System;

namespace AgentLoopBridge
{
    internal static class DramaActivityHook
    {
        /// <summary>累计被调次数（只增不减；主线程调用，无锁）。</summary>
        private static int _calls;

        /// <summary>上次打日志时的累计值（每打一行推进一次）。</summary>
        private static int _loggedAt;

        /// <summary>与实测对齐：`OpenMapDrama` 每次进世界被调约 35 次（09-21 日志），
        /// 每 35 次打一行 ⇒ 一行/次进世界，正好能核对"是任务扫描、不是剧情窗"。</summary>
        private const int LogEvery = 35;

        internal static void Postfix()
        {
            try
            {
                _calls++;
                if (_calls - _loggedAt < LogEvery) return;
                _loggedAt = _calls;
                ModMain.P("[DramaActivityHook] OpenMapDrama 累计被调 " + _calls +
                          " 次（只记录不闸门；每次进世界约 35 次 = 任务批量检查，非剧情窗开启）");
            }
            catch { }
        }
    }
}
#endif // AB_UI
