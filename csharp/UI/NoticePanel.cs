/// <summary>
/// 「须知」面板 —— 使用须知 + 更新公告（10-23 用户需求）。
///
/// ## 为什么另建一个面板
/// 玩家装好 mod 后**没有任何地方**告诉他：连不上多半是杀毒把 exe 隔离了、通讯录是 C 键、
/// 填完 Key 必须点「测试连接」、这次更新改了什么。这些原本只散在工坊页与 README 里。
///
/// ## ★ 架构：必须走 `g.ui.OpenUI`，不能自建 Canvas ★（10-23 实机返工，勿回退）
///
/// 第一版是**自建 Canvas**（学 `ToolPanel` / `UnreadBanner`），实机三个症状：
///   ① 面板开着时**鼠标滚轮照样缩放地图**、角色照样能走；
///   ② 按 ESC **同时**关掉面板并弹出游戏自己的菜单；
///   ③ 关掉面板后全屏黑色蒙版还在（蒙版与窗口是兄弟节点，只关了一个）。
/// 根因是同一条：**自建画布不在游戏 UI 管理器的登记表里**，游戏压根不知道"有界面开着"。
/// 而 `AbChatPanel` / `AbConfigPanel` / `AbContactPanel` 走 `g.ui.OpenUI` 登记进 UIMgr，
/// 游戏把它们当模态 —— 世界输入被门控（这是 09-10 事故注释里反复出现的同一句：
/// "宿主根节点 OFF 是防游戏把『UI 层顶部有一个打开中的界面』当成模态"）。
///
/// 所以本面板与那三个**同构**：
///   ① 树**运行时搭**（不出图、不重打 `.ab`，与 `ToolPanel`/`ImageAttachPreview` 同款理由）；
///   ② 搭好的树当"预制体"塞进 `g.res.allRes`（`ModAbRes.PutRuntimePrefab`）；
///   ③ `g.ui.OpenUI(new UITypeBase(Key, (UILayer)0))` 让游戏创建实例 + 挂 Canvas + 排层级；
///   ④ 关闭走 `g.ui.CloseUI(...)`，**绝不自己 SetActive(false)** ——
///      绕过关窗契约会让登记项泄漏（09-10 事故：世界输入被永久门控）。
///
/// 副产物：蒙版（Dim）与窗口共用一个实例，关窗时由管理器整份收走 ⇒ 症状③一并消失。
///
/// ## 三条设计定案
///
/// ★ ① **一个字节的文案都不许写在本文里。** 所有标题/正文来自 `NoticeContentCore`，
///   "该不该弹 / 弹哪页 / 哪条算新"来自 `NoticeCore`，水位落盘在 `NoticeStore`。
///
/// ★ ② **不新增 MonoBehaviour 类型。** `OpenUI` 返回的 `UIBase` 已经挂在实例上，
///   本类直接用它（`AbXxxPanel` 之所以要派生一个 UIBase 子类，是为了接 presenter 与事件；
///   本面板的逻辑全在静态字段里，不需要实例宿主，于是也不必
///   `RegisterTypeInIl2Cpp` —— 而"UIBase 派生类型在初始化早期注册会毒化类型系统"
///   正是本仓最贵的一条教训，能绕就绕）。
///
/// ★ ③ **层顶就是封面。** 面板开着时不许任何东西从底下探出来；`OwnUiProbe` 里排在
///   三个面板**之前**（它是最后开的、玩家眼前那一层）。
///
/// ## 版面（单位：窗口 720×640，左上角为原点向下排）
/// <code>
/// ┌──────────────────────────────────────────────┐
/// │ 须知   v1.0.1                           ✕    │ ← 标题 52
/// │ 使用须知  更新公告                            │ ← 页签 34（选中 = 墨色 + 青瓷下划线）
/// │ ────────────────────────────────────────────  │ ← 发丝线
/// │ ┌ ────────────────────────────────────────┐  │
/// │ │▌ 连不上，先看杀毒隔离区                 │  │ ← 卡片：白底 + 1px 发丝线 + 左侧青瓷竖条
/// │ │  正文…（卡片高度按实测文字算）          │  │
/// │ └─────────────────────────────────────────┘  │
/// │ …（滚动）                                     │
/// │ 上次看过 v1.0.0               有 1 条新公告   │ ← 页脚 40
/// └──────────────────────────────────────────────┘
/// </code>
///
/// ## 调用约定
/// 仅主线程；`AbHotkeys.OnFrame → NoticePanel.Tick()` 推 ESC 与自动弹出；
/// `ModMain.OnIntoWorldEvent → OnIntoWorld + ScheduleAutoShow` 安排首次自动弹出。
/// </summary>
using System;
using UnityEngine;
using UnityEngine.UI;

using static AgentLoopBridge.UiKit;
namespace AgentLoopBridge
{
    /// <summary>实例上那些节点的引用（每次 OpenUI 重建后重新收集）。</summary>
    internal sealed class NoticeRefs
    {
        internal GameObject Root;          // 实例根（UIMgr 挂的 Canvas 在它上面）
        internal GameObject Dim;           // 全屏蒙版（吃点击，防穿透）
        internal RectTransform Window;     // 窗口本体（720×640）
        internal ScrollRect Scroll;
        internal RectTransform Content;    // 卡片容器（VLG + CSF）
        internal Text TabGuideLabel, TabUpdatesLabel;
        internal RectTransform TabGuideLine, TabUpdatesLine;
        internal Text FooterLeft, FooterRight;
    }

    internal static class NoticePanel
    {
        /// <summary>
        /// 资源键（= `UITypeBase` 名）。**与三个 AB 面板同一套命名**（`UI` + 名）。
        /// 这张表里的预制体是**运行时搭出来的**，不是 `.ab` 里的（见文件头 ★ 架构）。
        /// </summary>
        internal const string PrefabKey = "UINoticeAi";

        // ---- 版面常量（改版式必须同步改文件头那张图）----
        private const float PanelW = 720f;
        private const float PanelH = 640f;
        private const float TitleH = 52f;
        private const float TabH = 34f;
        private const float FooterH = 40f;
        private const float PadX = 32f;          // 内容左右留白
        private const float ScrollTop = 96f;     // 滚动区顶 = 标题 52 + 页签 34 + 10

        // ---- 文字规格（字号都是整数：Unity Text 只认 int）----
        private const int FsPanelTitle = 20;
        private const int FsTab = 15;
        private const int FsSectionTitle = 15;
        private const int FsBody = 14;
        private const int FsPill = 12;
        private const int FsFooter = 12;

        // ---- 卡片规格 ----
        private const float CardPad = 16f;
        private const float BarW = 3f;
        private const float BarGap = 12f;
        private const float CardGap = 10f;
        private const float ReleaseGap = 24f;
        private const float KeyCapW = 26f;
        private const float KeyCapH = 24f;
        private const float KeyCapGap = 10f;
        private const float ItemIndent = 20f;    // 公告条目给序号留的宽度（"1." 两位足够）

        // ---- 状态 ----
        private static GameObject _template;      // 运行时模板（塞进 g.res，OpenUI 每次克隆它）
        private static GameObject _instance;      // 当前实例根（CloseUI 后由管理器销毁/隐藏 → fake-null）
        private static NoticeRefs _refs;
        private static int _tab = NoticeCore.TabGuide;
        /// <summary>本次打开时刻的水位快照（`Show` 里在 `MarkSeen` **之前**抓）。
        ///
        /// 为什么不用实时值：「新」标记与页脚"还剩几条"必须**一次打开内保持稳定**。
        /// 实时值会在 `MarkSeen` 之后立刻变成"全都看过" ⇒ 玩家点开更新公告、只看到标题还没读，
        /// 切一下页签再切回来，那些「新」标记就消失了；页脚也会在同一屏里自己闪一下。
        /// 下次打开才该是"都已看过"。</summary>
        private static int _renderSeenSeq;
        /// <summary>同一份快照里的版本号（页脚左侧"上次看过 vX"用）。
        ///
        /// 为什么也要快照：`Show()` 先建页脚、后 `MarkSeen`，所以首屏显示的是旧版本；
        /// 但切一下页签会重建页脚，那时 `Seen` 已是新版本 ⇒ 同一屏内左边从 "v1.0.0" 跳成
        /// "v1.0.1"、右边从"有 1 条新公告"跳成"已是最新"。玩家没做任何事，页脚自己变了。
        /// 两侧必须同源、同快照。</summary>
        private static string _renderSeenVersion;
        private static int _escFrame = -1;
        /// <summary>ESC 请求关闭的帧号（0/-1 = 没有待办）。见 `Tick` 里"推迟一帧"的理由。</summary>
        private static int _closeAtFrame = -1;

