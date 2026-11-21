/// <summary>
/// 对话 UI 视图门面（MonoBehaviour）—— 对外只说「视图语言」：
/// Open/Close、加一条用户气泡、加分隔条、回合过程折叠组、流式气泡（delta 合并）、历史替换、忙态。
///
/// 内聚三个性能措施：
///  ① delta 逐帧合并：AppendDelta 只写 StringBuilder 缓冲，LateUpdate 每帧一次刷 Text
///     ——流式 token 很密，不合并会逐 token 触发 Canvas rebuild（最常见的流式 UI 卡顿源）
///  ② 行数上限 MAX_ROWS：超出销毁最旧行（同步裁 list），长对话顶点数有界
///  ③ 智能滚底：仅当用户处于底部时才自动跟随（滚轮上翻即停；再向下滚回跟随），回看历史不被拽走
///
/// 点的手感：CloseButton / SendButton 走 Button + ClickUtils（委托桥三步写法，ClickCatcher 已退役）。
/// 明确不做：不解析 JSON 协议字段、不持有 WsClient 引用、不发起任何网络调用——
/// 那些都在 ChatPresenter。本类只知道 ChatWindowRefs 与行视图。
/// </summary>
using System;
using System.Collections.Generic;
using System.Text;
using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.UI;

namespace AgentLoopBridge
{
    /// <summary>
    /// IL2CPP 注意：本类是自定义 MonoBehaviour，MelonLoader 0.5（Unhollower）要求
    /// 先用显式 ClassInjector.RegisterTypeInIl2Cpp&lt;ChatWindow&gt;() 注册进 IL2CPP 类型系统，
    /// 否则 AddComponent&lt;ChatWindow&gt;() 会抛 TypeInitializationException。
    /// 注册调用在宿主侧（主工程 ModMain / 冒烟 UiSmokeProbe）完成，本类不引 MelonLoader——
    /// 保持纯 Unity 依赖，可被 Unity 预览工程链接编译（神识传音同款：注册后才 AddComponent）。
    /// </summary>
    public class ChatWindow : MonoBehaviour
    {
        /// <summary>
        /// IL2CPP 互操作标准构造（Unhollower 按此签名实例化）。
        /// 仅 MelonLoader/Unhollower 环境需要；标准 Unity 编辑器不存在此签名，
        /// 用 #if MELONLOADER 隔离该符号下的差异（原先是为了同一源码能被已删除的 ui_preview 预览工程共用）。
        /// </summary>
#if MELONLOADER
        public ChatWindow(System.IntPtr ptr) : base(ptr) { }
#endif

        private ChatWindowRefs _r;
        private readonly StringBuilder _delta = new StringBuilder();
        private Text _bubbleText;              // 当前流式气泡的文本组件
        private GameObject _deltaBubble;       // 当前流式气泡行（Root）
        /// <summary>当前流式气泡的**视图对象**（11-20 加：流式期间宽度要跟着文字长）。
        ///
        /// 为什么必须持有它：宽度算在 `ChatBubble` 里（`_bubble` / `_refs` / `_width` 都是它的字段），
        /// 而 `LateUpdate` 只有 `_bubbleText`（一个 `Text` 组件）—— 拿着 Text 没法重算宽度。
        /// 不持有的话，`BeginNpcBubble("")` 那一刻（文字还是空串）算出来的宽度会**定死整段流式**：
        /// 旧代码量不出空串 ⇒ 直接给上限 1060，用户 11-20 截图就是"一段短话横到天花板"。
        /// </summary>
        private ChatBubble _deltaView;
        private bool _deltaDirty;
        // 本段流式的**开头**（`BeginNpcBubble` 预写的那几个字，群聊是「甲：」）长度。
        // 用途见 `ActiveBubbleHasNoBody`：区分"开了头还没说"与"说了几个字"。
        private int _deltaPrefixLen;
        private bool _followingBottom = true;
        private int _lastChildCount = -1;      // 跟随用：上一帧 Content 子节点数（判"内容长了"）
        private float _lastContentH = -1f;     // 跟随用：上一帧 Content 高度（文本流式增长也算）
        // 批量重建后的"钉底保持"帧数（`HoldScrollAtBottom` 置位）：这几帧里**无条件**钉底，
        // 且不吃"位置不在底部就停跟随"那条判决。为什么需要：
        //   一次画很多行（历史重放 / 整轮重演）时，布局要好几帧才结算 —— 而钉底只发生在
        //   "内容变高"的那一帧，若布局结算发生在之后（视口 rect 变了 / 行高最后才定），
        //   下一帧就是"内容没长高 + 位置已不在底部" ⇒ 跟随被**永久关掉**，视图停在顶部
        //   （实机：重开窗后要自己往下翻）。保持期内连钉几帧，把这段结算窗口整个盖住。
        private int _pinHoldFrames;
        // 「↓ N条新消息」浮动钮（10-22 UI 批次）：上翻历史（_followingBottom=false）期间
        // 新注册的行数。0 或恢复跟随时清零；节点运行时新建（AB 预制件没有它），
        // 显隐/文案在 `RefreshNewMsgBadge`（LateUpdate 每帧一次，零轮询成本）。
        private int _newRowsWhileUnfollowed;
        private GameObject _newMsgBtn;
        private Text _newMsgBtnLabel;
        private int _rowCount;
        private bool _busy;

        // ---- 折叠行位置校正（「不够就滚够」，2026-09-16 用户选 B 档）----
        // 展开会让 Content 长高，而 LateUpdate 的流式跟随把"长高"当成"新内容到达" → 钉底。
        // 后果：用户点开一行，看到的是这段文字的**结尾**（开头被顶出视口上沿）。
        // 修法：点开时关掉跟随（用户接管屏幕，同滚轮上滚），并记下被点的那一行；
        // 布局要到帧末才重建，所以等行高**真的变了**再算"还差多少像素才装得下"，
        // 只滚那一点点（行比视口高就把行顶对齐视口顶）。全程不写 verticalNormalizedPosition。
        private GameObject _foldAnchorRow;
        private float _foldAnchorHeight;
        private int _foldAnchorWait;

        private StepGroup _curGroup;           // 当前回合过程组（直播）
        private bool _stepOpen;
        private ThinkGroup _curThink;          // 当前内心思量折叠区（think 流式）
        // ---- 压缩纪要折叠行（10-31）----
        // 为什么单独一张表：锁定/刷新要按 compactionId 找回"屏幕上那几行"，而 `_rows` 里
        // 只有 RowMeta（删除模式的选中键），没有视图对象。行被 MAX_ROWS 裁掉/重放清空时
        // 该表跟着清；Lock/Refresh 前逐个判 `Root != null`（Unity 的已销毁判据）。
        private readonly List<SummaryGroup> _summaryRows = new List<SummaryGroup>();
        private StepGroup _curSys;             // 当前回合「系统组装」折叠块（复用 StepGroup 模板）
        private StepGroup _curCtx;             // 当前回合「运行时上下文」折叠块（复用 StepGroup 模板）

        // ---- 同一回合同段正文「只成泡一次」+「主动传音分隔条只插一条」（修重复输出）----
        // 终稿正文会**两路到达**：① `step(text)`（每步正文 / 非流式后端整段正文）
        // ② `npc_reply`（回合收口帧，主动传音还带 initiative=true）。
        // 旧写法：① 先成泡并 ClearStreamingState（`_deltaBubble=null`），② 到的时候发现
        // "没有活跃流式气泡" → 又建一条新气泡（还补一条分隔条）→ 屏幕上同段话出现两遍
        // （真机 00:50 云含"论道婉拒"那回合：日志里 `step(text)` 与 `npc_reply` 逐字相同）。
        //
        // ★ 判重的正确语义（09-16 定稿）：「**屏幕真相**」而不是「我记不记得画过」。
        //   往屏幕上放正文的有三条路 —— `FinishNpcBubble`（step/npc_reply）、
        //   `ReplaceHistory` 的 AppendNpcStatic（回放）、流式 `AppendDelta`；下面三个字段
        //   必须始终描述"屏幕上**最后一条**正文泡"，**每一条放正文的路都要登记**。
        //   漏登记哪条，npc_reply 就会在那条之后判不出重复 → 同一段话两遍
        //   （09-16 实机两次：① 回合已 turn/end、npc_reply 未到的空档重开窗 → 回放没登记；
        //    ② 模型在工具返回后复述旁白 → 流式重绘泡没被撤）。
        //   判定规则集中在 `BubbleCommitPolicy`（纯逻辑，有单测）；字段含义：
        private int _curTurn = -1;             // 最近一个 step 帧的回合号（ChatPresenter.NoteTurn 写）
        private int _lastRenderTurn = -1;      // 屏幕上末条正文泡所属回合（去重判据之一，未登记=-1）
        private string _lastRenderText;        // 屏幕上末条正文泡的正文（去重判据之二，未登记=null）
        private GameObject _lastRenderRow;     // 那条气泡行（补分隔条 / 终稿覆盖时定位用）
        private int _dividerTurn = -1;         // 已插过「—— 主动传音 ——」分隔条的回合号（同回合只插一条）
        private int _inputTurn = -1;           // 输入块归属回合（换回合新建，防跨回合串块）
        private Text _tokensBar;                 // 底部 token 统计文本（节点由预制体提供：BG/StatsBar，缺失容错=不显示）
        private bool _statsBarWarned;            // 统计栏节点没解析到：只吼一次（见 SetStats）

        // ---- 微信式头像槽位（立绘预填到头像模板，克隆行共享纹理）----
        private RawImage _userAvatarImg;
        private RawImage _npcAvatarImg;
        public RawImage UserAvatarImg => _userAvatarImg;
        public RawImage NpcAvatarImg => _npcAvatarImg;
        // 旧顶部大立绘槽位（已废），保留只读属性以兼容未启用 AB_UI 的代码分支
        public RawImage PortraitLeft => null;
        public RawImage PortraitRight => null;

        // ---- 群聊：逐行头像的说话人来源 ----
        /// <summary>玩家名（`SetPlayerName` 填；群聊里玩家行的头像按它取图）。</summary>
        private string _playerName = "";
        /// <summary>群聊里"当前正在说话的人"（`room_state(phase=speak)` 写、`phase=end` 清）。
        /// 为什么需要：群聊的正文有**三条路**到达屏幕 —— 流式起泡（带名字，`BeginNpcBubble`）、
        /// `room_say` 定稿（带名字）、以及 `step(text)` 整段正文（**不带说话人**）。
        /// 第三条若没有默认值，它建的泡就会退回"模板那张陈旧的脸"，而这是**静默**的
        /// （头像画着上一个单聊 NPC）。所以按回合记住说话人，作为缺省。</summary>
        private string _roomSpeaker = "";
        /// <summary>`_roomSpeaker` 的**精确 unitID**（与它同写同清，永不成对漂移）。
        /// 为什么成对：名字是给屏幕看的，id 是给取图用的 —— 群聊里 Python 本来就知道
        /// "这个名字是哪个 unitID"，随帧一起送上来就不必让 C# 再按名猜一次
        /// （重名时猜错**不报错、不缺字段**，只是画上别人的脸）。
        /// 两者都由 `SetRoomSpeaker(name, id)` 一个入口写（分开写就会漂移）。</summary>
        private string _roomSpeakerId = "";

        // ---- 群聊动作按钮（拉人 / 踢人，预制件 BG/InviteBtn + BG/KickBtn）----
        /// <summary>拉人按钮（仅群聊模式显示；预制体未加该节点时恒为 null，事件永不触发）</summary>
        public event Action InviteClicked;
        /// <summary>踢人按钮（同上）</summary>
        public event Action KickClicked;
        private GameObject _inviteBtn;
        private GameObject _kickBtn;
        /// <summary>预制体有没有这两个按钮（Present 据此决定"降级"还是"报错"）。</summary>
        public bool RoomActionButtonsPresent => _inviteBtn != null || _kickBtn != null;

        // ---- 群聊：跳过（预制件 BG/Input/SkipBtn，发送键正上方 8px）----
        /// <summary>这一轮玩家不置一词，让 NPC 按轮转自己接下去（Presenter 发 `room_skip`）。
        /// 与拉人/踢人同款：**可选节点**，预制件没打补丁时恒为 null ⇒ 事件永不触发，
        /// 其余功能一字不变（不是 critical 节点，缺了不拒绝装配）。</summary>
        public event Action SkipRequested;
        private GameObject _skipBtn;

        /// <summary>渲染行数上限（200 → 1000）。
        /// 历史回放现在要**全部**（Python 侧 limit=0），200 行会在实机 26 回合 ≈ 104 行时刚好卡住、
        /// 更长一点就又开始偷偷丢最旧的（用户明确要求"我要看全部"）。1000 行 ≈ 250 回合，
        /// 覆盖实际规模；真的超了会**显式**在列表顶部插一行「更早的 N 条未显示」——
        /// 绝不再静默截断（今天两次误判都是"UI 悄悄少给"引起的）。
        /// 代价：Content 带 VerticalLayoutGroup + ContentSizeFitter，整表重排 O(行数)，
        /// 1000 行是"能忍"的上界；真要上千回合，正解是上拉分页/虚拟列表（仍未做）。</summary>
        private const int MAX_ROWS = 1000;
        /// <summary>本次 ResetContent 之后被裁掉的行数（ReplaceHistory 结束时据此插提示行）。</summary>
        private int _trimmedRows;

        // ---- 历史回放的**分帧构建**（11-20 修「开窗卡住 1.1 秒」）----
        // 真机：575 条单聊历史，按下开窗键后 `[MFrame][FrameStall] ms=770` + `ms=358`，
        // 期间画面完全停住、连 ESC 都不响应。那两帧里做了：克隆 575 行节点树、
        // 给 575 个头像挂点击按钮、写 2 张立绘快照。
        //
        // 根因不是某一步特别慢，是**全部塞进同一帧**。玩家感受到的不是"575 行要建多久"，
        // 而是"屏幕停止响应多久"。所以把构建摊到多帧，判据在 `HistoryBuildCore`。
        //
        // 这里的三个字段只在该模式下有意义（`_histItems == null` = 没在分帧构建）：
        private JArray _histItems;             // 本次回放的条目（构建期间持有）
        private int _histCursor;               // 下一条待建的下标
        private StepGroup _histGroup;          // 跨帧延续的「行动记录」组（tool_call/tool_result 要归并）
        private RowMeta _histGroupMeta;
        private bool _histPending;             // 有一次回放还没收尾（= 还没跑 AfterHistoryBuilt）
        private bool _histBuilding;            // 正在**分帧**构建（同步路径全程为 false，但 _histPending 为 true）
        private Action _histDone;              // 建完之后要续跑的那一段（见 AfterHistoryBuilt）

        // ---- 分页窗口（11-21：修「消息越多越卡」）----
        //
        // ★ 数据来源变了（11-21 二次定案）★
        //   第一版是**纯客户端切片**（`open_chat` 拿全量、本地只渲染最近 40 条）。
        //   用户否决了这条路，理由正确：**每次开窗仍要把全部历史传过来**，
        //   "等很久"那一段一点没省。现在 `open_chat` 只取最近 N 回合，
        //   更早的由 `ChatPresenter` 发 `get_history(before_seq, before_turn)` 拉回来 ——
        //   于是本类**不再持有全量**，只做两件本地的事：
        //     ① 建/撤「加载更早」入口行（`RefreshMoreRow`）；
        //     ② 把 Presenter 送来的那一批条目**插到顶部**并补偿滚动位置（`InsertEarlier`）。
        //   剩余量由 Presenter 通过 `SetEarlierState` 告知（本地无从推算）。
        /// <summary>上方还有更早的历史可取（由 Presenter 按回执游标设置）。</summary>
        private bool _histHasMore;
        /// <summary>还没渲染的**条目数**（只用于文案"还有 K 条"；-1 = 未知）。</summary>
        private int _histHiddenCount = -1;
        /// <summary>「加载更早」那一行的根（空 = 没有这一行）。**它在 `_rows` 之外**：
        /// 它不是一条消息，不该参与选中/删除/统计。</summary>
        private GameObject _moreRow;
        private Text _moreRowText;
        private bool _moreLoading;
        private const float BOTTOM_EPS = 0.02f;

        // ---- 对外事件（Present 订阅）----
        public event Action CloseClicked;
        /// <summary>用户点了发送/回车（Present 读输入框文本再发 WS）</summary>
        public event Action SubmitRequested;
        /// <summary>用户点了标题栏 ⚙ 配置按钮（ModMain 订阅 → ConfigPanelOpener）。
        /// AB 预制体未加该节点时永远不触发，无副作用</summary>
        public event Action ConfigClicked;
        /// <summary>用户点了标题栏 🗑 删除按钮（Presenter 决定进入流程：忙时拒绝，闲时重放后入模式）。
        /// 预制体未加 BG/DeleteBtn 时永远不触发</summary>
        public event Action DeleteBtnClicked;
        /// <summary>用户点了标题栏「压缩」按钮（Presenter 发手动压缩请求，忙时即时拒绝）。
        /// 预制体未加 BG/CompactBtn 时永远不触发</summary>
        public event Action CompactClicked;
        /// <summary>用户点了标题栏「工具」按钮（Presenter 打开/收起右侧「可做的事」侧边栏）。
        /// 那颗按钮与面板本体都由 `ToolPanel` 在**运行时克隆 `ConfigBtn`** 得到，
        /// 所以它不依赖预制体里有没有这个节点 —— 与本类其它「可选节点」事件不同，它恒可触发。
        /// 群聊模式下按钮会被藏起来（工具的发起方恒为某个 NPC，群里没有唯一对象）。</summary>
        public event Action ToolsClicked;
        /// <summary>点了压缩纪要折叠行的「修改」：参数 = (compactionId, 当前正文)。
        /// Presenter 据此打开左侧编辑面板（正文预填）。</summary>
        public event Action<string, string> SummaryEditClicked;
        /// <summary>左侧编辑面板点了「发送」：参数 = 文本域正文（未 strip）。
        /// Presenter 拿它走 `edit_compact_summary` RPC —— **不是**发玩家消息。</summary>
        public event Action<string> SummaryEditSubmitted;
        /// <summary>删除模式两段确认后执行（Presenter 发 delete_history RPC，载荷=选中的回合号）</summary>
        public event Action<List<int>> DeleteConfirmed;
        /// <summary>删除确认按钮第一击武装时触发（Presenter 发 preview_delete_history 取弹窗数据，
        /// 回来后经 ApplyDeletePreview 展示摘要/拒绝原因）。预制体无删除功能则永不触发</summary>
        public event Action<List<int>> DeletePreviewRequested;
        /// <summary>**群聊**删除：第一击武装时请求预览（Presenter 发 `preview_room_delete`，
        /// 载荷=选中的发言 uid）。与单聊的两个事件严格对称：群删按**条**、单聊按**回合**</summary>
        public event Action<List<string>> RoomDeletePreviewRequested;
        /// <summary>**群聊**删除：两段确认后执行（Presenter 发 `room_delete`，载荷=选中的发言 uid）
        /// —— 会同时改公共记录与**在场成员**的记忆，确认文案必须写明这一点（§9.2）</summary>
        public event Action<List<string>> RoomDeleteConfirmed;

        // ---- 删除模式（微信式多选）----
        /// <summary>是否处于删除选择模式（行点击=切换选中、输入区禁用、底部操作条显示）</summary>
        public bool Selecting { get; private set; }
        private readonly List<RowMeta> _rows = new List<RowMeta>();   // 行注册表：与 Content 子节点同序，绑定 seqs/遮罩
        private RowMeta _lastMeta;            // 最近一次注册的行（Append* 系列取它回传）
        private bool _confirmArmed;            // 删除按钮两段确认的武装态
        private float _confirmArmTime;

        // ---- 状态（Present 查询）----
        public string CurrentNpcId { get; private set; }
        public bool IsOpen { get; private set; }
        public bool HasActiveBubble => _deltaBubble != null;
        public bool IsBusy => _busy;
        public InputField NpcLabel => _r != null ? _r.NpcLabel : null;
        /// <summary>玩家名显示框（BG/Playerput，只读；缺失容错）</summary>
        public InputField PlayerLabel => _r != null ? _r.PlayerLabel : null;
        public InputField ChatInput => _r != null ? _r.ChatInput : null;

        /// <summary>滚轮灵敏度**下限**（一格 ≈ 4.5 行正文；正文行高实测 19.79px）。
        ///
        /// 为什么在 C# 里再设一遍（"两条腿"，同 `AbConfigPanel.EnsureScrollInputTarget` 的约定）
        /// 预制件 `BG/Scroll` 的 `m_ScrollSensitivity` 在老 AB 里是 30 —— 聊天余量动辄 2000~4000px，
        /// 30/格从底爬到顶要 70~130 格，手感像"爬"。只改预制件的话**必须重打 AB 才生效**；
        /// 这里兜一道，手上这份老 AB 也能立刻好用，预制件那条腿只负责"把修复固化进资产"。
        ///
        /// 语义是**下限**不是赋值：预制件配得比它高就听预制件的（以后想更快改资产即可，不用动 C#）。
        /// </summary>
        private const float SCROLL_SENSITIVITY_MIN = 90f;

        /// <summary>把 `BG/Scroll` 的滚轮灵敏度抬到下限（低于才动，幂等；缺节点/异常只记一行不阻断开窗）。</summary>
        private void ApplyScrollSensitivityFloor()
        {
            try
            {
                var s = _r != null ? _r.Scroll : null;
                if (s == null) return;
                if (s.scrollSensitivity < SCROLL_SENSITIVITY_MIN)
                {
                    float was = s.scrollSensitivity;
                    s.scrollSensitivity = SCROLL_SENSITIVITY_MIN;
                    ModMain.P("[ChatWindow] 滚轮灵敏度兜底：" + was + " → " + SCROLL_SENSITIVITY_MIN
                              + "（预制件未重打 AB，走运行期兜底）");
                }
            }
            catch (Exception e) { ModMain.P("[ChatWindow] ApplyScrollSensitivityFloor: " + e.Message); }
        }

        /// <summary>气泡文字版式的**运行期兜底**（"两条腿"，同 `ApplyScrollSensitivityFloor` 的约定）。
        ///
        /// **预制件是真源**（`scripts/dev/prefab_patch_chat_bubble_style.py`），真源那条腿规定：
        ///   · **两侧正文都「左对齐 + 顶部」**（`TextAnchor.UpperLeft`）—— 气泡整体靠左还是靠右，
        ///     由**行模板的 HorizontalLayoutGroup** 决定，与文字对齐无关。
        ///     ⚠ 09-17 实机纠正过一次：曾把玩家侧改成"右对齐 + 垂直居中"，用户截图打回
        ///     （短句会浮在气泡中间偏右，正确观感是贴左上）。
        ///   · **尾巴那一侧**的 padding 要 ≥ 图里 body 的内缩（NPC 尾巴在左 / 玩家在右），
        ///     否则文字会画到气泡白底外（`npc气泡.png` 470 宽、body 从 x=35 起 ⇒ 内缩 ~33.5 单位）。
        ///
        /// 但是**预制件改动必须重打 AB 才生效** ⇒ 这里按**下限**再兜一道：手上这份老 AB
        /// 一重启就正确，预制件那条腿只负责"把修复固化进资产"。语义是下限不是赋值
        /// （只抬高 padding、只在对齐不对时纠正），以后想调大改资产即可，不用动 C#。
        /// 缺节点/异常只记一行、不阻断开窗（气泡模板结构变了也不该让窗开不出来）。
        /// </summary>
        private const int BUBBLE_TAIL_PAD = 40;   // 尾巴侧下限（> NPC 33.5 / 玩家 31.7 的内缩）
        private const int BUBBLE_FAR_PAD = 14;    // 另一侧下限
        private const int BUBBLE_PAD_V = 12;      // 上下限（实测首行墨迹离顶沿只剩 4px ⇒ 提到 12）

        /// <summary>九宫格 border（L/B/R/T，单位 = 图片像素；PPU 100 = canvas 参考 ⇒ 1px ≈ 1 单位）。
        ///
        /// **必须与 `Assets/art/*.png.meta` 里的 `spriteBorder` 逐项相等** ——
        /// 预制件那条腿（改 meta、要重打 AB）与运行期那条腿（这段代码）说的是同一件事。
        /// 数值是从两张 PNG 上**量**出来的：取没有尾巴那一侧的边缘，看它从哪一行开始稳定，
        /// 那就是圆角结束、可以安全拉伸的起点。两图圆角都占高度的 27%。
        ///
        /// ⚠ 全 0（改动前的值）会让长气泡的圆角被纵向拉成椭圆、尾巴拉成尖刺。</summary>
        private const int BUBBLE_BORDER_NPC_L  = 38;   // 左：尾巴 + 圆角（图里 body 从 x=35 起）
        private const int BUBBLE_BORDER_NPC_B  = 17;   // 下：圆角
        private const int BUBBLE_BORDER_NPC_R  = 35;   // 右：圆角（原来漏了，正是椭圆的主因）
        private const int BUBBLE_BORDER_NPC_T  = 17;   // 上：圆角
        private const int BUBBLE_BORDER_USER_L = 10;   // 左：圆角（原来漏了）
        private const int BUBBLE_BORDER_USER_B = 19;   // 下：圆角
        private const int BUBBLE_BORDER_USER_R = 40;   // 右：尾巴 + 圆角
        private const int BUBBLE_BORDER_USER_T = 19;   // 上：圆角

