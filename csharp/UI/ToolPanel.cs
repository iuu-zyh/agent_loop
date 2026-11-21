/// <summary>
/// 对话窗右侧「可做的事」侧边栏（10-19 用户需求）。
///
/// ## 为什么要有它
///
/// 十个工具全在 `tools/schemas.py` 里，玩家**看不见**。他不知道这个 mod 能做什么，于是那些
/// 工具只能等着他碰巧说出某句恰好能触发的台词 —— 发现性为零。这个侧边栏把"能做的事"摆出来：
/// 一行 = 一句人话 + 它背后那个工具，点一下 = 说出那句话。
///
/// ## 三条设计定案（改这个文件之前先读）
///
/// ★ ① **一个预制体字节都不动。** 侧边栏整棵树在运行时搭（`Build`），按钮样式靠**克隆**
///   `BG/ConfigBtn` 得到（字号/字体/描边/Image 精灵/Button 过渡逐字相同，不会出现"这颗按钮的
///   字比别人大一号"）。理由：改 `UIChatAi.prefab` 要重打 `.ab`（手工步骤 + Unity 许可证风险），
///   而改错了会连累整个对话窗。同款先例见 `ImageAttachPreview`（运行时建节点 + `ignoreLayout`
///   不扰动原布局）。
///
/// ★ ② **点击不是"执行"。** 十个工具的 description 里全写着「发起方恒为当前对话 NPC」——
///   工具是 NPC 的动作，不是玩家的按钮。这里只把一句话送进正常的玩家发言通道，
///   模型仍可以拒、可以讲条件、可以反问。这是**引导**，不是命令。
///
/// ★ ③ **两种点击语义，由 Python 下发的 `mode` 决定**（判定在 `ToolCatalogCore`）：
///   · `send` —— 台词直接发出去（走 `ChatPresenter.OnSubmitRequested` 同一条路：
///     时间戳、死者拦截、`TurnRegistry`、渲染闸全都不用复制一遍）。
///   · `fill` —— 台词**不发**，只填进输入框、光标停在空位，玩家补齐再按回车。
///     经济三件（赠送/买卖/索取）必属此类：它们要玩家说出具体要哪件东西，
///     没有一句预设台词能替他说。让模型自己猜 = 送错东西，比不让点更糟。
///
/// ## 数据从哪来
///
/// 目录的唯一真相是 Python 的 `tools/catalog.py`，经 `list_tools` RPC 下发（首次打开时拉一次，
/// 之后内存缓存 —— 缓存的是**数据本体** `_data`，宿主销毁重建时从它原地重画，不再发 RPC；
/// 只缓存"拉过没有"的布尔位会出 10-21 那个"第二次开窗空面板"的 bug）。
/// **C# 不认识任何工具名**，它只把 `{title, sub, mode, say}` 画成行。
/// 这样新加一个工具只需改 Python，不会出现"两个真相源"。
///
/// ## 版面（单位 = BG 的 1200×800 坐标系）
///
/// <code>
///  BG 1200×800
///  ┌──────────────────────────────┬───────────┐
///  │ NpcInput     [工具][踢人]…[✕] │ ← y 760~800（顶栏不压）
///  ├──────────────────────────────┼───────────┤
///  │                              │ 可做的事 ✕ │ ← 头 52
///  │        消息区                 ├───────────┤
///  │                              │ 分组标题   │
///  │                              │ 行…（滚动）│
///  ├──────────────────────────────┼───────────┤
///  │  输入框              [发送]   │           │
///  └──────────────────────────────┴───────────┘
///                                  └── 300 宽 ──┘  x: 900~1200, y: 0~760
/// </code>
///
/// 面板顶边**刻意停在 760**（顶栏下沿）而不是 800：顶栏那一排按钮是"窗口级"控件，
/// 让它们全程可点（尤其 ✕），比"面板盖住一切"安全 —— 面板一旦盖住 ✕，玩家就只能靠 ESC 退出。
/// </summary>
using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.UI;

