/// <summary>
/// DungeonDramaCore —— 「副本内剧情写进 NPC 经历」的**纯格式化逻辑**（零 Unity 依赖，
/// 故单元测试工程直接编同一份源码）。
///
/// ## 为什么要有它
/// 组队下副本打完会写一条经历（`与{0}同闯秘境，斩杀{1}。`）。但副本里常常**先演一段剧情**
/// （原生剧情窗，带选项）——那才是这场副本里"发生了什么"的关键。经历只写击杀，
/// 队友好感/后续对话就只能从"杀了几只怪"里推，等于没有记忆。
///
/// 本类只做一件事：把**捕获到的页/选项**排成给人看（也给 agent 读）的短句列表。
/// 采集、贴进引擎日志条目、只写给 NPC —— 都在 `DungeonDramaCapture` / `TeamBattleFollowup`。
///
/// ## 三条口径（都是刻意的）
///   ① **页** = `谁：「原话」`，原话一字不改（与"屏幕上的就是权威"同一条哲学）；
///   ② **选项** = `你选择了「…」` —— 玩家选什么是模型最需要知道的（谁知道问题、不知道回答，
///      就会把已完成的交互当成还没开始）；
///   ③ **判不出就闭嘴**：说话人/名字取不到时写「有人」，绝不猜成某个具体的人。
///
/// ## 截断（防把经历撑爆）
/// 一次副本的剧情可能几十页，而经历面板是给人扫一眼的。所以有上限，且**超限保留最近的那些**
/// （剧情通常"铺垫在前、抉择与结果在后"，玩家选了什么一般在尾部）。丢掉多少条会进日志，
/// 不静默。
/// </summary>
using System;
using System.Collections.Generic;

namespace AgentLoopBridge
{
    /// <summary>捕获到的一行（页 / 选项）。</summary>
    internal sealed class DungeonDramaLine
    {
        /// <summary>"page" = 页面正文；"option" = 玩家点的选项。</summary>
        public string Kind = "page";
        /// <summary>"npc" / "player" / "choice" / ""（判不出）。</summary>
        public string Speaker = "";
        /// <summary>说话人显示名（判不出为空串）。</summary>
        public string Name = "";
        /// <summary>屏幕上的成品话，一字不改。</summary>
        public string Text = "";
        public int DramaId;
        public float At;
    }

    internal static class DungeonDramaCore
    {
        /// <summary>最多写几行（超出的丢最早的，见类头"截断"）。</summary>
        internal const int MaxLines = 16;
        /// <summary>单行最多多少字（原话超长时截断并加省略号）。</summary>
        internal const int MaxTextChars = 70;
        /// <summary>整段最多多少字（再长面板读起来就是一坨）。</summary>
        internal const int MaxTotalChars = 1000;
        /// <summary>缓冲区硬上限（长副本也不许无界增长）。超过就丢最早的 —— 只影响日志，不影响战斗。</summary>
        internal const int HardLimit = 400;

        /// <summary>
        /// 记一行。**连续重复直接丢**：同一页可能因为窗口重建/重复 `InitData` 被上报两次，
        /// 同一枚选项按钮也可能因为重复挂监听被上报两次（`DramaAiOption` 里已有同文本去抖，
        /// 这里再兜一层，两道都不许省）。
        /// </summary>
        internal static void Add(List<DungeonDramaLine> buf, string kind, string speaker, string name,
                                 string text, int dramaId, float at)
        {
            if (buf == null) return;
            if (string.IsNullOrEmpty(text)) return;
            string t = text.Trim();
            if (t.Length == 0) return;

            string k = string.IsNullOrEmpty(kind) ? "page" : kind;
            string sp = speaker ?? "";
            if (buf.Count > 0)
            {
                DungeonDramaLine last = buf[buf.Count - 1];
                if (last.Kind == k && last.Speaker == sp && last.Text == t) return;
            }

            DungeonDramaLine l = new DungeonDramaLine();
            l.Kind = k;
            l.Speaker = sp;
            l.Name = name ?? "";
            l.Text = t;
            l.DramaId = dramaId;
            l.At = at;
            buf.Add(l);

            while (buf.Count > HardLimit) buf.RemoveAt(0);
        }

        /// <summary>
        /// 缓冲区 → 给人看的短句（**按发生顺序**）。超上限时保留最近的 `MaxLines` 条。
        /// 返回空列表 = 没什么可写（调用方据此完全不碰经历）。
        /// </summary>
        internal static List<string> Format(List<DungeonDramaLine> buf, string playerName)
        {
            List<string> outp = new List<string>();
            if (buf == null || buf.Count == 0) return outp;

            int from = buf.Count > MaxLines ? buf.Count - MaxLines : 0;
            int total = 0;
            for (int i = from; i < buf.Count; i++)
            {
                string s = One(buf[i], playerName);
                if (s.Length == 0) continue;
                if (total + s.Length > MaxTotalChars) break;
                outp.Add(s);
                total += s.Length;
            }
            return outp;
        }

        /// <summary>丢掉多少条（只用于日志，让"被截断"这件事不静默）。</summary>
        internal static int DroppedCount(List<DungeonDramaLine> buf)
        {
            if (buf == null || buf.Count <= MaxLines) return 0;
            return buf.Count - MaxLines;
        }

        /// <summary>单行渲染。`Kind=option` ⇒ `你选择了「…」`；页 ⇒ `谁：「…」`。</summary>
        internal static string One(DungeonDramaLine l, string playerName)
        {
            if (l == null) return "";
            string t = Clip(l.Text, MaxTextChars);
            if (string.IsNullOrEmpty(t)) return "";
            if (l.Kind == "option") return "你选择了「" + t + "」";

            string who = l.Name ?? "";
            if (who.Length == 0)
            {
                if (l.Speaker == "player") who = string.IsNullOrEmpty(playerName) ? "你" : playerName;
                else who = "有人";       // 判不出就闭嘴：不许猜成某个具体的人
            }
            return who + "：「" + t + "」";
        }

        /// <summary>截断（不切半个代理对；截了才加省略号）。</summary>
        internal static string Clip(string s, int max)
        {
            if (string.IsNullOrEmpty(s)) return "";
            if (max <= 0 || s.Length <= max) return s;
            int cut = max;
            if (cut > 0 && char.IsHighSurrogate(s[cut - 1])) cut--;
            if (cut <= 0) return "…";
            return s.Substring(0, cut) + "…";
        }
    }
}