        /// <summary>把两个气泡模板的正文对齐/内边距抬到下限（幂等；缺节点/异常只记一行不阻断开窗）。</summary>
        private void ApplyBubbleTextLayoutFloor()
        {
            try
            {
                bool changed = false;
                changed |= FixBubbleLayout(_r != null ? _r.NpcRowTemplate : null, "NpcBubble", true);
                changed |= FixBubbleLayout(_r != null ? _r.UserRowTemplate : null, "UserBubble", false);
                if (changed)
                    ModMain.P("[ChatWindow] 气泡文字版式兜底：预制件未重打 AB，运行期已按下限改正"
                              + "（正文=左上 / 尾巴侧 padding=" + BUBBLE_TAIL_PAD
                              + " 另一侧=" + BUBBLE_FAR_PAD + " 上下=" + BUBBLE_PAD_V
                              + " 行距=" + BubbleTextCore.BubbleLineSpacing
                              + " 富文本=开）");
            }
            catch (Exception e) { ModMain.P("[ChatWindow] ApplyBubbleTextLayoutFloor: " + e.Message); }
        }

        /// <param name="tailOnLeft">这张气泡图的尾巴在不在左边（决定哪一侧要给到 40）。</param>
        /// <returns>是否真的改动过（用于一次性日志）。</returns>
        private static bool FixBubbleLayout(GameObject rowTemplate, string bubbleName, bool tailOnLeft)
        {
            if (rowTemplate == null) return false;                 // 老 AB 没有该模板：跳过
            Transform b = rowTemplate.transform.Find(bubbleName);
            if (b == null) return false;                           // 结构变了：静默跳过
            bool changed = false;
            // ① 正文对齐：只纠正，不覆盖（将来预制件若改成别的对齐，这里也只会把它拉回左上）
            var text = b.GetComponentInChildren<Text>(true);
            if (text != null && text.alignment != TextAnchor.UpperLeft)
            {
                text.alignment = TextAnchor.UpperLeft;
                changed = true;
            }
            // ② 内边距：按**下限**抬高（只增不减 —— 预制件配得更大就听预制件的）
            var vlg = b.GetComponent<VerticalLayoutGroup>();
            if (vlg != null)
            {
                if (vlg.childAlignment != TextAnchor.UpperLeft)
                {
                    vlg.childAlignment = TextAnchor.UpperLeft;     // 单行泡文字贴顶（用户要"顶部"）
                    changed = true;
                }
                RectOffset p = vlg.padding;
                int l = tailOnLeft ? BUBBLE_TAIL_PAD : BUBBLE_FAR_PAD;
                int r = tailOnLeft ? BUBBLE_FAR_PAD : BUBBLE_TAIL_PAD;
                int nl = Mathf.Max(p != null ? p.left : 0, l);
                int nr = Mathf.Max(p != null ? p.right : 0, r);
                int nt = Mathf.Max(p != null ? p.top : 0, BUBBLE_PAD_V);
                int nb = Mathf.Max(p != null ? p.bottom : 0, BUBBLE_PAD_V);
                if (p == null || nl != p.left || nr != p.right || nt != p.top || nb != p.bottom)
                {
                    vlg.padding = new RectOffset(nl, nr, nt, nb);
                    changed = true;
                }
            }
            // ③ 行距 + 富文本（10-31 用户："文字多的时候密密麻麻" / "想用富文本上色"）
            //
            //    两件事都在 Text 组件上，且都是"预制件没重打 AB 就还是老样子"的东西 ——
            //    与 ①② 同款**运行期兜底**：老 AB 一重启就对，预制件那条腿只负责固化进资产。
            //
            //    lineSpacing 取 `BubbleTextCore.BubbleLineSpacing`（1.35），**不在这里重新写一个
            //    字面量** —— 与富文本清洗器共用同一个常量，才不会出现"跑了新 AB 反而行距变了"。
            //
            //    supportRichText 必须为 true：`Render()` 产出的 `<color=…>` 要它才会被当标签。
            //    老预制件本来就是 1（uGUI 默认），这里显式写一遍是为了**不被别人的全局设置带偏**
            //    （真见过某个面板把它关掉，于是同一段文字在那边显示成裸标签）。
            if (text != null)
            {
                if (Mathf.Abs(text.lineSpacing - BubbleTextCore.BubbleLineSpacing) > 0.001f)
                {
                    text.lineSpacing = BubbleTextCore.BubbleLineSpacing;
                    changed = true;
                }
                if (!text.supportRichText)
                {
                    text.supportRichText = true;
                    changed = true;
                }
            }
            // ④ 九宫格 border（10-31：气泡变高时圆角被拉成椭圆、尾巴拉成尖刺）
            //
            //    症状来自 `spriteBorder` 的**上下边框是 0**：`Image.Type.Sliced` 会把没设 border
            //    的那一维**整段拉伸**，于是长气泡的圆角（占图高 27%）被纵向拉长成椭圆，
            //    尾巴也被拉成一根尖刺。短气泡看不出来，**文本一长就变形** ——
            //    而"文本长"恰恰是这次要修的场景。
            //
            //    兜底做法：直接改**运行时 Sprite 实例**的 border。`Sprite.border` 是只读的，
            //    必须走 `Sprite.Create` 造一份新的（同一张纹理、同一 rect、带正确 border），
            //    再赋回 Image。纹理是共享的，不会多占显存。
            //
            //    ⚠ 这条兜底**只在老 AB 上生效**：预制件那条腿改的是 `.png.meta` 的 spriteBorder
            //      （需要重打 AB）。两边数值必须一致，`BUBBLE_BORDER_*` 与
            //      `tests/test_chat_bubble_layout_contract.py` 里的期望逐项比对。
            var img = b.GetComponent<Image>();
            if (img != null && img.sprite != null)
            {
                Sprite old = img.sprite;
                Vector4 want = tailOnLeft
                    ? new Vector4(BUBBLE_BORDER_NPC_L, BUBBLE_BORDER_NPC_B, BUBBLE_BORDER_NPC_R, BUBBLE_BORDER_NPC_T)
                    : new Vector4(BUBBLE_BORDER_USER_L, BUBBLE_BORDER_USER_B, BUBBLE_BORDER_USER_R, BUBBLE_BORDER_USER_T);
                Vector4 cur = old.border;   // (L, B, R, T)
                if (Mathf.Abs(cur.x - want.x) > 0.5f || Mathf.Abs(cur.y - want.y) > 0.5f
                    || Mathf.Abs(cur.z - want.z) > 0.5f || Mathf.Abs(cur.w - want.w) > 0.5f)
                {
                    try
                    {
                        var fixedSpr = Sprite.Create(old.texture, old.rect, old.pivot,
                                                     old.pixelsPerUnit, 0, SpriteMeshType.FullRect,
                                                     new Vector4(want.x, want.y, want.z, want.w));
                        if (fixedSpr != null) { img.sprite = fixedSpr; changed = true; }
                    }
                    catch (Exception e)
                    {
                        ModMain.P("[ChatWindow] 气泡九宫格 border 兜底失败（" + bubbleName + "）：" + e.Message);
                    }
                }
            }
            return changed;
        }

        public void Init(ChatWindowRefs refs)
        {
            _r = refs;
            ApplyScrollSensitivityFloor();   // 老 AB 也立刻好用，不必等重打
            ApplyBubbleTextLayoutFloor();    // 同上：气泡对齐/内边距的运行期兜底
            EnsureOwnCanvas();               // ★ 11-21：拖动不卡的命门（见方法说明）
            if (_r.CloseButton != null)
            {
                var btn = _r.CloseButton.GetComponent<Button>();
                if (btn != null) ClickUtils.Attach(btn, () => CloseClicked?.Invoke());
            }
            if (_r.ConfigButton != null)
            {
                var cfgBtn = _r.ConfigButton.GetComponent<Button>();
                if (cfgBtn != null) ClickUtils.Attach(cfgBtn, () => ConfigClicked?.Invoke());
            }
            if (_r.SendButton != null)
            {
                var btn = _r.SendButton.GetComponent<Button>();
                if (btn != null) ClickUtils.Attach(btn, () => SubmitRequested?.Invoke());
            }
            // 右侧「可做的事」侧边栏 + 顶栏那颗「工具」钮：**运行时建/克隆**，不碰预制体。
            // 在这里搭（而不是 Presenter 里）是因为两者都挂在 BG 下、都需要 `_r.Window` 这个坐标基准，
            // 且必须在 `Close()`（Init 末行）之前建好，否则首次开窗时树还不存在。
            // 缺 BG（`_r.Window == null`）时 `Build` 自己记一行日志并放弃，不影响其它功能。
            ToolPanel.Build(_r.Window, () => ToolsClicked?.Invoke());
            // 左侧「修改压缩纪要」面板（10-31）：与工具侧边栏同一套做法 —— 运行时搭树、不碰预制体；
            // 输入框**克隆** `_r.ChatInput` 拿到同一套字体/底图/光标色（`ToolPanel` 克隆 ConfigBtn 同理）。
            CompactEditPanel.Build(_r.Window, _r.ChatInput, t => SummaryEditSubmitted?.Invoke(t));
            if (_r.Window != null && !ToolPanel.HasPanel)
                ModMain.P("[ChatWindow] ★工具侧边栏未能建起（BG 存在但建树失败）→ 顶栏「工具」钮按下去只提示，不影响其余功能");
            // 顶栏「须知」钮（10-23）：运行时克隆 ⚙ ConfigBtn，放在工具钮左侧 8px
            //（坐标实测见 `NoticeButton` 文件头；群聊时由 SetRoomButtonsVisible 一并收起）
            NoticeButton.EnsureChatButton(_r.Window);
            // 删除模式节点（全部可选，缺失=无删除功能）
            if (_r.DeleteButton != null)
            {
                var btn = _r.DeleteButton.GetComponent<Button>();
                if (btn != null) ClickUtils.Attach(btn, () => DeleteBtnClicked?.Invoke());
            }
            if (_r.CompactButton != null)
            {
                var btn = _r.CompactButton.GetComponent<Button>();
                if (btn != null)
                {
                    _compactBtnRef = btn;
                    ClickUtils.Attach(btn, () => CompactClicked?.Invoke());
                    // 忙态文案组件（TMP 优先，回落 uGUI Text）；取不到就只做置灰，不报错
                    try { _compactLabel = btn.GetComponentInChildren<TMPro.TextMeshProUGUI>(true); } catch { }
                    if (_compactLabel == null) { try { _compactLabel = btn.GetComponentInChildren<Text>(true); } catch { } }
                    // ：压缩按钮接线留痕——"点了没反应"时先看这行在不在
                    ModMain.P("[ChatWindow] 压缩按钮已接线（忙态文案组件=" +
                              (_compactLabel != null ? "找到" : "未找到→只置灰") + "）");
                }
            }
            if (_r.SelectAllBtn != null)
            {
                var btn = _r.SelectAllBtn.GetComponent<Button>();
                if (btn != null) ClickUtils.Attach(btn, SelectAllToggle);
            }
            if (_r.DeleteConfirmBtn != null)
            {
                var btn = _r.DeleteConfirmBtn.GetComponent<Button>();
                if (btn != null) ClickUtils.Attach(btn, OnDeleteConfirmClicked);
            }
            if (_r.CancelBtn != null)
            {
                var btn = _r.CancelBtn.GetComponent<Button>();
                if (btn != null) ClickUtils.Attach(btn, ExitSelecting);
            }
            if (_r.DeleteBar != null) _r.DeleteBar.SetActive(false);   // 平时隐藏，进选择模式才显示
            // 群聊动作按钮（拉人 / 踢人）：可选节点，缺失=两个入口都没有（单聊窗不受影响）
            _inviteBtn = _r.InviteButton;
            _kickBtn = _r.KickButton;
            if (_inviteBtn != null)
            {
                var btn = _inviteBtn.GetComponent<Button>();
                if (btn != null) ClickUtils.Attach(btn, () => { var h = InviteClicked; if (h != null) h(); });
            }
            if (_kickBtn != null)
            {
                var btn = _kickBtn.GetComponent<Button>();
                if (btn != null) ClickUtils.Attach(btn, () => { var h = KickClicked; if (h != null) h(); });
            }
            // 群聊「跳过」（可选节点，缺失=没有这个入口）
            _skipBtn = _r.SkipButton;
            if (_skipBtn != null)
            {
                var btn = _skipBtn.GetComponent<Button>();
                if (btn != null) ClickUtils.Attach(btn, () => { var h = SkipRequested; if (h != null) h(); });
            }
            // 头像槽位：直取行模板内的 RawImage（模板 inactive，画布为零；克隆行时才激活）。
            _userAvatarImg = _r.UserAvatarImg;
            _npcAvatarImg = _r.NpcAvatarImg;
            // 底部 token 统计条：节点由预制体提供（BG/StatsBar），代码不再构建；缺失=null 容错
            _tokensBar = _r.StatsBarText;
            Close();
        }

        // ------------------------------------------------------------------
        // 底部 token 统计条（节点由预制体提供：BG/StatsBar，样式在编辑器里随意调；
        // 数值格式化仍在此处——对齐 stats.py 头注释："数字格式化由 C# UI 侧做"）
        // ------------------------------------------------------------------

        /// <summary>刷新底部 token 统计文本（接收 stats_update 事件 / open_chat 首屏基线）。</summary>
        public void SetStats(JObject stats)
        {
            if (stats == null) return;
            if (_tokensBar == null)
            {
                // 主动开口开窗后底部一直显示预制体的默认文本 "New Text"，
                // 有两种可能，必须分开：
                //   (a) 从没调到 SetStats —— 那是历史回放被丢弃（已在 ChatPresenter 侧修）
                //   (b) 节点没解析到 —— AbChatPanel 找的是 `BG/StatsBar/Text` ?? `BG/StatsBar`；
                //       若两者都不是 Text（可见那行在更深一层），`_tokensBar` 就是 null，
                //       本函数一直静默 no-op，界面永远停在预制体默认文本。
                // 这行把 (b) 单独钉死。只吼一次，不刷屏。
                if (!_statsBarWarned)
                {
                    _statsBarWarned = true;
                    ModMain.P("[ChatWindow] SetStats: ★统计栏节点未解析★（BG/StatsBar/Text 与 BG/StatsBar "
                              + "都没拿到 Text 组件）→ 统计被静默丢弃，界面会一直停在预制体的默认文本。"
                              + "反之若日志里没有这一行，问题就在调用侧（根本没调 SetStats）");
                }
                return;
            }
            string line = BuildStatsLine(stats);
            // 群聊里两种帧都可能到：
            //   · 带 `roomUsage` = **本群口径**（本群期间累计花费）—— 它自带"本群期间"字样，不加人名；
            //   · 不带（老后端/成员口径）= 某一个成员的账 ⇒ 必须标名字，否则会被读成"这个群的统计"。
            // 单聊一律不加前缀（窗口标题已经是他本人）。
            if (IsRoomMode && stats["roomUsage"] == null)
            {
                string who = (string)stats["npc_id"] ?? "";
                if (!string.IsNullOrEmpty(who)) line = who + " ｜ " + line;
            }
            if (_tokensBar.text != line) _tokensBar.text = line;
        }

        private static string BuildStatsLine(JObject stats)
        {
            // ★ 群聊优先走"本群口径"（09-18 用户拍板）★：群窗底部那一行报的是**本群期间**的花费，
            //   不是某个成员的账 —— 群里每人一份账本与上下文，"各自的上下文/占用率"没有可加语义，
            //   所以本群那一行**只报花费**（总/输入/输出/缓存率 + 本群轮步），且**必须自报口径**
            //   （"本群期间"），否则玩家会拿它跟单聊窗那行对比（那是"我在这个 NPC 身上花的"，含单聊）。
            var ru = stats["roomUsage"] as JObject;
            if (ru != null) return BuildRoomStatsLine(ru);
            var tu = stats["tokenUsage"] as JObject;
            if (tu == null) return "";
            long total = LongOf(tu["totalTokens"]);
            long billed = LongOf(tu["billedInputTokens"]);
            long cache = LongOf(tu["cacheReadTokens"]);
            long uncached = LongOf(tu["uncachedInputTokens"]);
            long output = LongOf(tu["outputTokens"]);
            if (total == 0 && billed == 0 && output == 0) return "";
            string hit = tu["cacheHitPercent"]?.ToString();   // "79" 或 null(无输入不显示)
            var sb = new StringBuilder("总 ");
            sb.Append(FormatTok(total))
              .Append(" ｜ 输入 ").Append(FormatTok(billed))
              .Append("（缓存 ").Append(FormatTok(cache))
              .Append(" / 未缓存 ").Append(FormatTok(uncached)).Append("）")
              .Append(" ｜ 输出 ").Append(FormatTok(output));
            if (!string.IsNullOrEmpty(hit)) sb.Append(" ｜ 缓存率 ").Append(hit).Append("%");
            // 上下文：**总量与当前值分开判**（09-18 修）。
            //   总量（contextWindow）= 配置事实，任何时候都该显示；
            //   当前值优先用**实测**（pressureTokens = 最新一次请求的输入规模），
            //   还没实测过（刚开窗 / agent 刚 resume / 刚压缩或删除过 —— 那几种情况下实测值
            //   被 Python 侧**故意作废**）就退到 estimatedTokens（当前 surface 的估算，与压缩器
            //   同一把尺子）并打上 ≈，连估算都没有才写 —。
            //   ★ 旧写法把总量也塞进"实测 > 0"的分支里 ⇒ 没实测时**整段消失**，
            //     看起来像"这一项被去掉了"（用户 09-18 反馈的原话）。
            var cp = stats["contextPressure"] as JObject;
            if (cp != null)
            {
                long win = LongOf(cp["contextWindow"]);
                long ctx = LongOf(cp["pressureTokens"]);
                bool est = false;
                if (ctx <= 0)
                {
                    ctx = LongOf(cp["estimatedTokens"]);
                    est = ctx > 0;
                }
                if (ctx > 0 || win > 0)
                {
                    sb.Append(" ｜ 上下文 ");
                    if (ctx > 0) sb.Append(est ? "≈" : "").Append(FormatTok(ctx));
                    else sb.Append("—");
                    if (win > 0)
                    {
                        sb.Append("/").Append(FormatTok(win));
                        string occ = cp[est ? "estimatedOccupancyPercent" : "occupancyPercent"]?.ToString();
                        if (!string.IsNullOrEmpty(occ)) sb.Append(" ").Append(occ).Append("%");
                    }
                }
            }
            var ss = stats["sessionStats"] as JObject;
            if (ss != null)
            {
                long turns = LongOf(ss["turns"]);
                long steps = LongOf(ss["steps"]);
                // 「累计」不是废话：删除/压缩之后它和"现存几轮"不再相等，
                // 不标清楚就会被读成"历史还剩这么多轮"（Python 侧 contextPressure.liveTurns 给现存）。
                if (turns > 0 || steps > 0) sb.Append(" ｜ 累计 ").Append(turns).Append("轮/").Append(steps).Append("步");
                var liveTok = cp != null ? cp["liveTurns"] : null;
                if (liveTok != null && LongOf(liveTok) != turns)
                    sb.Append("（现存 ").Append(LongOf(liveTok)).Append(" 轮）");
            }
            return sb.ToString();
        }

        /// <summary>本群期间那一行的渲染（口径：只算群内回合，不含各成员的单聊）。</summary>
        private static string BuildRoomStatsLine(JObject ru)
        {
            long billed = LongOf(ru["inputTokens"]);
            long cache = LongOf(ru["cacheReadTokens"]);
            long uncached = LongOf(ru["uncachedInputTokens"]);
            long output = LongOf(ru["outputTokens"]);
            long total = LongOf(ru["totalTokens"]);
            long turns = LongOf(ru["turns"]);
            long steps = LongOf(ru["steps"]);
            if (total == 0 && output == 0 && turns == 0)
                return "本群期间 ｜ 还没有发言 ｜ 只统计群内消耗（不含各成员的单聊）";
            var sb = new StringBuilder("本群期间 ｜ 总 ");
            sb.Append(FormatTok(total))
              .Append(" ｜ 输入 ").Append(FormatTok(billed))
              .Append("（缓存 ").Append(FormatTok(cache))
              .Append(" / 未缓存 ").Append(FormatTok(uncached)).Append("）")
              .Append(" ｜ 输出 ").Append(FormatTok(output));
            string hit = ru["cacheHitPercent"]?.ToString();
            if (!string.IsNullOrEmpty(hit)) sb.Append(" ｜ 缓存率 ").Append(hit).Append("%");
            if (turns > 0 || steps > 0)
                sb.Append(" ｜ 本群 ").Append(turns).Append("轮/").Append(steps).Append("步");
            sb.Append(" ｜ 不含各自单聊");
            return sb.ToString();
        }

        private static long LongOf(JToken t)
        {
            if (t == null) return 0;
            try { return Convert.ToInt64(t); } catch { }
            try { return (long)Convert.ToDouble(t); } catch { }
            return 0;
        }

        private static string FormatTok(long n)
        {
            if (n >= 1000000) return (n / 1000000.0).ToString("0.#") + "M";
            if (n >= 1000) return (n / 1000.0).ToString("0.#") + "k";
            return n.ToString();
        }

        private void Update()
        {
            PortraitCache.PumpCapture();   // 立绘快照队列：开窗渲染成功后延迟一帧抓 RT（窗口开着 RT 才有效）
            // 立绘逐行贴图：读盘限流 + 给已就绪的待贴行贴图。
            // ★ 这一行是"只开对话窗时头像也能出现"的唯一保证：`PortraitCache.PumpLoad` 此前
            //   只挂在 `ContactPresenter.Update` 上，而通讯录关闭时它所在根节点是 OFF 的。
            RowAvatars.Pump();
            // 群成员立绘预热（开群时逐个走立绘 API，严格串行；不在预热时是空转）
            RoomPortraitWarmup.Pump();
            // ③ 智能滚底：上滚 → 停止跟随（回看历史）。
            //   下滚**不**在这里恢复跟随（手感修复）：旧实现是 `else if(wheel<0)_followingBottom=true`，
            //   而 LateUpdate 每帧 `verticalNormalizedPosition = 0f` —— 于是**向下滚一格 = 下一帧瞬移到底**，
            //   用户报的"向下一下子就到最新消息"就是这一行。跟随的恢复改由 LateUpdate 按**真实位置**判定
            //   （见 AtBottom），滚回底部才恢复；向上回看中途误触下滚不再把人拽走。
            var wheel = Input.mouseScrollDelta.y;
            if (wheel > 0.01f) _followingBottom = false;
            // 滚到顶 → 自动加载更早（11-21 分页窗口）
            //   只在**真的滚到顶**时触发一次；靠 `_moreLoading` 防重入，且加载完位置补偿会把
            //   视口停在原处，所以不会"一到底就连环触发"。滚轮向上是唯一触发源 ——
            //   不用 `AtTop()` 每帧轮询，因为内容高度变化也会让"在顶"为真（会误触发）。
            if (wheel > 0.01f) TryAutoLoadEarlier();
            // 删除模式两段确认：武装超过 3 秒未再击 → 自动解除
            if (_confirmArmed && Time.time - _confirmArmTime > 3f) DisarmConfirm();
            // 插入更早历史后的视口锚定（11-21）：必须等布局结算，见 `TickEarlierAnchor`
            TickEarlierAnchor();
            // 窗口拖动（10-22 UI 批次：钉死在右侧的窗挡场景，走路/打架时尤其碍事）
            TickDrag();
            // 版式恢复（11-21：位置跨局记住）：开窗后等两帧再写回，见 `TickRestoreLayout`
            TickRestoreLayout();
        }

