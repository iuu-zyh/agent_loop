/// <summary>
/// 工具侧边栏（「可做的事」）的**判定核** —— 零 Unity 依赖的纯逻辑，与 `HotkeyCore` /
/// `ToolFoldPolicy` 同款：刻意不 using UnityEngine，好让 net8.0 单测直接把它编进去跑。
///
/// ## 它判定什么
///
/// 侧边栏每行点下去只有两种结局（由 Python 下发目录时定的 `mode` 决定）：
///   · `send` —— 这句话**直接发出去**（走玩家发言的同一条路）。
///   · `fill` —— 这句话**不发**，只填进输入框，光标停在空位，玩家补齐再按回车。
///
/// 这个文件只负责后者的两个纯判定：**空位在哪**、**光标该落在哪**。
///
/// ## 为什么空位判定必须被测试锁住
///
/// `fill` 型条目的台词里用**全角空格 U+3000** 表示"这里等你补"。C# 若把它当普通空格
/// 处理（`IndexOf(' ')`），永远找不到 → 光标落在末尾 → 玩家得自己找位置删字。
/// 更糟的是若认错了字符（比如半角空格），`say` 里其他位置的空格会让光标停在一个
/// **无关的字中间** —— 而这种错在实机上只是"手感怪"，零报错，永远不会有人来修。
///
/// 所以 `FirstBlank` / `CaretAt` 两个判定带着下面的单测（`ToolCatalogTests`）走。
/// 接线（建面板、拉数据、发消息）在 `UI/ToolPanel.cs`，那边由
/// `tests/test_tool_sidebar_contract.py` 钉住。
/// </summary>
namespace AgentLoopBridge
{
    /// <summary>侧边栏目录的纯判定核。所有输入都是已解析好的字符串，不碰 Unity、不碰网络。</summary>
    public static class ToolCatalogCore
    {
        /// <summary>空位标记：全角空格 U+3000（与 `tools/catalog.py` 的 `BLANK` 逐字相同）。
        /// **不要改成半角空格** —— 见文件头。</summary>
        public const char Blank = '\u3000';

        /// <summary>`mode` 字符串的两个合法取值（与 Python 侧 `_i()` 生成的一致）。</summary>
        public const string ModeSend = "send";
        public const string ModeFill = "fill";

        /// <summary>空位下标；没有空位返回 -1。</summary>
        public static int FirstBlank(string say)
        {
            if (string.IsNullOrEmpty(say)) return -1;
            return say.IndexOf(Blank);
        }

        /// <summary>是不是 fill 型（含空位）。目录给的 `mode` 只作参考 —— **以台词本身为准**：
        /// 万一 Python 侧把 mode 写错、而台词里确实有空位，仍按 fill 处理（宁可多填一次，
        /// 也不要把一句带着空格的话直接发给模型）。</summary>
        public static bool IsFill(string mode, string say)
        {
            if (FirstBlank(say) >= 0) return true;
            return string.Equals(mode ?? "", ModeFill, System.StringComparison.OrdinalIgnoreCase);
        }

        /// <summary>
        /// 点下这一行之后，输入框里该是什么、光标该在哪。
        ///
        /// 返回值：`text` = 要填进输入框的串（**原样保留空位** —— 玩家在空位上打字即可，
        /// 不需要先删掉一个占位符；删了他反而不知道原来该填这儿）；
        /// `caret` = 光标位置（Unity `InputField.caretPosition` 的语义：**字符**下标）。
        ///
        /// 没有空位时 `caret` = 串尾（等价于"直接发"后清空，但这个函数只管 fill 型）。
        /// </summary>
        public static string TextFor(string say)
        {
            return say ?? "";
        }

        public static int CaretAt(string say)
        {
            int i = FirstBlank(say);
            return i >= 0 ? i : (say ?? "").Length;
        }

        /// <summary>
        /// 发出去之前**把空位换成什么**：玩家点 fill 型条目后直接按了回车、一个字没补 ——
        /// 这时串里还留着全角空格。原样发出去会让模型看到一段莫名的空白（账本里永久留着），
        /// 所以这里把裸空位收成一个**全角逗号占位**？不 —— 那样更怪。
        ///
        /// 定案：**原样发**。理由是这条路径实际上不该发生（`ToolPanel` 在 fill 型点击后
        /// 不发送，只填框），而万一发生（玩家自己按了回车），他要的就是"把这句话发出去"，
        /// 其中那个空位正是他跳过的内容 —— 模型看到「你那儿的那　，可否匀我一件？」
        /// 会自然追问"哪件"，这正是**正确的对话行为**，不是故障。改写成任何别的字符都只是
        /// 把"我漏填了"伪装成"我说了别的"。
        ///
        /// 本函数因此只是把契约写下来（并且给测试一个可断言的落点），**不改变任何字符**。
        /// </summary>
        public static string BeforeSend(string say)
        {
            return say ?? "";
        }
    }
}
