/// <summary>
/// BattleFlowHooks —— 「战前 / 战中」判据的三个挂点（10-25，用户拍板）。
///
/// 三个挂点各管一件事，缺一个都会漏掉一个案子：
///   · `UIDramaBase.CreateOptions` **postfix** —— 页级入口。寻仇页（20203 摆着 100003）与
///     切磋确认页（21204 摆着 212042）都靠它：**页面一弹出来就成立**，早于 NPC 调工具
///     （真机 18:51:10.638 页出来、18:51:22.014 工具才调 ⇒ 早 11 秒。这是唯一赶得上的判据）；
///   · `UIDramaBase.ClickOption` **prefix** —— 玩家点了选项。命中开战按钮 ⇒ 记"已确认"
///     （此后页没了也不退出）；战前期间点了别的 ⇒ 婉拒，退出。
///     **必须是 prefix**：引擎在 `ClickOption` 内部**同步**跑完选项效果（`DramaDrillChoice`
///     头注释已记过这条实证），postfix 会晚于整条链；
///   · `DramaFunction.OptionsFunction` **prefix** —— 剧情命令。只在函数名是"进副本"时报信，
///     用来**学**"刚才那一枚选项会开打"（读不到选项自己那个 `function` 字段：真机恒为 0）。
///
/// ## 为什么 `DramaFunction` 要按名反射挂
/// 它**不在本仓任何反编 dump 里**（只有 `Player.log` 的栈帧证明它存在），编译期拿不到类型。
/// 与 `BattleEndHook`（`BattleEndMgr`）、`FastKeyGate`（`MapWorldMgr`）同一套路。
/// 找不到就**降级不报错**：页级 + 应战窗 + 战斗位三个信号照样工作，只是"副本那一类"学得慢一点。
///
/// ## 铁律
/// 所有挂点**恒放行**且**全程 try/catch**：这里任何一个异常顺着游戏自身的点击链/开窗链
/// 往上炸，玩家就会点不动选项、或者战斗开不起来。判据坏了最坏只是"少挡一次"。
/// </summary>
using System;
using System.Reflection;
using HarmonyLib;

namespace AgentLoopBridge
{
    /// <summary>`CreateOptions` postfix → 页级入口（这一页有哪些选项编号）。
    /// 与 `DramaAiOption.DramaOptionTapHook` 挂在**同一个方法**上（Harmony 允许多个 postfix），
    /// 两者互不依赖：那个负责给按钮挂监听，这个只读一眼选项编号。</summary>
    [HarmonyPatch(typeof(UIDramaBase), "CreateOptions")]
    internal static class BattleFlowPageHook
    {
        private static void Postfix(UIDramaBase __instance)
        {
            try { BattleFlowGate.NotePage(__instance); } catch { }
        }
    }

    /// <summary>
    /// `ClickOption` prefix + postfix → 玩家点了哪一枚，并把它圈在一个**作用域**里。
    ///
    /// ★为什么需要 postfix 才能圈出作用域（而不是只记"最近一次点击"）★
    /// "这一枚选项会不会开打"的答案是 `IntoDungeon` 这条剧情命令，而它是**同步跑在
    /// `ClickOption` 内部**的（真机栈 4 次一致）。用"最近一次点击 + 时间窗"去归属，
    /// 就得回答"隔多久还算它引起的"——那个问题没有正确答案（用户 10-25 明确否掉了时间窗）。
    /// 用作用域就没有这个问题：只有**栈里还在 ClickOption 里**时来的进副本命令才学，
    /// 引擎自己发起的（外面、`_clickDepth == 0`）一律不学。**零秒数。**
    ///
    /// ★为什么 prefix 也必须保留★ 引擎在 `ClickOption` **内部**就把选项效果跑完了
    /// （09-22 实证：切磋在这里直接 `IntoBattle`），postfix 会晚于整条链 ——
    /// "点了开战按钮 = 已确认"这一击必须发生在 prefix（否则页一关就再也拿不到）。
    /// </summary>
    [HarmonyPatch(typeof(UIDramaBase), "ClickOption")]
    internal static class BattleFlowClickHook
    {
        /// <summary>Harmony 用它把 prefix 的返回值传给 postfix（`__state`）。</summary>
        private static bool Prefix(ConfDramaOptionsItem __0, out int __state)
        {
            __state = -1;
            try
            {
                int id = OptionIdOf(__0);
                // ★作用域**无条件**开★ 编号读不到也开、也要在 postfix 里关 ——
                //   否则"prefix 没开、postfix 照退"会让配对不对称（退掉的是外层的那次）。
                //   读不到编号时作用域里记 0，学习侧会报 `NoId` 并拒绝学（见 `ClickScope`）。
                __state = BattleFlowGate.EnterClickScope(id);       // 记下上一层
                if (id > 0) BattleFlowGate.NoteOptionClicked(id);
            }
            catch { }
            return true;     // 永不拦截：拦了玩家就点不动选项
        }