        private void LateUpdate()
        {
            // ① delta 合并刷新：一帧一次写 Text
            //    ★ Render = 折空行 + 富文本白名单（10-31）：流式期间就要收掉空行、把裸标签
            //      变成可渲染的色。定稿那几条路（`OverwriteStreaming` / `SetRowText` /
            //      `ChatBubble.SetText`）走的是**同一个**整形，否则定稿瞬间会"跳一下"。
            //      见 BubbleTextCore。
            if (_deltaDirty && _bubbleText != null)
            {
                _bubbleText.text = BubbleTextCore.Render(_delta.ToString());
                _deltaDirty = false;
                // ★ 11-20：文字变了，宽度也要跟着长 ★
                //   原先这里只写 text、不重算宽度 ⇒ 宽度停在起泡那一刻（`BeginNpcBubble("")`，
                //   文字是空串、量不出）算出的值上，整段流式期间都不再变。
                //   用户 11-20 截图（一段短话横到天花板）就是这个形态。
                //   每帧调用是安全的：`ApplyBubbleWidth` 内部有 `ShouldRewrite` 去抖
                //   （变化 < 1.5 不写），没变化时不会触发布局重排。
                if (_deltaView != null) _deltaView.OnStreamTextChanged();
            }
            // ③ 智能滚底
            if (!IsOpen || _r == null || _r.Scroll == null || _r.Content == null) return;
            try
            {
                // 新消息浮动角标：每帧刷一次显隐/文案（跟随状态机的唯一出口，见 `RefreshNewMsgBadge`）
                RefreshNewMsgBadge();
                // ② 折叠行位置校正：这一帧由它接管（点开后绝不再钉底），处理/等待完直接返回
                if (_foldAnchorRow != null) { TickFoldAnchor(); return; }
                // ★ 批量重建后的保持期：无条件钉底 + 忽略"位置不在底部"（见 `_pinHoldFrames`）。
                //   放在折叠校正之后、常规跟随之前：折叠校正是用户动作、优先级更高。
                if (_pinHoldFrames > 0)
                {
                    _pinHoldFrames--;
                    _followingBottom = true;
                    _lastChildCount = _r.Content.childCount;
                    _lastContentH = _r.Content.rect.height;
                    _r.Scroll.verticalNormalizedPosition = 0f;
                    return;
                }
                if (!_followingBottom)
                {
                    // 未跟随：用户滚/拖回底部 → 自愈恢复跟随（判据是**位置**不是滚轮方向）
                    if (AtBottom()) { _followingBottom = true; _newRowsWhileUnfollowed = 0; }   // 人肉回底 = 已读，角标作废
                    return;
                }
                // 已跟随：只在**内容真的长高/多了行**时钉底。
                // 旧实现每帧无条件写 `= 0f`，于是拖动/惯性在下一帧就被抹掉（滚动条拖不动、
                // 内容拖不动，这是"手感很差"的另一半）；改成"只在增长时钉"后，用户拖动得以生效，
                // 下一分支再据此停止跟随。
                int cc = _r.Content.childCount;
                float ch = _r.Content.rect.height;
                bool grew = cc != _lastChildCount || ch > _lastContentH + 0.5f;
                _lastChildCount = cc;
                _lastContentH = ch;
                if (grew) _r.Scroll.verticalNormalizedPosition = 0f;   // 新内容到达 → 钉底
                else if (!AtBottom()) _followingBottom = false;        // 用户自己滚/拖走了 → 停止跟随
            }
            catch { }   // 布局尚未就绪时读 rect 可能抛，滚底失败不该影响主流程
        }

        /// <summary>批量重建（历史重放 / 未闭合回合整段重演）之后调：**钉底并保持几帧**。
        ///
        /// 为什么不能只靠"内容变高就钉底"：一次画几十行时布局要好几帧才结算，钉底那一刻
        /// 量到的 Content 高度可能还是中间态；随后布局定稿（或视口 rect 变化）会让位置**不再
        /// 在底部**，而下一帧是"内容没长高"⇒ 走 `else if (!AtBottom()) _followingBottom = false;`
        /// ⇒ 跟随被**永久关掉**，视图停在顶部（实机：重开窗后得自己往下翻）。
        /// 保持期内无条件钉底 + 不吃那条判决，把结算窗口整个盖住；顺手把两个跟随判据归零，
        /// 让保持期结束后的第一帧必然判"长了"→ 再钉一次。
        ///
        /// 帧数取 6（约 0.1s @60fps）：足够覆盖 UGUI 的"实例化 → 帧末重建布局 → 再结算"
        /// 两三轮，又短到不会跟用户抢屏幕（他真在这 0.1s 内上滚，下一帧照常接管）。</summary>
        public void HoldScrollAtBottom(int frames = 6)
        {
            _followingBottom = true;
            _lastChildCount = -1;
            _lastContentH = -1f;
            if (frames > _pinHoldFrames) _pinHoldFrames = frames;
        }

        // ------------------------------------------------------------------
        // 「↓ N条新消息」浮动钮（10-22 UI 批次）
        //
        // 智能滚底的上半场做得对（上翻不被拽走），缺的是下半场：停在历史里时新回复
        // **零提示**，玩家不知道下面有没有话。这里按跟随状态机显隐一个浮钮，点它
        // = 复用 `HoldScrollAtBottom`（不另写一份钉底 —— 保持帧那套坑已经踩过一次）。
        // 计数口径：`RegisterRow` 里"未跟随且非重放保持期"的新行（气泡/行动行/系统
        // 提示/分隔行都算——玩家关心的是"下面多了东西"）。
        // ------------------------------------------------------------------

        private void RefreshNewMsgBadge()
        {
            bool want = IsOpen && !_followingBottom && _newRowsWhileUnfollowed > 0
                        && _r != null && _r.Scroll != null;
            if (want && _newMsgBtn == null)
            {
                Text lbl;
                _newMsgBtn = ChatUiBuilder.MakeNewMsgButton(_r.Scroll.transform, out lbl);
                _newMsgBtnLabel = lbl;
                var btn = _newMsgBtn != null ? _newMsgBtn.GetComponent<Button>() : null;
                if (btn != null) ClickUtils.Attach(btn, () => ScrollToNewest());
            }
            if (_newMsgBtn == null) return;
            if (_newMsgBtn.activeSelf != want) _newMsgBtn.SetActive(want);
            if (want && _newMsgBtnLabel != null)
                _newMsgBtnLabel.text = _newRowsWhileUnfollowed == 1 ? "↓ 新消息"
                    : _newRowsWhileUnfollowed > 9 ? "↓ 9+条新消息"
                    : "↓ " + _newRowsWhileUnfollowed + "条新消息";
        }

        private void ScrollToNewest()
        {
            _newRowsWhileUnfollowed = 0;
            if (_newMsgBtn != null) _newMsgBtn.SetActive(false);   // 立刻收，不等下一帧
            HoldScrollAtBottom(2);
        }

        // ------------------------------------------------------------------
        // 窗口拖动（10-22 UI 批次；11-21 修「拖不动」）
        //
        // 四件纪律：
        //   ① **不新建 MonoBehaviour**：IL2CPP 下每个自定义组件都要 RegisterTypeInIl2Cpp，
        //      漏注册就是启动期 TypeInitializationException。拖动挂在已注册的 ChatWindow
        //      自己的 Update 上（Input 轮询，无事件系统依赖）。
        //   ② **起点判据 = 标题带 + 头顶无可点件**：标题带按几何算（窗口顶 48px，与代码版
        //      Title 等高）——AB 预制件的按钮 09-08 起挂在 BG 直下、Title 节点已废，
        //      按节点找会两版分叉。"头顶有没有按钮"交给 EventSystem 射线问最上层，
        //      **绝不手写控件矩形名单**（名单必漏，漏一个就是"点 ✕ 结果把窗拖走了"）。
        //   ③ 位移换算与钳制全在 `DragCore`（纯算术可单测）：屏幕像素 ≠ 画布单位
        //      （CanvasScaler 缩放），不钳制一拽就飞出屏。
        //   ④ **屏幕坐标换算必须带画布相机**（11-21 修的实机 bug，见下）。
        //
        // ── 11-21 修的两处「拖不动」──────────────────────────────────────────
        //   ① `RectangleContainsScreenPoint(w, mp, null)` / `ScreenPointToLocalPointInRectangle(..., null, ...)`：
        //      **null 相机只对 Overlay 成立**。AB 面板的根 Canvas 是 `ScreenSpaceCamera`
        //      （Player.log 实证：`Canvas mode=ScreenSpaceCamera order=0`），传 null 的后果
        //      不是报错，而是**恒不命中** ⇒ `InTitleBand` 永远 false ⇒ 一次都拖不起来。
        //      同一个坑 `HoverTip`（气泡一个都不出）与 `DramaAiOption`（屏坐标全错）各踩过一次。
        //      `PressOnSelectable` 的 `PointerEventData` 同理会拿不到射线命中 ⇒
        //      "点 ✕ 也把窗拖走"，故一并补 `pressEventCamera`。
        //   ② 钳制用错了锚点公式：`ClampAnchored` 是给**右中锚**的代码版窗口写的，
        //      AB 版的 `BG` 是**居中点锚**（anchor=pivot=(0.5,0.5)）。拿右中公式钳居中锚，
        //      1920 视口下算出 `x ∈ [-720, 0]`（正确 `±360`）⇒ **只能往左拖**。
        //      现在按锚点分派：点锚走 `DragCore.ClampToViewport`，拉伸/右中锚走 `ClampAnchored`。
        //
        // 位置**跨局记住**（11-21 用户定案「这就和配置里面的设置一样的啊」）：
        // 落盘在 `<数据根>\window_layout.json`（见 `WindowLayoutStore`），
        // **不写 config.json**（那个归 Python 独占解释，两边同写必然互相覆盖）。
        // ------------------------------------------------------------------

        // ------------------------------------------------------------------
        // 独立子 Canvas（11-21：修「拖动卡」）
        //
        // ── 症状与证据 ────────────────────────────────────────────────────
        // 用户：「拖动明显能感觉很卡」。Player.log 里拖动那 10 秒：
        //     185 fps → 184 fps → 23 → 78 → 53 → 38 → **28** → 62 → 44 → 77 → 117 → 146 → 184
        // 同一份日志的 `TickProfiler` 显示我们所有常驻回调合计 **< 1ms**
        // （diag 0.01ms / teamjoin 0.02ms / hotkeys 0.01ms）—— 卡顿**不在我们的代码里**。
        //
        // ── 根因：UGUI 的重算单位是「Canvas」，不是「元素」────────────────────
        // 一个 Canvas 下所有 Graphic 共享批次；**改其中任何一个元素的 transform，
        // 整个 Canvas 的顶点都要重算**（CanvasUpdateRegistry 的 Rebuild 是全量的）。
        // 而 `BG` 底下挂着：Scroll→Viewport→Content→N 行消息（每行头像 RawImage +
        // 气泡 Image + Text）、输入区、8 颗顶栏钮、工具侧边栏、压缩纪要面板……
        // 拖动每帧写一次 `BG.anchoredPosition` ⇒ 每帧重算这几百上千个顶点。
        // 消息越多越卡 —— 正好对上"帧率是**逐渐**掉下去的"这个观察。
        //
        // ── 解法：给 BG 挂自己的 Canvas（Unity 官方对这个问题的标准答案）──
        // 子 Canvas 内部的顶点坐标是**相对于子 Canvas** 的；移动子 Canvas 只更新它的
        // 变换矩阵，内部 N 个 Graphic 的顶点**一个都不用重算**。
        // 类比：现在是"把整个房间的家具挪一遍再拍照"，挂子 Canvas 后是
        // "拍一张照片，然后移动相框"。
        //
        // ── 三个必须注意的点（每条都是踩过的坑）────────────────────────────
        //  ① **必须补一个 `GraphicRaycaster`**（11-21 实机事故，见下"按钮全失灵"）
        //  ② **不要开 `overrideSorting`**（11-21 第二版修正，见下"排序别碰"）
        //  ③ **幂等**：`Init` 可能被重复调用（宿主重建后 `AssembleResident` 再跑一次），
        //     已有 Canvas 就不再挂第二个（挂两个 = 两倍批次 + 排序错乱）。
        //
        // ── ① 的事故：第一版没补 Raycaster，**整个对话窗的按钮全失灵**（11-21 用户报）──
        // 我当时的注释写的是"父级那个已经覆盖本子 Canvas 的内容，再加会双倍拾取"——
        // **这句话是错的，正好反了**。真实机制：
        //     `Graphic.OnEnable` 里 `GraphicRegistry.RegisterGraphicForCanvas(graphic.canvas, this)`，
        //     其中 `graphic.canvas` 取的是**最近的 Canvas 祖先**。
        //     ⇒ 挂了子 Canvas 之后，BG 底下所有按钮/输入框都注册到**子 Canvas**；
        //     ⇒ 而 `GraphicRaycaster` 只查**自己所属 Canvas** 的注册表
        //        （`GraphicRegistry.GetGraphicsForCanvas(canvas)`）；
        //     ⇒ 父级 raycaster 挂在 `UIChatAi` 上，查的是 `UIChatAi` 那张表 ⇒ **一个都看不到**。
        // 结果是**零拾取**而不是双倍拾取：按钮点不动、输入框点不进、Tooltip 不响应。
        //
        // 为什么拖动**还能用**（这是当时没立刻怀疑到子 Canvas 的原因）：
        //   `InTitleBand` 走 `RectTransformUtility.RectangleContainsScreenPoint`，
        //   那是**直接算矩形**、不经过射线 ⇒ 拖动照常。
        //   而"控件避让"(`PressOnSelectable`) 走 `EventSystem.RaycastAll` ⇒ 它也失效了，
        //   只是失效的表现是"按下去不避让"，在按钮本身也点不动的前提下看不出来。
        //
        // 教训：**射线是按 Canvas 分表的**。挂子 Canvas = 新开一张表，
        //       必须自带一个 raycaster，否则那张表没人查。
        //       本仓 `BigPortrait` 文件头"自建 Canvas 要自带 GraphicRaycaster"说的就是这件事，
        //       我当时读到了却理解反了（以为"不加才安全"，实际"不加就收不到事件"）。
        //
        // ── ② 的修正：**别碰排序**（11-21 第二版）──────────────────────────────
        // 第一版我写了 `overrideSorting = true; sortingOrder = 0;`，理由是"保证整块 BG
        // 作为一个整体与其它 UI 的先后关系不变"。**这个理由同样是推的，不是测的。**
        //
        // 后来翻 Player.log 才看清运行时事实：
        //     游戏 UI 层   = `ScreenSpaceCamera order=0`（16 次）
        //     我们的实例根 = `WorldSpace order=20`（8 次）
        // 两者是**两套渲染模式**，不是同一套 order 序列上的两个值。
        // 子 Canvas 一旦 `overrideSorting=true`，它就变成**分离的排序条目**、
        // 拿自己那个 order 去参与比较 ⇒ 脱离 `UIChatAi` 的 WorldSpace 上下文，
        // 内容可能被压到 BG 自己的背景之下（窗口在、内容不见）。
        //
        // 而**我们本来要修的只是"重算粒度"，排序根本没坏**。
        // ⇒ 保持 `overrideSorting=false`（默认）让子 Canvas 继承父级上下文，
        //   不改变任何既有排序，只改变重算粒度。这是"只动该动的东西"。
        //
        // 对照：`UnreadBanner` 用 `overrideSorting=true + order=2400` 是**对的**，
        // 但那是**自建根 Canvas**（挂 `g.root` 下、与游戏层级无关），必须自己决定 order
        // 才能盖住东西。**同样是 Canvas，一个是根、一个是子，结论相反** —— 不能照抄。
        // ------------------------------------------------------------------

        /// <summary>给 `BG`（`_r.Window`）挂一个自己的 Canvas，让拖动只更新变换矩阵、
        /// 不重算内部顶点。幂等；失败只记一行日志（拖动会退回"卡但能用"）。
        ///
        /// ⚠ **必须同时挂 `GraphicRaycaster`**（见本节 ①）：子 Canvas 是独立的一张
        /// Graphic 注册表，父级的 raycaster 查不到它 ⇒ 不挂就整个窗点不动。
        /// </summary>
        private void EnsureOwnCanvas()
        {
            try
            {
                var bg = _r != null ? _r.Window : null;
                if (bg == null) { ModMain.P("[Chat] 独立画布跳过：BG 为空（拖动会卡，功能不受影响）"); return; }
                if (bg.GetComponent<Canvas>() != null)
                {
                    // 已有 Canvas（预制体自带或上次已挂）：**仍要补 raycaster** ——
                    // 幂等早退不能跳过这一步，否则"第二次 Init 时缺 raycaster"会让按钮又失灵。
                    EnsureOwnRaycaster(bg.gameObject);
                    ModMain.P("[Chat] 独立画布已存在（预制体自带或上次已挂），不重复添加");
                    return;
                }
                var cv = bg.gameObject.AddComponent<Canvas>();
                // ★ 不覆盖排序（overrideSorting 保持默认 false）★
                //
                // 这是**故意不同**于 `UnreadBanner` 的选择，理由见本节 ②：
                //   `UnreadBanner` 是**自建根 Canvas**（挂 `g.root` 下，与游戏层级无关），
                //   必须自己决定 order（2400）才能盖住东西；
                //   而我们是**嵌套子 Canvas**，挂在实例根 `UIChatAi` 之内 ——
                //   那个根的运行时实测是 `WorldSpace order=20`（Player.log 8 次，
                //   与游戏 UI 层的 `ScreenSpaceCamera order=0` 是两套渲染模式），
                //   所以子 Canvas 只要"跟着父级走"就天然保持原有先后关系。
                //
                // 反过来开 `overrideSorting=true` 会让子 Canvas 变成一个**分离的排序条目**，
                // 用自己那个 order 去和别的 Canvas 比较 ⇒ 脱离 `UIChatAi` 那套 WorldSpace
                // 上下文，内容可能被压到 BG 自己的背景之下（窗口还在、内容不见了）。
                // 而"保持继承"没有这个风险：它不改变任何既有排序，只改变**重算粒度**——
                // 而重算粒度才是本轮要修的东西，排序本来就没坏。
                EnsureOwnRaycaster(bg.gameObject);
                // ⚠ 不加 CanvasScaler：缩放由父级 Canvas 的 CanvasScaler 统一决定，
                //   子 Canvas 再挂一个会把坐标再缩一次（窗口会缩成四分之一）。
                ModMain.P("[Chat] 独立画布已挂到 BG：拖动时只更新变换矩阵，内部顶点不再重算"
                          + "（不覆盖排序=继承父级 WorldSpace，已补 Raycaster，不加 Scaler）"
                          + " 父级Canvas=" + DescribeCanvas(cv));
            }
            catch (Exception e)
            {
                // 挂不上就退回原样：拖动仍可用，只是卡。绝不因为性能优化而让窗打不开。
                ModMain.P("[Chat] 独立画布挂载失败（拖动会卡，功能不受影响）: " + e.Message);
            }
        }

        /// <summary>诊断用：把子 Canvas 自己与它所属的**根 Canvas** 的关键参数摊成一行。
        ///
        /// 为什么需要这行：子 Canvas 的排序行为取决于"根的 renderMode/order"这套
        /// 运行时事实（本仓实测根的实例是 `WorldSpace order=20`，而游戏 UI 层是
        /// `ScreenSpaceCamera order=0` —— 两套模式）。这类事实**没法从源码推断**，
        /// 只能让它在日志里自己说出来，下次改这块时一眼可查。
        /// </summary>
        private static string DescribeCanvas(Canvas self)
        {
            try
            {
                if (self == null) return "?";
                var root = self;
                try { var rc = self.rootCanvas; if (rc != null) root = rc; } catch { }
                return "self[mode=" + self.renderMode + " order=" + self.sortingOrder
                       + " override=" + self.overrideSorting + "]"
                       + " root[mode=" + root.renderMode + " order=" + root.sortingOrder
                       + " override=" + root.overrideSorting
                       + " name=" + (root != null ? root.name : "?") + "]";
            }
            catch (Exception e) { return "<异常 " + e.Message + ">"; }
        }

        /// <summary>
        /// 给挂了子 Canvas 的 `BG` 补一个 `GraphicRaycaster`（**不补就整个窗点不动**）。
        ///
        /// 幂等：已有就跳过（`Init` 会因宿主重建重跑）。
        ///
        /// ⚠ **不要再加第二个** —— 一个 Canvas 一个 raycaster 就够；多挂的后果是同一个点
        /// 被拾取两次（按钮回调触发两遍）。这条与"必须补"并不矛盾：
        /// **必须补一个，且只能有一个**。
        /// </summary>
        private static void EnsureOwnRaycaster(GameObject go)
        {
            try
            {
                if (go == null) return;
                if (go.GetComponent<UnityEngine.UI.GraphicRaycaster>() != null) return;   // 已有
                go.AddComponent<UnityEngine.UI.GraphicRaycaster>();
            }
            catch (Exception e)
            {
                ModMain.P("[Chat] 子画布补 Raycaster 失败（窗会点不动！）: " + e.Message);
            }
        }

        private bool _dragging;
        private bool _dragMoved;                     // 本次按下期间真的挪过（松手才落盘）
        private Vector2 _dragLastMouse;
        private Vector2 _dragAppliedPos;             // 上一次真正写进 anchoredPosition 的值（降频用）
        private bool _layoutRestored;                // 本次开窗已恢复过版式
        private int _layoutRestoreWait;              // 等布局结算的帧数（见 `TickRestoreLayout`）
        private static bool _dragDiagLogged;         // 「拖动」钳制域只打一行（排障用）
        private static bool _restoreDiagLogged;      // 「恢复」钳制域单独一行（不与拖动挤占）
        // ⚠ IL2CPP 桥：`EventSystem.RaycastAll` 的出参是 **Il2CppSystem** 的 List（Unhollower
        // 签名如此），托管 List 传不进去（CS1503）；它也不实现 IEnumerable ⇒ 只能 Count+索引。
        private static readonly Il2CppSystem.Collections.Generic.List<UnityEngine.EventSystems.RaycastResult> _dragHits
            = new Il2CppSystem.Collections.Generic.List<UnityEngine.EventSystems.RaycastResult>();

        /// <summary>拖动写位置的**最小步长**（画布单位）。位移没攒够这么多就先不写 ——
        /// 每写一次都要走一遍 UGUI 的布局/渲染同步（见 `EnsureOwnCanvas` 的说明）。
        ///
        /// 取 2 而不是更大：`BG` 宽 1200、1920 屏，2 个画布单位 ≈ 2 像素，
        /// 肉眼完全看不出跳格；而鼠标慢速微调时能省掉大量"不到 2 单位"的写入。
        /// 快速拖动时每帧位移远超 2，等于不降频（不会拖尾）。
        /// </summary>
        private const float DragStepEpsilon = 2f;

        private void TickDrag()
        {
            if (!IsOpen || _r == null || _r.Window == null) return;
            try
            {
                if (Input.GetMouseButtonDown(0))
                {
                    Vector2 mp = Input.mousePosition;
                    _dragging = InTitleBand(mp) && !PressOnSelectable(mp);
                    _dragMoved = false;
                    _dragLastMouse = mp;
                    if (_dragging && _r.Window != null)
                        _dragAppliedPos = _r.Window.anchoredPosition;   // 降频基准：按下那一刻的位置
                }
                else if (_dragging && Input.GetMouseButtonUp(0))
                {
                    _dragging = false;
                    if (_dragMoved) FlushDragPosition();   // 松手补最后一笔（降频可能漏掉尾巴）
                    _dragMoved = false;
                }
                if (!_dragging || !Input.GetMouseButton(0)) return;

                Vector2 cur = Input.mousePosition;
                float dx = cur.x - _dragLastMouse.x, dy = cur.y - _dragLastMouse.y;
                _dragLastMouse = cur;
                var canvas = _r.Window.GetComponentInParent<Canvas>();
                float sf = canvas != null && canvas.scaleFactor > 0.001f ? canvas.scaleFactor : 1f;
                var rt = _r.Window;
                float nx = rt.anchoredPosition.x + DragCore.ScreenToCanvas(dx, sf);
                float ny = rt.anchoredPosition.y + DragCore.ScreenToCanvas(dy, sf);
                ClampWindowToViewport(rt, sf, ref nx, ref ny);
                // ★ 降频：攒够 `DragStepEpsilon` 才写 ★ 见该常量的说明。
                //   注意基准是**上一次真正写入的值**（`_dragAppliedPos`），不是 `anchoredPosition`
                //   ——后者已经被我们自己改过，拿它当基准等于没有阈值。
                if (Mathf.Abs(nx - _dragAppliedPos.x) < DragStepEpsilon
                    && Mathf.Abs(ny - _dragAppliedPos.y) < DragStepEpsilon) return;
                rt.anchoredPosition = new Vector2(nx, ny);
                _dragAppliedPos = rt.anchoredPosition;
                _dragMoved = true;                   // 钳在边界上原地不动时不算"动过"
            }
            catch { }   // 拖动失败绝不影响聊天主流程（下一帧自然重试）
        }

        /// <summary>把当前位置**立刻**写进去（松手时补最后一笔，绕过降频阈值）。
        ///
        /// 为什么要补：降频会让"最后不到 2 单位的位移"留在 `_dragAppliedPos` 里没写进去，
        /// 松手后窗会停在前一格 —— 玩家视角是"松手时窗弹回去一点点"。
        /// </summary>
        private void FlushDragPosition()
        {
            try
            {
                if (_r == null || _r.Window == null) return;
                var rt = _r.Window;
                var canvas = rt.GetComponentInParent<Canvas>();
                float sf = canvas != null && canvas.scaleFactor > 0.001f ? canvas.scaleFactor : 1f;
                float x = rt.anchoredPosition.x, y = rt.anchoredPosition.y;
                ClampWindowToViewport(rt, sf, ref x, ref y);
                var pos = new Vector2(x, y);
                if (Mathf.Abs(pos.x - rt.anchoredPosition.x) > 0.01f
                    || Mathf.Abs(pos.y - rt.anchoredPosition.y) > 0.01f)
                {
                    rt.anchoredPosition = pos;
                    _dragAppliedPos = pos;
                }
            }
            catch { }
        }

