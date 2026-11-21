/// <summary>
/// Python 侧存活监测 —— **唯一状态持有者 + 唯一起作用点**
///
/// 为什么要有这个类：
///   旧实现把「进程死活」交给「WS 连接状态」回答，而那是**单向蕴含**：
///       WS 连着  ⟹ 进程活着        ✅
///       进程活着 ⇏ WS 连着          ❌ 反向不成立
///   WS 断开的五种原因里只有一种是"进程死了"，其余四种（onefile 解压中 / 还没 bind /
///   握手失败 / 正常重连窗口）进程都**活着**。于是：
///     · 进程真的死掉时 → 只会每 2 秒重连一次，**永远不重新拉起**；
///     · 若反过来用"断开 N 秒"当拉起依据 → 会把正在解压的 onefile 当成死的，
///       再拉一份抢端口（正是 那类故障）。
///
/// 三条不变量：
///   ① **不跨层推断**：进程层与传输层各自独立观测，谁都不拿去推对方；
///      破坏性动作（重新拉起）要求两层**都同意**（见 Evaluate 的分支注释）。
///   ② **拉起只有一个出口**：就是本类。配置重启编排必须先 Suspend，不许并行拉。
///   ③ **每个异常状态都要有玩家可见的表现** —— ChatWindow.cs:397「不许静默失败」。
///
/// 三层职责（本类实现前两层；应用层心跳是 TODO，见文档 §4）：
///   进程层 = 脑子还在不在   → Launcher.ProcessState()
///   传输层 = 现在能不能说话 → WsClient.IsConnected
///   应用层 = 脑子还清醒吗   → 心跳 RPC（未实现：socket 通但事件循环卡死目前看不出来）
///
/// IL2CPP 约束：纯静态类，**不新增 MonoBehaviour 类型**（免 RegisterTypeInIl2Cpp）。
/// 线程：OnUpdate 由 g.timer.Frame 驱动 = 主线程，故可直接碰 UI。
/// </summary>
using System;
using UnityEngine;

namespace AgentLoopBridge
{
    public static class BrainLink
    {
        public enum LinkState
        {
            /// <summary>未启动 / Destroy 之后。</summary>
            Stopped,
            /// <summary>不是本 mod 拉起的（玩家手起 server.py）→ 只报告，不干预。</summary>
            Idle,
            /// <summary>已拉起但尚未连上。**这是正常态**：onefile 热启动实测约 2 秒
            /// （冷启动 + 杀软首次扫描会明显更久，见 StartGraceSeconds 的说明）。</summary>
            Starting,
            /// <summary>连着 = 健康。</summary>
            Ready,
            /// <summary>进程活着但连接断了 → 等 WsClient 自己重连（它每 2s 一轮）。</summary>
            Reconnecting,
            /// <summary>进程死了，正在退避等待下一次拉起。</summary>
            WaitingRelaunch,
            /// <summary>正在拉起。</summary>
            Relaunching,
            /// <summary>拉起额度用尽 → 必须玩家重启游戏。</summary>
            GaveUp,
        }

        // ---- 节拍与阈值 ----------------------------------------------------
        /// <summary>轮询间隔。**不能每帧**：HasExited 是系统调用，每帧查是浪费。</summary>
        private const float PollSeconds = 0.5f;

