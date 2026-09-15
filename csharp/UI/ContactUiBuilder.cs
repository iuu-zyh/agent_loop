/// <summary>
/// 传音簿 UI 构建器 —— 对标通讯录参考图：白主题 · 淡雅清新 · 青绿点缀
/// 窄竖条 380×620：近白页面 + 顶栏（居中标题 / 🔍 青绿 / ✕ 灰）+ 胶囊 Tab（好友字母序/最近时间序）
/// + 🔍点开才占位的搜索覆盖层 + 投影白卡列表（每行：正圆头像占位 / 深色名字 / 灰副标题 / 右侧 = 未读红点(默认隐藏) + ✓ 青绿正圆钮）
/// 层次设计：页面 #F5F7F8 → 白卡靠 2~3px 投影边浮起（告别惨白一片），文字两级深浅保证可读。
/// 圆形一律走 MakeCircleSprite（真圆+抗锯齿）；矩形走 MakeSlicedSprite（border 必须 < size/2，helper 已钳制防呆）。
/// </summary>
using UnityEngine;
using UnityEngine.UI;

namespace AgentLoopBridge
{
    public static class ContactUiBuilder
    {
        private const float PANEL_W = 380f;
        private const float PANEL_H = 620f;
        private const int UI_LAYER = 5;

        // ---- 参考图配色（白主题 + 灰阶层次 + 青绿点缀）----
        private static readonly Color PageBg = new Color(0.961f, 0.969f, 0.973f, 1f);    // #F5F7F8 页面近白
        private static readonly Color CardBg = Color.white;                               // 卡片纯白
        private static readonly Color CardShadow = new Color(0.48f, 0.55f, 0.61f, 0.34f); // 白卡投影（淡灰蓝）
        private static readonly Color TrackBg = new Color(0.925f, 0.937f, 0.949f, 1f);   // #ECEFF2 Tab胶囊轨道
        private static readonly Color LineColor = new Color(0.914f, 0.929f, 0.941f, 1f); // #E9EDF0 细分隔线
        private static readonly Color TextDark = new Color(0.141f, 0.157f, 0.176f, 1f);  // #242830 主文字
        private static readonly Color TextSub = new Color(0.447f, 0.478f, 0.514f, 1f);   // #727A83 副标题
        private static readonly Color TextFaint = new Color(0.604f, 0.631f, 0.663f, 1f); // #9AA1A9 弱文字/占位
        private static readonly Color AccentGreen = new Color(0f, 0.788f, 0.682f, 1f);   // #00C9AE 主题点缀
        private static readonly Color UnreadRed = UnreadStore.UnreadRed;                 // #FF3B30 未读红点（与 HUD 角标/横幅同源）
        private static readonly Color AvatarBg = new Color(0.890f, 0.929f, 0.918f, 1f);  // #E3EDEA 头像占位淡青

