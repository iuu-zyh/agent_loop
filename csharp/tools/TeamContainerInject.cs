/// <summary>
/// TeamContainerInject —— 组队「容器直塞」钩子。
///
/// ## 为什么存在（2026-10-19 反汇编定案，见 docs/features/team.md §2.3）
///
/// 原「条件组选人」链在 type=2 后天气运上**必炸**：
///   `BattleFunction::AddBattleNPCData` → `ConfNpcCondition::GetUnits(int,null)`
///   → `GetUnitsInID`（5 槽 Where）→ `UnitCondition::Feature` → `WorldUnitBase::GetLuck`
///   → **`allLuck` 里存在 `luckData==null` 的元素时直接 throw**（0x1832BC53C je throw stub）。
///   而 `CreateLuck` 对 type=2 经 `WorldFactory.CreateLuckInID` 建 `WorldUnitLuckDyn`
///   （ctor 不填 +0x20 luckData），`Init`（vtable+0x188）只有在 **返回非 null** 时才被调
///   （0x1832BADB0）—— 若工厂返回 null，+0x20 永远是 null。
///   `NewCreateLuck` @0x1837696E9 **第一件事就是读 this+0x20**，null 直接 throw —— 它救不了。
///
/// ## 这条钩子做什么（不修气运链，绕开它）
///
/// `addBattleNPCData_<组>_<N>` 的全部作用只是
///   「GetUnits(组) → CommonTool::Index(list, N) → battle.data.npcUnits.Add(unit)」，
/// `addBattleUnit_3/4/5` 再从 npcUnits 按位取人上场（`GetUnitWorld`: id 3..10 → Index(arr, id-3)）。
///
/// 既然**我们已经知道自己要哪几个人**（TeamTool.Members），就不再让引擎算：
/// 在 `BattleFunction.AddBattleNPCData(string[])` 的**前缀**里，
/// 把队伍成员按号位直接写进 `g.world.battle.data.npcUnits`，然后**跳过原方法**（返回 false）。
/// 原方法会做的事（含它抛的雷）一并跳过；`addBattleUnit_3/4/5` 与 `setBattleData` 照常执行。
///
/// ## 时序依据
///   `OptionsFunction(string)` 把每个 `|` 分隔的 token 包成委托 **Add 进 CallQueue 再统一 Run**
///   （0x29086CB CallQueue::Add → 0x29086D9 …0x2908798 CallQueue::Run）。
///   ⇒ 同一条 battleFunction 里的 token 按**队列顺序**执行：
///   只要本钩子在任何 `addBattleNPCData` token 上被调，注入就发生在
///   **同队列更靠后的 `addBattleUnit_3/4/5` 之前** ⇒ 容器到位，取人必成。
///
/// ## 失败安全（三层）
///   ① `BattleFunction` 类型按名反射找不到 ⇒ 不挂，一切照旧（条件组那条路还在）；
///   ② 前缀里任何异常 ⇒ 记日志后**放行原方法**（宁可回到「引擎选人」，不挡战斗）；
///   ③ 队伍为空（没组队）⇒ **不拦截**，原方法照跑 —— 副本自带 NPC 的生成不受影响。
///
/// ## 为什么不用 [HarmonyPatch] 特性
///   与 BattleEndHook 同理：目标类在 il2cpp 运行时里，编译期拿不到强类型，
///   按 `AccessTools.TypeByName("BattleFunction")` 反射挂，与现有钩子同一套路。
/// </summary>
using System;
using System.Collections.Generic;
using System.Reflection;
using System.Linq;
using System.Text;
using HarmonyLib;
using UnhollowerBaseLib;
using UnityEngine.UI;

namespace AgentLoopBridge
{
    internal static class TeamContainerInject
    {
        private const string Tag = "[TeamInject]";
        internal static string InstallReport = "(未执行)";

        // ══════════════════════════════════════════════════════════════════════
        //  ★ 静态状态生命周期契约（10-21 显式化）★
        // ══════════════════════════════════════════════════════════════════════
        //
        // 本类 + `TeamBattleFollowup` + `BattleKillStats` 共有 29 个可变静态字段，
        // 它们承载的是**跨战斗的生命周期状态**。历史上多次因为"谁在什么时候清"没写清
        // 而出过静默 bug（最典型：击杀统计被提前 Reset ⇒ 永远报「本场无击杀记录」）。
        //
        // 字段分散时，"重置契约"只活在作者脑子里。所以这里把契约**写死在字段旁**：
        // 每个可变字段都标明 **谁来清、什么时候清**。新增字段必须照此登记。
        //
        // 三个清算时机（顺序不能换）：
        //   ① `ResetForBattle`  —— BattleEndFront **开头**。只能清"上一场的东西"，
        //      绝不能清"本场还要用/还要报"的东西（本场战果在末尾才输出！）。
        //   ② `OnBattleExit.finally` —— 正常收口的**唯一**完整清理点。
        //   ③ `ResetTickState`  —— 读档/换世界，与战斗无关的缓存。
        //
        // | 字段 | 清在哪 | 为什么 |
        // |---|---|---|
        // | `_installed`            | 从不清（进程级一次性） | Harmony 只挂一次 |
        // | `InstallReport`         | 从不清（覆盖写）       | 每次 EnsureInstalled 重写 |
        // | `NoInject`              | 探针 Parse 时写        | 测试开关，不参与战斗生命周期 |
        // | `ProbeOn`/`DeepDeathHooks`/`DeathDiag` | 不参与战斗生命周期 | 编译期/调试常量 |
        // | `InjectCount`           | ①                  | 每场从 0 数 |
        // | `_decided`              | ①                  | 决斗那支的"本场已决定"（注入过/明确拒绝）；**不能**拿 InjectCount 顶替 |
        // | `_mateBattleUnits`      | ② `ClearMateBattleUnits` | 引用会随战斗销毁失效 |
        // | `DeadTeamIds`           | ② `ClearDeadTeamIds`     | **不能放①**：BattleEndFront 的 190 还要读 |
        // | `_usedAsDramaLead`      | ①                  | 190 早于①打开，清掉=给下一场干净起点 |
        // | `_nonMateLogBudget`     | ② 内重置为 3        | 日志节流预算按场重置 |
        // ══════════════════════════════════════════════════════════════════════

        private static bool _installed;
        /// <summary>本次战斗注入次数。<b>清于 ①`ResetForBattle`。</b></summary>
        internal static int InjectCount;

        /// <summary>
        /// 本场是否**已经做过决定**（决斗：注入过 / 明确拒绝）。<b>清于 ①`ResetForBattle`。</b>
        ///
        /// 为什么不复用 `InjectCount`：拒绝那条路必须"一个人都不带"，但那不是一次注入 ——
        /// 若拿 `InjectCount` 当去重位，`BattleEndFront` 会把"拒绝"读成"本场有队友参战"，
        /// 于是战后照样归队/写经历（用户要的恰恰是"拒绝就一个人打，什么都不发生"）。
        /// </summary>
        private static bool _decided;

        /// <summary>测试开关：noinject=1 时完全不碰 npcUnits，用于剧情副本对照。不参与战斗生命周期。</summary>
        internal static bool NoInject;

        /// <summary>
        /// 本场已经倒下的队友 unitID（UnitCtrlBase.Die 直接记录，避免尸体被移除后漏判）。
        /// <b>清于 ②`TeamBattleFollowup.OnBattleExit` 的 finally（`ClearDeadTeamIds`）。</b>
        /// ⚠ **不能**放①：`BattleEndFront` 里 190 立绘还要读它认人，提前清 = 立绘永远找不到人。
        /// </summary>
        internal static readonly HashSet<string> DeadTeamIds = new HashSet<string>();

        /// <summary>「非队友死亡」日志的每分钟预算（小怪批量死亡会刷屏，必须节流）。<b>清于 ②（重置为 3）。</b></summary>
        private static int _nonMateLogBudget = 3;

        /// <summary>
        /// 本场队友的**战场单位**引用（`UnitCtrlBase`），在 `BattleUnitMgr.AddUnit` 时登记。
        /// <b>清于 ②`ClearMateBattleUnits`。</b>
        ///
        /// 为什么要有这个（10-20 定案，这是死亡判定的第二条独立通道）：
        ///   前面三条钩子（`Die` / `OnUnitDie` / `Effect6009`）全部依赖**回调真的被调到**，
        ///   而真机日志证明：190 剧情开了 2 次（回调确实响了），但 `[death]` 一条没记、
        ///   `[probe] 6009.Create 挂到 → ?()` 的 unitID 是空串。
        ///   与其继续猜"回调为什么认不出人"，不如**自己攥住这些单位对象**，
        ///   在战后直接问它们 `isDie` / `hp` —— 不依赖任何回调、任何字符串。
        /// </summary>
        private static readonly List<object> _mateBattleUnits = new List<object>();

        internal static void ClearMateBattleUnits()
        {
            _mateBattleUnits.Clear();
        }

        /// <summary>探针总开关（10-20 加：先确认 6009 有没有挂到队友，再谈重伤）。</summary>
        internal static bool ProbeOn = true;

        /// <summary>
        /// 深度死亡钩子开关（**默认关**）。
        ///
        /// 10-21 真机统计：三条死亡钩子里只有 `BattleUnitMgr.OnUnitDie` 真正命中队友
        /// （× 2），另外两条 `UnitCtrlBase.Die` / `Effect6009.b__0_0` 都是 **0 命中**。
        /// 它们每到一次单位死亡都要跑一遍，纯开销。
        ///
        /// 什么时候才需要打开：
        ///   · 某天日志里不再出现 `队友倒下（OnUnitDie）`，
        ///     要排查"队友的死亡是不是走了别的路径"时；
        ///   · 想验证 `Effect6009` 到底挂到了谁身上时。
        /// 打开方式：本字段改 true 重新编译（**不要**做成配置项，避免长期开着）。
        /// </summary>
        internal static bool DeepDeathHooks = true;   // 击杀统计需要 Die 钩子，保持开启

        /// <summary>
        /// `isDie` 语义诊断开关（**默认开**，一次性调查用；查清后应改回 false）。
        ///
        /// 目的：搞清 `OnUnitDie` 里读到的 `isDie=False` 到底意味着什么
        /// （详见 `DiagOnUnitDie` 的注释）。这个结论直接决定"击杀统计"能不能做、
        /// 以及统计口径该怎么定。
        ///
        /// ⚠ 它会**给每个死亡事件打一行**。小怪多的时候会刷屏，
        ///   但这是必要的——统计口径错了比没有统计更糟。
        ///   查清后把本字段改回 false。
        /// </summary>
        internal static bool DeathDiag = false;