        // ---- 拉起失败横幅（09-20 新增） --------------------------------------
        //
        // 用户要求：**首次拉起失败就弹**（不等 GaveUp），且**每次失败都要弹**。
        // 为什么不等 GaveUp：GaveUp 要等宽限期 35s + 重拉 3 次走完（约 1~2 分钟），
        //   那时玩家已经在"NPC 不回话"里困惑很久了。
        // 代价（知情接受）：有些失败下一轮能自愈（典型：旧进程还没让出端口），
        //   这时横幅是虚惊一场 —— 但它说的是**事实**（"这一次拉起失败了"），不是误报。
        //
        // 两条硬约束（都在 TickFailureBanner 里落实）：
        //   ① **必须进世界之后才弹**：`g.root` 未建时弹，要么崩要么被吞，横幅看不见。
        //      判据复用 AbPanelProber.CanCreateNow()（它查 MapMain 是否已建 = 世界真就绪），
        //      没就绪就**挂着**，下一帧再试（不丢，只是延后）。
        //      另有一条**自己的**剧情闸（`NpcInitiativeMonitor.IsDramaWindowShowing()`）：
        //      剧情演到一半不弹横幅。它不在 CanCreateNow 里 —— 那条闸门管的是"能不能建
        //      面板宿主"，玩家亲手点的东西不该被剧情拦（09-21 定，详见 AbPanelProber 文件头）。
        //   ② **只弹一次/次失败**：Evaluate 每 0.5s 跑一遍，不加闩锁会刷屏。
        //      用 "已播报到第几次失败" 做闩锁 —— 这样"每次失败都弹"与"不刷屏"同时成立。

        /// <summary>已播报过的失败次数（Launcher.FailureCount 的水位线）。</summary>
        private static int _failNotifiedCount;
        /// <summary>失败发生的时间戳（用于超时放弃：世界迟迟不就绪就别再挂了）。</summary>
        private static float _failPendingAt = -1f;
        /// <summary>挂着等"世界就绪"的失败横幅最多等多久（秒）。
        /// 超时就丢掉 —— 世界一直没就绪说明玩家还在主菜单，这时弹也没意义。</summary>
        private const float FailureBannerMaxWait = 120f;

        /// <summary>启动宽限期。**只在"从未连上过"时生效**（见 Evaluate 分支③），
        /// 所以它是"防把启动中当死亡"的屏障，正常游玩期间不影响反应速度。
        ///
        /// 取值依据（Windows 11 / 文件缓存已热）：
        ///   PyInstaller 版 `--selftest` 全程 1.84s / 1.77s；Nuitka 版 2.16s / 1.93s。
        ///   —— 即"热启动约 2 秒"，不是先前文档里写的 15 秒（那个数字来自一次
        ///   `sleep 15` 后查端口，是**上界而非测量值**，已更正）。
        ///   35 秒是给**玩家机器上的冷启动**留的余量：首次运行要冷读 15~18MB，
        ///   且未签名的 exe 会被杀软完整扫描一遍（这一档**本机无法实测**，是估的）。
        /// 代价：进程若在拉起后 35 秒内**从未连上过**就死了，要等宽限期满才重试（+退避 4s）。
        /// 这是有意的保守方向——误拉第二份抢端口的代价，远大于晚 35 秒重试。</summary>
        private const float StartGraceSeconds = 35f;
        /// <summary>同一局游戏内最多自动拉起几次（防拉起风暴）。</summary>
        private const int MaxRelaunch = 3;
        /// <summary>退避阶梯（秒）：越拉不起来等得越久。</summary>
        private static readonly float[] BackoffSeconds = { 4f, 15f, 45f };
        /// <summary>非致命状态的横幅节流（秒）：连接抖动每 2s 一轮，不节流会刷屏。</summary>
        private const float AnnounceThrottleSeconds = 10f;

        // ---- 状态 ----------------------------------------------------------
        private static WsClient _ws;
        // volatile：`State`/`StatusText` 会被后台线程的 WsClient.NoteDropped 读（它在丢弃帧时
        // 要把当前状态一起写进日志），而写发生在主线程。enum 底层是 int，可以 volatile。
        private static volatile LinkState _state = LinkState.Stopped;
        private static volatile string _statusText = "";
        private static volatile bool _suspended;

        private static float _pollAcc;
        private static float _launchedAt;        // 本轮进程的拉起时刻（重拉会刷新）
        private static float _nextAttemptAt;     // 退避：早于这个时刻不拉
        private static int _relaunchCount;
        /// <summary>**本次拉起**是否成功连上过。用于收窄启动宽限期的适用范围：
        /// 从没连上过 → 宽限期保护"可能还在启动"；已经连上过 → 进程再死就是真故障，不必等。</summary>
        private static bool _launchEverConnected;

