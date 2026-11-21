/// <summary>
/// WebSocket 客户端 —— 连 Python 的 WsServer（127.0.0.1:8766），单连接全双工。
///
/// 分层分权：C# 是"双手"，本类只做「传输 + 路由」，不碰游戏逻辑：
///  - 后台线程收发 JSON 帧（WsTransport：纯托管 RFC6455，替代原生组件版 ClientWebSocket，见 WsTransport.cs 头注释），断线自动重连
///  - request（get_context / call_tool）→ Dispatcher.Enqueue 主线程 →
///    GameContext / ToolExecutor 执行 → 回 response（req_id 配对）
///  - event（step / text_delta / npc_reply / compact_result）→ Dispatcher.Enqueue 主线程 →
///    （未来 UI 渲染，现在打日志）
///  - 玩家消息（未来游戏 UI 输入）→ SendPlayerMessage 发出
///  - NPC 主动开口（NpcInitiativeMonitor 触发检测）→ SendInitiative 发出
///
/// 协议见 agent_loop/ws_channel.py 与 docs（request/response + event）。
/// </summary>
using System;
using System.Collections.Generic;
using System.IO;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;

namespace AgentLoopBridge
{
    public class WsClient : IDisposable
    {
        private readonly MainThreadDispatcher _dispatcher;
        private readonly GameContext _context = new GameContext();
        private readonly ToolExecutor _tools = new ToolExecutor();

        private volatile WsTransport _ws;
        private readonly object _sendLock = new object();
        private volatile bool _running;
        /// <summary>本实例是否**成功连上过**（`Disconnected` 的门闸，见其注释）。</summary>
        private volatile bool _everConnected;
        private Thread _thread;
        private const int BUFFER_SIZE = 64 * 1024;

        // C#→Python 方向 RPC（get_history 等 UI 读请求）：req_id → 回调 TCS，
        // 后台接收线程 SetResult，回调经 Dispatcher 转主线程执行。两边 req_id 各查各的表，互不串。
        private readonly Dictionary<string, TaskCompletionSource<JObject>> _pending =
            new Dictionary<string, TaskCompletionSource<JObject>>();
        private readonly object _pendingLock = new object();
        private int _reqSeq;

        // 模态确认窗的延迟 response：req_id → slot。工具弹 UICustomDramaDyn 后憋住 response，
        // 玩家点击 → DramaGate.Resolve → OnDramaResolved 反查 req_id 补发（Python 零改动，
        // ws_channel 的 request 只认 req_id 配对不在乎等多久）。断线时清空（future 已随连接死亡）。
        private readonly Dictionary<string, int> _deferred = new Dictionary<string, int>();
        /// <summary>挂起中的**动作类**工具归属：req_id → [npc, tool]。
        /// 模态点完那一刻才算"动作完成"，`OnDramaResolved` 据此补发 ActionWatcher 通知。
        /// 与 `_deferred` 同锁、同生命周期（断线一起清）。</summary>
        private readonly Dictionary<string, string[]> _deferredAction = new Dictionary<string, string[]>();

        /// <summary>
        /// UI 事件出口（step / text_delta / npc_reply / compact_result）。
        /// 触发线程 = 主线程（本类收到 event 帧后经 Dispatcher.Enqueue 转动，OnUiEvent 即主线程回调）；
        /// UI 层订阅即可，本类不依赖任何 UI 类型。
        /// </summary>
        public event Action<JObject> UiEvent;

        /// <summary>
        /// 连上事件：每次成功建立连接触发一次（主线程，经 Dispatcher 转入）。
        /// 断线自动重连（RunLoop 每 2s 一轮）后同样会再次触发 —— 消费方（如配置面板
        /// 「重启 Python 后重连」、ModMain 的 initiative 配置重拉）据此刷新状态。
        /// </summary>
        public event Action Connected;

        /// <summary>
        /// 断开事件：连接**曾经建立过**、随后断了，触发一次（主线程，经 Dispatcher 转入）。
        ///
        /// 为什么要有它（11-20）：此前只有 `Connected`，所以"连接断了"这件事在 UI 层
        /// **完全不可见** —— 玩家看到的现象是"发了消息一直没回"，而屏幕上没有任何提示
        /// 能让人分辨"模型在思考"和"根本连不上了"。真机日志里那 8 次
        /// `get_context` 干等 120 秒正是这个形态。
        ///
        /// 与 `Connected` 严格配对，且**只在真的断过之后发**：初次连接失败（Python 没起）
        /// 不算断开 —— 那种情况 `Connected` 从未触发过，玩家也不该看到"断线了"。
        /// 由 `_everConnected` 把关。
        /// </summary>
        public event Action Disconnected;

        /// <summary>是否已建立连接（收发线程更新，供 UI 轮询查询）</summary>
        public bool IsConnected => _ws != null && _ws.IsOpen;

        // 断线重连间隔：常规 2s；配置 UI 重启 Python 期间由 ConfigPresenter 调成 0.3s，
        // 让「新进程开始监听 → C# 连上」的等待从最坏 ~2.3s 缩到 ~0.3s（拉起后即快速探一次）。
        // 注：连不上时的失败尝试本身要 ~2s（本机对无监听端口的 connect 约 2s 才回拒绝），
        // 所以快速重试只是收窄「就绪后才发现」的窗口，不改变单次连接的固有延迟。
        private volatile int _retryDelayMs = 2000;

        /// <summary>切换断线重连节拍（true=快速 0.3s，供 Python 重启编排用；用完必须还原）。</summary>
        public void SetFastRetry(bool on)
        {
            _retryDelayMs = on ? 300 : 2000;
            ModMain.P("[WsClient] 重连节拍=" + _retryDelayMs + "ms");
        }

        /// <summary>连接目标 URL。端口由调用方（ModMain）按 config.json 下发；
        /// 传空则回落到 <see cref="ModConfigFile.DefaultPort"/>（端口常量的唯一来源）。</summary>
        private string _url;

