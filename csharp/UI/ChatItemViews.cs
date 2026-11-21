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
using System;
using System.Collections.Generic;
using System.Text;
using UnityEngine;
using UnityEngine.UI;

namespace AgentLoopBridge
{
    /// <summary>单条消息行（微信式：头像 + 气泡组成一行，由行模板克隆，宽自动、高自适应）。
    /// 高度驱动链：气泡内 Text.preferredHeight → 气泡自身 VLG+CSF 撑高 → 行根 HLG 取最高子项
    /// （头像恒 80×80 / 长文本气泡高取大）→ 外层 Content VLG 同步行高。
    ///
    /// 头像（2026-09-16 群聊逐行改造）：
    ///  · **不传 speaker**（单聊全部走这条）= 沿用模板预填的那张立绘，克隆行共享纹理 ——
    ///    与改造前逐字节一致，单聊观感零变化；
    ///  · **传 speaker**（群聊每行都传）= 这一行的头像由 `RowAvatars` 按说话人取图决定，
    ///    含"取不到就隐藏" —— 模板里那张陈旧的脸（上一个单聊 NPC）不会被沿用。
    ///    改造前正是它在群聊里让**所有人共用一张脸**，而且零报错。</summary>
    public class ChatBubble
    {
        /// <summary>行根（行模板克隆，挂在 Content 下，内含头像+气泡）</summary>
        public readonly RectTransform Row;
        /// <summary>行实例（模板克隆根）</summary>
        public readonly GameObject Root;
        /// <summary>气泡文本（流式合并/终稿覆盖直接写它）</summary>
        public readonly Text Text;
        /// <summary>本行头像槽位（`Npcavatar`/`Useravatar` 的 RawImage）。
        ///
        /// 对外暴露的唯一消费者是**点击放大立绘**（`BigPortrait.Bind`，由 `ChatWindow` 在建行后挂）——
        /// 挂在这里而不是构造函数里，是因为"这一行该放谁的脸"由 `ChatWindow` 的三态判据
        /// （`SpeakerOf`：null=单聊走窗口对象 / 非空=群聊该说话人 / 空串=不知道）决定，
        /// 而 `ChatBubble` 只看得到 `speaker` 一个入参、拿不到"当前窗口对象"。
        /// 模板缺失或头像节点不存在时为 null（调用方自行判空）。</summary>
        public readonly RawImage Avatar;

        // ---- 气泡宽度策略（11-19）----
        //  气泡盒在预制体里是**死宽 450**（`NpcBubble/UserBubble` 的 `m_SizeDelta.x`），
        //  行模板 HLG 又是 `m_ChildControlWidth = 0`（子节点用自己 sizeDelta）⇒ 布局永不覆盖它。
        //  于是 1200 宽的面板里气泡恒 450、右边空一大片（用户报"气泡全挤在左侧"）。
        //  这里每次写文字后按"内容区可用宽"重算：短句贴合文字、长句夹到上限并换行。
        //  判据纯粹部分在 `BubbleWidthCore`（有单测），这里只负责读 Unity 的 rect。
        private RectTransform _bubble;              // NpcBubble / UserBubble
        private LayoutElement _bubbleLe;            // 有就同步写 preferredWidth（两种机制都覆盖）
        private VerticalLayoutGroup _bubbleVlg;     // 左右 padding 要算进"一行放得下多宽"
        private HorizontalLayoutGroup _rowHlg;      // spacing × 间隙数
        private ChatWindowRefs _refs;               // rect 全读不到时的回退链
        private float _width = -1f;                 // 上一次写入的宽度（去抖用）
        private static bool _diagLogged;            // 整个进程只打一行自检（成功/失败都打）
        /// <summary>群聊行的首次自检**单独一条**（11-20 加）。
        ///
        /// 为什么不能只靠 `_diagLogged`：那是**进程级** static，只会打出**第一条**气泡的数值。
        /// 单聊先开过窗的话，群聊那一整屏的数值永远不会出现在日志里 ——
        /// 群聊溢出就是这样漏掉的（用户报"群聊有些没显示完全"，日志里一条群聊数据都没有）。
        /// 现在单聊与群聊**各打一条**，两条对比就能立刻看出差别在哪一项。
        /// </summary>
        private static bool _diagLoggedRoom;
        /// <summary>本行的说话人（群聊=中文名，单聊=null）。只用于自检分类与日志，不参与布局。</summary>
        private readonly string _speakerName;
        private int _remeasureTries;                // 已重测次数（见 `ShouldWrite` 与 `MaxRemeasureTries`）

        public ChatBubble(ChatWindowRefs refs, Transform contentParent, bool isUser, string text)
            : this(refs, contentParent, isUser, text, null) { }

