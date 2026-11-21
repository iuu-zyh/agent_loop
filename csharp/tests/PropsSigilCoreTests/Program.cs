// 物品/装备介绍占位符核（PropsSigilCore）单元测试。
//
// 为什么要有这个工程：这段判定一旦走偏，症状是两种**都不报错**的坏结果：
//   ① 该替换的没替换 → 模型又看到 `&608211_kcll&`（用户 09-17 实机投诉的那张图）；
//   ② 猜错了列 → 数值差几十倍（`&608203_qtsh&` 境界1=80 / 境界10=5454），而且**看着像正常数字**，
//      比占位符更难发现。
// 真机上这两类都没法"随手复现"（要正好背着一件带占位符的符箓），所以钉在判定表里。
//
// ⚠ 诚实边界：真实 conf（`g.conf.battleSkillValue.GetItem`）、`GameTool.LS`、
//   `new BattleSkillValueData(propsData)` 都要游戏运行时，WSL 里跑不了。这里只测**规则**；
//   "接线有没有被改回去 / 列来源是不是配置表境界"由 Python 侧源码契约测试
//   tests/test_item_desc_sigil_contract.py 锁在真实 UnitSnapshot.cs 上 —— 两边合起来才完整。

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
                  "实际=" + got + " 期望=" + want);

        private static int Main()
        {
            Console.WriteLine("=== PropsSigilCore 单元测试 ===");
            Section("境界归一与来源", GradePick);
            Section("选列：正常路径", PickNormal);
            Section("选列：绝不猜", PickNeverGuess);
            Section("选列：合法值与空值", PickZeroAndEmpty);
            Section("选列：真机数据实证", PickRealRows);
            Section("扫描：替换与保留", ScanReplace);
            Section("扫描：残留计数与健壮性", ScanRobust);
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
                // 一节里抛了也要如实记成"这一节失败"并继续跑后面的小节 —— 崩在第一行会让
                // 后面全都没跑，看起来像"测试通过"（ToolFoldPolicyTests 踩过这个坑）
                Check("小节「" + name + "」不应抛异常", false, e.GetType().Name + ": " + e.Message);
            }
        }

        /// <summary>十列取值行（值形如 "10","20",…，便于一眼看出取了第几列）。</summary>
        private static string[] Cols(int start, int step)
        {
            var v = new string[10];
            for (int i = 0; i < 10; i++) v[i] = (start + step * i).ToString();
            return v;
        }

        private static string[] Same(string val)
        {
            var v = new string[10];
            for (int i = 0; i < 10; i++) v[i] = val;
            return v;
        }

        // ------------------------------------------------------------ 境界

        private static void GradePick()
        {
            Eq("NormGrade(0)=0（未知）", PropsSigilCore.NormGrade(0), 0);
            Eq("NormGrade(-3)=0", PropsSigilCore.NormGrade(-3), 0);
            Eq("NormGrade(11)=0（超表宽）", PropsSigilCore.NormGrade(11), 0);
            Eq("NormGrade(1)=1", PropsSigilCore.NormGrade(1), 1);
            Eq("NormGrade(10)=10", PropsSigilCore.NormGrade(10), 10);

            Eq("配置表有效 → 用配置表（可审计，Excel 表头「道具境界」）",
               PropsSigilCore.PickGrade(2, 5), 5);
            Eq("配置表有效且与运行时一致 → 同一个", PropsSigilCore.PickGrade(3, 3), 3);
            Eq("配置表未知 → 退回运行时 vd.grade", PropsSigilCore.PickGrade(4, 0), 4);
            Eq("配置表越界同样视为未知", PropsSigilCore.PickGrade(4, 99), 4);
            Eq("两边都未知 → 0", PropsSigilCore.PickGrade(0, 0), 0);
            Eq("两边都越界 → 0", PropsSigilCore.PickGrade(-1, 11), 0);
        }

        // ------------------------------------------------------------ 选列

        private static void PickNormal()
        {
            var r = PropsSigilCore.PickColumn(Cols(10, 10), 0, 1);
            Eq("境界1 → 取第 1 列", r.Value, "10");
            Eq("境界1 → Grade 报告 1", r.Grade, 1);
            Check("境界1 → Kind=Ok", r.Kind == PropsSigilCore.PickKind.Ok, r.Kind.ToString());
            Check("十列不同 → AllEqual=false", !r.AllEqual);

            Eq("境界10 → 取第 10 列", PropsSigilCore.PickColumn(Cols(10, 10), 0, 10).Value, "100");
            Eq("境界5 → 取第 5 列", PropsSigilCore.PickColumn(Cols(10, 10), 0, 5).Value, "50");
            Eq("运行时 vd 也能定列（配置表缺）", PropsSigilCore.PickColumn(Cols(10, 10), 7, 0).Value, "70");
            Eq("配置表优先于运行时（两者不一致时取配置表）",
               PropsSigilCore.PickColumn(Cols(10, 10), 7, 2).Value, "20");
            Eq("十列全同 → AllEqual=true", PropsSigilCore.PickColumn(Same("3000"), 0, 1).AllEqual, true);
        }

        private static void PickNeverGuess()
        {
            // ★ 这条是本次修复的核心纪律：境界未知 + 十列不一致 → 不猜，交给调用方留占位符
            var r = PropsSigilCore.PickColumn(Cols(80, 1), 0, 0);
            Check("境界未知且十列不同 → Ambiguous（绝不猜）",
                  r.Kind == PropsSigilCore.PickKind.Ambiguous, r.Kind.ToString());
            Eq("Ambiguous 时 Value 必须是 null", r.Value, null);
            Eq("Ambiguous 时 Grade=0", r.Grade, 0);

            Check("境界越界（-3）等同未知 → Ambiguous",
                  PropsSigilCore.PickColumn(Cols(80, 1), -3, 0).Kind == PropsSigilCore.PickKind.Ambiguous);
            Check("境界越界（11）等同未知 → Ambiguous",
                  PropsSigilCore.PickColumn(Cols(80, 1), 0, 11).Kind == PropsSigilCore.PickKind.Ambiguous);

            // 境界未知但十列全同 → 无歧义，可以安全兜底（取第一列与取任何一列等价）
            var s = PropsSigilCore.PickColumn(Same("60000"), 0, 0);
            Check("境界未知但十列全同 → Ok（安全兜底）", s.Kind == PropsSigilCore.PickKind.Ok, s.Kind.ToString());
            Eq("十列全同兜底取第 1 列的值", s.Value, "60000");
            Eq("兜底时 Grade 记为 1", s.Grade, 1);
            Check("兜底时 AllEqual=true（日志据此说明）", s.AllEqual);

            Eq("null 行 → NoRow（不抛）", PropsSigilCore.PickColumn(null, 1, 1).Kind, PropsSigilCore.PickKind.NoRow);
            Eq("长度不足 10 → NoRow", PropsSigilCore.PickColumn(new[] { "1", "2" }, 1, 1).Kind, PropsSigilCore.PickKind.NoRow);
        }

        private static void PickZeroAndEmpty()
        {
            // "0" 是**合法数值**（DLC 表大量只填 value1、value2..10 全是 "0"），绝不能被当成"取不到"
            var r = PropsSigilCore.PickColumn(Same("0"), 0, 1);
            Eq("十列皆 \"0\" → Ok", r.Kind, PropsSigilCore.PickKind.Ok);
            Eq("十列皆 \"0\" → 值是 \"0\"（不是缺失）", r.Value, "0");
            Eq("十列皆 \"0\" 且境界未知 → 仍取到 \"0\"",
               PropsSigilCore.PickColumn(Same("0"), 0, 0).Value, "0");

            var mixed = Cols(10, 10);
            mixed[4] = "0";
            Eq("第 5 列写着 \"0\" → 取到 \"0\"（不能跳到别的列）",
               PropsSigilCore.PickColumn(mixed, 0, 5).Value, "0");

            var hole = Cols(10, 10);
            hole[2] = "";                       // 第 3 格空
            Eq("该列为空串 → NoRow（留占位符，不挪去别的列）",
               PropsSigilCore.PickColumn(hole, 0, 3).Kind, PropsSigilCore.PickKind.NoRow);
            Eq("整行为空 → NoRow",
               PropsSigilCore.PickColumn(new[] { "", "", "", "", "", "", "", "", "", "" }, 1, 1).Kind,
               PropsSigilCore.PickKind.NoRow);
            // 关键：**境界也未知**时整行为空同样必须 NoRow —— 否则会替换成一个空串（看着像"这行没数值"，
            // 实际是把占位符删了，属于静默改字）
            Eq("整行为空 + 境界未知 → 仍 NoRow（不许替换成空串）",
               PropsSigilCore.PickColumn(new[] { "", "", "", "", "", "", "", "", "", "" }, 0, 0).Kind,
               PropsSigilCore.PickKind.NoRow);
        }

        private static void PickRealRows()
        {
            // 真机数据：残·灵盾符（ItemPill.grade=1）介绍里的两条
            Eq("&608211_kcll 境界1 → 3000（再 ×0.01 = 30%）",
               PropsSigilCore.PickColumn(Same("3000"), 1, 1).Value, "3000");
            Eq("&608211_cxsj 境界1 → 60000（再 ×0.001 = 60秒）",
               PropsSigilCore.PickColumn(Same("60000"), 1, 1).Value, "60000");

            // 迅雷符：同一条介绍被 10 个品阶共用，靠列区分（残 80 … 道 5454）
            var qtsh = new[] { "80", "128", "204", "326", "521", "833", "1332", "2131", "3409", "5454" };
            Eq("&608203_qtsh 残(境界1) → 80", PropsSigilCore.PickColumn(qtsh, 0, 1).Value, "80");
            Eq("&608203_qtsh 真(境界2) → 128", PropsSigilCore.PickColumn(qtsh, 0, 2).Value, "128");
            Eq("&608203_qtsh 禁(境界8) → 2131", PropsSigilCore.PickColumn(qtsh, 0, 8).Value, "2131");
            Eq("&608203_qtsh 道(境界10) → 5454", PropsSigilCore.PickColumn(qtsh, 0, 10).Value, "5454");

            // 反例守卫：品质（ItemProps.level=2，残/真 共用）不是列 —— 若误用品质当列，
            // 残(境界1) 会算成 200（真·拘灵符的数），正确值是 125
            var shwl = new[] { "125", "200", "320", "512", "819", "1310", "2096", "3353", "5364", "8582" };
            Eq("&608213_shwl 残(境界1) → 125（不是 品质当列 的 200）",
               PropsSigilCore.PickColumn(shwl, 0, 1).Value, "125");
            Eq("&608213_shwl 品质2 对应的另一轴值确实存在（200）→ 说明必须按境界选",
               shwl[1], "200");

            // 装备类（无 ItemPill 行、境界拿不到）：十列全同 → 兜底安全
            Eq("&3011441_nlxh 境界未知但十列全同 → -200",
               PropsSigilCore.PickColumn(Same("-200"), 0, 0).Value, "-200");
        }

        // ------------------------------------------------------------ 扫描

        private static void ScanReplace()
        {
            // 真机技能说明原文（B4 那次修的模板）
            string tpl = "对前方范围&12111_range&以内的敌人造成威力&12111_dmg&的$s_dao$伤害。 装备此技能后，可吸血&12113_xxbl&%。";
            var vals = new Dictionary<string, string>
            {
                { "12111_range", "300" }, { "12111_dmg", "480" }, { "12113_xxbl", "7" },
            };
            var texts = new Dictionary<string, string> { { "s_dao", "刀法" } };
            var r = PropsSigilCore.Scan(tpl,
                k => vals.ContainsKey(k) ? vals[k] : null,
                k => texts.ContainsKey(k) ? texts[k] : k);
            Eq("技能说明：3 个数值 + 1 个文本全替换",
               r.Text, "对前方范围300以内的敌人造成威力480的刀法伤害。 装备此技能后，可吸血7%。");
            Eq("Replaced=4（三个 &数值& + 一个 $文本$）", r.Replaced, 4);
            Eq("Residual=0", r.Residual, 0);

            // 未命中（LS 返回原 key）= 不算替换成功
            var miss = PropsSigilCore.Scan("$s_unknown$", k => null, k => k);
            Eq("LS 未命中 → 原样保留", miss.Text, "$s_unknown$");
            Eq("LS 未命中 → 不计入 Replaced", miss.Replaced, 0);
            Eq("LS 未命中 → 计入 Residual", miss.Residual, 1);

            // 数值未命中（表里没有这条）
            var noval = PropsSigilCore.Scan("威力&99999_dmg&", k => null, k => k);
            Eq("数值未命中 → 原样保留", noval.Text, "威力&99999_dmg&");
            Eq("数值未命中 → Residual=1", noval.Residual, 1);

            // 算式（含 `|`）由回调决定怎么算 —— 扫描器只负责"把结果放进去"
            var math = PropsSigilCore.Scan("&22111_xzsh|x22111_dmg|/100|f0&",
                k => k.IndexOf('|') >= 0 ? "480" : null, k => k);
            Eq("算式交给 valueOf（本层不管算法）", math.Text, "480");

            // 回调抛异常 → 不传播，该记号原样保留
            var boom = PropsSigilCore.Scan("威力&1_dmg&。", k => { throw new Exception("boom"); }, k => k);
            Eq("回调抛异常 → 原样保留（不崩、不吞成空）", boom.Text, "威力&1_dmg&。");
            Eq("回调抛异常 → 计入 Residual", boom.Residual, 1);
        }

        private static void ScanRobust()
        {
            // 没有记号 → 原文返回，且**不调用回调**（避免无谓的 conf 查询与日志噪声）
            int calls = 0;
            var plain = PropsSigilCore.Scan("最为常见的疗伤丹药，使用后能够恢复少量的体力值。",
                k => { calls++; return "x"; }, k => { calls++; return "y"; });
            Eq("无记号 → 原文", plain.Text, "最为常见的疗伤丹药，使用后能够恢复少量的体力值。");
            Eq("无记号 → 不调用任何回调", calls, 0);
            Eq("无记号 → Replaced=0", plain.Replaced, 0);
            Eq("无记号 → Residual=0", plain.Residual, 0);

            // 未闭合 / 空记号：既不替换也不误报
            Eq("未闭合的 & 原样保留", PropsSigilCore.Scan("威力&12111_dmg", k => "480", k => k).Text, "威力&12111_dmg");
            Eq("未闭合 → Residual=0（不误报成占位符）",
               PropsSigilCore.Scan("威力&12111_dmg", k => "480", k => k).Residual, 0);
            Eq("空记号 && 原样保留", PropsSigilCore.Scan("a&&b", k => "X", k => k).Text, "a&&b");

            // 首尾相邻
            Eq("记号在首尾都能替换",
               PropsSigilCore.Scan("&a_b&中&c_d&", k => "*", k => k).Text, "*中*");
            Eq("相邻记号都能替换",
               PropsSigilCore.Scan("&a_b&&c_d&", k => "*", k => k).Text, "**");

            // null / 空
            Eq("null → 空串（不抛）", PropsSigilCore.Scan(null, k => "X", k => k).Text, "");
            Eq("空串 → 空串", PropsSigilCore.Scan("", k => "X", k => k).Text, "");
            Eq("回调为 null → 不替换、不崩", PropsSigilCore.Scan("&a_b&", null, null).Text, "&a_b&");

            // 残留计数两族都认（只数 `&` 会让没替换掉的 `$key$` 报告成"已干净"）
            Eq("CountSigils 认 $key$", PropsSigilCore.CountSigils("$a$"), 1);
            Eq("CountSigils 认 &expr&", PropsSigilCore.CountSigils("&a_b&"), 1);
            Eq("CountSigils 两族混排", PropsSigilCore.CountSigils("&a_b&$c$"), 2);
            Eq("Scan 未替换的 $key$ 计入 Residual", PropsSigilCore.Scan("$x$", k => null, k => k).Residual, 1);

            // 单趟：替换进去的文本不回扫（值都是数字；这条钉住行为，免得以后误加"递归替换"）
            Eq("替换结果里若含 & 不回扫",
               PropsSigilCore.Scan("&a_b&", k => "&x_y&", k => k).Text, "&x_y&");

            // 幂等：对已替换干净的文本再扫一次不变
            var once = PropsSigilCore.Scan("威力&1_dmg&", k => "480", k => k).Text;
            Eq("已替换结果再扫一次不变", PropsSigilCore.Scan(once, k => "999", k => k).Text, once);
        }
    }
}
