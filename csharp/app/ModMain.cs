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
using System.Collections.Generic;
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

        // ---------- 存档身份帧的补发/补投（09-18「世界身份丢失」修复，见 OnWsReconnected） ----------
        // world_id 只活在 Python 进程内存里（故意不落盘）：Python 一重启就没了，而补发原先只有
        // save/load 两条事件。没补上的那段窗口里，读写一律落**扁平目录**（不属于任何存档）——
        // 症状是"NPC 看不见本存档的历史"，更重的是下一次存档会把从扁平目录读来的短账本
        // **整份覆盖**进正确存档（静默丢记忆）。09-15 02:13 实机已经发生过一次 Python 中途
        // 重启（同一游戏 PID 起了两次 Python），当时只差一条消息就踩上。
        // 队列规则（未连接排队 / 按序补投 / 超限丢最旧）在 `WorldFrameOutbox` —— 那是零 Unity
        // 依赖的纯逻辑，有独立单测（`csharp/tests/WorldFrameOutboxTests/`）；这里只做接线。
        private WorldFrameOutbox<JObject> _worldOutbox;

        // ---------- 进世界后的「身份补发重试」（10-18「新开存档继承了通讯录」修复） ----------
        // 问题：`EGameType.IntoWorld` 那一刻 `g.world.playerUnit` 可能还没挂上（世界正在重建），
        // 于是 `FillWorldIdentity` 读不到身份 —— Python 那边就不知道"这是哪个存档"了，
        // 而**旧身份一定已经错了**（世界换过了）。所以：进世界读不到身份时，Python 会作废身份
        // （拒绝回答通讯录/会话，一个字都不写，见 ws_channel 的 `_invalidate_world`），
        // 而**这里负责把身份补回去**：逐帧重试读玩家单位，读到就补发一帧 `load_happened`
        // （幂等：第一次已经丢弃过，这次必然是"丢弃 0 个"）并重拉名单。
        // 为什么必须重试而不是等下一次存档：**新存档还没有存档文件**，`save_happened` 要等
        // 第一次自动存档，可能是几分钟后 —— 不补，玩家整局都看不到名单。
        // 为什么读不到时不该沿用旧身份：实机日志里 67 次"未携带 world_id"有 17 次是**人在世界里**
        // 却读不到（世界加载/换场景窗口），这个窗口真实且频繁。
        private int _worldEntrySeq;          // 每次 IntoWorld +1：重试轮次据此自我作废
        private bool _identityRetryActive;   // 同一世界入口只起一轮（避免叠加重试）

        /// <summary>「常驻帧回调」第二行延后多少帧再打（≈1s@60fps）。
        /// 为什么要延：`Init` 末尾那一刻回调刚交给游戏、一次都还没跑，
        /// `idle` 只能显示"刚挂上"；跑过约 1s 之后打出来的才是**真的心跳数值**，
        /// 于是"正常（个位数）"与"挂了但没人叫它（`!N` 或持续变大）"能分开。</summary>
        private const int ReportDelayFrames = 60;

        /// <summary>存档身份帧队列（惰性建：构造点要等 `Init` 里 `_wsClient` 就绪）。
        /// 连接状态与发送动作都从现场取（`() => ...`），所以 Python 重启换 socket 也照样正确。</summary>
        private WorldFrameOutbox<JObject> WorldOutbox
        {
            get
            {
                if (_worldOutbox == null)
                {
                    _worldOutbox = new WorldFrameOutbox<JObject>(
                        () => _wsClient != null && _wsClient.IsConnected,
                        (m, p) => _wsClient.SendRequest(m, p, _ => { }, 10000),
                        s => P("[AgentLoopBridge] " + s));
                }
                return _worldOutbox;
            }
        }

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

        /// <summary>
        /// 「世界真的进来了」——`EGameType.IntoWorld` 那一整段善后**跑完之后**才置真。
        ///
        /// 为什么需要这个位（10-26 真机踩出来的）：
        /// 读档时 `g.world` 与世界数据**在加载屏里就已经可读**（`UnitLookup.Resolve` 能认人），
        /// 但 `IntoWorld` 事件要到加载屏结束才来。诊断/观战若在加载屏里开打，
        /// `OnIntoWorldEvent` 收尾时那句 `DramaGate.ForceResolveAll("世界重载/读档")`
        /// 会**当场把观战票收掉**（真机形态：`发起观战 … 已开打=True` → 下一屏
        /// `WorldMgr:IntoWorld()` → `[NpcDuel] 槽位 1 被外力收掉` → 那场仗静默作废，
        /// 连 `BattleEndMgr.BattleEnd` 都不会来）。
        /// ⇒ 一切"要在世界里打仗"的入口都先看这一位；它是**一次性的**（进程内只置真），
        /// 因为读档时的重建同样会走本事件并把各位复位，不需要它自己会变假。
        /// </summary>
        internal static bool WorldEntered;

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
                // ★11-20：本轮换成工具层拆分后的戳。上一版是 `0915-clean2`，一直没再更新过 ——
                //   它于是既证明不了"新 DLL 进了游戏"，也在排障时把人往旧结论上带。
                //   换戳的规矩：**只在本轮确实改了 DLL 行为时换**，否则它又会变成一句永远不变的废话。
                P("[Build] 1121-earlier4 修日期分隔行堆在末尾：一个 item 可能新增多个子节点（分隔行+气泡），旧代码只收 after-1 漏掉分隔行；改为收集 [before,after) 整段");
                // ★11-20 工具层自检★：反射查 13 个类是否都在**已加载的这份程序集**里。
                //   和上面那行 `[Build]` 的区别：那是一句手写字符串（想说啥写啥），
                //   这里问的是运行时事实 —— 缺一个类就说明跑的是旧 DLL 或编译没带上。
                try { P("[ToolLayer] " + ToolExecutor.DescribeLayout()); }
                catch (Exception tlEx) { P("[ToolLayer] 自检失败（工具层可能没进这份 DLL）: " + tlEx.Message); }
                // ★ 正在运行的是哪个文件 ★（10-26 加，起因：换完 DLL 却查不出为什么新代码没生效）
                //   上面那行 `[Build]` 是**手写的字符串**，从来没人给它加版本号 ⇒ 它证明不了
                //   "加载的是新 DLL"。真正不会撒谎的是**程序集自己在磁盘上的那份文件**：
                //   路径 + 大小 + 最后写时间。排查「我明明换了却没反应」时，先看这一行：
                //     · 路径不是你以为的那份 ⇒ 部署到了错的目录（今天就是栽在这上面）；
                //     · 时间/大小还是旧的 ⇒ 文件没换成功；
                //     · 都对却还是旧行为 ⇒ 进程没重启（.NET 程序集载入后无法卸载）。
                //   读不到就什么都不打（不影响任何功能）。
                //   ⚠ 实测 `Assembly.Location` 在**官方模组加载器**下是空串（它从字节流载入），
                //     所以退一步：按 `ModPaths.DataRoot` 反推 `ModCode\dll\MOD_Jgmg5L.dll` 的
                //     磁盘状态并打出来。它不能证明"内存里跑的就是这份"，但配上进程启动时刻
                //     （`Initialize engine version` 那一行）就能判：磁盘写入时间 > 启动时刻
                //     ⇒ 文件是启动之后才换的，跑的还是旧的（今天就是栽在这上面）。
                try
                {
                    string dllPath = null;
                    try { dllPath = typeof(ModMain).Assembly.Location; } catch { }
                    if (string.IsNullOrEmpty(dllPath))
                    {
                        string dataRoot = ModPaths.DataRoot;
                        if (!string.IsNullOrEmpty(dataRoot))
                        {
                            string cand = System.IO.Path.Combine(dataRoot, "ModCode\\dll\\MOD_Jgmg5L.dll");
                            if (!System.IO.File.Exists(cand))
                            {
                                string up = System.IO.Path.GetDirectoryName(dataRoot);
                                if (!string.IsNullOrEmpty(up))
                                    cand = System.IO.Path.Combine(up, "ModCode\\dll\\MOD_Jgmg5L.dll");
                            }
                            if (System.IO.File.Exists(cand)) dllPath = cand;
                        }
                    }
                    if (!string.IsNullOrEmpty(dllPath))
                    {
                        var asmFile = new System.IO.FileInfo(dllPath);
                        if (asmFile.Exists)
                        {
                            // ★ 11-19：这一行原来是一句**固定模板**（永远写"写入更晚 ⇒ 跑的还是旧代码"），
                            //   于是"进程里是不是新代码"根本判不出来 —— 同一个会话里它照样那么说，
                            //   导致把"没重启"误判成"代码没生效"。现在真的比一次。
                            string verdict;
                            string started = "?";
                            try
                            {
                                var t0 = System.Diagnostics.Process.GetCurrentProcess().StartTime;
                                started = t0.ToString("MM-dd HH:mm:ss");
                                verdict = asmFile.LastWriteTime > t0
                                    ? "★ 晚于进程启动 ⇒ 本进程跑的是**旧代码**，必须整局重启才生效 ★"
                                    : "早于进程启动 ⇒ 本进程跑的就是这份文件";
                            }
                            catch { verdict = "（拿不到进程启动时刻，无法判断）"; }
                            P("[Build] 磁盘上的程序集：" + asmFile.FullName
                              + "  " + asmFile.Length + "B  写入=" + asmFile.LastWriteTime.ToString("MM-dd HH:mm:ss")
                              + "  进程启动=" + started + "  → " + verdict);
                        }
                    }
                }
                catch { }

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
                    // 选项监听自检（09-23）：PatchAll 对不上方法签名时是**静默**跳过的 —— 启动时必须
                    // 有一行说清"这个补丁到底有没有挂上"，否则"没生效"与"没玩到"在日志里长得一样。
                    try { P("[DramaAiOption] CreateOptions 监听自检：" + DramaOptionTapHook.PatchSelfCheck()); }
                    catch (Exception scEx) { P("[DramaAiOption] 选项补丁自检调用失败: " + scEx.Message); }
                    // 10-22 论道诊断探针组（IsCreateProbe / GridProbe / AiActionMove）已全部移除，
                    //   根因与修法见 `docs/archive/2026-10-22-论道静默失败/修法与清理.md`。
                    //   移除原因：探针只为定位「论道静默失败」而存在，根因已定案（传送扣光 residueDay）。
                    //   ⚠ 若将来又要排查同类问题，别照搬旧探针：它们会往 Player.log 写大量行，
                    //     且其中 `day=` 列与 `Discovery` 的关卡无关（曾误导过一次，见该文档「踩坑」）。
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

                    // 9e)战斗结束事件（切磋要跨过战斗才收工具结果）：`BattleEndMgr.BattleEnd(Boolean)`。
                    // 该类型不在本仓库任何反编 dump 里（只有日志栈帧证明它存在），故按名反射手动挂；
                    // 挂不上会降级为轮询 isBattle / UIBattleInfo，不报错。详见 drama/BattleEndHook.cs。
                    try { BattleEndHook.EnsureInstalled(); }
                    catch (Exception bEx)
                    {
                        P("[AgentLoopBridge] BattleEndHook 挂载失败（不影响其它补丁）: " + bEx.Message);
                    }

                    // 9e-2) 组队战后的**实际收尾**（归队 + 写经历）—— 真正会改状态的业务逻辑，
                    //   见 tools/TeamBattleFollowup.cs。
                    //   历史：这里原本还挂过一个 `TeamBattleEventProbe`（只读取证，用途是确认
                    //   `triggerType=6` 对应 `EBattleType.BattleEndFront`）。它的结论已被本收尾
                    //   逻辑直接实现，10-21 随 `TeamProbe` 一起删除。
                    try { TeamBattleFollowup.EnsureInstalled(); }
                    catch (Exception tfEx)
                    {
                        P("[AgentLoopBridge] TeamBattleFollowup 挂载失败: " + tfEx.Message);
                    }

                    // 9e-4) 组队「容器直塞」（2026-10-19 反汇编定案）：条件组选人链在 type=2
                    //   后天气运上必抛（GetLuck 遇 luckData=null 直接 throw，见
                    //   docs/features/team.md §5.7）。本钩子在
                    //   `BattleFunction.AddBattleNPCData(string[])` 前缀里把队伍成员按号位
                    //   直接写进 `battle.data.npcUnits`，跳过引擎选人；`addBattleUnit_3/4/5`
                    //   照常从容器按位取人。没组队时不拦截，普通战斗不受影响。
                    try { TeamContainerInject.EnsureInstalled(); }
                    catch (Exception tiEx)
                    {
                        P("[AgentLoopBridge] TeamContainerInject 挂载失败（不影响其它补丁，仍走条件组）: " + tiEx.Message);
                    }