        /// <param name="speaker">说话人**中文名**（群聊逐行头像的取图键；null/空 = 沿用模板立绘）。
        /// 玩家行传玩家名、NPC 行传该 NPC 名（见 `ChatWindow.AppendUserMessage` / `AppendNpcStatic`）。</param>
        /// <param name="speakerId">说话人的**精确 unitID**（群聊帧/投影随行携带）；给了就不按名解析。
        /// 详见 `RowAvatars.Apply` —— 那里是这条链的终点。</param>
        public ChatBubble(ChatWindowRefs refs, Transform contentParent, bool isUser, string text,
                          string speaker, string speakerId = null)
        {
            // 先记下"这一行是不是群聊行"（仅用于自检日志分类，见 `ShouldLogDiag`）。
            // 必须放在任何提前 return 之前：模板缺失分支也要能分类。
            _speakerName = speaker;
            GameObject tpl = isUser ? refs.UserRowTemplate : refs.NpcRowTemplate;
            if (tpl == null)   // 模板缺失兜底（装配校验已拦，正常不会走到）
            {
                Root = null; Row = null; Text = null; Avatar = null;
                return;
            }
            // 克隆整行模板到 Content：行模板自持 HorizontalLayoutGroup 完成左右对齐与内部排布
            Root = TemplateUtils.Instantiate(tpl, contentParent);
            if (Root == null)
            {
                Row = null; Text = null; Avatar = null;
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
            // 头像
            string avatarName = isUser ? "Useravatar" : "Npcavatar";
            var avatarTr = Root.transform.Find(avatarName);
            // 先落到字段上：`Avatar` 是 readonly，必须在构造函数里定完，而下面还有分支
            Avatar = avatarTr != null ? avatarTr.GetComponent<RawImage>() : null;
            if (avatarTr != null)
            {
                var av = Avatar;
                if (speaker != null)
                {
                    // 群聊：逐行取图，模板那张陈旧的脸**不参与**（空串=不知道是谁 → 隐藏，
                    // 也绝不落到下面那条"沿用模板"的路上）。
                    // speakerId 优先：有精确 id 就不做任何按名解析（见 RowAvatars.Apply）。
                    RowAvatars.Apply(av, speaker, speakerId);
                }
                else
                {
                    // 单聊（speaker == null）：无立绘纹理（模板未预填）时隐藏，避免空白方块；气泡贴边
                    bool show = av != null && av.texture != null;
                    avatarTr.gameObject.SetActive(show);
                }
            }
            Text = Root.GetComponentInChildren<Text>(true);
            // 气泡盒与布局引用（宽度策略用）。逐项可缺：缺了只是退化成"沿用预制体的原宽"，
            // 不能让建行失败 —— 气泡结构一变就整窗没有气泡，比宽了一点糟得多。
            _refs = refs;
            Transform bt = Root.transform.Find(isUser ? "UserBubble" : "NpcBubble");
            _bubble = UiRects.Of(bt);
            _rowHlg = Root.GetComponent<HorizontalLayoutGroup>();
            // 名字对不上时的自救：气泡里的文字**父亲就是气泡盒**（Text 直接挂在 bubble 下）。
            // 11-19 首版就是栽在"只在构造里按名找、找不到就静默什么都不做"——
            // 玩家看到的还是 450 死宽，而日志里一个字都没有。
            if (_bubble == null && Text != null && Text.transform.parent != null)
                _bubble = UiRects.Of(Text.transform.parent);
            if (_bubble != null)
            {
                _bubbleVlg = _bubble.GetComponent<VerticalLayoutGroup>();
                _bubbleLe = _bubble.GetComponent<LayoutElement>();
            }
            SetText(text);
        }

        /// <summary>写正文。走 `BubbleTextCore.Render`（折叠空行 + 富文本白名单 + 配平）——
        /// 这一条是**新建气泡**的唯一入口（单聊/群聊终稿、历史重放、玩家自己发的都经它），
        /// 与流式刷新、终稿覆盖两条路共用同一个整形（几处不一致会让定稿瞬间"跳一下"，
        /// 或者"某一条路露出裸标签"）。</summary>
        public void SetText(string text)
        {
            if (Text != null) Text.text = BubbleTextCore.Render(text);
            ApplyBubbleWidth();
        }

        /// <summary>
        /// 流式刷新专用：全文变了、**宽度要跟着长**。
        ///
        /// 【为什么必须单独一个入口（11-20 补）】
        /// 流式气泡的宽度语义是"随内容增长逐步撑开"。而 `SetText` 只在构造时被调一次，
        /// 之后 `ChatWindow.LateUpdate` 每帧只写 `_bubbleText.text`（避开逐 token 触发布局），
        /// **从不重算宽度** ⇒ 起泡那一刻 `BeginNpcBubble("")` 文字还是空串，
        /// 宽度就在那时被定死（旧代码量不出 ⇒ 直接给上限）。
        ///
        /// 两种错法都要避免：
        ///   · 每帧无条件重算 → 流式 token 很密，每帧写 sizeDelta 会连带触发 Content 整列重排
        ///     （正是 `LateUpdate` 合并刷 Text 要躲的那个卡顿源）；
        ///   · 只在终稿算一次 → 整段流式期间宽度是错的（用户 11-20 截图就是这个）。
        /// 折中：`ApplyBubbleWidth` 内部本来就有 `ShouldRewrite` 去抖（差值 &lt; 1.5 不写），
        /// 所以这里每帧调用是安全的 —— 没变化就什么都不做。
        /// </summary>
        internal void OnStreamTextChanged()
        {
            ApplyBubbleWidth();
        }

        /// <summary>按内容区可用宽重算气泡盒宽度（背景见上面 `_bubble` 那组字段的注释）。
        ///
        /// 三步：① 内容区可用宽（回退链见 `ContentWidth`）→ 减去同行活动头像与间距、
        ///       再减安全边距 = 上限；② 这段文字单行需要多宽（含气泡左右内边距）；
        ///       ③ 取小者，写进 `sizeDelta.x`（行布局不控制宽度，这条一定生效）+ `LayoutElement`。
        ///
        /// ★ 11-19 第二版：**不许再静默失败** ★ 首版把整段包在一个空 `catch` 里、日志只写在
        /// "成功写入"之后 —— 实机结果是气泡一点没变、日志一个字没有，等于无法排障。
        /// 现在：① 量不出文字宽（`Text.preferredWidth` 抛异常或给 0）时**退化为"直接给上限"**，
        /// 至少满足用户要的"最大宽跟对话框走"；② 首个气泡无条件打一行自检（含各中间量与异常原文）。
        /// 变化 &lt; `BubbleWidthCore.RewriteEpsilon` 就不写：流式输出每帧都在改文字，
        /// 每帧重写宽度会连带触发 Content 整列重排。</summary>
        private void ApplyBubbleWidth()
        {
            if (Text == null) return;

            float content = -1f, siblings = 0f, spacing = 0f, cap = -1f;
            float measured = -1f, need = -1f, w = -1f, before = -1f;
            string err = null;
            try
            {
                if (_bubble == null && Text.transform.parent != null)
                    _bubble = UiRects.Of(Text.transform.parent);        // 名字对不上时的自救

                before = _bubble != null ? _bubble.sizeDelta.x : -1f;
                content = ContentWidth();

                int n = Root != null ? Root.transform.childCount : 0;
                int gaps = 0;
                for (int i = 0; i < n; i++)
                {
                    Transform c = Root.transform.GetChild(i);
                    if (c == null || (_bubble != null && c == _bubble.transform)) continue;
                    if (!c.gameObject.activeSelf) continue;
                    // 选中遮罩是**铺满整行**的覆盖层（删除模式），不是布局兄弟 ——
                    // 算进"兄弟宽度"会把气泡挤成最小宽。按名字排除比按宽度猜稳。
                    if (c.name != null && c.name.IndexOf("Overlay", StringComparison.OrdinalIgnoreCase) >= 0) continue;
                    siblings += SiblingWidth(c);
                    gaps++;
                }
                spacing = _rowHlg != null ? _rowHlg.spacing * gaps : 0f;
                cap = BubbleWidthCore.Cap(content, siblings, spacing, BubbleWidthCore.SafetyMargin);

                measured = MeasureTextWidth();                 // -1 = 量不出来
                need = measured > 0f ? BubbleWidthCore.Needed(measured, PadLeft(), PadRight()) : -1f;
                w = need > 0f ? BubbleWidthCore.Fit(need, cap) : -1f;

                // ★ 11-20 补（群聊溢出）：最后一道硬闸 ★
                //   `Cap()` 减掉的头像/间距都来自**读数**，回放/换档那一帧读到脏值就会算出偏大的
                //   cap，`Fit()` 忠实照夹 → 气泡溢出可视区被右侧裁掉（用户群聊截图）。
                //   这里不看任何兄弟节点，只保证"气泡 + 两侧边距 ≤ 内容区宽"。
                if (w > 0f)
                {
                    float clamped = BubbleWidthCore.ClampToContent(w, content, BubbleWidthCore.SafetyMargin);
                    if (clamped < w - 0.5f)
                    {
                        // 只在真的夹掉时才留痕（正常路径永不触发）—— 这是"上游算歪了"的报警灯。
                        try
                        {
                            ModMain.P("[Chat] 气泡宽度被硬闸收窄：" + Fmt(w) + " → " + Fmt(clamped)
                                      + "（内容宽=" + Fmt(content) + " 头像=" + Fmt(siblings)
                                      + " 间距=" + Fmt(spacing) + " 上限=" + Fmt(cap) + "）");
                        }
                        catch { }
                        w = clamped;
                    }
                }

                if (_bubble == null) return;                   // 盒子都没有：只留自检日志

                // ★ 11-20 修「文字没到上限却撑满上限」★
                //   量不出来（新建气泡那一帧 `preferredWidth` 读到 0）时**不写**，排一次下一帧重测。
                //   旧代码在这里 `w = cap`（量不出就按上限给）—— 而非流式气泡只在构造时走这一遍
                //   （流式靠每帧 _deltaDirty 刷新才会重算），于是那一帧的"读 0"被永久固化成满宽。
                //   真机表现：一段 30 字的回话横到天花板；关窗重开（走回放、布局已结算）又正常。
                if (!BubbleWidthCore.ShouldWrite(measured, w))
                {
                    if (_remeasureTries < BubbleWidthCore.MaxRemeasureTries)
                    {
                        _remeasureTries++;
                        if (ShouldLogDiag())
                        {
                            NoteDiagLogged();
                            LogSelfCheck(content, siblings, spacing, cap, measured, need, w, before,
                                         "本帧量不出文字宽 → 已排下一帧重测（第 " + _remeasureTries + " 次）");
                        }
                        RequestRemeasure();
                    }
                    else if (ShouldLogDiag())
                    {
                        NoteDiagLogged();
                        LogSelfCheck(content, siblings, spacing, cap, measured, need, w, before,
                                     "重测 " + BubbleWidthCore.MaxRemeasureTries + " 次仍量不出 → 保持预制体原宽");
                    }
                    return;
                }
                if (!BubbleWidthCore.ShouldRewrite(_width, w))
                {
                    // 去抖：宽度没变就什么都不写，但首泡的自检仍要打出来
                    if (ShouldLogDiag()) { NoteDiagLogged(); LogSelfCheck(content, siblings, spacing, cap, measured, need, w, before, null); }
                    return;
                }
                _width = w;
                Vector2 sd = _bubble.sizeDelta;
                sd.x = w;
                _bubble.sizeDelta = sd;
                if (_bubbleLe != null) _bubbleLe.preferredWidth = w;
            }
            catch (Exception e) { err = e.GetType().Name + ": " + e.Message; }

            if (ShouldLogDiag())
            {
                NoteDiagLogged();
                LogSelfCheck(content, siblings, spacing, cap, measured, need, w, before, err);
            }
        }

        /// <summary>
        /// 这次要不要打自检行？**单聊与群聊各打一条**（见 `_diagLoggedRoom` 的说明）。
        ///
        /// 分类依据是构造时的 `speaker`：群聊行一定带（说话人中文名），单聊恒为 null ——
        /// 这是 `ChatBubble` 构造函数的入参约定，比"看头像有没有显示"可靠
        /// （单聊模板也可能预填了图）。
        /// </summary>
        private bool IsRoomRow { get { return _speakerName != null; } }

        private bool ShouldLogDiag()
        {
            return IsRoomRow ? !_diagLoggedRoom : !_diagLogged;
        }

        private void NoteDiagLogged()
        {
            if (IsRoomRow) _diagLoggedRoom = true; else _diagLogged = true;
        }

        /// <summary>
        /// 排一次"下一帧重新量宽度"。走 `BubbleWidthPump`（一条常驻帧回调，见该类说明），
        /// **不是**给每个气泡挂 MonoBehaviour：气泡是纯 C# 类（非组件），挂不上。
        ///
        /// 为什么必须重测而不能"先给个近似值"：见 `BubbleWidthCore.ShouldWrite` 的说明 ——
        /// 气泡只有一次测量机会（非流式），第一帧读 0 就等于永远读 0。
        /// </summary>
        private void RequestRemeasure()
        {
            BubbleWidthPump.Enqueue(this);
        }

        /// <summary>
        /// 重测入口（由 `BubbleWidthPump` 调用）：再走一遍 `ApplyBubbleWidth`。
        ///
        /// 这里**不重置** `_remeasureTries` —— 计数器归气泡自己所有，封顶后就永远不再排，
        /// 否则"量不出"会变成每帧一次的循环（字体缺失、行被销毁时都会一直量不出）。
        /// 量到了之后 `_width` 已有值，后续 `ShouldRewrite` 会正常去抖。
        /// </summary>
        internal void RetryMeasureWidth()
        {
            try
            {
                if (Row == null || _bubble == null) return;   // 行已销毁：不再重试
                ApplyBubbleWidth();
            }
            catch { }
        }

        /// <summary>首个气泡的自检行 —— 成功/失败都打，且把每个中间量都摊开。
        /// 排障时这一行足够判断"是没接上、读不到 rect、还是文字宽度量不出来"。</summary>
        private void LogSelfCheck(float content, float siblings, float spacing, float cap,
                                  float measured, float need, float w, float before, string err)
        {
            try
            {
                ModMain.P("[Chat] 气泡宽度自检：气泡盒=" + (_bubble != null ? _bubble.name : "<未找到>")
                          + " 写入前=" + Fmt(before)
                          + " 内容宽=" + Fmt(content)
                          + " 头像=" + Fmt(siblings) + " 间距=" + Fmt(spacing)
                          + " 上限=" + Fmt(cap)
                          + " 文字单行=" + Fmt(measured) + " 需要=" + Fmt(need)
                          + " 写入=" + Fmt(w)
                          + (err != null ? " 异常=" + err : ""));
            }
            catch { }
        }

        private static string Fmt(float v) { return v < 0f ? "<无>" : v.ToString("F0"); }

        /// <summary>文字单行需要多宽。**单独 try**：IL2CPP 下 `Text.preferredWidth` 是这条链上
        /// 唯一没被本仓验证过的调用，抛了也只该退化成"给上限"，不该把整段策略带走。</summary>
        private float MeasureTextWidth()
        {
            try
            {
                float tw = Text.preferredWidth;
                return tw > 0f ? tw : -1f;
            }
            catch { return -1f; }
        }

        private int PadLeft()
        {
            try { return _bubbleVlg != null && _bubbleVlg.padding != null ? _bubbleVlg.padding.left : 0; }
            catch { return 0; }
        }

        private int PadRight()
        {
            try { return _bubbleVlg != null && _bubbleVlg.padding != null ? _bubbleVlg.padding.right : 0; }
            catch { return 0; }
        }

        /// <summary>同行非气泡子节点的宽度（头像）。优先 LayoutElement，其次 rect，最后 sizeDelta。</summary>
        private static float SiblingWidth(Transform t)
        {
            var le = t.GetComponent<LayoutElement>();
            if (le != null && le.preferredWidth > 0f) return le.preferredWidth;
            var rt = UiRects.Of(t);
            if (rt != null && rt.rect.width > 0f) return rt.rect.width;
            if (rt != null) return rt.sizeDelta.x;
            return 0f;
        }

        /// <summary>内容区可用宽。回退链：Content → Viewport → Scroll → 窗口宽 − 内缩 → 800。
        /// 为什么必须回退：开窗那一帧（历史重放）`Content.rect` 常常还是 0，
        /// 只读它会得到 cap = 最小宽，整屏气泡缩成一条线。</summary>
        private float ContentWidth()
        {
            float w = 0f;
            if (Row != null && Row.parent != null)
            {
                var p = UiRects.Of(Row.parent);
                if (p != null) w = p.rect.width;
            }
            if (!BubbleWidthCore.ContentUsable(w) && _refs != null && _refs.Viewport != null)
                w = _refs.Viewport.rect.width;
            if (!BubbleWidthCore.ContentUsable(w) && _refs != null && _refs.Scroll != null)
            {
                var srt = UiRects.Of(_refs.Scroll.transform);
                if (srt != null) w = srt.rect.width;
            }
            if (!BubbleWidthCore.ContentUsable(w) && _refs != null && _refs.Window != null)
                w = _refs.Window.rect.width - BubbleWidthCore.WindowSideInset;
            if (!BubbleWidthCore.ContentUsable(w)) w = BubbleWidthCore.FallbackContentWidth;
            return w;
        }
    }

