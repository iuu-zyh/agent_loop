/// <summary>
/// 配置 UI 静态构建器 —— 用代码搭出整棵 UGUI 节点树，返回 ConfigPanelRefs 契约。
///
/// 各司其职（仿 ChatUiBuilder）：本文件只负责「搭节点/贴样式/藏模板」。不订阅事件、
/// 不碰 WS 协议（那是 ConfigPresenter 的事）。配色/字号集中此文件，与对话 UI 同深色系。
///
/// 双环境共用：MelonLoader 主工程运行时 Build(g.root) 直接可用；
/// Unity 编辑器（BuildConfigPrefabEditor）在编辑模式调 Build 后 SaveAsPrefabAsset
/// 存 UIConfigAi.prefab——「代码就是 UI 的源文件」，改这里重新生成即可。
///
/// IL2CPP 约定同 ChatUiBuilder：取组件一律 GetComponent&lt;T&gt;()；按钮回调经 ClickUtils 三步写法。
///
/// 节点树（BG 命名契约对齐 UIChatAi，AB 版 AbConfigPanel.CollectRefs 按路径 Find）：
/// UIConfigAi(Canvas) └ BG(窗口 760×640) ├ Title(标题+关闭) ├ Tabs(大模型/提示词)
///   ├ PageLlm └ Scroll └ Viewport(RectMask2D) └ Content(VLG + ContentSizeFitter)
///   │            4 个 GroupHeader_* 组标题 + 18 个参数行 + SaveLlmBtn（整页可滚，见 ⑤）
///   ├ PagePrompt(文件列表+多行编辑+新建人设) └ Status(状态条)
///
/// 路径契约（09-13 分组改版后行节点全部下沉一层；改名必须与
/// AbConfigPanel.cs CollectRefs 的常量、scripts/dev/prefab_patch_config_groups.py 三处同步）：
///   BG/PageLlm/Scroll/Viewport/Content/&lt;Row&gt;/Input|ToggleBg
/// </summary>
using UnityEngine;
using UnityEngine.UI;

namespace AgentLoopBridge
{
    public static class ConfigUiBuilder
    {
        // ---- 版式常量 ----
        private const float PANEL_W = 760f;
        private const float PANEL_H = 640f;
        private const int UI_LAYER = 5;

        // ---- 配色（与 ChatUiBuilder 同深色系）----
        private static readonly Color PanelBg = new Color(0.043f, 0.043f, 0.075f, 0.95f);
        private static readonly Color CtrlBg = new Color(0.16f, 0.18f, 0.26f, 1f);
        private static readonly Color CtrlBgActive = new Color(0.24f, 0.27f, 0.38f, 1f);
        private static readonly Color TextLight = new Color(0.94f, 0.95f, 0.98f, 1f);
        private static readonly Color TextDim = new Color(0.60f, 0.62f, 0.70f, 1f);
        private static readonly Color TextOk = new Color(0.55f, 0.85f, 0.60f, 1f);
        private static readonly Color LabelColor = new Color(0.08f, 0.08f, 0.08f, 1f); // 羊皮纸上左侧表单标签：纯黑加粗