        /// <summary>默认连接 URL（端口常量的唯一来源 = ModConfigFile.DefaultPort）</summary>
        private static string DefaultUrl()
        {
            return "ws://127.0.0.1:" + ModConfigFile.DefaultPort;
        }

        /// <summary>当前连接目标的端口（供「端口被改」提示用；改端口需整机重启游戏）</summary>
        public int ConnectedPort
        {
            get
            {
                try
                {
                    var u = new Uri(_url ?? DefaultUrl());
                    if (u.Port > 0) return u.Port;
                }
                catch { }
                return ModConfigFile.DefaultPort;
            }
        }

        public WsClient(MainThreadDispatcher dispatcher)
        {
            _dispatcher = dispatcher;
            _url = DefaultUrl();
        }

        public void Start(string url = null)
        {
            if (string.IsNullOrEmpty(url)) url = DefaultUrl();
            _running = true;
            _url = url;
            // 模态确认窗结果补发（DramaGate 回调在主线程触发；Send 自带 _sendLock 线程安全）
            DramaGate.OnResolved += OnDramaResolved;
            // 长等槽位的心跳（切磋要跨过一场战斗）：Python 据此滑动它的 RPC deadline，
            // 界面据此知道"还在等"而不是"帧丢了"。见 OnDramaStillPending。
            DramaGate.OnStillPending += OnDramaStillPending;
            _thread = new Thread(() => RunLoop(url)) { IsBackground = true, Name = "AgentLoopWsClient" };
            _thread.Start();
            ModMain.P("[WsClient] starting, url=" + url);
        }

        // ---------- 连接与接收（后台线程） ----------
        private void RunLoop(string url)
        {
            while (_running)
            {
                try
                {
                    // ws://host:port[/path] → WsTransport（TcpClient + RFC6455 握手/帧，纯托管）
                    var uri = new Uri(url);
                    int port = uri.Port > 0 ? uri.Port : 80;
                    string path = string.IsNullOrEmpty(uri.AbsolutePath) ? "/" : uri.AbsolutePath;
                    var ws = new WsTransport();
                    ws.Connect(uri.Host, port, path);
                    _ws = ws;
                    ModMain.P("[WsClient] connected: " + url);
                    _everConnected = true;
                    FireConnected();
                    ReceiveLoop(ws);
                }
                catch (Exception e)
                {
                    ModMain.P("[WsClient] connection error: " + e.Message);
                }
                finally
                {
                    _ws = null;
                    // 断线：丢弃全部待补发的延迟 response（Python 侧 future 已随连接死亡，补发无主）。
                    // 长等槽位（切磋跨战斗）的心跳也随连接一起停了 —— Python 那边按"心跳断了"
                    // 在 90s 内收口成请求超时，不会无限挂着。战斗结果本身救不回来（req_id 已无主），
                    // 但槽位与归因凭证在 C# 侧由各自的过期/收口路径清掉。
                    lock (_pendingLock) { _deferred.Clear(); _deferredAction.Clear(); }
                    // ★ 11-20：通知 UI 层"断了"（玩家可见提示的唯一来源）★
                    // 只在**曾经连上过**时发：初次连不上（Python 没起）不算断线，
                    // 那种情况 `Connected` 从没触发过，弹"断线了"只会更让人困惑。
                    // 放在 `_ws = null` 之后：订阅方在回调里查 `IsConnected` 必须已经是 false。
                    if (_everConnected) FireDisconnected();
                }
                if (!_running) break;
                Thread.Sleep(_retryDelayMs); // 断线重连（节拍见 SetFastRetry）
            }
        }

        private void ReceiveLoop(WsTransport ws)
        {
            while (_running)
            {
                string text;
                try
                {
                    text = ws.ReadMessage();
                }
                catch (Exception)
                {
                    break; // 断开
                }
                if (text == null) break;
                try
                {
                    HandleMessage(ws, text);
                }
                catch (Exception e)
                {
                    ModMain.P("[WsClient] handle error: " + e.Message);
                }
            }
        }

        /// <summary>连上事件经 Dispatcher 转主线程广播（订阅方多在碰 UI/g，非主线程不可碰）。</summary>
        private void FireConnected()
        {
            try
            {
                _dispatcher.Enqueue(() =>
                {
                    try
                    {
                        Connected?.Invoke();
                    }
                    catch (Exception e)
                    {
                        ModMain.P("[WsClient] Connected 回调异常: " + e.Message);
                    }
                    return null;
                });
            }
            catch (Exception e)
            {
                ModMain.P("[WsClient] FireConnected enqueue 异常: " + e.Message);
            }
        }

        /// <summary>断开事件的广播（经 Dispatcher 转主线程，与 <see cref="FireConnected"/> 对称）。
        ///
        /// 同样必须走 Dispatcher：订阅方（`ChatPresenter`）要碰窗口与 UI，后台线程直接调
        /// IL2CPP 的 UI 会炸得莫名其妙。异常在这里吞掉并留痕 —— 断线通知失败
        /// 绝不能反过来把重连循环打断。</summary>
        private void FireDisconnected()
        {
            try
            {
                _dispatcher.Enqueue(() =>
                {
                    try
                    {
                        Disconnected?.Invoke();
                    }
                    catch (Exception e)
                    {
                        ModMain.P("[WsClient] Disconnected 回调异常: " + e.Message);
                    }
                    return null;
                });
            }
            catch (Exception e)
            {
                ModMain.P("[WsClient] FireDisconnected enqueue 异常: " + e.Message);
            }
        }

        // ReadMessage（帧读取）已移入 WsTransport.ReadMessage——见 WsTransport.cs。

