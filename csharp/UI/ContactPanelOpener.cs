/// <summary>传音簿（通讯录）统一开关入口。
///
/// ⚠ **本 mod 已无任何热键**：`AbHotkeys` 不再轮询按键，`ChatPresenter` 的 F9 也已删。
/// 全部开关都走界面入口 —— 传音簿 = 主 HUD 的「传」按钮（`MapMainContactButton.cs:457`）、
/// 对话窗内拉人/踢人（`AbContactPanel.BeginInviteStatic`/`BeginKickStatic` 直连 EnsureResident）、
/// 配置面板 = 对话窗标题栏 ⚙。**日志文案里若再冒出 F9/F10/F11，那是文案陈旧，不是真有热键**
/// （09-17 复查：本文件第 26 行那句"F10 本次不响应"就骗过一次排障，已改）。
///
/// 代码构建版：ModMain.Init 装配 ContactPresenterInstance 直接用；
/// AB 构建版（迁移）：常驻宿主按需创建（`AbContactPanel.EnsureResident`），此处兜底懒创建，
/// 成功即回填 ModMain.ContactPresenterInstance。
/// 僵尸教训：宿主被 GC 回收后引用连 == null 都会抛（ObjectCollectedException），
/// 故 AB 分支一律先走 AbContactPanel.GetAlivePresenter() 验活取用，僵尸/已毁自动清引用，
/// null 再兜底重建——不再裸判 ModMain.ContactPresenterInstance。
///
/// ============================ 待开重试（09-20 实机事故） ============================
/// 用户现场：「刚进游戏点击传按钮尝试打开UI但是没反应，**需要点好几遍才行**」。
///
/// 根因**不是**闸门太严，而是**点击被静默丢弃且不重试**：`EnsureResident()` 有五条
/// `return false` 路径（世界未就绪 / 剧情窗 / 资源注入失败 / 登记冲突 / 组件注册失败），
/// 任一条不过，原实现就 `ModMain.P(...)` 一行然后 `return` —— **这次点击蒸发，玩家零反馈**。
/// 于是"要点好几遍"不是计数器，而是**"你点的那一下有没有落在闸门翻转之后"**：
/// 闸门一开，下一次点击就成功；手快点得多，白点的次数就多。
///
/// 【09-21 更正】最初列的"各闸门实测窗口"有两条**不成立**，特此更正以免误导后人：
///   · "Python 冷启中位 67s" —— **不适用**。那条"智能体尚未连接"闸判的是
///     `ChatGlobals.WsClientInstance == null`，而它只在 `ModMain.Init` 赋值一次、**从不置空**
///     ⇒ 进存档后永远非空，这条分支根本到不了；且构造客户端 ≠ socket 已连上。
///     （WS 实测：进程启动 13 秒内 connected，之后断线自动秒级重连。）
///   · "HUD 钮自身注入最多 ~5.6s" —— **张冠李戴**。`MapMainContactButton.RetryDelays`
///     是在等**锚点「修为」文本**建好（源码注释："境界文本晚建约 1.5s 内"），不是按钮本身；
///     且实测两次进世界都是**第一发就命中**（"未找到可克隆的圆钮模板" 0 次）。
///   · "剧情静置窗 300 帧" —— 这条是真的，但**前提是错的**：`OpenMapDrama` 是读档期的
///     任务批量检查器（每次进世界 35 次），不是"剧情窗要开了" ⇒ 每次进世界误拦 ~5s。
///     已随 09-21 闸门整改删除（详见 AbPanelProber 头注）。
///
/// 修法**照搬工程里已验证的范式**：`NpcInitiativeMonitor.TickPendingOpen`（其注释原话：
/// "旧版是'3 帧后只试一次'，失败就退化成'回复到达时才弹窗'……现改为每帧重试、成功即止、
/// 5s 超时"）。对话窗那条路早就解决了同款问题，**「传」按钮从来没接到它上面**。
///
/// 语义：**一次点击 = 一次最终会兑现的意图**。点一下 → 挂起 → 每帧重试 → 闸门一开就弹；
/// 超时或玩家中途改主意则放弃，并把**失败原因**如实告诉玩家（`AbContactPanel.LastFailureReason`）。
///
/// 为什么反馈不能省：各条原因**该做的事完全不同** —— "智能体尚未连接"是等几秒到一分多钟、
/// "界面资源缺失（.ab 未部署？）"是**等一辈子也不会好**。只写日志 = 玩家只能靠猜。
/// 载体用 HUD 级横幅（`UnreadBanner.ShowSystem`）：面板此刻还不存在，任何挂在面板上的
/// 提示都显示不出来（这正是旧横幅那条教训）。
/// </summary>
using System;
using UnityEngine;

namespace AgentLoopBridge
{
    public static class ContactPanelOpener
    {
        // ------------------------------------------------------------------
        // 待开重试状态（仅主线程访问；入口全是 UI 点击 / 帧回调）
        // ------------------------------------------------------------------

        /// <summary>有待兑现的打开意图（玩家点过「传」但宿主还没建出来）。</summary>
        private static bool _pending;

