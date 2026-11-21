using System;

namespace AgentLoopBridge
{
    /// <summary>
    /// 物品/装备介绍里的占位符（`&amp;表达式&amp;`）与**取值列决策**——**零 Unity、零游戏类型**，
    /// 所以能被编成 net8.0 单测工程直接跑（同 `UI/TwoStepConfirm.cs`、`UnitIdRegistryCore.cs` 的做法）。
    ///
    /// 为什么要有这个文件（09-17）：
    ///   `inspect_unit` 的背包/装备介绍只做了 `GameTool.LS`，**没占位符解析** —— 原版「残·灵盾符」的
    ///   介绍就这样喂给模型：`吸收使用者当前&amp;608211_kcll&amp;%的灵力…护盾持续&amp;608211_cxsj&amp;秒。`
    ///   面板上它是 `30%` / `60秒`（`ConfBattleSkillValue`：`&amp;608211_kcll` = 3000 × `x0.01|f0`）。
    ///
    /// 取值表的结构（游戏自带 Excel 表头实证，`配置修改教程/配置表头/`）：
    ///   · `BattleSkillValue`：`value1..value10` = **境界1数值..境界10数值**；`valueScale` = 显示数值缩放；
    ///     `valueMon` = 怪物专用数值（物品悬浮窗用不到）。
    ///   · `ItemPill`：`grade` = **道具境界**（残·灵盾符..道·灵盾符 = 1..10）。
    ///   · `ItemProps.level` = **物品品质**（1..6 = 凡品..下品..仙品，见 `ItemLevelDesc`）——
    ///     它**不是**列索引：残(境界1) 与 真(境界2) 的品质都是 2，但 `&amp;608213_shwl&amp;` 对二者是 125 / 200，
    ///     拿品质当列会把这件装备的数值算成另一个品阶的。
    ///
    /// 铁律：**宁可留着占位符，也绝不猜一个数**。境界拿不到、且十列不一致时返回 `Ambiguous`，
    /// 由调用方原样保留占位符并留痕（错数字比占位符更有害）。
    /// </summary>
    internal static class PropsSigilCore
    {
        /// <summary>`ConfBattleSkillValue` 的列数（境界 1..10）。</summary>
        public const int GradeMax = 10;

        /// <summary>最小合法境界。</summary>
        public const int GradeMin = 1;

        /// <summary>选列结果类别。</summary>
        public enum PickKind
        {
            /// <summary>取到了确定的一列（`Value` 是**未缩放**原值，交给 `ApplyValueScale`）。</summary>
            Ok,
            /// <summary>表里没有这一行 / 该格为空 —— 属于"这个占位符不是本表管的"，不打 ★ 日志。</summary>
            NoRow,
            /// <summary>境界拿不到，且十列**不一致** —— 不许猜，留占位符 + ★ 留痕。</summary>
            Ambiguous,
        }

        /// <summary>一次选列的完整结论（供日志复述：用了哪一列、十列是否全同）。</summary>
        public struct Pick
        {
            public PickKind Kind;
            /// <summary>`Kind == Ok` 时的未缩放原值。</summary>
            public string Value;
            /// <summary>实际用的列号（1..10）；未取到时为 0。</summary>
            public int Grade;
            /// <summary>十列是否全同（全同时"境界未知"也不影响结果，可以安全兜底）。</summary>
            public bool AllEqual;
        }

        /// <summary>把任意整数收敛成合法境界；不合法返回 0（=未知）。</summary>
        public static int NormGrade(int grade)
        {
            return (grade >= GradeMin && grade <= GradeMax) ? grade : 0;
        }

        /// <summary>
        /// 两个境界来源取一个：配置表 `ItemPill.grade`（Excel 表头写明「道具境界」，可审计）优先，
        /// 拿不到才退回运行时 `BattleSkillValueData.grade`；两边都有效但**不一致**时也取配置表，
        /// 并由调用方打 `★境界不一致★` 留痕（真机一比就知道该信哪个，绝不静默）。
        /// </summary>
        public static int PickGrade(int vdGrade, int confGrade)
        {
            int c = NormGrade(confGrade);
            if (c > 0) return c;
            return NormGrade(vdGrade);
        }

