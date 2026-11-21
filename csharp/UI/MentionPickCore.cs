/// <summary>
/// 群聊 @ 补全的**纯逻辑核**（零 Unity、零游戏类型 ⇒ 可编成 net8.0 单测）。
///
/// 【它解决什么】
/// 用户 11-21 提的需求（原话）：
///
///     你应该也知道qq或者微信里面的@人，不是需要用户完整输入npc的名字的，
///     用户输入@就会自动跳出列表可以选择，同时随着用户进一步输入，候选列表也会变化
///
/// 即：打 `@` 就弹出全体成员，继续打字则按输入过滤。选中后把那段替换成完整名字。
///
/// 【为什么这部分必须纯逻辑】
/// 本仓既有约定（`BubbleWidthCore` / `DragCore` / `HistoryBuildCore` …）：
/// **判据归核、接线归层**。@ 补全的判据有三处"错了不报错、只是行为怪"的地方：
///   ① **光标位置**：`@` 只在光标**左侧**才算触发。写成"全文含 @"的话，
///      用户把光标挪到 `@张三` 前面继续打字，候选仍会弹着不消失；
///   ② **查询词边界**：`@李四 你好` 里光标在末尾时，往回找 `@` 会跨越空格 ——
///      若不截断，查询词变成「李四 你好」，候选恒为空（看着像"补全坏了"）；
///   ③ **替换范围**：替换必须是「@ 到光标」这一段，而不是"从 @ 到下一个空格"。
///      后者在 `@李` + 光标在 `李` 后面、后面还有 `四` 时会把 `四` 吃掉。
/// 这三条在真机上都是**静默**的（候选不弹 / 弹着不消失 / 吃掉半个名字），
/// 所以必须能被 `csharp/tests/MentionPickCoreTests` 直接链接跑断言。
///
/// 【一个必须承认的边界】
/// UGUI 的 `InputField` **不支持内联富文本芯片**（`@张三` 变成带底色的块）——
/// 它的 `text` 是纯字符串，`richText` 只影响显示、不影响光标定位。所以本核走的是
/// Slack/Discord 那种**纯文本 @**（选中后插入 `@张三 `），而不是微信那种内联 chip。
/// 这是 UGUI 的硬限制，不是取舍。
/// </summary>
using System;
using System.Collections.Generic;
using System.Text;

namespace AgentLoopBridge
{
    internal static class MentionPickCore
    {
        /// <summary>`@` 的全角与半角（中文输入法下 `＠` 很常见）。</summary>
        public const char AtHalf = '@';
        public const char AtFull = '＠';

        /// <summary>查询词遇到这些字符就截断（名字里不可能有它们）。</summary>
        /// <remarks>
        /// 与 Python 侧 `room/mention.py` 的 `_NAME_STOP` **必须保持同源**：
        /// 两边判据一旦漂移，就会出现"UI 弹出了候选、Python 解析却认不出"的错位
        /// （玩家点了候选、消息却发给全群）—— 那是本功能最坏的失败形态。
        /// 有契约测试 `tests/test_room_mention_contract.py` 的 G 段钉住两边。
        /// </remarks>
        public static readonly char[] NameStop = new char[]
        {
            ' ', '\t', '\r', '\n', '　', ',', '，', '.', '。', '!', '！', '?', '？',
            ';', '；', ':', '：', '、', '~', '～', ')', '）', ']', '】', '》',
            '"', '\'', '“', '”', '‘', '’',
        };

        /// <summary>候选列表最多显示几行（超出的不画 —— 浮层不该盖住整个窗口）。</summary>
        public const int MaxCandidates = 6;

        /// <summary>一次 @ 补全的上下文。`HasAt == false` 时其余字段都无意义。</summary>
        public struct PickContext
        {
            /// <summary>光标左侧有没有一个"有效的 @"（即当前应该弹候选）。</summary>
            public bool HasAt;
            /// <summary>`@` 在文本里的下标（`HasAt` 为真时有效）。</summary>
            public int AtIndex;
            /// <summary>`@` 与光标之间的串（可能为空 = 刚打完 @）。</summary>
            public string Query;
            /// <summary>替换范围：`[ReplaceStart, ReplaceEnd)` —— 选中候选时把这段换掉。</summary>
            public int ReplaceStart;
            public int ReplaceEnd;
        }

        private static bool IsAt(char c) { return c == AtHalf || c == AtFull; }

        private static bool IsStop(char c)
        {
            for (int i = 0; i < NameStop.Length; i++)
                if (NameStop[i] == c) return true;
            return false;
        }

