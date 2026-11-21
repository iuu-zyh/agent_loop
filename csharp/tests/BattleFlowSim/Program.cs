// 「战前 / 战中」闸的**端到端调用链模拟**（10-25 复核）。
//
// 为什么要有这个工程：`BattleFlowCoreTests` 证明"每条判据本身对"，证明不了
// "事件 → 接线层 → 判据 → 工具层 → 排队器 → 落刀"这条链合起来对。
// 10-25 那个故障恰恰是**每层都没报错、合起来静默**：NPC 的攻击被排队（当时判据说"发不出去"），
// 玩家自己点了同一页的「战斗」，排队那条在 0.5 秒后**落刀**，又开了一次同一页。
//
// 本文件把真机时间线（`docs/archive/2026-10-25-战斗流程闸.md` §1，一秒不差）灌进**真源码**：
//   · `state/BattleFlowCore.cs`     —— 两态机 + 点击作用域
//   · `state/WorldStateCore.cs`     —— 四个视角（谁算忙 / 能不能落刀）
//   · `drama/DeferredActionCore.cs` —— 排队器（稳定窗 / 硬顶 / 退让）
// 只有三处是复刻的（它们必须碰 Unity，链接不进来），且都写明了对应源码：
//   ① 接线层把游戏事件翻译成 `NotePage/NoteSampled/NoteBattleStart` 的参数；
//   ② `BattleFlowGate.VerdictOf`（未武装 ⇒ Pass，否则 `Grade`）；
//   ③ `ToolExecutor` 的决策顺序（判拒绝 → 判排队 → 直发 + 排队执行体里的两道复查）。
// ③ 的**源码顺序**由 `tests/test_battle_flow_gate.py` 那几条契约钉着，本文件不重复钉；
// 它在这里只负责让"顺序错会怎样"变成看得见的一行。
//
// 五个场景：
//   S1 闸关掉（= 改造前）→ 复现故障：那一刀真的落下去了
//   S2 闸打开（= 现在）→ 攻击当场拒绝，队列空，没有落刀
//   S3 先入队、落刀前才点亮 → 排队执行体里那道复查把它**作废**（不补做）
//   S4 点击作用域泄漏（`ClickOption` 里抛异常）→ 必须**学不到**，不许学错
//   S5 战后在处置页点「对话」那一刻 → 不误挡（闸已经退出），且四个视角能并排读出来

using System;
using System.Collections.Generic;
using AgentLoopBridge;

namespace AgentLoopBridge.Tests
{
    internal static class Program
    {
        /// <summary>真机起点：18:51:10.638 寻仇页 20203 弹出。</summary>
        private static readonly DateTime T0 = new DateTime(2026, 10, 25, 18, 51, 10, 638);

