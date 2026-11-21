/// <summary>
/// 对话 UI 控制器（MonoBehaviour）—— 全工程唯一认 WS 协议的类：
/// 订阅 WsClient.UiEvent，把 step / text_delta / npc_reply / compact_result 翻译成 ChatWindow 视图语言；
/// 把用户输入经 WsClient 发回 Python；开窗/换 NPC 时发 open_chat（开窗=激活 agent + 历史回放）、
/// 关窗时发 dispose_agent（关窗=落盘销毁）。
///
/// IL2CPP 下的输入提交：InputField 的 onSubmit/onEndEdit 事件家族不可靠（UnityEvent 受委托转类影响），
/// 因此 Update 里轮询按键：消息框有内容 + Enter 即发送（不依赖 isFocused——InputField 提交会自动失焦）。
///
/// 各司其职：不建 UI 节点、不管布局（那是 ChatUiBuilder/ChatWindow 的事）；
/// 不碰传输细节（那是 WsClient 的事）。所有回调都在 Unity 主线程
/// （UiEvent 由 Dispatcher 转入主线程，SendRequest 的回调包装同样转主线程）。
///
/// 已知取舍：窗口未开时到达的事件直接丢弃（不做离线缓存）——
/// 关闭期间的对话可由「开窗→open_chat」补齐（重开即 resume 激活），与直播不交叠。
/// </summary>
using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;
using Newtonsoft.Json.Linq;

namespace AgentLoopBridge
{
    /// <summary>
    /// IL2CPP 注意：自定义 MonoBehaviour 需宿主侧显式 ClassInjector.RegisterTypeInIl2Cpp
    /// 注册进类型系统（主工程 ModMain 已调用），否则 AddComponent 抛
    /// TypeInitializationException——与 ChatWindow 同规约。本类不引 MelonLoader，
    /// 保持纯 Unity 依赖。
    /// </summary>
    public class ChatPresenter : MonoBehaviour
    {
        /// <summary>IL2CPP 互操作标准构造</summary>
        public ChatPresenter(System.IntPtr ptr) : base(ptr) { }

        private ChatWindow _window;
        private WsClient _ws;
        private bool _busy;
        private Text _compactNotice;   // 「正在压缩中」提示行（compact_result 到达时原位更新）
        // ---- 压缩在跑（用户反馈"压缩按钮按下去没反应"）----
        // 真机现场：17:23:46 点下 → Python 摘要 LLM 调用跑了 104.7s → 17:25:31 成功。
        // 这 105 秒里 C# 侧一行日志都没有、按钮毫无变化，玩家只能判定"没触发"（于是连点三次）。
        // 现在：按钮忙态（文案「压缩中…」+ 置灰）+ 提示行秒表 + 两端各留一行日志。
        private bool _compacting;
        private float _compactStartAt;      // 起算时刻（unscaled；提示行秒表与超时兜底用）
        private string _compactNoticeBase;  // 提示行原文案（秒表刷新用）
        // 左侧编辑面板正在改的那条纪要（compactionId）。空 = 没有在编辑。
        // 只在这里存：面板是静态的、宿主会被销毁重建，把"在改哪条"存进面板会跨宿主串味。
        private string _editCompactionId = "";
        private const float CompactStuckSec = 900f;   // 兜底：15 分钟没回音就当丢了，恢复按钮（摘要实测 105s）
        private bool _enterDeleteOnReplay;   // 删除模式入口流程中：本次重放落地后进入选择模式
        // ---- 历史重放的「欠账」----
        // 症状：NPC 主动开口 → 同意后开窗，历史全空，底部统计栏停在预制体默认的 "New Text"。
        // 现场（Player.log:2691 + Python 日志 16:44:/33）：`open_chat` **发出去了**，
        // Python 也把 34 项历史送回来了，但响应到达时回合已经流式输出十几秒 → `_busy` 为真
        // → 响应被回调侧守卫丢弃 → `ReplaceHistory` / `SetStats` 双双没跑。
        // **一个 return 解释两个症状**（历史没了 = 没 ReplaceHistory；New Text = 没 SetStats）。
        //
        // 为什么改成「欠账」而不是继续丢弃：`ReplaceHistory` 是全量重投影（第一句就是
        // `ResetContent()`），与"正在追加的直播"天然互斥 —— 所以**拒绝的理由是对的，
        // 丢弃的处理是错的**。冲突不是失败，是「还没轮到」。
        // 记一个持续为真的期望，等冲突解除（回合收口）再还。
        //
        // 为什么还的时候**重新请求**、不套用手里这份响应：这份快照早于回合收口，
        // 按 Python 的语义（`open_chat` 只回已闭合 turn）它**不含**玩家刚看完的那条回复——
        // 直接套用等于把回复擦掉，比现状更糟。重放是幂等的（全量重投影），多跑一次无副作用。
        //
        // 注意：这是**兜底**，不是治本。治本是让开窗赶上首 token 前那 4~13s 的 TTFT 空窗
        // （见 NpcInitiativeMonitor.cs:386）；欠账机制只在那一场竞速输掉时兜住。
        private bool _wantReplay;
        // 图片预览去重键：= 上一次算过预览的「输入框文本 + 待附图集合」（见 PreviewKey）。
        // 解码整图不便宜，只在这个键变化时重算
        private string _previewKey;
        // 剪贴板粘进来的图片路径：**只待在内存里，绝不写进输入框**（框里摆一串路径很难看，
        // 而且玩家不需要看见它）。他在屏幕上看到的唯一痕迹是输入框上方的缩略图预览条。
        // 手打/「复制文件地址」粘进文本的路径仍然照旧由 ImageInput.SplitPaths 从正文里摘走，
        // 两种来源在发送侧汇成同一条通道（顺序：待附图在前 ⇒ 与预览条一致）。
        private readonly List<string> _pendingImages = new List<string>();

        // ---- 单聊渲染闸（09-17 二次改造）----
        //
        // 单聊帧现在**始终**由 `ChatFrameTee` 记进队列（含"关窗之前"的那半截与玩家自己的输入），
        // 重开窗时整段重演。这样就必须回答一个问题：**回执到达前的这几十毫秒，谁来画？**
        //   直播也在订阅同一批帧 —— 不掐掉它，同一帧会被画两遍（队列重演一次 + 直播一次），
        //   表现是正文/工具行重复，而重复的工具折叠**不归 BubbleCommitPolicy 管**（看得见）。
        // 所以：开窗请求**发出之前**置位，回执里消费完队列才落位。闸门期间直播不画帧（帧不会丢：
        // tee 已经记下了），队列是这段时间**唯一**的帧来源 ⇒ 不需要任何"合并去重"。
        //
        // ★ "不画帧"只对**会进段的那几类**成立（判据 = `ChatFrameQueue.IsSoloBuffered`，
        //   与 tee 的收帧白名单**同源**）：`stats_update` / `compact_result` / `step(sys|ctx)`
        //   队列根本不收，闸门吞掉它们 = 那一帧"两边都没有"（既不直播也不重演），
        //   其中 `compact_result` 还会让"正在压缩中"那行卡到看门狗超时（见 `OnUiEvent` 判据处）。
        //
        // ★ 落位要覆盖**所有出口**（回执失败 / 超时 / 窗口已关 / NPC 已切 / 玩家自己发了消息 /
        //   关窗 / 换 NPC / 进群）：漏一个出口 = 这个窗口的直播从此静默（帧只进队列不出画），
        //   而且**零日志**。这与忙标、活跃泡那两次"漏一条出口就卡死"是同一类坑。
        private string _replayGate = "";
        // 快照过期兜底的重试计数（见 `SendOpenChatReplay` 回执里的代数比对）。上限 2：正常 0~1 次。
        private int _replayEpochRetries;

        /// <summary>历史**分帧构建**期间到达的直播帧（11-20）。
        ///
        /// 为什么不丢给帧队列：群聊那边队列**不补**（tee 的群分支 `IsShowingRoom ⇒ return`，
        /// 窗口正显示这个群时不缓冲），丢了就是真丢。暂存到建完再按序补画，
        /// 顺序天然由 `OnUiEvent` 的到达顺序保证。
        /// 上限防无界：构建最长也就几十帧，正常个位数；超了丢最旧的（那些行本来也快过时了）。</summary>
        private readonly List<JObject> _heldDuringBuild = new List<JObject>();
        private const int HeldDuringBuildMax = 64;

        // ---- 历史分页（11-21）----
        /// <summary>首屏取最近多少**回合**（不是条：一回合可能产出 1~20 条）。
        ///
        /// 真机实测（姜萌账本 43 回合 / 169 条 / 42.1KB）：
        ///   limit=0（全量）→ 169 条 / 42.1KB；limit=20 → 69 条 / 13.5KB。
        /// 一屏约 12 行，69 条足够填满并留出上翻余量；43 回合两点就到顶。
        ///
        /// ⚠ 与 Python `_parse_limit` 的单位一致（回合）。改这个值要同步看
        ///   `tests/test_history_cursor_paging.py` 里的实测数字。
        /// </summary>
        private const int HistoryPageTurns = 20;

        /// <summary>群窗首屏取多少**条**发言。
        ///
        /// ⚠ 单位与单聊**不同**：单聊 `limit` 是"回合"（`project_ui_history` 按 turn 切），
        ///   群聊 `limit` 是"条"（`project_room_history` 按 items 切）。
        ///   这不是笔误 —— 群发言没有回合概念（`project_room_history` 不返回 turn 号，
        ///   见其文件头 §9.2）。60 条 ≈ 填满一屏（一屏约 12 行）并留出上翻余量。
        /// </summary>
        private const int RoomHistoryPageRows = 60;

        /// <summary>当前会话还没渲染的更早历史游标（`(seq, turn)` 双锚，见 Python
        /// `session.history._turn_before_cursor`）。两个都为 0 = 已经到头。
        ///
        /// **必须两个都存**：账本里有些回合一个 seq 都没有（`initiative` / `turn_error`
        /// 这类合成内容，姜萌账本 43 回合里有 2 个），只靠 seq 会让它们每页重复返回。
        /// </summary>
        private long _histBeforeSeq;
        private int _histBeforeTurn;
        /// <summary>本会话是否还有更早的历史可取（回执里 `next_before_*` 非 0 且拿到过数据）。</summary>
        private bool _histHasMore;
        /// <summary>「加载更早」请求在飞（防连点重复发）。</summary>
        private bool _histEarlierPending;
        /// <summary>还没渲染的更早条目数（只用于 UI 文案"还有 K 条"；-1 = 未知）。
        ///
        /// 为什么是"未知"而不是"算出来"：服务端只告诉我们"还有没有更早的"，
        /// 没告诉我们总数（那要再扫一遍账本，反而变慢）。
        /// ⇒ 文案退化成"↑ 上划加载更早的消息"，不报数字。**不编造数字**是刻意的。
        /// </summary>
        private int _histHiddenCount = -1;

        /// <summary>把构建期间暂存的帧按原序补画一遍（建完调；调完清空）。</summary>
        private void FlushHeldDuringBuild()
        {
            if (_heldDuringBuild.Count == 0) return;
            var frames = new List<JObject>(_heldDuringBuild);
            _heldDuringBuild.Clear();
            ModMain.P("[ChatPresenter] 分帧构建期间暂存的 " + frames.Count + " 帧开始补画");
            foreach (var p in frames)
            {
                try { DispatchUiEvent(p); }
                catch (Exception e)
                {
                    try { ModMain.P("[ChatPresenter] 补画暂存帧失败（跳过）：" + e.Message); } catch { }
                }
            }
        }

        /// <summary>置位渲染闸（记在**哪个 NPC** 上：同一个 presenter 会被 `OpenForNpc` 换绑）。</summary>
        private void ArmReplayGate(string npc)
        {
            _replayGate = npc ?? "";
        }

        /// <summary>落位渲染闸。`why` 只进日志（哪一种出口落的位置，排障时一眼看出）。</summary>
        private void ClearReplayGate(string why)
        {
            if (string.IsNullOrEmpty(_replayGate)) return;
            ModMain.P("[ChatPresenter] 渲染闸落位（" + why + "）｜ npc=" + _replayGate + " 实例#" + _id);
            _replayGate = "";
        }

        // ---- 群聊（Room）----
        // 群聊**没有**唯一对话对象，所以走独立的状态：`_window.CurrentRoomId` 非空即群聊模式。
        // 单聊时下面这些字段全部不参与判据 ⇒ 既有路径逐字节不变。
        private string _roomSpeaking = "";       // 当前正在说话的成员（流式气泡的归属名）
        /// <summary>`_roomSpeaking` 的**精确 unitID**（与它同写同清）。来源 = Python 随
        /// `room_say` / `room_state` 送上来的 `speaker_id` / `speaking_ids`（那边的真相是
        /// `Room.member_unit_ids`）。有它，群聊每一行的头像都是**直接取图**，不再按名解析 ——
        /// 按名在重名时会画上别人，且**不报错、不缺字段**（正是本工程最怕的形态）。</summary>
        private string _roomSpeakingId = "";
        private readonly HashSet<string> _roomEchoSeen = new HashSet<string>();
        // 群聊发言的回声去重集（`room_say` 的 uid）。只用于**玩家自己**那句：
        // 发送方本地已经画过气泡，Python 会把同一句广播回来（多端一致 + 群列表预览要用），
        // 不去重就会每条玩家发言出现两个气泡。NPC 的 uid 不记——它们本来就没有本地副本。
        // ★ 但这个集合**救不了本地那条**：本地泡是同步画的，那时还没有 uid（见
        //   `ApplyRoomSay` 的说明）。所以另配一份"我方刚发出、还没等到回声"的待销账表。
        private readonly Queue<string> _roomEchoPending = new Queue<string>();
        private const int RoomEchoPendingMax = 16;   // 上限：掉线的回声不许把这张表撑大

        // ---- 忙标卡死排查----
        // 症状：对话已收口，删除模式仍提示"回合进行中"（守卫 `_busy || HasActiveBubble`， 忙=True）。
        // 而 `_busy` 只能经 `SetBusy(true)` 置真、且必然伴随一行 `忙标 ON`；日志里只有 ON 没有 OFF
        // ⇒ `SetBusy(false)` 从未执行 ⇒ 要么本 presenter 收不到 `npc_reply`，要么收口路径半途抛异常。
        // 这三条探针把"收不到 / 收到了但没跑完 / 跑了但没清"三种情形一次分开。
        private static int _seq;
        private int _id;                       // 实例号（ClassInjector 注入的托管类，不依赖 GetInstanceID）
        private float _busySince;              // 忙标置真的时刻（unscaled）
        private float _lastFrameAt;            // 最近一次收到本 NPC 帧的时刻（unscaled）
        // 玩家消息时间戳自检：记录上次**打过的**标签，换月才打一行日志。
        // static：多个 presenter 实例（多 NPC）共用一行，免得每开一个 NPC 都重打一遍同一个月。
        private static string _lastStampLogged = "";
        /// <summary>忙标自愈阈值（秒）：超过这么久一条帧都没有 = 这个忙标是死的。
        /// 取 240s —— 实测最长回合约 29s（含工具循环），留近 10 倍余量，绝不会误伤真在跑的回合。</summary>
        private const float BusyStaleSeconds = 240f;

        // ------------------------------------------------------------------
        // 忙标阶段（11-21）：让玩家分得清「在等谁」
        //
        // 用户要求：等 C# 取快照 / 等模型开口 / 模型正在回话，这三种要显示不同文案，
        // 且超过阈值（`ui.busy_hint_after`，秒）后追加已等秒数。
        //
        // 数据从哪来：Python 发 `wait_phase` 帧（进/出各一次），本类只做两件事 ——
        //   ① 记住当前阶段与进入时刻；② 在 `Update()` 里按秒刷新文案。
        // **不由本类判断阶段**：C# 看不到模型首字，猜不出来；猜错比不显示更糟。
        // ------------------------------------------------------------------

        /// <summary>当前等待阶段（空 = 没有阶段，用默认文案）。
        /// 取值与 Python `DialogueAgent.on_wait_phase` 一一对应：snapshot / think / stream。</summary>
        private string _waitPhase = "";
        /// <summary>该阶段的进入时刻（unscaled）。秒数从这里算，**不从 Python 的发送时刻算** ——
        /// 窗口没开时帧会晚到/被丢，那时该从玩家真看到那一刻起算。</summary>
        private float _waitPhaseSince;
        /// <summary>上次刷进 BusyLabel 的文案（避免每帧都改 Text 触发重建）。</summary>
        private string _waitPhaseShown = "";
        /// <summary>「超过多久才显示秒数」的阈值（秒），来自配置 `ui.busy_hint_after`。
        /// 0 = 关闭（永远不追加秒数）。**默认 10 秒**与 config_loader 的默认值一致：
        /// 拿不到配置时用同一个数，免得"没连上"和"配置成 10"表现不同。</summary>
        private float _busyHintAfter = 10f;
        /// <summary>文案模板：阶段 → 前缀。秒数由本类拼在后面。</summary>
        private static string WaitPhaseText(string phase)
        {
            switch (phase)
            {
                case "snapshot": return "正在读取人物状态…";
                case "think": return "对方正在斟酌…";
                case "stream": return "正在回话…";
                default: return "";
            }
        }

        public void Init(ChatWindow window, WsClient ws)
        {
            _window = window;
            _ws = ws;

            if (_ws != null) _ws.UiEvent += OnUiEvent;
            else ModMain.P("[ChatPresenter] ★Init 时 WsClient 为空 → 未订阅 UiEvent："
                           + "按钮会照接（能发消息）但收不到任何回帧 → 忙标永久卡死。这是已知故障形态。");
            // ★ 11-20：断线/恢复的玩家可见提示 ★
            // 此前断线在 UI 上**完全没有痕迹**：玩家发了消息，屏幕上什么都不动，
            // 分不清"模型在思考"和"根本连不上"。真机 8 次 get_context 干等 120 秒就是这个形态。
            // 订阅在 `Init`（这里）而不是 `ModMain`：提示要落到**当前这个窗口**，
            // 而 ModMain 手上没有窗口引用（`ChatWindowInstance` 只在代码模式下赋值，
            // 实际走 AB 面板时是 null）。
            if (_ws != null)
            {
                _ws.Disconnected += OnTransportLost;
                _ws.Connected += OnTransportBack;
            }
            if (_id == 0) _id = ++_seq;
            // 登记进实例表：配置下发（`ConfigureBusyHintAll`）要靠它找到所有活着的窗口。
            // 幂等：同一个实例重复 Init（换绑窗口）不该重复登记。
            try
            {
                bool already = false;
                foreach (var w in _livePresenters)
                    if (w.Target == (object)this) { already = true; break; }
                if (!already) _livePresenters.Add(new System.WeakReference(this));
            }
            catch { }
            ModMain.P("[ChatPresenter] Init 完成 实例#" + _id + " 订阅=" + (_ws != null));
            if (_window != null)
            {
                _window.CloseClicked += OnCloseClicked;
                _window.EarlierRequested += OnEarlierRequested;   // 分页：上划加载更早（11-21）
                _window.SubmitRequested += OnSubmitRequested;
                _window.DeleteBtnClicked += OnDeleteBtnClicked;
                _window.CompactClicked += OnCompactBtnClicked;
                _window.DeletePreviewRequested += OnDeletePreviewRequested;
                _window.DeleteConfirmed += OnDeleteConfirmed;
                _window.RoomDeletePreviewRequested += OnRoomDeletePreviewRequested;
                _window.RoomDeleteConfirmed += OnRoomDeleteConfirmed;
                _window.InviteClicked += OnInviteClicked;
                _window.KickClicked += OnKickClicked;
                _window.SkipRequested += OnSkipClicked;
                _window.ToolsClicked += OnToolsClicked;
                _window.SummaryEditClicked += OnSummaryEditClicked;
                _window.SummaryEditSubmitted += OnSummaryEditSubmit;
                ToolPanel.ItemChosen += OnToolItemChosen;
            }
        }

        /// <summary>连接断了 → 在窗口里留一行**看得见**的系统提示（11-20）。
        ///
        /// 为什么必须给玩家看：断线后 C# 发出的任何 request 都会被静默丢弃，
        /// 屏幕上表现为"发了消息一直没有回复"。玩家没有任何线索能分辨
        /// "模型正在想"和"根本连不上"——真机日志里 8 次 `get_context` 干等 120 秒
        /// 期间，窗口里一片安静，最后只换来一句莫名其妙的"请求超时"。
        ///
        /// 只在本回合**真的在等**的时候提示（`_busy` / 活跃泡 / 渲染闸）：
        /// 玩家只是开着窗、什么都没发的时候断线，插一行红字只会制造焦虑，而且
        /// 重连只需 2 秒（真机实测 20/22 次如此），玩家根本不会察觉。
        /// </summary>
        private void OnTransportLost()
        {
            try
            {
                if (_window == null) return;
                bool waiting = _busy || _window.HasActiveBubble || _replayGate.Length > 0;
                ModMain.P("[ChatPresenter] 连接断开 ｜ 正在等回复=" + waiting + " 实例#" + _id);
                if (!waiting) return;   // 没在等 ⇒ 不打扰（重连通常 2 秒内完成）
                _window.AppendSystemNotice("与 Python 的连接断开了 —— 这次回复收不到了，"
                                           + "正在自动重连（通常几秒）。重连后可以直接再发一次。");
                // 忙标必须灭：连接没了就不可能再有收口帧，留着它转圈是纯粹的误导。
                // 下一次回合开始时 `OnSubmitRequested` 会重新点亮。
                SetBusy(false);
            }
            catch (Exception e)
            {
                try { ModMain.P("[ChatPresenter] OnTransportLost 异常：" + e.Message); } catch { }
            }
        }

        /// <summary>连接回来了 → 提示一句并复位重放闸（11-20）。
        ///
        /// 闸门复位是实质性的：断线时那个 `open_chat` 请求发不出去（`WsClient.Send` 静默丢弃），
        /// 而 `_replayGate` 是按 NPC 记的 —— 不清掉的话重连后这个窗口的直播帧会一直被吞，
        /// 表现成"连上了但屏幕上再也不出新内容"，且零报错。`ReopenAfterReconnect`
        /// 会重走一遍 `open_chat` 并重新置位，所以这里只是把旧的那份作废。
        /// </summary>
        private void OnTransportBack()
        {
            try
            {
                if (_window == null) return;
                _heldDuringBuild.Clear();      // 断线期间的暂存帧已随连接作废
                ModMain.P("[ChatPresenter] 连接已恢复 ｜ 实例#" + _id);
                if (!_window.IsOpen) return;
                _window.AppendSystemNotice("已重新连上 Python。");
            }
            catch (Exception e)
            {
                try { ModMain.P("[ChatPresenter] OnTransportBack 异常：" + e.Message); } catch { }
            }
        }

        private void OnDestroy()
        {
            // 从实例表摘除（11-21）：配置下发要按它找活着的窗口，留着死引用会一直涨。
            try
            {
                for (int i = _livePresenters.Count - 1; i >= 0; i--)
                    if (!_livePresenters[i].IsAlive || _livePresenters[i].Target == (object)this)
                        _livePresenters.RemoveAt(i);
            }
            catch { }
            // @ 候选浮层随 presenter 一起销毁（11-21）：它的树挂在窗口下面，
            // presenter 没了却留着树 = 每次开关窗口泄漏一层节点（且它 raycastTarget=true，
            // 残留下来会**吃掉聊天区的点击**，表现为"点哪都没反应"且完全无日志）。
            try { MentionPickPanel.Dispose(); } catch { }
            if (_ws != null) _ws.UiEvent -= OnUiEvent;
            if (_window != null)
            {
                _window.CloseClicked -= OnCloseClicked;
                _window.EarlierRequested -= OnEarlierRequested;   // 分页：与 Init 成对（11-21 补漏）
                _window.SubmitRequested -= OnSubmitRequested;
                _window.DeleteBtnClicked -= OnDeleteBtnClicked;
                _window.CompactClicked -= OnCompactBtnClicked;
                _window.DeletePreviewRequested -= OnDeletePreviewRequested;
                _window.DeleteConfirmed -= OnDeleteConfirmed;
                _window.RoomDeletePreviewRequested -= OnRoomDeletePreviewRequested;
                _window.RoomDeleteConfirmed -= OnRoomDeleteConfirmed;
                _window.InviteClicked -= OnInviteClicked;
                _window.KickClicked -= OnKickClicked;
                _window.SkipRequested -= OnSkipClicked;
                _window.ToolsClicked -= OnToolsClicked;
                _window.SummaryEditClicked -= OnSummaryEditClicked;
                _window.SummaryEditSubmitted -= OnSummaryEditSubmit;
                ToolPanel.ItemChosen -= OnToolItemChosen;
            }
        }

