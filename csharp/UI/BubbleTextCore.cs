/// <summary>
/// 气泡正文的**显示层整形**（纯逻辑：零 Unity、零游戏类型 ⇒ 可编成 net8.0 直接单测）。
///
/// ── 为什么需要它（用户 09-17 实机截图）────────────────────────────────────
/// 模型写台词时会自带分段习惯：
///
///     (这倒是有趣……我与他素不相识，却能在同一坐标相遇。炼气境散修，看来也是初入仙道的新人。)
///     ␤
///     见过道友。
///     ␤
///     (仔细打量对方) 你我能在此地相遇，也算有缘。不知道友来此是为了寻访机缘，还是有要事在身？
///
/// `␤` = 一个**完整的空行**。uGUI 的 `Text` 没有"段间距"这种概念，一个空行就是**一整行行高**
/// （fontSize 19 时 ≈ 23 单位），于是气泡高度里 1/4 是纯空白 —— 观感就是"行间距/换行特别大"。
/// 实测那条回复：气泡 183 单位高，其中 46 单位是空行。
///
/// 折叠规则（`Tighten`）：先把 `\r\n`/`\r` 归一成 `\n`，再**丢掉空行与纯空白行**，
/// 每行首尾空白修掉 ⇒ `\n{2,}` 塌成单个 `\n`。分段结构保留（仍然分行），只是不再空一整行。
///
/// ── 为什么放在显示层，而不是改提示词 / 在 Python 侧拼串时处理 ──────────────
///  · 来源不止一处：单聊终稿、群聊 `room_say`、流式 delta、历史重放（`AppendNpcStatic`）、
///    玩家自己输入的多行文本 —— 全部要收敛到同一处才不会有"某条路还是大空行"；
///  · **流式**（`_delta` 缓冲逐帧刷 Text）与**终稿覆盖**必须给出同样的观感，否则定稿瞬间
///    会"跳一下"；
///  · 提示词里并没有要求空行（查过 `prompts/`，Python 侧也没有 `"\n\n".join`）——
///    这是模型的自发排版，管不住的，只能在渲染前收口。
///
/// ⚠ 只碰"行结构"，不动内容：不合并空格、不删标点、不改变行内文本。
///   真正的"这一行是谁说的"等语义都在调用方，这里只做纯字符串变换。
///
/// ── 10-31 第二批：**行距**与**富文本**（用户需求）──────────────────────────
/// 玩家反馈两条：「文字多的时候密密麻麻」「希望模型能输出富文本，用不同颜色」。
///
/// · 行距：uGUI `Text.lineSpacing` 默认 1（= 紧贴）。中文字形是满框的，1.0 在 19px 上
///   行间几乎没有空隙 ⇒ 整段读起来是一坨。这里给两个常量，**预制件与运行期两条腿共用同一个数**
///   （预制件那条腿在 `scripts/dev/prefab_patch_chat_bubble_style.py`，改了这里要同步改那边，
///   `tests/test_chat_bubble_layout_contract.py` 会卡）。
///
/// · 富文本：uGUI 的 `Text` 本来就能渲染 `<color>`/`<b>`/`<size>`，但**模型直接吐标签有
///   两个真实风险**，所以不能把它原样塞进 `Text.text`：
///     ① 格式不合法/标签不在 Unity 支持集里 ⇒ 上屏是一堆尖括号，比不富文本更难看；
///     ② 颜色选得浅（`#FFFF00`）⇒ 浅灰/浅绿气泡底上**等于看不见**，玩家只会觉得"字没了"。
///   所以正文统一过 `Render()` = 折叠空行 + **白名单**富文本。白名单只放行
///   `color`（调色板名或 #RRGGBB[AA]，且按亮度上限压暗）/ `b` / `i` / `size(8..30)`；
///   其余一切 `<…>` 按字面剥掉（与 `UnitSnapshot.StripRichTags` 同一个括号机语义）。
///   顺便做**标签配平**：缺闭合自动补齐、多出来的孤立闭合丢掉 —— 流式期间开标签先到、
///   闭标签后到，不配平的话半句话会被染成彩色闪一下。
///
///   白名单的**名字表**必须与 `prompts/personas/_suffix.txt` 里教模型的颜色名一致，
///   否则模型写对了名字、我们却不认 ⇒ 静默无颜色。`tests/test_rich_text_bubble_contract.py`
///   锁这条（两侧同名同值）。
/// </summary>
using System;
using System.Collections.Generic;
using System.Text;