    /// <summary>
    /// 气泡宽度的**延迟重测**队列（11-20 修「文字没到上限却撑满上限」）。
    ///
    /// 【为什么需要】
    /// `Text.preferredWidth` 依赖 Unity 缓存的 text generator；**新建气泡那一帧**
    /// 布局还没结算，读出来是 0（或抛异常）。而气泡是**一次性**的：
    /// 非流式气泡只在构造时 `SetText → ApplyBubbleWidth` 走一遍，之后再没有第二次机会
    /// （流式气泡靠每帧 `_deltaDirty` 刷新才会重算，所以那个 bug 只在整段到达时出现 ——
    /// 与本仓 `step(text)` 非流式路径正好对上）。
    ///
    /// 旧代码对"量不出来"的处理是 `w = cap`（直接给上限），于是第一帧的读 0
    /// 被**永久固化**成满宽。真机表现：一段 30 字的回话横到天花板；
    /// 关窗重开（走 `ReplaceHistory` 回放，那时布局早已结算）就完全正常。
    ///
    /// 【为什么是队列而不是给气泡挂组件】
    /// `ChatBubble` 是纯 C# 类（不是 MonoBehaviour），挂不上 `Update`。
    /// 而"每条气泡各挂一个"本身也是浪费 —— 需要重测的只在开窗头几帧是个位数。
    /// 走一条常驻帧回调（`FrameTicker`，与 `UiKit.PumpAudit` 同款思路）：
    /// 队列空时自动 `Stop`，零常驻开销。
    ///
    /// 【为什么不直接延时 N 帧后无条件重算】
    /// 那是"猜一个帧数"。重测的成功判据是**这一次真的量到了**（`ShouldWrite`），
    /// 量不到就继续排（上限 `BubbleWidthCore.MaxRemeasureTries`），量到了立刻停。
    /// </summary>
    internal static class BubbleWidthPump
    {
        /// <summary>FrameTicker 键（号段集中在 `app/FrameTicker.cs`，此处只引用）。</summary>
        internal const int Key = FrameTicker.KBubbleWidth;

