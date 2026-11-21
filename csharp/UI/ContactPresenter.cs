/// <summary>
/// 传音簿（通讯录）控制器（MonoBehaviour，仿 ConfigPresenter）。
///
/// 数据两层（好友语义 = 玩家认识的 NPC，非全图筛选）：
///  ① 玩家关系记录全集：RelationNetwork（十容器+好友簿+仇人簿+GetAllGoodRelation 含敌）——本地主线程采集，开面板即得；
///  ② 手动好友：list_contacts RPC（Python 侧 contacts.json）——异步回填后合并重渲。
/// 合并按中文名去重（关系层优先，手动层补缺）；副标题 = 宗门·境界 · 关系/仇敌 好感。
///
/// Tab：「好友」= 合并全量按名字拼音序（zh-CN 排序，异常回退码位序）；
///      「最近」= 通讯录成员 ∩ 有互动记录，按最近互动倒序（互动 = 会话文件 mtime ∪ 本地 npc_reply 时间戳）；
///      「群聊」= `list_rooms` 拉到的群名录，点一行进群续聊（P3）。
/// 搜索：本地子串过滤（名字/副标题），非空时跨两 Tab 搜全量——零 RPC。
/// 行点击 / ✓：解析 WorldUnitBase → ChatLauncher.OpenForUnit（先隐藏本面板，防 3050 Canvas 盖住对话窗）。
/// 行内 ✕（仅好友行，运行时新建）：移除好友（两段确认，走 ContactDuty remove_contact）。
/// 未读：NPC 主动传音（npc_reply initiative=true）分流——对话窗开着且是该 NPC=已读直播；
///       同格+空闲+没开别的对话窗=当面弹出对话 UI；其余=UnreadStore 登记（行红点 UnreadDot +
///       HUD「传」钮角标 + 顶部横幅 UnreadBanner，横幅**纯提示不可点**）。
///       **打开本面板即全部已读**（09-26 用户拍板，见 `Show` ⓪）——修"红点点不下去"。
///       见 README.md 附录二 G（功能设计底稿）。
///
/// ── 群聊页（P3）──────────────────────────────────────────────────────
/// 两个入口，都汇到 `ChatLauncher.OpenForRoom` → `ChatPresenter.OpenRoomById`（群窗唯一开启路径）：
///   ① 群列表点一行 = 续聊（传 `room_id`）；
///   ② 「＋ 新建群聊」→ 进**选人模式**（当前页切换成好友列表 + 行内复选框）→「确认」建群（传成员名）。
///
/// 选人模式是**群聊 Tab 的一个子状态**，不是第四个 Tab：Tab 高亮仍停在「群聊」，
/// 列表源临时换成联系人。「取消」退出并整体回群列表，不留残留选中。
///
/// 为什么群列表数据留**本类**而不进 `ContactStore`：`ContactStore`/`ContactDuty` 的分工是
/// "面板不在场也必须活着"的常驻数据（未读红点、加好友回调、主动传音分流）。群名录只在
/// 面板打开时才有意义，`Show()` 里现拉一次即可 —— 放进去只是徒增一个跨面板生命周期的真相源。
///
/// IL2CPP 约定：与 ChatPresenter 同规约（宿主侧 RegisterTypeInIl2Cpp）；按钮回调 ClickUtils 三步写法；
/// InputField.onValueChanged 先转存 System.Action&lt;string&gt; 再 AddListener；遍历子节点用索引循环。
/// </summary>
using System;
using System.Collections.Generic;
using System.Globalization;
using UnityEngine;
using UnityEngine.UI;
using Newtonsoft.Json.Linq;

namespace AgentLoopBridge
{
    public class ContactPresenter : MonoBehaviour
    {
        /// <summary>IL2CPP 互操作标准构造</summary>
        public ContactPresenter(IntPtr ptr) : base(ptr) { }

        private const int MAX_ROWS = 50;      // 单次实例化行数上限（VerticalLayoutGroup 下全量克隆会卡）
        private const int RECENT_MAX = 30;    // 「最近」显示条数上限

        private static readonly Color AccentGreen = new Color(0f, 0.788f, 0.682f, 1f);   // #00C9AE（与 ContactUiBuilder 同源）
        private static readonly Color TextFaint = new Color(0.604f, 0.631f, 0.663f, 1f); // #9AA1A9
        private static readonly Color StatusDim = new Color(0.60f, 0.62f, 0.70f, 1f);
        private static readonly Color StatusWarn = new Color(0.95f, 0.80f, 0.45f, 1f);
        //: 成功回执（目前只有「建群」用）。取面板主色，与 Tab/确认键同一支青绿 ——
        //: 不新造颜色，玩家一眼能认出"这是这个面板的肯定回答"。
        private static readonly Color StatusOk = AccentGreen;

        /// <summary>通讯录条目（合并层①②后的一份）。</summary>
        private class ContactEntry
        {
            public string Name;
            public WorldUnitBase Unit;    // 可空：手动好友解析失败 / 已不在世间
            public string UnitId;         // 该名字的**精确 unitID**（关系数据的 unitID / contacts.json 的 unit_id）
            public bool Exact;            // `Unit` 是不是**精确**解析来的（false = 按名猜的）
            public bool Manual;           // 是否手动添加（与关系层并存时置 true，展示仍以关系层为准）
            public bool ManualOnly;       // 仅由手动层引入（关系层没有）——「移除好友」后需整条出列
            /// <summary>仅由**「说过话」**这一层引入（10-24）。
            ///
            /// 语义 = "他和我说过话，但我们既不是好友、也没有任何社会关系"。这类条目**只出现在
            /// 「临时」页**（`RenderCurrent` 的 Friend/Recent/建群选人各分支都会把它滤掉），
            /// 原因见 `MergeHeardIntoContacts`。
            ///
            /// `Manual` 一旦为真就把它清掉（见 `MergeManualIntoContacts` 第 ① 步）：
            /// 玩家显式加了好友，这个人就从「临时」转正到「好友」，不能两边都挂。</summary>
            public bool HeardOnly;
            /// <summary>**已故**（游戏事实，与"他是不是好友"正交）。
            ///
            /// 死亡不改成员资格 —— 他只是不能再说话，不该从名单里消失（`dead` 行仍可点开
            /// 翻旧账，发消息时由 `ChatPresenter` 的死者闸拦住并提示"遭遇了不测"）。
            /// 判据：`GetUnit(unitID)` 解析不到 + `UnitSnapshot.IsDead(unitID)` 命中。
            /// **只对有精确 unitID 的条目判**（O(1) 字典直查）；没有 ID 的老记录不判 ——
            /// 按名查是遍历整个死者账本，逐行调会让开面板变成 O(行数×死者数)。
            /// </summary>
            public bool Dead;
            /// <summary>仅由「已故」层引入：关系层这回合拿不到真身（他死了）、手动层也没有他。
            /// 与 `ManualOnly` 同构，回答的是"这条是谁引进来的"，不是一个页签。</summary>
            public bool DeadOnly;
            /// <summary>「殁于X年X月」；取不到为空串（不编时间）。</summary>
            public string DeathNote;
            public long LastInteract;     // 最近互动（unix 秒；服务端 last → 本地 → 文件 mtime 兜底）
        }

        /// <summary>群名录条目（`list_rooms` 回包的一份）。**纯托管数据** ——
        /// 群没有对应的 `WorldUnitBase`，本类也绝不缓存任何 Unity 引用（同 ContactStore 铁律）。</summary>
        private class RoomEntry
        {
            public string RoomId;
            public string Title;
            public List<string> Members = new List<string>();
            public long LastSeq;          // = Python 侧 Room.last_seq（即流水条数）
            public long Created;
        }

        /// <summary>四个 Tab。用枚举而不是几个 bool —— 两 bool 能表示 4 个状态，
        /// 其中"又最近又群聊"是非法态，而非法态一旦可达就会有人写出来。</summary>
        private enum Tab { Friend, Recent, Group, Temp }

        /// <summary>群聊页的四种**选择模式**（共用底部那条 SelectBar）。
        ///
        /// 用枚举而不是几个 bool，理由同 `Tab`：`建群中 && 解散中` 是非法态，
        /// 两 bool 让它可达，而它的症状是"确认键到底在建群还是删群"这种没人能推理的东西。
        /// 四个模式两两互斥、由同一个 `_pick` 保证 —— 加模式时**必须**同时想清楚
        /// `RenderCurrent` / `UpdateSelectCount` / `UpdateConfirmLabel` / `ConfirmPick`
        /// 四处怎么分派（编译器不会提醒，漏一处就是静默的行为错乱）。
        /// </summary>
        private enum PickMode
        {
            None,     // 普通：列表就是它自己（好友 / 最近 / 群）
            Create,   // 建群选人：列表源 = 联系人（多选），确认 → open_room
            Delete,   // 解散选群：列表源 = 群（多选），确认 → dissolve_room（两段确认）
            Invite,   // 拉人进群：列表源 = 联系人**减去已在群里的人**，确认 → add_room_members
            Kick,     // 踢人出群：列表源 = 本群成员，确认 → kick_room_member（两段确认）
        }

        private ContactPanelRefs _refs;
        private WsClient _ws;
        private bool _isOpen;
        private Tab _tab = Tab.Friend;
        private string _search = "";
        private UnityEngine.Events.UnityAction<string> _onSearchChangedAction;

        // ── 群聊页状态 ──
        private readonly List<RoomEntry> _rooms = new List<RoomEntry>();
        private bool _roomsLoaded;                                        // 区分"还没拉到"与"确实没有群"
        private PickMode _pick = PickMode.None;                           // 当前选择模式（默认普通）
        // 两个模式**各有一份选中集**：清空时机不同（切模式/换 Tab 都要清），
        // 共用一个 list 会让"建群选到一半去删群"带着一堆好友名进删除流程。
        private readonly List<string> _selected = new List<string>();        // 建群：好友名
        private readonly List<string> _selectedRooms = new List<string>();   // 解散：群 id
        private readonly List<string> _lastRowNames = new List<string>();    // 上次渲染出来的**联系人**行
        private readonly List<string> _lastRoomIds = new List<string>();     // 上次渲染出来的**群**行
        // 「解散」的两段确认（首击武装 → 3s 内再击执行）。不可恢复操作走工程既有的
        // TwoStepConfirm（零 Unity 依赖的纯逻辑，已被单测锁着，删除文件/重置默认也用）。
        private readonly TwoStepConfirm _delConfirm = new TwoStepConfirm();

        // ── 群成员变更（P3 第 4 步：拉人 / 踢人；由**对话窗**的按钮发起）──
        /// <summary>操作目标群（Invite/Kick 模式专用；其余模式为空）。</summary>
        private string _roomTargetId = "";
        /// <summary>目标群名（只用于状态条文案）。</summary>
        private string _roomTargetTitle = "";
        /// <summary>目标群**当前成员**：拉人时用来把它们从候选里剔除，踢人时就是列表源本身。
        /// 由对话窗在发起时给（它手上的 `CurrentRoomMembers` 就是权威值 —— 来自 open_room /
        /// add_room_members / kick_room_member 的回执），本面板不自己猜。</summary>
        private readonly List<string> _roomTargetMembers = new List<string>();
        /// <summary>踢人的选中集。与 `_selected`（好友名，建群/拉人共用）分开：
        /// 清空时机不同，而"拉人勾到一半改去踢人"带着一堆好友名进踢人流程是最坏的一类状态错乱。</summary>
        private readonly List<string> _selectedKick = new List<string>();
        /// <summary>踢人的两段确认。与解散同款理由：误点一下就把人移出群聊，
        /// 而他之后的群消息全部收不到（虽然记忆还在、也能再拉回来）。</summary>
        private readonly TwoStepConfirm _kickConfirm = new TwoStepConfirm();

        // ── 行内 ✕「移除好友」（09-26 用户拍板：✓ 左侧、两段确认）──
        // ★ 两段确认**不带超时**（09-26 用户二次拍板："只要用户点了确认就行，不需要超时"）：
        //   首击武装（按钮变「确认」），再点同一行即执行 —— 没有 3 秒窗口这回事。
        //   故这里**不用** TwoStepConfirm（那是带窗口的通用件，仍服务于解散/踢人）；
        //   武装态只活在下面这两个字段里。
        /// <summary>当前被武装的行名（空 = 没有处在确认态的行）。</summary>
        private string _unfriendArmName = "";
        /// <summary>被武装那一行的 ✕ 图标：换行/重渲/关面板时要把它复位回「✕」——
        /// 否则旧行会一直停在「确认」文案上（玩家以为它还武装着）。行对象销毁后是 Unity fake-null，安全。</summary>
        private Text _unfriendArmIcon;
        /// <summary>下一次 RenderCurrent 末尾要顶上去的一次性状态回执（如"已移除好友：X"）。
        /// 必须"先存后写"：RenderCurrent 自己会把状态栏刷成默认文案，直接 SetStatus 会被当场覆盖。</summary>
        private string _pendingStatus = "";
        private Color _pendingStatusColor = StatusDim;

        private readonly List<ContactEntry> _contacts = new List<ContactEntry>();      // 合并后全量（字母序）
        // 手动好友镜像 / 会话索引 / 本地互动时间戳已迁往 ContactStore（静态层，与面板实例解耦——定案）
        // ：通讯录不再调用立绘渲染 API（对未进场 NPC 调 CreateTextureInModelData 直接异常）；
        // ：改用 PortraitCache 像素缓存——对话 UI 渲染成功后的 PNG 快照，命中即贴，未命中保持占位圆。

        private float _bannerUntil;     // 横幅自动隐藏时刻（Time.unscaledTime；0=不显示）

        /// <summary>等待缓存读盘的头像（Update 每帧泵：命中即贴，行已销毁自动出列）。</summary>
        private class PendingAvatar { public string Id; public Image Img; }
        private readonly List<PendingAvatar> _pendingAvatars = new List<PendingAvatar>();

        // ------------------------------------------------------------------
        // 生命周期 / 装配
        // ------------------------------------------------------------------