using static AgentLoopBridge.UiKit;
namespace AgentLoopBridge
{
    /// <summary>右侧「可做的事」侧边栏。静态类：一棵树、一份缓存，随对话宿主生灭。</summary>
    internal static class ToolPanel
    {
        // ---- 版式常量（与文件头那张图逐项对应；改版式必须同步改注释）----
        private const float PanelW = 300f;     // 面板宽 = BG 宽 1200 的四分之一（用户定的"右侧四分之一"）
        private const float TopInset = 40f;    // 顶边距 = BG 高 800 − 顶栏下沿 760
        private const float HeadH = 52f;       // 头部（标题 + ✕）
        private const float PadX = 10f;        // 左右内缩
        private const float PadB = 12f;        // 底部内缩
        private const float RowH = 54f;        // 一行目录项
        private const float GroupH = 30f;      // 分组标题
        private const float RowGap = 6f;       // 行间距

        // ---- 配色（与对话窗同一套浅色底：BG 是 0.868/0.847/0.847 的暖白）----
        private static readonly Color PanelBg = new Color(0.795f, 0.780f, 0.780f, 1f);
        private static readonly Color RowBg = new Color(0.948f, 0.940f, 0.940f, 1f);
        private static readonly Color RowBgDim = new Color(0.900f, 0.895f, 0.895f, 1f);
        private static readonly Color TitleColor = new Color(0.10f, 0.11f, 0.16f, 1f);
        private static readonly Color SubColor = new Color(0.44f, 0.45f, 0.52f, 1f);
        private static readonly Color GroupColor = new Color(0.34f, 0.35f, 0.42f, 1f);
        private static readonly Color HeadColor = new Color(0.16f, 0.17f, 0.24f, 1f);

        // ---- 状态 ----
        private static GameObject _root;          // 面板根（BG 的子节点）
        private static GameObject _btn;           // 顶栏那颗「工具」钮（克隆 ConfigBtn 得到）
        private static RectTransform _content;    // 滚动内容容器（行的父）
        private static ScrollRect _scroll;
        private static bool _open;
        private static bool _hasData;             // 已经拿到过一次目录（不是"现在有行"——空目录也算拿到了）
        private static JObject _data;             // 上次拉回的目录本体（10-21）：**树随宿主死，数据不跟着死**

        /// <summary>顶栏那颗「工具」钮的宽度（与相邻按钮同节奏：64 宽、间距 8px）。</summary>
        private const float ToolBtnW = 64f;

        /// <summary>
        /// 顶栏那颗「工具」钮的 x（右锚点 = 右缘坐标）。**= 压缩键左缘再往左 8px**。
        ///
        /// ★ 10-19 实机纠正（用户截图实证「工具/删除重叠」）：第一版放在 ⚙ 左边 -107.8，
        ///   是因为把顶栏当成了「✕、设置、压缩」三颗 —— 实际是**五颗**（单聊可见四颗），
        ///   「删除」(-169.8~-105.8) 正好被盖住。完整序列（右缘坐标，右→左）：
        ///     ✕ -40 ／ 设置 -99.8 ／ 删除 -105.8 起步宽64 ⇒ 左缘 -169.8 ／
        ///     压缩 -177.8（右缘）⇒ 左缘 -241.8 ／ 拉人 -249.8 ／ 踢人 -321.8
        ///   ⇒ 工具钮右缘 = **-249.8**。
        ///
        /// 为什么这个位置**永远安全**：拉人/踢人只在群聊可见（`SetRoomButtonsVisible`），
        /// 工具钮只在单聊可见 —— 两者**从不同屏**，压缩左边的这一格在两种模式下都是空的。
        /// </summary>
        private const float ToolBtnX = -249.8f;

        /// <summary>点了一行。`mode` = `send` / `fill`，`say` = 那句话（原样，含空位）。</summary>
        public static event Action<string, string> ItemChosen;

        /// <summary>面板此刻是否显示着（ESC 链与"关窗要收起"都用它）。</summary>
        public static bool IsOpen { get { return _open && Alive(_root) && _root.activeSelf; } }

        /// <summary>已经拉到过目录（true 时 `Toggle` 直接开，不再发 RPC）。</summary>
        public static bool HasData { get { return _hasData; } }