        /// <summary>
        /// 按窗口的**锚点类型**选钳制公式，并把"可视域"取准。
        ///
        /// 可视域优先读**父节点 rect**（画布根，全屏 stretch）——它比
        /// `Screen.width / scaleFactor` 准：ScreenSpaceCamera 画布在极端分辨率下
        /// 这两者会差几十个单位，而 `anchoredPosition` 的坐标系正是父 rect 那一套。
        /// </summary>
        private void ClampWindowToViewport(RectTransform rt, float sf, ref float x, ref float y,
                                           string why = "拖动")
        {
            float viewW = 0f, viewH = 0f;
            var parentRt = rt.parent != null ? UiRects.Of(rt.parent) : null;
            if (parentRt != null) { viewW = parentRt.rect.width; viewH = parentRt.rect.height; }
            bool readOk = viewW >= 1f && viewH >= 1f;
            if (!readOk) { viewW = Screen.width / sf; viewH = Screen.height / sf; readOk = viewW >= 1f && viewH >= 1f; }
            // ★ 两个来源都读不到（开窗首帧父级 rect 未结算、或 Screen 尺寸异常）时**不钳制** ★
            //   照钳的后果是把窗钉到 (0,0)：0×0 视口会让 `ClampToViewport` 的合法域反转，
            //   取中点正好是 0 —— 玩家视角="拖一下窗就跳回正中"，且没有任何报错。
            //   宁可这一帧不钳（位置由下一帧的正常读数纠正），也不写一个错值进去。
            if (!readOk) return;

            Vector2 aMin = rt.anchorMin, aMax = rt.anchorMax, piv = rt.pivot;
            bool pointAnchor = Mathf.Abs(aMin.x - aMax.x) < 0.001f && Mathf.Abs(aMin.y - aMax.y) < 0.001f;
            float ox, oy;
            if (pointAnchor)
            {
                // AB 预制体版（BG 居中点锚 1200×800）
                DragCore.ClampToViewport(x, y, rt.rect.width, rt.rect.height, viewW, viewH,
                                         aMin.x, aMin.y, piv.x, piv.y, out ox, out oy);
            }
            else
            {
                // 代码版构建器（Window 右中锚）——历史公式，超宽时贴右
                DragCore.ClampAnchored(x, y, rt.rect.width, rt.rect.height, viewW, viewH,
                                       out ox, out oy);
            }
            // 拖动与恢复**各打一行**（两个 static 标志互不挤占）。
            //   共用一个标志的话，恢复发生在开窗第 2 帧、早于任何拖动，
            //   那行日志永远是"恢复"那次的值 —— 而排障要看的正是拖动那次。
            bool logged = why == "恢复" ? _restoreDiagLogged : _dragDiagLogged;
            if (!logged)
            {
                if (why == "恢复") _restoreDiagLogged = true; else _dragDiagLogged = true;
                // 一行给全：锚点类型 + 尺子（窗/视口）+ 钳制前后。若"往某个方向拖不动"，
                // 这一行能立刻看出是公式选错（锚点类型）还是视口读错（尺子差几十）。
                ModMain.P("[Chat] 窗口钳制域(" + why + ")：锚=" + (pointAnchor ? "点锚" : "拉伸/右中锚")
                          + "(" + aMin.x + "," + aMin.y + ")" + " pivot=(" + piv.x + "," + piv.y + ")"
                          + " 窗=" + rt.rect.width.ToString("F0") + "x" + rt.rect.height.ToString("F0")
                          + " 视口=" + viewW.ToString("F0") + "x" + viewH.ToString("F0")
                          + " 请求(" + x.ToString("F0") + "," + y.ToString("F0") + ")"
                          + " → 钳后(" + ox.ToString("F0") + "," + oy.ToString("F0") + ")"
                          + (Mathf.Abs(ox - x) > 0.01f || Mathf.Abs(oy - y) > 0.01f ? " 已夹" : ""));
            }
            x = ox; y = oy;
        }

        /// <summary>按下点是否落在标题带（窗口顶 48px，几何判定，见上 ②）。</summary>
        private bool InTitleBand(Vector2 mousePos)
        {
            var w = _r.Window;
            // ★ 相机必须随画布 ★ 见本节 ④：AB 面板是 ScreenSpaceCamera，传 null 恒不命中
            var cam = UiKit.CanvasCamera(w);
            if (!RectTransformUtility.RectangleContainsScreenPoint(w, mousePos, cam)) return false;
            if (!RectTransformUtility.ScreenPointToLocalPointInRectangle(w, mousePos, cam, out var lp)) return false;
            return lp.y >= w.rect.yMax - 48f;
        }

        /// <summary>按下点头顶是否压着任何可交互件（名牌/✕/设置/删除…）。EventSystem 说了算。
        ///
        /// ── 关于相机（11-21 复核，**与 `InTitleBand` 不同**）─────────────────────
        /// 这里**不需要也不能**指定相机：
        ///   · 不需要：`GraphicRaycaster.Raycast` 内部是
        ///     `var cam = eventData.pressEventCamera ?? canvas.worldCamera;`，
        ///     即它本来就用**自己画布**的相机，我们传什么都只影响"要不要覆盖它"；
        ///   · 不能：本工程引用的 uGUI 里 `PointerEventData.pressEventCamera` 是**只读属性**
        ///     （`=> pointerPressEventCamera`，internal set）—— 赋值直接 CS0200 编译不过。
        /// 所以控件避让沿用"让 EventSystem 自己解析相机"，这与它平时的点击判定完全同源。
        /// `InTitleBand` 那边必须传相机，是因为 `RectTransformUtility` 没有"自己猜"这回事，
        /// 传 null 就按 Overlay 算 —— 两者不是同一个问题，别照搬。
        ///
        /// 诊断：`[Chat] 拖动起点` 一行给出命中数。若出现"点 ✕ 却把窗拖走"，
        /// 命中数会是 0（= 射线整个没打中），据此可直接区分"控件避让失效"与"判据写错"。</summary>
        private bool PressOnSelectable(Vector2 mousePos)
        {
            var es = UnityEngine.EventSystems.EventSystem.current;
            if (es == null) return false;
            var ped = new UnityEngine.EventSystems.PointerEventData(es) { position = mousePos };
            _dragHits.Clear();
            es.RaycastAll(ped, _dragHits);
            for (int i = 0; i < _dragHits.Count; i++)
                if (_dragHits[i].gameObject.GetComponentInParent<UnityEngine.UI.Selectable>() != null) return true;
            return false;
        }

        // ------------------------------------------------------------------
        // 版式恢复/落盘（11-21：位置跨局记住）
        // ------------------------------------------------------------------

        /// <summary>
        /// 开窗时把上次记住的位置应用回去（**等两帧**再应用：`Open()` 当帧刚 SetActive，
        /// 父级 rect 还是 0，这时候算钳制域会拿到 0×0 的视口，把窗钳到 (0,0)）。
        ///
        /// 为什么需要"等两帧"而不是直接读：真机实证 —— 游戏 `CloseUI` 会**销毁**宿主，
        /// 每次开窗都是 `g.ui.OpenUI` 重新实例化（Player.log 里 `创建宿主` 出现 23 次），
        /// `BG.anchoredPosition` 回到预制体的 (0,0)。所以"记住位置"必须由我们在
        /// 实例化之后写回去，而那一刻布局还没结算。
        ///
        /// 恢复也**过钳制**：换了分辨率/窗口模式后，上次存的位置可能已经在屏外，
        /// 直接写回去就是"窗口看不见了"（玩家视角=丢了）。
        /// </summary>
        /// <summary>版式恢复最多等这么多帧：超时就放弃（回出厂版式），不许无限重试。
        /// 3 帧足够让 `Open()` → `SetActive(true)` → 布局结算跑完；等不到说明画布本身有问题。</summary>
        private const int LayoutRestoreMaxWait = 3;

        private void TickRestoreLayout()
        {
            if (_layoutRestored) return;
            if (!IsOpen || _r == null || _r.Window == null) return;
            if (++_layoutRestoreWait < 2) return;         // 等两帧让父级 rect 结算

            try
            {
                var rt = _r.Window;

                // ★ 视口读不到就先别落定 ★
                //   否则会拿"出厂/上次的原始位置"直接写下去，而那个位置是按**旧视口**算的
                //   （换了分辨率就在屏外），且 `_layoutRestored` 已置位 ⇒ 再也不重试。
                //   有界重试：`LayoutRestoreMaxWait` 帧后仍读不到就放弃（回出厂版式，
                //   比把窗摆到屏幕外好）。
                var parentRt = rt.parent != null ? UiRects.Of(rt.parent) : null;
                bool viewReady = parentRt != null && parentRt.rect.width >= 1f && parentRt.rect.height >= 1f;
                if (!viewReady)
                {
                    if (_layoutRestoreWait >= LayoutRestoreMaxWait)
                    {
                        _layoutRestored = true;
                        ModMain.P("[Chat] 版式恢复放弃：等了 " + _layoutRestoreWait
                                  + " 帧仍读不到画布视口（用出厂版式）");
                    }
                    return;                                // 下一帧再来
                }

                _layoutRestored = true;
                var saved = WindowLayoutStore.Current;
                if (!saved.HasPos && !saved.HasSize) return;   // 首次安装：保持预制体出厂版式

                var canvas = rt.GetComponentInParent<Canvas>();
                float sf = canvas != null && canvas.scaleFactor > 0.001f ? canvas.scaleFactor : 1f;

                if (saved.HasSize)
                {
                    // 尺寸的上限要等这里才拿得到（视口），所以读盘时只做了下限校验
                    float w, h;
                    if (WindowLayoutCore.SanitizeSize(saved.W, saved.H,
                                                      parentRt.rect.width, parentRt.rect.height,
                                                      out w, out h))
                        rt.sizeDelta = new Vector2(w, h);
                }

                if (saved.HasPos)
                {
                    float x = saved.X, y = saved.Y;
                    ClampWindowToViewport(rt, sf, ref x, ref y, "恢复");
                    rt.anchoredPosition = new Vector2(x, y);
                    ModMain.P("[Chat] 窗口版式已恢复：位置 " + x.ToString("F0") + "," + y.ToString("F0")
                              + " 尺寸 " + rt.rect.width.ToString("F0") + "x" + rt.rect.height.ToString("F0")
                              + "（存 " + saved.X.ToString("F0") + "," + saved.Y.ToString("F0") + "）");
                }
            }
            catch (Exception e)
            {
                _layoutRestored = true;      // 异常也要落定，否则每帧重试刷日志
                ModMain.P("[Chat] 版式恢复失败（用出厂位置）：" + e.Message);
            }
        }

        /// <summary>把当前位置写进 `WindowLayoutStore`（**关窗时调一次**；只在真变化时落盘）。
        ///
        /// ── 为什么从"松手就写"改成"关窗才写"（11-21）────────────────────────
        /// 用户实测日志里一次拖动出现了 **4 次** `[WindowLayout] 已落盘`：
        ///     -74,5 → -214,14 → 245,113 → 5,-28
        /// 说明"松手"在一次连续拖动里被识别了 4 次（鼠标微松/事件抖动都算），
        /// 而每次落盘都是**同步文件 IO**，直接阻塞主线程 —— 与拖动卡顿叠加。
        /// 关窗只发生一次，落盘代价摊薄到"关窗那一下"，而位置本来就只需要记住最后一次。
        ///
        /// 代价：游戏崩溃/强杀时位置丢失，回到上次成功保存的位置。
        /// 用户已确认接受（"关窗时才写"）。
        /// </summary>
        private void SaveWindowPos()
        {
            try
            {
                if (_r == null || _r.Window == null) return;
                var rt = _r.Window;
                WindowLayoutStore.Save(rt.anchoredPosition.x, rt.anchoredPosition.y,
                                       rt.rect.width, rt.rect.height, false);
            }
            catch (Exception e) { ModMain.P("[Chat] 位置落盘失败：" + e.Message); }
        }

        /// <summary>滚动位置是否已在底部（跟随的唯一恢复判据）。
        ///
        /// `verticalNormalizedPosition` 0=底 1=顶，但**内容比视口矮时 Unity 返回 1**（没得滚却报"在顶"）
        /// —— 直接拿它判"在底部"会让**短对话永远判成不在底部**，新消息再也不会自动滚出来。
        /// 所以先用高度差判"没得滚"，那才是任何位置都算底部。
        /// 视口 rect 读不到（如预制件里 Viewport 是退化 0×0）时退回 ScrollRect 自身矩形。</summary>
        private bool AtBottom()
        {
            try
            {
                var s = _r.Scroll;
                float vh = s.viewport != null ? s.viewport.rect.height
                                              : ((RectTransform)s.transform).rect.height;
                float range = s.content.rect.height - vh;
                if (range <= 0.5f) return true;              // 没得滚 = 任何位置都在底部
                return s.verticalNormalizedPosition <= 0.02f;
            }
            catch { return true; }   // 读不到就当作在底部：退化成"永远跟随"，不会把跟随锁死
        }

        // ------------------------------------------------------------------
        // 折叠行位置校正（「不够就滚够」）
        // ------------------------------------------------------------------

        /// <summary>折叠行被点开/收起：先关掉流式跟随（语义="用户接管了屏幕"，与滚轮上滚那一行同义），
        /// 再记下这一行，等布局生效后只滚"还差的那一点"。
        /// 跟随的恢复不用管：他滚回底部时 `AtBottom()` 会自愈（见 LateUpdate 未跟随分支）。</summary>
        private void OnFoldToggled(GameObject row)
        {
            if (row == null || _r == null || _r.Content == null) return;
            _foldAnchorRow = row;
            _foldAnchorHeight = RowHeight(row);
            _foldAnchorWait = 0;
            _followingBottom = false;
        }

        /// <summary>每帧推进折叠校正；处理完（或放弃）即清状态。
        ///
        /// 为什么要等：`Text.text` 赋值只标脏布局，重建发生在**帧末**（CanvasUpdateRegistry），
        /// 点击那一帧读到的行高还是旧的 → 会误判成"已经装得下"。所以等到行高真的变了才动手，
        /// 最多等 3 帧（超时就收手——宁可这次不补位置，也绝不把状态永久挂住）。</summary>
        private void TickFoldAnchor()
        {
            try
            {
                var row = _foldAnchorRow;
                if (row == null) { ClearFoldAnchor(); return; }          // 行已销毁（裁剪/重放）→ 放弃

                float h = RowHeight(row);
                if (Mathf.Abs(h - _foldAnchorHeight) < 0.5f && _foldAnchorWait < 3)
                {
                    _foldAnchorWait++;
                    return;                                             // 布局还没生效 → 这一帧仍不钉底
                }

                var content = _r.Content;
                float vh = ScrollViewportHeight();
                if (content != null && vh > 1f && h > 0f)
                {
                    float s = content.anchoredPosition.y;                // 已滚过多少像素（内容坐标=视口顶）
                    float below = RowTopBelowViewportTop(row);
                    float room = Mathf.Max(0f, content.rect.height - vh - s);
                    float delta = ToolFoldPolicy.RevealDelta(below, h, vh, room);
                    if (delta > 0.5f)
                        content.anchoredPosition = new Vector2(content.anchoredPosition.x, s + delta);
                }
            }
            catch { }   // 布局没就绪时读 rect 可能抛：位置没补上不影响主流程（不该把聊天窗搞崩）
            ClearFoldAnchor();
        }

        private void ClearFoldAnchor()
        {
            _foldAnchorRow = null;
            _foldAnchorHeight = 0f;
            _foldAnchorWait = 0;
        }

        /// <summary>某个 UI 节点的矩形高度（取不到算 0）。</summary>
        private static float RectHeight(GameObject go)
        {
            try
            {
                if (go == null) return 0f;
                var rt = go.GetComponent<RectTransform>();
                return rt != null ? rt.rect.height : 0f;
            }
            catch { return 0f; }
        }

        private static float RowHeight(GameObject row) { return RectHeight(row); }

        /// <summary>某行的**顶边**在视口顶边之下多少像素（负数 = 行顶已滚到视口顶之上）。
        /// 行模板 pivot 未必是 (0.5,0.5)（StepGroup 模板实测是），所以按 pivot 折算顶边，
        /// 不假设 `position` 就是顶边。世界 Y 向上 → 用"视口顶 Y − 行顶 Y"。</summary>
        private float RowTopBelowViewportTop(GameObject row)
        {
            var rowRt = row != null ? row.GetComponent<RectTransform>() : null;
            var vp = _r != null && _r.Scroll != null && _r.Scroll.viewport != null
                     ? _r.Scroll.viewport : (_r != null ? _r.Viewport : null);
            if (rowRt == null || vp == null) return 0f;
            float vTop = vp.position.y + vp.rect.height * (1f - vp.pivot.y);
            float rTop = rowRt.position.y + rowRt.rect.height * (1f - rowRt.pivot.y);
            return vTop - rTop;
        }

        /// <summary>视口高（滚动计算的参照）。与 `AtBottom()` 同款兜底，但这里**多一步**：
        /// 预制件里 Viewport 可能是退化 0×0（`uichatai.ab` 已知），此时退回 ScrollRect 自身矩形，
        /// 否则"装不装得下"会被算成 0 高 → 不但不补位置，还会把滚动范围算歪。</summary>
        private float ScrollViewportHeight()
        {
            try
            {
                var s = _r.Scroll;
                float vh = s.viewport != null ? s.viewport.rect.height : 0f;
                if (vh <= 1f) vh = ((RectTransform)s.transform).rect.height;
                return vh;
            }
            catch { return 0f; }
        }

        // ------------------------------------------------------------------
        // 开/关/状态
        // ------------------------------------------------------------------

        /// <summary>打开窗口（重置内容）并设定当前 NPC——Present 随后赶历史。
        /// npcId 非空时自动回填顶部 NpcInput（面板按钮/通讯录/主动传音唤出即带出当前 NPC 中文名；
        /// NpcInput 为只读展示，不再支持手改切换对话对象）</summary>
        public void OpenFor(string npcId)
        {
            CurrentNpcId = npcId ?? "";
            ClearRoom();                      // ★ 单聊模式必须清掉群状态，否则帧闸会放错人进来
            // 离开群聊：成员立绘预热必须停 —— 它渲的是**与单聊共用的**模板槽位，
            // 不停就会把单聊刚渲的那张脸存成群成员的 id（静默错脸）。
            RoomPortraitWarmup.Stop();
            SetRoomButtonsVisible(false);
            if (_r != null && _r.NpcLabel != null && CurrentNpcId.Length > 0)
                _r.NpcLabel.text = CurrentNpcId;
            Open();
        }

        // ------------------------------------------------------------------
        // 群聊（Room）模式
        //
        // 设计要点：**房间是一个与 npc_id 并列的场合标识，不是某个 npc 的属性**
        //（`docs/archive/2026-09-17-群聊编排/设计定稿.md` §4.3：微信群可以随便拉人踢人而记录不断，靠的是群有稳定 id）。
        // 所以群窗里 `CurrentNpcId` 刻意留空 —— 它是"当前对话对象"，
        // 而群聊没有唯一对话对象。留空同时让所有既有的 `CurrentNpcId` 守卫
        // （open_chat / dispose_agent / 删除 / 压缩）**自然早退**，不会拿群 id 去当 npc 用。
        // ------------------------------------------------------------------

        /// <summary>当前房间 id（空 = 单聊）。群聊下 `CurrentNpcId` 为空，两者互斥。</summary>
        public string CurrentRoomId { get; private set; }

        /// <summary>当前房间成员（npc_id 列表，不含玩家）。帧路由与说话人归属都靠它。</summary>
        public List<string> CurrentRoomMembers { get; private set; }

        /// <summary>当前群名（`open_room` 回执里的 title）。给"往哪个群拉人"这类提示语用。</summary>
        public string CurrentRoomTitle { get; private set; }

        /// <summary>是否处于群聊模式。**所有群聊分支都以它为唯一判据** ——
        /// 单聊时它恒为 false，于是本次改动对既有单聊路径逐字节无影响。</summary>
        public bool IsRoomMode { get { return !string.IsNullOrEmpty(CurrentRoomId); } }

        private void ClearRoom()
        {
            CurrentRoomId = "";
            CurrentRoomMembers = null;
            CurrentRoomTitle = "";
            _roomSpeaker = "";
            _roomSpeakerId = "";
        }

        /// <summary>以群聊模式开窗：设房间身份、顶部标签显示群名、清内容。
        /// 历史由 Presenter 收到 `open_room` 回执后赶 `ReplaceHistory(items)`。</summary>
        public void OpenForRoom(string roomId, string title, List<string> members)
        {
            CurrentNpcId = "";                // 群聊没有唯一对话对象（见上）
            CurrentRoomId = roomId ?? "";
            CurrentRoomMembers = members ?? new List<string>();
            CurrentRoomTitle = title ?? "";
            _roomSpeaker = "";
            _roomSpeakerId = "";
            if (_r != null && _r.NpcLabel != null)
                _r.NpcLabel.text = string.IsNullOrEmpty(title) ? CurrentRoomId : title;
            Open();
            SetRoomButtonsVisible(true);
            // 成员立绘预热：排在 `Open()` **之后**（Open→ResetContent 会整块重建内容，
            // 预热要用的模板槽位与贴图目标都在那之后才稳定）。
            // 名单里**带上玩家**：群聊里玩家行也走逐行取图（见 AppendUserMessage），
            // 而玩家可能这一局还没单聊过（没有快照）。
            StartRoomPortraitWarmup();
        }

        /// <summary>换一批群成员（拉人/踢人回执）。**不重建内容** —— 历史还在屏幕上，
        /// 重建会让列表跳一下；只更新成员表（帧路由/踢人列表用）并给新人排队预热。</summary>
        public void SetRoomMembers(List<string> members, List<string> newlyAdded)
        {
            if (!IsRoomMode) return;
            CurrentRoomMembers = members ?? new List<string>();
            if (newlyAdded != null && newlyAdded.Count > 0)
            {
                var only = new List<string>();
                for (int i = 0; i < newlyAdded.Count; i++)
                    if (!string.IsNullOrEmpty(newlyAdded[i])) only.Add(newlyAdded[i]);
                // ★ 用 `Enqueue` 而不是 `Start`：开群时排下的其他成员可能还在队里，
                //   `Start` 会把整队撤掉（他们的脸就再也不会补上，直到下次开群）。
                if (only.Count > 0) RoomPortraitWarmup.Enqueue(only);
            }
        }

        /// <summary>把"玩家 + 全体成员"排进立绘预热队列。</summary>
        private void StartRoomPortraitWarmup()
        {
            var names = new List<string>();
            if (!string.IsNullOrEmpty(_playerName)) names.Add(_playerName);
            if (CurrentRoomMembers != null)
                for (int i = 0; i < CurrentRoomMembers.Count; i++)
                    if (!string.IsNullOrEmpty(CurrentRoomMembers[i])) names.Add(CurrentRoomMembers[i]);
            if (names.Count > 0) RoomPortraitWarmup.Start(names, _npcAvatarImg);
        }

        /// <summary>顶栏按钮随「群聊 / 单聊」切换的显隐。**每次开窗都要显式设置**（两个方向、
        /// 四个按钮都要设，不能只设一边、指望另一次保持）：拉人/踢人出厂是**隐藏**的、
        /// 压缩/删除出厂是**可见**的，漏设的表现就是"该出现时不出现 / 该消失时还在"，
        /// 而且零报错（与通讯录底栏同一条教训）。节点缺失时静默（预制件没打这个补丁）。
        ///
        /// ★ 群聊里**压缩**必须收起来（docs/archive/2026-09-17-群聊编排/设计定稿.md §9.2.1 定稿）：压缩的单位是 **session**
        ///   （一个 NPC 一辈子的记忆），不是"这个群" —— "压群"的两种解释（压全员 / 压某成员）
        ///   都不成立，正确归属地是他的单聊窗。它在群窗里点下去只会**静默 return**
        ///   （群模式刻意清空 `CurrentNpcId`，守卫早退），比"不支持"更坏：看着能点、点了没反应。
        ///
        /// ★ **删除**在群聊里**照常可见**（v2 已落地，§9.2）：群窗的删除走另一条载荷与另一对
        ///   RPC（uid + `room_delete`），不再是死按钮 —— 所以这里**不碰**它（预制件出厂可见，
        ///   两个模式都用）。当初"一并藏起来"只是 v2 落地前的临时状态。
        ///
        /// ★ **跳过**（`BG/Input/SkipBtn`）与拉人/踢人同一条：出厂隐藏、**只在群聊亮**。
        ///   单聊的一轮就是"玩家问一句、NPC 答一句"，没有"让 NPC 自己接下去"这回事。
        ///
        /// ★ **工具**（运行时克隆的顶栏钮，见 `ToolPanel`）也**只在单聊亮**（10-19 用户拍板）。
        ///   理由：十个工具的 description 里全写着「发起方恒为当前对话 NPC」，而群聊**没有**
        ///   唯一对话对象（`CurrentNpcId` 刻意为空）。一颗"点了不知道在跟谁说"的按钮，
        ///   正是本文件反复写死的"看得见却没用"。群里有话直接说即可。</summary>
        private void SetRoomButtonsVisible(bool show)
        {
            try
            {
                if (_inviteBtn != null) _inviteBtn.SetActive(show);
                if (_kickBtn != null) _kickBtn.SetActive(show);
                if (_skipBtn != null) _skipBtn.SetActive(show);
                if (_r != null && _r.CompactButton != null) _r.CompactButton.SetActive(!show);
                // 工具侧边栏：群里不显示（理由见上方 ★）。顺带把已开的面板收起 ——
                // 否则从单聊切群聊时面板会留在屏幕上，而那里面每一条都指向"某个 NPC"。
                var toolBtn = ToolPanel.ButtonGo;
                if (toolBtn != null) toolBtn.SetActive(!show);
                if (show) ToolPanel.Hide();
                // 编辑面板同理：纪要是某个 NPC 的记忆，群里没有"当前这个人的纪要"这回事
                if (show) CompactEditPanel.Hide();
                // 「须知」钮（10-23）占的 -321.8 那一格，群聊时正是「踢人」的位置
                //（拉人 -249.8 / 踢人 -321.8）。两者必须互斥显示，否则会叠在一起。
                NoticeButton.SetChatButtonVisible(!show);
            }
            catch { }
        }

