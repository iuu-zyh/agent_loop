/// <summary>
/// 左侧「修改压缩纪要」面板（10-31 用户需求）。
///
/// 用途只有一个：让玩家在**压缩刚完成、还没开始下一轮对话**的窗口内，就地改掉那条纪要的正文。
/// 入口 = 对话里那条「压缩纪要」折叠行的「修改」钮；出口 = 本面板右下角的「发送」。
///
/// ## 版式（坐标沿用 BG 的 1200×800，与 `ToolPanel` 同一套口径）
///
/// <code>
///  BG 1200×800
///  ┌────────────────────────────┬──────────────────────────┐
///  │ 修改压缩纪要            ✕  │                          │
///  │ ┌────────────────────────┐ │      消息区（被盖住）      │
///  │ │  大文本域（多行）        │ │                          │
///  │ │  预填当前纪要全文        │ │                          │
///  │ └────────────────────────┘ │                          │
///  │ 状态行（成功/失败原因）      │                          │
///  │                  [ 发送 ]  │                          │
///  └────────────────────────────┴──────────────────────────┘
///  └──────── 600 宽（BG 的一半，用户定的）────────┘
/// </code>
///
/// ## 四条设计定案（改之前先读）
///
/// ★ ① **一个预制体字节都不动**：整棵树运行时搭，与 `ToolPanel` 同一条理由
///   （改 `UIChatAi.prefab` 要重打 .ab，改错会连累整个对话窗）。顶边同样停在 760
///   （顶栏那排窗口级按钮必须全程可点 —— ✕ 被盖住就只能靠 ESC 退出，`ToolPanel` 踩过）。
/// ★ ② **多行文本域照 `ConfigUiBuilder.MakeMultilineInput` 的建法**：那是本工程已验证的
///   多行编辑框（`InputField + lineType=MultiLineNewline + 左上对齐`）。这里只换字体与配色，
///   保证与对话窗同一族观感。
/// ★ ③ **发送不是发消息**：它只把正文交给 `ChatPresenter` 走 `edit_compact_summary` RPC，
///   不产生玩家发言、不产生回合（用户原话「不是发送消息」）。成功提示也只补一行系统提示。
/// ★ ④ **失败不关面板**：玩家刚敲的字还在框里，改一下再发即可；原因显示在状态行（红字）。
///   成功才收起（此时正文已经回到折叠行上，框里留着旧值只会误导）。
///
/// ## 失败姿态
/// 建树失败只记一行日志并让 `Root` 保持 null（`IsOpen` 恒 false）——其余功能一字不变。
/// </summary>
using System;
using UnityEngine;
using UnityEngine.UI;

using static AgentLoopBridge.UiKit;
namespace AgentLoopBridge
{
    /// <summary>左侧编辑面板。静态类：一棵树、随对话宿主生灭（宿主重建后 `Build` 自动重搭）。</summary>
    internal static class CompactEditPanel
    {
        // ---- 版式常量（与文件头那张图逐项对应；改版式必须同步改注释）----
        private const float PanelW = 600f;    // BG 宽 1200 的一半（用户定的"占据半个 UI"）
        private const float TopInset = 40f;   // 顶边距 = 800 − 顶栏下沿 760
        private const float HeadH = 52f;
        private const float PadX = 16f;
        private const float PadB = 14f;
        private const float BtnW = 120f;
        private const float BtnH = 40f;
        private const float StatusH = 22f;
        private const int MaxChars = 20000;   // 与 `compaction/summary.py::MAX_CHARS` 同值（服务端权威，这里只是即时反馈）

        // ---- 配色（与对话窗同一套暖白浅色底）----
        private static readonly Color PanelBg = new Color(0.862f, 0.851f, 0.851f, 1f);
        private static readonly Color FieldBg = new Color(1f, 1f, 1f, 0.92f);
        private static readonly Color TitleColor = new Color(0.10f, 0.11f, 0.16f, 1f);
        private static readonly Color StatusColor = new Color(0.42f, 0.44f, 0.50f, 1f);
        private static readonly Color ErrorColor = new Color(0.72f, 0.16f, 0.16f, 1f);
        private static readonly Color OkColor = new Color(0.13f, 0.48f, 0.22f, 1f);
        private static readonly Color BtnBg = new Color(0.20f, 0.42f, 0.75f, 0.95f);

        // ---- 状态 ----
        private static GameObject _root;
        private static RectTransform _fieldRt;   // 文本域根（Show 时强制贴合 + 打几何日志）
        private static InputField _input;
        private static Text _status;
        private static Button _sendBtn;
        private static Text _sendLabel;
        private static bool _sending;

