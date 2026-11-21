/// <summary>
/// 微信式**时间分隔行**的判档（纯逻辑：零 Unity、零游戏类型 ⇒ 可编成 net8.0 直接单测）。
///
/// ── 需求（用户 10-22）──────────────────────────────────────────────────────
/// 气泡里不再显示日期（沉浸感，见 `BubbleTextCore.StripLeadStamp`），但回放里"所有的对话
/// 没有时间区分"。参照微信：消息流中间插一行**居中灰色小字**（`ChatWindow.AppendDivider`
/// 现成件，"主动传音"分隔同款），且粒度按**游戏历**（年/月/日），格式随"距上次分隔行
/// 变了哪一档"动态收窄：
///
///   · 同年同月（只有日变了） → `6日`
///   · 同年不同月            → `3月6日`
///   · 跨年（"都不同"）      → `2年3月6日`
///   · 会话第一条            → 全档（没有"上次"可比）
///   · 同一天                → **不打**（微信也是变化才打；天天打等于没有）
///
/// ── 日期从哪来（为什么 NPC 行不驱动分隔行）────────────────────────────────
/// 账本里唯一持久化的游戏日期 = 玩家消息行首戳（`[N年M月D日] `，09-13 机制）。
/// NPC 回复/行动行账本里**没有**日期，Python 侧也没有可靠时钟源（游戏历在 C# 手里）。
/// 所以分隔行只由**玩家行**驱动：NPC 回复与触发它的那句玩家话几乎必然同一天（一个回合
/// 之内），分组观感正确。已知盲区：**NPC 跨天主动开口**（无玩家戳可锚）不打分隔行，
/// 直到玩家下一句才补上日期——实时与回放**同样不打**，观感一致（本仓铁律：宁可两处都缺，
/// 不可一处有一处无）。要补这个盲区得给账本加回合级日期元数据（协议改动），另案。
///
/// 无日退化形（`[N年M月]`，日历读不到日时 d=0）：同年同月一律不打；跨月只打到"月"档、
/// 跨年打到"年月"档——**绝不编造一个日**（与 `UnitSnapshot.CnDate` 同一条纪律）。
/// </summary>

namespace AgentLoopBridge
{
    internal static class TimeSepCore
    {
        /// <summary>上一条分隔行的日期是否有效（会话开头/重放重建后 = false）。</summary>
        /// <param name="hasPrev">已有上一条分隔行日期。</param>
        /// <param name="py">上次已展示的年月日（hasPrev=false 时忽略）。</param>
        /// <param name="y">本条玩家消息的日期（`TryParseLeadStamp` 的产出；日缺失时 d=0）。</param>
        /// <returns>要插入的分隔行文本；null = 不打（同一天 / 日未知且年月未变）。</returns>
        public static string Label(bool hasPrev, int py, int pm, int pd, int y, int m, int d)
        {
            if (!hasPrev) return Full(y, m, d);
            if (y == py && m == pm)
            {
                // 同月：只有"日确实变了"才打；任一日未知（退化形）→ 判不出变化，不打
                if (d == 0 || pd == 0 || d == pd) return null;
                return d + "日";
            }
            if (y == py) return m + "月" + (d > 0 ? d + "日" : "");   // 跨月不跨年
            return Full(y, m, d);                                     // 跨年：全档
        }

        private static string Full(int y, int m, int d)
        {
            string s = y + "年" + m + "月";
            if (d > 0) s += d + "日";
            return s;
        }
    }
}