        /// <summary>帧来自本房间吗（群窗的唯一准入判据）。判据是 `room_id` **相等**，
        /// 而不是"npc 是成员"—— 后者会让别的群的成员帧漏进来。</summary>
        public bool AcceptsRoomFrame(string roomId)
        {
            return IsRoomMode && !string.IsNullOrEmpty(roomId) && roomId == CurrentRoomId;
        }

        /// <summary>重置内容并显示</summary>
        public void Open()
        {
            ResetContent();
            _r.Root.SetActive(true);
            IsOpen = true;
            _followingBottom = true;
            // ★ 每次开窗都重新走一次版式恢复（11-21）★
            //   游戏 `CloseUI` 会销毁宿主（Player.log 实证 23 次「创建宿主」），
            //   新实例的 `BG.anchoredPosition` 是预制体的 (0,0)；不重置这两个位，
            //   第二次开窗就不会再把记住的位置写回去（表现为"只有第一次开窗在正确位置"）。
            _layoutRestored = false;
            _layoutRestoreWait = 0;
        }

        /// <summary>填玩家名（BG/Playerput 左上铭牌）。只读展示，不做切换入口；空名兜底「玩家」。
        /// 同时存下来：群聊里玩家行的头像按它逐行取图（见 `AppendUserMessage`）。</summary>
        public void SetPlayerName(string name)
        {
            _playerName = string.IsNullOrEmpty(name) ? "" : name;
            if (_r == null || _r.PlayerLabel == null) return;
            _r.PlayerLabel.text = string.IsNullOrEmpty(name) ? "玩家" : name;
        }

        public void Close()
        {
            IsOpen = false;
            // ★ 拖动状态必须清（11-21）★
            //   不清的后果：拖到一半按 F9/ESC 关窗，`_dragging` 还是 true；
            //   下次开窗时鼠标可能正好按着，`GetMouseButtonUp` 那帧已经错过 ⇒
            //   窗会"跟着鼠标跑"。
            _dragging = false;
            _dragMoved = false;
            // ★ 位置落盘（11-21 用户定案：**关窗才写**）★
            //   本轮之前是"松手就写"，实测一次拖动写了 4 次盘（同步 IO 阻塞主线程，
            //   与拖动卡顿叠加）。关窗只发生一次，代价摊薄，而位置只需要记住最后一次。
            //   不看 `_dragMoved`：恢复过版式也算"位置与盘上可能不同"，该更新就更新。
            //   必须在 `_r.Root.SetActive(false)` 之前读（关掉后 rect 不再更新）。
            SaveWindowPos();
            // ★ 关窗必须中止在飞的分帧构建（11-20）★
            // `Close` 不走 `ResetContent`，所以它原本是这条路上唯一的漏口：
            // 构建会继续跑（窗口都关了、行还在往隐藏节点里塞），跑完还会执行续跑
            // ——那段续跑里有 `EnterSelecting` / `DrainFrames`，对着一个已经关掉的窗口做，
            // 症状是"下次开窗直接停在选择模式里 / 莫名多出几行"。
            if (_histPending) CancelHistoryBuild("关窗");

            CompactEditPanel.Hide();        // 编辑面板是 BG 的子节点：关窗必须一起收起
            RoomPortraitWarmup.Stop();      // 关窗：停预热（它的捕获任务引用着窗口内的槽位）
            // 角标随窗掩蔽（LateUpdate 关窗后不再刷它；重开若走重放，ResetContent 会连状态一起归零）
            if (_newMsgBtn != null) _newMsgBtn.SetActive(false);
            if (_r != null && _r.Root != null)
            {
                _r.Root.SetActive(false);
                // 事故定案：不要动 UI 层兄弟位次（见 AbContactPanel 注释）。
            }
            ClearStreamingState();
            // 定案（"对话窗打不开"事故根因）：删除旧 CloseAllButMapSafe 收尾——
            // 它=CloseAllUI 强清全部 UI（保留 MapMain），会把刚创建的对话窗实例自己连同游戏
            // 其他 UI 一起强关销毁。关闭登记由 AbChatPanel.CloseViaManager（g.ui.CloseUI）交游戏。
        }

        public void SetBusy(bool busy)
        {
            // 只在真变化时留痕：忙标"该灭没灭"是最难查的一类（卡死就是它），
            // 有这对 ON/OFF 行，下次一眼能看出是"没收到 OFF"还是"UI 没执行 OFF"。
            if (_busy != busy) { try { ModMain.P("[ChatWindow] 忙标 " + (busy ? "ON" : "OFF")); } catch { } }
            _busy = busy;
            if (_r != null && _r.BusyLabel != null)
            {
                if (busy) EnsureBusyText();
                else SetBusyPhase("");   // 不恢复的话：下一次忙时会把「战斗进行中…」当成通用文案
                _r.BusyLabel.SetActive(busy);
            }
        }

        /// <summary>忙提示文案兜底（同意后立刻开窗，首 token 前 4~13s 空窗期要有反馈）。
        /// 预制体里该节点已写字（AB 美术可自定义）则**不动**，只补空白的情形。</summary>
        private void EnsureBusyText()
        {
            try
            {
                if (_r == null || _r.BusyLabel == null) return;
                var t = _r.BusyLabel.GetComponentInChildren<Text>(true);
                if (t != null && string.IsNullOrEmpty(t.text)) t.text = "对方正在斟酌…";
            }
            catch { }
        }

        // ------------------------------------------------------------------
        // 静态行（用户气泡 / NPC 整泡 / 分隔条 / 系统提示）
        // ------------------------------------------------------------------

        private string _busyTextOriginal;   // 首次改写忙标文案前存下的原文（AB 美术可能自定义过）

        /// <summary>把忙标文案改成"当前在等什么"（切磋跨战斗的心跳帧带过来的 phase）。
        /// 空串 = 恢复原文案。只改文本，不动显隐（显隐仍归 <see cref="SetBusy"/>）。
        /// 为什么要改：回合挂着等一场战斗时，「对方正在斟酌…」是**错的** —— 玩家会以为模型卡了，
        /// 而实际是他在打仗、NPC 在等他打完。</summary>
        public void SetBusyPhase(string phase)
        {
            try
            {
                if (_r == null || _r.BusyLabel == null) return;
                var t = _r.BusyLabel.GetComponentInChildren<Text>(true);
                if (t == null) return;
                if (_busyTextOriginal == null) _busyTextOriginal = t.text ?? "";
                string want = _busy && !string.IsNullOrEmpty(phase) ? phase : _busyTextOriginal;
                if (t.text != want) t.text = want;
            }
            catch { }
        }

        /// <summary>玩家泡的**唯一**渲染口（实时回显 / 关窗重演 / get_history 回放都从这里出）。
        /// 入参是账本原串——行首游戏时间戳 `[N年M月(D日)]` **只在这里、只为上屏**剥掉
        /// （10-22 用户需求：气泡别带日期，破坏沉浸感；带戳的那份照旧进账本/发模型）。
        /// 契约细节见 `BubbleTextCore.StripLeadStamp`。
        ///
        /// 剥掉的日期改由**微信式时间分隔行**承担（10-22 追加需求，判档见 `TimeSepCore`）：
        /// 解析行首戳 → 与上一条分隔行比档 → 变了就在本行**上方**插一行居中灰小字。
        /// 只由玩家行驱动（NPC 行账本无日期，驱动了就实时/回放不一致），
        /// 状态随 `ResetContent` 清零 ⇒ 重放重算，与实时逐字同。</summary>
        public RowMeta AppendUserMessage(string text)
        {
            if (string.IsNullOrEmpty(text)) return null;
            int sy, sm, sd;
            if (BubbleTextCore.TryParseLeadStamp(text, out sy, out sm, out sd))
            {
                string sep = TimeSepCore.Label(_sepHas, _sepY, _sepM, _sepD, sy, sm, sd);
                if (sep != null) AppendDivider(sep);
                _sepHas = true; _sepY = sy; _sepM = sm; _sepD = sd;
            }
            text = BubbleTextCore.StripLeadStamp(text);
            if (string.IsNullOrEmpty(text)) return null;   // 整条只剩个时间戳：不画空泡
            // 群聊里玩家行也逐行取图（`_playerName` → unitID → 快照）；单聊传 null = 沿用模板立绘
            var b = new ChatBubble(_r, _r.Content, true, text, IsRoomMode ? _playerName : null);
            BindAvatarClick(b, true, null, null);
            return RegisterRow(b.Row.gameObject);
        }

        // 微信式时间分隔行的"上一条日期"（AppendUserMessage 驱动；ResetContent 清零重算）
        private bool _sepHas;
        private int _sepY, _sepM, _sepD;

        /// <summary>NPC 整泡（回放 / 非流式整段）。</summary>
        /// <param name="speaker">群聊说话人中文名（逐行头像的取图键；空 = 用本回合记住的
        /// `_roomSpeaker`，单聊为空 ⇒ 沿用模板立绘）。</param>
        /// <param name="speakerId">说话人精确 unitID（有就直接取图，不按名解析）。</param>
        public RowMeta AppendNpcStatic(string text, string speaker = null, string speakerId = null)
        {
            if (string.IsNullOrEmpty(text)) return null;
            var b = new ChatBubble(_r, _r.Content, false, text, SpeakerOf(speaker), SpeakerIdOf(speakerId));
            BindAvatarClick(b, false, SpeakerOf(speaker), SpeakerIdOf(speakerId));
            return RegisterRow(b.Row.gameObject);
        }

        /// <summary>取本次气泡该用的说话人：显式给的优先，否则回落"本回合正在说话的人"。
        ///
        /// 返回值有三态，靠它区分"用哪张脸"：
        ///   · **null**（单聊）→ `ChatBubble` 走模板立绘那条老路（单聊观感零变化）；
        ///   · **非空名字**（群聊）→ 逐行取这个人的图；
        ///   · **空串**（群聊但不知道是谁）→ 逐行取图那条路上解析不到 → **这一行不画脸**。
        ///     刻意返回空串而不是 null：群聊里退回模板立绘等于画**上一个单聊 NPC** 的脸，
        ///     那是静默撒谎；没有脸只是少一张图。
        ///     什么时候会"不知道是谁"：窗口关着时那条 `room_state(phase=speak)` 被守卫丢掉后
        ///     又重开窗 / 中途切走再切回 / `phase="end"` 之后还有迟到 delta。
        ///     **不是**帧乱序 —— speak 帧在 Python 侧是 await 在回合之前发的，C# 也是 FIFO。</summary>
        private string SpeakerOf(string speaker)
        {
            if (!string.IsNullOrEmpty(speaker)) return speaker;
            return IsRoomMode ? (_roomSpeaker ?? "") : null;
        }

        /// <summary>`SpeakerOf` 的 id 版 —— **判据必须与它逐字同构**，否则会出现
        /// "名字用了 A、id 却用了 B"这种最坏组合（画错人且名字看着对）。
        /// 所以两处都用同一个条件：显式给的优先，否则回落本回合记住的那个。
        /// 显式给了空串（"知道是群聊但不知道是谁"）⇒ id 也返回空串，让取图那条路隐藏头像。</summary>
        private string SpeakerIdOf(string speakerId)
        {
            if (!string.IsNullOrEmpty(speakerId)) return speakerId;
            return IsRoomMode ? (_roomSpeakerId ?? "") : null;
        }

        /// <summary>把「点开头像放大立绘」接到这一行上（`BigPortrait.Bind`）。
        ///
        /// 判据**必须与 `SpeakerOf` 的三态逐字同构** —— 那才是"这一行画谁的脸"的权威判据，
        /// 而"点开显示谁"必须和"画的是谁"**是同一个人**，否则就是"看着是甲、点开是乙"，
        /// 比不能点更坏（与 `RowAvatars.Apply` 拒绝"模板那张别人的脸"同一条理由）：
        ///   · **单聊**（`who == null`）→ 这张脸是**当前对话对象**的 ⇒ 点开显示 `CurrentNpcId`；
        ///   · **群聊某人**（`who` 非空）→ 点开显示**该说话人**，带精确 id（不按名猜，重名会错）；
        ///   · **群聊但不知道是谁**（`who == ""`）→ **不挂**。头像本身都已按"不画错人"隐藏了
        ///     （见 `RowAvatars.Apply` 的空串语义），再挂个点了弹别人脸的按钮就是撒谎。
        ///
        /// 玩家行同理走 `_playerName`（单聊/群聊都成立：玩家行画的恒是玩家）。
        /// 失败静默 —— 点不开图不该影响一条气泡的正常显示。</summary>
        private void BindAvatarClick(ChatBubble b, bool isUser, string who, string whoId)
        {
            try
            {
                if (b == null || b.Avatar == null) return;
                if (isUser)
                {
                    BigPortrait.Bind(b.Avatar, _playerName, null);   // 玩家 unitId 本窗口不持有，按名解析
                    return;
                }
                if (who == null)                                     // 单聊：窗口对象就是这一行的人
                {
                    BigPortrait.Bind(b.Avatar, CurrentNpcId, null);
                    return;
                }
                if (who.Length == 0) return;                         // 群聊未知：不挂（见上）
                BigPortrait.Bind(b.Avatar, who, whoId);
            }
            catch (Exception e)
            {
                ModMain.P("[ChatWindow] BindAvatarClick: " + e.Message);
            }
        }

        /// <summary>记下"本回合正在说话的人"（`room_state(phase=speak)` 写、`end` 清）。
        /// 补的是 `step(text)` 那条**不带说话人**的正文路径 —— 缺省为空时它建的泡会退回
        /// 模板那张陈旧的脸，而这是静默的。
        ///
        /// ★ 名字与 id **必须同一次写**（这就是本方法收成一个入口的原因）：
        ///   分开写就会出现"名字是新的、id 还是上一轮的" —— 那一行的头像会是**别人**，
        ///   而名字看着完全正常（比全空更糟）。</summary>
        public void SetRoomSpeaker(string who, string whoId = null)
        {
            _roomSpeaker = who ?? "";
            _roomSpeakerId = whoId ?? "";
        }

        public GameObject AppendDivider(string label)
        {
            if (string.IsNullOrEmpty(label)) return null;
            var d = new ChatDivider(_r, _r.Content);
            d.SetText(label);
            if (d.Root != null) RegisterRow(d.Root);
            return d.Root;
        }

        /// <summary>系统提示行（居中灰字）。返回该行的 Text 供调用方原位更新
        /// （手动压缩的「正在压缩中」→ 结果反馈），普通调用忽略返回值即可。</summary>
        public Text AppendSystemNotice(string text)
        {
            if (string.IsNullOrEmpty(text)) return null;
            var go = TemplateUtils.Instantiate(_r.SystemTemplate, _r.Content);
            if (go == null)
            {
                // （用户反馈"压缩按钮按下去没反应"）旧版这里静默 return null：
                // 预制体缺 BG/Templates/SystemLine 时，**压缩进度/删除提示/回合异常全部隐形**，
                // 看起来就像"按钮没接上"。同 09-13 红点事故的教训：不许静默失败。
                if (!_sysTplWarned)
                {
                    _sysTplWarned = true;
                    ModMain.P("[ChatWindow] ★系统提示行模板缺失（BG/Templates/SystemLine）→ " +
                              "所有系统提示都不会显示（压缩进度/删除提示/回合异常都静默消失）；" +
                              "请在该 AB 预制体补 BG/Templates/SystemLine 节点。");
                }
                return null;
            }
            var t = go.GetComponent<Text>();
            if (t != null) t.text = text;
            RegisterRow(go);
            return t;
        }
        private static bool _sysTplWarned;   // 模板缺失只吼一次（每行提示都吼会刷屏）

        // ------------------------------------------------------------------
        // 分页窗口（11-21：修「消息越多越卡」）
        //
        // 只渲染**服务端给的最近 N 个回合**，上方留一行「↑ 上划加载更早的消息」。
        //
        // ★ 数据来源：**服务端分页**，不是本地切片 ★
        //   用户否决了"本地切片"的方案，理由正确：那样每次开窗仍要把全部历史
        //   （真机 42.1KB / 169 条）传一遍，"等很久"一点没省。
        //   现在 `open_chat` 只取最近 20 回合，更早的由 `ChatPresenter` 发
        //   `get_history(before_seq, before_turn)` 拉回来 —— 本类**不持有全量**。
        //
        // 本类只做两件事：
        //   ① 建/撤「加载更早」入口行（`RefreshMoreRow`）；
        //   ② 把 Presenter 送来的那一批**插到顶部**并补偿滚动位置（`InsertEarlier`）。
        // ------------------------------------------------------------------

        /// <summary>由 Presenter 告知"上方还有没有更早的历史"，并刷新入口行（11-21）。</summary>
        /// <param name="hasMore">还有更早的可取。</param>
        /// <param name="hiddenCount">还没渲染的条目数（用于文案；-1 = 未知，文案就不报数）。</param>
        public void SetEarlierState(bool hasMore, int hiddenCount)
        {
            _histHasMore = hasMore;
            _histHiddenCount = hiddenCount;
            _moreLoading = false;              // 有回音了，把"正在加载"放掉
            RefreshMoreRow();
        }

        /// <summary>按当前状态建/更新/撤掉「加载更早」那一行。
        ///
        /// 三条纪律：
        ///  ① **不进 `_rows`**：它不是一条消息，不该参与删除模式选中、不该计入统计。
        ///     只作为 `Content` 的一个子节点存在（`SetAsFirstSibling` 钉到最前）。
        ///  ② **幂等**：已存在就只更新文案，不重建（重建会让它闪一下）。
        ///  ③ **不需要时立刻撤掉**：加载到头之后还留着这行 = 点了没反应，比没有更糟。
        /// </summary>
        private void RefreshMoreRow()
        {
            try
            {
                if (_r == null || _r.Content == null) return;
                bool need = _histHasMore;
                string label = need
                    ? (_moreLoading
                        ? "正在加载更早的消息…"
                        : (_histHiddenCount > 0
                            ? "↑ 上划加载更早的消息（还有 " + _histHiddenCount + " 条）"
                            : "↑ 上划加载更早的消息"))
                    : "";

                if (!need || label.Length == 0)
                {
                    if (_moreRow != null) { TemplateUtils.DestroySafe(_moreRow); _moreRow = null; _moreRowText = null; }
                    return;
                }
                if (_moreRow != null)
                {
                    // 已存在：只更新文案（幂等路径）
                    if (_moreRowText != null) _moreRowText.text = label;
                    return;
                }
                if (_r.SystemTemplate == null) return;   // 没有提示行模板：静默放弃（滚到顶仍可触发）
                var go = TemplateUtils.Instantiate(_r.SystemTemplate, _r.Content);
                if (go == null) return;
                _moreRow = go;
                _moreRowText = go.GetComponent<Text>();
                if (_moreRowText != null)
                {
                    _moreRowText.text = label;
                    _moreRowText.raycastTarget = true;   // 要能点（系统提示行出厂是不吃的）
                }
                // 点击 = 加载更早。`ClickUtils` 是委托桥三步写法（本仓统一走它）。
                var btn = go.GetComponent<Button>();
                if (btn == null)
                {
                    // 模板本身不是按钮：补一个透明的点击区（不改观感，只加可点性）
                    go.AddComponent<CanvasRenderer>();
                    var img = go.AddComponent<Image>();
                    img.color = new Color(0f, 0f, 0f, 1f / 255f);   // 极低但不为 0：α=0 会被剔除出射线
                    img.raycastTarget = true;
                    btn = go.AddComponent<Button>();
                }
                ClickUtils.Attach(btn, RequestEarlier);
                go.transform.SetAsFirstSibling();        // 钉在最前（所有消息之上）
                ModMain.P("[Chat] 「加载更早」行已建：" + label);
            }
            catch (Exception e) { ModMain.P("[Chat] 刷新「加载更早」行失败：" + e.Message); }
        }

        /// <summary>用户点了「加载更早」/ 滚到顶 —— 交给 Presenter 发请求（11-21）。
        ///
        /// ⚠ 本类**不自己发请求**：它不持有 `WsClient`（见文件头"明确不做"），
        ///   也不该知道 RPC 名字。这里只抬高一个标志并触发事件。
        /// </summary>
        private void RequestEarlier()
        {
            if (!_histHasMore || _moreLoading) return;
            _moreLoading = true;
            RefreshMoreRow();                    // 立刻显示"正在加载…"（网络往返期间要有反馈）
            try { EarlierRequested?.Invoke(); }
            catch (Exception e) { ModMain.P("[Chat] 触发「加载更早」失败：" + e.Message); _moreLoading = false; }
        }

