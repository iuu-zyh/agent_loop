// 群聊 @ 补全策略核（MentionPickCore）的断言程序。
//
// 全部是**纯逻辑**断言：不碰 Unity、不碰游戏、不需要运行时。
// 每条都对应真机上一种"静默失败"（候选不弹 / 弹着不消失 / 吃掉半个名字）。
using System;
using System.Collections.Generic;
using AgentLoopBridge;

namespace AgentLoopBridge.Tests
{
    internal static class Program
    {
        private static int _pass;
        private static int _fail;

        private static void Check(bool cond, string what)
        {
            if (cond) { _pass++; return; }
            _fail++;
            Console.WriteLine("  ✗ " + what);
        }

        private static void Eq<T>(T actual, T expect, string what)
        {
            bool ok = EqualityComparer<T>.Default.Equals(actual, expect);
            if (ok) { _pass++; return; }
            _fail++;
            Console.WriteLine("  ✗ " + what + $"：期望 {expect}，实际 {actual}");
        }

        private static readonly string[] Members = { "林婉清", "李四", "王五", "张三丰", "张三" };

        public static int Main()
        {
            Console.WriteLine("=== MentionPickCore 单元测试 ===");

            // ── ① Analyze：该不该弹 ──────────────────────────────────
            Console.WriteLine("① Analyze：触发判据");

            var c = MentionPickCore.Analyze("你好", 2);
            Check(!c.HasAt, "没有 @ 时不该弹");

            c = MentionPickCore.Analyze("@", 1);
            Check(c.HasAt, "刚打完 @ 就该弹（查询词为空）");
            Eq(c.Query, "", "@ 后面还没打字时查询词为空");
            Eq(c.AtIndex, 0, "@ 的下标");

            c = MentionPickCore.Analyze("@李", 2);
            Check(c.HasAt, "@ + 一个字仍然弹");
            Eq(c.Query, "李", "查询词 = @ 到光标之间");

            c = MentionPickCore.Analyze("我想找@李", 5);
            Check(c.HasAt, "@ 前面有文字也弹");
            Eq(c.Query, "李", "查询词不含 @ 之前的文字");

            // ★ 坑②：光标已经离开 @ 段（中间有空格）⇒ 不该弹
            c = MentionPickCore.Analyze("@李四 你好", 7);
            Check(!c.HasAt, "光标离开 @ 段（隔了空格）后不该继续弹");

            // ★ 标点也算边界
            c = MentionPickCore.Analyze("@李四，你好", 7);
            Check(!c.HasAt, "光标在中文逗号之后 ⇒ 不弹");

            // 光标在 @ 之前（用户把光标挪回去）⇒ 不弹
            c = MentionPickCore.Analyze("找@李四", 0);
            Check(!c.HasAt, "光标在 @ 左边 ⇒ 不弹");

            // 全角 @
            c = MentionPickCore.Analyze("＠李", 2);
            Check(c.HasAt, "全角 ＠ 也要认");

            // ★ 连写：@张三@李 应补**后一个**
            c = MentionPickCore.Analyze("@张三@李", 5);
            Check(c.HasAt, "连写时弹");
            Eq(c.Query, "李", "连写时应补后一个 @");
            Eq(c.AtIndex, 3, "连写时 @ 下标指向后一个");

            // caret 越界（UGUI 在输入法组合态会给 -1 或超长）
            c = MentionPickCore.Analyze("@李", -5);
            Check(!c.HasAt, "caret<0 夹到 0 ⇒ 光标在 @ 左边 ⇒ 不弹");
            c = MentionPickCore.Analyze("@李", 999);
            Check(c.HasAt, "caret 超长夹到末尾，仍能识别");
            Eq(c.Query, "李", "caret 超长时查询词正确");

            // 空文本
            c = MentionPickCore.Analyze("", 0);
            Check(!c.HasAt, "空文本不弹");
            c = MentionPickCore.Analyze(null, 0);
            Check(!c.HasAt, "null 文本不弹（不许抛）");

            // ── ② Filter：候选 ──────────────────────────────────────
            Console.WriteLine("② Filter：候选过滤");

            var all = MentionPickCore.Filter(Members, "");
            Eq(all.Count, 5, "空查询词 = 全体成员");
            Eq(all[0], "林婉清", "保持成员原始顺序");

            var li = MentionPickCore.Filter(Members, "李");
            Eq(li.Count, 1, "「李」只匹配李四");
            Eq(li[0], "李四", "匹配到李四");

            // ★ 子串匹配：记得后两个字也要能查到
            var wan = MentionPickCore.Filter(Members, "婉清");
            Eq(wan.Count, 1, "子串匹配（不要求从第一个字开始）");
            Eq(wan[0], "林婉清", "「婉清」命中林婉清");

            // 「张三」应同时命中「张三丰」与「张三」
            var zs = MentionPickCore.Filter(Members, "张三");
            Eq(zs.Count, 2, "「张三」同时命中张三丰与张三");

            var none = MentionPickCore.Filter(Members, "赵六");
            Eq(none.Count, 0, "没匹配 ⇒ 空（调用方据此隐藏浮层）");

            // 上限
            var many = new List<string>();
            for (int i = 0; i < 30; i++) many.Add("人" + i);
            Eq(MentionPickCore.Filter(many, "").Count, MentionPickCore.MaxCandidates,
               "候选数被 MaxCandidates 截断");

            // null 容错
            Eq(MentionPickCore.Filter(null, "李").Count, 0, "members=null 不许抛");

            // ── ③ Accept：替换 ──────────────────────────────────────
            Console.WriteLine("③ Accept：选中后替换");

            string nt; int nc;

            // 最常见：@李 + 选中李四
            c = MentionPickCore.Analyze("@李", 2);
            MentionPickCore.Accept("@李", c, "李四", out nt, out nc);
            Eq(nt, "@李四 ", "补上完整名字 + 一个空格");
            Eq(nc, 4, "光标在空格之后（可以直接接着打字）");

            // 后面已经有空格：不重复补
            c = MentionPickCore.Analyze("@李 你好", 2);
            MentionPickCore.Accept("@李 你好", c, "李四", out nt, out nc);
            Eq(nt, "@李四 你好", "后面已有空格则不重复补");

            // 后面紧跟标点：不补空格
            c = MentionPickCore.Analyze("@李，你好", 2);
            MentionPickCore.Accept("@李，你好", c, "李四", out nt, out nc);
            Eq(nt, "@李四，你好", "后面是标点则不补空格");

            // 只打 @（还没打字）
            c = MentionPickCore.Analyze("@", 1);
            MentionPickCore.Accept("@", c, "林婉清", out nt, out nc);
            Eq(nt, "@林婉清 ", "刚打完 @ 就选");
            Eq(nc, 5, "光标位置正确");

            // @ 前面有文字
            c = MentionPickCore.Analyze("我想找@李", 5);
            MentionPickCore.Accept("我想找@李", c, "李四", out nt, out nc);
            Eq(nt, "我想找@李四 ", "只替换 @ 段，前面的文字不动");

            // ★ 已知取舍：原文 @ 后面还有字（用户手打了半个名字又点候选）
            //   替换范围只到光标 ⇒ 后面的字留着，结果 @李四四
            c = MentionPickCore.Analyze("@李", 2);
            MentionPickCore.Accept("@李四", c, "李四", out nt, out nc);
            // 结果 `@李四 四`：补的空格把残留的 `四` 分开了 —— 难看但**信息没丢**，
            // 玩家一眼能看出多了个「四」，删掉即可。反方向（吞掉后面的字）会静默吃内容。
            Eq(nt, "@李四 四", "已知取舍：只替换到光标，不吞后面的字");

            // 连写：替换后一个
            c = MentionPickCore.Analyze("@张三@李", 5);
            MentionPickCore.Accept("@张三@李", c, "李四", out nt, out nc);
            Eq(nt, "@张三@李四 ", "连写时只替换后一个 @ 段");

            // 无 @ 上下文：安全地什么都不做
            c = MentionPickCore.Analyze("你好", 2);
            MentionPickCore.Accept("你好", c, "李四", out nt, out nc);
            Eq(nt, "你好", "没有 @ 上下文时原样返回");
            Eq(nc, 2, "光标不动");

            // name 为空：原样返回
            c = MentionPickCore.Analyze("@李", 2);
            MentionPickCore.Accept("@李", c, null, out nt, out nc);
            Eq(nt, "@李", "name 为空时原样返回");

            // ── ④ MoveSelection：键盘上下 ────────────────────────────
            Console.WriteLine("④ MoveSelection：键盘选择");

            Eq(MentionPickCore.MoveSelection(0, 1, 3), 1, "向下");
            Eq(MentionPickCore.MoveSelection(1, 1, 3), 2, "向下到末位");
            Eq(MentionPickCore.MoveSelection(2, 1, 3), 0, "末位再向下 ⇒ 回到首位（循环）");
            Eq(MentionPickCore.MoveSelection(0, -1, 3), 2, "首位再向上 ⇒ 跳末位（循环）");
            Eq(MentionPickCore.MoveSelection(1, -1, 3), 0, "向上");
            Eq(MentionPickCore.MoveSelection(-1, 1, 3), 0, "无高亮时向下 ⇒ 首位");
            Eq(MentionPickCore.MoveSelection(-1, -1, 3), 2, "无高亮时向上 ⇒ 末位");
            Eq(MentionPickCore.MoveSelection(0, 1, 0), -1, "没有候选项 ⇒ -1");
            Eq(MentionPickCore.MoveSelection(5, 1, 3), 0, "越界时归位");

            // ── ⑤ 与 Python 侧判据同源（分隔符表）───────────────────
            Console.WriteLine("⑤ 与 Python 解析器的分隔符表同源");

            // 这些字符在 Python 的 `_NAME_STOP` 里都有；少一个就会"UI 弹候选、Python 认不出"
            foreach (char ch in new char[] { ' ', '\t', '\n', '　', ',', '，', '.', '。',
                                             '!', '！', '?', '？', ';', '；', ':', '：',
                                             '、', '~', '～', ')', '）', ']', '】', '》' })
            {
                bool inCore = Array.IndexOf(MentionPickCore.NameStop, ch) >= 0;
                Check(inCore, $"分隔符 {ch}（U+{(int)ch:X4}）在 MentionPickCore.NameStop 里");
            }

            Console.WriteLine();
            if (_fail == 0)
            {
                Console.WriteLine($"全部通过：{_pass} 项断言");
                return 0;
            }
            Console.WriteLine($"失败 {_fail} 项，通过 {_pass} 项");
            return 1;
        }
    }
}