        // ---- 自动弹出状态 ----
        private static bool _autoDoneThisRun;    // 每进程只自动弹一次
        private static bool _autoPending;
        private static float _autoAt;
        /// <summary>自动弹出的等待上限（进世界那一刻 +`AutoWaitSeconds`）。超时 = **本次放弃**，
        /// 但不花「每进程一次」的额度（见 `Tick` 里的超时分支）。</summary>
        private static float _autoDeadline;
        /// <summary>一次自动弹出最多等多久世界就绪（秒）。
        ///
        /// 为什么是 300 而不是 60（10-28 实测教训）：`OnIntoWorld` 事件触发得**很早** ——
        /// 真机日志里它到达时 `g.world.playerUnit` 还是 null（闸门原话「存档世界尚未加载」），
        /// 而那次读档从事件到世界可交互整整跨了一分钟以上。60 秒的窗口在这类慢存档上
        /// **必然等不到**，等于自动弹出永远不生效。</summary>
        private const float AutoWaitSeconds = 300f;
        /// <summary>闸门未开时上一行日志的时刻（节流：那份等待里每 5 秒最多一行）。</summary>
        private static float _autoGateAt;
        /// <summary>本轮是否已为"世界未就绪"打过一行（复位点：`ScheduleAutoShow`）。</summary>
        private static bool _autoGateLogged;
        /// <summary>本轮 `ScheduleAutoShow` 的时刻（只用于成功那行里报"等世界就绪等了多久"）。</summary>
        private static float _autoScheduledAt;
        /// <summary>`EnsureResident` 里那条闸门日志的上次时刻（节流用；<=0 = 还没打过）。</summary>
        private static float _residentGateAt = -1f;

        // ------------------------------------------------------------------
        // 对外入口
        // ------------------------------------------------------------------

        /// <summary>
        /// 面板此刻是否真的显示在屏幕上（`OwnUiProbe` / ESC 链唯一判据）。
        /// **不看"宿主引用非空"**——那是本仓踩过的坑：关窗只把 BG 灰掉，引用会一直非空。
        /// </summary>
        internal static bool IsShowing
        {
            get
            {
                try
                {
                    if (_refs == null || _refs.Window == null) return false;
                    return _refs.Window.gameObject.activeInHierarchy;
                }
                catch { return false; }   // GC 僵尸/已 Destroy：判否（宁可漏屏蔽，不可永久屏蔽）
            }
        }

        /// <summary>打开到指定页（默认「使用须知」）。已开着则只切页。</summary>
        internal static void Show(int tab = NoticeCore.TabGuide)
        {
            try
            {
                int want = (tab == NoticeCore.TabUpdates) ? NoticeCore.TabUpdates : NoticeCore.TabGuide;
                if (!EnsureResident()) return;
                var snap = NoticeStore.Seen;             // ★ 必须在 MarkSeen 之前抓（见字段注释）
                _renderSeenSeq = snap.Seq;
                _renderSeenVersion = snap.Version;
                _tab = want;
                var r = _refs;
                if (r == null || r.Window == null) return;
                // ★ 先点亮再建内容 ★：`ForceRebuildLayoutImmediate` 在**未激活**的节点上量到的
                //   高度是 0（ContentSizeFitter 不参与），先建后亮会让首屏文字全挤成一行高。
                if (!r.Window.gameObject.activeSelf) r.Window.gameObject.SetActive(true);
                if (r.Dim != null && !r.Dim.activeSelf) r.Dim.SetActive(true);
                BuildTab(_tab);
                ScrollToTop();
                // ★ 水位在这里推进（唯一调用点）★ 放在"真的显示出来"这一刻，
                //   而不是"点了按钮"那一刻：EnsureResident 失败时不算看过。
                NoticeStore.MarkSeen();
            }
            catch (Exception e) { ModMain.P("[Notice] Show 失败: " + e.Message); }
        }

        /// <summary>
        /// 关闭：**交给游戏管理器**（动画/CloseUIEnd/登记摘除全归它）。
        /// 绝不自己 SetActive(false) —— 那是 09-10 世界输入被永久门控的根因。
        /// 蒙版随实例一起被管理器收走，不会再留一层黑罩。
        /// </summary>
        internal static void Hide()
        {
            try
            {
                // ★ 幂等 ★：现在面板登记在 UIMgr 里，**游戏自己也会关它**（ESC 落到层顶 UI 时
                // 由管理器走 CloseUI）。关掉之后 `_refs`/`_instance` 变 fake-null，
                // 这里若还去 CloseViaManager() 就是"拿已关的 UI 走关闭流程"——本仓的老坑
                // （见 AbChatPanel 注释：窗已经没了，再调一次是拿已关的 UI 去走关闭流程）。
                if (!IsShowing && _instance == null) return;
                CloseViaManager();
            }
            catch (Exception e) { ModMain.P("[Notice] Hide 失败: " + e.Message); }
        }

        internal static void Toggle(int tab = NoticeCore.TabGuide)
        {
            if (IsShowing) { Hide(); return; }
            Show(tab);
        }

        /// <summary>
        /// ESC 归属（`BigPortrait.ConsumesEscape` 同款）：调用方问的是**这一次按键归谁**。
        /// 两条判据合起来才与执行顺序无关：① 我开着 ⇒ 让路；② 本帧已被我关过 ⇒ 也让路
        /// （否则"关须知的同一次 ESC"会顺手把对话窗也关了）。
        /// </summary>
        internal static bool ConsumesEscape()
        {
            if (IsShowing) return true;
            try { return _escFrame == Time.frameCount; } catch { return false; }
        }

        /// <summary>
        /// 安排一次自动弹出（进世界时调）。`delay` 秒后由 `Tick` 弹。
        /// **幂等**：同一局里 `_autoDoneThisRun` 一旦置位就不再安排（反复读档不重复弹）。
        /// </summary>
        internal static void ScheduleAutoShow(float delay = 3f)
        {
            if (_autoDoneThisRun || _autoPending) return;
            if (!NoticeCore.AutoShowAllowed(NoticeStore.NoNoticeFile, NoticeStore.AlwaysFile))
            {
                ModMain.P("[Notice] 不自动弹：诊断哨兵 _diag_no_notice.txt 生效（手动入口照常）");
                return;
            }
            var seen = NoticeStore.Seen;
            bool force = NoticeStore.AlwaysFile;
            int max = NoticeContentCore.MaxSeq;
            if (!NoticeCore.ShouldAutoShow(seen.Seq, max, force))
            {
                // ★ 为什么不弹也留一行（10-28 用户报「版本更新了但没自动弹」）★
                // 这条判据无声退出的样子，和"自动弹出功能整个没接线"完全一样 ——
                // 上一次排查只能靠翻 `notice_seen.json` 里的 seq 反推。
                // 水位是判据的唯一事实，就把它打出来：`已看过最新` 与 `maxSeq 没涨`
                // 两种原因一眼可分（后者 = 内容核忘了加公告条目）。
                ModMain.P("[Notice] 不自动弹：已看过最新（水位=" + seen.Seq + " / 最新=" + max + "）");
                return;
            }
            _autoPending = true;
            _autoScheduledAt = Now();
            _autoAt = Now() + Mathf.Max(0.5f, delay);
            _autoDeadline = Now() + AutoWaitSeconds;
            _autoGateLogged = false;
            ModMain.P("[Notice] 已安排自动弹出：" + Mathf.Max(0.5f, delay).ToString("0.#") + "s 后（水位="
                      + seen.Seq + " / 最新=" + max + "，落页="
                      + (NoticeCore.TabFor(seen.Seq) == NoticeCore.TabUpdates ? "更新公告" : "使用须知") + "）");
            // 诊断开关生效时留一行（否则玩家/我们都没法确认它真的起作用了）
            if (force) ModMain.P("[Notice] 诊断开关 _diag_notice_always.txt 生效：本轮仍会弹出");
        }