        internal static void EnsureInstalled()
        {
            // ★11-19：`_installed` 只守 Harmony 补丁，**不再连帧回调一起挡**★
            //   真机日志（20:25 那次 5 段世界）证明了后果：`teamjoin` 只在第一段出现过，
            //   之后三次重进世界都没回来 —— 因为 `TeamJoinGate.Attach()` 写在 `_installed`
            //   的早退之后，`ModMain.Init` 第二次进来时压根走不到那一行。
            //   形状与此前的 `_tickRegistered` 一模一样：**守卫的寿命（全进程）比它守的东西
            //   （每世界的帧登记）长**。补丁确实只需打一次，帧回调必须每世界重新确保。
            if (_installed)
            {
                // 补丁已经在，但帧回调可能随上一个世界一起没了 ⇒ 每次都确保一次
                try { TeamJoinGate.Attach(); } catch { }
                return;
            }
            _installed = true;
            var note = new StringBuilder();
            try
            {
                var t = AccessTools.TypeByName("BattleFunction");
                if (t == null)
                {
                    InstallReport = "BattleFunction 类型未找到 → 容器直塞未启用，仍走条件组";
                    ModMain.P(Tag + " " + InstallReport);
                    return;
                }
                note.Append("BattleFunction=有;");

                // ★ 无条件枚举并打印全部同名方法签名 ★
                //   为什么必须无条件打：17:32 那次失败时日志只说"未找到"，看不出真机到底叫什么名，
                //   于是又得让用户重测一轮。现在把签名**全部**打进日志，一次启动就能核对。
                MethodInfo m = null;
                var allSigs = new List<string>();
                foreach (var cand in AccessTools.GetDeclaredMethods(t))
                {
                    if (!cand.Name.Contains("AddBattleNPCData")) continue;
                    var ps = cand.GetParameters();
                    allSigs.Add(cand.Name + "(" + string.Join(",", ps.Select(x => x.ParameterType.Name)) + ")");
                    // 兜底口径：只要名字对、参数个数 ≤1，就挂 —— 不再挑参数类型。
                    // （参数类型在 Il2Cpp 桥接下不可靠；Prefix 无参，也用不到它。）
                    if (m == null && ps.Length <= 1) m = cand;
                }
                note.Append("同名方法 " + allSigs.Count + " 个[" + string.Join(" / ", allSigs) + "];");

                if (m == null)
                {
                    InstallReport = note + " → 无可挂目标，仍走条件组";
                    ModMain.P(Tag + " " + InstallReport);
                    return;
                }
                note.Append("选中=" + m.Name + "(" + string.Join(",", m.GetParameters().Select(x => x.ParameterType.Name)) + ");");

                var pre = typeof(TeamContainerInject).GetMethod(nameof(Prefix),
                    BindingFlags.Static | BindingFlags.NonPublic);
                var harm = new HarmonyLib.Harmony("AgentLoopBridge.TeamInject");
                harm.Patch(m, prefix: new HarmonyMethod(pre));

                // ★挂载后自检★ Harmony Patch 不抛异常 ≠ 真的挂上了（Il2Cpp 下 detour 可能静默失败）。
                //   用 GetPatchInfo 把实际挂载的 owner 列表读回来 —— 这是"挂没挂上"的硬证据。
                string verify = "未确认";
                try
                {
                    var info = HarmonyLib.Harmony.GetPatchInfo(m);
                    if (info == null || info.Prefixes == null || info.Prefixes.Count == 0)
                        verify = "❌ PatchInfo 里没有 Prefix（挂载未生效）";
                    else
                    {
                        var owners = new List<string>();
                        foreach (var o in info.Prefixes) owners.Add(o.owner);
                        verify = "✅ Prefix 已登记 owner=[" + string.Join(",", owners) + "]";
                    }
                }
                catch (Exception ve) { verify = "自检异常:" + ve.Message; }

                // ══════════════════════════════════════════════════════════
                //  ★ 死亡钩子：只有 `OnUnitDie` 是必需品 ★
                // ══════════════════════════════════════════════════════════
                //
                // 真机日志（10-21 00:20 成功那次）统计：
                //   · `队友倒下（OnUnitDie）` × 2   ← **唯一真正抓到队友的**
                //   · `队友倒下（Die）`        × 0   ← 零命中
                //   · `队友倒下（Effect6009）`  × 0   ← 零命中
                //
                // 小怪的死亡是 `OnUnitDie` + `Die` **成对**出现；
                // 而队友死亡**只有 `OnUnitDie`，没有配对的 `Die`**
                // ⇒ 队友的死亡路径与小怪不同，`Die` 那条对我们毫无用处。
                //
                // ⇒ 现在：`OnUnitDie` 常挂；`Die` / `Effect6009` 只在 `DeepDeathHooks=1` 时挂。
                // `Die` 钩子：**默认不挂**（真机 0 命中，每次单位死亡白跑一次反射）。
                // 需要排查"队友是否走了别的死亡路径"时，把 DeepDeathHooks 置 true 再看日志。
                if (DeepDeathHooks)
                {
                    try
                    {
                        var uc = AccessTools.TypeByName("UnitCtrlBase");
                        var dieSigs = new StringBuilder();
                        int dieCount = 0;
                        if (uc != null)
                        {
                            foreach (var cand in AccessTools.GetDeclaredMethods(uc))
                            {
                                if (cand.Name != "Die") continue;
                                var ps = cand.GetParameters();
                                dieSigs.Append("UnitCtrlBase.Die(")
                                      .Append(string.Join(",", ps.Select(x => x.ParameterType.Name)))
                                      .Append(");");
                                dieCount++;
                                var dpost = typeof(TeamContainerInject).GetMethod(nameof(DeathPostfix),
                                    BindingFlags.Static | BindingFlags.NonPublic);
                                harm.Patch(cand, postfix: new HarmonyMethod(dpost));
                            }
                        }
                        var uch = AccessTools.TypeByName("UnitCtrlHuman");
                        if (uch != null)
                        {
                            foreach (var cand in AccessTools.GetDeclaredMethods(uch))
                            {
                                if (cand.Name != "Die") continue;
                                var ps = cand.GetParameters();
                                dieSigs.Append("UnitCtrlHuman.Die(")
                                      .Append(string.Join(",", ps.Select(x => x.ParameterType.Name)))
                                      .Append(");");
                                dieCount++;
                                var dpost = typeof(TeamContainerInject).GetMethod(nameof(DeathPostfix),
                                    BindingFlags.Static | BindingFlags.NonPublic);
                                harm.Patch(cand, postfix: new HarmonyMethod(dpost));
                            }
                        }
                        note.Append("死亡钩子(Die)=" + dieCount + ";Die 签名[" + dieSigs + "];");
                    }
                    catch (Exception de) { note.Append("死亡钩子异常=" + de.Message + ";"); }
                }
                else note.Append("死亡钩子(Die)=跳过(0命中);");

                try
                {
                    var bu = AccessTools.TypeByName("BattleUnitMgr");
                    MethodInfo onDie = null;
                    if (bu != null)
                    {
                        foreach (var cand in AccessTools.GetDeclaredMethods(bu))
                        {
                            if (cand.Name != "OnUnitDie") continue;
                            if (cand.GetParameters().Length == 1) { onDie = cand; break; }
                        }
                    }
                    if (onDie != null)
                    {
                        var opost = typeof(TeamContainerInject).GetMethod(nameof(OnUnitDiePostfix),
                            BindingFlags.Static | BindingFlags.NonPublic);
                        harm.Patch(onDie, postfix: new HarmonyMethod(opost));
                        note.Append("OnUnitDie 钩子=已挂;");
                    }
                    else note.Append("OnUnitDie 钩子=未找到;");
                }
                catch (Exception oe) { note.Append("OnUnitDie 钩子异常=" + oe.Message + ";"); }

                // ★ 兜底 B：`Effect6009.<Create>b__0_0` @0x3522C60 —— **最准的一条** ★
                //
                //   ⚠ 千万别挂 `Effect6009.Create`（10-20 差点踩死）：
                //     `Create` @0x3522B80 只是**注册死亡回调**，不是死亡事件：
                //       183522bc0: mov r14,[rsi+0x18]     ; r14 = this.unit (EffectBase.unit)
                //       183522bda: mov rdi,[r14+0x18]     ; rdi = unit.onDieCall
                //       183522c04: call Delegate::Combine ; 把 b__0_0 挂上去
                //       183522c31: mov [r14+0x18],rdx     ; 写回 unit.onDieCall
                //     而 `DungeonTrigger MID&002` 是 triggerType=14「**开始战斗后**间隔触发」，
                //     开战就把 6009 挂到 3/4/5 号位 —— 挂 `Create` 会在**开战瞬间**
                //     把全部队友记成"倒下" ⇒ 全员误判重伤 ⇒ 全员自动离队。灾难。
                //
                //   `b__0_0` @0x3522C60 才是**真正的死亡回调**（被 Combine 上去的那个 Action）：
                //       183522cbb: mov rax,[rax+0x68]     ; SceneType.battle
                //       183522cc4: cmp BYTE PTR [rax+0x10],0 ; 死亡单位 isDie
                //       183522cd5: call 0x1840C9370        ; EffectDataBase 取 unit
                //       183522d08: jmp DramaTool.OpenDrama(string, null)
                // `Effect6009.b__0_0`：**默认不挂**（真机 0 命中）。
                // 它是"如果队友走了完全不同的死亡路径"时的兜底线索，
                // 排查时置 DeepDeathHooks=true 可重新打开。
                if (DeepDeathHooks)
                try
                {
                    var e9 = AccessTools.TypeByName("Effect6009");
                    MethodInfo onDieCb = null;
                    var sigs = new StringBuilder();
                    if (e9 != null)
                    {
                        foreach (var cand in AccessTools.GetDeclaredMethods(e9))
                        {
                            if (!cand.Name.Contains("b__0_0")) continue;
                            var ps = cand.GetParameters();
                            sigs.Append("b__0_0(").Append(string.Join(",", ps.Select(x => x.ParameterType.Name))).Append(");");
                            if (onDieCb == null) onDieCb = cand;
                        }
                    }
                    if (onDieCb != null)
                    {
                        var post = typeof(TeamContainerInject).GetMethod(nameof(Effect6009Postfix),
                            BindingFlags.Static | BindingFlags.NonPublic);
                        harm.Patch(onDieCb, postfix: new HarmonyMethod(post));
                        note.Append("Effect6009 死亡回调=已挂;");
                    }
                    else note.Append("Effect6009 死亡回调=未找到[" + sigs + "];");
                }
                catch (Exception ee) { note.Append("Effect6009 死亡回调异常=" + ee.Message + ";"); }
                else note.Append("Effect6009 死亡回调=跳过(0命中);");

                // ★ 战场单位登记：`BattleUnitMgr.AddUnit(UnitCtrlBase, UnitType)` Postfix ★
                //
                //   目的：**攥住队友的战场单位对象引用**，供 `SweepDeadBattleUnits`
                //   战后直接查 `isDie` —— 这条不依赖任何死亡回调，是最后一道保险。
                //
                //   ⚠ 10-21 清理死代码时这块曾被误删（当时它只在 lowhp 测试里用），
                //     但它的登记逻辑现在是 `[sweep]` 的唯一数据来源。删了会看到
                //     `[sweep] 本场没有登记到队友战场单位`。
                try
                {
                    var bu = AccessTools.TypeByName("BattleUnitMgr");
                    MethodInfo addUnit = null;
                    if (bu != null)
                    {
                        foreach (var cand in AccessTools.GetDeclaredMethods(bu))
                        {
                            if (cand.Name != "AddUnit") continue;
                            if (cand.GetParameters().Length == 2) { addUnit = cand; break; }
                        }
                    }
                    if (addUnit != null)
                    {
                        var apost = typeof(TeamContainerInject).GetMethod(nameof(AddUnitPostfix),
                            BindingFlags.Static | BindingFlags.NonPublic);
                        harm.Patch(addUnit, postfix: new HarmonyMethod(apost));
                        note.Append("AddUnit 登记钩子=已挂;");
                    }
                    else note.Append("AddUnit 登记钩子=未找到;");
                }
                catch (Exception ae) { note.Append("AddUnit 登记钩子异常=" + ae.Message + ";"); }

                // ★ 190 立绘/重伤修正：挂 **UIDramaBase.InitData(int, DramaData)** ★
                //
                //   10-20 raw 定案（详见 docs/features/team.md §5.4；逐条 VA 在
                //   docs/archive/2026-10-19-队友重伤与死亡链路.md §11）：
                //
                //   `Effect6009.<Create>b__0_0` @0x3522C60 尾部
                //       xor edx,edx          ; DramaData = **null**
                //       jmp 0x18463F1F0      ; DramaTool.OpenDrama(string, DramaData)
                //   ⇒ 6009 调的是 **string 重载**，而且 data 传 null。
                //     上一版挂 `OpenDrama(int)` @0x463F740 —— 6009 根本不走那条，等于没挂。
                //
                //   `OpenDrama(string)` 在 0x18463F5C1 调 `OpenDramaInID(int, DramaData)`；
                //   `OpenDramaInID` 里 `[rdi+0x10]==null` 就 new 一个**空 DramaData**。
                //
                //   最终落点 = `UIDramaBase.InitData(int, DramaData)` @0x32FA590：
                //       1832fa8f8: this.dramaData = data
                //       1832fa949: InitCondition(dramaData)
                //       1832fa964: OptionsFunctionDramaData(item.openFunction, dramaData)
                //   ⇒ 在 Prefix 里填 `unitLeft/unitRight/unit`，
                //     立绘（读 DramaData）与气运（经 functionData 回填）**一次修好**。
                try
                {
                    var ub = AccessTools.TypeByName("UIDramaBase");
                    MethodInfo initData = null;
                    var sigs = new List<string>();
                    if (ub != null)
                    {
                        foreach (var cand in AccessTools.GetDeclaredMethods(ub))
                        {
                            if (cand.Name != "InitData") continue;
                            var ps = cand.GetParameters();
                            sigs.Add("InitData(" + string.Join(",", ps.Select(x => x.ParameterType.Name)) + ")");
                            if (ps.Length == 2 && ps[0].ParameterType == typeof(int)) initData = cand;
                        }
                    }
                    note.Append("UIDramaBase.InitData 签名[" + string.Join(" / ", sigs.ToArray()) + "];");
                    if (initData != null)
                    {
                        // ★ Prefix：`InitData` 里
                        //   ① 0x1832FA8F1 才把 `dramaData` 写进 `this+0xA8`
                        //   ② 0x1832FA949 调 `InitCondition(dramaData)`
                        //   ③ 0x1832FA964 调 `OptionsFunctionDramaData(item.openFunction, dramaData)`
                        //   ⇒ Prefix 阶段 `__2` 就是我们传进来的那个 `DramaData`，
                        //     在它被 `InitCondition` 消费**之前**填好 unitLeft/unitRight。
                        var dpre = typeof(TeamContainerInject).GetMethod(nameof(DramaInitDataPrefix),
                            BindingFlags.Static | BindingFlags.NonPublic);
                        harm.Patch(initData, prefix: new HarmonyMethod(dpre));
                        note.Append("UIDramaBase.InitData 190 守卫=已挂(Prefix);");
                    }
                    else note.Append("UIDramaBase.InitData 190 守卫=未找到;");
                }
                catch (Exception oe)
                {
                    note.Append("UIDramaBase.InitData 190 守卫异常=" + oe.Message + ";");
                }

                // ★★ 190 立绘真正的修点：`UIDramaDialogue.UpdateUnit` ★★
                //
                // ## 为什么光写 DramaData 不够（10-20 二次 raw 定案，逐条可复核）
                //
                // 配表 `DramaDialogue MID&190` 是 `npcLeft=1 / npcRight=11`。
                // `UpdateUI` @0x32FF5F0 两次调 `UpdateUnit`：
                //   左 = id[npcLeft=1]，右 = id[npcRight=11]
                //
                // 而 `UpdateUnit` @0x3300D80 的 **id==1 分支根本读不到 DramaData**：
                // ```text
                //   183301428: cmp DWORD PTR [rax+0x48],0x1     ; id == 1 ?
                //   183301438: jne 0x183301459
                //   18330143c: call 0x183f877c0                 ; WorldMgr 访问器
                //   18330144a: mov rdx,[rax+0x10]               ; rdx = g.world.playerUnit
                //   183301451: mov QWORD PTR [rdi],rdx          ; **左位 = 玩家，硬编码**
                // ```
                // ⇒ **左位永远是玩家**，无论往 `DramaData.unitLeft` 里写谁。
                //   这就是「立绘是玩家不是队友」的真正原因 —— 不是 unitLeft 为空，
                //   而是**左侧那个 id=1 压根不看 unitLeft**。
                //
                // ## 本钩子做什么
                //
                // 只在 **190 这一页** 把 `id==1` 改写成 `11`：
                //   `11 → dramaData.unitLeft`（0x1833014f3: `mov rdx,[rdx+0x10]`）
                // 于是左位改读 `unitLeft` —— 而 `DramaInitDataPrefix` 已经把
                // `unitLeft` 写成倒下的队友。
                //
                // ⚠ 只对 `item.id == ModIds.DramaTeamHurtBase` 生效，别的剧情页一律放行。
                try
                {
                    var ud = AccessTools.TypeByName("UIDramaDialogue");
                    MethodInfo updateUnit = null;
                    var usig = new StringBuilder();
                    if (ud != null)
                    {
                        foreach (var cand in AccessTools.GetDeclaredMethods(ud))
                        {
                            if (cand.Name != "UpdateUnit") continue;
                            var ps = cand.GetParameters();
                            usig.Append("UpdateUnit(")
                                .Append(string.Join(",", ps.Select(x => x.ParameterType.Name)))
                                .Append(");");
                            // 10 参、下标 8 是 int id
                            if (updateUnit == null && ps.Length == 10 && ps[8].ParameterType == typeof(int))
                                updateUnit = cand;
                        }
                    }
                    note.Append("UIDramaDialogue.UpdateUnit 签名[" + usig + "];");
                    if (updateUnit != null)
                    {
                        var upre = typeof(TeamContainerInject).GetMethod(nameof(DramaUpdateUnitPrefix),
                            BindingFlags.Static | BindingFlags.NonPublic);
                        harm.Patch(updateUnit, prefix: new HarmonyMethod(upre));
                        note.Append("190 立绘左位改写=已挂(Prefix);");

                        // 0/0 页立绘位：**单独一次 Patch**（带了 __9/__4 两个位置参数）。
                        // 绑定失败只让这一支不挂，190 那条已经挂上了，不受牵连。
                        try
                        {
                            var spre = typeof(TeamContainerInject).GetMethod(nameof(DramaUpdateUnitSlotPrefix),
                                BindingFlags.Static | BindingFlags.NonPublic);
                            harm.Patch(updateUnit, prefix: new HarmonyMethod(spre));
                            note.Append("0/0 立绘位=已挂(Prefix);");
                        }
                        catch (Exception se)
                        {
                            note.Append("0/0 立绘位=挂载失败(" + se.GetType().Name + ":" + se.Message + ");");
                        }
                    }
                    else note.Append("190 立绘左位改写=未找到;");
                }
                catch (Exception ue)
                {
                    note.Append("190 立绘左位改写异常=" + ue.Message + ";");
                }

                InstallReport = note + "★容器直塞=已挂★｜" + verify;
                ModMain.P(Tag + " " + InstallReport);

                // ★ 决斗参战（10-25）：确认窗那条链的安装点 ★
                //   为什么挂在**这里**而不是 `ModMain`：本方法的调用点已经在启动链上，
                //   而 `ModMain` 是并行改动最密集的文件之一 —— 少一个改点就少一次互相覆盖的机会。
                //   它自己注册帧回调 + `IntoBattle` 补丁，失败只打日志，绝不影响本类。
                try { TeamJoinGate.Attach(); }
                catch (Exception jex) { ModMain.P(Tag + " 决斗参战模块安装失败（不影响副本直塞）: " + jex.Message); }
            }
            catch (Exception e)
            {
                InstallReport = "挂载失败: " + e.Message;
                ModMain.P(Tag + " 失败（不影响其它补丁，仍走条件组选人）: " + e.Message);
            }
        }