        // ---- 尺寸 / 滚动（真机 11-10 二次报障后的落点）----
        // 教训一：`_root.transform.parent` 在 Show 那一刻**可能拿不到 RectTransform**
        //   （日志 `BG=600x760` 其实是回落读了根自己的 rect，于是面板被算矮 40）。
        //   于是 BG 尺寸改成 Build 时量一次存下来，Show 不再问父级。
        // 教训二：把**输入框**撑满不等于把**正文**撑满 —— 正文是输入框的子节点，
        //   模板里它是"左上定点 + 780×70 + Truncate"，70 高 ÷ 19 号字 ≈ 4 行。
        private static float _bgW = 1200f;
        private static float _bgH = 800f;
        private static bool _scrollReady;                 // 遮罩 + 滚轮都就位 ⇒ 正文才敢按全文高度铺开
        /// <summary>滚轮驱动是否已注册进 Il2Cpp（重复注册会抛，故记账；失败不置位 = 下次重试）。</summary>
        private static bool _scrollDriverRegistered;
        private static float _textHeight;      // 正文全文高度（滚轮上限）
        private static float _appliedTextH;    // 上次写给 Text 的高度（漂移检测用）
        private static float _scrollY;         // 已向上滚了多少（0 = 顶部）

        /// <summary>点「发送」的唯一接收方（= 当前对话宿主）。**单一赋值而不是 event**：
        /// 面板是静态的、宿主会被游戏销毁重建，event 会越积越多并调用到已销毁对象上；
        /// 赋值让"谁最后搭的树谁收"成为唯一事实。</summary>
        private static Action<string> _onSubmit;

        /// <summary>面板此刻是否显示着（ESC 链与"关窗要收起"都用它）。</summary>
        public static bool IsOpen { get { return Alive(_root) && _root.activeSelf; } }

        /// <summary>树还在不在（宿主重建后为 false；`Build` 会重搭）。</summary>
        public static bool HasPanel { get { return Alive(_root); } }

        // ------------------------------------------------------------------
        // 构建（幂等：宿主重建后自动重搭）
        // ------------------------------------------------------------------

        /// <summary>在 BG 下搭出面板（初始隐藏）。`bg` = `ChatWindowRefs.Window`；
        /// `template` = `ChatWindowRefs.ChatInput`（**克隆**它来得到同一个输入框观感，
        /// 与 `ToolPanel` 克隆 `ConfigBtn` 同一条路子；为空则从零建一个）。</summary>
        public static void Build(RectTransform bg, InputField template = null,
                                 Action<string> onSubmit = null)
        {
            _onSubmit = onSubmit;          // 宿主重建时原地换掉，不累积
            try
            {
                if (bg == null)
                {
                    Log("Build: BG 为空，编辑面板不搭（「修改」钮点了只会提示）");
                    return;
                }
                if (Alive(_root))
                {
                    if (_root.transform.parent != bg) _root.transform.SetParent(bg, false);
                    return;                        // 同一宿主：不重复搭
                }
                _root = null;
                _input = null;
                _status = null;
                _sendBtn = null;
                _sendLabel = null;
                _sending = false;
                // BG 的逻辑尺寸在**这一刻**是准的（预制体 1200×800，宿主归一化之后也一样）；
                // 存下来给 Show 用 —— 真机 11-10 证明 Show 时按父级现量会拿到 600×760（读的是根自己）。
                _bgW = bg.rect.width > 1f ? bg.rect.width : 1200f;
                _bgH = bg.rect.height > 1f ? bg.rect.height : 800f;

                var go = new GameObject("CompactEditPanel");
                var rt = go.AddComponent<RectTransform>();
                rt.SetParent(bg, false);
                // 左锚点 + 上下拉伸：顶边钉在顶栏下沿，底边贴 BG 底
                rt.anchorMin = new Vector2(0f, 0f);
                rt.anchorMax = new Vector2(0f, 1f);
                rt.pivot = new Vector2(0f, 1f);
                rt.anchoredPosition = new Vector2(0f, -TopInset);
                rt.sizeDelta = new Vector2(PanelW, -TopInset);
                go.AddComponent<CanvasRenderer>();

                var bgImg = bg.GetComponent<Image>();
                var img = go.AddComponent<Image>();
                img.sprite = bgImg != null ? bgImg.sprite : null;
                img.type = Image.Type.Sliced;
                img.color = PanelBg;
                img.raycastTarget = true;      // 面板自己吃掉落在它身上的点击（不穿透到消息区）

                BuildHead(rt);
                BuildField(rt, template);
                BuildFooter(rt);

                go.transform.SetAsLastSibling();   // 画在 BG 的所有子节点之上
                go.SetActive(false);
                _root = go;
                Log(string.Format("编辑面板已搭好 宽={0:F0} ｜ BG={1:F0}x{2:F0} 面板={3:F0}x{4:F0}",
                                  PanelW, bg.rect.width, bg.rect.height, rt.rect.width, rt.rect.height));
            }
            catch (Exception e)
            {
                Log("Build 失败（编辑面板不可用，其余功能一字不变）: " + e.Message);
                _root = null;
                if (bg != null) UiKit.AuditSoon(bg, "CompactEdit");
            }
        }