        /// <summary>搭配置 UI 整树。parent 通常是 g.root（运行时）或 null（编辑器生成预制件）。</summary>
        public static ConfigPanelRefs Build(Transform parent)
        {
            var refs = new ConfigPanelRefs();

            int layer = LayerMask.NameToLayer("UI");
            if (layer < 0) layer = UI_LAYER;

            // ---------- ① Canvas 根 ----------
            var canvasGo = new GameObject("UIConfigAi");
            if (parent != null) canvasGo.transform.SetParent(parent, false);
            canvasGo.layer = layer;
            canvasGo.AddComponent<RectTransform>();
            var canvas = canvasGo.AddComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            canvas.sortingOrder = 3100; // 压过对话 UI(3000)
            var scaler = canvasGo.AddComponent<CanvasScaler>();
            scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            scaler.referenceResolution = new Vector2(1920f, 1080f);
            scaler.matchWidthOrHeight = 0.5f;
            canvasGo.AddComponent<GraphicRaycaster>();
            refs.Root = canvasGo;
            EnsureEventSystem(parent);

            // ---------- ② BG 主面板（居中，默认隐藏，Presenter 开关） ----------
            var bgRt = MakeImage("BG", canvasGo.transform, ChatUiBuilder.MakeSlicedSprite(PanelBg, 12, 4),
                                 Color.white, Image.Type.Sliced);
            bgRt.anchorMin = bgRt.anchorMax = bgRt.pivot = new Vector2(0.5f, 0.5f);
            bgRt.anchoredPosition = Vector2.zero;
            bgRt.sizeDelta = new Vector2(PANEL_W, PANEL_H);
            refs.Window = bgRt;
            SetLayerRecursive(bgRt, layer);
            bgRt.gameObject.SetActive(false);

            // ---------- ③ 标题栏 ----------
            var titleRt = MakeRect("Title", bgRt);
            StretchTopBar(titleRt, 12f, 12f, 0f, 42f);

            var titleLabel = MakeText("TitleLabel", titleRt, "AI 设置", 19f, TextLight, TextAnchor.MiddleLeft);
            StretchFill(AsRt(titleLabel), 6f, 0f, 6f, 0f);

            refs.CloseButton = MakeClickable("CloseBtn", titleRt, "✕", 16f);
            var closeRt = refs.CloseButton.GetComponent<RectTransform>();
            closeRt.anchorMin = new Vector2(1f, 0f);
            closeRt.anchorMax = new Vector2(1f, 1f);
            closeRt.pivot = new Vector2(1f, 0.5f);
            closeRt.sizeDelta = new Vector2(36f, 0f);
            closeRt.anchoredPosition = Vector2.zero;

            // ---------- ④ Tab 页切换 ----------
            var tabsRt = MakeRect("Tabs", bgRt);
            StretchTopBar(tabsRt, 12f, 12f, 44f, 38f);

            refs.TabLlmButton = MakeClickable("TabLlmBtn", tabsRt, "大模型", 16f);
            var tabLlmRt = refs.TabLlmButton.GetComponent<RectTransform>();
            tabLlmRt.anchorMin = new Vector2(0f, 0f); tabLlmRt.anchorMax = new Vector2(0.5f, 1f);
            tabLlmRt.offsetMin = Vector2.zero; tabLlmRt.offsetMax = new Vector2(-4f, 0f);

            refs.TabPromptButton = MakeClickable("TabPromptBtn", tabsRt, "提示词", 16f);
            var tabPromptRt = refs.TabPromptButton.GetComponent<RectTransform>();
            tabPromptRt.anchorMin = new Vector2(0.5f, 0f); tabPromptRt.anchorMax = Vector2.one;
            tabPromptRt.offsetMin = new Vector2(4f, 0f); tabPromptRt.offsetMax = Vector2.zero;

            // ---------- ⑤ 大模型设置页（分组改版：组标题 + 整页纵向滚动）----------
            // 18 行 + 4 组标题 + 保存键在 504px 高的页里放不下 → 整页套 ScrollRect，
            // 行节点全部挂到 Content 下（VerticalLayoutGroup + ContentSizeFitter 撑出总高）。
            // 路径契约（与 AbConfigPanel.CollectRefs 严格对齐；改名必须三处同步）：
            //   BG/PageLlm/Scroll/Viewport/Content/<Row>/Input|ToggleBg
            var pageLlmRt = MakeRect("PageLlm", bgRt);
            StretchFill(pageLlmRt, 12f, 44f, 12f, 92f);
            refs.PageLlm = pageLlmRt.gameObject;
            // PageLlm 本体保留 VLG（预制件侧同样保留：python 补丁只往里塞 Scroll，本体不动）。
            // Scroll 必须挂 LayoutElement(ignoreLayout)——否则 VLG 会把 ScrollRect 当普通子节点算高度
            // （ScrollRect 的 preferred/min 全是 -1 → 高度算成 0，整页直接看不见）。
            var formVlg = pageLlmRt.gameObject.AddComponent<VerticalLayoutGroup>();
            formVlg.spacing = 6f;
            formVlg.padding = new RectOffset(4, 4, 6, 6);
            formVlg.childControlWidth = true;
            formVlg.childControlHeight = true;
            formVlg.childForceExpandWidth = true;
            formVlg.childForceExpandHeight = false;

            // 滚动容器：节点名是 "Scroll"（不是 LlmScroll，字段名才是 LlmScroll）——预制件契约同名
            var llmScrollRt = MakeRect("Scroll", pageLlmRt);
            StretchFill(llmScrollRt, 0f, 0f, 0f, 0f);
            var llmScrollLe = llmScrollRt.gameObject.AddComponent<LayoutElement>();
            llmScrollLe.ignoreLayout = true;                 // 见上：PageLlm 的 VLG 必须完全跳过它
            var llmScroll = llmScrollRt.gameObject.AddComponent<ScrollRect>();
            llmScroll.horizontal = false;
            llmScroll.vertical = true;
            llmScroll.movementType = ScrollRect.MovementType.Clamped;
            llmScroll.scrollSensitivity = 30f;
            refs.LlmScroll = llmScroll;

            // 视口：stretch 满 Scroll + RectMask2D 裁切（不用 Mask+Image，省一张 sprite）
            var llmViewportRt = MakeRect("Viewport", llmScrollRt);
            StretchFill(llmViewportRt, 0f, 0f, 0f, 0f);
            llmViewportRt.gameObject.AddComponent<RectMask2D>();
            llmViewportRt.gameObject.AddComponent<CanvasRenderer>();

            // 内容容器：pivot 顶中 + 顶锚 → 内容长高时向下延伸（不是向两侧长大）；
            // VLG 排布子节点，ContentSizeFitter 把 Content 自身高度撑成"内容总高"，ScrollRect 才有得滚。
            var llmContentRt = MakeRect("Content", llmViewportRt);
            llmContentRt.anchorMin = new Vector2(0f, 1f);
            llmContentRt.anchorMax = new Vector2(1f, 1f);
            llmContentRt.pivot = new Vector2(0.5f, 1f);
            llmContentRt.sizeDelta = Vector2.zero;
            llmContentRt.anchoredPosition = Vector2.zero;
            var contentVlg = llmContentRt.gameObject.AddComponent<VerticalLayoutGroup>();
            contentVlg.spacing = 6f;
            contentVlg.padding = new RectOffset(4, 4, 6, 6);
            contentVlg.childControlWidth = true;
            contentVlg.childControlHeight = true;
            contentVlg.childForceExpandWidth = true;
            contentVlg.childForceExpandHeight = false;
            var contentFitter = llmContentRt.gameObject.AddComponent<ContentSizeFitter>();
            contentFitter.verticalFit = ContentSizeFitter.FitMode.PreferredSize;

            llmScroll.viewport = llmViewportRt;
            llmScroll.content = llmContentRt;

            // 【模型与生成】
            MakeSectionHeader(llmContentRt, "GroupHeader_Model", "模型与生成");
            // 占位符（水印）用公开示例端点，不用开发机的本地网关（127.0.0.1:8123）——
            // 发行版里本地网关地址只会让用户困惑，与 ConfigPresenter 的行内说明保持一致。
            refs.BaseUrlInput = MakeFormRow(llmContentRt, "BaseUrlRow", "接口地址 base_url", "https://api.deepseek.com/v1", InputField.ContentType.Standard);
            refs.ApiKeyInput = MakeFormRow(llmContentRt, "ApiKeyRow", "API Key", "sk-…", InputField.ContentType.Password);
            refs.ModelInput = MakeFormRow(llmContentRt, "ModelRow", "模型 model", "模型名", InputField.ContentType.Standard);
            // 自定义请求头：可空。放在「模型与生成」组末尾，因为这四项是同一个问题
            // ——「怎么连上这个模型」；排查顺序也正好是先看前三个、再看它。
            // 格式 `k1: v1; k2: v2`（也接受整体 JSON）；留空 = 不附加任何头。
            refs.HeadersInput = MakeFormRow(llmContentRt, "HeadersRow", "自定义请求头（可空）", "x-opencode-session: agent-loop", InputField.ContentType.Standard);
            refs.ImageToggle = MakeToggleRow(llmContentRt, "ImageRow", "多模态（支持图片输入）");

            // 【主动互动】（起除 Python 端口外全部即时生效，文案不再写"重启生效"）
            MakeSectionHeader(llmContentRt, "GroupHeader_Initiative", "主动互动");
            refs.InitiativeEnabledToggle = MakeToggleRow(llmContentRt, "InitiativeEnabledRow", "主动互动总开关（即时生效）");
            refs.DailyChanceInput = MakeFormRow(llmContentRt, "DailyChanceRow", "每日触发概率 0-100（即时生效）", "15", InputField.ContentType.IntegerNumber);
            refs.NpcCooldownRealInput = MakeFormRow(llmContentRt, "NpcCooldownRealRow", "单人现实冷却秒（即时生效）", "600", InputField.ContentType.IntegerNumber);
            refs.NpcCooldownDaysInput = MakeFormRow(llmContentRt, "NpcCooldownDaysRow", "单人游戏日冷却（即时生效）", "3", InputField.ContentType.IntegerNumber);

            // 【记忆与压缩】
            MakeSectionHeader(llmContentRt, "GroupHeader_Memory", "记忆与压缩");
            refs.CompactionEnabledToggle = MakeToggleRow(llmContentRt, "CompactionEnabledRow", "自动压缩（即时生效）");
            refs.CtxWindowInput = MakeFormRow(llmContentRt, "CtxWindowRow", "上下文窗口 tokens（即时生效）", "32768", InputField.ContentType.IntegerNumber);
            refs.RetainRatioInput = MakeFormRow(llmContentRt, "RetainRatioRow", "保留原文比例 0.05-0.6（即时生效）", "0.16", InputField.ContentType.DecimalNumber);
            refs.ThresholdRatioInput = MakeFormRow(llmContentRt, "ThresholdRatioRow", "自动压缩阈值 0.3-0.95（即时生效）", "0.8", InputField.ContentType.DecimalNumber);

            // 【高级】（"高级组默认隐藏"那套 ui.show_advanced 已废除：本组常显，无任何 SetActive(false)）
            MakeSectionHeader(llmContentRt, "GroupHeader_Advanced", "高级");
            refs.MinIntervalInput = MakeFormRow(llmContentRt, "MinIntervalRow", "全局熔断秒（即时生效）", "300", InputField.ContentType.DecimalNumber);
            refs.LowThreshInput = MakeFormRow(llmContentRt, "LowThreshRow", "低好感阈值（即时生效）", "60", InputField.ContentType.IntegerNumber);
            refs.LowHalveToggle = MakeToggleRow(llmContentRt, "LowHalveRow", "低好感减半（即时生效）");
            refs.PortInput = MakeFormRow(llmContentRt, "PortRow", "WS 端口（重启生效）", "8766", InputField.ContentType.IntegerNumber);   // 唯一仍需重启的一项
            refs.TimeoutInput = MakeFormRow(llmContentRt, "TimeoutRow", "RPC 超时秒（即时生效）", "5.0", InputField.ContentType.DecimalNumber);
            // 【AI 时间与重试】：四个 llm.* 键，紧随「RPC 超时秒」——
            // 全是"等多久 / 试几次"，放一起排查时不用来回找。默认值本身可用，一般不用动；
            // 为什么单列成四行而不是一个"超时秒"：见 config.example.json 的「超时与重试说明」，
            // 核心是**流式与非流式的量纲不同**（流式量"块间隔"，非流式量"完整生成时长"）。
            refs.LlmTimeoutInput = MakeFormRow(llmContentRt, "LlmTimeoutRow", "AI 单次超时秒·流式（即时生效）", "45", InputField.ContentType.DecimalNumber);
            refs.LlmTimeoutNsInput = MakeFormRow(llmContentRt, "LlmTimeoutNsRow", "AI 单次超时秒·压缩（即时生效）", "180", InputField.ContentType.DecimalNumber);
            refs.LlmBudgetInput = MakeFormRow(llmContentRt, "LlmBudgetRow", "AI 总等待上限秒（即时生效）", "100", InputField.ContentType.DecimalNumber);
            refs.LlmRetriesInput = MakeFormRow(llmContentRt, "LlmRetriesRow", "AI 失败重试次数（即时生效）", "1", InputField.ContentType.IntegerNumber);
            refs.PortraitToggle = MakeToggleRow(llmContentRt, "PortraitRow", "显示立绘（即时生效）");

            refs.SaveLlmButton = MakeClickable("SaveLlmBtn", llmContentRt, "保存大模型设置", 17f);
            var saveLe = refs.SaveLlmButton.AddComponent<LayoutElement>();
            saveLe.preferredHeight = 42f;

            // 「测试连接」：拿**表单当前值**真调一次模型，结果显示在底部状态条。
            // 放在保存下面一行 —— 它的典型用法就是"填完先试，试通了再保存"（保存会重启 Python，很重）。
            refs.TestLlmButton = MakeClickable("TestLlmBtn", llmContentRt, "测试连接（不保存，直接试一次）", 17f);
            var testLe = refs.TestLlmButton.AddComponent<LayoutElement>();
            testLe.preferredHeight = 42f;

            // ---------- ⑥ 提示词编辑页（默认隐藏） ----------
            var pagePromptRt = MakeRect("PagePrompt", bgRt);
            StretchFill(pagePromptRt, 12f, 44f, 12f, 92f);
            pagePromptRt.gameObject.SetActive(false);
            refs.PagePrompt = pagePromptRt.gameObject;

            // 左：文件列表（ScrollRect）
            Sprite scrollSpr = ChatUiBuilder.MakeSlicedSprite(new Color(0f, 0f, 0f, 0.30f), 8, 3);
            var listRt = MakeImage("FileScroll", pagePromptRt, scrollSpr, Color.white, Image.Type.Sliced);
            listRt.anchorMin = new Vector2(0f, 0f); listRt.anchorMax = new Vector2(0f, 1f);
            listRt.pivot = new Vector2(0f, 0.5f);
            listRt.offsetMin = new Vector2(0f, 92f); listRt.offsetMax = new Vector2(230f, 0f);
            var fileScroll = listRt.gameObject.AddComponent<ScrollRect>();
            fileScroll.horizontal = false; fileScroll.vertical = true;
            fileScroll.movementType = ScrollRect.MovementType.Clamped;
            var viewportRt = MakeRect("Viewport", listRt);
            StretchFill(viewportRt, 0f, 0f, 0f, 0f);
            viewportRt.gameObject.AddComponent<RectMask2D>();
            viewportRt.gameObject.AddComponent<CanvasRenderer>();
            var contentRt = MakeRect("Content", viewportRt);
            contentRt.anchorMin = new Vector2(0f, 1f); contentRt.anchorMax = Vector2.one;
            contentRt.pivot = new Vector2(0.5f, 1f);
            contentRt.sizeDelta = Vector2.zero; contentRt.anchoredPosition = Vector2.zero;
            var vlg = contentRt.gameObject.AddComponent<VerticalLayoutGroup>();
            vlg.spacing = 2f; vlg.padding = new RectOffset(2, 2, 2, 2);
            vlg.childControlWidth = true; vlg.childControlHeight = true;
            vlg.childForceExpandWidth = true; vlg.childForceExpandHeight = false;
            var fileFitter = contentRt.gameObject.AddComponent<ContentSizeFitter>();
            fileFitter.verticalFit = ContentSizeFitter.FitMode.PreferredSize;
            fileScroll.content = contentRt;
            refs.FileScroll = fileScroll;
            refs.FileListContent = contentRt;

            // 文件项模板（藏 BG 下 inactive，运行时克隆进 Content）
            refs.FileItemTemplate = MakeClickable("FileItem", bgRt, "文件", 14f);
            var itemLe = refs.FileItemTemplate.AddComponent<LayoutElement>();
            itemLe.preferredHeight = 26f;
            refs.FileItemTemplate.SetActive(false);

            // 右：多行编辑框
            refs.PromptInput = MakeMultilineInput("PromptInput", pagePromptRt, "选择左侧文件后编辑…");
            var piRt = AsRt(refs.PromptInput);
            piRt.anchorMin = new Vector2(0f, 0f); piRt.anchorMax = Vector2.one;
            piRt.offsetMin = new Vector2(242f, 92f); piRt.offsetMax = Vector2.zero;

            // 中带：当前文件标签
            var curLabel = MakeText("CurrentFileLabel", pagePromptRt, "（未选择文件）", 13f, TextDim, TextAnchor.MiddleLeft);
            var clRt = AsRt(curLabel);
            clRt.anchorMin = new Vector2(0f, 0f); clRt.anchorMax = Vector2.one;
            clRt.offsetMin = new Vector2(2f, 48f); clRt.offsetMax = new Vector2(-2f, 92f);
            refs.CurrentFileLabel = curLabel;

            // 底带：保存 / 新建人设 / 删除文件（三等分；删除键红字警示）
            refs.SavePromptButton = MakeClickable("SavePromptBtn", pagePromptRt, "保存文件（下回合生效）", 15f);
            var spRt = refs.SavePromptButton.GetComponent<RectTransform>();
            spRt.anchorMin = new Vector2(0f, 0f); spRt.anchorMax = new Vector2(1f / 3f, 0f);
            spRt.offsetMin = new Vector2(0f, 4f); spRt.offsetMax = new Vector2(-4f, 44f);

            // 新建人设：NPC 名直接取自上下文（对话内 ⚙ 打开即带），输入框已弃用（节点保留但隐藏，兼容引用）
            refs.NewNpcInput = MakeInputField("NewNpcInput", pagePromptRt, "NPC 名", 15f);
            var nnRt = AsRt(refs.NewNpcInput);
            nnRt.anchorMin = new Vector2(0.5f, 0f); nnRt.anchorMax = new Vector2(1f, 0f);
            nnRt.offsetMin = new Vector2(6f, 4f); nnRt.offsetMax = new Vector2(-112f, 44f);
            nnRt.gameObject.SetActive(false);

            refs.NewNpcButton = MakeClickable("NewNpcBtn", pagePromptRt, "新建人设", 15f);
            var nbRt = refs.NewNpcButton.GetComponent<RectTransform>();
            nbRt.anchorMin = new Vector2(1f / 3f, 0f); nbRt.anchorMax = new Vector2(2f / 3f, 0f);
            nbRt.offsetMin = new Vector2(4f, 4f); nbRt.offsetMax = new Vector2(-4f, 44f);

            // 删除文件：仅新增文件可删（Python delete_prompt fail-closed 把关）；
            // 无任何交互（未选中/不可删）时点击只在状态条提示，安全无害
            refs.DeletePromptButton = MakeClickable("DeletePromptBtn", pagePromptRt, "删除文件", 15f);
            var delLbl = refs.DeletePromptButton.GetComponentInChildren<Text>(true);
            if (delLbl != null) delLbl.color = new Color(0.86f, 0.36f, 0.32f, 1f);   // 警示红
            var dbRt = refs.DeletePromptButton.GetComponent<RectTransform>();
            dbRt.anchorMin = new Vector2(2f / 3f, 0f); dbRt.anchorMax = new Vector2(1f, 0f);
            dbRt.offsetMin = new Vector2(4f, 4f); dbRt.offsetMax = new Vector2(-6f, 44f);

            // ---------- ⑦ 状态条 ----------
            var status = MakeText("Status", bgRt, "", 14f, TextDim, TextAnchor.MiddleLeft);
            var stRt = AsRt(status);
            stRt.anchorMin = new Vector2(0f, 0f); stRt.anchorMax = Vector2.one;
            stRt.offsetMin = new Vector2(12f, 8f); stRt.offsetMax = new Vector2(-12f, 40f);
            refs.StatusLabel = status;

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
            img.raycastTarget = true;
            return rt;
        }

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
            txt.font = ChatUiBuilder.ResolveSharedFont();
            txt.raycastTarget = false;
            txt.horizontalOverflow = HorizontalWrapMode.Wrap;
            txt.verticalOverflow = VerticalWrapMode.Overflow;
            return txt;
        }