        /// <summary>把**服务端送来的一批更早条目**插到顶部（11-21）。
        ///
        /// ── 为什么必须补偿滚动位置 ────────────────────────────────────────────
        /// 往顶部插入 N 行会让内容整体变高，而 `ScrollRect.verticalNormalizedPosition` 是
        /// **比例**（0=底 1=顶）。内容变高后同样的比例对应的是**不同的绝对位置**
        /// ⇒ 画面会往上跳一段（玩家视角="点一下，刚刚看的内容全跑下面去了"）。
        /// 解法：插入前记"已滚距离"，插入后按新的内容高度换算回比例。
        /// 换算在 `HistoryWindowCore.ScrollAfterPrepend`（有单测）。
        ///
        /// `items` 必须是**按时间正序**的一条完整批次（服务端已排好）。
        /// 返回实际建出的行数（0 = 这一批一个可见条目都没有）。
        /// </summary>
        public int InsertEarlier(JArray items)
        {
            if (items == null || items.Count == 0) return 0;
            if (_r == null || _r.Content == null) return 0;
            int built = 0;
            float vp = 0f, oldH = 0f, oldScrollPx = 0f;
            try
            {
                // ---- ① 记下插入前的位置 ----
                var sc = _r.Scroll;
                vp = sc.viewport != null ? sc.viewport.rect.height
                                         : ((RectTransform)sc.transform).rect.height;
                oldH = _r.Content.rect.height;
                float range = oldH - vp;
                if (range > 0.5f)
                    oldScrollPx = (1f - sc.verticalNormalizedPosition) * range;

                // ---- ② 按序建好，暂存 ----
                //   不能边建边 `SetAsFirstSibling`（那会把顺序倒过来）：
                //   先建完记下引用，最后按倒序逐个挪到最前、保持相对次序。
                var made = new List<GameObject>(items.Count);
                _histGroup = null;       // 跨插入的折叠组不延续（这一段是独立的历史段）
                _histGroupMeta = null;

                // ★ 日期分隔条的"上一条日期"必须**从这里重新起算**（11-21 实机 bug）★
                //   症状：插入更早历史后，分隔条出现 `2年8月3日 → 3年7月4日 → 8月19日 → 2年8月3日`
                //   这种年份倒退的乱序（用户截图）。
                //   根因：`_sepHas/_sepY/_sepM/_sepD` 是**跨行累进**状态 ——
                //   它记的是"刚画过的上一条日期"，从新到旧插入时那个值属于**更晚的**历史，
                //   于是首批旧消息的每一条都被判成"日期变了"（或该画的不画）。
                //   修法：插入前把它清空，让这一批从**它自己的第一条**重新累进；
                //   插入完再把它复原成原来那条（那个值仍然代表"内容区末尾的上一条日期"）。
                bool saveSepHas = _sepHas;
                int saveSepY = _sepY, saveSepM = _sepM, saveSepD = _sepD;
                _sepHas = false; _sepY = _sepM = _sepD = 0;
                // ★ 必须收集**整段新增节点**，不是"最后一个"（11-21 实机 bug）★
                //   症状：插入更早历史后，日期分隔行全部堆在内容**末尾**，
                //   而不是各自插在对应消息的上方（用户截图：末尾连着三行日期）。
                //
                //   根因：一个 item 可能产出**多个**子节点 —— `AppendUserMessage` 在
                //   日期变化时会先 `AppendDivider(...)` 再建气泡（见 `AppendUserMessage`），
                //   于是 `childCount` 一次 +2。旧代码只取 `GetChild(after - 1)`（最后一个 = 气泡），
                //   **分隔行没进 `made`** ⇒ 后续 `SetAsFirstSibling` 只搬了气泡，
                //   分隔行留在原地 = 堆在末尾。
                //
                //   修法：把 `[before, after)` 这段**全部**收下，保持它们在 Content 里的
                //   原始先后（分隔行在前、气泡在后），后面倒序挪到最前时就自然还原成
                //   "日期在上、消息在下"。
                for (int i = 0; i < items.Count; i++)
                {
                    int before = _r.Content.childCount;
                    ApplyHistoryItem(items[i]);
                    int after = _r.Content.childCount;
                    for (int k = before; k < after; k++)
                    {
                        Transform ch = _r.Content.GetChild(k);
                        if (ch != null) made.Add(ch.gameObject);
                    }
                }
                // 收口跨批的折叠组（末尾可能停在 tool_call 没等到 result）
                var g = _histGroup;
                _histGroup = null;
                _histGroupMeta = null;
                try { FinishHistoryGroup(g); } catch { }

                // 复原"内容区末尾的上一条日期"：它说的是**最新那一段**，与刚插入的更早那批无关。
                // 不复原的后果：之后再来的实时消息，分隔条判据会拿旧消息的日期当基准 ⇒ 多画/漏画一行。
                _sepHas = saveSepHas; _sepY = saveSepY; _sepM = saveSepM; _sepD = saveSepD;

                // ---- ③ 挪到最前（保持原顺序）----
                for (int i = made.Count - 1; i >= 0; i--)
                {
                    if (made[i] != null) made[i].transform.SetAsFirstSibling();
                }
                // 「加载更早」行必须仍在最前（它比刚插入的更早）
                if (_moreRow != null) _moreRow.transform.SetAsFirstSibling();

                // ---- ④ 补偿滚动位置（**必须等布局结算**）----
                //   ★ 11-21 实机 bug：刚 `Instantiate` 完就 `rect.height`，
                //     读到的还是**旧高度**（布局要等一帧）⇒ `added ≈ 0`
                //     ⇒ `ScrollAfterPrepend` 把视口推到 **v=1（顶部）**
                //     ⇒ 用户手指还在滚轮上，下一格又满足 `AtTop()` ⇒ **连环加载**：
                //        实测 4 秒内自动翻了 2 页（把全部历史都拉了下来）。
                //   修法：先记参数，交给 `TickEarlierAnchor` 在下一帧（布局结算后）落位。
                //   期间进"保持期"式静默：`_earlierPending` 让自动触发不重复发请求。
                _earlierAnchorPending = true;
                _earlierScrollPx = oldScrollPx;
                _earlierViewportH = vp;
                _earlierOldH = oldH;
                _lastChildCount = _r.Content.childCount;
                built = made.Count;
            }
            catch (Exception e)
            {
                ModMain.P("[Chat] 插入更早历史失败（已插入的保留）：" + e.Message);
                built = 0;
            }
            ModMain.P("[Chat] 已插入更早历史：" + built + " 行（本批 " + items.Count + " 条）");
            return built;
        }

        /// <summary>用户点/滚到顶时请求更早的历史（事件：Presenter 订阅后发 RPC）。</summary>
        public event Action EarlierRequested;

        /// <summary>上方还有没有更早的历史（诊断/自检用）。</summary>
        public bool HasEarlier { get { return _histHasMore; } }

        // ---- 插入历史后的"锚定"（11-21：修连环加载与位置跳动）----
        // `InsertEarlier` 建完行时布局还没结算，读 `rect.height` 得到的是旧值。
        // 必须等下一帧再补偿，否则视口被推到顶部 ⇒ 连环触发加载（实测 4 秒翻了 2 页）。
        private bool _earlierAnchorPending;
        private float _earlierScrollPx;      // 插入前"离顶部"的像素距离
        private float _earlierViewportH;
        private float _earlierOldH;
        private int _earlierAnchorWait;      // 已等的帧数（兜底，防永久挂起）

        /// <summary>
        /// 插入历史后延迟一帧**把视口锚回原处**（布局结算后才有真实高度）。
        ///
        /// 为什么要等一帧：`Instantiate` 当帧 `Content.rect.height` 还是旧值，
        /// 用它算 `added` 会得到 0，于是视口被顶到最上方 —— 玩家手指还在滚轮上，
        /// 下一格立刻又满足"到顶"⇒ 连环加载，几秒内把全部历史拉下来。
        ///
        /// 达到的效果（用户 11-21 明确定案）：**刚上划的那段内容停在视口中间**，
        /// 新加载的历史出现在它上方，需要继续上滚才看得到。
        /// </summary>
        private void TickEarlierAnchor()
        {
            if (!_earlierAnchorPending) return;
            try
            {
                if (_r == null || _r.Content == null || _r.Scroll == null) { _earlierAnchorPending = false; return; }
                // 等两帧：`Instantiate` 的当帧 + 布局结算那一帧。超时（10 帧）就放弃，
                // 绝不允许"永久 pending"（那会让自动加载再也不触发）。
                if (_earlierAnchorWait++ < 2) return;
                float newH = _r.Content.rect.height;
                if (newH <= _earlierOldH + 0.5f && _earlierAnchorWait < 10)
                    return;                      // 还没算完，再等等（有上限）
                float added = newH - _earlierOldH;
                if (added < 0f) added = 0f;
                _r.Scroll.verticalNormalizedPosition =
                    HistoryWindowCore.ScrollAfterPrepend(_earlierScrollPx, added,
                                                         _earlierViewportH, newH);
                _lastChildCount = _r.Content.childCount;
                _lastContentH = newH;
                _earlierAnchorPending = false;
                ModMain.P("[Chat] 更早历史就位：内容 " + (int)_earlierOldH + "→" + (int)newH
                          + "（+" + (int)added + "），视口锚回 "
                          + _r.Scroll.verticalNormalizedPosition.ToString("0.000"));
            }
            catch (Exception e)
            {
                _earlierAnchorPending = false;
                ModMain.P("[Chat] 更早历史锚定失败（位置可能偏一点）：" + e.Message);
            }
        }

        /// <summary>
        /// 用户往上滚、且**已经滚到顶**时自动加载上一页。
        ///
        /// 判据特意用"滚到顶"（`AtTop`）而**不是**"每帧看一眼在不在顶"：
        /// 内容高度变化、插入行补偿、布局结算都会让"在顶"为真，
        /// 每帧轮询会变成"一开窗就连环加载到全部加载完"——等于没做分页。
        /// 这里只在**滚轮向上**这一个事件上问一次，语义明确。
        /// </summary>
        private void TryAutoLoadEarlier()
        {
            try
            {
                if (_moreLoading || !_histHasMore) return;
                if (!IsOpen || _r == null || _r.Scroll == null) return;
                // ★ 锚定还没落位（布局未结算）时**绝不许再触发**（11-21 连环加载的根因）★
                //   那时 `verticalNormalizedPosition` 还是"被顶到顶部"的中间态，
                //   `AtTop()` 会返回 true ⇒ 立刻再发一次请求 ⇒ 4 秒内把全部历史拉下来。
                if (_earlierAnchorPending) return;
                if (!AtTop()) return;
                RequestEarlier();
            }
            catch { }
        }

        /// <summary>滚动位置是否已在顶部。与 `AtBottom` 同款处理"内容比视口矮"的退化情形
        /// （那时 Unity 的 `verticalNormalizedPosition` 恒为 1，任何位置都算顶）。</summary>
        private bool AtTop()
        {
            try
            {
                var s = _r.Scroll;
                float vh = s.viewport != null ? s.viewport.rect.height
                                              : ((RectTransform)s.transform).rect.height;
                float range = s.content.rect.height - vh;
                if (range <= 0.5f) return true;
                return s.verticalNormalizedPosition >= 0.98f;
            }
            catch { return false; }
        }

        // ------------------------------------------------------------------
        // 压缩按钮忙态（用户反馈"按下去没反应"）
        // ------------------------------------------------------------------
        private Button _compactBtnRef;        // 压缩按钮本体（忙态置灰用）
        private Component _compactLabel;      // 按钮文案组件（Text 或 TMP_Text，取到哪个用哪个）
        private string _compactLabelText;     // 原文案（还原用）

        /// <summary>预制体是否有压缩按钮（无 = 只能靠 /compact 命令）。</summary>
        public bool CompactSupported => _compactBtnRef != null;

        /// <summary>压缩按钮忙态：文案换「压缩中…」+ 置灰。
        /// 为什么需要：摘要 LLM 调用实测可达 **105 秒**（真机 17:23:46→17:25:31），
        /// 其间若界面毫无动效，玩家会判定"按钮没反应"——真机上用户就是这么连点了三次。
        /// 忙态画在按钮自身，不依赖系统提示行模板，提示行缺失时也看得见。</summary>
        public void SetCompactBusy(bool busy)
        {
            try
            {
                if (_compactBtnRef != null) _compactBtnRef.interactable = !busy;
                if (_compactLabel == null) return;
                if (busy)
                {
                    if (_compactLabelText == null) _compactLabelText = GetLabelText(_compactLabel);
                    SetLabelText(_compactLabel, "压缩中…");
                }
                else if (_compactLabelText != null)
                {
                    SetLabelText(_compactLabel, _compactLabelText);
                    _compactLabelText = null;
                }
            }
            catch (Exception e) { ModMain.P("[ChatWindow] SetCompactBusy: " + e.Message); }
        }

        /// <summary>取按钮文案（兼容 TMP 与 uGUI Text；都没有返回 null）。</summary>
        private static string GetLabelText(Component c)
        {
            try
            {
                var tmp = c as TMPro.TextMeshProUGUI;
                if (tmp != null) return tmp.text;
            }
            catch { }
            try
            {
                var t = c as Text;
                if (t != null) return t.text;
            }
            catch { }
            return null;
        }

        private static void SetLabelText(Component c, string s)
        {
            try
            {
                var tmp = c as TMPro.TextMeshProUGUI;
                if (tmp != null) { tmp.text = s; return; }
            }
            catch { }
            try
            {
                var t = c as Text;
                if (t != null) t.text = s;
            }
            catch { }
        }

        // ------------------------------------------------------------------
        // 回合过程折叠组（tool_call / tool_result 时间线）
        // ------------------------------------------------------------------

        /// <summary>折叠行标题的**归属前缀**（群聊专用）：`姜萌 · 内心思量`。
        ///
        /// 为什么需要（09-17 与"点名不再预开气泡"配套）：群聊里气泡只在**正文到达时**
        /// 才出现，于是思考那几十秒里，"现在是谁在动"只能靠折叠行标题说 ——
        /// 标题不带名字的话，成员一旦出错/弃权，屏幕上剩下的「内心思量 · 850 字」
        /// 就是一段**无主内容**（实机原话："不知道是谁的，看着很诡异"）。
        /// 顺带解决群聊里"多人各来一块、分不清谁是谁"。
        /// 单聊恒返回原标签（观感逐字不变；单聊没有归属歧义）。
        /// 注：群聊的 sys/ctx 折叠已经**不显示**（见 `ChatPresenter.OnStepEvent`），
        /// 所以这里实际只服务「内心思量」与「行动记录」两块。</summary>
        private string SpeakerLabel(string baseLabel)
        {
            if (!IsRoomMode) return baseLabel;
            string who = _roomSpeaker ?? "";
            return who.Length > 0 ? who + " · " + baseLabel : baseLabel;
        }

        /// <summary>建组统一入口：所有 StepGroup 都在这里建，**折叠行的位置校正一并接上**
        /// （漏接的组会退化成"点开只看到结尾"，所以别在别处 new StepGroup）。</summary>
        private StepGroup NewStepGroup(string label = "行动记录", bool autoOpen = true)
        {
            var g = new StepGroup(_r, _r.Content, label, autoOpen);
            g.FoldToggled = OnFoldToggled;
            return g;
        }

        public void BeginTurnProcess()
        {
            if (_stepOpen && _curGroup != null) return;
            _curGroup = NewStepGroup(SpeakerLabel("行动记录"));
            _stepOpen = true;
            if (_curGroup.Root != null) RegisterRow(_curGroup.Root);
        }

        public void AddToolCall(string name, string argsJson)
        {
            if (_curGroup == null) BeginTurnProcess();
            if (_curGroup != null) _curGroup.AddCall(name ?? "", argsJson ?? "");
        }

        public void AddToolResult(string text, bool isError)
        {
            if (_curGroup == null) BeginTurnProcess();
            if (_curGroup != null) _curGroup.AddResult(text ?? "", isError);
        }

        /// <summary>一条**独立的**「行动记录」行（建组 → 加一行 → 立刻收起）。
        ///
        /// 用途：群聊的**旁白**（`room_say.narration`，如切磋那条「（与唐炎切磋了一场 —— …）」）。
        /// 那类行不是谁说的话、里面还夹着玩家的原话 ⇒ 画成气泡就是把它安到某个人头上
        /// （09-22 用户报）。改画成与**工具返回结果同款的折叠行**："这件事发生了"由行动记录说，
        /// 台词留给真正的气泡。重开窗走 `ReplaceHistory` 的 `tool_result` 分支，形态一致。
        ///
        /// **本回合已有进行中的记录组就并进去**（那正是它所属的那一轮的过程），收口交给回合本身 ——
        /// 否则会把这个组提前收起、之后这一轮再出工具行还会另起一个组，一轮的行动记录被劈成两半。</summary>
        public void AppendActionRow(string text)
        {
            if (string.IsNullOrEmpty(text)) return;
            bool standalone = _curGroup == null;
            AddToolResult(text, false);            // 无组时它自己会 BeginTurnProcess
            if (standalone) FinishStepProcess();   // 只有"单开一条"才立刻收起
        }

        /// <summary>回合收口：过程组头变“行动记录”并**整组收起**（把位置让给台词；README 的"做完自动收起"）。
        /// 用户手动点开过的组不收（手动优先，见 `StepGroup.Finish`）。</summary>
        public void FinishStepProcess()
        {
            if (_curGroup != null) _curGroup.Finish();
            _curGroup = null;
            _stepOpen = false;
        }

        // ------------------------------------------------------------------
        // 输入侧折叠区（sys「系统组装」/ ctx「运行时上下文」：只实时，历史回放不重放）
        // ------------------------------------------------------------------

        /// <summary>按回合+类别取（或新建）输入折叠块并追加一段。
        /// turn 变化即换新块（旧块留在上方，形如 think 每回合一个）。复用 StepGroup（行动记录同款模板）。
        /// </summary>
        public void AppendInputPart(int turn, string kind, string title, string label, string text, bool changed)
        {
            try
            {
                if (_inputTurn != turn)
                {
                    _inputTurn = turn;
                    _curSys = null;
                    _curCtx = null;
                }
                StepGroup block = kind == "sys" ? _curSys : _curCtx;
                if (block == null)
                {
                    string labelTitle = string.IsNullOrEmpty(title)
                        ? (kind == "sys" ? "系统组装" : "运行时上下文") : title;
                    block = NewStepGroup(labelTitle, autoOpen: false);
                    if (kind == "sys") _curSys = block; else _curCtx = block;
                    if (block.Root != null) RegisterRow(block.Root);
                }
                block.AddPart(label ?? "?", text ?? "", changed);
            }
            catch (Exception e)
            {
                try { UnityEngine.Debug.Log("[ChatWindow] input block: " + e.Message); } catch { }
            }
        }

        // ------------------------------------------------------------------
        // 内心思量折叠区（think 通道：reasoning_content / 内嵌 <think> 拆出）
        // ------------------------------------------------------------------

        /// <summary>开始（或复用）当前回合的内心思量区。
        /// 标题带说话人前缀（群聊）：`姜萌 · 内心思量 · 850 字` —— 群聊里气泡要等正文
        /// 才出现，思考期间它是唯一的归属线索（见 `SpeakerLabel`）。</summary>
        public void BeginThink()
        {
            if (_curThink != null) return;
            _curThink = new ThinkGroup(_r.Content, SpeakerLabel("内心思量"));
            _curThink.FoldToggled = OnFoldToggled;   // 展开 think 也是折叠行：同样「不够就滚够」
            if (_curThink.Root != null) RegisterRow(_curThink.Root);
        }

        /// <summary>思考流增量（Header 实时涨字数）。
        /// 新思考段开始（_curThink == null）即时间线分段点：先收口当前行动记录组——
        /// think 段在真实顺序里总紧跟上一工具段之后，不收口会让下一 step 的工具行
        /// 继续塞进旧组，视觉上「工具全挤一组、think 散在组外」（时间线乱序根因）。
        /// 收口后 AddToolCall 会另起新组，Content children 顺序 = 真实时间线（与历史回放一致）。</summary>
        public void AppendThinkDelta(string token)
        {
            if (_curThink == null)
            {
                FinishStepProcess();
                BeginThink();
            }
            _curThink?.AppendDelta(token);
        }

        /// <summary>思考整段写入（非流式 step / 历史回放）。
        /// 同 AppendThinkDelta：整段 think 也是时间线分段点，先收口行动记录组再建行
        /// （流式路径已在段首收口过，此处幂等 no-op；历史回放时无直播组同样 no-op）。</summary>
        public RowMeta AppendThinkStatic(string text)
        {
            if (string.IsNullOrEmpty(text)) return null;
            FinishStepProcess();
            BeginThink();
            _curThink?.SetFull(text);
            _curThink?.Finish();
            _curThink = null;
            return _lastMeta;
        }

        /// <summary>思考流收尾</summary>
        public void FinishThink()
        {
            _curThink?.Finish();
            _curThink = null;
        }

        // ------------------------------------------------------------------
        // 压缩纪要折叠行（10-31）：直播 `compact_summary` 与回放 `kind:"compaction"` 的唯一入口
        // ------------------------------------------------------------------

        /// <summary>追加一行「压缩纪要」。返回视图对象（回放时不需要，直播时可用于定位）。</summary>
        /// <param name="editable">能否修改（**由 Python 按账本算好**，C# 不自己猜）。
        /// 自动压缩产出的纪要来就是 false（它落在回合内部）。</param>
        public SummaryGroup AppendSummaryRow(string compactionId, string text,
                                             bool editable, string lockReason)
        {
            if (string.IsNullOrEmpty(text)) return null;
            try
            {
                // 纪要是时间线上的一段边界（后面紧跟的才是正文/工具），先收口当前行动记录组，
                // 否则下一 step 的工具行会继续塞进旧组 —— 与 think 段起点的处理同一条规矩。
                FinishStepProcess();
                var g = new SummaryGroup(_r.Content, compactionId, text, editable, lockReason);
                g.FoldToggled = OnFoldToggled;
                g.EditRequested = (cid, body) => SummaryEditClicked?.Invoke(cid, body);
                if (g.Root != null) RegisterRow(g.Root);
                _summaryRows.Add(g);
                return g;
            }
            catch (Exception e)
            {
                // 一行纪要画不出来，不该连累整段对话
                try { ModMain.P("[ChatWindow] 压缩纪要行渲染失败: " + e.Message); } catch { }
                return null;
            }
        }

        /// <summary>锁定全部还亮着的「修改」钮（玩家发消息 / 新回合开始 / 面板开着时又压了一次）。
        /// 幂等：`SetLocked` 对已锁定的行是空操作（只刷一遍文案）。</summary>
        public void LockSummaryEdits(string reason)
        {
            try
            {
                for (int i = 0; i < _summaryRows.Count; i++)
                {
                    var g = _summaryRows[i];
                    if (g == null || g.Root == null) continue;   // 已被 MAX_ROWS 裁掉/重放销毁
                    if (g.Editable) g.SetLocked(reason);
                }
            }
            catch (Exception e)
            {
                try { ModMain.P("[ChatWindow] 锁定压缩纪要失败: " + e.Message); } catch { }
            }
        }

        /// <summary>改完之后把屏幕上的那一行就地刷新（正文 + 字数）。找不到就静默跳过 ——
        /// 行可能已被裁掉，而账本已经改了（下一次回放自然是新正文）。</summary>
        public void RefreshSummaryRow(string compactionId, string text)
        {
            if (string.IsNullOrEmpty(compactionId)) return;
            try
            {
                for (int i = 0; i < _summaryRows.Count; i++)
                {
                    var g = _summaryRows[i];
                    if (g == null || g.Root == null) continue;
                    if (g.CompactionId == compactionId) g.SetSummaryText(text);
                }
            }
            catch (Exception e)
            {
                try { ModMain.P("[ChatWindow] 刷新压缩纪要失败: " + e.Message); } catch { }
            }
        }

        // ------------------------------------------------------------------
        // 流式气泡（delta 合并 → 终稿覆盖）
        // ------------------------------------------------------------------

        /// <summary>开始一条 NPC 气泡（可带初始文本；initiative=true 表示 NPC 主动传音，前置分隔条）</summary>
        public void BeginNpcBubble(string text, bool initiative = false, string speaker = null, string speakerId = null)
        {
            if (initiative && _deltaBubble == null)
            {
                AppendDivider("—— 主动传音 ——");
                _dividerTurn = _curTurn;      // 记账：本回合的分隔条已就位
            }
            var b = new ChatBubble(_r, _r.Content, false, text ?? "", SpeakerOf(speaker), SpeakerIdOf(speakerId));
            BindAvatarClick(b, false, SpeakerOf(speaker), SpeakerIdOf(speakerId));
            _deltaBubble = b.Root;
            _bubbleText = b.Text;
            _deltaView = b;               // 流式期间重算宽度要用（见字段注释）
            _delta.Length = 0;
            _delta.Append(text ?? "");
            // 预写的那几个字（群聊里是「甲：」）算**开头**不算正文：`ActiveBubbleHasNoBody`
            // 靠它判断"这条泡开了头却一个字都没说"（成员弃权）→ 该撤掉而不是留在屏幕上。
            _deltaPrefixLen = (text ?? "").Length;
            _deltaDirty = true;
            RegisterRow(_deltaBubble);
        }

        /// <summary>流式增量：只入缓冲，LateUpdate 合并刷一次</summary>
        public void AppendDelta(string token)
        {
            if (_deltaBubble == null || _bubbleText == null)
            {
                // 收口之后还能走到这里 = 无主增量凭空建泡（09-13 排查「回合完毕后
                //   删除模式进不去」时加的判据）：正常时序是「流式泡 → 收口帧清账」，
                //   若一条 delta 落在收口帧**之后**，它会新建一个没人收口的泡 →
                //   `HasActiveBubble` 永久为真 → 删除/压缩入口被守卫永久挡死。
                try { ModMain.P("[ChatWindow] 收口后仍有流式增量 → 凭空建泡 turn=" + _curTurn + " 长度=" + (token ?? "").Length); }
                catch { }
                BeginNpcBubble("");
            }
            _delta.Append(token ?? "");
            _deltaDirty = true;
        }

        /// <summary>
        /// 回合收口：**强制清掉「流式进行中」的记账**（不动已渲染的气泡行）。
        ///
        /// 为什么单独开一个口子：`HasActiveBubble` 是 `ChatPresenter` 里删除/压缩入口的守卫项之一
        /// （`_busy || HasActiveBubble`），而它只在 `FinishNpcBubble` 内部被清 —— 一旦那条渲染路径
        /// 中途抛异常（异常又被没人 await 的 Task 静默吞掉，见 `MainThreadDispatcher` 注释），
        /// 标记就永久泄漏，表现为「对话明明完了，删除模式却一直说回合进行中」。
        /// 收口帧一到，"这一回合没在飞了"是**固有语义**，绝不能依赖渲染成功。
        /// </summary>
        public void EndTurnStreaming()
        {
            ClearStreamingState();
        }

