/// <summary>
/// AB 预制体版通讯录面板 —— 游戏原生 UI 管理器体系（迁移），镜像 AbConfigPanel。
///
/// 迁移史：①ModMain.Init 早期 g.res.Load 双 key + Instantiate 挂自建宿主 _chatUiRoot
/// + 常驻显隐；②随对话窗统一迁到 g.ui.OpenUI + UIBase 继承（层级由游戏自动排，后开盖先开）。
///
/// 三件套（AbChatPanel/AbConfigPanel 同款）：RegisterTypeInIl2Cpp（运行期懒注册！）→
/// g.ui.OpenUI(new UIType.UITypeBase(PrefabKey,(UILayer)0)) → AddComponent（逻辑装配不变）。
///
/// 创建时机 = 进入存档世界首帧（ModMain 的 Frame 探测调 EnsureResident，初始隐藏）：
/// 与对话窗「首次打开才建」不同，ContactPresenter 的核心职责是常驻的——NPC 主动传音
/// 三分流（未读横幅/红点/当面弹出都要在窗口关闭时工作）、F10 轮询、横幅 8s 自动隐藏计时、
/// 立绘懒渲染每帧队列、NPC 面板「加好友」回调——宿主不存在则这些全断线，故进世界即建。
/// 时机安全性：能取到游戏日历 = 存档世界已加载、登录 UI 早已完成——避开「MelonLoader
/// 初始化早期注册 UIBase 派生类型毒化 il2cpp 类型系统 → 登录原生崩溃」窗口。
///
/// 显隐语义保持换壳不换魂：宿主常驻，ContactPresenter 只显隐 BG(Window)+Dim（横幅
/// UnreadBanner 独立于 BG 常显，出厂默认隐藏）；关闭 = 隐藏，不销毁宿主。
///
/// IL2CPP 约定同 AbConfigPanel：AB_UI 符号下编译；UIBase 派生类型绝不能在初始化早期注册，
/// 注册+OpenUI 全部推迟到运行期（EnsureResident 内幂等执行）。
/// UITypeBase 是 UIType 的嵌套类型（互操作程序集无顶层 UITypeBase）。
/// </summary>
#if AB_UI
using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;

namespace AgentLoopBridge
{
    /// <summary>继承游戏 UIBase 走 g.ui 体系（AbConfigPanel 同款）——注册必须运行期懒执行，
    /// 早期注册会毒化 il2cpp 类型系统（见类头注释）。</summary>
    public class AbContactPanel : UIBase
    {
        private static AbContactPanel _resident;  // 通讯录常驻宿主（进世界懒创建；销毁后 fake-null 自动重建）
        private ContactPresenter _presenter;
        private ContactPanelRefs _refs;
        private bool _assembled;                  // 防重复 AddComponent/重复接事件
        private static bool _typeRegistered;      // UIBase 派生类型只注册一次（重复注册会抛）

        // IL2CPP 硬约束（官方 Example.cs 实证）：缺此构造 → 注册 NRE 并毒化类型系统，同 AbChatPanel
        public AbContactPanel(IntPtr ptr) : base(ptr) { }

        public static AbContactPanel Resident => _resident;

        /// <summary>
        /// 传音簿此刻是否**真的显示着**（HotkeyGate 判「该不该屏蔽游戏原生快捷键」用）。
        ///
        /// 不要用 `Resident != null` 来判：`ContactPresenter.Hide` 只把 `BG`(Window)+`Dim` 置灰
        /// （文件头「关闭 = 隐藏，不销毁宿主」），宿主引用照旧非空 → 点过 ✕ 之后恒为真（陈旧真）。
        /// 真相在 presenter 自己的 `IsOpen`（`Show()` 置真 / `Hide()` 置假）。
        ///
        /// 取用顺序：**先问常驻宿主自己的 `_presenter`**，再退回 `GetAlivePresenter()`。
        /// 理由（实测的坑）：后者读的是 `ModMain.ContactPresenterInstance` 这个静态回填位，
        /// 而 `AbConfigPanel.CloseViaManager` 里曾被复制进一行 `ModMain.ContactPresenterInstance = null`
        /// —— 关一次**配置**面板就把传音簿的引用清空，于是传音簿明明开着却被判成"关着"（快捷键漏屏蔽）。
        /// 那一行已经删掉，但判据不该依赖别人的副作用：宿主在就直问宿主。
        /// </summary>
        public static bool IsShowing
        {
            get
            {
                try
                {
                    var r = _resident;
                    var p = r != null ? r._presenter : null;
                    if (p == null) p = GetAlivePresenter();
                    return p != null && p.IsOpen;
                }
                catch { return false; }   // GC 僵尸/已 Destroy：判否（宁可漏屏蔽，不可永久屏蔽）
            }
        }