        // ---------- 路由 ----------
        private void HandleMessage(WsTransport ws, string text)
        {
            JObject msg;
            try { msg = JObject.Parse(text); } catch { return; }
            string type = (string)msg["type"];

            if (type == "request")
            {
                string reqId = (string)msg["req_id"];
                string method = (string)msg["method"];
                JObject p = msg["params"] as JObject ?? new JObject();
                // ★ 11-20：分段计时，专治"Python 报 get_context 120s 超时，而 C# 侧零线索" ★
                //
                // 真机 8 次 `RPC get_context 超时（120.0s，等 C# 主线程无应答）`，而 C# 侧
                // 只有一个 `disp.job` 的**总**耗时（那里面混着所有 job），看不出这 30~120 秒
                // 到底是"排在队列里等主线程"还是"真的在执行"。两者修法完全不同：
                //   · 排队久  → 主线程被别的活占着（要找那个活），或 job 太多要限流；
                //   · 执行久  → get_context 内部某一段慢（LayerInfo 那类全图扫描？）。
                // `wait` = 入队到开始执行；`exec` = 真正跑 `ExecuteRequest`。
                // 只对 get_context / call_tool 打（其余是心跳类，高频且无意义）。
                bool _trace = method == "get_context" || method == "call_tool";
                long _tEnq = _trace ? TickProfiler.Begin() : 0L;
                // 排队耗时要写在**主线程开始执行的那一刻**，而读取在主线程（`task.Result` 之后）。
                // 用数组而不是普通局部变量：lambda 捕获的是同一个引用，主线程写入、这里读出。
                double[] _waitMs = _trace ? new double[1] : null;
                // 主线程执行（碰 g.world），再回 response
                var task = _dispatcher.Enqueue(() =>
                {
                    if (_trace) _waitMs[0] = TickProfiler.ElapsedMs(_tEnq);
                    return ExecuteRequest(method, p);
                });
                // ★ 09-27：执行侧抛异常**也必须回一帧** ★
                //   此前这里让异常直接往上抛：本方法退出 ⇒ **一帧不发** ⇒ Python 的 future 挂满
                //   120s 才判「请求超时」，而真正的错因被整条吞掉（真机 09-27：`Fail("缺少 items…")`
                //   撞上下方 queued 那行的崩溃，模型只看到"超时"，随后把没办成的事讲成"已交割"）。
                //   异常本身 Dispatcher 已留痕（`[Dispatcher] 主线程任务异常`）；这里补的是给
                //   Python/模型的那一帧明确失败 —— 回合按"工具失败"正常收口，模型可自纠重发。
                JObject response;
                try { response = (JObject)task.Result; }
                catch (Exception e)
                {
                    try { ModMain.P("[WsClient] " + method + " 执行异常，已回错误帧：" + e.Message); } catch { }
                    Send(ws, new JObject
                    {
                        ["type"] = "response",
                        ["req_id"] = reqId,
                        ["ok"] = false,
                        ["data"] = null,
                        ["error"] = "工具执行异常（C# 侧，详见 Player.log）：" + e.Message,
                    });
                    return;
                }
                // ★ 11-20：分段耗时落日志（见上面 `_tEnq` 处的说明）★
                // 阈值只用来避免刷屏：正常 get_context 排队+执行也就几十毫秒。
                // 真机上"Python 报超时"的那几次，这里必须能看到到底是哪一段。
                if (_trace)
                {
                    try
                    {
                        double _wait = _waitMs[0];
                        double _total = TickProfiler.ElapsedMs(_tEnq);
                        double _exec = _total - _wait;
                        if (_total > 300.0)
                        {
                            ModMain.P("[WsClient] ★慢请求★ " + method + " 总=" + _total.ToString("F0")
                                      + "ms（排队=" + _wait.ToString("F0") + "ms 执行=" + _exec.ToString("F0")
                                      + "ms）npc=" + ((string)p["npc_id"] ?? "-")
                                      + (p["roster"] is JArray ra ? " 同席=" + ra.Count : ""));
                        }
                        TickProfiler.AddMs("ws.wait." + method, _wait);
                        TickProfiler.AddMs("ws.exec." + method, _exec);
                    }
                    catch { }
                }
                // 模态确认窗：工具弹窗等玩家点选，此刻憋住 response（req_id → slot 暂存），
                // 玩家点击 → DramaGate.Resolve → OnDramaResolved 补发。不发 = Python future 挂起 = LLM 不说话。
                if (response["__pending__"]?.Value<bool>() == true)
                {
                    int slot = response["__slot__"].Value<int>();
                    string dn = response["__npc_id__"]?.ToString() ?? "";
                    string dt = response["__tool__"]?.ToString() ?? "";
                    lock (_pendingLock)
                    {
                        _deferred[reqId] = slot;
                        if (dn.Length > 0) _deferredAction[reqId] = new string[] { dn, dt };
                    }
                    return;
                }
                response["req_id"] = reqId;
                Send(ws, response);
            }
            else if (type == "event")
            {
                string ev = (string)msg["event"];
                // 白名单：只把这几个转发到 UI 线程。群聊的 room_say / room_state 必须在这里 ——
                // 漏了的话表现是"Python 一切正常、日志里群聊跑得好好的，但群窗一个气泡都不出"，
                // 且没有任何报错（帧在传输层就被静默丢了）。
                // ★ `stats_update` 必须在这里（09-21 修复）：此前一直被漏，回合收口的统计帧
                //   在传输层就被静默丢弃 ⇒ 直播对话中底部统计条永远不刷新，只有关窗重开
                //   （open_chat 响应自带 stats 基线）才有数。ChatPresenter 的
                //   `case "stats_update"` 分支此前是收不到帧的死代码。
                if (ev == "step" || ev == "text_delta" || ev == "npc_reply" || ev == "compact_result"
                    || ev == "compact_summary"
                    || ev == "room_say" || ev == "room_state" || ev == "stats_update")
                {
                    var payload = (JObject)msg.DeepClone();
                    _dispatcher.Enqueue(() => { OnUiEvent(payload); return null; });
                }
            }
            else if (type == "response")
            {
                // C#→Python request 的应答：按 req_id 解掉 pending TCS（各查各的表，编号撞了也不串）
                string reqId = (string)msg["req_id"];
                if (reqId != null)
                {
                    TaskCompletionSource<JObject> tcs;
                    lock (_pendingLock)
                    {
                        if (_pending.TryGetValue(reqId, out tcs)) _pending.Remove(reqId);
                        else tcs = null;
                    }
                    tcs?.TrySetResult((JObject)msg.DeepClone());
                }
            }
        }