        private static void BuildHead(RectTransform parent)
        {
            var head = MakeRect("Head", parent);
            head.anchorMin = new Vector2(0f, 1f);
            head.anchorMax = new Vector2(1f, 1f);
            head.pivot = new Vector2(0.5f, 1f);
            head.offsetMin = new Vector2(PadX, -HeadH);
            head.offsetMax = new Vector2(-PadX, 0f);

            var title = NewText("Title", head, "修改压缩纪要", 18, TitleColor,
                                TextAnchor.MiddleLeft, FontStyle.Bold);
            Stretch(title.rectTransform, 0f, 0f, 44f, 0f);

            var close = NewButton("Close", head, "✕", 15, TitleColor, new Color(0f, 0f, 0f, 0f));
            var crt = close.GetComponent<RectTransform>();
            crt.anchorMin = new Vector2(1f, 0.5f);
            crt.anchorMax = new Vector2(1f, 0.5f);
            crt.pivot = new Vector2(1f, 0.5f);
            crt.sizeDelta = new Vector2(40f, 40f);
            crt.anchoredPosition = Vector2.zero;
            ClickUtils.Attach(close.GetComponent<Button>(), Hide);
        }

        private static void BuildField(RectTransform parent, InputField template)
        {
            var field = MakeRect("Field", parent);
            field.anchorMin = new Vector2(0f, 0f);
            field.anchorMax = new Vector2(1f, 1f);
            // 底边：状态行 + 按钮行都让开；顶边：让开头部
            field.offsetMin = new Vector2(PadX, PadB + BtnH + 8f + StatusH + 8f);
            field.offsetMax = new Vector2(-PadX, -HeadH - 8f);

            _fieldRt = field;
            var inp = CloneInput(template, field);
            _input = inp != null ? inp : MakeInputFromScratch(field);
            PrepareScroll(field);
        }

        /// <summary>克隆对话窗自己的输入框（`BG/Input/ChatInput`）—— 同字体/同底图/同光标色。
        /// 只改三件：多行、字数上限、正文左上对齐 + 纵向溢出可见（单行那套会截断多行文本）。
        /// 失败返回 null，由调用方回落"从零建"。</summary>
        private static InputField CloneInput(InputField template, RectTransform parent)
        {
            if (template == null) return null;
            try
            {
                var go = UnityEngine.Object.Instantiate(template.gameObject) as GameObject;
                if (go == null) return null;
                go.name = "EditInput";
                go.transform.SetParent(parent, false);
                var rt = go.GetComponent<RectTransform>();
                if (rt != null)
                {
                    rt.anchorMin = Vector2.zero;
                    rt.anchorMax = Vector2.one;
                    rt.offsetMin = new Vector2(2f, 2f);
                    rt.offsetMax = new Vector2(-2f, -2f);
                    rt.pivot = new Vector2(0.5f, 0.5f);
                }
                var inp = go.GetComponent<InputField>();
                if (inp == null)
                {
                    // 模板不是 InputField（预制体被换过？）：销毁克隆体，交给从零建
                    TemplateUtils.DestroySafe(go);
                    return null;
                }
                inp.lineType = InputField.LineType.MultiLineNewline;   // 回车换行，不提交
                inp.characterLimit = MaxChars;
                // 克隆来的监听一律清掉：这是对话输入框的回调那份，挂到这里会"改纪要顺手发消息"
                try { inp.onValueChanged.RemoveAllListeners(); } catch { }
                try { inp.onEndEdit.RemoveAllListeners(); } catch { }
                // 注：本工程的 Unity 版本里 `InputField` **没有** `onSubmit`（只有 onEndEdit /
                // onValueChanged），别照 TMP 的 API 写 —— 编译期就会炸。
                if (inp.textComponent != null)
                {
                    inp.textComponent.alignment = TextAnchor.UpperLeft;
                    inp.textComponent.horizontalOverflow = HorizontalWrapMode.Wrap;
                    inp.textComponent.verticalOverflow = VerticalWrapMode.Overflow;
                    inp.textComponent.supportRichText = false;   // 纪要正文里出现 < > 时按字面显示
                    // ★ 真机 11-10 二次报障的落点：模板 `ChatInput/Text` 在预制体里是
                    //   「左上定点 + 780×70 + m_VerticalOverflow=0(Truncate)」——70 高 ÷ 19 号字
                    //   ≈ 4 行，多出来的直接砍掉。**文字是输入框的子节点**，只撑大输入框对它无效，
                    //   必须把它也改成"跟着输入框走"。（Show 时 `RelayoutText` 还会按实际宽高再校一次）
                    Stretch(inp.textComponent.rectTransform, 4f, 4f, 4f, 4f);
                }
                var ph = inp.placeholder as Text;
                if (ph != null)
                {
                    ph.alignment = TextAnchor.UpperLeft;
                    ph.verticalOverflow = VerticalWrapMode.Overflow;
                    Stretch(ph.rectTransform, 4f, 4f, 4f, 4f);
                }
                inp.text = "";
                Log("文本域：克隆 ChatInput（同观感）→ 已改为多行");
                return inp;
            }
            catch (Exception e)
            {
                Log("克隆 ChatInput 失败，回落从零建: " + e.Message);
                return null;
            }
        }

