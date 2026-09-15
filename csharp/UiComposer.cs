/// <summary>
/// 三 UI 装配器 —— 主 mod（ModMain.Init 第 5 段）与自测工程（SelfTestBoot）共用的唯一装配序列。
///
/// 当前与 ModMain【诊断版】同序同日志（UI-2 核心四类+AB 两面板 → UI-3 HoverTip →
/// UI-4 通讯录宿主 → UI-5 F11 帧，每步独立 try/catch）——主 mod 注册序列诊断收尾后，
/// ModMain 换一行 UiComposer.Compose 调用即可收编（同步精简诊断打点）。
///
/// 只装配不持状态：实例引用经 Composed 返回，调用方自行赋给各自的静态门面
/// （主 mod 赋给 ModMain 静态属性；测试工程赋给 TestShims 同名门面）——装配只有这一份，防双份漂移。
/// 硬约束：触 g.root / Unhollower 注册 / g.timer，仅主线程调用。
/// </summary>
using System;
using UnityEngine;

namespace AgentLoopBridge
{
    public static class UiComposer
    {
        public sealed class Composed
        {
            public ChatWindow ChatWindow;      // 仅代码版构建（AB 为 null——对话走 AB 预制体）
            public ChatPresenter Presenter;    // 仅代码版（AB 模式在 AbChatPanel.InitData 时挂）
            public ConfigPresenter Config;     // 仅代码版常驻（AB=随进世界探测经 AbConfigPanel 创建）
            public ContactPresenter Contact;   // 仅代码版常驻（AB=随进世界探测经 AbContactPanel 创建）
        }

        public static Composed Compose(GameObject root, WsClient ws)
        {
            var c = new Composed();

            // 【诊断版】每步独立打点 + 独立 try/catch：一次运行定位注册序列里的崩溃/污染点。
            // 教训（迁移定案）：AbChatPanel/AbConfigPanel/AbContactPanel 三面板均已
            // 继承游戏 UIBase 走 g.ui.OpenUI 体系——继承 il2cpp 类型的注册【不能】发生在初始化
            // 早期（毒化类型系统 → 登录 UI 原生崩溃），已全部移出此处，推迟到运行期懒注册
            // （各面板 EnsureResident 内幂等执行）。
            try
            {
                ModMain.P("[AgentLoopBridge] UI-2 注册 ChatWindow/ChatPresenter/ConfigPresenter/ContactPresenter...");
                UnhollowerRuntimeLib.ClassInjector.RegisterTypeInIl2Cpp<ChatWindow>();
                UnhollowerRuntimeLib.ClassInjector.RegisterTypeInIl2Cpp<ChatPresenter>();
                UnhollowerRuntimeLib.ClassInjector.RegisterTypeInIl2Cpp<ConfigPresenter>();
                UnhollowerRuntimeLib.ClassInjector.RegisterTypeInIl2Cpp<ContactPresenter>();
                ModMain.P("[AgentLoopBridge] UI-2 注册完成（AB 三面板改运行期懒注册）");
            }
            catch (Exception e) { ModMain.P("[AgentLoopBridge] UI-2 注册失败: " + e); }

            try
            {
                ModMain.P("[AgentLoopBridge] UI-3 注册 HoverTip...");
                UnhollowerRuntimeLib.ClassInjector.RegisterTypeInIl2Cpp<HoverTip>();
                ModMain.P("[AgentLoopBridge] UI-3 注册完成");
            }
            catch (Exception e) { ModMain.P("[AgentLoopBridge] UI-3 HoverTip 注册失败: " + e); }

#if !AB_UI
            // 代码版对话 UI（默认编译；AB_UI 模式下对话走 AB 预制体，NPC 面板「AI 对话」→ AbChatPanel）。
            // AB 模式下绝不建第二份 presenter：否则两个 ChatPresenter 同订 WsClient.UiEvent，
            // 每条消息（气泡/流式/系统行）都会被处理两次。
            var window = root.AddComponent<ChatWindow>();
            window.Init(ChatUiBuilder.Build(root.transform));
            var presenter = root.AddComponent<ChatPresenter>();
            presenter.Init(window, ws);
            c.ChatWindow = window;
            c.Presenter = presenter;
            // 标题栏 ⚙ → 仅该Npc+全局（过滤由 ConfigPresenter+prompt_files 完成）
            window.ConfigClicked += () => ConfigPanelOpener.Toggle(window.CurrentNpcId);

            // 配置 UI（F11 开关）：大模型参数 + 提示词文件编辑，全走 WS RPC。
            // 仅非 AB 构建常驻；AB 构建若再建常驻 ConfigPresenter，它会与 AbConfigPanel 的
            // presenter 同帧双双轮询 F11，两次 toggle 一关一开互相抵消，F11 永远关不上 AB 面板
            var configPresenter = root.AddComponent<ConfigPresenter>();
            configPresenter.Init(ConfigUiBuilder.Build(root.transform), ws);
            c.Config = configPresenter;
#endif

            // 传音簿（通讯录，F10 开关）：好友（社会关系∪手动）+ 最近 + 搜索 + 行点击传音
#if !AB_UI
            var contactPresenter = root.AddComponent<ContactPresenter>();
            contactPresenter.Init(ContactUiBuilder.Build(root.transform), ws);
            c.Contact = contactPresenter;
#else
            // AB 构建（迁移，与 ModMain 同构）：通讯录/配置宿主随「进世界首帧探测」
            // 经 g.ui.OpenUI 创建（AbPanelProber.OnFrame，两宿主初始隐藏）——ContactPresenter
            // 的未读三分流/F10 轮询/加好友回调、ConfigPresenter 的 F11 轮询都是常驻职责，
            // 宿主不存在则无人响应，被动懒创建会先失去这些职责。探测失败记日志稀疏重试；
            // 装配成败经 AbPanelProber 记日志，c.Contact 保持 null 由调用方按需取
            // （AbPanelProber 成功后已回填 ModMain.ContactPresenterInstance）。
            ModMain.P("[AgentLoopBridge] UI-4 通讯录/配置宿主改为进世界首帧探测创建（OpenUI 体系）");
            g.timer.Frame(new Action(AbPanelProber.OnFrame), 1, true);
#endif

            return c;
        }
    }
}