#endif

                // 9f)赠礼路径探针（`UnitActionRoleGive` 三分支 + `UINPCInfoProp.OnGiveClick/OpenPropsMask`）
                //    09-24 已**退役**：字段口径与分支语义都拿到了（结论留档在 `tools/UnitActionHooks.cs`），
                //    赠送路径随后也改成了"只认引擎返回码"（`ToolExecutor` 赠礼段）。
                //    NPC 面板那条（玩家自己送）始终没触发过 ⇒ 至今零采集，将来要补重新打探针即可。

                // 9g)攻击探针（`UnitActionRoleAttack.IsCreate/OnCreate/Init`）09-24 已定叉并**退役**：
                //    它回答的是"引擎收不收"—— 结论是 attack 那两次被引擎拒绝（返回码 2）且动作没创建，
                //    现场为 NPC 精力 1/80（游戏规则：战斗需要消耗精力，精力不足则无法进行）。
                //    返回值现在由 `ToolExecutor` 的 attack/spar 分支正式消费（非 0 立刻如实失败、
                //    不再挂 60s 等一场不存在的战斗），所以这层打点不再需要。

                // 1) 主线程调度器：每帧回调（游戏主线程）
                _dispatcher = new MainThreadDispatcher();
                // ★11-19 全仓守卫扫描：原先是裸 `g.timer.Frame(...)`（返回值丢弃）★
                //   代价在真机日志里量到了：裸注册每进一次世界多一份、上一份还活着、
                //   `Destroy` 一条都撤不掉 —— 20:25 那份日志里 `npcmon`/`hotkeys`/`diag`/`prober`
                //   从第 3 段世界起恒为 **2.00 次/帧**，而走 FrameTicker 的 `deferq`/`battleflow`
                //   恒为 1.00。改走 FrameTicker 后：同帧由 `Once` 去重、`Destroy` 能撤干净、
                //   停摆能被 `Describe()` 看见。
                //   ★传 owner★ 本回调是**实例方法**：`new MainThreadDispatcher()` 每次进世界都换新的，
                //   只按句柄判活会挂到被 `Dispose()` 掉的旧实例上 ⇒ 主线程调度器静默停摆
                //   ⇒ Python 的每一次工具调用都超时，而日志显示"一切正常"。
                FrameTicker.Ensure(FrameTicker.KDispatcher, "disp", _dispatcher.OnUpdate, _dispatcher);

                // 1c) 组队探针自检（10-17 加）：只打一行，确认探针有没有进游戏。
                //     ★ Init 期同步输出，不依赖 g.timer.Frame / 不依赖是否进世界 ★
                //     起因：早先空有 DLL 替换、探针日志却一条不出，排查花了半天
                //     （先是"部署时机晚于游戏启动"，再是"没进世界 ⇒ 帧回调不跑"）。
                //     有这一行，"新代码到底进没进游戏"不必再靠猜。
                //     （10-21 删掉了 `TeamProbe`，它当时就是缺这一行自检的受害者；
                //       自检这件事本身保留在仍然存在的 `TeamMultiProbe` 上。）
                try { TeamMultiProbe.SelfCheck(); }
                catch (Exception se) { P("[AgentLoopBridge] Multi 自检失败: " + se.Message); }

                // 2) WS 客户端：连 Python 的 WsServer（127.0.0.1，端口读 config.json），单连接全双工
                //    端口不再写死 8766：配置面板允许用户改 network.port，
                //    而 C# 原先恒连 8766 → 用户一改就永久失联（重启游戏也没用，因 Start() 取默认）。
                //    现在两侧同读 config.json，配置改完重启游戏即生效。
                _wsClient = new WsClient(_dispatcher);
                _wsClient.Start("ws://127.0.0.1:" + ModConfigFile.ReadWsPort());
                // WS 全局桥（两种构建模式都要用：AB 面板与 DramaAiOption 经此复用同一连接）
                ChatGlobals.WsClientInstance = _wsClient;
                ContactDuty.Attach(_wsClient);   // 09-10 定案：常驻职责（消息分流/好友镜像）与面板实例解耦
                // 聊天帧缓冲：关窗 / 开别人窗期间推来的群帧与单聊帧先收进队列，
                // 重开窗重放落地之后再消费（见 ChatFrameTee / ChatFrameQueue 文件头）。
                ChatFrameTee.Attach(_wsClient);

                // 3) NPC 主动互动触发监测：日节拍（WorldAddDay）+ Frame兜底，产出 npc_initiative 事件给 Python
                _initMonitor = new NpcInitiativeMonitor(_wsClient);
                // 重连（含配置 UI 触发的 Python 重启）后重拉 initiative 参数——不用整机重启游戏
                _wsClient.Connected += OnWsReconnected;
                // 断线/恢复的**玩家可见提示**不在这里做：它要落到"当前那个对话窗"上，
                // 而 ModMain 手上没有窗口引用（`ChatWindowInstance` 只在代码模式下赋值，
                // 实际走 AB 面板时是 null）。订阅点在 `ChatPresenter.Init`。
                // Frame 兜底（读档当日等 WorldAddDay 未触发场景）
                // 耗时采样（TickProfiler，09-28 卡顿定位）：每帧日历读 + 待开窗重试 +（换日时）候选集全量扫描
                // ★11-19：同理改走 FrameTicker，并传 owner —— `_initMonitor` 每次进世界都是新实例★
                FrameTicker.Ensure(FrameTicker.KNpcMonitor, "npcmon", () =>
                {
                    long __t = TickProfiler.Begin();
                    try { _initMonitor.OnUpdate(); }
                    finally { TickProfiler.End("npcmon", __t); }
                }, _initMonitor);
                // 游戏日事件（主闸）：每游戏日一次
                // ★11-19：与下面 SaveData/IntoWorld 同一处修法 —— 注册前先退订自己上一次那份★
                //   `WorldAddDay` 原先只在 `Destroy` 里退订，`Init` 侧同样只加不减；
                //   而 `_initMonitor` 每次进世界都是**新实例**，旧 handler 绑在旧实例上。
                //   不加这一句的话，"只 Init 不 Destroy"的段（真机日志里出现过）会留下一份
                //   指向死实例的订阅 —— 表变长、且派发时白调一次。
                try
                {
                    if (_worldAddDayHandlerIl2Cpp != null)
                    {
                        try { g.events.Off(EGameType.WorldAddDay, _worldAddDayHandlerIl2Cpp); } catch { }
                        _worldAddDayHandlerIl2Cpp = null;
                    }
                }
                catch { }
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

                // 3.5) 「此刻发不出去的动作」排队器接线（09-24 用户拍板 B 方案）
                //      症状：战斗中 / 剧情链收尾期间发起动作（实测：处置页上让 NPC 赠送灵石），
                //      原生窗会沉进游戏系统队列不出队 → 90 秒后静默超时（"玩家长时间未响应"）。
                //      现在改为：**立即受理 → 此间事了自动执行 → 办完主动告知**（若当时没办成，
                //      也不判失败，退回等待）。判据/状态机在 `DeferredActionCore`（纯逻辑、有单测），
                //      接线在 `DramaGate.DeferredActionQueue`；工具层的闸在 `ToolExecutor.Execute`。
                //      这里注入四件：能不能执行（判据）/ 入队提示 / 办完告知 / 日志。
                DeferredActionQueue.Logger = P;
                // ★ 10-25：状态判据接线★ 五条件判据**已搬到** `state/WorldStateCore.ExecuteBlocker`
                //   （那边四条视角并排，各自的成员与顺序都写了理由）。这里只做两件事：
                //     · 把两个"剧情标记"注入给状态模块 —— `state/` 刻意不依赖 `drama/`
                //       （否则 drama ↔ state 成环，表现为初始化顺序敏感、偶发不生效）；
                //     · 把整条判据挂上去。
                //   注入为空时状态模块会当 false（= DramaGate 的空态，行为与改造前逐字一致），
                //   并打一行 ⚠ —— 保证"漏注入"不可能静默退化。
                try
                {
                    WorldStateGate.Attach(
                        () => { try { return DramaGate.ChainHold; } catch { return false; } },
                        () => { try { return DramaGate.HasPending; } catch { return false; } });
                    P("[AgentLoopBridge] 状态判据已接线（" + WorldStateGate.InstallReport() + "）");
                }
                catch (Exception wsEx) { P("[AgentLoopBridge] 状态判据接线失败: " + wsEx.Message); }
                DeferredActionQueue.WaitBlocker = () =>
                {
                    // 判据唯一实现：`state/WorldStateCore.ExecuteBlocker`（五条，顺序即优先级：
                    // 链收尾 → 战斗中 → 我方挂起 → 玩家被界面占着 → 不在主世界）。
                    try { return WorldStateGate.ExecuteBlocker(); } catch { return ""; }
                };
                DeferredActionQueue.TipShower = (msg) => { try { UITipItem.AddTip(msg, 5f); } catch { } };
                DeferredActionQueue.Notifier = (npc, text) =>
                {
                    // 办完的告知走既有 npc_initiative 通道（intent=action_done）：Python 侧当一次
                    // 主动开口处理（该 NPC 正忙则投成下一步增量）—— 与"主动传音"同一条路。
                    // npc_unit_id：这条路径手里**没有真身**（排队条目不持有 unit 包装），故取
                    // `UnitIdRegistry`（按存档的名字→id 表）。**旧 id 是安全的**：工具层
                    // `ResolveArgExact` 拿到 id 后会回验 unitID 相等，不等就留一行日志、
                    // 回落按名 —— 不会把动作落到别人身上。
                    string uid = "";
                    try { uid = UnitIdRegistry.IdOf(npc); } catch { }
                    try { _wsClient?.SendInitiative(npc, "action_done", text, unitId: uid); } catch { }
                };
                DeferredActionQueue.Attach();
                P("[AgentLoopBridge] 动作排队器已接线（战斗中/链收尾期间的动作改走「此间事了自动办理」）");

                // 3.6) 「战前 / 战中」两态闸（10-25，用户拍板；判据在 `drama/BattleFlowCore.cs`）
                //      症状：NPC 寻仇那条 attack 被排队 → 玩家自己点了同一页上的「战斗」→
                //      排队那条**落刀又开了一次同一页** ⇒ 两场架叠在一起 ⇒ 处置页上点「对话」
                //      拿不到任何新内容、静默无反应。
                //      现在：即将进入战斗期间，**会再开一场仗/会把玩家挪走**的四样
                //      （attack/spar/teleport/summon）当场拒绝、不排队；其余动作照旧排队；
                //      只读一律照常。**不是一刀切禁全部工具**（用户明确要求）。
                //      三个入口：页上有开战按钮（寻仇 100003 / 切磋 212042）、应战窗亮着、
                //      已经在打（`IntoBattle`/`BattleActive`）。
                BattleFlowGate.SetDir(ModPaths.DiagDir);
                BattleFlowGate.Attach();
                try { BattleFlowFuncHook.EnsureInstalled(); } catch (Exception bf1) { P("[BattleFlowHooks] 剧情命令钩子失败: " + bf1.Message); }
                try { BattleStartHook.EnsureInstalled(); } catch (Exception bf2) { P("[BattleStartHook] 开打钩子失败: " + bf2.Message); }
                try { NpcDuel.EnsureInstalled(); } catch (Exception nd) { P("[NpcDuel] 观战收口订阅失败: " + nd.Message); }
                P("[AgentLoopBridge] 战前/战中闸已接线（攻击/切磋/传送/召唤当场拒绝，其余排队）");

                // 3.7) 「玩家忙 / 战斗中」判据的**现场记录器**（11-19，用户报障驱动）
                //      报障原话："检测会频繁显示自己正在忙或者正在处于战斗当中，但是实际不是。"
                //      为什么现有日志查不出来，见 `state/BusyWatch.cs` 头注释（只记结论不记电平 /
                //      没有边沿 / 没有持续时长）。这里只做两件事：注入日志出口 + 挂常驻采样。
                //      采样 1s 一次（不逐帧：那是 09-28 卡顿事故的形状），走 FrameTicker 管生命周期。
                try
                {
                    BusyWatch.Logger = P;
                    FrameTicker.Ensure(FrameTicker.KBusyWatch, "busywatch", () =>
                    {
                        // 用 unscaledTime：游戏暂停时 scaled 会停，而"暂停期间一直判忙"
                        // 恰恰是要抓的形态之一。
                        try { BusyWatch.Sample(Time.unscaledTime); } catch { }
                    });
                    P("[AgentLoopBridge] 忙/战中判据观测已接线（进忙/出忙/原因变化各一行，含原始电平；"
                      + "关掉：数据根放 _diag_no_busy_watch.txt）");
                }
                catch (Exception bwEx) { P("[AgentLoopBridge] 忙判据观测接线失败: " + bwEx.Message); }

                // 存档语义事件：存档=通知 Python 固化对话增量；进世界=通知丢弃
                // 未固化增量（读档=回到存档时刻；关游戏不保存=增量随 Python 进程消失）。
                // 手动/自动存档都走 EGameType.SaveData；读档/新建都走 EGameType.IntoWorld。
                //
                // ★11-19：注册前先退订一次（幂等解毒）★
                //   真机日志里每次进世界 `OnIntoWorldEvent` 都跑 **两遍**（6 处副作用的条数
                //   全部一比一：`load_happened` / 立绘清空 / ToolPanel / UnitIdRegistry /
                //   账本 / 世界已就绪）。而 `g.events` 的订阅是**按事件名存在一张表里**、
                //   派发时把表里每一项都调一遍；我们这边历史上只 `Off` 过 `WorldAddDay`
                //   （见 `Destroy`），`SaveData` / `IntoWorld` 一条都没退订过。
                //
                //   于是每进一次世界就多留一份 handler：`On` 只加不减，表只会变长。
                //   在**注册前**先 `Off` 掉自己上一次那份，无论旧的那份是在哪种时机漏掉的
                //   （`Destroy` 没跑到 / 跑了但没退订 / 游戏自己清了表但我们又加了一份），
                //   最终表里我们的处理器最多只有一份。
                //
                //   为什么不只靠 `Destroy` 里的 `Off`：`Init` 并不保证与 `Destroy` 严格配对
                //   （真机日志里出现过只 `Init` 不见 `Destroy` 的段）。注册点自己清一遍，
                //   是唯一不依赖调用顺序的做法。
                //
                //   `Off` 对不存在的处理器是安全的（返回 false，不抛），所以这是纯幂等操作。
                try
                {
                    if (_saveDataHandlerIl2Cpp != null)
                    {
                        try { g.events.Off(EGameType.SaveData, _saveDataHandlerIl2Cpp); } catch { }
                        _saveDataHandlerIl2Cpp = null;
                    }
                    if (_intoWorldHandlerIl2Cpp != null)
                    {
                        try { g.events.Off(EGameType.IntoWorld, _intoWorldHandlerIl2Cpp); } catch { }
                        _intoWorldHandlerIl2Cpp = null;
                    }
                }
                catch { }
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

                // 4.1) 存活监测：进程层判据 + 退避重生
                //      **必须排在拉起之后** —— 启动宽限期从"刚拉起"那一刻算起，
                //      排在前面会把上一轮遗留的时间差算进来。
                //      用 g.timer.Frame 驱动 = 主线程，故 BrainLink 可直接碰 UI（横幅）。
                BrainLink.Start(_wsClient);
                // ★11-19：改走 FrameTicker（原裸注册，每次进世界叠一份且 `Destroy` 撤不掉）★
                FrameTicker.Ensure(FrameTicker.KBrainLink, "brainlink", BrainLink.OnUpdate);