        /// <summary>兜底：从零建一个多行输入框（预制体没有 ChatInput 时用；观感按对话窗同一族配色）。</summary>
        private static InputField MakeInputFromScratch(RectTransform parent)
        {
            parent.gameObject.AddComponent<CanvasRenderer>();
            var fimg = parent.gameObject.AddComponent<Image>();
            fimg.color = FieldBg;
            fimg.raycastTarget = true;

            var txt = NewText("Text", parent, "", 15, Color.black, TextAnchor.UpperLeft, FontStyle.Normal);
            Stretch(txt.rectTransform, 10f, 8f, 10f, 8f);
            txt.horizontalOverflow = HorizontalWrapMode.Wrap;
            txt.verticalOverflow = VerticalWrapMode.Overflow;
            txt.supportRichText = false;

            var ph = NewText("Placeholder", parent, "在这里修改压缩纪要的正文……", 15,
                             new Color(0.55f, 0.56f, 0.62f, 1f), TextAnchor.UpperLeft, FontStyle.Normal);
            Stretch(ph.rectTransform, 10f, 8f, 10f, 8f);

            var inp = parent.gameObject.AddComponent<InputField>();
            inp.textComponent = txt;
            inp.placeholder = ph;
            inp.lineType = InputField.LineType.MultiLineNewline;
            inp.characterLimit = MaxChars;
            Log("文本域：从零建（模板不可用）");
            return inp;
        }

        private static void BuildFooter(RectTransform parent)
        {
            _status = NewText("Status", parent, "", 13, StatusColor, TextAnchor.MiddleLeft, FontStyle.Normal);
            var srt = _status.rectTransform;
            srt.anchorMin = new Vector2(0f, 0f);
            srt.anchorMax = new Vector2(1f, 0f);
            srt.pivot = new Vector2(0.5f, 0f);
            srt.offsetMin = new Vector2(PadX, PadB + BtnH + 8f);
            srt.offsetMax = new Vector2(-PadX, PadB + BtnH + 8f + StatusH);

            var send = NewButton("Send", parent, "发送", 16, Color.white, BtnBg);
            var rt = send.GetComponent<RectTransform>();
            rt.anchorMin = new Vector2(1f, 0f);
            rt.anchorMax = new Vector2(1f, 0f);
            rt.pivot = new Vector2(1f, 0f);
            rt.sizeDelta = new Vector2(BtnW, BtnH);
            rt.anchoredPosition = new Vector2(-PadX, PadB);
            _sendBtn = send.GetComponent<Button>();
            _sendLabel = send.transform.childCount > 0 ? send.transform.GetChild(0).GetComponent<Text>() : null;
            ClickUtils.Attach(_sendBtn, OnSendClicked);
        }

        // ------------------------------------------------------------------
        // 对外：显示 / 收起 / 发送态
        // ------------------------------------------------------------------