        /// <summary>
        /// 前缀：把队伍成员按号位写进 npcUnits，然后跳过原方法。
        /// 返回 false = 跳过原方法；返回 true = 照跑原方法。
        /// ★ 任何异常都必须返回 true 放行 —— 炸掉这里等于炸掉引擎的战斗初始化。
        /// </summary>
        /// <summary>
        /// 把队伍成员按号位写进 `battle.data.npcUnits`，供后续 `addBattleUnit_3/4/5` 按位取人。
        /// 返回 false = 跳过原方法（原方法 = 条件组选人，type=2 气运下会抛）；true = 照跑原方法。
        ///
        /// 本方法是 10-25 从 `Prefix` 里原样抽出来的（两处调用：副本 / 决斗+同意）。
        /// **抽出来时一个字符的业务逻辑都没改** —— 只把 `nu`/`mates`/`dupSlots` 变成参数。
        /// </summary>
        private static bool InjectInto(WorldBattleData data, List<WorldUnitBase> mates, int dupSlots)
        {
            try
            {
                if (data == null) { ModMain.P(Tag + " InjectInto：battle.data 为空，放行原方法"); return true; }
                var nu = data.npcUnits;
                if (nu == null) { ModMain.P(Tag + " InjectInto：npcUnits 容器不可用，放行原方法"); return true; }
                if (mates == null || mates.Count == 0) { ModMain.P(Tag + " InjectInto：没有队友，放行原方法"); return true; }

                // ② 幂等：本 hook 会被 addBattleNPCData_0/_1/_2 三个 token 各触发一次。
                //    第一次注入后清掉标记气运之外的旧容器内容已在 Members 语义里；
                //    这里第二次起直接跳过（容器已有我们的人）。
                if (InjectCount > 0)
                {
                    ModMain.P(Tag + $" 第 {InjectCount + 1} 次 addBattleNPCData 到达，容器已注入，跳过");
                    return false;   // 继续跳过原方法（它只会重复/抛雷）
                }

                // ★ 关键诊断：Clear 前容器里原本有什么 ★
                //   剧情副本如果先用 addBattleNPCData 放了救助 NPC，这里就能看到；
                //   看不到则说明剧情 NPC 走的是 updateConfUnits / 其他入口。
                try
                {
                    var before = new List<string>();
                    int bn = 0;
                    try { bn = nu.Count; } catch { }
                    for (int i = 0; i < bn; i++)
                    {
                        WorldUnitBase u = null;
                        try { u = nu[i]; } catch { continue; }
                        string uid = "";
                        try { uid = u?.data?.unitData?.unitID ?? ""; } catch { }
                        before.Add(TeamTool.NameOf(u, "?") + "(" + uid + ")");
                    }
                    ModMain.P(Tag + " Clear 前 npcUnits Count=" + bn
                              + " [" + string.Join("、", before.ToArray()) + "]");
                }
                catch (Exception ex) { ModMain.P(Tag + " Clear 前读取异常：" + ex.Message); }

                // ③ 直塞：先清容器（等价 clearBattleNPCDatas_0 的语义），再按号位压入。
                //    ★去重三保险★（2026-10-19 答辩补强）：
                //    a) Members 每槽取第一人 ⇒ 同一人若身上挂了多枚号位气运也只进一次；
                //    b) 这里再按实例引用判重，同一 WorldUnitBase 绝不 Add 两次；
                //    c) 容器先 Clear ⇒ 副本自带旧容器内容/上次残留不可能与我方重复。
                try { nu.Clear(); } catch { }
                int put = 0;
                var names = new StringBuilder();
                var seen = new List<object>();   // 实例引用去重（兜底）
                var seenIds = new List<string>(); // ★ 主判据：unitID 去重 ★
                foreach (var u in mates)
                {
                    if (u == null) continue;

                    string uid = null;
                    try { uid = u.data?.unitData?.unitID; } catch { }
                    if (!string.IsNullOrEmpty(uid))
                    {
                        if (seenIds.Contains(uid))
                        {
                            ModMain.P(Tag + " 跳过重复 unitID: " + TeamTool.NameOf(u, "?") + "(" + uid + ")");
                            continue;
                        }
                        seenIds.Add(uid);
                    }

                    bool dup = false;
                    foreach (var prev in seen) { try { if (prev == u) { dup = true; break; } } catch { } }
                    if (dup) { ModMain.P(Tag + " 跳过重复成员: " + TeamTool.NameOf(u, "?")); continue; }
                    try { nu.Add(u); seen.Add(u); put++; names.Append(TeamTool.NameOf(u, "?")).Append(' '); }
                    catch (Exception ex) { ModMain.P(Tag + " Add 失败: " + ex.Message); }
                }
                InjectCount++;

                try
                {
                    var after = new List<string>();
                    int an = 0;
                    try { an = nu.Count; } catch { }
                    for (int i = 0; i < an; i++)
                    {
                        WorldUnitBase u = null;
                        try { u = nu[i]; } catch { continue; }
                        string uid = "";
                        try { uid = u?.data?.unitData?.unitID ?? ""; } catch { }
                        after.Add(TeamTool.NameOf(u, "?") + "(" + uid + ")");
                    }
                    ModMain.P(Tag + " Clear 后 npcUnits Count=" + an
                              + " [" + string.Join("、", after.ToArray()) + "]");
                }
                catch (Exception ex) { ModMain.P(Tag + " Clear 后读取异常：" + ex.Message); }

                ModMain.P(Tag + $" ★直塞完成★ 队员 {put} 人（dupSlots={dupSlots}）: {names}"
                    + $"｜容器 Count={nu.Count}｜后续 addBattleUnit_3/4/5 将按位取人（GetUnitWorld: id3..10 → Index(容器, id-3)）");
                // ★ 剧情账本：战斗区间起点（10-25）★
                //   为什么用"直塞完成"而不是 `BattleStart`：
                //     ① `BattleStart` 真机会**重复触发**（10-24 日志同一毫秒打了 5 次），要自己去重；
                //        这一行是我们的代码打的，一次就是一次；
                //     ② 语义正好对上 —— 单人战斗不写履历，而"有人被塞进容器"正是"这场要写履历"的前提。
                //   起点之后的所有剧情页/选项都会进账本，等 `BattleExit` 插上 end 钉子再一次性取出。
                try { DramaLedger.BeginWindow(""); }
                catch (Exception ledEx) { ModMain.P(Tag + " 账本战斗起点失败（不影响战斗）: " + ledEx.Message); }
                return false;   // 跳过原方法（原方法=条件组选人，type=2 气运下会抛）
            }
            catch (Exception e)
            {
                ModMain.P(Tag + " InjectInto 异常（放行原方法）: " + e.GetType().Name + " " + e.Message);
                return true;
            }
        }