namespace AgentLoopBridge
{
    internal static class BubbleTextCore
    {
        /// <summary>折叠空行 + 修掉每行首尾空白 + 统一换行符。null/空 → 空串。</summary>
        public static string Tighten(string s)
        {
            if (string.IsNullOrEmpty(s)) return "";
            var sb = new StringBuilder(s.Length);
            int n = s.Length;
            int i = 0;
            while (i < n)
            {
                int j = i;
                while (j < n && s[j] != '\n' && s[j] != '\r') j++;
                AppendContentLine(sb, s, i, j);
                if (j >= n) break;
                // 行结束符：\r\n 算一个；单独的 \r 或 \n 也算一个
                if (s[j] == '\r' && j + 1 < n && s[j + 1] == '\n') j++;
                i = j + 1;
            }
            return sb.ToString();
        }

        /// <summary>取 s[start,end) 这一行：去掉首尾空白后非空才写入（空行 = 直接丢弃）。</summary>
        private static void AppendContentLine(StringBuilder sb, string s, int start, int end)
        {
            int a = start, b = end;
            while (a < b && IsPad(s[a])) a++;
            while (b > a && IsPad(s[b - 1])) b--;
            if (a >= b) return;                     // 空行 / 纯空白行：丢
            if (sb.Length > 0) sb.Append('\n');     // 行间只留一个换行
            sb.Append(s, a, b - a);
        }

        /// <summary>算"空白"的字符：半角空格/制表符/全角空格/不换行空格（模型都会用）。</summary>
        private static bool IsPad(char c)
        {
            return c == ' ' || c == '\t' || c == '\u3000' || c == '\u00A0';
        }

        // ==================================================================
        // 行距（10-31）
        // ==================================================================

        /// <summary>气泡正文行距（uGUI `Text.lineSpacing`，1 = 紧贴）。
        ///
        /// 1.35 的来处：fontSize 19 的字形墨迹高度约 19 单位，而 uGUI 的行高 = fontSize × lineSpacing
        /// ⇒ 1.0 时两行墨迹**几乎正好相接**（中文字是满框的，不像拉丁字母有留白），整段看起来
        /// 就是一坨。1.35 给出行间 6~7 单位的空隙，与 `NoticePanel` 正文同款（那里实测 1.35 好读）。
        ///
        /// ⚠ **两条腿共用一个数**：预制件那条腿在
        /// `scripts/dev/prefab_patch_chat_bubble_style.py`（改的是 `m_FontData.m_LineSpacing`，
        /// 需要重打 AB 才生效），运行期那条腿是 `ChatWindow.ApplyBubbleTextLayoutFloor`。
        /// 两条腿数字不一致 = "重打 AB 前后观感不同"这类最难查的 bug，
        /// `tests/test_chat_bubble_layout_contract.py` 逐值比对。
        /// </summary>
        public const float BubbleLineSpacing = 1.35f;

        // ==================================================================
        // 富文本（10-31）：白名单 + 配平 + 亮度压制
        // ==================================================================

        /// <summary>可读配色表：**名字（模型写这个）→ #RRGGBB**。
        ///
        /// ── 选色是按**两种气泡底**算出来的，不是看着挑的（10-31 重做）─────────────
        /// 两种底都不是白色：NPC `#E9E9E9`、**玩家是浅绿 `#9BE375`**。
        /// 第一版按灰底选了 8 个明快的色（`#C62828` 红、`#C05621` 橙…），在玩家气泡上
        /// 对比度只有 **2.97~3.7**（WCAG 正文要求 4.5）⇒ 绿底上的橙字几乎看不见。
        ///
        /// 现在的每个值都是"在两种底上都 ≥4.5"的最大亮度（见 `tests` 里的对比度断言），
        /// 副作用是颜色整体偏深，但**看得见**比"鲜艳"重要。
        ///
        /// ★ 为什么没有「橙」★ 深色区里红/橙/黄的色相距离只有 45~50（<55 人眼就分不清），
        ///   物理上塞不下三个暖色。去掉橙，留红/黄两个可区分的；模型写「橙」会被当成
        ///   未知颜色名 **静默降级成白字**（不报错，但那句话就是没标）。
        ///   提示词里也只教这 7 个，两边一致。
        ///
        /// ★ 这张表必须与 `prompts/sections/world_rich_text.txt` 教给模型的颜色名**同名同值** ——
        ///   模型写对名字而我们不认，就是静默无色。
        ///   `tests/test_rich_text_bubble_contract.py` 双向比对两侧。
        /// </summary>
        private static readonly Dictionary<string, string> Palette = BuildPalette();