        /// <summary>
        /// 十列取值决策。`values` 必须是 `value1..value10` 顺序（长度不足 10 视为没有这一行）。
        /// `vdGrade` = 运行时 `BattleSkillValueData.grade`；`confGrade` = 配置表 `grade`。
        /// </summary>
        public static Pick PickColumn(string[] values, int vdGrade, int confGrade)
        {
            var r = new Pick { Kind = PickKind.NoRow, Value = null, Grade = 0, AllEqual = false };
            if (values == null || values.Length < GradeMax) return r;

            string first = values[0];
            bool allEqual = true;
            for (int i = 1; i < GradeMax; i++)
            {
                if (!string.Equals(values[i], first, StringComparison.Ordinal)) { allEqual = false; break; }
            }
            r.AllEqual = allEqual;
            // 整行都是空的：本表没有可用数值（不是"值为 0"——`"0"` 是合法值，见下面的测试）
            if (string.IsNullOrEmpty(first)) return r;

            int g = PickGrade(vdGrade, confGrade);
            if (g > 0)
            {
                string v = values[g - 1];
                if (string.IsNullOrEmpty(v)) return r;       // 该格为空 → 当没有，留占位符
                r.Kind = PickKind.Ok;
                r.Grade = g;
                r.Value = v;
                return r;
            }

            // 境界未知：十列全同则无歧义（取第一列与取任何一列等价），否则**不猜**
            if (allEqual)
            {
                r.Kind = PickKind.Ok;
                r.Grade = GradeMin;
                r.Value = first;
                return r;
            }
            r.Kind = PickKind.Ambiguous;
            return r;
        }

        /// <summary>占位符扫描结果。</summary>
        public struct ScanResult
        {
            /// <summary>替换后的文本（未替换的记号**原样保留**）。</summary>
            public string Text;
            /// <summary>成功替换的记号数。</summary>
            public int Replaced;
            /// <summary>替换后仍残留的记号数（`&amp;` / `$` 成对算一个）。</summary>
            public int Residual;
        }

        /// <summary>
        /// 占位符扫描（技能说明与物品/装备介绍**共用这一段**）：
        ///   · `$key$` → `textOf(key)`（本地化引用；返回原 key 视为未命中）；
        ///   · `&amp;expr&amp;` → `valueOf(expr)`（数值；含 `|` 的算式由调用方的 `valueOf` 决定怎么算）。
        /// 两个回调都返回 null/空 = 未替换，该记号**原样保留**（不猜、不删），由调用方统计残留并留痕。
        /// </summary>
        public static ScanResult Scan(string s, Func<string, string> valueOf, Func<string, string> textOf)
        {
            var res = new ScanResult { Text = s ?? "", Replaced = 0, Residual = 0 };
            if (string.IsNullOrEmpty(s)) return res;
            if (s.IndexOf('&') < 0 && s.IndexOf('$') < 0) return res;

            var o = new System.Text.StringBuilder(s.Length + 32);
            int i = 0;
            while (i < s.Length)
            {
                char c = s[i];
                if (c == '&' || c == '$')
                {
                    int close = s.IndexOf(c, i + 1);
                    if (close > i + 1)
                    {
                        string inner = s.Substring(i + 1, close - i - 1);
                        string rep = null;
                        if (c == '$')
                        {
                            if (textOf != null) { try { rep = textOf(inner); } catch { } }
                            // LS 未命中通常原样返回 key —— 那不算替换成功
                            if (!string.IsNullOrEmpty(rep) && rep == inner) rep = null;
                        }
                        else if (valueOf != null)
                        {
                            try { rep = valueOf(inner); } catch { }
                        }
                        if (!string.IsNullOrEmpty(rep)) { o.Append(rep); res.Replaced++; i = close + 1; continue; }
                    }
                }
                o.Append(c);
                i++;
            }
            res.Text = o.ToString();
            res.Residual = CountSigils(res.Text);
            return res;
        }

        /// <summary>残留占位符计数（`&amp;`/`$` 成对算一个）—— 只用于"替换干净没有"的留痕。</summary>
        public static int CountSigils(string s)
        {
            if (string.IsNullOrEmpty(s)) return 0;
            int n = 0;
            for (int i = 0; i < s.Length; i++)
            {
                if (s[i] != '&' && s[i] != '$') continue;
                int close = s.IndexOf(s[i], i + 1);
                if (close > i) { n++; i = close; }
            }
            return n;
        }
    }
}