        /// <summary>
        /// `UIDramaBase.InitData(int id, DramaData data)` 的**前缀** —— 190 立绘 + 重伤的唯一修点。
        ///
        /// ## 为什么必须是这里（10-20 raw 定案，逐条可复核）
        ///
        /// ```text
        /// UIDramaBase.InitData @0x32FA590
        ///   1832fa8f1: lea r15,[r13+0xa8]      ; r15 = &this.dramaData
        ///   1832fa8f8: mov [r15],rdi           ; this.dramaData = **我们传进来的 data**
        ///   1832fa949: call 0x1832FA480        ; InitCondition(dramaData)   ← 建 unitConditionData
        ///   1832fa960: mov rcx,[rcx+0x60]      ; item.openFunction
        ///   1832fa964: call 0x184634BD0        ; OptionsFunctionDramaData(openFunction, dramaData)
        /// ```
        ///
        /// `OptionsFunctionDramaData` @0x4634BD0：
        /// ```text
        ///   184634c24: mov r8,[rbx+0xE0]       ; r8 = dramaData.functionData  (DramaFunctionData)
        ///   184634c3d: call DramaFunction::OptionsFunction(function, functionData)
        /// ```
        ///
        /// `DramaFunction.AddNPCLuck` @0x223E270：
        /// ```text
        ///   18223e351: mov rcx,rbx             ; this = DramaFunction
        ///   18223e354: call GetUnitInType(type) ; → [DramaFunctionData+0x18] for type==1
        ///   18223e35f: je 0x18223E5CE          ; **target==null 直接 return，不报错**
        /// ```
        /// `GetUnitInType` @0x2253A90（`rax = DramaFunction.get_data()` = `DramaFunctionData`）：
        /// ```text
        ///   type 0 → [rax+0x10] = functionData.unitA
        ///   type 1 → [rax+0x18] = functionData.unitLeft     ← addNPCLuck_1 走这条
        ///   type 2 → [rax+0x20] = functionData.unitRight
        /// ```
        ///
        /// ## 两个对象别搞混（这是上一版修错的地方）
        ///
        /// | 谁 | 字段 | 谁读它 |
        /// |---|---|---|
        /// | `DramaData` | `unitLeft(+0x10)` / `unitRight(+0x18)` | **UI 立绘** `UIDramaDialogue.UpdateUnit` |
        /// | `DramaFunctionData` | `unitLeft(+0x18)` / `unitRight(+0x20)` | **`addNPCLuck` 挂气运** |
        ///
        /// `DramaData.set_unitLeft/set_unitRight` @0x223AA90/0x223AB20 会**单向回填**
        /// `functionData.unitLeft/unitRight`（仅当那边为 null）——
        /// 所以**先写 DramaData 就够两边都生效**，这正是本方法的做法。
        ///
        /// ## 为什么两侧都写
        ///
        /// 配表是 `npcLeft=1 / npcRight=11`。`UIDramaDialogue.UpdateUnit` 的 id 分支：
        /// ```text
        ///   id==1  → g.world.playerUnit          ← **1 = 玩家**！左位永远是玩家立绘
        ///   id==0  → 按 isLeft 取 unitLeft/unitRight，空则退回 dramaData.unit
        ///   id==11 → dramaData.unitLeft
        ///   id==12 → dramaData.unitRight
        /// ```
        /// 左侧配的是 1（玩家），右侧配 11（= unitLeft）。所以只改 unitRight 不够 ——
        /// 左位仍然画玩家。**两侧都写成倒下的队友**，`id` 走 0/11/12 任一条都画队友。
        /// </summary>
        private static void DramaInitDataPrefix(UIDramaBase __instance, int id, DramaData data)
        {
            try
            {
                // ★ 参数签名必须是**强类型**（10-20 真机踩过，别改回 object）★
                //   Harmony 按方法体里的 IL 类型做参数绑定；`InitData` 的第二个参数
                //   在引擎里是 `DramaData`，声明成 `object` 会让 Harmony 生成不出
                //   转接方法，报 **`IL Compile Error (unknown location)`**，
                //   整个 Prefix 静默不生效（日志里就是这个错，190 修了一版等于没修）。
                //   对照可用样板：`drama/DramaSegmentProbe.cs` 的
                //   `Postfix(UIDramaBase __instance, int id, DramaData data)` —— 一直好用。
                if (id != ModIds.DramaTeamHurtBase) return;
                if (data == null)
                {
                    ModMain.P(Tag + " 190 立绘修正跳过：InitData 拿不到 DramaData");
                    return;
                }

                var target = PickHurtMate();
                if (target == null)
                {
                    // 这里**必须显眼**：190 已经开了，却认不出该显示谁。
                    // 删掉兜底之后，这条日志就是"死亡识别坏了"的唯一信号。
                    // 沿着它查：`[isDie]` 行有没有 `队友倒下（OnUnitDie）`、
                    // `死亡钩子 N 人` 的 N 是否为 0。
                    ModMain.P(Tag + " ⚠ 190 立绘跳过：死亡钩子没记到倒下的队友"
                              + "（DeadTeamIds=" + DeadTeamIds.Count + " 人）"
                              + "—— 本页立绘/重伤会是错的，请查 [isDie] 与 [death] 日志");
                    return;
                }

                string nm = TeamTool.NameOf(target, "?");
                string uid = TeamTool.UnitIdOf(target);

                // ★ 只填**左槽**，右槽留空（10-21 用户实测定案）★
                //
                //   上一版把 unitLeft / unitRight 都写成同一人，于是：
                //     · 左右各画一个 → **同一个人出现两次**（用户截图确认）
                //     · 两个队友倒下时 190 开两次，但两次都填同一个人 → 只显示第一个
                //
                //   190 是「一个队友倒下」的单人剧情页，本就该只出现一个人。
                //   配表是 `npcLeft=1 / npcRight=11`，我们已把左位 id 1 改写成 11
                //   ⇒ 左右**都**读 `unitLeft`。所以只要填 `unitLeft`，两侧都会出人。
                //
                //   ⚠ 那为什么还要清 `unitRight`？因为若某天配表改回 `npcRight=12`
                //     （读 unitRight），旧值会残留在 `DramaData` 上，画出第二个人。
                //     显式置空 = 让"一个人"这个语义在两条配表下都成立。
                data.unitLeft = target;
                data.unitRight = null;
                // `set_unit` @0x223AB70 会**同时**写 +0x10/+0x18，会把 unitRight 又填上
                // ⇒ **不能调**。但 `DramaFunctionData` 那边还需要它：
                //    `addNPCLuck_1` 读 `functionData.unitLeft(+0x18)`，
                //    而 `set_unitLeft` @0x223AA90 已经把它回填了（仅当那边为 null）。
                try { data.unitLeftName = nm; data.unitRightName = ""; } catch { }

                ModMain.P(Tag + " 190 立绘修正 → 左=" + nm + "（右留空）"
                          + "（" + uid + "，死亡钩子 " + DeadTeamIds.Count + " 人）");
            }
            catch (Exception e)
            {
                ModMain.P(Tag + " 190 立绘修正异常: " + e.GetType().Name + " " + e.Message);
            }
        }