        private static Dictionary<string, string> BuildPalette()
        {
            var d = new Dictionary<string, string>(StringComparer.Ordinal)
            {
                { "红", "#B2121D" }, { "red",    "#B2121D" },
                { "黄", "#705600" }, { "yellow", "#705600" },   // 暗金，不是 #FFFF00
                { "绿", "#0A681D" }, { "green",  "#0A681D" },
                { "青", "#0B6369" }, { "cyan",   "#0B6369" },
                { "蓝", "#1257B1" }, { "blue",   "#1257B1" },
                { "紫", "#8423C5" }, { "purple", "#8423C5" },
                { "灰", "#595959" }, { "gray",   "#595959" },
            };
            return d;
        }

        /// <summary>两种气泡底色（从 AB 工程的 PNG 上量的，见 docs/docs/reference/ui-workflow.md §6）。
        /// ★ 玩家气泡是**浅绿**不是白 —— 第一版只按灰底挑色，绿底上橙字对比度只有 2.97。</summary>
        private static readonly int[] BubbleBgNpc = { 0xE9, 0xE9, 0xE9 };
        private static readonly int[] BubbleBgUser = { 0x9B, 0xE3, 0x75 };

        /// <summary>可接受的最小 WCAG 对比度。4.5 = WCAG 2.1 AA 对正文的要求。
        /// 不用 3.0（大字标准）：气泡正文 19px 在游戏里会被缩放，且玩家常在移动中扫读。</summary>
        private const double MinContrast = 4.5;

        /// <summary>旧判据（感知亮度上限）。**已不用于判定**，只留作文档参照 ——
        /// 它判的是"够不够暗"，而"读不读得出来"是两个颜色之间的关系，见 `CapLum` 的说明。</summary>
        private const double LumCap = 0.50;

        /// <summary>size 标签允许的字号区间（px）。下限防"小到看不清"，上限防"大得撑破气泡"。</summary>
        private const int SizeMin = 8;
        private const int SizeMax = 30;

        /// <summary>标签体的最大长度 —— 与 Python `_RICH_TAG_RE` 的 `{1,80}` 同界。
        /// 超过就当它不是标签（只是正文里的一个 `<`），避免把一整段当成标签吃掉。</summary>
        private const int MaxTagLen = 80;

        /// <summary>气泡正文的**唯一渲染入口**：折叠空行（`Tighten`）+ 富文本清洗（`Rich`）。
        ///
        /// 四个写泡点全部走它（流式刷新 / 终稿覆盖 / 行覆盖 / 新建气泡），
        /// 与 `Tighten` 当年"四处必须同款"的理由完全一样 —— 漏一处 = 某条路显示裸标签。
        /// </summary>
        public static string Render(string s)
        {
            return Rich(Tighten(s));
        }

        /// <summary>剥掉所有富文本标签（不渲染、只留字）。给**单行预览**这类放不下颜色的地方用
        /// （`UnreadBanner` 的「「甲」传音：…」若带着标签，玩家会看到一串尖括号）。
        /// 与 <see cref="Rich"/> 共用同一个括号机，语义是"标签一律消失、内容留下"。</summary>
        public static string StripTags(string s)
        {
            if (string.IsNullOrEmpty(s)) return "";
            var sb = new StringBuilder(s.Length);
            int i = 0, n = s.Length;
            while (i < n)
            {
                if (s[i] != '<') { sb.Append(s[i++]); continue; }
                int close = FindTagEnd(s, i);
                if (close < 0) { sb.Append(s[i++]); continue; }   // 不是标签，就是这个 '<'
                i = close + 1;
            }
            return sb.ToString();
        }