        /// <summary>面板树是否建起来了（`ChatWindow.Init` 据此决定要不要告警）。
        /// 用 `Alive` 而不是 `!= null`：宿主被游戏销毁后静态引用是**僵尸**（见该函数）。</summary>
        public static bool HasPanel { get { return Alive(_root); } }

        /// <summary>顶栏那颗钮（群聊要藏、删除模式要藏）。僵尸引用返回 null。</summary>
        public static GameObject ButtonGo
        {
            get
            {
                if (Alive(_btn)) return _btn;
                _btn = null;
                return null;
            }
        }

        /// <summary>
        /// 僵尸守卫（`AbConfigPanel.IsAlive` 同款，真机教训）：面板挂在对话宿主 `BG` 下，
        /// 而宿主随**场景/存档**销毁 —— 销毁之后静态引用不再是 null，而是"看着还在、
        /// 实际节点已死"的僵尸：连 `== null` 判空都可能抛 `ObjectCollectedException`。
        ///
        /// 不守的后果：换一次存档之后，`Show()` 会对一个死节点 SetActive ——
        /// 要么抛异常、要么"静默成功"但屏幕上什么都不出现；后一种更要命，因为
        /// `_open` 会停在 true，ESC 链每次都先把那一击吞掉（表现成「ESC 不管用了」）。
        /// </summary>
        private static bool Alive(GameObject go)
        {
            if (go == null) return false;
            try { return go != null; }        // fake-null（已 Destroy）在这里变 false
            catch { return false; }          // GC 僵尸：判空即抛
        }

        /// <summary>组件版（`_content`/`_scroll` 是 Component，不是 GameObject）。同款僵尸守卫。</summary>
        private static bool Alive(Component c)
        {
            if (c == null) return false;
            try { return c != null; }
            catch { return false; }
        }

        // ------------------------------------------------------------------
        // 构建（幂等：宿主重建后自动重搭）
        // ------------------------------------------------------------------

        /// <summary>在 BG 下搭出侧边栏（初始隐藏）+ 顶栏那颗「工具」钮。`bg` = `ChatWindowRefs.Window`
        /// （BG 的 RectTransform）。`onClick` = 顶栏钮的回调（由 `ChatWindow` 转成 `ToolsClicked` 事件）。
        /// 失败只记一行日志，绝不影响开窗。</summary>
        public static void Build(RectTransform bg, Action onClick)
        {
            try
            {
                if (bg == null) { Log("Build: BG 为空，侧边栏不搭（顶栏那颗「工具」钮也建不出来）"); return; }
                if (Alive(_root))
                {
                    // 宿主还在：只把父级对齐一次（同一棵树不重复搭）
                    if (_root.transform.parent != bg) _root.transform.SetParent(bg, false);
                    if (!Alive(_btn)) MakeToolButton(bg, onClick);
                    return;
                }
                // 走到这里可能是"从没建过"，也可能是"上次的宿主随场景没了"（僵尸引用）——
                // 两种都当新建处理；僵尸那两个字段先归零，免得后面读到旧包装器（见 `Alive`）。
                _root = null;
                _btn = null;
                var go = new GameObject("ToolSidePanel");
                var rt = go.AddComponent<RectTransform>();
                rt.SetParent(bg, false);
                // 右锚点 + 上下拉伸：顶边钉在顶栏下沿，底边贴 BG 底
                rt.anchorMin = new Vector2(1f, 0f);
                rt.anchorMax = new Vector2(1f, 1f);
                rt.pivot = new Vector2(1f, 1f);
                rt.anchoredPosition = new Vector2(0f, -TopInset);
                rt.sizeDelta = new Vector2(PanelW, -TopInset);
                go.AddComponent<CanvasRenderer>();

                // 底板：借 BG 的 Image 精灵（同一张九宫格 ⇒ 圆角/描边与窗口一致）
                var bgImg = bg.GetComponent<Image>();
                var img = go.AddComponent<Image>();
                img.sprite = bgImg != null ? bgImg.sprite : null;
                img.type = Image.Type.Sliced;
                img.color = PanelBg;
                img.raycastTarget = true;      // ★ 面板自己吃掉落在它身上的点击（不穿透到消息区）

                BuildHead(rt);
                BuildScroll(rt);

                go.transform.SetAsLastSibling();   // 画在 BG 的所有子节点之上
                go.SetActive(false);
                _root = go;
                MakeToolButton(bg, onClick);
                // ★ 10-21 实机 bug（用户截图：第二次开窗面板只剩头部，行全空）：
                //   游戏 `CloseUI` 会**销毁**对话宿主（Player.log 实证：每次重开都有一行
                //   "创建宿主"），这棵树挂在 BG 下随它生灭；行是在 `SetData` 里画的，
                //   重建出来的新树只有头和滚动区。而 `_hasData` 活在静态字段里**不跟着树死**
                //   ⇒ `OnToolsClicked` 永远走 `HasData → Show()` 快路径，再也不会补画行。
                //   修法：目录本体缓存于 `_data`（见 `SetData`），重建后**原地重画**——
                //   既不出空面板，也保住"一个世界只拉一次 RPC"的原设计（见文件头"数据从哪来"）。
                _open = false;                     // 新树出厂是隐藏的，状态位跟齐
                if (_data != null) SetData(_data);
                Log("侧边栏已搭好 宽=" + PanelW + " 高=" + (800f - TopInset) + "（顶边钉在顶栏下沿）" +
                    (_data != null ? " + 目录已从缓存重画" : ""));
            }
            catch (Exception e)
            {
                Log("Build 失败（侧边栏不可用，其余功能一字不变）: " + e.Message);
                _root = null;
                if (bg != null) UiKit.AuditSoon(bg, "ToolPanel");
            }
        }

