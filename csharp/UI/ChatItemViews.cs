/// <summary>
/// 对话 UI 行视图 —— 三个普通 C# 类（非 MonoBehaviour，无逐帧逻辑），只操纵向 API：
///  - ChatBubble：从用户/NPC 模板克隆成一条气泡行（行 = 全宽 Row + 模板气泡实例）
///  - StepGroup：  回合过程折叠组（头部可点 + 内容容器，AddCall/AddResult 各加一行）
///  - ChatDivider：「—— 主动传音 ——」式分隔条
///
/// 只做「实例化 + 填文本」，不关心消息从哪来、不解析协议；模板引用来自 ChatWindowRefs。
/// 视图创建的行由 ChatWindow 统一登记行数（上限裁剪），这里不关心。
/// IL2CPP 下点的回调统一走 Button + ClickUtils（委托桥三步写法，ClickCatcher 已退役）。
/// </summary>
using System.Text;
using UnityEngine;
using UnityEngine.UI;

namespace AgentLoopBridge
{
    /// <summary>单条消息行（微信式：头像 + 气泡组成一行，由行模板克隆，宽自动、高自适应）。
    /// 高度驱动链：气泡内 Text.preferredHeight → 气泡自身 VLG+CSF 撑高 → 行根 HLG 取最高子项
    /// （头像恒 80×80 / 长文本气泡高取大）→ 外层 Content VLG 同步行高。
    /// 头像模板立绘由 AbChatPanel 开窗时预填，克隆行共享同一纹理；无立绘时该头像隐藏（气泡贴边）。</summary>
    public class ChatBubble
    {
        /// <summary>行根（行模板克隆，挂在 Content 下，内含头像+气泡）</summary>
        public readonly RectTransform Row;
        /// <summary>行实例（模板克隆根）</summary>
        public readonly GameObject Root;
        /// <summary>气泡文本（流式合并/终稿覆盖直接写它）</summary>
        public readonly Text Text;

        public ChatBubble(ChatWindowRefs refs, Transform contentParent, bool isUser, string text)
        {
            GameObject tpl = isUser ? refs.UserRowTemplate : refs.NpcRowTemplate;
            if (tpl == null)   // 模板缺失兜底（装配校验已拦，正常不会走到）
            {
                Root = null; Row = null; Text = null;
                return;
            }
            // 克隆整行模板到 Content：行模板自持 HorizontalLayoutGroup 完成左右对齐与内部排布
            Root = TemplateUtils.Instantiate(tpl, contentParent);
            if (Root == null)
            {
                Row = null; Text = null;
                return;
            }
            Row = Root.GetComponent<RectTransform>();
            // 行模板 inactive：克隆只激活行根，子节点（头像+气泡）须显式激活，否则恒不可见
            int cnt = Root.transform.childCount;
            for (int i = 0; i < cnt; i++)
            {
                Transform c = Root.transform.GetChild(i);
                if (c != null) c.gameObject.SetActive(true);
            }
            // 头像：无立绘纹理（模板未预填）时隐藏，避免空白方块；气泡贴边
            string avatarName = isUser ? "Useravatar" : "Npcavatar";
            var avatarTr = Root.transform.Find(avatarName);
            if (avatarTr != null)
            {
                var av = avatarTr.GetComponent<RawImage>();
                bool show = av != null && av.texture != null;
                avatarTr.gameObject.SetActive(show);
            }
            Text = Root.GetComponentInChildren<Text>(true);
            SetText(text);
        }

        public void SetText(string text)
        {
            if (Text != null) Text.text = text ?? "";
        }
    }