        /// <summary>
        /// 每帧（`AbHotkeys.OnFrame` 调；**常驻**，不依赖任何面板的激活状态）：
        /// ① 开着时吃 ESC；② 推进自动弹出的到期判定。
        /// 关着时只有两次 bool 比较 + 一个 `Input.GetKeyDown`，常态零开销。
        /// </summary>
        internal static void Tick()
        {
            try
            {
                if (IsShowing)
                {
                    // ★ ESC 不立刻关，推迟一帧 ★ （10-23）
                    //
                    // 游戏自己也会读 ESC。谁先读到取决于 Update 顺序，而我们控制不了它。
                    // 若我们先关：面板一关，游戏那边的"层顶还有没有 UI"就到手为空 ⇒
                    // 同一次 ESC 顺手弹出游戏菜单（用户报的第二个 bug）。
                    // 推迟一帧则两种顺序都安全：本帧面板始终**还开着**，游戏无论何时来看
                    // 都看到"层顶有界面"，于是把这一击让给我们；下一帧 `GetKeyDown` 已为 false，
                    // 谁也不会再动手。一帧的延迟玩家感知不到。
                    if (Input.GetKeyDown(KeyCode.Escape))
                    {
                        // 帧号记在**检测到这一击**的帧上 —— 那才是"有人来问这一次 ESC 归谁"的帧。
                        // （记在下一帧没用：那时 GetKeyDown 已是 false，没人再来问，字段等于死的。）
                        if (_closeAtFrame < 0) _escFrame = Time.frameCount;
                        _closeAtFrame = Time.frameCount + 1;
                    }
                    if (_closeAtFrame > 0 && Time.frameCount >= _closeAtFrame)
                    {
                        _closeAtFrame = -1;
                        _autoDeadline = 0f;
                        Hide();
                    }
                    return;   // 开着时不做自动弹出判定（已经开着，没什么可弹）
                }
                _closeAtFrame = -1;
                if (!_autoPending) return;
                if (Now() < _autoAt) return;
                // 忙就不打扰：每 0.5s 重试（战斗/剧情/确认窗期间绝不弹）
                string busy = "";
                try { busy = NpcInitiativeMonitor.BusyReasonText() ?? ""; } catch { }
                if (busy.Length > 0) { _autoAt = Now() + 0.5f; return; }
                // ★ 顺序是命门（10-23 复查修）★
                //   `_autoDoneThisRun` 必须**在 Show 成功之后**才置位。
                //   旧写法先置位再 Show，而 Show → EnsureResident 可能因
                //   `GateReason()`（游戏日历/存档世界/世界界面未就绪）直接 return false
                //   —— 那一刻面板根本没出现，但"每进程一次"的额度已经花光，
                //   于是**首次玩家永远看不到公告**，且日志里连一句失败都没有。
                //   现在：Show 返回后按 `IsShowing` 判定，没弹出来就留着重试。
                int wantTab = NoticeCore.TabFor(NoticeStore.Seen.Seq);
                Show(wantTab);
                if (IsShowing)
                {
                    _autoPending = false;
                    _autoDoneThisRun = true;
                    // 成功这一行同样必须留（10-28）：与 `已安排` / `待命` / `超时` 合起来，
                    // 「这次进世界到底弹没弹」永远能在日志里被单独回答一次。
                    ModMain.P("[Notice] 自动弹出成功（页=" + (wantTab == NoticeCore.TabUpdates ? "更新公告" : "使用须知")
                              + "，等世界就绪耗时 " + (Now() - _autoScheduledAt).ToString("0.0") + "s）");
                }
                else
                {
                    // 没弹出来（世界还没就绪）⇒ 半秒后再试，**不消耗本次额度**。
                    // 上限靠 `_autoDeadline`：进世界后 `AutoWaitSeconds` 秒还没成功就**本次**放弃，
                    // 免得在某个永远不就绪的场景里每 0.5s 空转一次（虽然成本极低）。
                    //
                    // ★ 超时**不置 `_autoDoneThisRun`**（10-28 修）★
                    //   旧写法把超时与"弹成功"当作同一件事，都花掉"每进程一次"的额度。
                    //   真机实证（10-23 15:47 那次）：进世界后世界界面整整 60 秒没就绪
                    //   （`当前不宜创建（存档世界尚未加载）` 连打 80 行），于是一次超时
                    //   ⇒ 本进程后面**再进世界也不弹了** ⇒ 玩家看到的就是"更新了但没弹"。
                    //   正确语义：额度只该被"真的弹出来"花掉；没就绪就等下一次进世界再来。
                    bool clockOk = _autoDeadline > 0f && Now() > 0f;
                    if (clockOk && Now() > _autoDeadline)
                    {
                        _autoPending = false;
                        ModMain.P("[Notice] 自动弹出等待超时（" + AutoWaitSeconds.ToString("0")
                                  + "s 内世界界面始终未就绪），本次放弃 —— 不消耗额度，下次进世界重试");
                    }
                    else
                    {
                        // 等世界就绪期间**每 5 秒留一行**（其余重试静默）。
                        //
                        // 为什么值得这几行：这条路径唯一的失败形态就是"世界里 60 秒都没就绪"，
                        // 而那时日志里只有 `Show` 内部那句闸门原因（且每条面板路径都在打，
                        // 混在一起看不出是谁在等）。有了这一行，`已安排自动弹出` 之后**必须**
                        // 出现 `已弹出` 或 `超时`，中间不会是一片空白 —— 空白即故障。
                        float t = Now();
                        if (!_autoGateLogged || t - _autoGateAt > 5f)
                        {
                            _autoGateLogged = true;
                            _autoGateAt = t;
                            string why = "";
                            try { why = AbPanelProber.GateReason() ?? ""; } catch { }
                            ModMain.P("[Notice] 自动弹出待命：世界尚未就绪（"
                                      + (why.Length > 0 ? why : "闸门未给原因")
                                      + "），继续等待，最多 " + AutoWaitSeconds.ToString("0") + "s");
                        }
                        _autoAt = Now() + 0.5f;
                    }
                }
            }
            catch (Exception e) { ModMain.P("[Notice] Tick 失败: " + e.Message); _autoPending = false; }
        }

        /// <summary>世界重载（读档 / 进世界）时复位"本次进入"相关位；`_autoDoneThisRun` 刻意**不**复位
        /// （每进程一次）。见 `ModMain.OnIntoWorldEvent` 的调用。</summary>
        internal static void OnIntoWorld()
        {
            _autoPending = false;
            _renderSeenSeq = 0;
            _renderSeenVersion = null;
            _closeAtFrame = -1;
            // 实例随场景销毁 ⇒ 引用变僵尸。Scene 没了时 UIMgr 也会清登记，
            // 这里只丢引用，下一次 Show 会重建（**不要**去 CloseUI 一个已经不在的实例）。
            _instance = null;
            _refs = null;
            _template = null;     // 模板挂在场景里的话也一起没了；下次重建
        }

        private static float Now()
        {
            try { return Time.unscaledTime; } catch { return 0f; }
        }

        // ------------------------------------------------------------------
        // 宿主：走 g.ui.OpenUI（与三个 AB 面板同构）
        // ------------------------------------------------------------------