        /// <summary>从 `<`（下标 open）往后找配对的 `>`；**不是一个标签**就返回 -1。
        ///
        /// 三条否决（任何一条成立 ⇒ 这个 `<` 是正文字符，原样保留）：
        ///   ① 范围内没有 `>`，或超过 <see cref="MaxTagLen"/>；
        ///   ② 范围里又出现一个 `<`（`a < b <c>` 不该被整体吃掉）；
        ///   ③ **标签体不是字母开头**（去掉前导 `/` 与空白之后）。
        ///
        /// ★ ③ 是必须的（实测 bug）★
        ///   正文里写"形状像 < 这个，也像 > 那个"时，`< 这个，也像 >` 会被 ①② 判成
        ///   一个合法形状的标签 ⇒ 白名单外 ⇒ **连里面的字一起吃掉**，玩家看到的句子缺一块。
        ///   真实的 Unity 标签**全部以字母开头**（`color`/`b`/`i`/`size`/`sprite`…），
        ///   所以"字母开头"既不误伤任何真标签，又把这句正文救回来。</summary>
        private static int FindTagEnd(string s, int open)
        {
            int n = s.Length;
            int limit = Math.Min(n, open + 1 + MaxTagLen);
            int close = -1;
            for (int k = open + 1; k < limit; k++)
            {
                char c = s[k];
                if (c == '>') { close = k; break; }
                if (c == '<') return -1;
            }
            if (close < 0) return -1;
            if (!LooksLikeTagBody(s, open + 1, close)) return -1;
            return close;
        }

        /// <summary>s[start,end) 这一段（标签体）像不像一个真标签：去掉前导 `/` 与空白后
        /// **以字母开头**。`color=红` / `/color` / `b` / `size=25` 通过；
        /// ` 这个，也像 ` / `123` / `#红色` 不通过 —— 那是正文。</summary>
        private static bool LooksLikeTagBody(string s, int start, int end)
        {
            int k = start;
            while (k < end && s[k] == ' ') k++;
            if (k < end && s[k] == '/') { k++; while (k < end && s[k] == ' ') k++; }
            // `<>` / `</>`（清干净后什么都不剩）：这不是正文，是模型写崩了的空标签 ——
            // **当标签丢掉**。第一版这里 return false（当正文），于是 `只是<>劝你先掂量掂量</>`
            // 原样上屏（10-31 实测：B 版提示词下模型真的会这么写）。
            if (k >= end) return true;
            char c = s[k];
            return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z');
        }

        /// <summary>这个 `<` 是不是"**流式删到一半**的标签开头"：它到串尾之间没有 `>`，
        /// 且形体像**白名单标签的开头**。
        ///
        /// 判据 = 两条同时成立：
        ///   ① 到串尾的每个字符都在"标签里可能出现的字符集"里（字母/数字/`#`/`/`/`=`/`-`/`.`/引号/空格/CJK）；
        ///   ② 去掉前导 `/` 之后，**以某个白名单标签名开头，或是它的前缀**。
        ///
        /// ★ 为什么②必须存在（第一版没有它，是真机级 bug）★
        ///   本项目的提示词教的就是 `<color=红>` 这种**中文色名**写法。第一版只按"出现中文就当正文"
        ///   判断，于是流式到 `<color=红` 那一帧时**把半截标签原样画上屏**（实测：
        ///   `见过<color=红` → `见过<color=红`），屏幕上闪一串尖括号。
        ///   改成"按标签名的前缀认"之后：`color=红` 以 `color` 开头 ⇒ 认得出来 ⇒ 整段丢掉；
        ///   而 `甲<乙`（`乙` 不是任何标签名的前缀）仍然是正文，一个字不少。
        ///
        /// 代价：玩家真在句末打一个裸 `<` 时，这个 `<` 不上屏（可接受，且流式下一帧就到齐了）。</summary>
        private static bool IsPartialTagTail(string s, int open)
        {
            int n = s.Length;
            if (n - open - 1 > MaxTagLen) return false;        // 太长，不可能是标签
            for (int k = open + 1; k < n; k++)
                if (!IsTagChar(s[k])) return false;            // 标点/符号 ⇒ 这是正文
            string tail = s.Substring(open + 1);
            if (tail.StartsWith("/", StringComparison.Ordinal)) tail = tail.Substring(1);
            tail = tail.TrimStart();
            if (tail.Length == 0) return true;                 // 就一个 `<`（或 `< /`）：等下一帧
            return StartsLikeTagName(tail);
        }

        /// <summary>"标签里可能出现的字符"。刻意**包含 CJK** ——
        /// 颜色名允许中文（`<color=红>`），排除它就会把半截标签当正文（见 IsPartialTagTail 的 ★）。
        /// 排除的是 `，。！？、；：` 这类**句子标点** —— 标签里绝不会出现它们。</summary>
        private static bool IsTagChar(char c)
        {
            if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')) return true;
            if (c >= '0' && c <= '9') return true;
            if (c == '#' || c == '/' || c == '=' || c == '-' || c == '_' || c == '.' || c == '"' || c == '\'') return true;
            if (c == ' ') return true;
            return c >= '\u4e00' && c <= '\u9fff';             // 中文颜色名
        }