        private void Update()
        {
            if (_window == null) return;
            BusyWatchdog();   // 忙标自愈：收口帧丢了也不至于卡到重启（见 BusyWatchdog 注释）
            RefreshBusyPhaseText();   // 阶段秒数按帧涨（只在文案真变时写 Text，见该方法注释）
            CompactWatch();   // 压缩秒表 + 超时兜底（09-13：摘要调用实测 105s，静态提示=像卡死）
            if (!_window.IsOpen)
            {
                // 关窗时清一次预览与待附图：预览节点挂在输入框同级（随窗口根一起隐藏），但待附图
                // 是内存态 → 不清就会在重开时突兀地又冒出来。语义与"关窗即收"一致。
                if (_previewKey != null || _pendingImages.Count > 0)
                {
                    _previewKey = null;
                    _pendingImages.Clear();
                    ImageAttachPreview.Hide();
                }
                return;
            }

            // F9 开关已删：对话窗一律由界面入口打开——
            // NPC 面板「AI 对话」/ 通讯录行点击 / 剧情 AI 选项（ChatLauncher.OpenForUnit）。
            // 删除原因见 AbHotkeys 文件头：帧回调被双重注册，同帧两次 toggle 会互相抵消。

            // Esc 关闭链（用户反馈"按 Esc 只影响游戏本体 UI，mod 窗不受影响"）：
            // ① 配置窗开着 → 先关配置窗（它是最上层）；② 输入框聚焦 → 先失焦（Esc 退输入态）；
            // ③ 否则关闭对话窗（同点 ✕：dispose agent + 落盘）
            if (Input.GetKeyDown(KeyCode.Escape))
            {
                // ★ 「须知」面板（10-23）在最上层：它可以从对话窗顶栏或设置窗打开，
                //   开着时两个窗都还在 ⇒ 这一击必须归它，否则会"关须知的同一次 ESC 顺手关掉对话窗"。
                //   判据与 `BigPortrait.ConsumesEscape()` 同款（IsShowing + 本帧已关过），
                //   与 `AbHotkeys.OnFrame → NoticePanel.Tick` 的先后无关。
#if AB_UI
                if (NoticePanel.ConsumesEscape()) return;
#endif
#if AB_UI
                if (AbConfigPanel.IsOpen)
                {
                    AbConfigPanel.ClosePanel();
                    return;
                }
#endif
                // ★ 放大立绘是模态：这一击归它 ★
                // 10-18 用户实测：不加这道判断，同一次 ESC 会把立绘**和对话窗一起关掉**
                // （两个处理器互不知情：本类的这份在 09-xx 为"按 Esc 关对话窗"加的）。
                // 判据见 `BigPortrait.ConsumesEscape()`：`IsOpen` 或"本帧已被它关过" ——
                // 两条合起来**与本类和 `AbHotkeys.OnFrame` 的先后无关**（谁先跑都拦得住）。
                // 位置在配置窗之后：配置窗真开着时它才是最上层，ESC 仍先关它。
                if (BigPortrait.ConsumesEscape()) return;
                // ★ 工具侧边栏也吃 ESC（10-19）：它盖在对话窗自己的画面上，
                //   开着的层先关 —— 否则按 ESC 会跳过它直接把整个对话窗关掉，
                //   而玩家的意图通常只是"收起这块面板"。位置在放大立绘之后：
                //   立绘是全屏模态，它开着时面板不可能在被操作。
                // 左侧编辑面板（10-31）：盖着半个屏幕且在编辑文本，ESC 的第一意图是"关掉它"，
                // 不是"关整个对话窗"。放在工具面板之前：它更"在进行中"。
                if (CompactEditPanel.IsOpen)
                {
                    CompactEditPanel.Hide();
                    return;
                }
                if (ToolPanel.IsOpen)
                {
                    ToolPanel.Hide();
                    return;
                }
                bool chatFocused = _window.ChatInput != null && _window.ChatInput.isFocused;
                bool npcFocused = _window.NpcLabel != null && _window.NpcLabel.isFocused;
                if (chatFocused || npcFocused)
                {
                    if (chatFocused) _window.ChatInput.DeactivateInputField();
                    if (npcFocused) _window.NpcLabel.DeactivateInputField();
                    return;
                }
                OnCloseClicked();
                return;
            }

            var input = _window.ChatInput;

            // 图片附件入口②：输入框聚焦时 Ctrl+V —— 剪贴板里的图（复制的文件 / 截图位图）收进**待附图**。
            // 三条来源（CF_HDROP / 注册格式 "PNG" / CF_DIB）都在 ImageInput.ClipboardImages 里归一成
            // 「一个文件路径」，无论成败都留痕。**不再把路径写进输入框**（09-22 用户反馈：一串路径很丑，
            // 而且没必要显示）；改由预览条 + 一句计数提示承担反馈。
            // 剪贴板里没有图时**原样放行**（不 return），普通文本粘贴照旧。
            if (input != null && input.isFocused &&
                (Input.GetKey(KeyCode.LeftControl) || Input.GetKey(KeyCode.RightControl)) &&
                Input.GetKeyDown(KeyCode.V))
            {
                string how;
                var clip = ImageInput.ClipboardImages(out how);
                try { ModMain.P("[ImageInput] Ctrl+V 取剪贴板：" + how + " → " + clip.Count + " 张"); } catch { }
                if (clip.Count > 0)
                {
                    int added = 0, dup = 0, over = 0;
                    for (int i = 0; i < clip.Count; i++)
                    {
                        // 去重两处：内存里已排着队的、以及文本里手打/已粘过的同一路径
                        if (IndexOfPath(_pendingImages, clip[i]) >= 0 ||
                            (input.text ?? "").IndexOf(clip[i], StringComparison.OrdinalIgnoreCase) >= 0)
                        { dup++; continue; }
                        if (_pendingImages.Count >= ImageInput.MaxCount) { over++; continue; }
                        _pendingImages.Add(clip[i]);
                        added++;
                    }
                    // 路径不进输入框，玩家对"粘上了没"的唯一凭据是预览条 → 计数与拒绝必须说出来
                    if (added > 0) _window.AppendSystemNotice("已选取 " + _pendingImages.Count + " 张图片（点缩略图右上角 ✕ 取消）");
                    if (dup > 0) _window.AppendSystemNotice("这张图已经在待附图里了");
                    if (over > 0) _window.AppendSystemNotice("最多 " + ImageInput.MaxCount + " 张，多出来的已忽略");
                    return;   // 已消费这次 Ctrl+V
                }
                // 剪贴板有内容但不是图（如普通文本）：放行给 InputField 走原生粘贴
            }

            // 图片预览条：文本**或待附图**变了才重算（解码整图不便宜，绝不能每帧跑）。
            // 预览是「到底附上了没有」的唯一可视反馈 —— 路径不再进输入框，玩家连文字痕迹都看不到。
            string textNow = input != null ? (input.text ?? "") : "";
            string key = PreviewKey(textNow);
            if (key != _previewKey)
            {
                _previewKey = key;
                string _clean;
                var shown = AttachedPaths(textNow, out _clean);
                if (shown.Count == 0) ImageAttachPreview.Hide();
                else ImageAttachPreview.Sync(input, key, shown, ImageInput.MaxCount, OnRemoveImage);
            }

            // ★ 群聊 @ 补全（11-21）★
            // 必须排在下面的"回车发送"**之前**：候选浮层开着时，回车/Tab 是**选中候选**，
            // 不是发送消息。顺序反了就会出现"我想选张三，结果把 @张 发出去了"。
            if (PollMentionPick(input)) return;

            // 消息回车 → 发送：不要求消息框聚焦（2026-09-08 实机反馈：Unity InputField 单行
            // 按 Enter 会先触发 Submit 并自动失焦，isFocused 判定恒假 → 用户只能手动点发送按钮）。
            // 改为"消息框有内容"即视为发送意图（NpcInput 已只读展示 NPC 名，不再支持手改切换。
            // 原 "NPC 名框未在编辑" 竞态守卫随只读化一并移除——它没有可编辑状态了）。
            // 待附图也算内容：路径不再进输入框，"只发图不发字"时框里是空的，只看文本会把这句吞掉。
            if (input != null && (input.text.Length > 0 || _pendingImages.Count > 0) &&
                (Input.GetKeyDown(KeyCode.Return) || Input.GetKeyDown(KeyCode.KeypadEnter)))
            {
                OnSubmitRequested();
                return;
            }
        }

        // ------------------------------------------------------------------
        // 图片附件：内存里的待附图 + 文本里手打的路径 → 一条通道
        // ------------------------------------------------------------------

        /// <summary>预览去重键。待附图不进输入框，所以键必须把它一起算进去 —— 只算文本的话
        /// 「空框 + 刚粘一张图」永远不触发重建，预览就再也出不来了。</summary>
        // ------------------------------------------------------------------
        // 群聊 @ 补全（11-21）
        //
        // 用户要求：打 @ 就弹成员候选、继续打字候选跟着变（与 QQ/微信一致）。
        //
        // ★ 为什么用 Update 轮询、不挂 `InputField.onValueChanged` ★
        //   本类文件头那条既有结论：IL2CPP 下 InputField 的事件家族**不可靠**
        //   （UnityEvent 受委托转类影响）—— 现有的"回车发送"就是因为这个改成轮询的。
        //   @ 补全照同一条规矩做：每帧读 `input.text` + `input.caretPosition`，
        //   与上一帧比对，变了才重算候选。代价是每帧一次字符串比较（几十字节），可忽略。
        //
        // ★ 为什么改文本后要设 `_suppressMentionPoll` ★
        //   选中候选时我们会**写** `input.text` 和 `caretPosition`，而下一帧轮询会
        //   读到这个新值 —— 若不挡住，它会立刻按新文本重算候选，表现为"选完了浮层又弹回来"。
        // ------------------------------------------------------------------

        /// <summary>上一帧看到的输入框内容（用于"变了才重算"）。</summary>
        private string _lastInputText;
        /// <summary>上一帧的光标位置（光标移动也要重算 —— 往回删到 @ 前面时该收起）。</summary>
        private int _lastCaret = -1;
        /// <summary>刚由程序改过文本 ⇒ 跳过**这一次**轮询（见上面那段说明）。</summary>
        private bool _suppressMentionPoll;

        /// <summary>
        /// **当前屏幕上那列候选**是根据哪个 `@ 段` 算出来的（11-21 修 bug 补）。
        ///
        /// ★ 为什么不能用"选中那一刻重新 Analyze 一次" ★
        ///   玩家按 Enter 时，`InputField`（单行）会**先 Submit 并自动失焦**（本文件头
        ///   记过这条实测）。失焦会把 `caretPosition` 挪走，于是同一帧里重新
        ///   `Analyze(text, caret)` 会算出"光标不在任何 @ 段里"⇒ 候选被收起 ⇒
        ///   发送分支接管 ⇒ 玩家"选了人却把 `@` 原样发了出去"。
        ///   实机证据（13:18 g2 群）：Python 侧 `unknown=[]`（@ 后为空），
        ///   Player.log 里 `@ 补全选中` **一次都没有** —— 回调压根没跑过。
        ///
        ///   所以：**候选是按什么算的，就按什么替换**。这个字段是那个"什么"。
        ///   它只随 `RefreshMentionCandidates` 一起变，与这一刻的光标无关。
        /// </summary>
        private MentionPickCore.PickContext _pickCtx;

        /// <summary>
        /// 每帧的 @ 补全驱动。返回 true = 这次按键已被浮层消费（调用方不该再当"发送"处理）。
        ///
        /// 只在**群聊**里生效：单聊没有"对谁说"的问题（窗口就绑着一个人），
        /// 弹候选只会打扰。判据用 `IsRoomMode`，与 `OnSubmitRequested` 的群聊分支同源。
        /// </summary>
        private bool PollMentionPick(InputField input)
        {
            try
            {
                if (_window == null || input == null) return false;

                // 非群聊 / 窗口关着 ⇒ 确保浮层收起，且不吃按键
                if (!_window.IsRoomMode || !_window.IsOpen)
                {
                    // ★ 带 owner ★（11-21）：本类同时存在多个实例（换 NPC / 重开窗时
                    //   旧的未必立刻销毁），它们的 Update 每帧都在跑。非群聊实例若
                    //   无条件 Hide，就会**每帧关掉群聊窗的候选浮层** —— 玩家看到的是
                    //   "浮层闪一下就没了"，且完全无日志。见 `MentionPickPanel._owner`。
                    if (MentionPickPanel.IsOpen) MentionPickPanel.Hide(this);
                    _pickCtx = new MentionPickCore.PickContext();
                    _lastInputText = null;
                    _lastCaret = -1;
                    return false;
                }

                // ① 程序刚改过文本：本帧跳过，只把基准对齐
                if (_suppressMentionPoll)
                {
                    _suppressMentionPoll = false;
                    _lastInputText = input.text ?? "";
                    _lastCaret = input.caretPosition;
                    return false;
                }

                string text = input.text ?? "";
                int caret = input.caretPosition;

                // ★ 先把"这一帧按了什么键"取下来 ★
                //   `GetKeyDown` 只看这一刻的按键状态；取完就固定，本帧内不再回头问 Input。
                bool keyEnter = Input.GetKeyDown(KeyCode.Return) || Input.GetKeyDown(KeyCode.KeypadEnter);
                bool keyTab = Input.GetKeyDown(KeyCode.Tab);
                bool keyUp = Input.GetKeyDown(KeyCode.UpArrow);
                bool keyDown = Input.GetKeyDown(KeyCode.DownArrow);
                bool keyEsc = Input.GetKeyDown(KeyCode.Escape);

                // ══════════════════════════════════════════════════════════════
                // ★★ 键盘必须在**重算之前**处理 —— 这是 11-21 事故的根因 ★★
                //
                //   旧顺序是「先重算候选（可能 Hide）→ 再看 `IsOpen` 决定吃不吃键」。
                //   而玩家按 Enter 时，单行 `InputField` 会**先 Submit 并自动失焦**
                //   （本文件头 :8 / :507 两处既有实测记录），失焦把 `caretPosition` 挪走。
                //   于是同一帧里：caret 变了 → 重算 → `Analyze` 算出"光标不在任何 @ 段里"
                //   → `Hide()` → `IsOpen` 变假 → 回车落到发送分支 → 把 `@` 原样发出去。
                //
                //   实机证据（13:18 g2 群，两条独立日志互相印证）：
                //     · Python 侧 `@ 解析失败（unknown=[]）` —— `@` 后面是**空的**；
                //     · Player.log 里 `@ 补全选中` **零次** —— 回调压根没跑过。
                //
                //   所以顺序必须是：**玩家的意图（按键）→ 数据（文本）**，
                //   而不是**数据（光标）→ 意图**。重算是副作用，副作用不许推翻意图。
                // ══════════════════════════════════════════════════════════════

                // ① Esc：只要文本里有个 `@` 段就吃（撤销这个 @），与浮层是否还开着无关
                if (keyEsc && _pickCtx.HasAt)
                {
                    MentionPickPanel.Hide(this);
                    _pickCtx = new MentionPickCore.PickContext();
                    _lastInputText = input.text ?? "";
                    _lastCaret = input.caretPosition;
                    return true;
                }

                // ② ↑↓ / Tab / Enter：**只看"文本里有没有一个还没确定的 @ 段"**（`_pickCtx`）
                //    它由 `RefreshMentionCandidates` 写入，是屏幕上那列候选的**来源**；
                //    与这一刻的光标无关，所以回车导致失焦也推翻不了它。
                //    再叠加 `IsOpen`：没真的画出候选就不许吃键（凭空吃回车比多发一条更糟）。
                bool picking = _pickCtx.HasAt && MentionPickPanel.IsOpen;
                if (picking)
                {
                    if (keyDown) { MentionPickPanel.MoveHot(+1); return true; }
                    if (keyUp) { MentionPickPanel.MoveHot(-1); return true; }
                    if (keyTab) { EnsureHotRow(); AcceptMentionPick(); return true; }
                    // 回车 = 选中候选（**不发送**）。这就是本方法必须排在发送之前的原因。
                    if (keyEnter) { EnsureHotRow(); AcceptMentionPick(); return true; }
                }

                // ③ 到这里键盘已经处理完 ⇒ 才重算候选（可能会 Hide，但已不影响上面的决策）
                bool changed = text != _lastInputText || caret != _lastCaret;
                if (changed)
                {
                    _lastInputText = text;
                    _lastCaret = caret;
                    RefreshMentionCandidates(input, text, caret);
                }

                return false;
            }
            catch (Exception e)
            {
                try { ModMain.P("[ChatPresenter] PollMentionPick 异常：" + e.Message); } catch { }
                return false;
            }
        }

        /// <summary>按当前文本/光标重算候选并喂给浮层。</summary>
        private void RefreshMentionCandidates(InputField input, string text, int caret)
        {
            var ctx = MentionPickCore.Analyze(text, caret);
            if (!ctx.HasAt)
            {
                // 没有 @ 段 ⇒ 清掉"当前候选来源"，键盘不再抢（否则回车会被莫名吃掉）
                _pickCtx = new MentionPickCore.PickContext();
                if (MentionPickPanel.IsOpen) MentionPickPanel.Hide(this);
                return;
            }
            var members = _window.CurrentRoomMembers;
            var hits = MentionPickCore.Filter(members, ctx.Query);
            if (hits.Count == 0)
            {
                // 用户拍板：没有匹配就不弹（打错名字时不打扰）。
                // ★ 但 `@` 段本身还在：保留 `_pickCtx` ★ —— 玩家可能只是还没打完，
                //   下一个字打进去就会命中；清掉的话"打错一个字再补"这个最常见的手势
                //   会让回车短暂变回"发送"。
                _pickCtx = ctx;
                if (MentionPickPanel.IsOpen) MentionPickPanel.Hide(this);
                return;
            }
            _pickCtx = ctx;
            var anchorRt = input.GetComponent<RectTransform>();
            MentionPickPanel.EnsureBuilt(anchorRt, name => ApplyMentionPick(input, name), this);
            MentionPickPanel.SetCandidates(hits, anchorRt);
        }

        /// <summary>
        /// 键盘/点击确认当前高亮候选 → 写回输入框。
        ///
        /// ★ 这一层存在的唯一理由：**在按键那一刻就把候选取下来** ★
        ///   `MentionPickPanel.AcceptHot()` 会先 `Hide()` 再回调 —— Hide 清空 `_names`，
        ///   而取名字发生在 Hide **之前**（`string name = _names[_hot]` 在 Hide 上面），
        ///   那一步本身是对的。问题出在**回车这条路**：玩家按回车时 InputField 已经
        ///   把光标挪走，回调里若再 `Analyze(text, caret)` 一次就会算错段
        ///   （见 `_pickCtx` 的注释）。所以这里显式取名字、显式用 `_pickCtx` 替换，
        ///   不依赖任何"此刻的状态"。
        ///
        /// 用 `IndexOf` 在候选里反查是刻意的：`AcceptHot` 用的是 `_names[_hot]`，
        /// 我们不知道它内部下标，重复实现一遍下标风险更大 —— 直接问"高亮的是谁"。
        /// </summary>
        /// <summary>保证浮层上有一行是亮的（回车/Tab 之前调）。</summary>
        /// <remarks>
        /// `MoveSelection` 在 `count>0` 时对 `current<0` 会返回 `0`（第一行），但它返回
        /// **false** 只有当 `count<=0`；而 `MoveHot` 外面还套了一层 `IsOpen` 判断。
        /// 这里直接问 `HotIndex`：-1 就往前挪一格（循环到第一行）。
        /// 为什么需要它：`Hide()` 会把 `_hot` 置 -1，而 `SetCandidates` 之后又被
        /// `RepaintHot` 置回 0 —— 两条路都可能留下 -1。放在这里兜底，
        /// 让"浮层开着 ⇒ 回车一定选中某一行"成为不变式（与 `AcceptHot` 里的兜底双保险）。
        /// </remarks>
        private static void EnsureHotRow()
        {
            if (MentionPickPanel.HotIndex < 0) MentionPickPanel.MoveHot(+1);
        }

        private void AcceptMentionPick()
        {
            try
            {
                string name = MentionPickPanel.HotName;
                // 返回 false 只有一种可能：浮层在两次检查之间被收起了（`IsOpen` 变假）。
                // 那就**什么也不写**，并留一行日志 —— 静默失败正是这次事故难查的原因。
                if (!MentionPickPanel.AcceptHot())
                    ModMain.P("[ChatPresenter] @ 补全未生效：没有可选的候选（HotName="
                              + (name ?? "null") + "）");
            }
            catch (Exception e)
            {
                try { ModMain.P("[ChatPresenter] AcceptMentionPick 异常：" + e.Message); } catch { }
            }
        }

        /// <summary>选中候选 → 改输入框文本并把光标放到末尾。</summary>
        private void ApplyMentionPick(InputField input, string name)
        {
            try
            {
                if (input == null || string.IsNullOrEmpty(name)) return;
                string text = input.text ?? "";
                // ★ 用 `_pickCtx`（屏幕上那列候选的来源），不重新 Analyze ★
                //   重新 Analyze 会读到"回车导致失焦后的光标"，算出的段是错的或干脆没有，
                //   `Accept` 于是走 `!ctx.HasAt` 分支 ⇒ 什么都不换 ⇒ 玩家发出去的只有 `@`。
                //   这正是 13:18 g2 群那次事故（Python 侧 `unknown=[]`、
                //   Player.log 里 `@ 补全选中` 零次）。
                var ctx = _pickCtx;
                if (!ctx.HasAt)
                {
                    // 兜底：没有记录来源时按当前光标算一次（正常路径不会走到）。
                    // 保留这一条是因为"什么都不做"会让玩家彻底无从判断（点了没反应）。
                    ctx = MentionPickCore.Analyze(text, input.caretPosition);
                }

                string nt;
                int nc;
                MentionPickCore.Accept(text, ctx, name, out nt, out nc);

                _suppressMentionPoll = true;      // 挡住下一帧重算（见字段注释）
                _pickCtx = new MentionPickCore.PickContext();   // 这次补全已消费
                input.text = nt;
                input.caretPosition = Mathf.Clamp(nc, 0, nt.Length);
                // 选中后把焦点还给输入框：玩家要继续打字（鼠标点候选会让它失焦）
                TryActivateInput(input);
                _lastInputText = nt;
                _lastCaret = input.caretPosition;
                ModMain.P("[ChatPresenter] @ 补全选中：" + name + " → " + nt);
            }
            catch (Exception e)
            {
                try { ModMain.P("[ChatPresenter] ApplyMentionPick 异常：" + e.Message); } catch { }
            }
        }

        /// <summary>待附图预览去重键。待附图不进输入框，所以键必须把它一起算进去 —— 只算文本的话
        /// 「空框 + 刚粘一张图」永远不触发重建，预览就再也出不来了。</summary>
        private string PreviewKey(string text)
        {
            if (_pendingImages.Count == 0) return text;
            var sb = new System.Text.StringBuilder(text).Append('\u0001');
            for (int i = 0; i < _pendingImages.Count; i++)
                sb.Append(_pendingImages[i]).Append('\u0002');
            return sb.ToString();
        }

        /// <summary>这条消息会带上的图：内存里的待附图在前，文本里识别出的路径在后（按路径去重，
        /// 忽略大小写）。文本侧走 ImageInput.SplitPaths 摘掉路径，cleanText 就是给模型的正文。</summary>
        private List<string> AttachedPaths(string text, out string cleanText)
        {
            List<string> fromText;
            ImageInput.SplitPaths(text, out cleanText, out fromText);
            var all = new List<string>(_pendingImages.Count + fromText.Count);
            for (int i = 0; i < _pendingImages.Count; i++) AddPathOnce(all, _pendingImages[i]);
            for (int i = 0; i < fromText.Count; i++) AddPathOnce(all, fromText[i]);
            return all;
        }

        private static void AddPathOnce(List<string> list, string p)
        {
            if (IndexOfPath(list, p) >= 0) return;
            list.Add(p);
        }

        /// <summary>按路径去重的判据（Windows 路径大小写不敏感）。</summary>
        private static int IndexOfPath(List<string> list, string p)
        {
            for (int i = 0; i < list.Count; i++)
                if (string.Equals(list[i], p, StringComparison.OrdinalIgnoreCase)) return i;
            return -1;
        }

        /// <summary>预览条上点了 ✕。待附图是内存态，直接从表里摘掉；文本里手打的那条只能改文本
        /// （把路径片段连同引号一起删掉，剩下的正文照留）。</summary>
        private void OnRemoveImage(string path)
        {
            int at = IndexOfPath(_pendingImages, path);
            if (at >= 0)
            {
                _pendingImages.RemoveAt(at);
                _previewKey = null;   // 强制下一帧重算预览（图少了一张，必须重画）
            }
            else
            {
                if (_window == null || _window.ChatInput == null) return;
                var input = _window.ChatInput;
                string raw = input.text ?? "";
                string next = raw.Replace(ImageInput.QuotePath(path), "").Replace(path, "").Trim();
                if (next != raw) input.text = next;
            }
            // 点 ✕ 会被 EventSystem 当成"选中了另一个 Selectable"，输入框因此失焦 —— 而 Ctrl+V
            // 的入口判据正是 `input.isFocused`。不把焦点抢回来，下一次粘贴就是**什么都不发生**。
            if (_window != null && _window.ChatInput != null) TryActivateInput(_window.ChatInput);
        }

        // ------------------------------------------------------------------
        // 窗口开关 / NPC 切换
        // ------------------------------------------------------------------