        /// <summary>
        /// 确保实例在（UIMgr 登记着、树已装配）。已就绪直接 true。
        /// 失败只记一行日志并返回 false —— 调用方（按钮/自动弹出）都不该因此崩。
        /// </summary>
        private static bool EnsureResident()
        {
            try
            {
                if (DiagSwitches.NoPanels) return false;
                // ① 复用优先：管理器 CloseUI 不一定销毁实例 —— 查到就复用，**绝不重复 OpenUI**
                //   （重复 OpenUI 会撞登记表 → 返回 null）。
                try
                {
                    var exist = g.ui.GetUI(new UIType.UITypeBase(PrefabKey, (UILayer)0));
                    if (exist != null)
                    {
                        var go0 = exist.gameObject;
                        // 同一个实例且节点都在 ⇒ 直接复用。**必须连 Window 一起验**：
                        // 只比 Root 的话，实例被管理器保活但子节点被销毁时这里会 return true，
                        // 而 Show 拿不到 Window 就静默返回 —— 症状是"点按钮没反应"，日志干干净净。
                        if (go0 != null && _refs != null && _refs.Root == go0 && _refs.Window != null)
                        {
                            BindClicks(go0);   // 幂等：`ClickUtils.Attach` 是先清空再挂
                            return true;
                        }
                        if (go0 != null) { _instance = go0; CollectRefs(go0); BindClicks(go0); return _refs != null; }
                    }
                }
                catch { }
                // ② 世界未就绪时绝不调 OpenUI（铁律，见 AbPanelProber 文件头）
                string gateWhy = AbPanelProber.GateReason();
                if (gateWhy.Length > 0)
                {
                    // 节流 5 秒一行（10-28）：自动弹出每 0.5s 重试一次，而 `GateReason` 在
                    // 世界加载期能连续失败一分钟 —— 真机日志里这条曾连打 80 行，
                    // 把真正有用的那几行（`已安排` / `超时`）挤得找不着。
                    float now = Now();
                    if (now - _residentGateAt > 5f || _residentGateAt <= 0f)
                    {
                        _residentGateAt = now;
                        ModMain.P("[Notice] 当前不宜创建（" + gateWhy + "），本次跳过");
                    }
                    return false;
                }
                // ③ 模板：第一次用才搭（搭一次够，之后一直躺在资源表里被克隆）
                if (_template == null) _template = BuildTemplate();
                if (_template == null) { ModMain.P("[Notice] 模板搭建失败"); return false; }
                if (!ModAbRes.PutRuntimePrefab(PrefabKey, _template))
                { ModMain.P("[Notice] 模板注入 g.res 失败"); return false; }
                // ④ 交给游戏创建（挂 Canvas / 排层级 / 登记"打开中"）
                var ui = g.ui.OpenUI(new UIType.UITypeBase(PrefabKey, (UILayer)0));
                if (ui == null) { ModMain.P("[Notice] OpenUI 返回 null（登记冲突？）"); return false; }
                var go = ui.gameObject;
                if (go == null) { ModMain.P("[Notice] OpenUI 实例为空"); return false; }
                go.name = PrefabKey;
                UiKit.NormalizeLayout(go, "Notice");        // UIMgr 会把实例置 scale=0 / rect=0x0（真机 dump 实证）
                _instance = go;
                CollectRefs(go);
                if (_refs == null || _refs.Window == null)
                {
                    ModMain.P("[Notice] 实例节点缺失（模板与实例结构不一致？），关闭本次实例");
                    CloseViaManager();
                    return false;
                }
                BindClicks(go);
                return true;
            }
            catch (Exception e)
            {
                ModMain.P("[Notice] EnsureResident 异常: " + e);
                return false;
            }
        }

        /// <summary>方案 A 契约：关闭交管理器，清本地引用（绝不 Destroy / SetActive(false)）。</summary>
        private static void CloseViaManager()
        {
            try { g.ui.CloseUI(new UIType.UITypeBase(PrefabKey, (UILayer)0), false); }
            catch (Exception e) { ModMain.P("[Notice] CloseUI: " + e.Message); }
            _instance = null;
            _refs = null;
        }

        /// <summary>
        /// 布局归一化（`AbChatPanel`/`AbConfigPanel` 同款）：`UIMgr.CreateUI` 会把实例
        /// RectTransform 置零（scale=0、rect=0x0）——scale 停 0 就完全不可见。
        /// 直接设 scale=1 + 全屏 stretch，窗口是它的子节点、居中定尺寸，不受影响。
        /// </summary>

        /// <summary>从实例上收节点引用（按名字 Find，与预制件时代 `CollectRefs` 同形）。</summary>
        private static void CollectRefs(GameObject root)
        {
            _refs = null;
            try
            {
                var r = new NoticeRefs();
                r.Root = root;
                var t = root.transform;
                r.Dim = FindGo(t, "Dim");
                var win = FindRt(t, "Window");
                if (win == null) { _refs = null; return; }
                r.Window = win;
                // 同样不走 GetComponentInChildren：`includeInactive` 这条重载在 il2cpp 下行为不稳，
                // 反正 Scroll 节点就在固定路径上，直接取。
                var scrollGo = FindByPath(win, "Scroll");
                r.Scroll = scrollGo != null ? scrollGo.GetComponent<ScrollRect>() : null;
                var content = FindRt(win, "Scroll/Viewport/Content");
                r.Content = content;
                // 页签文字就挂在 "TabGuide"/"TabUpdates" 节点本身上（`MakeText` 不套 Label 子节点），
                // 与 "TabGuideHit"（那层透明点击区）是两个节点，别写成 "TabGuide/Label"。
                r.TabGuideLabel = FindComp<Text>(win, "TabGuide");
                r.TabUpdatesLabel = FindComp<Text>(win, "TabUpdates");
                r.TabGuideLine = FindRt(win, "TabGuideLine");
                r.TabUpdatesLine = FindRt(win, "TabUpdatesLine");
                r.FooterLeft = FindComp<Text>(win, "FooterLeft");
                r.FooterRight = FindComp<Text>(win, "FooterRight");
                _refs = r;
            }
            catch (Exception e) { ModMain.P("[Notice] CollectRefs: " + e.Message); _refs = null; }
        }

        /// <summary>
        /// ★ 在**实例**上绑点击（10-23 第二次返工，勿回退）★
        ///
        /// `g.ui.OpenUI` 内部是 `Instantiate(模板)`，而 `Instantiate` **不会复制运行期挂上去的
        /// `Button.onClick` 监听**（`UnityEvent` 的监听列表不是序列化字段，克隆时不走）。
        /// 所以"在模板里绑好、指望克隆出来还带着"这条路走不通：
        /// 实例建出来一切正常、按钮却**完全没反应**，且日志干净 —— 用户报的"更新公告点不动"。
        ///
        /// 规矩：**模板只管外观，回调一律在实例上绑**（与 `AbChatPanel` 装配完再 `ClickUtils.Attach`
        /// 的做法一致）。本方法必须**幂等**：复用分支每次打开都会再调一次，
        /// 而 `ClickUtils.Attach` 自身是"先 `RemoveAllListeners` 再挂"，重复调用安全。
        /// </summary>
        private static void BindClicks(GameObject root)
        {
            try
            {
                if (root == null) return;
                var t = root.transform;

                var closeT = FindByPath(t, "Window/CloseBtn");
                if (closeT != null)
                {
                    var btn = closeT.GetComponent<Button>();
                    if (btn != null) ClickUtils.Attach(btn, Hide);
                }

                BindTabClick(t, "Window/TabGuideHit", NoticeCore.TabGuide);
                BindTabClick(t, "Window/TabUpdatesHit", NoticeCore.TabUpdates);
            }
            catch (Exception e) { ModMain.P("[Notice] BindClicks 失败: " + e.Message); }
        }

        private static void BindTabClick(Transform root, string path, int tab)
        {
            var node = FindByPath(root, path);
            if (node == null)
            {
                // 只报路径，不抛：少一个页签按钮不该让整个面板不可用
                ModMain.P("[Notice] 点击区缺失: " + path);
                return;
            }
            var btn = node.GetComponent<Button>();
            if (btn == null) { ModMain.P("[Notice] 点击区无 Button: " + path); return; }
            ClickUtils.Attach(btn, () => SwitchTab(tab));
        }

        /// <summary>
        /// 按路径逐段查找 —— **绝不能用 `Transform.Find`**（10-23 实机事故，勿回退）。
        ///
        /// ★ `Transform.Find` 只找"激活路径上"的节点 ★：路径上任何一段 `activeSelf == false`，
        ///   它就直接返回 null，**不报错、不打日志**。而我们的模板里 `Dim`/`Window` 出厂是关的
        ///   （模板躺在资源表里不该渲染）⇒ `root.Find("Window")` 恒为 null
        ///   ⇒ `CollectRefs` 判定"实例节点缺失" ⇒ 每次点「须知」都是
        ///   `OpenUI` 成功、随即被关掉，玩家看到的就是"点了没有任何反应"。
        ///   实机日志形状（就此定案）：
        ///     `[Notice] g.ui.OpenUI("UINoticeAi") 创建宿主...`
        ///     `[Notice] 实例节点缺失，关闭本次实例`      ← 无限循环
        ///
        /// 这里改成遍历 `childCount` 自己比名字：**与 active 状态完全无关**。
        /// （该实现已收进 `UI/UiKit.cs` 的 `FindByPath`，全仓唯一一份。）
        /// </summary>

        // ------------------------------------------------------------------
        // 模板：整棵树在运行时搭（不出图、不重打 .ab）
        // ------------------------------------------------------------------

