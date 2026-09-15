/// <summary>
/// 对话 UI 静态构建器 —— 用代码搭出整棵 UGUI 节点树，返回 ChatWindowRefs 契约。
///
/// 各司其职：本文件只负责「搭节点/贴样式/藏模板」。不订阅任何事件、没有 Update、
/// 不知道 WsClient 存在。所有视觉定义（层级、颜色、字号、九宫格、模板）只在这里。
///
/// IL2CPP 注意：所有「可点」对象统一挂 UnityEngine.UI.Button，回调经 ClickUtils
/// 「三步写法」（Action → UnityAction.op_Implicit → onClick.AddListener）挂接——
/// 委托桥已由编译级冒烟实证（UnityAction 定义于 UnityEngine.CoreModule.dll，
/// 含 implicit operator UnityAction(Action)），ClickCatcher 轮询已退役。
/// 直接 AddListener(() => {}) 会 CS1660（UnityAction 是 Unhollower 生成类，非 C# delegate），
/// 必须显式三步转换。
///
/// 对外只有一个入口：Build(Transform parent) → ChatWindowRefs。
/// </summary>
using System;
using UnityEngine;
using UnityEngine.Events;
using UnityEngine.UI;

namespace AgentLoopBridge
{
    public static class ChatUiBuilder
    {
        // ---- 版式常量（视觉定义集中于此）----
        private const float PANEL_W = 520f;
        private const float PANEL_H = 760f;
        private const float BUBBLE_MAX_WIDTH = 330f;       // 气泡固定最大宽（文本在此换行，高度自适应）
        private const int UI_LAYER = 5;                    // Unity 标准 UI 层（Build 时用 NameToLayer 兜底解析）

        // ---- 配色 ----
        private static readonly Color PanelBg = new Color(0.043f, 0.043f, 0.075f, 0.95f);
        private static readonly Color ScrollBg = new Color(0f, 0f, 0f, 0.30f);
        private static readonly Color UserBubble = new Color(0.145f, 0.30f, 0.58f, 1f);
        private static readonly Color NpcBubble = new Color(0.93f, 0.94f, 0.97f, 1f);
        private static readonly Color CtrlBg = new Color(0.16f, 0.18f, 0.26f, 1f);
        private static readonly Color TextDark = new Color(0.10f, 0.11f, 0.16f, 1f);
        private static readonly Color TextLight = new Color(0.94f, 0.95f, 0.98f, 1f);
        private static readonly Color TextDim = new Color(0.60f, 0.62f, 0.70f, 1f);