        public void Init(ContactPanelRefs refs, WsClient ws)
        {
            _refs = refs;
            _ws = ws;
            if (_refs?.Window != null) _refs.Window.gameObject.SetActive(false);
            if (_refs?.Dim != null) _refs.Dim.SetActive(false);

            ClickUtils.Attach(GetButton(_refs?.CloseButton), Hide);
            ClickUtils.Attach(GetButton(_refs?.SearchIconButton), OpenSearch);
            ClickUtils.Attach(GetButton(_refs?.SearchCancelButton), CloseSearch);
            ClickUtils.Attach(GetButton(_refs?.TabFriendButton), () => SwitchTab(Tab.Friend));
            ClickUtils.Attach(GetButton(_refs?.TabRecentButton), () => SwitchTab(Tab.Recent));
            ClickUtils.Attach(GetButton(_refs?.TabGroupButton), () => SwitchTab(Tab.Group));
            // 「临时」页（10-24）：按钮在预制件里不存在，由本类在 `EnsureTempTab` 里运行时克隆。
            // 这里照样先挂一次 —— 克隆出来若晚于本方法（懒建），`EnsureTempTab` 会自己再挂。
            ClickUtils.Attach(GetButton(_refs?.TabTempButton), () => SwitchTab(Tab.Temp));
            // 群聊底栏（旧 .ab 里没有 → GetButton(null) 返回 null，Attach 自己会早退）
            ClickUtils.Attach(GetButton(_refs?.NewGroupButton), EnterSelect);
            ClickUtils.Attach(GetButton(_refs?.DelGroupButton), EnterDelete);
            ClickUtils.Attach(GetButton(_refs?.SelectAllButton), SelectAllVisible);
            ClickUtils.Attach(GetButton(_refs?.SelectCancelButton), ExitSelect);
            ClickUtils.Attach(GetButton(_refs?.SelectConfirmButton), ConfirmPick);

            // IL2CPP：UnityEvent 的 lambda 转换不可靠，先转存 Action<string> 再 AddListener（ConfigPresenter 同款）
            System.Action<string> act = OnSearchChanged;
            _onSearchChangedAction = act;
            if (_refs?.SearchInput != null)
                _refs.SearchInput.onValueChanged.AddListener(_onSearchChangedAction);

            // 常驻职责已移交 ContactDuty（静态层，与面板实例解耦）；本组件只是视图：
            ContactDuty.Changed += OnStoreChanged;
            ContactStore.Changed += OnStoreChanged;

            // 「临时」页的 Tab 胶囊：必须在上面那批 Attach **之后**建 —— 建的时候要重新挂一次
            // 点击（克隆不会带走运行期 AddListener 的监听，见 EnsureTempTab 内注释）。
            EnsureTempTab();
        }

        /// <summary>
        /// 建「临时」页的 Tab 胶囊（**运行时克隆**，预制件里没有它）。
        ///
        /// ★ 为什么克隆而不是让预制件多一个节点 ★
        /// 这个面板已有既定路线：行内 ✕ 与时间标签都是运行时新建（`ContactUiBuilder` 里那两个
        /// 方法，注释原话是两个构建版"共用这一条路径"）。走同一条路 ⇒ **旧 `.ab` 也能用**，
        /// 不必进 Unity 重打包、不必重部署 `ModRes/AssetBundle/ab/ui/`。
        ///
        /// ★ 为什么要重排全部 Tab ★
        /// 预制件的 `Tabs` 是 300 宽三等分（每段 100px）。直接往第 4 段塞会**叠在「群聊」上**，
        /// 而 uGUI 只给最上层 raycast target ⇒ 表现为"某个 Tab 点不动"，**零报错**。
        /// 排在「最近」与「群聊」之间（顺序 = 好友 / 最近 / 临时 / 群聊），四个都是两字词，
        /// 四等分后每段 100px（400/4），宽度与原来完全一致。
        ///
        /// 失败一律降级：克隆不出来就把 `TabTempButton` 留空，本页整页不可达，
        /// 面板其余三页照常（与 `MissingOptional` 那套"少一个入口"的取向一致）。
        /// 幂等：已建过直接返回。
        /// </summary>
        private void EnsureTempTab()
        {
            if (_refs == null) return;
            if (_refs.TabTempButton != null) return;
            // 克隆源：优先「群聊」（它也是可选节点、缺失时同样降级），否则「最近」
            var source = _refs.TabGroupButton ?? _refs.TabRecentButton;
            var tabsTr = source != null ? source.transform.parent : null;
            if (source == null || tabsTr == null)
            {
                ModMain.P("[ContactPresenter] 「临时」页 Tab 无法创建：没有可用的克隆源（群聊/最近都缺）"
                          + " → 本页不可达，其余三页照常");
                return;
            }
            try
            {
                var pill = ContactUiBuilder.BuildTabPill(tabsTr, source, "TabTempBtn", "临时");
                if (pill == null)
                {
                    ModMain.P("[ContactPresenter] 「临时」页 Tab 克隆失败 → 本页不可达，其余三页照常");
                    return;
                }
                _refs.TabTempButton = pill;
                // 四等分重排。轨道宽度**仍是 300**（不放大）：每段 75px，而栏目名都是两个字
                // （14px 字号下约 28px 宽），余量充足；放大到 400 反而会溢出 380 宽的面板
                //（预制件里 Tabs 是居中定宽，锚点 0.5/1，溢出会顶出边框外）。
                ContactUiBuilder.LayoutTabsEqually(tabsTr, new GameObject[]
                {
                    _refs.TabFriendButton, _refs.TabRecentButton, _refs.TabTempButton, _refs.TabGroupButton,
                }, 300f);
                // ★ 克隆**不带走**运行期 AddListener 的监听（`Instantiate` 只复制序列化状态），
                //   而 `Init` 里那次 Attach 跑在克隆之前 ⇒ 必须在这里补挂一次。
                ClickUtils.Attach(GetButton(pill), () => SwitchTab(Tab.Temp));
                ModMain.P("[ContactPresenter] 「临时」页 Tab 已建（运行时克隆，四等分 300/4=75px；"
                          + "页内只列「说过话但非好友、非关系」的人）");
            }
            catch (Exception e)
            {
                ModMain.P("[ContactPresenter] EnsureTempTab: " + e);
            }
        }

        private void OnDestroy()
        {
            ContactDuty.Changed -= OnStoreChanged;
            ContactStore.Changed -= OnStoreChanged;
            _refs = null;
            _ws = null;
        }

        /// <summary>常驻层数据变更（面板开着时重渲行，红点即时点亮）。
        ///
        /// 修复（勿再简化）：这里必须**先合并手动层 + 重算互动时间戳**再渲染。
        /// 「常驻职责与面板分离」把 list_contacts/list_sessions 两个 RPC 从本类搬到
        /// ContactDuty/ContactStore 后，只留了 RenderCurrent()，`MergeManualIntoContacts` 与
        /// `RecomputeRecency` 从此**无人调用（死代码）**，后果：
        ///   ① NPC 面板「加好友」写入 contacts.json 成功、按钮也翻转，但通讯录列表里**看不到新增**
        ///      （手动层从未并进 _contacts）；
        ///   ② 「最近」Tab 恒空（LastInteract 永远是 0 → FindAll(LastInteract>0) 命中 0 条）。
        /// 两步都是 O(条目数) 的纯内存操作、且只在 Store 事件（事件驱动）上跑，无轮询开销。
        /// </summary>
        private void OnStoreChanged()
        {
            if (!_isOpen || _refs == null) return;
            MergeManualIntoContacts();
            MergeDeadIntoContacts();
            MergeHeardIntoContacts();
            RecomputeRecency();
            RenderCurrent();
        }

        private void Update()
        {
            // 迁移：F10 轮询已移到常驻的 AbHotkeys（ModMain 的 g.timer.Frame 回调）——
            // 本组件所在根节点在关闭时是 OFF 的（防屏蔽世界输入），Update 不再运行。

            // 立绘缓存：读盘限流泵 + 待贴头像轮询（命中即贴 overrideSprite，行销毁自动出列）
            // ★ 09-19：`PumpLoad()` 同时也挂在常驻的 `AbHotkeys.OnFrame` 上（面板关闭时本
            //   方法不跑，而待读 id 是常驻层塞进来的）。这里保留一份是**无害的冗余**：
            //   队列空即早退，且面板打开时能多推进几张、让头像更快铺满。
            PortraitCache.PumpLoad();
            ApplyPendingAvatars();

            // 未读横幅自动隐藏
            if (_bannerUntil > 0f && Time.unscaledTime > _bannerUntil)
            {
                _bannerUntil = 0f;
                if (_refs?.UnreadBanner != null) _refs.UnreadBanner.SetActive(false);
            }
        }

        /// <summary>行头像：缓存命中（含本帧读盘落地）→ 贴圆形烘焙立绘；未命中 → 排队等读盘，占位圆暂代。
        /// 恒不触碰渲染 API——本方法只读像素快照，绝不调 `PortraitService.Fill`。
        ///
        /// ★ 缓存键取三级，**顺序即优先级**（09-19 修复，勿回退成"只看 e.Unit"）：
        ///   ① `e.UnitId` —— 精确身份（关系数据的 unitID / contacts.json 的 unit_id）。
        ///      这是**最可靠**的一级：它不依赖人此刻在不在场上。
        ///   ② `PortraitCache.IdOf(e.Unit)` —— 手上真握着 `WorldUnitBase` 时现场读。
        ///   ③ `PortraitCache.IdByName(e.Name)` —— 按名解析（带失败限流，见其注释）。
        ///
        /// 事故（用户实证"通讯录存了头像、重进还是未加载"的第二个独立成因）：
        /// 旧写法第一行是 `if (avatarGo == null || e?.Unit == null) return;` —— 在**算 key 之前**
        /// 就返回了。后果是：任何"此刻不在场"的好友（`Unit == null`，如未进场的 NPC、
        /// `ResolveEntry` 按名也没解析到的老记录）**连 `RequestLoad` 都不会调**，盘上那张
        /// `portrait_cache/<unitID>.png` 永远读不出来，行上恒是占位图。
        /// 而且 `MergeManualIntoContacts` 对已存在的条目 `continue`，**不会重试 `ResolveEntry`**
        /// ⇒ 该行在整个会话里都不会自愈（只有玩家点开聊天才重试）。
        ///
        /// 对话窗早就走的是"精确 id → 按名兜底"两级（`RowAvatars.Apply`），通讯录漏了这一层；
        /// 本方法补齐，两侧语义对齐。`SubOf` 那句"人物档案缺失"提示的是**社交档案**读不到，
        /// 与"头像能不能显示"是两回事 —— 有 unitID 就该能贴脸。</summary>
        private void ApplyAvatar(GameObject avatarGo, ContactEntry e)
        {
            if (avatarGo == null || e == null) return;
            var img = avatarGo.GetComponent<Image>();
            if (img == null) return;

            // ① 精确 unitID（条目自带，与 `Unit` 在不在场无关）
            string id = !string.IsNullOrEmpty(e.UnitId) ? e.UnitId : null;
            // ② 现场从真身读（手上握着 WorldUnitBase 时最权威）
            if (string.IsNullOrEmpty(id) && e.Unit != null) id = PortraitCache.IdOf(e.Unit);
            // ③ 按名兜底（内部有失败限流；解析不到返回 null，不排队）
            if (string.IsNullOrEmpty(id)) id = PortraitCache.IdByName(e.Name);
            if (string.IsNullOrEmpty(id)) return;   // 实在不知道是谁 → 保持占位（不猜、不报错）

            if (PortraitCache.TryGetSprite(id, out var sp))
            {
                img.overrideSprite = sp;
                img.type = Image.Type.Simple;
                img.color = Color.white;
                return;
            }
            PortraitCache.RequestLoad(id);
            for (int i = 0; i < _pendingAvatars.Count; i++)
                if (_pendingAvatars[i].Img == img) return;   // 同一行重复入队去重
            _pendingAvatars.Add(new PendingAvatar { Id = id, Img = img });
        }

        private void ApplyPendingAvatars()
        {
            for (int i = _pendingAvatars.Count - 1; i >= 0; i--)
            {
                var p = _pendingAvatars[i];
                if (p.Img == null) { _pendingAvatars.RemoveAt(i); continue; }   // 行已随重建销毁
                if (!PortraitCache.TryGetSprite(p.Id, out var sp)) continue;    // 读盘未落地，下帧再看
                p.Img.overrideSprite = sp;
                p.Img.type = Image.Type.Simple;
                p.Img.color = Color.white;
                _pendingAvatars.RemoveAt(i);
            }
        }

        // ------------------------------------------------------------------
        // 开关（对外：F10 经 ContactPanelOpener；NPC 面板加好友后刷新镜像）
        // ------------------------------------------------------------------

        public bool IsOpen => _isOpen;

        public void TogglePanel()
        {
            if (_isOpen) Hide();
            else Show();
        }

        public void Show()
        {
            if (_refs?.Window == null) return;
            // 修复：关闭态根 OFF，打开前先把根激活
            gameObject.SetActive(true);
            _isOpen = true;
            if (_refs.Dim != null) _refs.Dim.SetActive(true);
            _refs.Window.gameObject.SetActive(true);
            SetStatus("加载中…", StatusDim);

            // ⓪ 打开即全部已读（09-26 用户拍板：点 HUD「传」/F10 打开面板就把未读清掉）。
            //   为什么：可见的提醒载体只有"行红点 + HUD 角标"两处，而角标由 UnreadStore.Any
            //   聚合驱动 —— 旧语义要逐个点开 NPC 对话才清，多人同时有未读时就成了
            //   "点开了 A、角标照旧不灭"（玩家读作"红点点不下去"）。打开面板 = 已看到提醒。
            //   只清"未读标记"：消息本体在各 NPC 的 session 账本里，不丢内容，历史仍可回放。
            //   ⚠ BeginInvite/BeginKick 也经本函数：那里同样是玩家正看着通讯录列表，语义一致。
            UnreadStore.ClearAll();

            // ① 关系层：本地采集（同步，快）
            BuildRelations();
            // ② 手动层 + 会话索引：RPC 由常驻层 ContactDuty 发起（结果写 ContactStore，事件回推重渲）
            // ★ 只有"取得到存档身份"时才发（09-20）★
            // 身份 = 玩家 unitID，取不到（世界尚未加载 / 换档间隙）时 Python 侧必然拿空表
            // "拒绝回答" —— 那一次请求**注定没有信息量**，只是白等 8s 超时，还让日志看起来像
            // "拉过一次但没有朋友"。这种时刻由下面的 ⚠ 如实告知即可；等身份到位后
            // `OnIntoWorldEvent` / `OnWsReconnected` 会自动重拉补齐。
            if (ModMain.WorldIdentityAvailable)
            {
                ContactDuty.RequestManualContacts();
                ContactDuty.RequestSessions();
                ContactDuty.RequestHidden();     // 隐藏名单（行内 ✕ 的"关系层隐藏"侧）
                ContactDuty.RequestKnownContacts();   // 关系层快照（死者不消失的依据）
            }
            else
            {
                // 不静默（教训）：说清"为什么这次没拉"，否则排障时只会看到"名单没更新"
                ModMain.P("[ContactPresenter] 尚未取得存档身份（世界未加载）→ 本次不拉名单，"
                          + "仅显示关系层；进入世界后会自动重拉");
            }
            // ②b 群名录：**本类自己发**（只在本面板打开时有意义，不进常驻层，理由见类头注释）
            RequestRooms();
            // ③ 先把 Store 里**已有**的镜像并进来（RPC 未回前也能显示上次拉到的名单/时间戳；回包后再经事件重算）
            MergeManualIntoContacts();
            MergeDeadIntoContacts();
            MergeHeardIntoContacts();     // 第三层：说过话的人（「临时」页；不需要 RPC 往返）
            RecomputeRecency();
            RenderCurrent();
        }