#if AB_UI
                // 4.2) 常驻帧回调（**不是热键**）：配置面板的 Python 重启状态机 + FastKeyGate 采样。
                //
                // 为什么必须有这一行（09-20 实机事故）：重启状态机原先由 `ConfigPresenter.Update`
                // 驱动，但配置面板的常驻宿主**关闭态根节点是 OFF** 的（防游戏把"UI 层顶部有界面"
                // 当模态 → 屏蔽世界输入），Update 不再运行 ⇒ tick 一起停摆。
                // 当时的修法是把它搬到 `AbHotkeys.OnFrame`，并在注释里写明"由 ModMain 的
                // `g.timer.Frame` 每帧回调驱动"——**但那一行注册从来没写**。
                // 后果：面板里改 network.port 保存 → shutdown 应答 ok=True（旧进程确实退了）
                // → 状态机**再也没有被 tick 过** → 既不拉起新进程、也不报错、也不收口：
                // 界面永远停在"正在重启 Python…"，第二次点保存则因连接已断报"未连接 Python"。
                // （实机实证：Player.log 在 `shutdown 应答 ok=True` 之后**一行都没有**，
                //   agent_loop.log 也停在 `shutdown 已受理`，新进程从未起来。）
                //
                // 与 BrainLink 同款：常驻、不依赖任何面板的激活状态。
                // ★11-19：改走 FrameTicker —— 这是本次全仓扫描修掉的第二处叠注册。★
                //   它叠起来的后果比"多烧点 CPU"重得多：`AbHotkeys.OnFrame` 里装着
                //   通讯录快捷键判定（同帧由 `HotkeyCore.Decide` 自己兜了去重）、
                //   配置面板的 Python 重启状态机、立绘读盘泵、须知面板 ESC —— 全是每帧一次就够的事。
                try { FrameTicker.Ensure(FrameTicker.KHotkeys, "hotkeys", AbHotkeys.OnFrame); }
                catch (Exception hkEx) { P("[AgentLoopBridge] AbHotkeys 帧回调注册失败: " + hkEx.Message); }

                // 4.3) 诊断开关复查（DiagSwitches.FrameTick）—— **09-28 修复**：类头写着"每 ~2 秒
                //      复查一次并即时生效"，但全工程从来没有调用点 ⇒ 所有 `_diag_*.txt`
                //      运行期开关（no_mod/no_monitor/no_fastkey/lazy_panels/initiative_force…）
                //      实际全部失效。这里补上注册（内部 120 帧节流，常态成本可忽略）。
                try { FrameTicker.Ensure(FrameTicker.KDiag, "diag", DiagSwitches.FrameTick); }
                catch (Exception dgEx) { P("[AgentLoopBridge] DiagSwitches 帧回调注册失败: " + dgEx.Message); }

                // 4.3.1) 进世界探测器的帧回调（09-28 补注册）：`AbPanelProber.OnFrame` 原先只由
                //      `UiComposer.Compose` 注册，而 Compose 全工程已无调用点（死代码）⇒
                //      `_diag_no_panels.txt` / `_diag_lazy_panels.txt` 两个哨兵实际无人消费。
                //      该回调不创建任何东西、只服务这两个诊断哨兵，常态零开销（带采样便于对账）。
                try
                {
                    FrameTicker.Ensure(FrameTicker.KProber, "prober", () =>
                    {
                        long __t = TickProfiler.Begin();
                        try { AbPanelProber.OnFrame(); }
                        finally { TickProfiler.End("prober", __t); }
                    });
                }
                catch (Exception prEx) { P("[AgentLoopBridge] AbPanelProber 帧回调注册失败: " + prEx.Message); }

                // 4.4) 卡顿定位采样器（09-28 用户拍板方案 A）：每 5s 一行 tick 耗时汇总。
                //      定位收工后把 `TickProfiler.Enabled` 改 false 或删文件 + 调用点。
                TickProfiler.AnnounceOnStart();
