// 剧情账本（DramaLedgerCore）+ 副本见闻渲染（DungeonDramaCore）的单元测试。
//
// 为什么要有这个工程：账本出错的表现**全是静默的**。
//   · 判不出归属的页被丢 ⇒ 日志一切正常，只是"进副本那一幕"永远缺席；
//   · 取区间取错（用了时间窗 / 钉子选错）⇒ 履历里剧情为空，或只拿到后半段；
//   · 裁容量没留痕 ⇒ 内容断了却看不出来（本工程最怕的失败形态）；
//   · 开/关窗不幂等 ⇒ 从第二场战斗起再也记不到剧情，且没有任何报错。
// 这些都没法靠"进游戏打一场"稳定复现（进副本那一刻才知道副本 NPC 是谁），所以判据表
// 必须由测试锁住。真机数字用 10-24 副本 4901 的实测时间线复现。
//
// 九组：
//   ① 全量留痕：判不出归属 / 私有窗都照记（这是它比段多出来的覆盖）
//   ② 连续重复去重（窗口重建/重复 InitData 会重报），但隔开就不算重复
//   ③ 事件钉子 + 区间：取最后一对 begin…end，区间内按发生顺序
//   ④ 多波次（多次 begin）：区间不切碎 —— 取"上一个 end 之后的第一个 begin"
//   ⑤ 单人战斗（只有 end 没有 begin）：不产生孤儿配对，也不影响下一场
//   ⑥ 起点被容量裁掉：StartFound=false / HadAnyMarker=true（必须能如实上报"只拿到后半段"）
//   ⑦ 容量上限 + 裁掉留痕（DroppedTotal / 窗口 Trimmed）
//   ⑧ 换世界整账作废（含窗状态）
//   ⑨ 见闻渲染：页→「谁：…」、选项→「你选择了…」、判不出写「有人」、截断与上限

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

        private static int Main()
        {
            try
            {
                Group1_KeepsEverything();
                Group2_Dedup();
                Group3_Window();
                Group4_MultiWave();
                Group5_OrphanEnd();
                Group6_TrimmedStart();
                Group7_Capacity();
                Group8_Reset();
                Group9_Render();
            }
            catch (Exception e)
            {
                Console.WriteLine("测试工程自身异常：" + e);
                return 2;
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

        private static DramaLedgerCore New() { return new DramaLedgerCore(); }

        // ================================================================ ① 全量留痕

        private static void Group1_KeepsEverything()
        {
            var c = New();
            // 判不出归属（unitId 空）——**段会丢，账本必须留**
            //   真机：进入副本那一页 610911（选项「深入探索」）就是这个形态
            var a = c.Add("page", "", "npc", "", "此地的气息不对……", 610911, false, 1f);
            Check("① 判不出归属的页照记（段会丢，账本是全量的）", a != null);
            Eq("① 归属存空串（不猜）", a == null ? "?" : a.UnitId, "");

            // 私有窗（我方自制）—— 照记，但打私有标记（读者自己过滤）
            var b = c.Add("page", "U1", "player", "知恩", "（我方工具窗）", -803158281, true, 2f);
            Check("① 我方私有窗也照记（打 priv 标记，由读者过滤）", b != null && b.Private);

            Eq("① 条数", c.Count, 2);
            Eq("① 空文本不记", c.Add("page", "U1", "npc", "甲", "   ", 1, false, 3f), null);
        }

        // ================================================================ ② 去重

        private static void Group2_Dedup()
        {
            var c = New();
            c.Add("page", "U1", "npc", "甲", "同一句话", 100, false, 10f);
            // 窗口重建/重复 InitData 会在极短窗内重报同一页
            var dup = c.Add("page", "U1", "npc", "甲", "同一句话", 100, false, 10.2f);
            Eq("② 0.5s 内的同文重复被丢掉（窗口重建会重报）", dup, null);
            Eq("② 条数仍是 1", c.Count, 1);

            // 隔开就不算重复（玩家翻回去再看一遍是真实发生的两页）
            var far = c.Add("page", "U1", "npc", "甲", "同一句话", 100, false, 12f);
            Check("② 隔 2s 的同文照记（翻回去又看了一遍是两件事）", far != null);
            Eq("② 条数 2", c.Count, 2);

            // 同文但不同人 ⇒ 不是重复（两个人说了同一句）
            var other = c.Add("page", "U2", "npc", "乙", "同一句话", 100, false, 12.1f);
            Check("② 换人后同文照记", other != null);
        }

        // ================================================================ ③ 区间

        private static void Group3_Window()
        {
            var c = New();
            c.Add("page", "U0", "npc", "路人", "战斗之前的事", 1, false, 1f);
            c.BeginWindow("", 2f);
            c.Add("page", "U1", "npc", "穆沈思", "此地的气息不对……", 24004, false, 3f);
            c.Add("option", "U1", "choice", "知恩", "深入探索", 24004, false, 4f);
            c.Add("page", "U1", "player", "知恩", "来者何人？", 24005, false, 5f);
            c.EndWindow("", 6f);
            c.Add("page", "U0", "npc", "路人", "战斗之后的事", 2, false, 7f);

            var w = c.TakeWindow(DramaLedgerCore.EventBattleBegin, DramaLedgerCore.EventBattleEnd, false);
            Check("③ 起点找到", w.StartFound);
            Check("③ 见过钉子", w.HadAnyMarker);
            Eq("③ 区间内条数（只含中间三条，两端各排除一条）", w.Items.Count, 3);
            Eq("③ 首条=战前那句", w.Items.Count > 0 ? w.Items[0].Text : "?", "此地的气息不对……");
            Eq("③ 末条=战中的玩家台词", w.Items.Count > 2 ? w.Items[2].Text : "?", "来者何人？");
            Check("③ includeEvents=false 时不含事件行",
                  !w.Items.Exists(x => x.Kind == "event"));

            // 端点（begin/end 两根钉子）本身就是**边界**，无论 includeEvents 与否都不算内容 ——
            // 否则读者会把钉子当剧情渲染出来。includeEvents 只影响**区间内部的**其他事件行。
            var w2 = c.TakeWindow(DramaLedgerCore.EventBattleBegin, DramaLedgerCore.EventBattleEnd, true);
            Eq("③ 端点始终排除（钉子不是剧情）", w2.Items.Count, 3);

            // 区间**内部**还有一个别的事件钉子（如以后加的"撤退"）：includeEvents 必须真的控制它
            var c2 = New();
            c2.BeginWindow("", 1f);
            c2.Add("page", "U1", "npc", "甲", "战前", 10, false, 2f);
            c2.Mark("retreat", "", 3f);
            c2.Add("page", "U1", "npc", "甲", "战中", 11, false, 4f);
            c2.EndWindow("", 5f);
            var wo = c2.TakeWindow(DramaLedgerCore.EventBattleBegin, DramaLedgerCore.EventBattleEnd, false);
            var wi = c2.TakeWindow(DramaLedgerCore.EventBattleBegin, DramaLedgerCore.EventBattleEnd, true);
            Eq("③ includeEvents=false 时内部事件被滤掉", wo.Items.Count, 2);
            Eq("③ includeEvents=true 时内部事件保留", wi.Items.Count, 3);
            Check("③ 起作用的正是内部那根（不是端点）",
                  wi.Items.Exists(x => x.Text == "retreat"));
            Check("③ includeEvents=true 也不含端点", !w2.Items.Exists(x => x.Text == DramaLedgerCore.EventBattleBegin));
        }

        // ================================================================ ④ 多波次

        private static void Group4_MultiWave()
        {
            // 一场副本里队友可能被多次塞进容器 ⇒ ★直塞完成★ 会打多次。
            // 若每次插一根 begin，"最后一个 begin"会把前面几波切掉。
            var c = New();
            Check("④ 第一次开窗成功", c.BeginWindow("", 1f));
            Check("④ 第二次开窗被幂等挡掉（不重复插钉子）", !c.BeginWindow("", 2f));
            c.Add("page", "U1", "npc", "甲", "第一波", 10, false, 3f);
            c.EndWindow("", 4f);
            c.BeginWindow("", 5f);
            c.Add("page", "U1", "npc", "甲", "第二波", 11, false, 6f);
            c.EndWindow("", 7f);

            var w = c.TakeWindow(DramaLedgerCore.EventBattleBegin, DramaLedgerCore.EventBattleEnd, false);
            Check("④ 起点找到", w.StartFound);
            // 取"最后一个 end 之前、上一个 end 之后的第一个 begin" ⇒ 应是第二波
            Eq("④ 取最后一对区间 ⇒ 只有第二波那一条", w.Items.Count, 1);
            Eq("④ 内容正确", w.Items.Count > 0 ? w.Items[0].Text : "?", "第二波");
        }

        // ================================================================ ⑤ 孤儿 end

        private static void Group5_OrphanEnd()
        {
            // 单人战斗也会走 BattleExit。若那时插一根孤儿 end，
            // 它会卡在"从最后一个 end 往前找 begin"的路径上，把真正那场配对堵死。
            var c = New();
            c.BeginWindow("", 1f);
            c.Add("page", "U1", "npc", "甲", "有队友那场", 10, false, 2f);
            c.EndWindow("", 3f);

            // 单人战斗：没开过窗
            Check("⑤ 没开过窗时 EndWindow 什么都不做（不插孤儿 end）", !c.EndWindow("", 4f));

            // 账本里压根没有 end 钉子时（比如刚开窗还没打）取区间：必须**什么都不报**
            //   若不判 endIdx<0 就往下走，beginIdx 会停在 -1，
            //   调用方看到 StartFound 语义混乱、拿到一个"看起来完整"的空区间。
            var empty0 = New();
            empty0.BeginWindow("", 1f);
            empty0.Add("page", "U1", "npc", "甲", "只开了窗还没关", 10, false, 2f);
            var w0 = empty0.TakeWindow(DramaLedgerCore.EventBattleBegin, DramaLedgerCore.EventBattleEnd, false);
            Check("⑤ 没有 end 钉子 ⇒ 起点不算找到", !w0.StartFound);
            Check("⑤ 没有 end 钉子 ⇒ 也不算「见过钉子」（那表示「没记过」而不是「被裁了」）", !w0.HadAnyMarker);
            Eq("⑤ 没有 end 钉子 ⇒ 区间为空（不许把半场当整场）", w0.Items.Count, 0);

            var w = c.TakeWindow(DramaLedgerCore.EventBattleBegin, DramaLedgerCore.EventBattleEnd, false);
            Check("⑤ 前一场仍能正确配对（孤儿 end 没堵死它）", w.StartFound && w.Items.Count == 1);

            // 再来一场真的
            c.BeginWindow("", 5f);
            c.Add("page", "U1", "npc", "甲", "第二场", 11, false, 6f);
            c.EndWindow("", 7f);
            var w2 = c.TakeWindow(DramaLedgerCore.EventBattleBegin, DramaLedgerCore.EventBattleEnd, false);
            Eq("⑤ 取最后一对 ⇒ 第二场", w2.Items.Count > 0 ? w2.Items[0].Text : "?", "第二场");
        }

        // ================================================================ ⑥ 起点被裁

        private static void Group6_TrimmedStart()
        {
            var c = New();
            c.BeginWindow("", 1f);
            // 塞爆容量，把 begin 钉子挤出去
            for (int i = 0; i < DramaLedgerCore.MaxEntries + 10; i++)
                c.Add("page", "U1", "npc", "甲", "第" + i + "页", 100 + i, false, 2f + i * 0.01f);
            c.EndWindow("", 999f);

            var w = c.TakeWindow(DramaLedgerCore.EventBattleBegin, DramaLedgerCore.EventBattleEnd, false);
            Check("⑥ 起点已被裁掉 ⇒ StartFound=false", !w.StartFound);
            Check("⑥ 但确实见过钉子（区分「没开过窗」与「开过但被裁了」）", w.HadAnyMarker);
            Check("⑥ 账本标记为「被裁过」", w.Trimmed);
            Check("⑥ 仍能拿到后半段（不为空）", w.Items.Count > 0);
            Eq("⑥ 后半段条数 = 容量上限 - 1（begin 已丢）", w.Items.Count, DramaLedgerCore.MaxEntries - 1);
        }

        // ================================================================ ⑦ 容量

        private static void Group7_Capacity()
        {
            var c = New();
            for (int i = 0; i < DramaLedgerCore.MaxEntries + 50; i++)
                c.Add("page", "U1", "npc", "甲", "第" + i + "页", 100 + i, false, i * 0.01f);
            Eq("⑦ 条数被限制在上限", c.Count, DramaLedgerCore.MaxEntries);
            Eq("⑦ 裁掉多少有记账（不许静默丢）", c.DroppedTotal, 50);
            // 最老的留下的是第 50 条
            var snap = c.Snapshot();
            Eq("⑦ 丢的是最旧的", snap[0].Text, "第50页");
            Check("⑦ Brief 里能看到裁过", c.Brief().Contains("已裁 50 条"), c.Brief());
        }

        // ================================================================ ⑧ 换世界

        private static void Group8_Reset()
        {
            var c = New();
            c.BeginWindow("", 1f);
            c.Add("page", "U1", "npc", "甲", "旧世界", 10, false, 2f);
            c.Reset();
            Eq("⑧ 换世界后清空", c.Count, 0);
            Eq("⑧ 裁掉计数也清", c.DroppedTotal, 0);
            Check("⑧ 窗状态也清（否则新档第一场会因「窗还开着」而记不到）", !c.WindowOpen);

            // 新世界的 end 因为没有开过窗 ⇒ 不插（不会与旧世界串味）
            Check("⑧ 新世界没开窗时 EndWindow 不动作", !c.EndWindow("", 3f));
            c.BeginWindow("", 4f);
            Check("⑧ 新世界能正常开窗", c.WindowOpen);
        }

        // ================================================================ ⑨ 渲染

        private static void Group9_Render()
        {
            var buf = new List<DungeonDramaLine>();
            DungeonDramaCore.Add(buf, "page", "npc", "穆沈思", "此地的气息不对……", 1, 1f);
            DungeonDramaCore.Add(buf, "option", "choice", "知恩", "深入探索", 1, 2f);
            DungeonDramaCore.Add(buf, "page", "player", "知恩", "来者何人？", 2, 3f);

            var lines = DungeonDramaCore.Format(buf, "知恩");
            Eq("⑨ 三行", lines.Count, 3);
            Eq("⑨ NPC 页", lines.Count > 0 ? lines[0] : "?", "穆沈思：「此地的气息不对……」");
            Eq("⑨ 选项", lines.Count > 1 ? lines[1] : "?", "你选择了「深入探索」");
            Eq("⑨ 玩家页（带名字）", lines.Count > 2 ? lines[2] : "?", "知恩：「来者何人？」");

            // 判不出名字 ⇒ 写「有人」，绝不猜成某个具体的人
            var b2 = new List<DungeonDramaLine>();
            DungeonDramaCore.Add(b2, "page", "npc", "", "谁在说话？", 3, 1f);
            Eq("⑨ 判不出发言人 ⇒ 写「有人」（不猜）",
               DungeonDramaCore.Format(b2, "知恩")[0], "有人：「谁在说话？」");

            // 玩家页但没名字 ⇒ 兜玩家名；连玩家名都没有 ⇒ 「你」
            var b3 = new List<DungeonDramaLine>();
            DungeonDramaCore.Add(b3, "page", "player", "", "我说的", 4, 1f);
            Eq("⑨ 玩家页缺名字 ⇒ 兜玩家名", DungeonDramaCore.Format(b3, "知恩")[0], "知恩：「我说的」");
            Eq("⑨ 连玩家名都没有 ⇒ 「你」", DungeonDramaCore.Format(b3, "")[0], "你：「我说的」");

            // 连续重复（同一页被重报两次）
            var b4 = new List<DungeonDramaLine>();
            DungeonDramaCore.Add(b4, "page", "npc", "甲", "同一句", 5, 1f);
            DungeonDramaCore.Add(b4, "page", "npc", "甲", "同一句", 5, 2f);
            Eq("⑨ 连续同文去重（与账本同一条纪律）", b4.Count, 1);

            // 单行截断
            string longText = new string('长', 200);
            var b5 = new List<DungeonDramaLine>();
            DungeonDramaCore.Add(b5, "page", "npc", "甲", longText, 6, 1f);
            string one = DungeonDramaCore.Format(b5, "知恩")[0];
            // 整句形如 `甲：「前70字…」` —— 省略号在**引号内**，所以判据是"含省略号且明显短于原文"
            Check("⑨ 单行被截断并加省略号", one.Contains("…"), one);
            Check("⑨ 截断后明显短于原文", one.Length < longText.Length, "len=" + one.Length);
            Check("⑨ 正文长度恰好 = 上限 + 省略号",
                  one.Contains(new string('长', DungeonDramaCore.MaxTextChars) + "…"));

            // 行数上限：超了保留**最近的**（剧情通常"铺垫在前、抉择在后"）
            var b6 = new List<DungeonDramaLine>();
            for (int i = 0; i < DungeonDramaCore.MaxLines + 5; i++)
                DungeonDramaCore.Add(b6, "page", "npc", "甲", "第" + i + "句", 100 + i, i * 1f);
            var l6 = DungeonDramaCore.Format(b6, "知恩");
            Eq("⑨ 行数被限到上限", l6.Count, DungeonDramaCore.MaxLines);
            Check("⑨ 保留的是最近的（末句在）",
                  l6[l6.Count - 1].Contains("第" + (DungeonDramaCore.MaxLines + 4) + "句"), l6[l6.Count - 1]);
            Check("⑨ 首句是较早的（不是从头留）",
                  l6[0].Contains("第5句"), l6[0]);
            Eq("⑨ 丢了多少能算出来", DungeonDramaCore.DroppedCount(b6), 5);

            // 空输入
            Eq("⑨ 空输入 ⇒ 空结果", DungeonDramaCore.Format(new List<DungeonDramaLine>(), "知恩").Count, 0);
            Eq("⑨ null 输入不炸", DungeonDramaCore.Format(null, "知恩").Count, 0);

            // 总字数上限
            var b7 = new List<DungeonDramaLine>();
            for (int i = 0; i < DungeonDramaCore.MaxLines; i++)
                DungeonDramaCore.Add(b7, "page", "npc", "甲", new string('字', DungeonDramaCore.MaxTextChars), 1, i);
            var l7 = DungeonDramaCore.Format(b7, "知恩");
            int total = 0;
            for (int i = 0; i < l7.Count; i++) total += l7[i].Length;
            Check("⑨ 总字数不超上限", total <= DungeonDramaCore.MaxTotalChars + DungeonDramaCore.MaxTextChars,
                  "total=" + total);
        }
    }
}