        /// <summary>「可点」对象：Image 底 + Button + 子 Text（回调由 Presenter 经 ClickUtils 挂）</summary>
        private static GameObject MakeClickable(string name, Transform parent, string label, float fontSize)
        {
            var rt = MakeImage(name, parent, ChatUiBuilder.MakeSlicedSprite(CtrlBg, 8, 3), Color.white, Image.Type.Sliced);
            var btn = rt.gameObject.AddComponent<Button>();
            btn.targetGraphic = rt.GetComponent<Image>();
            var txt = MakeText("Label", rt, label ?? "", fontSize, TextLight, TextAnchor.MiddleCenter);
            StretchFill(AsRt(txt), 4f, 0f, 4f, 0f);
            return rt.gameObject;
        }

        private static InputField MakeInputField(string name, Transform parent, string placeholder, float fontSize)
        {
            var rt = MakeImage(name, parent, ChatUiBuilder.MakeSlicedSprite(CtrlBg, 8, 3), Color.white, Image.Type.Sliced);
            var txt = MakeText("Text", rt, "", fontSize, TextLight, TextAnchor.MiddleLeft);
            StretchFill(AsRt(txt), 10f, 2f, 10f, 2f);
            var ph = MakeText("Placeholder", rt, placeholder ?? "", fontSize, TextDim, TextAnchor.MiddleLeft);
            StretchFill(AsRt(ph), 10f, 2f, 10f, 2f);
            var field = rt.gameObject.AddComponent<InputField>();
            field.textComponent = txt;
            field.placeholder = ph;
            return field;
        }