        /// <summary>打开并把正文预填进去（光标置末尾）。返回 false = 树不在（宿主刚重建），
        /// 调用方应给玩家一句人话而不是静默什么都不发生。</summary>
        public static bool Show(string text)
        {
            if (!Alive(_root))
            {
                Log("Show 忽略：面板树不存在（宿主重建后未搭？）");
                return false;
            }
            _sending = false;
            SetStatus("", StatusColor);
            SetSendEnabled(true);
            try
            {
                _input.text = text ?? "";
                // 光标放**开头**：纪要常常一屏放不下，跳末尾会变成"看着顶部、改着尾部"。
                _input.caretPosition = 0;
                _scrollY = 0f;
                _root.SetActive(true);
                _root.transform.SetAsLastSibling();
                // ★ 显形后**再贴一次**并打一行几何：Build 发生在宿主创建期（面板还 inactive），
                //   RectTransform 拉伸与 InputField 初始化要到激活后才结算；真机 11-10 的
                //   "输入框只有一小块"就是这个窗口里没结算。Show 时尺寸已知，贴一次幂等且可靠。
                FitInputToField();
                _input.ActivateInputField();       // 打开即聚焦：玩家可以直接改
            }
            catch (Exception e)
            {
                Log("Show 失败: " + e.Message);
                return false;
            }
            return true;
        }

        public static void Hide()
        {
            try
            {
                if (_input != null) _input.DeactivateInputField();
            }
            catch { }
            if (Alive(_root)) _root.SetActive(false);
        }

        /// <summary>把面板/文本域/输入框/**正文**一起贴合一次（面板已激活、尺寸已知），并打一行几何。
        ///
        /// 为什么需要：`Build` 发生在对话宿主创建期，整棵面板是 **inactive** 的 ——
        /// 锚点拉伸要等到激活后的那次布局才结算。真机 11-10 的两次报障都落在这里：
        ///   ① 「输入框只有一小块」= 输入框自己的矩形没结算；
        ///   ② 「输入框撑满了、正文还是四行」= 正文（Text 子节点）在模板里是固定 780×70 + Truncate，
        ///      从来没人动过它。
        /// 所以这里显式重设四个矩形（面板 / 域 / 输入 / 正文），幂等、不依赖时序。
        /// 日志那串数字就是下次排查的入口：**哪一层不对，一眼能看出来**。
        /// </summary>
        private static void FitInputToField()
        {
            try
            {
                if (_root == null) return;
                var rootRt = _root.GetComponent<RectTransform>();
                if (rootRt == null) return;
                if (_input == null)
                {
                    // 真机 11-10 的日志里出现过「输入=-1x-1 输入父=?」—— `_input` 是空的，
                    // 于是三处尺寸一个都没写进去。空着就**当场从文本域里捞**，别静默跳过。
                    var found = _fieldRt != null ? _fieldRt.GetComponentsInChildren<InputField>(true) : null;
                    if (found != null && found.Length > 0) _input = found[0];
                    Log("Show: _input 为空 → 从文本域里重新取到 "
                        + (found != null ? found.Length : 0) + " 个");
                }
                // 面板尺寸按 **Build 时量到的 BG** 算：真机 11-10 证明 Show 时按父级现量会读到
                // 根自己的 rect（600×760），面板因此被算矮 40。
                float bgW = _bgW > 1f ? _bgW : 1200f;
                float bgH = _bgH > 1f ? _bgH : 800f;
                float panelH = bgH - TopInset;
                float fieldH = panelH - (HeadH + 8f) - (PadB + BtnH + 8f + StatusH + 8f);
                if (fieldH < 80f) fieldH = 80f;        // 兜底：量错了也别算出个负高度

                // ★ 面板：显式尺寸（左上锚点 + pivot），不依赖"父级拉伸在激活后结算"
                rootRt.anchorMin = new Vector2(0f, 1f);
                rootRt.anchorMax = new Vector2(0f, 1f);
                rootRt.pivot = new Vector2(0f, 1f);
                rootRt.anchoredPosition = new Vector2(0f, -TopInset);
                rootRt.sizeDelta = new Vector2(PanelW, panelH);
                if (_fieldRt != null)
                {
                    _fieldRt.anchorMin = new Vector2(0f, 1f);
                    _fieldRt.anchorMax = new Vector2(0f, 1f);
                    _fieldRt.pivot = new Vector2(0f, 1f);
                    _fieldRt.anchoredPosition = new Vector2(PadX, -(HeadH + 8f));
                    _fieldRt.sizeDelta = new Vector2(PanelW - 2f * PadX, fieldH);
                }
                var irt = _input != null ? _input.GetComponent<RectTransform>() : null;
                if (irt != null)
                {
                    irt.anchorMin = new Vector2(0f, 1f);
                    irt.anchorMax = new Vector2(0f, 1f);
                    irt.pivot = new Vector2(0f, 1f);
                    irt.anchoredPosition = new Vector2(2f, -2f);
                    irt.sizeDelta = new Vector2(PanelW - 2f * PadX - 4f, fieldH - 4f);
                }
                LayoutRebuilder.ForceRebuildLayoutImmediate(rootRt);
                RelayoutText();          // ★ 正文也要跟着撑开 —— 这才是"只显示四行"的正主
                ApplyTextScroll();
                Log(string.Format(
                    "Show 几何: BG={0:F0}x{1:F0} 面板={2:F0}x{3:F0} 域={4:F0}x{5:F0} 输入={6:F0}x{7:F0}"
                    + " 正文={8:F0}x{9:F0} 全文高={10:F0} 字数={11} 滚动={12}",
                    bgW, bgH,
                    rootRt.rect.width, rootRt.rect.height,
                    _fieldRt != null ? _fieldRt.rect.width : -1f,
                    _fieldRt != null ? _fieldRt.rect.height : -1f,
                    irt != null ? irt.rect.width : -1f, irt != null ? irt.rect.height : -1f,
                    _input != null && _input.textComponent != null
                        ? _input.textComponent.rectTransform.rect.width : -1f,
                    _input != null && _input.textComponent != null
                        ? _input.textComponent.rectTransform.rect.height : -1f,
                    _textHeight,
                    _input != null && _input.text != null ? _input.text.Length : -1,
                    _scrollReady ? "开" : "关"));
            }
            catch (Exception e)
            {
                Log("贴合文本域失败: " + e.Message);
            }
        }