        public static LinkState State { get { return _state; } }
        /// <summary>当前状态的一行说明（健康时为空串）。供 UI / 诊断读取。</summary>
        public static string StatusText { get { return _statusText; } }
        public static bool Healthy { get { return _state == LinkState.Ready; } }
        public static int RelaunchCount { get { return _relaunchCount; } }
        public static bool Suspended { get { return _suspended; } }

        // ---- 生命周期 ------------------------------------------------------

        /// <summary>Init 时调用（在 Launcher.LaunchPythonOnce() **之后**，让宽限期从拉起那刻算）。</summary>
        public static void Start(WsClient ws)
        {
            _ws = ws;
            _suspended = false;
            _pollAcc = 0f;
            _launchedAt = Time.unscaledTime;
            _nextAttemptAt = 0f;
            _relaunchCount = 0;
            _launchEverConnected = false;   // 新的一轮拉起，宽限期重新生效
            _everReady = false;      // 本局第一次连上算正常过程，不该弹"已恢复"
            _failNotifiedCount = 0;  // 本轮启动的失败横幅额度（见 TickFailureBanner）
            _state = LinkState.Starting;
            _statusText = "正在接通传音法阵…";
            ModMain.P("[BrainLink] 存活监测已启动（进程层判据；宽限 " + StartGraceSeconds
                      + "s，最多重拉 " + MaxRelaunch + " 次）");
        }

        /// <summary>Destroy 时调用。**注意 Destroy 不停 Python**（回主界面要复用），
        /// 所以这里只是停掉"监测"这一半动作，进程本身照旧活着等下次 Init 复用。</summary>
        public static void Stop()
        {
            _ws = null;
            _suspended = false;
            _state = LinkState.Stopped;
            _statusText = "";
            ModMain.P("[BrainLink] 存活监测已停止（Destroy；Python 进程按既有设计保持运行）");
        }

        /// <summary>
        /// 挂起：配置重启编排期间必须调，否则编排刚把旧进程杀掉，
        /// 本类会把它判成"死亡"并**同时**拉起 → 两个 Process.Start 抢端口。
        /// </summary>
        public static void Suspend(string why)
        {
            if (_suspended) return;
            _suspended = true;
            ModMain.P("[BrainLink] 挂起（" + why + "）：此期间不判定、不拉起");
        }

        /// <summary>恢复（编排收口时调）。重新给一个启动宽限期——对方刚拉起新进程。</summary>
        public static void Resume()
        {
            if (!_suspended) return;
            _suspended = false;
            _pollAcc = 0f;
            _launchedAt = Time.unscaledTime;
            _nextAttemptAt = 0f;
            _relaunchCount = 0;      // 编排是一次"有意重启"，额度重新给满
            _state = LinkState.Starting;
            _statusText = "正在接通传音法阵…";
            ModMain.P("[BrainLink] 恢复监测（重新计宽限期与重拉额度）");
        }

        // ---- 每帧（g.timer.Frame → 主线程）--------------------------------

        public static void OnUpdate()
        {
            if (_suspended || _ws == null) return;
            _pollAcc += Time.unscaledDeltaTime;
            if (_pollAcc < PollSeconds) return;
            _pollAcc = 0f;
            // 耗时采样（TickProfiler，09-28 卡顿定位）：0.5s 一次的进程/连接状态机
            long __tpEval = TickProfiler.Begin();
            try { Evaluate(); }
            catch (Exception e) { ModMain.P("[BrainLink] Evaluate 异常: " + e.Message); }
            finally { TickProfiler.End("brainlink.eval", __tpEval); }
            // 拉起失败横幅独立于 Evaluate 的状态机：它跟的是 Launcher 的**失败计数**，
            // 而不是连接状态。挂在这里（而不是 SetState 里）是因为它要等"世界就绪"这个外部条件。
            long __tpBan = TickProfiler.Begin();
            try { TickFailureBanner(); }
            catch (Exception e) { ModMain.P("[BrainLink] 失败横幅异常: " + e.Message); }
            finally { TickProfiler.End("brainlink.banner", __tpBan); }
        }