        private void ToggleWindow()
        {
            if (_window == null) return;
            if (_window.IsOpen)
            {
                string closingId = _window.CurrentNpcId;
                UnreadStore.NotifyChatClosed(closingId);
                DisposeAgentOnServer(_ws, closingId);   // 关窗=销毁活体（落盘留账，重开自动 resume）
                ClearReplayGate("关窗");                 // 渲染闸落位（同 OnCloseClicked）
#if AB_UI
                AbChatPanel.CloseViaManager();   // 方案 A：关闭交游戏（实例可被销毁，重开自动 resume+回放）
#else
                _window.Close();
#endif
                return;
            }
            string id = _window.NpcLabel != null && _window.NpcLabel.text != null
                ? _window.NpcLabel.text.Trim()
                : "";
            // @ 候选随窗作废（11-21）：成员名单换了，旧候选就是上一个群的名单。
            // ★ 用 `HideForce`（越过 owner）★：换绑是"这份候选与当前窗口无关"的绝对判断，
            //   不该因为"树是别的 presenter 建的"就留下（留下的名单属于上一个群）。
            MentionPickPanel.HideForce();
            _pickCtx = new MentionPickCore.PickContext();
            _lastInputText = null;
            _lastCaret = -1;
            _window.OpenFor(id);
            UnreadStore.NotifyChatOpen(id);
            if (id.Length == 0)
            {
                _window.AppendSystemNotice("请先在右上角输入 NPC 名（回车生效）");
                return;
            }
            // 经刷新而不是直接赋字段：字段与窗口标签必须一起复位。
            // 原来只改 `_busy`（Presenter 的私有字段），`ChatWindow` 的 BusyLabel 无人 SetActive(false)
            // ⇒ 守卫已经放行、标签却还亮着（实机 bug 的最后一环）。
            // ★ 10-17：改成**按登记表重算**（不再无条件 false）—— 手动切到的这个 NPC
            //   可能本来就有回合在跑（比如它刚主动开口），无脑灭会让他的忙标消失。
            RefreshSoloBusy("手动开窗");
            OpenChatAndReplay();
        }

        private void OnCloseClicked()
        {
            // 关窗即清欠账：否则下一回合收口会对着一个已关的窗口补发 open_chat
            //（回调侧有 `!IsOpen` 守卫会拦下，所以只是白发一次 RPC，但没必要）。
            _wantReplay = false;
            // @ 候选浮层随窗收起（11-21）：它的树挂在窗口下面，窗口关了不收起会留一层
            // 看不见但吃射线的浮层（点聊天区没反应，且完全无日志）。
            // ★ 这里必须 `HideForce()`（越过 owner 判据）★：关窗时浮层的 owner 可能是
            //   另一个 presenter（换 NPC 时新旧实例可能同时活着），带上 owner 会被
            //   直接 return 掉 —— 结果是"窗口关了、浮层还在吃点击"。
            MentionPickPanel.HideForce();
            _pickCtx = new MentionPickCore.PickContext();
            _lastInputText = null;
            _lastCaret = -1;
            // 工具侧边栏随窗收起（10-19）：面板挂在 BG 下，窗口一关就看不见了 —— 但它是
            // **独立于窗口显隐**的一棵树（ToolPanel 自己的 root），不收起的话下次开窗会直接
            // 以"开着"的姿态出现，而那时目录可能还是上一个 NPC 的上下文。
            ToolPanel.Hide();
            // 渲染闸一并落位：这个 presenter 的窗口要没了（下一次开窗是新实例、会重新置位）。
            // 不落位的话，`_replayGate` 残留会让**重开的同一个实例**（`OpenForNpc` 复用本实例）
            // 把回执到达前的帧全吞掉。
            ClearReplayGate("关窗");
            // ★ 构建期间暂存的帧一并作废（11-20）：它们属于**这个**窗口的那次回放，
            //   补画到下一个窗口就是"把别人的话挂到这个人头上"。分帧构建会随
            //   `Close → ResetContent` 一起被取消，暂存表却没有别的出口会清它。
            _heldDuringBuild.Clear();
            if (_window != null)
            {
                // 关窗必须先让 Python 把全员复位成单聊（scene/roster 清空）。
                // 漏了的话那群成员之后**每一次单聊回合都会带着群标签** ⇒ 在 1:1 窗里
                // 凭空消失，而且是静默的（回合照跑、模型照看，只有 UI 少东西）。
                // 这不是本地外观问题，是协议义务。
                if (_window.IsRoomMode) CloseRoomCommand();
                string closingId = _window.CurrentNpcId;
                UnreadStore.NotifyChatClosed(closingId);
                DisposeAgentOnServer(_ws, closingId);
#if AB_UI
                AbChatPanel.CloseViaManager();   // 方案 A：关闭交游戏
#else
                _window.Close();
#endif
            }
        }

        /// <summary>NPC 面板按钮入口：开窗并绑定当前 NPC（同 F9 开窗逻辑，供 NpcPanelButton 外部调用）</summary>
        public void OpenForNpc(string npcId)
        {
            if (_window == null) return;
            string id = (npcId ?? "").Trim();
            // 换 NPC：上一轮的渲染闸必须落位（闸门是按 npc_id 记的，残留只会白吞帧；
            // 紧接着的 `OpenChatAndReplay` 会按新 npc 重新置位）。
            ClearReplayGate("换 NPC");
            // 暂存帧同理作废（11-20）：它们是**上一个人**那次回放期间到的，
            // 补画到新人窗里就是把别人的话挂到这个人头上。
            _heldDuringBuild.Clear();
            // 换人先把侧边栏收起（10-19）：面板里每一条都是"对**这个人**说什么"，
            // 换绑后还开着就等于把上一个人的上下文摆在新人脸上。
            ToolPanel.Hide();
            // 换人 = 成员名单变了 ⇒ 旧候选作废（否则会弹出上一个群的人）
            // 同上：换绑是无条件作废，用 `HideForce`
            MentionPickPanel.HideForce();
            _pickCtx = new MentionPickCore.PickContext();
            _lastInputText = null;
            _lastCaret = -1;
            _window.OpenFor(id);            // ← 先绑新 npc，RefreshSoloBusy 才查得到对的人
            UnreadStore.NotifyChatOpen(id);
            // ★ 10-17（用户明确要求的行为）★ 换绑时**不再无条件灭忙标**，改成按新 NPC 重算：
            //     看 A（A 在跑，忙标 ON）→ 切 B（B 空闲 ⇒ OFF）→ 切回 A ⇒ **恢复成 ON**
            //   状态活在 `TurnRegistry`（按 npc 名记），不在窗口里 —— 所以切走不丢。
            //   这里若写成 `SetBusy(false)`，切回来时 A 明明还在跑却显示空闲。
            RefreshSoloBusy("换 NPC");
            // 换 NPC / 关开窗：压缩提示行会随重放被销毁，忙态也要一起复位
            // （压缩若仍在跑，结果到达时按 npc_id 过滤后落到 `AppendSystemNotice` 兜底补一行）
            if (_compacting) { _compactNotice = null; ClearCompactBusy(); }
            if (id.Length == 0)
            {
                _window.AppendSystemNotice("未获取到 NPC id");
                return;
            }
            OpenChatAndReplay();
        }

        // ------------------------------------------------------------------
        // 发送（用户 → Python）
        // ------------------------------------------------------------------

        private void OnSubmitRequested()
        {
            if (_window == null) return;
            var input = _window.ChatInput;
            string msg = input != null ? input.text.Trim() : "";
            // 待附图不在文本里（路径不进输入框了），所以"只发图不发字"时 msg 是空的 —— 光看 msg
            // 会把这条消息整个吞掉，而且吞得毫无痕迹。
            if (msg.Length == 0 && _pendingImages.Count == 0) return;

            string npc = _window.CurrentNpcId;
            // ★ 群聊模式下 `CurrentNpcId` **刻意为空**（群聊没有唯一对话对象，见 ChatWindow.OpenForRoom）。
            //   所以下面那条「请先填写 NPC 名」的早退**不能**先跑 —— 否则群聊每句都会被它挡下，
            //   且提示文案还是错的（玩家明明在群里、不在填 NPC 名）。
            //   判据顺序：连接 → 群聊命令 → 群聊发言 → 单聊前置检查 → 单聊路径。
            if (_ws == null)
            {
                _window.AppendSystemNotice("未连接 Python（WsClient 未启动）");
                return;
            }
            // 上面那个判据查的是「客户端对象在不在」，而 `_ws` 在 Init 赋值后
            // **永不为 null**（见本文件 :37/:71）——所以 Python 进程死掉时这里照样放行，
            // 帧到 `WsClient.Send` 被 `!IsOpen` 静默丢弃，玩家屏幕上"什么都没发生"。
            // 这里按**连接状态**再判一道，并把当前真实状态如实告诉玩家（不许静默失败）。
            if (!_ws.IsConnected)
            {
                _window.AppendSystemNotice(BrainLink.SendBlockedNotice());
                return;
            }

            // ---- 群聊入口已全部搬到**通讯录**（P3）----
            // 这里原先有 `/group 甲 乙`（建群）与 `/ungroup`（离群）两条斜杠命令，是 P1b
            // "零 UI 风险先跑起来"的临时入口。通讯录的群聊页做出来之后它们就是**重复入口**：
            // 建群 → 「＋ 新建群聊」，离群 → 群窗右上角 ✕（`OnCloseClicked` 在群模式下走
            // `CloseRoomCommand`）。留着只会让"同一件事有两种说法"，而且斜杠命令会**吃掉**
            // 玩家正常的一句话（词边界判断再严也有 `"/group"` 这种真·正文）。故一并删除。

            // ---- 群聊模式下，这句话是**对全群**说的：走 room_message 事件 ----
            // 与单聊的关键差别：单聊每句都指定 `npc_id`，群里不指定 —— 由 Python 的
            // 房间调度决定谁接话（`speak_first` 留给将来点头像点名）。
            if (_window.IsRoomMode)
            {
                // 群聊不带图：`room_message` 帧里根本没有 images 字段，Python 侧也不接。
                // 以前路径写在输入框里，会以文本形式混进发言（难看但至少"看得见"）；现在待附图
                // 只在内存里 → 必须明着拒绝并清空，不能让玩家以为图发出去了。
                if (_pendingImages.Count > 0)
                {
                    _window.AppendSystemNotice("群聊不支持发图，已忽略 " + _pendingImages.Count
                        + " 张（要到某个人的单聊窗发）");
                    _pendingImages.Clear();
                    _previewKey = null;
                    if (msg.Length == 0) return;
                }
                // ★ 群聊**不提供**手动压缩（docs/archive/2026-09-17-群聊编排/设计定稿.md §9.2.1 定稿）：压缩的单位是某个
                //   NPC 的**一整份记忆**（`compact_now` 对整份 surface 选段、不做 scene 过滤），
                //   正确归属地是他的单聊窗。此前这条命令会掉进下面的群发言分支 —— 结果
                //   不是"压缩没生效"，而是"/compact"这串命令文本被当成**一条群消息**发出去：
                //   写进 Room.log、被所有 NPC 看到（§16.11 记的既有行为）。
                //   就地拦下：清输入框 + 一句指路提示，**不落气泡、不发请求**（本地气泡若
                //   已画、而群里根本没有这条，就会变成两边不一致的幽灵发言）。
                //   （Python 侧另有一道同口径兜底，见 ChatHub.handle_room_message。）
                if (msg.Equals("/compact", StringComparison.OrdinalIgnoreCase))
                {
                    if (input != null) { input.text = ""; TryActivateInput(input); }
                    _window.AppendSystemNotice(
                        "群聊不提供手动压缩：压缩作用于某个 NPC 的全部记忆，请到他的单聊窗口操作");
                    ModMain.P("[ChatPresenter] 群聊里 /compact 已在本地拦下（不发送）");
                    return;
                }
                _window.AppendUserMessage(msg);          // 本地先落气泡
                // 记一笔"这条是我方刚发的"：Python 的回声到达时凭它**销账而不是再画一遍**
                // （本地这条没有 uid 可登记，光靠 `_roomEchoSeen` 去不掉，见 `ApplyRoomSay`）。
                _roomEchoPending.Enqueue(msg);
                while (_roomEchoPending.Count > RoomEchoPendingMax) _roomEchoPending.Dequeue();
                // 发送即收起候选浮层：消息已经带上了 @名字，浮层再留着就是残影。
                // ★ 同时清掉 `_pickCtx` ★：它代表"文本里那个还没确定的 @ 段"，
                //   而这条消息已经发出去了 —— 残留会让**下一句**的回车被它吃掉一次
                //   （浮层其实是空的，`picking` 里还有 IsOpen 兜底，但状态不该留着）。
                MentionPickPanel.Hide(this);
                _pickCtx = new MentionPickCore.PickContext();
                _lastInputText = null;
                _lastCaret = -1;
                // ★ 留一次原文 ★（11-21 加）：上次 @ 事故排查时，`unknown=[]` 只能证明
                //   "@ 后面是空的"，却无法回答"玩家到底打了什么" —— 两份日志都查不到原文。
                //   群聊发送是本文件唯一的外发入口，就在这里记一次。
                ModMain.P("[ChatPresenter] 群发言发送 ｜ room=" + _window.CurrentRoomId
                          + " 原文=" + (msg.Length > 200 ? msg.Substring(0, 200) + "…" : msg));
                _ws.SendRoomMessage(_window.CurrentRoomId, msg);
                if (input != null) { input.text = ""; TryActivateInput(input); }
                SetBusy(true);                            // 等 room_state(phase=end) 复位
                return;
            }

            if (npc.Length == 0)
            {
                _window.AppendSystemNotice("请先填写 NPC 名");
                return;
            }

            // /compact 控制指令：不进玩家气泡、不进对话历史——清空输入框后
            // 走专用压缩通道（event=compact），Python 端调 compact_now 并回推 compact_result；
            // 「正在压缩中」提示行由结果事件原位更新为压缩反馈（成功/无内容/失败/忙）。
            if (msg.Equals("/compact", StringComparison.OrdinalIgnoreCase))
            {
                // 死者收件（10-15 待办③）：逝者的记忆无从压缩 —— 本地一句提示，不发请求。
                {
                    string deadUid0 = "";
                    try { deadUid0 = UnitIdRegistry.IdOf(npc); } catch { }
                    if (UnitSnapshot.IsDead(deadUid0, npc))
                    {
                        if (input != null) { input.text = ""; TryActivateInput(input); }
                        _window.AppendSystemNotice(npc + "许久没有回复，也许是已经遭遇了不测（逝者的记忆无从压缩）");
                        return;
                    }
                }
                if (input != null)
                {
                    input.text = "";
                    TryActivateInput(input);
                }
                // 控制指令不吃图：待附图一并清掉，否则会骑到下一句真正的发言上
                if (_pendingImages.Count > 0) { _pendingImages.Clear(); _previewKey = null; }
                StartManualCompact(npc);
                return;
            }

            // 图片附件：待附图（内存）+ 文本里的图片路径 → 读文件 → 降采样 → base64 data URL。
            // 路径**从正文里摘掉**（路径对模型是噪音），再按 `[图片：文件名]` 占位拼回 text ——
            // 占位由 C# 一侧生成、Python 不再改写，故本地气泡 / 落盘历史 / 后续回合三者同一串。
            string clean;
            List<string> imgPaths = AttachedPaths(msg, out clean);

            List<JObject> attachments = null;
            if (imgPaths.Count > 0)
            {
                List<string> names, errors;
                attachments = ImageInput.Build(imgPaths, out names, out errors);
                for (int i = 0; i < errors.Count; i++) _window.AppendSystemNotice("图片：" + errors[i]);
                if (attachments.Count == 0) attachments = null;
                else
                {
                    var sb = new System.Text.StringBuilder(clean);
                    for (int i = 0; i < names.Count; i++)
                    {
                        if (sb.Length > 0) sb.Append('\n');
                        sb.Append(ImageInput.Placeholder(names[i]));
                    }
                    clean = sb.ToString();
                    _window.AppendSystemNotice("已附加 " + attachments.Count + " 张图片");
                }
            }
            if (clean.Length == 0) return;      // 路径全被吃掉又没有正文：理论上不会（占位非空）

            // 游戏时间戳：NPC 感知不到时间，故玩家消息在**进 WS 之前**拼上当前游戏
            // 日期（`[1年1月3日] `，读不到日退化成 `[1年1月]`），让 Python 账本永久带上"这句话
            // 是哪年哪月说的"——这样 NPC 才可能说出"上次见你是三月前"。日期读数与换算见
            // NpcInitiativeMonitor.CurrentTimeLabel / UnitSnapshot.CnDate（与经历「近况」共用
            // 同一条换算，不各写一份）。
            //
            // 位置必须在这里（本地回显之前）—— 账本 / WS / 模型后续回合 / `OfferSolo` 重演帧
            //   看到的必须是**同一个串**（同 `[图片：xx]` 占位那条唯一来源约定）。若改在
            //   WsClient.SendPlayerMessage 里拼，落账的串与这里回显/记帧的串就成了两条。
            //   气泡观感是另一层（10-22 用户定案）：**屏幕不显示日期**（破坏沉浸感），由渲染口
            //   `ChatWindow.AppendUserMessage` 调 `BubbleTextCore.StripLeadStamp` 统一剥行首戳——
            //   实时回显 / 关窗重演 / get_history 回放三个出口都过它 ⇒ 三处观感必然同步，
            //   而这里拼的串照旧原样发送、落账、记帧。
            // 日历取不到 → 标签为空串 → **原样照发**，时间戳绝不阻断玩家发言。
            string stamp = NpcInitiativeMonitor.CurrentTimeLabel();
            if (stamp.Length > 0)
            {
                // 自检留痕（换月才打一行，不刷屏）：这是**唯一**能反查"账面月标度对不对"的现场证据。
                // 判读：同一游戏月内，这里打的标签必须与经历「近况」条目前的 `(N年M月)` 一致；
                // 若整月差一个月（近况 (1年1月) 而这里 [1年2月]），就是 CurrentAccountMonth 的
                // `roundMonth + 1` 标度错了 —— 那是本机制唯一的一处魔法常量。
                if (stamp != _lastStampLogged)
                {
                    _lastStampLogged = stamp;
                    ModMain.P("[ChatPresenter] 玩家消息时间戳 " + stamp
                              + "（roundMonth=" + (NpcInitiativeMonitor.CurrentAccountMonth() - 1) + "）");
                }
                clean = stamp + " " + clean;
            }

            // 本地回显 → 清输入框并保持焦点 → 发 WS
            _window.AppendUserMessage(clean);
            // ★ 玩家发言进"当前未闭合段"（**与是否显示无关**）。单聊**没有**回声帧（群聊那条
            //   `room_say` 是 Python 广播回来的），这是"关窗前我说过什么"的唯一记录来源 ——
            //   不记它，重开窗的整段重演里就永远缺这一行（用户 09-17 实机报告的第一条）。
            //   记的必须是**发出去/落账的那一串**（含时间戳与 `[图片：xx]` 占位）——重演帧与
            //   账本同串，上屏时才由渲染口剥戳（与回放同一路径，见上面契约）。
            ChatFrameQueue.OfferSolo(npc, new ChatFrame { Event = "user_say", Seq = -1, Turn = -1, Text = clean });
            // 死者收件（10-15 待办③）：消息**照发、账照记**（上面气泡已画、OfferSolo 已落 = 回放可见），
            // 但**不进 LLM**——不发 WS、不进 busy（没有回帧会来，busy 会永远转圈）。
            // 收件人精确身份：UnitIdRegistry 是"打开对话 UI 那一刻"登记的（GetUnit 对死者必 null，
            // 活人解析路径拿不到）；ID 缺失就按名查账本兜底（IsDead/FindDead 原生支持）。
            {
                string deadUid = "";
                try { deadUid = UnitIdRegistry.IdOf(npc); } catch { }
                if (UnitSnapshot.IsDead(deadUid, npc))
                {
                    if (input != null) { input.text = ""; TryActivateInput(input); }
                    // 待附图一并清：图片没随消息编码发走（Build 的成果作废），留着会骑到
                    // 下一句真正的发言上（与 /compact 分支同款处理）。
                    if (_pendingImages.Count > 0) { _pendingImages.Clear(); _previewKey = null; }
                    _window.AppendSystemNotice(npc + "许久没有回复，也许是已经遭遇了不测");
                    ModMain.P("[ChatPresenter] 收件人已身故，消息只落账不发送：npc=" + npc);
                    return;
                }
            }
            // 玩家开始输入/发言 = 他接管了屏幕：渲染闸立刻落位（否则他这句话的流式回帧会被
            // 闸门吞掉，等一个已经过期的重放回执 —— 那种"发了没反应"最难查）。
            ClearReplayGate("玩家发消息");
            if (input != null)
            {
                input.text = "";
                TryActivateInput(input);
            }
            // 图已经随这条消息编码发走了（Build 是同步读文件），待附表必须清空 ——
            // 留着会让下一句凭空带上这张图。
            if (_pendingImages.Count > 0) { _pendingImages.Clear(); _previewKey = null; }
            // ★ 10-17：**回合起点**（协议事实）—— 先登记，再按登记表刷新忙标。
            //   不能只 `SetBusy(true)`：那样忙态只活在 presenter 字段里，窗口换绑/内容重建
            //   都会把它冲掉（见 `TurnRegistry` 文件头的事故链）。
            TurnRegistry.Begin(npc, "玩家发消息");
            RefreshSoloBusy("玩家发消息");
            // 新一轮对话开始 ⇒ 压缩纪要的修改窗口关闭（Python 侧同样会拒；这里让按钮立刻跟上）
            _window.LockSummaryEdits("玩家发消息");
            // 无图走原重载（保外部自测工程既有 override 生效）；带图才走新重载
            if (attachments != null) _ws.SendPlayerMessage(npc, clean, new JArray(attachments));
            else _ws.SendPlayerMessage(npc, clean);
        }

        /// <summary>手动压缩公共入口（/compact 命令与标题栏「压缩」按钮共用，单一实现不复制）：
        /// 立一条「正在压缩中」提示行，压缩结果由 compact_result 事件原位更新该行。
        /// 调用方自把守卫（按钮路径忙时拒绝；命令路径沿用原语义，Python 侧闸兜底）。</summary>
        private void StartManualCompact(string npc)
        {
            _compactNoticeBase = "正在压缩对话历史…";
            _compactNotice = _window.AppendSystemNotice(_compactNoticeBase + "（已 0s，摘要调用较慢，请勿重复点击）");
            _compacting = true;
            _compactStartAt = Time.unscaledTime;
            _compactLastSec = -1;
            try { _window.SetCompactBusy(true); } catch { }
            // 日志：此前零日志 → "点了没反应"无法从 C# 侧确认；现在两端都有时间戳
            ModMain.P("[ChatPresenter] 压缩请求已发：npc=" + npc +
                      (_compactNotice != null ? "" :
                       "（★提示行未渲染：系统提示行模板缺失？按钮忙态仍可见，压缩照常执行）"));
            _ws.SendCompactRequest(npc);
        }

        // ------------------------------------------------------------------
        // 右侧「可做的事」侧边栏（10-19 用户需求）
        //
        // 三个要点，改这里之前先读：
        //   ① **点击不是执行**。十个工具全是 NPC 的动作（description 里都写着「发起方恒为
        //      当前对话 NPC」），这里做的只是把一句话送进正常的玩家发言通道；模型仍可拒、
        //      可讲条件、可反问。
        //   ② `send` 型走 `OnSubmitRequested` **同一条路**（时间戳 / 死者拦截 / TurnRegistry /
        //      渲染闸一样不少）。所以实现方式是"把话写进输入框再调它"，
        //      **绝不另写一份发送逻辑** —— 两份实现必然漂移，而漂移的那一份通常是安全校验。
        //   ③ `fill` 型**不发送**，只填框 + 定位光标，玩家补齐自己按回车。
        // ------------------------------------------------------------------

        /// <summary>顶栏「工具」钮：开关侧边栏。目录没拉过就先拉（首次打开多一跳 RPC）。</summary>
        private void OnToolsClicked()
        {
            if (_window == null || !_window.IsOpen) return;
            // 群聊里这颗钮是藏起来的（`SetRoomButtonsVisible`），但事件仍可能从别处来 —— 明拒
            if (_window.IsRoomMode)
            {
                _window.AppendSystemNotice("群聊里不提供工具面板：这里的工具都是对某一个人做的事");
                return;
            }
            if (ToolPanel.IsOpen) { ToolPanel.Hide(); return; }
            if (ToolPanel.HasData) { ToolPanel.Show(); return; }

            if (_ws == null || !_ws.IsConnected)
            {
                _window.AppendSystemNotice(BrainLink.SendBlockedNotice());
                return;
            }
            // 首次：拉目录。回调总在 Unity 主线程（SendRequest 的约定），可直接建 UI。
            ModMain.P("[ChatPresenter] 请求工具目录（list_tools）");
            _ws.SendRequest("list_tools", new JObject(), resp =>
            {
                bool ok = false;
                try { ok = resp != null && resp["ok"] != null && resp["ok"].Value<bool>(); } catch { }
                if (!ok)
                {
                    string err = "";
                    try { err = (string)resp["error"]; } catch { }
                    ModMain.P("[ChatPresenter] ★工具目录拉取失败: " + (err ?? "未知"));
                    // 不静默：玩家点了要有回音（否则就是"点了没反应"）
                    if (_window != null)
                        _window.AppendSystemNotice("工具目录没能取到（" + (string.IsNullOrEmpty(err) ? "无应答" : err) + "）");
                    return;
                }
                var data = resp["data"] as JObject;
                ToolPanel.SetData(data);
                // 目录回来了才开：空目录也开（面板顶部会有"可做的事"与 ✕，比"什么都不发生"诚实）
                ToolPanel.Show();
            }, 8000);
        }

