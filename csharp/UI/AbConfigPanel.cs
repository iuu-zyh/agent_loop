/// <summary>
/// AB 预制体版配置面板 —— 游戏原生 UI 管理器体系（迁移），镜像 AbChatPanel。
///
/// 迁移史：①ModMain.Init 早期 Instantiate 到自建宿主 _chatUiRoot + 独立 Canvas（3100 压对话）
/// + 常驻显隐；②随对话窗统一迁到 g.ui.OpenUI + UIBase 继承（层级由游戏自动排，后开盖先开）。
///
/// 三件套（AbChatPanel 同款）：RegisterTypeInIl2Cpp（运行期懒注册！）→
/// g.ui.OpenUI(new UIType.UITypeBase(PrefabKey,(UILayer)0)) → AddComponent（逻辑装配不变）。
///
/// 创建时机 = 进入存档世界首帧（ModMain 的 Frame 探测调 EnsureResident，初始隐藏）：
/// 配置面板的 F11 轮询在 ConfigPresenter（挂本宿主）里，宿主不存在则 F11 无人响应——
/// 被动懒创建（首次按键）会先失去 F11，故进世界即建。时机安全性：能取到游戏日历 = 存档
/// 世界已加载、登录 UI 早已完成——避开「MelonLoader 初始化早期注册 UIBase 派生类型毒化
/// il2cpp 类型系统 → 登录原生崩溃」窗口（AbChatPanel 迁移实证教训）。
///
/// 显隐语义保持换壳不换魂：宿主常驻，ConfigPresenter 只显隐 BG(Window)（Tooltip 随之）；
/// 关闭 = HidePanel，不销毁宿主。SetAsLastSibling 保留为常驻体间排序兜底（对话⚙开配置场景）。
///
/// IL2CPP 约定同 AbChatPanel：AB_UI 符号下编译；UIBase 派生类型绝不能在初始化早期注册，
/// 注册+OpenUI 全部推迟到运行期 Frame 回调（EnsureResident 内幂等执行）。
/// UITypeBase 是 UIType 的嵌套类型（互操作程序集无顶层 UITypeBase）。
/// </summary>
#if AB_UI
using System;
using UnityEngine;
using UnityEngine.UI;

namespace AgentLoopBridge
{
    /// <summary>继承游戏 UIBase 走 g.ui 体系（AbChatPanel 同款）——注册必须运行期懒执行，
    /// 早期注册会毒化 il2cpp 类型系统（见类头注释）。</summary>
    public class AbConfigPanel : UIBase
    {
        private static AbConfigPanel _resident;  // 配置常驻宿主（进世界懒创建；销毁后 fake-null 自动重建）
        private static AbConfigPanel _current;   // 当前打开的实例（单例语义）
        private ConfigPresenter _presenter;
        private ConfigPanelRefs _refs;
        private bool _assembled;                 // 防重复 AddComponent/重复接事件
        private static bool _typeRegistered;     // UIBase 派生类型只注册一次（重复注册会抛）

        // IL2CPP 硬约束（官方 Example.cs 实证）：缺此构造 → 注册 NRE 并毒化类型系统，同 AbChatPanel
        public AbConfigPanel(IntPtr ptr) : base(ptr) { }

        public static AbConfigPanel Resident => _resident;