        /// <summary>搭对话 UI 整树。parent 通常是 g.root（游戏场景根，随游戏持久）。</summary>
        public static ChatWindowRefs Build(Transform parent)
        {
            var refs = new ChatWindowRefs();

            int layer = LayerMask.NameToLayer("UI");
            if (layer < 0) layer = UI_LAYER;

            // ---------- ① Canvas 根 ----------
            var canvasGo = new GameObject("AgentLoopChatCanvas");
            canvasGo.transform.SetParent(parent, false);
            canvasGo.layer = layer;
            canvasGo.AddComponent<RectTransform>();
            var canvas = canvasGo.AddComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            canvas.sortingOrder = 3000; // 压过普通 UI
            canvasGo.AddComponent<CanvasScaler>().uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            var scaler = canvasGo.GetComponent<CanvasScaler>();
            scaler.referenceResolution = new Vector2(1920f, 1080f);
            scaler.matchWidthOrHeight = 0.5f;
            canvasGo.AddComponent<GraphicRaycaster>();
            refs.Root = canvasGo;

            // 防御：场景没有 EventSystem 时补建一个（游戏必有，纯保险）
            EnsureEventSystem(parent);

            // ---------- ② 主面板 ----------
            Sprite panelSpr = MakeSlicedSprite(PanelBg, 12, 4);
            var windowRt = MakeImage("Window", canvasGo.transform, panelSpr, Color.white, Image.Type.Sliced);
            windowRt.anchorMin = new Vector2(1f, 0.5f);
            windowRt.anchorMax = new Vector2(1f, 0.5f);
            windowRt.pivot = new Vector2(1f, 0.5f);
            windowRt.anchoredPosition = new Vector2(-24f, 0f);
            windowRt.sizeDelta = new Vector2(PANEL_W, PANEL_H);
            refs.Window = windowRt;
            SetLayerRecursive(windowRt, layer);

            // ---------- ③ 标题栏 ----------
            var titleRt = MakeRect("Title", windowRt);
            titleRt.anchorMin = new Vector2(0f, 1f);
            titleRt.anchorMax = new Vector2(1f, 1f);
            titleRt.pivot = new Vector2(0.5f, 1f);
            titleRt.offsetMin = new Vector2(10f, -48f);
            titleRt.offsetMax = new Vector2(-10f, 0f);

            refs.NpcLabel = MakeInputField("NpcLabel", titleRt, "NPC 名（回车切换）");
            var npcRt = AsTransform(refs.NpcLabel);
            npcRt.anchorMin = new Vector2(0f, 0f);
            npcRt.anchorMax = new Vector2(0f, 1f);
            npcRt.pivot = new Vector2(0f, 0.5f);
            npcRt.sizeDelta = new Vector2(190f, 0f);
            npcRt.anchoredPosition = Vector2.zero;

            refs.CloseButton = MakeClickable("CloseButton", titleRt, "✕");
            var closeRt = refs.CloseButton.GetComponent<RectTransform>();
            closeRt.anchorMin = new Vector2(1f, 0f);
            closeRt.anchorMax = new Vector2(1f, 1f);
            closeRt.pivot = new Vector2(1f, 0.5f);
            closeRt.sizeDelta = new Vector2(40f, 0f);
            closeRt.anchoredPosition = Vector2.zero;

            // ⚙ 配置面板入口（CloseBtn 左侧；点击 → ConfigPanelOpener，由 ModMain 订阅事件接线）
            refs.ConfigButton = MakeClickable("ConfigButton", titleRt, "⚙");
            var cfgBtnRt = refs.ConfigButton.GetComponent<RectTransform>();
            cfgBtnRt.anchorMin = new Vector2(1f, 0f);
            cfgBtnRt.anchorMax = new Vector2(1f, 1f);
            cfgBtnRt.pivot = new Vector2(1f, 0.5f);
            cfgBtnRt.sizeDelta = new Vector2(40f, 0f);
            cfgBtnRt.anchoredPosition = new Vector2(-44f, 0f);

            // ---------- ④ 消息滚动区 ----------
            Sprite scroBgSpr = MakeSlicedSprite(ScrollBg, 8, 3);
            var scrollRt = MakeImage("ChatScroll", windowRt, scroBgSpr, Color.white, Image.Type.Sliced);
            scrollRt.anchorMin = new Vector2(0f, 0f);
            scrollRt.anchorMax = new Vector2(1f, 1f);
            scrollRt.offsetMin = new Vector2(10f, 54f);
            scrollRt.offsetMax = new Vector2(-10f, -58f);
            var scroll = scrollRt.gameObject.AddComponent<ScrollRect>();
            scroll.horizontal = false;
            scroll.vertical = true;
            scroll.movementType = ScrollRect.MovementType.Clamped;
            scroll.scrollSensitivity = 30f;
            refs.Scroll = scroll;

            // Viewport（裁剪）
            var viewportRt = MakeRect("Viewport", scrollRt);
            Stretch(viewportRt, 0f, 0f, 0f, 0f);
            viewportRt.gameObject.AddComponent<RectMask2D>();
            viewportRt.gameObject.AddComponent<CanvasRenderer>();
            refs.Viewport = viewportRt;

            // Content（纵向排布）
            var contentRt = MakeRect("Content", viewportRt);
            contentRt.anchorMin = new Vector2(0f, 1f);
            contentRt.anchorMax = new Vector2(1f, 1f);
            contentRt.pivot = new Vector2(0.5f, 1f);
            contentRt.sizeDelta = Vector2.zero;
            contentRt.anchoredPosition = Vector2.zero;
            var vlg = contentRt.gameObject.AddComponent<VerticalLayoutGroup>();
            vlg.spacing = 7f;
            vlg.padding = new RectOffset(6, 6, 4, 4);
            vlg.childControlWidth = true;      // 行全宽（对齐由行内布局决定）
            vlg.childControlHeight = true;     // 行高自适应
            vlg.childForceExpandWidth = true;
            vlg.childForceExpandHeight = false;
            var csf = contentRt.gameObject.AddComponent<ContentSizeFitter>();
            csf.verticalFit = ContentSizeFitter.FitMode.PreferredSize;
            csf.horizontalFit = ContentSizeFitter.FitMode.Unconstrained;
            refs.Content = contentRt;

            // ---------- ⑤ 底部输入栏 ----------
            var bottomRt = MakeRect("BottomBar", windowRt);
            bottomRt.anchorMin = new Vector2(0f, 0f);
            bottomRt.anchorMax = new Vector2(1f, 0f);
            bottomRt.pivot = new Vector2(0.5f, 0f);
            bottomRt.offsetMin = new Vector2(10f, 8f);
            bottomRt.offsetMax = new Vector2(-10f, 47f);

            refs.ChatInput = MakeInputField("ChatInput", bottomRt, "对 NPC 说…");
            var inputRt = AsTransform(refs.ChatInput);
            inputRt.anchorMin = Vector2.zero;
            inputRt.anchorMax = Vector2.one;
            inputRt.offsetMin = Vector2.zero;
            inputRt.offsetMax = new Vector2(-64f, 0f);

            refs.SendButton = MakeClickable("SendButton", bottomRt, "发送");
            var sendRt = refs.SendButton.GetComponent<RectTransform>();
            sendRt.anchorMin = new Vector2(1f, 0f);
            sendRt.anchorMax = new Vector2(1f, 1f);
            sendRt.pivot = new Vector2(1f, 0.5f);
            sendRt.sizeDelta = new Vector2(58f, 0f);
            sendRt.anchoredPosition = Vector2.zero;

            // ---------- ⑥ 忙态标记 ----------
            refs.BusyLabel = MakeText("BusyLabel", windowRt, "NPC 回应中…", 14f, TextDim, TextAnchor.UpperRight).gameObject;
            var busyRt = refs.BusyLabel.GetComponent<RectTransform>();
            busyRt.anchorMin = new Vector2(1f, 1f);
            busyRt.anchorMax = new Vector2(1f, 1f);
            busyRt.pivot = new Vector2(1f, 1f);
            busyRt.anchoredPosition = new Vector2(-16f, -58f);
            busyRt.sizeDelta = new Vector2(300f, 24f);
            refs.BusyLabel.SetActive(false);

            // ---------- ⑦ 行模板（全部藏到 Root 下，inactive，运行时克隆）----------
            refs.UserTemplate = MakeBubbleTemplate("UserTemplate", refs.Window, UserBubble, TextLight);
            refs.NpcTemplate = MakeBubbleTemplate("NpcTemplate", refs.Window, NpcBubble, TextDark);
            refs.StepGroupTemplate = MakeStepGroupTemplate("StepGroupTemplate", refs.Window);
            refs.SystemTemplate = MakeSystemTemplate("SystemTemplate", refs.Window);
            refs.DividerTemplate = MakeDividerTemplate("DividerTemplate", refs.Window);
            foreach (var tpl in new[] { refs.UserTemplate, refs.NpcTemplate, refs.StepGroupTemplate, refs.SystemTemplate, refs.DividerTemplate })
                tpl.SetActive(false);

            return refs;
        }