        /// <summary>侧边栏里点了一行（`ToolPanel.ItemChosen`）。`mode`/`say` 原样来自目录。</summary>
        private void OnToolItemChosen(string mode, string say)
        {
            if (_window == null || !_window.IsOpen) return;
            string text = say ?? "";
            if (text.Length == 0) return;

            if (ToolCatalogCore.IsFill(mode, text))
            {
                // 填空型：**不发**，只填进输入框并把光标落在空位上。
                // 空位字符（全角空格）原样保留 —— 玩家在它前面打字，比"先删一个占位符"直观。
                FillInput(text);
                return;
            }

            // 发送型：写进输入框 → 复用 `OnSubmitRequested`（唯一发送路径）。
            var input = _window.ChatInput;
            if (input == null)
            {
                _window.AppendSystemNotice("输入框不可用，无法从面板发言");
                return;
            }
            input.text = ToolCatalogCore.BeforeSend(text);
            ModMain.P("[ChatPresenter] 面板发言：" + text);
            OnSubmitRequested();
        }

        /// <summary>把一句话填进输入框、聚焦、光标停在空位（`ToolCatalogCore.CaretAt`）。
        /// 同时给一行提示 —— 玩家看到的是"半句话"，没有提示会以为已经发出去了。</summary>
        private void FillInput(string say)
        {
            var input = _window.ChatInput;
            if (input == null) return;
            input.text = ToolCatalogCore.TextFor(say);
            int caret = ToolCatalogCore.CaretAt(say);
            try { input.caretPosition = caret; } catch { }
            try { input.selectionAnchorPosition = caret; input.selectionFocusPosition = caret; } catch { }
            try { input.Select(); input.ActivateInputField(); } catch { }
            _window.AppendSystemNotice("把「" + BLANK_HINT + "」处补上要说的东西，再按回车发出");
            ModMain.P("[ChatPresenter] 面板填空：光标 @ " + caret + " ｜ " + say);
        }

        /// <summary>填空提示里的"空位"符号（玩家看的话，不是目录里的全角空格）。</summary>
        private const string BLANK_HINT = "空格";

        /// <summary>标题栏「压缩」按钮：忙时即时拒绝（与 🗑 删除按钮同款轻守卫，反馈不隔回合），
        /// 闲时与 /compact 命令同通路。删除选择模式中放行——压缩与删除在 Python 侧天然互斥
        /// （delete RPC 有 _compacting 闸），无需特判。</summary>
        private void OnCompactBtnClicked()
        {
            if (_window == null || _ws == null || string.IsNullOrEmpty(_window.CurrentNpcId)) return;
            // 未连接时压缩请求同样会被静默丢弃（它的回调永不触发 → 看起来"按了没反应"）
            if (!_ws.IsConnected)
            {
                _window.AppendSystemNotice(BrainLink.SendBlockedNotice());
                return;
            }
            if (_busy || _window.HasActiveBubble)
            {
                _window.AppendSystemNotice("回合进行中，结束后再压缩");
                return;
            }
            StartManualCompact(_window.CurrentNpcId);
        }

        private static void TryActivateInput(InputField field)
        {
            try
            {
                if (!field.isFocused) field.ActivateInputField();
            }
            catch (Exception e)
            {
                ModMain.P("[ChatPresenter] ActivateInputField: " + e.Message);
            }
        }

        // ------------------------------------------------------------------
        // 入站（Python → 视图）：唯一的协议翻译点
        // ------------------------------------------------------------------

        private void OnUiEvent(JObject payload)
        {
            string ev0 = (string)payload?["event"] ?? "";
            // 收口类帧逐帧留痕（其余帧高频，只在被丢弃时打）：把"收不到"与"收到了但守卫拦了"分开
            if (ev0 == "npc_reply")
                ModMain.P("[ChatPresenter] npc_reply 入站 实例#" + _id + " 窗口=" + (_window != null)
                          + " 开=" + (_window != null && _window.IsOpen) + " 忙(前)=" + _busy);
            if (_window == null) { DiagDrop(ev0, payload, "窗口为空"); return; }
            // 心跳帧（切磋跨战斗这类长等）：**不画内容，只喂"还在等"这个事实**，所以放在
            // npc/room 准入过滤**之前**处理 —— 它没有 room_id，按内容帧的规矩会被群窗一律丢弃，
            // 而"忙标被 BusyStaleSeconds 误清成空闲"在群窗里同样会发生。
            if (ev0 == "tool_pending")
            {
                string pn = (string)payload["npc_id"] ?? "";
                if (pn.Length == 0 || _window.IsRoomMode || pn == _window.CurrentNpcId)
                {
                    _lastFrameAt = Time.unscaledTime;   // 这不是"帧丢了"，是在等一场战斗
                    if (_window.IsOpen) _window.SetBusyPhase((string)payload["phase"] ?? "");
                }
                return;
            }
            // 忙标阶段帧（11-21）：与 tool_pending 同一个位置 —— **准入过滤之前**。
            // 理由相同：它没有 room_id（单聊帧），按内容帧的规矩会被群窗一律丢弃；
            // 而群聊里同样需要"正在等谁"。它不画内容、只改忙标文案，所以谁都能收。
            if (ev0 == "wait_phase")
            {
                string wp = (string)payload["npc_id"] ?? "";
                if (wp.Length == 0 || _window.IsRoomMode || wp == _window.CurrentNpcId)
                {
                    bool entering = payload["entering"]?.Value<bool>() ?? false;
                    ApplyWaitPhase((string)payload["phase"] ?? "", entering);
                }
                return;
            }
            if (!_window.IsOpen) { DiagDrop(ev0, payload, "窗未开"); return; }
            string npcId = (string)payload["npc_id"] ?? "";
            bool isRoomFrame = ev0 == "room_say" || ev0 == "room_state";
            if (_window.IsRoomMode)
            {
                // 群窗：**唯一准入判据是 room_id 相等**（不是"npc 是成员"——后者会让
                // 别的群的成员帧漏进来）。群聊里 `npc_id` 只是气泡归属，不参与准入。
                if (!_window.AcceptsRoomFrame((string)payload["room_id"] ?? ""))
                {
                    DiagDrop(ev0, payload, "room不匹配 payload=" + ((string)payload["room_id"] ?? "")
                             + " 面板=" + _window.CurrentRoomId);
                    return;
                }
            }
            else
            {
                // 单聊：群聊专用帧一律不进（它们没有 npc_id，放进来会画错窗口）
                if (isRoomFrame) { DiagDrop(ev0, payload, "单聊窗收到群聊帧"); return; }
                if (npcId != _window.CurrentNpcId)
                {
                    DiagDrop(ev0, payload, "npc不匹配 payload=" + npcId + " 面板=" + _window.CurrentNpcId);
                    return;
                }
            }
            _lastFrameAt = Time.unscaledTime;
            // ★ 渲染闸：重开窗的重放还没落地 ⇒ **会进段的那几类帧**（step / text_delta /
            //   npc_reply）由帧队列负责（`ChatFrameTee` 已经记下），直播这边**不画** ——
            //   否则回执落地时整段重演会把同一批帧画第二遍。放在 `_lastFrameAt` 之后：
            //   闸门期间照样算"有帧到达"，忙标自愈时钟不受影响。
            //   ★ 判据**只吞队列真会补画的**（`IsSoloBuffered` 与 tee 的收帧白名单同源）：
            //   队列不收的（`stats_update` / `compact_result` / `step(sys|ctx)`）一律照常直播
            //   —— 吞了它们就是"两边都没有"（既不直播也不重演），而 `compact_result` 还是
            //   "正在压缩中"那一行唯一的收尾信号（吞掉 ⇒ 提示行停在"已 Ns"直到看门狗
            //   误报"长时间无响应"，且零日志）。见 `ChatFrameQueue.IsSoloBuffered`。
            if (!_window.IsRoomMode && _replayGate.Length > 0 && _replayGate == npcId
                && ChatFrameQueue.IsSoloBuffered(ev0, payload["kind"]?.ToString() ?? "")) return;
            // ★ 分帧构建期间先**暂存**，建完再补画（11-20）★
            //
            // 历史正在分帧往 Content 里建（大回放摊到十几~几十帧）。这期间到达的直播帧
            // 如果照画，会插进"还没建完的历史"中间 —— 屏幕上表现为**新消息出现在旧消息之间**，
            // 且越早到的越靠中间。等历史建完，它该在的位置早被历史行占住，永久错位。
            //
            // ★ 判据是"构建中"就**全暂存**，不按事件类型挑 ★
            //   一开始只暂存 step/room_say/text_delta/npc_reply，漏了 `room_state` ——
            //   它的 `error` / `end` 分支会 `AppendSystemNotice` 往 Content 里加行、
            //   `speak` 分支会改流式状态，全都插得进历史中间；而它**没有 `kind` 字段**，
            //   拿 `IsPlayableStepKind` 过滤时恒为 false ⇒ 永远走不到暂存。
            //   `compact_summary` 同理（它建纪要行）。按类型枚举必然漏，所以反过来：
            //   构建期间屏幕内容本来就不完整，任何直播帧都不该往里插。
            //
            // 为什么不能"直接丢掉、指望帧队列补画"：
            //   · 单聊：队列确实会补（tee 始终记），但 `_replayGate` 在回执那一刻就落位了，
            //     而构建还要跑十几帧 ⇒ 之间到的帧两边都不画（闸已开、队列又没人消费）；
            //   · **群聊：队列根本不补** —— tee 的群分支有 `IsShowingRoom ⇒ return`
            //     （窗口正显示这个群时不缓冲）。这里丢掉就是**真的丢**，那句话永远不出现。
            // 所以自己暂存一份，`AfterHistoryBuilt` 那段续跑里按原序补画。
            if (_window.IsBuildingHistory)
            {
                if (_heldDuringBuild.Count >= HeldDuringBuildMax) _heldDuringBuild.RemoveAt(0);
                _heldDuringBuild.Add(payload);
                DiagDrop(ev0, payload, "历史分帧构建中（已暂存，建完补画）");
                return;
            }
            switch (ev0)
            {
                case "step":
                    OnStepEvent(payload);
                    break;
                case "text_delta":
                    OnDeltaEvent(payload);
                    break;
                case "npc_reply":
                    OnReplyEvent(payload);
                    break;
                case "compact_result":
                    OnCompactResult(payload);
                    break;
                case "compact_summary":
                    OnCompactSummary(payload);
                    break;
                case "stats_update":
                    // 回合结束全量 token/时延快照 → 刷新底部统计条
                    if (_window != null) _window.SetStats(payload);
                    break;
                case "room_say":
                    OnRoomSay(payload);
                    break;
                case "room_state":
                    OnRoomState(payload);
                    break;
            }
        }

        /// <summary>只做"把帧交给对应处理"这一步，不跑任何准入守卫。
        ///
        /// 存在的唯一原因：`FlushHeldDuringBuild` 补画时**必须**跳过守卫 ——
        /// 那些帧已经通过过一次守卫（只是当时历史还在建），再走一遍会在
        /// "构建刚结束、`IsBuildingHistory` 还是 true"之类的边界上被再吞一次。
        /// 守卫与分发拆开之后，补画这条路只有"按序重放"这一件事。
        /// </summary>
        private void DispatchUiEvent(JObject payload)
        {
            string ev0 = (string)payload["event"] ?? "";
            switch (ev0)
            {
                case "step": OnStepEvent(payload); break;
                case "text_delta": OnDeltaEvent(payload); break;
                case "npc_reply": OnReplyEvent(payload); break;
                case "compact_result": OnCompactResult(payload); break;
                case "compact_summary": OnCompactSummary(payload); break;
                case "stats_update": if (_window != null) _window.SetStats(payload); break;
                case "room_say": OnRoomSay(payload); break;
                case "room_state": OnRoomState(payload); break;
            }
        }

        /// <summary>被守卫拦下的帧留痕（只关心收口类，避免高频帧刷屏）。
        /// ：删/压缩入口卡死时，"帧到底有没有到 presenter"此前完全无迹可查 ——
        /// 只能靠推理，而推理在"到底卡在收不到还是卡在没复位"上会绕圈。</summary>
        private void DiagDrop(string ev, JObject payload, string why)
        {
            if (ev != "npc_reply" && ev != "room_say" && ev != "room_state") return;
            try
            {
                ModMain.P("[ChatPresenter] ★帧被丢弃(" + why + ") event=" + ev
                          + " npc=" + ((string)payload?["npc_id"] ?? "")
                          + " room=" + ((string)payload?["room_id"] ?? "") + " 实例#" + _id);
            }
            catch { }
        }

        // ------------------------------------------------------------------
        // 群聊（Room）—— 开窗入口 / 事件翻译
        //
        // 原先这里还有 `/group`（建群）与 `/ungroup`（离群）两条斜杠命令，是 P1b 的临时
        // 入口；通讯录群聊页做出来之后已**删除**：
        //   · 建群 → 通讯录「＋ 新建群聊」；离群 → 群窗右上角 ✕（`OnCloseClicked` 在群模式下
        //     走 `CloseRoomCommand`）。留着就是"同一件事有两种说法"。
        //   · 斜杠命令还会**吃掉玩家正常的正文**（词边界判断再严，`/group` 本身也是合法输入）。
        // 现在唯一的开窗入口是 `OpenRoomById`，调用链只有一条：
        //   通讯录 → `ChatLauncher.OpenForRoom` → `AbChatPanel.OpenForRoomStatic` → 本方法。
        // ------------------------------------------------------------------

        /// <summary>进入 / 新建一个群 —— **群窗的唯一开启路径**。
        ///
        /// **两个**入口都汇到这：通讯录群列表点一行（给 `room_id`）、
        /// 通讯录「＋ 新建群聊」选人后确认（给成员）。
        ///
        /// `room_id` 非空 = 重开/续聊那个群（`members` 可空，成员以 Python 侧为准）；
        /// `room_id` 为空 + `members` 非空 = 新建。
        /// ★ **room_id 一律由 Python 分配并回传**（`new_room_id` 需要权威的已用 id 集合，
        ///   C# 造不出来；两个生成器必然发散）。本方法从不自己造 id。
        ///
        /// 为什么"先占位开窗、再发 RPC"（而不是等回执再开）：
        ///   ① 点击立刻有反馈（先崩出一个空群窗），而不是十几毫秒的"没反应"；
        ///   ② 回执回调里那条 `IsOpen` 守卫**天然成立** —— 玩家中途关窗仍会被正确丢弃，
        ///      不需要为"通讯录发起的开群"再开一个例外口子。
        /// 标题先用调用方给的 hint（通讯录手上有 `list_rooms` 拉到的群名），回执再校准。</summary>
        public void OpenRoomById(string roomId, List<string> members, string titleHint,
                                 JObject memberUnitIds = null)
        {
            if (_window == null) return;
            // 进群 = 离开单聊：渲染闸落位（单聊的整段重演不该在群窗里发生；
            // 群窗要不要重放由 `ApplyOpenRoom` 的 baseline 那套决定）。
            ClearReplayGate("进群");
            // 进群 = 离开单聊：工具侧边栏收起（它只在单聊可见，理由见 `SetRoomButtonsVisible`）。
            ToolPanel.Hide();
            bool hasRoom = !string.IsNullOrEmpty(roomId);
            bool hasMembers = members != null && members.Count > 0;
            if (!hasRoom && !hasMembers)
            {
                _enterDeleteOnReplay = false;   // 门都没开成：删除欠账不许留到下一次开窗
                _window.AppendSystemNotice("开群失败：既没有群 id，也没有成员");
                return;
            }
            if (_ws == null || !_ws.IsConnected)
            {
                _enterDeleteOnReplay = false;
                _window.AppendSystemNotice(BrainLink.SendBlockedNotice());
                return;
            }
            var p = new JObject();
            if (hasRoom) p["room_id"] = roomId;
            if (hasMembers)
            {
                var arr = new JArray();
                foreach (var m in members) arr.Add(m);
                p["members"] = arr;
            }
            // 与 open_chat / delete_history **同宽**：`0 = 全部历史`。
            // ★ 必须显式传：Python 的 `_parse_limit` 缺省是 **10**（为不传参的老调用方保留的），
            //   不传的话群窗只回放最后 10 条，而且是**静默**的 —— 界面上看不出历史被截断，
            //   玩家只会觉得"这个群怎么少了一段"。1:1 窗与删除重放都显式传了 0，这里对齐。
            // ★ 11-21 群聊分页：不再拉全量（与单聊 `HistoryPageTurns` 同一思路）★
            //   群发言是平铺的 `Utterance`，`limit` 在群侧单位是**条**（不是回合），
            //   所以这里用一个"够填满一屏"的条数；更早的由 `get_room_history` 按游标翻。
            p["limit"] = RoomHistoryPageRows;
            // 成员的**精确身份**（可选）：只有调用方在"握着真身"的那一刻取到的才收。
            // 上行后落进 Python 的 `Room.member_unit_ids`，回包再原样回来 → 喂回 `Pin`。
            if (memberUnitIds != null && memberUnitIds.Count > 0) p["member_unit_ids"] = memberUnitIds;
            ModMain.P("[ChatPresenter] 开群：" + (hasRoom ? "重开 " + roomId : "新建")
                      + " 成员=" + (hasMembers ? string.Join("、", members.ToArray()) : "（以服务端为准）")
                      + " 精确身份=" + (memberUnitIds != null ? memberUnitIds.Count : 0) + " 人");
            _window.OpenForRoom(roomId ?? "", titleHint ?? "", members);
            _roomSpeaking = "";
            _roomSpeakingId = "";
            _roomEchoSeen.Clear();
            _roomEchoPending.Clear();   // 换窗/重放：本地那几条的销账一并作废（见 ApplyRoomSay）
            _ws.SendRequest("open_room", p, resp => ApplyOpenRoom(resp), 15000);
        }

        /// <summary>`open_room` 应答 → 群窗（**唯一**的填充点）。
        /// 应答形状与 `open_chat` 一致：`{ok, error?, data?}` —— 业务字段在 data 里。
        /// 返回是否成功（日志/测试用；调用方不需要分支）。</summary>
        public bool ApplyOpenRoom(JObject resp)
        {
            if (_window == null) return false;
            // 删除模式入口的**欠账**：进门时先收下 —— 无论成败都不许漏到下一次开窗
            // （失败路径留着它，下一次成功的重放会莫名其妙进选择模式）。
            bool wantSelect = _enterDeleteOnReplay;
            _enterDeleteOnReplay = false;
            if (!_window.IsOpen)
            {
                ModMain.P("[ChatPresenter] 开群响应丢弃：窗口已关/未开");
                return false;
            }
            if (resp?["ok"]?.Value<bool>() != true)
            {
                _window.AppendSystemNotice("开群失败：" + (resp?["error"]?.ToString() ?? "未知错误"));
                ModMain.P("[ChatPresenter] 开群响应失败 ok=" + resp?["ok"] + " error=" + resp?["error"]);
                return false;
            }
            var data = resp["data"] as JObject;
            if (data == null)
            {
                _window.AppendSystemNotice("开群失败：Python 应答无 data");
                return false;
            }
            string roomId = (string)data["room_id"] ?? "";
            if (roomId.Length == 0)
            {
                _window.AppendSystemNotice("开群失败：Python 未返回 room_id");
                return false;
            }
            var members = new List<string>();
            var ma = data["members"] as JArray;
            if (ma != null) foreach (var t in ma) members.Add((string)t ?? "");
            // 成员精确身份：**回包是权威的**（新建时已经把建群那一刻的真身交给了 Python，
            // 重开时是 Python 从 `Room.member_unit_ids` 里读回来的）。
            // ★ 09-17 起**不再喂回全局登记表**（作用域泄漏，见 `PinMemberUnitIds` 处那段说明）；
            //   它是随帧携带的：`room_say.speaker_id` / `room_state.speaking_ids` /
            //   回放 `items[].speaker_id`。这里只留给新人预热立绘用。
            // 再调一次 OpenForRoom：这一次成员/群名是**权威值**（新建时占位那次还没有 id）。
            // 先切模式再灌历史：ReplaceHistory 走的是与单聊同一条渲染路径，
            // 群聊的差异只在条目 kind（peer）。
            _window.OpenForRoom(roomId, (string)data["title"] ?? "", members);
            // ★ 分帧构建（11-20）：`ReplaceHistory` 可能**当场没建完**就返回（历史条目多时），
            //   所以下面那一整段"回放之后才能做"的动作全部登记成续跑。
            //   同步路径（小历史）里 `AfterHistoryBuilt` **立刻执行** —— 两条路只有一种写法。
            // ★ 11-21 群聊分页：记"更早"游标（群发言平铺带 seq，单锚即可）
            RememberHistoryCursor(data);   // 状态告知同样放在 AfterHistoryBuilt 里（见下）
            _window.ReplaceHistory(data["items"] as JArray);
            _window.AfterHistoryBuilt(() =>
            {
            // 首屏统计基线（**本群口径**）：与单聊 open_chat 的 `stats` 同一套路，只是群里给的是
            // `roomUsage`（本群期间累计花费）。不刷的话那行会停在预制件默认文本（"None"），
            // 一直等到**第一个成员发言结束**才有第一帧 stats_update —— 用户 09-18 的截图正是如此。
            // ★ 群窗同样要告知「加载更早」状态（11-21）★
            //   单聊那边踩过"只记游标不告知 UI ⇒ 入口行永不出现"，群聊是同一条路。
            _window.SetEarlierState(_histHasMore, _histHiddenCount);
            if (data["roomUsage"] is JObject roomUsageSeed) _window.SetStats(data);
            _roomSpeaking = "";
            _roomSpeakingId = "";
            _roomEchoSeen.Clear();
            _roomEchoPending.Clear();   // 换窗/重放：本地那几条的销账一并作废（见 ApplyRoomSay）
            SetBusy(false);
            // 关窗期间缓冲的群帧（`ChatFrameTee` 收、`ChatFrameQueue` 存）：必须**重放落地之后**
            // 消费（先消费会被 `ReplaceHistory` 整个擦掉），也必须在 `SetBusy(false)` **之后**
            // （未闭合轮的重放会 `SetBusy(true)` ——"还在说"是真的，反过来写就把它抹掉了）。
            DrainRoomFrames(roomId, data["items"] as JArray);
            // ★ 构建期间暂存的直播帧按原序补画（11-20）：排在 drain 之后（这些帧更新）。
            //   群聊这条路尤其不能省 —— tee 在"窗口正显示这个群"时**不缓冲**，
            //   暂存的这批是那些帧唯一的去处。
            FlushHeldDuringBuild();
            // 群删入口流程：重放落地（每行已绑上 uid）→ 进选择模式（与单聊的
            // `OpenChatAndReplay` 回执里那段严格对称）。
            if (wantSelect) _window.EnterSelecting();
            ModMain.P("[ChatPresenter] 群窗已开：" + roomId + "（" + members.Count + " 人，历史 "
                      + ((data["items"] as JArray)?.Count ?? 0) + " 项）");
            });
            return true;
        }

        /// <summary>消费"关窗期间"缓冲的群帧（`ChatFrameTee` 收、`ChatFrameQueue` 存）。
        ///
        /// ★ 时序（两条都是硬要求）：
        ///   · 必须在 `ReplaceHistory` **之后**：重放是 `ResetContent` + 全量重投影，
        ///     先消费会被整个擦掉（"正在说的那半截"反而更没了）；
        ///   · 必须在 `SetBusy(false)` **之后**：未闭合轮的重放会 `SetBusy(true)`
        ///     （"还在说"是真的）——反过来写就把这个真状态抹掉了。
        ///
        /// ★ 去重靠 `baseline`（重放 items 的最大 seq）：**群窗的 `BubbleCommitPolicy`
        ///   两档去重（Skip / OverwriteLastRow）对群窗不可达**（群窗回放不带 turn，
        ///   见 `ChatWindow.ReplaceHistory` 的注释）——所以"同一句不出两遍"在群窗里
        ///   **只有 baseline 这一道防线**，它同时挡 NPC 定稿与**玩家回声**
        ///   （`_roomEchoSeen` 在这里刚被 `Clear()` 过，指望不上）。
        ///
        /// 单帧失败只记日志跳过（队列消费不许炸开窗流程）；`ForgetRoom` 放在最后 ——
        /// 中途抛异常时桶还在，下次开窗还能重放。</summary>
        private void DrainRoomFrames(string roomId, JArray items)
        {
            int baseline = 0;
            if (items != null)
                foreach (var it in items)
                {
                    int s = it?["seq"]?.Value<int?>() ?? 0;
                    if (s > baseline) baseline = s;
                }
            List<ChatFrame> frames;
            try { frames = ChatFrameQueue.TakeRoom(roomId, baseline); }
            catch (Exception e) { ModMain.P("[ChatPresenter] 群帧队列取帧失败：" + e.Message); return; }
            if (frames.Count == 0)
            {
                ChatFrameQueue.ForgetRoom(roomId);
                return;
            }
            foreach (var f in frames)
            {
                try
                {
                    switch (f.Event)
                    {
                        case "room_say":
                            ApplyRoomSay(f.Uid, f.Speaker, f.Text, f.SpeakerId, f.Narration, f.EchoText);
                            break;
                        case "room_state":
                            ApplyRoomState(f.Phase,
                                           (f.Speaking != null && f.Speaking.Length > 0) ? f.Speaking[0] : "",
                                           f.Note,
                                           (f.SpeakingIds != null && f.SpeakingIds.Length > 0) ? f.SpeakingIds[0] : "");
                            break;
                        case "step":
                            // 工具 / 思考折叠（09-17 统一后新纳入）：与直播同一条字段版路径。
                            // 说话人靠本轮先重演的 `room_state(speak)` 落进 `_roomSpeaker` 兜底
                            // （step(text) 的整段正文不带说话人 —— 与直播口径一致）。
                            ApplyStep(f.Turn, f.Kind, f.Name, f.Args, f.Text, f.IsError);
                            break;
                        case "text_delta":
                            ApplyDelta(f.Text, f.Part);
                            break;
                    }
                }
                catch (Exception e)
                {
                    ModMain.P("[ChatPresenter] 群帧重放单帧失败（跳过）：" + f.Event + " " + e.Message);
                }
            }
            ChatFrameQueue.ForgetRoom(roomId);
            ModMain.P("[ChatPresenter] 关窗期间的群帧已回放 " + frames.Count + " 条（room=" + roomId
                      + " 基线 seq=" + baseline + "）");
        }