#endif

                // 4.5) Mod AB 预载：直载 bundle 并把 UI 预制体注入 g.res 官方索引
                //（必须在 UI 构建前——AbContactPanel 的 g.res.Load 与 OpenUI 系面板都依赖这些 key）
                ModAbRes.PreloadAll();

                // 5) 游戏内对话 UI（尽力而为：初始化失败只打日志，不拖垮对话主流程——chat_cli 链路照常）
                //    ★ 核心托管 UI 类型的 Il2Cpp 注册：**这一步不能省**。
                //    AB 三面板装配时对 presenter 做 `GetComponent<X>() ?? AddComponent<X>()`，
                //    X 全是本程序集的托管 MonoBehaviour；没注册 ⇒ Unhollower 造不出它的
                //    Il2CppSystem.Type ⇒ `GetComponent<T>` 的泛型静态构造抛
                //    TypeInitializationException，**且 CLR 缓存该失败 ⇒ 该 T 本次进程内永久不可用**。
                //    09-17 事故：这里原本的 UiComposer 调用随"UI 步骤精简"被删（全工程零调用方），
                //    结果全进程没人注册任何 presenter → 传音簿装配炸（入口=HUD「传」按钮）+ 回滚绕过 UIMgr
                //    → 登记项泄漏 → 世界输入被门控（人物动不了、UI 打不开）。见 docs/archive/2026-09-17-群聊编排/设计定稿.md §13.4。
                //    幂等：三面板 EnsureResident 里还会在 OpenUI 之前兜底再调一次。
                try { UiComposer.EnsureCoreTypesRegistered(); }
                catch (Exception ue) { P("[AgentLoopBridge] 核心 UI 类型注册异常（面板装配会失败）: " + ue); }

                // ★11-19：把常驻帧回调的在册情况打出来★
                //   这一行是"驱动死没死"的现场证据。以前这个故障完全静默——队列有活、驱动没了、
                //   日志里一个字都没有。现在每次进世界都能看到在册几条、各自 idle 多少帧；
                //   下次再出现"动作排了队不执行"，第一件事就是看这行跟 `[FrameTicker] … 停摆` 有没有出现。
                //
                //   ★为什么立刻打一次、还要再延后打一次★
                //   立刻打的这一行证明"挂上了几条"（旧实现只打这一行，而且 idle 恒为 `?` ——
                //   因为此刻回调刚交出去、游戏还没进下一帧，必然一次都没跑过）。
                //   idle 有意义的时刻是"跑过若干帧之后"，所以再延后约 60 帧（≈1s）打第二行：
                //   那时每条的 idle 应当是个位数 —— 若是 `!N`（挂了 N 帧一次都没跑）或数字持续变大，
                //   就是真故障。两行合起来才区分得开"刚挂上"与"挂了但没人叫它"。
                try { P("[AgentLoopBridge] 常驻帧回调（刚挂上）：" + FrameTicker.Describe()); } catch { }
                try
                {
                    FrameTicker.Ensure(FrameTicker.KDiagReport, "tickreport", () =>
                    {
                        // 只在那条延后回调自己"挂了够久"之后才打 —— 它每帧都被叫，
                        // 所以这里用挂载帧号做闸（而不是自己数帧，省一个字段）。
                        if (FrameTicker.FramesSinceAttach(FrameTicker.KDiagReport) < ReportDelayFrames) return;
                        FrameTicker.Stop(FrameTicker.KDiagReport);      // 一次性：打完就撤
                        try { P("[AgentLoopBridge] 常驻帧回调（" + ReportDelayFrames + " 帧后）：" + FrameTicker.Describe()); }
                        catch { }
                    });
                }
                catch { }

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
        /// （本作存在与玩家同名的 NPC，名字不可当键）。取不到就不写该字段，Python 侧沿用旧命名空间。
        ///
        /// ★ `player_unit_id`（09-17 新增）= 同一个值的**原样**副本，给 Python **当 id 用**
        ///   （注入 `target_id`：模型写「玩家」时要精确落到玩家这个人身上）。
        ///   为什么不能拿 `world_id` 兼任：Python 侧对它做了 `sanitize_world_id()`
        ///   （percent-encode + 截断，当目录名用），非 ASCII 的 id 会被写成转义串 ——
        ///   那不是 id，注进去就是一条查不到的键。</summary>
        /// <summary>
        /// 此刻能否取到**存档身份**（玩家 unitID）—— 即"世界已加载、玩家单位存在"。
        ///
        /// 用途（09-20）：通讯录面板据此决定**要不要发起注定为空的那次 RPC**，并把 ⚠ 文案说准。
        ///
        /// 为什么必须问这个、而不是"面板开着就拉"：`world_id` 的实现是
        /// `g.world.playerUnit.data.unitData.unitID`（见 `UnitSnapshot.PlayerUnitId`），
        /// **玩家单位不存在时它是空的**。此时 Python 侧 `_blocked()` 为真，`list_contacts`
        /// 返回 `[]` 表示"我拒绝回答" —— 发这一次请求拿不到任何信息（我们**事先就知道**会空），
        /// 只是白等一个 8s 超时回调，还让日志看起来像"拉过一次但没有朋友"。
        ///
        /// ⚠ 它**不是**"Python 已定档"的判据：身份取得到 ≠ Python 已经收到（帧还在路上时
        /// 同样取得到）。那种情形的空回包由 `world_known=false` 兜住
        /// （见 `ContactDuty.WorldKnown`）；本属性只挡"压根无从可发"的那一类。
        /// </summary>
        public static bool WorldIdentityAvailable
        {
            get
            {
                try { return !string.IsNullOrEmpty(UnitSnapshot.PlayerUnitId()); }
                catch { return false; }
            }
        }

        /// <summary>
        /// 存档身份注入（三态；见上一条 summary 的 `player_unit_id` 说明）。
        ///
        /// ★★ 读不到玩家单位时，帧里**写什么**取决于这是哪一类帧（10-18，勿退回两态）★★
        ///   · `worldEvent: true`（`load_happened` = 进世界 / `save_happened`）：写
        ///     `identity:"unknown"` —— 明说"我知道世界事件发生了，但我读不到身份"。Python 据此
        ///     **作废**旧身份（而不是沿用！旧行为正是这样把上一个存档的通讯录端给新存档的，
        ///     见 ws_channel `_invalidate_world`）。
        ///   · `worldEvent: false`（`world_hello` = 重连补发）：**什么都不写** = 沿用现状。
        ///     这条必须留着：Python 中途重启（配置面板保存 / 崩溃重生）时身份就靠它补，而主菜单里
        ///     本来就读不到身份（实机 67 次空帧里 50 次是这种），作废会把正常局面毁掉。
        /// 一句话：**"进世界"这件事本身就是"旧身份作废"的证据**，与能不能读到新身份无关。
        ///
        /// ★ `worldEntry`（10-18 复核加）与 `worldEvent` **不是一回事**，勿合并：
        ///   · `worldEntry: true` 只在**进世界**（`load_happened`）传 —— 它是**唯一**能把
        ///     `_expectedWorld` 置空的场合：世界换了，本机对"我在哪个档"的认知必须重来。
        ///   · `save_happened` 只传 `worldEvent`（留三态诊断），**不传** `worldEntry`：
        ///     存档**不是**"世界换了"的证据（你能存的档就是你在的那个世界）。若在这里也置空，
        ///     一次"存档那一刻恰好读不到玩家单位"就会让**此后所有回包判废**（`WorldMatches`
        ///     对空基准 fail-closed）⇒ 面板一直显示"尚未取得存档身份"、`list_sessions` 也刷不动，
        ///     直到下一次存档/重连 —— 而 Python 那边好好的。本机日志里这种缺身份存档一次没出现
        ///     （`save_happened 未携带 world_id` = 0 次），但代价不对称，按 fail-safe 处理。</summary>
        private static void FillWorldIdentity(JObject p, bool worldEvent = false, bool worldEntry = false)
        {
            try
            {
                string wid = UnitSnapshot.PlayerUnitId();
                if (!string.IsNullOrEmpty(wid))
                {
                    p["world_id"] = wid;
                    p["player_unit_id"] = wid;      // ★ 原样，勿 sanitize（Python 侧那份是目录名）
                    ContactDuty.ExpectWorld(wid);   // 回包判废的基准（见 ContactDuty.WorldMatches）
                }
                else if (worldEvent)
                {
                    p["identity"] = "unknown";      // ★ 世界事件 + 读不到 → 宣告"旧身份作废"
                    if (worldEntry) ContactDuty.ExpectWorld(null);  // 只有进世界才把基准也清掉
                }
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
        /// 活/冻 agent 的对话增量固化进 session 账本（save_happened → flush_all），
        /// **同时**把 C# 的 unitID 登记表落盘（`UnitIdRegistry.Flush`）—— 两者是同一次握手
        /// 产出的两份数据，寿命语义必须一致：存档=固化，不存档=两份一起丢。
        /// 玩家不存档就退游戏 → 增量从未写盘 → 读档后 NPC 不记得、身份也回到按名解析。</summary>
        private void OnSaveDataEvent(ETypeData e)
        {
            // 登记表落盘是**本地**动作，与 WS 通不通无关，所以单独 try —— 不能因为 Python
            // 那边断了就连本地这份也不写（`Flush` 内部无待写内容时直接返回，代价为零）。
            try { UnitIdRegistry.Flush(); }
            catch (Exception fe) { P("[AgentLoopBridge] 存档时写 unitID 登记表失败: " + fe.Message); }
            // 剧情账本同理：它是**本局草稿**，存档就是"这一局到此为止"的自然固化点。
            // 与对话增量、unitID 登记表同寿命语义（存档=固化，不存档=随进程消失）。
            try { DramaLedger.Flush(); }
            catch (Exception lfe) { P("[AgentLoopBridge] 存档时写剧情账本失败: " + lfe.Message); }
            // 通讯录关系层快照（known.json）：**存档 = 固化**，与上面两份同寿命语义。
            // 为什么要在这里再推一次、而不只靠"开面板时推"：玩家完全可能从没打开过
            // 通讯录就存档 —— 而那个人一旦死了，`GetUnit=null` 让"他来过"再也查不回来。
            // 内容没变时 `SyncKnownContacts` 会整份跳过（签名比对），代价接近零。
            try { ContactDuty.SyncKnownContacts(RelationNetwork.ToContactSnapshot(RelationNetwork.CollectKnownUnits())); }
            catch (Exception ke) { P("[AgentLoopBridge] 存档时同步通讯录快照失败: " + ke.Message); }
            try
            {
                JObject p = new JObject();
                FillWorldIdentity(p, true);     // 世界事件：读不到身份要**明说**（见其 summary 的三态）
                bool sent = SendWorldFrame("save_happened", p);
                P("[AgentLoopBridge] save_happened " + (sent ? "已发送" : "未连接 → 已排队")
                  + "（存档触发对话固化）world=" + (string)p["world_id"]);
            }
            catch (Exception evEx)
            {
                P("[AgentLoopBridge] save_happened 发送失败: " + evEx.Message);
            }
        }

        /// <summary>进世界事件（EGameType.IntoWorld，读档/新建都走）：通知 Python 丢弃全部
        /// 未固化增量并销毁活/冻 agent（load_happened → discard_all）——世界已（重）载，
        /// 对话回到上次存档时刻；关游戏不保存的场景由增量随 Python 进程消失天然覆盖。
        /// 同时携带存档身份（world_id）→ Python 切换存储命名空间（换存档不再串号）。
        ///
        /// ★ 读不到身份时（10-18）：帧里宣告 `identity:"unknown"` → Python **作废**旧身份
        ///   （不许沿用！那正是"新开存档继承了通讯录"的根因），同时本方法启动**逐帧重试**
        ///   把它补回来（`StartIdentityRetry`）。</summary>
        private void OnIntoWorldEvent(ETypeData e)
        {
            try
            {
                _worldEntrySeq++;               // 上一轮重试据此自我作废（见 StartIdentityRetry）
                int entrySeq = _worldEntrySeq;
                // ★11-19：把"这是第几次进世界"打进日志★
                //   起因是核对日志时发现一处对不上的量值：会话 3/5 各出现 **两次** `load_happened`，
                //   而在会话开头那一次里又是"未连接排队"→"已发送"两行并排。
                //   没有这个计数，"一行=一次调用"与"两行=两次调用"分不开（早先差点把
                //   `WorldMgr:IntoWorld()` 的**调用栈装饰行**当成调用次数，是误判）。
                //   现在每行自带 `#N`：同一 N 出现两次 = 同一次调用打了两行（就应当合并）；
                //   两个不同 N = 真的被调了两次（那才需要查为什么重复派发）。
                P("[AgentLoopBridge] IntoWorld 处理器第 " + entrySeq + " 次进入"
                  + "（同一 N 出现两次 = 一次调用两行；两个不同 N = 真的调了两次）");
                JObject p = new JObject();
                FillWorldIdentity(p, true, true);   // 进世界：三态 + 清基准（唯一会清基准的场合）
                bool sent = SendWorldFrame("load_happened", p);
                P("[AgentLoopBridge] load_happened " + (sent ? "已发送" : "未连接 → 已排队")
                  + "（世界重载，未固化增量作废）world=" + (string)p["world_id"]
                  + (WorldIdentityAvailable ? "" : "（★ 读不到玩家单位 → 已宣告 identity=unknown，"
                                              + "Python 会作废旧身份；本机将逐帧重试补发）"));
                // ★ 身份还没读到 → 起一轮重试（读到就补发一帧 load_happened）。
                //   必须在这里就起：新存档没有存档文件，`save_happened` 可能几分钟后才来，
                //   而这段时间里 Python 处于"未定档"（拒绝回答）—— 不补，玩家整局看不到名单。
                if (!WorldIdentityAvailable) StartIdentityRetry(entrySeq);
                // ★ 静态表活过世界重载 —— 必须先收口在飞的动作，再刷别的。
                // 挂起的确认窗/战斗不会跟到新世界里：结局永远不会来，而 DramaGate 的 busy 键
                // （`ModIds.AiActionGate`）是**所有动作工具共用**的，槽位不释放 = 之后每次送礼/
                // 邀约/传功/切磋都被挡成「已有一个行动在等待玩家响应」，整套动作系统瘫到重启。
                // 同一形状的旧事故：立绘像素缓存 `_mem` 存场景级纹理，世界重载后留僵尸条目（见下）。
                try { BattleOutcome.ForceClose("世界重载/读档"); }
                catch (Exception spEx) { P("[AgentLoopBridge] BattleOutcome 收口失败: " + spEx.Message); }
                try { DramaGate.ForceResolveAll("世界重载/读档"); }
                catch (Exception dgEx) { P("[AgentLoopBridge] DramaGate 强制收口失败: " + dgEx.Message); }
                // ★ 组队描述复位（10-18）★ 文案表 `allText` 会随世界重载从配置重新初始化，
                //   我们运行时覆盖的那几句（气运描述）全部丢失。而心跳是按"成员签名变了才刷"的
                //   —— 同一个存档同一批队友时签名不变 ⇒ 永远不重刷 ⇒ 描述退回配置表那句静态话。
                //   复位后下一次心跳必定重刷一遍。放在 DramaGate 收口之后：它更便宜，且与窗口无关。
                try { TeamTool.ResetTickState(); } catch { }
                // 换存档后立刻重拉通讯录镜像/会话索引/隐藏名单：ContactStore 是静态缓存，
                // 不重拉就会把**上一个存档**的手动好友继续喂给候选集与 NPC 面板按钮状态。
                //
                // ★ 顺序即正确性（10-18）★ 先**整表作废**、再重拉：
                //   ① 作废（`ContactDuty.ResetForWorld`）：这些名字全部属于刚离开的那个世界；
                //      "等回包覆盖"不够 —— 回包回来之前面板会先把它们渲染出来
                //      （`ContactPresenter.Show()` 第 ③ 步"先把 Store 里已有的镜像并进来"），
                //      玩家在新档里就看到上一个档的好友（这正是用户报的那个症状）。
                //   ② 重拉：即使旧档的回包**跨档迟到**（面板刚开过 / 连接正在更替），C# 也会按
                //      回包自带的 `world_id` 判废（`ContactDuty.WorldMatches`）—— 所以下面那句
                //      "同一连接按序处理 ⇒ 必然读到新名单"不再是唯一防线。
                try { ContactDuty.ResetForWorld(); }
                catch (Exception rce) { P("[AgentLoopBridge] 换存档后作废通讯录镜像失败: " + rce.Message); }
                try { ContactDuty.RequestManualContacts(); ContactDuty.RequestSessions(); ContactDuty.RequestHidden(); ContactDuty.RequestKnownContacts(); }
                catch (Exception ce) { P("[AgentLoopBridge] 换存档后重拉通讯录失败: " + ce.Message); }
                // 未读登记同理（10-18）：红点/横幅记的是**上一个世界**的传音，在新档里毫无意义
                // （内容仍在各 NPC 的账本里，不会丢）；不清就会为新档里不存在的人亮红点。
                try { UnreadStore.ClearAll(); MapMainContactButton.SetUnread(UnreadStore.Any); }
                catch (Exception uc) { P("[AgentLoopBridge] 换存档后清空未读登记失败: " + uc.Message); }
                // 立绘的名字→unitID 备忘录同理（同一个中文名在新存档里是另一个 unitID）：
                // 不清就会把上一存档的脸贴到这一存档的人身上，且不报错。
                try { PortraitCache.ClearNameIndex(); }
                catch (Exception pe) { P("[AgentLoopBridge] 换存档后清空立绘名字映射失败: " + pe.Message); }
                // ★ 立绘**像素缓存**（`_mem`）也必须清（09-19 修复，勿删）★
                // 它是"名字备忘录"的孪生兄弟，原先只清了备忘录、漏了这个 —— 而后果更硬：
                // `_mem` 里存的是**场景级 Unity 纹理**，世界重载时被销毁，静态字典却活下来
                // ⇒ 留下"key 在、纹理已死"的僵尸条目，该 unitID 同进程内**永久读不出头像**
                // （`TryGet` 判失败而 `RequestLoad` 见 key 就拒绝入队，盘上 PNG 再也读不回来）。
                // 与 `UnitIdRegistry.ResetForWorld` / `ChatFrameQueue.ClearAll` 同一个钩子、同一个理由。
                try { PortraitCache.ResetForWorld(); }
                catch (Exception pce) { P("[AgentLoopBridge] 换存档后清空立绘像素缓存失败: " + pce.Message); }
                // 关窗期间缓冲的帧同理：换存档后旧世界的帧不该灌进新档（room_id 是高水位复用的，
                // 新档里可能又出现同名群；NPC 名也会撞 —— 残留帧会造成"没说过的话"）。
                try { ChatFrameQueue.ClearAll(); }
                catch (Exception qe) { P("[AgentLoopBridge] 换存档后清空帧缓冲失败: " + qe.Message); }
                // 回合登记表同理（10-17）：旧世界里"还在跑"的回合在新档里永远不会收口
                // ⇒ 不清就会把新档某个同名 NPC 的忙标**永久点亮**（且删除/压缩入口被误锁）。
                try { TurnRegistry.ClearAll("换存档"); }
                catch (Exception tre) { P("[AgentLoopBridge] 换存档后清空回合登记表失败: " + tre.Message); }
                // 放大立绘浮层同理（10-18）：宿主挂在**游戏根 Canvas** 下，而它随场景销毁
                // ⇒ 静态引用变僵尸条目（看着"还开着"、实际节点已死，也不再重建）。
                // 只清引用、不 Destroy（节点已随场景没了）。原生窗那条路同理要复位标志位，
                // 否则新档里 `IsOpen` 恒真 ⇒ ESC 每按一次都去 CloseUI 一个不存在的窗。
                try { BigPortrait.ResetForWorld(); }
                catch (Exception bpe) { P("[AgentLoopBridge] 换存档后重置放大立绘失败: " + bpe.Message); }
                // 工具侧边栏同理（10-19）：面板与顶栏那颗钮都挂在对话宿主的 BG 下，随场景销毁
                // ⇒ 静态引用变僵尸。**这里必须调 `ResetForWorld` 而不是 `Reset`**：后者只收显隐、
                // 不清引用，于是新档里 `Build` 看到 `_root != null` 会以为"宿主还在"直接返回
                // ⇒ 面板永远不重建，而且日志里一句都不打（唯一线索是 `HasPanel` 恒 false）。
                try { ToolPanel.ResetForWorld(); }
                catch (Exception tpe) { P("[AgentLoopBridge] 换存档后重置工具侧边栏失败: " + tpe.Message); }
                // 名字→unitID 的**全局登记表**同理（本表比上面两个更要紧：它**落了盘**，
                // 不重置就会把上一存档的 unitID 当成这一存档的权威 —— 而 unitID 换档即失效，
                // 最坏情形是那个 id 在新档里恰好是**另一个人**，静默错人）。
                try { UnitIdRegistry.ResetForWorld(); }
                catch (Exception re) { P("[AgentLoopBridge] 换存档后重置 unitID 登记表失败: " + re.Message); }
                // 剧情相遇分段器攒的段同理：一段相遇的内容属于**刚离开的那个世界**
                // （页里的对方编号换档即失效），跟着存档一起丢 —— 不丢的话，新档里点「对话」
                // 会把上一档那场相遇的原文喂给模型（最坏情形：那个 unitID 在新档是另一个人）。
                try { DramaSegmentRecorder.OnWorldChanged(); }
                catch (Exception sgEx) { P("[AgentLoopBridge] 换存档后清空剧情段失败: " + sgEx.Message); }
                // 剧情账本同理（10-25）：条目里的归属编号与"哪场战斗开过窗"都是**上一档**的事实，
                // 留着最坏会把上一档的剧情写进这一档某场战斗的履历（静默错内容，比什么都没有更糟）。
                // 盘上那份按 world 首行校验，换档后整份作废、由本档重新累积。
                try { DramaLedger.OnWorldChanged(); }
                catch (Exception lgEx) { P("[AgentLoopBridge] 换存档后清空剧情账本失败: " + lgEx.Message); }
                // 「此刻发不出去」的排队动作同理：条目里的目标是**刚离开那个世界**的人
                // （名字/编号换档即失效，最坏情形是那个人在新档里是另一个人），
                // 而且"此间事了"永远不会来了（那场战斗随旧世界一起没了）⇒ 一律作废。
                try { DeferredActionQueue.OnWorldChanged(); }
                catch (Exception dqEx) { P("[AgentLoopBridge] 换存档后作废排队动作失败: " + dqEx.Message); }
                // 战前/战中两态同理：旧世界那场仗的"应战窗亮着/已确认"在新世界里毫无意义。
                // **学到的开战按钮编号保留**（那是配表事实，不随存档变 —— 见 BattleFlowGate 头注释）。
                try { BattleFlowGate.OnWorldChanged(); }
                catch (Exception bfEx) { P("[AgentLoopBridge] 换存档后复位战前状态失败: " + bfEx.Message); }
                // 忙判据观测同理：旧世界"忙了多久"对新世界毫无意义，留着会让出世界后
                // 第一行打出"忙了 900s"这种假时长（统计窗口不清 —— 那是进程级配置）。
                try { BusyWatch.OnWorldChanged(); } catch { }
                // 面板闸门 ③ 的「世界曾就绪」粘滞位同理：新世界的 HUD 未必已建，
                // 不复位就会把"上一世界曾就绪"当成通行证（重开进世界早期的 OpenUI 窗口）。
                // ★ 战斗场景加载**不**走本事件 ⇒ 粘滞位能正确跨场景留存（那正是修复要的）。
#if AB_UI
                try { AbPanelProber.EnteringWorld(); }
                catch (Exception abEx) { P("[AgentLoopBridge] 换存档后复位面板闸门失败: " + abEx.Message); }
                // 「须知」面板（10-23）：宿主挂在 g.root 下，随场景销毁 ⇒ 静态引用变僵尸。
                // 只复位**本次进入**相关的位（自动弹出是否已安排）；`_autoDoneThisRun` 刻意不清
                // —— 同一进程里反复读档不该反复弹同一条公告（版本没变就是"看过了"）。
                // 随后安排一次自动弹出：三重闸（每进程一次 / 静置 3s / 玩家不忙）见 NoticePanel 文件头。
                try
                {
                    NoticePanel.OnIntoWorld();
                    NoticePanel.ScheduleAutoShow(3f);
                }
                catch (Exception ntEx) { P("[AgentLoopBridge] 须知面板进世界复位失败: " + ntEx.Message); }
#endif
                // 剧情账本的诊断（10-25）：进世界时打一行，排障时一眼看出
                // "盘上那份读回来了多少条、有没有被裁过、是不是属于别的存档"。
                // 不带任何判定作用 —— 只是让"账本是不是空的"这件事不用去翻两万行日志。
                try { P("[AgentLoopBridge] " + DramaLedger.Report()); }
                catch (Exception lgR) { P("[AgentLoopBridge] 剧情账本诊断失败: " + lgR.Message); }

                // ★ 最后一句：到此为止"世界真的进来了" ★
                //   必须在所有 ForceResolveAll / ResetForWorld 之后 —— 上面任何一句都可能
                //   收掉在飞的观战票，而本位的唯一用途就是"别在加载屏里开打"。
                WorldEntered = true;
                try { P("[AgentLoopBridge] 世界已就绪（IntoWorld 善后跑完，WorldEntered=true）"); } catch { }
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
            try
            {
                // ① 补发存档身份：Python 重启后 world_id 只活在它的内存里 → 丢了，而补发原先
                //    只有 save/load 两条事件。不补，那段窗口里的对话会落到"谁都不属于"的扁平
                //    目录（既看不见本存档历史，下一次存档还会把短账本覆盖进真存档 —— 静默丢记忆）。
                SendWorldHello();
                // ② 补投断线期间攒下的 save/load（WsClient 未连接时是直接回 ok:false、不排队）。
                //    顺序有讲究：hello 在前（帧按发送顺序到），若身份确实变了，Python 会借
                //    hello 完成"丢弃上一个存档的活体"；随后那条 load_happened 再跑一次是幂等的。
                FlushPendingWorldFrames();
                // ③ ★ 重拉通讯录镜像 / 会话索引（09-20 实机事故修复，勿删）★
                //    与 `OnIntoWorldEvent`（进世界/读档，那里有重拉）对齐 —— 这一条原先**没有**，
                //    于是两条路径不对称：读档走 `load_happened` → 会重拉（自愈）；而
                //    "Python 重启后重连、人已经在世界里"只走 `world_hello` → **不重拉**。
                //    后果（用户实证）：Python 冷启后 `world_id` 尚在内存里为空，此刻面板若拉过一次
                //    `list_contacts`，拿到的是"未定档"的空表；等 hello 补上身份后，**没有任何代码
                //    会再去拉一次** ⇒ 手动加的好友整局都残缺（只剩关系层那一个人），必须手动
                //    关开面板才好 —— 这正是用户说的"有时候只显示一个，有时候又都显示"。
                //    顺序有讲究：必须在 `SendWorldHello()` **之后** —— 同一连接按序处理，hello
                //    先到 ⇒ Python 的 world_id 已就位 ⇒ 这次拉取才拿得到真名单。
                //    （两条世界身份帧"未连接则排队"，而这两条 RPC 未连接时直接失败，故也放在
                //      FlushPendingWorldFrames 之后，避免抢在身份前面发出去。）
                try { ContactDuty.RequestManualContacts(); ContactDuty.RequestSessions(); ContactDuty.RequestHidden(); ContactDuty.RequestKnownContacts(); }
                catch (Exception ce) { P("[AgentLoopBridge] 重连后重拉通讯录失败: " + ce.Message); }
            }
            catch (Exception e)
            {
                P("[AgentLoopBridge] 重连补发存档身份失败: " + e.Message);
            }
        }

        /// <summary>进世界时读不到身份 → 逐帧重试，读到就**补发一帧 `load_happened`**（10-18）。
        ///
        /// 配套的另一半在 Python：`load_happened` 带 `identity:"unknown"` 时它会**作废**存档身份
        /// （拒绝回答通讯录/会话，一个字都不写），于是"新存档读到上一个存档的通讯录"不可能再发生
        /// —— 但代价是**作废期间玩家看不到名单**，所以这里必须把身份补回去，否则要等到下一次
        /// 存档（新档可能几分钟都不存档）或下一次重连。
        ///
        /// 四条出口（缺一不可）：
        ///   · 读到了 → 补发 + 重拉三个名单，本轮结束；
        ///   · `_worldEntrySeq` 变了（玩家又换了世界）→ 本轮作废，绝不能拿旧世界读到的人去报新世界；
        ///   · `Destroy()`（回主界面）→ 帧回调**随场景销毁**（与 `_initMonitor` 同一个既有理由），
        ///     本轮自然结束；
        ///   · 超时（~60s）→ 放弃。**放弃也是安全的**：Python 停在作废态（不会端出旧档数据），
        ///     面板继续如实显示"尚未取得存档身份"，等下一次存档/重连再补。
        ///
        /// 为什么补发的是 `load_happened` 而不是 `world_hello`：前者语义是"世界刚载入，未固化增量
        /// 作废"—— 补发这一刻它**仍然成立**（我们还在完成同一次进世界），所以**幂等**（第一次已经
        /// 丢弃过，这次必然是"丢弃 0 个"）；`world_hello` 则是"连接事件、只切不丢"，拿它补发等于把
        /// "进世界"降级成"随口报一下"，而且它必须保留"读不到就沿用"的老行为（见 FillWorldIdentity）。</summary>
        private void StartIdentityRetry(int entrySeq)
        {
            if (_identityRetryActive) return;      // 同一入口只起一轮（Init/Destroy 抖动时防叠加）
            _identityRetryActive = true;
            DateTime deadline = DateTime.UtcNow.AddSeconds(60);
            try
            {
                g.timer.Frame(new Action(() =>
                {
                    try
                    {
                        if (entrySeq != _worldEntrySeq) { _identityRetryActive = false; return; }
                        string wid = null;
                        try { wid = UnitSnapshot.PlayerUnitId(); } catch { }
                        if (string.IsNullOrEmpty(wid))
                        {
                            if (DateTime.UtcNow > deadline)
                            {
                                _identityRetryActive = false;
                                P("[AgentLoopBridge] 身份重试放弃（60s 仍读不到玩家单位）→ Python 保持"
                                  + "「未定档」（不会端出旧档数据），面板如实提示；下一次存档/重连会再补发");
                            }
                            return;                 // 还没就绪：下一帧再看
                        }
                        _identityRetryActive = false;
                        JObject q = new JObject();
                        FillWorldIdentity(q, true, true);       // 读到 → 帧里带真身份（补完那次进世界）
                        bool sent = SendWorldFrame("load_happened", q);
                        P("[AgentLoopBridge] 身份迟到 → 补发 load_happened "
                          + (sent ? "已发送" : "未连接 → 已排队") + "（世界重载语义，幂等）world=" + wid);
                        // 补发之后必须**重拉三个名单**：作废期间那几次拉取拿回的是"拒绝回答"
                        // （world_known=false，C# 一个字都没写），此刻镜像还是空的。
                        try { ContactDuty.RequestManualContacts(); ContactDuty.RequestSessions(); ContactDuty.RequestHidden(); ContactDuty.RequestKnownContacts(); }
                        catch (Exception ce) { P("[AgentLoopBridge] 补发身份后重拉通讯录失败: " + ce.Message); }
                    }
                    catch (Exception ex)
                    {
                        _identityRetryActive = false;           // 出异常不再自我循环
                        P("[AgentLoopBridge] 身份重试异常: " + ex.Message);
                    }
                }), 1, false);
            }
            catch (Exception fe)
            {
                _identityRetryActive = false;                   // 帧回调挂不上（不在世界/定时器已停）
                P("[AgentLoopBridge] 身份重试挂载失败: " + fe.Message);
            }
        }

        /// <summary>连接建立时补发的存档身份（`world_hello`）。
        ///
        /// ★ 与 save/load 两条帧的**语义差别**（Python 侧实现见 `ws_channel` 的 world_hello 分支）：
        ///   只切命名空间，**不丢弃**未固化增量、也不触发固化。重连时 Python 那个进程可能还活着、
        ///   手里正握着玩家刚聊完还没存档的内容 —— 用 `load_happened` 补发会把它们凭空抹掉。
        ///   真换了档（玩家在断线期间读了别的档）才由 Python 丢弃那些"账本属于别人"的活体。
        /// 未进世界（主菜单 / 尚未读档）时 `FillWorldIdentity` 自然不写 `world_id`，
        /// Python 侧沿用现状 + INFO —— 不猜、不报错（开机首次连接就是这个形态）。</summary>
        private void SendWorldHello()
        {
            JObject p = new JObject();
            FillWorldIdentity(p);
            bool sent = SendWorldFrame("world_hello", p);
            P("[AgentLoopBridge] world_hello " + (sent ? "已发送" : "未连接 → 已排队")
              + "（补发存档身份：Python 重启/重连后不再丢档）world=" + (string)p["world_id"]);
        }

        /// <summary>把一条"存档身份帧"（world_hello / load_happened / save_happened）交给 Python：
        /// 连着就直发；未连接就**排队**，连上后由 `FlushPendingWorldFrames` 按原顺序补投。
        ///
        /// 为什么不能像原先那样 `SendRequest` 了事：`WsClient.SendRequest` 在未连接时**直接回调
        /// `ok:false` 且不排队**，而这两个调用点的回调是 `_ => { }`（丢弃回执）却照样打印"已发送"
        /// —— 于是"进世界早于 WS 连上"或"Python 正在重启"这类时序会把帧**静默**吞掉，整局都没有
        /// 存档身份（所有写入落扁平目录 = 跨档串号）。实机日志里连接与 load_happened 相隔 7~45s，
        /// 这个窗口真实存在。
        /// 返回是否**直发**成功（false = 已排队）。队列规则全在 `WorldFrameOutbox`（有单测）。</summary>
        private bool SendWorldFrame(string method, JObject p)
        {
            return WorldOutbox.Send(method, p);
        }

        /// <summary>连接恢复后按原顺序补投排队中的存档身份帧（只投一次；投完即清）。
        /// 仍未连接时一条都不动（队列留在原地等下一次连接事件）—— 那是 `WorldFrameOutbox.Flush`
        /// 的契约，所以这里不做判断。</summary>
        private void FlushPendingWorldFrames()
        {
            int n = WorldOutbox.Flush();
            if (n > 0) P("[AgentLoopBridge] 补投 " + n + " 条存档身份帧（连接恢复）");
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
                    // 捎话优先队列（11-13）：与配置同一次重连/保存后就位。
                    // 为什么挂在这里而不是单开一个重连回调：**Python 重启后这份名单一定过期**
                    // （它活在 Python 内存里，重启即空），而"重连"与"保存配置"是仅有的两个
                    // 能保证 list_* 类 RPC 可用且身份已就位的时刻（② 那里解释了顺序）。
                    _initMonitor.RequestMailPending();
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
                    // 忙标「慢」提示阈值（11-21）：超过这么多秒就在忙标上追加已等秒数。
                    // 与立绘开关同一次拉取 —— 都是"玩家可见、纯 C# 消费"的 ui 块字段。
                    // 缺失 = 保持当前值（旧 Python 端没有这个键，不该把默认值冲掉）。
                    if (ui?["busy_hint_after"] != null)
                        ChatPresenter.ConfigureBusyHintAll((float)ui["busy_hint_after"]);
                }
                catch { }
            }, 8000);
        }


        /// <summary>MOD 销毁：回主界面，重新初始化时会再次调用 Init</summary>
        public void Destroy()
        {            try
            {
                // ★11-19：主动撤掉全部常驻帧回调★
                //   这是本次故障修复的另一半。老实现十几处 `g.timer.Frame(...)` 一条都没 Stop 过，
                //   全靠"帧回调随场景销毁"——而真机 Player.log 里 `Destroy ok` 之后体检表还在继续打，
                //   说明那个假设从没验证过。官方模组模板与参考实现都在这里 `g.timer.Stop(cor)`。
                //   放在最前面：此时本体还没拆，但"这一局结束了"这件事已经成立。
                //   撤干净之后，"句柄在册 = 确实在跑"这句话才成立，`Init` 里的重挂也才不可能重复。
                try { FrameTicker.StopAll("回主界面 Destroy"); } catch { }
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
                // ★11-19：`SaveData` / `IntoWorld` 也要退订★
                //   这两个订阅从 09-xx 加进来起，`Destroy` 里就一直没退过 —— 只退 `WorldAddDay`
                //   是当初只写了那一处留下的不对称。后果是真机日志里 `OnIntoWorldEvent` 每次
                //   进世界跑两遍（6 处副作用条数一比一，见 `Init` 里注册处的长注释）。
                //
                //   为什么必须成对：`g.events` 的订阅是按事件名存在一张表里的，`On` 只往里加、
                //   派发时把表里每一项都调一遍。谁订阅谁退订，否则表只会越来越长。
                //
                //   注意顺序：先 `Off` 再置 null。反过来的话 `Off` 拿不到处理器引用，
                //   表里那一份就永久留下了 —— 这正是"看起来写了退订、实际没退"的经典写法错误。
                if (_saveDataHandlerIl2Cpp != null)
                {
                    try { g.events.Off(EGameType.SaveData, _saveDataHandlerIl2Cpp); }
                    catch { }
                    _saveDataHandlerIl2Cpp = null;
                }
                if (_intoWorldHandlerIl2Cpp != null)
                {
                    try { g.events.Off(EGameType.IntoWorld, _intoWorldHandlerIl2Cpp); }
                    catch { }
                    _intoWorldHandlerIl2Cpp = null;
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