        // ------------------------------------------------------------------
        // 组件工厂
        // ------------------------------------------------------------------

        private static RectTransform MakeRect(string name, Transform parent)
        {
            var go = new GameObject(name);
            go.transform.SetParent(parent, false);
            return go.AddComponent<RectTransform>();
        }

        private static RectTransform MakeImage(string name, Transform parent, Sprite sprite, Color color, Image.Type type)
        {
            var go = new GameObject(name);
            go.transform.SetParent(parent, false);
            var rt = go.AddComponent<RectTransform>();
            go.AddComponent<CanvasRenderer>();
            var img = go.AddComponent<Image>();
            img.sprite = sprite;
            img.type = type;
            img.color = color;
            img.raycastTarget = true; // 面板/输入区接收点击
            return rt;
        }

        /// <summary>
        /// Unhollower 下 Component.transform 返回基类 Transform 包装，直接 (RectTransform)castclass
        /// 会抛 InvalidCastException——统一改走泛型 GetComponent&lt;RectTransform&gt;()（神识传音同款规避）。
        /// </summary>
        private static RectTransform AsTransform(Component c)
        {
            return c != null ? c.GetComponent<RectTransform>() : null;
        }

        /// <summary>创建 Text 并返回其文本组件（调用方用 .gameObject 拿节点）</summary>
        private static Text MakeText(string name, Transform parent, string text, float size, Color color, TextAnchor align)
        {
            var go = new GameObject(name);
            go.transform.SetParent(parent, false);
            go.AddComponent<RectTransform>();
            go.AddComponent<CanvasRenderer>();
            var txt = go.AddComponent<Text>();
            txt.text = text ?? "";
            txt.fontSize = Mathf.RoundToInt(size);
            txt.color = color;
            txt.alignment = align;
            txt.font = ResolveSharedFont();
            txt.raycastTarget = false;
            txt.horizontalOverflow = HorizontalWrapMode.Wrap;
            txt.verticalOverflow = VerticalWrapMode.Overflow;
            return txt;
        }

