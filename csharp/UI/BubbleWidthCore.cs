/// <summary>
/// 气泡宽度的**纯逻辑核**（零 Unity、零游戏类型 ⇒ 可编成 net8.0 单测）。
///
/// 【它解决什么】
/// 用户 11-19 报「对话 UI 气泡全部挤在左侧」——准确说是气泡盒宽度是**死的 450**：
///   · `UIChatAi.prefab` 里 `NpcBubble` / `UserBubble` 的 `m_SizeDelta.x = 450`；
///   · 行模板 `NpcRow` / `UserRow` 的 HLG 是 `m_ChildControlWidth = 0`
///     （子节点用自己 sizeDelta）⇒ 布局**永不会**覆盖它；
///   · 气泡的 `ContentSizeFitter` 只有 `m_VerticalFit`，`LayoutElement.m_PreferredWidth = 0`。
/// 而面板 `BG` 是 1200 宽、`Scroll` 1170 ⇒ 内容区比 450 宽一倍多，右边空一大片。
///
/// 现在改成"跟对话窗走"：上限 = 内容区可用宽 − 活动头像/间距 − 安全边距；
/// 短句仍按文字实际需要收缩（不留一条 450 的空壳）。
///
/// 【11-20 补：一个"算式全对但功能还是错"的坑】
/// 用户截图：一段 30 字的回话气泡横到天花板，可关窗重开（回放）又正常。
/// 算式没错，错的是**测量时机** —— 新建气泡那一帧 `Text.preferredWidth` 读到 0
/// （布局未结算），而接线当时写的是 `w = need > 0 ? Fit(...) : cap`（量不出就按上限给）。
/// 气泡又只有一次测量机会（非流式），于是那一帧的读 0 被永久固化成满宽。
/// 现在由 `ShouldWrite` 把关：**量不出就不写**，排下一帧重测。
/// 详见 `docs/journal/2026-11-20-气泡宽度满宽bug/`。
///
/// 【为什么上限是"内容宽 − 兄弟节点"而不是"比例"】
/// 单聊里头像会被隐藏（`ChatBubble` 里 `avatarTr.gameObject.SetActive(false)`），
/// 这时整行宽度都该归气泡。写成固定比例就等于把这条省掉 —— 头像一隐，右边又空一截。
///
/// 【为什么纯逻辑】
/// 本仓的既有约定（`BubbleTextCore` / `DeferredCore` …）：**判据归核、接线归层**。
/// 这里的每条算式在真机上错都是静默的（气泡撑出窗、或缩成一条线），
/// 必须能被 `csharp/tests/BubbleWidthCoreTests` 直接链接编译跑断言。
/// </summary>
using System;

namespace AgentLoopBridge
{
    internal static class BubbleWidthCore
    {
        /// <summary>气泡最小宽（约 3 个汉字 + 内边距）：防"嗯""哦"这类短句缩成一个点。</summary>
        public const float MinWidth = 96f;

        /// <summary>两侧安全边距之和：用户要的"比对话框稍微短一点、避免穿模"。</summary>
        public const float SafetyMargin = 24f;

        /// <summary>内容区宽度小于这个值就当"还没布局好"，往上走回退链。
        /// 取 200 是因为正常的对话内容区在千级单位，200 只可能是布局前的脏值。</summary>
        public const float MinContentWidth = 200f;

        /// <summary>回退链的最后一档（所有 rect 都读不到时）：800 ≈ 1200 面板的常用内容区。</summary>
        public const float FallbackContentWidth = 800f;

        /// <summary>拿窗口宽当兜底时，左右要减掉的内缩（滚动条/边距的经验值）。</summary>
        public const float WindowSideInset = 120f;

        /// <summary>量文字时给的富余（`preferredWidth` 是整数进位后的值，留 2 个单位防末字贴边）。</summary>
        public const float TextSlack = 2f;

        /// <summary>写入阈值：与上次相差小于它就不改 —— 流式输出每帧都会变长度，
        /// 每帧重写 sizeDelta 会连带触发 Content 的整列重排。</summary>
        public const float RewriteEpsilon = 1.5f;

        /// <summary>气泡可用的**最大宽**（cap）。</summary>
        /// <param name="contentWidth">内容区（`Content`）的可用宽。</param>
        /// <param name="siblingsWidth">同一行里**活动**的非气泡子节点宽度之和（头像）。</param>
        /// <param name="spacing">行布局组的 spacing × 间隙数。</param>
        /// <param name="safety">两侧安全边距之和。</param>
        public static float Cap(float contentWidth, float siblingsWidth, float spacing, float safety)
        {
            float cap = contentWidth - siblingsWidth - spacing - safety;
            if (cap < MinWidth) cap = MinWidth;
            return cap;
        }