        /// <summary>
        /// 搭一份**模板**（不是实例）：一次性产出，之后每次 `OpenUI` 由游戏克隆它。
        ///
        /// 硬约束（`ModAbRes.Inject` 的 `stripCanvas` 同一条）：
        ///   **不许带 Canvas / CanvasScaler / GraphicRaycaster** —— 那三件套由 `UIMgr.CreateUI`
        ///   自己挂，预制体自带会撞 "Can't add component ... already added" → OpenUI 内部 NRE（真机实证）。
        /// 模板建成后保持 inactive：它躺在资源表里，不该在场景里渲染。
        /// </summary>
        private static GameObject BuildTemplate()
        {
            try
            {
                var root = new GameObject(PrefabKey);
                var rrt = root.AddComponent<RectTransform>();
                rrt.sizeDelta = new Vector2(PanelW, PanelH);   // 有值即可；实例的尺寸由归一化接管

                // ① 全屏蒙版：吃掉落到面板外的点击（模态），并压暗世界
                var dim = MakeRect("Dim", root.transform);
                Stretch(dim);
                dim.gameObject.AddComponent<CanvasRenderer>();
                var dimImg = dim.gameObject.AddComponent<Image>();
                dimImg.sprite = ChatUiBuilder.MakeSlicedSprite(new Color(0f, 0f, 0f, 0.45f), 8, 2);
                dimImg.type = Image.Type.Sliced;
                dimImg.color = Color.white;
                dimImg.raycastTarget = true;

                // ② 窗口（720×640 居中）
                var win = MakeRect("Window", root.transform);
                win.anchorMin = win.anchorMax = win.pivot = new Vector2(0.5f, 0.5f);
                win.sizeDelta = new Vector2(PanelW, PanelH);
                win.anchoredPosition = Vector2.zero;
                win.gameObject.AddComponent<CanvasRenderer>();
                var winImg = win.gameObject.AddComponent<Image>();
                var paper = TryLoadPaperSprite();
                if (paper != null)
                {
                    winImg.sprite = paper;
                    winImg.type = Image.Type.Simple;    // 满幅贴：这张图是"整张纸"，不能九宫格拉伸
                    winImg.color = Color.white;
                }
                else
                {
                    winImg.sprite = ChatUiBuilder.MakeSlicedSprite(Theme.PaperPageBg, 16, 6);
                    winImg.type = Image.Type.Sliced;
                    winImg.color = Color.white;
                    ModMain.P("[Notice] 底图缺失（art bundle 未载到）→ 退纯色宣纸，功能不受影响");
                }
                winImg.raycastTarget = true;      // 窗口自己吃掉点击

                BuildTitleBar(win);
                BuildTabs(win);
                BuildScroll(win);
                BuildFooter(win);

                // ★ layer 必须**最后**统一刷 ★：`new GameObject()` 的 layer 是 0(Default)，
                //   **不会继承父节点**；而游戏 UI 相机只渲染 UI 层(5)。
                //   在建 Dim/Window 之前刷 ⇒ 所有子节点仍是 0 ⇒ 面板整屏不可见、且不报任何错。
                int layer = LayerMask.NameToLayer("UI");
                if (layer >= 0) SetLayerRecursive(root, layer);

                // ★ 模板根**保持激活**，只把 Dim/Window 关掉 ★
                //   为什么不能关根：`new GameObject()` 建出来是**真场景对象**（不是 prefab 资产），
                //   它躺在 g.res.allRes 里等着被 Instantiate。根关掉的话，克隆出来的实例也是关的，
                //   而 `Show()` 只点亮 Window ⇒ `activeInHierarchy` 仍为 false ⇒ 面板永远不显示。
                //   关掉两个子节点就够了：根上没有 Graphic，不渲染任何东西。
                //   （这也正好与三个 AB 面板同形：预制体根是活的，BG 出厂是关的。）
                dim.gameObject.SetActive(false);
                win.gameObject.SetActive(false);
                UiKit.AuditSoon(UiRects.Of(root.transform), "Notice");
                return root;
            }
            catch (Exception e)
            {
                ModMain.P("[Notice] BuildTemplate 失败: " + e.Message);
                return null;
            }
        }

        /// <summary>
        /// 从 art bundle 里取宣纸底图。**失败返回 null**（纯色兜底，绝不抛）。
        /// bundle 名 = `panel_bg.png.png`（`ModAbRes` 已把 `ModRes/AssetBundle/**` 全部直载并
        /// 按文件名小写入表），取其第一张 Sprite。
        /// </summary>
        private static Sprite TryLoadPaperSprite()
        {
            try { return ModAbRes.LoadFirstSprite("panel_bg.png.png"); }
            catch (Exception e) { ModMain.P("[Notice] 取底图失败: " + e.Message); return null; }
        }

        private static void BuildTitleBar(RectTransform win)
        {
            var title = MakeText("TitleLabel", win, "须知", FsPanelTitle, Theme.PaperInk,
                                 TextAnchor.MiddleLeft, FontStyle.Bold);
            var trt = title.rectTransform;
            trt.anchorMin = new Vector2(0f, 1f); trt.anchorMax = new Vector2(0f, 1f);
            trt.pivot = new Vector2(0f, 1f);
            trt.sizeDelta = new Vector2(120f, TitleH);
            trt.anchoredPosition = new Vector2(PadX, 0f);

            var ver = MakeText("VersionLabel", win, "v" + NoticeContentCore.LatestVersion, FsPill,
                               Theme.PaperInkDim, TextAnchor.MiddleLeft, FontStyle.Normal);
            var vrt = ver.rectTransform;
            vrt.anchorMin = new Vector2(0f, 1f); vrt.anchorMax = new Vector2(0f, 1f);
            vrt.pivot = new Vector2(0f, 1f);
            vrt.sizeDelta = new Vector2(120f, TitleH);
            vrt.anchoredPosition = new Vector2(PadX + 58f, 0f);

            // ✕ 的底同样是 α=1/255（不是 0）：见 MakeTab 里"点不动"的注释
            var closeBg = new Color(0f, 0f, 0f, 1f / 255f);
            var close = NewButton("CloseBtn", win, "✕", 16, Theme.PaperInkDim, closeBg);
            var crt = close.GetComponent<RectTransform>();
            crt.anchorMin = crt.anchorMax = new Vector2(1f, 1f);
            crt.pivot = new Vector2(1f, 1f);
            crt.sizeDelta = new Vector2(44f, TitleH);
            crt.anchoredPosition = new Vector2(-8f, 0f);
            // ★ 这里**不绑** onClick ★：模板只是"外观样板"，`OpenUI` 克隆出来的实例
            //   不会带运行期挂的 UnityEvent 监听。绑定统一在 `BindClicks`（实例上）做。
        }

        private static void BuildTabs(RectTransform win)
        {
            MakeTab(win, NoticeCore.TabGuide, NoticeContentCore.GuideTitle, PadX, "TabGuide", "TabGuideLine");
            MakeTab(win, NoticeCore.TabUpdates, NoticeContentCore.UpdatesTitle, PadX + 108f,
                    "TabUpdates", "TabUpdatesLine");

            var line = MakeRect("TabLine", win);
            line.anchorMin = new Vector2(0f, 1f); line.anchorMax = new Vector2(1f, 1f);
            line.pivot = new Vector2(0.5f, 1f);
            line.sizeDelta = new Vector2(-PadX * 2f, 1f);
            line.anchoredPosition = new Vector2(0f, -(TitleH + TabH));
            line.gameObject.AddComponent<CanvasRenderer>();
            var img = line.gameObject.AddComponent<Image>();
            img.color = Theme.PaperHairline;
            img.raycastTarget = false;
        }