        private static readonly List<ChatBubble> _queue = new List<ChatBubble>();

        internal static void Enqueue(ChatBubble b)
        {
            if (b == null) return;
            if (_queue.Contains(b)) return;              // 同一帧被排两次只算一次
            _queue.Add(b);
            try { FrameTicker.Ensure(Key, "bubblewidth", Pump); } catch { }
        }

        /// <summary>每帧处理一条：量到了就出队，没量到就留在队里下一帧再来（次数由气泡自己封顶）。</summary>
        private static void Pump()
        {
            try
            {
                if (_queue.Count == 0) { FrameTicker.Stop(Key); return; }
                var b = _queue[0];
                _queue.RemoveAt(0);
                if (b == null) return;                   // 行已被销毁（裁剪/换档）：静默跳过
                b.RetryMeasureWidth();                   // 量到了会自己不再入队
                if (_queue.Count == 0) FrameTicker.Stop(Key);
            }
            catch
            {
                try { FrameTicker.Stop(Key); } catch { }
            }
        }
    }

    /// <summary>
    /// 回合过程折叠组：头部「▸ 行动记录 (n)」；首行到达即自动展开（运行中看得见他查了什么），
    /// **回合收口即收起**——他动完手、开始说话，列表把位置让给台词（README 承诺的"做完自动收起"）。
    /// 头部点击手动收起/展开；内部 AddResult（工具结果）默认折成一行摘要、点击展开全文，
    /// AddPart（系统组装/运行时上下文等输入侧块）同款折叠行。行全文自适应高度直出——
    /// 不截断、无内部滚动，超长内容靠外层聊天列表滚动查看。
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
        private bool _userToggled;                // 用户手动点过组头（手动优先：收口不再自动收走他正看的东西）
        private int _count;
        private readonly bool _autoOpen;         // true=行动记录（运行中首行自动展开）；false=sys/ctx（运行中也不展开）

