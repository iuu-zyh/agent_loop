/// <summary>
/// 工具结果折叠判定 + 展开后的位置校正 —— **零 Unity 依赖的纯逻辑**（与 `BubbleCommitPolicy` 同款，
/// 刻意不 using UnityEngine，好让 net8.0 单测直接把它编进去跑判定表）。
///
/// ## 为什么要有它（2026-09-16 用户定调）
/// 行动记录里的**工具结果**原先全文直出：实机一屏 28 行全是道具词条。账本实测——
/// 21 条工具结果中位 267 字、最长 908 字（17 行），**工具原文合计 6901 字 &gt; 同期台词旁白 6237 字**。
/// 新形态：结果默认折成一行摘要，点击展开全文；回合收口后整组也收起（兑现 README 的"做完自动收起"）。
///
/// ## 三条不变量（动这个文件之前先读）
///  ① **只折视图，不改数据**：Python `tools/text_render.py` 渲染出的那个字符串**同时**喂 LLM 与 UI
///     （`dialogue_agent.py` 的 `tool_result` 帧与账本 `tool/result` 是同一产物）。折叠必须止步于
///     屏幕这一层——绝不回改渲染层，否则模型看到的东西变少（`tests/test_text_render.py::
///     test_no_truncation_anywhere` 是那条线的哨兵）。
///  ② **展开态一字不改**：`ExpandedLine` 必须是原文（含富文本标签与换行）。摘要是"预告"，不是替代品；
///     截断只许发生在**折叠态摘要**里。
///  ③ **摘要要先剥富文本标签**：结果里 `&lt;color=#004FCA&gt;` 这类很多（`UnitSnapshot.StripRichTags`
///     同款处理）。直接按字数截断会把标签截一半 → 屏幕上出现 `&lt;colo` 或整段串色。
///
/// ## 「不够就滚够」（`RevealDelta`）
/// 展开会让内容长高。若沿用流式跟随那条"内容变高就钉底"，用户点开一行只会看到**结尾**
/// （开头被顶出视口上沿）。用户选定 B 档：点开后视图**不跳**，只在"这一行装不下"时往下滚
/// **刚好够**的像素；行比视口还高就把行顶对齐视口顶，从头读。
/// </summary>
namespace AgentLoopBridge
{
    internal static class ToolFoldPolicy
    {
        /// <summary>折叠态摘要保留的字数（不含 `…（共 N 字，点击展开）` 后缀）。
        /// 40 字在 340px 宽的结果行里约两行——够认出"这是什么"，又不至于又变成一屏。</summary>
        internal const int BriefChars = 40;

        /// <summary>折叠箭头（折叠/展开态各一个），与行动记录组头 `▸/▾` 同款。</summary>
        private const string ArrowCollapsed = "▸ ";
        private const string ArrowExpanded = "▾ ";

        /// <summary>结果行前缀（与折叠前的旧版逐字一致：`✓ ` / `✗ `）。</summary>
        internal static string Marker(bool isError)
        {
            return (isError ? "✗ " : "✓ ");
        }

        /// <summary>值不值得折：可读文本长于摘要长度，或本来就是多行（三行短句直出也占三行）。
        /// 空文本/纯空白/纯标签/短句返回 false —— 直出，不让用户为一行字多点一次。
        /// 先算 `Visible` 再判所有条件：**按可读文本判**，不按原始长度
        /// （`&lt;color=#004FCA&gt;` 这类标签能把一行短结果的原始长度撑到 200+，
        /// 按原始长度判会把它误折成"共 30 字"的一行摘要）。</summary>
        internal static bool ShouldFold(string text)
        {
            if (string.IsNullOrEmpty(text)) return false;
            string visible = Visible(text);
            if (visible.Length == 0) return false;      // 纯空白/纯标签：没东西可折
            if (visible.Length > BriefChars) return true;
            return text.IndexOf('\n') >= 0 || text.IndexOf('\r') >= 0;
        }

        /// <summary>错误行默认展开：失败信息正是要立刻看的东西（观测里 0 条错误，所以这条基本不改变观感，
        /// 只是别让 `✗` 也被折起来藏住）。</summary>
        internal static bool ExpandByDefault(bool isError)
        {
            return isError;
        }