        /// <summary>
        /// 顶栏那颗「工具」钮 —— **克隆 ⚙ ConfigBtn**（同排、同尺寸族、同 Image/Button 组件集），
        /// 再改锚点与文案。克隆而不是新建的理由：新建出来的按钮字号/字体/描边/过渡色与隔壁不一样，
        /// 实机上一眼就看出"这不是美术做的"。同款做法见 `prefab_patch_chat_skip_button.py`
        /// （那颗「跳过」也是克隆 SendBtn，而不是画一个新的）。
        /// </summary>
        private static void MakeToolButton(RectTransform bg, Action onClick)
        {
            try
            {
                if (bg == null) return;
                var src = bg.Find("ConfigBtn");
                GameObject go;
                if (src != null)
                {
                    go = UnityEngine.Object.Instantiate(src.gameObject) as GameObject;
                    if (go == null) { Log("工具钮：克隆 ConfigBtn 得到 null，改用渐变兜底"); MakeFallbackButton(bg, onClick); return; }
                    go.name = "ToolBtn";
                    go.transform.SetParent(bg, false);
                }
                else
                {
                    // 没装 ConfigBtn 的预制体（老 AB / 代码版）—— 照样要有个入口
                    Log("工具钮：预制体里没有 ConfigBtn，改用纯色兜底钮");
                    MakeFallbackButton(bg, onClick);
                    return;
                }
                var rt = go.GetComponent<RectTransform>();
                if (rt != null)
                {
                    // ★ 位置 = 压缩键的左边（10-19 实机纠正：第一版放 ⚙ 左边 -107.8，
                    //   把「删除」键 -105.8~-169.8 整个盖住了 —— 顶栏实际是**五颗**，
                    //   我当时只算了三颗）。完整序列（右锚点，右缘坐标）：
                    //     ✕(-40) 设置(-99.8) 删除(-105.8? 不，删除 64 宽 ⇒ -169.8)
                    //     压缩(-177.8) 拉人(-249.8) 踢人(-321.8)
                    //   压缩右缘 -177.8 ⇒ 左缘 -241.8 ⇒ 工具钮右缘 = -249.8（8px 间距）。
                    //   拉人/踢人**只在群聊出现**、工具钮**只在单聊出现** —— 两者永不同屏，
                    //   所以这个位置无论哪个模式都不会撞（这正是选它而不是别处的原因）。
                    rt.anchorMin = new Vector2(1f, 1f);
                    rt.anchorMax = new Vector2(1f, 1f);
                    rt.pivot = new Vector2(1f, 0.5f);
                    rt.sizeDelta = new Vector2(ToolBtnW, 40f);
                    rt.anchoredPosition = new Vector2(ToolBtnX, -20f);
                }
                var label = go.GetComponentInChildren<Text>(true);
                if (label != null) label.text = "工具";
                var btn = go.GetComponent<Button>();
                if (btn != null)
                {
                    ClickUtils.Attach(btn, onClick);
                    // 克隆来的 Button 可能带着游戏原回调（ConfigBtn 的 ⚙ 行为）——
                    // Attach 内部先 RemoveAllListeners，所以这里已经很干净。
                }
                _btn = go;
                Log("顶栏「工具」钮已就位（克隆 ConfigBtn，宽=" + ToolBtnW + "，x=" + ToolBtnX + " = 压缩键左侧）");
            }
            catch (Exception e)
            {
                Log("工具钮建失败: " + e.Message);
            }
        }