        /// <summary>创建「可点」对象：Image（底色）+ Button（onClick 三步写法）+ 子 Text 标签</summary>
        private static GameObject MakeClickable(string name, Transform parent, string label)
        {
            Sprite spr = MakeSlicedSprite(CtrlBg, 8, 3);
            var rt = MakeImage(name, parent, spr, Color.white, Image.Type.Sliced);
            var btn = rt.gameObject.AddComponent<Button>();
            btn.targetGraphic = rt.GetComponent<Image>();
            var txt = MakeText("Label", rt, label ?? "", 17f, TextLight, TextAnchor.MiddleCenter);
            Stretch(AsTransform(txt), 4f, 0f, 4f, 0f);
            return rt.gameObject;
        }

        private static InputField MakeInputField(string name, Transform parent, string placeholder)
        {
            Sprite spr = MakeSlicedSprite(CtrlBg, 8, 3);
            var rt = MakeImage(name, parent, spr, Color.white, Image.Type.Sliced);
            var img = rt.GetComponent<Image>();

            var txt = MakeText("Text", rt, "", 19f, TextLight, TextAnchor.MiddleLeft);
            Stretch(AsTransform(txt), 10f, 4f, 10f, 4f);

            var ph = MakeText("Placeholder", rt, placeholder ?? "", 19f, TextDim, TextAnchor.MiddleLeft);
            Stretch(AsTransform(ph), 10f, 4f, 10f, 4f);

            var field = rt.gameObject.AddComponent<InputField>();
            field.textComponent = txt;
            field.placeholder = ph;
            return field;
        }

        private static GameObject MakeBubbleTemplate(string name, Transform parent, Color bg, Color fg)
        {
            Sprite spr = MakeSlicedSprite(bg, 14, 6);
            var rt = MakeImage(name, parent, spr, Color.white, Image.Type.Sliced);
            rt.GetComponent<Image>().raycastTarget = false;
            rt.sizeDelta = new Vector2(BUBBLE_MAX_WIDTH, 60f); // 固定最大宽，文本 wrap 换行

            var txt = MakeText("Text", rt, "", 19f, fg, TextAnchor.MiddleLeft);
            Stretch(AsTransform(txt), 12f, 8f, 12f, 8f);
            txt.GetComponent<Text>().raycastTarget = false;

            // 高度自适应（宽固定；换行后高度随内容）
            var csf = rt.gameObject.AddComponent<ContentSizeFitter>();
            csf.verticalFit = ContentSizeFitter.FitMode.PreferredSize;
            csf.horizontalFit = ContentSizeFitter.FitMode.Unconstrained;
            return rt.gameObject;
        }

