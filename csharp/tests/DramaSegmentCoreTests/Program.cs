// 剧情相遇分段器（DramaSegmentCore）单元测试。
//
// 为什么要有这个工程：分段判据一旦写错，表现**全是静默的** —— 话被并进别人的链、战后那两句被
// 切成新的一段、反复点「对话」把同一段投三遍。实机上只看到"NPC 说话怪怪的"，没有异常、没有报错。
// 所以判据表必须由测试锁住，不能靠每次进游戏打一场来验。
//
// 十九组：
//   ① 跨战斗全链（用 09-22 真机探针的实测数字）：开段 → 关窗+B1 同刻起跳 ⇒ 挂起 →
//      转场在飞时静默 15s 不收 → 同人新页 ⇒ 回程 → 尾页关窗（B1 同刻**翻假**，不是起跳）
//      ⇒ 干净关窗 ⇒ 可投；投递按顺序、再投为空；
//   ② 干净关窗 + 待定窗内来的页 ⇒ 那些页开**新段**（"结束之后再开口 = 两件事"）；
//   ③ 游标：连投只给增量、投完不清空；
//   ④ 换人：旧段收、新段开，两边各投各的；
//   ⑤ 挂起：转场在飞时静默多久都不收；转场全落假后满 20s 无页才收；
//      **链还在飞 ⇒ 预算滑动**（长战斗不被切段）；硬上限（不可续期）兜僵尸；
//   ⑥ 空对方 / 我方私有 id：不记；
//   ⑦ 点击不参与任何判定（只计数）；
//   ⑧ 可投段上限 4（丢最老）；换世界立刻全丢；
//   ⑨ 工具链的段（我方动作发起）照记但不投；
//   ⑩ 「不投」标记**跟着页走**：被扣住、ε 到点才开成的段照样继承（打在活动段上会漏）；
//   ⑪ 取的是**时间上最近**的那一段（不与上一次相遇串味）；
//   ⑫ 点击时重判的归属写回（只改当前页那一条；判不出不覆盖）；
//   ⑬ 混合段（处置页不投 + 原生尾页该投）按**行**过滤；
//   ⑭ 分层回归（09-23 事故）：闲聊（A 灭收口）后 0.5s 的同人新页必须**另开一段** —— 不许按"时间近"合并；
//   ⑮ 结构判据（主）：关窗时动作 UI（ActionCourseInfo）亮 ⇒ 判交接（挂起 → 回程），不靠时间窗；
//   ⑯ 场景类交互（邀约）页间隙（09-23 实测）：**不设时间窗**的结构判据 —— 关窗那刻 B1+B2 亮
//      ⇒ 直接挂起；同人新页到达（仍处场景态，多长间隙都算）⇒ 回程同段；散场后（场景态解除）的同人页
//      ⇒ 不回程、另开一段；单 B1 亮不算场景态。另：`A↑`（关窗后才起的动作 UI）也算交接证据；
//   ⑰ 选项行进段（09-23）：玩家点的选项记成段里一行（`Kind=option`/`Speaker=choice`），
//      插在当前页之后、**永不静态"不投"**（选项必须投）、只认同人活动段（没有段不凭空造）。
//   ⑱ 区间读数（09-23）：工具结果读段的 `[from,until]`（跨段归并、按时序），
//      **不碰投递游标**；`consume=true` 读走即标消费（之后点「对话」不重投）。
//   ⑲ 链心跳（09-23）：`RenewChain` 让挂起预算滑动（长战斗/长论道不被切段）；
//      心跳停了现行规则立刻接管；一直跳也活不过不可续期的硬上限。
//
// 诚实边界：真机上"页事件与 W 边沿同刻""关窗与 B1 起跳同刻"是探针实测结论；这里用同一组数字
// 复现。接线（Harmony 挂点、探针采样）在 DramaSegmentRecorder 里，由真机日志与源码契约测试管。

using System;
using System.Collections.Generic;
using AgentLoopBridge;

namespace AgentLoopBridge.Tests
{
    internal static class Program
    {
        // 与 ModIds.Mod 同值（这里写死可避免把 ModIds.cs 也编进测试工程；M2 接线时才用真常量）
        private const int PrivateMin = -803158451;
        private const int PrivateMax = PrivateMin + 999;

        private static int _pass;
        private static readonly List<string> _fails = new List<string>();
        private static readonly List<string> _logs = new List<string>();

        private static void Check(string name, bool ok, string detail = "")
        {
            if (ok) { _pass++; return; }
            _fails.Add(name + (detail.Length > 0 ? "  ｜ " + detail : ""));
            Console.WriteLine("  ✗ " + name + (detail.Length > 0 ? "  ｜ " + detail : ""));
        }

        private static void Eq<T>(string name, T got, T want)
        {
            Check(name, EqualityComparer<T>.Default.Equals(got, want),
                  "实际=" + Show(got) + " 期望=" + Show(want));
        }

        private static string Show<T>(T v)
        {
            if (v == null) return "<null>";
            string s = v.ToString();
            return s.Length == 0 ? "「空」" : s;
        }

        private static DramaSegmentCore NewCore()
        {
            _logs.Clear();
            return new DramaSegmentCore(PrivateMin, PrivateMax, s => _logs.Add(s));
        }

        /// <summary>基线：喂两帧 W=真（第一帧只做基线，第二帧起才判边沿）。</summary>
        private static void Setup(DramaSegmentCore c, float t)
        {
            c.Tick(true, false, false, t);
            c.Tick(true, false, false, t + 0.25f);
        }

        /// <summary>关窗那一帧同时 B1 起跳（实测：同一毫秒）⇒ ε 到点判交接 ⇒ 挂起。</summary>
        private static void CloseWithHandoff(DramaSegmentCore c, float closeAt)
        {
            c.Tick(false, true, false, closeAt);
            c.Tick(false, true, false, closeAt + 1f);
        }

        /// <summary>关窗那一帧没有任何起跳 ⇒ ε 到点判干净关窗 ⇒ 可投。</summary>
        private static void CloseClean(DramaSegmentCore c, float closeAt)
        {
            c.Tick(false, false, false, closeAt);
            c.Tick(false, false, false, closeAt + 1f);
        }