        /// <summary>
        /// 拉起失败的横幅播报（09-20 新增）。
        ///
        /// 语义：**每次失败弹一次**，但**必须等世界就绪**（`g.root` / MapMain 建好）才弹。
        /// 世界没就绪不算"放弃"，只是**挂着**（`_failPendingAt` 记着），下一轮再试；
        /// 挂超过 <see cref="FailureBannerMaxWait"/> 秒仍未就绪就丢掉（玩家还在主菜单，弹了也没用）。
        ///
        /// 为什么用"水位线"而不是布尔闩锁：用户要求"每次失败都要弹"。
        /// 布尔闩锁只能弹第一次；水位线（已播报次数 vs Launcher.FailureCount）天然支持每次。
        /// </summary>
        private static void TickFailureBanner()
        {
            int fails = Launcher.FailureCount;
            if (fails <= _failNotifiedCount) return;          // 没有新失败

            // 有新的失败待播报：记下"第一次发现它"的时刻（挂起计时从这里起算）
            if (_failPendingAt < 0f) _failPendingAt = Time.unscaledTime;
            if (Time.unscaledTime - _failPendingAt > FailureBannerMaxWait)
            {
                // 等太久了：世界一直没就绪。丢掉横幅但**推进水位线**，
                // 否则下一次真进世界时会补弹一堆陈年旧账。
                ModMain.P("[BrainLink] 失败横幅超时放弃（世界 " + FailureBannerMaxWait
                          + "s 内未就绪），已跳过 " + (fails - _failNotifiedCount) + " 条");
                _failNotifiedCount = fails;
                _failPendingAt = -1f;
                return;
            }

#if AB_UI
            // 世界就绪闸：复用 AbPanelProber 的三条判据（CurrentGameDay / playerUnit / MapMain）。
            // 铁律同 AbConfigPanel：世界未就绪时绝不碰 UI 层。
            bool worldReady;
            try { worldReady = AbPanelProber.CanCreateNow(); }
            catch { worldReady = false; }
            if (!worldReady) return;                          // 挂着，下一轮再试
            // ★ 剧情闸是**我们自己的一条**，不是从面板闸门顺来的（09-21）★
            //   横幅是系统在玩家没要求时自己弹的东西 → 语义属 `BusyReason` 那一类
            //   （"玩家正忙就别打断"），而不是"世界没就绪"。原先它跟着 `CanCreateNow()`
            //   里的 ④ 走，那道 ④ 因另有理由被删（见 AbPanelProber 文件头），删了不等于
            //   这条诉求消失了 —— 诉求还在，判据就留在这里。
            if (NpcInitiativeMonitor.IsDramaWindowShowing()) return;
#endif

            _failNotifiedCount = fails;
            _failPendingAt = -1f;
            // 横幅正文预算 34 字（UnreadBanner.ShowSystem 会截断）→ 只给"原因 + 一句话该怎么办"。
            // 完整清单在 _diag_last_failure.txt，不往横幅里塞。
            string msg = Launcher.LastFailure;
            if (!string.IsNullOrEmpty(Launcher.LastFailureHint))
                msg += "；" + Launcher.LastFailureHint;
            ModMain.P("[BrainLink] 拉起失败横幅（第 " + fails + " 次）：" + msg);
            Announce(_state, msg);
        }