        public static ContactPanelRefs Build(Transform parent)
        {
            var refs = new ContactPanelRefs();
            int layer = LayerMask.NameToLayer("UI");
            if (layer < 0) layer = UI_LAYER;

            // ① Canvas
            var canvasGo = new GameObject("UIContactAi");
            if (parent != null) canvasGo.transform.SetParent(parent, false);
            canvasGo.layer = layer;
            canvasGo.AddComponent<RectTransform>();
            var canvas = canvasGo.AddComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            canvas.sortingOrder = 3050;
            var scaler = canvasGo.AddComponent<CanvasScaler>();
            scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            scaler.referenceResolution = new Vector2(1920f, 1080f);
            scaler.matchWidthOrHeight = 0.5f;
            canvasGo.AddComponent<GraphicRaycaster>();
            refs.Root = canvasGo;
            EnsureEventSystem(parent);

            // ② Dim 全屏遮罩（预览也能看见，游戏里盖住世界）
            var dimRt = MakeImage("Dim", canvasGo.transform, ChatUiBuilder.MakeSlicedSprite(new Color(0f,0f,0f,0.45f), 8, 2), new Color(0f,0f,0f,0.45f), Image.Type.Sliced);
            StretchFill(dimRt, 0f,0f,0f,0f);
            SetLayerRecursive(dimRt, layer);
            dimRt.gameObject.SetActive(false);
            refs.Dim = dimRt.gameObject;

            // ② BG 窄竖条 近白页面（白卡靠投影区分层次）
            var bgRt = MakeImage("BG", canvasGo.transform, ChatUiBuilder.MakeSlicedSprite(PageBg, 18, 8), PageBg, Image.Type.Sliced);
            bgRt.anchorMin = bgRt.anchorMax = bgRt.pivot = new Vector2(0.5f, 0.5f);
            bgRt.anchoredPosition = Vector2.zero;
            bgRt.sizeDelta = new Vector2(PANEL_W, PANEL_H);
            refs.Window = bgRt;
            SetLayerRecursive(bgRt, layer);
            bgRt.gameObject.SetActive(false);

            // ③ 顶栏：不做整条色带（页面本身近白），居中深色标题 + 标题下细分隔线 + 右上 ✕ / 🔍
            var titleLabel = MakeText("TitleLabel", bgRt, "通讯录", 17f, TextDark, TextAnchor.MiddleCenter);
            titleLabel.fontStyle = FontStyle.Bold;
            var tRt = AsRt(titleLabel);
            tRt.anchorMin = new Vector2(0f, 1f); tRt.anchorMax = new Vector2(1f, 1f);
            tRt.pivot = new Vector2(0.5f, 1f);
            tRt.offsetMin = new Vector2(60f, -48f); tRt.offsetMax = new Vector2(-60f, -8f);

            var lineRt = MakeImage("TitleLine", bgRt, ChatUiBuilder.MakeSlicedSprite(LineColor, 4, 1), LineColor, Image.Type.Sliced);
            lineRt.anchorMin = new Vector2(0f, 1f); lineRt.anchorMax = new Vector2(1f, 1f);
            lineRt.pivot = new Vector2(0.5f, 1f);
            lineRt.offsetMin = new Vector2(16f, -54f); lineRt.offsetMax = new Vector2(-16f, -53f);

            refs.CloseButton = MakeClickable("CloseBtn", bgRt, "✕", 15f);
            var closeRt = refs.CloseButton.GetComponent<RectTransform>();
            closeRt.anchorMin = closeRt.anchorMax = closeRt.pivot = new Vector2(1f, 1f);
            closeRt.sizeDelta = new Vector2(44f, 46f);
            closeRt.anchoredPosition = new Vector2(-4f, -6f);
            var closeTxt = refs.CloseButton.GetComponentInChildren<Text>();
            if (closeTxt != null) { closeTxt.color = TextFaint; closeTxt.fontSize = 15; }

            refs.SearchIconButton = MakeClickable("SearchIconBtn", bgRt, "🔍", 15f);
            var siBtnRt = refs.SearchIconButton.GetComponent<RectTransform>();
            siBtnRt.anchorMin = siBtnRt.anchorMax = siBtnRt.pivot = new Vector2(1f, 1f);
            siBtnRt.sizeDelta = new Vector2(44f, 46f);
            siBtnRt.anchoredPosition = new Vector2(-48f, -6f);
            var siTxt = refs.SearchIconButton.GetComponentInChildren<Text>();
            if (siTxt != null) siTxt.color = AccentGreen;

            // ④ Tab胶囊 34高 宽240 居中 顶距62（选中=白色浮起胶囊+青绿粗体，未选=灰字）
            var tabsRt = MakeImage("Tabs", bgRt, ChatUiBuilder.MakeSlicedSprite(TrackBg, 18, 8), TrackBg, Image.Type.Sliced);
            tabsRt.anchorMin = tabsRt.anchorMax = tabsRt.pivot = new Vector2(0.5f, 1f);
            tabsRt.sizeDelta = new Vector2(240f, 34f);
            tabsRt.anchoredPosition = new Vector2(0f, -62f);

            refs.TabFriendButton = MakeTabPill("TabFriendBtn", tabsRt, "好友", true);
            var tfRt = refs.TabFriendButton.GetComponent<RectTransform>();
            tfRt.anchorMin = new Vector2(0f, 0f); tfRt.anchorMax = new Vector2(0.5f, 1f);
            tfRt.offsetMin = new Vector2(3f, 3f); tfRt.offsetMax = new Vector2(-2f, -3f);

            refs.TabRecentButton = MakeTabPill("TabRecentBtn", tabsRt, "最近", false);
            var trRt = refs.TabRecentButton.GetComponent<RectTransform>();
            trRt.anchorMin = new Vector2(0.5f, 0f); trRt.anchorMax = new Vector2(1f, 1f);
            trRt.offsetMin = new Vector2(2f, 3f); trRt.offsetMax = new Vector2(-3f, -3f);

            // ⑤ 搜索覆盖层（默认隐藏，点🔍展开，白色圆角卡盖住Tab区）
            var searchOverlayRt = MakeImage("SearchOverlay", bgRt, ChatUiBuilder.MakeSlicedSprite(CardBg, 14, 6), CardBg, Image.Type.Sliced);
            searchOverlayRt.anchorMin = new Vector2(0f, 1f); searchOverlayRt.anchorMax = new Vector2(1f, 1f);
            searchOverlayRt.pivot = new Vector2(0.5f, 1f);
            searchOverlayRt.offsetMin = new Vector2(14f, -102f);
            searchOverlayRt.offsetMax = new Vector2(-14f, -58f);
            searchOverlayRt.gameObject.SetActive(false);
            refs.SearchOverlay = searchOverlayRt.gameObject;

            refs.SearchInput = MakeInputField("SearchInput", searchOverlayRt.transform, "搜名字/宗门…", 13f, TrackBg);
            var inpRt = AsRt(refs.SearchInput);
            inpRt.anchorMin = new Vector2(0f, 0f); inpRt.anchorMax = new Vector2(1f, 1f);
            inpRt.offsetMin = new Vector2(7f, 7f); inpRt.offsetMax = new Vector2(-60f, -7f);

            refs.SearchCancelButton = MakeClickable("SearchCancelBtn", searchOverlayRt.transform, "取消", 13f);
            var cancelRt = refs.SearchCancelButton.GetComponent<RectTransform>();
            cancelRt.anchorMin = new Vector2(1f, 0f); cancelRt.anchorMax = new Vector2(1f, 1f);
            cancelRt.pivot = new Vector2(1f, 0.5f); cancelRt.sizeDelta = new Vector2(52f, 0f);
            cancelRt.anchoredPosition = Vector2.zero;
            var cancelTxt = refs.SearchCancelButton.GetComponentInChildren<Text>();
            if (cancelTxt != null) cancelTxt.color = AccentGreen;

            // ⑥ 列表 顶距104（标题52+线+Tab+间隙） 底距34
            var scrollRt = MakeImage("ContactScroll", bgRt, ChatUiBuilder.MakeSlicedSprite(PageBg, 10, 4), PageBg, Image.Type.Sliced);
            scrollRt.anchorMin = new Vector2(0f, 0f); scrollRt.anchorMax = new Vector2(1f, 1f);
            scrollRt.offsetMin = new Vector2(0f, 34f); scrollRt.offsetMax = new Vector2(0f, -104f);
            scrollRt.gameObject.GetComponent<Image>().raycastTarget = false;
            var scroll = scrollRt.gameObject.AddComponent<ScrollRect>();
            scroll.horizontal = false; scroll.vertical = true;
            scroll.movementType = ScrollRect.MovementType.Clamped;
            scroll.scrollSensitivity = 28f;
            refs.ContactScroll = scroll;

            var viewportRt = MakeRect("Viewport", scrollRt);
            StretchFill(viewportRt, 0f, 0f, 0f, 0f);
            viewportRt.gameObject.AddComponent<RectMask2D>();
            viewportRt.gameObject.AddComponent<CanvasRenderer>();
            scroll.viewport = viewportRt;   // 显式指定视口（不指定时 Unity 回退用 ScrollRect 自身矩形，行为等价但非教科书形态）

            var contentRt = MakeRect("Content", viewportRt);
            contentRt.anchorMin = new Vector2(0f, 1f); contentRt.anchorMax = Vector2.one;
            contentRt.pivot = new Vector2(0.5f, 1f);
            contentRt.sizeDelta = Vector2.zero; contentRt.anchoredPosition = Vector2.zero;
            var vlg = contentRt.gameObject.AddComponent<VerticalLayoutGroup>();
            vlg.spacing = 10f; vlg.padding = new RectOffset(12, 12, 10, 10);
            vlg.childControlWidth = true; vlg.childControlHeight = true;
            vlg.childForceExpandWidth = true; vlg.childForceExpandHeight = false;
            var fitter = contentRt.gameObject.AddComponent<ContentSizeFitter>();
            fitter.verticalFit = ContentSizeFitter.FitMode.PreferredSize;
            scroll.content = contentRt;
            refs.ContactContent = contentRt;

            // ⑦ 行模板 投影白卡（含头像占位）
            refs.ContactItemTemplate = BuildContactItem(bgRt);
            refs.ContactItemTemplate.SetActive(false);

            // ⑧ 状态条
            var status = MakeText("Status", bgRt, "好友按字母排序 · 最近按时间倒序", 11f, TextFaint, TextAnchor.MiddleCenter);
            var stRt = AsRt(status);
            stRt.anchorMin = new Vector2(0f, 0f); stRt.anchorMax = Vector2.one;
            stRt.offsetMin = new Vector2(12f, 9f); stRt.offsetMax = new Vector2(-12f, 21f);
            refs.StatusLabel = status;

            // ⑨ 未读传音横幅（Canvas 顶部居中，默认隐藏；深底白字 + 红点装饰，整条可点开最新未读对话）
            var bannerRt = MakeImage("UnreadBanner", canvasGo.transform,
                ChatUiBuilder.MakeSlicedSprite(new Color(0.08f, 0.09f, 0.11f, 0.94f), 12, 5),
                Color.white, Image.Type.Sliced);
            bannerRt.anchorMin = bannerRt.anchorMax = bannerRt.pivot = new Vector2(0.5f, 1f);
            bannerRt.sizeDelta = new Vector2(360f, 46f);
            bannerRt.anchoredPosition = new Vector2(0f, -14f);
            var bannerBtn = bannerRt.gameObject.AddComponent<Button>();
            bannerBtn.targetGraphic = bannerRt.GetComponent<Image>();
            var bannerDot = MakeImage("BannerDot", bannerRt.transform, ChatUiBuilder.MakeCircleSprite(UnreadRed, 32), Color.white, Image.Type.Simple);
            bannerDot.anchorMin = new Vector2(0f, 0.5f); bannerDot.anchorMax = new Vector2(0f, 0.5f);
            bannerDot.pivot = new Vector2(0.5f, 0.5f);
            bannerDot.sizeDelta = new Vector2(10f, 10f);
            bannerDot.anchoredPosition = new Vector2(18f, 0f);
            bannerDot.gameObject.GetComponent<Image>().raycastTarget = false;
            var bannerTxt = MakeText("BannerText", bannerRt.transform, "📨 有新的传音", 13f, Color.white, TextAnchor.MiddleLeft);
            var btRt = AsRt(bannerTxt);
            btRt.anchorMin = Vector2.zero; btRt.anchorMax = Vector2.one;
            btRt.offsetMin = new Vector2(34f, 0f); btRt.offsetMax = new Vector2(-12f, 0f);
            bannerRt.gameObject.SetActive(false);
            refs.UnreadBanner = bannerRt.gameObject;
            refs.UnreadBannerText = bannerTxt;

            return refs;
        }

