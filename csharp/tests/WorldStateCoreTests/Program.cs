// 「此刻能不能做事」四个视角（WorldStateCore）的单元测试。
//
// 为什么要有这个工程：这四个视角出错**全是静默的**。
//   · Yield 里误加"对话窗" ⇒ 模型在聊天里发起动作时排队器永远让路，
//     动作被拖到玩家关窗（他可能聊很久）—— 看起来就是"NPC 不理我"；
//   · Busy 里少一条"对话窗" ⇒ 玩家正跟 A 聊天，B 直接弹确认窗打断他；
//   · Execute 里少一条"不在主世界" ⇒ 弹窗沉进游戏系统队列不出队，90 秒后静默超时；
//   · SpeechBlock 里误加"战斗中" ⇒ 战斗中传音全被丢掉（本该允许，只弹顶部横幅）。
// 四个视角**必须不同**，而"不同"这件事只有断言能守住 —— 谁都能顺手把它们合并。
//
// 六组：
//   ① SpeechBlock：只拦"根本没进世界 / 玩家死了"；战斗**不在**此列
//   ② BusyReason：七条成员 + 顺序（isBattle 先，战斗界面单列，不是取或）
//   ③ YieldReason：刻意**不含**对话窗与挂起动作
//   ④ ExecuteBlocker：五条 + 顺序（链收尾最先、战斗中取或、末位才是主世界）
//   ⑤ 四视角的差异矩阵（同一份快照，四个答案各不相同 —— 这就是"不能合并"的证据）
//   ⑥ IsPlayerBusy / Describe 的形态

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

        /// <summary>全空闲的快照（在世界里、什么界面都没开、没挂起、没在打）。</summary>
        private static WorldStateSnapshot Idle()
        {
            return new WorldStateSnapshot
            {
                PlayerUnit = PlayerUnitRead.Present,
                DeathWindowVisible = false,
                IsBattleLevel = false,
                BattleUiVisible = false,
                DramaPendingAction = false,
                DramaChainHold = false,
                OwnDramaWindowVisible = false,
                UiLayerTopName = "",
                ChatWindowVisible = false,
                MaskLayerName = "",
                WindowLayerName = "",
                MainWorldReady = true,
            };
        }

        private static string Speech(WorldStateSnapshot sn) { return WorldStateCore.SpeechBlock(ref sn); }
        private static string Busy(WorldStateSnapshot sn) { return WorldStateCore.BusyReason(ref sn); }
        private static string Yield(WorldStateSnapshot sn) { return WorldStateCore.YieldReason(ref sn); }
        private static string Exec(WorldStateSnapshot sn) { return WorldStateCore.ExecuteBlocker(ref sn); }

        private static int Main()
        {
            try
            {
                Group1_Speech();
                Group2_Busy();
                Group3_Yield();
                Group4_Execute();
                Group5_FourViewsDiffer();
                Group6_Wrappers();
            }
            catch (Exception e)
            {
                Console.WriteLine("  ✗ 抛异常: " + e);
                _fails.Add("抛异常");
            }

            Console.WriteLine();
            if (_fails.Count == 0)
            {
                Console.WriteLine("✓ WorldStateCore 全部通过（" + _pass + " 项）");
                return 0;
            }
            Console.WriteLine("✗ 失败 " + _fails.Count + " 项 / 通过 " + _pass + " 项");
            foreach (string f in _fails) Console.WriteLine("   · " + f);
            return 1;
        }

        // ---------------------------------------------------------------- ①
        private static void Group1_Speech()
        {
            Console.WriteLine("① SpeechBlock：只拦'根本没进世界 / 玩家死了'");
            var sn = Idle();
            Eq("全空闲 ⇒ 能说", Speech(sn), "");

            sn = Idle(); sn.PlayerUnit = PlayerUnitRead.Absent;
            Eq("没进世界", Speech(sn), "playerUnit=null（未进世界）");

            sn = Idle(); sn.PlayerUnit = PlayerUnitRead.Error;
            Eq("读异常（与'没进世界'是两句不同的话）", Speech(sn), "playerUnit 读取异常");

            sn = Idle(); sn.DeathWindowVisible = true;
            Eq("玩家死亡/复活中", Speech(sn), "UIMapDie 可见（玩家死亡/复活中）");

            // ★战斗不在此列★ 战斗中允许传音（顶部横幅、非模态、不挡操作）
            sn = Idle(); sn.IsBattleLevel = true; sn.BattleUiVisible = true;
            Eq("战斗中仍能开口（★别加这条★）", Speech(sn), "");

            // 剧情窗也不该拦"开口"——那只影响"能不能做动作"
            sn = Idle(); sn.UiLayerTopName = "UIDramaDialogue"; sn.OwnDramaWindowVisible = true;
            Eq("剧情窗不拦开口", Speech(sn), "");
        }

        // ---------------------------------------------------------------- ②
        private static void Group2_Busy()
        {
            Console.WriteLine("② BusyReason：七条成员 + 顺序");
            var sn = Idle();
            Eq("全空闲 ⇒ 不忙", Busy(sn), "");

            sn = Idle(); sn.IsBattleLevel = true;
            Eq("战斗位", Busy(sn), "world.battle.isBattle（战斗中）");

            // 顺序①：战斗位优先于挂起动作
            sn = Idle(); sn.IsBattleLevel = true; sn.DramaPendingAction = true;
            Eq("战斗位先于挂起动作", Busy(sn), "world.battle.isBattle（战斗中）");

            sn = Idle(); sn.DramaPendingAction = true;
            Eq("我方挂起动作", Busy(sn), "DramaGate.HasPending");

            sn = Idle(); sn.UiLayerTopName = "UIDramaDialogue";
            Eq("游戏剧情窗（带名字）", Busy(sn), "剧情窗开着（UIDramaDialogue）");

            sn = Idle(); sn.OwnDramaWindowVisible = true;
            Eq("我方确认窗优先于层顶名", Busy(sn), "我方确认窗 UICustomDramaDyn 可见");
            sn = Idle(); sn.OwnDramaWindowVisible = true; sn.UiLayerTopName = "UIDramaDialogue";
            Eq("两者都在 ⇒ 报我方确认窗（顺序）", Busy(sn), "我方确认窗 UICustomDramaDyn 可见");

            // 战斗界面**单列**：不是与 isBattle 取或，而是各自一条
            sn = Idle(); sn.BattleUiVisible = true;
            Eq("战斗界面单列", Busy(sn), "UIBattleInfo 可见");

            sn = Idle(); sn.ChatWindowVisible = true;
            Eq("对话窗（用户原话那条）", Busy(sn),
               "对话窗开着（UIChatAi）：正在与 NPC 对话，不弹窗不行动");

            sn = Idle(); sn.MaskLayerName = "UIMessageBox";
            Eq("Mask 层遮罩", Busy(sn), "Mask 层有模态遮罩：UIMessageBox");

            sn = Idle(); sn.WindowLayerName = "UINPCInfo";
            Eq("Window 层窗口", Busy(sn), "Window 层有窗口：UINPCInfo");

            // 顺序②：Mask 先于 Window
            sn = Idle(); sn.MaskLayerName = "M"; sn.WindowLayerName = "W";
            Eq("Mask 先于 Window", Busy(sn), "Mask 层有模态遮罩：M");

            // 顺序③：对话窗先于 Mask
            sn = Idle(); sn.ChatWindowVisible = true; sn.MaskLayerName = "M";
            Check("对话窗先于 Mask", Busy(sn).StartsWith("对话窗开着"), Busy(sn));

            // ★Busy 不含"链收尾 / 不在主世界"★ 那两条是排队器的语义
            sn = Idle(); sn.DramaChainHold = true;
            Eq("链收尾不算'忙'（那是排队器的判据）", Busy(sn), "");
            sn = Idle(); sn.MainWorldReady = false;
            Eq("不在主世界不算'忙'（同上）", Busy(sn), "");
        }

        // ---------------------------------------------------------------- ③
        private static void Group3_Yield()
        {
            Console.WriteLine("③ YieldReason：刻意不含对话窗与挂起动作");
            var sn = Idle();
            Eq("全空闲 ⇒ 不用让", Yield(sn), "");

            sn = Idle(); sn.UiLayerTopName = "UIDramaDialogue";
            Eq("剧情窗 ⇒ 让", Yield(sn), "剧情窗开着（UIDramaDialogue）");

            sn = Idle(); sn.OwnDramaWindowVisible = true;
            Eq("我方确认窗 ⇒ 让", Yield(sn), "我方确认窗 UICustomDramaDyn 可见");

            sn = Idle(); sn.MaskLayerName = "UIMessageBox";
            Eq("Mask ⇒ 让", Yield(sn), "Mask 层有模态遮罩：UIMessageBox");

            sn = Idle(); sn.WindowLayerName = "UINPCInfo";
            Eq("Window ⇒ 让", Yield(sn), "Window 层有窗口：UINPCInfo");

            // ★★ 三个"刻意不含"（改动这几条前先读类头那张表）★★
            sn = Idle(); sn.ChatWindowVisible = true;
            Eq("★对话窗不算让路★（直发路径就是在对话窗开着时弹窗的）", Yield(sn), "");

            sn = Idle(); sn.DramaPendingAction = true;
            Eq("★我方挂起动作不算让路★（那条由排队器自己的阻塞项管）", Yield(sn), "");

            sn = Idle(); sn.IsBattleLevel = true; sn.BattleUiVisible = true;
            Eq("战斗中也不在让路集里（战斗中弹窗由别的判据挡）", Yield(sn), "");

            sn = Idle(); sn.MainWorldReady = false;
            Eq("不在主世界不在让路集里", Yield(sn), "");
        }

        // ---------------------------------------------------------------- ④
        private static void Group4_Execute()
        {
            Console.WriteLine("④ ExecuteBlocker：五条 + 顺序");
            var sn = Idle();
            Eq("全空闲 ⇒ 可以落刀", Exec(sn), "");

            sn = Idle(); sn.DramaChainHold = true;
            Eq("① 链收尾", Exec(sn), "剧情链收尾中");

            // 顺序：链收尾最先（连"战斗中"都排在它后面）
            sn = Idle(); sn.DramaChainHold = true; sn.IsBattleLevel = true;
            Eq("链收尾先于战斗中", Exec(sn), "剧情链收尾中");

            // ② 战斗中 = 取或（与 Busy 的单列**刻意不同**）
            sn = Idle(); sn.IsBattleLevel = true;
            Eq("② 逻辑战斗位", Exec(sn), "战斗中");
            sn = Idle(); sn.BattleUiVisible = true;
            Eq("② 只有战斗界面也算（取或）", Exec(sn), "战斗中");

            sn = Idle(); sn.DramaPendingAction = true;
            Eq("③ 我方挂起", Exec(sn), "另有一个动作在等玩家点选");

            sn = Idle(); sn.UiLayerTopName = "UIDramaDialogue";
            Eq("④ 玩家被界面占着", Exec(sn), "玩家正被界面占着（剧情窗开着（UIDramaDialogue））");

            sn = Idle(); sn.MainWorldReady = false;
            Eq("⑤ 不在主世界", Exec(sn), "不在主世界（世界界面未就绪）");

            // 顺序：界面占着先于主世界
            sn = Idle(); sn.UiLayerTopName = "UIDramaDialogue"; sn.MainWorldReady = false;
            Check("界面占着先于主世界", Exec(sn).StartsWith("玩家正被界面占着"), Exec(sn));

            // ★ 对话窗**不**挡落刀（与 Yield 同一个理由：那是正常语义）
            sn = Idle(); sn.ChatWindowVisible = true;
            Eq("★对话窗不挡落刀★", Exec(sn), "");
        }

        // ---------------------------------------------------------------- ⑤
        private static void Group5_FourViewsDiffer()
        {
            Console.WriteLine("⑤ 四视角差异矩阵（同一份快照，答案各不相同 = 不能合并的证据）");

            // 场景 A：战斗中 → 能说、不能做、不用让、不能落刀
            var sn = Idle(); sn.IsBattleLevel = true;
            Check("A 战斗中：能说 / 不能做 / 不让 / 不能落刀",
                  Speech(sn) == "" && Busy(sn).Length > 0 && Yield(sn) == "" && Exec(sn).Length > 0);

            // 场景 B：对话窗开着 → 能说、不能做、**不用让**、**能落刀**
            sn = Idle(); sn.ChatWindowVisible = true;
            Check("B 对话窗：不能做，但让路与落刀都不拦（直发语义）",
                  Speech(sn) == "" && Busy(sn).Length > 0 && Yield(sn) == "" && Exec(sn) == "",
                  "yield=" + Yield(sn) + " exec=" + Exec(sn));

            // 场景 C：不在主世界 → 能说、不忙、不让、不能落刀
            sn = Idle(); sn.MainWorldReady = false;
            Check("C 不在主世界：只有落刀拦（别的不该跟着拦）",
                  Speech(sn) == "" && Busy(sn) == "" && Yield(sn) == "" && Exec(sn).Length > 0);

            // 场景 D：链收尾 → 能说、不忙、不让、不能落刀
            sn = Idle(); sn.DramaChainHold = true;
            Check("D 链收尾：只有落刀拦",
                  Speech(sn) == "" && Busy(sn) == "" && Yield(sn) == "" && Exec(sn).Length > 0);

            // 场景 E：玩家死了 → 四个全拦
            sn = Idle(); sn.DeathWindowVisible = true; sn.MainWorldReady = false;
            Check("E 玩家死亡：能说被拦 + 落刀被拦",
                  Speech(sn).Length > 0 && Exec(sn).Length > 0);
        }

        // ---------------------------------------------------------------- ⑥
        private static void Group6_Wrappers()
        {
            Console.WriteLine("⑥ IsPlayerBusy / Describe");

            var sn = Idle();
            Check("全空闲 ⇒ 不忙", !WorldStateCore.IsPlayerBusy(ref sn));

            sn = Idle(); sn.ChatWindowVisible = true;
            Check("对话窗 ⇒ 忙", WorldStateCore.IsPlayerBusy(ref sn));

            sn = Idle(); sn.DeathWindowVisible = true;
            Check("玩家死亡 ⇒ 忙（由能说闸命中）", WorldStateCore.IsPlayerBusy(ref sn));

            sn = Idle(); sn.IsBattleLevel = true;
            Check("战斗中 ⇒ 忙", WorldStateCore.IsPlayerBusy(ref sn));

            // 能说闸单独命中也要算忙（原实现是"任一命中"）
            sn = Idle(); sn.PlayerUnit = PlayerUnitRead.Absent;
            Check("没进世界 ⇒ 忙（能说闸单独命中）", WorldStateCore.IsPlayerBusy(ref sn));

            // Describe：四视角并排，全通时是四个"通"
            sn = Idle();
            string d = WorldStateCore.Describe(ref sn);
            Check("Describe 四视角并排", d.Contains("说=") && d.Contains("做=")
                  && d.Contains("让=") && d.Contains("行="), d);
            Check("全通时四个'通'", d == "说=通 做=通 让=通 行=通", d);

            sn = Idle(); sn.IsBattleLevel = true;
            d = WorldStateCore.Describe(ref sn);
            Check("战斗中 Describe 里'做'与'行'非通", !d.Contains("做=通") && !d.Contains("行=通"), d);
        }
    }
}