        /// <summary>这段（已去前导 `/` 与空白）是不是某个白名单标签名的**开头**：
        /// 要么它以某个名字开头（`color=红`），要么它本身是某个名字的前缀（`col`）。</summary>
        private static bool StartsLikeTagName(string tail)
        {
            string[] names = { "color", "size" };   // 与 Canonical 的白名单保持一致
            string low = tail.ToLowerInvariant();
            for (int k = 0; k < names.Length; k++)
            {
                string nm = names[k];
                if (low.StartsWith(nm, StringComparison.Ordinal)) return true;
                if (nm.StartsWith(low, StringComparison.Ordinal)) return true;
            }
            return false;
        }

        /// <summary>白名单富文本清洗：**只放行** `color`（调色板名 / #RRGGBB[AA]）、`b`、`i`、
        /// `size`；其余标签一律连标签带尖括号丢掉（内容保留）。输出保证**标签配平**。
        ///
        /// 为什么要配平：流式期间开标签先到、闭标签后到。不配平的话那半句话会被染成彩色
        /// 闪一下再变回去；而**多出来的孤立 `</color>`** 在 Unity 里会让后续整段错色。
        /// 规则：同类标签重复开 = 先自动闭合前一个；闭合一个没开的标签 = 直接丢弃；
        /// 收尾时把还开着的按栈倒序补闭合。
        /// </summary>
        private static string Rich(string s)
        {
            if (string.IsNullOrEmpty(s)) return "";
            var sb = new StringBuilder(s.Length + 16);
            var open = new List<string>();          // 还没闭合的标签名（color/b/i/size）
            int i = 0, n = s.Length;
            while (i < n)
            {
                char c = s[i];
                if (c != '<') { sb.Append(c); i++; continue; }

                int close = FindTagEnd(s, i);
                if (close < 0)
                {
                    // 流式期常见：这一帧刚好断在标签中间（`<color=#C6`）。留一半上屏会闪一串尖括号，
                    // 而下一帧完整的标签就到了 ⇒ 尾部这半截**直接丢弃**（只在结尾、只在形状像标签时）。
                    if (IsPartialTagTail(s, i)) break;
                    sb.Append(c); i++; continue;              // 正文里的裸 '<'
                }

                // 有的模型会写成 `<color>黄>字</color>`（漏了等号，颜色名跑到尖括号外面）。
                // 那时这里拿到的是 `color`（合法但缺参数 ⇒ 下面整段丢掉），紧接着的 `黄>` 会成为
                // **正文**上屏，玩家看到"黄>几时到期"。所以：紧跟一个孤立 `>` 的局面，
                // 把这段尾巴一并吃掉 —— 判据是"丢掉标签后，紧邻的下一个字符是 `>`"。
                string body = s.Substring(i + 1, close - i - 1).Trim();
                bool isClose = body.StartsWith("/", StringComparison.Ordinal);
                string name = isClose ? body.Substring(1).Trim() : body;
                string arg = "";
                int eq = name.IndexOf('=');
                if (eq >= 0)
                {
                    if (isClose) { name = ""; }        // `</color=x>` 这类不是合法闭合，当噪声丢掉
                    else { arg = name.Substring(eq + 1).Trim(); name = name.Substring(0, eq).Trim(); }
                }

                string canon = Canonical(name);         // null = 不在白名单
                if (canon == null) { i = SkipTagTail(s, close + 1); continue; }   // 白名单外：整段标签丢掉

                if (isClose)
                {
                    int at = open.LastIndexOf(canon);
                    if (at < 0) { i = SkipTagTail(s, close + 1); continue; }      // 孤立闭合：丢
                    // 从栈顶到它之间的都补闭合（嵌套写错时把它们收掉，保证输出合法）
                    for (int k = open.Count - 1; k >= at; k--)
                        sb.Append("</").Append(open[k]).Append('>');
                    open.RemoveRange(at, open.Count - at);
                    i = SkipTagTail(s, close + 1);
                    continue;
                }

                string emit = OpenTag(canon, arg);
                if (emit == null) { i = SkipTagTail(s, close + 1); continue; }    // 参数不合法：整段丢掉

                int dup = open.LastIndexOf(canon);
                if (dup >= 0)   // 同类重复开：先把前面那个收掉，避免嵌套混淆
                {
                    for (int k = open.Count - 1; k >= dup; k--)
                        sb.Append("</").Append(open[k]).Append('>');
                    open.RemoveRange(dup, open.Count - dup);
                }
                sb.Append(emit);
                open.Add(canon);
                i = close + 1;
            }
            for (int k = open.Count - 1; k >= 0; k--)
                sb.Append("</").Append(open[k]).Append('>');
            return sb.ToString();
        }