        /// <summary>把正文（`InputField.textComponent`）从模板那个"固定小框"改成铺满文本域。
        ///
        /// 高度两种口径（这就是"看不到尾巴"和"盖住发送键"之间的那道闸）：
        ///   · 滚轮可用（`_scrollReady`）→ 按**全文高度**铺开，超长部分由文本域上的 `RectMask2D`
        ///     裁掉，滚轮上下翻 —— 既铺满、又不外溢；
        ///   · 滚轮没就位 → 退回文本域高度 + `Truncate`（宁可看不到尾巴，也不许压到发送键上）。
        /// </summary>
        private static void RelayoutText()
        {
            var t = _input != null ? _input.textComponent : null;
            if (t == null || _fieldRt == null) return;
            float viewW = _fieldRt.rect.width;
            float viewH = _fieldRt.rect.height;
            if (viewW <= 1f) return;
            float w = viewW - 8f;                  // 左右各留 4
            var trt = t.rectTransform;
            trt.anchorMin = new Vector2(0f, 1f);
            trt.anchorMax = new Vector2(1f, 1f);
            trt.pivot = new Vector2(0.5f, 1f);
            _textHeight = MeasureTextHeight(t, w);
            float h = _scrollReady ? (_textHeight + 8f) : (viewH - 8f);
            if (h < viewH - 8f && _scrollReady) h = viewH - 8f;
            _appliedTextH = h;
            trt.sizeDelta = new Vector2(-8f, h);
            trt.anchoredPosition = new Vector2(0f, -4f - _scrollY);
            t.horizontalOverflow = HorizontalWrapMode.Wrap;
            t.verticalOverflow = _scrollReady ? VerticalWrapMode.Overflow : VerticalWrapMode.Truncate;
            t.alignment = TextAnchor.UpperLeft;
        }

        /// <summary>量"这段话按这个宽度排下来有多高"（与 `ConfigPresenter.UpdatePromptLayout` 同一套算法：
        /// 首行顶到末行底，比 `preferredHeight` 准，最后一行不会被吃掉半个字高）。</summary>
        private static float MeasureTextHeight(Text t, float width)
        {
            try
            {
                var gen = new TextGenerator();
                var settings = t.GetGenerationSettings(new Vector2(width, 1000000f));
                if (gen.Populate(t.text ?? "", settings) && gen.lineCount > 0)
                {
                    float top0 = gen.lines[0].topY;
                    var last = gen.lines[gen.lineCount - 1];
                    float h = top0 - (last.topY - last.height);
                    if (h > 0f) return h;
                }
            }
            catch { }
            try { return t.preferredHeight; } catch { }
            return 0f;
        }