        /// <summary>
        /// `UIDramaDialogue.UpdateUnit(..., int id, bool isLeft)` 的**前缀** ——
        /// 只在 190 页把 `id==1`（硬编码=玩家）改写成 `id==11`（= `dramaData.unitLeft`）。
        ///
        /// `ref int __8`：Harmony 支持用 `ref` 前缀参数**回写**原方法的入参。
        /// 前 8 个参数是 Unity 组件（`RawImage`/`GameObject`/`Text`/`Button`），
        /// 强类型声明会把它们全拉进编译期依赖；只改一个 int，用编号最稳。
        /// </summary>
        private static void DramaUpdateUnitPrefix(object __instance, ref int __8)
        {
            try
            {
                if (__8 != 1) return;                      // 只拦「玩家」这一支
                var ui = __instance as UIDramaBase;
                if (ui == null) return;
                var it = ui.item;
                if (it == null || it.id != ModIds.DramaTeamHurtBase) return;  // 只认 190
                __8 = 11;                                  // → dramaData.unitLeft

                // ★ 自证：这一行必须出现在 190 打开的日志里 ★
                //   若"立绘仍是玩家"且**没有**这行 ⇒ ref 回写没生效（Harmony 版本差异），
                //   改走 Postfix + 直接重设 UI，别再盲猜。
                ModMain.P(Tag + " 190 立绘：左位 id 1→11（改读 unitLeft）");
            }
            catch { }
        }

        /// <summary>
        /// 同一个 `UpdateUnit(..., id, isLeft)` 的第二个前缀：**配表让这一侧「运行时取人」(id==0) 时，
        /// 按侧别取自己的人**。
        ///
        /// 真机 23205→23206（同一段话第一、二句）：第一页正常，第二页两侧都画成左位那个人
        /// （名签也同名），而 `DramaData.unitLeft/unitRight` 全程是两个不同的人。
        /// 脏值发生在 `id==0` 的解析里 —— 在 InitData 之前把 `unitLeftID/unitRightID` 归零压不住
        /// （那时它们本来就是 0），所以改在解析点：直接给 11（unitLeft +0x10）/ 12（unitRight +0x18），
        /// 谁写脏都不影响。（id 语义见 190 段文档与 0x3300D80 反编。）
        ///
        /// 判据只看**这次调用拿到的 id**：非 0 说明配表已把这一侧写死（1=玩家、11=unitLeft、
        /// 12=unitRight…），一律不动；0 才是「运行时取人」，此时才按侧别改写。
        /// 两侧单位为空或本来是同一个人时也不动（单人页/独白页）。
        ///
        /// 侧别优先按「这次真正要刷的名签」判定（`__4` 与 `textLeftName/textRightName` 比指针），
        /// 游戏传的 `isLeft` 只作兜底 —— 槽位是实参决定的。
        ///
        /// 单独一个方法、单独 try/catch 挂载：参数绑定一旦失败只影响本支，190 那条不受牵连。
        /// </summary>
        private static void DramaUpdateUnitSlotPrefix(object __instance, ref int __8, bool __9, Text __4)
        {
            try
            {
                if (__8 != 0) return;                              // 配表写死了这一侧 ⇒ 不动
                var ui = __instance as UIDramaBase;
                if (ui == null) return;
                var it = ui.item;
                if (it == null) return;
                var dd = ui.dramaData;
                if (dd == null) return;
                var lu = dd.unitLeft;
                var ru = dd.unitRight;
                if (lu == null || ru == null) return;              // 单人页/独白页不动
                string lid = "", rid = "";
                try { lid = DramaNativeClaim.UidOf(lu) ?? ""; } catch { }
                try { rid = DramaNativeClaim.UidOf(ru) ?? ""; } catch { }
                if (string.IsNullOrEmpty(lid) || lid == rid) return;   // 两侧本来就是同一个人

                bool isLeftSlot = __9;
                string sideVia = "isLeft";
                try
                {
                    var dlg = ui.TryCast<UIDramaDialogueBase>();
                    if (dlg != null && __4 != null)
                    {
                        var tl = dlg.textLeftName;
                        var tr = dlg.textRightName;
                        if (tl != null && __4.Pointer == tl.Pointer) { isLeftSlot = true; sideVia = "名签左"; }
                        else if (tr != null && __4.Pointer == tr.Pointer) { isLeftSlot = false; sideVia = "名签右"; }
                    }
                }
                catch { }

                int want = isLeftSlot ? 11 : 12;
                __8 = want;
                ModMain.P("[DramaAiOption] 立绘位强制 page=" + it.id + " " + (isLeftSlot ? "左" : "右")
                          + " id 0→" + want + "（" + sideVia + "；" + lid + " / " + rid + "）");
            }
            catch { }
        }

        /// <summary>
        /// 挑「这一次真正倒下的那个队友」当 190 的主角。
        ///
        /// ## 唯一依据：`DeadTeamIds`（死亡钩子记下的）
        ///
        /// 按「本轮尚未当过 190 主角」筛一遍：
        ///   · 第一个倒下 → 选他 → 标记已用
        ///   · 第二个倒下 → 选另一个 → 标记已用
        /// 于是表对每个人各挂一次气运，不会重复挂同一个人。
        ///
        /// ## 10-21：兜底已删除（这是关键，别加回来）
        ///
        /// 旧实现末尾有「取本场第一个号位队友」的兜底。它在本体识别失败时会
        /// **假装成功**：真机日志里两人各开一页、各挂一次、各离队，看起来完美，
        /// 但两行都写着 `死亡钩子 0 人` —— 根本没识别出谁倒下，只是按队伍顺序
        /// 恰好取到了两个不同的人。
        ///
        /// ⇒ 现在只认死亡记录。认不出就返回 null，让上层打⚠日志。
        ///   **宁可不开页，也不要挂着错的判断装成功。**
        /// </summary>
        private static WorldUnitBase PickHurtMate()
        {
            // ① 死亡钩子记下的，优先「还没当过 190 主角」的
            foreach (var id in DeadTeamIds)
            {
                var u = FindTeamUnit(id);
                if (u == null || TeamTool.IsPlayer(u)) continue;
                string k = TeamTool.UnitIdOf(u);
                if (string.IsNullOrEmpty(k)) { try { k = u.data?.unitData?.unitID ?? ""; } catch { } }
                if (k.Length > 0 && _usedAsDramaLead.Contains(k)) continue;   // 已经给他开过页
                if (k.Length > 0) _usedAsDramaLead.Add(k);
                return u;
            }

            // ② 兜底已删除（10-21 用户要求）
            //
            // ## 为什么必须删（这条兜底会**掩盖 bug**，比没有更糟）
            //
            // 旧兜底是「取第一个还没当过主角的号位队友」。它的危害在真机日志里
            // 表现为"看起来全对"：
            // ```text
            // 190 立绘修正 → 左=关欣远（右留空）（OtjPFK，死亡钩子 0 人）
            // 190 立绘修正 → 左=东方亦玉（右留空）（vmBqoH，死亡钩子 0 人）
            // 重伤处理：表挂 2，代码补挂 0，离队 2，失败 0
            // ```
            // 两人各开一页、各挂一次、各离队 —— 结果看似完美，
            // 但两次都写着 `死亡钩子 0 人`，说明**根本没有识别出谁倒下**，
            // 只是按队伍顺序恰好取了两个不同的人。
            //
            // ⇒ 若那一局只有关欣远倒地、东方亦玉安然无恙，
            //   兜底仍会给东方亦玉开页、挂重伤、让他离队 —— **误判且看不出异常**。
            //
            // 删掉后：认不出死亡 ⇒ `PickHurtMate` 返回 null ⇒
            //   日志打「190 立绘修正跳过：找不到倒下的队友」⇒ **问题立刻可见**。
            //   这才是正确的失败姿态：宁可不开页，也不要挂着错的判断装成功。
            ModMain.P(Tag + " 190 立绘修正跳过：死亡钩子没记到任何队友"
                      + "（不再兜底取号位队员，避免误挂）");
            return null;
        }

        /// <summary>已经当过 190 主角的队友（每个倒下的人只开一次页、只挂一次气运）。</summary>
        private static readonly HashSet<string> _usedAsDramaLead = new HashSet<string>();

        internal static void ClearDramaLead()
        {
            _usedAsDramaLead.Clear();
        }

        private static bool IsTeamUnit(object u)
        {
            try
            {
                var wu = u as WorldUnitBase;
                if (wu == null || TeamTool.IsPlayer(wu)) return false;
                string id = wu.data?.unitData?.unitID;
                if (string.IsNullOrEmpty(id)) return false;
                foreach (var m in TeamTool.Members(out _))
                {
                    string mid = null;
                    try { mid = m?.data?.unitData?.unitID; } catch { }
                    if (mid == id) return true;
                }
            }
            catch { }
            return false;
        }

        private static WorldUnitBase FindTeamUnit(string id)
        {
            if (string.IsNullOrEmpty(id)) return null;
            foreach (var m in TeamTool.Members(out _))
            {
                string mid = null;
                try { mid = m?.data?.unitData?.unitID; } catch { }
                if (mid == id) return m;
            }
            return null;
        }

        /// <summary>
        /// UnitCtrlBase.Die / UnitCtrlHuman.Die 的后缀：记录倒下的队友。
        /// 只记录，不改任何战斗逻辑。判据统一走 `RecordDeath`（带引用兜底）。
        /// </summary>
        private static void DeathPostfix(object __instance)
        {
            // `Die` 与 `OnUnitDie` 的**先后顺序与 isDie 值**，是区分
            // "①`OnUnitDie` 不是死亡事件" 和 "②读得太早" 的关键证据：
            // 若 `Die` 里 isDie 已 True 而 `OnUnitDie` 里是 False
            // ⇒ 两者语义不同（或 OnUnitDie 更早），统计不能只看其中一个。
            if (DeathDiag) DiagOnUnitDie(__instance, "Die");
            // 击杀统计：**只挂 `Die`（真死）**，理由见 BattleKillStats 的类注释。
            // `OnUnitDie` 是"离开战场缓存"，撤退/换场也会走，绝不能用来统计。
            BattleKillStats.OnUnitDie(__instance);
            RecordDeath(__instance, "Die");
        }

