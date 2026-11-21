// 气泡正文整形核（BubbleTextCore.Tighten）单元测试。
//
// 为什么要有这个工程：这段折叠一旦写偏，症状**全都不报错**——
//   ① 空行没折掉 → 用户看到的还是"换行太大"（就是被投诉的那张图，等于白改）；
//   ② 折过头（把单个换行也吃了、或把行内空格也吃了）→ 模型精心分的段并成一行、
//      语气/断句全变，而且看起来"像模型自己写成这样的"，比①更难发现；
//   ③ 换行符处理偏 → `\r\n` 变成两个换行 ⇒ 反而多出一行空白（Windows 侧模型会发 \r\n）。
// 这三类在实机上都没法"随手复现"（要正好等到模型发空行的那条回复），所以钉在判定表里。
//
// ⚠ 诚实边界：这里只测**规则**。"有没有接上、接的是不是那四个写泡点"由 Python 侧源码契约
//   测试 tests/test_bubble_text_contract.py 锁在真实 ChatWindow.cs / ChatItemViews.cs 上 ——
//   单测全绿 + 一处没接 = 用户侧一点变化都没有。

using System;
using System.Collections.Generic;
using AgentLoopBridge;

namespace AgentLoopBridge.Tests
{
    internal static class Program
    {
        private static int _pass;
        private static readonly List<string> _fails = new List<string>();

        private static void Check(string name, bool ok, string detail = "")
        {
            if (ok) { _pass++; return; }
            _fails.Add(name + (detail.Length > 0 ? "  ｜ " + detail : ""));
            Console.WriteLine("  ✗ " + name + (detail.Length > 0 ? "  ｜ " + detail : ""));
        }

        private static void Eq<T>(string name, T got, T want) =>
            Check(name, EqualityComparer<T>.Default.Equals(got, want),
                  "实际=" + Show(got) + " 期望=" + Show(want));

        private static string Show<T>(T v) => v == null ? "<null>" : v.ToString().Replace("\n", "\\n").Replace("\r", "\\r");

        private static int Main()
        {
            Console.WriteLine("=== BubbleTextCore 单元测试 ===");
            Section("空行折叠（核心症状）", CollapseBlank);
            Section("换行符归一（\\r\\n 不许变成两行）", NewlineNormalize);
            Section("行首尾空白与全角空白", TrimPad);
            Section("不该动的东西：内容 / 单换行 / 行内空格", KeepContent);
            Section("边界：null / 空 / 纯空白 / 幂等", EdgeAndIdempotent);
            Section("真机样本：09-17 截图那条回复", RealSample);
            Section("游戏时间戳剥离（显示层，10-22）", StripStamps);
            Section("富文本白名单（10-31：模型输出着色）", RichWhitelist);
            Section("富文本配平与流式半截标签", RichBalance);
            Section("富文本亮度压制（浅色气泡底可读性）", RichLuminance);
            Section("模型写崩的畸形标签（10-31 实测样本）", MalformedTags);
            Section("行距常量（两条腿共用）", LineSpacingConst);
            Console.WriteLine();
            if (_fails.Count == 0)
            {
                Console.WriteLine("全部通过：" + _pass + " 项断言");
                return 0;
            }
            Console.WriteLine("失败 " + _fails.Count + " 项 / 通过 " + _pass + " 项");
            return 1;
        }

        private static void Section(string name, Action body)
        {
            try { body(); }
            catch (Exception e)
            {
                // 一节抛了也要如实记成"这一节失败"并继续跑后面 —— 崩在第一行会让后面全没跑，
                // 看起来像"测试通过"（ToolFoldPolicyTests 踩过这个坑）
                Check("小节「" + name + "」不应抛异常", false, e.GetType().Name + ": " + e.Message);
            }
        }