    /// <summary>
    /// 回合过程折叠组：头部「⚙ 正在行动…」→ 完成后「▾ {label} (n)」；首行到达即自动展开，
    /// 头部点击可手动收起/展开；内部 AddCall / AddResult（行动记录）与 AddPart（系统组装/
    /// 运行时上下文等输入侧块）各加一行。行全文自适应高度直出——不截断、无内部滚动，
    /// 超长内容靠外层聊天列表滚动查看。
    /// </summary>
    public class StepGroup
    {
        /// <summary>组根（注册行数用）</summary>
        public readonly GameObject Root;
        private readonly Button _header;
        private readonly Text _headerText;
        private readonly RectTransform _body;
        private readonly string _label;          // 组标题（行动记录 / 系统组装 / 运行时上下文）
        private bool _bodyExpanded;
        private int _count;
        private readonly bool _autoOpen;         // true=行动记录（首行自动展开、完成后保持）；false=sys/ctx（默认收起）

        /// <param name="label">组标题（默认「行动记录」）；输入侧复用同一模板传「系统组装」/「运行时上下文」。</param>
        /// <param name="autoOpen">行动记录传 true：工具调用全文直出、首行自动展开、完成后保持展开；
        /// 系统组装/运行时上下文传 false：块默认收起，头部点击展开（2026-09-09 用户澄清：
        /// sys/ctx 不要每次全量直出，只按差分行显示）。</param>
        public StepGroup(ChatWindowRefs refs, Transform contentParent, string label = "行动记录", bool autoOpen = true)
        {
            _label = label;
            _autoOpen = autoOpen;
            Root = TemplateUtils.Instantiate(refs.StepGroupTemplate, contentParent);
            var headerGo = Root != null ? Root.transform.Find("Header") : null;
            if (headerGo != null)
            {
                // 实机根因：AB 预制件里模板子节点 Header/Body 自持 active=0，
                // Instantiate 只激活克隆根不带动子节点 → Header 永远不可见，VLG/CSF 组高算出 ≈0，
                // 行动记录整条隐形且无任何报错。必须显式激活 Header；Body 保持收起语义。
                headerGo.gameObject.SetActive(true);
                _header = headerGo.GetComponent<Button>();
                _headerText = headerGo.GetComponentInChildren<Text>(true);
            }
            _body = Root != null ? Root.transform.Find("Body")?.GetComponent<RectTransform>() : null;
            if (_body != null) _body.gameObject.SetActive(false);
            if (_header != null) ClickUtils.Attach(_header, ToggleBody);
        }

        /// <summary>回合完成态：行动记录保持展开（全文观察）；sys/ctx 旧块收起让位。</summary>
        public void Finish()
        {
            RefreshHeader();
            if (!_autoOpen && _body != null)
            {
                _body.gameObject.SetActive(false);
                _bodyExpanded = false;
            }
        }

        /// <summary>当前行数归零并刷新标题（供复用/重建）</summary>
        public void Clear()
        {
            _count = 0;
            RefreshHeader();
        }

        private void RefreshHeader()
        {
            if (_headerText != null)
                _headerText.text = (_bodyExpanded ? "▾ " : "▸ ") + _label + " (" + _count + ")";
        }

        public void AddCall(string name, string argsJson)
        {
            _count++;
            RefreshHeader();
            string brief = FlattenArgs(argsJson);
            AddTextLine("▶ " + (name ?? "") + (brief.Length > 0 ? "  —  " + brief : ""),
                        new Color(0f, 0f, 0f, 1f));
        }

        public void AddResult(string text, bool isError)
        {
            _count++;
            RefreshHeader();
            AddResultLine(text ?? "", isError ? "✗ " : "✓ ");
        }

        /// <summary>输入侧通用段行（sys/ctx 等）：可折叠行——收起显示「【标签】+摘要」，点击展开全文、
        /// 再点收回（变更段标 ↑）。</summary>
        public void AddPart(string label, string text, bool changed)
        {
            _count++;
            RefreshHeader();
            AddCollapsibleLine((changed ? "↑ " : "") + "【" + (string.IsNullOrEmpty(label) ? "?" : label) + "】\n" + (text ?? ""));
        }