        /// <summary>多行编辑框（提示词正文）</summary>
        private static InputField MakeMultilineInput(string name, Transform parent, string placeholder)
        {
            var field = MakeInputField(name, parent, placeholder, 15f);
            field.lineType = InputField.LineType.MultiLineNewline;
            // alignment 是 Text 的属性，不在 InputField 上：多行时把正文与占位符一起改成左上对齐
            if (field.textComponent != null)
                field.textComponent.alignment = TextAnchor.UpperLeft;
            var ph = field.placeholder as Text;
            if (ph != null)
                ph.alignment = TextAnchor.UpperLeft;
            return field;
        }

        /// <summary>表单一行（挂在 VerticalLayoutGroup 页内）：左标签 + 右 210 宽输入框，行高 36。
        /// rowName 用 ASCII（AB 版 CollectRefs 按路径 Find，节点名是跨工程契约）</summary>
        private static InputField MakeFormRow(RectTransform page, string rowName, string label, string placeholder, InputField.ContentType contentType)
        {
            var row = MakeRect(rowName, page);
            var rowLe = row.gameObject.AddComponent<LayoutElement>();
            rowLe.preferredHeight = 36f;

            var lbl = MakeText("Label", row, label, 15f, LabelColor, TextAnchor.MiddleLeft);
            lbl.fontStyle = FontStyle.Bold;
            var lr = AsRt(lbl);
            lr.anchorMin = Vector2.zero; lr.anchorMax = Vector2.one;
            lr.offsetMin = Vector2.zero; lr.offsetMax = new Vector2(-220f, 0f);

            var field = MakeInputField("Input", row, placeholder, 15f);
            field.contentType = contentType;
            var fr = AsRt(field);
            fr.anchorMin = new Vector2(1f, 0f); fr.anchorMax = new Vector2(1f, 1f);
            fr.pivot = new Vector2(1f, 0.5f);
            fr.sizeDelta = new Vector2(210f, 0f);
            fr.anchoredPosition = Vector2.zero;
            return field;
        }