        /// <summary>
        /// 「这个战场单位是不是队友」——给 `BattleKillStats` 排除队友用。
        /// 直接复用 `TryResolveMate`（已含 worldUnitData / unitID / 引用 / 名字四路判据）。
        /// </summary>
        internal static bool IsTeammateBattleUnit(object unit)
        {
            WorldUnitBase mate; string uid;
            return TryResolveMate(unit, out mate, out uid);
        }

        /// <summary>
        /// `BattleUnitMgr.OnUnitDie(UnitCtrlBase)` @0x2B064C0 的后缀。
        /// 引擎内部"战场单位死亡"的统一入口，`Die` 钩子挂不上时靠它兜底。
        /// </summary>
        private static void OnUnitDiePostfix(object __0)
        {
            if (DeathDiag) DiagOnUnitDie(__0, "OnUnitDie");
            RecordDeath(__0, "OnUnitDie");
        }

        /// <summary>
        /// ══════════════════════════════════════════════════════════════════
        ///  `isDie` 语义诊断（10-21 加，**只读，不改任何状态**）
        /// ══════════════════════════════════════════════════════════════════
        ///
        /// ## 为什么必须查清（它挡着"击杀统计"这件事）
        ///
        /// 真机日志里 `OnUnitDie` 打出来的 `isDie` **一直是 False**：
        /// ```text
        /// OnUnitDie 非队友：UnitDataBase name="竹妖" ... isDie=False
        /// ```
        /// 这条钩子挂的是"单位死亡统一入口"，它自己却报告"没死"。
        /// 三种可能，不查清就无法定义统计口径：
        ///   ① `OnUnitDie` ≠ 死亡事件，可能只是"移出战场 / 死亡流程开始"；
        ///   ② 读 `isDie` 的时机太早（Postfix 仍在流程中，标志位还没置上）；
        ///   ③ `isDie` 读到的不是这个单位的真实状态。
        ///
        /// ⇒ ① 成立则**击杀统计不能用这个入口**（会把没死的算进去）；
        ///   ② 成立则统计要延后一拍读；③ 成立则要改读私有字段 `mIsDie`。
        ///
        /// ## 本诊断靠什么区分三者
        ///
        ///   · `isDie` **虚属性** 与 `mIsDie` **私有字段** 同时打（区分 ③）；
        ///   · `hp/maxHP`（hp&gt;0 却进了 OnUnitDie ⇒ 强烈指向 ①）；
        ///   · `onDieCall` 是否已挂（引擎是否已认定它进入死亡流程）；
        ///   · 运行时类型 + 名字 + unitID（决定能否用于聚合统计）。
        /// </summary>
        private static void DiagOnUnitDie(object unit, string via)
        {
            try
            {
                if (unit == null) { ModMain.P(Tag + "[isDie] " + via + " unit=null"); return; }

                string utype = unit.GetType().Name;
                object data = Refl.Get(unit, "data");
                string dtype = data == null ? "data=null" : data.GetType().Name;
                string nm = (Refl.Get(data, "name") as string) ?? "";

                object wud = Refl.Get(data, "worldUnitData");
                object ud = Refl.Get(wud, "unitData");
                string uid = (Refl.Get(ud, "unitID") as string) ?? "";

                string vDie = "?";
                vDie = (Refl.Get(unit, "isDie") ?? "?").ToString();

                // `mIsDie` 是 `UnitCtrlBase` 上的 **protected 原生字段**（不是属性）——
                // 只有这里该走 `GetField`，而且它会沿 BaseType 上找（`GetField` 不搜基类）。
                string fDie = (Refl.GetField(unit, "mIsDie") ?? "?").ToString();

                string cb = "?";
                cb = Refl.Get(unit, "onDieCall") == null ? "null" : "有";

                string hp = "?", mhp = "?";
                hp = (Refl.Get(data, "hp") ?? "?").ToString();
                mhp = (Refl.Get(data, "maxHP") ?? "?").ToString();

                ModMain.P(Tag + "[isDie] " + via + " " + utype + " " + dtype
                          + " name=\"" + nm + "\""
                          + (uid.Length > 0 ? " uid=" + uid : "")
                          + " ｜isDie属性=" + vDie + " mIsDie字段=" + fDie
                          + " onDieCall=" + cb
                          + " ｜hp=" + hp + "/" + mhp);
            }
            catch (Exception e)
            {
                ModMain.P(Tag + "[isDie] " + via + " 异常: " + e.GetType().Name + " " + e.Message);
            }
        }

        /// <summary>
        /// `Effect6009.<Create>b__0_0` @0x3522C60 的后缀 —— **最准的一条死亡记录**。
        ///
        /// 它就是 `Effect6009.Create` 通过 `Delegate::Combine` 挂到 `unit.onDieCall` 上的
        /// 那个 Action —— **只在单位真的死亡时**才被调用。`__instance` 是
        /// `Effect6009`（闭包捕获的 this），`EffectBase.unit`(+0x18) = 倒下的战场单位。
        ///
        /// ⚠ 不要改成挂 `Create`：`Create` 是"把回调挂上去"的那一刻（开战时），
        /// 挂了会把全部队友误判成倒下。
        /// </summary>
        private static void Effect6009Postfix(object __instance)
        {
            try
            {
                if (__instance == null) return;
                object unit = Refl.Get(__instance, "unit");
                RecordDeath(unit, "Effect6009");
            }
            catch (Exception e)
            {
                ModMain.P(Tag + "[death] Effect6009 记录异常: " + e.GetType().Name + " " + e.Message);
            }
        }

        /// <summary>
        /// 死亡记录的唯一入口（`OnUnitDie` 后缀调用）。
        ///
        /// 判据全部交给 `TryResolveMate`，本方法只负责"记下来 + 说清楚"。
        ///
        /// ## 10-21 定案（推翻了 10-20 的两条猜测）
        ///
        /// 之前以为是"`worldUnitData.unitData.unitID` 读不到"，于是加了引用兜底。
        /// 真正的根因是**对象类型不对**：所有死亡事件里那一批 `data=UnitDataBase`
        /// 是**小怪**（`name="竹妖"`/`"蛇"`/`"蜘蛛"`），而 `worldUnitData` 只存在于
        /// `UnitDataHuman`（+0x238）—— 在基类上找它，结构上永远找不到。
        ///
        /// 队友的真身是 `UnitDataHuman`，`OnUnitDie` 送进来时 `unitID` **是完整的**
        /// （`vmBqoH` / `3X52i9`），一次都没读空过。
        ///
        /// ⇒ 现在 `TryResolveMate` 第一件事就是**类型闸**：不是人形立刻放行，
        ///   小怪连一个字段都不会被读。
        /// </summary>
        private static void RecordDeath(object unit, string via)
        {
            try
            {
                if (unit == null) return;

                WorldUnitBase mate; string uid;
                if (!TryResolveMate(unit, out mate, out uid))
                {
                    // ★ 非队友死亡：默认**不打日志**（10-21 收尾）★
                    //   实测一场副本里小怪死亡能来几十条（`竹妖`/`蛇`/`蜘蛛`），
                    //   对功能零价值。只在 `ProbeOn` 且预算内时才打，且预算降到 3 条
                    //   —— 留一点点样本用于排查"为什么认不出"，又不刷屏。
                    //   ⚠ 预算判断放在 `DescribeUnit` **之前**：那个方法要反射读好几个字段，
                    //     小怪死亡频率很高，白跑它才是真正的性能浪费。
                    if (ProbeOn && _nonMateLogBudget > 0)
                    {
                        _nonMateLogBudget--;
                        ModMain.P(Tag + "[death] " + via + " 非队友：" + DescribeUnit(unit));
                    }
                    return;
                }

                string nm = TeamTool.NameOf(mate, "?");
                // unitID 读空时用 `name:` 前缀占位，下游按名字再对一次
                string key = string.IsNullOrEmpty(uid) ? ("name:" + nm) : uid;
                if (DeadTeamIds.Add(key))
                    ModMain.P(Tag + "[death] 队友倒下（" + via + "）" + nm
                              + "(" + (string.IsNullOrEmpty(uid) ? "unitID读空→按名字记" : uid)
                              + ")，已记录");
            }
            catch (Exception e)
            {
                ModMain.P(Tag + "[death] " + via + " 记录异常: " + e.GetType().Name + " " + e.Message);
            }
        }

        /// <summary>
        /// ② 时机的清理：`DeadTeamIds` + 日志预算。
        ///
        /// ⚠ **只在 `TeamBattleFollowup.OnBattleExit` 的 finally 调用**，不能挪进 `ResetForBattle`：
        ///   后者在 `BattleEndFront` **开头**跑，而同一拍里 190 立绘（`DramaInitDataPrefix`）
        ///   还要读 `DeadTeamIds` 认"该显示谁"。提前清 = 立绘永远找不到人。
        /// </summary>
        internal static void ClearDeadTeamIds()
        {
            DeadTeamIds.Clear();
            _nonMateLogBudget = 3;       // 每场重置预算
        }

        /// <summary>
        /// `BattleUnitMgr.AddUnit` 之后：把**队友**的战场单位对象登记下来。
        ///
        /// ⚠ 这条钩子的历史用途已经变了：早期它是 `lowhp` 测试（把队友 HP 强制设 1），
        ///   那个行为 10-21 已删除（让队友能被一击打倒本身就是反玩家行为）。
        ///   现在只剩**登记**这一件事 —— 供 `SweepDeadBattleUnits` 战后直接查 `isDie`。
        /// </summary>
        private static void AddUnitPostfix(object __0)
        {
            try
            {
                object unit = __0;
                if (unit == null) return;

                // 只做一件事：登记队友的战场单位对象引用，供 `SweepDeadBattleUnits` 战后查 isDie。
                //
                // ⚠ 10-21 真机发现这条**从来没登记成功过**（日志 `[sweep] 本场没有登记到
                //   队友战场单位`）。原因是 `AddUnit` 发生在战斗初始化早期，
                //   此时 `worldUnitData` 往往还没挂上 ⇒ `TryResolveMate` 的类型闸
                //   （要求 `UnitDataHuman`）能过，但 `worldUnitData` 读空 ⇒ 认不出。
                //
                // ⇒ 改成**按名字**登记：`UnitDataBase.name` 与队伍成员名直接比，
                //   不碰 `worldUnitData`。这条在 AddUnit 阶段是可用的。
                object data = Refl.Get(unit, "data");
                if (data == null) return;
                string dn = Refl.Get(data, "name") as string;
                if (string.IsNullOrEmpty(dn)) return;

                bool hit = false;
                foreach (var m in TeamTool.Members(out _))
                {
                    string mn = null;
                    try { mn = m?.data?.unitData?.propertyData?.GetName(); } catch { }
                    if (mn == dn) { hit = true; break; }
                }
                if (!hit) return;

                foreach (var x in _mateBattleUnits)
                    if (ReferenceEquals(x, unit)) return;      // 已登记过

                _mateBattleUnits.Add(unit);
                ModMain.P(Tag + "[battleunit] 登记 → " + dn
                          + "，本场累计 " + _mateBattleUnits.Count + " 个");
            }
            catch (Exception e)
            {
                ModMain.P(Tag + "[battleunit] 登记异常: " + e.GetType().Name + " " + e.Message);
            }
        }