        public void Hide()
        {
            _isOpen = false;
            // 退出选择模式：关面板 = 放弃这次操作（下次打开从干净状态开始，不留半截选中，
            // 也不留"确认键还武装着"的两段确认态）
            ClearPick();
            // 重置搜索：下次打开从干净状态开始（SearchOverlay 随之关闭；此刻 _isOpen 已 false，RenderCurrent 空转）
            CloseSearch();
            // 方案 A 契约：关闭交给游戏管理器（动画/CloseUIEnd 事件/登记摘除全归它）。
            // 绝不自己 SetActive(false)/Destroy——那正是本轮"世界输入被门控"的根因。
            AbContactPanel.CloseViaManager();
        }

        /// <summary>退出选择模式并清掉各模式自己的选中集（唯一的复位入口，免得漏清一边）。</summary>
        private void ClearPick()
        {
            _pick = PickMode.None;
            _selected.Clear();
            _selectedRooms.Clear();
            _selectedKick.Clear();
            _roomTargetId = "";
            _roomTargetTitle = "";
            _roomTargetMembers.Clear();
            _delConfirm.Disarm();
            _kickConfirm.Disarm();
            // 行内 ✕ 的未完成确认一并作废：关面板/切 Tab/换模式 = 放弃这次操作
            // （复位入口只有一个，漏掉这条就是"确认态跨面板活下来"）
            DisarmUnfriend();
        }

        private void SwitchTab(Tab tab)
        {
            // 切 Tab 一律退出选择模式：否则「群聊→好友→群聊」会带着上一轮的选中回来，
            // 而中途切换时列表源已经变过，玩家会看到一个自己没勾过的选中集合。
            ClearPick();
            _tab = tab;
            RenderCurrent();
        }

        // ------------------------------------------------------------------
        // 数据构建：关系层（本地） + 手动层（RPC）
        // ------------------------------------------------------------------

        private void BuildRelations()
        {
            _contacts.Clear();
            // 关系记录全集（十容器+好友簿+仇人簿+GetAllGoodRelation 含敌）——通讯录层①
            var units = RelationNetwork.CollectKnownUnits();
            foreach (var u in units)
            {
                if (u == null) continue;
                string name = null;
                try { name = u.data.unitData.propertyData.GetName(); } catch { }
                if (string.IsNullOrEmpty(name)) continue;
                // 关系层是**精确**的一层：这些对象是 `RelationNetwork` 用关系数据里存的
                // unitID 经 `GetUnit(unitID)` 查出来的（不是按名猜）。把 ID 一起收着，
                // 后面「拉人建群」就能把精确身份交给 Python 落盘。
                string uid = null;
                try { uid = u.data.unitData.unitID; } catch { }
                _contacts.Add(new ContactEntry
                {
                    Name = name,
                    Unit = u,
                    UnitId = uid,
                    Exact = !string.IsNullOrEmpty(uid),
                    Manual = false,
                });
            }
            // 关系层快照：死者靠它才不会从名单里消失（活着时抓不到，死后就永久没了）。
            // 内容没变时 `ContactDuty` 会整份跳过（签名比对），这里不用自己算去重。
            try { ContactDuty.SyncKnownContacts(RelationNetwork.ToContactSnapshot(units)); }
            catch (Exception ex) { ModMain.P("[ContactPresenter] 关系层快照同步失败: " + ex.Message); }
        }

        /// <summary>条目 → `WorldUnitBase`，**先精确、后按名**。返回是否**精确**命中。
        ///
        /// 两级判据的来由：`contacts.json` 里现在存着精确 `unit_id`（由 C# 在 NPC 面板
        /// 点「加好友」那一刻从真身读到），所以正常情况一步到位。但 ID 可能**陈旧**
        /// （存档变动、单位消失），那时必须能退回按名解析 —— 否则这个好友会变成
        /// "已不在世间"，而人明明还在。
        ///
        /// ★ 为什么不把回退写进 `UnitLookup.ResolveEx`：那条契约是"给了 unitId 就只认它，
        ///   查不到返回 null" —— 工具层（`inspect_unit`）靠它保证"模型说精确查就真的精确"。
        ///   在那里偷偷按名兜底 = 把明确失败降级成一次静默猜测。所以兜底放在**调用方**，
        ///   并且**返回值要把"这次是猜的"如实带出来**（调用方据此决定不许 `Pin`、不许回写）。
        /// </summary>
        private static bool ResolveEntry(ContactEntry e, out WorldUnitBase unit)
        {
            unit = null;
            if (e == null) return false;
            string id = !string.IsNullOrEmpty(e.UnitId) ? e.UnitId : ContactStore.ManualUnitId(e.Name);
            // ① 精确优先（手上已有精确结果的条目跳过重查）
            if (!string.IsNullOrEmpty(id) && (!e.Exact || e.Unit == null))
            {
                List<UnitLookup.Candidate> _;
                var byId = UnitLookup.ResolveEx(e.Name, id, out _);
                if (byId != null) { e.Unit = byId; e.UnitId = id; e.Exact = true; unit = byId; return true; }
                ModMain.P("[ContactPresenter] 精确 unitID 失效（" + e.Name + " → " + id +
                          "），回落按名解析（可能撞重名）");
            }
            if (e.Unit != null) { unit = e.Unit; return e.Exact; }   // 本轮早先解出的结果
            // ② 按名兜底 —— 这一步**是猜的**，所以 Exact 一律记 false。
            //    （即便它内部走了 Pin 登记表而恰好猜对，记 false 也只是少写一次幂等的
            //      Pin/回填，方向安全；反过来把猜的记成 true 才是真危险。）
            unit = UnitLookup.Resolve(e.Name);
            e.Unit = unit;
            e.Exact = false;
            return false;
        }

        /// <summary>
        /// 手动层并入合并列表（**幂等，可反复调用**——Store 每次变更都会重跑）：
        /// ① 刷新"关系层带来的条目"的 Manual 标记（真相＝Python contacts.json 的本地镜像）；
        /// ② 剔除"仅由手动层引入、现已移除"的条目（关系层条目不受影响）；
        /// ③ 补入缺失的手动好友（按名解析 Unit，解析失败也保留条目——点开时 OpenChatFor 会再解析一次）。
        /// </summary>
        private void MergeManualIntoContacts()
        {
            // ① 标记以真相为准（与关系层并存的手动好友 → 行副标题会多一个「· 好友」）
            for (int i = 0; i < _contacts.Count; i++)
            {
                var c = _contacts[i];
                if (c != null) c.Manual = ContactStore.IsManualContact(c.Name);
            }

            // ② 手动独有的条目在「移除好友」后整条出列
            _contacts.RemoveAll(c => c != null && c.ManualOnly && !ContactStore.IsManualContact(c.Name));

            // ③ 补入
            foreach (var name in ContactStore.ManualContactNames)
            {
                if (string.IsNullOrEmpty(name)) continue;
                if (_contacts.Exists(c => c != null && c.Name == name)) continue;
                var e = new ContactEntry
                {
                    Name = name,
                    Manual = true,
                    ManualOnly = true,
                    UnitId = ContactStore.ManualUnitId(name),   // 可能是 null（老记录还没回填）
                };
                ResolveEntry(e, out _);   // 先精确（contacts.json 的 unit_id）后按名
                MarkDeadIfAny(e);         // 手动好友死了：行留着（不许消失），只是标成已故
                _contacts.Add(e);
            }
            SortContacts();
        }

        /// <summary>
        /// 第三层半：「已故但曾经认识」的人并回 `_contacts`。
        ///
        /// ★ 它解决什么 ★ 关系层是**每次渲染现取**游戏数据的，而死者 `GetUnit(id)` 恒 null
        /// （三条采集路都 `if (u == null) return`）⇒ 人一死就从通讯录里**静默消失**。
        /// 死后也反推不出他来过（`ClearDieUnit` 清了关系簿、死者快照 `GetRelation(玩家)=None`），
        /// 所以只能靠 `known.json` 里活着时留下的那份（`BuildRelations` 每次开面板都会推）。
        ///
        /// ★ 唯一一条消费规则，别放宽 ★ **只在 `GetUnit(id)` 解析不到时才补**：
        ///   解析得到 = 他还活着，那就完全由关系层决定去留（断交/改嫁/不在名单里了 ⇒ 照旧不显示，
        ///   不许借快照复活）。
        ///
        /// 成本：每条一次 `GetUnit(id)`（O(1) 字典）+ 一次 `IsDead(id)`（O(1)）。**不做全图/全账本遍历。**
        /// </summary>
        private void MergeDeadIntoContacts()
        {
            // 快照还没拉到（刚进世界 / 未定档）→ 一条都不补：否则面板会先少几行、
            // 回包后再突然冒出来（与 `_manualLoaded` 同一个理由）。
            if (!ContactStore.KnownLoaded) return;
            string playerId = null;
            try { playerId = UnitSnapshot.PlayerUnitId(); } catch { }

            int added = 0;
            foreach (var k in ContactStore.KnownContacts)
            {
                if (k == null || string.IsNullOrEmpty(k.Name)) continue;
                if (!string.IsNullOrEmpty(playerId) && k.Name == playerId) continue;
                if (_contacts.Exists(c => c != null && c.Name == k.Name)) continue;
                // 没有精确身份就不判（按名查死亡账本是遍历；这类老记录等玩家发消息那一步再说）
                if (string.IsNullOrEmpty(k.UnitId)) continue;
                // 解析得到 = 活人：由关系层决定去留，不借快照复活
                List<UnitLookup.Candidate> cands;
                WorldUnitBase alive = null;
                try { alive = UnitLookup.ResolveEx(k.Name, k.UnitId, out cands); } catch { }
                if (alive != null) continue;

                var e = new ContactEntry
                {
                    Name = k.Name,
                    Unit = null,
                    UnitId = k.UnitId,
                    Exact = true,          // 身份是精确的（只是这个 id 现在解析不到活体）
                    Manual = ContactStore.IsManualContact(k.Name),
                    ManualOnly = false,
                    DeadOnly = !ContactStore.IsManualContact(k.Name),
                };
                MarkDeadIfAny(e);
                _contacts.Add(e);
                added++;
            }
            if (added > 0)
            {
                ModMain.P("[ContactPresenter] 已故层并入 " + added + " 人（快照共 "
                          + ContactStore.KnownCount + " 条）");
                SortContacts();
            }
        }

        /// <summary>解析不到真身时补「已故」标记。**只有拿得到精确 unitID 才是 O(1)**；
        /// 没有 ID 的条目按名兜底查一次（条目数很少，且这类老记录本来就少见）。
        /// 非死者什么都不改 —— "解析不到"不等于"死了"（可能是 unitID 失效 / 单位不在场）。</summary>
        private static void MarkDeadIfAny(ContactEntry e)
        {
            if (e == null || e.Unit != null || e.Dead) return;
            try
            {
                string id = !string.IsNullOrEmpty(e.UnitId) ? e.UnitId : ContactStore.KnownUnitId(e.Name);
                if (!UnitSnapshot.IsDead(id, e.Name)) return;
                e.Dead = true;
                e.DeathNote = UnitSnapshot.DeadMonthLabel(id, e.Name);
            }
            catch { }
        }

        /// <summary>
        /// 第三层：「说过话」的人并入 `_contacts`（10-24 的「临时」页）。
        ///
        /// ★ 它解决的问题 ★
        /// 通讯录原本只有两个成员来源：① 游戏关系数据（`BuildRelations` —— **只由游戏原生交互
        /// 写入**，结拜/论道/赠礼才记）② 手动好友（`contacts.json`）。而 MOD 的传音对游戏来说
        /// **压根没发生过**（那是我们从 `npc_initiative` 发指令给 Python、LLM 生成一句话推回来，
        /// 游戏侧一个字节都没写）。于是"他跟我说过话"这件事，两层都不记 ⇒ 横幅弹了、红点亮了，
        /// 打开通讯录却找不到这个人。这就是本次要修的现象。
        ///
        /// ★ 名字从哪来（三份既有记录取并集，**不新增存储**）★
        ///   · `ContactStore.MarkHeard` 的本地即时表 —— `npc_reply` 收口帧到达当帧就记，
        ///     **不需要存档**（这是补"没存档就没有"那个洞的主力，见 `ContactStore._heard`）；
        ///   · 会话账本文件的键 —— `list_sessions` 回包顺带收进来（给老存档兜底）；
        ///   · 持久化互动时刻 —— 同上（上次存档之前的记录）。
        ///
        /// ★ 为什么只标 `HeardOnly` 而不进「好友」页 ★
        /// 语义把两类人分得很清：
        ///   · **好友/关系** = 我认识他（社会身份，游戏里有记录）；
        ///   · **临时** = 他跟我说过话，但我从没认过他（一次性的接触）。
        /// 混在「好友」页会让"我认识的人"这个列表失去意义，而且会诱导玩家以为已经加了好友。
        /// ⇒ 本层条目**只在「临时」页出现**，其余各页由 `IsTempOnly` 滤掉。
        ///
        /// ★ 为什么**不**写 `contacts.json`（不自动加好友）★
        /// 写进去有三个**行为**变化（不是显示变化）：① 他会变成合法的"异地传音"候选
        /// （`NpcInitiativeMonitor` 的门槛是"非通讯录成员仅同格可发起"）② NPC 面板的按钮会变成
        /// 「移除好友」③ 被玩家隐藏过的人会被"好友优先放行"强制冒出来。
        /// 三条都不是修一个显示缺陷该付的代价。**"说过话"是事实，"是好友"是玩家的选择。**
        /// </summary>
        private void MergeHeardIntoContacts()
        {
            // 只清"仍然是临时"的那些。⚠ **不能无条件 `RemoveAll(HeardOnly)`**：
            // 玩家刚把某人加为好友时，`MergeManualIntoContacts` 只会给这条**已有**的条目
            // 置 `Manual=true`（它不会重加一遍）—— 这里若把它一起删掉，本次渲染这个人就
            // 直接消失了（好友页没有、临时页也没有），而下次合并也补不回来。
            _contacts.RemoveAll(c => c != null && c.HeardOnly && !ContactStore.IsManualContact(c.Name));

            var names = ContactStore.HeardNames();
            if (names == null || names.Count == 0) return;
            string me = null;
            try { me = UnitSnapshot.PlayerUnitId(); } catch { }
            for (int i = 0; i < names.Count; i++)
            {
                string name = names[i];
                if (string.IsNullOrEmpty(name)) continue;
                if (name == me) continue;                                  // 防御：账本不该有自己
                if (_contacts.Exists(c => c != null && c.Name == name)) continue;   // 关系层/手动层优先
                // 死者不进「临时」：他没法再说话，留着只会让人点开一个空窗
                // （`IsDead` 是既有判据，与候选集/收件人判定同源）。
                try { if (UnitSnapshot.IsDead("", name)) continue; } catch { }

                var e = new ContactEntry
                {
                    Name = name,
                    HeardOnly = true,
                    // 精确身份：只从「我曾握着真身」的登记表取（点开对话/加好友那一刻写的）。
                    // 取不到就留空 —— **绝不按名猜一个写进来**（`UnitLookup` 文件头那次
                    // "把别人的档案端给模型"的事故，就是猜出来的 id 被当权威用了）。
                    UnitId = UnitIdRegistry.IdOf(name),
                };
                if (!string.IsNullOrEmpty(e.UnitId)) e.Exact = true;        // 登记表里的 = 精确
                _contacts.Add(e);
            }
            // 搜索会跨页返回全量（含本层），补一次排序让结果仍然按拼音序
            //（「临时」页自己按时间倒序，不看这个顺序）。
            SortContacts();
        }

