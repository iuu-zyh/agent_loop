/// <summary>
/// TeamDescService —— 组队气运的**动态描述**（运行时改文案表）。
///
/// 【要解决的问题】
///   配置表里的气运描述是**静态文本**，但组队状态要说得具体：
///     · NPC 身上  =「正与「唐炎」结伴同行」
///     · 玩家身上  =「正与「江宇文」「张票」结伴同行」
///   而且玩家侧随每次邀请/解散都要变 —— 静态配置表做不到。
///
/// 【为什么能改】反射实证（10-17）：
///     ConfLocalText  { Dictionary&lt;String, ConfLocalTextItem&gt; allText; String GetText(String key) }
///     ConfLocalTextItem { String key, ch, tc, en, kr; }
///     GameTool.LS(String) 是 **静态方法**，读的就是 `g.conf.localText.allText[key].ch`
///   ⇒ 运行时把 `allText[<key>].ch` 换掉，`GameTool.LS` 立刻返回新文案。
///   `allText` 是可写字典（不是只读视图），这是本方案成立的唯一前提。
///
/// 【为什么不会串味】
///   改的是**共享**的文案 key，所以所有同类气运同时变。这在本设计里是**正确的**：
///     · NPC 侧：所有队友的描述都只依赖"玩家叫什么"，而玩家只有一个 ⇒ 大家一样是应该的；
///     · 玩家侧：玩家自己只有一个 ⇒ 只有一条描述，不存在"两个玩家看到不同文案"。
///   反过来说：**不要**用它表达"张三看到李四不同"这类 per-viewer 差异 —— 那做不到。
///
/// 【纯展示，不进判定】
///   任何 `feature_*` / `npcConditionExamine_*` 判定读的都是**气运 ID**，与文案无关。
///   所以文案刷新失败不影响组队功能本身（失败只降级成旧文案，不阻断流程）。
/// </summary>
using System;
using System.Collections.Generic;
using System.Text;

namespace AgentLoopBridge
{
    internal static class TeamDescService
    {
        // 文案 key（与 LocalText 表里的一致）
        internal const string KeyNamePlayer   = "team_feature_name_54";   // 玩家：组队中
        internal const string KeyTipsPlayer   = "team_feature_tips_54";   // 玩家：正与…同行
        internal const string KeyNameMate     = "team_feature_name_51";   // NPC：结伴而行
        internal const string KeyTipsMate     = "team_feature_tips_51";   // NPC：正与…同行
        internal const string KeyNameHurt     = "team_feature_name_52";   // NPC：内外重伤
        internal const string KeyTipsHurt     = "team_feature_tips_52";
        internal const string KeyDramaHurt    = "agentloop_team_hurt_190";

        /// <summary>名字列表的书写风格：「甲」「乙」</summary>
        private static string Quote(string name)
        {
            if (string.IsNullOrEmpty(name)) return "";
            return "「" + name + "」";
        }

        /// <summary>
        /// 刷新**玩家**身上那条组队气运的描述（队友名单来自队友气运扫描）。
        ///
        /// 每次 invite / dismiss 成功后都必须调一次 —— 这是"描述跟着关系走"的唯一入口。
        /// </summary>
        /// <param name="mateNames">当前全部队友的名字（空 = 没有队友）</param>
        internal static bool RefreshPlayerDesc(List<string> mateNames)
        {
            string playerName = "";
            try { playerName = g.world?.playerUnit?.data?.unitData?.propertyData?.GetName() ?? ""; } catch { }
            if (string.IsNullOrEmpty(playerName)) playerName = "玩家";

            if (mateNames == null || mateNames.Count == 0)
            {
                // 没队友：不该还挂着"组队中"。清成中性文案（气运本身由调用方摘掉）
                return SetText(KeyTipsPlayer, "尚未与任何人结伴")
                    && SetText(KeyNamePlayer, "未组队");
            }

            var sb = new StringBuilder();
            for (int i = 0; i < mateNames.Count; i++)
            {
                if (i > 0) sb.Append("、");
                sb.Append(Quote(mateNames[i]));
            }
            string who = sb.ToString();

            string tips = "正与 " + who + " 结伴同行";
            string name = mateNames.Count > 1 ? ("组队中（" + mateNames.Count + " 人）") : "组队中";

            bool ok = SetText(KeyTipsPlayer, tips);
            ok &= SetText(KeyNamePlayer, name);
            return ok;
        }