        // ------------------------------------------------------------------
        // ① 空行折叠
        // ------------------------------------------------------------------
        private static void CollapseBlank()
        {
            Eq("一个空行折成单换行", BubbleTextCore.Tighten("a\n\nb"), "a\nb");
            Eq("三个空行也折成单换行", BubbleTextCore.Tighten("a\n\n\n\nb"), "a\nb");
            Eq("首部空行丢弃", BubbleTextCore.Tighten("\n\na"), "a");
            Eq("尾部空行丢弃", BubbleTextCore.Tighten("a\n\n"), "a");
            Eq("通篇空行 → 空串", BubbleTextCore.Tighten("\n\n\n"), "");
            Eq("纯空格行也算空行", BubbleTextCore.Tighten("a\n   \nb"), "a\nb");
            Eq("制表符行也算空行", BubbleTextCore.Tighten("a\n\t\nb"), "a\nb");
            Eq("全角空格行也算空行", BubbleTextCore.Tighten("a\n\u3000\nb"), "a\nb");
            Eq("不换行空格行也算空行", BubbleTextCore.Tighten("a\n\u00A0\nb"), "a\nb");
            Eq("连续多个空白行 + 单换行混合",
               BubbleTextCore.Tighten("甲\n\n \n乙\n丙"), "甲\n乙\n丙");
            Eq("行尾单换行（流式半截）不留尾巴空行",
               BubbleTextCore.Tighten("甲\n"), "甲");
        }

        // ------------------------------------------------------------------
        // ② 换行符归一
        // ------------------------------------------------------------------
        private static void NewlineNormalize()
        {
            Eq("\\r\\n 归一成单换行", BubbleTextCore.Tighten("a\r\nb"), "a\nb");
            Eq("\\r\\n\\r\\n（Windows 空行）折成单换行", BubbleTextCore.Tighten("a\r\n\r\nb"), "a\nb");
            Eq("孤立 \\r 也算换行", BubbleTextCore.Tighten("a\rb"), "a\nb");
            Eq("孤立 \\r 空行也折掉", BubbleTextCore.Tighten("a\r\rb"), "a\nb");
            Eq("混用 \\r\\n 与 \\n 的空行一起折", BubbleTextCore.Tighten("a\r\n\nb"), "a\nb");
            Eq("输出里不含 \\r", BubbleTextCore.Tighten("a\r\nb\rc").Contains("\r"), false);
        }

        // ------------------------------------------------------------------
        // ③ 首尾空白
        // ------------------------------------------------------------------
        private static void TrimPad()
        {
            Eq("行首尾半角空格修掉", BubbleTextCore.Tighten("  a  \n  b  "), "a\nb");
            Eq("行首尾制表符修掉", BubbleTextCore.Tighten("\ta\t"), "a");
            Eq("行首尾全角空格修掉", BubbleTextCore.Tighten("\u3000a\u3000"), "a");
            Eq("整体首尾空白修掉", BubbleTextCore.Tighten("\n  甲  \n"), "甲");
        }

        // ------------------------------------------------------------------
        // ④ 不许乱动
        // ------------------------------------------------------------------
        private static void KeepContent()
        {
            // ★ 单个换行必须**保留** —— 那是模型分的段，折掉就把两句话粘成一句了
            Eq("单个换行保留（分段结构）", BubbleTextCore.Tighten("甲\n乙"), "甲\n乙");
            Eq("三段各一行全保留", BubbleTextCore.Tighten("甲\n乙\n丙"), "甲\n乙\n丙");
            // 行内空格/全角空格不许动（折行内空格 = 改断句）
            Eq("行内半角空格保留", BubbleTextCore.Tighten("a b  c"), "a b  c");
            Eq("行内全角空格保留", BubbleTextCore.Tighten("甲\u3000乙"), "甲\u3000乙");
            Eq("行内标点/括号保留", BubbleTextCore.Tighten("(甲)乙，丙。"), "(甲)乙，丙。");
            Eq("数字与 ASCII 保留", BubbleTextCore.Tighten("HP 100/200"), "HP 100/200");
            Eq("中文标点 `……` 保留", BubbleTextCore.Tighten("这倒是有趣……"), "这倒是有趣……");
            Eq("反斜杠 / 引号等原样", BubbleTextCore.Tighten("a\\b\"c'd"), "a\\b\"c'd");
            // 名字前缀（群聊 `名字：正文`）不许被动
            Eq("群聊名字前缀保留", BubbleTextCore.Tighten("张三：你好\n\n李四：在"), "张三：你好\n李四：在");
            // 工具折叠标记等特殊符号
            Eq("★ 标记保留", BubbleTextCore.Tighten("★DynInt 缺失★"), "★DynInt 缺失★");
            Eq("富文本标签保留（StripRichTags 是另一边的事）",
               BubbleTextCore.Tighten("<color=#057600>甲</color>"), "<color=#057600>甲</color>");
        }