        /// <summary>丢掉一个标签之后，下一个读位置在哪。
        ///
        /// ★ 除了"就是 `close+1`"，还要吃掉**紧邻的一个孤立 `>`**（10-31 实测 bug）★
        /// 模型会写成 `<color>黄>几时到期</color>`（漏等号，颜色名跑到尖括号外面）。
        /// 那时 `<color>` 因缺参数被丢掉，而紧随的 `黄>` 会当正文上屏 ⇒ 玩家看到"黄>几时到期"。
        /// 判据：标签之后**跳过空白**，若第一个字符是 `>` 就一并吃掉（这个 `>` 本来也该是标签的一部分）。
        /// 只在"刚丢了一个标签"这一刻生效 —— 正文里独立的 `>` 不受影响。
        /// </summary>
        private static int SkipTagTail(string s, int at)
        {
            int n = s.Length;
            int k = at;
            while (k < n && (s[k] == ' ' || s[k] == '\t')) k++;
            if (k < n && s[k] == '>') return k + 1;                 // 紧邻的孤立 '>'
            // `<color>黄>字`：漏等号时颜色名落在尖括号外，紧跟的 `黄>` 也会上屏。
            // 只在**后面还跟着一个 `>`** 时才吃（那说明这截确实是写崩的标签尾巴）；
            // `甲<blink>乙` 这种"标签后面直接接正文"不会被误吃。
            int m = k;
            while (m < n && IsTagChar(s[m]) && s[m] != '>') m++;
            if (m > k && m < n && s[m] == '>') return m + 1;
            return at;
        }

        /// <summary>白名单归一化：`color`/`size` 原样返回；其余 null。</summary>
        private static string Canonical(string name)
        {
            if (string.IsNullOrEmpty(name)) return null;
            string k = name.ToLowerInvariant();
            // ★ 只有 color（+ size，但提示词不教）。`b`/`i` 10-31 删掉：
            //   实测模型 8/8 场景都不用，强制要求也不用（见 docs §E.4.2）；
            //   留着就是"告诉模型能用、它不用、偶然用了又突然变粗"的不一致。
            return (k == "color" || k == "size") ? k : null;
        }

        /// <summary>把一条已过白名单的开标签变成**明确字面量**（参数不合法返回 null）。
        /// 颜色在这里被解析成 `#RRGGBB[AA]` 并压亮度 —— 输出永远是可读的那一档。</summary>
        private static string OpenTag(string canon, string arg)
        {
            // （没有 b/i 分支：它们不在白名单，Canonical 已经把它们判掉了）
            if (canon == "color")
            {
                string hex = ResolveColor(arg);
                return hex == null ? null : "<color=" + hex + ">";
            }
            // size
            int px;
            if (!int.TryParse(arg, out px)) return null;
            if (px < SizeMin || px > SizeMax) return null;
            return "<size=" + px + ">";
        }

        /// <summary>`颜色名` 或 `#RRGGBB` / `#RRGGBBAA` → 规范化的 `#RRGGBB[AA]`（已压亮度）。
        /// 认不出返回 null（调用方把整段标签丢掉）。</summary>
        private static string ResolveColor(string arg)
        {
            if (string.IsNullOrEmpty(arg)) return null;
            string named;
            if (Palette.TryGetValue(arg, out named)) return named;
            string lower = arg.ToLowerInvariant();
            if (Palette.TryGetValue(lower, out named)) return named;
            if (lower.Length != 7 && lower.Length != 9) return null;
            if (lower[0] != '#') return null;
            for (int k = 1; k < lower.Length; k++)
                if (!Uri.IsHexDigit(lower[k])) return null;
            int r = Hex(lower, 1), g = Hex(lower, 3), b = Hex(lower, 5);
            string alpha = lower.Length == 9 ? lower.Substring(7, 2) : "";
            CapLum(ref r, ref g, ref b);
            return "#" + r.ToString("X2") + g.ToString("X2") + b.ToString("X2") + alpha;
        }