        /// <summary>折叠态整行文本：`▸ ✓ 摘要…（共 598 字，点击展开）`。
        /// 字数是**可读字符数**（剥标签 + 压空白之后），屏幕上看到多少就报多少。</summary>
        internal static string CollapsedLine(string text, bool isError)
        {
            string visible = Visible(text);
            string brief = visible.Length > BriefChars ? visible.Substring(0, BriefChars) + "…" : visible;
            return ArrowCollapsed + Marker(isError) + brief + "（共 " + visible.Length + " 字，点击展开）";
        }

        /// <summary>展开态整行文本：`▾ ✓ ` + **原文**（含富文本标签/换行，一字不改）。</summary>
        internal static string ExpandedLine(string text, bool isError)
        {
            return ArrowExpanded + Marker(isError) + (text ?? "");
        }

        /// <summary>展开后内容还需要往下滚多少像素（0 = 一动不动）。
        ///
        /// 参数都在"视口"这个参照系里，单位=像素：
        ///   <paramref name="rowTopBelowViewportTop"/> 行顶边在视口顶边**之下**多少（负数=行顶已滚到视口上方）；
        ///   <paramref name="rowHeight"/> 展开后的行高；
        ///   <paramref name="viewportHeight"/> 视口高；
        ///   <paramref name="scrollRoom"/> 还能往下滚多少（内容高 − 视口高 − 当前偏移，非负）。
        ///
        /// 规则（用户选定的 B 档）：
        ///   ① 装得下 → 0（"不够才滚够"，不无谓地晃屏）；
        ///   ② 装不下 → 滚到行底正好贴着视口下沿，但**最多**把行顶对齐视口顶（行比视口高时）；
        ///   ③ 再被 <paramref name="scrollRoom"/> 夹一次（滚到底了就只能给这么多）。</summary>
        internal static float RevealDelta(float rowTopBelowViewportTop, float rowHeight,
                                          float viewportHeight, float scrollRoom)
        {
            if (viewportHeight <= 0f || rowHeight <= 0f) return 0f;
            if (scrollRoom < 0f) scrollRoom = 0f;

            float need = rowTopBelowViewportTop + rowHeight - viewportHeight;   // 行底超出视口下沿多少
            if (need <= 0f) return 0f;                                          // ① 已经全在视口里

            float cap = rowTopBelowViewportTop > 0f ? rowTopBelowViewportTop : 0f;   // ② 不许把行顶推出上沿
            if (need > cap) need = cap;
            if (need > scrollRoom) need = scrollRoom;                                // ③ 到底了就滚不动了

            return need > 0f ? need : 0f;
        }

        /// <summary>剥富文本标签：只剥 `&lt;…&gt;`、不改字（与 `UnitSnapshot.StripRichTags` 同款）。</summary>
        internal static string StripTags(string s)
        {
            if (string.IsNullOrEmpty(s)) return "";
            var sb = new System.Text.StringBuilder(s.Length);
            bool inTag = false;
            for (int i = 0; i < s.Length; i++)
            {
                char c = s[i];
                if (c == '<') { inTag = true; continue; }
                if (c == '>') { inTag = false; continue; }
                if (!inTag) sb.Append(c);
            }
            return sb.ToString();
        }

        /// <summary>空白折叠成单空格、首尾去净（换行也算空白）。</summary>
        internal static string CollapseWhitespace(string s)
        {
            if (string.IsNullOrEmpty(s)) return "";
            var o = new System.Text.StringBuilder(s.Length);
            bool sp = false;
            for (int i = 0; i < s.Length; i++)
            {
                char c = s[i];
                if (c == ' ' || c == '\t' || c == '\r' || c == '\n' || c == '\u3000')
                {
                    if (!sp && o.Length > 0) { o.Append(' '); sp = true; }
                    continue;
                }
                sp = false;
                o.Append(c);
            }
            return o.ToString().Trim();
        }

        /// <summary>摘要与字数用的"可读文本"：剥标签 → 压空白。</summary>
        internal static string Visible(string s)
        {
            return CollapseWhitespace(StripTags(s));
        }
    }
}