        /// <summary>组内某一行被折叠/展开后的回调（参数=被点的那一行）。ChatWindow 用它做
        /// 「不够就滚够」的位置校正；不接也不影响折叠本身。</summary>
        internal Action<GameObject> FoldToggled;

        /// <param name="label">组标题（默认「行动记录」）；输入侧复用同一模板传「系统组装」/「运行时上下文」。</param>
        /// <param name="autoOpen">行动记录传 true：工具调用**运行中**首行自动展开（看得见他在查什么），
        /// 收口时整体收起；系统组装/运行时上下文传 false：块从头就收起，头部点击展开
        /// （2026-09-09 用户澄清：sys/ctx 不要每次全量直出，只按差分行显示）。</param>
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

        /// <summary>回合收口（他动完手、开始说话）：整组收起，把列表位置让给台词
        /// —— 这就是 README 里写的「执行的时候自动展开，做完自动收起」。
        /// **手动点开过的组不收**（手动优先）：否则他正看着过程，下一句话一到就被收走。
        /// 注：刷新标题放在收放**之后**——否则箭头还停在 `▾`，与收起状态不符。</summary>
        public void Finish()
        {
            if (!_userToggled && _body != null && _body.gameObject.activeSelf)
            {
                _body.gameObject.SetActive(false);
                _bodyExpanded = false;
            }
            RefreshHeader();
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
            AddResultLine(text ?? "", isError);
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
                var h = FoldToggled;               // 同结果行：展开后做「不够就滚够」
                if (h != null) h(go);
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

        /// <summary>工具结果行：**默认折成一行摘要，点击展开全文**（2026-09-16 用户定调）。
        ///
        /// 两态文本：
        ///   · 折叠态 = `▸ ✓ 摘要…（共 598 字，点击展开）`（摘要剥富文本标签 + 压空白）；
        ///   · 展开态 = `▾ ✓ ` + **原文**（含标签/换行，不截断不改写；忠实原则见 ToolFoldPolicy）。
        /// 短结果（单行且 ≤ 40 字）不值得折：直出，与折叠前的旧行为逐字一致，不让用户为一行字多点一次。
        /// 错误行默认展开（`ExpandByDefault`）：失败信息正是要立刻看的。
        ///
        /// 整行可点：`img.raycastTarget = true` + Button（与 sys/ctx 段行同款；Text 的
        /// raycastTarget 在 AttachAutoText 里恒 false，事件冒到 Image 上）。点击后回调
        /// `FoldToggled` → ChatWindow 做「不够就滚够」的位置校正（否则贴底点开只看到结尾）。</summary>
        private void AddResultLine(string text, bool isError)
        {
            EnsureOpen();
            var go = new GameObject("Result");
            var rt = go.AddComponent<RectTransform>();
            go.AddComponent<CanvasRenderer>();
            var img = go.AddComponent<Image>();
            rt.SetParent(_body, false);
            img.color = new Color(0f, 0f, 0f, 0.16f);

            AttachAutoText(go, "", Color.black, TextAnchor.UpperLeft);
            var txt = go.GetComponentInChildren<Text>(true);

            if (!ToolFoldPolicy.ShouldFold(text))
            {
                img.raycastTarget = false;
                if (txt != null) txt.text = ToolFoldPolicy.Marker(isError) + text;
                return;
            }

            bool expanded = ToolFoldPolicy.ExpandByDefault(isError);
            string collapsed = ToolFoldPolicy.CollapsedLine(text, isError);
            string full = ToolFoldPolicy.ExpandedLine(text, isError);
            if (txt != null) txt.text = expanded ? full : collapsed;

            img.raycastTarget = true;               // 折叠行整行可点（命中 Image → Button）
            var btn = go.AddComponent<Button>();
            btn.targetGraphic = img;
            ClickUtils.Attach(btn, () =>
            {
                expanded = !expanded;
                if (txt != null) txt.text = expanded ? full : collapsed;
                var h = FoldToggled;
                if (h != null) h(go);
            });
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
            // 行距（10-31）：这里画的是工具结果/旁白/系统段的长文本，默认 1 比气泡更容易读成一片。
            // 与气泡共用同一个常量 —— 行距是"整块文本"的属性，两处不一致会比都不改更难看。
            txt.lineSpacing = BubbleTextCore.BubbleLineSpacing;
            // 富文本**保持 uGUI 默认的 true**：工具结果里有大量游戏自带标签
            // （`<color=#004FCA>` 之类，见 `UnitSnapshot.StripRichTags` 的实测计数），
            // 开着它们才是彩色的 —— 关掉会退化成满屏尖括号。气泡正文那条路则不同：
            // 它的标签来自模型，必须先过白名单（见 `BubbleTextCore.Render`）。
        }

        /// <summary>组头点击：手动收起/展开。标记 `_userToggled` —— 手动优先，
        /// 收口时不再把用户主动点开的组收走（`Finish()` 读它）。</summary>
        private void ToggleBody()
        {
            _userToggled = true;
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
        /// <summary>标题（默认「内心思量」）。群聊传「姜萌 · 内心思量」—— 说话人前缀由
        /// `ChatWindow.SpeakerLabel` 拼好（见那里的注释：气泡要等正文才出现，
        /// 思考期间这块标题是唯一的归属线索）。</summary>
        private readonly string _label;

        /// <summary>展开/收起后的回调（参数=本组根）。ChatWindow 用它做「不够就滚够」的位置校正；
        /// 不接也不影响折叠本身。</summary>
        internal Action<GameObject> FoldToggled;

        public ThinkGroup(Transform contentParent, string label = "内心思量")
        {
            _label = string.IsNullOrEmpty(label) ? "内心思量" : label;
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
            _bodyText.lineSpacing = BubbleTextCore.BubbleLineSpacing;   // 10-31：与气泡同款行距
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
            var h = FoldToggled;                   // 同结果行：展开后做「不够就滚够」
            if (h != null) h(Root);
        }

        private void UpdateHeader()
        {
            if (_headerText != null)
                _headerText.text = (_expanded ? "▾ " : "▸ ") + _label + " · " + _chars + " 字";
        }
    }

    /// <summary>
    /// 压缩纪要折叠行（10-31 用户需求）：默认收起只显示字数，点开看全文；右侧「修改」钮
    /// 在"压缩刚完成、还没开启新一轮对话"的窗口内可点（打开左侧编辑面板）。
    ///
    /// 与 `ThinkGroup` 的三处刻意差异（改之前先读）：
    ///  ① **根节点不挂 Button**，只有左侧标题挂。uGUI 的点击会沿层级冒泡，子按钮点一下会
    ///     连父按钮的展开一起触发（"点修改顺手把面板收起来"）。「修改」钮因此是标题的**兄弟**。
    ///  ② 锁定态是**一等状态**：置灰按钮 + 标题后缀「已锁定」+ 正文底部补一行原因。
    ///     自动压缩产出的纪要来就是锁定的（它落在回合内部，见 `compaction/summary.py`），
    ///     玩家需要知道为什么这颗钮点不了。
    ///  ③ 正文一次给全（不做流式）：纪要是压缩完成那一刻的成品，没有增量过程。
    ///
    /// 数据来自两条路（直播 `compact_summary` 事件 / 回放 `kind:"compaction"` 条目），
    /// 字段同名同义，所以 `ChatWindow.AppendSummaryRow` 是两者唯一入口。
    /// </summary>
    public class SummaryGroup
    {
        public readonly GameObject Root;
        private readonly Text _headerText;
        private readonly Text _bodyText;
        private readonly GameObject _bodyGo;
        private readonly Button _editBtn;
        private readonly Text _editLabel;
        private readonly string _compactionId;
        private string _text;
        private string _lockReason;
        private bool _expanded;
        private bool _editable;

        /// <summary>展开/收起后的回调（参数=本组根）。ChatWindow 用它做「不够就滚够」。</summary>
        internal Action<GameObject> FoldToggled;
        /// <summary>点了「修改」：参数 = (compactionId, 当前正文)。</summary>
        internal Action<string, string> EditRequested;

        public string CompactionId { get { return _compactionId; } }
        public bool Editable { get { return _editable; } }
        public string Text { get { return _text ?? ""; } }

        public SummaryGroup(Transform contentParent, string compactionId, string text,
                            bool editable, string lockReason)
        {
            _compactionId = compactionId ?? "";
            _text = text ?? "";
            _editable = editable;
            _lockReason = lockReason ?? "";

            var go = new GameObject("SummaryGroup");
            Root = go;
            var rt = go.AddComponent<RectTransform>();
            go.AddComponent<CanvasRenderer>();
            var img = go.AddComponent<Image>();
            // 淡蓝底：与「内心思量」的灰底一眼区分（那块是模型想什么，这块是它记住了什么）
            img.color = new Color(0.30f, 0.40f, 0.62f, 0.12f);
            img.raycastTarget = true;

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

            // ---- 头行：左侧标题（可点展开）+ 右侧「修改」（独立按钮，点击不冒泡到展开）----
            var head = new GameObject("HeadRow");
            var headRt = head.AddComponent<RectTransform>();
            head.AddComponent<CanvasRenderer>();
            headRt.SetParent(go.transform, false);
            var hlg = head.AddComponent<HorizontalLayoutGroup>();
            hlg.spacing = 6f;
            hlg.childControlWidth = true;
            hlg.childControlHeight = true;
            hlg.childForceExpandWidth = false;
            hlg.childForceExpandHeight = false;
            var headLe = head.AddComponent<LayoutElement>();
            headLe.preferredHeight = 24f;

            var toggle = new GameObject("Title");
            var trt = toggle.AddComponent<RectTransform>();
            toggle.AddComponent<CanvasRenderer>();
            trt.SetParent(head.transform, false);
            var timg = toggle.AddComponent<Image>();
            // ★ α 不能写 0：α=0 会被 UGUI 当"不渲染"整块剔除，射线也进不来（点不动）。
            //   与 `ToolPanel` 滚动区那颗 1/255 的底是同一个坑。
            timg.color = new Color(0f, 0f, 0f, 0.002f);
            timg.raycastTarget = true;
            var tbtn = toggle.AddComponent<Button>();
            tbtn.targetGraphic = timg;
            var tle = toggle.AddComponent<LayoutElement>();
            tle.flexibleWidth = 1f;
            tle.preferredHeight = 24f;
            _headerText = MakeText("Label", toggle.transform, "", 14, Color.black,
                                   TextAnchor.MiddleLeft, FontStyle.Bold);
            StretchFull(_headerText.rectTransform);
            ClickUtils.Attach(tbtn, ToggleExpanded);

            var edit = new GameObject("EditBtn");
            var ert = edit.AddComponent<RectTransform>();
            edit.AddComponent<CanvasRenderer>();
            ert.SetParent(head.transform, false);
            var eimg = edit.AddComponent<Image>();
            eimg.color = new Color(0.20f, 0.42f, 0.75f, 0.88f);
            eimg.raycastTarget = true;
            _editBtn = edit.AddComponent<Button>();
            _editBtn.targetGraphic = eimg;
            var ele = edit.AddComponent<LayoutElement>();
            ele.preferredWidth = 56f;
            ele.preferredHeight = 24f;
            _editLabel = MakeText("Label", edit.transform, "修改", 12, Color.white,
                                  TextAnchor.MiddleCenter, FontStyle.Bold);
            StretchFull(_editLabel.rectTransform);
            ClickUtils.Attach(_editBtn, OnEditClicked);

            // ---- 正文（默认收起；CSF 让高度随文本长）----
            var body = new GameObject("Body");
            _bodyGo = body;
            var brt = body.AddComponent<RectTransform>();
            body.AddComponent<CanvasRenderer>();
            brt.SetParent(go.transform, false);
            var bcsf = body.AddComponent<ContentSizeFitter>();
            bcsf.verticalFit = ContentSizeFitter.FitMode.PreferredSize;
            _bodyText = body.AddComponent<Text>();
            _bodyText.fontSize = 14;
            _bodyText.color = Color.black;
            _bodyText.font = ChatUiBuilder.ResolveSharedFont();
            _bodyText.fontStyle = FontStyle.Normal;
            _bodyText.alignment = TextAnchor.UpperLeft;
            _bodyText.raycastTarget = false;
            _bodyText.horizontalOverflow = HorizontalWrapMode.Wrap;
            _bodyText.verticalOverflow = VerticalWrapMode.Overflow;
            _bodyText.lineSpacing = BubbleTextCore.BubbleLineSpacing;   // 10-31：与气泡同款行距
            body.SetActive(false);

            ApplyLockVisual();
            UpdateHeader();
        }

        /// <summary>整段写入（直播/回放/改完刷新都走它）。</summary>
        public void SetSummaryText(string text)
        {
            _text = text ?? "";
            if (_bodyText != null) _bodyText.text = BodyContent();
            UpdateHeader();
        }

        /// <summary>锁定：置灰「修改」、标题标「已锁定」、正文补一行原因。幂等。</summary>
        public void SetLocked(string reason)
        {
            if (!string.IsNullOrEmpty(reason)) _lockReason = reason;
            _editable = false;
            ApplyLockVisual();
            if (_bodyText != null) _bodyText.text = BodyContent();
            UpdateHeader();
        }

        private void ApplyLockVisual()
        {
            if (_editBtn != null) _editBtn.interactable = _editable;
            if (_editLabel != null)
                _editLabel.color = _editable ? Color.white : new Color(1f, 1f, 1f, 0.45f);
        }

        private string BodyContent()
        {
            string body = _text ?? "";
            // 锁定时把原因写进正文尾部：玩家点开就能看到"为什么不能改"，
            // 不必悬停/猜（按钮置灰本身不解释原因）。
            if (!_editable && !string.IsNullOrEmpty(_lockReason))
                body += "\n\n（" + _lockReason + "）";
            return body;
        }

        private void OnEditClicked()
        {
            if (!_editable) return;
            var h = EditRequested;
            if (h != null) h(_compactionId, _text);
        }

        private void ToggleExpanded()
        {
            _expanded = !_expanded;
            if (_bodyGo != null) _bodyGo.SetActive(_expanded);
            if (_expanded && _bodyText != null) _bodyText.text = BodyContent();
            UpdateHeader();
            var h = FoldToggled;                   // 同「内心思量」：展开后做「不够就滚够」
            if (h != null) h(Root);
        }

        private void UpdateHeader()
        {
            if (_headerText == null) return;
            int n = _text != null ? _text.Length : 0;
            _headerText.text = (_expanded ? "▾ " : "▸ ") + "压缩纪要 · " + n + " 字"
                               + (_editable ? "" : " · 已锁定");
        }

        private static Text MakeText(string name, Transform parent, string text, int size,
                                     Color color, TextAnchor align, FontStyle style)
        {
            return UiKit.MakeText(name, parent, text, size, color, align, style);
        }

        private static void StretchFull(RectTransform rt)
        {
            if (rt == null) return;
            rt.anchorMin = Vector2.zero;
            rt.anchorMax = Vector2.one;
            rt.offsetMin = Vector2.zero;
            rt.offsetMax = Vector2.zero;
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
            var go = (GameObject)UnityEngine.Object.Instantiate(tpl);
            if (go == null) return null;
            go.transform.SetParent(parent, false);
            go.SetActive(true);
            return go;
        }

        /// <summary>
        /// 安全销毁：Unity 编辑模式（预览工程）必须用 DestroyImmediate，运行时（游戏）
        /// 用 Destroy。用 UNITY_EDITOR 宏自动切换——同一源码两环境共用，游戏行为不变。
        /// （参数写全 `UnityEngine.Object`：本文件 since 09-16 有 `using System`（折叠回调用
        /// `Action&lt;GameObject&gt;`），裸 `Object` 会与 `System.Object` 撞成 CS0104。）
        /// </summary>
        public static void DestroySafe(UnityEngine.Object obj)
        {
            if (obj == null) return;
#if UNITY_EDITOR
            if (!UnityEditor.EditorApplication.isPlaying)
            {
                UnityEngine.Object.DestroyImmediate(obj);
                return;
            }
#endif
            UnityEngine.Object.Destroy(obj);
        }
    }
}