        // 行 = 投影层(外) + 白卡(内缩2~3px露出投影边) + 头像/文字/双圆按钮盖在卡上。
        // 布局器只布局 row 根；白卡作为第一个子节点渲染在其余兄弟之下 = 充当卡片底。
        private static GameObject BuildContactItem(Transform parent)
        {
            var rowRt = MakeImage("ContactItem", parent, ChatUiBuilder.MakeSlicedSprite(CardShadow, 20, 9), CardShadow, Image.Type.Sliced);
            rowRt.sizeDelta = new Vector2(0f, 70f);
            var le = rowRt.gameObject.AddComponent<LayoutElement>();
            le.preferredHeight = 70f; le.flexibleWidth = 1f;

            var cardRt = MakeImage("Card", rowRt.transform, ChatUiBuilder.MakeSlicedSprite(CardBg, 22, 10), Color.white, Image.Type.Sliced);
            StretchFill(cardRt, 2f, 3f, 2f, 3f);

            // 头像 46 正圆占位（真圆精灵；不拦截点击，整行按钮可点）。
            // ：通讯录不再渲染立绘（UnitPortrait 缓存层整体移除），头像就是淡青占位圆。
            var avatarRt = MakeImage("Avatar", rowRt.transform, ChatUiBuilder.MakeCircleSprite(AvatarBg, 64), Color.white, Image.Type.Simple);
            avatarRt.anchorMin = avatarRt.anchorMax = avatarRt.pivot = new Vector2(0f, 0.5f);
            avatarRt.sizeDelta = new Vector2(46f, 46f);
            avatarRt.anchoredPosition = new Vector2(14f, 0f);
            avatarRt.gameObject.GetComponent<Image>().raycastTarget = false;

            // 名字 15 粗 深色（参考图主文字，保证清晰）
            var nameTxt = MakeText("Name", rowRt.transform, "林婉清", 15f, TextDark, TextAnchor.MiddleLeft);
            nameTxt.fontStyle = FontStyle.Bold;
            var nRt = AsRt(nameTxt);
            nRt.anchorMin = new Vector2(0f, 0.5f); nRt.anchorMax = new Vector2(1f, 1f);
            nRt.offsetMin = new Vector2(74f, -32f); nRt.offsetMax = new Vector2(-76f, 0f);

            // 副标题 12 中灰（宗门·境界 · 状态）
            var subTxt = MakeText("Sub", rowRt.transform, "清虚宗·金丹 · 在线", 12f, TextSub, TextAnchor.MiddleLeft);
            var sRt = AsRt(subTxt);
            sRt.anchorMin = new Vector2(0f, 0f); sRt.anchorMax = new Vector2(1f, 0.5f);
            sRt.offsetMin = new Vector2(74f, 0f); sRt.offsetMax = new Vector2(-76f, -2f);

            // 未读红点（✓ 左侧）：有未读消息时由列表逻辑点亮；模板默认隐藏（节点名 UnreadDot）
            var dotRt = MakeImage("UnreadDot", rowRt.transform, ChatUiBuilder.MakeCircleSprite(UnreadRed, 32), Color.white, Image.Type.Simple);
            dotRt.anchorMin = dotRt.anchorMax = dotRt.pivot = new Vector2(1f, 0.5f);
            dotRt.sizeDelta = new Vector2(12f, 12f);
            dotRt.anchoredPosition = new Vector2(-54f, 0f);
            dotRt.gameObject.GetComponent<Image>().raycastTarget = false;
            dotRt.gameObject.SetActive(false);

            // 右侧 ✓ 主题青绿正圆（白粗对勾）—— 行内只保留这一个按钮（✕ 已按需求移除）
            var chatRt = MakeImage("ChatBtn", rowRt.transform, ChatUiBuilder.MakeCircleSprite(AccentGreen, 48), Color.white, Image.Type.Simple);
            chatRt.anchorMin = chatRt.anchorMax = chatRt.pivot = new Vector2(1f, 0.5f);
            chatRt.sizeDelta = new Vector2(32f, 32f);
            chatRt.anchoredPosition = new Vector2(-13f, 0f);
            chatRt.gameObject.AddComponent<Button>().targetGraphic = chatRt.GetComponent<Image>();
            var check = MakeText("Icon", chatRt.transform, "✓", 17f, Color.white, TextAnchor.MiddleCenter);
            check.fontStyle = FontStyle.Bold;
            StretchFill(AsRt(check), 0f,0f,0f,0f);

            // 整行可点（悬停/按下的变色目标 = 白卡）
            var rowBtn = rowRt.gameObject.AddComponent<Button>();
            rowBtn.targetGraphic = cardRt.GetComponent<Image>();
            return rowRt.gameObject;
        }