        private static GameObject MakeStepGroupTemplate(string name, Transform parent)
        {
            var root = MakeRect(name, parent);
            var group = root.gameObject.AddComponent<VerticalLayoutGroup>();
            group.spacing = 3f;
            group.childControlWidth = true;
            group.childControlHeight = true;
            group.childForceExpandWidth = true;
            group.childForceExpandHeight = false;

            // 折叠头（Image + Button，onClick 三步写法）
            var header = MakeClickable("Header", root, "⚙ 正在行动…");
            var headerRt = header.GetComponent<RectTransform>();
            Stretch(headerRt, 0f, 0f, 0f, 0f);
            headerRt.sizeDelta = new Vector2(0f, 30f);
            foreach (var t in header.GetComponentsInChildren<Text>(true))
            {
                t.fontSize = 15;
                t.color = Color.black;
                t.fontStyle = FontStyle.Bold;
            }

            // 内容容器（默认收起）
            var body = MakeRect("Body", root);
            var bodyGroup = body.gameObject.AddComponent<VerticalLayoutGroup>();
            bodyGroup.spacing = 3f;
            bodyGroup.childControlWidth = true;
            bodyGroup.childControlHeight = true;
            bodyGroup.childForceExpandWidth = true;
            bodyGroup.childForceExpandHeight = false;
            bodyGroup.padding = new RectOffset(14, 14, 4, 4);
            body.gameObject.SetActive(false);
            return root.gameObject;
        }

        private static GameObject MakeSystemTemplate(string name, Transform parent)
        {
            var txt = MakeText(name, parent, "", 15f, TextDim, TextAnchor.MiddleCenter);
            return txt.gameObject;
        }

        private static GameObject MakeDividerTemplate(string name, Transform parent)
        {
            var txt = MakeText(name, parent, "", 13f, new Color(0.45f, 0.47f, 0.55f, 0.9f), TextAnchor.MiddleCenter);
            return txt.gameObject;
        }

        // ------------------------------------------------------------------
        // 工具
        // ------------------------------------------------------------------

        private static void Stretch(RectTransform rt, float l, float b, float r, float t)
        {
            rt.anchorMin = Vector2.zero;
            rt.anchorMax = Vector2.one;
            rt.offsetMin = new Vector2(l, b);
            rt.offsetMax = new Vector2(-r, -t);
            rt.pivot = new Vector2(0.5f, 0.5f);
        }

        /// <summary>递归设置整个子树到指定层（Canvas 渲染依赖 layer，漏设会不渲染）</summary>
        private static void SetLayerRecursive(Transform t, int layer)
        {
            t.gameObject.layer = layer;
            for (int i = 0; i < t.childCount; i++)
                SetLayerRecursive(t.GetChild(i), layer);
        }

        /// <summary>
        /// 字体三级兜底：① 捞游戏现成 Text 的字体（自带 CJK，最优）→ ② 动态 OS 字体 → ③ 内置 LegacyRuntime.ttf
        /// 结果缓存为静态字段，避免每帧/每模板克隆都重新查找。
        /// </summary>
        private static Font _sharedFont;

        public static Font ResolveSharedFont()
        {
            if (_sharedFont != null) return _sharedFont;
            _sharedFont = ResolveCjkFont();
            return _sharedFont;
        }

        private static Font ResolveCjkFont()
        {
            try
            {
                var existing = UnityEngine.Object.FindObjectOfType<Text>();
                if (existing != null && existing.font != null)
                    return existing.font;
            }
            catch { }
            try
            {
                var osFont = Font.CreateDynamicFontFromOSFont("SimHei", 20);
                if (osFont != null) return osFont;
            }
            catch { }
            var legacy = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            if (legacy != null) return legacy;
            return Resources.GetBuiltinResource<Font>("Arial.ttf");
        }