        /// <summary>折叠行（重做）：容器 VLG+CSF 常开（行高永远=当前文本的 preferred），
        /// 点击只在「摘要截断文本」与「全文」间切换——高度随文本自适应，无手动 sizeDelta。
        /// 有意不用旧版的 ScrollRect+RectMask2D 定高视口结构（该结构下点击展开真机失效），
        /// 也不做全文直出（sys/ctx 每回合全量刷屏，用户明确不要）。</summary>
        private void AddCollapsibleLine(string text)
        {
            var go = new GameObject("Part");
            var rt = go.AddComponent<RectTransform>();
            go.AddComponent<CanvasRenderer>();
            var img = go.AddComponent<Image>();
            rt.SetParent(_body, false);
            img.color = new Color(0f, 0f, 0f, 0.16f);
            img.raycastTarget = true;   // 折叠行整行可点（命中 Image → Button）

            AttachAutoText(go, "", Color.black, TextAnchor.UpperLeft);
            var txt = go.GetComponentInChildren<Text>(true);

            string full = text ?? "";
            string brief = BriefText(full);
            txt.text = brief;

            bool expanded = false;
            var btn = go.AddComponent<Button>();
            btn.targetGraphic = img;
            ClickUtils.Attach(btn, () =>
            {
                expanded = !expanded;
                if (txt != null) txt.text = expanded ? full : brief;
            });
        }

        /// <summary>收起态摘要：压平换行截 ~80 字（≈2 行高），全文点击展开可见。</summary>
        private static string BriefText(string s)
        {
            if (string.IsNullOrEmpty(s)) return "";
            string one = s.Replace("\r", "").Replace("\n", " ");
            return one.Length > 80 ? one.Substring(0, 80) + "…" : one;
        }

        /// <summary>组内普通说明行（调用行）：自适应高度 + 自动换行，全文显示不截断。</summary>
        private void AddTextLine(string text, Color color)
        {
            EnsureOpen();
            var go = new GameObject("Line");
            var rt = go.AddComponent<RectTransform>();
            rt.SetParent(_body, false);
            AttachAutoText(go, text, color, TextAnchor.UpperLeft);
        }

        /// <summary>结果/段行：自适应高度全文直出（不截断、无内部滚动、无点击切换）；
        /// 带浅底色区分调用行。</summary>
        private void AddResultLine(string text, string prefix = "")
        {
            EnsureOpen();
            var go = new GameObject("Result");
            var rt = go.AddComponent<RectTransform>();
            go.AddComponent<CanvasRenderer>();
            var img = go.AddComponent<Image>();
            rt.SetParent(_body, false);
            img.color = new Color(0f, 0f, 0f, 0.16f);
            img.raycastTarget = false;
            AttachAutoText(go, prefix + text, Color.black, TextAnchor.UpperLeft);
        }

        /// <summary>首行到达即自动展开 Body——仅行动记录（_autoOpen=true）；sys/ctx 保持收起。</summary>
        private void EnsureOpen()
        {
            if (!_autoOpen) return;
            if (_body == null || _body.gameObject.activeSelf) return;
            _body.gameObject.SetActive(true);
            _bodyExpanded = true;
            RefreshHeader();
        }

        /// <summary>自适应高度全文行（ThinkGroup 同款已实证结构）：容器 VLG+CSF 按内容撑高，
        /// 子 Text 宽度由 VLG 控制换行、高度按全文 preferred —— 无裁剪无内部滚动，
        /// 超长内容靠外层聊天列表滚动。</summary>
        private static void AttachAutoText(GameObject container, string text, Color color, TextAnchor align)
        {
            var vlg = container.AddComponent<VerticalLayoutGroup>();
            vlg.padding = new RectOffset(10, 10, 4, 4);
            vlg.spacing = 0f;
            vlg.childAlignment = TextAnchor.UpperLeft;
            vlg.childControlWidth = true;
            vlg.childControlHeight = true;
            vlg.childForceExpandWidth = true;
            vlg.childForceExpandHeight = false;
            var csf = container.AddComponent<ContentSizeFitter>();
            csf.horizontalFit = ContentSizeFitter.FitMode.Unconstrained;
            csf.verticalFit = ContentSizeFitter.FitMode.PreferredSize;

            var textGo = new GameObject("Text");
            textGo.AddComponent<CanvasRenderer>();
            var txt = textGo.AddComponent<Text>();
            var tcsf = textGo.AddComponent<ContentSizeFitter>();
            textGo.GetComponent<RectTransform>().SetParent(container.transform, false);
            tcsf.horizontalFit = ContentSizeFitter.FitMode.Unconstrained;
            tcsf.verticalFit = ContentSizeFitter.FitMode.PreferredSize;
            txt.text = text ?? "";
            txt.fontSize = 14;
            txt.color = color;
            txt.font = ChatUiBuilder.ResolveSharedFont();
            txt.fontStyle = FontStyle.Bold;
            txt.alignment = align;
            txt.raycastTarget = false;
            txt.horizontalOverflow = HorizontalWrapMode.Wrap;
            txt.verticalOverflow = VerticalWrapMode.Overflow;
        }

