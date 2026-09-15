/// <summary>
/// OwnUiProbe —— 「我们自己的界面此刻有没有开着」的唯一收敛点（用户反馈驱动）。
///
/// 【为什么需要它】HotkeyGate 要回答的问题是「现在该不该把游戏原生快捷键掐掉」。这个问题在
/// 的答案是「输入框聚焦中」（用户打字被 i/b/n/m/j/z 抢），用户把诉求扩大成
/// **「打开我的 UI 的时候，游戏快捷键一律不许用（ESC 除外）」**——因为开着配置面板调参数、
/// 开着对话窗读剧情时，手一抖按到 X（技能）游戏就跳页，是同一个病的另一种发作形式。
/// 于是判据从「聚焦」升级为「**面板开着** OR 聚焦」，本类就是前者的判据来源。
///
/// 【为什么不用 Resident != null】三个面板的关闭语义各不相同，只有各自 presenter 里的
/// 「窗口节点是否 active」才是屏幕上的真相：
///   · 对话窗   —— 关 = CloseViaManager（_resident 置空）+ OnDestroy 兜底 ⇒ AbChatPanel.IsShowing
///   · 配置面板 —— 关 = ConfigPresenter.Hide()（只置灰 BG，**不清** AbConfigPanel._current）
///                 ⇒ 必须问 ConfigPresenter.IsPanelOpen，`AbConfigPanel.IsOpen` 点过 ✕ 后陈旧为真
///   · 传音簿   —— 关 = ContactPresenter.Hide()（只置灰 BG+Dim，**不销毁**宿主）
///                 ⇒ 必须问 ContactPresenter.IsOpen
/// 三处都能被 ✕ / ESC / 管理器 / 场景切换四条路径关掉，没有一个静态引用能独立判准，
/// 所以统一收在这里，各面板自己实现 `IsShowing`，本类只做「谁开着」的汇总。
///
/// 【失败姿态】任何一处取值抛异常（IL2CPP 的 GC 僵尸引用连 `== null` 都会抛）都当作「没开」——
/// 宁可漏屏蔽一次快捷键，也绝不把游戏快捷键永久掐死（那会让玩家连背包都打不开）。
///
/// 【为什么不用委托表】OpenPanelName 在屏蔽期间每帧都会被求值，`Func&lt;bool&gt;` 表会每帧
/// 分配三个委托 → 无谓的 GC 压力。游戏主循环里一律直写，不做这种"优雅"。
/// </summary>
#if AB_UI
namespace AgentLoopBridge
{
    internal static class OwnUiProbe
    {
        /// <summary>开着的界面名（给日志用）；一个都没开返回 null。</summary>
        internal static string OpenPanelName()
        {
            // 顺序按"用户感知的最上层"：对话内 ⚙ 会让两者同时开着，报最上层的更有诊断价值
            try { if (AbConfigPanel.IsShowing) return "配置面板"; } catch { }
            try { if (AbChatPanel.IsShowing) return "对话窗"; } catch { }
            try { if (AbContactPanel.IsShowing) return "传音簿"; } catch { }
            return null;
        }

        /// <summary>有没有任何一个自建界面开着（bool 版，判断热路径用它）。</summary>
        internal static bool AnyOpen()
        {
            return OpenPanelName() != null;
        }
    }
}
#endif // AB_UI