        /// <summary>
        /// 刷新**NPC** 身上那条组队气运的描述（写的是"与谁组队" = 玩家名）。
        ///
        /// 所有 NPC 共用同一个 key，所以只需要写一次。
        /// 但如果队友不止一个，NPC 侧只关心"我和谁在一起" —— 那是玩家，不是其他队友。
        /// </summary>
        internal static bool RefreshMateDesc(string playerName)
        {
            if (string.IsNullOrEmpty(playerName)) playerName = "玩家";
            string tips = "正与 " + Quote(playerName) + " 结伴同行";
            return SetText(KeyTipsMate, tips) && SetText(KeyNameMate, "结伴而行");
        }

        /// <summary>
        /// 一次性把两边都刷新（invite / dismiss 之后调这个就够）。
        /// </summary>
        internal static void RefreshAll(List<string> mateNames, string playerName)
        {
            RefreshMateDesc(playerName);
            RefreshPlayerDesc(mateNames);
        }

        // ══════════════════════════════════════════════════════════
        //  文案写入
        // ══════════════════════════════════════════════════════════

        /// <summary>
        /// 把某个 LocalText key 的中文换掉。
        ///
        /// 四种语言都写（ch/tc/en/kr 同值）：气运面板会按当前语言取字段，
        /// 只改 `ch` 的话，切到繁体/英文就露出旧文案 —— 表现为"只有简中是对的"。
        ///
        /// 返回 false 只表示"没写成功"，调用方**不应**因此失败：这是展示层，
        /// 组队功能本身靠气运 ID，不依赖文案。
        /// </summary>
        internal static bool SetText(string key, string text)
        {
            if (string.IsNullOrEmpty(key)) return false;
            if (text == null) text = "";
            try
            {
                var conf = g.conf?.localText;
                if (conf == null) return false;
                var dict = conf.allText;
                if (dict == null) return false;

                ConfLocalTextItem item = null;
                try { dict.TryGetValue(key, out item); } catch { }
                if (item == null) return false;      // 表里没这个 key（导出漏了）→ 不改，如实失败

                item.ch = text;
                item.tc = text;
                item.en = text;
                item.kr = text;
                return true;
            }
            catch { return false; }
        }

        /// <summary>读当前中文文案（自检/诊断用；读不到返回 null）。</summary>
        internal static string GetText(string key)
        {
            try
            {
                var dict = g.conf?.localText?.allText;
                if (dict == null) return null;
                ConfLocalTextItem item = null;
                dict.TryGetValue(key, out item);
                return item?.ch;
            }
            catch { return null; }
        }

        /// <summary>
        /// 检查文案表里我们需要的 key 是否都在（导出完整性自检）。
        /// 缺 key = LocalText.xlsx 没导出成功，此时 `SetText` 会静默失败，
        /// 表现为"气运描述永远是配置表里那句静态话"。启动时记一行日志比事后猜快。
        /// </summary>
        internal static List<string> MissingKeys()
        {
            var miss = new List<string>();
            foreach (var k in new[]
            {
                KeyNamePlayer, KeyTipsPlayer, KeyNameMate, KeyTipsMate,
                KeyNameHurt, KeyTipsHurt, KeyDramaHurt,
            })
            {
                if (string.IsNullOrEmpty(GetText(k))) miss.Add(k);
            }
            return miss;
        }
    }
}