        /// <summary>
        /// 配置面板此刻是否**真的显示着**（HotkeyGate 判「该不该屏蔽游戏原生快捷键」用）。
        ///
        /// 判据 = `_current != null`（本面板开过且没关）AND `presenter.IsPanelOpen`
        /// （= `BG.activeInHierarchy`，即屏幕上真的看得见）。两者缺一不可：
        ///   · 只看 `_current`：它曾在点过 ✕ 之后陈旧为真（CloseViaManager 漏清），
        ///     已在 CloseViaManager 里补齐，但保留 presenter 这一问做双保险——
        ///     "宿主引用非空"从来不是"看得见"，这是本类唯一想表达的事；
        ///   · 只看 presenter：常驻宿主建好但一次都没开过时，presenter 存在而窗口是灰的，
        ///     `_current` 为空正好把这段空窗排除掉。
        /// 注意 `IsPanelOpen` 内部必须用 activeInHierarchy（见 ConfigPresenter 处注释）：
        /// 用 activeSelf 的话，管理器关面板只灰根节点、`BG.activeSelf` 仍为真 → 这里会恒判"开着"
        /// → **游戏快捷键被永久屏蔽**，比不屏蔽严重得多。
        /// </summary>
        public static bool IsShowing
        {
            get
            {
                var r = _current;
                if (r == null) return false;
                try { return r._presenter != null && r._presenter.IsPanelOpen; }
                catch { return false; }   // GC 僵尸/已 Destroy：判否（宁可漏屏蔽，不可永久屏蔽）
            }
        }

        /// <summary>诊断/收尾用：立刻销毁常驻宿主（先从 UIMgr 注销再 Destroy）。</summary>
        public static void DestroyResident()
        {
            try
            {
                if (!IsAlive) return;
                var r = _resident;
                _resident = null;
                if (_current == r) _current = null;
                try { g.ui.CloseUI(r, true); } catch { }
                try { if (r != null) UnityEngine.Object.Destroy(r.gameObject); } catch { }
                ModMain.P("[AbConfigPanel] 常驻宿主已销毁（UIMgr 已注销）");
            }
            catch (Exception e) { ModMain.P("[AbConfigPanel] DestroyResident: " + e.Message); }
        }

        /// <summary>
        /// 验活（真机教训，AbContactPanel 同款）：面板被 il2cpp GC 回收后 C# 引用变"僵尸"——
        /// 连 == null 判空都会抛 ObjectCollectedException（原生指针已失效），裸 fake-null 检查会让
        /// EnsureResident 兜底永远走不到。统一判定四态：未创建（null）/正常/已 Destroy（fake-null，
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

