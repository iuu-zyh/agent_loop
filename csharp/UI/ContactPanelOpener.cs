/// <summary>
/// 传音簿（通讯录）统一开关入口 —— F10 与后续外部入口都经此路由。
/// 代码构建版：ModMain.Init 装配 ContactPresenterInstance 直接用；
/// AB 构建版（迁移）：常驻宿主随「进世界首帧探测」经 g.ui.OpenUI 创建
/// （AbContactPanel.EnsureResident），此处兜底懒创建，成功即回填 ModMain.ContactPresenterInstance。
/// 僵尸教训：宿主被 GC 回收后引用连 == null 都会抛（ObjectCollectedException），
/// 故 AB 分支一律先走 AbContactPanel.GetAlivePresenter() 验活取用，僵尸/已毁自动清引用，
/// null 再兜底重建——不再裸判 ModMain.ContactPresenterInstance。
/// </summary>
using System;

namespace AgentLoopBridge
{
    public static class ContactPanelOpener
    {
        public static void Toggle()
        {
            ContactPresenter inst = null;
#if AB_UI
            // AB 构建：验活取用（GC 僵尸引用连 == null 都会抛——GetAlivePresenter 统一收口）
            inst = AbContactPanel.GetAlivePresenter();
            if (inst == null)
            {
                if (!AbContactPanel.EnsureResident())
                {
                    ModMain.P("[ContactPanelOpener] AB 常驻宿主不可用（OpenUI 失败），F10 本次不响应");
                    return;
                }
                ModMain.ContactPresenterInstance = AbContactPanel.Presenter;
                inst = AbContactPanel.Presenter;
                // 诊断开关 createOnly：只创建宿主、**不 Show**——用于二分"创建"与"显示"哪个破坏世界输入
                if (DiagSwitches.CreateOnly)
                {
                    ModMain.P("[ContactPanelOpener] createOnly：仅创建宿主（不显示），本轮到此为止");
                    return;
                }
            }
#else
            inst = ModMain.ContactPresenterInstance;
#endif
            if (inst == null)
            {
                ModMain.P("[ContactPanelOpener] ContactPresenterInstance 未就绪");
                return;
            }
            inst.TogglePanel();
        }
    }
}