        /// <summary>兜底钮（预制体没有 ConfigBtn 时）：纯色块 + 文字，样式与目录行同一族。</summary>
        private static void MakeFallbackButton(RectTransform bg, Action onClick)
        {
            var go = NewButton("ToolBtn", bg, "工具", 15, TitleColor, RowBg);
            var rt = go.GetComponent<RectTransform>();
            rt.anchorMin = new Vector2(1f, 1f);
            rt.anchorMax = new Vector2(1f, 1f);
            rt.pivot = new Vector2(1f, 0.5f);
            rt.sizeDelta = new Vector2(ToolBtnW, 40f);
            rt.anchoredPosition = new Vector2(ToolBtnX, -20f);   // 与克隆路径同一个位置常量
            ClickUtils.Attach(go.GetComponent<Button>(), onClick);
            _btn = go;
        }

        private static void BuildHead(RectTransform parent)
        {
            var head = MakeRect("Head", parent);
            head.anchorMin = new Vector2(0f, 1f);
            head.anchorMax = new Vector2(1f, 1f);
            head.pivot = new Vector2(0.5f, 1f);
            head.offsetMin = new Vector2(PadX, -HeadH);
            head.offsetMax = new Vector2(-PadX, 0f);

            var title = NewText("Title", head, "可做的事", 18, HeadColor, TextAnchor.MiddleLeft, FontStyle.Bold);
            Stretch(title.rectTransform, 0f, 0f, 44f, 0f);   // 右侧让开 ✕

            // ✕：整块面板唯一的关闭入口（顶栏的 ✕ 关的是对话窗，不是面板 —— 两者别混）
            var close = NewButton("Close", head, "✕", 15, HeadColor, new Color(0f, 0f, 0f, 0f));
            var crt = close.GetComponent<RectTransform>();
            crt.anchorMin = new Vector2(1f, 0.5f);
            crt.anchorMax = new Vector2(1f, 0.5f);
            crt.pivot = new Vector2(1f, 0.5f);
            crt.sizeDelta = new Vector2(40f, 40f);
            crt.anchoredPosition = Vector2.zero;
            ClickUtils.Attach(close.GetComponent<Button>(), Hide);
        }

        private static void BuildScroll(RectTransform parent)
        {
            var scrollRt = MakeRect("Scroll", parent);
            scrollRt.anchorMin = new Vector2(0f, 0f);
            scrollRt.anchorMax = new Vector2(1f, 1f);
            scrollRt.offsetMin = new Vector2(PadX, PadB);
            scrollRt.offsetMax = new Vector2(-PadX, -HeadH);

            // 滚动区必须自带一张**可命中的 Graphic**，否则滚轮/拖动一点都进不来
            // （UGUI 事件要先命中 Graphic 再冒泡到 ScrollRect —— 与 `prefab_patch_scroll_hit.py`
            //  那次"面板能开、节点全 True、就是滚不动"是同一个坑）。全透明但仍然吃射线。
            scrollRt.gameObject.AddComponent<CanvasRenderer>();
            var hit = scrollRt.gameObject.AddComponent<Image>();
            hit.color = new Color(0f, 0f, 0f, 1f / 255f);   // 刻意不写 0：α=0 会被当"不渲染"剔除
            hit.raycastTarget = true;

            var scroll = scrollRt.gameObject.AddComponent<ScrollRect>();
            scroll.horizontal = false;
            scroll.vertical = true;
            scroll.movementType = ScrollRect.MovementType.Clamped;
            scroll.scrollSensitivity = 30f;
            scroll.inertia = true;
            _scroll = scroll;

            var viewport = MakeRect("Viewport", scrollRt);
            Stretch(viewport, 0f, 0f, 0f, 0f);
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
            vlg.spacing = RowGap;
            vlg.padding = new RectOffset(0, 0, 2, 8);
            vlg.childControlWidth = true;
            vlg.childControlHeight = true;
            vlg.childForceExpandWidth = true;
            vlg.childForceExpandHeight = false;
            var csf = content.gameObject.AddComponent<ContentSizeFitter>();
            csf.verticalFit = ContentSizeFitter.FitMode.PreferredSize;
            csf.horizontalFit = ContentSizeFitter.FitMode.Unconstrained;
            scroll.content = content;
            _content = content;
        }