        /// <summary>在主线程执行 request（GameContext / ToolExecutor 业务不变），返回 response。
        /// protected：自测工程 FakePythonLink 经此注入「模拟 Python 的 call_tool/get_context」
        /// （支柱①工具测试），走与真机完全相同的执行链。</summary>
        protected JObject ExecuteRequest(string method, JObject p)
        {
            if (method == "get_context")
            {
                string npcId = (string)p["npc_id"] ?? "";
                // 群聊：Python 随帧带上同席名单（`roster`），C# 据此产出 `raw.others[]`。
                // **名单的真相在 Python**（将来是 Room.members）—— 这里只认它给的，不自己猜
                // "谁在场"：另存一份必然漂移，而且回合可能在 C# 关窗之后还在跑，那时 C# 的
                // "当前场合"已经失效，Python 的房间仍然准确。
                // 单聊不发这个键 → roster 为空 → raw 里没有 others，载荷与改动前逐字节相同。
                var roster = new List<string>();
                var rosterArr = p["roster"] as JArray;
                if (rosterArr != null)
                {
                    foreach (var t in rosterArr)
                    {
                        string s = t == null ? null : t.ToString();
                        if (!string.IsNullOrEmpty(s)) roster.Add(s);
                    }
                }
                // 同席者的**精确身份** `{名字 → unitID}`（与 roster 同一次上行）。
                // ★ 为什么需要：`BuildOthers` 原先只能 `Resolve(名字)` —— **全图按名扫**，
                //   重名时命中第一个，而它回填的 `entry["name"]` 在重名时与查询串**逐字相同**
                //   ⇒ Python 与模型**根本无法察觉**拿到的是另一个人的境界/宗门/好感。
                //   有了 id 就走 `ResolveEx(name, id)` 的 ②unitID精确 分支，零歧义。
                // 缺项/老 Python 不发 ⇒ 那一条回落按名（行为与本功能上线前一致）。
                var rosterIds = new System.Collections.Generic.Dictionary<string, string>();
                var idsObj = p["roster_ids"] as JObject;
                if (idsObj != null)
                {
                    foreach (var kv in idsObj)
                    {
                        string k = kv.Key == null ? "" : kv.Key.Trim();
                        string v = kv.Value == null ? "" : kv.Value.ToString().Trim();
                        if (k.Length > 0 && v.Length > 0) rosterIds[k] = v;
                    }
                }
                return new JObject { ["type"] = "response", ["ok"] = true,
                                     ["data"] = _context.GetL1(npcId, roster, rosterIds,
                                                               (string)p["npc_unit_id"] ?? "") };
            }
            if (method == "call_tool")
            {
                string name = (string)p["name"] ?? "";
                JObject args = p["arguments"] as JObject ?? new JObject();
                // npc_id（Python 起随帧带上）= 发起这次调用的 NPC。动作类工具成功执行后，
                // ActionWatcher 要在**这一刻**（动作真的跑完了）判断是否把对话 UI 推到玩家眼前。
                string npcId = (string)p["npc_id"] ?? "";
                // npcId 一并交给工具层：动作工具"此刻发不出去"时要按这个身份排队
                // （办完的告知也发给他 —— `DeferredActionQueue` → `npc_initiative/action_done`）。
                JObject res = _tools.Execute(name, args, npcId);
                // 模态确认窗标记原样上交（HandleMessage 据此憋住 response），不走 ok/data/error 包装
                if (res["__pending__"]?.Value<bool>() == true)
                {
                    // 模态要等玩家点选才是"动作完成"：把归属信息随挂起项一起存下，
                    // 由 OnDramaResolved 在点完那一刻补发通知（下面的字段不会被上行，仅内部用）
                    res["__npc_id__"] = npcId;
                    res["__tool__"] = name;
                    return res;
                }
                bool ok = res["success"]?.Value<bool>() ?? false;
                // ★ 排队受理（`queued`）**不是"动作完成"**（09-24）★：动作此刻还没执行（等战斗链收场），
                //   在"完成即开窗"那条规则里放行它，等于在玩家正忙着的时候凭空推一个对话窗上来；
                //   真正的完成时刻由排队器自己发 `action_done` 告知（见 ActionWatcher 的说明）。
                // ★ 09-27 修：`res["data"] != null` **判不出 JSON null** —— Newtonsoft 里它是
                //   `JValue(Null)` 实例（JToken 的 `!=` 是值比较，不与 C# null 相等），紧接着
                //   的 `["queued"]` 就是"对 JValue 取子键" ⇒ `InvalidOperationException:
                //   Cannot access child value on JValue`。真机 09-27：`Fail("缺少 items…")`
                //   正是不带 data 的失败帧 ⇒ 崩在这里 ⇒ 无响应 ⇒ Python 120s 超时。
                //   改成**只认对象**：非对象（缺席 / JSON null / 标量）一律当"没有 queued"。
                var resData = res["data"] as JObject;
                bool queued = resData != null && resData["queued"] != null
                              && resData["queued"].Value<bool>();
                if (ok && !queued) ActionWatcher.OnActionDone(npcId, name);
                return new JObject
                {
                    ["type"] = "response",
                    ["ok"] = ok,
                    ["data"] = ok ? res["data"] : null,
                    ["error"] = ok ? null : res["error"],
                };
            }
            return new JObject { ["type"] = "response", ["ok"] = false, ["error"] = "unknown method: " + method };
        }