        // ------------------------------------------------------------------
        // ⑤ 边界与幂等
        // ------------------------------------------------------------------
        private static void EdgeAndIdempotent()
        {
            Eq("null → 空串", BubbleTextCore.Tighten(null), "");
            Eq("空串 → 空串", BubbleTextCore.Tighten(""), "");
            Eq("单个空格 → 空串", BubbleTextCore.Tighten(" "), "");
            Eq("单换行 → 空串", BubbleTextCore.Tighten("\n"), "");
            Eq("单字原样", BubbleTextCore.Tighten("甲"), "甲");

            // 幂等：进过气泡的文本再进一次不许再变（历史重放 / 终稿覆盖会重复经手）
            foreach (var s in new[] { "甲\n\n乙", "  甲  \n\n\n  乙  ", "a\r\n\r\nb", "\n\n", "甲\n乙" })
                Eq("幂等：" + Show(s), BubbleTextCore.Tighten(BubbleTextCore.Tighten(s)), BubbleTextCore.Tighten(s));

            // 长文本不崩（流式期间每帧都调，长度可达数千字）
            var big = new System.Text.StringBuilder();
            for (int i = 0; i < 2000; i++) big.Append("甲\n\n乙\n");
            var t = BubbleTextCore.Tighten(big.ToString());
            Eq("2000 段不崩且无空行", t.Contains("\n\n"), false);
            Eq("2000 段行数 = 4000", t.Split('\n').Length, 4000);
        }

        // ------------------------------------------------------------------
        // ⑥ 真机样本（09-17 截图那条回复的逐字文本）
        // ------------------------------------------------------------------
        private static void RealSample()
        {
            // 用户截图里 NPC 那条回复 = 三个自然段，段间各一个**空行**（`\n\n`）
            const string raw =
                "(这倒是有趣……我与他素不相识，却能在同一坐标相遇。炼气境散修，看来也是初入仙道的新人。)\n" +
                "\n" +
                "见过道友。\n" +
                "\n" +
                "(仔细打量对方) 你我能在此地相遇，也算有缘。不知道友来此是为了寻访机缘，还是有要事在身?";
            var got = BubbleTextCore.Tighten(raw);
            Eq("真机样本：不再含空行", got.Contains("\n\n"), false);
            Eq("真机样本：三段各一行",
               got,
               "(这倒是有趣……我与他素不相识，却能在同一坐标相遇。炼气境散修，看来也是初入仙道的新人。)\n" +
               "见过道友。\n" +
               "(仔细打量对方) 你我能在此地相遇，也算有缘。不知道友来此是为了寻访机缘，还是有要事在身?");
            // 行数 = 3 段（wrap 由 uGUI 按宽度做，不归这里管）⇒ 气泡高度少掉两个整行
            Eq("真机样本：行数 3", got.Split('\n').Length, 3);

            // 玩家侧那条一样的空行也要折（同一函数）
            Eq("玩家消息里的空行也折", BubbleTextCore.Tighten("[1年1月16日]见过道友\n\n你好"), "[1年1月16日]见过道友\n你好");
        }

