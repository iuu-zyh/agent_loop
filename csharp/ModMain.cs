/// <summary>
/// 游戏内对话 Agent 桥 —— C# 端（MelonLoader 插件）
///
/// 分层分权：Python 是大脑（对话/工具决策），C# 是双手（g.world/g.conf 与主线程调度）。
/// 二者只以单条 WebSocket 全双工连接交换 JSON（Python 作 WsServer :8766，C# 作客户端连入）。
///
/// 本类为插件入口（对标官方 ModMain 教程）：
///  - Init()     进入游戏后调用：起主线程调度 + WS 客户端（连 Python）+ 拉起 Python server.py
///  - Destroy()  回主界面时调用：停调度、停 WS
///
/// 硬约束：任何触及 WorldUnitData/UnitInfoData 的调用必须在主线程执行，
///         后台线程（WsClient 收发）只负责入队；主线程 g.timer.Frame 每帧消费。
/// </summary>
using System;
using System.Diagnostics;
using System.Reflection;
using System.Threading;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace AgentLoopBridge
{
    public class ModMain
    {
        // Harmony 补丁（如需监听进世界时机 / 补丁拦截等功能时启用）
        private static HarmonyLib.Harmony _harmony;

        // WS 客户端（连 Python WsServer）+ 主线程调度器
        private WsClient _wsClient;
        private MainThreadDispatcher _dispatcher;
        // NPC 主动互动触发监测（日节拍 + 状态闸）
        private NpcInitiativeMonitor _initMonitor;
        private Il2CppSystem.Action<ETypeData> _worldAddDayHandlerIl2Cpp;
        // 存档语义：存档=固化对话增量；进世界=丢弃未固化增量（读档=回到存档时刻）
        private Il2CppSystem.Action<ETypeData> _saveDataHandlerIl2Cpp;
        private Il2CppSystem.Action<ETypeData> _intoWorldHandlerIl2Cpp;

        // 是否已拉起过 Python 进程（避免重复拉起）；Launcher 同程序集内访问
        internal static bool _pythonStarted;

        // 主线程 ID（Init 时捕获）：线程感知日志的判据。IL2CPP 下非主线程调用 UnityEngine API
        // （含 Debug.Log）会在 interop 层原生崩溃（il2cpp_class_get_static_field_data，2026-09-07
        // 实证：WS 后台线程握手成功后首次 Debug.Log 即崩；神识传音全 mod 异步路径只用
        // Console.WriteLine 佐证同一铁律）。后台线程日志一律走 Console + 线程日志文件。
        private static int _mainThreadId = -1;

        // 自建对话 UI 门面（Init 时填充；NPC 面板按钮等外部入口经此打开窗口）
        internal static ChatWindow ChatWindowInstance { get; private set; }
        internal static ChatPresenter PresenterInstance { get; private set; }
        /// <summary>本插件实例（09-13：配置面板保存成功后要立刻重拉一次 get_config 让 C# 侧
        /// 参数热生效，那是个实例方法，需要实例入口；顺手也给今后"面板 → 插件内部动作"留个门）。</summary>
        internal static ModMain Instance { get; private set; }
        // 配置 UI 控制器（F11 开关；配置/提示词 RPC 经同一连接）
        internal static ConfigPresenter ConfigPresenterInstance { get; private set; }
        // 传音簿（通讯录）控制器（F10 开关；手动好友名单 + 会话索引 RPC 经同一连接）
        // internal set：AB 构建下 ContactPanelOpener/NpcPanelAddContact 兜底懒创建成功后回填
        internal static ContactPresenter ContactPresenterInstance { get; set; }

        // AB 通讯录/配置宿主的进世界探测：逻辑在 UI\AbPanelProber（静态，UiComposer 共用），
        // 此处只持挂载事实；Destroy 时 Reset 复位

        /// <summary>MOD 初始化：进入游戏时调用（官方约定入口）</summary>
        public void Init()
        {
            try
            {
                // 捕获主线程 ID（官方约定 Init 在主线程调用）：线程感知日志 P() 的判据
                _mainThreadId = Thread.CurrentThread.ManagedThreadId;
                Instance = this;   // 实例入口（配置面板保存后重拉配置用）

                // 构建版本戳（排查「查玩家经历恒空」）：确认新 DLL 真的被加载
                P("[Build] 0915-clean2 探针大清理（-1208 行死探针）；我们的界面开着时屏蔽游戏大地图快捷键（拦 MapWorldMgr.FastKey）；+配置参数热生效；+主动互动总开关/自动压缩开关/立绘开关/保留比/阈值；+配置面板分组+整页滚动+滚动命中层【滚轮/拖动修复】");

                // 署名水印：二次打包者若不移除本行，任何流出的副本都会在日志里打出原作者
                P($"[About] {About.Banner}");
                P($"[About] 许可：{About.License}");

                // 启用当前程序集全部 Harmony 补丁（先清理旧实例再重建，避免重复 Patch）
                    if (_harmony != null)
                    {
                        _harmony.UnpatchSelf();
                        _harmony = null;
                    }
                    _harmony = new HarmonyLib.Harmony("AgentLoopBridge");
                    _harmony.PatchAll(Assembly.GetExecutingAssembly());
#if AB_UI
                    // 9b)剧情开启钩子（世界输入失效事故）：单独手动挂，避免方法名歧义
                    // 抛异常中断上面 PatchAll（PatchAll 是一次性遍历，任一失败会影响后续补丁）。
                    try
                    {
                        var dramaMethod = HarmonyLib.AccessTools.Method(typeof(WorldSystemMgr), "OpenMapDrama");
                        if (dramaMethod != null)
                        {
                            var post = HarmonyLib.AccessTools.Method(typeof(DramaActivityHook), "Postfix");
                            _harmony.Patch(dramaMethod, postfix: new HarmonyLib.HarmonyMethod(post));
                            P("[AgentLoopBridge] 剧情钩子已挂：WorldSystemMgr.OpenMapDrama");
                        }
                        else P("[AgentLoopBridge] WorldSystemMgr.OpenMapDrama 未找到，剧情钩子跳过（闸门仍靠剧情窗判定）");
                    }
                    catch (Exception dEx)
                    {
                        P("[AgentLoopBridge] 剧情钩子挂载失败（不影响其它补丁）: " + dEx.Message);
                    }

                    // 9d)大地图快捷键派发点 `MapWorldMgr.FastKey` 的前缀
                    // 全程序集元数据里查出来的唯一"快捷键派发函数"；我们的界面开着时跳过它。
                    // 详见 UI/FastKeyGate.cs 头部：为什么这次不会再弄坏 UI。
                    // （09-15 那批"只计数不拦截"的观察探针已全部退役 —— 它们各自的结论都已写进
                    //   HotkeyGate.cs 文件头的"别再试"清单，代码本身没有保留价值。）
                    try { FastKeyGate.EnsureInstalled(); }
                    catch (Exception fEx)
                    {
                        P("[AgentLoopBridge] FastKeyGate 挂载失败（不影响其它补丁）: " + fEx.Message);
                    }
#endif

                // 1) 主线程调度器：g.timer.Frame 每帧回调（游戏主线程）
                _dispatcher = new MainThreadDispatcher();
                g.timer.Frame(new Action(_dispatcher.OnUpdate), 1, true);

                // 2) WS 客户端：连 Python 的 WsServer（127.0.0.1，端口读 config.json），单连接全双工
                //    端口不再写死 8766：配置面板允许用户改 network.port，
                //    而 C# 原先恒连 8766 → 用户一改就永久失联（重启游戏也没用，因 Start() 取默认）。
                //    现在两侧同读 config.json，配置改完重启游戏即生效。
                _wsClient = new WsClient(_dispatcher);
                _wsClient.Start("ws://127.0.0.1:" + ModConfigFile.ReadWsPort());
                // WS 全局桥（两种构建模式都要用：AB 面板与 DramaAiOption 经此复用同一连接）
                ChatGlobals.WsClientInstance = _wsClient;
                ContactDuty.Attach(_wsClient);   // 09-10 定案：常驻职责（消息分流/好友镜像）与面板实例解耦

                // 3) NPC 主动互动触发监测：日节拍（WorldAddDay）+ Frame兜底，产出 npc_initiative 事件给 Python
                _initMonitor = new NpcInitiativeMonitor(_wsClient);
                // 重连（含配置 UI 触发的 Python 重启）后重拉 initiative 参数——不用整机重启游戏
                _wsClient.Connected += OnWsReconnected;
                // Frame 兜底（读档当日等 WorldAddDay 未触发场景）
                g.timer.Frame(new Action(_initMonitor.OnUpdate), 1, true);
                // 游戏日事件（主闸）：每游戏日一次
                try
                {
                    System.Action<ETypeData> sysAct = _initMonitor.OnWorldAddDay;
                    _worldAddDayHandlerIl2Cpp = sysAct; // implicit Il2CppSystem.Action<ETypeData>
                    g.events.On(EGameType.WorldAddDay, _worldAddDayHandlerIl2Cpp, 0, false);
                }
                catch (Exception evEx)
                {
                    P("[AgentLoopBridge] WorldAddDay 监听注册失败（Frame兜底仍生效）: " + evEx.Message);
                }
                // 存档语义事件：存档=通知 Python 固化对话增量；进世界=通知丢弃
                // 未固化增量（读档=回到存档时刻；关游戏不保存=增量随 Python 进程消失）。
                // 手动/自动存档都走 EGameType.SaveData；读档/新建都走 EGameType.IntoWorld。
                try
                {
                    System.Action<ETypeData> saveAct = OnSaveDataEvent;
                    _saveDataHandlerIl2Cpp = saveAct;
                    g.events.On(EGameType.SaveData, _saveDataHandlerIl2Cpp, 0, false);
                    System.Action<ETypeData> intoAct = OnIntoWorldEvent;
                    _intoWorldHandlerIl2Cpp = intoAct;
                    g.events.On(EGameType.IntoWorld, _intoWorldHandlerIl2Cpp, 0, false);
                }
                catch (Exception evEx2)
                {
                    P("[AgentLoopBridge] 存档/进世界事件注册失败（对话固化语义退化）: " + evEx2.Message);
                }
                // 配置拉回：连上 Python 后异步取 initiative 块热更（失败用默认 15/3日/600s）
                try
                {
                    // 延迟 2s 等 WS 连上（SendRequest 需 Open 状态）
                    g.timer.Frame(new Action(() =>
                    {
                        // 用 Frame 延迟一帧再 Time，避免嵌套 timer 转换问题
                        try
                        {
                            g.timer.Time(new Action(() =>
                            {
                                // 首次连接延迟 2s（SendRequest 需 Open 状态）
                                try
                                {
                                    RefreshInitiativeFromConfig();
                                }
                                catch { }
                            }), 2f, false);
                        }
                        catch { }
                    }), 1, false);
                }
                catch { }

                // 4) 拉起 Python 平台（server.py，其内 WsServer 监听 8766）
                Launcher.LaunchPythonOnce();

                // 4.1) 存活监测：进程层判据 + 退避重生（模型见 docs/brain-liveness-design.md）
                //      **必须排在拉起之后** —— 启动宽限期从"刚拉起"那一刻算起，
                //      排在前面会把上一轮遗留的时间差算进来。
                //      用 g.timer.Frame 驱动 = 主线程，故 BrainLink 可直接碰 UI（横幅）。
                BrainLink.Start(_wsClient);
                g.timer.Frame(new Action(BrainLink.OnUpdate), 1, true);

                // 4.5) Mod AB 预载：直载 bundle 并把 UI 预制体注入 g.res 官方索引
                //（必须在 UI 构建前——AbContactPanel 的 g.res.Load 与 OpenUI 系面板都依赖这些 key）
                ModAbRes.PreloadAll();

                // 5) 游戏内对话 UI（尽力而为：初始化失败只打日志，不拖垮对话主流程——chat_cli 链路照常）

                P("[AgentLoopBridge] Init ok：WS 已尝试连接 127.0.0.1:8766，Python 已尝试拉起，主动互动监测已挂载");
            }
            catch (Exception e)
            {
                P("[AgentLoopBridge] Init failed: " + e);
                throw;
            }
        }

        /// <summary>WS（重）连上：重拉 initiative 参数喂监测器。
        /// 幂等：首次连上与延迟 2s 的启动拉取会各触发一次，Configure 重复赋值无害。</summary>
        /// <summary>存档身份注入（Python 侧按存档隔离通讯录/对话历史/最近索引）。
        /// world_id = **玩家 unitID**——随存档固化、世界唯一（实机：当前存档 `FJLlLl`／缪嘉歆，
        /// 上一存档 `BOlQu6`／唐炎），换存档必变；player_name 只作人类可读标签
        /// （本作存在与玩家同名的 NPC，名字不可当键）。取不到就不写该字段，Python 侧沿用旧命名空间。</summary>
        private static void FillWorldIdentity(JObject p)
        {
            try
            {
                string wid = UnitSnapshot.PlayerUnitId();
                if (!string.IsNullOrEmpty(wid)) p["world_id"] = wid;
                var u = g.world.playerUnit;
                if (u != null)
                {
                    string pn = null;
                    try { pn = u.data.unitData.propertyData.GetName(); } catch { }
                    if (!string.IsNullOrEmpty(pn)) p["player_name"] = pn;
                }
            }
            catch (Exception e) { P("[AgentLoopBridge] FillWorldIdentity: " + e.Message); }
        }

        /// <summary>存档事件（EGameType.SaveData，手动/自动存档都触发）：通知 Python 把所有
        /// 活/冻 agent 的对话增量固化进 session 账本（save_happened → flush_all）。
        /// 玩家不存档就退游戏 → 增量从未写盘 → 读档后 NPC 不记得 = 存档语义对齐。</summary>
        private void OnSaveDataEvent(ETypeData e)
        {
            try
            {
                JObject p = new JObject();
                FillWorldIdentity(p);
                _wsClient?.SendRequest("save_happened", p, _ => { }, 10000);
                P("[AgentLoopBridge] save_happened 已发送（存档触发对话固化）world=" + (string)p["world_id"]);
            }
            catch (Exception evEx)
            {
                P("[AgentLoopBridge] save_happened 发送失败: " + evEx.Message);
            }
        }

        /// <summary>进世界事件（EGameType.IntoWorld，读档/新建都走）：通知 Python 丢弃全部
        /// 未固化增量并销毁活/冻 agent（load_happened → discard_all）——世界已（重）载，
        /// 对话回到上次存档时刻；关游戏不保存的场景由增量随 Python 进程消失天然覆盖。
        /// 同时携带存档身份（world_id）→ Python 切换存储命名空间（换存档不再串号）。</summary>
        private void OnIntoWorldEvent(ETypeData e)
        {
            try
            {
                JObject p = new JObject();
                FillWorldIdentity(p);
                _wsClient?.SendRequest("load_happened", p, _ => { }, 10000);
                P("[AgentLoopBridge] load_happened 已发送（世界重载，未固化增量作废）world=" + (string)p["world_id"]);
                // 换存档后立刻重拉通讯录镜像/会话索引：ContactStore 是静态缓存，
                // 不重拉就会把**上一个存档**的手动好友继续喂给候选集与 NPC 面板按钮状态。
                // 同一连接上按序处理，load_happened 先到 → 这里必然读到新存档的名单。
                try { ContactDuty.RequestManualContacts(); ContactDuty.RequestSessions(); }
                catch (Exception ce) { P("[AgentLoopBridge] 换存档后重拉通讯录失败: " + ce.Message); }
            }
            catch (Exception evEx)
            {
                P("[AgentLoopBridge] load_happened 发送失败: " + evEx.Message);
            }
        }

        private void OnWsReconnected()
        {
            try
            {
                RefreshInitiativeFromConfig();
            }
            catch (Exception e)
            {
                P("[AgentLoopBridge] OnWsReconnected: " + e.Message);
            }
        }

        /// <summary>拉 get_config 的 initiative 块 → NpcInitiativeMonitor.Configure
        /// （失败静默，监测器保持旧值/默认值）。主线程回调。
        ///
        /// 动态读取：本方法原先只在 WS（重）连时调一次，于是面板改这几个值必须重启 Python
        /// 才能生效。现在 `ConfigPresenter` 在 `set_config` 成功后**也会直接调它**（保存即重拉），
        /// 因为消费方全是 C# 内存字段（触发时现读）→ 改完下一个游戏日就是新值，零重启。
        /// internal：ConfigPresenter 同程序集直接调用。</summary>
        internal void RefreshInitiativeFromConfig()
        {
            if (_wsClient == null || _initMonitor == null) return;
            _wsClient.SendRequest("get_config", new Newtonsoft.Json.Linq.JObject(), (resp) =>
            {
                try
                {
                    var ok = resp["ok"] != null && (bool)resp["ok"];
                    if (!ok) return;
                    var data = resp["data"] as Newtonsoft.Json.Linq.JObject;
                    if (data == null) data = resp as Newtonsoft.Json.Linq.JObject;
                    var ini = data?["initiative"] as Newtonsoft.Json.Linq.JObject;
                    if (ini == null) ini = data?["data"]?["initiative"] as Newtonsoft.Json.Linq.JObject;
                    if (ini == null) return;
                    int dc = ini["daily_chance"] != null ? (int)ini["daily_chance"] : 15;
                    int cd = ini["npc_cooldown_days"] != null ? (int)ini["npc_cooldown_days"] : 3;
                    int rs = ini["npc_cooldown_real_s"] != null ? (int)ini["npc_cooldown_real_s"] : 600;
                    int thr = ini["low_intim_threshold"] != null ? (int)ini["low_intim_threshold"] : 60;
                    bool halve = ini["low_intim_halve"] != null ? (bool)ini["low_intim_halve"] : true;
                    bool enabled = ini["enabled"] != null ? (bool)ini["enabled"] : true;
                    _initMonitor.Configure(dc, cd, rs, thr, halve, enabled);
                    // 立绘开关（config ui.portraits_enabled，纯 C# 消费）：与 initiative 同一次拉取带回，
                    // 免得为它单独加一个 RPC。缺失=保持当前值（旧 Python 端没有这个键）。
                    var ui = data?["ui"] as Newtonsoft.Json.Linq.JObject;
                    if (ui == null) ui = data?["data"]?["ui"] as Newtonsoft.Json.Linq.JObject;
                    if (ui?["portraits_enabled"] != null)
                        PortraitService.Enabled = ui["portraits_enabled"].Value<bool>();
                }
                catch { }
            }, 8000);
        }


        /// <summary>MOD 销毁：回主界面，重新初始化时会再次调用 Init</summary>
        public void Destroy()
        {            try
            {
                // 存活监测先停：Destroy 之后帧回调随场景销毁，但显式停掉更清楚，
                // 也杜绝"生命周期尾巴上还想去拉进程"这种动作。
                try { BrainLink.Stop(); } catch { }
                if (_wsClient != null)
                {
                    _wsClient.Stop();
                    _wsClient = null;
                }
                if (_dispatcher != null)
                {
                    _dispatcher.Dispose();
                    _dispatcher = null;
                }
                if (_worldAddDayHandlerIl2Cpp != null)
                {
                    try
                    {
                        g.events.Off(EGameType.WorldAddDay, _worldAddDayHandlerIl2Cpp);
                    }
                    catch { }
                    _worldAddDayHandlerIl2Cpp = null;
                }
                _initMonitor = null;   // 帧回调随场景销毁，停用引用即可
                ChatWindowInstance = null;
                PresenterInstance = null;
                ConfigPresenterInstance = null;
                ContactPresenterInstance = null;
#if AB_UI
                AbPanelProber.Reset();      // 进世界探测复位：重新 Init 后重新探测创建宿主
#endif
                // 注意：按官方示例，Destroy 时不停 Python 常驻进程（重回游戏 Init 会复用）
                P("[AgentLoopBridge] Destroy ok");
            }
            catch (Exception e)
            {
                P("[AgentLoopBridge] Destroy failed: " + e);
            }
        }

        /// <summary>线程感知统一日志：主线程走 Debug.Log（进 Player.log）；后台线程
        /// 走 Console.WriteLine + MelonLoader/Logs/AgentLoopBridge-thread.log——绝不碰
        /// UnityEngine（IL2CPP 非主线程 interop 原生崩溃，见 _mainThreadId 注释）。
        /// 主线程 ID 未捕获前（Init 之前）同样走后台通道，保证任何线程调用都安全。</summary>
        public static void P(string msg)
        {
            string line = "[AgentLoopBridge] " + msg;
            try
            {
                if (_mainThreadId > 0 && Thread.CurrentThread.ManagedThreadId == _mainThreadId)
                {
                    UnityEngine.Debug.Log(line);
                    return;
                }
            }
            catch { }
            // 后台线程 / 主线程 ID 未捕获：纯托管输出（Console 进 MelonLoader 控制台；文件可 grep）
            try { Console.WriteLine(line); } catch { }
            try
            {
                System.IO.File.AppendAllText(
                    "MelonLoader/Logs/AgentLoopBridge-thread.log",
                    DateTime.Now.ToString("HH:mm:ss.fff ") + line + Environment.NewLine);
            }
            catch { }
        }

        // ------------------------------------------------------------------
        // 诊断：整机停用（因果对照实验用，见 DiagSwitches._diag_no_mod.txt）
        // ------------------------------------------------------------------

        /// <summary>本 mod 是否已被整体停用（诊断用；一次性，重启游戏才恢复）。</summary>
        public static bool Suspended { get; private set; }

        /// <summary>诊断：撤销全部 Harmony 补丁（运行期可行；重新打补丁必须重启游戏）。</summary>
        public static void UnpatchAllPatches()
        {
            try
            {
                if (_harmony != null)
                {
                    _harmony.UnpatchSelf();
                    P("[AgentLoopBridge] 【诊断】已撤销全部 Harmony 补丁");
                }
            }
            catch (Exception e) { P("[AgentLoopBridge] 【诊断】撤销补丁失败: " + e.Message); }
        }

        /// <summary>诊断：只撤销"剧情开启钩子"（WorldSystemMgr.OpenMapDrama 的 Postfix）——
        /// 它是唯一挂在**世界逻辑方法**上的补丁，最可能干扰进世界的剧情队列/输入门控。</summary>
        public static void UnpatchDramaHook()
        {
            try
            {
                var m = HarmonyLib.AccessTools.Method(typeof(WorldSystemMgr), "OpenMapDrama");
                if (m == null || _harmony == null) { P("[AgentLoopBridge] 【诊断】剧情钩子未找到，跳过"); return; }
                _harmony.Unpatch(m, HarmonyLib.HarmonyPatchType.All, _harmony.Id);
                P("[AgentLoopBridge] 【诊断】已单独撤销剧情钩子（WorldSystemMgr.OpenMapDrama）");
            }
            catch (Exception e) { P("[AgentLoopBridge] 【诊断】撤销剧情钩子失败: " + e.Message); }
        }

        /// <summary>
        /// 整体停用本 mod：撤掉全部 Harmony 补丁 + 销毁我们的常驻 UI + 停止各帧回调的实质工作。
        /// 目的：做**最干净的因果对照**——"某现象是不是本 mod 引起的"。事件订阅与 WS 连接不动，
        /// 只摘掉"会影响游戏"的部分（补丁/UI）。
        /// </summary>
        public static void SuspendAll(string reason)
        {
            if (Suspended) return;
            Suspended = true;
            P("[AgentLoopBridge] 【诊断】mod 整体停用中：" + reason);
            UnpatchAllPatches();
#if AB_UI
            try { AbContactPanel.DestroyResident(); } catch { }
            try { AbConfigPanel.DestroyResident(); } catch { }
#endif
            P("[AgentLoopBridge] 【诊断】mod 已整体停用（Harmony 已撤、常驻 UI 已销毁）");
        }
    }
}