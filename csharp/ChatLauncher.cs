/// <summary>
/// 统一「打开对话 UI」入口 —— NPC 面板「AI 对话」按钮与通讯录行点击共用。
///
/// AB 模式：对话常驻宿主（首次打开时 AbChatPanel.EnsureResident 经 g.ui.OpenUI 懒创建，
/// 挂游戏 UI 层、层级自动排）→ OpenForUnitStatic 重绑当前 NPC 并显形
/// （历史回放/open_chat/立绘全在 Presenter 路径内）。打不开只记日志放弃。
/// 代码模式：ModMain.PresenterInstance.OpenForNpc(中文名) 复用现成 ChatWindow。
///
/// npc_id 统一发中文名（Python 侧会话/人设以中文名为键，见 UnitLookup 注释）；
/// GetName 失败回退 unitID。硬约束：触 g.ui / g.world，仅主线程调用。
/// </summary>
using System;
using UnityEngine;

namespace AgentLoopBridge
{
    internal static class ChatLauncher
    {
        /// <summary>打开对话 UI 并绑定该单位。unit 为 null 时返回 false。</summary>
        public static bool OpenForUnit(WorldUnitBase unit)
        {
            if (unit == null) return false;
            string id = null;
            try { id = unit.data.unitData.propertyData.GetName(); } catch { }
            if (string.IsNullOrEmpty(id)) id = unit.data.unitData.unitID;
            // 抓住真身的那一刻把名字钉到 unitID：这里是全工程唯一"拿着 WorldUnitBase
            // 去开对话窗"的入口（NPC 面板按钮 / 通讯录行点击 / 剧情窗按钮全汇到这），
            // 也是**自身段（L1）唯一能零歧义确定"我是谁"的时机** —— 后续 GameContext.GetL1(npc_id)
            // 按名解析时先查这份登记，重名也不会挂到别人身上。
            try { UnitLookup.Pin(id, unit.data.unitData.unitID); } catch { }

#if AB_UI
            // AB 模式：懒创建常驻宿主（首次调用时经 g.ui.OpenUI 挂游戏 UI 层）+ 重绑显形。
            // 层级随游戏 UI 管理器自动排（迁移），确认窗/论道弹窗后开自然盖住对话窗。
            if (AbChatPanel.OpenForUnitStatic(unit)) return true;
            ModMain.P("[ChatLauncher] 对话宿主不可用（OpenUI 失败），本次不打开");
            return false;
#else
            // 无 AB 编译时的默认路径：复用代码构建 UI（Presenter 打开同一窗口）
            if (ModMain.PresenterInstance == null)
            {
                ModMain.P("[ChatLauncher] PresenterInstance 未就绪，无法打开对话 UI");
                return false;
            }
            ModMain.PresenterInstance.OpenForNpc(id);
            FillPortraits(unit);
            return true;
#endif
        }

        /// <summary>「对方正在斟酌…」占位（用户拍板：同意后立刻开窗吃流式）。
        /// 开窗到首个 token 之间有 4~13s 空窗（ttft），期间显示忙提示；回合收口时
        /// Presenter 的 OnReplyEvent → SetBusy(false) 自动关掉，开窗重放也会清（ChatWindow.ReplaceHistory）。
        /// 打不开就静默（不是关键路径）。仅主线程调用。</summary>
        public static void SetThinking(bool on)
        {
#if AB_UI
            try { if (AbChatPanel.SetBusyStatic(on)) return; } catch { }
#endif
            try
            {
                var w = ModMain.ChatWindowInstance;
                if (w != null) w.SetBusy(on);
            }
            catch { }
        }

        /// <summary>代码版窗口的左右立绘（左 玩家 / 右 NPC，与 AB 版同方向；失败静默隐藏槽位）。
        /// AB 模式的立绘在常驻宿主 OpenForUnit 内填充（其窗口不经 ModMain 注册）。</summary>
        private static void FillPortraits(WorldUnitBase unit)
        {
            try
            {
                var w = ModMain.ChatWindowInstance;
                if (w == null) return;
                if (w.PortraitLeft != null)
                    w.PortraitLeft.gameObject.SetActive(PortraitService.Fill(g.world.playerUnit, w.PortraitLeft));
                if (w.PortraitRight != null)
                    w.PortraitRight.gameObject.SetActive(PortraitService.Fill(unit, w.PortraitRight));
            }
            catch (Exception e)
            {
                ModMain.P("[ChatLauncher] portraits: " + e.Message);
            }
        }

#if AB_UI
#endif
    }
}