        /// <summary>
        /// 模态确认窗结果补发（DramaGate.OnResolved 回调，主线程触发）：
        /// 反查 slot → req_id，按普通 call_tool 的 ok/data/error 形态包装后补发。
        /// 幂等：断线已清空 / 未知 slot 时静默丢弃。
        /// </summary>
        private void OnDramaResolved(int slot, JObject result)
        {
            string reqId = null;
            string[] action = null;
            lock (_pendingLock)
            {
                foreach (var kv in _deferred)
                {
                    if (kv.Value == slot) { reqId = kv.Key; break; }
                }
                if (reqId != null)
                {
                    _deferred.Remove(reqId);
                    if (_deferredAction.TryGetValue(reqId, out action)) _deferredAction.Remove(reqId);
                    if (action == null) action = null;
                }
            }
            if (reqId == null) return;
            var ws = _ws;
            if (ws == null) return;
            bool ok = result["success"]?.Value<bool>() ?? false;
            // 挂起的是动作类工具：玩家刚把模态点完 = **此刻**动作才真正完成
            // → 补发"动作完成"通知（同格则自动开窗，见 ActionWatcher）
            if (ok && action != null)
            {
                // 例外：**等过一场战斗**的收口（切磋）不抢着开窗。ActionWatcher 那套规则的
                // 前提是"点完同意窗就开着"（`_openedByConsent`，ActionWatcher.cs:141-147），
                // 而收口时刻现在后移到战斗结束后 —— 玩家屏幕上还压着战后窗，再盖一层 1:1 窗
                // 正好撞上"对话窗后开盖住剧情窗"那条还没验过的 z-order 问题。
                // 改走未读红点 + 顶部横幅（那条路 ActionWatcher 里本来就现成）。
                bool crossed = (result["data"]?["crossed_battle"]?.Value<bool>()) ?? false;
                if (crossed)
                    ModMain.P("[WsClient] 动作完成但这场是打完战斗才回的 → 不自动开窗，走未读+横幅 npc=" + action[0]);
                else ActionWatcher.OnActionDone(action[0], action[1]);
            }
            var resp = new JObject
            {
                ["type"] = "response",
                ["ok"] = ok,
                ["data"] = ok ? result["data"] : null,
                ["error"] = ok ? null : result["error"],
                ["req_id"] = reqId,
            };
            Send(ws, resp);
        }

        /// <summary>长等槽位的心跳（`DramaGate.OnStillPending`，主线程）。
        ///
        /// 为什么要发：切磋应战之后这条 RPC 要等一场**时长不可知**的战斗。Python 那边不能
        /// 把超时放大到小时级 —— 那样对端真失联时人要干等；也不能留 120s —— 那样一场慢一点的
        /// 切磋就会被掐成「请求超时」，而几分钟后真结果回来时已无人认领（future 被 pop 掉，
        /// 唯一正确的结果被静默丢弃）。所以改成**滑动**：C# 每 30s 报一次"我还在等"，
        /// Python 收到就把 deadline 往后推；报停了 = 对端没了，90 秒内收口。
        ///
        /// 顺带把同一件事广播给界面：对话窗的忙标自愈（`BusyStaleSeconds=240`）判据是
        /// "距最后一个帧多久"，而战斗期间 Python 一帧都不发 —— 不喂这条，4 分钟后 UI 会自己
        /// 清成空闲，玩家再发一句就撞上还在跑的回合。</summary>
        private void OnDramaStillPending(int slot)
        {
            string reqId = null;
            string npcId = "";
            lock (_pendingLock)
            {
                foreach (var kv in _deferred)
                    if (kv.Value == slot)
                    {
                        reqId = kv.Key;
                        string[] act;
                        if (_deferredAction.TryGetValue(kv.Key, out act) && act != null && act.Length > 0)
                            npcId = act[0] ?? "";
                        break;
                    }
            }
            if (reqId == null) return;        // 已被收口/断线清表：不发无主心跳
            var ws = _ws;
            if (ws == null) return;           // 掉线期间静默：Python 侧靠"心跳断了"自己收口
            string phase = DramaGate.PhaseOf(slot);
            Send(ws, new JObject
            {
                ["type"] = "pending",
                ["req_id"] = reqId,
                ["phase"] = phase,
            });
            _dispatcher.Enqueue(() =>
            {
                OnUiEvent(new JObject { ["event"] = "tool_pending", ["npc_id"] = npcId, ["phase"] = phase });
                return null;
            });
        }

        /// <summary>UI 事件出口统一走这里（日志 + UiEvent 广播）。
        /// protected：自测工程 FakePythonLink 的注入也要过这条真路径（日志/时序与真机一致）。</summary>
        protected void OnUiEvent(JObject payload)
        {
            // 未来：游戏内 UI 渲染。现在打日志。
            string ev = (string)payload["event"];
            if (ev == "npc_reply")
            {
                bool initiative = payload["initiative"]?.Value<bool>() ?? false;
                ModMain.P("[WsClient] " + (initiative ? "（NPC 主动传音）npc_reply: " : "npc_reply: ") + payload["text"]);
            }
            else if (ev == "step")
            {
                string kind = (string)payload["kind"];
                ModMain.P("[WsClient] step(" + kind + "): " + (payload["name"]?.ToString() ?? payload["text"]?.ToString() ?? ""));
            }
            // text_delta：未来逐字渲染用，现已并入 npc_reply 全文，此处不单独输出

            // 转发给 UI 层（游戏内对话 UI 订阅；触发线程=主线程，见 UiEvent 注释）
            UiEvent?.Invoke(payload);
        }

        /// <summary>玩家消息入口（未来游戏 UI 调用；现在供诊断/测试）。
        /// virtual：自测工程 FakePythonLink 子类覆写为记账（不外发）。
        /// 无图路径**刻意原样保留**（不改签名、不改调用链）—— 外部自测工程的既有 override
        /// 继续对本路径生效；带图走下面那个新重载。</summary>
        public virtual void SendPlayerMessage(string npcId, string text)
        {
            SendPlayerMessage(npcId, text, null);
        }