        /// <summary>开关一行（挂在 VerticalLayoutGroup 页内）：左标签 + 右 Toggle（点亮=亮色勾块）</summary>
        private static Toggle MakeToggleRow(RectTransform page, string rowName, string label)
        {
            var row = MakeRect(rowName, page);
            var rowLe = row.gameObject.AddComponent<LayoutElement>();
            rowLe.preferredHeight = 36f;

            var lbl = MakeText("Label", row, label, 15f, LabelColor, TextAnchor.MiddleLeft);
            lbl.fontStyle = FontStyle.Bold;
            var lr = AsRt(lbl);
            lr.anchorMin = Vector2.zero; lr.anchorMax = Vector2.one;
            lr.offsetMin = Vector2.zero; lr.offsetMax = new Vector2(-220f, 0f);

            var toggleBg = MakeImage("ToggleBg", row, ChatUiBuilder.MakeSlicedSprite(CtrlBg, 8, 3), Color.white, Image.Type.Sliced);
            var tbRt = toggleBg.GetComponent<RectTransform>();
            tbRt.anchorMin = new Vector2(1f, 0f); tbRt.anchorMax = new Vector2(1f, 1f);
            tbRt.pivot = new Vector2(1f, 0.5f);
            tbRt.sizeDelta = new Vector2(56f, 0f);
            tbRt.anchoredPosition = Vector2.zero;

            var checkRt = MakeImage("Checkmark", toggleBg.transform,
                                    ChatUiBuilder.MakeSlicedSprite(CtrlBgActive, 8, 3), TextOk, Image.Type.Sliced);
            StretchFill(checkRt, 2f, 2f, 2f, 2f);

            var toggle = toggleBg.gameObject.AddComponent<Toggle>();
            toggle.targetGraphic = toggleBg.GetComponent<Image>();
            toggle.graphic = checkRt.GetComponent<Image>();
            return toggle;
        }