        private static GameObject MakeTabPill(string name, Transform parent, string label, bool selected)
        {
            // 精灵恒为白色不透明，选中态改由 Image.color 的 alpha 控制（0=未选透明 / 1=选中白底）——
            // Presenter 运行时切 Tab 只换色不换精灵。
            var rt = MakeImage(name, parent, ChatUiBuilder.MakeSlicedSprite(Color.white, 18, 8),
                               selected ? Color.white : new Color(1f, 1f, 1f, 0f), Image.Type.Sliced);
            rt.gameObject.AddComponent<Button>().targetGraphic = rt.GetComponent<Image>();
            var txt = MakeText("Label", rt, label, 14f, selected ? AccentGreen : TextFaint, TextAnchor.MiddleCenter);
            if (selected) txt.fontStyle = FontStyle.Bold;
            StretchFill(AsRt(txt),0f,0f,0f,0f);
            return rt.gameObject;
        }

        // ---- 工厂 ----
        private static RectTransform MakeRect(string name, Transform parent)
        {
            var go = new GameObject(name);
            go.transform.SetParent(parent,false);
            return go.AddComponent<RectTransform>();
        }
        private static RectTransform MakeImage(string name, Transform parent, Sprite sprite, Color color, Image.Type type)
        {
            var go = new GameObject(name);
            go.transform.SetParent(parent,false);
            var rt = go.AddComponent<RectTransform>();
            go.AddComponent<CanvasRenderer>();
            var img = go.AddComponent<Image>();
            img.sprite=sprite; img.type=type; img.color=color; img.raycastTarget=true;
            return rt;
        }
        private static Text MakeText(string name, Transform parent, string text, float size, Color color, TextAnchor align)
        {
            var go = new GameObject(name);
            go.transform.SetParent(parent,false);
            go.AddComponent<RectTransform>();
            go.AddComponent<CanvasRenderer>();
            var txt = go.AddComponent<Text>();
            txt.text=text??""; txt.fontSize=Mathf.RoundToInt(size); txt.color=color; txt.alignment=align;
            txt.font=ChatUiBuilder.ResolveSharedFont(); txt.raycastTarget=false;
            txt.horizontalOverflow=HorizontalWrapMode.Wrap; txt.verticalOverflow=VerticalWrapMode.Overflow;
            return txt;
        }
        private static GameObject MakeClickable(string name, Transform parent, string label, float fontSize)
        {
            var rt = MakeImage(name, parent, ChatUiBuilder.MakeSlicedSprite(new Color(0,0,0,0),8,3), Color.white, Image.Type.Sliced);
            rt.gameObject.AddComponent<Button>().targetGraphic=rt.GetComponent<Image>();
            var txt = MakeText("Label", rt, label, fontSize, TextDark, TextAnchor.MiddleCenter);
            StretchFill(AsRt(txt),4f,0f,4f,0f);
            return rt.gameObject;
        }
        private static InputField MakeInputField(string name, Transform parent, string placeholder, float fontSize, Color? bg = null)
        {
            var bgc = bg ?? Color.white;
            var rt = MakeImage(name, parent, ChatUiBuilder.MakeSlicedSprite(bgc, 12,5), bgc, Image.Type.Sliced);
            var txt = MakeText("Text", rt, "", fontSize, TextDark, TextAnchor.MiddleLeft);
            StretchFill(AsRt(txt),12f,4f,12f,4f);
            var ph = MakeText("Placeholder", rt, placeholder, fontSize, TextFaint, TextAnchor.MiddleLeft);
            StretchFill(AsRt(ph),12f,4f,12f,4f);
            var field = rt.gameObject.AddComponent<InputField>();
            field.textComponent=txt; field.placeholder=ph;
            return field;
        }
        private static RectTransform AsRt(Component c)=> c!=null?c.GetComponent<RectTransform>():null;
        private static void StretchFill(RectTransform rt,float l,float b,float r,float t)
        {
            rt.anchorMin=Vector2.zero; rt.anchorMax=Vector2.one;
            rt.offsetMin=new Vector2(l,b); rt.offsetMax=new Vector2(-r,-t); rt.pivot=new Vector2(0.5f,0.5f);
        }
        private static void SetLayerRecursive(Transform t,int layer){ t.gameObject.layer=layer; for(int i=0;i<t.childCount;i++) SetLayerRecursive(t.GetChild(i),layer); }
        private static void EnsureEventSystem(Transform parent)
        {
            if(UnityEngine.Object.FindObjectOfType<UnityEngine.EventSystems.EventSystem>()!=null) return;
            var go=new GameObject("ContactEventSystem");
            if(parent!=null) go.transform.SetParent(parent,false);
            go.AddComponent<UnityEngine.EventSystems.EventSystem>();
            go.AddComponent<UnityEngine.EventSystems.StandaloneInputModule>();
        }
    }
}
