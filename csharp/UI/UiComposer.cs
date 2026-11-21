/// <summary>
/// 三 UI 装配器 —— 自测工程（SelfTestBoot）用的装配序列；**主 mod 已不再调 `Compose`**
/// （ModMain 的 UI-4 步在 09-09 起改挂 AbPanelProber.OnFrame，UI-1/2/3 也各自搬走）。
///
/// ★ 但本文件里 `EnsureCoreTypesRegistered()` 是**活的、被依赖的**，不是死代码：
///   它是 ChatWindow/ChatPresenter/ConfigPresenter/ContactPresenter/HoverTip 五个托管类型的
///   唯一注册入口，调用点 = `ModMain.Init` 第 5 段 + 三个 Ab*Panel 的 EnsureResident（OpenUI 前）。
///   09-17 实机事故正是"把 Compose 当死代码、连它的调用点一起删掉"造成的：
///   没人注册 ⇒ `GetComponent<ContactPresenter>()` 的泛型静态构造抛 TypeInitializationException
///   （CLR 缓存该失败 ⇒ 该 T 永久不可用）⇒ F10 传音簿/对话窗全灭，回滚又绕过 UIMgr
///   ⇒ 世界输入被门控（人物动不了、UI 打不开）。改动本文件前先读 §13.4 与那条注释。
///
/// `Compose` 自身（代码版 UI 链）在 AB_UI 下确实不生效：除两个注册块外全在 `#if !AB_UI` 里。
///
/// 只装配不持状态：实例引用经 Composed 返回，调用方自行赋给各自的静态门面
/// （测试工程赋给 TestShims 同名门面）——装配只有这一份，防双份漂移。
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

        // ---- 核心托管 UI 类型的 Il2Cpp 注册 --------------------------------
        //
        // ★ 这不是"代码版装配链"的一部分，AB 构建同样离不开它 ★
        // AB 三面板装配时都对 presenter 做
        // `GetComponent<X>() ?? AddComponent<X>()`，而 X（ChatWindow/ChatPresenter/
        // ConfigPresenter/ContactPresenter/HoverTip）都是本程序集的**托管** MonoBehaviour。
        // 没注册进 Il2Cpp 时 Unhollower 造不出它的 Il2CppSystem.Type
        // （`ClassInjector.ClassFromTypePatch` 里 NRE）→
        // `GameObject+MethodInfoStoreGeneric_GetComponent_Public_T_0`1<T>` 的**静态构造**抛
        // `TypeInitializationException`，而 CLR 会**缓存**该失败 ⇒ 这个 T 的
        // GetComponent/AddComponent 在本次进程内**永久不可用**（不会再自愈）。
        //
        // 09-17 实机事故：`Compose` 的调用点被删（全工程零调用方，只当"死代码"看待）⇒
        // 全进程没人注册这五个类型 ⇒ 传音簿（点主 HUD 的「传」按钮，本 mod 已无热键）
        // `AssembleResident` 在 `GetComponent<ContactPresenter>()` 上抛异常，随后回滚又绕过 UIMgr
        // 直接 Destroy ⇒ 登记项泄漏 → **世界输入被门控（人物动不了、UI 打不开）**。
        // 详见 docs/archive/2026-09-17-群聊编排/设计定稿.md §13.4。
        //
        // 教训（迁移定案，仍然成立）：AbChatPanel/AbConfigPanel/AbContactPanel 三面板
        // **继承游戏 UIBase**，它们的注册【不能】发生在初始化早期（毒化类型系统 → 登录 UI
        // 原生崩溃），只能运行期懒注册（各面板 EnsureResident 内）。上面五个是纯
        // MonoBehaviour，从 UI-2 时代起就是初始化期注册，不受此限。
        private static readonly string[] _coreTypeNames =
        { "ChatWindow", "ChatPresenter", "ConfigPresenter", "ContactPresenter", "HoverTip" };
        private static readonly bool[] _coreTypeDone = new bool[5];

        /// <summary>
        /// 把五个宿主托管的 UI 类型注册进 Il2Cpp。**幂等、可重入、绝不抛**。
        ///
        /// 调用点（三处，缺一不可）：
        ///  ① `ModMain.Init` 第 5 段——沿用本方法原先的调用时序（初始化期注册纯 MonoBehaviour）；
        ///  ② `AbChatPanel.EnsureResident` / ③ `AbConfigPanel` / `AbContactPanel`——
        ///     **OpenUI 之前**兜底再调一次：注册失败时干脆不建宿主，免得留下要回滚的僵尸登记。
        ///
        /// 返回值 = 是否**全部**就绪。false ⇒ 调用方应放弃本轮 UI 创建（别去 OpenUI）。
        /// 单项失败只记日志不抛：一个类型炸了不该连累另外四个（也便于日志一眼看出是谁）。
        /// </summary>
        public static bool EnsureCoreTypesRegistered()
        {
            bool all = true;
            for (int i = 0; i < _coreTypeDone.Length; i++)
            {
                if (_coreTypeDone[i]) continue;   // 重复 RegisterTypeInIl2Cpp 会抛，故记账
                try
                {
                    switch (i)
                    {
                        case 0: UnhollowerRuntimeLib.ClassInjector.RegisterTypeInIl2Cpp<ChatWindow>(); break;
                        case 1: UnhollowerRuntimeLib.ClassInjector.RegisterTypeInIl2Cpp<ChatPresenter>(); break;
                        case 2: UnhollowerRuntimeLib.ClassInjector.RegisterTypeInIl2Cpp<ConfigPresenter>(); break;
                        case 3: UnhollowerRuntimeLib.ClassInjector.RegisterTypeInIl2Cpp<ContactPresenter>(); break;
                        default: UnhollowerRuntimeLib.ClassInjector.RegisterTypeInIl2Cpp<HoverTip>(); break;
                    }
                    _coreTypeDone[i] = true;
                }
                catch (Exception e)
                {
                    all = false;   // 不置 _coreTypeDone：下次调用还会重试
                    ModMain.P("[AgentLoopBridge] ★核心 UI 类型注册失败 " + _coreTypeNames[i]
                              + "（该类型在本次进程内不可用）: " + e.Message);
                }
            }
            return all;
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
                ModMain.P("[AgentLoopBridge] UI-2 注册 ChatWindow/ChatPresenter/ConfigPresenter/ContactPresenter/HoverTip...");
                bool allOk = EnsureCoreTypesRegistered();
                ModMain.P("[AgentLoopBridge] UI-2 注册完成（全部就绪=" + allOk
                          + "；AB 三面板仍改运行期懒注册）");
            }
            catch (Exception e) { ModMain.P("[AgentLoopBridge] UI-2 注册失败: " + e); }

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
            // ★11-19：这一行原先是裸 `g.timer.Frame(new Action(AbPanelProber.OnFrame), 1, true)`★
            //   它挂在 `Compose` 里，而 `Compose` 全工程**已无调用点**（死代码）—— 换句话说
            //   这条注册从来不会执行，只会误导后来读代码的人以为"prober 已经有人注册了"。
            //   真正在跑的那条在 `ModMain.Init`（`FrameTicker.Ensure(KProber, "prober", …)`）。
            //   这里删掉而不是改写成 FrameTicker：同一个键在没有调用点的路径上再注册一次，
            //   一旦将来有人复活 `Compose`，就会变成"两条同名 tick 互相顶掉"。
            //   （全仓"裸注册"扫描见 `tests/test_resident_tick_contract.py`。）
#endif

            return c;
        }
    }
}