        /// <summary>诊断/收尾用：立刻销毁常驻宿主（先从 UIMgr 注销再 Destroy，避免留下僵尸登记项）。</summary>
        public static void DestroyResident()
        {
            try
            {
                if (!IsAlive) return;
                var r = _resident;
                _resident = null;
                ModMain.ContactPresenterInstance = null;
                try { g.ui.CloseUI(r, true); } catch { }
                try { if (r != null) UnityEngine.Object.Destroy(r.gameObject); } catch { }
                ModMain.P("[AbContactPanel] 常驻宿主已销毁（UIMgr 已注销）");
            }
            catch (Exception e) { ModMain.P("[AbContactPanel] DestroyResident: " + e.Message); }
        }

        /// <summary>
        /// 验活（真机教训）：面板被 il2cpp GC 回收后 C# 引用变"僵尸"——连 == null 判空都会抛
        /// ObjectCollectedException（原生指针已失效），裸 fake-null 检查会让 EnsureResident 兜底永远
        /// 走不到、F10 每按一次炸一次。统一判定四态：未创建（null）/正常/已 Destroy（fake-null，
        /// 丢弃引用）/被 GC（异常，丢弃引用）；注入类型的 C# 字段（_assembled 等）读自托管包装器，
        /// 验活通过后访问不受影响。
        /// </summary>
        public static bool IsAlive
        {
            get
            {
                try
                {
                    var r = _resident;
                    if (r == null) return false;                                      // 未创建/已清理
                    if (r.gameObject == null) { _resident = null; return false; }     // Destroy 后 fake-null
                    return true;
                }
                catch (Exception) { _resident = null; return false; }                 // GC 僵尸：判空即抛，丢弃重建
            }
        }

        /// <summary>
        /// 取"活着的"通讯录 presenter（F10/地图传音按钮/加好友统一取用入口）：先验活
        /// ModMain.ContactPresenterInstance（僵尸/已毁 → 清引用返回 null），调用方对 null
        /// 走 EnsureResident 兜底重建。
        /// </summary>
        public static ContactPresenter GetAlivePresenter()
        {
            var p = ModMain.ContactPresenterInstance;
            try
            {
                if (p == null) return null;                                           // 未创建/未回填
                if (p.gameObject == null) { ModMain.ContactPresenterInstance = null; return null; }  // Destroy
                return p;
            }
            catch (Exception) { ModMain.ContactPresenterInstance = null; return null; }              // GC 僵尸
        }

        /// <summary>常驻宿主上的 ContactPresenter（ModMain 回填 ContactPresenterInstance /
        /// Opener 兜底 / 加好友回调统一经此取）；宿主未就绪时为 null。</summary>
        public static ContactPresenter Presenter => _resident != null ? _resident._presenter : null;

        /// <summary>AB 资源键（= UITypeBase 名；OpenUI 按名从 g.res 取预制体，ModAbRes 双 key 兜底）</summary>
        public const string PrefabKey = "UIContactAi";