        /// <summary>放弃时刻（`Time.unscaledTime`；0 = 未启用计时）。</summary>
        private static float _deadline;

        /// <summary>本次意图已尝试次数（日志与"第 N 次成功"用）。</summary>
        private static int _tries;

        /// <summary>失败原因是否已提示过（避免每帧弹横幅；原因变化时会再提示一次）。</summary>
        private static string _hintedReason;

        /// <summary>重试总时长（秒）。取 5s 与 `NpcInitiativeMonitor.PendingOpenTimeoutSec` 一致。
        ///
        /// 为什么是 5s 而不是更长：它要覆盖的是"世界就绪/剧情静置窗"这类**秒级**等待。
        /// 而"Python 冷启"实测中位 67s —— 那种情况**不该靠重试硬等**（等到 5s 放弃并如实
        /// 告知"智能体尚未连接"，玩家知道自己在等什么，比默默转 67 秒好）。
        /// 玩家再点一次即可重新计时，成本极低。</summary>
        private const float PendingTimeoutSec = 5f;

        /// <summary>首帧延迟：让 `Show()` 之前的世界状态先稳定一帧（照搬 TickPendingOpen 的 3 帧思路，
        /// 取 1 帧是因为这里没有"确认窗实例尚未销毁"那种同帧竞争）。</summary>
        private const int PendingDelayFrames = 1;

        private static int _delayFrames;

        /// <summary>玩家点「传」（或其它界面入口调 `Toggle`）时的唯一入口。**开关语义**：
        /// 面板开着就关、关着就开（保持原行为不变）。</summary>
        public static void Toggle()
        {
            if (TryOpenNow(toggle: true)) return;   // 闸门已开：立刻打开/关闭（常见情况，零延迟）
            ArmPending();                           // 否则挂起这次意图，交给常驻帧回调重试
        }

        /// <summary>
        /// 快捷键入口（C）：**只开不关**（09-24 用户需求：「点击一个按钮直接唤出打开通讯录 UI」，
        /// 且「传音簿已经打开的情况下不会触发快捷键」）。
        ///
        /// 与 `Toggle()` 的差别是语义、不是实现：玩家按快捷键的诉求是"**唤出**传音簿"，
        /// 已开时**不该**走 Toggle 的"再按一下关掉"（那会变成"按了反而消失"）。
        /// 已开时在接线层就已被拦掉（`HotkeyGate.OurPanelOpen` 判据），
        /// 这里再靠 `TryOpenNow(toggle: false)` 的"已开则不动"兜一层 —— 两层都不会误关。
        ///
        /// 未就绪时与点按钮**同一条**"待开重试"：挂起 → 每帧重试 → 闸门一开自动兑现
        /// （5s 超时则如实告知原因）。
        /// </summary>
        public static void RequestOpen()
        {
            if (TryOpenNow(toggle: false)) return;
            ArmPending();
        }

        /// <summary>
        /// 立刻尝试一次。成功 true（含"已复用活实例"）。
        ///
        /// ★ `toggle` 的区分是必须的（09-20 自查发现的回归）★
        ///   · `toggle: true`  —— 玩家**直接点击**：开关语义（开着就关）。原行为，一字不改。
        ///   · `toggle: false` —— **兑现挂起的打开意图**：只该打开，**已经开着就什么都不做**。
        ///
        /// 不分这两种会怎样（真实场景，不是刁钻边界）：玩家点「传」→ 闸门未开 → 挂起 →
        /// 0.6s 后世界就绪、`TickPending` 把面板打开 → 玩家手快又点一下（他以为还卡着）→
        /// 走到这里用开关语义 → 把**刚打开的面板关掉**。用户症状本就是"要点好几遍"，
        /// 这种"连点"是常态；等于我修了重试却引入了"一闪就没了"。
        /// </summary>
        private static bool TryOpenNow(bool toggle)
        {
            ContactPresenter inst = null;
#if AB_UI
            // AB 构建：验活取用（GC 僵尸引用连 == null 都会抛——GetAlivePresenter 统一收口）
            inst = AbContactPanel.GetAlivePresenter();
            if (inst == null)
            {
                if (!AbContactPanel.EnsureResident())
                {
                    string why = AbContactPanel.LastFailureReason ?? "未知原因";
                    ModMain.P("[ContactPanelOpener] 常驻宿主尚不可用（" + why + "）"
                              + "（入口：HUD「传」按钮；本 mod 已无 F10 热键）");
                    return false;
                }
                ModMain.ContactPresenterInstance = AbContactPanel.Presenter;
                inst = AbContactPanel.Presenter;
                // 诊断开关 createOnly：只创建宿主、**不 Show**——用于二分"创建"与"显示"哪个破坏世界输入
                if (DiagSwitches.CreateOnly)
                {
                    ModMain.P("[ContactPanelOpener] createOnly：仅创建宿主（不显示），本轮到此为止");
                    ClearPending();
                    return true;
                }
            }
#else
            inst = ModMain.ContactPresenterInstance;
#endif
            if (inst == null)
            {
                ModMain.P("[ContactPanelOpener] ContactPresenterInstance 未就绪");
                return false;
            }
            if (toggle) inst.TogglePanel();
            else if (!inst.IsOpen) inst.Show();     // 兑现打开意图：已开则不动（不误关）
            ClearPending();
            return true;
        }