        /// <summary>退出作用域。异常一律吞 —— 这里抛出去会顺着游戏自己的点击链往上炸。</summary>
        private static void Postfix(int __state)
        {
            try { BattleFlowGate.ExitClickScope(__state); } catch { }
        }

        /// <summary>选项行的配表 id（读不到返回 0）。与 `DramaDrillChoice.OptionIdOf` 同源双路写法。</summary>
        private static int OptionIdOf(ConfDramaOptionsItem opt)
        {
            if (opt == null) return 0;
            try { return (int)opt.id; }
            catch { try { return (int)((dynamic)opt).id; } catch { return 0; } }
        }
    }

    /// <summary>
    /// `DramaFunction.OptionsFunction(string, DramaFunctionData)` prefix —— 剧情命令。
    /// 只在函数名是"进副本"时报信（`IntoDungeon`，真机 4 次实证的调用栈）。
    /// 按名反射挂（类型不在任何 dump 里），挂不上自动降级。
    /// </summary>
    internal static class BattleFlowFuncHook
    {
        /// <summary>挂载结果摘要（进日志；也让"没挂上"这件事不可能被静默忽略）。</summary>
        internal static string InstallReport = "(未执行)";

        private static bool _installed;

        internal static void EnsureInstalled()
        {
            if (_installed) return;
            _installed = true;
            try
            {
                var t = AccessTools.TypeByName("DramaFunction");
                if (t == null)
                {
                    InstallReport = "DramaFunction 类型未找到 → 只靠页级/应战窗/战斗位（副本那类学不到编号）";
                    ModMain.P("[BattleFlowHooks] " + InstallReport);
                    return;
                }
                MethodInfo m = null;
                foreach (var cand in AccessTools.GetDeclaredMethods(t))
                {
                    if (cand.Name != "OptionsFunction") continue;
                    var ps = cand.GetParameters();
                    // 签名（真机栈帧）：OptionsFunction(String, DramaFunctionData)
                    if (ps.Length == 2 && ps[0].ParameterType == typeof(string)) { m = cand; break; }
                }
                if (m == null)
                {
                    InstallReport = "DramaFunction.OptionsFunction(String, …) 未找到 → 同上降级";
                    ModMain.P("[BattleFlowHooks] " + InstallReport);
                    return;
                }
                var pre = typeof(BattleFlowFuncHook).GetMethod(nameof(Prefix),
                    BindingFlags.Static | BindingFlags.NonPublic);
                new HarmonyLib.Harmony("AgentLoopBridge.BattleFlowFunc").Patch(m, prefix: new HarmonyMethod(pre));
                InstallReport = "DramaFunction.OptionsFunction=已挂（进副本学习可用）";
                ModMain.P("[BattleFlowHooks] " + InstallReport);
            }
            catch (Exception e)
            {
                InstallReport = "挂载失败: " + e.Message;
                ModMain.P("[BattleFlowHooks] 失败（不影响其它补丁，副本那类学不到编号）: " + e.Message);
            }
        }

        /// <summary>只报信、不改行为。异常一律吞。</summary>
        private static void Prefix(string __0)
        {
            try
            {
                if (string.IsNullOrEmpty(__0)) return;
                // ★ 只认"进副本"。其余命令（开页 OpenDrama / 装监听 AddSystemInMap / 移动镜头 /
                //   战斗功能 BattleFunction）与"这一页会不会开打"无关，认错了会把普通页学成战斗页。
                //   真机 10-25 全量日志里 OptionsFunction 的 19 次调用中，IntoDungeon 占 4 次，
                //   每一次的栈都是 `…→ OptionsFunction → IntoDungeon → WorldBattleMgr:IntoBattle`。
                if (__0.IndexOf("IntoDungeon", StringComparison.OrdinalIgnoreCase) < 0) return;
                BattleFlowGate.NoteDungeonCommand("DramaFunction:" + __0);
            }
            catch { }
        }
    }