        /// <summary>这一行是不是**只属于「临时」页**（10-24）。
        ///
        /// 判据 = 「说过话引入的」且「不是好友」。两道都判是因为它们会**独立变化**：
        ///   · `HeardOnly` 由 `MergeHeardIntoContacts` 维护（谁进过这一层）；
        ///   · `IsManualContact` 是 Python `contacts.json` 的本地镜像（谁被加过好友）。
        /// 玩家刚点「加好友」时镜像已经翻了、而条目上的 `HeardOnly` 还在 ⇒ 只信标记就会
        /// 让这个人同时挂在「好友」和「临时」两页；只信好友镜像则会在他被移除好友后
        /// 直接从两页都消失。两个一起判，进/出都只有一个方向。
        ///
        /// ⚠ **不看 `c.Unit`**：`OpenChatFor` 会把点开过的人的 `Unit` 填上（`ResolveEntry`），
        ///   若拿"有没有真身"当判据，玩家点一下「临时」里的人，他当场就从这一页蒸发。
        ///   这一页的语义是"我们说过话"，与"此刻能不能解析到真身"无关。</summary>
        private static bool IsTempOnly(ContactEntry c)
        {
            if (c == null || !c.HeardOnly) return false;
            if (c.Manual) return false;
            return !ContactStore.IsManualContact(c.Name);
        }

        /// <summary>互动时间戳重算：会话 mtime ∪ 本地 npc_reply 时间戳（取大）。
        /// 调用点只有两个（Show / OnStoreChanged），删任何一个「最近」Tab 都会恒空</summary>
        private void RecomputeRecency()
        {
            foreach (var c in _contacts)
            {
                c.LastInteract = ContactStore.LastInteract(c.Name);
            }
        }

        private void SortContacts()
        {
            var cmp = NameComparer();
            _contacts.Sort((a, b) =>
            {
                // 已故统一沉底（10-27）：死者混在活人中间会让"还有谁在"看不出来；
                // 而按名字排会让同一个人"死后突然跳到另一个位置"，更怪。
                bool da = a != null && a.Dead, db = b != null && b.Dead;
                if (da != db) return da ? 1 : -1;
                return cmp.Compare(a.Name, b.Name);
            });
        }

        /// <summary>中文名拼音序（zh-CN 文化排序），文化包缺失时回退码位序。
        /// 返回类型用 StringComparer：本工程引用组合下 Comparer&lt;string&gt; 会触发 CS0029 误报</summary>
        private static StringComparer NameComparer()
        {
            try { return StringComparer.Create(CultureInfo.GetCultureInfo("zh-CN"), true); }
            catch { return StringComparer.Ordinal; }
        }

        // ------------------------------------------------------------------
        // 渲染：Tab 视觉 + 行构建（模板克隆）
        // ------------------------------------------------------------------

        private void RenderCurrent()
        {
            if (!_isOpen || _refs == null) return;
            LayoutStatusBar();          // 状态栏钉底（含群聊页让开底栏；见版式注释）
            UpdateTabVisuals();
            UpdateBarVisuals();

            // 踢人：列表源 = 本群成员（**不是**群列表）。与建群/拉人不同，它没有搜索的意义
            // （成员就那么几个，且搜索框的过滤对象在这是人也说得通）→ 仍然套用搜索，
            // 免得搜索框在这一页变成"输入了没反应"的死控件。
            if (_pick == PickMode.Kick)
            {
                RenderKickList();
                return;
            }

            // 群聊页：只有**建群选人**会把列表源换成联系人；解散选群仍然列群（选的就是群）
            if (_tab == Tab.Group && _pick != PickMode.Create && _pick != PickMode.Invite)
            {
                RenderRoomList();
                return;
            }

            List<ContactEntry> rows;
            if (_search.Length > 0)
            {
                // 搜索**跨全部页面**（含「临时」）：它是"按名字找人"的功能，玩家记得名字却
                // 想不起在哪一页时不该被页签挡住。行副标题会标出「传音过」，看得见它属于哪类。
                rows = _contacts.FindAll(MatchSearch);
            }
            else if (_tab == Tab.Temp)
            {
                // 「临时」页（10-24）：只有"说过话、但既非好友也非社交关系"的人。
                // 排序同「最近」（他找过我 = 时间倒序）；`HeardOnly` 的行必然有互动时间，
                // 但**不拿它当进入条件** —— 判据是 `IsTempOnly`（见其注释）。
                var temp = _contacts.FindAll(IsTempOnly);
                temp.Sort((a, b) =>
                {
                    int byTime = b.LastInteract.CompareTo(a.LastInteract);
                    if (byTime != 0) return byTime;
                    return string.CompareOrdinal(a.Name ?? "", b.Name ?? "");
                });
                if (temp.Count > RECENT_MAX) temp.RemoveRange(RECENT_MAX, temp.Count - RECENT_MAX);
                rows = temp;
            }
            else if (_tab == Tab.Recent)
            {
                // 「最近」= 聊过天的人，但**不再包含只属于「临时」页的**：
                // 这一页的语义是"我认识的人里谁最近说过话"，把一次性接触混进去，
                // 玩家会以为这些人都已经进了好友簿（而其中很多人他只是见过一面）。
                var recent = _contacts.FindAll(c => c.LastInteract > 0 && !IsTempOnly(c));
                // ★ 10-21：**先分层、再比时间**（顺序写反 = 原 bug 的镜像形态）★
                //   只按 `LastInteract` 比大小时，刚存过一次档就会把所有 `mtime` 刷成"此刻"，
                //   而此刻系统性晚于所有人的真实说话时刻 ⇒ **从没说过话的老 NPC 排到最前面**
                //   （模拟复盘实测到：`管星贤/公冶抚/司徒月` 顶掉了刚说过话的 `姜萌/江宇文`）。
                //   `HasInteraction` = "有真实互动记录"，它与存档无关 ⇒ 这条分层对存档免疫。
                //   层内才比时间：第 2 层（只有文件时间=未知）按 mtime 倒序，仅作稳定次序。
                recent.Sort((a, b) =>
                {
                    bool ha = ContactStore.HasInteraction(a.Name);
                    bool hb = ContactStore.HasInteraction(b.Name);
                    if (ha != hb) return ha ? -1 : 1;          // 已知永远在未知之前
                    int byTime = b.LastInteract.CompareTo(a.LastInteract);
                    if (byTime != 0) return byTime;
                    // 完全相等时按名字定序：否则每次重渲顺序都可能变（列表"自己会抖"）
                    return string.CompareOrdinal(a.Name ?? "", b.Name ?? "");
                });
                if (recent.Count > RECENT_MAX) recent.RemoveRange(RECENT_MAX, recent.Count - RECENT_MAX);
                rows = recent;
            }
            else
            {
                // 「好友」页 = 关系层 ∪ 手动层。**必须滤掉 `HeardOnly` 的行**：
                // 这一页回答的是"我认识哪些人"，而一次擦肩而过不该算认识
                //（否则每个跟我说过话的陌生人都杵在好友簿里，好友列表就失去意义了）。
                rows = _contacts.FindAll(c => !IsTempOnly(c));
            }
            // 建群/拉人：**已故的人不能入群**（选人的两个入口都走这一支，故在这里一刀切）。
            // 好友页照常显示他们 —— 死亡不改名单成员，只是不能再一起做事。
            if (_pick == PickMode.Create || _pick == PickMode.Invite)
                rows = rows.FindAll(c => c != null && !c.Dead);
            // 拉人：把已在群里的人从列表里摘掉 —— 「列表里能勾的」必须恰好等于「拉了会有用的人」。
            // 不摘的话，勾一个已在群里的好友会得到一个"成功但没变化"的回执（Python 的
            // add_member 对已在群里的人返回 False，`added` 里没有他），玩家只会觉得没反应。
            if (_pick == PickMode.Invite) rows = ExcludeRoomMembers(rows);
            // 隐藏名单过滤（09-26 用户拍板）：隐藏者不渲染；**好友优先** —— 被重新加为好友的人
            // 必须显示（玩家显式加好友 = 明确要联系），即便名单里还留着记录。
            // 放在渲染层而不是 `_contacts`：名单保持全量，"踢人"等按其它来源渲染的页面不受影响。
            rows = ExcludeHidden(rows);

            RebuildRows(rows);

            // 状态条
            // ★ 未定档优先说（09-20 实机事故）：它回答的是"**这份名单可不可信**"，比"排序方式"
            //   之类的信息性文案要紧得多。不放在最前，玩家就会看到一份**残缺**名单配一句
            //   "好友按字母排序 · 点行或 ✓ 传音"，从而以为好友真的丢了（而 contacts.json 好好的）。
            //   放在 `_pick`/`_search` 之后是刻意的：那两种是玩家正在进行的操作，反馈优先。
            //
            //   文案分两种情形（09-20 收尾）—— 它们**成因不同、该怎么做的建议也不同**，
            //   混成一句会让玩家照着一个做不到的建议干等：
            //     · 身份取不到（世界还没加载）→ 建议"进世界"（存/读档）
            //     · 身份取得到但 Python 还没收到（帧在路上/未定档）→ 建议"稍候"，重拉已排好
            if (_pick == PickMode.None && _search.Length == 0 && ContactStore.WorldUnknown)
                SetStatus(ModMain.WorldIdentityAvailable
                    ? "⚠ 存档身份尚未送达智能体，名单可能不完整 — 请稍候（进入世界后会自动重拉）"
                    : "⚠ 尚未取得存档身份，名单可能不完整 — 请先进入世界（存一次档或读一次档）",
                    StatusWarn);
            else if (_pick == PickMode.Invite)
                SetStatus(rows.Count == 0
                    ? "没有可拉进群的好友（好友都已在群里，或还没有好友）"
                    : "勾选要拉进「" + TargetLabel() + "」的好友，再点「确认」"
                      + "（★ 新加入的成员看不到入群前的对话）",
                    StatusDim);
            else if (_pick == PickMode.Create)
                SetStatus(rows.Count == 0 ? "没有可拉进群的好友" : "勾选要拉进群的好友，再点「确认」", StatusDim);
            else if (_search.Length > 0)
                SetStatus("搜索「" + _search + "」：命中 " + rows.Count + " 人", StatusDim);
            else if (_tab == Tab.Temp)
                SetStatus(rows.Count == 0
                    ? (ContactStore.HeardLoaded
                        ? "还没有临时传音（跟你说过话、但还没加好友的人会出现在这里）"
                        : "临时传音记录加载中…")
                    : "这些人和你通过话，但还不是好友 · 点行继续传音，或在他的面板里加好友",
                    StatusDim);
            else if (_tab == Tab.Recent)
                SetStatus(rows.Count == 0 ? "暂无最近互动（聊过天的好友会出现在这里）" : "按最近互动排序", StatusDim);
            else if (!ContactStore.ManualLoaded && _ws != null)
                SetStatus("好友按字母排序（通讯录名单加载中…）", StatusDim);
            else if (rows.Count > MAX_ROWS)
                SetStatus("共 " + rows.Count + " 位好友，显示前 " + MAX_ROWS + " 位（搜索可精确查找）", StatusDim);
            else
                SetStatus("好友按字母排序 · 点行或 ✓ 传音", StatusDim);

            // 一次性回执（如"已移除好友：X"）：默认文案全部写完后顶上去，只显示这一次渲染
            //（行内 ✕ 的执行反馈必须走这里，直接 SetStatus 会被上面这条默认文案当场覆盖）。
            if (_pendingStatus.Length > 0)
            {
                SetStatus(_pendingStatus, _pendingStatusColor);
                _pendingStatus = "";
            }
        }

        /// <summary>群聊页：列出现有群，点一行进群续聊（**解散模式下**点一行 = 勾选它）。
        ///
        /// 搜索在这页**同样生效**（按群名或任一成员名过滤）。不接的话，搜索框在群聊页
        /// 会变成一个"输入了没反应"的死控件 —— 而它明明还在屏幕上（顶栏那个图标是共用的）。
        /// 群通常不多，但"看得见却没用"的控件比没有更糟。</summary>
        private void RenderRoomList()
        {
            List<RoomEntry> rows = _rooms;
            if (_search.Length > 0)
                rows = _rooms.FindAll(MatchRoomSearch);
            RebuildRoomRows(rows);
            if (_pick == PickMode.Delete)
            {
                SetStatus(_selectedRooms.Count == 0
                    ? "勾选要解散的群聊（可多选），再点「确认删除」"
                    : "将解散 " + _selectedRooms.Count + " 个群聊（**不可恢复**，再点一次「确认删除？」执行）",
                    _selectedRooms.Count == 0 ? StatusDim : StatusWarn);
            }
            else if (_rooms.Count == 0)
                SetStatus(_roomsLoaded ? "还没有群聊，点下方「＋ 新建群聊」" : "群聊名录加载中…", StatusDim);
            else if (_search.Length > 0)
                SetStatus("搜索「" + _search + "」：命中 " + rows.Count + " 个群聊", StatusDim);
            else if (_rooms.Count > MAX_ROWS)
                SetStatus("共 " + _rooms.Count + " 个群聊，显示前 " + MAX_ROWS + " 个", StatusDim);
            else
                SetStatus("共 " + _rooms.Count + " 个群聊 · 点一行继续聊", StatusDim);
        }

        // ------------------------------------------------------------------
        // 群成员变更（拉人 / 踢人）—— 由**对话窗**顶栏那两个按钮发起
        //
        // 为什么挑人界面放在传音簿而不是对话窗：拉人要从**好友名单**里选，那份名单只有
        // 传音簿有（它自己就是通讯录）。既然拉人必须借道这里，踢人就跟着对称走同一条路
        // （列表源换成"本群成员"），两处的勾选行 / 立绘 / 计数条 / 两段确认全部复用现成的。
        // 在对话窗里另造一套"成员列表 + 选择条"还要与历史删除模式共用 `DeleteBar` 并互斥，
        // 收益不抵复杂度。
        // ------------------------------------------------------------------

        /// <summary>进「拉人」模式（对话窗「拉人」按钮 → `AbContactPanel.BeginInviteStatic`）。
        /// 返回 false = 面板不可用（装配失败），调用方据此给玩家一句明确提示。</summary>
        public bool BeginInvite(string roomId, List<string> members, string title)
        {
            if (string.IsNullOrEmpty(roomId) || _refs?.Window == null) return false;
            ClearPick();
            _pick = PickMode.Invite;
            SetRoomTarget(roomId, members, title);
            _tab = Tab.Group;      // 高亮仍落在「群聊」——这确实是群聊页的一个子状态
            ModMain.P("[ContactPresenter] 进拉人模式：目标=" + roomId
                      + "（现有 " + _roomTargetMembers.Count + " 人）");
            Show();                // Show 内部的 RenderCurrent 会按 _pick 渲染出候选名单
            return true;
        }