        // ------------------------------------------------------------------
        // ⑦ 游戏时间戳剥离（StripLeadStamp，10-22：气泡不显示日期，账本照旧带戳）
        //    镜像 Python `transport/ws_channel._TIME_STAMP_RE`：`^\[\d+年\d+月(\d+日)?\]\s*`
        // ------------------------------------------------------------------
        private static void StripStamps()
        {
            // 现实形状：ChatPresenter 拼 `[N年M月D日] `；CnDate 读不到日退化 `[N年M月]`
            Eq("年月日 + 一个空格", BubbleTextCore.StripLeadStamp("[1年1月3日] 你好"), "你好");
            Eq("无日退化形状", BubbleTextCore.StripLeadStamp("[1年1月] 你好"), "你好");
            Eq("多位数", BubbleTextCore.StripLeadStamp("[123年10月29日] 出发"), "出发");
            Eq("戳后无空格也剥（对齐 Python \\s*）", BubbleTextCore.StripLeadStamp("[1年1月]你好"), "你好");
            Eq("戳后连续空白", BubbleTextCore.StripLeadStamp("[1年1月] \t 你好"), "你好");
            Eq("戳后换行", BubbleTextCore.StripLeadStamp("[1年1月]\n你好"), "你好");
            Eq("裸戳 → 空串", BubbleTextCore.StripLeadStamp("[1年1月]"), "");
            Eq("纯空白尾 → 空串", BubbleTextCore.StripLeadStamp("[1年1月]   "), "");

            // 不许误伤：形状不符一律原样（玩家正文永远完整）
            Eq("非行首不剥", BubbleTextCore.StripLeadStamp("你好[1年1月]"), "你好[1年1月]");
            Eq("只剥一次（双戳留第二个）",
               BubbleTextCore.StripLeadStamp("[1年1月] [2年2月] 甲"), "[2年2月] 甲");
            Eq("非数字", BubbleTextCore.StripLeadStamp("[x年y月] 甲"), "[x年y月] 甲");
            Eq("光年无月", BubbleTextCore.StripLeadStamp("[1年] 甲"), "[1年] 甲");
            Eq("无年字（如纯日戳）", BubbleTextCore.StripLeadStamp("[1月2日] 甲"), "[1月2日] 甲");
            Eq("缺右括号", BubbleTextCore.StripLeadStamp("[1年1月3日 甲"), "[1年1月3日 甲");
            Eq("有日数缺日字（1年1月3）", BubbleTextCore.StripLeadStamp("[1年1月3] 甲"), "[1年1月3] 甲");
            Eq("空方括号", BubbleTextCore.StripLeadStamp("[] 甲"), "[] 甲");
            Eq("纯数字方括号（坐标串）", BubbleTextCore.StripLeadStamp("[123] 甲"), "[123] 甲");
            Eq("无戳普通话", BubbleTextCore.StripLeadStamp("道友请了"), "道友请了");

            // 边界与幂等
            Eq("空串原样", BubbleTextCore.StripLeadStamp(""), "");
            Eq("null 原样", BubbleTextCore.StripLeadStamp(null), (string)null);
            Eq("幂等（剥过再进不变）",
               BubbleTextCore.StripLeadStamp(BubbleTextCore.StripLeadStamp("[1年1月] 甲")), "甲");

            // 与 Tighten 的组合顺序：AppendUserMessage 先剥戳，气泡内部再 Tighten ——
            // 剥完剩多行正文时 Tighten 照常折空行
            Eq("剥戳后正文含空行仍被折",
               BubbleTextCore.Tighten(BubbleTextCore.StripLeadStamp("[1年1月3日] 甲\n\n乙")), "甲\n乙");
        }