        /// <summary>
        /// 从「文本 + 光标位置」算出当前该不该弹候选、查询词是什么、替换哪一段。
        ///
        /// ── 判据（每条都对应上面文件头的一个坑）──────────────────────────
        /// ① 从光标**往回**扫，找最近的 `@`；
        /// ② 往回扫的途中若先遇到分隔符（空格/标点）⇒ **没有有效的 @**（坑②：
        ///    光标已经离开 @ 段，候选该消失）；
        /// ③ 再往前若还有一个 `@` 且两者之间只有名字字符 ⇒ 用**更靠后**那个
        ///    （`@张三@李` 时应该补第二个）；
        /// ④ 查询词 = `@` 到光标之间的串；替换范围 = `[@下标, 光标)`（坑③）。
        ///
        /// `caret` 越界会被夹到 `[0, text.Length]`（UGUI 在某些输入法组合态下会给 -1）。
        /// </summary>
        public static PickContext Analyze(string text, int caret)
        {
            var ctx = new PickContext();
            if (string.IsNullOrEmpty(text)) return ctx;
            int n = text.Length;
            if (caret < 0) caret = 0;
            if (caret > n) caret = n;

            // ② 从光标往回找 @，遇到分隔符就停（说明光标不在任何 @ 段里）
            int i = caret - 1;
            while (i >= 0)
            {
                char c = text[i];
                if (IsAt(c)) break;
                if (IsStop(c)) return ctx;   // 先遇到分隔符 ⇒ 没有有效 @
                i--;
            }
            if (i < 0) return ctx;

            ctx.HasAt = true;
            ctx.AtIndex = i;
            ctx.Query = text.Substring(i + 1, caret - i - 1);
            ctx.ReplaceStart = i;
            ctx.ReplaceEnd = caret;
            return ctx;
        }

        /// <summary>
        /// 按查询词过滤成员名。返回**最多 `MaxCandidates`** 个，顺序 = 成员的原始顺序。
        ///
        /// 匹配规则：**子串包含**（不区分大小写，中文无影响但英文名用得上）。
        /// 为什么不是前缀匹配：用户常记得名字的**后两个字**（「婉清」而不是「林婉清」），
        /// 前缀匹配会让这类输入一个候选都没有 —— 而候选空就隐藏，玩家会以为补全坏了。
        /// 为什么不做拼音/首字母：那要一张码表，而成员名是游戏内动态生成的（可能含生僻字），
        /// 维护不起；子串包含已经覆盖绝大多数真实输入。
        ///
        /// `query` 为空 ⇒ 返回全体（刚打完 `@` 的形态）。
        /// </summary>
        public static List<string> Filter(IEnumerable<string> members, string query)
        {
            var outp = new List<string>();
            if (members == null) return outp;
            string q = (query ?? "").Trim();
            foreach (var raw in members)
            {
                if (string.IsNullOrEmpty(raw)) continue;
                if (q.Length == 0 ||
                    raw.IndexOf(q, StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    outp.Add(raw);
                    if (outp.Count >= MaxCandidates) break;
                }
            }
            return outp;
        }

        /// <summary>
        /// 选中一个候选后的**新文本 + 新光标位置**。
        ///
        /// 形态：把 `[@下标, 光标)` 换成 `@名字`，再补一个空格（若后面不是空格/标点）。
        /// 补空格的用途：让玩家可以立刻接着打字（`@李四 你好`），而不用自己敲空格 ——
        /// 微信/Slack 都是这个行为。
        ///
        /// ★ 不删除 @ 后面的**已有**内容 ★：`@李` 而原文后面还有 `四` 时，
        ///   替换范围只到光标（`@李`），`四` 留在原地 ⇒ 结果是 `@李四四`。
        ///   这是**已知的、刻意的**取舍：另一种做法（把 `@李四` 整段认成一次补全、
        ///   吃掉后面的 `四`）会在 `@李 四` 这种真·分两段输入时把空格也吃掉。
        ///   真实使用里用户是"打 @、看候选、点选"，不会先手打半个名字再点候选 ——
        ///   所以选不破坏原文的那一种。见 `test_accept_when_user_typed_partial_suffix`。
        /// </summary>
        public static void Accept(string text, PickContext ctx, string name,
                                  out string newText, out int newCaret)
        {
            newText = text ?? "";
            newCaret = 0;
            if (!ctx.HasAt || string.IsNullOrEmpty(name))
            {
                newCaret = newText.Length;
                return;
            }
            int start = Math.Max(0, Math.Min(ctx.ReplaceStart, newText.Length));
            int end = Math.Max(start, Math.Min(ctx.ReplaceEnd, newText.Length));

            string insert = "@" + name;
            // 后面紧跟着的不是空格/标点时补一个 —— 否则 `@李四你过来` 连成一串，
            // 而 Python 侧的解析器靠"名字完整匹配"仍能认出来，但人看着难受。
            bool needSpace = true;
            if (end < newText.Length)
            {
                char nxt = newText[end];
                if (nxt == ' ' || nxt == '\t' || nxt == '　' || IsStop(nxt)) needSpace = false;
            }
            if (needSpace) insert += " ";

            newText = newText.Substring(0, start) + insert + newText.Substring(end);
            newCaret = start + insert.Length;
        }

        /// <summary>
        /// 键盘选择：把当前高亮行上下移动。`delta` = -1 上、+1 下。
        ///
        /// **循环**（到顶再按上 ⇒ 跳到底）：微信/Slack 都是这样，而且实现更简单
        /// （不用处理"到头了没反应"这种让人以为卡住的静默）。
        /// `count <= 0` ⇒ 返回 -1（没有可选项）。
        /// </summary>
        public static int MoveSelection(int current, int delta, int count)
        {
            if (count <= 0) return -1;
            if (current < 0 || current >= count) return delta > 0 ? 0 : count - 1;
            int next = (current + delta) % count;
            if (next < 0) next += count;
            return next;
        }
    }
}