        /// <summary>进「踢人」模式（对话窗「踢人」按钮 → `AbContactPanel.BeginKickStatic`）。</summary>
        public bool BeginKick(string roomId, List<string> members, string title)
        {
            if (string.IsNullOrEmpty(roomId) || _refs?.Window == null) return false;
            if (members == null || members.Count == 0) return false;
            ClearPick();
            _pick = PickMode.Kick;
            SetRoomTarget(roomId, members, title);
            _tab = Tab.Group;
            ModMain.P("[ContactPresenter] 进踢人模式：目标=" + roomId + "，候选 " + _roomTargetMembers.Count + " 人");
            Show();
            return true;
        }

        private void SetRoomTarget(string roomId, List<string> members, string title)
        {
            _roomTargetId = roomId ?? "";
            _roomTargetTitle = title ?? "";
            _roomTargetMembers.Clear();
            if (members == null) return;
            for (int i = 0; i < members.Count; i++)
            {
                string m = members[i];
                if (!string.IsNullOrEmpty(m) && !_roomTargetMembers.Contains(m)) _roomTargetMembers.Add(m);
            }
        }

        /// <summary>状态条里那个群的名字（空则回落 room_id —— 绝不显示空白）。</summary>
        private string TargetLabel()
        {
            return string.IsNullOrEmpty(_roomTargetTitle) ? _roomTargetId : _roomTargetTitle;
        }

        /// <summary>把已在群里的人从候选里摘掉（**返回新表，不动 `_contacts`**。
        /// 就地 RemoveAll 会把"已在群里"这件事永久写进通讯录名单，换群后候选就少人了）。</summary>
        private List<ContactEntry> ExcludeRoomMembers(List<ContactEntry> rows)
        {
            var outp = new List<ContactEntry>();
            if (rows == null) return outp;
            for (int i = 0; i < rows.Count; i++)
            {
                var c = rows[i];
                if (c == null) continue;
                if (_roomTargetMembers.Contains(c.Name)) continue;
                outp.Add(c);
            }
            return outp;
        }

        /// <summary>把被隐藏的人从**渲染结果**里摘掉（返回新表，不动 `_contacts`）。
        ///
        /// 好友优先（09-26 用户拍板）：`c.Manual` 为真一律放行 —— 玩家重新加为好友 = 明确要联系，
        /// 不能因为名字还留在隐藏名单里就看不见他。
        /// 名单为空时原样返回（`HasHidden` 短路），常路径零成本。</summary>
        private static List<ContactEntry> ExcludeHidden(List<ContactEntry> rows)
        {
            if (rows == null || rows.Count == 0 || !ContactStore.HasHidden) return rows;
            var outp = new List<ContactEntry>(rows.Count);
            for (int i = 0; i < rows.Count; i++)
            {
                var c = rows[i];
                if (c == null) continue;
                if (!c.Manual && ContactStore.IsHidden(c.Name)) continue;
                outp.Add(c);
            }
            return outp;
        }

        /// <summary>踢人模式的列表：本群成员，按通讯录里的条目取（**带立绘**）；
        /// 通讯录里没有的成员（不是好友 / 解析不到单位）也**照样列出来** ——
        /// 否则"不是好友的人踢不掉"，而他明明在群里说话。</summary>
        private void RenderKickList()
        {
            List<ContactEntry> entries = new List<ContactEntry>();
            for (int i = 0; i < _roomTargetMembers.Count; i++)
            {
                string name = _roomTargetMembers[i];
                if (string.IsNullOrEmpty(name)) continue;
                if (_search.Length > 0 && name.IndexOf(_search, StringComparison.Ordinal) < 0) continue;
                var found = _contacts.Find(c => c != null && c.Name == name);
                entries.Add(found ?? new ContactEntry { Name = name, Unit = null });
            }
            RebuildRows(entries);
            if (_selectedKick.Count == 0)
                SetStatus(entries.Count == 0
                    ? (_search.Length > 0 ? "搜索「" + _search + "」：群里没有匹配的成员"
                                          : "这个群聊里没有成员")
                    : "勾选要移出「" + TargetLabel() + "」的成员，再点「确认移出」",
                    StatusDim);
            else
                SetStatus("将移出 " + _selectedKick.Count + " 人（他记得之前聊过的事，"
                          + "但之后的群消息不再发给他）", StatusWarn);
        }

        /// <summary>勾选/取消一个**成员**（踢人模式）。选中集一变就 `Disarm()`：
        /// 两段确认的语义是"确认**这一个**动作"，列表换了，上一次的武装不该继续生效
        /// （同 `ToggleRoom` 的理由）。</summary>
        private void ToggleKick(string name)
        {
            if (string.IsNullOrEmpty(name)) return;
            if (!_selectedKick.Remove(name)) _selectedKick.Add(name);
            _kickConfirm.Disarm();
            RenderCurrent();
        }

        private bool MatchRoomSearch(RoomEntry r)
        {
            if (r == null) return false;
            if (!string.IsNullOrEmpty(r.Title) && r.Title.IndexOf(_search, StringComparison.Ordinal) >= 0)
                return true;
            if (!string.IsNullOrEmpty(r.RoomId) && r.RoomId.IndexOf(_search, StringComparison.Ordinal) >= 0)
                return true;
            for (int i = 0; i < r.Members.Count; i++)
                if (r.Members[i] != null && r.Members[i].IndexOf(_search, StringComparison.Ordinal) >= 0)
                    return true;
            return false;
        }

        private bool MatchSearch(ContactEntry c)
        {
            if (c.Name.IndexOf(_search, StringComparison.Ordinal) >= 0) return true;
            string sub = SubOf(c);
            return sub != null && sub.IndexOf(_search, StringComparison.Ordinal) >= 0;
        }

        private void UpdateTabVisuals()
        {
            SetTabSelected(_refs?.TabFriendButton, _tab == Tab.Friend);
            SetTabSelected(_refs?.TabRecentButton, _tab == Tab.Recent);
            // 选择模式仍高亮「群聊」——它确实是群聊页的一个子状态，高亮跳到「好友」会误导
            SetTabSelected(_refs?.TabGroupButton, _tab == Tab.Group);
            SetTabSelected(_refs?.TabTempButton, _tab == Tab.Temp);
        }

        /// <summary>底栏显隐。**必须每次渲染都显式设置每一个节点**（不能只设一部分、指望其余
        /// 保持出厂值或上一轮的值）：出厂 `NewGroupBtn`/`DelGroupBtn`/`SelectBar` 全是隐藏的，
        /// 一旦某条分支漏设，表现就是"该出现时不出现"或"选完人回来还挂着确认条"，
        /// 且**没有任何报错**。
        ///
        /// 互斥：两个按钮（新建 / 解散）只在**普通模式**显示；一旦进了任一选择模式，
        /// 整条换成 `SelectBar`（它整条铺满，会盖住那两个按钮 —— 留着只会让人误点，
        /// 而且"盖住"意味着点击落到 SelectBar 上，看起来像按钮失灵）。</summary>
        private void UpdateBarVisuals()
        {
            bool showBtns = _tab == Tab.Group && _pick == PickMode.None;
            if (_refs?.NewGroupButton != null) _refs.NewGroupButton.SetActive(showBtns);
            if (_refs?.DelGroupButton != null) _refs.DelGroupButton.SetActive(showBtns);
            if (_refs?.SelectBar != null) _refs.SelectBar.SetActive(_pick != PickMode.None);
            UpdateSelectCount();
            UpdateConfirmLabel();
        }

        /// <summary>计数条文案（每个模式各说各的；解散/踢人说"将…"而不是"已选"，
        /// 因为它们是**把别人挪走**的动作，用"已选"会读成"选了要聊天的人"）。</summary>
        private void UpdateSelectCount()
        {
            if (_refs?.SelectCountLabel == null) return;
            if (_pick == PickMode.Delete)
                _refs.SelectCountLabel.text = "将解散 " + _selectedRooms.Count + " 个群";
            else if (_pick == PickMode.Kick)
                _refs.SelectCountLabel.text = "将移出 " + _selectedKick.Count + " 人";
            else if (_pick == PickMode.Create || _pick == PickMode.Invite)
                _refs.SelectCountLabel.text = "已选 " + _selected.Count + " 人";
            else
                _refs.SelectCountLabel.text = "";
        }

        /// <summary>确认键文案。解散/踢人走两段确认 ⇒ 武装后必须**换文案**，否则玩家看不出
        /// "这一下会真的执行"，两段确认就退化成"要点两次的按钮"。</summary>
        private void UpdateConfirmLabel()
        {
            if (_refs?.SelectConfirmButton == null) return;
            var txt = _refs.SelectConfirmButton.GetComponentInChildren<Text>(true);
            if (txt == null) return;
            if (_pick == PickMode.Delete)
                txt.text = _delConfirm.Armed ? "确认删除？" : "确认删除";
            else if (_pick == PickMode.Kick)
                txt.text = _kickConfirm.Armed ? "确认移出？" : "确认移出";
            else
                txt.text = "确认";     // 与预制件出厂文案一致（建群/拉人/普通三种情形都是它）
        }

        private static void SetTabSelected(GameObject pill, bool selected)
        {
            if (pill == null) return;
            var img = pill.GetComponent<Image>();
            if (img != null) img.color = selected ? Color.white : new Color(1f, 1f, 1f, 0f);
            var txt = pill.GetComponentInChildren<Text>(true);
            if (txt != null)
            {
                txt.color = selected ? AccentGreen : TextFaint;
                txt.fontStyle = selected ? FontStyle.Bold : FontStyle.Normal;
            }
        }

        private void RebuildRows(List<ContactEntry> rows)
        {
            // 重渲 = 行对象全部重建：未完成的"移除好友确认"必须随之作废 —— 否则武装态还活着，
            // 而 ✕ 的确认文案已随旧行销毁（玩家看不到自己在确认什么），再点一下却真的删了。
            // （DisarmUnfriend 对已销毁图标的复位是安全的：Unity fake-null 判空早退。）
            DisarmUnfriend();

            var content = _refs?.ContactContent;
            var template = _refs?.ContactItemTemplate;
            if (content == null || template == null) return;

            // 清旧行（IL2CPP：索引循环，禁 foreach(Transform)）
            for (int i = content.childCount - 1; i >= 0; i--)
                UnityEngine.Object.Destroy(content.GetChild(i).gameObject);

            if (rows == null) return;
            // 「最近」与「临时」两个 Tab 行右侧打相对时间（10-22 微信式；判档见 `ContactTimeCore`）。
            // 「临时」页加时间是因为它**按时间倒序**排（最近跟我说过话的排前面），
            // 不打时间玩家看不出这个顺序；「好友」页按字母序，时间与排序无关，不打。
            bool showTime = _tab == Tab.Recent || _tab == Tab.Temp;
            long nowSec = showTime ? DateTimeOffset.UtcNow.ToUnixTimeSeconds() : 0;
            _lastRowNames.Clear();
            int shown = Mathf.Min(rows.Count, MAX_ROWS);
            for (int i = 0; i < shown; i++)
            {
                var e = rows[i];
                if (e == null || string.IsNullOrEmpty(e.Name)) continue;
                _lastRowNames.Add(e.Name);      // 「全选」的作用域 = 真正渲染出来的这些行
                var clone = UnityEngine.Object.Instantiate(template);
                clone.transform.SetParent(content, false);
                clone.SetActive(true);

                var nameTxt = clone.transform.Find("Name")?.GetComponent<Text>();
                if (nameTxt != null) nameTxt.text = e.Name;
                var subTxt = clone.transform.Find("Sub")?.GetComponent<Text>();
                if (subTxt != null) subTxt.text = SubOf(e) ?? "";

                // 行右上的最近互动时间（仅「最近」Tab 且真有时间；无记录 = 不打空标签）。
                // Time 是**运行时新建**节点（AB 预制件里没有它，与行内 ✕ 同款路径；
                // 行对象每轮渲染全新建 ⇒ 不需要先 Find 现成的）。Name 右边界同步让位防压字。
                if (showTime && e.LastInteract > 0)
                {
                    var timeTxt = ContactUiBuilder.BuildRowTimeText(clone.transform);
                    if (timeTxt != null) timeTxt.text = ContactTimeCore.Format(nowSec, e.LastInteract);
                    if (nameTxt != null)
                        nameTxt.rectTransform.offsetMax = new Vector2(-166f, nameTxt.rectTransform.offsetMax.y);
                }

                // 未读红点：该 NPC 有未读传音时点亮
                var dot = clone.transform.Find("UnreadDot")?.gameObject;
                if (dot != null) dot.SetActive(UnreadStore.Has(e.Name));

                // 头像：PortraitCache 像素快照命中→贴立绘（圆形烘焙），未命中→占位圆+排队读盘
                var avatarGo = clone.transform.Find("Avatar")?.gameObject;
                if (avatarGo != null) ApplyAvatar(avatarGo, e);

                // ★ 复选框与 ✓ 按钮**同格、且复选框在上层**（预制件里 CheckBox 是行内最后一个
                //   子节点）。uGUI 拾取只给最上层的 raycast target ⇒ 两者必须**互斥显隐**：
                //   选人模式里 ✓ 让位，正常模式里复选框让位。漏掉任何一边，表现都是
                //   "看得见却点不动"，而且零报错。
                // ★★ `Check` 必须写**相对路径**：它是 `CheckBox` 的子节点（预制件结构
                //   `ContactItem/CheckBox/Check`），而 `FindChild` 走的是 `Transform.Find(name)`
                //   —— 只认**直接子节点**。传裸名 "Check" 会静默拿到 null ⇒ `check.SetActive`
                //   永不执行 ⇒ 勾选时只有底部计数在动、行内那个绿勾**永远不亮**，零报错
                //   （09-17 实机复现：建群选人点了 2 个人，"已选 2 人"在动，行内没反应）。
                //   路径与 `AbContactPanel.CollectRefs` 的同名契约一致 —— 那边是全路径
                //   `BG/ContactItem/CheckBox/Check`，由 `test_contact_ui_contract.py` 钉住。
                var checkBox = FindChild(clone.transform, "CheckBox");
                var check = FindChild(clone.transform, "CheckBox/Check");
                var chatBtnGo = FindChild(clone.transform, "ChatBtn");
                // 四个选择模式（建群/拉人/踢人）都要复选框；只有普通模式留给 ✓ 传音键。
                // ★ 踢人用的是**另一份**选中集（`_selectedKick`）：好友名与成员名分开清，
                //   共用一个 list 会让"拉人勾到一半改去踢人"带着一堆好友名进踢人流程。
                bool pickMode = _pick == PickMode.Create || _pick == PickMode.Invite || _pick == PickMode.Kick;
                bool isKick = _pick == PickMode.Kick;
                bool isChecked = pickMode && (isKick ? _selectedKick.Contains(e.Name) : _selected.Contains(e.Name));
                if (checkBox != null) checkBox.SetActive(pickMode);
                if (check != null) check.SetActive(isChecked);
                if (chatBtnGo != null) chatBtnGo.SetActive(!pickMode);

                // 行点击：正常模式=传音；任一选择模式=勾选/取消
                string nm = e.Name;
                var captured = e;
                if (pickMode)
                {
                    if (isKick) ClickUtils.Attach(clone.GetComponent<Button>(), () => ToggleKick(nm));
                    else ClickUtils.Attach(clone.GetComponent<Button>(), () => ToggleSelect(nm));
                }
                else ClickUtils.Attach(clone.GetComponent<Button>(), () => OpenChatFor(captured));
                // 复选框自己也是个 Button（点到方框上也算勾选）；✓ 只在正常模式下可点
                if (checkBox != null)
                {
                    if (isKick) ClickUtils.Attach(checkBox.GetComponent<Button>(), () => ToggleKick(nm));
                    else ClickUtils.Attach(checkBox.GetComponent<Button>(), () => ToggleSelect(nm));
                }
                if (!pickMode && chatBtnGo != null)
                {
                    var chatBtn = chatBtnGo.GetComponent<Button>();
                    if (chatBtn != null) ClickUtils.Attach(chatBtn, () => OpenChatFor(captured));
                }

                // ── 行内 ✕「移除」（09-26 用户拍板：✓ 左侧、**所有行**、两段确认）──
                // 位置**相对 ✓ 现算**（不依赖预制件数值，AB/代码版通用）；行内未读红点随之左移让位
                //（不然 ✕ 会盖在红点上/红点盖在 ✕ 上）。
                // 语义（见 `OnRemoveRow`）：好友行 = 删好友（并顺带隐藏仍有关系的部分）；
                // 纯关系行 = 隐藏（关系数据不动）。选择模式里一律让位 —— 否则勾人时会点到删除。
                GameObject removeBtnGo = null;
                Text removeIcon = null;
                var chatRt = chatBtnGo != null ? UiRects.Of(chatBtnGo.transform) : null;
                if (chatRt != null)
                {
                    removeBtnGo = ContactUiBuilder.BuildRowRemoveButton(clone.transform, chatRt, out removeIcon);
                    if (dot != null && removeBtnGo != null)
                    {
                        var drt = UiRects.Of(dot.transform);
                        var rrt = UiRects.Of(removeBtnGo.transform);
                        if (drt != null && rrt != null)
                            drt.anchoredPosition = new Vector2(
                                rrt.anchoredPosition.x - (rrt.sizeDelta.x * 0.5f + 8f + drt.sizeDelta.x * 0.5f),
                                drt.anchoredPosition.y);
                    }
                }
                bool canRemove = !pickMode;
                if (removeBtnGo != null)
                {
                    removeBtnGo.SetActive(canRemove);
                    if (canRemove)
                        ClickUtils.Attach(removeBtnGo.GetComponent<Button>(),
                                          () => OnRemoveRow(captured, removeIcon));
                }
            }
        }