        /// <summary>AB 资源键（= UITypeBase 名；OpenUI 按名从 g.res 取预制体，ModAbRes 双 key 兜底）</summary>
        public const string PrefabKey = "UIConfigAi";

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
                // ①复用优先（方案 A）：管理器 CloseUI 不一定销毁实例——查到就复用，绝不重复 OpenUI
                try
                {
                    var exist = g.ui.GetUI(new UIType.UITypeBase(PrefabKey, (UILayer)0));
                    if (exist != null)
                    {
                        var comp = exist.gameObject.GetComponent<AbConfigPanel>();
                        if (comp != null && comp._assembled)
                        {
                            _resident = comp;
                            ModMain.P("[AbConfigPanel] 复用管理器保活的既有实例");
                            return true;
                        }
                    }
                }
                catch { }
                // 铁律：世界未就绪（MapMain 未建）/剧情窗展示中绝不调 g.ui.OpenUI
                if (!AbPanelProber.CanCreateNow())
                {
                    ModMain.P("[AbConfigPanel] 当前不宜创建（世界未就绪或剧情窗展示中），本次跳过");
                    return false;
                }
                if (!_typeRegistered)
                {
                    UnhollowerRuntimeLib.ClassInjector.RegisterTypeInIl2Cpp<AbConfigPanel>();
                    _typeRegistered = true;
                }
                if (!ModAbRes.EnsureInjected(PrefabKey))
                { ModMain.P("[AbConfigPanel] 预制体注入失败，本轮不创建"); return false; }
                ModMain.P("[AbConfigPanel] g.ui.OpenUI(\"" + PrefabKey + "\") 创建宿主...");
                var ui = g.ui.OpenUI(new UIType.UITypeBase(PrefabKey, (UILayer)0));
                if (ui == null) { ModMain.P("[AbConfigPanel] OpenUI 返回 null"); return false; }
                var go = ui.gameObject;
                go.name = "UIConfigAi";
                // 事故结论（勿回退）：实例根上的 Canvas/GraphicRaycaster 是【游戏 UIMgr
                // 自己挂的】——销毁它会打乱 UI 层布局、连累同层的剧情窗。不要在此销毁实例组件。
                var panel = go.GetComponent<AbConfigPanel>() ?? go.AddComponent<AbConfigPanel>();
                // 层级完全交给游戏：UIMgr.CreateUI 自己给实例挂 Canvas 并按 UILayer 排序
                // （预制体自带 Canvas 已由 ModAbRes 注入前剥离），此处不再触碰 sortingOrder。
                if (!panel.AssembleResident())
                {
                    UnityEngine.Object.Destroy(go);
                    return false;
                }
                _resident = panel;
                NormalizeLayout(go);
                // 事故定案（勿回退）：不动兄弟位次、不 SetActive(false)（按需创建=用户要看，
                // 隐藏必须走 CloseUI 契约——见 AbConfigPanel.CloseViaManager / AbContactPanel 注释）。
                ModMain.P("[AbConfigPanel] 常驻宿主经 OpenUI 创建完成（游戏 UI 层，按需创建=直接显示，不动兄弟位次）");
                return true;
            }
            catch (Exception e)
            {
                ModMain.P("[AbConfigPanel] EnsureResident 异常: " + e);
                return false;
            }
        }

        /// <summary>
        /// 方案 A 契约：关闭交给游戏管理器（动画/CloseUIEnd/登记摘除全归它）。绝不自己
        /// SetActive(false)/Destroy——登记项泄漏会让世界输入被永久门控（事故根因）。
        /// </summary>
        public static void CloseViaManager()
        {
            try { g.ui.CloseUI(new UIType.UITypeBase(PrefabKey, (UILayer)0), false); }
            catch (Exception e) { ModMain.P("[AbConfigPanel] CloseUI: " + e.Message); }
            _resident = null;
            // 修（两处都是复制 AbContactPanel.CloseViaManager 带过来的）
            // ① 这一行原来写的是 `ModMain.ContactPresenterInstance = null;` —— 那是**传音簿**的语句。
            //    后果：关一次配置面板就把传音簿的 presenter 静态引用清空（下次点「传」要多绕一次
            //    EnsureResident 才自愈；若此刻传音簿正开着，`AbContactPanel.IsShowing` 会把它
            //    误判成"关着" → 面板明明开着却不屏蔽游戏快捷键）。配置面板没有资格动别人的引用。
            // ② 漏了本面板自己的 `_current`。后果：`IsOpen` 在点过 ✕ 之后**恒为真** →
            //    ChatPresenter 的 Esc 链会先去"关"一个已经关掉的配置面板（要按两次 Esc 才关得掉
            //    对话窗）；`IsShowing` 也因此必须绕开 `_current` 去问 presenter（见上）。
            //    这里补齐，让 `_current` 与真实开关状态一致。
            _current = null;
        }

        /// <summary>
        /// 布局归一化（AbChatPanel.NormalizeLayout 同款）：UIMgr.CreateUI 会把实例 RectTransform
        /// 置零（scale=0、rect=0x0，真机 dump 实证）——scale 停 0 即完全不可见。直接设 scale=1 +
        /// 全屏 stretch（BG 面板居中定尺寸，是根的子节点，不受影响）。
        /// </summary>
        private static void NormalizeLayout(GameObject go)
        {
            try
            {
                var rt = go.GetComponent<RectTransform>();
                if (rt == null) { ModMain.P("[AbConfigPanel] 归一化跳过：根无 RectTransform"); return; }
                rt.localScale = new Vector3(1f, 1f, 1f);
                rt.anchorMin = new Vector2(0f, 0f);
                rt.anchorMax = new Vector2(1f, 1f);
                rt.offsetMin = new Vector2(0f, 0f);
                rt.offsetMax = new Vector2(0f, 0f);
                rt.anchoredPosition = new Vector2(0f, 0f);
                ModMain.P("[AbConfigPanel] 布局已归一化: scale=1 全屏stretch rect="
                    + rt.rect.width + "x" + rt.rect.height);
            }
            catch (Exception e) { ModMain.P("[AbConfigPanel] NormalizeLayout: " + e.Message); }
        }

        /// <summary>一次性装配逻辑层（ConfigPanelRefs + ConfigPresenter），幂等；装完保持隐藏</summary>
        private bool AssembleResident()
        {
            try
            {
                if (_assembled) return true;
                _refs = CollectRefs(transform);
                var missing = MissingCritical(_refs);
                if (missing.Count > 0)
                {
                    ModMain.P("[AbConfigPanel] 预制件缺少关键节点: " + string.Join(", ", missing.ToArray()));
                    return false;
                }
                // 分组改版诊断：行节点全部下沉到 Scroll/Viewport/Content 之后，**任何一条
                // 路径写错都只会静默变成 null**（输入框空着、开关不动）。这一行把新增节点一次列全，
                // 与 ChatWindow 的"可选节点清单"同款手法：一看就知道是预制件没打好还是代码接错。
                ModMain.P("[AbConfigPanel] 新增节点清单：LlmScroll=" + (_refs.LlmScroll != null) +
                          " InitiativeEnabled=" + (_refs.InitiativeEnabledToggle != null) +
                          " CompactionEnabled=" + (_refs.CompactionEnabledToggle != null) +
                          " RetainRatio=" + (_refs.RetainRatioInput != null) +
                          " ThresholdRatio=" + (_refs.ThresholdRatioInput != null) +
                          " Portrait=" + (_refs.PortraitToggle != null) +
                          " LlmTimeouts=" + (_refs.LlmTimeoutInput != null && _refs.LlmTimeoutNsInput != null
                                             && _refs.LlmBudgetInput != null && _refs.LlmRetriesInput != null) +
                          " SaveLlmBtn=" + (_refs.SaveLlmButton != null));
                EnsureScrollInputTarget(_refs);
                _presenter = gameObject.GetComponent<ConfigPresenter>() ?? gameObject.AddComponent<ConfigPresenter>();
                _presenter.Init(_refs, ChatGlobals.WsClientInstance);
                if (_refs.Window != null) _refs.Window.gameObject.SetActive(false);  // 初始隐藏
                _assembled = true;
                return true;
            }
            catch (Exception e)
            {
                ModMain.P("[AbConfigPanel] AssembleResident: " + e);
                return false;
            }
        }

        /// <summary>
        /// 实机修复：给整页滚动区补一张「命中层」（滚轮/拖动进不来的元凶）。
        ///
        /// 症状：面板能开、`新增节点清单` 全 True、几何也对（Content 1014.7px / 视口 504px，
        /// 有 510px 可滚），但**滚轮与拖动都没反应**，下半页配置永远看不到。
        /// 原因：UGUI 的滚轮/拖动（IScrollHandler/IDragHandler）必须先**射线命中一个 Graphic**，
        /// 再沿父级冒泡到 ScrollRect。我们的 `Scroll` 是纯容器（既无 Image、也无 CanvasRenderer），
        /// 行与行之间、两侧留白的空白处没有任何可命中物 → 事件根本进不到 ScrollRect。
        /// 对照组：同一预制件里**能滚**的 `PagePrompt/FileScroll`，正是在 ScrollRect 节点上挂了
        /// 一张 `Image(raycastTarget=1, a=0.267)`——当初抄结构时只抄了 Scroll/Viewport/Content 三层，
        /// 漏了这张图。
        ///
        /// 补一张**全透明** Image：a=0 不影响观感，raycastTarget=1 只吃事件。子节点（输入框/开关/
        /// 标题）仍压在它上面，照常先被命中、点击不受影响；空白处的滚轮/拖动冒泡到 ScrollRect。
        /// 预制件侧同步补了这张图（`scripts/dev/prefab_patch_scroll_hit.py`）——本方法是"手上这份
        /// 老 AB 也能救"的兜底，新包则两边都有。
        /// </summary>
        private static void EnsureScrollInputTarget(ConfigPanelRefs r)
        {
            try
            {
                if (r.LlmScroll == null) return;
                var go = r.LlmScroll.gameObject;
                var old = go.GetComponent<Image>();
                if (old != null)
                {
                    if (!old.raycastTarget) { old.raycastTarget = true; ModMain.P("[AbConfigPanel] 滚动命中层：Image 存在但 raycastTarget=0 → 已打开"); }
                    else ModMain.P("[AbConfigPanel] 滚动命中层：预制件已带 Image(raycastTarget=1)，无需补");
                    return;
                }
                var img = go.AddComponent<Image>();
                img.color = new Color(0f, 0f, 0f, 0f);   // 全透明：只做射线命中，不改变观感
                img.raycastTarget = true;
                ModMain.P("[AbConfigPanel] 滚动命中层：Scroll 补 Image(raycastTarget=1, a=0) → 滚轮/拖动可用");
            }
            catch (Exception e)
            {
                ModMain.P("[AbConfigPanel] EnsureScrollInputTarget: " + e.Message);
            }
        }

        /// <summary>装配成败判定线：三页容器缺一即拒绝（其余行级节点 Presenter 侧 null 容错）</summary>
        private static System.Collections.Generic.List<string> MissingCritical(ConfigPanelRefs r)
        {
            var missing = new System.Collections.Generic.List<string>();
            if (r.Window == null) missing.Add("BG");
            if (r.PageLlm == null) missing.Add("BG/PageLlm");
            if (r.PagePrompt == null) missing.Add("BG/PagePrompt");
            return missing;
        }

        /// <summary>AB 面板是否处于打开状态</summary>
        public static bool IsOpen => _current != null;

        /// <summary>
        /// F11 / ⚙ 统一开关：懒创建兜底（进世界探测已建，此处兜底重建场景）→ 常驻体显隐
        /// （Hide 即关，不销毁；重开 ShowPanel 刷新）。npcId空=全量，非空=仅该Npc（对话内⚙）。
        /// </summary>
        public static bool HandleToggle(string npcId = null)
        {
            string n = (npcId ?? "").Trim();
            try
            {
                if (!EnsureResident())
                {
                    ModMain.P("[AbConfigPanel] 常驻宿主不可用（OpenUI 失败），本次不打开");
                    return false;
                }
                var r = _resident;
                if (r == null || !r._assembled || r._presenter == null)
                {
                    ModMain.P("[AbConfigPanel] 常驻宿主未就绪");
                    return false;
                }
                // 打开与否**只问 presenter**：`IsPanelOpen` = `BG.activeInHierarchy`。
                // 不要退回 `BG.activeSelf` —— 管理器关闭面板时会连同根节点一起置灰，
                // 而 activeSelf 是"本地开关"，根灰了它照样是 true → 会把"已关"误判成"已开"
                // → 走到下面的关分支 → 配置面板**再也打不开**（一并修）。
                bool open = r._presenter.IsPanelOpen;
                if (_current != null && _current != r) _current = null;
                if (open)
                {
                    // 已开：带 Npc 切该 Npc 并刷新（对话内⚙），空 Npc 则关（F11）
                    if (n.Length > 0) { r._presenter.ShowPanel(n); r.transform.SetAsLastSibling(); }
                    else { r._presenter.HidePanel(); _current = null; return true; }
                }
                else
                {
                    r._presenter.ShowPanel(n);
                    // 提到兄弟最上层：否则被后 SetAsLastSibling 的对话窗压住（对话⚙开配置场景）
                    r.transform.SetAsLastSibling();
                }
                _current = r;
                return true;
            }
            catch (Exception e)
            {
                ModMain.P("[AbConfigPanel] toggle failed: " + e.Message);
                return false;
            }
        }

        /// <summary>直开入口（未知调用方兼容）：EnsureResident + ShowPanel，语义同 HandleToggle 的开分支</summary>
        public static bool TryOpen(string npcId = null)
        {
            return HandleToggle(npcId);
        }

        /// <summary>常驻 tick（由 AbHotkeys 每帧调用）：Python 重启状态机必须继续推进——
        /// 宿主根在关闭态是 OFF 的，ConfigPresenter.Update 不再运行，故在此承接。</summary>
        public static void Tick()
        {
            try
            {
                if (!IsAlive) return;
                var r = _resident;
                if (r == null || r._presenter == null) return;
                r._presenter.TickRestart();
            }
            catch { }
        }

        /// <summary>只关不切（Esc 等外部入口用）：隐藏常驻体并清 _current；未开则无操作。</summary>
        public static void ClosePanel()
        {
            try
            {
                var r = _current;
                if (r == null || r._presenter == null) return;
                r._presenter.HidePanel();
                _current = null;
            }
            catch (Exception e)
            {
                ModMain.P("[AbConfigPanel] close failed: " + e.Message);
                _current = null;
            }
        }

        /// <summary>预制体 → ConfigPanelRefs（只做引用组装；外观完全由预制体负责）。
        /// 路径契约 = ConfigUiBuilder 生成的节点树（BG/ 前缀，表单行 ASCII 名）</summary>
        /// <summary>装配（命名 InitPanel，避免与组件常见 Init 语义混淆；现经 AssembleResident 统一，保留兼容）</summary>
        private void InitPanel(string npcId = null)
        {
            try
            {
                if (!AssembleResident()) return;
                if (_resident == null) _resident = this;
                _presenter.ShowPanel(npcId);
                _current = this;
            }
            catch (Exception e)
            {
                ModMain.P("[AbConfigPanel] Init: " + e);
            }
        }

        private static ConfigPanelRefs CollectRefs(Transform root)
        {
            var r = new ConfigPanelRefs();
            r.Root = root.gameObject;
            r.Window = FindRt(root, "BG");
            r.CloseButton = FindGo(root, "BG/Title/CloseBtn");
            r.StatusLabel = FindComp<Text>(root, "BG/Status");

            r.TabLlmButton = FindGo(root, "BG/Tabs/TabLlmBtn");
            r.TabPromptButton = FindGo(root, "BG/Tabs/TabPromptBtn");
            r.PageLlm = FindGo(root, "BG/PageLlm");
            r.PagePrompt = FindGo(root, "BG/PagePrompt");

            // 分组改版：大模型页整页套了 ScrollRect（行数从 13 涨到 22，760×640 放不下），
            // 所以所有行节点都下沉到 `PageLlm/Scroll/Viewport/Content/` 下 —— 路径常量收在此处，
            // 与预制件脚本 `scripts/dev/prefab_patch_config_groups.py` 的命名严格对齐（改名两边一起改）。
            const string Llm = "BG/PageLlm/Scroll/Viewport/Content/";
            r.LlmScroll = FindComp<ScrollRect>(root, "BG/PageLlm/Scroll");

            r.BaseUrlInput = FindComp<InputField>(root, Llm + "BaseUrlRow/Input");
            r.ApiKeyInput = FindComp<InputField>(root, Llm + "ApiKeyRow/Input");
            r.ModelInput = FindComp<InputField>(root, Llm + "ModelRow/Input");
            // 新增两节点（都可空）：headers 行与「测试连接」按钮。
            // 故意**不进** MissingCritical —— 老 AB 没有它们时应该只是看不到这两个功能，
            // 而不是整个配置面板打不开。
            r.HeadersInput = FindComp<InputField>(root, Llm + "HeadersRow/Input");
            r.TestLlmButton = FindGo(root, Llm + "TestLlmBtn");
            r.ImageToggle = FindComp<Toggle>(root, Llm + "ImageRow/ToggleBg");
            r.PortInput = FindComp<InputField>(root, Llm + "PortRow/Input");
            r.TimeoutInput = FindComp<InputField>(root, Llm + "TimeoutRow/Input");
            r.MinIntervalInput = FindComp<InputField>(root, Llm + "MinIntervalRow/Input");
            r.DailyChanceInput = FindComp<InputField>(root, Llm + "DailyChanceRow/Input");
            r.NpcCooldownDaysInput = FindComp<InputField>(root, Llm + "NpcCooldownDaysRow/Input");
            r.NpcCooldownRealInput = FindComp<InputField>(root, Llm + "NpcCooldownRealRow/Input");
            r.LowThreshInput = FindComp<InputField>(root, Llm + "LowThreshRow/Input");
            r.LowHalveToggle = FindComp<Toggle>(root, Llm + "LowHalveRow/ToggleBg");
            r.CtxWindowInput = FindComp<InputField>(root, Llm + "CtxWindowRow/Input");
            r.InitiativeEnabledToggle = FindComp<Toggle>(root, Llm + "InitiativeEnabledRow/ToggleBg");
            r.CompactionEnabledToggle = FindComp<Toggle>(root, Llm + "CompactionEnabledRow/ToggleBg");
            r.RetainRatioInput = FindComp<InputField>(root, Llm + "RetainRatioRow/Input");
            r.ThresholdRatioInput = FindComp<InputField>(root, Llm + "ThresholdRatioRow/Input");
            r.PortraitToggle = FindComp<Toggle>(root, Llm + "PortraitRow/ToggleBg");
            r.SaveLlmButton = FindGo(root, Llm + "SaveLlmBtn");

            // 新增四行（AI 时间与重试）。同样**不进** MissingCritical ——
            // 老 AB 没有这四行时只是看不到这四项（config.json 仍可手改），不该整个面板打不开。
            // 正路 = 跑 `scripts/dev/prefab_patch_config_groups.py`（已含这四行）补进预制件 → 重打 AB。
            // **刻意不做运行期补行兜底**（用户 拍板：走 AB 资产固化）。
            r.LlmTimeoutInput = FindComp<InputField>(root, Llm + "LlmTimeoutRow/Input");
            r.LlmTimeoutNsInput = FindComp<InputField>(root, Llm + "LlmTimeoutNsRow/Input");
            r.LlmBudgetInput = FindComp<InputField>(root, Llm + "LlmBudgetRow/Input");
            r.LlmRetriesInput = FindComp<InputField>(root, Llm + "LlmRetriesRow/Input");

            // 悬停气泡模板（可选节点：预制件经补节点菜单添加；缺失时 ConfigPresenter 运行时现造）
            r.TooltipGo = FindGo(root, "BG/Tooltip");
            r.TooltipText = FindComp<Text>(root, "BG/Tooltip/TooltipText");

            r.FileScroll = FindComp<ScrollRect>(root, "BG/PagePrompt/FileScroll");
            r.FileListContent = FindRt(root, "BG/PagePrompt/FileScroll/Viewport/Content");
            r.FileItemTemplate = FindGo(root, "BG/FileItem");
            r.PromptInput = FindComp<InputField>(root, "BG/PagePrompt/PromptInput");
            r.PromptScroll = FindComp<ScrollRect>(root, "BG/PagePrompt/PromptInput");  // 预制件外挂滚动（超长内容滚轮），代码版构建 null 容错
            r.CurrentFileLabel = FindComp<Text>(root, "BG/PagePrompt/CurrentFileLabel");
            r.SavePromptButton = FindGo(root, "BG/PagePrompt/SavePromptBtn");
            r.NewNpcInput = FindComp<InputField>(root, "BG/PagePrompt/NewNpcInput");
            r.NewNpcButton = FindGo(root, "BG/PagePrompt/NewNpcBtn");
            r.DeletePromptButton = FindGo(root, "BG/PagePrompt/DeletePromptBtn");   // 可选节点，缺失=无删除功能
            return r;
        }

        // ---- Find 助手（AbChatPanel 的同名方法为 private，此处独立一份）----
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
            if (_current == this) _current = null;
        }
    }
}
#endif // AB_UI