        private static int Hex(string s, int at)
        {
            return Convert.ToInt32(s.Substring(at, 2), 16);
        }

        /// <summary>亮度过高就整体等比压暗到 <see cref="LumCap"/>（保色相，不换色）。
        /// 例：`#FFFFFF` → 中性浅灰，`#FFFF00` → 暗金 —— 两者在浅色气泡上都还读得出来。</summary>
        /// <summary>把颜色压到「在两种气泡底上都读得出来」。
        ///
        /// ── 为什么不再是"按亮度上限截断"（10-31 第二次重做）────────────────────
        /// 第一版拿感知亮度 `<= LumCap(0.50)` 当判据。扫描整个色空间后发现**大面积盲区**：
        /// 在"过了 LumCap 判定"的采样里，**34% 实际上对比度不足**（灰阶 `#5C5C5C~#7C7C7C` 整段都是）。
        /// 例：`#7F7F7F` 亮度 0.498 过判，但玩家气泡（浅绿 `#9BE375`）上对比度只有 **2.60** —— 等于隐形。
        ///
        /// 根因：亮度判的是"够不够暗"，而"读不读得出来"是两个**颜色之间**的关系。
        /// 正确判据是 WCAG 对比度（相对亮度比），且要**两个气泡底都过**。
        ///
        /// ── 现在的行为 ──────────────────────────────────────────────────────
        /// 先按 <see cref="MinContrast"/>（4.5，WCAG 2.1 正文标准）查**最差的那个底**；
        /// 不过就**等比压暗**（保色相），逐步收紧直到过线。
        /// 压到最暗（纯黑）仍不过的极端情况直接给黑 —— 黑色在两种浅底上对比度最高，必过。
        ///
        /// 严格来说这是"必要时压暗"，不是"无条件压暗"：本来就够清楚的颜色**一个字节都不动**
        /// （调色板里的 7 个色都是算出来的，正常路径走不到这里）。</summary>
        private static void CapLum(ref int r, ref int g, ref int b)
        {
            if (PassesContrast(r, g, b)) return;          // 已经够清楚：不动
            // 等比压暗（保色相）。k 从 1 往下收，找到第一个「两个底都过」的档。
            // 步长 2%：19px 的中文字上，2% 的差别肉眼看不出来，但能保证不会压过头。
            for (double k = 0.98; k > 0.0; k -= 0.02)
            {
                int nr = Clamp255(r * k), ng = Clamp255(g * k), nb = Clamp255(b * k);
                if (PassesContrast(nr, ng, nb))
                {
                    r = nr; g = ng; b = nb;
                    return;
                }
            }
            r = g = b = 0;                                // 兜底：纯黑（两种浅底上都必过）
        }

        /// <summary>这个颜色在**两种气泡底**上是否都达到 <see cref="MinContrast"/>。
        /// 两种底 = 代码里 <see cref="BubbleBgNpc"/>(灰) 与 <see cref="BubbleBgUser"/>(浅绿)；
        /// 后者是玩家气泡，第一版就是漏了它才出现"绿底上看不见"。</summary>
        private static bool PassesContrast(int r, int g, int b)
        {
            return ContrastVs(r, g, b, BubbleBgNpc) >= MinContrast
                && ContrastVs(r, g, b, BubbleBgUser) >= MinContrast;
        }

        /// <summary>WCAG 2.1 对比度：(L1+0.05)/(L2+0.05)，L 为相对亮度。</summary>
        private static double ContrastVs(int r, int g, int b, int[] bg)
        {
            double l1 = RelLum(r, g, b);
            double l2 = RelLum(bg[0], bg[1], bg[2]);
            double hi = l1 > l2 ? l1 : l2;
            double lo = l1 > l2 ? l2 : l1;
            return (hi + 0.05) / (lo + 0.05);
        }

        /// <summary>WCAG 相对亮度（sRGB 先线性化再加权）。</summary>
        private static double RelLum(int r, int g, int b)
        {
            return 0.2126 * SrgbLin(r) + 0.7152 * SrgbLin(g) + 0.0722 * SrgbLin(b);
        }

        private static double SrgbLin(int v)
        {
            double x = v / 255.0;
            return x <= 0.03928 ? x / 12.92 : Math.Pow((x + 0.055) / 1.055, 2.4);
        }