        // ------------------------------------------------------------------
        // 数据（`list_tools` 的 response.data）
        // ------------------------------------------------------------------

        /// <summary>把目录画成行。可重复调用（整体重建）。目录为空也只记一行 —— 空面板比"没反应"诚实。</summary>
        public static void SetData(JObject data)
        {
            try
            {
                // ★ 先缓存、后画图（10-21）：RPC 回包时宿主可能已被游戏销毁（关窗落在 8s 超时内），
                //   树死了数据不该跟着丢——存进 `_data`，下次 `Build` 重建新树时原地重画。
                _data = data;
                _hasData = true;
                if (_root == null || _content == null) return;   // 树不在/已僵尸：只缓存不画
                ClearRows();

                var groups = data != null ? data["groups"] as JArray : null;
                if (groups == null || groups.Count == 0)
                {
                    Log("SetData: 目录为空（Python 侧 list_tools 回了空表）");
                    return;
                }
                int made = 0;
                for (int gi = 0; gi < groups.Count; gi++)
                {
                    var g = groups[gi] as JObject;
                    if (g == null) continue;
                    string gtitle = Str(g["title"]);
                    if (gtitle.Length > 0) MakeGroupHeader(gtitle);
                    var items = g["items"] as JArray;
                    if (items == null) continue;
                    for (int i = 0; i < items.Count; i++)
                    {
                        var it = items[i] as JObject;
                        if (it == null) continue;
                        MakeRow(Str(it["title"]), Str(it["sub"]), Str(it["mode"]), Str(it["say"]));
                        made++;
                    }
                }
                Log("侧边栏目录已就绪：" + groups.Count + " 组 / " + made + " 条");
            }
            catch (Exception e)
            {
                Log("SetData 失败: " + e.Message);
            }
        }

        private static void MakeGroupHeader(string text)
        {
            var t = NewText("Group", _content, text, 13, GroupColor, TextAnchor.LowerLeft, FontStyle.Bold);
            var le = t.gameObject.AddComponent<LayoutElement>();
            le.preferredHeight = GroupH;
            le.minHeight = GroupH;
            le.ignoreLayout = false;
        }

