/// <summary>
/// 配置面板统一开关入口 —— F11 与对话面板 ⚙ 按钮都经此进入。
///
/// 路由（编译期分流，无运行时回退）：
///   AB_UI 构建 → 只走 AB 常驻宿主（AbConfigPanel，Init 装配 + F11/⚙ 显隐；样式来自 UIConfigAi.prefab）；
///               未就绪只报错并中止本次操作（按需求：报错了就报错，不回退代码版）。
///   非 AB 构建 → 代码构建版（ModMain.ConfigPresenterInstance）。
/// 两个版本的逻辑都是 ConfigPresenter，区别只在样式载体。
/// </summary>
using System;

namespace AgentLoopBridge
{
    public static class ConfigPanelOpener
    {
        /// <summary>开关配置面板（npcId空=全量F11；非空=对话内⚙仅该Npc）</summary>
        public static void Toggle(string npcId = null)
        {
            string n = (npcId ?? "").Trim();
#if AB_UI
            try
            {
                if (AbConfigPanel.HandleToggle(n)) return;
                // HandleToggle 返回 false = 常驻宿主未就绪（ab 未部署/未注入），其内部已记日志
                ModMain.P("[ConfigPanelOpener] AB 配置面板不可用（无代码版回退），本次不打开");
            }
            catch (Exception e)
            {
                ModMain.P("[ConfigPanelOpener] AB 配置面板异常（无代码版回退）: " + e);
            }
            return;
#else
            var inst = ModMain.ConfigPresenterInstance;
            if (inst == null) return;
            if (n.Length > 0) inst.ShowPanel(n);
            else inst.TogglePanel();
#endif
        }
    }
}