        // ------------------------------------------------------------------
        // ⑧ 富文本白名单（10-31 用户需求："希望模型能输出富文本、不同颜色"）
        //    白名单**只放行** color(名字/#hex) / b / i / size(8..30)；
        //    其余一律连标签丢掉 —— 否则上屏就是一串尖括号。
        // ------------------------------------------------------------------
        private static void RichWhitelist()
        {
            // 无标签：一个字都不许动（含中文标点、括号、数字）
            Eq("纯文本原样", BubbleTextCore.Render("道友请了。"), "道友请了。");
            Eq("正文里的比较符不是标签", BubbleTextCore.Render("甲 < 乙"), "甲 < 乙");
            Eq("行内尖括号成对但不在白名单", BubbleTextCore.Render("见 <x> 字"), "见  字");
            Eq("Render 先折空行", BubbleTextCore.Render("甲\n\n乙"), "甲\n乙");

            // ① 命名颜色 → 规范 hex（表在 BubbleTextCore.Palette）
            Eq("颜色名·红", BubbleTextCore.Render("<color=红>甲</color>"), "<color=#B2121D>甲</color>");
            Eq("颜色名·蓝", BubbleTextCore.Render("<color=蓝>甲</color>"), "<color=#1257B1>甲</color>");
            Eq("英文名同义", BubbleTextCore.Render("<color=red>甲</color>"), "<color=#B2121D>甲</color>");
            Eq("大小写不敏感", BubbleTextCore.Render("<COLOR=RED>甲</COLOR>"), "<color=#B2121D>甲</color>");
            Eq("直接给 hex", BubbleTextCore.Render("<color=#B2121D>甲</color>"), "<color=#B2121D>甲</color>");
            Eq("hex 归一成大写", BubbleTextCore.Render("<color=#b2121d>甲</color>"), "<color=#B2121D>甲</color>");
            Eq("认不出颜色名 → 丢标签留字",
               BubbleTextCore.Render("<color=荧光粉>甲</color>"), "甲");
            Eq("坏 hex → 丢标签留字", BubbleTextCore.Render("<color=#GG0000>甲</color>"), "甲");

            // ② 加粗/斜体
            // ★ b / i **不再放行**（10-31）：实测模型 8/8 场景不用、强制要求也不用，
            //   留着就是"教了不用、偶然用了又突然变粗"的不一致。现在它们同未知标签：只留字。
            Eq("b 标签不放行（只留字）", BubbleTextCore.Render("<b>甲</b>"), "甲");
            Eq("i 标签不放行（只留字）", BubbleTextCore.Render("<i>甲</i>"), "甲");

            // ③ size：区间 8..30，越界丢标签留字
            Eq("size 放行", BubbleTextCore.Render("<size=25>甲</size>"), "<size=25>甲</size>");
            Eq("size 太小丢标签", BubbleTextCore.Render("<size=3>甲</size>"), "甲");
            Eq("size 太大丢标签", BubbleTextCore.Render("<size=99>甲</size>"), "甲");
            Eq("size 非数字丢标签", BubbleTextCore.Render("<size=big>甲</size>"), "甲");

            // ④ 游戏自带标签（<g>/<y>/<sprite>）一律不进气泡：它们绑定的是游戏的调色板/图集
            Eq("游戏标签 <g> 丢", BubbleTextCore.Render("<g>甲</g>"), "甲");
            Eq("游戏标签 <y> 丢", BubbleTextCore.Render("<y>乙</y>"), "乙");
            Eq("sprite 标签丢", BubbleTextCore.Render("<sprite name=\"icon\">甲"), "甲");
            Eq("未知标签丢（白名单外）", BubbleTextCore.Render("<blink>甲</blink>"), "甲");

            // ⑤ 组合：颜色里套加粗
            Eq("color 里套 b（b 被剥、color 保留）",
               BubbleTextCore.Render("<color=红>甲<b>乙</b></color>"),
               "<color=#B2121D>甲乙</color>");

            // ⑥ 单行预览用的剥标签入口（不带配平、不认白名单：一律消失）
            Eq("StripTags 全剥", BubbleTextCore.StripTags("<color=#B2121D>甲</color>乙"), "甲乙");
            Eq("StripTags 不动普通正文", BubbleTextCore.StripTags("甲 乙"), "甲 乙");
            Eq("StripTags 空串", BubbleTextCore.StripTags(""), "");
        }