        private static void Evaluate()
        {
            // ① 传输层通 → 一定健康（进程必然活着；这是**唯一**成立的蕴含方向）
            if (_ws.IsConnected)
            {
                _relaunchCount = 0;          // 连上了 = 这一轮故障结束，额度重新给满
                _launchEverConnected = true; // 本次拉起成功过 → 宽限期从此不再适用
                SetState(LinkState.Ready, "");
                return;
            }

            Launcher.ProcState ps = Launcher.ProcessState();
            float sinceLaunch = Time.unscaledTime - _launchedAt;

            // 宽限期**只在"本次拉起从未连上过"时生效**：
            //   已经连上过 = 我们确知"成功启动长什么样"，进程再死就不是"还在启动中"，
            //   没必要再等满 35 秒（典型：配置错误导致 Python 起来几秒后崩掉）。
            bool inGrace = sinceLaunch < StartGraceSeconds && !_launchEverConnected;

            // ② 不是我们拉起的 → 只报告不干预（拉了就是两个进程抢同一个端口）
            if (ps == Launcher.ProcState.NotOwned)
            {
                SetState(LinkState.Idle, "传音法阵未运行（非本 mod 拉起）");
                return;
            }

            // ②′ **没有可用入口** → 没什么可等的，立即给结论。
            //     走到这里说明 LaunchAttempted 为真，所以 CanRelaunch 为假只可能是
            //     EntryResolved 为假（FindPython 没找到 exe / 解释器，或 server.py 找不到）。
            //     不加这条的话，「缺 exe」这种一眼可见的故障会被启动宽限期拖满 35 秒，
            //     期间一直显示"正在接通传音法阵…" —— 那是在骗玩家（根本没有东西在被唤醒）。
            if (ps == Launcher.ProcState.Dead && !Launcher.CanRelaunch)
            {
                TryRelaunch();      // 里面第一道闸就会判成 GaveUp 并说明原因
                return;
            }

            // ③ 进程活着 / 句柄不可判定 → 只是"还没连上"，等就行
            //    句柄不可判定（Unknown）时**不下结论**：宁可等着，也不误拉第二份。
            //    宽限期内（且从未连上过）也走这里：onefile 启动期间 WS 必然是断的，那不是故障。
            if (ps != Launcher.ProcState.Dead || inGrace)
            {
                if (inGrace)
                    SetState(LinkState.Starting, "正在接通传音法阵…");
                else
                    SetState(LinkState.Reconnecting, "与传音法阵的连接中断，正在重连…");
                return;
            }

            // ④ 两层都同意"没了"（进程层 Dead + 传输层断）才动手。
            //    要求一致是有意的：若句柄失真（父进程没了但 Python 子进程还活着），
            //    WS 通常仍连着 → 上面 ① 就返回了，绝不会误拉。
            TryRelaunch();
        }

        private static void TryRelaunch()
        {
            // 没解析到入口 → 重试多少次都没用，直接给结论（玩家需要知道要自己处理）
            if (!Launcher.CanRelaunch)
            {
                SetState(LinkState.GaveUp, "传音法阵已停止（未找到可执行入口，无法自动重启）");
                return;
            }
            if (DiagSwitches.NoRelaunch)
            {
                SetState(LinkState.GaveUp, "传音法阵已停止（自动重启已被 _diag_no_relaunch.txt 关闭）");
                return;
            }
            if (_relaunchCount >= MaxRelaunch)
            {
                SetState(LinkState.GaveUp, "传音法阵反复停止，已放弃自动重启，请重启游戏");
                return;
            }
            if (Time.unscaledTime < _nextAttemptAt)
            {
                SetState(LinkState.WaitingRelaunch, "传音法阵已停止，等待自动重启…");
                return;
            }

            _relaunchCount++;
            _nextAttemptAt = Time.unscaledTime
                             + BackoffSeconds[Math.Min(_relaunchCount - 1, BackoffSeconds.Length - 1)];
            SetState(LinkState.Relaunching,
                     "传音法阵已停止，正在自动重启（第 " + _relaunchCount + "/" + MaxRelaunch + " 次）…");

            string err = Launcher.SupervisorRelaunch();
            if (err != null)
            {
                // 拉起失败（杀软拦截 / exe 被删）→ 不退避重来，交给下一轮的 _nextAttemptAt
                ModMain.P("[BrainLink] 重新拉起失败：" + err);
                return;
            }
            _launchedAt = Time.unscaledTime;    // 新进程 → 重新给启动宽限期
            _launchEverConnected = false;       // 新一轮拉起，宽限期重新生效
            SetState(LinkState.Starting, "正在接通传音法阵…");
        }