        /// <summary>段内容压成一行，便于断言：`1:npc:原话 | 2:player:原话`。</summary>
        private static string Rows(DramaSegment seg)
        {
            var sb = new System.Text.StringBuilder();
            for (int i = 0; i < seg.Lines.Count; i++)
            {
                if (sb.Length > 0) sb.Append(" | ");
                SegmentLine l = seg.Lines[i];
                sb.Append(l.Seq).Append(':').Append(l.Speaker.Length == 0 ? "(判不出)" : l.Speaker)
                  .Append(':').Append(l.Text);
            }
            return sb.ToString();
        }

        private static int Main()
        {
            try { Console.OutputEncoding = System.Text.Encoding.UTF8; } catch { }

            // ---------- ① 跨战斗全链（实测数字：关窗=B1 起跳同刻；回程页 15s 后到） ----------
            Console.WriteLine("① 跨战斗全链：开段 / 挂起 / 回程 / 干净关窗 / 可投 / 游标");
            {
                DramaSegmentCore c = NewCore();
                Setup(c, 0f);
                c.OnPage("LCEQ53", 21204, "npc", "方成和", "我见你修为高深，能否与我切磋一番？", 0.5f);
                Eq("① 开段：状态=记录中", c.Active.State, SegmentState.Recording);
                Eq("① 开段：1 条", c.Active.Lines.Count, 1);
                Eq("① 对方=精确编号而非名字", c.Active.Counterpart, "LCEQ53");

                c.NoteOptionClick(2.5f);
                CloseWithHandoff(c, 2.5f);
                Eq("① 关窗+B1 同刻起跳 ⇒ 挂起", c.Active.State, SegmentState.Suspended);
                Eq("① 挂起：一条不删", c.Active.Lines.Count, 1);

                c.Tick(false, true, false, 10f);
                c.Tick(false, true, false, 17.5f);
                Eq("① 转场在飞时静默 15s 不收", c.Active.State, SegmentState.Suspended);

                c.Tick(true, true, false, 17.5f);   // 战后那扇窗重建：W 假→真（不是交接信号）
                c.OnPage("LCEQ53", 21401, "player", "唐炎", "厉害，我输了。", 17.5f);
                Eq("① 同人新页 ⇒ 回程（回记录中）", c.Active.State, SegmentState.Recording);
                Eq("① 回程：2 条", c.Active.Lines.Count, 2);
                Eq("① 第 2 条说话人=player", c.Active.Lines[1].Speaker, "player");

                c.OnPage("LCEQ53", 21402, "npc", "方成和", "看来这次是我赢了！", 18.25f);
                Eq("① 第 3 条入段", c.Active.Lines.Count, 3);

                c.NoteOptionClick(19.75f);
                CloseClean(c, 19.75f);              // 尾页：关窗 + B1 同刻**翻假**（不是起跳）
                Check("① 尾页干净关窗 ⇒ 收口（无活动段）", c.Active == null);
                Eq("① 可投段：1 段", c.Deliverables.Count, 1);
                Eq("① 可投段：3 条", c.Deliverables[0].Lines.Count, 3);
                Check("① 收口理由=干净关窗", c.Deliverables[0].SettleReason.Contains("干净关窗"),
                      c.Deliverables[0].SettleReason);
                Eq("① 原话顺序与说话人（一字不改）",
                   Rows(c.Deliverables[0]),
                   "1:npc:我见你修为高深，能否与我切磋一番？ | 2:player:厉害，我输了。 | 3:npc:看来这次是我赢了！");
                Check("① 关窗理由写明了靠哪条判据", _logs.Count > 0);

                List<SegmentLine> d = c.TakeDelta("LCEQ53", 21f);
                Eq("① 投递：3 条", d.Count, 3);
                Eq("① 投递：第 1 条 seq", d[0].Seq, 1);
                Eq("① 投递：第 3 条 seq", d[2].Seq, 3);
                Eq("① 投递：第 3 条原文", d[2].Text, "看来这次是我赢了！");
                Eq("① 再投一次=0 条（游标到底）", c.TakeDelta("LCEQ53", 21.5f).Count, 0);

                c.OnPage("LCEQ53", 22604, "player", "唐炎", "另一件事", 23f);
                Check("① 结束之后再开口 ⇒ 新段（不并）",
                      c.Active != null && c.Active.Counterpart == "LCEQ53" && c.Active.Lines.Count == 1);
                Eq("① 旧可投段不吞（仍在）", c.Deliverables.Count, 1);
                List<SegmentLine> d2 = c.TakeDelta("LCEQ53", 24f);
                Eq("① 点对话只投当前那一段", d2.Count, 1);
                Eq("① 当前段内容", d2[0].Text, "另一件事");
            }

            // ---------- ② 干净关窗 + 待定窗内来的页 ⇒ 开新段 ----------
            Console.WriteLine("② 干净关窗 + 待定窗内的页 ⇒ 旧段只留原文收口，那些页开新段");
            {
                DramaSegmentCore c = NewCore();
                Setup(c, 0f);
                c.OnPage("A1", 30001, "npc", "甲", "第一件事", 0.5f);
                c.Tick(false, false, false, 2.5f);        // 关窗（无起跳）⇒ 进入待定
                Eq("② 待定期间旧段仍是记录中", c.Active.State, SegmentState.Recording);
                c.Tick(true, false, false, 2.9f);         // 新窗开了：W 假→真（**不算交接**）
                c.OnPage("A1", 30002, "npc", "甲", "第二件事·上", 2.9f);
                c.OnPage("A1", 30003, "npc", "甲", "第二件事·下", 3f);
                Eq("② 待定期间扣住的页不写进旧段", c.Active.Lines.Count, 1);
                c.Tick(true, false, false, 3.5f);         // ε 到点：无起跳 ⇒ 干净关窗
                Eq("② 旧段收口成 1 条", c.Deliverables.Count, 1);
                Eq("② 旧段内容只有第一件", c.Deliverables[0].Lines.Count, 1);
                Check("② 扣住的页开成新段", c.Active != null && c.Active.Lines.Count == 2);
                Eq("② 新段内容按顺序", Rows(c.Active), "1:npc:第二件事·上 | 2:npc:第二件事·下");
            }
            {
                // ②b 已投过（"结束之后再开口 = 两件事"）：先投掉上一段 ⇒ 新页开新段、旧段不吞
                DramaSegmentCore c = NewCore();
                Setup(c, 0f);
                c.OnPage("A1", 30001, "npc", "甲", "第一件事", 0.5f);
                c.Tick(false, false, false, 2.5f);
                c.Tick(false, false, false, 3.5f);        // 干净关窗 ⇒ 可投
                Eq("② 先投掉那一段", c.TakeDelta("A1", 3.6f).Count, 1);
                c.Tick(true, false, false, 3.7f);
                c.OnPage("A1", 30002, "npc", "甲", "第二件事", 3.8f);
                Check("② 已投过 ⇒ 新页开新段（不并回）",
                      c.Active != null && c.Active.Lines.Count == 1);
                Eq("② 旧段仍在（不吞）", c.Deliverables.Count, 1);
                Eq("② 旧段内容不变", c.Deliverables[0].Lines.Count, 1);
            }

            // ---------- ③ 游标 ----------
            Console.WriteLine("③ 游标：只投增量、投完不清空");
            {
                DramaSegmentCore c = NewCore();
                Setup(c, 0f);
                c.OnPage("B1", 1, "npc", "乙", "a", 0.5f);
                c.OnPage("B1", 2, "npc", "乙", "b", 1f);
                c.OnPage("B1", 3, "npc", "乙", "c", 1.5f);
                Eq("③ 连投 3 条", c.TakeDelta("B1", 2f).Count, 3);
                Eq("③ 立刻再投=0 条", c.TakeDelta("B1", 2.1f).Count, 0);
                c.OnPage("B1", 4, "npc", "乙", "d", 2.5f);
                List<SegmentLine> d = c.TakeDelta("B1", 3f);
                Eq("③ 只补新的 1 条", d.Count, 1);
                Eq("③ 补的那条 seq=4", d[0].Seq, 4);
                Check("③ 内容没有被清空", c.Active.Lines.Count == 4);
            }

            // ---------- ④ 换人 ----------
            Console.WriteLine("④ 换人：旧段收、新段开，各投各的");
            {
                DramaSegmentCore c = NewCore();
                Setup(c, 0f);
                c.OnPage("A1", 1, "npc", "甲", "甲说的话", 0.5f);
                c.OnPage("B2", 2, "npc", "乙", "乙说的话", 1f);
                Eq("④ 旧段收口", c.Deliverables.Count, 1);
                Eq("④ 旧段是 A1", c.Deliverables[0].Counterpart, "A1");
                Check("④ 新段是 B2", c.Active != null && c.Active.Counterpart == "B2");
                Eq("④ 旧人仍能投", c.TakeDelta("A1", 2f).Count, 1);
                Eq("④ 新人能投", c.TakeDelta("B2", 2f).Count, 1);
            }

            // ---------- ⑤ 挂起：什么时候不收 / 什么时候收 ----------
            Console.WriteLine("⑤ 挂起：转场在飞时不收；转场落假后 20s 无页才收；超预算收");
            {
                DramaSegmentCore c = NewCore();
                Setup(c, 0f);
                c.OnPage("A1", 1, "npc", "甲", "约战", 0.5f);
                CloseWithHandoff(c, 2.5f);                 // SuspendedAt=3.5f
                c.Tick(false, true, false, 60f);
                Eq("⑤ 转场在飞、静默 56s 不收", c.Active.State, SegmentState.Suspended);
                c.Tick(false, false, false, 61f);          // 转场全落假 ⇒ 起回程窗
                c.Tick(false, false, false, 80.5f);        // 19.5s < 20
                Eq("⑤ 回程窗未满不收", c.Active.State, SegmentState.Suspended);
                c.Tick(false, false, false, 81.25f);       // 20.25s ≥ 20
                Check("⑤ 回程窗满 ⇒ 可投", c.Active == null && c.Deliverables.Count == 1);
                Check("⑤ 收口理由=回程无新页", c.Deliverables[0].SettleReason.Contains("无新页"),
                      c.Deliverables[0].SettleReason);
            }
            {
                // 09-23 改：挂起预算**随链滑动**（转场还亮 = 链还在飞 ⇒ 不许掐），
                // 防僵尸改由**不可续期的硬上限**承担（见 ⑲）。
                DramaSegmentCore c = NewCore();
                Setup(c, 0f);
                c.OnPage("A1", 1, "npc", "甲", "约战", 0.5f);
                CloseWithHandoff(c, 2.5f);                 // SuspendedAt=3.5f
                c.Tick(false, true, false, 183f);          // 转场一直亮 ⇒ 预算滑动
                Eq("⑤ 链在飞：179.5s 不收", c.Active.State, SegmentState.Suspended);
                c.Tick(false, true, false, 184f);
                Eq("⑤ 链在飞：超 180s 仍不收（长战斗不被切段）", c.Active.State, SegmentState.Suspended);
                Check("⑤ 收口前一条不少", c.Active.Lines.Count == 1);
                c.Tick(false, true, false, 1804f);         // 探针一直亮到硬上限
                Check("⑤ 硬上限到点 ⇒ 无条件收口",
                      c.Active == null && c.Deliverables.Count == 1 && c.Deliverables[0].Lines.Count == 1);
                Check("⑤ 收口理由=硬上限", c.Deliverables[0].SettleReason.Contains("硬上限"),
                      c.Deliverables[0].SettleReason);
            }

            // ---------- ⑥ 空对方 / 我方私有 id ----------
            Console.WriteLine("⑥ 空对方与我方私有 id：一律不记");
            {
                DramaSegmentCore c = NewCore();
                Setup(c, 0f);
                c.OnPage("", 1, "npc", "?", "无对方的系统页", 0.5f);
                Check("⑥ 空对方不记", c.Active == null);
                c.OnPage("A1", PrivateMin, "npc", "甲", "我方确认窗", 1f);
                c.OnPage("A1", PrivateMax, "npc", "甲", "我方确认窗 2", 1.25f);
                Check("⑥ 我方私有 id 不记", c.Active == null);
                Check("⑥ 私有段边界（含端点）",
                      c.IsPrivateId(PrivateMin) && c.IsPrivateId(PrivateMax)
                      && !c.IsPrivateId(PrivateMin - 1) && !c.IsPrivateId(PrivateMax + 1));
            }

            // ---------- ⑦ 点击不参与判定 ----------
            Console.WriteLine("⑦ 点击：只计数，不改状态、不进内容、不推游标");
            {
                DramaSegmentCore c = NewCore();
                Setup(c, 0f);
                c.OnPage("A1", 1, "npc", "甲", "x", 0.5f);
                c.NoteOptionClick(1f);
                c.NoteOptionClick(1.25f);
                c.NoteOptionClick(1.5f);
                Eq("⑦ 状态不变", c.Active.State, SegmentState.Recording);
                Eq("⑦ 行数不变", c.Active.Lines.Count, 1);
                Eq("⑦ 只计数", c.Active.Clicks, 3);
                Eq("⑦ 游标不受点击影响", c.TakeDelta("A1", 2f).Count, 1);
            }

            // ---------- ⑧ 上限与终态 ----------
            Console.WriteLine("⑧ 可投段上限 4（丢最老）；换世界立刻全丢");
            {
                DramaSegmentCore c = NewCore();
                Setup(c, 0f);
                for (int i = 1; i <= 5; i++)
                {
                    c.Tick(true, false, false, 1.5f + i);                 // 新窗开（W 假→真）
                    c.OnPage("U" + i, 100 + i, "npc", "甲", "第" + i + "件", 1.6f + i);
                    CloseClean(c, 2.5f + i);
                }
                Eq("⑧ 可投段上限=4", c.Deliverables.Count, 4);
                Eq("⑧ 最老的一段被丢", c.TakeDelta("U1", 20f).Count, 0);
                Eq("⑧ 最新的一段能投", c.TakeDelta("U5", 20f).Count, 1);
                Eq("⑧ 已投完不再给", c.TakeDelta("U5", 20.5f).Count, 0);
            }
            {
                DramaSegmentCore c = NewCore();
                Setup(c, 0f);
                c.OnPage("A1", 1, "npc", "甲", "x", 0.5f);
                DramaSegment a1 = c.Active;
                c.OnPage("B2", 2, "npc", "乙", "y", 1f);      // A1 落成可投段
                c.OnWorldChanged(2f);
                Check("⑧ 换世界：活动段清空", c.Active == null);
                Eq("⑧ 换世界：可投段清空", c.Deliverables.Count, 0);
                Eq("⑧ 换世界：内容取不到", c.TakeDelta("B2", 3f).Count, 0);
                Eq("⑧ 换世界：旧段标记为丢弃", a1.State, SegmentState.Discarded);
            }

            // ---------- ⑨ 工具链：照记但不投 ----------
            Console.WriteLine("⑨ 工具链的段：内容照记，但 TakeDelta 永不返回（结果已给过模型）");
            {
                DramaSegmentCore c = NewCore();
                Setup(c, 0f);
                // 「不投」标记**跟着页走**（noInject 位在第 6 个参数）：闸 1 / 闸 1.5 的页各带各的。
                c.OnPage("A1", 1, "npc", "甲", "工具链的话", true, 0.5f);
                c.OnPage("A1", 2, "npc", "甲", "工具链第二句", true, 1f);
                CloseClean(c, 2.5f);
                Eq("⑨ 内容照记（2 条）", c.Deliverables[0].Lines.Count, 2);
                Eq("⑨ 段还在（不是被丢）", c.Deliverables.Count, 1);
                Eq("⑨ 但不投（取不到）", c.TakeDelta("A1", 3f).Count, 0);
                Check("⑨ 且算「有段」（不是「没有段」）", c.HasAnySegment("A1"));

                c.Tick(true, false, false, 4f);             // 之后原生链又开口（同人）
                c.OnPage("A1", 3, "npc", "甲", "原生链的话", 4.1f);
                Check("⑨ 原生新页另开一段（不并回工具链段）",
                      c.Active != null && c.Active.Lines.Count == 1 && c.Active.Lines[0].DramaId == 3);
                List<SegmentLine> d = c.TakeDelta("A1", 5f);
                Eq("⑨ 只投原生那句（工具链段仍不投）", d.Count, 1);
                Eq("⑨ 投的是原生那句", d[0].Text, "原生链的话");
            }

            // ---------- ⑩ 「不投」标记跟着页走（扣住的页开成新段也不能漏标） ----------
            Console.WriteLine("⑩ 待定期间扣住的认领页：ε 到点开成的新段也被标「照记但不投」（落在行上）");
            {
                // ⑩a 同人：旧段收口后扣住的页**另开一段**（A 灭的关窗=分层），标记跟着它自己那一行
                DramaSegmentCore c = NewCore();
                Setup(c, 0f);
                c.OnPage("A1", 100, "npc", "甲", "上一件事", 0.5f);
                c.Tick(false, false, false, 2.5f);          // 关窗（无起跳）⇒ 进入待定
                c.OnPage("A1", 200, "npc", "甲", "工具链页", true, 2.6f);   // 扣住（带不投标记）
                c.Tick(true, false, false, 3.5f);           // ε 到点 ⇒ 干净关窗 ⇒ 扣住的页开新段
                Check("⑩ 扣住的页开成新段（1 条）", c.Active != null && c.Active.Lines.Count == 1);
                Check("⑩ 新段带「不投」标记（落在行上）",
                      c.Active != null && c.Active.Lines[0].NoInject);
                Eq("⑩ 于是取不到增量", c.TakeDelta("A1", 4f).Count, 0);
                Check("⑩ 但仍然算「有段」（点一下不会退回单页）", c.HasAnySegment("A1"));
                Eq("⑩ 旧段留在可投列表（没被吞）", c.Deliverables.Count, 1);
            }
            {
                // ⑩b 扣住的页属于**另一个人** ⇒ 开新段；标记照样落在那一段的行上（打在旧段上就会漏）
                DramaSegmentCore c = NewCore();
                Setup(c, 0f);
                c.OnPage("A1", 100, "npc", "甲", "上一件事", 0.5f);
                c.Tick(false, false, false, 2.5f);
                c.OnPage("B2", 200, "npc", "乙", "另一人的工具链页", true, 2.6f);
                c.Tick(true, false, false, 3.5f);
                Check("⑩ 换人 ⇒ 开新段且带标记",
                      c.Active != null && c.Active.Counterpart == "B2"
                      && c.Active.Lines.Count == 1 && c.Active.Lines[0].NoInject);
                Eq("⑩ 另一个人的旧段留在可投列表", c.Deliverables.Count, 1);
                Eq("⑩ 旧段照常可投", c.TakeDelta("A1", 4f).Count, 1);
            }

            // ---------- ⑪ 取的是「时间上最近的那一段」 ----------
            Console.WriteLine("⑪ 最近段优先：不与「上一次相遇」串味（同人紧接开新页也分成两段）");
            {
                DramaSegmentCore c = NewCore();
                Setup(c, 0f);
                c.OnPage("A1", 1, "npc", "甲", "上一次相遇的话", 0.5f);
                CloseClean(c, 2.5f);                        // 上一次相遇收口（3.5f）⇒ 可投（一直没点）
                c.Tick(true, false, false, 3.6f);           // 0.1s 后就又来（也不并 —— A 灭的关窗=分层）
                c.OnPage("A1", 2, "npc", "甲", "这一次的话", 3.7f);   // 这一次（活动段）
                List<SegmentLine> d = c.TakeDelta("A1", 4f);
                Eq("⑪ 只投当前那一段", d.Count, 1);
                Eq("⑪ 投的是「这一次」", d[0].Text, "这一次的话");
                Eq("⑪ 上一次那份没被动（还在，未被投）", c.Deliverables[0].Delivered, 0);
                Eq("⑪ 旧段仍在可投列表", c.Deliverables.Count, 1);
            }

            // ---------- ⑫ 点击时重判的归属写回 ----------
            Console.WriteLine("⑫ 点击时重判：只改当前页那一条；判不出不覆盖；名字一起改");
            {
                DramaSegmentCore c = NewCore();
                Setup(c, 0f);
                c.OnPage("A1", 10, "player", "甲", "页时刻判成玩家", 0.5f);
                c.OnPage("A1", 11, "npc", "乙", "页时刻判成 NPC", 0.75f);
                c.OverrideSpeaker("A1", 11, "player", "唐炎", 1f);
                Eq("⑫ 当前页归属被改正", c.Active.Lines[1].Speaker, "player");
                Eq("⑫ 名字一起改", c.Active.Lines[1].Name, "唐炎");
                Eq("⑫ 上一条不动", c.Active.Lines[0].Speaker, "player");
                c.OverrideSpeaker("A1", 11, "", "", 1.25f);
                Eq("⑫ 判不出不覆盖（已知不降级为未知）", c.Active.Lines[1].Speaker, "player");
                Eq("⑫ 判不出也不清名字", c.Active.Lines[1].Name, "唐炎");
                c.OverrideSpeaker("A1", 999, "npc", "乙", 1.5f);
                Eq("⑫ 段里没有这一页 ⇒ 空操作", c.Active.Lines[1].Speaker, "player");
            }

            // ---------- ⑬ 混合段：不投的行跳过、该投的行照投 ----------
            Console.WriteLine("⑬ 混合段（处置页不投 + 之后的原生尾页该投）：只投该投的那几行");
            {
                DramaSegmentCore c = NewCore();
                Setup(c, 0f);
                // 处置页（闸 1.5 认领）：原文已经当 tool result 给过模型 ⇒ 不投；但它**开段**。
                c.OnPage("A1", 20403, "npc", "甲", "求饶的话", true, 0.5f);
                // 收口之后同人新页（尾页）：该投 —— 它的原文模型没见过。
                c.OnPage("A1", 20413, "npc", "甲", "那么我先走了", 1f);
                CloseClean(c, 2.5f);
                Eq("⑬ 段里两条都记着（对账用）", c.Deliverables[0].Lines.Count, 2);
                List<SegmentLine> d = c.TakeDelta("A1", 3f);
                Eq("⑬ 只投可投的那一条", d.Count, 1);
                Eq("⑬ 投的是尾页那句", d[0].Text, "那么我先走了");
                Eq("⑬ 不投的那句不会被补投", c.TakeDelta("A1", 3.5f).Count, 0);

                // 反面：整段都是「不投」（纯工具链）⇒ 与从前的段级口径**同效**（取不到）
                c.Tick(true, false, false, 4f);                 // 新窗开（W 假→真）
                c.OnPage("A1", 30001, "npc", "甲", "纯工具链的一段", true, 4.1f);
                CloseClean(c, 5f);
                Eq("⑬ 全是「不投」的段取不到（与段级同效）", c.TakeDelta("A1", 6f).Count, 0);
                Check("⑬ 但算「有段」（点一下不会退回单页）", c.HasAnySegment("A1"));
            }

            // ---------- ⑭ 分层回归（09-23 实机事故）：闲聊收口后 0.5s 的同人新页**不许并进来** ----------
            // 当时的错误实现（同人 + 时间近 + 未投 ⇒ 续接）把"闲聊 2 句 + 论道 6 句"并成一段一起投，
            // 模型拿着大杂烩把**刚完成**的论道读成"我推辞了论道"，回复里凭空道歉。已删该兜底。
            Console.WriteLine("⑭ 事故形态：闲聊（A 灭→干净关窗）后 0.5s 论道开场 ⇒ 必须分成两段（不许按时间近合并）");
            {
                DramaSegmentCore c = NewCore();
                Setup(c, 0f);
                // 闲聊：22604（玩家）+ 22601（NPC），干净关窗、关窗时 A 不亮
                c.OnPage("A1", 22604, "player", "唐炎", "道友，近来可好？", 0.5f);
                c.OnPage("A1", 22601, "npc", "公冶抚", "一切安好。", 1f);
                c.Tick(false, false, false, false, 2.5f);     // 关窗（A 灭）
                c.Tick(false, false, false, false, 3.5f);     // 干净关窗 ⇒ 收口
                Eq("⑭ 闲聊段收口", c.Deliverables.Count, 1);
                Eq("⑭ 闲聊段 2 条", c.Deliverables[0].Lines.Count, 2);
                // 0.5s 后论道开场（22811）—— 若按"同人+未投+时间近"续接，这里就会并成一段（事故）
                c.Tick(true, false, false, true, 4f);
                c.OnPage("A1", 22811, "player", "唐炎", "道友，可愿与我论道？", 4.1f);
                Check("⑭ 论道开场**另开一段**（不并入闲聊）",
                      c.Active != null && c.Active.Lines.Count == 1 && c.Active.Lines[0].DramaId == 22811);
                Eq("⑭ 闲聊段原样留着（未被吞、未被动）", c.Deliverables.Count, 1);
                // 论道页间（A 亮）⇒ 交接 → 回程，同一段
                c.Tick(false, false, false, true, 6f);        // 关窗（A 亮）
                c.Tick(false, false, false, true, 7f);        // 判交接 ⇒ 挂起
                Eq("⑭ 论道页间 ⇒ 挂起（A 判据）", c.Active.State, SegmentState.Suspended);
                c.Tick(true, false, false, true, 7.2f);
                c.OnPage("A1", 22813, "npc", "公冶抚", "好，请。", 7.3f);
                Check("⑭ 回程 ⇒ 论道同一段 2 条", c.Active != null && c.Active.Lines.Count == 2);
                // 点「对话」：只投**最近那段**（论道 2 条）
                List<SegmentLine> d = c.TakeDelta("A1", 8f);
                Eq("⑭ 只投论道那段（2 条）", d.Count, 2);
                Eq("⑭ 首条是论道开场", d[0].Text, "道友，可愿与我论道？");
                Eq("⑭ 闲聊段仍在（没被投）", c.Deliverables[0].Delivered, 0);
            }

            // ---------- ⑮ 论道页间隙的**结构判据**：关窗时动作 UI 亮 ⇒ 交接（不靠时间窗） ----------
            Console.WriteLine("⑮ 关窗时动作 UI（ActionCourseInfo）亮着 ⇒ 交接：挂起 → 同人新页回程 → A 灭后关窗收口");
            {
                DramaSegmentCore c = NewCore();
                c.Tick(true, false, false, false, 0f);        // 基线（A=false）
                c.Tick(true, false, false, false, 0.25f);
                c.OnPage("A1", 22811, "player", "唐炎", "我们来论道", 0.5f);
                // 关窗那一帧动作 UI 仍亮（实测：段①→②间隙层顶=ActionCourseInfo）
                c.Tick(false, false, false, true, 2.5f);
                c.Tick(false, false, false, true, 3.5f);      // ε 到点：A 在 ⇒ 判交接
                Eq("⑮ 判交接 ⇒ 挂起（不按干净关窗收口）", c.Active.State, SegmentState.Suspended);
                Eq("⑮ 于是没有可投段", c.Deliverables.Count, 0);
                c.Tick(true, false, false, true, 4f);         // 下一页的窗开了（A 仍亮）
                c.OnPage("A1", 22813, "npc", "公冶抚", "好，请", 4.1f);
                Check("⑮ 同人新页 ⇒ 回程，同一段 2 条",
                      c.Active != null && c.Active.State == SegmentState.Recording
                      && c.Active.Lines.Count == 2);
                // 交互结束：动作 UI 灭 ⇒ 关窗按干净关窗收口
                c.Tick(false, false, false, false, 20f);
                c.Tick(false, false, false, false, 21f);
                Check("⑮ A 灭后关窗 ⇒ 收口（可投 1 段）", c.Active == null && c.Deliverables.Count == 1);
                Eq("⑮ 一段到底 2 条（前文不丢）", c.Deliverables[0].Lines.Count, 2);
                Eq("⑮ 点对话一次投 2 条", c.TakeDelta("A1", 22f).Count, 2);
            }

            // ---------- ⑯ 场景类交互（邀约）的页间隙：**不设时间窗**的结构判据 ----------
            // 09-23 邀约实测：进场景后 B1（逻辑战斗位）与 B2（战斗界面）**从进到出一直亮**，
            // 页间"点选项 → 关窗 → 新页"没有任何起跳边沿；关窗到新页的间隔由玩家的阅读/演出决定
            // （实测 0.53s ~ 6.64s，可能更长）。判据 = 关窗那刻 B1+B2 亮 ⇒ 这次关窗只是页间隙 ⇒
            // 直接挂起；回程（同人新页到达）**额外要求"到达时仍在场景"** —— 散场后的同人页另开一段。
            Console.WriteLine("⑯ 邀约场景：场景态关窗 ⇒ 直接挂起；场景内新页（多长都算）⇒ 回程；散场后新页 ⇒ 另开一段");
            {
                // (a) 场景态关窗 ⇒ 当即挂起（没有"待定窗"——判据不依赖时间）
                DramaSegmentCore c = NewCore();
                c.Tick(true, true, true, false, 0f);            // 进场景后基线：B1=B2=亮
                c.Tick(true, true, true, false, 0.25f);
                c.OnPage("yqVSYV", 81031, "npc", "姜萌", "要不要同去赏花？", 0.5f);
                c.NoteOptionClick(2.9f);
                c.Tick(false, true, true, false, 3f);           // 关窗那帧：B1+B2 仍亮（无任何起跳）
                Eq("⑯a 场景态关窗 ⇒ 当即挂起（不等 ε/不设窗）", c.Active.State, SegmentState.Suspended);
                Check("⑯a 日志写明场景挂起", _logs.Exists(s => s.Contains("场景内（B1+B2 电平）⇒ 挂起")));

                // (b) 6.64s 后的同人新页（长间隙——1s/4s 窗都接不住）仍在场景 ⇒ 回程，同一段
                c.OnPage("yqVSYV", 81121, "player", "唐炎", "要和姜萌做什么…", 9.64f);
                Check("⑯b 6.64s 长间隙 ⇒ 照样回程（不依赖时间窗）",
                      c.Active != null && c.Active.State == SegmentState.Recording && c.Active.Lines.Count == 2);
                Eq("⑯b 没有可投段（内容还在活动段里）", c.Deliverables.Count, 0);

                // (c) 场景内继续互动（又关窗 → 回程 → 再续页）：一路同段
                c.Tick(true, true, true, false, 9.7f);          // 新页的窗开着（W 假→真）
                c.Tick(false, true, true, false, 12f);          // 又关窗（点选项）⇒ 再挂起
                Eq("⑯c 再关窗 ⇒ 再挂起", c.Active.State, SegmentState.Suspended);
                c.Tick(true, true, true, false, 12.4f);
                c.OnPage("yqVSYV", 81341, "npc", "姜萌", "好呀，走吧。", 12.5f);
                c.Tick(false, true, true, false, 16f);          // 再关窗 ⇒ 再挂起
                c.Tick(true, true, true, false, 16.3f);
                c.OnPage("yqVSYV", 22703, "npc", "姜萌", "再来一局？", 16.4f);
                Eq("⑯c 三轮一路同段 4 条", c.Active.Lines.Count, 4);
                Eq("⑯c 点对话投 4 条", c.TakeDelta("yqVSYV", 17f).Count, 4);

                // (d) 散场之后同人新页（场景态已解除）⇒ **不回程**：挂起段收口 + 新页开新段
                DramaSegmentCore c2 = NewCore();
                c2.Tick(true, true, true, false, 0f);
                c2.Tick(true, true, true, false, 0.25f);
                c2.OnPage("A2", 1, "npc", "乙", "（场景内最后一句）", 0.5f);
                c2.Tick(false, true, true, false, 3f);          // 场景态关窗 ⇒ 挂起
                Eq("⑯d 挂起", c2.Active.State, SegmentState.Suspended);
                c2.Tick(true, false, false, false, 5f);         // 散场：B1/B2 落假（回程窗起）
                c2.OnPage("A2", 2, "npc", "乙", "散场之后的另一件事", 6f);   // 场景外的同人页
                Check("⑯d 不回程：挂起段收口、新页开新段",
                      c2.Deliverables.Count == 1 && c2.Deliverables[0].Lines.Count == 1
                      && c2.Active != null && c2.Active.Lines.Count == 1 && c2.Active.Lines[0].DramaId == 2);
                Check("⑯d 收口理由写明「场景已退出」", c2.Deliverables[0].SettleReason.Contains("场景已退出"));

                // (e) 场景态外的老路径不受影响：单 B1 亮不算场景态，仍是 1s 边沿判据
                DramaSegmentCore c3 = NewCore();
                c3.Tick(true, true, false, false, 0f);
                c3.Tick(true, true, false, false, 0.25f);
                c3.OnPage("A4", 1, "npc", "戊", "x", 0.5f);
                c3.Tick(false, true, false, false, 3f);         // 关窗（单 B1 亮——不算场景态）
                c3.Tick(false, true, false, false, 4f);         // 1s 到点 ⇒ 干净收口
                Check("⑯e 单 B1 亮不算场景态 ⇒ 照旧 1s 收口", c3.Active == null && c3.Deliverables.Count == 1);

                // (f) `A↑` 边沿证据（09-23 邀约实测：关窗 0.5s 后 A 才起跳，旧实现只看电平会漏）
                DramaSegmentCore c4 = NewCore();
                c4.Tick(true, false, false, false, 0f);
                c4.Tick(true, false, false, false, 0.25f);
                c4.OnPage("A5", 1, "npc", "己", "x", 0.5f);
                c4.Tick(false, false, false, false, 3f);        // 关窗（A=0，非场景态 ⇒ 起 1s 待定窗）
                c4.Tick(false, false, false, true, 3.5f);       // 关窗后 0.5s：层顶才变 ActionCourseInfo
                c4.Tick(false, false, false, true, 4f);         // 1s 到点：ε 窗内见过 A↑ ⇒ 判交接
                Eq("⑯f A↑（关窗后起跳）也算交接证据 ⇒ 挂起", c4.Active.State, SegmentState.Suspended);
                Check("⑯f 日志写明 A↑ 的人话", _logs.Exists(s => s.Contains("动作 UI 起跳")));
            }

            // ---------- ⑰ 选项行进段（09-23）：把"玩家选了什么"记进段 ----------
            // 为什么：页面正文不含选项 —— 论道/邀约这类问答式交互里，只投正文会让模型
            // "只知道疑问、不知道玩家选了什么"，甚至把已发生的交互当成没开始（又发起一次动作工具）。
            Console.WriteLine("⑰ 选项行：插在当前页之后、跟随归属/不投标记、只认同人活动段");
            {
                DramaSegmentCore c = NewCore();
                Setup(c, 0f);
                c.OnPage("A1", 81031, "npc", "姜萌", "要和姜萌做什么…", 0.5f);
                c.NoteOption("A1", 81031, "赏花", "唐炎", 1.0f);
                c.OnPage("A1", 81341, "npc", "姜萌", "好呀，走吧。", 1.5f);
                CloseClean(c, 2.5f);
                Eq("⑰ 段里 3 行（页/选项/页）", c.Deliverables[0].Lines.Count, 3);
                Eq("⑰ 第 2 行是选项", c.Deliverables[0].Lines[1].Kind, "option");
                Eq("⑰ 选项行说话人=choice", c.Deliverables[0].Lines[1].Speaker, "choice");
                Eq("⑰ 选项文本一字不改", c.Deliverables[0].Lines[1].Text, "赏花");
                Eq("⑰ 顺序：页→选项→页",
                   Rows(c.Deliverables[0]), "1:npc:要和姜萌做什么… | 2:choice:赏花 | 3:npc:好呀，走吧。");
                Eq("⑰ 三行都能投", c.TakeDelta("A1", 3f).Count, 3);

                // 排除项：没有同人活动段时丢弃（不凭空造段）；空文本不记
                DramaSegmentCore c2 = NewCore();
                c2.Tick(true, false, false, false, 0f);
                c2.Tick(true, false, false, false, 0.25f);
                c2.OnPage("A1", 1, "npc", "甲", "x", 0.5f);
                c2.NoteOption("B9", 1, "赏花", "唐炎", 1f);       // 别人 ⇒ 丢弃
                c2.NoteOption("A1", 1, "", "唐炎", 1.2f);         // 空文本 ⇒ 丢弃
                Eq("⑰ 非本段对方的选项不记", c2.Active.Lines.Count, 1);

                // 工具链的段（页面正文不投）：选项行**不跟随**（09-23 用户定：选项必须投）。
                // 真机形状：攻击处置页（正文已当 tool result ⇒ 不投）上玩家点的「击杀此人」——
                // 旧实现跟着不可投 ⇒ 直接丢；现在必须能投出去。
                DramaSegmentCore c3 = NewCore();
                Setup(c3, 0f);
                c3.OnPage("A1", 20403, "npc", "甲", "求饶的话", true, 0.5f);
                c3.NoteOption("A1", 20403, "击杀此人", "唐炎", 1f);
                CloseClean(c3, 2.5f);
                List<SegmentLine> opt = c3.TakeDelta("A1", 3f);
                Eq("⑰ 处置页正文不投、选项照样投", opt.Count, 1);
                Eq("⑰ 投出去的就是那个选择", opt[0].Text, "击杀此人");
                Eq("⑰ 页正文仍留着（只不投、不删）", c3.Deliverables[0].Lines.Count, 2);
            }

            // ---------- ⑱ 区间读数（09-23）：工具结果读段、读走即消费 ----------
            // 为什么要它：`TalkOutcome`/`BattleOutcome` 不再自建内容台账 —— 工具结果改读段的区间。
            // 与「对话」的投递游标**互不抢**（各自读各自的口径），读走即标消费（之后点对话不重投）。
            Console.WriteLine("⑱ 区间读数：跨段归并、按时序、不碰游标、读走即消费");
            {
                DramaSegmentCore c = NewCore();
                Setup(c, 0f);
                c.OnPage("A1", 100, "npc", "甲", "第一句", 1f);
                c.NoteOption("A1", 100, "选项一", "唐炎", 1.5f);
                c.Tick(false, false, false, 3f);
                c.Tick(false, false, false, 4f);                    // 干净关窗 ⇒ 旧段可投
                c.OnPage("A1", 200, "npc", "甲", "第二件事", 5f);   // 同人新页 ⇒ 新段
                c.NoteOption("A1", 200, "选项二", "唐炎", 5.5f);

                List<SegmentLine> all = c.ReadRange("A1", 0.5f, 6f, false);
                Eq("⑱ 跨段归并：4 行（页/选项 × 两段）", all.Count, 4);
                Eq("⑱ 按时序（追加序 = 时序）",
                   all[0].Text + "," + all[1].Text + "," + all[2].Text + "," + all[3].Text,
                   "第一句,选项一,第二件事,选项二");
                Eq("⑱ 只读：不碰投递游标（对话仍拿得到当前段）", c.TakeDelta("A1", 6.1f).Count, 2);

                List<SegmentLine> taken = c.ReadRange("A1", 0.5f, 6f, true);
                Eq("⑱ 读走即消费：还是 4 行", taken.Count, 4);
                Eq("⑱ 消费后「对话」不再重投", c.TakeDelta("A1", 6.2f).Count, 0);
                Eq("⑱ 只标记不删（对账仍在）", taken[0].Text, "第一句");
                Eq("⑱ 计数查询（只读）", c.CountRange("A1", 0.5f, 6f), 4);
                Eq("⑱ 区间外的行不算", c.CountRange("A1", 5.2f, 6f), 1);
                // 页数（选项不算）：收口判据要的是"抓到了台词"，选择只说明玩家答了话
                Eq("⑱ 页数只数页（两个选项不算）", c.CountPages("A1", 0.5f, 6f), 2);
                Eq("⑱ 别人名下读不到（不猜）", c.ReadRange("B9", 0.5f, 6f, false).Count, 0);
                Eq("⑱ 空 uid 读不到（不猜）", c.ReadRange("", 0.5f, 6f, false).Count, 0);
            }

            // ---------- ⑲ 链心跳（09-23）：段的寿命由链驱动 ----------
            Console.WriteLine("⑲ 链心跳：续命滑动预算、停跳回归现行规则、硬上限不可续期");
            {
                DramaSegmentCore c = NewCore();
                Setup(c, 0f);
                c.OnPage("A1", 1, "npc", "甲", "约战", 0.5f);
                CloseWithHandoff(c, 2.5f);                 // 挂起
                c.Tick(false, false, false, 3.5f);         // 转场全落假（探针全暗，回程窗起）
                for (float t = 4f; t <= 400f; t += 10f) c.RenewChain("A1", t);
                c.Tick(false, false, false, 400f);
                Eq("⑲ 心跳新鲜：超 180s 预算也照样活着（长战斗不切段）",
                   c.Active.State, SegmentState.Suspended);
                c.Tick(false, false, false, 500f);
                Eq("⑲ 心跳过期：仍在挂起（等回程窗走完）", c.Active.State, SegmentState.Suspended);
                c.Tick(false, false, false, 560.1f);
                Check("⑲ 心跳停 → 现行规则接管 ⇒ 回程窗满收口",
                      c.Active == null && c.Deliverables.Count == 1 && c.Deliverables[0].Lines.Count == 1);

                // 别人的心跳不许给这个人续命
                DramaSegmentCore c2 = NewCore();
                Setup(c2, 0f);
                c2.OnPage("A1", 1, "npc", "甲", "x", 0.5f);
                CloseWithHandoff(c2, 2.5f);
                for (float t = 4f; t <= 40f; t += 10f) c2.RenewChain("B9", t);
                c2.Tick(false, false, false, 23f);         // 转场落假 ⇒ 回程窗起
                c2.Tick(false, false, false, 43.1f);       // 20s 到点
                Check("⑲ 心跳按对方隔离 ⇒ 别人的心跳不续我的命",
                      c2.Active == null && c2.Deliverables.Count == 1);

                // 一直有心跳也活不过硬上限（防僵尸的那一道）
                DramaSegmentCore c3 = NewCore();
                Setup(c3, 0f);
                c3.OnPage("A2", 1, "npc", "乙", "x", 0.5f);
                CloseWithHandoff(c3, 2.5f);
                for (float t = 4f; t <= 1810f; t += 10f) c3.RenewChain("A2", t);
                c3.Tick(false, true, false, 1810f);
                Check("⑲ 硬上限不可续期 ⇒ 收口",
                      c3.Active == null && c3.Deliverables.Count == 1);
                Check("⑲ 收口理由=硬上限", c3.Deliverables[0].SettleReason.Contains("硬上限"),
                      c3.Deliverables[0].SettleReason);

                // 换世界 ⇒ 心跳跟着清（不许把新世界的段续上）
                DramaSegmentCore c4 = NewCore();
                Setup(c4, 0f);
                c4.OnPage("A1", 1, "npc", "甲", "x", 0.5f);
                CloseWithHandoff(c4, 2.5f);
                c4.RenewChain("A1", 4f);
                c4.OnWorldChanged(5f);
                c4.OnPage("A1", 2, "npc", "甲", "新世界", 6f);
                c4.Tick(false, false, false, 30f);
                Check("⑲ 换世界清心跳：老段已丢、新段仍在记录",
                      c4.Active != null && c4.Active.Lines.Count == 1 && c4.Deliverables.Count == 0);
            }

            Console.WriteLine();
            if (_fails.Count == 0)
            {
                Console.WriteLine("全部通过：" + _pass + " 项断言");
                return 0;
            }
            Console.WriteLine("失败 " + _fails.Count + " 项（通过 " + _pass + " 项）：");
            for (int i = 0; i < _fails.Count; i++) Console.WriteLine("  - " + _fails[i]);
            return 1;
        }
    }
}