        private static Text MakeTab(RectTransform win, int tab, string label, float x,
                                    string nodeName, string lineName)
        {
            var t = MakeText(nodeName, win, label, FsTab, Theme.PaperInk,
                             TextAnchor.MiddleLeft, FontStyle.Bold);
            var rt = t.rectTransform;
            rt.anchorMin = new Vector2(0f, 1f); rt.anchorMax = new Vector2(0f, 1f);
            rt.pivot = new Vector2(0f, 1f);
            rt.sizeDelta = new Vector2(96f, TabH);
            rt.anchoredPosition = new Vector2(x, -TitleH);

            // 点击区：文字本身不吃射线（见 MakeText），故加一层"几乎透明"的按钮盖住它。
            //
            // ★ 底色**必须是 α=1/255，绝不能是 Color.clear(α=0)** ★（10-23 用户报"更新公告点不动"，就是这条）
            //   `alpha=0` 的 Graphic 会被 canvas 判为剔除（`CanvasRenderer.cull = true`），
            //   而 `GraphicRaycaster` **会跳过被剔除的图形** ⇒ 这层既看不见、也点不到。
            //   与 `ToolPanel:342`（滚动命中区）、`BigPortrait.PickTint`（模态拦截层）同一处理。
            //   1/255 在屏幕上肉眼不可见，但足以让 raycaster 认它。
            var hitColor = new Color(0f, 0f, 0f, 1f / 255f);
            var hit = NewButton(nodeName + "Hit", win, "", FsTab, hitColor, hitColor);
            var hrt = hit.GetComponent<RectTransform>();
            hrt.anchorMin = new Vector2(0f, 1f); hrt.anchorMax = new Vector2(0f, 1f);
            hrt.pivot = new Vector2(0f, 1f);
            // 点击区比文字**宽一点**（96 → 104）且左移 4px：玩家不会精确点中字，
            // 点在两页之间的空隙里没反应会被当成"按钮坏了"。
            // 高度用满整条页签带（TabH），上下不留缝。
            hrt.sizeDelta = new Vector2(104f, TabH);
            hrt.anchoredPosition = new Vector2(x - 4f, -TitleH);
            // 同上：不在此绑 onClick，交给实例侧的 `BindClicks`

            var u = MakeRect(lineName, win);
            u.anchorMin = new Vector2(0f, 1f); u.anchorMax = new Vector2(0f, 1f);
            u.pivot = new Vector2(0f, 1f);
            u.sizeDelta = new Vector2(56f, 3f);
            u.anchoredPosition = new Vector2(x, -(TitleH + TabH - 3f));
            u.gameObject.AddComponent<CanvasRenderer>();
            var ui = u.gameObject.AddComponent<Image>();
            ui.color = Theme.PaperAccent;
            ui.raycastTarget = false;
            return t;
        }

        private static void BuildScroll(RectTransform win)
        {
            var scrollRt = MakeRect("Scroll", win);
            scrollRt.anchorMin = new Vector2(0f, 0f);
            scrollRt.anchorMax = new Vector2(1f, 1f);
            scrollRt.pivot = new Vector2(0.5f, 0.5f);
            // 底部比页脚多让 8px：文字如果正好贴到页脚顶边，看着像"被页脚压住"
            scrollRt.offsetMin = new Vector2(PadX, FooterH + 8f);
            scrollRt.offsetMax = new Vector2(-PadX, -ScrollTop);

            // 滚动区必须自带一张**可命中的 Graphic**，否则滚轮/拖动一点进不来
            //（`ToolPanel`/`AbConfigPanel.EnsureScrollInputTarget` 同一个坑）。
            scrollRt.gameObject.AddComponent<CanvasRenderer>();
            var hit = scrollRt.gameObject.AddComponent<Image>();
            hit.color = new Color(0f, 0f, 0f, 1f / 255f);   // 不写 0：α=0 会被当"不渲染"剔除
            hit.raycastTarget = true;

            var scroll = scrollRt.gameObject.AddComponent<ScrollRect>();
            scroll.horizontal = false;
            scroll.vertical = true;
            scroll.movementType = ScrollRect.MovementType.Clamped;
            scroll.scrollSensitivity = 40f;
            scroll.inertia = true;

            var viewport = MakeRect("Viewport", scrollRt);
            Stretch(viewport);
            viewport.gameObject.AddComponent<CanvasRenderer>();
            viewport.gameObject.AddComponent<RectMask2D>();
            scroll.viewport = viewport;

            var content = MakeRect("Content", viewport);
            content.anchorMin = new Vector2(0f, 1f);
            content.anchorMax = new Vector2(1f, 1f);
            content.pivot = new Vector2(0.5f, 1f);
            content.sizeDelta = Vector2.zero;
            content.anchoredPosition = Vector2.zero;
            var vlg = content.gameObject.AddComponent<VerticalLayoutGroup>();
            vlg.spacing = CardGap;
            vlg.padding = new RectOffset(0, 0, 2, 10);
            vlg.childControlWidth = true;
            vlg.childControlHeight = true;
            vlg.childForceExpandWidth = true;
            vlg.childForceExpandHeight = false;
            var csf = content.gameObject.AddComponent<ContentSizeFitter>();
            csf.verticalFit = ContentSizeFitter.FitMode.PreferredSize;
            csf.horizontalFit = ContentSizeFitter.FitMode.Unconstrained;
            scroll.content = content;
        }

        private static void BuildFooter(RectTransform win)
        {
            var left = MakeText("FooterLeft", win, "", FsFooter, Theme.PaperInkDim,
                                TextAnchor.MiddleLeft, FontStyle.Normal);
            var lrt = left.rectTransform;
            lrt.anchorMin = new Vector2(0f, 0f); lrt.anchorMax = new Vector2(0.5f, 0f);
            lrt.pivot = new Vector2(0f, 0f);
            lrt.offsetMin = new Vector2(PadX, 8f);
            lrt.offsetMax = new Vector2(0f, FooterH - 8f);

            var right = MakeText("FooterRight", win, "", FsFooter, Theme.PaperInkDim,
                                 TextAnchor.MiddleRight, FontStyle.Normal);
            var rrt = right.rectTransform;
            rrt.anchorMin = new Vector2(0.5f, 0f); rrt.anchorMax = new Vector2(1f, 0f);
            rrt.pivot = new Vector2(1f, 0f);
            rrt.offsetMin = new Vector2(0f, 8f);
            rrt.offsetMax = new Vector2(-PadX, FooterH - 8f);
        }

        // ------------------------------------------------------------------
        // 内容（页切换 = 整块重建；两张表都只有十来张卡，重建成本可忽略）
        // ------------------------------------------------------------------

        private static void SwitchTab(int tab)
        {
            if (_tab == tab) return;
            _tab = tab;
            BuildTab(tab);
            ScrollToTop();
        }

        private static void ScrollToTop()
        {
            try
            {
                if (_refs == null || _refs.Scroll == null) return;
                _refs.Scroll.verticalNormalizedPosition = 1f;
                // 内容刚重建时 ContentSizeFitter 还没算完，直接置 1 会被下一帧归位覆盖 →
                // 强制重建一次布局再置（与 `ConfigPresenter` 切页回顶同一个手法）。
                LayoutRebuilder.ForceRebuildLayoutImmediate(_refs.Content);
                _refs.Scroll.verticalNormalizedPosition = 1f;
            }
            catch { }
        }

        private static void BuildTab(int tab)
        {
            UpdateTabVisual(tab);
            UpdateFooter();
            var r = _refs;
            if (r == null || r.Content == null) return;
            ClearContent(r.Content);
            try
            {
                if (tab == NoticeCore.TabUpdates) BuildReleases(r.Content);
                else BuildGuide(r.Content);
                LayoutRebuilder.ForceRebuildLayoutImmediate(r.Content);
            }
            catch (Exception e) { ModMain.P("[Notice] BuildTab 失败: " + e.Message); }
        }

        private static void UpdateTabVisual(int tab)
        {
            var r = _refs;
            if (r == null) return;
            bool guide = tab == NoticeCore.TabGuide;
            if (r.TabGuideLabel != null) r.TabGuideLabel.color = guide ? Theme.PaperInk : Theme.PaperInkDim;
            if (r.TabUpdatesLabel != null) r.TabUpdatesLabel.color = guide ? Theme.PaperInkDim : Theme.PaperInk;
            if (r.TabGuideLine != null) r.TabGuideLine.gameObject.SetActive(guide);
            if (r.TabUpdatesLine != null) r.TabUpdatesLine.gameObject.SetActive(!guide);
        }

        private static void UpdateFooter()
        {
            var r = _refs;
            if (r == null) return;
            // 两侧都用**本次打开的快照**（理由见 _renderSeenVersion 字段注释）：
            // 左侧 = 打开之前看过哪个版本，右侧 = 打开之前还差几条。
            // 不用实时值：MarkSeen 之后实时值立刻变成"全都看过"，同一屏内页脚会自己变。
            if (r.FooterLeft != null)
                r.FooterLeft.text = NoticeCore.FooterSeen(_renderSeenSeq, _renderSeenVersion);
            if (r.FooterRight != null)
                r.FooterRight.text = NoticeCore.FooterUnread(_renderSeenSeq, NoticeContentCore.MaxSeq);
        }