        /// <summary>消费缓冲的**未闭合回合**（`ChatFrameTee` 收、`ChatFrameQueue` 存）。
        ///
        /// ★ 时序：必须在 `ReplaceHistory` **之后**（重放是 `ResetContent` + 全量重投影，
        ///   先消费会被整个擦掉 —— 与群聊 `DrainRoomFrames` 同规约）、且排在
        ///   `EnterSelecting` **之前**（重演出来的行不该带选择遮罩；与 `ApplyOpenRoom` 对称）。
        ///
        /// ★ "直播优先"的欠账分支**不消费**（并在那里 `ForgetSolo` 丢段）：那时窗口里已有
        ///   直播在飞（重开窗后新到的 delta 建了活跃泡），把旧段 append 上去会**顺序错乱**；
        ///   而段的内容要么已由直播续写（收口全文覆盖）、要么已收口（重放必含）—— 丢弃安全。
        ///
        /// ★ 判决（都在队列侧）：桶里只留"最后一个未闭合段"（`user_say` 开段、`npc_reply`
        ///   到达即清段、`step` 回合号推进即隐式清段）。这里把它整段重演：`user_say`（玩家自己
        ///   那句话）→ `step`（工具 / 思考 / 整段正文）→ `text_delta`（正文 / 思考流，已按 part
        ///   合并成"当前全文"）。逐帧走字段版 —— 与直播同一条执行路径。
        ///   段非空 ⇒ 那一轮"还在说" ⇒ `SetBusy(true)`（与群聊未闭合轮重演同款；`SetBusy`
        ///   顺带刷新 `_busySince`）；真回合若其实已死，`BusyWatchdog` 会兜底自愈。
        ///
        /// 单帧失败只记日志跳过（队列消费不许炸开窗流程）；`ForgetSolo` 放在最后 ——
        /// 中途抛异常时段还在，下次开窗还能重放。</summary>
        private void DrainSoloFrames(string npcId)
        {
            List<ChatFrame> frames;
            try { frames = ChatFrameQueue.TakeSolo(npcId); }
            catch (Exception e) { ModMain.P("[ChatPresenter] 单聊帧队列取帧失败：" + e.Message); return; }
            if (frames.Count == 0)
            {
                ChatFrameQueue.ForgetSolo(npcId);
                // 空段也要把视图钉到底：刚做完 `ReplaceHistory`（整块重建），布局还在结算 ——
                // 不钉就会停在**顶部**（历史开头），这是"重开窗视角不对"的直接成因之一。
                if (_window != null) _window.HoldScrollAtBottom();
                return;
            }
            // 最近一次"收到帧"刷新：重演的是这次会话缓冲下来的帧，自愈时钟不该从更早的时刻起算
            _lastFrameAt = Time.unscaledTime;
            int users = 0, steps = 0, texts = 0;
            foreach (var f in frames)
            {
                try
                {
                    switch (f.Event)
                    {
                        case "user_say":
                            // 玩家自己那句话（本地帧）。**必须画**：不画就出现"NPC 回了话、
                            // 我上面那句不在"（实机截图钉过的那一幕）。删除模式的可选键由
                            // 进入删除模式前的那次静默重放补绑，这里与直播行同待遇（不带键）。
                            _window.AppendUserMessage(f.Text ?? "");
                            users++;
                            break;
                        case "step":
                            ApplyStep(f.Turn, f.Kind, f.Name, f.Args, f.Text, f.IsError);
                            steps++;
                            break;
                        case "text_delta":
                            ApplyDelta(f.Text, f.Part);
                            texts++;
                            break;
                    }
                }
                catch (Exception e)
                {
                    ModMain.P("[ChatPresenter] 单聊帧重放单帧失败（跳过）：" + f.Event + " " + e.Message);
                }
            }
            ChatFrameQueue.ForgetSolo(npcId);
            // ★ 10-17：段非空 = **有帧但还没等到 `npc_reply`** = 这一轮确实没闭合（证据）。
            //   先补登记再刷新，避免"登记表不知道这个回合"时把重演中的忙标显示成空闲。
            //   ⚠ 这只是**兜底证据**，**不能替代** `Begin`：段是被第一条 step/text_delta 懒开的，
            //   而模型 ttft 有 4~15 秒 —— 那段空窗里段是空的，正是最需要忙标的时候
            //   （见 `TurnRegistry` 文件头"与 ChatFrameQueue 的分工"）。
            TurnRegistry.Begin(npcId, "未闭合回合重演");
            RefreshSoloBusy("未闭合回合的整段重演");
            // 一次画了很多行（玩家输入 + 工具/思考折叠 + 半截正文）⇒ 视图钉底并**保持几帧**：
            // 批量重建后布局要好几帧才结算，只在"内容变高"的那一帧钉（`ChatWindow.LateUpdate`）
            // 会漏掉"布局结算后位置已经不在底部"的情形。见 `ChatWindow.HoldScrollAtBottom`。
            if (_window != null) _window.HoldScrollAtBottom();
            ModMain.P("[ChatPresenter] 未闭合回合的帧已整段重演 " + frames.Count + " 条（npc=" + npcId
                      + " ｜ 玩家输入=" + users + " 工具/思考=" + steps + " 流式文本=" + texts + "）");
        }

        /// <summary>离群（群窗右上角 ✕ 在群模式下的动作）：全员复位成单聊；
        /// **群与流水都留着**，随时可以从通讯录群聊页点回来。与「解散」是两件事。</summary>
        private void CloseRoomCommand()
        {
            if (_window == null) return;
            if (!_window.IsRoomMode)
            {
                _window.AppendSystemNotice("当前不在群聊中");
                return;
            }
            ClearReplayGate("离群回单聊");   // 回到单聊外观：闸门不该跨场合留着
            string roomId = _window.CurrentRoomId;
            if (_ws != null && _ws.IsConnected)
            {
                var p = new JObject { ["room_id"] = roomId };
                // SendRequest 的实现**无条件**调用 onResponse（连"未连接"分支也调），
                // 传 null 会直接空引用 —— 必须给一个真的回调。
                _ws.SendRequest("close_room", p, resp =>
                {
                    ModMain.P("[ChatPresenter] 关群回执：" + roomId
                              + " ok=" + resp?["ok"] + " error=" + resp?["error"]);
                });
            }
            // 本地立刻回单聊外观：不等回执（关群是幂等的，回执只用于日志）
            // @ 候选随窗作废（11-21）：成员名单换了，旧候选就是上一个群的名单。
            // 退出群聊 = 无条件作废（`HideForce` 越过 owner，理由同 `OnCloseClicked`）。
            MentionPickPanel.HideForce();
            _pickCtx = new MentionPickCore.PickContext();
            _lastInputText = null;
            _lastCaret = -1;
            _window.OpenFor("");
            _window.AppendSystemNotice("已离开群聊 " + roomId + "（记录保留，可在传音簿「群聊」页点回来）");
            _roomSpeaking = "";
            _roomSpeakingId = "";
            _roomEchoSeen.Clear();
            _roomEchoPending.Clear();   // 换窗/重放：本地那几条的销账一并作废（见 ApplyRoomSay）
            SetBusy(false);
            ModMain.P("[ChatPresenter] 离群：" + roomId);
        }

        /// <summary>某个群被**解散**了（通讯录侧发起 ⇒ `dissolve_room` ⇒ 回执里回调过来）。
        ///
        /// 只有正开着**这个**群时才需要动：本地回单聊外观并明说一句。不做的话，群窗会继续
        /// 显示一个已经不存在的群，而之后的发言永远收不到回复（Python 侧 `_require` 会
        /// KeyError）—— 表现是"发出去没反应"，且没有任何提示。
        ///
        /// 判据复用 `AcceptsRoomFrame`（`room_id` 相等）——它就是"当前显示的是不是这个群"。
        /// 解散**不影响**成员记忆：那些话已经在各自 session 里了（见 §十五）。</summary>
        public void OnRoomDissolved(string roomId)
        {
            if (_window == null || string.IsNullOrEmpty(roomId)) return;
            // 群没了 ⇒ 它的帧缓冲也扔。放在早退守卫**之前**：解散的往往不是当前显示的那个群，
            // 但它的桶同样不该留着（room_id 是高水位复用的，残留帧会灌进将来的同名新群）。
            ChatFrameQueue.ForgetRoom(roomId);
            if (!_window.AcceptsRoomFrame(roomId)) return;   // 不是当前这个群 → 什么都不做
            ClearReplayGate("群解散回单聊外观");
            // @ 候选随窗作废（11-21）：成员名单换了，旧候选就是上一个群的名单。
            // 退出群聊 = 无条件作废（`HideForce` 越过 owner，理由同 `OnCloseClicked`）。
            MentionPickPanel.HideForce();
            _pickCtx = new MentionPickCore.PickContext();
            _lastInputText = null;
            _lastCaret = -1;
            _window.OpenFor("");
            _window.AppendSystemNotice("这个群聊已被解散（记录已删除；与会 NPC 仍记得聊过什么）");
            _roomSpeaking = "";
            _roomSpeakingId = "";
            _roomEchoSeen.Clear();
            _roomEchoPending.Clear();   // 换窗/重放：本地那几条的销账一并作废（见 ApplyRoomSay）
            SetBusy(false);
            ModMain.P("[ChatPresenter] 当前群已被解散，回单聊外观：" + roomId);
        }

        // ------------------------------------------------------------------
        // 群成员变更（P3 第 4 步：拉人 / 踢人）
        //
        // 两个按钮在**对话窗顶栏**（预制件 BG/InviteBtn / BG/KickBtn，压缩键左侧），
        // 但**挑选界面在传音簿**：
        //   拉人 —— 要从好友名单里选，那份名单只有传音簿有（对话窗没有也不该有），
        //           所以借它的选人模式（`PickMode.Invite`）；
        //   踢人 —— 与拉人**对称**走同一条路（`PickMode.Kick`，列表源=本群成员），
        //           两处的勾选行、立绘、计数条、两段确认全部复用现成的，不必在对话窗里
        //           另造一套"成员列表 + 选择条"（那还要与历史删除模式共用 DeleteBar 并互斥）。
        //
        // 唯一的方向差异：拉人/踢人**由对话窗发起**（静态入口进传音簿），
        // 完成后由传音簿**回调**对话窗（`NotifyMembersChangedStatic`），
        // 与"传音簿发起解散 → 回调对话窗"是同一套跨面板约定（不互相持引用）。
        // ------------------------------------------------------------------

        /// <summary>「拉人」：进传音簿的选人模式（目标=本群，列表源=好友）。</summary>
        private void OnInviteClicked()
        {
            if (_window == null) return;
            if (!_window.IsRoomMode)
            {
                _window.AppendSystemNotice("当前不在群聊中");
                return;
            }
            if (!RoomActionAllowed("拉人")) return;
            string roomId = _window.CurrentRoomId;
            var members = _window.CurrentRoomMembers;
            bool ok;
#if AB_UI
            ok = AbContactPanel.BeginInviteStatic(roomId, members, _window.CurrentRoomTitle);
#else
            ok = false;
#endif
            ModMain.P("[ChatPresenter] 拉人入口：" + roomId + " ok=" + ok);
            if (!ok)
                _window.AppendSystemNotice("拉人失败：传音簿不可用（OpenUI 未成功），稍后再试");
        }

        /// <summary>「踢人」：进传音簿的选人模式（目标=本群，列表源=本群成员）。
        /// 一个成员都没有时不必开面板 —— 空列表只会让人以为界面坏了。</summary>
        private void OnKickClicked()
        {
            if (_window == null) return;
            if (!_window.IsRoomMode)
            {
                _window.AppendSystemNotice("当前不在群聊中");
                return;
            }
            var members = _window.CurrentRoomMembers;
            if (members == null || members.Count == 0)
            {
                _window.AppendSystemNotice("这个群聊里没有可移出的成员");
                return;
            }
            if (!RoomActionAllowed("踢人")) return;
            string roomId = _window.CurrentRoomId;
            bool ok;
#if AB_UI
            ok = AbContactPanel.BeginKickStatic(roomId, members, _window.CurrentRoomTitle);
#else
            ok = false;
#endif
            ModMain.P("[ChatPresenter] 踢人入口：" + roomId + " 候选=" + members.Count + " ok=" + ok);
            if (!ok)
                _window.AppendSystemNotice("踢人失败：传音簿不可用（OpenUI 未成功），稍后再试");
        }

        /// <summary>「跳过」：这一轮玩家不置一词，让 NPC 按轮转顺序自己接下去。
        ///
        /// 与"发送"的关键差别（决定了这里的守卫与发送键**故意不同**）：
        ///   · 发送键在忙态**不禁用** —— 玩家插话是有价值的功能，第二条消息排队进来是对的；
        ///   · 跳过在忙态**必须挡** —— 一轮本来就在跑 NPC 的回合，再点一次只是把**又一整轮**
        ///     （每轮 K 个 LLM 回合）排到队列后面，而玩家在第一轮跑完之前看不到任何区别，
        ///     于是会继续点。挡的反馈复用手边这条既有规约：一行系统提示（不是静默 return）。
        ///
        /// ★ 点下去**先落一行本地系统提示**再发请求：跳过这一轮玩家**没有气泡**上屏，
        ///   若这一轮所有人又都弃权，屏幕上就是"忙碌标记转一圈、什么都没变"——
        ///   与按钮坏了长得一模一样。这行提示是"我确实按到了"的即时凭据
        ///   （不落盘、不进任何 NPC 的记忆；Python 侧那句终帧文案是另一条腿）。
        ///
        /// 为什么不复用 `SubmitRequested` 发一条空文本：见 `WsClient.SendRoomSkip` 的注释。
        /// </summary>
        private void OnSkipClicked()
        {
            if (_window == null) return;
            if (!_window.IsRoomMode)
            {
                // 按钮只在群聊可见（`SetRoomButtonsVisible`）；走到这里说明显隐逻辑没跑
                // （旧预制件 + 新 DLL 之类的组合），照样给一句人话，不静默。
                _window.AppendSystemNotice("当前不在群聊中");
                return;
            }
            var members = _window.CurrentRoomMembers;
            if (members == null || members.Count == 0)
            {
                // 踢光了成员的空群：`plan_round([])` 返回空 ⇒ 这一轮**必然**什么都发生不了。
                // 与其让玩家点了等一场空，不如当场说清楚。
                _window.AppendSystemNotice("这个群聊里一个人都没有了，跳过也没人说话");
                return;
            }
            if (!RoomActionAllowed("跳过")) return;    // 忙态 / 未连接：已经给过提示
            string roomId = _window.CurrentRoomId;
            _window.AppendSystemNotice("（你没有插话，他们继续。）");
            _ws.SendRoomSkip(roomId);
            SetBusy(true);                             // 等 room_state(phase=end) 复位
            try { ModMain.P("[ChatPresenter] 群聊跳过：" + roomId + " 成员=" + members.Count); }
            catch { }
        }

        /// <summary>群聊动作入口共用的守卫：**回合进行中**或**没连上**时拒绝并给一行提示。
        ///
        /// 为什么必须挡（拉人/踢人）：正在跑的一轮已经按当时的 `members` 定了 roster 与轮转，
        /// 中途增删会让"这一轮谁说话"与"名单显示有谁"对不上（Python 侧下一个回合才重算）。
        ///
        /// 为什么跳过也用它：判据（`_busy || HasActiveBubble`）与那条"不许改成员"的理由不同
        /// ——跳过不改任何数据，它的问题是"再排一整轮 LLM 回合、而玩家看不到区别"。
        /// 两件事共用一个**判据**是有意的：'忙' 只该有一个定义，各写一份必然漂移。
        /// 与删除历史入口同款守卫，并且**把两个分量都打出来** —— 这个守卫"哪一项为真"
        /// 没有日志时最难查（见 OnDeleteBtnClicked 的注释）。</summary>
        private bool RoomActionAllowed(string what)
        {
            if (_ws == null || !_ws.IsConnected)
            {
                _window.AppendSystemNotice(BrainLink.SendBlockedNotice());
                return false;
            }
            if (_busy || _window.HasActiveBubble)
            {
                try
                {
                    ModMain.P("[ChatPresenter] " + what + "入口被拒：忙=" + _busy
                              + " 活跃泡=" + _window.HasActiveBubble + " room=" + _window.CurrentRoomId);
                }
                catch { }
                _window.AppendSystemNotice("回合进行中，结束后再" + what);
                return false;
            }
            return true;
        }

        /// <summary>成员变更回执（传音簿侧 RPC 成功后调回来）。
        ///
        /// 做三件事：① 刷新窗口的成员表（帧路由与踢人候选都读它）；② 给新人排队立绘预热；
        /// ③ 明说结果 —— 拉人那句必须包含"看不到之前的对话"，
        /// 因为 Python 的 `add_member` 把入群位置钉在**当前末尾**（`joined_at=room.last_seq`），
        /// 新人对入群前的事一无所知。不说明的话这会被当成 bug 报上来。</summary>
        public void OnRoomMembersChanged(string roomId, List<string> members,
                                         List<string> added, List<string> removed,
                                         JObject unitIds = null)
        {
            if (_window == null || string.IsNullOrEmpty(roomId)) return;
            if (!_window.AcceptsRoomFrame(roomId)) return;    // 不是当前这个群 → 不动这个窗口
            // 名单先动（帧路由与踢人候选都读它）。身份不再在这里登记 ——
            // 拉进来的新人在**他开口那一帧**带着自己的 `speaker_id` 到达（见 PinMemberUnitIds 处说明）。
            _window.SetRoomMembers(members, added);
            int nAdd = added != null ? added.Count : 0;
            int nDel = removed != null ? removed.Count : 0;
            if (nAdd > 0)
            {
                _window.AppendSystemNotice("已把 " + string.Join("、", added.ToArray())
                    + " 拉进群聊（他看不到入群前的对话；之后的每句都会收到）");
            }
            if (nDel > 0)
            {
                _window.AppendSystemNotice("已把 " + string.Join("、", removed.ToArray())
                    + " 移出群聊（他记得之前聊过的事，但之后的群消息不再发给他）");
            }
            ModMain.P("[ChatPresenter] 群成员变更：" + roomId
                      + " +" + nAdd + " -" + nDel + "，现 " + (members != null ? members.Count : 0) + " 人");
        }

        /// <summary>★ 09-17 **已删除**原 `PinMemberUnitIds`（把群回包的 `{npc_id: unitID}`
        /// 灌进 `UnitLookup` 的全局登记表）。
        ///
        /// 删它的理由 —— **作用域泄漏**：`Room.member_unit_ids` 是**房间级**的事实，而那张
        /// 登记表是**全局**的，且**后写覆盖先写**。于是"开一个含益婉容B的群"会把单聊里登记过的
        /// 益婉容A**静默覆盖**掉，之后 A 的自身段 / 查人 / 候选人集全部挂到 B 身上 ——
        /// 正是文件头那次真机事故的形态，只是换了个后门进来。
        ///
        /// 现在身份**随帧携带**，不由 C# 再查一遍：
        ///   · `room_say`   带 `speaker_id`     → `ApplyRoomSay`   → `FinishNpcBubble(…, id)`
        ///   · `room_state` 带 `speaking_ids`   → `ApplyRoomState` → `SetRoomSpeaker(name, id)`
        ///   · 重开回放的 `items[].speaker_id`  → `ReplaceHistory` 的 `peer` 分支
        /// 三条路都直达 `RowAvatars.Apply(img, name, id)`，**群聊一行都不走按名解析**。
        /// 于是群聊既不需要、也不该往全局表里写东西。
        ///
        /// （`UnitIdRegistry` 那个全局表仍在，但它只由**两个握着真身的时刻**写：
        ///   「AI 对话」与「加好友」—— 与房间无关，见 `ChatLauncher.OpenForUnit` /
        ///   `NpcPanelAddContact.ToggleContact`。）
        ///
        /// 已知空白：成员立绘**预热**（`RoomPortraitWarmup`）仍按名解析。它错了只是白烘一张图
        /// （真正的行用精确 id 取图，缓存按 id 分桶，不会串），所以不值得为它再开一条身份通道。</summary>

        /// <summary>一条群发言广播。
        ///
        /// ★ `speaker != player` 的 `room_say` 是**定稿/替换**，不是新气泡 ——
        /// 流式期间已经用 `text_delta` 显示了半截（与单聊里 `npc_reply` 的角色完全相同）。
        /// 不照此实现的话每条发言会画两个气泡。
        ///
        /// ★ `speaker == player` 是**回声**：发送方本地已经画过，按 `uid` 去重。</summary>
        private void OnRoomSay(JObject payload)
        {
            if (_window == null) return;
            ApplyRoomSay((string)payload["uid"] ?? "",
                         (string)payload["speaker"] ?? "",
                         (string)payload["text"] ?? "",
                         (string)payload["speaker_id"] ?? "",
                         payload["narration"]?.Value<bool>() ?? false,
                         (string)payload["echo_text"] ?? "");
        }

        /// <summary>`room_say` 的**字段版** —— 直播（`OnRoomSay`）与关窗重放
        /// （`DrainRoomFrames`）共用同一条执行路径，两种来路的表现必须逐字一致。
        ///
        /// `speakerId` = 说话人的精确 unitID（Python 从 `Room.member_unit_ids` 查出）。
        /// 它只影响**取图**，不参与正文拼接（正文里的名字仍是中文）。
        ///
        /// `narration` = **旁白**（第三人称叙述行，09-22 用户报）：**不画气泡**，改画
        /// 「行动记录」行 —— 那类行不是谁说的话，里面还可能引用玩家的原话，挂谁名下都不对。
        /// 与重开窗那条路同形：`project_room_history` 把它的条目发成 `kind="tool_result"`，
        /// `ChatWindow.ReplaceHistory` 的既有分支正好也是「行动记录」折叠行。</summary>
        private void ApplyRoomSay(string uid, string speaker, string text, string speakerId = null,
                                  bool narration = false, string echoText = null)
        {
            if (_window == null || text.Length == 0) return;
            if (narration)
            {
                _window.AppendActionRow(text);
                _roomSpeaking = "";
                _roomSpeakingId = "";
                return;
            }
            if (speaker == "player")
            {
                if (uid.Length > 0 && !_roomEchoSeen.Add(uid)) return;   // 同一条已画过
                // ★ 本地那条**已经画在屏幕上了**（发送时 `AppendUserMessage`）⇒ 回帧只销账、不再画。
                //
                //   为什么不能只靠上面的 uid：本地泡是**同步**画出来的，而 uid 要等 Python 回帧
                //   才知道 —— `SendRoomMessage` 是 fire-and-forget 的 event（`WsClient` 里没有
                //   回执可拿 uid），所以"本地这条叫什么 uid"在回帧之前**根本不存在**，
                //   `_roomEchoSeen` 里永远不会有它 ⇒ 每一条群消息都会画两遍（09-17 复核实证）。
                //   这份待销账表只装"我方刚发出去、还没等到回声"的那几条，按 FIFO 对号。
                //   ★ 但"按文本相等"这个判据自 @ 功能上线后就不够用了 ★（11-21）：
                //   `RoomFlow.run_message` 现在会把 `@名字` **剥掉**再落账
                //   （寻址信息不该进模型的眼睛），而本地气泡存的是玩家输入的原文
                //   ⇒ 拿剥过的 `text` 去比永远不等 ⇒ 画两遍。所以下面优先用 `echoText`。
                // ★ 对号优先用 `echoText`（Python 回的**玩家原文**）★
                //   `text` 是剥掉 `@名字` 之后的正文，而本地那条气泡画的是玩家敲进去的原文
                //   ⇒ 拿 `text` 去比**永远不等**，同一条消息在群窗出现两遍
                //   （11-21 用户截图：`@姜萌 你在干什么` + `你在干什么`）。
                //   没有 `echo_text` 的帧（老服务端 / 无 @ 的发言，两者本来就相等）退回比 `text`。
                string key = string.IsNullOrEmpty(echoText) ? text : echoText;
                if (_roomEchoPending.Count > 0 && _roomEchoPending.Peek() == key)
                {
                    _roomEchoPending.Dequeue();
                    return;
                }
                // 再兜一道：本地那条若按 `text` 也对得上（无 @ 的普通发言）就照样销账。
                // 顺序在 `key` 之后 —— 有 `echo_text` 时以它为准，避免"原文恰好等于别人的正文"。
                if (key != text && _roomEchoPending.Count > 0 && _roomEchoPending.Peek() == text)
                {
                    _roomEchoPending.Dequeue();
                    return;
                }
                // 对不上（历史回放里的旧回声 / 换过窗）：不再留旧账，按新消息画。
                _roomEchoPending.Clear();
                _window.AppendUserMessage(text);
                return;
            }
            // 说话人拼进正文（最小档，同 ReplaceHistory 的 peer 分支）：
            // 流式那半截是裸文本，这里把名字补上，最终一致。
            // ★ 第三/四个参数（speaker / speakerId）是**逐行头像的取图键**：不带它们的话，
            //   若这条 `room_say` 走的是"新建"那一档（流式泡已被撤 / 本回合没起过流式），
            //   新行的头像会退回模板那张陈旧的脸（上一个单聊 NPC），而且完全静默。
            _window.FinishNpcBubble(speaker.Length > 0 ? speaker + "：" + text : text, false,
                                    speaker, speakerId);
            _roomSpeaking = "";
            _roomSpeakingId = "";
        }