        /// <summary>分组标题（挂在滚动 Content 内，分组改版）：左对齐灰字，字号比参数标签(15)略小，
        /// 固定行高 24（LayoutElement 压住 Text 自己的 preferredHeight，免得行高随字体行高漂）。
        /// 与预制件侧 GroupHeader_* 同契约（预制件是克隆行内 Label 后改字号 13 / 灰色）。</summary>
        private static Text MakeSectionHeader(Transform parent, string name, string text)
        {
            var txt = MakeText(name, parent, text, 13f, TextDim, TextAnchor.MiddleLeft);
            txt.fontStyle = FontStyle.Bold;
            var le = txt.gameObject.AddComponent<LayoutElement>();
            le.preferredHeight = 24f;
            return txt;
        }

        /// <summary>
        /// 悬停气泡模板（配置面板鼠标悬停说明用）。
        /// 两个消费方共用本契约：① 编辑器补节点菜单（写进 UIConfigAi.prefab 的 BG/Tooltip，样式走预制体）；
        /// ② ConfigPresenter 运行时兜底（代码版面板无预制体节点时现造）。
        /// Build() 主树刻意不包含本节点——气泡属于运行时行为件，重跑生成菜单不会覆盖它。
        /// 节点契约：Tooltip（深色圆角半透明底，raycast 关闭不挡点击）+ TooltipText（白字）。
        /// 尺寸/定位由 ConfigPresenter 运行时决定。
        /// </summary>
        public static GameObject BuildTooltipTemplate(Transform parent)
        {
            var tipColor = new Color(0.13f, 0.15f, 0.18f, 0.94f);
            var tipRt = MakeImage("Tooltip", parent, ChatUiBuilder.MakeSlicedSprite(tipColor, 12, 5), tipColor, Image.Type.Sliced);
            tipRt.gameObject.GetComponent<Image>().raycastTarget = false;
            tipRt.sizeDelta = new Vector2(260f, 60f);
            var txt = MakeText("TooltipText", tipRt, "", 13f, new Color(0.95f, 0.96f, 0.97f, 1f), TextAnchor.UpperLeft);
            txt.raycastTarget = false;
            var trt = AsRt(txt);
            trt.anchorMin = Vector2.zero; trt.anchorMax = Vector2.one;
            trt.offsetMin = new Vector2(10f, 8f); trt.offsetMax = new Vector2(-10f, -8f);
            return tipRt.gameObject;
        }