        /// <summary>群聊页的行构建：复用同一个 `ContactItem` 模板（所以没有第二套版式要维护）。
        /// 群头像**不贴立绘**：一个群没有唯一的脸，拿首位成员的立绘充当是撒谎；
        /// 群名（默认「与甲、乙的聚谈」）已经把成员写在名字里了。
        ///
        /// 复选框：**只在解散模式显示**（那时行点击 = 勾选这个群）；普通模式下这里的行点击是
        /// "进群"。这一行不是可选的 —— 模板里复选框出厂可见（父节点 BG 出厂隐藏，所以预制件里
        /// 看不出来），不显式关掉的话它会盖在 ✓ 上，把进群按钮的点击吃掉。</summary>
        private void RebuildRoomRows(List<RoomEntry> rows)
        {
            var content = _refs?.ContactContent;
            var template = _refs?.ContactItemTemplate;
            if (content == null || template == null) return;
            for (int i = content.childCount - 1; i >= 0; i--)
                UnityEngine.Object.Destroy(content.GetChild(i).gameObject);
            // 「全选」的作用域按**这次渲染出来的是谁**决定，所以两边都要维护：
            // 不清的话，切到群列表后「全选」还会去勾一批早已销毁的联系人行。
            _lastRowNames.Clear();
            _lastRoomIds.Clear();
            if (rows == null) return;
            bool del = _pick == PickMode.Delete;
            int shown = Mathf.Min(rows.Count, MAX_ROWS);
            for (int i = 0; i < shown; i++)
            {
                var r = rows[i];
                if (r == null || string.IsNullOrEmpty(r.RoomId)) continue;
                _lastRoomIds.Add(r.RoomId);
                var clone = UnityEngine.Object.Instantiate(template);
                clone.transform.SetParent(content, false);
                clone.SetActive(true);

                var nameTxt = clone.transform.Find("Name")?.GetComponent<Text>();
                if (nameTxt != null)
                    nameTxt.text = string.IsNullOrEmpty(r.Title) ? r.RoomId : r.Title;
                var subTxt = clone.transform.Find("Sub")?.GetComponent<Text>();
                if (subTxt != null)
                    subTxt.text = r.Members.Count + " 人 · " + r.LastSeq + " 条发言"
                                  + (r.Members.Count > 0 ? " · " + string.Join("、", r.Members.ToArray()) : "");

                var dot = FindChild(clone.transform, "UnreadDot");
                if (dot != null) dot.SetActive(false);      // 群级未读是 P3 第 5 步，此处先不亮
                // ★ 集合模式里两者必须互斥（见 RebuildRows 的长注释）：解散模式下 ✓ 让位给复选框
                // ★★ `Check` 是 `CheckBox` 的子节点，同样必须走相对路径 —— 理由与后果
                //   见 `RebuildRows` 的同名注释（传裸名 = 静默 null = 解散群聊的勾也不亮）。
                //    这里修了、`RebuildRows` 没修的话，就是"建群能勾、解散不能勾"这种
                //    只在一半路径上发作的缺陷 —— 两处必须同时保有相对路径。
                var checkBox = FindChild(clone.transform, "CheckBox");
                var check = FindChild(clone.transform, "CheckBox/Check");
                var chatBtnGo = FindChild(clone.transform, "ChatBtn");
                if (checkBox != null) checkBox.SetActive(del);
                if (check != null) check.SetActive(del && _selectedRooms.Contains(r.RoomId));
                if (chatBtnGo != null) chatBtnGo.SetActive(!del);

                string rid = r.RoomId, title = r.Title;
                if (del)
                {
                    ClickUtils.Attach(clone.GetComponent<Button>(), () => ToggleRoom(rid));
                    if (checkBox != null)
                        ClickUtils.Attach(checkBox.GetComponent<Button>(), () => ToggleRoom(rid));
                }
                else
                {
                    ClickUtils.Attach(clone.GetComponent<Button>(), () => OpenRoomFor(rid, title));
                    if (chatBtnGo != null)
                    {
                        var chatBtn = chatBtnGo.GetComponent<Button>();
                        if (chatBtn != null) ClickUtils.Attach(chatBtn, () => OpenRoomFor(rid, title));
                    }
                }
            }
        }

        // ------------------------------------------------------------------
        // 行点击 → 对话 UI
        // ------------------------------------------------------------------

        private void OpenChatFor(ContactEntry e)
        {
            if (e == null) return;
            // 先精确（contacts.json / 关系数据里的 unitID）后按名。返回的 exact 决定
            // 这个人能不能进 `UnitLookup` 登记表 —— 猜来的人不许钉（见 ResolveEntry 注释）。
            bool exact = ResolveEntry(e, out var unit);
            if (unit == null)
            {
                // 解析不到真身 = 死者 / unitID 失效。**不再挡在这里**（10-27）：
                // 旧实现直接"已不在世间"返回，于是逝者的旧对话**永远打不开**。
                // 下层本来就支持按名开窗 —— Python `open_chat` 只认 `npc_id`，
                // 账本从 jsonl 恢复（见 `ChatLauncher.OpenForName`）。
                if (string.IsNullOrEmpty(e.Name))
                {
                    SetStatus("找不到对应人物", StatusWarn);
                    return;
                }
                UnreadStore.Clear(e.Name);
                Hide();
                if (!ChatLauncher.OpenForName(e.Name, e.UnitId))
                {
                    // 开不出来要说话（不许静默）：面板已经 Hide，只能把现场写进日志
                    ModMain.P("[ContactPresenter] 按名开窗失败：npc=" + e.Name
                              + " unitID=" + (e.UnitId ?? "null") + " Dead=" + e.Dead);
                }
                return;
            }
            UnreadStore.Clear(e.Name);   // 打开即已读（未读指示由 HUD 按钮红点承载）
            Hide();   // 通讯录 Canvas 3050 高于对话窗：先藏自己再开对话
            ChatLauncher.OpenForUnit(unit, guessed: !exact);
        }

        // ------------------------------------------------------------------
        // 行内 ✕：移除好友（两段确认）
        // ------------------------------------------------------------------

        /// <summary>
        /// 行内 ✕ 点击（两段确认，**无超时窗口**：首击变「确认」，再点同一行即执行）。
        ///
        /// 双语义（09-26 用户拍板 —— ✕ = "尽量把这个人从通讯录移除"）：
        ///   · **好友行**（`Manual`）→ 删好友（与 NPC 面板「移除好友」同一条链路：本地镜像即时
        ///     翻转 + `remove_contact` RPC）；若关系层仍有他 → **顺带隐藏**，行就此消失
        ///     （否则删完好友行还杵在那儿，玩家会以为"没删掉"）。
        ///   · **纯关系行** → 隐藏（写 `hidden.json`），**关系数据一点不动**：道侣仍是道侣、
        ///     剧情/好感照常，只是不在通讯录渲染。
        /// 重现规则：该 NPC 再次主动传音时由 `ContactDuty.UnhideOnInitiative` 取消隐藏（唯一出口）。
        /// `unitId` 一并交上去：删/藏都不靠它，但"这个名字是谁"的事实不该因为一次移除而丢掉。
        /// </summary>
        private void OnRemoveRow(ContactEntry e, Text icon)
        {
            if (e == null) return;
            if (_unfriendArmName != e.Name)
            {
                // 首击（含"换行点另一行"）：旧武装先复位，本行进入确认态。
                // ★ 无超时窗口（09-26 用户拍板）：再点**同一行**即执行，间隔多久都算数；
                //   换行 / 重渲 / 关面板 → 作废（见 DisarmUnfriend 的各调用点）。
                DisarmUnfriend();
                _unfriendArmName = e.Name;
                _unfriendArmIcon = icon;
                SetRemoveIcon(icon, true);
                SetStatus(e.Manual
                    ? "再点一次 ✕ 确认移除好友「" + e.Name + "」"
                    : "再点一次 ✕ 确认隐藏「" + e.Name + "」（不在通讯录显示）", StatusWarn);
                return;
            }

            // 第二击：执行（先把确认态褪掉再动手）
            DisarmUnfriend();
            bool wasFriend = e.Manual;
            // 关系层也会有他（或本来就是纯关系行）。
            // ★ 已故行格外要隐藏（10-27）★ 关系层这回合已经拿不到他，判不出"关系还在不在"；
            //   不隐藏的话，纯手动好友被移除后会被 `MergeDeadIntoContacts` 从快照里原地**补回来**
            //   —— 玩家点一次 ✕、他回来一次，像删不掉。
            bool keepRelation = !e.ManualOnly || e.Dead;
            ModMain.P("[ContactPresenter] 行内 ✕：" + (wasFriend ? "移除好友" : "隐藏") + " " + e.Name
                      + "（unitID=" + (e.UnitId ?? "null") + "，Manual=" + e.Manual
                      + "，ManualOnly=" + e.ManualOnly + "）");
            if (wasFriend)
            {
                ContactDuty.SetManualContact(e.Name, false, e.UnitId);
                ContactDuty.ToggleManualContact(e.Name, false, e.UnitId);
            }
            if (keepRelation)
                ContactDuty.SetHidden(e.Name, true, e.UnitId);

            // 回执必须说清"行会不会消失、关系还在不在"—— 三种终态各一句。
            // 已故行单独一句：说"他再次传音时会回来"在死者身上是**假话**（他再也传不了音），
            // 而"已隐藏"本身是准的（hidden.json 里留着，玩家手动取消隐藏仍能看到）。
            _pendingStatus = e.Dead
                ? "已隐藏故人：" + e.Name + (keepRelation ? "（关系仍在，只是不在通讯录显示）" : "")
                : wasFriend
                    ? (keepRelation ? "已移除好友并隐藏：" + e.Name + "（关系仍在，他再次传音时会回来）"
                                    : "已移除好友：" + e.Name)
                    : "已隐藏：" + e.Name + "（关系仍在，他再次传音时会回来）";
            _pendingStatusColor = StatusOk;
            RenderCurrent();
        }

        /// <summary>✕ 的确认态视觉：武装时圆内文案「确认」（该行待确认），否则回到「✕」。</summary>
        private static void SetRemoveIcon(Text icon, bool armed)
        {
            if (icon == null) return;
            icon.text = armed ? "确认" : "✕";
            icon.fontSize = armed ? 11 : 15;
        }

        /// <summary>解除"移除好友"的确认态（换行 / 重渲 / 关面板 / 执行后统一走这里）。
        /// 有武装行时把它的 ✕ 图标复位回「✕」；该行已随重渲销毁时是 fake-null，
        /// `SetRemoveIcon` 自己会早退（不会去碰已销毁对象）。</summary>
        private void DisarmUnfriend()
        {
            if (_unfriendArmIcon != null) SetRemoveIcon(_unfriendArmIcon, false);
            _unfriendArmIcon = null;
            _unfriendArmName = "";
        }

        // ------------------------------------------------------------------
        // 群聊页：名录 / 选人建群 / 进群
        // ------------------------------------------------------------------

