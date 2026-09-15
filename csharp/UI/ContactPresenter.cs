/// <summary>
/// 传音簿（通讯录）控制器（MonoBehaviour，仿 ConfigPresenter）。
///
/// 数据两层（好友语义 = 玩家认识的 NPC，非全图筛选）：
///  ① 玩家关系记录全集：RelationNetwork（十容器+好友簿+仇人簿+GetAllGoodRelation 含敌）——本地主线程采集，开面板即得；
///  ② 手动好友：list_contacts RPC（Python 侧 contacts.json）——异步回填后合并重渲。
/// 合并按中文名去重（关系层优先，手动层补缺）；副标题 = 宗门·境界 · 关系/仇敌 好感。
///
/// Tab：「好友」= 合并全量按名字拼音序（zh-CN 排序，异常回退码位序）；
///      「最近」= 通讯录成员 ∩ 有互动记录，按最近互动倒序（互动 = 会话文件 mtime ∪ 本地 npc_reply 时间戳）。
/// 搜索：本地子串过滤（名字/副标题），非空时跨两 Tab 搜全量——零 RPC。
/// 行点击 / ✓：解析 WorldUnitBase → ChatLauncher.OpenForUnit（先隐藏本面板，防 3050 Canvas 盖住对话窗）。
/// 未读：NPC 主动传音（npc_reply initiative=true）分流——对话窗开着且是该 NPC=已读直播；
///       同格+空闲+没开别的对话窗=当面弹出对话 UI；其余=UnreadStore 登记（行红点 UnreadDot +
///       顶部横幅 UnreadBanner，点横幅打开最新未读对话）。见 README.md 附录二 G（功能设计底稿）。
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

        /// <summary>通讯录条目（合并层①②后的一份）。</summary>
        private class ContactEntry
        {
            public string Name;
            public WorldUnitBase Unit;    // 可空：手动好友解析失败 / 已不在世间
            public bool Manual;           // 是否手动添加（与关系层并存时置 true，展示仍以关系层为准）
            public bool ManualOnly;       // 仅由手动层引入（关系层没有）——「移除好友」后需整条出列
            public long LastInteract;     // 最近互动（unix 秒；会话 mtime ∪ 本地时间戳取大）
        }

        private ContactPanelRefs _refs;
        private WsClient _ws;
        private bool _isOpen;
        private bool _showRecent;                       // false=好友(字母序) true=最近(时间倒序)
        private string _search = "";
        private UnityEngine.Events.UnityAction<string> _onSearchChangedAction;

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
            ClickUtils.Attach(GetButton(_refs?.TabFriendButton), () => SwitchTab(false));
            ClickUtils.Attach(GetButton(_refs?.TabRecentButton), () => SwitchTab(true));

            // IL2CPP：UnityEvent 的 lambda 转换不可靠，先转存 Action<string> 再 AddListener（ConfigPresenter 同款）
            System.Action<string> act = OnSearchChanged;
            _onSearchChangedAction = act;
            if (_refs?.SearchInput != null)
                _refs.SearchInput.onValueChanged.AddListener(_onSearchChangedAction);

            // 常驻职责已移交 ContactDuty（静态层，与面板实例解耦）；本组件只是视图：
            ContactDuty.Changed += OnStoreChanged;
            ContactStore.Changed += OnStoreChanged;
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
            RecomputeRecency();
            RenderCurrent();
        }

        private void Update()
        {
            // 迁移：F10 轮询已移到常驻的 AbHotkeys（ModMain 的 g.timer.Frame 回调）——
            // 本组件所在根节点在关闭时是 OFF 的（防屏蔽世界输入），Update 不再运行。

            // 立绘缓存：读盘限流泵 + 待贴头像轮询（命中即贴 overrideSprite，行销毁自动出列）
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
        /// 恒不触碰渲染 API——条目未解析到单位（未进场/已故）就没有缓存键，直接保持占位。</summary>
        private void ApplyAvatar(GameObject avatarGo, ContactEntry e)
        {
            if (avatarGo == null || e?.Unit == null) return;
            string id = PortraitCache.IdOf(e.Unit);
            if (string.IsNullOrEmpty(id)) return;
            var img = avatarGo.GetComponent<Image>();
            if (img == null) return;
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

            // ① 关系层：本地采集（同步，快）
            BuildRelations();
            // ② 手动层 + 会话索引：RPC 由常驻层 ContactDuty 发起（结果写 ContactStore，事件回推重渲）
            ContactDuty.RequestManualContacts();
            ContactDuty.RequestSessions();
            // ③ 先把 Store 里**已有**的镜像并进来（RPC 未回前也能显示上次拉到的名单/时间戳；回包后再经事件重算）
            MergeManualIntoContacts();
            RecomputeRecency();
            RenderCurrent();
        }

        public void Hide()
        {
            _isOpen = false;
            // 重置搜索：下次打开从干净状态开始（SearchOverlay 随之关闭；此刻 _isOpen 已 false，RenderCurrent 空转）
            CloseSearch();
            // 方案 A 契约：关闭交给游戏管理器（动画/CloseUIEnd 事件/登记摘除全归它）。
            // 绝不自己 SetActive(false)/Destroy——那正是本轮"世界输入被门控"的根因。
            AbContactPanel.CloseViaManager();
        }

        private void SwitchTab(bool recent)
        {
            _showRecent = recent;
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
                _contacts.Add(new ContactEntry { Name = name, Unit = u, Manual = false });
            }
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
                var unit = UnitLookup.Resolve(name);   // 主线程；直查失败全图按名（手动好友量小，可承受）
                _contacts.Add(new ContactEntry { Name = name, Unit = unit, Manual = true, ManualOnly = true });
            }
            SortContacts();
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
            _contacts.Sort((a, b) => cmp.Compare(a.Name, b.Name));
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
            UpdateTabVisuals();

            List<ContactEntry> rows;
            if (_search.Length > 0)
            {
                rows = _contacts.FindAll(MatchSearch);
            }
            else if (_showRecent)
            {
                var recent = _contacts.FindAll(c => c.LastInteract > 0);
                recent.Sort((a, b) => b.LastInteract.CompareTo(a.LastInteract));
                if (recent.Count > RECENT_MAX) recent.RemoveRange(RECENT_MAX, recent.Count - RECENT_MAX);
                rows = recent;
            }
            else
            {
                rows = _contacts;
            }

            RebuildRows(rows);

            // 状态条
            if (_search.Length > 0)
                SetStatus("搜索「" + _search + "」：命中 " + rows.Count + " 人", StatusDim);
            else if (_showRecent)
                SetStatus(rows.Count == 0 ? "暂无最近互动（聊过天的好友会出现在这里）" : "按最近互动排序", StatusDim);
            else if (!ContactStore.ManualLoaded && _ws != null)
                SetStatus("好友按字母排序（通讯录名单加载中…）", StatusDim);
            else if (rows.Count > MAX_ROWS)
                SetStatus("共 " + rows.Count + " 位好友，显示前 " + MAX_ROWS + " 位（搜索可精确查找）", StatusDim);
            else
                SetStatus("好友按字母排序 · 点行或 ✓ 传音", StatusDim);
        }

        private bool MatchSearch(ContactEntry c)
        {
            if (c.Name.IndexOf(_search, StringComparison.Ordinal) >= 0) return true;
            string sub = SubOf(c);
            return sub != null && sub.IndexOf(_search, StringComparison.Ordinal) >= 0;
        }

        private void UpdateTabVisuals()
        {
            SetTabSelected(_refs?.TabFriendButton, !_showRecent);
            SetTabSelected(_refs?.TabRecentButton, _showRecent);
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
            var content = _refs?.ContactContent;
            var template = _refs?.ContactItemTemplate;
            if (content == null || template == null) return;

            // 清旧行（IL2CPP：索引循环，禁 foreach(Transform)）
            for (int i = content.childCount - 1; i >= 0; i--)
                UnityEngine.Object.Destroy(content.GetChild(i).gameObject);

            if (rows == null) return;
            int shown = Mathf.Min(rows.Count, MAX_ROWS);
            for (int i = 0; i < shown; i++)
            {
                var e = rows[i];
                var clone = UnityEngine.Object.Instantiate(template);
                clone.transform.SetParent(content, false);
                clone.SetActive(true);

                var nameTxt = clone.transform.Find("Name")?.GetComponent<Text>();
                if (nameTxt != null) nameTxt.text = e.Name;
                var subTxt = clone.transform.Find("Sub")?.GetComponent<Text>();
                if (subTxt != null) subTxt.text = SubOf(e) ?? "";

                // 未读红点：该 NPC 有未读传音时点亮
                var dot = clone.transform.Find("UnreadDot")?.gameObject;
                if (dot != null) dot.SetActive(UnreadStore.Has(e.Name));

                // 头像：PortraitCache 像素快照命中→贴立绘（圆形烘焙），未命中→占位圆+排队读盘
                var avatarGo = clone.transform.Find("Avatar")?.gameObject;
                if (avatarGo != null) ApplyAvatar(avatarGo, e);

                // 行点击 / ✓ 同一动作：传音（打开对话 UI）
                var captured = e;
                ClickUtils.Attach(clone.GetComponent<Button>(), () => OpenChatFor(captured));
                var chatBtn = clone.transform.Find("ChatBtn")?.GetComponent<Button>();
                if (chatBtn != null) ClickUtils.Attach(chatBtn, () => OpenChatFor(captured));
            }
        }

        // ------------------------------------------------------------------
        // 行点击 → 对话 UI
        // ------------------------------------------------------------------

        private void OpenChatFor(ContactEntry e)
        {
            if (e == null) return;
            // 手动好友延迟解析：点开时再找（开面板时解析失败的可能此刻已就位）
            if (e.Unit == null) e.Unit = UnitLookup.Resolve(e.Name);
            if (e.Unit == null)
            {
                SetStatus(e.Name + " 已不在世间（找不到对应人物）", StatusWarn);
                return;
            }
            UnreadStore.Clear(e.Name);   // 打开即已读（未读指示由 HUD 按钮红点承载）
            Hide();   // 通讯录 Canvas 3050 高于对话窗：先藏自己再开对话
            ChatLauncher.OpenForUnit(e.Unit);
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

        /// <summary>副标题：宗门·境界 · 关系 好感（关系层实时读游戏，手动无 Unit 时兜底文案）。</summary>
        private static string SubOf(ContactEntry e)
        {
            if (e == null) return "";
            if (e.Unit == null) return e.Manual ? "通讯录好友 · 人物档案缺失" : "";
            try
            {
                var ud = e.Unit.data.unitData;
                var pd = ud.propertyData;
                string sect = UnitSnapshot.SectName(e.Unit);
                string realm = "";
                try { realm = g.conf.roleGrade.GetGradeName(pd.gradeID); } catch { }
                string head = sect + "·" + realm;
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