        // ---- 状态广播 ------------------------------------------------------

        /// <summary>严重度分级：只有**跨级**变化才打扰玩家（同级的 Reconnecting↔WaitingRelaunch
        /// 之类不重复提示）。GaveUp 是最高级，必须让玩家看到"要重启游戏"。</summary>
        private static int Severity(LinkState s)
        {
            switch (s)
            {
                case LinkState.Ready: return 0;
                case LinkState.Starting: return 1;
                case LinkState.Idle: return 1;
                case LinkState.Stopped: return 1;
                case LinkState.Reconnecting: return 2;
                case LinkState.WaitingRelaunch: return 3;
                case LinkState.Relaunching: return 3;
                case LinkState.GaveUp: return 4;
                default: return 1;
            }
        }

        private static void SetState(LinkState s, string text)
        {
            LinkState prev = _state;
            _state = s;
            _statusText = text ?? "";
            if (s == prev) return;

            int sevNew = Severity(s), sevOld = Severity(prev);

            // ---- 连上了：先补会话，再决定要不要说话 ----
            if (s == LinkState.Ready)
            {
                // 断线期间发出的 open_chat 已被 Send 丢弃，这里补回来，
                // 否则"连接回来了但当前 NPC 的 agent 没激活"，接着发消息会落到未激活的 agent 上。
                bool recovered = _everReady;
                _everReady = true;
                if (!recovered) return;      // 本局第一次连上：正常过程，不打扰
#if AB_UI
                try { var p = ModMain.PresenterInstance; if (p != null) p.ReopenAfterReconnect(); }
                catch (Exception e) { ModMain.P("[BrainLink] 重开会话异常: " + e.Message); }
#endif
                // 恢复提示同样要节流：断线每 2s 一轮，抖动时会反复"断开→恢复"
                if (Time.unscaledTime - _lastAnnounceAt < AnnounceThrottleSeconds) return;
                _lastAnnounceAt = Time.unscaledTime;
                Announce(s, "传音法阵已恢复");
                return;
            }

            if (s == LinkState.Starting || s == LinkState.Idle) return;   // 正常过程态：不打扰
            if (sevNew == sevOld) return;                                 // 同级抖动：不打扰
            // GaveUp 不节流 —— 它是"必须玩家重启游戏"的终点，漏掉就没意义了
            if (sevNew < 4 && Time.unscaledTime - _lastAnnounceAt < AnnounceThrottleSeconds) return;
            _lastAnnounceAt = Time.unscaledTime;
            Announce(s, _statusText);
        }

        private static float _lastAnnounceAt = -999f;
        private static bool _everReady;

        private static void Announce(LinkState s, string msg)
        {
            if (string.IsNullOrEmpty(msg)) return;
            ModMain.P("[BrainLink] 状态=" + s + " → " + msg);
#if AB_UI
            // 复用现有的 HUD 级常驻横幅（挂 g.root、纯提示不可点、8s 自动隐藏）。
            // 不新建 UI：玩家可能正开着对话窗，也可能是别处触发，横幅是唯一"在哪都看得见"的出口。
            try { UnreadBanner.ShowSystem(msg); }
            catch (Exception e) { ModMain.P("[BrainLink] 横幅显示异常: " + e.Message); }
#endif
        }

        /// <summary>给发送路径用的一句话（ChatPresenter 在未连接时拿它提示玩家）。</summary>
        public static string SendBlockedNotice()
        {
            switch (_state)
            {
                case LinkState.Starting: return "传音法阵正在接通，稍等几秒再发";
                case LinkState.Reconnecting: return "与传音法阵的连接已断开，正在自动重连…";
                case LinkState.WaitingRelaunch:
                case LinkState.Relaunching: return "传音法阵已停止，正在自动重启…";
                case LinkState.GaveUp: return "传音法阵已停止且自动重启失败，请重启游戏";
                case LinkState.Idle: return "传音法阵未运行（当前未连接）";
                default: return "与传音法阵的连接未就绪";
            }
        }
    }
}