        /// <summary>回合定稿：用最终全文覆盖（流式期间缓冲已刷，终稿最准）；未开过流式→直接成整泡。
        /// **同一回合的同一段正文只成泡一次**（修「结果输出两遍」，见字段注释）。
        /// 整个渲染体走 `try/finally`：`ClearStreamingState()` 是**收口语义**，不能依赖渲染成功
        /// （09-13：渲染半途抛异常 → 标记泄漏 → 删除模式永久提示"回合进行中"；异常还被
        ///  MainThreadDispatcher 静默吞掉，日志里查不到）。
        ///
        /// 决策本身抽到 `BubbleCommitPolicy`（纯逻辑、可单测）。这里只负责执行它：
        /// 判重 → 撤掉多余的重绘泡；有流式泡 → 覆盖它；无流式泡但本回合末行还在 → 覆盖那一行；
        /// 都没有 → 新建。**"新建"是最后一档**，不再是判重失败后的默认动作。</summary>
        public void FinishNpcBubble(string fullText, bool initiative = false, string speaker = null, string speakerId = null)
        {
            try
            {
                var action = BubbleCommitPolicy.Decide(
                    fullText, _lastRenderText, _curTurn, _lastRenderTurn,
                    _deltaBubble != null && _bubbleText != null,
                    _lastRenderRow != null);

                switch (action)
                {
                    case BubbleCommit.Skip:
                        // 本回合同段正文已经渲染过（通常是 step(text) 先到、npc_reply 后到）：
                        // 不再成泡，只补"主动传音"分隔条（首次那条多半没带）+ 收尾流式状态。
                        // 判据带回合号：不同回合哪怕正文逐字相同（模型复读）也照常成泡。
                        //
                        // ★ 但"已经成泡"不等于"没有多余的行"：流式门
                        //   （OnDeltaEvent / AppendDelta → BeginNpcBubble）**没有去重**（它也
                        //   不可能有——那时还不知道全文）。于是"同一步的正文被重流一遍"
                        //   （模型在工具返回后复述自己的旁白；账本 09-13 赵勤第 1 回合
                        //   step3/step4 逐字节相同 116 字）会在这里再建一条重绘泡，
                        //   而这一路的 `return` 原先不撤它 → 屏幕上同段话两遍。
                        //   能走到这里就说明这段正文已经定稿 ⇒ 那条重绘泡一定是多余的。
                        DestroyActiveStreamingBubble();
                        if (initiative) EnsureInitiativeDivider(_lastRenderRow);
                        return;

                    case BubbleCommit.OverwriteStreaming:
                        // 正常流式定稿：覆盖活跃流式泡（Render 与流式那一帧同款，见 BubbleTextCore）
                        if (!string.IsNullOrEmpty(fullText))
                        {
                            _bubbleText.text = BubbleTextCore.Render(fullText);
                            _delta.Length = 0;
                            _deltaDirty = false;
                            // 11-20：终稿可能比流式最后一帧更长/更短，宽度要跟着定稿
                            // （流式期间已在 LateUpdate 里跟，这里补一次收口后的最终值）。
                            if (_deltaView != null) _deltaView.OnStreamTextChanged();
                        }
                        _lastRenderRow = _deltaBubble;
                        break;

                    case BubbleCommit.OverwriteLastRow:
                        // 终稿与已显示文本**有差异**（`_collect_reply` 会剥 <think>/剥行首自名前缀/
                        // strip，而 step(text) 发的是原文）→ 覆盖那一条，**绝不新建**：
                        // 新建就等于同一段话说两遍（09-16 实机截图的两条孪生气泡就是这个形状）。
                        if (!string.IsNullOrEmpty(fullText)) SetRowText(_lastRenderRow, fullText);
                        break;

                    default:   // BubbleCommit.CreateNew
                        BeginNpcBubble(fullText, initiative, speaker, speakerId);
                        _lastRenderRow = _deltaBubble;
                        break;
                }

                _lastRenderTurn = _curTurn;
                _lastRenderText = fullText;
                // 覆盖活跃流式气泡这条路不会经过 BeginNpcBubble → 分隔条要在这里补
                if (initiative) EnsureInitiativeDivider(_lastRenderRow);
            }
            finally
            {
                ClearStreamingState();
            }
        }
        /// <summary>记下当前回合号（ChatPresenter 在每个 step 帧上调用）。`npc_reply` 不带 turn，
        /// 靠它把"回合收口的终稿"与"该回合 step 已渲染过的正文"认成同一份。</summary>
        public void NoteTurn(int turn)
        {
            if (turn <= 0) return;
            // 回合号真的变了 = 新一轮开始了 ⇒ 压缩纪要的修改窗口关闭（Python 侧同样会拒，
            // 这里只是让按钮状态立刻跟上事实，不必等一次跨进程往返）。
            bool changed = turn != _curTurn;
            _curTurn = turn;
            if (changed) LockSummaryEdits("新一轮对话");
        }

        /// <summary>保证本回合恰有一条「—— 主动传音 ——」分隔条，插在 above 这条气泡之前。
        /// 三条路都要覆盖：① 新泡（BeginNpcBubble 自己会插）② 覆盖活跃流式泡（不会插）
        /// ③ 去重跳过（首条由 step(text) 渲染，不带分隔条）。</summary>
        private void EnsureInitiativeDivider(GameObject above)
        {
            if (above == null || _dividerTurn == _curTurn) return;
            try
            {
                var div = AppendDivider("—— 主动传音 ——");
                if (div != null) div.transform.SetSiblingIndex(above.transform.GetSiblingIndex());
                _dividerTurn = _curTurn;
            }
            catch { }
        }

        /// <summary>撤销当前流式气泡（失败兜底：不留半截气泡）</summary>
        public void CancelActiveBubble()
        {
            DestroyActiveStreamingBubble();
            ClearStreamingState();
        }

        /// <summary>当前流式泡**只说出了开头那几个字**（`speak` 预写的「甲：」），还没有任何正文。
        ///
        /// 为什么会提出这个问题（09-17 复核发现的真机形态）：群聊里 `room_state(phase="speak")`
        /// 会先开一条泡写上「甲：」，而该成员**可能弃权**（回【沉默】或空）—— 弃权不写群流水，
        /// 于是永远等不到 `room_say` 去定稿它，屏幕上就永久留着「甲：」或「甲：【沉默】」。
        /// 判据：`_delta` 里只有开头那段。无活跃泡 ⇒ false（调用方别把"没泡"当"空泡"撤）。</summary>
        public bool ActiveBubbleHasNoBody => _deltaBubble != null && _delta.Length <= _deltaPrefixLen;

        /// <summary>只撤掉当前流式气泡的行，不碰其他记账。
        /// 两处用：① 失败兜底（CancelActiveBubble）② 判重命中时撤掉"多余的重绘泡"
        /// （见 FinishNpcBubble 的 Skip 分支 —— 流式门没有去重，重流一遍就会多一条）。</summary>
        private void DestroyActiveStreamingBubble()
        {
            DestroyRow(_deltaBubble);
        }

        /// <summary>销毁一行，并把它从行注册表里**成对摘掉**。
        /// `_rows` 与 `Content` 子节点同序（行元数据按 `RegisterRow` 顺序入表），
        /// 只销毁 GameObject 不摘表会留下一条"已销毁"的死条目（消费方靠 `Row == null`
        /// 兜住，但序就错位了）。指向它的记账一并失效，免得后续去重判到一条不存在的行。</summary>
        private void DestroyRow(GameObject row)
        {
            if (row == null) return;
            try
            {
                for (int i = 0; i < _rows.Count; i++)
                {
                    var m = _rows[i];
                    if (m != null && m.Row == row) { _rows.RemoveAt(i); break; }
                }
                if (_lastRenderRow == row) _lastRenderRow = null;
                if (_lastMeta != null && _lastMeta.Row == row) _lastMeta = null;
                TemplateUtils.DestroySafe(row);
                _rowCount = Mathf.Max(0, _rowCount - 1);
            }
            catch (Exception e)
            {
                try { ModMain.P("[ChatWindow] 销毁行异常: " + e.Message); } catch { }
            }
        }

        /// <summary>把一条**已存在**的 NPC 气泡行的正文换掉（终稿覆盖用）。
        /// 取值路径与 `ChatBubble` 构造时一致（`GetComponentInChildren&lt;Text&gt;(true)`），
        /// 否则会写到与气泡不同的那个 Text 上。返回是否写成功——写不成功时调用方
        /// **不要**退化成新建（那正是要修的重复），宁可少一次规范化。</summary>
        private bool SetRowText(GameObject row, string text)
        {
            if (row == null) return false;
            try
            {
                var t = row.GetComponentInChildren<Text>(true);
                if (t == null) return false;
                // 与 ChatBubble.SetText / 流式那一帧同款整形（终稿覆盖也算"写泡"）
                t.text = BubbleTextCore.Render(text);
                return true;
            }
            catch (Exception e)
            {
                try { ModMain.P("[ChatWindow] 终稿覆盖失败: " + e.Message); } catch { }
                return false;
            }
        }

        private void ClearStreamingState()
        {
            _deltaBubble = null;
            _bubbleText = null;
            // 收口即解引用：气泡行本身还在屏幕上（由 `_rows` 持有），这里只是流式记账结束。
            // 不清的话下一回合会拿上一回合的已销毁视图去重算宽度（静默 no-op，但语义脏）。
            _deltaView = null;
            _delta.Length = 0;
            _deltaPrefixLen = 0;
            _deltaDirty = false;
        }

        // ------------------------------------------------------------------
        // 历史回放（Present 调；Python 只回已闭合 turn，重放与直播天然不交叠）
        // ------------------------------------------------------------------

        /// <summary>
        /// 全量重投影历史。**大回放走分帧**（见 `HistoryBuildCore`），小回放当场建完。
        ///
        /// ★ 11-20 修「开窗卡住 1.1 秒」★
        /// 真机：575 条单聊历史，`[MFrame][FrameStall] ms=770` + `ms=358` 两帧，
        /// 期间画面完全停住、ESC 都不响应。
        /// 判据是**条目数**而不是耗时（耗时得先建才知道，鸡生蛋）：超过 `SyncTotalLimit`
        /// 就交给 `BuildHistoryBatch` 每帧建一批，建完自己撤掉帧回调。
        ///
        /// ★ 调用方必须知道的唯一一件事：**分帧模式下返回时历史还没建完**。
        /// 这一点由 `IsBuildingHistory` 暴露，`ChatPresenter` 那几个后续动作
        /// （消费缓冲帧 / 撤渲染闸 / 进删除模式 / 刷统计 / 补画暂存帧）
        /// 全部后移到 `AfterHistoryBuilt` 登记的那一段里。
        /// 小回放（≤ 门槛）行为与改动前**逐字相同**：同一个函数里同步建完、返回时已完成，
        /// 续跑立刻执行，所以调用方那几段只是"晚一个回调"而不是"换一条路"。
        /// </summary>
        public void ReplaceHistory(JArray items)
        {
            ResetContent();   // ← 内含 CancelHistoryBuild：上一次没收尾的回放会在这里被撤干净
            if (items == null) return;

            int total = items.Count;
            // ★ 11-21：**服务端已经分页**，这一批就是首屏（最近 N 回合）★
            //   本类不再做本地切片 —— 第一版那样做的问题：`open_chat` 仍传 limit=0 拿全量，
            //   传输与解析一分没省（用户否决得对，见字段区注释）。
            //   更早的历史由 `get_history` 拉回来、走 `InsertEarlier` 插到顶部。
            //
            //   所以这里的 `items` 就是"要建的全部"，从 0 开始建到末尾。
            if (!HistoryBuildCore.ShouldDefer(total))
            {
                // 小回放：同步建完（与改动前逐字同路），但**照发**完成事件 ——
                // 调用方只认事件，不必分两种情况写两遍。
                _histPending = true;
                for (int i = 0; i < total; i++) ApplyHistoryItem(items[i]);
                CompleteHistoryBuild();
                return;
            }

            // 大回放：登记状态 → 本帧先建第一批 → 剩下的交给帧回调
            _histItems = items;
            _histCursor = 0;
            _histGroup = null;
            _histGroupMeta = null;
            _histPending = true;
            _histBuilding = true;
            try { ModMain.P("[ChatWindow] 历史回放转分帧构建：共 " + total + " 条（服务端已分页，这就是首屏），预计 "
                            + HistoryBuildCore.EstimateFrames(total) + " 帧"
                            + "（单帧预算 " + HistoryBuildCore.FrameBudgetMs + "ms）"); } catch { }
            BuildHistoryBatch();          // 本帧先建第一批：别让屏幕上白一帧
        }

        /// <summary>本次回放是否还在分帧构建中（调用方据此决定"后续动作现在做还是等事件"）。</summary>
        public bool IsBuildingHistory { get { return _histBuilding; } }

        /// <summary>
        /// 中止正在进行的分帧构建（**不执行** `AfterHistoryBuilt` 登记的那一段）。
        ///
        /// 什么时候必须调：**构建途中整块重建**（换 NPC / 关窗 / 又来一次回放）。
        /// 不调的话那条帧回调会继续往已经换过内容的 Content 里塞上一个 NPC 的行，
        /// 而 `ResetContent` 刚把 `_rows` 清空 ⇒ 行注册表与界面**永久错位**
        /// （删除模式选中的行与实际气泡对不上 —— 这类错位本仓栽过）。
        /// </summary>
        private void CancelHistoryBuild(string why)
        {
            if (!_histPending) return;
            _histPending = false;
            _histBuilding = false;
            _histItems = null;
            _histCursor = 0;
            _histGroup = null;
            _histGroupMeta = null;
            // ★ 续跑那一段**丢弃**，不补跑 ★
            // 它绑的是被取消的那一次回放：拿旧的 npc 去消费缓冲帧、撤一个已经不属于它的闸
            // ⇒ 新的那次回放会少画一段（静默）。新的那次自己会登记自己的一段。
            _histDone = null;
            try { FrameTicker.Stop(FrameTicker.KHistoryBuild); } catch { }
            try { ModMain.P("[ChatWindow] 分帧构建中止（" + why + "）"); } catch { }
        }

        /// <summary>
        /// 登记"历史建完之后再做这件事"。**立刻执行**（若已建完）或**等建完再执行**。
        ///
        /// 为什么是这个形状（而不是一个 `HistoryBuilt` 事件 + 订阅）：
        /// `ChatPresenter` 的两个开窗入口各自有**三条**动作必须排在"历史已建完"之后 ——
        /// 消费关窗期间的缓冲帧 / 撤渲染闸 / 进删除选择模式。它们原本就是
        /// `ReplaceHistory(...)` 的**下三行**。
        ///
        /// 用一个"续跑"而不是"事件"：事件会有多个订阅者、生命周期跨窗口，而这条续跑的
        /// 归属天然是**某一次回放**（`ReplaceHistory` 开头会把它清掉）——
        /// 快速连开两个窗时，第一次的续跑必须**被丢弃**而不是补跑，
        /// 否则它会拿旧的 `npc` 去消费帧、撤一个已经不属于它的闸。
        /// 事件模型下这件事要靠订阅者自己判，那就把"哪一次回放"这条信息丢了。
        /// </summary>
        public void AfterHistoryBuilt(Action done)
        {
            if (done == null) return;
            if (!_histPending)
            {
                // 已经建完（同步路径 / 上一次已经收尾）：就当普通调用，立刻做。
                try { done(); }
                catch (Exception e) { try { ModMain.P("[ChatWindow] AfterHistoryBuilt 立刻执行异常：" + e.Message); } catch { } }
                return;
            }
            _histDone = done;
        }

        /// <summary>建一条回放条目。**从原 `ReplaceHistory` 的 switch 原样搬出**，
        /// 唯一改动是"行动记录组"的两个局部变量变成了字段（`_histGroup` / `_histGroupMeta`），
        /// 因为它们要跨帧存活 —— `tool_call` 和它的 `tool_result` 可能落在不同批次里。
        /// 归并判据本身一字未动（同一次调用内相邻的 call/result 归一组）。</summary>
        private void ApplyHistoryItem(JToken token)
        {
            if (token == null || token.Type == JTokenType.Null) return;
            var o = (JObject)token;
            string kind = (string)o["kind"] ?? "";
            StepGroup his = _histGroup;
            RowMeta hisMeta = _histGroupMeta;
            switch (kind)
                {
                    case "user":
                        FinishHistoryGroup(his); his = null; hisMeta = null;
                        BindKey(AppendUserMessage((string)o["text"] ?? ""), o);
                        break;
                    case "assistant":
                        {
                            FinishHistoryGroup(his); his = null; hisMeta = null;
                            string body = (string)o["text"] ?? "";
                            var rm = AppendNpcStatic(body);
                            BindKey(rm, o);
                            // ★ 回放也是"往屏幕上放正文"的一条路，必须参与终稿判重记账。
                            //   否则 ResetContent 清零之后，"屏幕真相"（重放刚画了什么）无人登记 →
                            //   随后到达的 npc_reply 判不出重复 → 同一段话画两遍
                            //   （09-16 实机：回合已 turn/end、npc_reply 未到的空档里重开窗）。
                            //   反向也要成立：若这次回放**没**包含本回合（未闭合回合不进投影，
                            //   history.py 只取 closed），末条正文就属于上一回合 ⇒ _lastRenderTurn
                            //   落在旧回合 ⇒ npc_reply 仍会正常成泡 —— 这正是原先"清零"想保的场景。
                            if (!string.IsNullOrEmpty(body))
                            {
                                _lastRenderText = body;
                                _lastRenderTurn = o["turn"]?.Value<int>() ?? -1;
                                _lastRenderRow = rm?.Row;
                            }
                        }
                        break;
                    case "peer":
                        // 群聊：同席 NPC 的发言。**最小档**把说话人拼进正文
                        // （`docs/archive/2026-09-17-群聊编排/设计定稿.md` §8 P1b：完整档要加 prefab 名字槽，留给 P3）。
                        // 缺这一条的话，群窗回放会**什么都不画** —— switch 没有 default，
                        // 未知 kind 静默落空，表现是"群窗开了但历史一片空白"。
                        {
                            FinishHistoryGroup(his); his = null; hisMeta = null;
                            string who = (string)o["speaker"] ?? "";
                            // 说话人**精确 unitID**（`room_history.project_room_history` 随行携带，
                            // 来源是 `Room.member_unit_ids`）。有它就不必按名再猜一次 ——
                            // 群窗回放的每一行头像从此是确定的（见 RowAvatars.Apply）。
                            string whoId = (string)o["speaker_id"] ?? "";
                            string body = (string)o["text"] ?? "";
                            // 登记的是**画上去的那一串**（含 `名字：` 前缀），不是原始 body ——
                            // "屏幕真相"必须与屏幕上真正显示的逐字一致，否则终稿比对永远不等。
                            string shown = who.Length > 0 ? who + "：" + body : body;
                            var rm = AppendNpcStatic(shown, who, whoId);
                            BindKey(rm, o);
                            // 与上面的 `assistant` 分支同理：回放是"往屏幕上放正文"的一条路，
                            // 必须参与终稿判重记账（09-16 那两条重复气泡的修法的同一条不变量）。
                            //
                            // 诚实说明其**当前效力**：群窗条目**不带 `turn`**（`room_history`
                            // 刻意不给 —— 群窗不绑会话回合，v1 也不做群窗删除），所以这里登记的是 -1；
                            // 而 `BubbleCommitPolicy` 要求 `curTurn > 0` 才可能判 Skip / OverwriteLastRow
                            // ⇒ **这两档对群窗回放不可达**。登记目前只是让"屏幕真相"字段保持真实，
                            // 不改变任何行为；将来若给群窗条目补上 turn，它会自然开始生效。
                            if (!string.IsNullOrEmpty(shown))
                            {
                                _lastRenderText = shown;
                                _lastRenderTurn = o["turn"]?.Value<int>() ?? -1;
                                _lastRenderRow = rm?.Row;
                            }
                        }
                        break;
                    case "compaction":
                        // 压缩纪要（10-31）：位置由 Python 按**原始那一格**算好，这里只画。
                        // 不给它 BindKey：它是某个 NPC 的记忆，不属于任何回合（`turn` 恒 0），
                        // 删除模式的选中单位里没有它（它的去向由删除算法按覆盖闭包决定）。
                        FinishHistoryGroup(his); his = null; hisMeta = null;
                        AppendSummaryRow((string)o["compaction_id"] ?? "",
                                         (string)o["text"] ?? "",
                                         o["editable"]?.Value<bool>() ?? false,
                                         (string)o["lock_reason"] ?? "");
                        break;
                    case "think":
                        FinishHistoryGroup(his); his = null; hisMeta = null;
                        BindKey(AppendThinkStatic((string)o["text"] ?? ""), o);
                        break;
                    case "tool_call":
                        if (his == null) { his = NewStepGroup(); hisMeta = RegisterRow(his.Root); }
                        his.AddCall((string)o["name"] ?? "", o["args"]?.ToString() ?? "");
                        BindKey(hisMeta, o);
                        break;
                    case "tool_result":
                        if (his == null) { his = NewStepGroup(); hisMeta = RegisterRow(his.Root); }
                        his.AddResult((string)o["text"] ?? "", o["isError"]?.Value<bool>() ?? false);
                        BindKey(hisMeta, o);
                        break;
                    case "initiative":
                        {
                            FinishHistoryGroup(his); his = null; hisMeta = null;
                            // 短标签优先（Python initiative.intent_label 给中文四到六字）；
                            // 旧账本/未知意图回落英文键——不让分隔条空白
                            string lbl = (string)o["intent_text"];
                            if (string.IsNullOrEmpty(lbl)) lbl = (string)o["intent"] ?? "";
                            AppendDivider("—— 主动传音 · " + lbl + " ——");
                            BindKey(_lastMeta, o);   // 分隔条也归属该回合（随回合删除）
                            // 回放已经给这个回合插过分隔条 → 记账，免得随后到达的直播终稿再插一条
                            try { _dividerTurn = o["turn"]?.Value<int>() ?? _dividerTurn; } catch { }
                        }
                        break;
                    case "turn_error":
                        FinishHistoryGroup(his); his = null; hisMeta = null;
                        AppendSystemNotice("回合异常：" + (string)o["text"] ?? "");
                        BindKey(_lastMeta, o);
                        break;
                }
            // 写回跨帧状态：`tool_call` 与它的 `tool_result` 可能落在不同批次里，
            // 组对象必须活到下一次调用（分帧模式下这是唯一让它们仍归一组的东西）。
            _histGroup = his;
            _histGroupMeta = hisMeta;
        }

        /// <summary>分帧构建的每帧一格：建一批就返回，建完自己撤掉帧回调。
        ///
        /// 判据（建几条）在 `HistoryBuildCore.ShouldStopFrame`，这里只负责
        /// 「量耗时 → 问判据 → 建下一条」这个循环，以及建完之后的一次性收尾。
        ///
        /// 进 `try/catch` 的**理由**：这是帧回调，抛出去会打断整帧的其余 tick
        /// （游戏每帧调它一次，异常会冒到 Unity 的帧循环）。构建失败就当成"建完了"收尾 ——
        /// 半截历史仍然可用，比整窗空白好。</summary>
        private void BuildHistoryBatch()
        {
            if (!_histBuilding) { FrameTicker.Stop(FrameTicker.KHistoryBuild); return; }
            // 耗时采样（`TickProfiler`）：真机上据此确认"每帧真的没超预算"，
            // 而不是只看总时长。分段名与体检表里其它项并列（`chat.replay.build`）。
            long __tp = TickProfiler.Begin();
            try
            {
                var sw = System.Diagnostics.Stopwatch.StartNew();
                int built = 0;
                int total = _histItems != null ? _histItems.Count : 0;
                while (_histCursor < total)
                {
                    if (HistoryBuildCore.ShouldStopFrame(built, total - _histCursor, sw.Elapsed.TotalMilliseconds))
                        break;
                    ApplyHistoryItem(_histItems[_histCursor]);
                    _histCursor++;
                    built++;
                }
                if (_histCursor >= total)
                {
                    CompleteHistoryBuild();
                    return;
                }
                // 还有剩：确保下一帧继续（Ensure 幂等，这里只是把句柄救活）
                FrameTicker.Ensure(FrameTicker.KHistoryBuild, "histbuild", BuildHistoryBatch, this);
            }
            catch (Exception e)
            {
                try { ModMain.P("[ChatWindow] 分帧构建异常（就地收尾）：" + e.Message); } catch { }
                CompleteHistoryBuild();
            }
            finally { TickProfiler.End("chat.replay.build", __tp); }
        }

        /// <summary>构建收尾：拼上被裁提示、钉底、复位状态、执行续跑。
        ///
        /// **幂等**（`_histBuilding` 拦第二遍）：异常路径与正常路径都会走到这里，
        /// 而收尾里的续跑会驱动 Presenter 做那几个后续动作 —— 跑两次
        /// 就是"缓冲帧被消费两遍 / 渲染闸落位两次"，所以必须只发一次。</summary>
        private void CompleteHistoryBuild()
        {
            if (!_histPending) return;             // 已经收过尾（幂等）
            _histPending = false;
            _histBuilding = false;
            _histItems = null;
            _histCursor = 0;
            // 组收口：最后一批可能停在"tool_call 建了组、还没等到 result"。
            // 与同步路径原本那句 `FinishHistoryGroup(his)` 是同一件事。
            // 必须在清 `_histGroup` **之前**取（顺序反了就是静默不收口：
            // 折叠行永远停在"展开中"，而屏幕上看着完全正常）。
            var last = _histGroup;
            _histGroup = null;
            _histGroupMeta = null;
            try { FrameTicker.Stop(FrameTicker.KHistoryBuild); } catch { }
            try { FinishHistoryGroup(last); } catch { }
            // 超出 MAX_ROWS 被裁掉的行 → 顶部显式说明（绝不静默少给：今天的两次误判都源于此）。
            // 提示行 Turn=0 → 不可选，不参与删除；SetAsFirstSibling 把它顶到列表最前。
            try
            {
                if (_trimmedRows > 0)
                {
                    var notice = AppendSystemNotice("…更早的 " + _trimmedRows + " 条历史未渲染（窗口上限 " +
                                                    MAX_ROWS + " 行，删除模式同样看不到）");
                    if (notice != null) notice.transform.SetAsFirstSibling();
                }
            }
            catch { }
            _followingBottom = true;
            // ★ 11-21：「加载更早」那一行（在钉底**之前**建：它是顶部行，
            //   钉底会把视口拉到底，两者互不干扰，但先建好能让"内容高度"一次算对）★
            try { RefreshMoreRow(); } catch { }
            // 建完那一刻内容高度从 0 变成真实值 —— 必须重新进保持期，否则
            // `_lastContentH` 停在分帧中途的中间值，下面那次"钉底"会判成"没长高"而不钉。
            HoldScrollAtBottom();
            // 续跑那一段（消费缓冲帧 / 撤渲染闸 / 进删除模式 …）。
            // 先取出来再清字段：续跑里可能又触发一次回放（换人），别让它踩到半清的状态。
            var done = _histDone;
            _histDone = null;
            if (done != null)
            {
                try { done(); }
                catch (Exception e)
                {
                    try { ModMain.P("[ChatWindow] AfterHistoryBuilt 续跑异常：" + e.Message); } catch { }
                }
            }
        }