        /// <summary>群窗状态帧。
        ///
        /// ★ `phase == "end"` 是**本轮唯一的终帧** ⇒ 复位忙碌态。
        /// 不能拿 `room_say` 当收尾信号：一轮里所有人都弃权时**一条 `room_say` 都不会有**
        /// （弃权不写流水、不广播），忙碌态就永远挂着（群窗一直显示"正在输入"）。
        /// Python 侧把它放在 `finally` 里无条件发（`docs/archive/2026-09-17-群聊编排/设计定稿.md` §6.2）。</summary>
        private void OnRoomState(JObject payload)
        {
            if (_window == null) return;
            var sp = payload["speaking"] as JArray;
            string who = (sp != null && sp.Count > 0) ? ((string)sp[0] ?? "") : "";
            // 与 `speaking` **同序同长**的精确 unitID 名单（老 Python 不发 ⇒ 空 ⇒ 回落按名）
            var si = payload["speaking_ids"] as JArray;
            string whoId = (si != null && si.Count > 0) ? ((string)si[0] ?? "") : "";
            ApplyRoomState((string)payload["phase"] ?? "", who, (string)payload["text"] ?? "", whoId);
        }

        /// <summary>`room_state` 的**字段版**（直播与关窗重放共用）。`note` = 终帧/错误帧可带的
        /// 提示文案（见各分支注释），非终帧为空。`whoId` = 说话人的精确 unitID
        /// （只影响取图；名字与 id 永远同一次写，见 `ChatWindow.SetRoomSpeaker`）。
        ///
        /// ★ **每个成员边界都关掉「内心思量」折叠**（09-17 复核补）：群聊是串行接力，
        /// `speak` / `silent` / `error` / `end` 都是"上一位的回合已经结束"的信号；而
        /// `ChatWindow.FinishThink()` 全工程**只有单聊的 `npc_reply` 那一处**调用过 ⇒
        /// 群聊里思考折叠**从来不关**：下一个成员的思考增量会并进上一位那块
        /// （实机：一块折叠显示 850 字，而 Python 侧日志显示那位**只推了 382 字** ——
        /// 其余是下一位并进去的，标题却只写着第一个人的名字）。
        /// `FinishThink()` 对"没有活着的折叠"是无害 no-op，故四个边界各调一次。
        ///
        /// ★ 群聊**点名不再预开气泡**（09-17 用户拍板，C2）：正文到了才起泡。
        ///   旧写法在 `speak` 就 `BeginNpcBubble(who + "：")`，于是"模型思考三四十秒"
        ///   期间屏幕上挂着一条空气泡；他一旦**弃权或出错**，那条泡还得等下一个人的
        ///   `speak`（或终帧）才被顺手撤掉 —— 实机表现是"她的气泡过了一会自己消失了，
        ///   然后另一个人开始说话"，看着像灵异事件。代价是思考期间没有"谁在说"，
        ///   由**带说话人的折叠标题**补上（「姜萌 · 内心思量 · 850 字」，见 `ChatWindow`）。</summary>
        private void ApplyRoomState(string phase, string who, string note, string whoId = null)
        {
            if (_window == null) return;
            // 每一帧都留痕（低频：一轮 2~6 行）。这一族帧此前**零日志**，而它恰恰是
            // "气泡为什么一直挂着 / 谁在说话 / 为什么没人开口"的唯一解释来源 ——
            // 09-17 排查「气泡过一会自己消失」时，Player.log 里连"谁被点名"都查不到，
            // 只能靠 Python 侧日志反推。
            try
            {
                ModMain.P("[ChatPresenter] 群状态 " + phase + " 说话=" + (who ?? "")
                          + " 实例#" + _id
                          + (string.IsNullOrEmpty(note) ? "" : " 文案=" + note));
            }
            catch { }
            if (phase == "speak")
            {
                _roomSpeaking = who;
                _roomSpeakingId = whoId ?? "";
                SetBusy(true, "群点名 " + who);
                // ★ 上一位的「内心思量」在此**定稿关闭**（09-17 复核补，见方法头注释）：
                //   群聊是串行接力，`speak` 一到就说明上一位的回合已经结束 —— 这是唯一
                //   可靠的成员边界。**漏掉这一句的形态**：思考折叠一直开着，下一个成员的
                //   思考增量会**并进上一位那块折叠**（字数越滚越大、两三个人的推理串在
                //   一起，标题却只写着第一个人的名字）。
                _window.FinishThink();
                // 记下说话人：群聊正文有两条路到达屏幕，其中 `step(text)`（非流式后端整段正文）
                // **不带说话人** —— 缺省为空时它建的泡会退回"模板那张陈旧的脸"（静默错脸）。
                _window.SetRoomSpeaker(who, whoId);
                // 上一个说话人留下的空气泡先撤掉（正常时序由 `silent` / `error` / `end` 收口，
                // 这里是**兜底**：那几帧丢了也不会让空气泡一条条堆在屏幕上）。
                if (_window.ActiveBubbleHasNoBody) _window.CancelActiveBubble();
                // ★ 这里**不再** BeginNpcBubble（见方法注释）：气泡改由正文那条路惰性创建 ——
                //   流式走 `ApplyDelta`（它自己会带 `_roomSpeaking + "："`），
                //   整段正文走 `ApplyStep(step/text)`（同样补名字前缀）。
            }
            else if (phase == "silent")
            {
                // 该成员**弃权**（回【沉默】/空，不写群流水 ⇒ 永远等不到 room_say 定稿）。
                // 无条件撤泡（不看有没有正文）：流式后端会把哨兵本身打进气泡
                // （屏幕上「甲：【沉默】」），那条"正文"是假的，必须撤。
                // **不复位忙碌态** —— 这一轮还在跑，终帧是 `end`。
                _window.CancelActiveBubble();
                _window.FinishThink();      // 他的回合结束了：思考折叠就此定稿（下一位另起一块）
                _roomSpeaking = "";
                _roomSpeakingId = "";
                _window.SetRoomSpeaker("");
            }
            else if (phase == "error")
            {
                // ★ 该成员这一轮**出错**（模型超时 / 内部异常），本轮跳过（09-17 新增）。
                //   与 `silent` 分开是**故意的**：弃权是模型的选择（正常结果），出错是我们的故障，
                //   混成一个 phase 会让"她不想说"和"她卡死了"在屏幕上长得一模一样。
                //   以前 Python 异常分支**一帧都不发** ⇒ 空气泡挂着、忙碌态不复位、
                //   玩家零解释（实机事故，见 room_flow._run_round 的注释）。
                if (_window.ActiveBubbleHasNoBody) _window.CancelActiveBubble();
                else if (_window.HasActiveBubble) _window.EndTurnStreaming();   // 半截正文：定稿留着
                // ★ 出错这条路**尤其**要关折叠：正常收尾时 `step(think)` 整段帧会顺手
                //   `SetFull` 并关掉它（`AppendThinkStatic`），而"超时/异常"恰恰是**没有那帧**
                //   的那条路 ⇒ 他的思考会一直挂着、并吞掉下一位的思考（实机：一块折叠里
                //   两个成员的推理串在一起，字数对不上任何一个人）。
                _window.FinishThink();
                _roomSpeaking = "";
                _roomSpeakingId = "";
                _window.SetRoomSpeaker("");
                // 人话解释（Python 只给归因短句，traceback 在 agent_loop.log）：
                // 这是玩家**唯一**的线索，绝不能省。
                string why = string.IsNullOrEmpty(note) ? "回合异常" : note;
                _window.AppendSystemNotice(who.Length > 0
                    ? who + " 这次没能开口：" + why + "，本轮跳过"
                    : "有一位成员这次没能开口：" + why + "，本轮跳过");
                // 不复位忙碌态：这一轮还在跑（与 `silent` 同规约）——终帧 `end` 负责复位。
            }
            else if (phase == "end")
            {
                // 空气泡（成员弃权/出错却没收到对应帧）不该留在屏幕上：撤掉它而不是"只清跟踪"。
                // 正常路径已被 `silent` / `error` 处理；这里兜住"那两帧丢了/旧 Python 不发"。
                if (_window.ActiveBubbleHasNoBody) _window.CancelActiveBubble();
                else if (_window.HasActiveBubble) _window.EndTurnStreaming();
                // 最后一位的思考折叠也要定稿（没有"下一个 speak"来关了，见方法头注释）
                _window.FinishThink();
                _roomSpeaking = "";
                _roomSpeakingId = "";
                _window.SetRoomSpeaker("");
                SetBusy(false);
                // ★ 终帧可以**带文案**：Python 用它回"这一轮为什么没有产出"——
                //   整轮异常（`FALLBACK_TEXT`）、群聊里说了不提供的 `/compact`、
                //   或**全员都没开口而有人出错**（09-17 起：单成员出错的解释写在
                //   `phase="error"` 那一帧上，只有"一个人都没说出来"时才汇总到终帧）。
                //   此前这句 text 被**静默丢掉**：玩家看到的是"忙碌态复位了、屏幕上什么都没有"，
                //   正是"按了没反应"的形态。与单聊 `npc_reply(error=true) → AppendSystemNotice`
                //   同一展示口径。
                if (!string.IsNullOrEmpty(note)) _window.AppendSystemNotice(note);
            }
        }

        // ------------------------------------------------------------------
        // 压缩纪要：展示与修改（10-31 用户需求）
        // ------------------------------------------------------------------

        /// <summary>Python 推来的纪要（**手动与自动压缩都会发**）。这里只负责上屏。</summary>
        private void OnCompactSummary(JObject payload)
        {
            if (_window == null || !_window.IsOpen) return;
            // 群聊回合里压出来的：事件带 room_id。单聊窗不展示别人的记忆（与"群聊不提供
            // 手动压缩"同一口径）；该纪要在 1:1 窗重开时由历史投影补上。
            string room = (string)payload["room_id"] ?? "";
            if (room.Length > 0 || _window.IsRoomMode) return;
            string npc = (string)payload["npc_id"] ?? "";
            if (npc != _window.CurrentNpcId) return;
            string text = (string)payload["text"] ?? "";
            if (text.Length == 0) return;
            bool editable = payload["editable"]?.Value<bool>() ?? false;
            _window.AppendSummaryRow((string)payload["compaction_id"] ?? "", text, editable,
                                     (string)payload["lock_reason"] ?? "");
            ModMain.P("[ChatPresenter] 压缩纪要上屏：" + text.Length + " 字，可改=" + editable
                      + "，来源=" + ((string)payload["source"] ?? "?"));
        }

        /// <summary>点了折叠行的「修改」：打开左侧编辑面板并预填正文。</summary>
        private void OnSummaryEditClicked(string compactionId, string text)
        {
            if (_window == null || !_window.IsOpen) return;
            if (_window.IsRoomMode)
            {
                _window.AppendSystemNotice("群聊里不能修改压缩纪要：那是某一个人的记忆");
                return;
            }
            if (_ws == null || !_ws.IsConnected)
            {
                _window.AppendSystemNotice(BrainLink.SendBlockedNotice());
                return;
            }
            _editCompactionId = compactionId ?? "";
            if (_editCompactionId.Length == 0)
            {
                _window.AppendSystemNotice("这条纪要缺少标识，无法修改");
                return;
            }
            if (!CompactEditPanel.Show(text))
            {
                // 面板没搭起来（宿主刚被游戏销毁重建过）：给出人话，绝不让点击"什么都不发生"
                _window.AppendSystemNotice("编辑面板不可用（详见 Player.log 的 CompactEditPanel 行）");
                return;
            }
            ModMain.P("[ChatPresenter] 打开纪要编辑面板：" + _editCompactionId);
        }

        /// <summary>左侧面板点了「发送」：**只把正文传回 Python**（不是发玩家消息、不产生回合）。
        /// 成败都留在面板上：成功收起并补一行系统提示，失败保留文本 + 红字原因。</summary>
        private void OnSummaryEditSubmit(string text)
        {
            if (_window == null) return;
            if (_editCompactionId.Length == 0)
            {
                CompactEditPanel.ShowError("没有正在编辑的纪要（请重新点「修改」）");
                return;
            }
            if (_ws == null || !_ws.IsConnected)
            {
                CompactEditPanel.ShowError("未连接 Python（服务未启动）");
                return;
            }
            JObject p = new JObject();
            p["npc_id"] = _window.CurrentNpcId;
            p["compaction_id"] = _editCompactionId;
            p["text"] = text ?? "";
            CompactEditPanel.SetSending(true);
            _ws.SendRequest("edit_compact_summary", p, resp =>
            {
                if (_window == null || !_window.IsOpen) return;   // 回执到达时窗已关：丢弃
                string err = EditRpcError(resp);
                if (err != null)
                {
                    ModMain.P("[ChatPresenter] 压缩纪要修改失败：" + err);
                    CompactEditPanel.ShowError(err);
                    return;
                }
                var data = resp["data"] as JObject;
                string body = data?["text"]?.ToString() ?? "";
                int chars = data?["chars"]?.Value<int>() ?? body.Length;
                ModMain.P("[ChatPresenter] 压缩纪要已传回 Python：" + chars + " 字");
                _window.RefreshSummaryRow(_editCompactionId, body);
                CompactEditPanel.ShowDone("已传回 Python · " + chars + " 字");
                // ★ 成功提示是"系统提示行"，不是玩家发言（用户原话「不是发送消息」）
                _window.AppendSystemNotice("压缩纪要已更新（" + chars + " 字，下次对话起生效）");
            }, 20000);
        }

        /// <summary>RPC 错误提取：两层 ok（传输层 / RPC 自身）。与 `RpcError` 同构，但兜底文案
        /// 说这一件事（`RpcError` 的兜底是"删除方案未通过校验"，用在这里会误导）。</summary>
        private static string EditRpcError(JObject resp)
        {
            if (resp == null) return "无响应";
            if (resp["ok"]?.Value<bool>() != true)
                return resp["error"]?.ToString() ?? "未知错误";
            var data = resp["data"] as JObject;
            if (data != null && data["ok"]?.Value<bool>() == false)
                return data["reason"]?.ToString() ?? "修改未能传回 Python";
            return null;
        }

        /// <summary>手动压缩结果（/compact 专用通道回推）：把「正在压缩中」提示行原位更新为
        /// 反馈文案。提示行已随开窗重放/换 NPC 重建而销毁时（Unity 已销毁对象 == null），
        /// 兜底直接补一行系统提示，反馈不丢。</summary>
        private void OnCompactResult(JObject payload)
        {
            string text = payload["text"]?.ToString() ?? "";
            bool ok = payload["ok"]?.Value<bool>() ?? false;
            float ms = _compacting ? (Time.unscaledTime - _compactStartAt) * 1000f : 0f;
            ClearCompactBusy();
            // 日志：这条路径此前**零日志**，导致"点了没反应"只能靠 Python 单侧推断
            ModMain.P("[ChatPresenter] 压缩结果：ok=" + ok + " 耗时=" + ms.ToString("F0") + "ms 反馈=" +
                      (text.Length > 80 ? text.Substring(0, 80) + "…" : text));
            if (text.Length == 0) return;
            if (_compactNotice != null)
            {
                _compactNotice.text = text;
                _compactNotice = null;
                return;
            }
            // 提示行不在（重放/换 NPC 销毁了，或系统提示模板缺失）→ 补一行兜底
            var line = _window.AppendSystemNotice(text);
            if (line == null)
                ModMain.P("[ChatPresenter] 压缩反馈渲染失败（系统提示行模板缺失？）原文=" + text);
        }

        /// <summary>压缩忙态收尾：按钮还原 + 秒表字段清零（结果到达 / 超时 / 换 NPC / 关窗共用）。</summary>
        private void ClearCompactBusy()
        {
            _compacting = false;
            _compactNoticeBase = null;
            try { if (_window != null) _window.SetCompactBusy(false); } catch { }
        }

        /// <summary>压缩秒表（Update 每帧调）：提示行刷新"已 Ns"，超时兜底恢复按钮。
        /// 为什么要有：摘要 LLM 一次可跑 100s+，静态的「正在压缩对话历史…」与卡死无法区分。</summary>
        private void CompactWatch()
        {
            if (!_compacting) return;
            float el = Time.unscaledTime - _compactStartAt;
            if (el > CompactStuckSec)
            {
                ModMain.P("[ChatPresenter] 压缩超过 " + CompactStuckSec + "s 无回音 → 恢复按钮（结果若迟到仍会补一行提示）");
                if (_compactNotice != null) { _compactNotice.text = "压缩长时间无响应（可再试一次）"; _compactNotice = null; }
                ClearCompactBusy();
                return;
            }
            int sec = (int)el;
            if (sec == _compactLastSec) return;
            _compactLastSec = sec;
            if (_compactNotice != null && _compactNoticeBase != null)
                _compactNotice.text = _compactNoticeBase + "（已 " + sec + "s，摘要调用较慢，请勿重复点击）";
        }
        private int _compactLastSec = -1;

        private void OnStepEvent(JObject payload)
        {
            string kind = (string)payload["kind"] ?? "";
            if (kind == "sys" || kind == "ctx")
            {
                // 输入侧折叠（系统组装 / 运行时上下文）：**只实时不重放** —— 历史重放不投影
                // 它们，帧缓冲也不收（`ChatFrameTee` 的白名单同口径），故留在这条直播专用路径。
                // ★ `NoteTurn` **必须排在下面那条判据之前**：回合号是气泡去重
                //   （`step(text)` vs `room_say` / `npc_reply`）的判据，跳掉它会让群聊里
                //   同一段正文成两次泡。
                _window.NoteTurn(payload["turn"]?.Value<int>() ?? -1);
                // ★ 群聊**不显示**这两类折叠（09-17 用户拍板）：多聊时每个成员各来一套
                //   「系统组装」「运行时上下文」，一屏大半是这些块，观感很差；而它们
                //   本来就不进历史回放 ⇒ 群里只留「内心思量」与「行动记录」两条过程线索。
                //   单聊保持原样（那是逐段差分的调试/审阅视图，用户明确要过）。
                if (_window.IsRoomMode) return;
                AppendInputParts(payload, kind, kind == "sys" ? "系统组装" : "运行时上下文");
                return;
            }
            // 其余 kind 都转**字段版**：直播与"关窗重放"（`DrainRoomFrames` / `DrainSoloFrames`）
            // 共用同一条执行路径 —— 两种来路的表现必须逐字一致（照 ApplyRoomSay / ApplyRoomState 的先例）。
            ApplyStep(payload["turn"]?.Value<int>() ?? -1, kind,
                      payload["name"]?.ToString() ?? "",
                      payload["args"]?.ToString() ?? "",
                      payload["text"]?.ToString() ?? "",
                      payload["isError"]?.Value<bool>() ?? false);
        }

        /// <summary>`step` 的**字段版**：直播（`OnStepEvent`）与关窗重放（`DrainRoomFrames` /
        /// `DrainSoloFrames`）共用 —— 两边表现必须逐字一致。`sys/ctx`（输入侧折叠）不进这条路：
        /// 它们是"只实时不重放"的（见 `OnStepEvent`）。</summary>
        private void ApplyStep(int turn, string kind, string name, string args, string text, bool isError)
        {
            if (_window == null) return;
            // 记回合号：npc_reply 帧不带 turn，"同回合同段正文只成泡一次"的去重判据靠它
            // （修 09-13 实机「step(text) 与 npc_reply 逐字相同 → 结果输出两遍」）
            _window.NoteTurn(turn);
            switch (kind)
            {
                case "tool_call":
                    _window.BeginTurnProcess();
                    _window.AddToolCall(name ?? "", args ?? "");
                    break;
                case "tool_result":
                    _window.AddToolResult(text ?? "", isError);
                    break;
                case "think":
                    // 思考整段（非流式后端）：独立折叠区展示，默认收起
                    _window.AppendThinkStatic(text ?? "");
                    break;
                case "text":
                    // 非流式后端整段正文：无条件渲染为最终全文（2026-09-09 实证根因——
                    // 旧 `if (!_sawDelta)` 挡板把全文跳掉：只要之前来过任意 text_delta（哪怕
                    // 只有首行），step(text) 全文就被丢弃 → UI 永久停留在流式残留首行）。
                    // 覆盖语义：有活跃流式气泡则全文覆盖，无则新建气泡，同一正文双路到达无害。
                    // ★ 群聊要**自带名字前缀**（09-17，配合"点名不再预开气泡"）：这条路的
                    //   气泡是现建的（`FinishNpcBubble` 的 CreateNew 分支不写前缀），
                    //   不补的话屏幕上会是一条没有归属的台词；单聊恒为空 ⇒ 逐字不变。
                    if (string.IsNullOrEmpty(text)) break;
                    _window.FinishStepProcess();
                    _window.FinishNpcBubble(_window.IsRoomMode && _roomSpeaking.Length > 0
                                            ? _roomSpeaking + "：" + text : text, false,
                                            _window.IsRoomMode ? _roomSpeaking : null,
                                            _window.IsRoomMode ? _roomSpeakingId : null);
                    break;
            }
        }

        /// <summary>把 sys/ctx 的 parts 数组展开成输入折叠块行。sys=全段无变更标记；ctx=仅变化段。
        /// remove（段消失）显示“（已移除）”。</summary>
        private void AppendInputParts(JObject payload, string kind, string title)
        {
            int turn = payload["turn"]?.Value<int>() ?? 0;
            var arr = payload["parts"] as JArray;
            if (arr == null) return;
            foreach (var p in arr)
            {
                if (!(p is JObject o)) continue;
                string label = o["label"]?.ToString() ?? o["name"]?.ToString() ?? "?";
                string text = o["text"]?.ToString() ?? "";
                string op = o["op"]?.ToString() ?? "set";
                bool changed = kind == "ctx" && op == "set";
                if (kind == "ctx" && op == "remove") text = "（已移除）";
                _window.AppendInputPart(turn, kind, title, label, text, changed);
            }
        }

        private void OnDeltaEvent(JObject payload)
        {
            ApplyDelta(payload["text"]?.ToString() ?? "",
                       payload["part"]?.ToString() ?? "body");
        }

        /// <summary>增量正文的**字段版**：直播（`OnDeltaEvent`）与关窗重放
        /// （`DrainRoomFrames`）共用 —— 两边表现必须逐字一致。</summary>
        private void ApplyDelta(string tok, string part)
        {
            if (_window == null || tok.Length == 0) return;

            // 分通道：think 进「内心思量」折叠区（Header 实时涨字数）；body 进正文气泡
            if (part == "think")
            {
                _window.AppendThinkDelta(tok);
                return;
            }

            // ：不再在 body delta 到达时立即 FinishThink（旧逻辑每 token 关一次
            // think 折叠 → think 流后续内容会新建第二条折叠，正文后出现重复 think）。
            // think 折叠的生命周期改为由回合收口统一关闭（OnReplyEvent），期间所有 think
            // delta 累积进同一个「内心思量」组。
            _window.FinishStepProcess();
            // 只在还没有活跃流式气泡时开新泡；BeginNpcBubble 是无条件新建，
            // 每个 delta 都调会把流式拆成一堆单 token 小气泡（实机截图实证）
            // 群聊：补开时要带上说话人。**不是**因为帧会乱序 —— Python 侧
            // `await _emit_state(phase="speak")` 在 `_one_speaker` **之前**（`room_flow.py:_run_round`），
            // 两条帧都是同一条连接上的 await 发送，C# 又把所有 event 塞进同一条 FIFO 队列
            // （`WsClient` → `MainThreadDispatcher`），所以 speak **必然**先于该发言者的 delta。
            // 真正需要兜底的是"这个窗口自己把说话人弄丢了"：
            //   ① 窗口**关着**的时候 speak 帧到达 → 被 `!IsOpen` 守卫丢掉，之后重开窗只剩 delta；
            //   ② 中途重开（切去单聊再切回）→ `ClearRoom()` 清空 `_roomSpeaking`，
            //      而当前发言者的 speak 帧不会再补发；
            //   ③ `ApplyOpenRoom` 在**回执**到达时又清一次，而重开一个已存在的群时
            //      `CurrentRoomId` 是同步设上的（玩家能立刻发言，回合先跑、回执后到）；
            //   ④ `phase="end"` 会清空 → 之后若有迟到/积压的 delta，下一句也没归属。
            // 不补名字的话这个气泡会没有归属。
            // ★ 第三个参数（speaker）同时是**逐行头像的取图键**：不传的话这一行的头像会退回
            //   模板预填的那张（= 上一个单聊 NPC 的脸），而群聊里这正是**静默画错人**。
            //   说话人确实还不知道时传空串：`SpeakerOf` 在群聊里会把它变成"这一行不画脸"
            //   （宁可没有头像，也不画一张别人的）。
            // 注：上面 ①②③④ 四种情形都是"窗口把说话人弄丢了"，与帧顺序无关（见该段注释）。
            if (!_window.HasActiveBubble)
                _window.BeginNpcBubble(_window.IsRoomMode && _roomSpeaking.Length > 0
                                       ? _roomSpeaking + "：" : "",
                                       false,
                                       _window.IsRoomMode ? _roomSpeaking : null,
                                       _window.IsRoomMode ? _roomSpeakingId : null);
            _window.AppendDelta(tok);
        }

