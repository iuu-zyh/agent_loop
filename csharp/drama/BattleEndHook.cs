/// <summary>
/// BattleEndHook —— 战斗结束信号（`BattleEndMgr.BattleEnd(Boolean)`）。
///
/// ## 为什么要它
/// 切磋应战之后，工具结果必须等这场战斗打完才有意义（见 `BattleOutcome` 头注释的 09-22 实证）。
/// 「战斗结束了」这件事有三个信号，本钩子是最硬的那个：
///   ① `BattleEndMgr.BattleEnd(bool)` —— 游戏自己的收口函数（真机 Player.log 5088-5091 见过它跑，游戏自己打的行是 `12 战斗结束：False`）；
///   ② `g.world.battle.isBattle` 由真翻假；
///   ③ `UIBattleInfo` 从可见变不可见。
/// ②③ 由 `BattleOutcome.Tick` 每帧轮询（合并在 `NpcInitiativeMonitor.BattleActive()`），
/// 本钩子给的是**事件**而不是采样 —— 有事件就不必靠"猜多久算结束"。三个任一到达都算数。
///
/// ## 为什么手动挂、不用 [HarmonyPatch]
/// `BattleEndMgr` **不在本仓库任何反编 dump 里**（只有日志栈帧证明它存在），编译期拿不到这个类型。
/// 所以按名反射找方法再 `harmony.Patch`，与 `FastKeyGate`（按名找 `MapWorldMgr`）、
/// `DramaActivityHook`（`ModMain.cs:119-123` 手动挂 OpenMapDrama）同一套路。
/// 找不到就**降级不报错**：②③ 两个轮询信号照样能收口，只是晚一点。
///
/// ## 那个 bool 的语义**未知**，别当胜负用
/// 参数个数与类型（1 × bool）是从日志栈帧看出来的，**名字与含义没有任何依据**
/// （同名的 `UnitActionRoleAppellationAttack.BattleEnd(bool isWin)` 在 dump 里叫 isWin，
/// 但那是**另一个类**，不能推给 BattleEndMgr）。所以本钩子只把它当"战斗结束了"的触发用，
/// 原值一并打进日志做校准；胜负一律由战后那两页的**原话 + 说话人**交给模型自己判
/// （`BattleOutcome` 的口径）。哪天真机样本攒够了，再考虑把它结构化成 `win` 字段。
/// ★ 已有的一个反例，正好说明为什么不能当胜负 ★ 真机那次切磋（玩家输）游戏自己打的是
/// `12 战斗结束：False`（Player.log 5088）—— 而它的调用栈是
/// `ClickOption → NextDialogue → BattleEndMgr.BattleEnd`（5093-5096），也就是**玩家点完
/// 战后窗最后一页**才跑的。所以这条信号的真实含义更接近"这场切磋整条流程走完了"
/// （比 `isBattle` 翻假更靠后），`BattleOutcome` 正是按这个位置用它当"可以收口了"的硬证据。
///
/// ## 10-26 追加：本钩子现在还会**拦住**一次收尾（NPC 对 NPC 观战的战后处置页）
///
/// `Prefix` 的返回类型从 `void` 改成 `bool`，并把 `__originalMethod` / `__instance` 一起带上：
/// 判据全在 `NpcDuel.NoteBattleEnding`（本类**不判**）。它只在"我们发起的观战、已判出胜者、
/// 败者是世界 NPC、本次还没放行过"时返回 false（= 跳过原方法），然后自己去开原版处置页 20403；
/// 玩家选完 / 关窗后它用 `__originalMethod.Invoke(__instance, new[]{ isWin })` 把这次收尾打回去。
///
/// 为什么能干净地跳过：反编 `SpecialBattle79::BattleEnd`@0x2BFDCD0 的结尾是一条**尾跳**
/// `jmp 0x1828FA0D0`（= `BattleEndMgr::BattleEnd`），调用方在它之后什么都不做（父代理 10-26 实测）
/// ⇒ 跳过它没有"半结束"的中间态。`BattleEndMgr.BattleEnd` 是**实例方法**
/// （`[this+0x10]/[this+0x11]` 有读写，反编见 `NpcDuel.NoteBattleEnding`），所以放行必须带实例。
///
/// ★ 三条报信仍然**先跑**（顺序见 `Prefix` 注释）★ 它们的证据与 `NpcDuel` 的胜负快照同拍，
///   而拦住之后 `BattleEndFront` 就不会再发了 —— 所以顺序不能反。
/// ★ 恒放行 + 全程 try/catch：判据坏了最坏只是"少拦一次"，绝不许炸掉游戏自己的战斗收尾 ★
/// </summary>
using System;
using System.Reflection;
using System.Text;
using HarmonyLib;

namespace AgentLoopBridge
{
    internal static class BattleEndHook
    {
        /// <summary>挂载结果摘要（进日志，也供诊断行读）。</summary>
        internal static string InstallReport = "(未执行)";

        private static bool _installed;