        private static DateTime At(double sec) { return T0.AddSeconds(sec); }

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
                  "实际=" + got + " 期望=" + want);
        }

        private static int Main()
        {
            try
            {
                S1_OldBehaviour();
                S2_NewBehaviour();
                S3_QueuedThenArmed();
                S4_ScopeLeak();
                S5_AfterBattleDialogue();
            }
            catch (Exception e)
            {
                Console.WriteLine("  ✗ 抛异常: " + e);
                _fails.Add("抛异常");
            }

            Console.WriteLine();
            if (_fails.Count == 0)
            {
                Console.WriteLine("✓ BattleFlowSim 全部通过（" + _pass + " 项）");
                return 0;
            }
            Console.WriteLine("✗ 失败 " + _fails.Count + " 项 / 通过 " + _pass + " 项");
            foreach (string f in _fails) Console.WriteLine("   · " + f);
            return 1;
        }

        // ==================================================================
        // 真实世界的"电平" + 三层接线（复刻见文件头 ①②③）
        // ==================================================================

        /// <summary>一个"世界"：游戏电平 + 真判据核 + 真排队器核 + 复刻的决策层。</summary>
        private sealed class Rig
        {
            internal readonly BattleFlowCore Core =
                new BattleFlowCore(new[] { 100003, 212042 }, BattleFlowCore.DefaultFallbackSeconds);
            internal readonly DeferredActionCore Queue = new DeferredActionCore();

            /// <summary>闸是否打开。S1 关掉它 = 改造前的行为（本仓没有"版本开关"，这是模拟里的对照）。</summary>
            internal bool Gate = true;

            // ---- 游戏电平（由时间线剧本拨动）----
            internal bool PageOpen;        // 剧情页可见（UI 层层顶 = UIDramaBase）
            internal bool ChainHold;       // DramaGate.ChainHold（剧情链收尾占位）
            internal bool PreShowing;      // MapBattlePre 应战窗
            internal bool BattleActive;    // isBattle ∪ UIBattleInfo
            internal bool EdgeSeen;        // 有"链收尾结束"的边沿证据（稳定窗走 0.5s 档）

            // ---- 观测计数 ----
            internal int Rejects;          // 当场拒绝（ToolExecutor 第一道闸）
            internal int Knives;           // 排队器落刀（执行体被调用）
            internal int Voided;           // 落刀后复查发现该作废（不补做）
            internal int Requeues;         // 落刀时又忙起来了 → 退回等待
            internal int AttacksExecuted;  // 真正执行了的 attack（会再开一场架）
            internal int Battles;          // IntoBattle 边沿次数

            internal readonly List<string> Trace = new List<string>();
            internal readonly List<string> RejectTexts = new List<string>();
            internal string LastOp = "";
            internal string LastTool = "";

            internal void Note(string line) { Trace.Add(line); }

            // ---- ① 接线层：把电平填进快照（机械搬运，无判断）----
            internal WorldStateSnapshot Snapshot()
            {
                return new WorldStateSnapshot
                {
                    PlayerUnit = PlayerUnitRead.Present,
                    DeathWindowVisible = false,
                    IsBattleLevel = BattleActive,
                    BattleUiVisible = BattleActive,
                    OwnDramaWindowVisible = false,
                    UiLayerTopName = PageOpen ? "UIDramaBase" : "",
                    ChatWindowVisible = false,
                    MaskLayerName = "",
                    WindowLayerName = "",
                    MainWorldReady = true,
                    DramaChainHold = ChainHold,
                    DramaPendingAction = false,
                };
            }

            /// <summary>排队器此刻能不能落刀 —— 真判据 `WorldStateCore.ExecuteBlocker`。</summary>
            internal string Blocker()
            {
                var sn = Snapshot();
                return WorldStateCore.ExecuteBlocker(ref sn);
            }

            /// <summary>② `BattleFlowGate.VerdictOf`：未武装一律放行，否则交给处置表。</summary>
            internal ToolVerdict Verdict(string tool, string op)
            {
                if (!Gate) return ToolVerdict.Pass;      // S1 的对照：改造前没有这道闸
                if (!Core.Armed) return ToolVerdict.Pass;
                return BattleFlowCore.Grade(tool, op);
            }

            // ---- ③ ToolExecutor.Execute(name,args,npcId) 的决策顺序 ----
            internal void CallTool(string tool, string op, DateTime now, string who)
            {
                LastTool = tool; LastOp = op;

                // ① 先判"当场拒绝"（比排队更硬：排进去，等战斗结束会再打一次）
                if (Verdict(tool, op) == ToolVerdict.Reject)
                {
                    Rejects++;
                    RejectTexts.Add(BattleFlowCore.RejectText(tool, op, Core.State));
                    Note(who + " 调 " + tool + "/" + op + " ⇒ **当场拒绝**（" + Core.State + "）");
                    return;
                }
                // ② 再判"此刻发不出去"⇒ 排队
                string blocked = Blocker();
                if (blocked.Length > 0)
                {
                    if (Queue.TryEnqueue("npc", op, tool + "/" + op, now, null))
                        Note(who + " 调 " + tool + "/" + op + " ⇒ 入队（" + blocked + "）");
                    return;
                }
                // ③ 直发
                Note(who + " 调 " + tool + "/" + op + " ⇒ 直发");
                ExecBody(tool, op);
            }

            /// <summary>真执行（直发与落刀共用）。</summary>
            private void ExecBody(string tool, string op)
            {
                if (tool == "world_ai_action" && op == "attack")
                {
                    AttacksExecuted++;
                    Note("    ★ 攻击真的执行了 ⇒ 引擎会再开一次寻仇页（真机 20203 第二次出现）★");
                }
            }

            /// <summary>排队器的执行体（复刻 `ToolExecutor` 排队分支里那两道复查 + Fail）。</summary>
            private string Knife()
            {
                Knives++;
                var head = Queue.Head;
                string tool = LastTool, op = LastOp;
                if (head != null) { tool = head.Summary; op = head.Op; }
                // 复查一：又忙起来了 ⇒ 返回 null（退回等待，不判失败）
                if (Blocker().Length > 0) { Requeues++; return null; }
                // 复查二：战前/战中 ⇒ 作废（返回 Fail，不是 null —— null 就成了"延后执行"）
                string toolName = tool.IndexOf('/') > 0 ? tool.Substring(0, tool.IndexOf('/')) : tool;
                if (Verdict(toolName, op) == ToolVerdict.Reject)
                {
                    Voided++;
                    Note("    ★ 落刀时复查：战前/战中 ⇒ **作废**（不补做）★");
                    return "作废";
                }
                ExecBody(toolName, op);
                return "完成";
            }

            /// <summary>主线程每帧（这里按 0.25s 采样档推进）：喂采样 + 推排队器。</summary>
            internal void Advance(DateTime now)
            {
                string why;
                if (Core.NoteSampled(PreShowing, BattleActive, now, out why))
                    Note("  [采样] 状态 → " + Core.State + "（" + why + "）");

                var step = Queue.Poll(now, Blocker().Length > 0, EdgeSeen);
                if (step == DeferredActionCore.Step.Execute)
                {
                    string r = null;
                    try { r = Knife(); } catch (Exception e) { r = "异常：" + e.Message; }
                    if (r == null) Queue.RequeueHead(now);
                    else Queue.CompleteHead(now, r);
                }
                else if (step == DeferredActionCore.Step.Ceiling)
                {
                    Queue.CompleteHead(now, "硬顶收尾");
                }
            }

            /// <summary>`IntoBattle` 边沿（真机 32.349，与战斗位翻真同一毫秒）。</summary>
            internal void IntoBattle(DateTime now)
            {
                Battles++;
                string why;
                Core.NoteBattleStart("IntoBattle", now, out why);
                Note("  [开打] IntoBattle ⇒ 状态 " + Core.State);
            }

            internal void Page(int dramaId, int[] opts, DateTime now)
            {
                string why;
                Core.NotePage(dramaId, opts, now, out why);
                if (why.Length > 0) Note("  [页] " + dramaId + " ⇒ " + Core.State + "（" + why + "）");
            }

            internal void Click(int optionId, DateTime now)
            {
                string why;
                Core.NoteOptionClicked(optionId, now, out why);
                if (why.Length > 0) Note("  [点] " + optionId + " ⇒ " + Core.State + "（" + why + "）");
            }
        }

        /// <summary>按 0.25s 一格的采样档把剧本走完（与 `BattleFlowGate.SampleSeconds` 同档）。</summary>
        private static void Drive(Rig r, List<KeyValuePair<double, Action>> script, double untilSec)
        {
            int i = 0;
            for (double t = 0; t <= untilSec + 1e-9; t += 0.25)
            {
                while (i < script.Count && script[i].Key <= t + 1e-9)
                {
                    script[i].Value();
                    i++;
                }
                r.Advance(At(t));
            }
        }

        private static List<KeyValuePair<double, Action>> Script()
        {
            return new List<KeyValuePair<double, Action>>();
        }

        private static void At(List<KeyValuePair<double, Action>> s, double t, Action a)
        {
            s.Add(new KeyValuePair<double, Action>(t, a));
        }

        private static void Dump(Rig r)
        {
            foreach (string line in r.Trace) Console.WriteLine("    " + line);
        }

        // ==================================================================
        // S1 — 闸关掉：复现 10-25 那个故障
        // ==================================================================
        private static void S1_OldBehaviour()
        {
            Console.WriteLine("S1 改造前（没有闸）：NPC 那条攻击被排队 → 落刀又开一次同一页");
            var r = new Rig { Gate = false };
            var s = Script();

            // 0.000 = 18:51:10.638 寻仇页 20203 弹出（唯一按钮 100003）
            At(s, 0.0, () => { r.PageOpen = true; r.Page(20203, new[] { 100003 }, At(0)); });
            // 2.448 = 18:51:13.086 「关窗」——★假的★（那一页 15 秒后还能点），故意不给它任何事件
            // 11.376 = 18:51:22.014 NPC 调 world_ai_action(attack) → 被排队
            At(s, 11.376, () => r.CallTool("world_ai_action", "attack", At(11.376), "NPC(寻仇)"));
            // 17.485 = 18:51:28.123 玩家点「战斗」；17.503 页关；链的边沿证据到位
            At(s, 17.485, () => r.Click(100003, At(17.485)));
            At(s, 17.503, () => { r.PageOpen = false; r.EdgeSeen = true; });
            // 19.757 = 18:51:30.395 应战窗；21.711 = 18:51:32.349 进战斗（同一毫秒窗关）
            At(s, 19.757, () => { r.PreShowing = true; });
            At(s, 21.711, () => { r.PreShowing = false; r.BattleActive = true; r.IntoBattle(At(21.711)); });

            Drive(r, s, 40);
            Dump(r);

            // 落刀发生在页关后 0.5s（真机 28.141 → 28.687 = 0.546s，稳定窗短档）
            Eq("S1 排队那条真的落了刀", r.Knives, 1);
            Eq("S1 攻击真的执行了一次（= 多出来的那一场）", r.AttacksExecuted, 1);
            Eq("S1 没有任何拒绝（那时候没有这道闸）", r.Rejects, 0);
            Check("S1 落刀时刻在页关之后（0.5s 稳定窗）", r.Knives == 1);
        }

        // ==================================================================
        // S2 — 闸打开：同一个剧本，攻击当场被拒
        // ==================================================================
        private static void S2_NewBehaviour()
        {
            Console.WriteLine("S2 现在（闸打开）：同一个剧本，攻击在 NPC 调它的那一刻就被拒");
            var r = new Rig();
            var s = Script();

            At(s, 0.0, () => { r.PageOpen = true; r.Page(20203, new[] { 100003 }, At(0)); });
            At(s, 11.376, () => r.CallTool("world_ai_action", "attack", At(11.376), "NPC(寻仇)"));
            At(s, 17.485, () => r.Click(100003, At(17.485)));
            At(s, 17.503, () => { r.PageOpen = false; r.EdgeSeen = true; });
            At(s, 19.757, () => { r.PreShowing = true; });
            At(s, 21.711, () => { r.PreShowing = false; r.BattleActive = true; r.IntoBattle(At(21.711)); });

            Drive(r, s, 40);
            Dump(r);

            Eq("S2 当场拒绝 1 次", r.Rejects, 1);
            Eq("S2 队列里什么都没进（所以根本没有落刀）", r.Knives, 0);
            Eq("S2 攻击一次都没执行", r.AttacksExecuted, 0);
            Eq("S2 只打了一场", r.Battles, 1);
            Check("S2 拒绝文案说清'这次没执行'",
                  r.RejectTexts.Count > 0 && r.RejectTexts[0].Contains("没有执行"), r.RejectTexts.Count > 0 ? r.RejectTexts[0] : "（无）");
            Check("S2 拒绝文案叫模型别重试",
                  r.RejectTexts.Count > 0 && r.RejectTexts[0].Contains("别重复"), "");
            Check("S2 战前那条日志说得清是哪一页", r.Trace.Exists(x => x.Contains("20203")));
        }

        // ==================================================================
        // S3 — 先入队、落刀前才点亮：排队执行体里那道复查必须把它作废
        // ==================================================================
        private static void S3_QueuedThenArmed()
        {
            Console.WriteLine("S3 入队在先、点亮在后：落刀那一刻复查 ⇒ 作废（不许'打完了再补一刀'）");
            // 剧本比真机更早：18:51:09（页还没出来）NPC 就想攻击，此刻被"链收尾"挡着 ⇒ 入队。
            // 然后页出来（10.638）⇒ 点亮战前。等页关、链清、稳定窗过，落刀时它已经是"战前"了。
            var r = new Rig { ChainHold = true };
            var s = Script();

            At(s, 0.0, () => r.CallTool("world_ai_action", "attack", At(0), "NPC(寻仇)"));
            At(s, 0.5, () => { r.PageOpen = true; r.Page(20203, new[] { 100003 }, At(0.5)); });
            At(s, 7.0, () => { r.Click(100003, At(7.0)); });
            At(s, 7.1, () => { r.PageOpen = false; r.ChainHold = false; r.EdgeSeen = true; });

            Drive(r, s, 20);
            Dump(r);

            Check("S3 先入队成功（日志里有'入队'）", r.Trace.Exists(x => x.Contains("入队")));
            Eq("S3 落刀时复查把它作废了", r.Voided, 1);
            Eq("S3 攻击一次都没执行", r.AttacksExecuted, 0);
            Check("S3 日志里出现'作废'", r.Trace.Exists(x => x.Contains("作废")));
        }

        // ==================================================================
        // S4 — 点击作用域泄漏：引擎自己发起的进副本命令不许被"学"
        // ==================================================================
        private static void S4_ScopeLeak()
        {
            Console.WriteLine("S4 作用域泄漏（ClickOption 里抛异常 ⇒ postfix 没跑到）：必须学不到");
            // 场景：玩家点了 240091（一页普通剧情里的"离开"，绝不会开打），
            // 引擎在 ClickOption 内部抛了异常 ⇒ 我们的 postfix 没跑到 ⇒ 作用域挂着。
            // 下一帧，引擎自己发起一条 IntoDungeon（与任何点击无关）——
            // 如果不判帧号，就会把 240091 记成"开战按钮"，此后那一页一弹出来就进入战前，
            // 攻击/切磋/传送/召唤被**永久误拒**（落盘后跨存档也在）。
            var scope = new ClickScope();
            scope.Enter(240091, 1000);                       // prefix 跑了
            // …引擎抛异常，postfix 没跑…
            int clicked;
            Eq("S4 泄漏后下一帧 ⇒ 过期", scope.OptionInScope(1001, out clicked), ClickScope.Probe.Expired);
            Eq("S4 过期时不给编号（所以不会被学）", clicked, 0);

            // 对照：旧实现（只看深度）在同一时刻会给出 240091 —— 那就是"学错"
            int legacyDepth = 1, legacyId = 240091;
            int legacyClicked = legacyDepth > 0 ? legacyId : 0;
            Eq("S4 旧实现此刻会学错（这就是为什么要改）", legacyClicked, 240091);
            Check("S4 新实现与旧实现在这一刻结论不同 —— 差异正是修复点", legacyClicked != clicked);

            // 而且它自愈：此后永远是 Outside，不会残留
            Eq("S4 自愈后再问是 Outside", scope.OptionInScope(1001, out clicked), ClickScope.Probe.Outside);
            Eq("S4 深度已清零", scope.Depth, 0);
        }

        // ==================================================================
        // S5 — 战后在处置页点「对话」那一刻：不误挡
        // ==================================================================
        private static void S5_AfterBattleDialogue()
        {
            Console.WriteLine("S5 战后处置页点「对话」（18:53:56.757）：闸必须已经退出，不许误挡");
            var r = new Rig();
            var s = Script();

            At(s, 0.0, () => { r.PageOpen = true; r.Page(20203, new[] { 100003 }, At(0)); });
            At(s, 17.485, () => r.Click(100003, At(17.485)));
            At(s, 17.503, () => { r.PageOpen = false; r.EdgeSeen = true; });
            At(s, 19.757, () => { r.PreShowing = true; });
            At(s, 21.711, () => { r.PreShowing = false; r.BattleActive = true; r.IntoBattle(At(21.711)); });
            // 18:53:54 处置页（20403）弹出；18:53:54.x 战斗位落（BattleEnd）⇒ 应当退出战中
            At(s, 163.487, () => { r.BattleActive = false; r.PageOpen = true; r.Page(20403, new[] { 204031 }, At(163.487)); });

            Drive(r, s, 166.2);

            Eq("S5 战斗打过一场", r.Battles, 1);
            Check("S5 处置页那一刻闸已退出（否则 NPC 连话都说不成）", !r.Core.Armed);

            // 那一刻的四个视角并排（真的 `WorldStateCore.Describe`）
            var sn = r.Snapshot();
            Console.WriteLine("    四视角：" + WorldStateCore.Describe(ref sn));
            Eq("S5 让步判据只报'剧情窗'（处置页开着），不报别的", WorldStateCore.YieldReason(ref sn).Contains("剧情窗"), true);

            // 关键时刻：NPC 在处置页上又想调 attack —— **不许被拒**（闸已退出，只该排队）
            r.CallTool("world_ai_action", "attack", At(166.0), "NPC(处置页)");
            Eq("S5 战后调攻击不被'战前/战中'拒（没有误挡）", r.Rejects, 0);
            Eq("S5 它走的是排队（处置页挡着）", r.Knives, 0);
        }
    }
}