        private void OnReplyEvent(JObject payload)
        {
            // 解析必须全部在 try 之内（忙标卡死的真根因）
            //   Python 玩家消息回合固定发 `error: null`（`is_fallback or None`），而
            //   `payload["error"]?.Value<bool>()` 在**键存在、值为 JSON null** 时 `?.` **不会短路**
            //   —— `JValue.Value<bool>()` 对 null 值抛
            //   `InvalidCastException: Null object cannot be converted to a value type`。
            //   这行原先写在 try 之外 → 异常在进入 try 前抛出 → **`finally` 根本不执行** →
            //   `SetBusy(false)` 被跳过 → `_busy` 永久为真 → 删除/压缩入口永远提示"回合进行中"，
            //   且只有重启能恢复。气泡照常显示，因为正文走的是 `step(text)` 那条不碰这两个布尔的路径，
            //   所以现象极具迷惑性："对话明明完了"。
            //   两道保险：① 取值换成 Null 安全的 BoolOf；② 解析本身也挪进 try，将来任何解析异常
            //   都不会再绕过 finally。
            try
            {
                bool isError = BoolOf(payload, "error");
                bool initiative = BoolOf(payload, "initiative");
                string text = payload["text"]?.ToString() ?? "";

                // 回合收口：think 折叠统一在此定稿关闭（body delta 不再逐个中断 think，见 OnDeltaEvent）
                _window.FinishThink();
                _window.FinishStepProcess();
                if (isError)
                {
                    // 失败兜底帧：不留半截气泡，推系统提示（友好文案只进 event，账本不留）
                    if (_window.HasActiveBubble) _window.CancelActiveBubble();
                    _window.AppendSystemNotice(text.Length > 0 ? text : "（NPC 没有回应）");
                }
                else
                {
                    _window.FinishNpcBubble(text, initiative);
                }
            }
            finally
            {
                // 收口帧 = "这一回合没在飞了"的唯一权威信号
                //   忙标与「活跃流式泡」是删除/压缩入口守卫的两个分量（`_busy || HasActiveBubble`）。
                //   原先只复位忙标；活跃泡标记留在 `FinishNpcBubble` 内部清 —— 渲染一旦半途抛异常，
                //   标记就永久泄漏，表现为「对话明明完了，删除模式却一直说回合进行中」。
                //   两者同源（都表示"回合在飞"），收口时必须一起落。见 ChatWindow.EndTurnStreaming。
                try { _window.EndTurnStreaming(); } catch { }
                // ★ 10-17：单聊走**登记表刷新** —— `TurnRegistry.End` 已在 `ContactDuty` 销过账
                //   （`UiEvent` 是多播，常驻职责先于本实例收到），所以这里读到 false，忙标照灭。
                //   群聊到不了本行（`AcceptsRoomFrame` 要求 `room_id` 非空，而 `npc_reply` 没有
                //   room_id ⇒ 在 `OnUiEvent` 的群窗准入就被丢了）；兜底仍保留直写，
                //   以防将来有人放开那条准入而使群聊忙标失去清点。
                if (_window != null && _window.IsRoomMode) SetBusy(false, "回合收口");
                else RefreshSoloBusy("回合收口");
                ModMain.P("[ChatPresenter] 收口复位完成 实例#" + _id
                          + " 忙(后)=" + _busy + " 活跃泡=" + (_window != null && _window.HasActiveBubble));

                // 收口 = 「这一回合没在飞了」的权威信号，也是**历史重放欠账该还的时刻**
                //   位置有意放在 EndTurnStreaming + SetBusy(false) **之后**：
                //   那一刻按定义没有东西在飞，`ReplaceHistory` 的 ResetContent 冲不掉任何直播内容。
                //   还账走同一条 `OpenChatAndReplay`（重新请求 → 拿到含本回合的最新投影），
                //   不新增路径、不套用旧响应（旧快照不含刚收口的这一回合，套用等于把回复擦掉）。
                //   若这次又撞上直播（下一回合已经开始），它会重新记上欠账，等再下一次收口。
                if (_wantReplay)
                {
                    _wantReplay = false;
                    ModMain.P("[ChatPresenter] 回合收口 → 补做挂起的历史回放 实例#" + _id);
                    try { OpenChatAndReplay(); }
                    catch (Exception e) { ModMain.P("[ChatPresenter] 补做历史回放异常: " + e.Message); }
                }
            }
        }

        // ------------------------------------------------------------------
        // 开窗激活 + 历史回放（C#→Python open_chat：get or create 激活活体并返回历史投影）
        // ------------------------------------------------------------------

        /// <summary>连接恢复后调用：把当前 NPC 的会话重新激活。
        ///
        /// 断线期间 `OpenChatAndReplay` 发出的 `open_chat` 会被 `WsClient.Send`
        /// 静默丢弃，而重连（`WsClient.Connected`）原先只被 ModMain 用来重拉 initiative 参数——
        /// 于是"连接回来了但会话没激活"，玩家接着发消息会落到一个未激活的 agent 上。
        /// 由 BrainLink 在「重新变成 Ready」的那一刻调用。</summary>
        public void ReopenAfterReconnect()
        {
            if (_window == null || _ws == null || !_ws.IsConnected) return;
            if (string.IsNullOrEmpty(_window.CurrentNpcId)) return;
            ModMain.P("[ChatPresenter] 连接已恢复 → 重新激活会话 npc=" + _window.CurrentNpcId);
            OpenChatAndReplay();
        }

        /// <summary>开窗/换 NPC 时调：发 open_chat 激活该 NPC 的 agent（磁盘账本自动 resume）
        /// 并用响应回放历史（响应形状与旧 get_history 完全一致，回放代码不变）。</summary>
        private void OpenChatAndReplay()
        {
            if (_window == null || _ws == null)
            {
                // 这 8 个提前返回原先**一个日志都没有**，所以这次故障在 C# 侧零痕迹，
                // 只能靠 Python 侧那句 `open_chat：...历史回放 34 项` 反推"请求发了、被丢了"。
                // 这份代码自己立的规矩（ChatWindow.cs「不许静默失败」）在本函数内部从没执行过。
                ModMain.P("[ChatPresenter] 历史回放放弃：窗口或 WsClient 为空 ｜ 窗口=" + (_window != null)
                          + " 客户端=" + (_ws != null));
                return;
            }
            string npc = _window.CurrentNpcId;
            if (string.IsNullOrEmpty(npc))
            {
                ModMain.P("[ChatPresenter] 历史回放放弃：当前 NPC 名为空");
                return;
            }
            // 直播优先：**不丢弃，记欠账**。冲突解除的权威信号 = 回合收口的 finally
            //（见 OnReplyEvent 里那句「收口帧 = 这一回合没在飞了的唯一权威信号」）。
            if (_busy || _window.HasActiveBubble)
            {
                _wantReplay = true;
                ModMain.P("[ChatPresenter] 历史回放让位于直播 → 记欠账，回合收口后补 ｜ npc=" + npc
                          + " 忙=" + _busy + " 活跃泡=" + _window.HasActiveBubble);
                return;
            }

            // 真正发出去了 = 这份欠账已有人负责（回调若再撞上直播会重新记上）
            _wantReplay = false;
            _replayEpochRetries = 0;
            SendOpenChatReplay(npc);
        }

        /// <summary>用户点了「加载更早」/ 滚到顶（11-21）：按游标请求上一页。
        ///
        /// 单聊走 `get_history`、群聊走 `get_room_history` —— 两条路的游标**单位不同**：
        ///   · 单聊 `limit` = **回合**数，游标 = `(before_seq, before_turn)` 双锚；
        ///   · 群聊 `limit` = **条**数，游标 = `before_seq` 单锚（群发言平铺、每条都有 seq）。
        /// 详见 `project_ui_history` / `project_room_history` 的文件头。
        /// </summary>
        private void OnEarlierRequested()
        {
            try
            {
                if (_ws == null || !_ws.IsConnected)
                {
                    ModMain.P("[ChatPresenter] 加载更早放弃：未连接");
                    SetEarlierStateAfterFailure("未连接");
                    return;
                }
                if (_window == null || !_window.IsOpen)
                {
                    ModMain.P("[ChatPresenter] 加载更早放弃：窗口已关");
                    return;
                }
                if (!_histHasMore)
                {
                    // 已经到头（正常情况下入口行已经被撤掉，走到这里说明状态没同步）
                    _window.SetEarlierState(false, 0);
                    return;
                }
                if (_histEarlierPending) return;      // 防连点
                if (_window.IsBuildingHistory)
                {
                    ModMain.P("[ChatPresenter] 加载更早让位：首屏分帧构建还没收尾");
                    SetEarlierStateAfterFailure("首屏还在构建");
                    return;
                }

                string npc = _window.CurrentNpcId;
                string room = _window.CurrentRoomId;
                bool isRoom = !string.IsNullOrEmpty(room) || string.IsNullOrEmpty(npc);

                JObject p = new JObject();
                if (isRoom)
                {
                    if (string.IsNullOrEmpty(room))
                    {
                        ModMain.P("[ChatPresenter] 加载更早放弃：既不是单聊也没有群 id");
                        SetEarlierStateAfterFailure("没有会话 id");
                        return;
                    }
                    p["room_id"] = room;
                    p["limit"] = RoomHistoryPageRows;
                    if (_histBeforeSeq > 0) p["before_seq"] = _histBeforeSeq;
                }
                else
                {
                    p["npc_id"] = npc;
                    p["limit"] = HistoryPageTurns;
                    if (_histBeforeSeq > 0) p["before_seq"] = _histBeforeSeq;
                    if (_histBeforeTurn > 0) p["before_turn"] = _histBeforeTurn;
                }

                _histEarlierPending = true;
                string method = isRoom ? "get_room_history" : "get_history";
                ModMain.P("[ChatPresenter] 加载更早：发 " + method + " ｜ "
                          + (isRoom ? "群=" + room : "npc=" + npc)
                          + " 游标=(" + _histBeforeSeq + "," + _histBeforeTurn + ")");

                _ws.SendRequest(method, p, resp =>
                {
                    _histEarlierPending = false;
                    // 窗口/会话已经换了 ⇒ 这一批是给上一个会话的，**必须丢**
                    // （插进去就是"张家的历史出现在李家窗里"，且零报错）
                    if (_window == null || !_window.IsOpen)
                    {
                        ModMain.P("[ChatPresenter] 加载更早响应丢弃：窗口已关");
                        return;
                    }
                    string nowNpc = _window.CurrentNpcId;
                    string nowRoom = _window.CurrentRoomId;
                    if (isRoom ? (nowRoom != room) : (nowNpc != npc))
                    {
                        ModMain.P("[ChatPresenter] 加载更早响应丢弃：会话已切 ｜ 响应="
                                  + (isRoom ? nowRoom : nowNpc));
                        return;
                    }
                    if (resp["ok"]?.Value<bool>() != true)
                    {
                        ModMain.P("[ChatPresenter] 加载更早失败：" + resp["error"]);
                        SetEarlierStateAfterFailure("回执失败");
                        return;
                    }
                    var data = resp["data"] as JObject;
                    var items = data != null ? data["items"] as JArray : null;
                    if (items == null || items.Count == 0)
                    {
                        // 到头了：撤掉入口（不然点了没反应，比没有更糟）
                        RememberHistoryCursor(data);
                        _window.SetEarlierState(false, 0);
                        ModMain.P("[ChatPresenter] 加载更早：没有更多了（入口已撤）");
                        return;
                    }
                    int built = _window.InsertEarlier(items);
                    RememberHistoryCursor(data);
                    _window.SetEarlierState(_histHasMore, _histHiddenCount);
                    ModMain.P("[ChatPresenter] 加载更早落地：本批 " + items.Count + " 条，建出 "
                              + built + " 行，还有更早=" + _histHasMore);
                }, 15000);
            }
            catch (Exception e)
            {
                _histEarlierPending = false;
                ModMain.P("[ChatPresenter] 加载更早异常：" + e.Message);
            }
        }

        /// <summary>请求失败/放弃时把 UI 的"正在加载"放掉（保留入口，让玩家能重试）。</summary>
        private void SetEarlierStateAfterFailure(string why)
        {
            try
            {
                if (_window != null && _window.IsOpen)
                    _window.SetEarlierState(_histHasMore, _histHiddenCount);
                ModMain.P("[ChatPresenter] 加载更早未成（" + why + "），入口保留可重试");
            }
            catch { }
        }

        /// <summary>从 `open_chat` / `get_history` / `open_room` 回执里记住"更早"游标（11-21）。</summary>
        private void RememberHistoryCursor(JObject data)
        {
            try
            {
                long seq = 0;
                int turn = 0;
                if (data != null)
                {
                    var s = data["next_before_seq"];
                    if (s != null) long.TryParse(s.ToString(), out seq);
                    var t = data["next_before_turn"];
                    if (t != null) int.TryParse(t.ToString(), out turn);
                }
                // ★ 两个都为 0 = 到顶 ★
                //   判据必须"两个都 0"：单聊里 `next_before_turn` 可能为 0 而 seq 非 0
                //   （无 seq 回合的账本反向情形），只看一个会误判"没更多了"。
                _histBeforeSeq = seq;
                _histBeforeTurn = turn;
                _histHasMore = seq > 0 || turn > 0;
            }
            catch (Exception e)
            {
                _histBeforeSeq = 0;
                _histBeforeTurn = 0;
                _histHasMore = false;
                ModMain.P("[ChatPresenter] 解析历史游标失败（按没有更早的处理）：" + e.Message);
            }
        }

        /// <summary>发出 `open_chat` 并在回执里落地"历史回放 + 整轮重演"。
        ///
        /// 与 `OpenChatAndReplay` 分开的理由：**回执里可能自己要再发一次**（段代数比对，
        /// 见下），而入口那半边带着 `_wantReplay` / 忙态守卫，递归回去会重复那套判断。
        ///
        /// ★ 渲染闸在**请求发出之前**置位：回执到达前的帧由队列负责，直播不画（见 `_replayGate`）。</summary>
        private void SendOpenChatReplay(string npc)
        {
            // ★ 发不出去就**绝不能置位**：闸门是靠回执落位的，请求没发出去就永远没有回执 ⇒
            //   这个窗口会**永久静默**（帧只进队列、直播不画），比"历史空着"严重得多。
            //   断线期开窗正好走这条：连接恢复时 `BrainLink` → `ReopenAfterReconnect` 会重走一遍。
            if (_ws == null || !_ws.IsConnected)
            {
                ClearReplayGate("未连接（请求未发）");
                ModMain.P("[ChatPresenter] 历史回放放弃：未连接 ｜ npc=" + npc
                          + " 客户端=" + (_ws != null));
                return;
            }
            ArmReplayGate(npc);
            int epoch0 = ChatFrameQueue.SoloEpoch(npc);   // 段闭合代数（请求前快照，回执里比对）

            JObject p = new JObject();
            p["npc_id"] = npc;
            // ★ 11-21：只取最近 N 个回合（分页首屏），不再拉全量 ★
            //   用户实测「消息一多就很卡」，并指出根因不是渲染而是**每次都把全部历史传给客户端**。
            //   真机数据（姜萌账本 43 回合 / 169 条 / 42.1KB）：
            //     limit=0 → 169 条 42.1KB；limit=20 → 69 条 13.5KB（约 1/3）
            //   更早的历史由 `get_history` + 游标按需翻（见 `RequestEarlierHistory`）。
            //   ⚠ 单位是**回合**不是条：一回合可能产出 1~20 条（工具调用会产出很多行）。
            p["limit"] = HistoryPageTurns;
            // ★ 精确身份随开窗上行：Python 拿它给每个工具调用注入 `initiator_id`
            //   （**模型看不到那个键**），C# 落刀时就再也不必按名猜"发起方是谁"。
            //   来源 = `UnitIdRegistry` —— 它是**打开对话 UI / 加好友**那一刻登记下的，
            //   而本方法正是"打开对话 UI"这条路的后续，所以这里几乎总能查到。
            //   查不到就不发这个键（回落按名，与本功能上线前逐字一致）。
            string npcUid = "";
            try { npcUid = UnitIdRegistry.IdOf(npc); } catch { }
            if (!string.IsNullOrEmpty(npcUid)) p["unit_id"] = npcUid;

            _ws.SendRequest("open_chat", p, resp =>
            {
                // 所有校验都在主线程回调里做：窗口已关/对象已切/直播已起
                if (_window == null || !_window.IsOpen)
                {
                    ClearReplayGate("窗口已关");
                    ModMain.P("[ChatPresenter] 历史回放响应丢弃：窗口已关 ｜ npc=" + npc
                              + " 窗口=" + (_window != null) + " 开=" + (_window != null && _window.IsOpen));
                    return;
                }
                if (resp["ok"]?.Value<bool>() != true)
                {
                    ClearReplayGate("回执失败");
                    ModMain.P("[ChatPresenter] 历史回放响应失败 ｜ npc=" + npc
                              + " ok=" + resp["ok"] + " error=" + resp["error"]);
                    return;
                }
                var data = resp["data"] as JObject;
                if (data == null)
                {
                    ClearReplayGate("回执无 data");
                    ModMain.P("[ChatPresenter] 历史回放响应无 data ｜ npc=" + npc);
                    return;
                }
                if (data["npc_id"]?.ToString() != _window.CurrentNpcId)
                {
                    ClearReplayGate("NPC 已切");
                    ModMain.P("[ChatPresenter] 历史回放响应丢弃：NPC 已切 ｜ 响应=" + data["npc_id"]
                              + " 当前=" + _window.CurrentNpcId);
                    return;
                }
                if (_busy || _window.HasActiveBubble)
                {
                    // 就是这一次造成了「主动开口开窗后没历史」（现场）
                    //   响应比首 token 晚了 → 让位给直播，但**记欠账**而不是丢弃。
                    // 同时丢掉单聊帧缓冲的那一段（09-17 统一）：它要么是"正在进行中的这一轮"
                    //   的前半截（已由重开窗后的 delta 续写、收口时全文覆盖），要么是已收口
                    //   回合的残留（重放必含）。留着 = 下一次"安静重放"会把幽灵泡演出来；
                    //   两害相权取其丢（见 `ChatFrameQueue` 文件头"消费侧"）。
                    ClearReplayGate("回执到达时已在直播");
                    ChatFrameQueue.ForgetSolo(npc);
                    _wantReplay = true;
                    ModMain.P("[ChatPresenter] 历史回放响应到达时已在直播中 → 记欠账，回合收口后补 ｜ npc="
                              + npc + " 忙=" + _busy + " 活跃泡=" + _window.HasActiveBubble);
                    return;
                }

                // ★ 快照过期兜底：`open_chat` 回的是**请求时刻**的账本 —— 若某一轮恰好在
                //   "请求发出 ~ 回执到达"之间收口，那份快照里没有它，而段又被 `npc_reply`
                //   刚清掉 ⇒ 两边都够不着（重开窗凭空缺一整轮，且**没有任何日志痕迹**）。
                //   判据 = 段闭合代数变了。补一次请求（上限 2 次：代数再变说明又收口了，
                //   但那时段里必然已经有新回合的帧，照常重演即可，不会无限套下去）。
                int epochNow = ChatFrameQueue.SoloEpoch(npc);
                if (epochNow != epoch0 && _replayEpochRetries < 2)
                {
                    _replayEpochRetries++;
                    ModMain.P("[ChatPresenter] 开窗期间有回合收口（段代数 " + epoch0 + "→" + epochNow
                              + "，第 " + _replayEpochRetries + " 次）→ 补一次历史回放 ｜ npc=" + npc);
                    SendOpenChatReplay(npc);
                    return;
                }

                var items = data["items"] as JArray;
                // ★ 11-21 分页：记下"更早"游标，供 `RequestEarlierHistory` 翻页 ★
                RememberHistoryCursor(data);
                // ★ 必须**马上把状态告知窗口**，否则「加载更早」入口行永远不会出现 ★
                //   踩过（11-21 实机）：`RememberHistoryCursor` 只写了 Presenter 自己的
                //   `_histHasMore`，而 `ChatWindow._histHasMore` 从来没被设置 ⇒
                //   窗口里 `RefreshMoreRow()` 看到的永远是 false ⇒ 连一行都不建。
                //   症状是"日志显示 还有更早=True，但界面上什么都没有"。
                //   调用点必须在 `ReplaceHistory` **之后**：那之前的 Content 会被 ResetContent 清空。
                //   所以这里先记状态，真正的 SetEarlierState 放到 AfterHistoryBuilt 里。
                ModMain.P("[ChatPresenter] 历史回放落地 ｜ npc=" + npc + " 条数=" + (items != null ? items.Count : 0)
                          + " 带统计=" + (data["stats"] != null)
                          + " 更早游标=(" + _histBeforeSeq + "," + _histBeforeTurn + ")"
                          + " 还有更早=" + _histHasMore);
                _window.ReplaceHistory(items ?? new JArray());
                // ★ 分帧构建（11-20）：条目多时 `ReplaceHistory` 会**当场没建完**就返回，
                //   所以下面这一整段"回放之后才能做"的动作登记成续跑。
                //   小历史（同步路径）里 `AfterHistoryBuilt` **立刻执行** ⇒ 行为与改动前一致。
                _window.AfterHistoryBuilt(() =>
                {
                // ★ 分页入口行：必须在这里（历史建完之后）才告知窗口 ★
                //   放前面会被 `ReplaceHistory` 里的 `ResetContent` 清掉（连同 `_moreRow` 引用作废）。
                _window.SetEarlierState(_histHasMore, _histHiddenCount);
                // 首屏 token/时延基线（open_chat 响应的 stats 与 stats_update 同构），开窗即有数
                var stats = data["stats"] as JObject;
                if (stats != null) _window.SetStats(stats);
                // 关窗 / 开别人窗期间缓冲的单聊帧（`ChatFrameTee` 收、`ChatFrameQueue` 存）：
                // 必须**重放落地之后**消费（先消费会被 `ReplaceHistory` 整个擦掉），且在
                // 删除模式入口**之前**（重演出来的行不该带选择遮罩；与 `ApplyOpenRoom` 对称）。
                DrainSoloFrames(npc);
                // 消费完才落位：之后到达的帧照常走直播（闸门期间它们都在队列里，一条不丢）
                ClearReplayGate("重放已落地");
                // ★ 构建期间暂存的直播帧按原序补画（11-20）：必须排在 drain 之后 ——
                //   这些帧比队列里那些**更新**（它们在构建期间才到），后画才是对的次序。
                FlushHeldDuringBuild();
                // 删除模式入口流程：重放落地（每行已绑定回合号）→ 进选择模式
                if (_enterDeleteOnReplay)
                {
                    _enterDeleteOnReplay = false;
                    _window.EnterSelecting();
                }
                });
            }, 10000);
        }

        // ------------------------------------------------------------------
        // 删除模式（微信式多选）：入口流程 + preview/delete 两段 RPC
        // 语义（对齐 Python history_ops）：删除以**完整回合**为单位——C# 只发 turn 编号，
        // seq 跨度由 Python 从账本换算（绝不信任裸 seq）；preview_delete_history 预检
        // 并给确认摘要（含「将同时删除折叠记忆」），delete_history 执行后**直接回新历史**
        // （C# 一次往返完成重放，不再二次 open_chat）。完整性与配对由 Python
        // plan_delete fail-closed 兜底（模拟新目录不平衡 → 整体拒绝，账本零写入）。
        // 响应帧双层语义：传输层 ok=false + error（压缩中/回合中/未激活等异常）；
        // 传输层 ok=true + data.ok=false + reason（配对 fail-closed）。
        // ------------------------------------------------------------------