        /// <summary>生成九宫格精灵：角部透明（圆角）、手工 border，Image.Sliced 使用（配置面板等兄弟构建器共用）</summary>
        internal static Sprite MakeSlicedSprite(Color fill, int size = 12, int border = 4)
        {
            // 防呆：不透明像素只存在于 border..size-border 的"十字"带，border ≥ size/2 时该带为空
            // → 整张纹理全被判成圆角 → 精灵全透明（通讯录透明背景/白卡隐形的根因）。钳到留 2px 十字。
            if (border > size / 2 - 1) border = size / 2 - 1;
            if (border < 1) border = 1;
            var tex = new Texture2D(size, size, TextureFormat.RGBA32, false);
            tex.filterMode = FilterMode.Bilinear;
            tex.wrapMode = TextureWrapMode.Clamp;
            var colors = new Color[size * size];
            for (int y = 0; y < size; y++)
            {
                for (int x = 0; x < size; x++)
                {
                    bool inCorner = (x < border && y < border) || (x >= size - border && y < border) ||
                                    (x < border && y >= size - border) || (x >= size - border && y >= size - border);
                    colors[y * size + x] = inCorner ? Color.clear : fill;
                }
            }
            tex.SetPixels(colors);
            tex.Apply(false, true);
            return Sprite.Create(tex, new Rect(0f, 0f, size, size), new Vector2(0.5f, 0.5f),
                                 100f, 0, SpriteMeshType.FullRect, new Vector4(border, border, border, border));
        }

        /// <summary>
        /// 生成抗锯齿正圆精灵（头像 / 圆形按钮专用）。
        /// 九宫格圆角矩形拼小圆：切角硬边 + 多边形感，圆形一律走本方法——
        /// 按像素到圆心距离算 alpha，边缘 softness 像素平滑过渡。
        /// </summary>
        internal static Sprite MakeCircleSprite(Color fill, int size = 64, int softness = 2)
        {
            if (softness < 1) softness = 1;
            var tex = new Texture2D(size, size, TextureFormat.RGBA32, false);
            tex.filterMode = FilterMode.Bilinear;
            tex.wrapMode = TextureWrapMode.Clamp;
            var colors = new Color[size * size];
            float r = size * 0.5f;
            float inner = r - softness;
            for (int y = 0; y < size; y++)
            {
                for (int x = 0; x < size; x++)
                {
                    float dx = x + 0.5f - r;
                    float dy = y + 0.5f - r;
                    float d = Mathf.Sqrt(dx * dx + dy * dy);
                    float a = Mathf.Clamp01((inner - d) / softness);
                    colors[y * size + x] = new Color(fill.r, fill.g, fill.b, fill.a * a);
                }
            }
            tex.SetPixels(colors);
            tex.Apply(false, true);
            return Sprite.Create(tex, new Rect(0f, 0f, size, size), new Vector2(0.5f, 0.5f), 100f);
        }

        /// <summary>防御：场景没有 EventSystem 时补建一个（UI 点击/输入需要）</summary>
        private static void EnsureEventSystem(Transform parent)
        {
            if (UnityEngine.Object.FindObjectOfType<UnityEngine.EventSystems.EventSystem>() != null)
                return;
            var go = new GameObject("AgentLoopChatEventSystem");
            go.transform.SetParent(parent, false);
            go.AddComponent<UnityEngine.EventSystems.EventSystem>();
            go.AddComponent<UnityEngine.EventSystems.StandaloneInputModule>();
        }
    }