        // ------------------------------------------------------------------
        // ⑨ 配平（流式期间开闭标签会分两帧到）+ 半截标签
        // ------------------------------------------------------------------
        private static void RichBalance()
        {
            // 缺闭合 → 自动补齐（否则那半句话一直带色，甚至渗到下一行）
            Eq("缺闭合自动补", BubbleTextCore.Render("<color=红>甲"), "<color=#B2121D>甲</color>");
            Eq("多层缺闭合倒序补",
               BubbleTextCore.Render("<color=红><i>甲"), "<color=#B2121D>甲</color>");

            // 孤立闭合 → 丢弃（Unity 里多余的 </color> 会让后续整段错色）
            Eq("孤立闭合丢掉", BubbleTextCore.Render("甲</color>"), "甲");
            Eq("多出来的闭合丢掉",
               BubbleTextCore.Render("<color=红>甲</color></color>"),
               "<color=#B2121D>甲</color>");

            // 交叉嵌套（`<color=红><color=蓝>…</color></color>` → 非法）→ 合法化
            Eq("交叉嵌套合法化",
               BubbleTextCore.Render("<color=红>甲<size=20>乙</color></size>"),
               "<color=#B2121D>甲<size=20>乙</size></color>");

            // 同类重复开 → 前一个先收掉，不产生嵌套色
            Eq("同类重复开先收前一个",
               BubbleTextCore.Render("<color=红><color=蓝>甲</color>"),
               "<color=#B2121D></color><color=#1257B1>甲</color>");

            // ★ 流式半截标签：这一帧断在标签中间，不许把 `<color=#C6` 画上屏
            Eq("半截开标签整段丢弃", BubbleTextCore.Render("<color=#C6"), "");
            Eq("半截标签前面的话保留", BubbleTextCore.Render("甲<color=#C6"), "甲");
            Eq("流式中途：完整部分照常", BubbleTextCore.Render("<color=红>甲"), "<color=#B2121D>甲</color>");

            // ★★ 中文色名的半截形态（10-31 端到端实测抓到的真 bug）★★
            // 提示词教的就是 `<color=红>`，流式到 `=红` 那一帧第一版判据把中文当"正文"，
            // 于是 `见过<color=红` **原样画上屏**（屏幕上闪一串尖括号）。
            // 修法 = 半截判据同时要求"以某个白名单标签名开头或是它的前缀"。
            Eq("流式中文色名半截整段丢", BubbleTextCore.Render("见过<color=红"), "见过");
            Eq("流式中文色名+空格半截丢", BubbleTextCore.Render("见过<color= 红"), "见过");
            Eq("流式闭合标签半截丢", BubbleTextCore.Render("甲<color=红>乙</col"), "甲<color=#B2121D>乙</color>");
            Eq("流式就一个 '<'", BubbleTextCore.Render("见过<"), "见过");
            Eq("流式 '</' 半截", BubbleTextCore.Render("甲</"), "甲");
            // 但"不是标签名的前缀"仍然是正文
            Eq("中文非标签前缀 → 保留", BubbleTextCore.Render("甲<乙"), "甲<乙");
            Eq("'<' 后跟空格+中文 → 保留", BubbleTextCore.Render("甲< 乙"), "甲< 乙");

            // ★★ 正文里的裸尖括号（同一次实测抓到）★★
            // `< 这个，也像 >` 只按"有没有 `>`"判断会被当成合法标签 ⇒ 白名单外 ⇒ 连字一起吃掉。
            // 真实 Unity 标签**全部字母开头**，所以"字母开头"这条既不误伤真标签、又救回正文。
            Eq("正文裸尖括号成对", BubbleTextCore.Render("形状像 < 这个，也像 > 那个。"),
               "形状像 < 这个，也像 > 那个。");
            Eq("数学小于号", BubbleTextCore.Render("甲 < 乙，乙 > 丙"), "甲 < 乙，乙 > 丙");
            Eq("尖括号里是数字", BubbleTextCore.Render("血量 <100> 点"), "血量 <100> 点");
            Eq("尖括号里是中文标点", BubbleTextCore.Render("像 <这个，那个> 一样"),
               "像 <这个，那个> 一样");

            // `a<b>` 现在**不是**白名单标签（b 已删）⇒ 按标签丢掉、只留字
            Eq("a<b>c 的 b 已不在白名单", BubbleTextCore.Render("a<b>c"), "ac");
            Eq("数学式用非白名单字母才当正文", BubbleTextCore.Render("a<x>c"), "ac");

            // 幂等：配平过的再进一次不许再变（流式每帧都刷，翻倍增长会撑爆文本）
            string[] idem = {
                "<color=红>甲</color>",
                "甲<color=红>乙",
                "<color=红>甲<size=20>乙</color>",
                "甲</color>",
            };
            foreach (var s in idem)
                Eq("幂等：" + Show(s),
                   BubbleTextCore.Render(BubbleTextCore.Render(s)), BubbleTextCore.Render(s));
        }

        // ------------------------------------------------------------------
        // ⑩ 亮度压制（两种气泡底都是浅色：#E9E9E9 / #9BE375）
        //    ★ 这是"富文本看起来什么都没变"的头号原因：模型写 #FFFF00 在浅底上等于隐形。
        // ------------------------------------------------------------------
        private static void RichLuminance()
        {
            // 纯白 → 压到中性灰
            // ★ 白/黄压到哪儿，由**对比度**决定而不是亮度：
            //   旧版压到 #808080（亮度达标），但它在玩家浅绿气泡上对比度只有 2.56（要求 4.5）
            //   = 隐形。新版压到 #575757（灰底 5.95 / 绿底 4.69）。
            Eq("纯白压到可读", BubbleTextCore.Render("<color=#FFFFFF>甲</color>"), "<color=#575757>甲</color>");
            // 纯黄 → 暗金（不是纯黑：保色相）
            Eq("纯黄压成暗金", BubbleTextCore.Render("<color=#FFFF00>甲</color>"), "<color=#5C5C00>甲</color>");

            // 已经够暗的颜色**一点不动**（这是最常见的路径，动了就是把正确的颜色改坏）
            Eq("暗色不受影响", BubbleTextCore.Render("<color=#B2121D>甲</color>"), "<color=#B2121D>甲</color>");
            Eq("调色板每个值本身都够暗",
               BubbleTextCore.Render("<color=绿>甲</color>"), "<color=#0A681D>甲</color>");

            // 带 alpha 的写法保留 alpha，只压 RGB
            Eq("带 alpha 只压 RGB",
               BubbleTextCore.Render("<color=#FFFFFF80>甲</color>"), "<color=#57575780>甲</color>");

            // 压完必须真的变暗：逐字符看红/绿/蓝都 ≤ 0x90（拿纯黄当样本）
            string got = BubbleTextCore.Render("<color=#FFFF00>甲</color>");
            int r = Convert.ToInt32(got.Substring(8, 2), 16);
            int g = Convert.ToInt32(got.Substring(10, 2), 16);
            Check("压暗后亮度 ≤ 上限", 0.299 * r + 0.587 * g <= 0.50 * 255 + 1.0,
                  "r=" + r + " g=" + g);
        }