        private static void ClearContent(RectTransform content)
        {
            try
            {
                for (int i = content.childCount - 1; i >= 0; i--)
                {
                    var c = content.GetChild(i);
                    if (c != null) UnityEngine.Object.Destroy(c.gameObject);
                }
                // VLG/CSF 会被 Destroy 延迟到帧末 → 先把布局位复位，免得这一帧算出一个坏高度
                LayoutRebuilder.MarkLayoutForRebuild(content);
            }
            catch { }
        }

        /// <summary>内容宽度（= 卡片宽度）。视口宽 − 两侧留白，固定值；文字测量靠它。</summary>
        private static float ContentW { get { return PanelW - PadX * 2f; } }

        private static void BuildGuide(Transform parent)
        {
            float w = ContentW;
            for (int i = 0; i < NoticeContentCore.Guide.Length; i++)
                MakeSectionCard(parent, NoticeContentCore.Guide[i], w);
        }

        private static void BuildReleases(Transform parent)
        {
            float w = ContentW;
            int seen = _renderSeenSeq;       // 快照，不是实时值（见 _renderSeenSeq 注释）
            for (int i = 0; i < NoticeContentCore.Releases.Length; i++)
            {
                var rel = NoticeContentCore.Releases[i];
                if (i > 0) AddSpacer(parent, ReleaseGap - CardGap);   // VLG 已给了 CardGap
                MakeReleaseHeader(parent, rel, w, NoticeCore.IsNew(rel.Seq, seen));
                for (int j = 0; j < rel.Items.Length; j++)
                    MakeItemCard(parent, j + 1, rel.Items[j], w);
            }
        }

        // ------------------------------------------------------------------
        // 卡片（高度按**实测文字**算：`Text.preferredHeight` 在宽度确定后就是准的）
        // ------------------------------------------------------------------

        /// <summary>「使用须知」的一节：青瓷竖条 + 标题（可带键帽）+ 正文。</summary>
        private static void MakeSectionCard(Transform parent, NoticeSection sec, float w)
        {
            float textW = w - CardPad * 2f - BarW - BarGap;
            bool hasCap = !string.IsNullOrEmpty(sec.KeyCap);
            float titleW = textW - (hasCap ? KeyCapW + KeyCapGap : 0f);

            var title = MakeText("Title", parent, sec.Title ?? "", FsSectionTitle, Theme.PaperInk,
                                 TextAnchor.UpperLeft, FontStyle.Bold);
            var trt = title.rectTransform;
            trt.sizeDelta = new Vector2(Mathf.Max(60f, titleW), 0f);
            float titleH = Mathf.Max(KeyCapH, title.preferredHeight);

            var body = MakeBodyText(parent, sec.Lines);
            var brt = body.rectTransform;
            brt.sizeDelta = new Vector2(textW, 0f);
            float bodyH = body.preferredHeight;

            float h = CardPad * 2f + titleH + 6f + bodyH;
            MakeCardShell(parent, w, h, out var inner);
            title.transform.SetParent(inner, false);
            Layout(trt, CardPad + BarW + BarGap + (hasCap ? KeyCapW + KeyCapGap : 0f), -CardPad,
                   Mathf.Max(60f, titleW), titleH);
            if (hasCap) MakeKeyCap(inner, sec.KeyCap, CardPad + BarW + BarGap, -CardPad, titleH);
            body.transform.SetParent(inner, false);
            Layout(brt, CardPad + BarW + BarGap, -(CardPad + titleH + 6f), textW, bodyH);
        }

        /// <summary>
        /// 「更新公告」的一条：`1. 正文`（编号与正文同为一行，工坊更新日志的写法）。
        ///
        /// 为什么不再给每条一个小标题：工坊那边就是"编号 + 一句话"，玩家读公告是**扫**不是读。
        /// 自己编一句短标题反而要求他两遍：先读标题、再读正文，才知道到底改了什么。
        /// </summary>
        private static void MakeItemCard(Transform parent, int index, string text, float w)
        {
            float textW = w - CardPad * 2f - BarW - BarGap - ItemIndent;
            if (textW < 80f) textW = 80f;    // 防呆：宽度算错时宁可窄，不要让 Text 宽度为负
            var body = MakeBodyText(parent, new[] { text });
            body.color = Theme.PaperInk;      // 公告正文用墨色（它本身就是那句"改了什么"）
            var brt = body.rectTransform;
            brt.sizeDelta = new Vector2(textW, 0f);
            float bodyH = body.preferredHeight;

            // 条目卡上下留白比段落卡窄（段落卡 CardPad=16 是给"一坨正文"留呼吸的；
            // 这里通常只有一行字，16+16 会让每张卡看着像空的，一屏也放不下几条）
            const float itemPad = 10f;
            float h = itemPad * 2f + bodyH;
            MakeCardShell(parent, w, h, out var inner);

            // 序号：与正文同一行起头（弱墨、等宽感靠两位数字自然对齐）
            var num = MakeText("Index", inner, index + ".", FsBody, Theme.PaperInkDim,
                               TextAnchor.UpperLeft, FontStyle.Normal);
            Layout(num.rectTransform, CardPad + BarW + BarGap, -itemPad, ItemIndent, bodyH);
            body.transform.SetParent(inner, false);
            Layout(brt, CardPad + BarW + BarGap + ItemIndent, -itemPad, textW, bodyH);
        }

        /// <summary>
        /// 版本组头：`更新于：10 月 23 日 下午 1:35` + `作者：iuuuuuu` +（新的）「新」标记。
        /// 两行都与工坊更新日志同字（用户 10-23 指定照那个格式）。
        /// </summary>
        private static void MakeReleaseHeader(Transform parent, NoticeRelease rel, float w, bool isNew)
        {
            // 两行（工坊更新日志就是"更新于 / 作者"两行）：各 20px 高 + 底部发丝线，
            // 行高必须 ≥ 40，否则第二行会溢出到下面第一张卡片上（无裁剪，会叠字）。
            const float rowH = 44f;
            const float lineH = 20f;
            var row = MakeRect("ReleaseHeader", parent);
            row.gameObject.AddComponent<CanvasRenderer>();
            var le = row.gameObject.AddComponent<LayoutElement>();
            le.preferredHeight = rowH;
            le.minHeight = rowH;

            var date = MakeText("Date", row,
                                "更新于：" + (rel.Date ?? ""), FsPill, Theme.PaperInkDim,
                                TextAnchor.MiddleLeft, FontStyle.Normal);
            Layout(date.rectTransform, 0f, 0f, w - 60f, lineH);

            if (isNew)
            {
                var tag = MakeRect("NewTag", row);
                tag.anchorMin = tag.anchorMax = new Vector2(1f, 1f);
                tag.pivot = new Vector2(1f, 0.5f);
                tag.sizeDelta = new Vector2(38f, 20f);
                tag.anchoredPosition = new Vector2(0f, -rowH * 0.5f);
                tag.gameObject.AddComponent<CanvasRenderer>();
                var timg = tag.gameObject.AddComponent<Image>();
                timg.sprite = ChatUiBuilder.MakeSlicedSprite(Theme.PaperAccent, 10, 4);
                timg.type = Image.Type.Sliced;
                timg.color = Color.white;
                timg.raycastTarget = false;
                var tl = MakeText("Label", tag, "新", 11, Color.white, TextAnchor.MiddleCenter, FontStyle.Bold);
                Stretch(tl.rectTransform, 2f, 0f, 2f, 0f);
            }

            var author = MakeText("Author", row,
                                  "作者：" + (rel.Author ?? ""), FsPill, Theme.PaperInkDim,
                                  TextAnchor.MiddleLeft, FontStyle.Normal);
            Layout(author.rectTransform, 0f, -lineH, w, lineH);

            // 组头下的发丝线（把版本头与它下面那串卡片绑成一组）
            var line = MakeRect("Line", row);
            line.anchorMin = new Vector2(0f, 0f);
            line.anchorMax = new Vector2(1f, 0f);
            line.pivot = new Vector2(0.5f, 0f);
            line.sizeDelta = new Vector2(0f, 1f);
            line.anchoredPosition = Vector2.zero;
            line.gameObject.AddComponent<CanvasRenderer>();
            var limg = line.gameObject.AddComponent<Image>();
            limg.color = Theme.PaperHairline;
            limg.raycastTarget = false;
        }