        internal static void EnsureInstalled()
        {
            if (_installed) return;
            _installed = true;
            var note = new StringBuilder();
            try
            {
                var t = AccessTools.TypeByName("BattleEndMgr");
                if (t == null)
                {
                    InstallReport = "BattleEndMgr 类型未找到 → 战斗结束只靠 isBattle/UIBattleInfo 轮询";
                    ModMain.P("[BattleEndHook] " + InstallReport);
                    return;
                }
                note.Append("BattleEndMgr=有;");

                // ① 首选：`BattleEnd(Boolean)`（与日志栈帧里的参数个数/类型一致）。
                // ② 退路：按名 + 恰好一个形参、且形参类型名里带 Boolean（互操作层可能不是
                //    编译期的 `System.Boolean`）。两条都把**全签名**打进安装日志 —— 挂错了要能一眼看见。
                MethodInfo m = null;
                string all = "";
                string picked = "";
                foreach (var cand in AccessTools.GetDeclaredMethods(t))
                {
                    if (cand.Name != "BattleEnd") continue;
                    var ps = cand.GetParameters();
                    string sig;
                    try { sig = cand.ToString(); } catch { sig = "BattleEnd(?)"; }
                    all += sig + ";";
                    if (m == null && ps.Length == 1 && ps[0].ParameterType == typeof(bool))
                    { m = cand; picked = "按名+Boolean（首选）"; }
                }
                if (m == null)
                {
                    foreach (var cand in AccessTools.GetDeclaredMethods(t))
                    {
                        if (cand.Name != "BattleEnd") continue;
                        var ps = cand.GetParameters();
                        if (ps.Length != 1) continue;
                        string tn = ps[0].ParameterType.Name ?? "";
                        if (tn.IndexOf("Boolean", StringComparison.OrdinalIgnoreCase) < 0) continue;
                        m = cand; picked = "按名+单参数（退路，" + tn + "）";
                        break;
                    }
                }
                if (m == null)
                {
                    InstallReport = note + "BattleEnd(Boolean) 未找到 → 战斗结束只靠轮询信号（签名[" + all + "]）";
                    ModMain.P("[BattleEndHook] " + InstallReport);
                    return;
                }
                note.Append("BattleEnd(Boolean)=有(" + picked + "," + (m.IsStatic ? "static" : "instance") + ");");

                var pre = typeof(BattleEndHook).GetMethod(nameof(Prefix),
                    BindingFlags.Static | BindingFlags.NonPublic);
                new HarmonyLib.Harmony("AgentLoopBridge.BattleEnd").Patch(m, prefix: new HarmonyMethod(pre));
                InstallReport = note + "★战斗结束事件=已挂★ 方法=" + SafeSig(m) + " 全部签名[" + all + "]";
                ModMain.P("[BattleEndHook] " + InstallReport);
            }
            catch (Exception e)
            {
                InstallReport = "挂载失败: " + e.Message;
                ModMain.P("[BattleEndHook] 失败（不影响其它补丁，战斗结束退化为轮询判定）: " + e.Message);
            }
        }

        private static string SafeSig(MethodInfo m)
        {
            try { return m.DeclaringType.Name + "." + m.ToString(); }
            catch { return m.Name; }
        }

        /// <summary>前缀：**先**照原顺序报信，**最后**问 `NpcDuel` 要不要拦住这次收尾。
        /// 返回 false = 跳过原方法（只有 `NpcDuel` 明确说"我在等玩家处置"才会这样）。
        ///
        /// ★ 顺序是刻意的（10-26）★ 三条报信都必须在"可能拦住"之前跑：
        ///   · `NpcDuel` 的胜负快照就发生在这一拍（`BattleEndFront` 还没发 —— 它就在被我们
        ///     拦住的这个方法体里），三条报信的时机与它同拍；
        ///   · 拦住之后原方法被跳过 ⇒ `BattleEndFront` 永远不会再来，这三条也就再没机会跑。
        /// ⇒ 反过来写（先问 NpcDuel 再报信）会让"被拦住的那一场"丢掉硬信号与边沿证据。
        ///
        /// ★ fail-open ★ 任何异常 → 返回 true（放行）。判据坏了最坏只是"少拦一次"。
        ///
        /// `hard:true` = 这是游戏自己报的收尾，不是轮询采样（区别见 `BattleOutcome.NoteBattleEnded`）。
        /// ★ 日志**无条件**打（09-22）★ 原先这里只调 `NoteBattleEnded`，而它第一行就是
        /// `if (_slot <= 0) return;` —— 于是"攻击链上 BattleEnd 到底跑没跑"在日志里**根本看不出来**
        /// （攻击当时没有槽位），"没看到"被误读成"没跑"。现在每跑一次留一行，带 arg0 原值。</summary>
        private static bool Prefix(bool __0, object __instance, MethodBase __originalMethod)
        {
            try { ModMain.P("[BattleEndHook] BattleEndMgr.BattleEnd 跑了 arg0=" + __0 +
                            "（台账在飞=" + BattleOutcome.Active + "）"); } catch { }
            try { BattleOutcome.NoteBattleEnded("BattleEndMgr.BattleEnd(arg0=" + __0 + ")", true); }
            catch { }
            // ★ 边沿证据（09-24）★：这条调用链是 `ClickOption → NextDialogue → BattleEndMgr.BattleEnd`，
            //   即"玩家翻完战后窗最后一页"——**游戏自己报的"整条流程走完了"**（比 `isBattle` 翻假更靠后）。
            //   排队的动作据此把稳定窗从保守档降到短档（见 `DeferredActionQueue.NoteChainOver`）。
            //   注：本钩子按名反射挂，挂不上时这条边沿不存在 ⇒ 自动退回轮询档，不会因此出错。
            try { DeferredActionQueue.NoteChainOver("BattleEndMgr.BattleEnd"); } catch { }

            // ★ 最后才问"要不要拦" ★ 判据全在 NpcDuel（本类不判）；它内部全程 try/catch 且
            //   fail-open，这里再兜一层，保证任何情况下都不会把游戏自己的收尾炸掉。
            try { return NpcDuel.NoteBattleEnding(__0, __instance, __originalMethod); }
            catch (Exception e)
            {
                try
                {
                    ModMain.P("[BattleEndHook] NpcDuel.NoteBattleEnding 抛异常 → 放行（fail-open）: "
                              + e.GetType().Name + " " + e.Message);
                }
                catch { }
                return true;
            }
        }
    }
}
