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
                    // 断线：丢弃全部待补发的延迟 response（Python 侧 future 已随连接死亡，补发无主）
                    lock (_pendingLock) { _deferred.Clear(); _deferredAction.Clear(); }
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
                // 主线程执行（碰 g.world），再回 response
                var task = _dispatcher.Enqueue(() => ExecuteRequest(method, p));
                JObject response = (JObject)task.Result;
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
                if (ev == "step" || ev == "text_delta" || ev == "npc_reply" || ev == "compact_result")
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
                return new JObject { ["type"] = "response", ["ok"] = true, ["data"] = _context.GetL1(npcId) };
            }
            if (method == "call_tool")
            {
                string name = (string)p["name"] ?? "";
                JObject args = p["arguments"] as JObject ?? new JObject();
                // npc_id（Python 起随帧带上）= 发起这次调用的 NPC。动作类工具成功执行后，
                // ActionWatcher 要在**这一刻**（动作真的跑完了）判断是否把对话 UI 推到玩家眼前。
                string npcId = (string)p["npc_id"] ?? "";
                JObject res = _tools.Execute(name, args);
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
                if (ok) ActionWatcher.OnActionDone(npcId, name);
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
            if (ok && action != null) ActionWatcher.OnActionDone(action[0], action[1]);
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
        /// NPC 主动开口入口（NpcInitiativeMonitor 在游戏主线程调用）。
        /// 只发「意图键 + 可选缘由」：文案由 Python 端 initiative.py 映射，本端不带提示词。
        /// consented=true 表示玩家刚在当面确认窗点了「同意」——Python 端据此跳过全局节流
        /// （玩家主动放行不该被事件防轰炸节流吞掉）。
        /// debug=true 表示诊断强制触发（`_diag_initiative_force.txt`）——Python 端同样豁免全局
        /// 节流，否则 min_interval（默认 300s）会把强制节拍削成"看着像没生效"。
        /// busy=true 表示玩家此刻正忙（战斗中/有窗口或模态/有挂起的 AI 行动）——**只禁动作、不禁言语**：
        /// Python 端本回合把 5 个动作工具从工具表剔除、只留 3 个只读（两级闸）。
        /// virtual：自测工程 FakePythonLink 子类覆写为记账（不外发）。
        /// </summary>
        public virtual void SendInitiative(string npcId, string intent, string reason, bool consented = false,
                                           bool debug = false, bool busy = false, bool speechOnly = false)
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
            if (consented) frame["consented"] = true;
            if (debug) frame["debug"] = true;
            if (busy) frame["busy"] = true;
            // speechOnly：本回合一个工具都不调（Python 侧插约束 + 执行层拦全部工具）
            if (speechOnly) frame["speech_only"] = true;
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
        /// </summary>
        public virtual void SendGameDrama(string npcId, string text, int dramaId, string speaker = "")
        {
            var ws = _ws;
            if (ws == null) return;
            Send(ws, new JObject
            {
                ["type"] = "event",
                ["event"] = "npc_initiative",
                ["npc_id"] = npcId,
                ["intent"] = "game_drama",
                ["reason"] = "",
                ["text"] = text ?? "",
                ["drama_id"] = dramaId,
                ["speaker"] = speaker ?? "",
            });
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
