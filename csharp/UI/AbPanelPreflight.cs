/// <summary>
/// AB 面板"创建前的四项检查" —— **唯一一份**。
///
/// 【抽它的理由】三个面板（聊天 / 配置 / 传音簿）的 `EnsureResident` 里有一段**逐字同构**的
/// 前置闸：注册宿主类型 → 注册核心 UI 类型 → （需要时）WsClient 就绪 → 预制体注入。
/// 这四项的顺序不是随手排的，每条都对应一次实机事故：
///
/// 1. **注册宿主类型**：IL2CPP 下自建 MonoBehaviour/UIBase 必须先
///    `ClassInjector.RegisterTypeInIl2Cpp&lt;T&gt;()`，否则 `GetComponent&lt;T&gt;` 的泛型静态构造会抛
///    `TypeInitializationException` 并被 CLR **永久缓存**（该类型本进程内再也用不了）。
/// 2. **注册核心 UI 类型**：`ChatWindow`/四个 Presenter/`HoverTip` 未注册时，
///    宿主一旦进了 UIMgr 登记表，装配失败的回滚就得走 `CloseUI`（白开关一次 + 闪一下）。
///    所以"注册没就绪就干脆别建"。
/// 3. **WsClient 就绪**：Presenters 要订阅 `WsClient.UiEvent`，没有连接装配不了。
///    注意**不是每个面板都需要**：配置面板纯本地读写，断线时也该能开 —— 所以是参数。
/// 4. **预制体注入**：游戏读档会重建资源表，注入条目会丢 ⇒ 每次创建前都要确认一次。
///
/// 【返回值】`""` = 可以继续；否则是**机器可判的原因**（`type` / `core` / `ws` / `inject`），
/// 由调用方决定怎么对玩家说 —— 三个面板的失败文案本来就不同（聊天面板只写日志，
/// 传音簿要回一条"智能体尚未连接"给 UI 层显示），那部分不该被统一。
/// </summary>
using System;
using System.Collections.Generic;

namespace AgentLoopBridge
{
    internal static class AbPanelPreflight
    {
        public const string Ok = "";
        public const string FailType = "type";
        public const string FailCore = "core";
        public const string FailWs = "ws";
        public const string FailInject = "inject";

        /// <summary>已注册成功的宿主类型（`RegisterTypeInIl2Cpp` 重复调用会抛，故记账）。</summary>
        private static readonly HashSet<Type> _registered = new HashSet<Type>();

        /// <summary>
        /// 四项前置检查。<paramref name="requireWs"/> = 该面板的 Presenter 是否依赖在线连接。
        /// </summary>
        public static string Check<T>(string prefabKey, bool requireWs) where T : class
        {
            string tag = "[" + typeof(T).Name + "]";
            try
            {
                // ① 宿主类型注册
                if (!_registered.Contains(typeof(T)))
                {
                    try
                    {
                        UnhollowerRuntimeLib.ClassInjector.RegisterTypeInIl2Cpp<T>();
                        _registered.Add(typeof(T));
                    }
                    catch (Exception e)
                    {
                        ModMain.P(tag + " 类型注册失败，本轮不创建：" + e.GetType().Name + " " + e.Message);
                        return FailType;
                    }
                }

                // ② 核心 UI 类型（ChatWindow / 四个 Presenter / HoverTip）
                if (!UiComposer.EnsureCoreTypesRegistered())
                {
                    ModMain.P(tag + " 核心 UI 类型未全部注册（见 UI-2 日志），本轮不创建");
                    return FailCore;
                }

                // ③ 在线连接（只对依赖 UiEvent 的面板）
                if (requireWs && ChatGlobals.WsClientInstance == null)
                {
                    ModMain.P(tag + " WsClient 未就绪，本轮不创建");
                    return FailWs;
                }

                // ④ 预制体注入（读档会重建资源表）
                if (!ModAbRes.EnsureInjected(prefabKey))
                {
                    ModMain.P(tag + " 预制体注入失败，本轮不创建");
                    return FailInject;
                }

                // 四项全过 —— 打一行（11-20 加）。理由：这一层是**收拢后的共用件**，
                // 它有没有真的被走到，光看"面板建成了"是分不出来的（旧代码也能建成）。
                // 有这一行就能确认：三面板确实走的是同一份前置检查，而不是各自的旧副本。
                ModMain.P(tag + " 前置四项通过（类型已注册 / 核心 UI 就绪 / " +
                          (requireWs ? "在线已连接" : "无需在线") + " / 预制体已注入）");
                return Ok;
            }
            catch (Exception e)
            {
                ModMain.P(tag + " 前置检查异常：" + e.GetType().Name + " " + e.Message);
                return FailType;
            }
        }
    }
}