        private static int Clamp255(double v)
        {
            int i = (int)Math.Round(v);
            return i < 0 ? 0 : (i > 255 ? 255 : i);
        }

        // ------------------------------------------------------------------
        // 游戏时间戳前缀的**显示层**剥离（10-22 用户需求："气泡别带日期，破坏沉浸感"）
        //
        // 契约（两侧必须同一串语义，改动前先看全）：
        //   · 带戳的那一份 = 发 WS / 落账 / 模型上下文 / OfferSolo 重演帧，**一个字不动**
        //     （NPC 靠它说"上次见你是三月前"；Python 的 `/compact` 判据也认它）；
        //   · 上屏的那一份 = 在这里剥掉行首前缀。三个渲染出口（实时回显、关窗重演、
        //     get_history 回放）全部收敛在 `ChatWindow.AppendUserMessage` 一个口 ⇒ 一处剥、
        //     三处观感必然同步（不会"实时干净、回放又长出日期"——那正是 09-13 定案时
        //     绕开的那个分叉，这次靠单一渲染口把它做对）。
        //   · 语义镜像 Python `transport/ws_channel._TIME_STAMP_RE`：
        //     `^\[\d+年\d+月(\d+日)?\]\s*` —— 行首、只一次、日可选（日历读不到日时
        //     `CnDate` 退化成"N年M月"）、前缀后的空白吃到第一个非空白为止。
        //   · 手写扫描不用 Regex：玩家泡每画一条跑一次（重放一窗几百条），与 Tighten
        //     同款零编译开销；形状不符**原样返回**，玩家正文绝不受伤。
        // ------------------------------------------------------------------

        /// <summary>剥行首游戏时间戳（`[N年M月] ` / `[N年M月D日] ` 及其后空白）。
        /// 不匹配形状 → 原样返回；只剥一次。null/空 → 原样返回。</summary>
        public static string StripLeadStamp(string s)
        {
            int end = MatchLeadStamp(s, out _, out _, out _);
            return end < 0 ? s : s.Substring(end);
        }

        /// <summary>解析行首游戏时间戳的年月日（供微信式时间分隔行判档，10-22）。
        /// 命中返回 true；**无日形态**（`[N年M月]`，日历读不到日时 CnDate 的退化形）
        /// 命中但 d=0。与 StripLeadStamp 共用同一个扫描器（MatchLeadStamp）——
        /// "剥的"与"认的"必须是同一个形状，各写一份解析就是下一次漂移的种子。</summary>
        public static bool TryParseLeadStamp(string s, out int y, out int m, out int d)
        {
            return MatchLeadStamp(s, out y, out m, out d) >= 0;
        }

        /// <summary>扫描行首戳，返回**前缀（含其后空白）结束处**的下标；不匹配返回 -1。
        /// 形状 = `\[数字+年数字+月(数字+日)?\]` + 任意空白（对齐 Python `_TIME_STAMP_RE`）。</summary>
        private static int MatchLeadStamp(string s, out int y, out int m, out int d)
        {
            y = m = d = 0;
            if (string.IsNullOrEmpty(s) || s[0] != '[') return -1;
            int n = s.Length;
            int i = 1;
            if (!ScanNum(s, ref i, out y)) return -1;             // 年：至少一位数字
            if (i >= n || s[i] != '年') return -1;
            i++;
            if (!ScanNum(s, ref i, out m)) return -1;             // 月：至少一位数字
            if (i >= n || s[i] != '月') return -1;
            i++;
            int save = i;
            int day = 0;
            if (ScanNum(s, ref i, out day) && i < n && s[i] == '日') i++;   // 日可选
            else { i = save; day = 0; }                                     // 缺日字 → 退回月起算（不吞数字）
            d = day;
            if (i >= n || s[i] != ']') return -1;
            i++;
            while (i < n && (IsPad(s[i]) || s[i] == '\n' || s[i] == '\r')) i++;
            return i;
        }

        /// <summary>从 ref i 起扫连续 ASCII 数字（≤9 位，防回绕）；至少一位才 true，i 停在结束处。</summary>
        private static bool ScanNum(string s, ref int i, out int v)
        {
            int start = i, acc = 0;
            while (i < s.Length && s[i] >= '0' && s[i] <= '9')
            {
                acc = acc * 10 + (s[i] - '0');
                i++;
            }
            v = acc;
            if (i == start) return false;
            if (i - start > 9) { i = start; v = 0; return false; }   // 超长数字不是日期
            return true;
        }
    }
}