        /// <summary>
        /// 玩家消息（带图）。`images` 为附件数组，每项 `{name,url,w,h,kb}`，
        /// `url` 是 data URL（`data:image/png;base64,...`），Python 侧原样喂 `image_url.url`。
        ///
        /// 契约（与 Python 侧共同约定）
        ///   · `text` 由 C# 侧**已经拼好占位**（`[图片：xxx.png]`），Python 不再改写 ——
        ///     本地气泡、落盘历史、模型后续回合看到的是同一个串（唯一来源，避免两边各拼一遍）。
        ///   · 图像字节**只进当回合**，不进历史：Python 把 url 放内存侧表、账本只留占位文本。
        ///   · `images` 为空时**不写该字段** —— 旧 Python 端收到也不会当未知字段报错。
        /// </summary>
        public virtual void SendPlayerMessage(string npcId, string text, JArray images)
        {
            var ws = _ws;
            if (ws == null) return;
            // 玩家自己发起的回合：撤销"动作完成自动开窗"标记（窗本来就在眼前，动作完成不该再抢焦点）
            ActionWatcher.NotePlayerTurn(npcId);
            var frame = new JObject
            {
                ["type"] = "event",
                ["event"] = "player_message",
                ["npc_id"] = npcId,
                ["text"] = text,
            };
            if (images != null && images.Count > 0) frame["images"] = images;
            Send(ws, frame);
        }

        /// <summary>
        /// 手动压缩请求（玩家输入 /compact 时由 ChatPresenter 调用）：专用通道事件——
        /// 不进对话历史、不触发 LLM 对话回合；结果经 compact_result 事件回推，
        /// 由 ChatPresenter 原位更新「正在压缩中」提示行。
        /// virtual：自测工程 FakePythonLink 子类覆写为记账（不外发）。
        /// </summary>
        public virtual void SendCompactRequest(string npcId)
        {
            var ws = _ws;
            if (ws == null) return;
            Send(ws, new JObject
            {
                ["type"] = "event",
                ["event"] = "compact",
                ["npc_id"] = npcId,
            });
        }

        /// <summary>
        /// 群聊发言（玩家在群里说的一句）。**事件**而不是 RPC —— 与 `player_message` 同形：
        /// 发送方不等回执，结果通过 `text_delta` / `room_say` 流式回推。
        /// 走 RPC 的话一轮接力含最多 K 个完整回合（每个都调 LLM），发送方要等整轮、
        /// 接收循环还会被堵死（连"玩家插话"都进不来）。
        ///
        /// `speakFirst` = 玩家在 UI 上点名的人（点头像让他先说），空/null 表示不点名、
        /// 由 Python 按轮转指针决定。P1 **不做**文本里的名字识别（脆弱，中文名会互相包含）。
        ///
        /// `room_id` 与 `npc_id` 是**两套坐标**：房间才是群聊的场合标识，`npc_id` 只出现在
        /// 出站帧里用于把正文路由到正确的气泡。所以本帧不带 `npc_id`。
        /// virtual：自测工程 FakePythonLink 子类可覆写为记账。
        /// </summary>
        public virtual void SendRoomMessage(string roomId, string text, List<string> speakFirst = null)
        {
            var ws = _ws;
            if (ws == null) return;
            if (string.IsNullOrEmpty(roomId) || string.IsNullOrEmpty(text)) return;
            var frame = new JObject
            {
                ["type"] = "event",
                ["event"] = "room_message",
                ["room_id"] = roomId,
                ["text"] = text,
            };
            if (speakFirst != null && speakFirst.Count > 0)
            {
                var arr = new JArray();
                foreach (var s in speakFirst)
                {
                    if (!string.IsNullOrEmpty(s)) arr.Add(s);
                }
                if (arr.Count > 0) frame["speak_first"] = arr;
            }
            Send(ws, frame);
        }

        /// <summary>
        /// 群聊「跳过」：这一轮玩家**不置一词**，按轮转顺序让 NPC 自己接下去
        /// （Python 侧 `ChatHub.handle_room_skip` → `RoomFlow.run_round`）。
        ///
        /// ★ 为什么另开一个事件、而不是发一条空文本的 `room_message`：
        ///   ① 空文本的 `room_message` 在 Python 侧被**直接丢弃**（`handle_room_message`
        ///      开头就 return，连一帧都不回）⇒ 玩家按下去**毫无反应**；
        ///   ② 就算放行，`run_message` 会先给玩家落一条**空发言**进 `Room.log` ——
        ///      全群可见、进所有成员记忆、群窗上留一个空气泡。
        ///   语义上"跳过"根本不是"说了一句没有字的话"。
        ///
        /// 本帧**不带 text / npc_id / speak_first**：跳过就是跳过，没有载荷。
        /// virtual：自测工程 FakePythonLink 子类可覆写为记账。
        /// </summary>
        public virtual void SendRoomSkip(string roomId)
        {
            var ws = _ws;
            if (ws == null) return;
            if (string.IsNullOrEmpty(roomId)) return;
            var frame = new JObject
            {
                ["type"] = "event",
                ["event"] = "room_skip",
                ["room_id"] = roomId,
            };
            Send(ws, frame);
        }