        // ------------------------------------------------------------------
        // ⑩bis 模型写崩的畸形标签（10-31 用真实模型跑出来的样本，不是编的）
        //
        // 提示词一变强，模型就会尝试用标签；尝试一多，写崩的形态就出现了。实测三种：
        //   · `<color>黄>字</color>`  漏等号，颜色名跑到尖括号外面
        //   · `<color:黄>字</color>`  用冒号不用等号
        //   · `只是<>劝你</>`          空标签
        // 三种都必须在**上屏前**处理掉 —— 否则玩家看到的是 `黄>几时到期` 或 `<>劝你</>`。
        // ------------------------------------------------------------------
        private static void MalformedTags()
        {
            Eq("漏等号 <color>黄>",
               BubbleTextCore.Render("剩<color>黄>三十枚</color>。"), "剩三十枚。");
            Eq("漏等号带空格 <color 黄>",
               BubbleTextCore.Render("剩<color 黄>三十枚</color>"), "剩三十枚");
            Eq("用冒号 <color:黄>",
               BubbleTextCore.Render("<color:黄>甲</color>"), "甲");
            Eq("空标签 <> </>",
               BubbleTextCore.Render("只是<>劝你</>。"), "只是劝你。");
            Eq("颜色名写错（浅黄不在表里）",
               BubbleTextCore.Render("<color=浅黄>甲</color>"), "甲");

            // ★ 不许误伤：上面那几条"吃掉写崩的尾巴"的逻辑很容易踩到正常文本
            Eq("正常富文本照旧",
               BubbleTextCore.Render("<color=红>甲</color>乙"), "<color=#B2121D>甲</color>乙");
            Eq("裸尖括号成对照旧",
               BubbleTextCore.Render("像 < 这个 > 那个"), "像 < 这个 > 那个");
            Eq("数学式照旧",
               BubbleTextCore.Render("甲 < 乙 且 乙 > 丙"), "甲 < 乙 且 乙 > 丙");
            Eq("孤立右尖照旧", BubbleTextCore.Render("甲 > 乙"), "甲 > 乙");
            Eq("数字比较照旧", BubbleTextCore.Render("100>50"), "100>50");
            Eq("未知标签仍只删标签",
               BubbleTextCore.Render("甲<blink>乙"), "甲乙");
            Eq("中文一个字不少", BubbleTextCore.Render("道友请了。"), "道友请了。");
        }

        // ------------------------------------------------------------------
        // ⑪ 行距常量（预制件那条腿在 prefab 补丁脚本里，Python 契约测试比对两侧）
        // ------------------------------------------------------------------
        private static void LineSpacingConst()
        {
            Check("行距 > 1（1 = 紧贴，正是「密密麻麻」的成因）",
                  BubbleTextCore.BubbleLineSpacing > 1.0f,
                  "实际=" + BubbleTextCore.BubbleLineSpacing);
            Check("行距 ≤ 1.6（过大会让气泡长一倍，滚动变累）",
                  BubbleTextCore.BubbleLineSpacing <= 1.6f,
                  "实际=" + BubbleTextCore.BubbleLineSpacing);
            Check("行距为 1.35（与 NoticePanel 正文同款，实测好读）",
                  Math.Abs(BubbleTextCore.BubbleLineSpacing - 1.35f) < 0.001f,
                  "实际=" + BubbleTextCore.BubbleLineSpacing);
        }
    }
}