        /// <summary>把回放条目的**选中键**绑到行元数据上（删除模式的选中依据）。
        ///
        /// 一条判据覆盖两个场合：有 `turn`（单聊投影）⇒ 绑回合号；否则取 `uid`
        /// （群窗投影 `project_room_history` 只给 seq/uid，**刻意不给 turn** —— 群发言不属于
        /// 任何 NPC 的会话回合）。两者都没有的行不可选（直播残留行；进删除模式前的静默重放会补）。
        ///
        /// ★ `Turn > 0` 时**清空** `Uid`：两键互斥，免得某天有人给单聊条目也塞个 uid
        /// 之后，`Key`/`ToggleRow` 的分支判据出现两义。
        /// </summary>
        private static void BindKey(RowMeta m, JObject o)
        {
            if (m == null) return;
            try
            {
                m.Turn = o?["turn"]?.Value<int>() ?? 0;
                m.Uid = m.Turn > 0 ? null : ((string)o?["uid"] ?? "");
            }
            catch
            {
                m.Turn = 0;
                m.Uid = null;
            }
        }

        private static void FinishHistoryGroup(StepGroup g)
        {
            if (g != null) g.Finish();
        }

        // ------------------------------------------------------------------
        // 删除模式（微信式多选）：进入/退出/切换选中/全选/两段确认
        //
        // **两个场合、两种选中单位**（`RowMeta.Turn` / `RowMeta.Uid`，见其注释）：
        //   · 单聊：单位=账本**回合**（与 Python `plan_delete` 对齐：只发 turn 编号，
        //     seq 跨度由 Python 从账本换算；同回合的多行共享遮罩、整段一起走）。
        //   · 群聊：单位=**一条发言**（uid=`{room_id}#{seq}`，与 Python `room_delete`/
        //     `Room.deleted_seqs` 对齐；一行一条、多选=多条）。
        // 行可选性由键绑定决定（回放行有、直播残留行无；进删除模式前会静默重放一次补绑）；
        // 遮罩优先取行模板的 SelectOverlay 子节点（美术可自定义），没有则代码现造
        // 黑 α0/α0.35 兜底。
        // ------------------------------------------------------------------

        /// <summary>进入选择模式（Presenter 静默重放绑定选中键后调用）：禁输入、藏发送、亮操作条、行挂点击遮罩。
        /// 可选性判据 = `RowMeta.Selectable`（单聊看回合号、群聊看 uid）。</summary>
        public void EnterSelecting()
        {
            if (Selecting || _r == null) return;
            Selecting = true;
            _confirmArmed = false;
            foreach (var m in _rows)
            {
                if (m == null || !m.Selectable) continue;
                EnsureOverlay(m);
                if (m.Overlay != null) m.Overlay.SetActive(true);
                SetOverlayDark(m, false);
            }
            if (_r.ChatInput != null) _r.ChatInput.interactable = false;
            if (_r.SendButton != null) _r.SendButton.SetActive(false);
            // 工具钮与面板同属"发起新内容"的入口，选删除时一起收起：
            // 否则玩家会在"正在挑要删哪几条"的当口从侧边栏发一整轮对话出去。
            var toolBtnSel = ToolPanel.ButtonGo;
            if (toolBtnSel != null) toolBtnSel.SetActive(false);
            ToolPanel.Hide();
            CompactEditPanel.Hide();        // 选删除时不该同时开着编辑框
            // 跳过键与发送键同属输入区，选择模式下一起收起（否则它悬在发送键上方、
            // 点下去会在"正在选要删哪几条"的当口另起一轮群聊）。
            if (_skipBtn != null) _skipBtn.SetActive(false);
            if (_r.BusyLabel != null) _r.BusyLabel.SetActive(false);
            if (_r.DeleteBar != null) _r.DeleteBar.SetActive(true);
            UpdateCount();
        }

        /// <summary>退出选择模式：拆遮罩、恢复输入区与发送按钮、藏操作条、清选择集</summary>
        public void ExitSelecting()
        {
            if (!Selecting) return;
            Selecting = false;
            _confirmArmed = false;
            foreach (var m in _rows)
            {
                if (m?.Overlay != null)
                {
                    m.Overlay.SetActive(false);   // 隐藏而非销毁——模板自带遮罩下次进入复用（保留自定义样式）
                    SetOverlayDark(m, false);
                }
                m.Selected = false;
                m.Overlay = null;
                m.OverlayImg = null;
            }
            if (_r != null)
            {
                if (_r.ChatInput != null) _r.ChatInput.interactable = true;
                if (_r.SendButton != null) _r.SendButton.SetActive(true);
                // 工具钮同一条规矩：**只在单聊可见**（群聊没有唯一对话对象，见
                // `SetRoomButtonsVisible` 的 ★）。无条件 SetActive(true) 会让群聊里冒出
                // 一颗"点了不知道在跟谁说"的按钮。
                var toolBtnBack = ToolPanel.ButtonGo;
                if (toolBtnBack != null) toolBtnBack.SetActive(!IsRoomMode);
                // ★ 跳过键**不能**照抄上面那句无条件 SetActive(true)：它出厂隐藏、**只在群聊可见**
                //   （`SetRoomButtonsVisible`）。照抄的后果是"在单聊窗里退出删除模式 ⇒ 跳过键亮起来"，
                //   而它在单聊下点了要么静默 return、要么提示"当前不在群聊中" —— 正是本文件反复
                //   写死的"看得见却没用"。
                if (_skipBtn != null) _skipBtn.SetActive(IsRoomMode && !Selecting);
                if (_r.DeleteBar != null) _r.DeleteBar.SetActive(false);
            }
        }

        /// <summary>全选/取消全选（文字随状态切换）。单位 = 单聊的**回合** / 群聊的一条**发言**</summary>
        private void SelectAllToggle()
        {
            var units = new HashSet<string>();
            var picked = new HashSet<string>();
            foreach (var m in _rows)
            {
                if (m == null || !m.Selectable) continue;
                units.Add(m.Key);
                if (m.Selected) picked.Add(m.Key);
            }
            bool target = units.Count > 0 && picked.Count < units.Count;   // 未全选→全选；已全选→取消全选
            foreach (var m in _rows)
            {
                if (m == null || !m.Selectable) continue;
                m.Selected = target;
                SetOverlayDark(m, target);
            }
            UpdateCount();
        }

        /// <summary>删除确认（两段）：无选中不响应；第一击武装为「确认删除」并请求 preview摘要，
        /// 3 秒内再击才真正触发（触发前 Python 还会 fail-closed 兜底）。
        ///
        /// **按场合分叉**（载荷类型不同、RPC 不同）：
        ///   · 单聊 → `SelectedTurns()` + `preview_delete_history`/`delete_history`
        ///     （Python `plan_delete`：整回合，只动这个 NPC 的记忆）；
        ///   · 群聊 → `SelectedUids()` + `preview_room_delete`/`room_delete`
        ///     （Python `plan_message_delete`：按条，公共记录 + 在场成员记忆一起改）。</summary>
        private void OnDeleteConfirmClicked()
        {
            if (IsRoomMode)
            {
                var uids = SelectedUids();
                if (uids.Count == 0) return;
                if (!_confirmArmed)
                {
                    _confirmArmed = true;
                    _confirmArmTime = Time.time;
                    SetConfirmText("确认删除");
                    RoomDeletePreviewRequested?.Invoke(uids);
                    return;
                }
                DisarmConfirm();
                RoomDeleteConfirmed?.Invoke(uids);   // Presenter 发 RPC；成功才退出选择模式
                return;
            }
            var turns = SelectedTurns();
            if (turns.Count == 0) return;
            if (!_confirmArmed)
            {
                _confirmArmed = true;
                _confirmArmTime = Time.time;
                SetConfirmText("确认删除");
                DeletePreviewRequested?.Invoke(turns);   // Presenter 发 preview RPC，回执进 ApplyDeletePreview
                return;
            }
            DisarmConfirm();
            DeleteConfirmed?.Invoke(turns);   // Presenter 发 RPC；保持选择模式等回执，成功才退出
        }

        /// <summary>当前选中的回合号（去重升序；Presenter 原样发 delete_history 的 turns 载荷）</summary>
        public List<int> SelectedTurns()
        {
            var result = new List<int>();
            foreach (var m in _rows)
            {
                if (m == null || !m.Selected || m.Turn <= 0) continue;
                if (!result.Contains(m.Turn)) result.Add(m.Turn);
            }
            result.Sort();
            return result;
        }

        /// <summary>当前选中的群发言 uid（去重、保持屏幕顺序；Presenter 原样发 `room_delete` 的 uids 载荷）。
        /// 与 `SelectedTurns` 严格对称 —— 两个场合各一条载荷，Python 侧两个 RPC 各认一种。</summary>
        public List<string> SelectedUids()
        {
            var result = new List<string>();
            foreach (var m in _rows)
            {
                if (m == null || !m.Selected || string.IsNullOrEmpty(m.Uid)) continue;
                if (!result.Contains(m.Uid)) result.Add(m.Uid);
            }
            return result;
        }

        /// <summary>preview_delete_history 回执落地：ok=true → 计数位换预览摘要（将删条数/字数/含纪要提示）；
        /// ok=false（fail-closed 预检失败）→ 解除武装（第二击不再执行），原因提示由 Presenter 发系统行</summary>
        public void ApplyDeletePreview(bool ok, string summary)
        {
            if (!Selecting) return;
            if (!ok)
            {
                DisarmConfirm();
                return;
            }
            if (_r?.CountLabel != null && !string.IsNullOrEmpty(summary)) _r.CountLabel.text = summary;
        }

        private void DisarmConfirm()
        {
            if (!_confirmArmed) return;
            _confirmArmed = false;
            SetConfirmText("删除");
            UpdateCount();   // 摘要退场，计数复位（武装解除=旧预览失效）
        }

        private void SetConfirmText(string t)
        {
            if (_r?.DeleteConfirmBtn == null) return;
            var txt = _r.DeleteConfirmBtn.GetComponentInChildren<Text>();
            if (txt != null) txt.text = t;
        }

        private void UpdateCount()
        {
            // 计数单位 = **选中键去重后的单位数**，不是行数：
            //   · 单聊：一个回合有 4 行（分隔条 / 行动记录组 / 内心思量 / 气泡），旧写法逐行 +1
            //     → 只显示了一个回合的窗口报「已选 4 回合」（用户实机误解："只显示了一句却说要删 4 轮"）；
            //     `SelectedTurns()` 本来就是去重的，是这里的数字与账本对不上。
            //   · 群聊：一行就是一条发言（uid），单位数 = 选中条数 ⇒ 文案换「条」。
            var all = new HashSet<string>();
            var sel = new HashSet<string>();
            foreach (var m in _rows)
            {
                if (m == null || !m.Selectable) continue;
                all.Add(m.Key);
                if (m.Selected) sel.Add(m.Key);
            }
            if (_r?.CountLabel != null)
                _r.CountLabel.text = "已选 " + sel.Count + (IsRoomMode ? " 条" : " 回合");
            if (_r?.SelectAllBtn != null)
            {
                var txt = _r.SelectAllBtn.GetComponentInChildren<Text>();
                if (txt != null) txt.text = (all.Count > 0 && sel.Count == all.Count) ? "取消全选" : "全选";
            }
        }

        /// <summary>切换一行的选中态。
        /// 单聊：单位=回合 —— 点同回合任意一行，整组行（气泡/工具组/分隔条）一起亮/灭，
        /// 与 Python「删除以完整回合为单位」的语义在视觉上对齐（亮的范围=将删的范围）。
        /// 群聊：单位=一条发言 —— 只亮这一行（一个气泡一条，微信式逐条删）。</summary>
        private void ToggleRow(RowMeta m)
        {
            if (m == null || !m.Selectable) return;
            if (m.Turn <= 0)
            {
                m.Selected = !m.Selected;
                SetOverlayDark(m, m.Selected);
                UpdateCount();
                DisarmConfirm();   // 选择集变了，武装与旧预览作废重计
                return;
            }
            bool target = !m.Selected;
            foreach (var r in _rows)
            {
                if (r == null || r.Turn != m.Turn) continue;
                r.Selected = target;
                SetOverlayDark(r, target);
            }
            UpdateCount();
            DisarmConfirm();   // 选择集变了，武装与旧预览作废重计
        }

        private void SetOverlayDark(RowMeta m, bool dark)
        {
            if (m?.OverlayImg != null)
                m.OverlayImg.color = dark ? new Color(0f, 0f, 0f, 0.35f) : new Color(0f, 0f, 0f, 0f);
        }

        /// <summary>行选中遮罩兼点击板：模板有 SelectOverlay 用之，否则现造。
        /// 全 stretch 的 Image（α0 待选/α0.35 已选）+ Button 收点击；置最末兄弟盖过行内内容；
        /// ignoreLayout——行根是 HorizontalLayoutGroup，遮罩不得参与排布。</summary>
        private void EnsureOverlay(RowMeta m)
        {
            if (m.Overlay != null) return;
            var t = m.Row.transform.Find("SelectOverlay");
            GameObject go = t != null ? t.gameObject : null;
            if (go == null)
            {
                go = new GameObject("SelectOverlay");
                go.layer = m.Row.layer;
                var rt = go.AddComponent<RectTransform>();
                rt.SetParent(m.Row.transform, false);
                rt.anchorMin = Vector2.zero;
                rt.anchorMax = Vector2.one;
                rt.offsetMin = Vector2.zero;
                rt.offsetMax = Vector2.zero;
                var img = go.AddComponent<Image>();
                img.raycastTarget = true;
                img.color = new Color(0f, 0f, 0f, 0f);
            }
            var le = go.GetComponent<LayoutElement>() ?? go.AddComponent<LayoutElement>();
            le.ignoreLayout = true;
            var btn = go.GetComponent<Button>() ?? go.AddComponent<Button>();
            var captured = m;
            ClickUtils.Attach(btn, () => ToggleRow(captured));
            m.OverlayImg = go.GetComponent<Image>() ?? go.AddComponent<Image>();
            m.OverlayImg.raycastTarget = true;   // 模板自带遮罩默认关 raycast（平时不挡点击），选择模式必须打开
            m.Overlay = go;
            go.transform.SetAsLastSibling();
            go.SetActive(true);
        }

        // ------------------------------------------------------------------
        // 内部：行登记 / 上限裁剪 / 重置
        // ------------------------------------------------------------------

        private RowMeta RegisterRow(GameObject row)
        {
            _lastMeta = new RowMeta { Row = row };
            _rows.Add(_lastMeta);
            _rowCount++;
            // 上翻回看期间的新到达计数（10-22 浮动钮）：跟随中/批量重放保持期**不**计
            //（重放开窗即置跟随，见 ResetContent；保持期兜底防漏网路径）
            if (!_followingBottom && _pinHoldFrames == 0) _newRowsWhileUnfollowed++;
            // ② 行数上限：删除最旧行（最旧必不是流式中的末条气泡），行注册表同步修剪
            while (_rowCount > MAX_ROWS && _rows.Count > 0)
            {
                var oldest = _rows[0];
                if (oldest?.Row != null) TemplateUtils.DestroySafe(oldest.Row);
                _rows.RemoveAt(0);
                _rowCount--;
                _trimmedRows++;   // 记账：ReplaceHistory 结束时会把它显式写进列表顶部
            }
            return _lastMeta;
        }

        private void ResetContent()
        {
            // ★ 分帧构建途中被整块重建（换 NPC / 关窗 / 又来一次回放）★
            // 必须在这里先撤掉那条帧回调：它正拿着**上一份** items 的游标往这个 Content 里塞行，
            // 而本方法下面会把 `_rows` 清空 ⇒ 行注册表与界面永久错位
            // （删除模式选中行与实际气泡对不上，且零报错）。放在第一句：早于任何清表动作。
            if (_histPending) CancelHistoryBuild("内容整块重建");
            if (Selecting) ExitSelecting();   // 先拆遮罩/恢复输入区，再清行（遮罩随行销毁也无妨）
            // 时间分隔行的"上一条日期"随内容清零：重放从头重算 ⇒ 与实时逐字同档
            // （留着旧值会让"开窗先见旧会话、再见新会话"的分隔行错位——不清 = 状态泄漏）
            _sepHas = false; _sepY = _sepM = _sepD = 0;
            // 重放 = 从头重建：新消息计数清零、跟随态复位
            //（否则重放灌进来的几百行会被当成"你没看的消息"，开窗就顶个假角标）
            _newRowsWhileUnfollowed = 0;
            _followingBottom = true;
            // 行整块重建 = 那些待贴头像的槽位全部失效 → 清待贴表（**不清像素缓存**：
            // 已读进内存的图继续可用，重放时立刻能贴上）。
            RowAvatars.Reset();
            // ★★ 10-17：这里**不再**碰忙标（原先一句 `SetBusy(false)` 已删）★★
            //
            //   那句话的来历是对的 —— 它当年补的是这个漏网时序：
            //     玩家发出消息 ⇒ busy=true ⇒ 关窗/换 NPC ⇒ 回包到达时 `ChatPresenter.OnUiEvent`
            //     因 `!IsOpen` / npcId 不符而早退（收口帧被丢弃）⇒ 忙标再也没人复位。
            //   **但它修错了地方**：把"内容被重建"当成了"回合已结束"，而这两件事毫无关系。
            //   真机代价（10-17 用户实测）：点「同意」→ 开窗点亮忙标 → **几百毫秒后历史回放
            //   落地**（走本方法）→ 忙标被灭 → 模型还要跑 15 秒，屏幕上一个字都没有
            //   （忙标只活 2 秒；`Player.log` 里 `忙标 ON` → `历史回放落地` → `忙标 OFF`）。
            //
            //   那个原始时序现在由 `TurnRegistry` 统一兜住，而且比原方案更准：
            //     · 收口帧的销账点挪到 `ContactDuty`（在一切早退**之前**无条件看到每一帧
            //       `npc_reply`，与窗口开不开、显示谁完全无关）⇒ 上面那条"回包被丢弃"不再发生；
            //     · 忙标改为**按登记表重算**（`ChatPresenter.RefreshSoloBusy`）⇒ 重建内容
            //       只是重建内容，忙态该是真是假由回合状态说了算；
            //     · `TurnRegistry` 自带 900s 安全阀 + `ChatPresenter.BusyWatchdog` 的
            //       "与登记表不一致"判据，双保险。
            //   ⇒ 删掉本行**不是**放松，而是把这条约束交给了唯一有资格判断的地方。
            _rows.Clear();
            _summaryRows.Clear();            // 视图对象随行一起销毁；新树由回放/直播重建
            CompactEditPanel.Hide();         // 内容整块重建 ⇒ 正在改的那条纪要已经不是"屏幕上这条"
            _lastMeta = null;
            _trimmedRows = 0;
            // 内容整块重建（开窗回放/换 NPC/删除后重放）：成泡记账与分隔条记账一并清零。
            // 清零本身是对的 —— 此刻屏幕上**确实**一条正文都没有了，记账说真话。
            // （原先的注释写"否则重放后再来的直播终稿会被误判成重复而不成泡"，那个顾虑仍然
            //   成立，但**不能靠清零来解决**：清零之后若这次回放包含本回合，重放刚画的正文就
            //   无人登记 → 终稿反而会再画一遍。09-16 修法：让 ReplaceHistory 自己登记它画了什么，
            //   两种次序就都对了 —— 回放没含本回合时 _lastRenderTurn 停在旧回合，终稿照常成泡。）
            // `_curTurn` 保留：它是"当前直播回合号"，与屏幕内容无关。
            _lastRenderTurn = -1;
            _lastRenderText = null;
            _lastRenderRow = null;
            _dividerTurn = -1;
            if (_r != null && _r.Content != null)
            {
                // IL2CPP 下枚举 Transform 子节点必须用索引循环，foreach 会抛 InvalidCastException
                int count = _r.Content.childCount;
                for (int i = 0; i < count; i++)
                {
                    Transform c = _r.Content.GetChild(i);
                    if (c != null) TemplateUtils.DestroySafe(c.gameObject);
                }
            }
            _rowCount = 0;
            // 「加载更早」那一行不属于消息，随内容一起销毁（它会被下面的遍历一起 Destroy，
            // 但引用要一并置空 —— 否则下次 `EnsureMoreRow` 会以为它还在）
            _moreRow = null;
            _moreRowText = null;
            _moreLoading = false;
            // 内容整块重建 ⇒ 在飞的锚定作废（它引用的行已经被销毁，落位只会算错）
            _earlierAnchorPending = false;
            _earlierAnchorWait = 0;
            // 统计行属于"上一个窗口"：单聊开窗马上会被 open_chat 的基线刷新，但**群窗**
            // 的基线要等第一个成员发言后才来 —— 不清就会把上一位单聊对象的 token 数
            // 挂在一个不相干的群窗上（用户看到的是"缓存率那行还在，上下文没了"的残影）。
            if (_tokensBar != null) _tokensBar.text = "";
            ClearStreamingState();
            _curGroup = null;
            _stepOpen = false;
            _curThink = null;
            _curSys = null;
            _curCtx = null;
            _inputTurn = -1;
            _followingBottom = true;
            _lastChildCount = -1;    // 内容整块重建 → 下一帧必判"长了" → 钉底一次（不重置会因
            _lastContentH = -1f;     // 子节点数恰好相同而漏掉钉底，开窗后停在顶部）
            // 整块重建 ⇒ 进保持期：布局结算这几帧里连钉几次（见 `HoldScrollAtBottom`）。
            // 放在这里而不是只放 `ReplaceHistory` 末尾：`Open()` 也走本方法，两条路一起覆盖。
            _pinHoldFrames = 6;
        }
    }

    /// <summary>
    /// 行注册表条目：行根 + 该行归属的**选中键**（删除模式的选中依据）。
    ///
    /// 两种键、按场合二选一（`docs/archive/2026-09-17-群聊编排/设计定稿.md` §9.2；由 `BindKey` 一条判据覆盖）：
    ///   · `Turn` **单聊** —— Python 侧按**完整回合**删（`plan_delete` 只信任 turn 编号，
    ///     不信任裸 seq，seq 跨度由它自己从账本换算）；同回合的 4 行（分隔条/行动记录组/
    ///     内心思量/气泡）共享遮罩、整段一起走。
    ///   · `Uid` **群聊** —— 群发言**没有回合**：`Room.log` 是平面流水，且同一条发言在不同
    ///     成员账本里的 turn 号不同 ⇒ 只能按**条**删，键就是 `{room_id}#{seq}`
    ///     （`room_delete` 的载荷，与 Python 的 `Room.deleted_seqs` 墓标一一对应）。
    ///
    /// 回放行由 `ReplaceHistory` 绑定；直播新产行两键皆空=不可选，进删除模式时的静默重放
    /// 会给它们补上身份。遮罩节点（SelectOverlay）在进入选择模式时启用，退出时隐藏
    /// （模板自带遮罩保留自定义样式）。
    /// </summary>
    public sealed class RowMeta
    {
        public GameObject Row;
        public int Turn;                // 账本回合号（0=未绑定）
        public string Uid;              // 群发言的全局锚点 `{room_id}#{seq}`（空=未绑定）
        public bool Selected;
        public GameObject Overlay;      // 选中遮罩兼点击板（模板 SelectOverlay 或代码现造）
        public Image OverlayImg;

        /// <summary>该行是否可选（两个场合的键有一个非空即可）。</summary>
        public bool Selectable
        {
            get { return Row != null && (Turn > 0 || !string.IsNullOrEmpty(Uid)); }
        }

        /// <summary>选中键的规范化串（计数/去重/全选用）：单聊 `t:12`（同回合的多行算一个
        /// 单位），群聊直接用 uid（一行一条）。空串 = 不可选。</summary>
        public string Key
        {
            get
            {
                if (Turn > 0) return "t:" + Turn;
                return Uid ?? "";
            }
        }
    }
}