        private static void MakeRow(string title, string sub, string mode, string say)
        {
            bool fill = ToolCatalogCore.IsFill(mode, say);
            var row = NewButton("Row", _content, "", 18, TitleColor, RowBg);
            var le = row.AddComponent<LayoutElement>();
            le.preferredHeight = RowH;
            le.minHeight = RowH;

            // 每行两个文本：主标题（做什么）+ 小字（背后是哪个工具 —— 用户要的"工具展示"就是它）
            var tt = NewText("Title", row.GetComponent<RectTransform>(), title, 17, TitleColor,
                             TextAnchor.UpperLeft, FontStyle.Normal);
            var trt = tt.rectTransform;
            trt.anchorMin = new Vector2(0f, 1f);
            trt.anchorMax = new Vector2(1f, 1f);
            trt.pivot = new Vector2(0.5f, 1f);
            trt.offsetMin = new Vector2(12f, -32f);
            trt.offsetMax = new Vector2(-12f, -6f);

            if (sub.Length > 0)
            {
                var st = NewText("Sub", row.GetComponent<RectTransform>(), sub, 11, SubColor,
                                 TextAnchor.UpperLeft, FontStyle.Normal);
                var srt = st.rectTransform;
                srt.anchorMin = new Vector2(0f, 0f);
                srt.anchorMax = new Vector2(1f, 0f);
                srt.pivot = new Vector2(0.5f, 0f);
                srt.offsetMin = new Vector2(12f, 5f);
                srt.offsetMax = new Vector2(-12f, 22f);
            }

            // fill 型给一个左侧的"待补"竖条：点了之后是填空，不是直接发 —— 这一点必须在按之前看得出来
            if (fill)
            {
                var bar = MakeRect("FillMark", row.GetComponent<RectTransform>());
                bar.anchorMin = new Vector2(0f, 0.5f);
                bar.anchorMax = new Vector2(0f, 0.5f);
                bar.pivot = new Vector2(0f, 0.5f);
                bar.sizeDelta = new Vector2(3f, RowH - 22f);
                bar.anchoredPosition = new Vector2(0f, 0f);
                bar.gameObject.AddComponent<CanvasRenderer>();
                var bi = bar.gameObject.AddComponent<Image>();
                bi.sprite = null;
                bi.color = RowBgDim;
                bi.raycastTarget = false;
            }

            string m = mode, s = say;
            ClickUtils.Attach(row.GetComponent<Button>(), () =>
            {
                // 先收起再抛：面板不收起的话，玩家看不到自己那句话落地（`fill` 型更看不到输入框）
                Hide();
                var h = ItemChosen;
                if (h != null) h(m, s);
            });
        }

        private static void ClearRows()
        {
            if (_content == null) return;
            var kids = new List<Transform>();
            for (int i = 0; i < _content.childCount; i++) kids.Add(_content.GetChild(i));
            for (int i = 0; i < kids.Count; i++)
            {
                if (kids[i] == null) continue;
                // 先摘父级再销毁：Destroy 要到帧末才生效，不摘的话同帧重建会算出双倍高度
                kids[i].SetParent(null, false);
                UnityEngine.Object.Destroy(kids[i].gameObject);
            }
        }

        // ------------------------------------------------------------------
        // 显隐
        // ------------------------------------------------------------------

        public static void Toggle()
        {
            if (IsOpen) Hide(); else Show();
        }

        public static void Show()
        {
            if (_root == null) { Log("Show: 面板尚未搭好（BG 解析失败？）"); return; }
            _root.SetActive(true);
            _root.transform.SetAsLastSibling();   // 每次开都置顶：别被后建的行/预览条压住
            _open = true;
            if (_scroll != null && _content != null)
            {
                // 回到顶部：目录是"从看开始"排的，每次开都从头读
                try { _scroll.verticalNormalizedPosition = 1f; } catch { }
            }
            Log("侧边栏打开");
        }

        public static void Hide()
        {
            if (_root != null && _root.activeSelf) _root.SetActive(false);
            _open = false;
        }

        /// <summary>宿主换人/换档/关窗时调：只收起，**不销毁**（树与目录缓存都还能复用）。</summary>
        /// <summary>宿主换人/换档/关窗时调：收起面板；**换档时用 `ResetForWorld`**（那个会清引用）。
        /// 本方法只收显隐 —— 树与目录缓存都还能复用（同一存档内关窗再开）。</summary>
        public static void Reset()
        {
            Hide();
        }

        /// <summary>
        /// 换存档：清掉静态引用（**不 Destroy** —— 节点已随场景没了）。
        ///
        /// 与 `Reset()` 的区别就是这一点：只 `Hide()` 的话，静态引用会指向已销毁的僵尸节点，
        /// 新档里 `Build` 看到 `_root != null` 会以为"宿主还在"直接 return ⇒
        /// **面板永远不会重建**，而日志里一句都不打（只有 `HasPanel` 恒 false 这条线索）。
        /// 同款处理见 `BigPortrait.ResetForWorld` / `PortraitCache.ResetForWorld`。
        /// </summary>
        public static void ResetForWorld()
        {
            try { Hide(); } catch { }
            _root = null;
            _btn = null;
            _content = null;
            _scroll = null;
            _open = false;
            // 目录缓存一并作废：新存档可能换了工具集（版本升级后旧目录也不该继续用）
            _hasData = false;
            _data = null;
            Log("换存档：侧边栏引用已清（下次开窗重建）");
        }