        /// <summary>
        /// 战后兜底：直接查登记过的战场单位 `isDie`，命中就记进 `DeadTeamIds`。
        ///
        /// 这条**不依赖任何回调**，是"队友到底有没有倒下"的最终事实来源。
        /// 由 `TeamBattleFollowup.SnapshotDeadMates` 在 BattleEndFront 调用。
        /// </summary>
        internal static int SweepDeadBattleUnits()
        {
            int n = 0;
            try
            {
                foreach (var u in _mateBattleUnits)
                {
                    if (u == null) continue;
                    bool die = false;
                    die = (bool)(Refl.Get(u, "isDie") ?? false);

                    // 登记时已按名字确认过是队友 ⇒ 这里直接取名字当 key。
                    // 战场上 `worldUnitData` 可能读空（见 AddUnitPostfix 注释），
                    // 但 `UnitDataBase.name` 一直可读。
                    object d = Refl.Get(u, "data");
                    string nm = (Refl.Get(d, "name") as string) ?? "?";

                    WorldUnitBase mate; string uid;
                    bool isMate = TryResolveMate(u, out mate, out uid);
                    if (isMate) nm = TeamTool.NameOf(mate, nm);
                    string key = isMate && !string.IsNullOrEmpty(uid) ? uid : ("name:" + nm);

                    if (die)
                    {
                        if (DeadTeamIds.Add(key)) n++;
                        ModMain.P(Tag + "[sweep] 战场单位倒下 → " + nm + "，已记入");
                    }
                    else
                    {
                        int hp = 0;
                        try
                        {
                            var f = d?.GetType().GetField("hp",
                                BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                            if (f != null) hp = (int)f.GetValue(d);
                        }
                        catch { }
                        ModMain.P(Tag + "[sweep] 战场单位存活 → " + nm + "(hp=" + hp + ")");
                    }
                }
                if (_mateBattleUnits.Count == 0)
                    ModMain.P(Tag + "[sweep] 本场没有登记到队友战场单位"
                              + "（AddUnit 钩子没挂上，或队友名与其战场单位名不一致）");
            }
            catch (Exception e)
            {
                ModMain.P(Tag + "[sweep] 异常: " + e.GetType().Name + " " + e.Message);
            }
            return n;
        }

        /// <summary>
        /// 「这个战场单位是不是队友？」——**全模块唯一判据**。
        ///
        /// ## 闸门（10-21 二次修正，第一版按类型名判是错的）
        ///
        /// `OnUnitDie` **不挑对象**：敌人、队友、召唤物死了都从这个入口过。
        /// 一场副本里小怪（竹妖/蛇/蜘蛛）能来几十次，必须尽早筛掉。
        ///
        /// 但**不能按类型名判**。第一版写 `typeName.EndsWith("UnitDataHuman")`，
        /// 真机立刻失败：队友倒下了却 `死亡钩子 0 人`。原因是队友的控制器是
        /// `UnitCtrlHumanNPC`（见 190 调用栈 `UnitCtrlHumanNPC:Die`），
        /// 其数据对象未必叫 `UnitDataHuman`。
        ///
        /// ⇒ 改成**能力判定**：能不能读到 `worldUnitData`。
        ///   · 读得到 ⇒ 是世界里的真人（队友 / 玩家 / NPC），继续认人；
        ///   · 读不到 ⇒ 小怪 / 召唤物 / 弹幕，直接放行，不再读任何字段。
        ///
        /// ## 认人的三条路（按可靠性）
        ///
        ///   ① `worldUnitData.unitData.unitID` 字符串精确相等（最快、最准）；
        ///   ② 比 `DataUnit.UnitInfoData` **对象引用**；
        ///   ③ `UnitDataBase.name` 名字比对（兜底）。
        ///
        /// 真机上队友的 `unitID` **一直是完整的**（`vmBqoH` / `3X52i9`），
        /// ① 就够；②③ 是给"战场克隆体的 unitData 不与世界单位共享"的情形留的后路。
        /// </summary>
        private static bool TryResolveMate(object unit, out WorldUnitBase mate, out string uid)
        {
            mate = null;
            uid = "";
            if (unit == null) return false;
            try
            {
                object data = Refl.Get(unit, "data");
                if (data == null) return false;

                // ★★ 类型闸（10-21 二次修正）★★
                //
                // ## 为什么不能按类型名判（第一版就栽在这）
                //
                // 第一版写的是 `if (!dt.EndsWith("UnitDataHuman")) return false;`，
                // 真机立刻打脸：**队友倒下了，却一条都没记到**（`死亡钩子 0 人`）。
                // 190 仍然开了（6009 触发，不经过这里），所以"看起来正常"，
                // 但填槽走的是"取第一个队友"兜底 —— 识别实际是坏的。
                //
                // 190 的调用栈给出了答案：
                // ```text
                //   UnitCtrlBase:Die → UnitCtrlHuman:Die → UnitCtrlHumanNPC:Die → UnitDataBase:AddHP
                // ```
                // ⇒ **队友的控制器是 `UnitCtrlHumanNPC`**，其数据对象未必叫 `UnitDataHuman`。
                //   按名字白名单判类型，必然漏掉 NPC 这一类。
                //
                // ## 三次修正定案（10-21，每一步都有真机日志背书）
                //
                // **v1**：按类型名 `EndsWith("UnitDataHuman")` ⇒ 队友是 `UnitCtrlHumanNPC`，
                //        被挡掉 ⇒ `死亡钩子 0 人`。
                // **v2**：改成"能读到 `worldUnitData`"就认 ⇒ 队友在**战场上**数据对象是
                //        `UnitDataBase`，**根本没有 `worldUnitData`** ⇒ 再次被挡掉 ⇒
                //        立绘不显示（`⚠ 190 立绘跳过：死亡钩子没记到倒下的队友`）。
                // **v3（本版）**：`worldUnitData` 只作**首选**，读不到时**按名字认人**。
                //
                // 真机证据（11:21 日志）：
                // ```text
                // [isDie] OnUnitDie UnitCtrlBase UnitDataBase name="祝月桃" isDie属性=False
                // Clear 后 npcUnits Count=2 [凌乐康(Okmfds)、祝月桃(RZgA0N)]
                // ```
                // 名字 `祝月桃` 与队伍成员**精确相等**，而 `worldUnitData` 读不到。
                // ⇒ 战场上唯一可靠的身份就是 `UnitDataBase.name`。
                //
                // 小怪不会被误判：`竹妖`/`蛇`/`蜘蛛`/`魔猿` 与队伍成员名永不相同。
                var team = TeamTool.Members(out _);

                object wud = Refl.Get(data, "worldUnitData");
                object ud = wud == null ? null : Refl.Get(wud, "unitData");
                uid = (Refl.Get(ud, "unitID") as string) ?? "";

                // ① unitID 精确相等
                if (!string.IsNullOrEmpty(uid))
                {
                    foreach (var m in team)
                    {
                        string mid = null;
                        try { mid = m?.data?.unitData?.unitID; } catch { }
                        if (mid == uid) { mate = m; return true; }
                    }
                }

                // ② 比 unitData 引用
                if (ud != null)
                {
                    foreach (var m in team)
                    {
                        object mud = null;
                        try { mud = m?.data?.unitData; } catch { }
                        if (mud == null || !ReferenceEquals(mud, ud)) continue;
                        mate = m;
                        if (string.IsNullOrEmpty(uid))
                        {
                            try { uid = m?.data?.unitData?.unitID ?? ""; } catch { }
                        }
                        return true;
                    }
                }

                // ③ `UnitDataBase.name` 名字比对 —— **战场上唯一可靠的身份判据**
                //
                //   走到这里有两种情况，都靠它兜住：
                //     a) `worldUnitData` 读不到（战场上的队友就是这种，数据对象是基类）
                //     b) `worldUnitData` 读得到，但 unitID / 引用都对不上
                //
                //   `name` 是 `UnitDataBase` 的**公开字段**，不经过世界单位，实测一直可读
                //   （`name="祝月桃"` 与队伍成员名精确相等）。
                //
                //   ⚠ 必须判空：真机日志里存在 `name=""` 的死亡事件
                //     （`[isDie] ... name="" isDie属性=True`），
                //     空串拿去比会与"名字为空的成员"误匹配。
                string dn = Refl.Get(data, "name") as string;
                if (!string.IsNullOrEmpty(dn))
                {
                    foreach (var m in team)
                    {
                        string mn = null;
                        try { mn = m?.data?.unitData?.propertyData?.GetName(); } catch { }
                        if (string.IsNullOrEmpty(mn) || mn != dn) continue;
                        mate = m;
                        if (string.IsNullOrEmpty(uid))
                        {
                            try { uid = m?.data?.unitData?.unitID ?? ""; } catch { }
                        }
                        return true;
                    }
                }
            }
            catch { }
            return false;
        }

        /// <summary>
        /// 只读诊断：把死亡事件看到的原始信息拼成一行（**不改任何状态**）。
        /// 10-21 加：真机上「认不出人」这件事必须能自证，
        /// 否则只能靠猜 —— 而这次猜错了两轮（以为是 unitID 空，实为类型不对）。
        /// </summary>
        private static string DescribeUnit(object unit)
        {
            try
            {
                if (unit == null) return "unit=null";
                object d = Refl.Get(unit, "data");
                string dt = d == null ? "data=null" : d.GetType().Name;
                string dn = (Refl.Get(d, "name") as string) ?? "";
                object w = Refl.Get(d, "worldUnitData");
                string wt = w == null ? "worldUnitData=null" : "worldUnitData=有";
                object u2 = Refl.Get(w, "unitData");
                string id = (Refl.Get(u2, "unitID") as string) ?? "";
                bool die = false;
                die = (bool)(Refl.Get(unit, "isDie") ?? false);
                return dt + " name=\"" + dn + "\" " + wt
                       + " unitID=\"" + id + "\" isDie=" + die;
            }
            catch (Exception e) { return "描述异常:" + e.Message; }
        }

        // ★ 无参 Prefix（2026-10-19 18:2x 定案）★
        //   Harmony 允许 prefix 不带任何参数。我们不需要读 args（不关心是哪个条件组），
        //   于是**彻底绕开**「string[] vs Il2CppStringArray」的参数类型转换问题 ——
        //   上一版本就是因为 Prefix(string[] __0) 的参数类型对不上而整条链路失效。
        private static bool Prefix()
        {
            try
            {
                // ★ 测试开关：剧情副本对照用，完全不碰 npcUnits ★
                if (NoInject)
                {
                    ModMain.P(Tag + " noinject=1，跳过容器直塞（保持原版 npcUnits）");
                    return true;
                }

                // ★ 10-21 删除「救助剧情不注入」分支（用户拍板）★
                //
                //   原设计（方案 A，10-19 21:57）：`IsRescueBattle()` 读 `battleCustomData` 的
                //   `rescue=1` / `NPC=300001~300004`；命中就放行原 `addBattleNPCData`、跳过队伍注入、
                //   拦掉队伍 `addBattleUnit_3/4/5`，把战斗位 3 留给救助 NPC（官方
                //   `DungeonSpecialRoom31` 的 `unitAnim_3_Die_1` 打的就是战斗位 3）。
                //
                //   为什么删：**这半年没有任何一局真机命中过它**。
                //   · 编译期证据 —— 该分支在 `bak_1019_2214` → `bak_1021_0030` 这些 DLL 里确实存在
                //     （`grep -c 救助战斗` = 2），之后才消失；
                //   · 运行期证据 —— 这几局（`runs/2026-10-19_222559`、`10-20_204755`、`10-20_230705`、
                //     `10-21_001653`）的 `Player.log` 里 `救助` **0 命中**，
                //     而同一批日志里 `Clear 前 npcUnits` 有命中 ⇒ Prefix 确实跑了，是守卫自己没命中。
                //     ⇒ `battleCustomData` 在这条路径上根本没有 `rescue`/`NPC` 两个 key。
                //   · 多出来的代价是每次 Prefix 白跑一次字典反射。
                //
                //   删掉之后救助副本走**原版**行为：队友照常参战、照常可能重伤
                //   （10-21 那次成功实测覆盖了这条路径）。
                //   ⚠ 若将来又有"救助 NPC 被队友挤掉/队友贴地"的反馈，**先拿日志证明
                //     `battleCustomData` 里真的有 `rescue`/`NPC`**，再决定是恢复守卫
                //     还是改走"检测战斗初始化后的 npcUnits 内容"——不要凭印象重新加回来。
                //     契约测试 `test_rescue_guard_stays_deleted` 钉住了这一点。
                // ══════════════════════════════════════════════════════════════
                //  ★ 决斗分支（type=40，攻击 / 寻仇）—— 10-25，见 docs/archive/2026-10-25-队友进决斗/ ★
                // ══════════════════════════════════════════════════════════════
                //
                // 为什么它必须排在最前面：决斗是一场**普通副本**（`dungeonID=11`、`type=40`），
                // 走的是同一套 `DungeonTrigger`。真机 10-25 的 `进入副本：11，等级：1` 就是它。
                // 区别只有一个：**带不带队友要玩家说了算**（应战窗上已经问过）。
                //
                // 三条口径（每条都对应一个曾经会静默出错的形态）：
                //  ① 同意才带，**没点也算没同意**（`JoinAnswer.None` 一律按"不带"）；
                //  ② "不带"**不能靠放行原方法** —— 原方法走 `ConfNpcCondition MID&003`，
                //     它的条件就是三枚号位气运（`feature_1_MID&51/55/56`），放行 = 把队友
                //     重新选出来塞进容器 = 变相同意。所以必须**清空容器 + return false**；
                //  ③ 本 hook 被 `addBattleNPCData_0/_1/_2` 三个 token 各调一次 ⇒ 必须幂等，
                //     否则第二次会把刚塞好的容器清掉。
                //
                //  ④ 本场到底是不是决斗（catch 里要用它决定 fail-open / fail-closed）
                bool isDuel = false;
                try
                {
                    var dBattle = g.world != null ? g.world.battle : null;
                    var dData = dBattle != null ? dBattle.data : null;
                    var ddbi = dData != null ? dData.dungeonBaseItem : null;
                    isDuel = ddbi != null && ddbi.type == ModIds.DungeonTypeDuel;

                    // ★ 幂等判据只在决斗上生效（10-26 复核改）★
                    //   旧写法把 `if (_decided) return false;` 放在**判类型之前**：万一上一场决斗
                    //   没正常收尾（读档/异常，两个清零点都没跑到），`_decided` 残留 true ⇒
                    //   **下一场普通副本也会被这里直接跳过** ⇒ 队友不进副本，而且只有一行
                    //   "本场已决定过（决斗）"的日志。收窄到"这一场确实是决斗"之后就不会误伤副本。
                    if (isDuel && _decided)
                    {
                        ModMain.P(Tag + " 本场已决定过（决斗），跳过后续 addBattleNPCData");
                        return false;
                    }
                    if (isDuel)
                    {
                        _decided = true;
                        var dMates = TeamTool.Members(out int dDup);
                        var dNu = dData.npcUnits;
                        var answer = TeamJoinGate.TakeConsent();
                        if (answer == JoinAnswer.Yes && dMates != null && dMates.Count > 0 && dNu != null)
                        {
                            ModMain.P(Tag + " 决斗：玩家已同意 ⇒ 按原有容器直塞（type=40 副本 id="
                                      + (ddbi != null ? ddbi.id.ToString() : "?") + "）");
                            return InjectInto(dData, dMates, dDup);
                        }
                        try { if (dNu != null) dNu.Clear(); }
                        catch (Exception cex) { ModMain.P(Tag + " 决斗：清空容器失败 " + cex.Message); }
                        ModMain.P(Tag + " 决斗：玩家未同意（" + answer + "，队=" + (dMates != null ? dMates.Count : 0)
                                  + "）⇒ 队友不参战（清空容器并跳过原方法）");
                        return false;
                    }
                }
                catch (Exception dex)
                {
                    ModMain.P(Tag + " 决斗分支异常: " + dex.GetType().Name + " " + dex.Message);
                    if (isDuel)
                    {
                        // ★ fail-closed（10-26 复核补）★
                        //   异常之后若掉回下面的副本分支，`mates` 非空就会直塞 —— 等于
                        //   "没问过玩家就把队友带进决斗"。宁可不带，也绝不绕过表态。
                        try
                        {
                            var b2 = g.world != null ? g.world.battle : null;
                            var d2 = b2 != null ? b2.data : null;
                            var nu2 = d2 != null ? d2.npcUnits : null;
                            if (nu2 != null) nu2.Clear();
                        }
                        catch { }
                        _decided = true;
                        ModMain.P(Tag + " 决斗分支异常 ⇒ 按未同意处理（不带队友，也不放行原方法）");
                        return false;
                    }
                }

                // ① 没组队就完全不干预（大多数战斗都是普通战斗）
                // ★判据口径★：用「面板层 addLuck」收集队员（纯数据读取，不经过
                // GetLuck → 不碰 allLuck 里的 null-data 雷）。HasLuck(GetLuck) 只作补充，
                // 且它自带 try/catch（TeamTool.HasLuck）——炸了按 false 算，不会把异常
                // 冒到引擎的战斗初始化里。
                var mates = TeamTool.Members(out int dupSlots);
                if (mates == null || mates.Count == 0) return true;

                var battle = g.world != null ? g.world.battle : null;
                var data = battle != null ? battle.data : null;
                if (data == null)
                {
                    ModMain.P(Tag + " battle.data 不可用，放行原方法");
                    return true;
                }

                var nu = data.npcUnits;
                if (nu == null)
                {
                    ModMain.P(Tag + " npcUnits 容器不可用，放行原方法");
                    return true;
                }

                // ② 幂等 + 直塞（抽成方法：决斗分支也要用它）
                return InjectInto(data, mates, dupSlots);
            }
            catch (Exception e)
            {
                ModMain.P(Tag + " 前缀异常（放行原方法）: " + e.GetType().Name + " " + e.Message);
                return true;
            }
        }

        /// <summary>
        /// ① 时机：`BattleEndFront` **开头**的"上一场残留"清理。
        ///
        /// 铁律：**这里只能清"上一场的东西"，不能清"本场还要用/还要报"的东西。**
        /// 本场战果要到 BattleEndFront 末尾（BattleExit 处理链）才输出，
        /// 在这里清 = 把刚打完的战果抹掉。
        ///
        /// 当前清：`InjectCount`（每场从 0 数）、`_usedAsDramaLead`（给下一场干净起点）。
        /// 当前**不**清：`DeadTeamIds`（本拍 190 立绘还要读）、击杀统计（末尾才报）。
        /// 完整契约见文件顶部「静态状态生命周期契约」表。
        /// </summary>
        /// <summary>
        /// **新一场开打那一拍**（`WorldBattleMgr.IntoBattle`，由 `TeamJoinGate` 报信）：
        /// 把"本场已决定"与注入计数清回起点。
        ///
        /// 为什么要在**开打**这一刻清，而不能只靠 `ResetForBattle`（它挂在 `BattleEndFront`）：
        /// 上一场若没有正常收尾（玩家中途退副本/读档/引擎异常），`BattleEndFront` 就不会来 ——
        /// 而 `_decided` 留着 true 的话，**下一场决斗永远不再注入**（队友怎么点都不来），
        /// 且一条报错都没有。这是本仓最忌的失败形态，所以再加一个"开打即清零"的自愈点。
        /// 两处都清是幂等的：正常流程里 `BattleEndFront` 已经清过一次，这里再清就是 0 → 0。
        /// </summary>
        internal static void OnBattleStarting()
        {
            _decided = false;
            InjectCount = 0;
        }

        internal static void ResetForBattle()
        {
            InjectCount = 0;
            _decided = false;          // 决斗那支的"本场已决定"（见字段注释）
            // ⚠ 击杀统计**不能**在这里清零（10-21 真机踩过）★
            //   本方法在 `BattleEndFront` **开头**被调用，而 `LogReport()` 在同一次
            //   BattleEndFront 的**末尾**（BattleExit 处理链）才读统计 ——
            //   在这里 Reset 等于把刚打完这一场的战果抹掉，日志永远「本场无击杀记录」。
            //   真机症状：明明打了 `竹妖`（`[death] Die ... isDie=True`），
            //   却打 `[KillStats] 本场无击杀记录`。
            //   ⇒ 清零改到 `TeamBattleFollowup.OnBattleExit` 的 finally，
            //     保证「统计 → 输出 → 写经历 → 清零」这个顺序。
            //
            // ★ 但这里要做一次"残留清理"（10-21 自查补上）★
            //   正常流程是 BattleExit 收口并清零。若玩家**中途退出副本/读档**，
            //   BattleExit 可能根本不触发 ⇒ 上一场的击杀会残留到下一场战报里。
            //   这里只清"已经输出过"的残留（`ResetIfReported`），
            //   不会误伤当前正在累积的这一场（那时 `_reported` 还是 false）。
            BattleKillStats.ResetIfReported();
            // 190 主角去重表按**场**清零：190 在战斗中打开（早于本方法），
            // 所以这里清 = 给下一场一个干净起点，不会影响本场已开的页。
            _usedAsDramaLead.Clear();
        }

        /// <summary>状态行（诊断面板用）。</summary>
        internal static string StatusLine()
        {
            return InstallReport + "｜本次战斗注入=" + InjectCount;
        }
    }
}