        // ------------------------------------------------------------------
        // 工具
        // ------------------------------------------------------------------

        private static RectTransform AsRt(Component c) => c != null ? c.GetComponent<RectTransform>() : null;

        /// <summary>顶部横条：左右拉伸、顶锚、固定高</summary>
        private static void StretchTopBar(RectTransform rt, float left, float right, float top, float height)
        {
            rt.anchorMin = new Vector2(0f, 1f);
            rt.anchorMax = new Vector2(1f, 1f);
            rt.pivot = new Vector2(0.5f, 1f);
            rt.offsetMin = new Vector2(left, -(top + height));
            rt.offsetMax = new Vector2(-right, -top);
        }

        /// <summary>四角拉伸（锚 0..1，四边距）</summary>
        private static void StretchFill(RectTransform rt, float l, float b, float r, float t)
        {
            rt.anchorMin = Vector2.zero;
            rt.anchorMax = Vector2.one;
            rt.offsetMin = new Vector2(l, b);
            rt.offsetMax = new Vector2(-r, -t);
            rt.pivot = new Vector2(0.5f, 0.5f);
        }

        private static void SetLayerRecursive(Transform t, int layer)
        {
            t.gameObject.layer = layer;
            for (int i = 0; i < t.childCount; i++)
                SetLayerRecursive(t.GetChild(i), layer);
        }

        /// <summary>防御：场景没有 EventSystem 时补建一个（与 ChatUiBuilder 同语义）</summary>
        private static void EnsureEventSystem(Transform parent)
        {
            if (UnityEngine.Object.FindObjectOfType<UnityEngine.EventSystems.EventSystem>() != null)
                return;
            var go = new GameObject("AgentLoopConfigEventSystem");
            if (parent != null) go.transform.SetParent(parent, false);
            go.AddComponent<UnityEngine.EventSystems.EventSystem>();
            go.AddComponent<UnityEngine.EventSystems.StandaloneInputModule>();
        }
    }
}