        /// <summary>
        /// 懒创建常驻宿主（进世界首帧 Frame 探测调；宿主被游戏销毁后 fake-null 走重建）：
        /// 注册类型（运行期）→ OpenUI 挂游戏 UI 层 → 布局归一化 → 装配逻辑层（初始隐藏）。
        /// 已就绪直接 true。
        /// </summary>
        public static bool EnsureResident()
        {
            try
            {
                if (DiagSwitches.NoPanels) return false;   // 诊断开关：禁用面板
                // 验活优先（教训）：GC 僵尸引用连 == null 都会抛——IsAlive 统一判定并丢弃僵尸
                if (IsAlive && _resident._assembled) return true;
                // ①复用优先（方案 A）：管理器 CloseUI 不一定销毁实例，可能仍保有——查到就复用，
                // 绝不重复 OpenUI（重复 OpenUI 会撞登记表 → 返回 null）。
                try
                {
                    var exist = g.ui.GetUI(new UIType.UITypeBase(PrefabKey, (UILayer)0));
                    if (exist != null)
                    {
                        var comp = exist.gameObject.GetComponent<AbContactPanel>();
                        if (comp != null && comp._assembled)
                        {
                            _resident = comp;
                            ModMain.ContactPresenterInstance = AbContactPanel.Presenter;
                            ModMain.P("[AbContactPanel] 复用管理器保活的既有实例");
                            return true;
                        }
                    }
                }
                catch { }
                // 铁律：世界未就绪（MapMain 未建）/剧情窗展示中绝不调 g.ui.OpenUI——
                // 会打乱 UIMgr 对该层的布局，连累正在创建的剧情窗（世界输入失效事故根因）
                if (!AbPanelProber.CanCreateNow())
                {
                    ModMain.P("[AbContactPanel] 当前不宜创建（世界未就绪或剧情窗展示中），本次跳过");
                    return false;
                }
                if (!_typeRegistered)
                {
                    UnhollowerRuntimeLib.ClassInjector.RegisterTypeInIl2Cpp<AbContactPanel>();
                    _typeRegistered = true;
                }
                if (ChatGlobals.WsClientInstance == null)
                {
                    // ContactPresenter.Init 需要订阅 WsClient.UiEvent（未读三分流），无连接则装配不了
                    ModMain.P("[AbContactPanel] WsClient 未就绪，本轮不创建");
                    return false;
                }
                if (!ModAbRes.EnsureInjected(PrefabKey))   // 游戏读档会重建资源表，注入条目会丢
                { ModMain.P("[AbContactPanel] 预制体注入失败，本轮不创建"); return false; }
                ModMain.P("[AbContactPanel] g.ui.OpenUI(\"" + PrefabKey + "\") 创建宿主...");
                var ui = g.ui.OpenUI(new UIType.UITypeBase(PrefabKey, (UILayer)0));
                if (ui == null) { ModMain.P("[AbContactPanel] OpenUI 返回 null"); return false; }
                var go = ui.gameObject;
                go.name = "UIContactAi";
                // 事故结论（勿回退）：实例根上的 Canvas/GraphicRaycaster 是【游戏 UIMgr
                // 自己挂的】（预制体自带的那套已被 ModAbRes 在注入前剥离）——UIMgr 用它做层级
                // 排序，销毁它会打乱整个 UI 层的布局、连累同层的剧情窗。不要在此销毁实例组件。
                var panel = go.GetComponent<AbContactPanel>() ?? go.AddComponent<AbContactPanel>();
                // 层级完全交给游戏：UIMgr.CreateUI 自己给实例挂 Canvas 并按 UILayer 排序
                // （预制体自带 Canvas 已由 ModAbRes 注入前剥离），此处不再触碰 sortingOrder。
                if (!panel.AssembleResident())
                {
                    UnityEngine.Object.Destroy(go);
                    return false;
                }
                _resident = panel;
                NormalizeLayout(go);
                // 事故定案（勿回退）：①此处**不要**做 SetAsFirstSibling / SetAsLastSibling——
                // 改动 UI 层兄弟位次会挤动游戏自己的界面顺序；②**不要** SetActive(false) 隐藏——
                // 按需创建的场景就是"用户正要看"，且隐藏必须走 CloseUI 契约（见 CloseViaManager）。
                ModMain.P("[AbContactPanel] 常驻宿主经 OpenUI 创建完成（游戏 UI 层，按需创建=直接显示，不动兄弟位次）");
                return true;
            }
            catch (Exception e)
            {
                ModMain.P("[AbContactPanel] EnsureResident 异常: " + e);
                return false;
            }
        }

        /// <summary>
        /// 方案 A 契约：关闭交给游戏管理器（动画/CloseUIEnd 事件/"打开中"登记摘除全归它）。
        /// 实例可能被管理器销毁或保活——_resident 一律置空，下次打开经 GetUI 复用或 OpenUI 重建。
        /// **绝不自己 SetActive(false)/Destroy**——那会让登记项泄漏、世界输入被永久门控（事故根因）。
        /// </summary>
        public static void CloseViaManager()
        {
            try { g.ui.CloseUI(new UIType.UITypeBase(PrefabKey, (UILayer)0), false); }
            catch (Exception e) { ModMain.P("[AbContactPanel] CloseUI: " + e.Message); }
            _resident = null;
            ModMain.ContactPresenterInstance = null;
        }

        /// <summary>
        /// 布局归一化（AbConfigPanel.NormalizeLayout 同款）：UIMgr.CreateUI 会把实例
        /// RectTransform 置零（scale=0、rect=0x0，真机 dump 实证）——scale 停 0 即完全
        /// 不可见。直接设 scale=1 + 全屏 stretch（BG 面板居中定尺寸、Dim/横幅 stretch，
        /// 是根的子节点，不受影响）。
        /// </summary>
        private static void NormalizeLayout(GameObject go)
        {
            try
            {
                var rt = go.GetComponent<RectTransform>();
                if (rt == null) { ModMain.P("[AbContactPanel] 归一化跳过：根无 RectTransform"); return; }
                rt.localScale = new Vector3(1f, 1f, 1f);
                rt.anchorMin = new Vector2(0f, 0f);
                rt.anchorMax = new Vector2(1f, 1f);
                rt.offsetMin = new Vector2(0f, 0f);
                rt.offsetMax = new Vector2(0f, 0f);
                rt.anchoredPosition = new Vector2(0f, 0f);
                ModMain.P("[AbContactPanel] 布局已归一化: scale=1 全屏stretch rect="
                    + rt.rect.width + "x" + rt.rect.height);
            }
            catch (Exception e) { ModMain.P("[AbContactPanel] NormalizeLayout: " + e.Message); }
        }