        /// <summary>
        /// 卡片外壳：外层 1px 发丝线（比内层大 1px）+ 内层纸白底 + 左侧青瓷竖条。
        /// 用两层 Image 而不是 `Outline` 组件：Outline 会把顶点翻四倍，且线宽随字号变化不稳。
        /// </summary>
        private static void MakeCardShell(Transform parent, float w, float h, out RectTransform inner)
        {
            var edge = MakeRect("CardEdge", parent);
            edge.gameObject.AddComponent<CanvasRenderer>();
            var edgeImg = edge.gameObject.AddComponent<Image>();
            edgeImg.sprite = ChatUiBuilder.MakeSlicedSprite(Theme.PaperHairline, 12, 4);
            edgeImg.type = Image.Type.Sliced;
            edgeImg.color = Color.white;
            edgeImg.raycastTarget = false;
            var edgeLe = edge.gameObject.AddComponent<LayoutElement>();
            edgeLe.preferredHeight = h;
            edgeLe.minHeight = h;
            edgeLe.flexibleWidth = 1f;

            inner = MakeRect("Card", edge);
            inner.anchorMin = Vector2.zero;
            inner.anchorMax = Vector2.one;
            inner.pivot = new Vector2(0.5f, 0.5f);
            inner.offsetMin = new Vector2(1f, 1f);
            inner.offsetMax = new Vector2(-1f, -1f);
            inner.gameObject.AddComponent<CanvasRenderer>();
            var bg = inner.gameObject.AddComponent<Image>();
            bg.sprite = ChatUiBuilder.MakeSlicedSprite(Theme.PaperCardBg, 12, 4);
            bg.type = Image.Type.Sliced;
            bg.color = Color.white;
            bg.raycastTarget = false;

            var bar = MakeRect("Bar", inner);
            bar.anchorMin = new Vector2(0f, 0f);
            bar.anchorMax = new Vector2(0f, 1f);
            bar.pivot = new Vector2(0f, 0.5f);
            bar.sizeDelta = new Vector2(BarW, -20f);
            bar.anchoredPosition = new Vector2(CardPad - 2f, 0f);
            bar.gameObject.AddComponent<CanvasRenderer>();
            var barImg = bar.gameObject.AddComponent<Image>();
            barImg.sprite = ChatUiBuilder.MakeSlicedSprite(Theme.PaperAccent, 6, 2);
            barImg.type = Image.Type.Sliced;
            barImg.color = Color.white;
            barImg.raycastTarget = false;
        }

        /// <summary>键帽（白底 + 1px 墨线圆角 + 青瓷字）——比"把字母加粗放大"更像键盘上的键。</summary>
        private static void MakeKeyCap(Transform parent, string key, float x, float y, float rowH)
        {
            var cap = MakeRect("KeyCap", parent);
            cap.anchorMin = cap.anchorMax = new Vector2(0f, 1f);
            cap.pivot = new Vector2(0f, 1f);
            cap.sizeDelta = new Vector2(KeyCapW, KeyCapH);
            cap.anchoredPosition = new Vector2(x, y - (rowH - KeyCapH) * 0.5f);
            cap.gameObject.AddComponent<CanvasRenderer>();
            var img = cap.gameObject.AddComponent<Image>();
            img.sprite = ChatUiBuilder.MakeSlicedSprite(Theme.PaperKeycapBg, 12, 4);
            img.type = Image.Type.Sliced;
            img.color = Color.white;
            img.raycastTarget = false;

            var stroke = MakeRect("Stroke", cap);
            Stretch(stroke);
            stroke.gameObject.AddComponent<CanvasRenderer>();
            var simg = stroke.gameObject.AddComponent<Image>();
            simg.sprite = ChatUiBuilder.MakeSlicedSprite(Theme.PaperInkDim, 12, 1);
            simg.type = Image.Type.Sliced;
            simg.color = new Color(1f, 1f, 1f, 0.55f);
            simg.raycastTarget = false;

            var label = MakeText("Label", stroke, key, 13, Theme.PaperAccent,
                                 TextAnchor.MiddleCenter, FontStyle.Bold);
            Stretch(label.rectTransform);
        }

        /// <summary>
        /// 一段正文。`lines` 的每一项是**一个自然段**，段之间空一行 —— 靠 `lineSpacing` 给的
        /// 行距只够分开"折行"，分不开"两条并列的步骤"：第一段（连不上怎么办）有五条动作，
        /// 挤成连续折行时玩家会以为是一句话没说完。
        /// </summary>
        private static Text MakeBodyText(Transform parent, string[] lines)
        {
            string s = "";
            if (lines != null)
            {
                for (int i = 0; i < lines.Length; i++)
                {
                    if (string.IsNullOrEmpty(lines[i])) continue;
                    if (s.Length > 0) s += "\n\n";      // 空一行 = 段落间隔
                    s += lines[i];
                }
            }
            var t = MakeText("Body", parent, s, FsBody, Theme.PaperInk, TextAnchor.UpperLeft, FontStyle.Normal);
            t.lineSpacing = 1.35f;      // 中文字密，1.35 比默认 1 好读（默认会把两行贴在一起）
            return t;
        }

        private static void AddSpacer(Transform parent, float h)
        {
            var sp = MakeRect("Spacer", parent);
            sp.gameObject.AddComponent<CanvasRenderer>();
            var le = sp.gameObject.AddComponent<LayoutElement>();
            le.preferredHeight = Mathf.Max(0f, h);
            le.minHeight = Mathf.Max(0f, h);
        }

        // ------------------------------------------------------------------
        // 小工具
        // ------------------------------------------------------------------


        /// <summary>左上角定位（本面板全用绝对坐标：尺寸固定，不做自适应）。</summary>
        private static void Layout(RectTransform rt, float x, float y, float w, float h)
        {
            rt.anchorMin = new Vector2(0f, 1f);
            rt.anchorMax = new Vector2(0f, 1f);
            rt.pivot = new Vector2(0f, 1f);
            rt.sizeDelta = new Vector2(w, h);
            rt.anchoredPosition = new Vector2(x, y);
        }

        private static Text MakeText(string name, Transform parent, string text, int size,
                                     Color color, TextAnchor align, FontStyle style)
        {
            // 公告栏的皮肤：行距 1.2、关富文本（文案里的 <> 不许被当标签吞掉）、字号不四舍五入
            return UiKit.MakeText(name, parent, text, size, color, align, style,
                                  wrap: true, richText: false, lineSpacing: 1.2f, roundSize: false);
        }

        private static GameObject NewButton(string name, Transform parent, string label, int size,
                                            Color labelColor, Color bg)
        {
            var rt = MakeRect(name, parent);
            rt.gameObject.AddComponent<CanvasRenderer>();
            var img = rt.gameObject.AddComponent<Image>();
            img.sprite = null;                // 纯色块（需要九宫格时调用方自行换贴图）
            img.color = bg;
            img.raycastTarget = true;
            var btn = rt.gameObject.AddComponent<Button>();
            btn.targetGraphic = img;
            // ★ 透明底必须用 None 过渡 ★：ColorTint 会把"透明黑"乘成不透明灰，
            //   hover 时凭空浮出一个灰方块盖住页签/✕ 的文字（实测过的观感 bug）。
            // 判据用 0.02 而不是 0.01：点击区底色是 1/255(≈0.0039) + 想留一点余量，
            // 这类"看不见的按钮"一律不要 hover 变色（否则会凭空浮出灰块）。
            if (bg.a < 0.02f) btn.transition = Selectable.Transition.None;
            else
            {
                btn.transition = Selectable.Transition.ColorTint;
                var cb = btn.colors;
                cb.normalColor = Color.white;
                cb.highlightedColor = new Color(0.94f, 0.94f, 0.94f, 1f);
                cb.pressedColor = new Color(0.86f, 0.86f, 0.86f, 1f);
                cb.selectedColor = Color.white;
                cb.disabledColor = new Color(1f, 1f, 1f, 0.5f);
                cb.colorMultiplier = 1f;
                cb.fadeDuration = 0.08f;
                btn.colors = cb;
            }
            if (!string.IsNullOrEmpty(label))
            {
                var t = MakeText("Label", rt, label, size, labelColor, TextAnchor.MiddleCenter, FontStyle.Normal);
                Stretch(t.rectTransform);
            }
            return rt.gameObject;
        }



    }
}