        /// <summary>
        /// NPC 主动开口入口（NpcInitiativeMonitor 在游戏主线程调用）。
        /// 只发「意图键 + 可选缘由」：文案由 Python 端 initiative.py 映射，本端不带提示词。
        /// consented=true 表示玩家刚在当面确认窗点了「同意」——Python 端据此跳过全局节流
        /// （玩家主动放行不该被事件防轰炸节流吞掉）。
        /// debug=true 表示诊断强制触发（`_diag_initiative_force.txt`）——Python 端同样豁免全局
        /// 节流，否则 min_interval（默认 300s）会把强制节拍削成"看着像没生效"。
        /// busy=true 表示玩家此刻正忙（战斗中/有窗口或模态/有挂起的 AI 行动）——**只禁动作、不禁言语**：
        /// Python 端本回合**不裁剪工具表**（前缀缓存，见 docs/features/brain-liveness.md §4），而是尾部追加 `BUSY_NOTE`（软）
        /// + 执行层 `speech_only_turn` 替全部 **6 个**动作工具回一条 error tool_result（硬），
        /// 只读三件套照常放行。
        /// virtual：自测工程 FakePythonLink 子类覆写为记账（不外发）。
        /// </summary>
        public virtual void SendInitiative(string npcId, string intent, string reason, bool consented = false,
                                           bool debug = false, bool busy = false, bool speechOnly = false,
                                           string unitId = null, bool? sameGrid = null)
        {
            var ws = _ws;
            if (ws == null) return;
            var frame = new JObject
            {
                ["type"] = "event",
                ["event"] = "npc_initiative",
                ["npc_id"] = npcId,
                ["intent"] = intent,
                ["reason"] = reason ?? "",
            };
            // ★ npc_unit_id（09-28 补）★：说话人的**精确身份**，由触发方从**真身**读出
            //   （`FireOne` 手里的 `wub` / `DramaAiOption` 的对页 NPC）。
            //
            //   为什么必须带：主动传音是**第三条**会话入口 —— 它既不经过单聊 `open_chat`
            //   （那里 C# 随请求带上 unit_id → Python `set_unit_id`），也不经过群聊入场，
            //   于是 Python 侧 `agent.unit_id` 恒为空 ⇒ 工具调用**不注入** `initiator_id` ⇒
            //   C# 只能按名猜。真机 09-28 实锤：`Resolve('管星贤')` 未命中（登记被自愈摘除 +
            //   全图按名扫描也没覆盖到他）→ 本回合 5 个动作工具全部 `未找到发起方`。
            //   带上它之后这一路走 `ResolveEx(name, id)` 的 ②unitID精确，永不按名猜。
            //   取不到（老调用方/无真身）→ 不发这个键 = 行为与从前逐字一致（回落按名）。
            if (!string.IsNullOrEmpty(unitId)) frame["npc_unit_id"] = unitId;
            if (consented) frame["consented"] = true;
            if (debug) frame["debug"] = true;
            if (busy) frame["busy"] = true;
            // speechOnly：本回合一个工具都不调（Python 侧插约束 + 执行层拦全部工具）
            if (speechOnly) frame["speech_only"] = true;
            // sameGrid（10-16 可选）：本次触发时**是否同格**。用途只有一个 —— 让 Python 侧
            // 给同一个意图键挑对应方位口吻的舞台指令（"当面交给你" vs "托传讯送过去"）。
            // 只在明确已知时带（null = 不发这个键）：老调用方不传时 Python 回落旧文案，逐字不变。
            if (sameGrid.HasValue) frame["same_grid"] = sameGrid.Value;
            Send(ws, frame);
        }

        /// <summary>
        /// 原生剧情「AI 对话」入口（DramaAiOption 按钮点击时在主线程调用）：
        /// 复用 npc_initiative 事件，intent=game_drama，额外带 text（剧情原文）、drama_id 与
        /// **speaker（本页那句是谁说的：npc / player / 空=判不出）**——
        /// 屏幕句里不含说话人，而同一枚按钮两头都服务（NPC 主动找上门 / 玩家点的闲聊），
        /// 不给归属模型只能猜（猜反：NPC 送礼那页被当成"玩家送我"）。空串 = 未知，
        /// Python 落回不点名的中性文案。
        /// Python 端按 game_drama 分支跳过全局节流，用原文包装舞台指令驱动 NPC 润色开场。
        ///
        /// **lines（可选）：一次当面相遇的整段原文**，每项 `{seq, drama_id, speaker, name, text}`，
        /// 由「剧情相遇分段器」（`drama/DramaSegmentRecorder`）攒出，**只含还没投过的增量**。
        /// 有它时 Python 按多句舞台指令包装（`initiative.format_game_drama_message` 的 lines 支路）；
        /// **不带它时行为与从前一字不差**（单句路径）—— C# 与 Python 是**成对**发布的，
        /// 但这条兼容让老 C# 配新 Python 也不炸。
        ///
        /// **busy（可选，09-22 补）：本回合"只许说话 + 只读"**。判据由调用方给
        /// （剧情窗那条路传的是 `DramaGate.ChainHold`：战局收尾中）。
        /// 为什么要补这一项：这条路径此前**从不带 busy**，而执行层的 busy 键在我们收口那一刻就释放了
        /// （链的尾页还要十几秒才来）⇒ "在处置页上边聊边被发起一个新动作"是通的，会与正在演出的链并行。
        ///
        /// **verdict（可选，09-24 补）：点「对话」那一刻**当前这一页就是战斗处置页**。
        /// 判据由调用方给（`DramaAiOption` 传给 `BattleOutcome.IsVerdictDramaId(_pendingDramaId)`：
        /// 页号 20403/20404，**配表事实**、只看页号不依赖台账 —— 处置页收口时台账已被清，
        /// 所以不能用 `IsVerdictPage`）。**只认"当前页就是处置页"**：翻到后页再点不带
        /// （那一轮里处置页的选项/台词本来就会随段增量投进去，用户 09-24 拍板甲案）。
        /// Python 端据此追加"别调工具去执行页面上的献物/求饶、只限这一页"的说明
        /// （`initiative._VERDICT_NOTE`）—— 与工具结果上的 `verdict_page`（管**生死**）分工不同。
        /// </summary>
        public virtual void SendGameDrama(string npcId, string text, int dramaId, string speaker = "",
                                          JArray lines = null, bool busy = false, bool action = false,
                                          bool verdict = false, string unitId = null)
        {
            var ws = _ws;
            if (ws == null) return;
            var frame = new JObject
            {
                ["type"] = "event",
                ["event"] = "npc_initiative",
                ["npc_id"] = npcId,
                ["intent"] = "game_drama",
                ["reason"] = "",
                ["text"] = text ?? "",
                ["drama_id"] = dramaId,
                ["speaker"] = speaker ?? "",
            };
            // npc_unit_id：本页 NPC 的精确身份（同 `SendInitiative`，09-28 补）。
            // 这条路的回合同样不经过 `open_chat` ⇒ 不带上它，Python 侧 `agent.unit_id` 为空、
            // 工具调用只能按名猜。空 = 老调用方 ⇒ 不发这个键（回落按名，逐字同旧行为）。
            if (!string.IsNullOrEmpty(unitId)) frame["npc_unit_id"] = unitId;
            // 只在真有增量时带这一项：空数组与缺项对 Python 是两件事（缺项 = 走单句路径）
            if (lines != null && lines.Count > 0) frame["lines"] = lines;
            // busy：Python 端据此剔除动作工具、并追加"玩家此刻正忙…可以在话里提一句"的约束
            if (busy) frame["busy"] = true;
            // action（可选，09-23）：游戏里"某个行动还在进行"（论道/邀约这类交互没走完）。
            // Python 端据此在舞台指令里点明"这场交互正在进行中"——不给这个事实，
            // 模型会把已发生的交互当成没开始、再去发起一遍同款工具（实测：论道页上又调论道）。
            if (action) frame["action"] = true;
            // verdict（可选，09-24）：当前页是战斗处置页（见上方 summary）→ Python 追加处置页说明
            if (verdict) frame["verdict"] = true;
            Send(ws, frame);
        }