        private void ToggleBody()
        {
            _bodyExpanded = !_bodyExpanded;
            if (_body != null) _body.gameObject.SetActive(_bodyExpanded);
            RefreshHeader();
        }

        /// <summary>参数全文（仅压平换行，不再截断——完整观察需要）。</summary>
        private static string FlattenArgs(string argsJson)
        {
            if (string.IsNullOrEmpty(argsJson)) return "";
            return argsJson.Replace("\r", "").Replace("\n", " ");
        }
    }

    /// <summary>
    /// 内心思量折叠容器（think 通道）：Header 默认收起显示字数，点击展开全文；
    /// 流式增量 AppendDelta 实时涨字数（展开时才刷新正文，省 Canvas rebuild）。
    /// 纯代码构建（不依赖预制体模板），AB 版与代码版均可直接用。
    /// </summary>
    public class ThinkGroup
    {
        public readonly GameObject Root;
        private readonly Text _headerText;
        private readonly GameObject _bodyGo;
        private readonly Text _bodyText;
        private readonly StringBuilder _buf = new StringBuilder();
        private bool _expanded;
        private int _chars;

        public ThinkGroup(Transform contentParent)
        {
            var go = new GameObject("ThinkGroup");
            Root = go;
            var rt = go.AddComponent<RectTransform>();
            go.AddComponent<CanvasRenderer>();
            var img = go.AddComponent<Image>();
            img.color = new Color(0f, 0f, 0f, 0.10f);
            img.raycastTarget = true;
            var btn = go.AddComponent<Button>();
            btn.targetGraphic = img;

            var vlg = go.AddComponent<VerticalLayoutGroup>();
            vlg.padding = new RectOffset(8, 8, 4, 4);
            vlg.spacing = 2f;
            vlg.childControlWidth = true;
            vlg.childControlHeight = true;
            vlg.childForceExpandWidth = true;
            vlg.childForceExpandHeight = false;
            var csf = go.AddComponent<ContentSizeFitter>();
            csf.verticalFit = ContentSizeFitter.FitMode.PreferredSize;

            rt.SetParent(contentParent, false);
            rt.anchorMin = Vector2.zero;
            rt.anchorMax = Vector2.one;
            rt.offsetMin = Vector2.zero;
            rt.offsetMax = Vector2.zero;
            rt.pivot = new Vector2(0.5f, 0.5f);

            // Header
            var headerGo = new GameObject("Header");
            headerGo.AddComponent<CanvasRenderer>();
            var headerRt = headerGo.AddComponent<RectTransform>();
            headerRt.SetParent(Root.transform, false);
            _headerText = headerGo.AddComponent<Text>();
            _headerText.fontSize = 14;
            _headerText.color = Color.black;
            _headerText.font = ChatUiBuilder.ResolveSharedFont();
            _headerText.fontStyle = FontStyle.Bold;
            _headerText.alignment = TextAnchor.MiddleLeft;
            _headerText.raycastTarget = false;
            _headerText.horizontalOverflow = HorizontalWrapMode.Overflow;

            // Body（默认收起）
            var bodyGo = new GameObject("Body");
            _bodyGo = bodyGo;
            bodyGo.AddComponent<CanvasRenderer>();
            var bodyRt = bodyGo.AddComponent<RectTransform>();
            bodyRt.SetParent(Root.transform, false);
            var bodyCsf = bodyGo.AddComponent<ContentSizeFitter>();
            bodyCsf.verticalFit = ContentSizeFitter.FitMode.PreferredSize;
            _bodyText = bodyGo.AddComponent<Text>();
            _bodyText.fontSize = 14;
            _bodyText.color = Color.black;
            _bodyText.font = ChatUiBuilder.ResolveSharedFont();
            _bodyText.fontStyle = FontStyle.Bold;
            _bodyText.alignment = TextAnchor.UpperLeft;
            _bodyText.raycastTarget = false;
            _bodyText.horizontalOverflow = HorizontalWrapMode.Wrap;
            _bodyText.verticalOverflow = VerticalWrapMode.Overflow;
            bodyGo.SetActive(false);

            ClickUtils.Attach(btn, Toggle);
            UpdateHeader();
        }

