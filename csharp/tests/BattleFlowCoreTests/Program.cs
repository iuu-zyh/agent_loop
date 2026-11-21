// 「战前 / 战中」两态判据（BattleFlowCore）的单元测试。
//
// 为什么要有这个工程：这类判据出错的表现**全是静默的**。
//   · 该挡没挡 ⇒ 两场架叠在一起（10-25「点了对话没反应」的整条根因链）；
//   · 不该挡却挡 ⇒ NPC 永久哑掉，日志里只表现为"它不再发起动作"；
//   · 退出条件写错 ⇒ 婉拒一场切磋后再也不能攻击，直到重启游戏。
// 没有一条会报错，所以必须用断言把判据表钉住。
//
// 时间线全部照抄 10-25 那场真机日志（Player.log 47558-47745），一秒不差 ——
// 这样"这条规则是不是为那个故障写的"在半年后还看得出来。
//
// 七组：
//   ① 页级入口：开战按钮一摆出来就是战前（早于 NPC 调工具 11 秒）
//   ② 记忆性：页关了、应战窗还没弹的那 2.2 秒空洞里**仍然是战前**（这是整个改动的核心）
//   ③ 点开战按钮 = 确认：此后页没了也不退出，直到真打起来
//   ④ 退出条件：新页没开战按钮 / 点非开战按钮 / 应战窗关了没打 / 兜底
//   ⑤ 战中：进战斗 ⇒ 战中（实时）；战斗结束 ⇒ 退出
//   ⑤b 边沿早于电平：`IntoBattle` 先到、战斗位后亮 —— 不能一帧就掉
//   ⑥ 接受应战时「窗关」与「进战斗」同毫秒 —— 不能被读成"取消"
//   ⑦ 处置表：攻击/切磋/传送/召唤 拒绝；其余排队；只读放行 + 拒绝文案三要素

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

        private static readonly DateTime T0 = new DateTime(2026, 10, 25, 18, 51, 10, 638);
        private static DateTime At(double sec) { return T0.AddSeconds(sec); }

        /// <summary>造一个已登记"寻仇页 100003 / 切磋确认页 212042"的核（编号都是配表事实）。</summary>
        private static BattleFlowCore Make()
        {
            return new BattleFlowCore(new[] { 100003, 212042 }, BattleFlowCore.DefaultFallbackSeconds);
        }

        private static int Main()
        {
            try
            {
                Group1_PageEntry();
                Group2_MemoryAcrossGap();
                Group3_ClickConfirms();
                Group4_ExitConditions();
                Group5_InBattle();
                Group5b_EdgeBeforeLevel();
                Group6_AcceptIsNotCancel();
                Group7_Grades();
                Group8_ClickScope();
            }
            catch (Exception e)
            {
                Console.WriteLine("  ✗ 抛异常: " + e);
                _fails.Add("抛异常");
            }

            Console.WriteLine();
            if (_fails.Count == 0)
            {
                Console.WriteLine("✓ BattleFlowCore 全部通过（" + _pass + " 项）");
                return 0;
            }
            Console.WriteLine("✗ 失败 " + _fails.Count + " 项 / 通过 " + _pass + " 项");
            foreach (string f in _fails) Console.WriteLine("   · " + f);
            return 1;
        }

        // ---------------------------------------------------------------- ①
        private static void Group1_PageEntry()
        {
            Console.WriteLine("① 页级入口");
            var c = Make();
            Eq("初始 Idle", c.State, BattleFlowState.Idle);
            Check("初始不武装", !c.Armed);

            string why;
            // 真机 18:51:10.638 寻仇页 20203 弹出，唯一按钮就是 100003
            Check("开战页 ⇒ 进战前", c.NotePage(20203, new[] { 100003 }, At(0), out why), why);
            Eq("状态=PreBattle", c.State, BattleFlowState.PreBattle);
            Check("武装", c.Armed);
            Check("事件原因提到页号", why.Contains("20203"), why);

            // 普通页不点亮
            var c2 = Make();
            c2.NotePage(22832, new[] { 228321, 228322 }, At(0), out why);
            Eq("普通页 ⇒ 仍 Idle", c2.State, BattleFlowState.Idle);

            // 我方自制窗（负 id）一律不参与 —— 否则赠礼确认窗会把战前误清
            var c3 = Make();
            c3.NotePage(20203, new[] { 100003 }, At(0), out why);
            c3.NotePage(-803158251, new[] { -803158249, -803158248 }, At(1), out why);
            Eq("自制负 id 页不动状态", c3.State, BattleFlowState.PreBattle);

            // 同一页重复建选项（窗口重建）不该反复改状态
            var c4 = Make();
            c4.NotePage(20203, new[] { 100003 }, At(0), out why);
            Check("重复同页不重复触发", !c4.NotePage(20203, new[] { 100003 }, At(1), out why));

            Eq("学到两个编号", c4.Count, 2);
            Check("100003 认得", c4.IsBattleOption(100003));
            Check("212042 认得", c4.IsBattleOption(212042));
            Check("232071 不认得", !c4.IsBattleOption(232071));
            Eq("负 id 不收", new BattleFlowCore(null, 0).AddBattleOption(-5), false);
        }

        // ---------------------------------------------------------------- ②
        private static void Group2_MemoryAcrossGap()
        {
            Console.WriteLine("② 记忆性：页关到应战窗之间那 2.2 秒空洞");
            var c = Make();
            string why;

            c.NotePage(20203, new[] { 100003 }, At(0), out why);              // 10.638 页弹出
            Eq("战前成立", c.State, BattleFlowState.PreBattle);

            // 18:51:11.638 / 12.638：NPC 调工具那两秒（真机 22.014 入队）
            Check("11s 采样：仍是战前", !c.NoteSampled(false, false, At(1), out why));
            Eq("状态不变", c.State, BattleFlowState.PreBattle);
            Check("18s 采样：仍是战前", !c.NoteSampled(false, false, At(8), out why));
            Eq("状态不变（关键：这段是空洞）", c.State, BattleFlowState.PreBattle);

            // 真机 28.141 页关 → 30.395 应战窗才弹。中间 28.687 那条攻击落刀。
            // 若战前不是记忆态，这一刻就会漏掉它 —— 本条断言就是整个改动的目的。
            Check("空洞里 28.687 仍是战前（否则那条攻击就放过去了）",
                  c.State == BattleFlowState.PreBattle);
        }

        // ---------------------------------------------------------------- ③
        private static void Group3_ClickConfirms()
        {
            Console.WriteLine("③ 点开战按钮 = 确认，此后页没了也不退出");
            var c = Make();
            string why;

            c.NotePage(20203, new[] { 100003 }, At(0), out why);
            // 真机 18:51:28.123 点了「战斗」（链里点了选项 id=100003）
            c.NoteOptionClicked(100003, At(20), out why);
            Eq("仍战前", c.State, BattleFlowState.PreBattle);
            Check("已确认", c.Confirmed);

            // 点了之后页没了、也没新页（30.395 应战窗还没弹）
            c.NoteSampled(false, false, At(20.5), out why);
            Eq("确认后不因'页没了'退出", c.State, BattleFlowState.PreBattle);

            // 确认后来了一页没有开战按钮的（转场/过场页）也不该退出
            c.NotePage(1450101, new[] { 0 }, At(21), out why);
            Eq("确认后新页也不退出", c.State, BattleFlowState.PreBattle);

            // 但要能真的被战斗接走
            c.NoteBattleStart("WorldUnitBase", At(22), out why);
            Eq("进战斗 ⇒ 战中", c.State, BattleFlowState.InBattle);
            Check("战中不再记确认", !c.Confirmed);
        }

        // ---------------------------------------------------------------- ④
        private static void Group4_ExitConditions()
        {
            Console.WriteLine("④ 退出条件");
            string why;

            // (a) 新页没有开战按钮
            var a = Make();
            a.NotePage(20203, new[] { 100003 }, At(0), out why);
            Check("新页无开战按钮 ⇒ 退出", a.NotePage(22832, new[] { 228321, 228322 }, At(2), out why), why);
            Eq("状态回 Idle", a.State, BattleFlowState.Idle);

            // (b) 战前期间点了非开战按钮 = 婉拒
            var b = Make();
            b.NotePage(21204, new[] { 212042, 212041 }, At(0), out why);   // 切磋确认页
            Eq("切磋页 ⇒ 战前", b.State, BattleFlowState.PreBattle);
            Check("点婉拒 ⇒ 退出", b.NoteOptionClicked(212041, At(3), out why), why);
            Eq("状态回 Idle", b.State, BattleFlowState.Idle);

            // (c) 应战窗关了但没进战斗 = 取消
            var c = Make();
            c.NoteSampled(true, false, At(0), out why);
            Eq("应战窗 ⇒ 战前", c.State, BattleFlowState.PreBattle);
            Check("窗关没打 ⇒ 退出", c.NoteSampled(false, false, At(1), out why), why);
            Eq("状态回 Idle", c.State, BattleFlowState.Idle);

            // (d) 兜底：60 秒一条证据都没有
            var d = Make();
            d.NotePage(20203, new[] { 100003 }, At(0), out why);
            Check("59s 还在", !d.NoteSampled(false, false, At(59), out why));
            Eq("59s 仍战前", d.State, BattleFlowState.PreBattle);
            Check("61s 兜底退出", d.NoteSampled(false, false, At(61), out why), why);
            Eq("状态回 Idle", d.State, BattleFlowState.Idle);

            // (e) 兜底被"新的证据"续期（有证据就不计时）
            //     续期用**页级**证据（50s 时又弹出一页带开战按钮的）——
            //     不能用"应战窗开→关"，那本身就是 (c) 那条退出条件，混在一起就测不出续期。
            var e = Make();
            e.NotePage(20203, new[] { 100003 }, At(0), out why);
            e.NotePage(21204, new[] { 212042, 212041 }, At(50), out why);   // 50s 又有开战页 → 续期
            Check("续期后 100s 仍战前（50+60 未到）", !e.NoteSampled(false, false, At(100), out why));
            Eq("仍战前", e.State, BattleFlowState.PreBattle);
            Check("续期后 111s 才兜底", e.NoteSampled(false, false, At(111), out why), why);

            // (f) 已确认的不会因兜底之外的软条件退出，但兜底照样能收（防永久卡住）
            var f = Make();
            f.NotePage(20203, new[] { 100003 }, At(0), out why);
            f.NoteOptionClicked(100003, At(1), out why);
            Check("确认后兜底仍能收（防永久卡住）", f.NoteSampled(false, false, At(70), out why), why);
            Eq("状态回 Idle", f.State, BattleFlowState.Idle);
        }

        // ---------------------------------------------------------------- ⑤
        private static void Group5_InBattle()
        {
            Console.WriteLine("⑤ 战前 → 战中 → 退出");
            var c = Make();
            string why;

            c.NotePage(20203, new[] { 100003 }, At(0), out why);
            // 真机 18:51:32.349 进入战斗
            Check("battleActive ⇒ 战中", c.NoteSampled(false, true, At(21.7), out why), why);
            Eq("状态=InBattle", c.State, BattleFlowState.InBattle);

            // 战中期间来页、点选项都不该改状态
            Check("战中新页不改状态", !c.NotePage(20403, new[] { 204032, 204033 }, At(30), out why));
            Eq("仍战中", c.State, BattleFlowState.InBattle);
            Check("战中点选项不改状态", !c.NoteOptionClicked(204032, At(31), out why));
            Eq("仍战中", c.State, BattleFlowState.InBattle);

            // 真机 18:53:54 处置页收口、之后 BattleEnd
            Check("battleActive 落 ⇒ 退出", c.NoteSampled(false, false, At(160), out why), why);
            Eq("状态回 Idle", c.State, BattleFlowState.Idle);

            // 重复采样幂等（每帧都会调）
            Check("重复采样不重复触发", !c.NoteSampled(false, false, At(161), out why));

            // 战中时兜底不走（实时电平优先）
            var d = Make();
            d.NoteSampled(false, true, At(0), out why);
            Eq("直接战中", d.State, BattleFlowState.InBattle);
            Check("战中 200s 采样仍战前态不误退", !d.NoteSampled(false, true, At(200), out why));
            Eq("仍战中", d.State, BattleFlowState.InBattle);
        }

        // ---------------------------------------------------------------- ⑤b
        private static void Group5b_EdgeBeforeLevel()
        {
            Console.WriteLine("⑤b 边沿早于电平：IntoBattle 先到、战斗位后亮");
            var c = Make();
            string why;

            // `IntoBattle` 是**前缀**，跑在"战斗真的开起来"之前 —— 此刻 battleActive 还是假的。
            c.NoteBattleStart("IntoBattle(WorldUnitBase, Int32, Action, WorldBattleData)", At(0), out why);
            Eq("IntoBattle ⇒ 战中", c.State, BattleFlowState.InBattle);

            // ★ 紧接着那一帧：电平还没亮。若这里判"战斗结束"，战中态只有一帧寿命 ★
            Check("电平还没亮时不判结束", !c.NoteSampled(false, false, At(0.02), out why));
            Eq("仍是战中（关键：不能一帧就掉）", c.State, BattleFlowState.InBattle);
            Check("再来几帧也不掉", !c.NoteSampled(false, false, At(0.5), out why));
            Eq("仍战中", c.State, BattleFlowState.InBattle);

            // 电平亮了 ⇒ 正常
            c.NoteSampled(false, true, At(1), out why);
            Eq("电平亮后仍战中", c.State, BattleFlowState.InBattle);

            // 打完了、电平落 ⇒ 立刻退出（这条路由"见过电平"标记把关，零秒数）
            Check("见过电平后落 ⇒ 退出", c.NoteSampled(false, false, At(60), out why), why);
            Eq("回 Idle", c.State, BattleFlowState.Idle);

            // 那场仗**根本没打起来**（引擎拒了/只是预演）：电平从没亮过 ⇒ 不猜，等兜底
            var n = Make();
            n.NoteBattleStart("IntoBattle", At(0), out why);
            Check("从没见电平：59s 还不退（不猜'没打'）", !n.NoteSampled(false, false, At(59), out why));
            Eq("仍战中", n.State, BattleFlowState.InBattle);
            Check("从没见过电平：61s 兜底退出", n.NoteSampled(false, false, At(61), out why), why);
            Eq("回 Idle", n.State, BattleFlowState.Idle);
        }

        // ---------------------------------------------------------------- ⑥
        private static void Group6_AcceptIsNotCancel()
        {
            Console.WriteLine("⑥ 接受应战时「窗关」与「进战斗」同一毫秒");
            var c = Make();
            string why;

            c.NotePage(20203, new[] { 100003 }, At(0), out why);
            c.NoteSampled(true, false, At(20), out why);       // 30.395 应战窗弹出
            Eq("战前", c.State, BattleFlowState.PreBattle);

            // 18:51:32.349 同一毫秒：进战斗 + 窗关。先判窗就会读成"取消"。
            Check("同毫秒 ⇒ 判进战斗，不是取消", c.NoteSampled(false, true, At(21.711), out why), why);
            Eq("状态=战中（不是 Idle）", c.State, BattleFlowState.InBattle);
            Check("原因写'已经在打了'", why.Contains("打"), why);

            // 顺序反过来也验证一遍：先给 battleActive 后给窗关（同一帧内两次调用）
            var c2 = Make();
            c2.NoteSampled(true, false, At(0), out why);
            c2.NoteSampled(false, true, At(0), out why);
            Eq("先窗关后战斗（同刻两次调用）仍是战中", c2.State, BattleFlowState.InBattle);
        }

        // ---------------------------------------------------------------- ⑦
        private static void Group7_Grades()
        {
            Console.WriteLine("⑦ 处置表 + 拒绝文案");
            // 会再开一场仗 / 会把玩家挪走 ⇒ 当场拒绝
            Eq("attack", BattleFlowCore.Grade("world_ai_action", "attack"), ToolVerdict.Reject);
            Eq("spar", BattleFlowCore.Grade("world_ai_action", "spar"), ToolVerdict.Reject);
            Eq("teleport", BattleFlowCore.Grade("movement", "teleport"), ToolVerdict.Reject);
            Eq("summon", BattleFlowCore.Grade("movement", "summon"), ToolVerdict.Reject);

            // 与战斗不冲突 ⇒ 排队
            Eq("give", BattleFlowCore.Grade("economy_item", ""), ToolVerdict.Queue);
            Eq("trade", BattleFlowCore.Grade("trade", ""), ToolVerdict.Queue);
            Eq("item_acquire", BattleFlowCore.Grade("item_acquire", ""), ToolVerdict.Queue);
            Eq("social_relation", BattleFlowCore.Grade("social_relation", ""), ToolVerdict.Queue);
            Eq("lun_dao", BattleFlowCore.Grade("world_ai_action", "lun_dao"), ToolVerdict.Queue);
            Eq("shuang_xiu", BattleFlowCore.Grade("world_ai_action", "shuang_xiu"), ToolVerdict.Queue);
            Eq("yao_yue", BattleFlowCore.Grade("world_ai_action", "yao_yue"), ToolVerdict.Queue);
            Eq("chuan_gong", BattleFlowCore.Grade("world_ai_action", "chuan_gong"), ToolVerdict.Queue);
            Eq("travel", BattleFlowCore.Grade("movement", "travel"), ToolVerdict.Queue);

            // 只读 / 未知 ⇒ 放行
            Eq("inspect_unit", BattleFlowCore.Grade("inspect_unit", ""), ToolVerdict.Pass);
            Eq("search_units", BattleFlowCore.Grade("search_units", ""), ToolVerdict.Pass);
            Eq("query_world", BattleFlowCore.Grade("query_world", ""), ToolVerdict.Pass);
            // 捎话：不碰世界（只往别人的账本里追加一句话）⇒ 既不拒也不排队，当场放行。
            // 排队会把它拖到战斗之后才投递 —— 时序错，还可能因链断而丢掉这句话。
            Eq("chuan_hua", BattleFlowCore.Grade("social_relation", "chuan_hua"), ToolVerdict.Pass);
            Eq("未知工具", BattleFlowCore.Grade("whatever", ""), ToolVerdict.Pass);

            // 文案三要素：发生了什么 / 为什么不行 / 别重试
            string atkPre = BattleFlowCore.RejectText("world_ai_action", "attack", BattleFlowState.PreBattle);
            Check("attack 文案说'马上要开打'", atkPre.Contains("开打"), atkPre);
            Check("attack 文案说'没有执行'", atkPre.Contains("没有执行"), atkPre);
            Check("attack 文案说'别重复'", atkPre.Contains("别重复"), atkPre);

            string atkIn = BattleFlowCore.RejectText("world_ai_action", "attack", BattleFlowState.InBattle);
            Check("战中文案说'正在打'", atkIn.Contains("正在打"), atkIn);
            Check("战中与战前文案不同", atkIn != atkPre, atkIn);

            Check("spar 文案点明切磋", BattleFlowCore.RejectText("world_ai_action", "spar", BattleFlowState.PreBattle)
                  .Contains("切磋"));
            Check("teleport 文案点明挪走",
                  BattleFlowCore.RejectText("movement", "teleport", BattleFlowState.PreBattle).Contains("挪"));
            Check("summon 文案点明召唤",
                  BattleFlowCore.RejectText("movement", "summon", BattleFlowState.PreBattle).Contains("召"));
        }

        // ---------------------------------------------------------------- ⑧
        /// <summary>
        /// 点击作用域（`ClickScope`，10-25 复核新增）。
        ///
        /// 它是**唯一会"学错东西"的地方**：学错一枚编号不是"少挡一次"，而是那一页此后
        /// 每次弹出来都进入战前 ⇒ 攻击/切磋/传送/召唤被**持续误拒**，而且落盘后跨存档、
        /// 跨重启都在。所以这一组盯的是"什么时候**不许**学"。
        /// </summary>
        private static void Group8_ClickScope()
        {
            Console.WriteLine("⑧ 点击作用域（学习归因 + 泄漏自愈）");

            // (a) 正常一次点击：prefix 开、同一个调用栈里读、postfix 关
            var s = new ClickScope();
            int prev = s.Enter(212042, 100);
            Eq("进入后深度=1", s.Depth, 1);
            int id;
            Eq("同一帧 ⇒ 命中", s.OptionInScope(100, out id), ClickScope.Probe.Hit);
            Eq("命中的是被点的那一枚", id, 212042);
            s.Exit(prev);
            Eq("退出后深度=0", s.Depth, 0);
            Eq("退出后不再命中", s.OptionInScope(100, out id), ClickScope.Probe.Outside);

            // (b) 泄漏：postfix 没跑到（引擎在 ClickOption 里抛了异常）⇒ 下一帧必须**不认**
            //     这是整组最重要的一条：不判帧号的话，下一帧"引擎自己发起的进副本命令"
            //     会记到 212042 上 —— 那枚选项从此被当成开战按钮，永久误挡。
            var leak = new ClickScope();
            leak.Enter(212042, 100);                       // postfix 永不执行
            Eq("泄漏后下一帧 ⇒ 过期（不学）", leak.OptionInScope(101, out id), ClickScope.Probe.Expired);
            Eq("过期时不给出编号", id, 0);
            Eq("过期顺手把深度清零", leak.Depth, 0);
            Eq("自愈之后再问就是 Outside", leak.OptionInScope(101, out id), ClickScope.Probe.Outside);
            // 关键：泄漏的作用域**绝不能**被当成合法归属
            Check("泄漏的作用域不产生可学编号", id == 0);

            // (c) 同帧的引擎自发命令仍算"在作用域里"（同步调用的事实不变）
            var same = new ClickScope();
            same.Enter(100003, 7);
            Eq("同一帧仍命中（同步栈的正当归属）", same.OptionInScope(7, out id), ClickScope.Probe.Hit);

            // (d) 嵌套（正常不出现，但写成计数是为了万一也不乱）
            //      注意配对：`Exit` 要还回**对应那次 `Enter`** 的返回值（Harmony 的 `__state`
            //      天然配对：同一对 prefix/postfix 才拿到同一个值）。
            var n = new ClickScope();
            int p1 = n.Enter(1, 5);          // 外层（prev=0）
            int p2 = n.Enter(2, 5);          // 内层（prev=1）
            Eq("内层 prev 是外层编号", p2, 1);
            Eq("嵌套内层可见", n.OptionInScope(5, out id), ClickScope.Probe.Hit);
            Eq("内层是后来那个", id, 2);
            n.Exit(p2);
            Eq("退一层回到外层", n.OptionInScope(5, out id), ClickScope.Probe.Hit);
            Eq("外层是先前那个", id, 1);
            n.Exit(p1);
            Eq("全退干净", n.Depth, 0);

            // (e) 帧号 0 是合法帧（`Time.frameCount` 从 0 起）—— 不能被当成"未设置"
            var z = new ClickScope();
            z.Enter(9, 0);
            Eq("帧号 0 也算同一帧", z.OptionInScope(0, out id), ClickScope.Probe.Hit);
            Eq("帧号 0 的编号正确", id, 9);

            // (f) 没点过任何选项时，引擎自发起事件一律不学
            var e = new ClickScope();
            Eq("空作用域 ⇒ Outside", e.OptionInScope(1, out id), ClickScope.Probe.Outside);
            Eq("空作用域不给编号", id, 0);

            // (f2) 泄漏之后又点了一次（新帧）：不许把那层泄漏"还原"成活作用域
            //      机制：`Exit` 的语义是"把 Enter 还回来的值放回去"，若外层是上个帧泄漏下来的编号，
            //      它就会被还原 ⇒ 同一帧后面来的引擎自发命令记到那枚无关选项上（又变回"学错"）。
            var re = new ClickScope();
            re.Enter(240091, 1000);                       // 泄漏（postfix 没跑到）
            int rp = re.Enter(212042, 2000);              // 新帧又点了一枚
            Eq("新帧进入时先清掉泄漏层（深度 1 而不是 2）", re.Depth, 1);
            Eq("还回来的 prev 是 0（泄漏层没被当外层）", rp, 0);
            re.Exit(rp);
            Eq("退出后不残留", re.OptionInScope(2000, out id), ClickScope.Probe.Outside);
            Eq("泄漏的编号没有被还原成活作用域", id, 0);

            // (g) 在作用域里、但那枚选项的编号读不到（`ConfDramaOptionsItem.id` 漏读）
            //     —— 与 Outside **分开记**：一个是"根本没人点"，一个是"点了但字段读不到"，
            //     合并就查不出配表字段改名那类故障。两种都**不学**。
            var noId = new ClickScope();
            int np = noId.Enter(0, 30);                 // prefix 照样开作用域（保证配对）
            Eq("读不到编号 ⇒ NoId（不是 Outside）", noId.OptionInScope(30, out id), ClickScope.Probe.NoId);
            Eq("NoId 也不给出编号（所以不会被学）", id, 0);
            noId.Exit(np);
            Eq("退出后照常 Outside", noId.OptionInScope(30, out id), ClickScope.Probe.Outside);
        }
    }
}