        /// <summary>拉群名录（`list_rooms`）。失败静默保持现状 —— 群里没有东西可聊不是错误。
        ///
        /// ★ 结果**不走 ContactStore**：那个静态层服务的是"面板不在场也要活着"的常驻职责，
        ///   而群名录只在面板打开时有意义（`Show()` 现拉）。放进 Store 只会多一个
        ///   跨面板生命周期的真相源，且与 ContactDuty 的职责边界打架。
        ///
        /// 回包时面板可能已经关了（用户手快）：`RenderCurrent` 自己会因 `!_isOpen` 早退，
        /// 但 `_rooms` 照样填好 ⇒ 下次打开立刻就有内容，不必等这一轮 RPC。</summary>
        private void RequestRooms()
        {
            if (_ws == null) return;
            _ws.SendRequest("list_rooms", new JObject(), resp =>
            {
                if (resp?["ok"]?.Value<bool>() != true)
                {
                    ModMain.P("[ContactPresenter] list_rooms 失败：ok=" + resp?["ok"]
                              + " error=" + resp?["error"]);
                    return;   // 保持 _roomsLoaded=false ⇒ 状态条继续显示"加载中"，不谎报"没有群"
                }
                _rooms.Clear();
                var arr = resp["data"]?["rooms"] as JArray;
                if (arr != null)
                {
                    foreach (var t in arr)
                    {
                        string rid = (string)t?["room_id"] ?? "";
                        if (rid.Length == 0) continue;
                        var e = new RoomEntry
                        {
                            RoomId = rid,
                            Title = (string)t["title"] ?? "",
                            LastSeq = t["last_seq"]?.Value<long>() ?? 0,
                            Created = t["created"]?.Value<long>() ?? 0,
                        };
                        var ms = t["members"] as JArray;
                        if (ms != null) foreach (var m in ms) e.Members.Add((string)m ?? "");
                        _rooms.Add(e);
                    }
                }
                // 同一次回包里的 last_seq 是真值（Python 侧 to_meta 会补读流水），
                // 不再需要"名录兜底值"那套；见 docs/archive/2026-09-17-群聊编排/设计定稿.md §13.1。
                _roomsLoaded = true;
                ModMain.P("[ContactPresenter] list_rooms 回来 " + _rooms.Count + " 个群");
                RenderCurrent();
            }, 8000);
        }

        /// <summary>进群续聊：先藏自己（本面板 Canvas 高于对话窗）再开群窗 —— 与 OpenChatFor 同一套次序。</summary>
        private void OpenRoomFor(string roomId, string title)
        {
            if (string.IsNullOrEmpty(roomId)) return;
            Hide();
            ChatLauncher.OpenForRoom(roomId, null, title ?? "");
        }

        /// <summary>进建群选人模式（群聊页 →「＋ 新建群聊」）。列表源随即切到联系人（见 RenderCurrent）。</summary>
        private void EnterSelect()
        {
            ClearPick();
            _pick = PickMode.Create;
            RenderCurrent();
        }

        /// <summary>进解散选群模式（群聊页 →「删除群聊」）。
        ///
        /// 列表源**不变**（还是群列表）—— 解散选的就是群。与建群模式共用底部那条 SelectBar，
        /// 差别只在：列表源、计数文案、确认键走两段确认（`ConfirmPick` 里分派）。</summary>
        private void EnterDelete()
        {
            ClearPick();
            _pick = PickMode.Delete;
            RenderCurrent();
        }

        /// <summary>「确认」→ **拉人进群**。成员名单排序后发出（同建群：同一批人无论勾选次序
        /// 如何，送给 Python 的都是同一个列表，日志/回执可比对）。
        ///
        /// 与建群的关键差别：room_id 是**已有的**（不新建群），所以走 `add_room_members` ——
        /// 刻意**不借** `open_room` 的重开分支：那条路会顺带把全量历史再吐一遍、
        /// 且 title 非空时还会顺手改群名（`ws_channel.py` 的注释里写明了这两个副作用）。
        ///
        /// 成功后 `Hide()` 回对话窗（是它把我们叫出来的），再由 `NotifyMembersChangedStatic`
        /// 把新名单交给它 —— 拉人那句话里必须带"看不到入群前的对话"（见 ChatPresenter）。</summary>
        private void ConfirmInvite()
        {
            if (_selected.Count == 0)
            {
                SetStatus("请先勾选要拉进群的好友", StatusWarn);
                return;
            }
            var members = new List<string>(_selected);
            members.Sort(NameComparer());
            string roomId = _roomTargetId;
            ModMain.P("[ContactPresenter] 拉人：" + string.Join("、", members.ToArray()) + " → " + roomId);
            ClearPick();
            Hide();
            if (_ws == null)
            {
                ModMain.P("[ContactPresenter] 拉人中止：WsClient 未就绪");
                return;
            }
            var p = new JObject { ["room_id"] = roomId };
            var arr = new JArray();
            foreach (var m in members) arr.Add(m);
            p["members"] = arr;
            // 精确身份（可选）：同建群 —— 只有 `Exact` 的条目才收，猜的一律不收。
            var unitIds = SelectedUnitIds(members);
            if (unitIds.Count > 0) p["member_unit_ids"] = unitIds;
            _ws.SendRequest("add_room_members", p, resp =>
            {
                if (!RpcOk(resp, "add_room_members")) return;
                var data = resp["data"] as JObject;
                var now = new List<string>();
                var ma = data?["members"] as JArray;
                if (ma != null) foreach (var t in ma) now.Add((string)t ?? "");
                var added = new List<string>();
                var aa = data?["added"] as JArray;
                if (aa != null) foreach (var t in aa) added.Add((string)t ?? "");
                // 回传的全员精确身份一并交给对话窗（老成员也能补上）。
                // 09-17 起对话窗**不再**拿它去灌全局登记表 —— 身份由 Python 随帧携带，
                // 这里留着只是为了让成员表/预热与回包对齐。
                AbChatPanel.NotifyMembersChangedStatic(roomId, now, added, null,
                                                       data?["member_unit_ids"] as JObject);
                RequestRooms();      // 群列表的"N 人 / M 条发言"要跟着变
            }, 8000);
        }

        /// <summary>「确认移出」→ 踢人（**两段确认**）。
        ///
        /// 为什么必须两段：踢错人不会丢数据（他的记忆还在，也能再拉回来），但他在群里
        /// **之后的每句话都收不到**，而玩家不一定当场发现。与解散同款范式（`TwoStepConfirm`）：
        /// 首击只武装 + 换文案，窗口内再击才真发 RPC，换选中目标 / 超时自动解除武装。
        ///
        /// 多个成员 = 多个 RPC，按**最后一个回执**统一回报名单（与解散那条的 `pending` 计数同款：
        /// 每个回执都回一次名单会让对话窗刷 N 行系统提示）。</summary>
        private void ConfirmKick()
        {
            if (_selectedKick.Count == 0)
            {
                _kickConfirm.Disarm();
                SetStatus("请先勾选要移出群聊的成员", StatusWarn);
                UpdateConfirmLabel();
                return;
            }
            var step = _kickConfirm.Click(Time.time);
            if (step == TwoStepConfirm.Step.First)
            {
                SetStatus("再点一次「确认移出？」即把 " + string.Join("、", _selectedKick.ToArray())
                          + " 移出「" + TargetLabel() + "」（他记得之前聊过的事，但之后的群消息不再发给他）",
                    StatusWarn);
                UpdateConfirmLabel();
                return;
            }
            if (step == TwoStepConfirm.Step.Expired)
            {
                SetStatus("确认已超时，请重新点击「确认移出」", StatusWarn);
                UpdateConfirmLabel();
                return;
            }
            ExecuteKick();
        }

        /// <summary>真踢。**乐观更新不做**（与解散不同）：成员名单的真相在 Python 侧，
        /// 而这里没有一份本地成员表可以先行摘除 —— 回执就是唯一的名单位置。</summary>
        private void ExecuteKick()
        {
            var targets = new List<string>(_selectedKick);
            string roomId = _roomTargetId;
            _kickConfirm.Disarm();
            ClearPick();
            Hide();
            if (_ws == null)
            {
                ModMain.P("[ContactPresenter] 踢人中止：WsClient 未就绪");
                return;
            }
            ModMain.P("[ContactPresenter] 踢出 " + targets.Count + " 人：" + string.Join("、", targets.ToArray())
                      + " ← " + roomId);
            int pending = targets.Count;
            var removedAll = new List<string>();
            var lastMembers = new List<string>();
            for (int i = 0; i < targets.Count; i++)
            {
                string npc = targets[i];
                var p = new JObject { ["room_id"] = roomId, ["npc_id"] = npc };
                _ws.SendRequest("kick_room_member", p, resp =>
                {
                    if (RpcOk(resp, "kick_room_member"))
                    {
                        var data = resp["data"] as JObject;
                        if (data?["removed"]?.Value<bool>() == true) removedAll.Add(npc);
                        var ma = data?["members"] as JArray;
                        if (ma != null)
                        {
                            lastMembers.Clear();
                            foreach (var t in ma) lastMembers.Add((string)t ?? "");
                        }
                    }
                    if (--pending > 0) return;   // 等最后一个回执，只回报一次
                    AbChatPanel.NotifyMembersChangedStatic(roomId, lastMembers, null, removedAll);
                    RequestRooms();
                }, 8000);
            }
        }

        /// <summary>统一判回执成功（失败打日志，返回 false）。业务失败在 `data.ok=false`
        /// 那条路上（与删除的 `RpcError` 同源语义，但这里不需要把原因回显到状态条 ——
        /// 面板已经 `Hide()` 了，玩家看不到状态条了）。</summary>
        private static bool RpcOk(JObject resp, string what)
        {
            bool ok = resp?["ok"]?.Value<bool>() == true
                      && (resp["data"]?["ok"]?.Value<bool>() ?? true);
            if (!ok)
            {
                try
                {
                    ModMain.P("[ContactPresenter] " + what + " 失败：ok=" + resp?["ok"]
                              + " error=" + resp?["error"]
                              + " reason=" + resp?["data"]?["reason"]);
                }
                catch { }
            }
            return ok;
        }

        /// <summary>退出选择模式（取消 / 切 Tab / 关面板都汇到"清空选中 + 解除武装"）。
        ///
        /// ★ 拉人/踢人是**从对话窗跳过来**的：取消 = "算了，回群里去"，所以要连面板一起关掉
        /// （否则玩家被留在通讯录里，得自己再按 F10 关）。建群/解散是 F10 自己打开的，
        /// 取消只退出选择模式、面板留着 —— 两者语义不同，不能一刀切。</summary>
        private void ExitSelect()
        {
            bool fromRoom = _pick == PickMode.Invite || _pick == PickMode.Kick;
            ClearPick();
            if (fromRoom && _isOpen) { Hide(); return; }
            RenderCurrent();
        }

        /// <summary>勾选/取消一个人。用 List 而不是 HashSet 是为了**保持勾选顺序**，
        /// 好让 status/日志能复现玩家的操作次序；成员名单真正发给 Python 前会再排序。</summary>
        private void ToggleSelect(string name)
        {
            if (string.IsNullOrEmpty(name)) return;
            if (!_selected.Remove(name)) _selected.Add(name);
            RenderCurrent();
        }

        /// <summary>勾选/取消一个**群**（解散模式，可多选）。
        ///
        /// 选中集一变就 `Disarm()`：两段确认的语义是"确认**这一个**动作"，而列表已经换了，
        /// 上一次的武装不该继续对着一批新的群生效（`TwoStepConfirm` 的注释里也把它列为
        /// 必须解除武装的时机之一：换目标）。</summary>
        private void ToggleRoom(string roomId)
        {
            if (string.IsNullOrEmpty(roomId)) return;
            if (!_selectedRooms.Remove(roomId)) _selectedRooms.Add(roomId);
            _delConfirm.Disarm();
            RenderCurrent();
        }

        /// <summary>「全选」= 勾上**当前列表里真正渲染出来的那些行**（两个模式各自的作用域）。
        ///
        /// 为什么不选全量：列表有 `MAX_ROWS=50` 的上限，而关系层的好友可能上百。
        /// 按全量选，玩家会得到一堆**看不见也取消不掉**的勾 —— 界面上显示"已选 120 人"、
        /// 却只有 50 行可操作，这是最坏的一类 UI 缺陷（状态与所见不一致，且无从修复）。
        /// 作用域取 `_lastRowNames` / `_lastRoomIds` 就天然与所见一致（含搜索过滤后的结果）。</summary>
        private void SelectAllVisible()
        {
            if (_pick == PickMode.Delete)
            {
                for (int i = 0; i < _lastRoomIds.Count; i++)
                    if (!_selectedRooms.Contains(_lastRoomIds[i])) _selectedRooms.Add(_lastRoomIds[i]);
                _delConfirm.Disarm();
            }
            else if (_pick == PickMode.Kick)
            {
                for (int i = 0; i < _lastRowNames.Count; i++)
                    if (!_selectedKick.Contains(_lastRowNames[i])) _selectedKick.Add(_lastRowNames[i]);
                _kickConfirm.Disarm();
            }
            else
            {
                for (int i = 0; i < _lastRowNames.Count; i++)
                    if (!_selected.Contains(_lastRowNames[i])) _selected.Add(_lastRowNames[i]);
            }
            RenderCurrent();
        }

        /// <summary>「确认」按当前模式分派。**四处必须同时改**（这里 + 两个文案 + RenderCurrent），
        /// 漏一处就是"确认键干的是上一个模式的事"，而且不报错。</summary>
        private void ConfirmPick()
        {
            if (_pick == PickMode.Delete) { ConfirmDelete(); return; }
            if (_pick == PickMode.Kick) { ConfirmKick(); return; }
            if (_pick == PickMode.Invite) { ConfirmInvite(); return; }
            ConfirmCreate();
        }