    /// <summary>
    /// 按钮事件工具 —— 挂 UnityAction 的回调（ClickCatcher 退役后全项目唯一挂点）。
    ///
    /// 两环境编译分流（同一源码同时喂主工程/冒烟 + Unity 预览工程）：
    ///   - MELONLOADER（MelonLoader 0.5 / Unhollower）：UnityAction 是带 op_Implicit 的 class，
    ///     须「三步写法」 Action → UnityAction 隐式转换 → AddListener；直接 lambda 会 CS1660。
    ///   - 标准 Unity 编辑器：UnityAction 是真 C# delegate，System.Action 无隐式转换过去，
    ///     须 lambda／方法组直接转；三步写法反而 CS0029。
    /// 回调异常本地兜底日志（不依赖 ModMain）。
    /// </summary>
    public static class ClickUtils
    {
        /// <summary>先清空再挂（避免重复注入/历史回调残留）；action 为 null 只清空</summary>
        public static void Attach(Button button, System.Action action)
        {
            if (button == null) return;
            var self = button.onClick;
            self.RemoveAllListeners();
            if (action == null) return;
#if MELONLOADER
            // 三步写法：① lambda → Action  ② Action → UnityAction(隐式 op_Implicit)  ③ AddListener
            System.Action a = () => { try { action(); } catch (Exception e) { Log(e); } };
            UnityAction ua = a;
            self.AddListener(ua);
#else
            // 标准 Unity：lambda 直接转 UnityAction 委托
            self.AddListener(() => { try { action(); } catch (Exception e) { Log(e); } });
#endif
        }

        /// <summary>追加挂接（不清空既有监听，供克隆按钮保留游戏原回调的场景）</summary>
        public static void Append(Button button, System.Action action)
        {
            if (button == null || action == null) return;
#if MELONLOADER
            System.Action a = () => { try { action(); } catch (Exception e) { Log(e); } };
            UnityAction ua = a;
            button.onClick.AddListener(ua);
#else
            button.onClick.AddListener(() => { try { action(); } catch (Exception e) { Log(e); } });
#endif
        }

        /// <summary>本地日志：主工程与冒烟/Unity 预览工程均可链接编译的公共实现</summary>
        private static void Log(Exception e)
        {
            try { UnityEngine.Debug.Log("[ClickUtils] " + e.Message); }
            catch { try { Console.WriteLine("[ClickUtils] " + e.Message); } catch { } }
        }
    }

    /// <summary>
    /// 取 RectTransform 的唯一正确姿势（IL2CPP 铁律，勿绕过）。
    ///
    /// **禁止 `t.transform as RectTransform`**：本环境（MelonLoader + Il2CppInterop）里
    /// `GameObject.transform` 返回的托管包装器静态类型就是 `Transform`，向下转型
    /// `as RectTransform` **静默返回 null、不抛异常** —— 09-13 两次实机事故都栽在这：
    ///   ① HUD「传」钮未读红点从建不出来（`MapMainContactButton.BuildBadge` 第一行就退）；
    ///   ② 配置面板悬停气泡**一个都不出**（`HoverTip.Setup` 的 `_canvasRt` 恒 null →
    ///      `Update()` 第一行 `if (_canvasRt == null) return;` → 检测代码从未执行）。
    /// 正确取法 = `GetComponent&lt;RectTransform&gt;()`（原生查询，返回类型正确的包装器）。
    /// 本类即该铁律的唯一收口点；新代码一律走 `UiRects.Of(...)`。
    /// </summary>
    public static class UiRects
    {
        /// <summary>Transform → RectTransform（取不到返回 null）。</summary>
        public static RectTransform Of(Transform t)
        {
            if (t == null) return null;
            try { var rt = t.GetComponent<RectTransform>(); if (rt != null) return rt; } catch { }
            try { return t as RectTransform; } catch { }   // 兜底：别的运行环境下转型有效
            return null;
        }

        /// <summary>组件所在父节点 → RectTransform（登记整行悬停区之类用）。</summary>
        public static RectTransform ParentOf(Component c)
        {
            if (c == null) return null;
            try { return Of(c.transform.parent); } catch { return null; }
        }

        /// <summary>组件自身 → RectTransform。</summary>
        public static RectTransform SelfOf(Component c)
        {
            if (c == null) return null;
            try { return Of(c.transform); } catch { return null; }
        }
    }
}