        /// <summary>这段文字"一行放得下"需要多宽（含气泡左右内边距）。</summary>
        public static float Needed(float textPreferredWidth, float padLeft, float padRight)
        {
            float n = textPreferredWidth + padLeft + padRight + TextSlack;
            if (n < MinWidth) n = MinWidth;
            return n;
        }

        /// <summary>最终宽度：短句按需要收缩，长句夹到 cap。</summary>
        public static float Fit(float needed, float cap)
        {
            if (cap < MinWidth) cap = MinWidth;
            return needed > cap ? cap : needed;
        }

        /// <summary>
        /// 这一帧**该不该**把宽度写下去。
        ///
        /// ★ 11-20 修「文字没到上限却撑满上限」的根因就在这条判据上 ★
        ///
        /// `measured &lt;= 0` 是"布局还没结算、`Text.preferredWidth` 读到 0"的正常状态，
        /// 而**不是**"这段文字不需要宽度"。旧调用方把它当成后者：
        ///     `w = need &gt; 0f ? Fit(need, cap) : cap;`
        /// 量不出来 ⇒ 直接撑满上限。可气泡是**一次性**的：非流式气泡只在构造时
        /// `SetText` → `ApplyBubbleWidth` 走一遍，之后再没有第二次测量
        /// （流式气泡靠每帧 `_deltaDirty` 刷新才会重算）。于是那一帧的读 0
        /// 被永久固化 —— 用户看到的就是"一段 30 字的回话横到天花板"，
        /// 而关窗重开（走 `ReplaceHistory` 回放，那时布局早已结算）又完全正常。
        ///
        /// 所以判据改成：**没量出来就不写，等下一帧重测**（见 `ChatBubble` 的重测队列）。
        /// 宁可让气泡多停在预制体的原宽一帧，也不要写一个凭空的满宽。
        /// </summary>
        /// <param name="measured">文字单行宽（&lt;= 0 = 这一帧量不出来）。</param>
        /// <param name="newWidth">算出来的目标宽度（&lt;= 0 = 没算出来）。</param>
        public static bool ShouldWrite(float measured, float newWidth)
        {
            if (measured <= 0f) return false;      // 布局未结算：这一帧不写，等重测
            if (newWidth <= 0f) return false;      // 没算出宽度：更不该写
            return true;
        }

        /// <summary>重测次数上限。给到 3 次：正常第一帧就成功，量不出多半是"窗口刚建好"，
        /// 两三帧内必然结算。超了就不许再排 —— 否则一帧一次的循环永远不会停
        /// （气泡行被销毁、字体确实缺失时都会一直量不出）。</summary>
        public const int MaxRemeasureTries = 3;

        /// <summary>
        /// 硬上限：气泡宽度**绝不允许**超过内容区可视宽（留安全边距）。
        ///
        /// 【11-20 补，群聊溢出】用户的群聊截图里有一批气泡被右侧裁掉了。
        /// 前面的 `Cap()` 已经在减头像/间距，但它依赖两个**读数**：
        /// `contentWidth`（Content 的 rect）与 `siblings`（头像宽度）——
        /// 只要其中任何一个在那一帧读到脏值（回放/换档/行刚建好），cap 就会偏大，
        /// 而 `Fit()` 忠实地按这个偏大的 cap 去夹，于是气泡溢出可视区、被 RectMask2D 裁掉。
        ///
        /// 所以再加一道**不依赖任何兄弟节点读数**的兜底：气泡本身加上左右两侧
        /// 至少该留的边距，不能超过内容区宽度。它与 bug 的成因无关 ——
        /// 不管上游哪一步算错，都不该写出一个超出可视区的结果。
        ///
        /// 注意这是"最后一道闸"，不参与正常取值：正常路径下 `Cap()` 的结果总是更小。
        /// </summary>
        public static float ClampToContent(float width, float contentWidth, float safety)
        {
            if (contentWidth < MinContentWidth) return width;   // 内容宽本身不可信：不夹
            float limit = contentWidth - safety;
            if (limit < MinWidth) limit = MinWidth;
            return width > limit ? limit : width;
        }

        /// <summary>是否值得写下去（变化小于 <see cref="RewriteEpsilon"/> 就当没变）。</summary>
        public static bool ShouldRewrite(float lastWidth, float newWidth)
        {
            if (lastWidth < 0f) return true;          // 还没写过
            return Math.Abs(newWidth - lastWidth) >= RewriteEpsilon;
        }

        /// <summary>内容区宽度是否可用（否则调用方走回退链）。</summary>
        public static bool ContentUsable(float contentWidth)
        {
            return contentWidth >= MinContentWidth;
        }
    }
}