        /// <summary>一次性装配逻辑层（ContactPanelRefs + ContactPresenter），幂等；装完保持隐藏。
        /// 装配失败（关键节点缺失）由 EnsureResident 销毁实例——报错就报错，不回退代码版。</summary>
        private bool AssembleResident()
        {
            try
            {
                if (_assembled) return true;
                _refs = CollectRefs(transform);
                var missing = MissingCritical(_refs);
                if (missing.Count > 0)
                {
                    ModMain.P("[AbContactPanel] 预制件缺少关键节点: " + string.Join(", ", missing.ToArray()));
                    return false;
                }
                _presenter = gameObject.GetComponent<ContactPresenter>() ?? gameObject.AddComponent<ContactPresenter>();
                _presenter.Init(_refs, ChatGlobals.WsClientInstance);
                // 初始隐藏兜底（预制件出厂即隐藏，OpenUI 实例化后显式再设一遍；
                // 横幅 UnreadBanner 独立于 BG，保持出厂隐藏态即可）
                if (_refs.Window != null) _refs.Window.gameObject.SetActive(false);
                if (_refs.Dim != null) _refs.Dim.SetActive(false);
                _assembled = true;
                return true;
            }
            catch (Exception e)
            {
                ModMain.P("[AbContactPanel] AssembleResident: " + e);
                return false;
            }
        }

        /// <summary>装配成败判定线：九项缺一即拒绝（其余可选节点 Presenter 侧 null 容错）</summary>
        private static List<string> MissingCritical(ContactPanelRefs r)
        {
            var missing = new List<string>();
            if (r.Window == null) missing.Add("BG");
            if (r.Dim == null) missing.Add("Dim");
            if (r.CloseButton == null) missing.Add("BG/CloseBtn");
            if (r.TabFriendButton == null) missing.Add("BG/Tabs/TabFriendBtn");
            if (r.TabRecentButton == null) missing.Add("BG/Tabs/TabRecentBtn");
            if (r.ContactScroll == null) missing.Add("BG/ContactScroll");
            if (r.ContactContent == null) missing.Add("BG/ContactScroll/Viewport/Content");
            if (r.ContactItemTemplate == null) missing.Add("BG/ContactItem");
            if (r.UnreadBanner == null) missing.Add("UnreadBanner");
            return missing;
        }

        /// <summary>预制体 → ContactPanelRefs（只做引用组装；外观完全由预制体负责）。
        /// 路径契约 = ContactUiBuilder 生成的节点树（与迁移前 CollectRefs 逐条一致）</summary>
        private static ContactPanelRefs CollectRefs(Transform root)
        {
            var r = new ContactPanelRefs();
            r.Root = root.gameObject;
            r.Window = FindRt(root, "BG");
            r.Dim = FindGo(root, "Dim");
            r.CloseButton = FindGo(root, "BG/CloseBtn");
            r.StatusLabel = FindComp<Text>(root, "BG/Status");

            r.TabFriendButton = FindGo(root, "BG/Tabs/TabFriendBtn");
            r.TabRecentButton = FindGo(root, "BG/Tabs/TabRecentBtn");

            r.SearchIconButton = FindGo(root, "BG/SearchIconBtn");
            r.SearchOverlay = FindGo(root, "BG/SearchOverlay");
            r.SearchInput = FindComp<InputField>(root, "BG/SearchOverlay/SearchInput");
            r.SearchCancelButton = FindGo(root, "BG/SearchOverlay/SearchCancelBtn");

            r.ContactScroll = FindComp<ScrollRect>(root, "BG/ContactScroll");
            r.ContactContent = FindRt(root, "BG/ContactScroll/Viewport/Content");
            r.ContactItemTemplate = FindGo(root, "BG/ContactItem");

            r.UnreadBanner = FindGo(root, "UnreadBanner");
            r.UnreadBannerText = FindComp<Text>(root, "UnreadBanner/BannerText");
            return r;
        }

        // ---- Find 助手（AbChatPanel/AbConfigPanel 的同名方法为 private，此处独立一份）----
        private static Transform FindTr(Transform root, string path)
        {
            return root != null ? root.Find(path) : null;
        }

        private static GameObject FindGo(Transform root, string path)
        {
            Transform t = FindTr(root, path);
            return t != null ? t.gameObject : null;
        }

        private static RectTransform FindRt(Transform root, string path)
        {
            var go = FindGo(root, path);
            return go != null ? go.GetComponent<RectTransform>() : null;
        }

        private static T FindComp<T>(Transform root, string path) where T : Component
        {
            var go = FindGo(root, path);
            return go != null ? go.GetComponent<T>() : null;
        }

        private void OnDestroy()
        {
            if (_resident == this) _resident = null;
        }
    }
}
#endif // AB_UI