        /// <summary>把当前滚动量写进正文矩形（夹在 0 ~ 全文高 − 视口高之间）。</summary>
        private static void ApplyTextScroll()
        {
            var t = _input != null ? _input.textComponent : null;
            if (t == null || _fieldRt == null) return;
            float max = _textHeight + 8f - _fieldRt.rect.height;
            if (max < 0f) max = 0f;
            if (_scrollY < 0f) _scrollY = 0f;
            if (_scrollY > max) _scrollY = max;
            var trt = t.rectTransform;
            trt.anchoredPosition = new Vector2(trt.anchoredPosition.x, -4f - _scrollY);
        }

        /// <summary>给文本域装"遮罩 + 滚轮"。两步都成了才允许正文按全文高度铺开（`_scrollReady`）；
        /// 任何一步失败就退回落在一屏内截断 —— 这个面板宁可少看尾巴，也不能把发送键盖住。</summary>
        private static void PrepareScroll(RectTransform field)
        {
            try
            {
                if (field == null) return;
                if (field.GetComponent<RectMask2D>() == null) field.gameObject.AddComponent<RectMask2D>();
                // ★ IL2CPP 铁律：自家 MonoBehaviour 必须先注册进 Il2Cpp 类型系统，否则
                //   `AddComponent<T>` 直接抛（"组件从未注册"），而这个面板把它 catch 成
                //   "滚轮没搭上" —— 表现就是滚轮永远不动、且看起来像 UI 问题。
                //   注册本身重复调用会抛，故用静态布尔记账（失败不置位，下次重试）。
                //   与 ChatWindow/ChatPresenter/HoverTip 同一个 ClassInjector 路子。
                if (!_scrollDriverRegistered)
                {
                    UnhollowerRuntimeLib.ClassInjector.RegisterTypeInIl2Cpp<CompactEditScrollDriver>();
                    _scrollDriverRegistered = true;
                }
                if (field.GetComponent<CompactEditScrollDriver>() == null)
                    field.gameObject.AddComponent<CompactEditScrollDriver>();
                _scrollReady = true;
                Log("文本域：遮罩 + 滚轮就位（正文按全文高度铺开，滚轮上下翻）");
            }
            catch (Exception e)
            {
                _scrollReady = false;
                Log("遮罩/滚轮没搭上 → 正文退回落在一屏内截断: " + e.Message);
            }
        }

        // ---- 给 `CompactEditScrollDriver`（文件末尾那个顶层小类）用的两个口 ----

        /// <summary>滚轮：`dy` 就是 UGUI 的 `scrollDelta.y`（向上为正）。</summary>
        internal static void ScrollBy(float dy)
        {
            if (!IsOpen) return;
            _scrollY -= dy;                       // 滚轮向上 = 看前面 = 数值变小
            ApplyTextScroll();
        }

        /// <summary>每帧一次：鼠标压在文本域上时把滚轮换算成滚动量。
        ///
        /// 只认"鼠标在框里" —— 否则在对话框正文上滚历史会把纪要偷偷滚走。
        /// 相机传 `null`：对话窗是 Overlay 画布，与 `ChatWindow.InTitleBand` 同一套判据。
        /// </summary>
        internal static void TickWheel()
        {
            try
            {
                if (!IsOpen || _fieldRt == null) return;
                float dy = Input.mouseScrollDelta.y;
                if (Mathf.Abs(dy) < 0.01f) return;
                if (!RectTransformUtility.RectangleContainsScreenPoint(_fieldRt, Input.mousePosition, null))
                    return;
                ScrollBy(dy * 40f);
            }
            catch { }
        }

        /// <summary>漂移兜底：InputField 自己也会摆正文矩形（`AssignPositioningIfNeeded` 会把
        /// 锚点从 caret 抄回来），被它写回去正文就退回"一小块"。只在**看出来不对**时才重设，
        /// 每帧只是几次浮点比较。</summary>
        internal static void GuardTextLayout()
        {
            try
            {
                if (!IsOpen) return;
                var t = _input != null ? _input.textComponent : null;
                if (t == null) return;
                var trt = t.rectTransform;
                bool drift = trt.anchorMin != new Vector2(0f, 1f)
                          || trt.anchorMax != new Vector2(1f, 1f)
                          || Mathf.Abs(trt.sizeDelta.x + 8f) > 0.5f
                          || Mathf.Abs(trt.sizeDelta.y - _appliedTextH) > 0.5f;
                if (drift)
                {
                    RelayoutText();
                    ApplyTextScroll();
                }
            }
            catch { }
        }