    /// <summary>
    /// `WorldBattleMgr.IntoBattle` 的**全部重载**统一 prepatch —— 游戏真正开打的那一下。
    ///
    /// 为什么挂全部重载、而不是挑一个：真机 10-25 出现过三个签名，
    ///   `IntoBattle(Vector2Int, MonstData, Action, WorldBattleData)`  ← 你自己打的副本（4 次）
    ///   `IntoBattle(MonstData, Action, WorldBattleData)`              ← 剧情选项送进副本（4 次）
    ///   `IntoBattle(WorldUnitBase, Int32, Action, WorldBattleData)`   ← NPC 寻仇（1 次）
    /// 我们只用它当"**现在开打**"的**边沿**，不拿重载当分类器 ——
    /// `(WorldUnitBase, …)` 那一支全日志只有 1 个样本，凭它区分"NPC 打架 vs 副本"
    /// 就是拿未校准的东西换已校准的（本仓的老教训）。
    /// 重载名只进日志，供将来攒够样本后重新评估。
    ///
    /// 它与 `BattleActive()` 是**同一件事**（真机 18:51:32.349 两侧日志同毫秒），
    /// 但早一点点、且带得走哪个重载。挂不上时 `BattleActive()` 照样能判 ⇒ 自动降级。
    /// </summary>
    internal static class BattleStartHook
    {
        internal static string InstallReport = "(未执行)";

        private static bool _installed;

        internal static void EnsureInstalled()
        {
            if (_installed) return;
            _installed = true;
            try
            {
                var t = AccessTools.TypeByName("WorldBattleMgr");
                if (t == null)
                {
                    InstallReport = "WorldBattleMgr 类型未找到 → 开打边沿只靠 BattleActive() 轮询";
                    ModMain.P("[BattleStartHook] " + InstallReport);
                    return;
                }
                var pre = typeof(BattleStartHook).GetMethod(nameof(Prefix),
                    BindingFlags.Static | BindingFlags.NonPublic);
                int n = 0;
                foreach (var cand in AccessTools.GetDeclaredMethods(t))
                {
                    if (cand.Name != "IntoBattle") continue;
                    new HarmonyLib.Harmony("AgentLoopBridge.BattleStart" + n)
                        .Patch(cand, prefix: new HarmonyMethod(pre));
                    n++;
                }
                InstallReport = n == 0
                    ? "WorldBattleMgr.IntoBattle 未找到 → 开打边沿只靠 BattleActive() 轮询"
                    : "WorldBattleMgr.IntoBattle=" + n + " 个重载均已挂";
                ModMain.P("[BattleStartHook] " + InstallReport);
            }
            catch (Exception e)
            {
                InstallReport = "挂载失败: " + e.Message;
                ModMain.P("[BattleStartHook] 失败（开打边沿退化为 BattleActive() 轮询）: " + e.Message);
            }
        }

        /// <summary>只报信、不改行为。`__originalMethod` 是 Harmony 注入的被调重载。</summary>
        private static void Prefix(MethodBase __originalMethod)
        {
            try
            {
                string sig = "IntoBattle";
                try
                {
                    if (__originalMethod != null)
                    {
                        var ps = __originalMethod.GetParameters();
                        var parts = new string[ps.Length];
                        for (int i = 0; i < ps.Length; i++)
                            parts[i] = ps[i].ParameterType == null ? "?" : ps[i].ParameterType.Name;
                        sig = "IntoBattle(" + string.Join(", ", parts) + ")";
                    }
                }
                catch { }
                ModMain.P("[BattleStartHook] 游戏开打：" + sig);
                BattleFlowGate.NoteBattleStart(sig);
                // NPC 对 NPC 观战在等这一场时，这是"真的开了"的第一手证据（见 NpcDuel）
                try { NpcDuel.NoteBattleStarted(sig); } catch { }
            }
            catch { }
        }
    }
}