        /// <summary>
        /// C#→Python 方向异步 RPC 请求（例如 get_history 读对话历史）。
        /// 调用者在主线程 → 本方法登记回调 → 发请求帧 → 应答经 MainThreadDispatcher 转回主线程后触发 onResponse。
        /// 超时后返回 {"ok":false,"error":"请求超时"} 不阻塞。
        /// </summary>
        /// <param name="method">请求方法名</param>
        /// <param name="parameters">参数字典</param>
        /// <param name="onResponse">回调（总是在 Unity 主线程执行）</param>
        /// <param name="timeoutMs">超时毫秒数，默认 10 秒</param>
        /// <remarks>virtual：自测工程 FakePythonLink 子类覆写为应答表直答（不经网络）。</remarks>
        public virtual void SendRequest(string method, JObject parameters, Action<JObject> onResponse, int timeoutMs = 10000)
        {
            var ws = _ws;
            if (ws == null || !ws.IsOpen)
            {
                _dispatcher.Enqueue(() =>
                {
                    onResponse(new JObject { ["ok"] = false, ["error"] = "未连接 Python" });
                    return null;
                });
                return;
            }

            // 生成 req_id：u 前缀（UI 起始）+ 序号递增，与 Python 请求编号空间不冲突
            string reqId = "u" + ++_reqSeq;
            var tcs = new TaskCompletionSource<JObject>();
            lock (_pendingLock) _pending[reqId] = tcs;

            // 发送 request 帧
            Send(ws, new JObject
            {
                ["type"] = "request",
                ["req_id"] = reqId,
                ["method"] = method,
                ["params"] = parameters,
            });

            // 超时计时器：超时后摘除并返回错误
            var timer = new Timer(_ =>
            {
                lock (_pendingLock)
                {
                    if (_pending.TryGetValue(reqId, out var pendingTcs))
                    {
                        _pending.Remove(reqId);
                        _dispatcher.Enqueue(() =>
                        {
                            onResponse(new JObject { ["ok"] = false, ["error"] = "请求超时" });
                            return null;
                        });
                    }
                }
            }, null, timeoutMs, Timeout.Infinite);

            // 等待应答 → 转主线程回调
            _ = tcs.Task.ContinueWith(task =>
            {
                timer.Dispose();
                JObject result;
                if (task.IsFaulted)
                {
                    result = new JObject { ["ok"] = false, ["error"] = task.Exception?.InnerException?.Message ?? "未知错误" };
                }
                else if (task.IsCanceled)
                {
                    result = new JObject { ["ok"] = false, ["error"] = "请求被取消" };
                }
                else
                {
                    result = task.Result;
                }

                // 回调必须在主线程
                _dispatcher.Enqueue(() =>
                {
                    onResponse(result);
                    return null;
                });
            });
        }

        private void Send(WsTransport ws, JObject obj)
        {
            bool dropped = false;
            lock (_sendLock)
            {
                if (ws == null || !ws.IsOpen) dropped = true;
                else ws.SendText(obj.ToString());
            }
            // 旧版这里**静默 return**：Python 进程死掉后玩家发消息，C# 既不记日志
            // 也不提示，屏幕上"什么都没发生"。现在至少留一行（按次数节流）。
            // **日志放在锁外**：ModMain.P 要写文件，占着发送锁做 I/O 会拖住整条发送路径。
            if (dropped) NoteDropped(obj);
        }

        /// <summary>丢弃帧的可见化（节流：第 1 帧 + 之后每 20 帧记一行）。</summary>
        private void NoteDropped(JObject obj)
        {
            _dropped++;
            if (_dropped != 1 && _dropped % 20 != 0) return;
            string kind = null;
            try { kind = (string)(obj?["type"] ?? obj?["event"] ?? obj?["method"]); } catch { }
            ModMain.P("[WsClient] ★帧被丢弃（未连接）：kind=" + (kind ?? "?")
                      + "，累计 " + _dropped + " 帧。玩家侧表现为「发出去没反应」，当前状态="
                      + BrainLink.State + "（" + BrainLink.StatusText + "）");
        }

        private int _dropped;

        public void Stop()
        {
            _running = false;
            var ws = _ws;
            _ws = null;
            try { ws?.Dispose(); } catch { }
            if (_thread != null)
            {
                try { _thread.Join(1000); } catch { }
                _thread = null;
            }
        }

        public void Dispose() { Stop(); }
    }
}