        /// <summary>目录缓存作废（Python 重启 / 换存档后工具表可能变了）：下一次打开重新拉。</summary>
        public static void Invalidate()
        {
            _hasData = false;
            _data = null;
            ClearRows();
        }

        /// <summary>诊断用：一眼看出面板到底搭没搭、开没开、有几行、数据缓存有没有。</summary>
        public static string Status()
        {
            int n = Alive(_content) ? _content.childCount : -1;
            return "root=" + (Alive(_root) ? (_root.activeSelf ? "on" : "off") : "无") +
                   " 行=" + n + " 有目录=" + _hasData + " 缓存=" + (_data != null ? "有" : "无") +
                   " 开=" + _open;
        }

        // ------------------------------------------------------------------
        // 建节点的小工具（样式全部集中在这里，行/头/按钮都走它们）
        // ------------------------------------------------------------------


        private static Text NewText(string name, Transform parent, string text, int size,
                                    Color color, TextAnchor align, FontStyle style)
        {
            var rt = MakeRect(name, parent);
            rt.gameObject.AddComponent<CanvasRenderer>();
            var t = rt.gameObject.AddComponent<Text>();
            t.text = text ?? "";
            t.fontSize = size;
            t.fontStyle = style;
            t.color = color;
            t.alignment = align;
            // 字体三级兜底与代码版对话窗同源（游戏现成 Text 的字体自带 CJK，最优）
            try { t.font = ChatUiBuilder.ResolveSharedFont(); } catch { }
            t.raycastTarget = false;     // 文字不吃射线：点了要落到行按钮上
            t.horizontalOverflow = HorizontalWrapMode.Wrap;
            t.verticalOverflow = VerticalWrapMode.Truncate;
            t.supportRichText = false;   // 目录里全是人话，不该有标签；关掉免得某天写进一个 <>
            return t;
        }

        /// <summary>可点行/按钮：Image（底色）+ Button + 居中或左对齐的标签。
        /// `labelAtTop` 为 true 时标签贴顶（行内标题由调用方另建两个 Text，故这里一般传空串）。</summary>
        private static GameObject NewButton(string name, Transform parent, string label, int size,
                                            Color labelColor, Color bg)
        {
            var rt = MakeRect(name, parent);
            rt.gameObject.AddComponent<CanvasRenderer>();
            var img = rt.gameObject.AddComponent<Image>();
            img.sprite = null;          // 纯色块：不借九宫格，圆角在 300px 宽的小卡上反而不稳
            img.color = bg;
            img.raycastTarget = true;
            var btn = rt.gameObject.AddComponent<Button>();
            btn.targetGraphic = img;
            btn.transition = Selectable.Transition.ColorTint;
            var cb = btn.colors;
            cb.normalColor = Color.white;
            cb.highlightedColor = new Color(0.93f, 0.93f, 0.93f, 1f);
            cb.pressedColor = new Color(0.85f, 0.85f, 0.85f, 1f);
            cb.selectedColor = Color.white;
            cb.disabledColor = new Color(1f, 1f, 1f, 0.5f);
            cb.colorMultiplier = 1f;
            cb.fadeDuration = 0.08f;
            btn.colors = cb;
            if (!string.IsNullOrEmpty(label))
            {
                var t = NewText("Text", rt, label, size, labelColor, TextAnchor.MiddleCenter, FontStyle.Normal);
                Stretch(t.rectTransform, 2f, 0f, 2f, 0f);
            }
            return rt.gameObject;
        }


        private static string Str(JToken t)
        {
            if (t == null) return "";
            try { return ((string)t) ?? ""; } catch { return ""; }
        }

        private static void Log(string msg)
        {
            try { ModMain.P("[ToolPanel] " + msg); } catch { }
        }
    }
}