        /// <summary>发送中：按钮禁用 + 文案「发送中…」（防连点发两遍）。</summary>
        public static void SetSending(bool sending)
        {
            _sending = sending;
            SetSendEnabled(!sending);
            if (_sendLabel != null) _sendLabel.text = sending ? "发送中…" : "发送";
            if (sending) SetStatus("正在传回 Python…", StatusColor);
        }

        /// <summary>失败：红字原因、按钮恢复、**面板不关**（玩家改的文字还在）。</summary>
        public static void ShowError(string reason)
        {
            _sending = false;
            SetSendEnabled(true);
            if (_sendLabel != null) _sendLabel.text = "发送";
            SetStatus(string.IsNullOrEmpty(reason) ? "修改未能传回 Python" : reason, ErrorColor);
        }

        /// <summary>成功：面板收起（正文已回到折叠行上，框里留着旧值只会误导）。</summary>
        public static void ShowDone(string note)
        {
            _sending = false;
            SetSendEnabled(true);
            if (_sendLabel != null) _sendLabel.text = "发送";
            SetStatus(string.IsNullOrEmpty(note) ? "已传回 Python" : note, OkColor);
            Hide();
        }

        private static void SetSendEnabled(bool on)
        {
            if (_sendBtn != null) _sendBtn.interactable = on;
            if (_sendLabel != null) _sendLabel.color = on ? Color.white : new Color(1f, 1f, 1f, 0.55f);
        }

        private static void SetStatus(string text, Color color)
        {
            if (_status == null) return;
            _status.text = text ?? "";
            _status.color = color;
        }

        private static void OnSendClicked()
        {
            if (_sending) return;
            string text = _input != null ? (_input.text ?? "") : "";
            if (text.Trim().Length == 0)
            {
                ShowError("纪要不能为空");
                return;
            }
            if (text.Length > MaxChars)
            {
                ShowError("纪要过长（上限 " + MaxChars + " 字）");
                return;
            }
            var h = _onSubmit;
            if (h == null)
            {
                ShowError("没有接收方（对话窗未接上）");
                return;
            }
            h(text);
        }

        // ------------------------------------------------------------------
        // 小工具（与 ToolPanel 同一族；各自私有一份，避免动那个文件的契约测试）
        // ------------------------------------------------------------------

        private static bool Alive(GameObject go)
        {
            // Unity 的 `!= null` 重载会认"已销毁"（== null）⇒ 这一句就是"树还在"的完整判据。
            // 不要写成 `activeSelf`：面板出厂是隐藏的，按显隐判会把"搭好了但没开"误判成"没搭"。
            try { return go != null; } catch { return false; }
        }


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
            try { t.font = ChatUiBuilder.ResolveSharedFont(); } catch { }
            t.raycastTarget = false;
            t.horizontalOverflow = HorizontalWrapMode.Wrap;
            t.verticalOverflow = VerticalWrapMode.Truncate;
            t.supportRichText = false;
            return t;
        }

        private static GameObject NewButton(string name, Transform parent, string label, int size,
                                            Color labelColor, Color bg)
        {
            var rt = MakeRect(name, parent);
            rt.gameObject.AddComponent<CanvasRenderer>();
            var img = rt.gameObject.AddComponent<Image>();
            img.sprite = null;
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


        private static void Log(string msg)
        {
            try { ModMain.P("[CompactEditPanel] " + msg); } catch { }
        }
    }

    /// <summary>「修改压缩纪要」文本域的滚轮接收器 —— 逻辑全在 `CompactEditPanel` 里。
    ///
    /// 为什么**轮询 `Input.mouseScrollDelta`** 而不是实现 `IScrollHandler`：
    /// 本工程的 UGUI 接口在 Il2Cpp 程序集里是**类**（`class X : MonoBehaviour, IScrollHandler`
    /// 会直接 CS1721 编不过），而且全工程没有一个自研组件实现过事件接口 —— 没有先例可抄。
    /// 对话窗自己的滚动也是这么做的（`ChatWindow.Update` 里读同一个 `Input.mouseScrollDelta`），
    /// 所以这里沿用同一条已验证的路子。
    ///
    /// 写成**顶层 internal 类**而不是嵌套类：MelonLoader 的 `AddComponent<T>` 对嵌套
    /// MonoBehaviour 的坑多，顶层类是工程里已验证过的形态（`HoverTip` / `ChatWindow` 同款）。
    /// </summary>
    public sealed class CompactEditScrollDriver : MonoBehaviour
    {
        private void Update()
        {
            CompactEditPanel.TickWheel();
            CompactEditPanel.GuardTextLayout();
        }
    }
}