        /// <summary>「确认」→ **建群**（`create_room`）。成员名单**排序后**发出：同一批人无论
        /// 勾选次序如何，送给 Python 的都是同一个列表（Python 按它建 `Room.members`，
        /// 顺序会进默认群名与轮转起点 —— 不排序的话"同一批人"会有多种表述）。
        ///
        /// ★ **建群 ≠ 开窗**：这里发的是 `create_room`（只建、只回名录条目），
        ///   **不**开对话窗、**不** `Hide()`。建完**留在通讯录**、退回群聊页，
        ///   新群出现在列表里；玩家点那一行才开窗（`OpenRoomFor` → `open_room`）。
        ///   与「点好友 = 开单聊」对称。
        ///
        /// 为什么不能让建群去走 `open_room`（09-17 之前的写法）：
        ///   ① 语义错位 —— 把"弹出一个对话窗"当成建群的副作用，于是通讯录被关掉、
        ///      玩家被踢出面板；
        ///   ② **失败态无家可归** —— 一旦回执失败，屏幕上留下的是"通讯录没了 +
        ///      一个空群窗 + 标题只剩占位符「npc 名称」+ 一行报错"，而**群其实已经建成了**
        ///      （Python 侧 `建群：g1` 就打在报错前一行）。"以为失败、实际成功"比彻底失败更糟：
        ///      玩家会重复建，或者以为这个群不存在；
        ///   ③ `open_room` 的发送点被契约钉死只有 1 处（群窗的唯一开启路径，
        ///      见 `test_there_is_exactly_one_open_room_path`）—— 通讯录再发一个就是
        ///      第二个真相源。
        ///
        /// 失败时**不清选中集、不退出选人模式**：勾还在，玩家可以直接重试，
        /// 而不是把刚选的人也一起弄丢（那会逼他重挑一遍）。
        /// 回执即名录条目 ⇒ **直接插进本地 `_rooms` 并立刻渲染**，不补发 `list_rooms`：
        /// 少一次往返就少一个"两次回包互相覆盖"的窗口，反馈也是即时的。</summary>
        private void ConfirmCreate()
        {
            if (_selected.Count == 0)
            {
                SetStatus("请先勾选要拉进群的好友", StatusWarn);
                return;
            }
            if (_ws == null || !_ws.IsConnected)
            {
                SetStatus(BrainLink.SendBlockedNotice(), StatusWarn);
                return;
            }
            var members = new List<string>(_selected);
            members.Sort(NameComparer());
            // ★ 在成功之后才 ClearPick() —— 失败要保留选中集（见方法注释）。
            //   身份来自 `_contacts` 条目，与选中集无关，但"这一刻"正是我们手里还握着
            //   真身信息的时候，取值与建群请求放在一起。
            var unitIds = SelectedUnitIds(members);
            var p = new JObject();
            var arr = new JArray();
            foreach (var m in members) arr.Add(m);
            p["members"] = arr;
            if (unitIds.Count > 0) p["member_unit_ids"] = unitIds;
            ModMain.P("[ContactPresenter] 建群：" + string.Join("、", members.ToArray())
                      + "（精确身份 " + unitIds.Count + " 人）");
            _ws.SendRequest("create_room", p, resp =>
            {
                if (!RpcOk(resp, "create_room"))
                {
                    SetStatus("建群失败：" + (resp?["error"]?.ToString() ?? "未知错误")
                              + "（勾选还在，可重试）", StatusWarn);
                    return;
                }
                var data = resp["data"] as JObject;
                string rid = (string)data?["room_id"] ?? "";
                if (rid.Length == 0)
                {
                    SetStatus("建群失败：Python 未返回 room_id（勾选还在，可重试）", StatusWarn);
                    return;
                }
                var entry = new RoomEntry
                {
                    RoomId = rid,
                    Title = (string)data["title"] ?? "",
                    LastSeq = data["last_seq"]?.Value<long>() ?? 0,
                    Created = data["created"]?.Value<long>() ?? 0,
                };
                var ms = data["members"] as JArray;
                if (ms != null) foreach (var m in ms) entry.Members.Add((string)m ?? "");
                _rooms.RemoveAll(x => x != null && x.RoomId == rid);
                _rooms.Add(entry);
                _roomsLoaded = true;          // 这一刻名录是权威的（回执就是真相源）
                ClearPick();                  // 成功后：退选人模式 + 清选中集
                RenderCurrent();              // 退回群聊页（**面板留着**）
                SetStatus("已建群「" + (entry.Title.Length > 0 ? entry.Title : rid)
                          + "」—— 点这一行进入", StatusOk);
            }, 8000);
        }

        /// <summary>把选中的名字翻成 `{npc_id: unitID}` —— **只收精确的那些**。
        ///
        /// 判据是 `ContactEntry.Exact`（`ResolveEntry` 的返回值）：关系层条目天然为 true
        /// （那些对象就是用关系数据里存的 unitID 经 `GetUnit` 精确查出来的）；手动独有条目
        /// 只有在 `contacts.json` 里存了 unit_id、且它此刻仍能解析时才是 true。
        ///
        /// ★ **猜来的（`Exact == false`）一律不收** —— 把一个猜测交给 Python 落盘，就是把它
        ///   固化到磁盘、跨会话生效，而且再也看不出它是猜的（`UnitIdRegistry.Remember` 同款理由：
        ///   "把'猜'的结果固化下来，比不登记更糟"）。
        ///   缺项**完全无害**：消费侧回落按名解析，行为与本功能上线前一致。
        ///
        /// 用 dict（而不是与 members 平行的数组）是刻意的：**顺序无关**，不存在"两端对不齐
        /// 就把甲的身份安到乙头上"这种失败模式（那正是本工程最怕的静默错人）。</summary>
        private JObject SelectedUnitIds(List<string> names)
        {
            var o = new JObject();
            if (names == null || _contacts == null) return o;
            for (int i = 0; i < names.Count; i++)
            {
                string n = names[i];
                if (string.IsNullOrEmpty(n)) continue;
                ContactEntry e = _contacts.Find(c => c != null && c.Name == n);
                if (e == null || !e.Exact || string.IsNullOrEmpty(e.UnitId)) continue;
                o[n] = e.UnitId;
            }
            return o;
        }

        /// <summary>「确认删除」→ 解散选中的群（**两段确认**）。
        ///
        /// 为什么必须两段：`dissolve_room` 会**unlink 掉群的流水文件**，删了就回不来
        /// （成员的记忆还在，但公共账本没了）。工程对不可恢复操作已有统一范式
        /// （`TwoStepConfirm`，用在「删除文件」「重置为默认」上），这里照用：
        ///   首击 → 只武装，按钮文案变「确认删除？」+ 状态条说明后果；
        ///   窗口内再击 → 真删；
        ///   超时 / 换选中目标 → 自动解除武装（`Click` 与 `ToggleRoom` 各管一头）。
        /// 不做这一步的话，"手滑点一下确认"就等于永久删掉一段群聊记录。</summary>
        private void ConfirmDelete()
        {
            if (_selectedRooms.Count == 0)
            {
                _delConfirm.Disarm();
                SetStatus("请先勾选要解散的群聊", StatusWarn);
                UpdateConfirmLabel();
                return;
            }
            var step = _delConfirm.Click(Time.time);
            if (step == TwoStepConfirm.Step.First)
            {
                SetStatus("再点一次「确认删除？」即解散 " + _selectedRooms.Count
                          + " 个群聊（**不可恢复**：群聊记录会被删除，NPC 的记忆保留）", StatusWarn);
                UpdateConfirmLabel();
                return;
            }
            if (step == TwoStepConfirm.Step.Expired)
            {
                SetStatus("确认已超时，请重新点击「确认删除」", StatusWarn);
                UpdateConfirmLabel();
                return;
            }
            ExecuteDissolve();
        }

        /// <summary>真删。**乐观更新**：先从本地列表摘掉再发 RPC，点击立刻有反馈；
        /// 回执之后重拉 `list_rooms` 校准（本地猜错了会被纠正回来）。</summary>
        private void ExecuteDissolve()
        {
            var ids = new List<string>(_selectedRooms);
            _delConfirm.Disarm();
            ClearPick();
            for (int i = _rooms.Count - 1; i >= 0; i--)
                if (ids.Contains(_rooms[i].RoomId)) _rooms.RemoveAt(i);
            RenderCurrent();

            if (_ws == null)
            {
                SetStatus("已从列表移除，但未连接 Python —— 重开面板会回来", StatusWarn);
                return;
            }
            ModMain.P("[ContactPresenter] 解散 " + ids.Count + " 个群：" + string.Join("、", ids.ToArray()));
            int pending = ids.Count;
            for (int i = 0; i < ids.Count; i++)
            {
                string rid = ids[i];
                _ws.SendRequest("dissolve_room", new JObject { ["room_id"] = rid }, resp =>
                {
                    bool ok = resp?["ok"]?.Value<bool>() == true;
                    bool gone = ok && (resp["data"]?["dissolved"]?.Value<bool>() ?? false);
                    ModMain.P("[ContactPresenter] dissolve_room " + rid + " ok=" + ok
                              + " dissolved=" + gone + " error=" + resp?["error"]);
                    // 对话窗正开着这个群 ⇒ 让它回单聊外观（否则它继续显示一个已不存在的群，
                    // 而且往后的发言永远收不到）
                    if (ok) AbChatPanel.NotifyRoomDissolvedStatic(rid);
                    // ★ 失败必须让玩家看见（09-17 复核修）：列表是**乐观删除**的（点击立刻有反馈），
                    //   而原先失败分支只写日志 —— 表现为"群先是没了、几秒后又自己回来"，
                    //   中间**一句解释都没有**（用户只会当成 bug）。这里如实报原因，
                    //   `RequestRooms()` 会把列表拉回真相（Python 那边确实还在）。
                    if (!ok)
                        SetStatus("解散失败：" + (resp?["error"]?.ToString() ?? "未知原因")
                                  + "（该群仍在列表里）", StatusWarn);
                    // 等最后一个回执再重拉名录，避免 N 次 list_rooms 互相覆盖
                    if (--pending <= 0) RequestRooms();
                }, 8000);
            }
        }

        /// <summary>行内子节点查找（找不到返回 null）。`Transform.Find(path)` 支持相对路径，
        /// 用它比逐级 FindChild 更短，但路径拼错只会静默 null —— 所以名字都写成常量、
        /// 且由契约测试钉住（`test_contact_ui_contract.py` 的 C# 源码半段）。</summary>
        private static GameObject FindChild(Transform parent, string name)
        {
            if (parent == null) return null;
            var t = parent.Find(name);
            return t != null ? t.gameObject : null;
        }

        // ------------------------------------------------------------------
        // 手动好友（NPC 面板「加好友」按钮对外接口）
        // ------------------------------------------------------------------

        // 手动好友的查询/翻转已迁往 ContactStore（查询）与 ContactDuty（RPC）——
        // NpcPanelAddContact / NpcInitiativeMonitor 直接走常驻层，不再依赖面板实例。

        // ------------------------------------------------------------------
        // 搜索 / 小工具
        // ------------------------------------------------------------------

        private void OpenSearch()
        {
            if (_refs?.SearchOverlay == null) return;
            _refs.SearchOverlay.SetActive(true);
            try { _refs.SearchInput?.ActivateInputField(); } catch { }
        }

        private void CloseSearch()
        {
            if (_refs?.SearchOverlay != null) _refs.SearchOverlay.SetActive(false);
            if (_refs?.SearchInput != null)
            {
                if (_onSearchChangedAction != null) _refs.SearchInput.onValueChanged.RemoveListener(_onSearchChangedAction);
                _refs.SearchInput.text = "";   // 清空即恢复全量（onValueChanged 会被短暂屏蔽，手动刷一次）
                if (_onSearchChangedAction != null) _refs.SearchInput.onValueChanged.AddListener(_onSearchChangedAction);
            }
            _search = "";
            RenderCurrent();
        }

        private void OnSearchChanged(string value)
        {
            _search = (value ?? "").Trim();
            RenderCurrent();
        }

        /// <summary>副标题：宗门·境界 · 关系 好感（关系层实时读游戏，手动无 Unit 时兜底文案）。
        /// 「临时」行的口径单独一支（见下）：它标的是"关系性质"，不是读不到的档案。</summary>
        private static string SubOf(ContactEntry e)
        {
            if (e == null) return "";
            // 「临时」页的行（10-24）：副标题是**关系性质**，不是"读不到的档案"。
            // 固定文案，不看 `e.Unit` —— 那是个会变的状态（`ResolveEntry` 会把它填上），
            // 拿它拼文案会让同一行在点开前后显示成两句话。宗门/境界在这里也没有意义：
            // 这一页要回答的是"这人跟我什么关系"，而答案是"只有过一次传音"。
            if (IsTempOnly(e)) return "传音过 · 未结识";
            if (e.Unit == null)
            {
                // 已故行（10-27）：关系标签只能来自快照 —— 死者快照的
                // `GetRelation(玩家)` 返回 None（真机确认），活体也解析不到。
                if (e.Dead)
                {
                    string rel = ContactStore.KnownRelation(e.Name);
                    string s = "已故";
                    if (!string.IsNullOrEmpty(rel)) s += " · " + rel;
                    if (!string.IsNullOrEmpty(e.DeathNote)) s += " · " + e.DeathNote;
                    return s;
                }
                return e.Manual ? "通讯录好友 · 人物档案缺失" : "";
            }
            try
            {
                var ud = e.Unit.data.unitData;
                string sect = UnitSnapshot.SectName(e.Unit);
                // 境界走 UnitSnapshot.RealmText（面板同源口径）——旧写法 `GetGradeName(pd.gradeID)`
                // 把 RoleGrade 的**行号**(1..44) 当**大境界号**(1..10) 喂进去，越界钳到最大档
                // ⇒ 高阶 NPC 全显示"登仙境"（09-13 已定案事故，见 UnitSnapshot.RealmOf 长注释）。
                string realm = UnitSnapshot.RealmText(e.Unit);
                string head = string.IsNullOrEmpty(realm) ? sect
                            : string.IsNullOrEmpty(sect) ? realm
                            : sect + "·" + realm;
                string relPart = "";
                try
                {
                    var player = g.world.playerUnit;
                    if (player != null)
                    {
                        string relEn = ud.relationData.GetRelation(player).ToString();
                        string relCn = UnitSnapshot.RelationCn(relEn);
                        int intim = ud.relationData.GetIntim(player);
                        if (intim < 0)
                            relPart = "仇敌 " + intim;   // 仇人簿来源（负好感）
                        else if (!string.IsNullOrEmpty(relCn) && relCn != "None")
                            relPart = relCn + " " + intim;
                    }
                }
                catch { }
                string tag = e.Manual ? " · 好友" : "";
                return string.IsNullOrEmpty(relPart) ? head + tag : head + " · " + relPart + tag;
            }
            catch
            {
                return e.Manual ? "通讯录好友" : "";
            }
        }

        // ------------------------------------------------------------------
        // 状态栏版式（09-26 用户实机反馈）
        //
        // 问题：AB 预制件里 `BG/Status` 是"全幅拉伸 + 居中对齐"，文字因此落在**面板中央**，
        //       列表一长就被行压住（截图实证："再点一次 ✕ 确认…"压在了好友行上）。
        // 修法：运行时把状态栏钉到**面板底端**（锚点改底边拉伸、pivot=底中）。
        //       不动预制件：AB 手术要重导出 + 重打包，而这里纯粹是几何；代码版与 AB 版
        //       共用同一套落点，两版不会再漂移。
        // 底栏让位：群聊页 `BottomBar` 占"距底 4~48"，状态栏抬到其上沿（44）避免压按钮；
        //       好友/最近页没有底栏，直接贴底（20）。两个数值上机可微调。
        // ------------------------------------------------------------------
        /// <summary>状态栏贴底高度（好友/最近页，距面板底）。</summary>
        private const float StatusBottomNormal = 20f;
        /// <summary>群聊页让开底栏的高度（BottomBar 占距底 4~48）。</summary>
        private const float StatusBottomGroup = 44f;
        /// <summary>状态栏条高（单行）。</summary>
        private const float StatusHeight = 18f;

        /// <summary>把状态栏钉在面板底端（见上方版式注释）。随每次渲染调用，O(1)。</summary>
        private void LayoutStatusBar()
        {
            var rt = _refs?.StatusLabel != null ? UiRects.Of(_refs.StatusLabel.transform) : null;
            if (rt == null) return;
            rt.anchorMin = new Vector2(0f, 0f);
            rt.anchorMax = new Vector2(1f, 0f);
            rt.pivot = new Vector2(0.5f, 0f);
            rt.sizeDelta = new Vector2(-24f, StatusHeight);
            rt.anchoredPosition = new Vector2(0f, _tab == Tab.Group ? StatusBottomGroup : StatusBottomNormal);
        }

        private void SetStatus(string msg, Color color)
        {
            if (_refs?.StatusLabel != null)
            {
                _refs.StatusLabel.text = msg;
                _refs.StatusLabel.color = color;
            }
        }

        private static Button GetButton(GameObject go) => go != null ? go.GetComponent<Button>() : null;
    }
}