        /// <summary>流式增量：字数实时涨；展开时才写正文（收起时省刷新）</summary>
        public void AppendDelta(string token)
        {
            if (string.IsNullOrEmpty(token)) return;
            _buf.Append(token);
            _chars += token.Length;
            UpdateHeader();
            if (_expanded) _bodyText.text = _buf.ToString();
        }

        /// <summary>整段写入（非流式 / 历史回放）</summary>
        public void SetFull(string text)
        {
            _buf.Length = 0;
            _buf.Append(text ?? "");
            _chars = _buf.Length;
            UpdateHeader();
            if (_expanded) _bodyText.text = _buf.ToString();
        }

        /// <summary>流结束：定稿</summary>
        public void Finish()
        {
            UpdateHeader();
            _bodyText.text = _buf.ToString();
        }

        private void Toggle()
        {
            _expanded = !_expanded;
            _bodyGo.SetActive(_expanded);
            if (_expanded) _bodyText.text = _buf.ToString();
            UpdateHeader();
        }

        private void UpdateHeader()
        {
            if (_headerText != null)
                _headerText.text = (_expanded ? "▾ 内心思量 · " : "▸ 内心思量 · ") + _chars + " 字";
        }
    }

    /// <summary>分隔条（主动传音 / 更早历史等上下文标记）</summary>
    public class ChatDivider
    {
        public readonly GameObject Root;
        private readonly Text _text;

        public ChatDivider(ChatWindowRefs refs, Transform contentParent)
        {
            Root = TemplateUtils.Instantiate(refs.DividerTemplate, contentParent);
            _text = Root.GetComponent<Text>();
        }

        public void SetText(string label) { if (_text != null) _text.text = label ?? ""; }
    }

    internal static class TemplateUtils
    {
        /// <summary>克隆模板并激活（模板本身 inactive，运行时才实例化）。
        /// 模板引用为 null 时直接返回 null（Instantiate(null) 会抛异常且被 Dispatcher 吞进 TCS 静默消失）</summary>
        public static GameObject Instantiate(GameObject tpl, Transform parent)
        {
            if (tpl == null) return null;
            var go = (GameObject)Object.Instantiate(tpl);
            if (go == null) return null;
            go.transform.SetParent(parent, false);
            go.SetActive(true);
            return go;
        }

        /// <summary>
        /// 安全销毁：Unity 编辑模式（预览工程）必须用 DestroyImmediate，运行时（游戏）
        /// 用 Destroy。用 UNITY_EDITOR 宏自动切换——同一源码两环境共用，游戏行为不变。
        /// </summary>
        public static void DestroySafe(Object obj)
        {
            if (obj == null) return;
#if UNITY_EDITOR
            if (!UnityEditor.EditorApplication.isPlaying)
            {
                Object.DestroyImmediate(obj);
                return;
            }
#endif
            Object.Destroy(obj);
        }
    }
}