        /// <summary>标题栏 🗑：选择模式中=退出；否则空闲才进（忙时拒绝）。进入前先静默重放一次，
        /// 让刚产生的直播行也绑上**选中键**（单聊=回合号、群聊=发言 uid，否则它们不可选）。
        ///
        /// **两个场合的入口不同**（`CurrentNpcId` 在群聊里刻意留空，所以不能拿它当第一道判据）：
        ///   · 单聊 → `OpenChatAndReplay`（open_chat 回执里补 EnterSelecting）；
        ///   · 群聊 → `OpenRoomById(当前房间)`（open_room 回执里补 EnterSelecting），
        ///     并要求这一轮群聊已经说完 —— 一轮进行中时 Python 会整体拒绝删除（守卫在
        ///     `room_delete` RPC 里），与其让玩家点了看到报错，不如在入口就挡住。</summary>
        private void OnDeleteBtnClicked()
        {
            if (_window == null || _ws == null) return;
            if (_window.Selecting)
            {
                _window.ExitSelecting();
                return;
            }
            if (_window.IsRoomMode)
            {
                string rid = _window.CurrentRoomId;
                if (string.IsNullOrEmpty(rid)) return;
                if (_busy || _window.HasActiveBubble || _roomSpeaking.Length > 0)
                {
                    try
                    {
                        ModMain.P("[ChatPresenter] 群删除入口被拒：忙=" + _busy
                                  + " 活跃泡=" + _window.HasActiveBubble
                                  + " 正在说话=" + _roomSpeaking + " room=" + rid);
                    }
                    catch { }
                    _window.AppendSystemNotice("这一轮还没说完，结束后再进入删除模式");
                    return;
                }
                _enterDeleteOnReplay = true;
                // 带上当前成员表：`OpenForRoom` 会用它设 `CurrentRoomMembers`，传 null 会让成员
                // 列表在回执到达前**空一下**（那一刻点拉人/踢人会看到空名单）。这些人都已在群里，
                // Python 侧 `add_member` 幂等返回 False，不会有副作用。
                OpenRoomById(rid, _window.CurrentRoomMembers, _window.CurrentRoomTitle);
                return;
            }
            if (string.IsNullOrEmpty(_window.CurrentNpcId)) return;
            if (_busy || _window.HasActiveBubble)
            {
                // 留证据：这个守卫此前**哪一项为真完全没日志**，"对话都完了为什么还说回合进行中"
                // 只能靠推理。两个分量分开打，一眼能看出是忙标没落还是活跃泡标记泄漏。
                try
                {
                    ModMain.P("[ChatPresenter] 删除入口被拒：忙=" + _busy
                              + " 活跃泡=" + _window.HasActiveBubble + " npc=" + _window.CurrentNpcId);
                }
                catch { }
                _window.AppendSystemNotice("回合进行中，结束后再进入删除模式");
                return;
            }
            _enterDeleteOnReplay = true;
            OpenChatAndReplay();
        }

        private int _previewReq;   // preview 请求序号：回执落地前选择集变了就丢弃（防过期摘要误导）

        /// <summary>确认按钮第一击武装时取弹窗数据：preview_delete_history → 摘要写计数位；
        /// 预检失败（配对不平衡/回合不存在）→ 解除武装 + 系统提示原因</summary>
        private void OnDeletePreviewRequested(List<int> turns)
        {
            if (_window == null || _ws == null || turns == null || turns.Count == 0) return;
            if (string.IsNullOrEmpty(_window.CurrentNpcId)) return;
            int req = ++_previewReq;
            JObject p = new JObject();
            p["npc_id"] = _window.CurrentNpcId;
            p["turns"] = ToJArray(turns);
            _ws.SendRequest("preview_delete_history", p, resp =>
            {
                if (_window == null || !_window.IsOpen || !_window.Selecting) return;   // 已退出模式：回执作废
                if (req != _previewReq) return;                                          // 期间选择集已变：过期回执
                string err = RpcError(resp);
                if (err != null)
                {
                    _window.ApplyDeletePreview(false, null);
                    _window.AppendSystemNotice("无法删除：" + err);
                    return;
                }
                _window.ApplyDeletePreview(true, BuildPreviewSummary(resp["data"]?["preview"] as JObject));
            }, 10000);
        }

        /// <summary>两段确认后执行：delete_history（载荷=回合号）→ 成功=退出模式 + 用响应里的
        /// 新历史直接重放（一次往返）+ 提示；失败=提示并留在选择模式</summary>
        private void OnDeleteConfirmed(List<int> turns)
        {
            if (_window == null || _ws == null || turns == null || turns.Count == 0) return;
            if (string.IsNullOrEmpty(_window.CurrentNpcId)) return;
            JObject p = new JObject();
            p["npc_id"] = _window.CurrentNpcId;
            p["turns"] = ToJArray(turns);
            p["reason"] = "user";
            // ★ 11-21：与 open_chat **同宽**（都是 `HistoryPageTurns` 回合）★
            //   不能传 0（全部）—— 那会让"删完一次"把全部历史重新灌一遍，正是
            //   "每次拿全部 ⇒ 卡"那条老路。与开窗保持一致，更早的仍走「加载更早」翻。
            //   ⚠ 与 open_chat 的页数**必须同一个常量**：两边不一致会表现为
            //     "删一次之后历史条数变了"（用户看着像"删错了"）。
            p["limit"] = HistoryPageTurns;
            _ws.SendRequest("delete_history", p, resp =>
            {
                if (_window == null || !_window.IsOpen) return;
                string err = RpcError(resp);
                if (err != null)
                {
                    _window.AppendSystemNotice("删除失败：" + err);
                    return;   // 留在选择模式，用户可改选重试
                }
                var data = resp["data"] as JObject;
                if (data?["npc_id"]?.ToString() != _window.CurrentNpcId) return;
                int n = (data["deleted"]?["turns"] as JArray)?.Count ?? turns.Count;
                var items = data["history"]?["items"] as JArray;
                _window.ExitSelecting();
                _window.ReplaceHistory(items ?? new JArray());
                // 删除是「内存账本追加注记」：真正写进 session jsonl 的时机是游戏存档
                // （save_happened→flush_all）——提示里写明，免得以为立刻落盘了
                // （分帧路径下这行提示等到历史建完才出现：它在列表末尾，先插会被后建的行顶走）
                _window.AfterHistoryBuilt(() =>
                    _window.AppendSystemNotice("已删除 " + n + " 个回合（随游戏存档固化）"));
            }, 10000);
        }

        // ------------------------------------------------------------------
        // 群窗删除（§9.2 v2）：按**条**（uid）、两个 RPC、回执一次给新群历史
        //
        // 与单聊删除的三处本质差异（都由 Python 侧承担，这里只是如实转达）：
        //   ① 载荷 = 发言 uid（`{room_id}#{seq}`），不是回合号 —— 群发言没有回合；
        //   ② 影响面 = **公共记录 + 在场成员记忆**（单聊只动那一个 NPC）⇒ 确认文案必须写明；
        //   ③ 守卫多一条"一轮群聊进行中"，且**挂起中的提案叙述行**会被拒（等它收口）。
        // 回执形状：`{ok, room_id, dry_run, uids[], members[{npc,deleted,folded,absent,note}], history}`。
        // ------------------------------------------------------------------

        /// <summary>确认按钮第一击武装：`preview_room_delete` → 摘要写计数位。
        /// 被拒（一轮进行中/成员在忙/提案行挂起/uid 不存在）→ 解除武装 + 系统提示原因</summary>
        private void OnRoomDeletePreviewRequested(List<string> uids)
        {
            if (_window == null || _ws == null || uids == null || uids.Count == 0) return;
            if (!_window.IsRoomMode) return;
            string rid = _window.CurrentRoomId;
            int req = ++_previewReq;
            JObject p = new JObject();
            p["room_id"] = rid;
            p["uids"] = ToJArray(uids);
            _ws.SendRequest("preview_room_delete", p, resp =>
            {
                if (_window == null || !_window.IsOpen || !_window.Selecting) return;   // 已退出模式：作废
                if (req != _previewReq) return;                                          // 期间选择集已变：过期回执
                if (!_window.IsRoomMode || _window.CurrentRoomId != rid) return;         // 已切群
                string err = RpcError(resp);
                if (err != null)
                {
                    _window.ApplyDeletePreview(false, null);
                    _window.AppendSystemNotice("无法删除：" + err);
                    return;
                }
                _window.ApplyDeletePreview(true, BuildRoomPreviewSummary(resp["data"] as JObject));
            }, 10000);
        }

        /// <summary>两段确认后执行：`room_delete` → 成功=退出模式 + 用回执里的新群历史重放（一次往返）
        /// + 提示（写明**成员记忆一并移除**、以及谁已折进纪要/谁本就没听到）；失败=提示并留在选择模式</summary>
        private void OnRoomDeleteConfirmed(List<string> uids)
        {
            if (_window == null || _ws == null || uids == null || uids.Count == 0) return;
            if (!_window.IsRoomMode) return;
            string rid = _window.CurrentRoomId;
            JObject p = new JObject();
            p["room_id"] = rid;
            p["uids"] = ToJArray(uids);
            _ws.SendRequest("room_delete", p, resp =>
            {
                if (_window == null || !_window.IsOpen) return;
                string err = RpcError(resp);
                if (err != null)
                {
                    _window.AppendSystemNotice("删除失败：" + err);
                    return;   // 留在选择模式，用户可改选重试
                }
                var data = resp["data"] as JObject;
                if (data?["room_id"]?.ToString() != rid) return;   // 已切群：回执作废
                _previewReq++;                                     // 在飞的预览一并作废
                int n = (data["uids"] as JArray)?.Count ?? uids.Count;
                var items = data["history"]?["items"] as JArray;
                _window.ExitSelecting();
                _window.ReplaceHistory(items ?? new JArray());
                // 诚实交代两件事：① 成员记忆也改了（不是只抹群窗）；② 随存档固化
                // （分帧路径下这行提示等到历史建完才出现：它在列表末尾，先插会被后建的行顶走）
                _window.AfterHistoryBuilt(() =>
                {
                    string s = "已删除 " + n + " 条群记录（在场成员的记忆一并移除；随游戏存档固化）";
                    string extra = BuildRoomDeleteNotice(data);
                    if (extra.Length > 0) s += "。" + extra;
                    _window.AppendSystemNotice(s);
                    ModMain.P("[ChatPresenter] 群删落地：" + rid + " 条数=" + n
                              + " 剩余历史=" + (items?.Count ?? 0));
                });
            }, 15000);
        }

        private static JArray ToJArray(List<string> list)
        {
            var arr = new JArray();
            foreach (var s in list) arr.Add(s);
            return arr;
        }

        /// <summary>统一解 RPC 错误帧：传输层失败取顶层 error（异常路径），业务失败取 data.reason
        /// （plan fail-closed 路径）；两者都无则给通用文案。null=成功</summary>
        private static string RpcError(JObject resp)
        {
            if (resp == null) return "无响应";
            if (resp["ok"]?.Value<bool>() != true)
                return resp["error"]?.ToString() ?? "未知错误";
            var data = resp["data"] as JObject;
            if (data != null && data["ok"]?.Value<bool>() == false)
                return data["reason"]?.ToString() ?? "删除方案未通过校验";
            return null;
        }

        private static JArray ToJArray(List<int> list)
        {
            var arr = new JArray();
            foreach (var t in list) arr.Add(t);
            return arr;
        }

        /// <summary>
        /// 从 `JObject` 安全取 bool：**键缺失、值为 JSON null、类型不符，一律当 false**。
        ///
        /// **绝不能**写 `o[k]?.Value&lt;bool&gt;() ?? false` —— 键存在但值是 JSON `null` 时
        /// `?.` 不会短路（`o[k]` 返回的是 `JValue` 实例，不是 C# null），随后
        /// `JValue.Value&lt;bool&gt;()` 对 null 值调 `Convert.ChangeType` 抛
        /// `InvalidCastException: Null object cannot be converted to a value type`。
        /// 「对话完了还说回合进行中」的根因就是它在 `OnReplyEvent` 的 try 之外炸掉，
        /// 连带 `finally` 里的忙标复位一起被跳过（详见 OnReplyEvent 注释）。
        /// </summary>
        private static bool BoolOf(JObject o, string key)
        {
            try
            {
                var t = o?[key];
                if (t == null || t.Type == JTokenType.Null) return false;
                return t.Value<bool>();
            }
            catch { return false; }
        }

        /// <summary>预览摘要（plan_delete 的 preview 报告 → 一行确认文案）：
        /// 将删条数/字数 + 纪要连带提示（全覆盖时连折叠记忆一起忘，用户必须知情）</summary>
        private static string BuildPreviewSummary(JObject plan)
        {
            if (plan == null) return null;
            int nodes = plan["removed_nodes"]?.Value<int>() ?? 0;
            int chars = plan["removed_chars"]?.Value<int>() ?? 0;
            int droppedSummaries = (plan["dropped_summaries"] as JArray)?.Count ?? 0;
            string s = "将删 " + nodes + " 条 · 约 " + chars + " 字";
            if (droppedSummaries > 0) s += " · 含折叠记忆 " + droppedSummaries + " 条";
            return s;
        }

        /// <summary>按成员分档（群删回执 `members[]`）：谁把这条折进了纪要、谁本就没听到。
        /// 两档都要说给玩家听 —— 前者是"删不干净"的如实交代，后者是"他确实不知道"。</summary>
        private static void RoomMemberReports(JObject data, List<string> folded, List<string> absent)
        {
            var ma = data?["members"] as JArray;
            if (ma == null) return;
            foreach (var t in ma)
            {
                var m = t as JObject;
                if (m == null) continue;
                string npc = (string)m["npc"] ?? "某位成员";
                if (((m["folded"] as JArray)?.Count ?? 0) > 0) folded.Add(npc);
                else if (((m["deleted"] as JArray)?.Count ?? 0) == 0) absent.Add(npc);
            }
        }

        /// <summary>群删确认摘要（一行）：**必须写明"群记录与在场成员记忆一并删除"** ——
        /// 单聊删只动那一个 NPC 的记忆，群删是当众抹掉一条（用户拍板的文案口径，见 §9.2）。</summary>
        private static string BuildRoomPreviewSummary(JObject data)
        {
            if (data == null) return null;
            int n = (data["uids"] as JArray)?.Count ?? 0;
            var folded = new List<string>();
            var absent = new List<string>();
            RoomMemberReports(data, folded, absent);
            string s = "将移除 " + n + " 条发言：群记录与在场成员的记忆一并删除";
            if (folded.Count > 0) s += "；" + string.Join("、", folded.ToArray()) + " 已折进纪要（无法逐字遗忘）";
            if (absent.Count > 0) s += "；" + string.Join("、", absent.ToArray()) + " 本就没听到";
            return s;
        }

        /// <summary>群删执行后的补充说明（折进纪要 / 本就没听到的那些人）。无补充返回空串。</summary>
        private static string BuildRoomDeleteNotice(JObject data)
        {
            if (data == null) return "";
            var folded = new List<string>();
            var absent = new List<string>();
            RoomMemberReports(data, folded, absent);
            var parts = new List<string>();
            if (folded.Count > 0)
                parts.Add(string.Join("、", folded.ToArray()) + "的纪要里仍有大意（折进去的无法逐字遗忘）");
            if (absent.Count > 0)
                parts.Add(string.Join("、", absent.ToArray()) + "本就没听到这条");
            return string.Join("；", parts.ToArray());
        }

        /// <summary>通知 Python 销毁该 NPC 的活体（关窗=落盘销毁；回合在跑由 Python 侧延至回合毕）。
        /// fire-and-forget：响应被忽略，但回调不可为 null（WsClient 全路径都会调它）。</summary>
        internal static void DisposeAgentOnServer(WsClient ws, string npcId)
        {
            if (ws == null || string.IsNullOrEmpty(npcId)) return;
            JObject p = new JObject();
            p["npc_id"] = npcId;
            ws.SendRequest("dispose_agent", p, _ => { }, 10000);
        }

        /// <summary>★ 10-17：单聊忙标的**唯一**刷新入口 —— 按 `TurnRegistry` 重算当前窗口 NPC 的忙态。
        ///
        /// 为什么要有它（用户拍板"改成单一事实来源"）：忙标此前是**谁最后碰它谁说了算**，
        /// 全工程 15 处 `SetBusy` 各写各的、判断标准互不相同。真机事故：
        ///   点「同意」→ 开窗点亮忙标 → 几百毫秒后历史回放落地 → `ResetContent` 灭掉它
        ///   → 模型还要跑 15 秒，屏幕上**什么都没有**。
        /// 根因不是某一行写错，而是没有单一事实来源 —— 见 `TurnRegistry` 文件头。
        ///
        /// ⇒ 本方法只**读**登记表，不**决定**忙态。所有单聊路径的忙标变化都该调它，
        ///   而不是直接写 `SetBusy(true/false)`。**群聊**（`IsRoomMode`）另有一套
        ///   `room_state` 阶段驱动，本方法不介入（那条路保持原样）。
        ///
        /// `why` 只进日志（看清是"哪条路径触发的重算"）。
        /// </summary>
        private void RefreshSoloBusy(string why)
        {
            if (_window == null) return;
            if (_window.IsRoomMode) return;                 // 群聊自成一套，不碰
            string npc = _window.CurrentNpcId ?? "";
            bool busy = TurnRegistry.IsRunning(npc);
            SetBusy(busy, why + "（登记表 npc=" + npc + "）");
            // 阶段文案（"战斗进行中…"这类）随忙态一起刷新：不忙时必须清空，
            // 否则下一次忙会把上一回合的阶段当成通用文案（`SetBusyPhase("")` 的语义）。
            try
            {
                if (_window != null)
                    _window.SetBusyPhase(busy ? TurnRegistry.PhaseOf(npc) : "");
            }
            catch { }
        }

        /// <summary>收一条阶段帧：进入/离开某阶段（11-21）。
        ///
        /// ★ 三个"必须" ★（它们就是这段代码存在的全部风险面）：
        ///   ① **必须刷新 `_lastFrameAt`**：`BusyWatchdog` 的判据是"多久没收到帧"，
        ///      而一个等模型 5 分钟的回合期间只有这几条阶段帧 —— 不刷它，
        ///      第 240 秒会被自愈逻辑清成空闲，玩家以为回完了。
        ///   ② **必须在 `_busy == false` 时不硬显示**：忙标灭着的时候改文案没有意义
        ///      （`SetBusyPhase` 内部已经有 `_busy` 判断，这里不重复造一套）。
        ///   ③ **离开时清空、不是保留**：留着"正在回话…"而字已经停了，比不显示更误导。
        ///      与 `SetBusy(false)` 里那句 `SetBusyPhase("")` 同一个道理。
        ///
        /// 阶段切换（think → stream）时**重算起点**：否则"正在回话…（15 秒）"里的 15 秒
        /// 会连等首字的时间一起算进去，而玩家看到的是"回话"这个动作本身才开始。
        /// </summary>
        private void ApplyWaitPhase(string phase, bool entering)
        {
            try
            {
                if (string.IsNullOrEmpty(phase)) return;
                _lastFrameAt = Time.unscaledTime;      // ① 见上
                if (entering)
                {
                    _waitPhase = phase;
                    _waitPhaseSince = Time.unscaledTime;
                }
                else
                {
                    // 只清"当前这个阶段"：迟到的旧阶段离开帧（think 的离开排在 stream 进入之后
                    // 到达，或断线重连后补发）不该把新阶段清掉。
                    if (_waitPhase != phase) return;
                    _waitPhase = "";
                }
                RefreshBusyPhaseText();
            }
            catch (Exception e)
            {
                try { ModMain.P("[ChatPresenter] ApplyWaitPhase 异常：" + e.Message); } catch { }
            }
        }

        /// <summary>按当前阶段 + 已等秒数刷新忙标文案。每帧调（`Update` 里），
        /// 只在**文案真变化**时写 Text —— 否则每帧一次 `Text.text` 赋值会让 UGUI 反复重建网格。</summary>
        private void RefreshBusyPhaseText()
        {
            try
            {
                if (_window == null) return;
                if (!_busy) return;                    // ② 忙标灭着：文案归 SetBusy 管
                string prefix = WaitPhaseText(_waitPhase);
                if (prefix.Length == 0) { _waitPhaseShown = ""; return; }
                float elapsed = Time.unscaledTime - _waitPhaseSince;
                // 阈值来自配置（ui.busy_hint_after）。<=0 = 关闭：只显示阶段文案，不报秒数。
                string want = (_busyHintAfter > 0f && elapsed >= _busyHintAfter)
                    ? prefix + "（" + (int)elapsed + " 秒）"
                    : prefix;
                if (want == _waitPhaseShown) return;
                _waitPhaseShown = want;
                _window.SetBusyPhase(want);
            }
            catch { }
        }

        /// <summary>把「超过多久才报秒数」的阈值换成配置值（`ui.busy_hint_after`，秒）。
        /// <=0 合法 = 关闭（只显示阶段文案，不报秒数）。</summary>
        internal void ConfigureBusyHint(float seconds)
        {
            _busyHintAfter = seconds;
        }

        /// <summary>配置入口：给**所有活着的** presenter 实例设同一个阈值。
        ///
        /// 为什么需要"所有"：一个 NPC 一个 presenter 实例（同时可能开着好几个窗口），
        /// 而配置是全局的。`ModMain` 拿不到实例表 —— 所以在这里维护一份弱引用表，
        /// 由 `Init` 登记、`OnDestroy` 摘除。
        ///
        /// ★ 必须是**静态**而不是走 `ModMain.PresenterInstance` ★：那个字段在 AB_UI 模式下
        ///   从不被赋值（只有置 null），拿它设配置等于什么都没做 —— 与 `Init` 里那些
        ///   订阅为什么不放 ModMain 是同一个原因（见 `ChatPresenter.Init` 的注释）。
        /// </summary>
        private static readonly List<System.WeakReference> _livePresenters = new List<System.WeakReference>();

        internal static void ConfigureBusyHintAll(float seconds)
        {
            try
            {
                // 顺手清掉已销毁的：实例随窗口生灭，不清会随游戏时长线性增长
                for (int i = _livePresenters.Count - 1; i >= 0; i--)
                {
                    var t = _livePresenters[i].Target as ChatPresenter;
                    if (t == null) { _livePresenters.RemoveAt(i); continue; }
                    try { t.ConfigureBusyHint(seconds); } catch { }
                }
                ModMain.P("[ChatPresenter] 忙标提示阈值已更新：" + seconds + "s（实例 "
                          + _livePresenters.Count + " 个）");
            }
            catch { }
        }


        private void SetBusy(bool busy, string why = "")
        {
            if (_busy != busy)
            {
                try
                {
                    ModMain.P("[ChatPresenter] 忙 " + (busy ? "ON" : "OFF")
                              + " 实例#" + _id + (busy ? " ← " + why : ""));
                }
                catch { }
            }
            _busy = busy;
            if (busy)
            {
                _busySince = Time.unscaledTime;
                // 点亮时若已有阶段，立刻按它刷一次（否则会先显示默认文案、下一帧才变，
                // 屏幕上闪一下「对方正在斟酌…」再跳成「正在读取人物状态…」）。
                RefreshBusyPhaseText();
            }
            else
            {
                // 灭忙标 = 回合结束/取消/断线 ⇒ 阶段一并作废。
                // 不清的话，下一个回合在没有 wait_phase 帧的间隙（比如开局几百毫秒）会
                // 顶着上一回合残留的「正在回话…（37 秒）」。
                _waitPhase = "";
                _waitPhaseShown = "";
            }
            if (_window != null) _window.SetBusy(busy);
        }

        /// <summary>
        /// 忙标自愈：`_busy` 唯一的清点是回合收口帧。一旦收口帧**没到**（presenter 未订阅 /
        /// 被守卫丢弃），它就是个死标志 —— 表现是"对话明明完了，删除/压缩永远提示回合进行中"，
        /// 而且**只有重启游戏才能恢复**。这里给一条有界兜底：只要连着 `BusyStaleSeconds` 一条帧都没
        /// 收到（真回合不可能这么久不吐一个字），就认定这个忙标是死的，主动放行。
        /// 判据用"最近一次收到帧的时刻"而不是"置忙时刻"—— 长回合会一直吐帧，不会被误杀。
        ///
        /// ★ 10-17：多一条"与登记表不一致"的判据（用户拍板单一事实来源的必然要求）★
        /// UI 显示的忙态与 `TurnRegistry` 说的不一致，就是不一致 —— 立刻对齐，不必等超时。
        /// 这条比原来的纯超时猜测**更早、更准**：超时那条仍保留（它抓的是"登记表也不知道
        /// 回合死了"的情形，比如 Python 真的崩了、收口帧永远不会来）。
        /// 群聊不适用（它的忙态由 `room_state` 阶段驱动，不在登记表里）。
        /// </summary>
        private void BusyWatchdog()
        {
            if (!_busy) return;
            if (_window != null && !_window.IsRoomMode)
            {
                string npc = _window.CurrentNpcId ?? "";
                if (!TurnRegistry.IsRunning(npc))
                {
                    ModMain.P("[ChatPresenter] ★忙标与登记表不一致★ 实例#" + _id + " npc=" + npc
                              + " → 立即对齐（UI 忙、登记表空闲）");
                    RefreshSoloBusy("与登记表不一致");
                    return;
                }
            }
            float last = _busySince > _lastFrameAt ? _busySince : _lastFrameAt;
            if (Time.unscaledTime - last < BusyStaleSeconds) return;
            ModMain.P("[ChatPresenter] ★忙标自愈★ 实例#" + _id + " 已 " + (int)(Time.unscaledTime - last)
                      + "s 无任何帧 → 强制复位（收口帧大概率丢了，查上面的 ★帧被丢弃 / Init 订阅）");
            if (_window != null && !_window.IsRoomMode) RefreshSoloBusy("自愈");
            else SetBusy(false, "自愈");
        }
    }
}