        /// <summary>
        /// 挂起这次打开意图（已挂起则只续期，不重复计数/不重复提示）。
        /// **这是"点一次就够"的关键**：玩家不必再点第二遍，闸门一开自己弹。
        /// </summary>
        private static void ArmPending()
        {
            bool first = !_pending;
            _pending = true;
            _delayFrames = PendingDelayFrames;
            _deadline = Time.unscaledTime + PendingTimeoutSec;
            if (first) _tries = 0;
            HintFailure(false);            // 立刻告知原因（首次），免得玩家以为按钮坏了
        }

        /// <summary>
        /// 每帧推进（由常驻的 `AbHotkeys.OnFrame` 调 —— **必须常驻**：本意图的存在意义
        /// 就是"面板还不存在"，任何挂在面板上的 tick 都跑不到）。
        /// </summary>
        public static void TickPending()
        {
            if (!_pending) return;
            try
            {
                if (_delayFrames > 0) { _delayFrames--; return; }
                if (Time.unscaledTime >= _deadline)
                {
                    // 超时：放弃并**如实说明**（不静默 —— 本工程反复栽在静默跳过上）
                    ModMain.P("[ContactPanelOpener] 打开传音簿重试 " + _tries + " 次仍未成功（原因："
                              + (AbContactPanel.LastFailureReason ?? "未知") + "），放弃本次；玩家可再点一次");
                    HintFailure(true);
                    ClearPending();
                    return;
                }
                // ★ 09-21 修：次数必须**在调 TryOpenNow 之前**存进局部变量。
                //   原代码 `_tries++;` 后直接 `if (TryOpenNow(...)) { P("…第 " + _tries + " 次…") }`，
                //   而 TryOpenNow 成功路径会调 ClearPending() 把 _tries 归零 —— 日志永远打印
                //   "第 0 次尝试成功"，等于这条最有诊断价值的计数被自己抹掉了。
                int attempt = _tries + 1;
                _tries = attempt;
                if (TryOpenNow(toggle: false))     // 兑现"打开"意图 —— 已开则不动，绝不误关
                {
                    ModMain.P("[ContactPanelOpener] 传音簿已打开（第 " + attempt + " 次尝试成功，"
                              + "玩家点的那一下被挂起后自动兑现）");
                    return;                // TryOpenNow 内部已 ClearPending
                }
                HintFailure(false);        // 原因可能变了（如"存档世界尚未加载"→"我方确认窗可见"）
            }
            catch (Exception e)
            {
                ModMain.P("[ContactPanelOpener] TickPending: " + e.Message);
                ClearPending();
            }
        }

        /// <summary>清空待开状态（成功 / 放弃 / 超时 / 玩家手动关门都调）。</summary>
        public static void ClearPending()
        {
            _pending = false;
            _tries = 0;
            _delayFrames = 0;
            _deadline = 0f;
            _hintedReason = null;
        }

        /// <summary>此刻是否有待兑现的打开意图（诊断/测试用）。</summary>
        public static bool HasPendingOpen { get { return _pending; } }

        /// <summary>
        /// 把失败原因**可见地**告诉玩家。
        ///
        /// 载体 = HUD 级横幅 `UnreadBanner.ShowSystem`，不是面板内的状态条 —— **面板此刻还不存在**，
        /// 挂在面板上的任何提示都显示不出来（这正是旧未读横幅那条教训：它是面板子节点，
        /// 面板根 OFF 后显示不出来，所以才搬到 HUD）。
        ///
        /// 同一原因只提示一次（`timeout=true` 时强制再提示一次，因为"放弃了"是新信息）。
        /// 横幅预算 34 字（见 `UnreadBanner.ShowSystem`），故文案必须短。
        /// </summary>
        private static void HintFailure(bool timeout)
        {
            string why = AbContactPanel.LastFailureReason;
            if (string.IsNullOrEmpty(why))
            {
                if (!timeout) return;
                why = "未知原因";
            }
            string key = timeout ? ("timeout:" + why) : why;
            if (!timeout && _hintedReason == key) return;    // 同一原因不重复弹
            _hintedReason = key;
            try
            {
                string hint;
                if (timeout) hint = "打开传音簿失败：" + why + "；可再点一次";
                else if (why == "智能体尚未连接") hint = "正在连接本地智能体，请稍候…";
                else if (why == "存档世界尚未加载") hint = "存档还在加载，请稍候…";
                else if (why == "世界界面尚未就绪") hint = "世界界面正在就绪，请稍候…";
                else if (why == "游戏日历尚未就绪") hint = "世界正在初始化，请稍候…";
                else if (why.StartsWith("剧情窗开着") || why.StartsWith("我方确认窗"))
                    hint = "剧情界面切换中，请稍候…";
                else hint = "暂不能打开传音簿：" + why;
                UnreadBanner.ShowSystem(hint);
            }
            catch (Exception e) { ModMain.P("[ContactPanelOpener] HintFailure: " + e.Message); }
        }
    }
}
