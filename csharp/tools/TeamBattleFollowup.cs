/// <summary>
/// TeamBattleFollowup —— 组队打完副本之后的**两件收尾事**：
///   ① **归队**：把队友传送到玩家所在坐标（用户口径 10-18：组队状态下结束战斗后，
///      队员位置应与玩家一致）；
///   ② **留痕**：给参战各方写一条「我们一起下过副本」的经历，让 agent 日后自己知道
///      （经历是 agent 读得到的东西，见 `UnitSnapshot.BuildLogs` 那条链）。
///
/// ## 触发点为什么是 BattleEndFront + BattleExit 两个
/// 真机实测（`[TeamBattleEvent]` 那段日志 —— 该探针已于 10-21 删除，
/// 这几行是当时 `Player.log` 的原文引文，前缀保持原样以免失真）：
/// <code>
///   BattleEndFront  isBattle=True  npcUnits=3   ← 只有这一拍队友还在场上
///   BattleEnd       isBattle=False npcUnits=0
///   BattleExit      isBattle=False npcUnits=0
/// </code>
/// ⇒ **"这一场有没有队友参战"只能在 `BattleEndFront` 判**（`npcUnits` 里还是那三个人）；
///   而**动手**要等 `BattleExit`（最后发的一个），否则我们的传送会被游戏自己的
///   战后收场/离开副本逻辑盖掉。
///
/// ## 判据用「气运」而不是「npcUnits 非空」
/// 副本本来就自带 NPC，`npcUnits` 非空不代表有队友。所以逐个查 `TeamTool.SlotOf(u) >= 0`
/// （队友 = 身上挂着号位气运的人），与 `NpcCondition` 的 `feature_1_<气运id>` 完全同源。
///
/// ## 只做一件事、只改一处
/// 传送只用引擎动作 `UnitActionMoveNPC` + `CreateAction`（与 `movement:teleport` 同一条路，
/// 已真机验证过可用），**返回码非 0 就算没成功**，不谎报；并且**回读坐标复核**。
/// 不用 `UnitInfoData.SetPoint` 硬写坐标 —— 那条路绕过引擎的寻路/格子索引，风险未知。
///
/// ## 崩溃教训
/// 10-18 有一次 Harmony 挂配置热路径 + 启动期全程序集扫描导致**游戏启动闪退**。
/// 所以本文件：不用 Harmony，只用游戏自己的 `g.events`；回调里**全部 try/catch**，
/// 任何异常只打日志，绝不往外抛（事件回调里抛异常会打断游戏自己的分发循环）。
///
/// ## 委托必须存字段
/// 不留强引用 = 委托被 GC 回收 = 订阅了也永不回调（`ModMain` 里 `_intoWorldHandlerIl2Cpp`
/// 已经把这条坑写死）。
/// </summary>
#if AB_UI
using System;
using System.Collections.Generic;
using System.Reflection;
using System.Text;

namespace AgentLoopBridge
{
    internal static class TeamBattleFollowup
    {
        private const string Tag = "[TeamFollowup] ";

        /// <summary>挂载结果摘要（进日志，也供诊断行读）。</summary>
        internal static string InstallReport = "(未执行)";

        private static bool _installed;

        // ══════════════════════════════════════════════════════════════════════
        //  ★ 本场状态的生命周期（10-21 显式化，配合 TeamContainerInject 顶部那张总表读）★
        // ══════════════════════════════════════════════════════════════════════
        //
        //  **置位**一律在 `OnBattleEndFront`（唯一还能看到队友的一拍）；
        //  **消费 + 清理**一律在 `OnBattleExit` 的 finally。
        //
        //  为什么必须严格两段式：`BattleEndFront` 那一刻世界还在，能读到战场单位；
        //  到 `BattleExit` 时战斗对象已经在拆，什么都读不到了。
        //  历史上把这两件事混在同一个回调里，导致"归队时找不到玩家"这类假失败。
        //
        // | 字段 | 置位于 | 清于 |
        // |---|---|---|
        // | `_installed`   | ——（进程级一次性） | 从不清 |
        // | `InstallReport`| `EnsureInstalled`    | 从不清（覆盖写） |
        // | `_frontHandler`/`_exitHandler` | `EnsureInstalled` | 从不清（**强引用必须留**，否则委托被 GC ⇒ 订阅了也永不回调） |
        // | `_armed`       | `OnBattleEndFront`（有队友才 true） | `OnBattleExit` 一进来就置 false |
        // | `_mates`/`_mateNames`/`_deadIds`/`_hurtMates`/`_hurtIds` | `OnBattleEndFront` | `OnBattleExit` 的 finally |
        // | `_killKeyWarned` | `OnBattleEndFront`（重置 false） | 每场重置，只提示一次 |
        // | `_rounds`      | 递增计数 | 从不清（跨场累计，日志用） |
        // ══════════════════════════════════════════════════════════════════════

        // ★ 强引用必须留：不留 = 委托被 GC 回收 = 订阅了也永不回调
        private static Action<ETypeData> _frontHandler;
        private static Action<ETypeData> _exitHandler;

        /// <summary>本场是否有队友参战（`OnBattleEndFront` 置位，`OnBattleExit` 消费后立刻置 false）。</summary>
        private static bool _armed;

        /// <summary>本场参战的队友（BattleEndFront 抓的快照）。</summary>
        private static readonly List<WorldUnitBase> _mates = new List<WorldUnitBase>();
        private static readonly List<string> _mateNames = new List<string>();

        /// <summary>本场倒下的队友 unitID（BattleEndFront 从战场单位快照）。</summary>
        private static readonly HashSet<string> _deadIds = new HashSet<string>();

        /// <summary>本场重伤的队友（倒下的 + 已经带重伤气运的）。</summary>
        private static readonly List<WorldUnitBase> _hurtMates = new List<WorldUnitBase>();
        private static readonly HashSet<string> _hurtIds = new HashSet<string>();

        /// <summary>已处理的场次计数（日志用）。</summary>
        private static int _rounds;

        // ══════════════════════════════════════════════════════════════════════
        //  ★ 这场是什么战斗（10-25）★
        // ══════════════════════════════════════════════════════════════════════
        //
        // 判据 = `g.world.battle.data.dungeonBaseItem.type`（配表事实，不是从重载名推的）：
        //   · `40` = 决斗（攻击 / 寻仇）。真机实证：`Player.log` 的 `进入副本：11，等级：1`
        //     配上 `DungeonBase.json` id=11 → type=40。
        //   · 其它 = 副本（沿用现有文案）。
        //   · 取不到 dbi = 没有副本体系的战斗（切磋那类）→ 沿用现有文案（历史行为）。
        //
        // 为什么必须在 `BattleEndFront` 就定下来：到 `BattleExit` 时战斗对象已经在拆，
        // 那时候再读 dbi 可能读不到（与"归队时找不到玩家"同一类时序问题）。
        private const int KindUnknown = 0;
        private const int KindDungeon = 1;
        private const int KindDuel = 2;
        private static int _kind = KindUnknown;

        internal static void EnsureInstalled()
        {
            if (_installed) return;
            _installed = true;

            var note = new StringBuilder();
            try
            {
                if (g.events == null)
                {
                    InstallReport = "g.events 为空（世界未加载？）→ 跳过";
                    ModMain.P(Tag + InstallReport);
                    return;
                }

                _frontHandler = OnBattleEndFront;
                g.events.On(EBattleType.BattleEndFront, _frontHandler, 0, false);
                note.Append("BattleEndFront=已挂;");

                _exitHandler = OnBattleExit;
                g.events.On(EBattleType.BattleExit, _exitHandler, 0, false);
                note.Append("BattleExit=已挂;");

                InstallReport = note + "★战后归队+写经历=已挂★";
                ModMain.P(Tag + InstallReport);
            }
            catch (Exception e)
            {
                InstallReport = "订阅异常: " + e.GetType().Name + " " + e.Message;
                ModMain.P(Tag + InstallReport);
            }
        }

        // ══════════════════════════════════════════════════════════
        //  ① BattleEndFront —— 唯一还能看到队友的一拍，只做"点名"
        // ══════════════════════════════════════════════════════════

        private static void OnBattleEndFront(ETypeData e)
        {
            try
            {
                _armed = false;
                _mates.Clear();
                _mateNames.Clear();
                _deadIds.Clear();
                _hurtMates.Clear();
                _hurtIds.Clear();
                _killKeyWarned = false;      // 每场重置"击杀模板缺失"提示
                _kind = ReadBattleKind();    // ★ 本场是副本还是决斗（见常量区的判据）
                // ★ 先记下本场直塞次数，再复位 ★
                //   全员倒下的极端局里，npcUnits 可能一个队友都不剩；
                //   只要本场确实注入过，就必须按号位气运把全队补回来判重伤。
                int injected = TeamContainerInject.InjectCount;
                TeamContainerInject.ResetForBattle();   // ★战斗收口：下一直塞从 0 计数

                var nu = g.world?.battle?.data?.npcUnits;
                if (nu == null) return;

                int n = 0;
                try { n = nu.Count; } catch { return; }

                // 同一人可能被塞进容器两次（老配置 `addBattleNPCData` 不去重）
                // ⇒ 按 unitID 去重，别对同一个人传两次、写两条一样的经历。
                var seenIds = new List<string>();
                for (int i = 0; i < n; i++)
                {
                    WorldUnitBase u = null;
                    try { u = nu[i]; } catch { continue; }
                    if (u == null) continue;
                    if (TeamTool.IsPlayer(u)) continue;        // 玩家不会是他的队友

                    // 判据 = 号位气运。副本自带的 NPC 走这条 continue。
                    if (TeamTool.SlotOf(u) < 0) continue;
                    if (!TeamBattleSafety.Participated(UnitIdOf(u))) continue;

                    string id = null;
                    try { id = u.data?.unitData?.unitID; } catch { }
                    if (!string.IsNullOrEmpty(id))
                    {
                        if (seenIds.Contains(id)) continue;
                        seenIds.Add(id);
                    }

                    _mates.Add(u);
                    _mateNames.Add(TeamTool.NameOf(u, "?"));
                }

                if (_mates.Count == 0 && injected == 0) return;

                // ★ 补全：倒下的队友可能已被移出 npcUnits，但号位气运还在 ★
                //   本场既然已有队友进容器（或全员倒下导致容器空），就把当前全队补进来；
                //   否则 solo 战斗（容器里没有号位气运）会被误判成组队战斗。
                try
                {
                    var all = TeamTool.Members(out _);
                    foreach (var m in all)
                    {
                        if (m == null) continue;

                        // ★ 主判据：unitID 去重 ★
                        //   `npcUnits` 扫进来的人已经进了 seenIds；`Members()` 返回的
                        //   可能是同一个 unitID 的新 IL2CPP 包装对象，ReferenceEquals 不可靠。
                        string mid = UnitIdOf(m);
                        if (!TeamBattleSafety.Participated(mid)) continue;
                        if (!string.IsNullOrEmpty(mid))
                        {
                            if (seenIds.Contains(mid)) continue;
                            seenIds.Add(mid);
                        }
                        else
                        {
                            bool exists = false;
                            foreach (var x in _mates)
                                if (ReferenceEquals(x, m)) { exists = true; break; }
                            if (exists) continue;
                        }

                        _mates.Add(m);
                        _mateNames.Add(TeamTool.NameOf(m, "?"));
                    }
                }
                catch (Exception ex) { SafeLog("补全队友名单失败: " + ex.Message); }

                if (_mates.Count == 0) return;

                SnapshotDeadMates();

                _armed = true;
                ModMain.P(Tag + "本场有 " + _mates.Count + " 名队友参战："
                          + string.Join("、", _mateNames.ToArray())
                          + " → 已排队「战后归队 + 写经历」");
            }
            catch (Exception ex) { SafeLog("BattleEndFront 异常: " + ex.Message); }
        }

        // ══════════════════════════════════════════════════════════
        //  ② BattleExit —— 最后发出的那个信号，在这里动手
        // ══════════════════════════════════════════════════════════

        private static void OnBattleExit(ETypeData e)
        {
            // ★ 剧情账本：**先关战斗窗**（10-25），位置有三条硬理由 ★
            //   ① 本方法下面有两处提前 `return`（找不到玩家 / 读不到坐标）——
            //      窗若留到那之后才关，那两条路上 `end` 钉子永远插不上，
            //      而窗是"开着"的 ⇒ **下一场**的 `BeginWindow` 会被幂等判据挡掉，
            //      症状是"打第二场起剧情再也没记过"，而且没有任何报错。
            //   ② `EndWindow` 自身幂等（没开过就什么都不做），所以单人来这里也不会插孤儿钉子。
            //   ③ 窗关得早不影响内容：剧情都在 `BattleEndFront` 之前就演完了。
            try { DramaLedger.EndWindow(""); }
            catch (Exception ledEx) { SafeLog("账本战斗终点失败（不影响归队/写经历）: " + ledEx.Message); }

            if (!_armed) return;          // 这场没队友 → 什么都不做（切磋、单人副本都不受影响）
            _armed = false;
            _rounds++;

            try
            {
                // ★ 击杀战报必须**最先**输出（10-21 自查发现）★
                //   本方法有两处提前 `return`（找不到玩家 / 读不到坐标），
                //   而 `finally` 里的 `Reset()` 无论走哪条路都会执行 ——
                //   若把 LogReport 放在 return 之后，那两种情况下战果会被静默丢弃。
                //   统计与"能不能归队/写经历"无关，所以放最前面。
                BattleKillStats.LogReport();

                var player = g.world?.playerUnit;
                if (player == null)
                {
                    SafeLog("第 " + _rounds + " 场战后归队跳过：找不到玩家");
                    return;
                }

                // 目标坐标：`pointX/pointY` 用于回读比对；动作那边的坐标直接
                // `pdata.GetPoint()` 就地构造（`Vector2Int` 这个类型名在编译期不可见，只能靠 var）
                var pdata = player.data.unitData;
                int tx, ty;
                try { tx = pdata.pointX; ty = pdata.pointY; }
                catch (Exception ex)
                {
                    SafeLog("第 " + _rounds + " 场战后归队跳过：读玩家坐标失败 " + ex.GetType().Name);
                    return;
                }

                // ── ① 传送归队 ──
                var done = new List<string>();
                var failed = new List<string>();
                foreach (var m in _mates)
                {
                    string nm = TeamTool.NameOf(m, "?");
                    try
                    {
                        if (SamePoint(m, tx, ty)) { done.Add(nm + "(本来就在同处)"); continue; }

                        var act = new UnitActionMoveNPC(pdata.GetPoint());
                        int ret = m.CreateAction(act, true);
                        if (ret != 0) { failed.Add(nm + "(引擎拒绝 code=" + ret + ")"); continue; }

                        // 返回码 0 也要回读 —— 与工具层同口径：能读到才算真的动了
                        if (SamePoint(m, tx, ty)) done.Add(nm);
                        else failed.Add(nm + "(动作成功但坐标未变)");
                    }
                    catch (Exception ex) { failed.Add(nm + "(" + ex.GetType().Name + ")"); }
                }

                SafeLog("第 " + _rounds + " 场战后归队：成功 " + done.Count + " / 未成 " + failed.Count
                        + (done.Count > 0 ? "；已归队：" + string.Join("、", done.ToArray()) : "")
                        + (failed.Count > 0 ? "；未归队：" + string.Join("、", failed.ToArray()) : ""));

                // ── ③ 重伤：挂气运 + 自动离队 ──
                ApplyHurtAndLeave();

                // ── ④ 写经历 ──
                //   副本：同行 / 重伤 / 击杀（现有文案）
                //   决斗：并肩应战 + 见闻（**另一套文案** —— 决斗里没有秘境、也没有斩杀成堆的怪）
                if (_kind == KindDuel) WriteDuelLogs(player);
                else WriteJourneyLogs(player);
            }
            catch (Exception ex) { SafeLog("BattleExit 异常: " + ex.GetType().Name + " " + ex.Message); }
            finally
            {
                _mates.Clear();
                _mateNames.Clear();
                _deadIds.Clear();
                _hurtMates.Clear();
                _hurtIds.Clear();
                TeamContainerInject.ClearDeadTeamIds();
                TeamBattleSafety.Reset();
                // ⚠ 只在这里清 —— 不能放进 ResetForBattle：
                //   ResetForBattle 在 BattleEndFront **开头**跑，
                //   而 SweepDeadBattleUnits 在同一次 BattleEndFront 的
                //   SnapshotDeadMates 里才用这些引用；开头清掉就白扫了。
                TeamContainerInject.ClearMateBattleUnits();
                _kind = KindUnknown;
                // ★ 击杀统计：**写完经历之后**才清零 ★
                //   顺序必须是「统计（Die 钩子累积）→ LogReport 输出 → 写经历取文案 → 清零」。
                //   放在 `ResetForBattle`（BattleEndFront 开头）会提前抹掉战果，
                //   真机症状就是明明打了怪却报「本场无击杀记录」。
                BattleKillStats.Reset();
            }
        }

        // ══════════════════════════════════════════════════════════
        //  重伤：判定 + 挂气运 + 自动离队（10-19）
        // ══════════════════════════════════════════════════════════
        //
        // 为什么不能只信配置链的 6009：
        //   `DungeonTrigger MID&002` 的 `unitAddEffect_MID&003_MID&001` 走
        //   `BattleFunction.GetUnit("MID&003")` → `ConfNpcCondition.GetUnits`，
        //   探针实测这条路径返回 0 人 ⇒ 6009 可能根本没挂上。
        //   这里在 `BattleEndFront` 用 `SceneBattle.unit.GetAllUnitIncludeDie()`
        //   直接看战场单位的 `isDie`，并在 `BattleExit` 兜底补挂「内外重伤」。
        //
        // 判据两层：
        //   ① 战场单位 `isDie`（真实倒下）；
        //   ② 战后身上已有「内外重伤」气运（配置链 6009 已生效）。
        //   两者任一成立 ⇒ 重伤，摘掉全部号位气运（自动离队），并在经历里写重伤文案。

        /// <summary>
        /// 本场是副本还是决斗。**读不到 dbi 一律当"不是决斗"**（= 沿用现有行为，
        /// 这条是本仓的硬约束：读不到判据时不许改变老行为）。异常同样吞掉按 `KindUnknown`。
        /// </summary>
        private static int ReadBattleKind()
        {
            try
            {
                var battle = g.world != null ? g.world.battle : null;
                var data = battle != null ? battle.data : null;
                var dbi = data != null ? data.dungeonBaseItem : null;
                if (dbi == null) return KindUnknown;
                if (dbi.type == ModIds.DungeonTypeDuel) return KindDuel;
                return KindDungeon;
            }
            catch (Exception e)
            {
                SafeLog("读战斗种类失败（按未知处理，沿用副本文案）: " + e.GetType().Name + " " + e.Message);
                return KindUnknown;
            }
        }

        private static void SnapshotDeadMates()
        {
            try
            {
                // ★ 第 0 条（最硬）：直接问本场登记过的战场单位 isDie ★
                //   前面的 Die / OnUnitDie / 6009 三条全靠"回调被调到且能认人"，
                //   真机证明这三条都可能静默失败。这条只依赖 AddUnit 钩子拿到对象引用。
                int swept = TeamContainerInject.SweepDeadBattleUnits();
                if (swept > 0) SafeLog("战场单位扫描：新发现倒下 " + swept + " 人");

                // 再合并死亡钩子（Die / OnUnitDie / 6009 记录）
                int hookDeaths = 0;
                foreach (var id in TeamContainerInject.DeadTeamIds)
                    if (_deadIds.Add(id)) hookDeaths++;
                if (hookDeaths > 0)
                    SafeLog("死亡钩子：记录倒下 " + hookDeaths + " 人："
                            + string.Join("、", new List<string>(_deadIds).ToArray()));

                object scene = FindSceneBattle();
                if (scene == null)
                {
                    SafeLog("重伤快照：拿不到 SceneBattle → 只用死亡钩子/气运判据");
                }
                else
                {
                    object mgr = Refl.Get(scene, "unit");
                    if (mgr == null)
                    {
                        SafeLog("重伤快照：SceneBattle.unit 为空 → 只用死亡钩子/气运判据");
                    }
                    else
                    {
                        object list = GetAllBattleUnitsIncludeDie(mgr);
                        if (list == null)
                        {
                            SafeLog("重伤快照：GetAllUnitIncludeDie 为空 → 只用死亡钩子/气运判据");
                        }
                        else
                        {
                            int n = CountOf(list);
                            int dead = 0;
                            for (int i = 0; i < n; i++)
                            {
                                object bu = null;
                                try { bu = ((dynamic)list)[i]; } catch { continue; }
                                if (bu == null) continue;

                                bool die = false;
                                die = Refl.Bool(Refl.Get(bu, "isDie"));
                                if (!die) continue;

                                string uid = UnitIdOfBattleUnit(bu);
                                if (!string.IsNullOrEmpty(uid) && _deadIds.Add(uid)) dead++;
                            }
                            SafeLog("重伤快照：战场单位 " + n + " 个，列表新倒下 " + dead + " 个"
                                    + (_deadIds.Count > 0
                                        ? "；累计：" + string.Join("、", new List<string>(_deadIds).ToArray())
                                        : ""));
                        }
                    }
                }

                int hurtId = TeamTool.HurtLuckId();
                foreach (var m in _mates)
                {
                    string id = UnitIdOf(m);
                    string nm = TeamTool.NameOf(m, "");
                    // ★ 两种 key 都认（10-20）★
                    //   `RecordDeath` 在 `worldUnitData.unitData.unitID` 读空时
                    //   会退化成 `name:<名字>` 记录 —— 真机日志里 6009 回调确实触发了
                    //   （190 剧情开了 2 次），但 unitID 读空 ⇒ 旧判据永远 false。
                    bool deadMate = (!string.IsNullOrEmpty(id) && _deadIds.Contains(id))
                                 || (nm.Length > 0 && _deadIds.Contains("name:" + nm));
                    bool hasHurt = hurtId != 0 && TeamTool.HasLuck(m, hurtId);
                    if (deadMate || hasHurt)
                        MarkHurt(m, deadMate ? "战场倒下" : "已带重伤气运");
                }
            }
            catch (Exception ex)
            {
                SafeLog("重伤快照异常: " + ex.GetType().Name + " " + ex.Message);
            }
            finally
            {
                // ⚠ 这里**不能**清 DeadTeamIds / 战场单位引用：
                //   SnapshotDeadMates 只负责"判定谁倒下"，真正挂气运在
                //   ApplyHurtAndLeave（BattleExit）里还要读 `_deadIds`。
                //   清空统一放在 OnBattleExit 的 finally。
            }
        }

        private static void ApplyHurtAndLeave()
        {
            int hurtId = TeamTool.HurtLuckId();
            if (hurtId == 0)
            {
                SafeLog("重伤处理跳过：气运表里没有「内外重伤」");
                return;
            }

            int added = 0, left = 0, failed = 0, fromTable = 0;
            foreach (var m in _mates)
            {
                string id = UnitIdOf(m);
                string nm = TeamTool.NameOf(m, "");
                bool dead = (!string.IsNullOrEmpty(id) && _deadIds.Contains(id))
                         || (nm.Length > 0 && _deadIds.Contains("name:" + nm));
                bool has = TeamTool.HasLuck(m, hurtId);
                if (!dead && !has) continue;

                // ★ 分工定案（10-21）：**表负责挂，代码只补漏** ★
                //
                //   190 的 `openFunction=addNPCLuck_1_MID&52` 是表的原生能力，
                //   只要我们填对了 `DramaData.unitLeft`，它自己就会挂上。
                //   真机日志佐证：`新挂 0` 那一次，气运其实**已经在他身上**
                //   —— 是表挂的，代码只是事后发现"居然带着"。
                //
                //   ⇒ 已经带着 = 表干完了，代码**什么都不做**（只记一笔）。
                //     没带着     = 表漏了（多半这次 190 没开/槽没填上），代码补挂。
                if (has)
                {
                    fromTable++;
                    MarkHurt(m, "表已挂好");
                    SafeLog("重伤已在身（表挂）→ " + TeamTool.NameOf(m, "?") + "，无需补挂");
                }
                else
                {
                    string err;
                    bool ok = TeamTool.AddLuck(m, hurtId, out err);
                    has = ok && TeamTool.HasLuck(m, hurtId);
                    if (!has)
                    {
                        failed++;
                        SafeLog("重伤补挂失败 → " + TeamTool.NameOf(m, "?") + "：" + err);
                        continue;
                    }
                    added++;
                    MarkHurt(m, "代码补挂（表未挂）");
                    SafeLog("重伤补挂 → " + TeamTool.NameOf(m, "?") + "：「内外重伤」已生效"
                            + "（表没挂上，由代码兜底）");
                }

                string err2;
                if (TeamTool.RemoveAllSlotLucks(m, out err2))
                {
                    left++;
                    SafeLog("重伤自动离队 → " + TeamTool.NameOf(m, "?") + "：号位气运已摘");
                }
                else
                {
                    failed++;
                    SafeLog("重伤自动离队失败 → " + TeamTool.NameOf(m, "?") + "：" + err2);
                }
            }

            if (added > 0 || left > 0 || fromTable > 0)
            {
                TeamTool.SyncTeamState(0);
                SafeLog("重伤处理：表挂 " + fromTable + "，代码补挂 " + added
                        + "，离队 " + left + "，失败 " + failed);
            }
            else if (failed > 0)
            {
                SafeLog("重伤处理失败 " + failed + " 人（未挂气运/未离队），请查上面的原因");
            }
        }

        private static void MarkHurt(WorldUnitBase m, string why)
        {
            string id = UnitIdOf(m);
            if (!string.IsNullOrEmpty(id))
            {
                // ★ 同一 unitID 只记一次 ★
                //   `_mates` 补全已经按 unitID 去重；这里再兜一层，防止
                //   SnapshotDeadMates / ApplyHurtAndLeave 重复调用时写两遍。
                if (!_hurtIds.Add(id)) return;
            }
            else
            {
                bool exists = false;
                foreach (var x in _hurtMates)
                    if (ReferenceEquals(x, m)) { exists = true; break; }
                if (exists) return;
            }

            _hurtMates.Add(m);
            SafeLog("判定重伤 → " + TeamTool.NameOf(m, "?") + "（" + why + "）");
        }

        /// <summary>拿 `SceneBattle` 实例：`SceneType.battle` 是静态属性（反编译已确认）。</summary>
        private static object FindSceneBattle()
        {
            try
            {
                Type t = null;
                try { t = Type.GetType("SceneType, Assembly-CSharp"); } catch { }
                if (t == null)
                {
                    foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
                    {
                        try { t = asm.GetType("SceneType"); } catch { }
                        if (t != null) break;
                    }
                }
                if (t == null) return null;

                var p = t.GetProperty("battle",
                    BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic);
                if (p != null) return p.GetValue(null);

                var f = t.GetField("battle",
                    BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic);
                return f == null ? null : f.GetValue(null);
            }
            catch { return null; }
        }

        private static object GetAllBattleUnitsIncludeDie(object mgr)
        {
            try
            {
                var mi = mgr.GetType().GetMethod("GetAllUnitIncludeDie",
                    BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (mi != null)
                {
                    var ps = mi.GetParameters();
                    object[] argv = ps.Length == 0 ? new object[0] : new object[] { false };
                    return mi.Invoke(mgr, argv);
                }
            }
            catch { }
            return Refl.Get(mgr, "allUnitIncludeDie");
        }

        private static string UnitIdOfBattleUnit(object bu)
        {
            try
            {
                object data = Refl.Get(bu, "data");
                object wud = Refl.Get(data, "worldUnitData");
                object ud = Refl.Get(wud, "unitData");
                return Refl.Get(ud, "unitID") as string;
            }
            catch { return null; }
        }

        // ══════════════════════════════════════════════════════════
        //  写经历
        //
        //  为什么写进「经历」而不是别处：agent 能读到的只有那些段
        //  （`UnitSnapshot.BuildLogs` 走 `WorldUnitLogMgr`）。写进经历 = 下次这个 NPC
        //  和人聊起"上次一起下副本"时，是真有据可依，而不是凭空编。
        //
        //  入口选择（反汇编 `WorldUnitLogMgr::AddLogData` VA=0x183D7CAF0 确证）：
        //  4 参重载是"填文本"的便捷壳，内部做的事是
        //      cond == null → new UnitConditionData(unit)
        //      logItem = new LogItemData(); logItem.conditionData = cond
        //      logItem.AddLogsMain(text, subs, false)
        //      this.AddLogData(unit, logItem)
        //  ⇒ 直接调它，不用自己拼 LogItemData（少一处会写错的地方）。
        //
        //  常规经历 vs 重要经历：这里写**常规**。重要经历是"大事记"面板，会顶掉剧情事件，
        //  一场副本不值得占那个位置；常规经历 agent 一样读得到。
        // ══════════════════════════════════════════════════════════

        /// <summary>
        /// 写经历用的文案键（`RoleLogLocal.keyID`）。
        ///
        /// ★ 10-19 真机定案：`AddLogData(unit, logKey, values, cond)` 的第二个参数必须是
        ///   `RoleLogLocal.keyID`，**不是正文**。旧版直接传「与…同闯秘境」这句话，
        ///   引擎打 `找不到日志：…`（`LogItemData:AddLogsMain`）后**静默丢弃**，
        ///   而托管层不抛异常 ⇒ 日志里那四行 `写经历 → …` 全是假成功。
        ///   对应配置：工程 `ModExcel/RoleLogLocal.xlsx` 的 `MID&1`
        ///   （ch = `与{0}同闯秘境，并肩作战。`）。
        /// </summary>
        private const string JourneyLogKey = "eventLogTeamDungeon";
        /// <summary>重伤经历文案键（`RoleLogLocal.keyID`）。缺这行只会让重伤者读不到，不影响离队/拦人。</summary>
        private const string HurtLogKey = "eventLogTeamDungeonHurt";

        /// <summary>
        /// 击杀战报文案键（`RoleLogLocal.keyID`）——**方案 A：一个模板两个占位符**。
        ///
        /// ⚠ **这一行需要你在 MOD 编辑器里新增**（`ModExcel/RoleLogLocal.xlsx`，建议 `MID&3`）：
        /// ```text
        /// keyID = eventLogTeamDungeonKill
        /// ch    = 与{0}同闯秘境，斩杀{1}。
        /// ```
        /// `{0}` = 同行者名单（同现有模板），`{1}` = 战绩（如「竹妖 ×12、蛇 ×3」）。
        ///
        /// ## 为什么这样可行（反汇编实证，不是猜）
        ///
        /// 引擎渲染经历的链路是
        /// `Data.GetLogString` → `ConfRoleLogLocalEx.text` → **`GameTool::StrPar(text, string[])`**。
        /// `StrPar` @0x2FD3FA0 的入参是**一个字符串数组**，内部循环替换 ——
        /// 所以模板支持 `{0}`/`{1}`/… 任意多个占位符，`values` 传几个就填几个。
        ///
        /// `AddLogData(unit, key, new[]{ a, b }, cond)` 正是这样传的。
        ///
        /// ## 表里没有这个 key 时
        ///
        /// `AddLogData` 打 `找不到日志：…` 后**静默丢弃**（托管层不抛异常），
        /// 本类据回读结果打 `❌未落库`，不会假装成功。
        /// 击杀统计本身仍工作（`[KillStats] 本场击杀 …`），只是不进人物经历。
        /// </summary>
        private const string KillLogKey = "eventLogTeamDungeonKill";

        /// <summary>决斗三套文案（**唯一来源**：运行时注册与实际渲染共用，避免两处各写一份漂移）。
        /// 占位符由引擎的 `GameTool::StrPar` 逐个替换，`{0}`=同行者名单、`{1}`=对手/见闻。</summary>
        private const string DuelJourneyTpl = "与{0}并肩应战{1}。";
        private const string DuelHurtTpl = "与{0}并肩应战{1}，战斗中身负重伤。";
        private const string DuelDramaTpl = "与{0}共历此战：{1}";

        /// <summary>击杀模板缺失的提示只打一次（每个人都会回退，不刷屏）。</summary>
        private static bool _killKeyWarned;

        /// <summary>
        /// 「副本见闻」文案键（`RoleLogLocal.keyID`）—— 10-25 新增，用来把**副本里演的剧情**
        /// 也写进队友的经历。
        ///
        /// ## 为什么必须是"另起一条"而不是塞进击杀模板
        /// 击杀模板 `与{0}同闯秘境，斩杀{1}。` 的两个槽都是语义槽；把一段自由文本塞进 `{1}`
        /// 会变成「斩杀竹妖 ×59、……、你选择了「誓死抵抗」。」—— 病句，而且一旦本场没击杀
        /// 就连槽都没有。`LogItemData` 本来就允许多条（`logs` 是 List），所以两条各自独立最干净。
        ///
        /// ## 模板长什么样
        /// `ch = {0}` —— 整段自由文本由 `values[0]` 提供（见 `DungeonDramaCore.Format`）。
        /// 表里那行是 `MID&904`（`ModIds.LogTeamDungeonDrama`，**数字 id 一旦入档不可改**）。
        ///
        /// ## 表里没有这行也能跑
        /// `EnsureDramaLogTemplate()` 会在运行时克隆现成模板注册一份。所以：
        ///   · 你重新导出 mod（表里有这行）→ 走表里的，运行时注册直接跳过；
        ///   · 还没导出 → 走运行时那份，功能立刻可用。
        /// 两条路都不需要改这个文件。
        /// </summary>
        private const string DramaLogKey = ModIds.LogTeamDungeonDramaKey;

        /// <summary>已经确认可用的"自定义经历模板"id 集合（查表命中或运行时注册成功）。
        /// 10-25 由单个 `_dramaLedgerTemplateState` 升级成集合：决斗又要三行，
        /// 一行一个状态位会变成四个各自漂移的静态字段。</summary>

        private static void WriteJourneyLogs(WorldUnitBase player)
        {
            try
            {
                var logMgr = g.world?.unitLog;
                if (logMgr == null) { SafeLog("写经历跳过：g.world.unitLog 为空"); return; }
                if (_mateNames.Count == 0) return;

                string playerName = TeamTool.NameOf(player, "玩家");
                var writtenUnits = new List<WorldUnitBase>();
                var writtenValues = new List<string>();

                // ── 击杀战报（方案 A：并入同一条模板的 {1}）──
                //
                // 模板：`与{0}同闯秘境，斩杀{1}。`
                //   {0} = 同行者名单，{1} = 战绩
                // 本场无击杀时退回原模板（`与{0}同闯秘境，并肩作战。`），
                // 免得出现"斩杀。」"这种半句。
                string killSummary = BattleKillStats.SummaryLine();
                bool useKillKey = killSummary.Length > 0;
                if (useKillKey) SafeLog("击杀战报：" + killSummary + "（并入同行经历 {1}）");
                else SafeLog("击杀战报：本场无击杀 → 用原模板");

                // ★ 玩家自己那条**不写**（10-25 用户第二次明确）★
                //   口径原话：「战斗的经历写入只写入到 npc 里面，不写玩家自身的」。
                //   旧版这里会给玩家写一条「与「项淑仪」同闯秘境，斩杀…。」——
                //   那是**玩家自己的履历**，用户明确不要。删掉整段（不是加开关：
                //   留个默认关的开关只会在下次被人"顺手打开"）。
                //   `UnitConditionData cdPlayer` 也一并去掉 —— 它只服务那一条。
                //   （`playerName` 仍然要用：队友那条的同行名单里有玩家。）

                // 队友视角：与玩家 + 其余队友同行（每个人的名单里不含自己）
                for (int i = 0; i < _mates.Count; i++)
                {
                    var others = new List<string>();
                    others.Add(playerName);
                    for (int j = 0; j < _mateNames.Count; j++)
                    {
                        if (j == i) continue;
                        others.Add(_mateNames[j]);
                    }
                    string nm = TeamTool.NameOf(_mates[i], "?");
                    string mateValue = Quote(others);
                    UnitConditionData cdMate = null;
                    try { cdMate = new UnitConditionData(_mates[i], player); } catch { }
                    string id = UnitIdOf(_mates[i]);
                    bool hurt = !string.IsNullOrEmpty(id) && _hurtIds.Contains(id);
                    if (WriteOne(logMgr, _mates[i], nm, mateValue, killSummary,
                                 useKillKey, hurt, cdMate))
                    {
                        writtenUnits.Add(_mates[i]);
                        writtenValues.Add(mateValue);
                    }
                }

                // ★ 副本剧情（10-25）：只写 NPC 队友，玩家自己那条**不动**（用户口径）。
                //   为什么放在归队/同行之后：这两条本来就成立，剧情是**附加**。
                //   任何失败都只打日志，绝不影响前面写好的内容。
                WriteDungeonDrama(logMgr, player, false);

                // ★ 立即合并进面板：AddLogData 只进暂存层，面板读的是 DataUnitLog.LogData
                //   的解码缓存；不主动合并 + 失效缓存，就要等月结/存档才显示。
                if (writtenUnits.Count > 0)
                    ForceMergeToPanel(writtenUnits, writtenValues);
            }
            catch (Exception ex) { SafeLog("写经历异常: " + ex.GetType().Name + " " + ex.Message); }
        }

        /// <summary>
        /// 决斗（`type=40`，攻击 / 寻仇）的经历 —— **另一套文案**（10-25，用户口径）。
        ///
        /// ## 为什么不能沿用副本那三条
        /// `与{0}同闯秘境，并肩作战。` / `…斩杀{1}。` —— 决斗里既没有"秘境"，也没有
        /// 成堆的怪（对手就是一个人）。真机若沿用，会出现「与某某同闯秘境，斩杀未知单位 ×12」
        /// 这种既错又诡异的履历。
        ///
        /// ## 三条新文案（`ModIds.LogTeamDuel/DuelHurt/DuelDrama`）
        /// · 主记录：`与{0}并肩应战{1}。`         `{1}` = 对手名（判不出写「强敌」，不猜）
        /// · 重伤：  `与{0}并肩应战{1}，战斗中身负重伤。`
        /// · 见闻：  `与{0}共历此战：{1}`         由 `WriteDungeonDrama(..., duel:true)` 写
        ///
        /// ## 与副本那条的共同纪律
        /// · **只写 NPC 队友**，玩家自己那条不写（用户 10-25 口径）；
        /// · 走 `AddLogData(unit, keyID, values, cond)`，keyID 必须是表里的 `keyID`（不是正文）；
        /// · 模板缺失时运行时克隆注册；仍失败则**如实打日志**，不假装写过。
        /// </summary>
        private static void WriteDuelLogs(WorldUnitBase player)
        {
            try
            {
                var logMgr = g.world != null ? g.world.unitLog : null;
                if (logMgr == null) { SafeLog("写经历跳过：g.world.unitLog 为空"); return; }
                if (_mateNames.Count == 0) return;

                // 对手名：判不出就不猜名字（"强敌"比编一个人名安全 —— 编错名字会污染记忆）
                string foe = TeamJoinGate.LastAttackerName;
                if (string.IsNullOrEmpty(foe)) foe = "强敌";

                bool tplMain = RoleLogTemplate.EnsureLogTemplate(ModIds.LogTeamDuel, ModIds.LogTeamDuelKey, DuelJourneyTpl, "决斗同行", Tag);
                bool tplHurt = RoleLogTemplate.EnsureLogTemplate(ModIds.LogTeamDuelHurt, ModIds.LogTeamDuelHurtKey, DuelHurtTpl, "决斗重伤", Tag);
                if (!tplMain && !tplHurt) SafeLog("⚠ 决斗经历模板都没就绪 ⇒ 本场只写见闻");

                string playerName = TeamTool.NameOf(player, "玩家");
                var units = new List<WorldUnitBase>();
                var vals = new List<string>();
                for (int i = 0; i < _mates.Count; i++)
                {
                    var mate = _mates[i];
                    if (mate == null) continue;
                    string nm = TeamTool.NameOf(mate, "?");

                    // 同行者名单：玩家 + 其余队友（不含自己）
                    var others = new List<string>();
                    others.Add(playerName);
                    for (int j = 0; j < _mateNames.Count; j++)
                    {
                        if (j == i) continue;
                        others.Add(_mateNames[j]);
                    }
                    string mateValue = Quote(others);

                    string id = UnitIdOf(mate);
                    bool hurt = !string.IsNullOrEmpty(id) && _hurtIds.Contains(id);
                    UnitConditionData cd = null;
                    try { cd = new UnitConditionData(mate, player); } catch { }

                    string key = hurt ? ModIds.LogTeamDuelHurtKey : ModIds.LogTeamDuelKey;
                    if (TryAddLog(logMgr, mate, key, new string[] { mateValue, foe }, cd, nm, hurt))
                    {
                        units.Add(mate);
                        vals.Add(mateValue);
                    }
                }

                if (units.Count > 0) ForceMergeToPanel(units, vals);

                // 见闻（战斗内剧情）—— 与副本共用同一套抽取，只是模板/文案不同
                WriteDungeonDrama(logMgr, player, true);
            }
            catch (Exception ex) { SafeLog("决斗经历写入异常: " + ex.GetType().Name + " " + ex.Message); }
        }

        /// <summary>
        /// 把「这场副本里演的剧情」写进**每个 NPC 队友**的经历（玩家自己那条不写）。
        ///
        /// ## 内容从哪来
        /// 从 `DramaLedger` 的**战斗窗**取（`[队友进场, 战斗结束]` 之间钉住的页与选项）。
        /// **不是**从分段器取 —— 段有 600 秒保质期，真机实测它比战斗结束早 33 秒就把内容
        /// 清掉了（4901 那场：18:36:44 收口 → 18:46:44 丢弃 → 18:47:17 才 BattleEndFront）。
        /// 从段取的话，长副本里这个功能**永远拿到空**，而且日志一片平静。
        ///
        /// ## 为什么用「另起一条经历」而不是并进击杀那条
        /// 击杀模板 `与{0}同闯秘境，斩杀{1}。` 两个槽都是语义槽；把一段话塞进 `{1}` 会变成
        /// 「斩杀竹妖 ×59、……、你选择了「誓死抵抗」。」—— 病句，且本场无击杀时连槽都没有。
        /// `LogItemData.logs` 是 List，多条各自独立最干净。
        ///
        /// ## 失败怎么处理
        /// 全部 try/catch + 回读。模板缺失时引擎只打 `找不到日志：…` 然后**静默丢弃**，
        /// 所以这里必须回读；读不到就如实打 `❌未落库`，不假装成功（与击杀那条同纪律）。
        /// </summary>
        private static void WriteDungeonDrama(object logMgr, WorldUnitBase player, bool duel)
        {
            string label = duel ? "决斗见闻" : "副本剧情";
            try
            {
                if (_mates.Count == 0) return;

                DramaLedgerWindow win = DramaLedger.TakeBattleWindow();
                if (win == null || win.Items.Count == 0)
                {
                    SafeLog(label + "：本场窗口内没有剧情，跳过（不写空条目）");
                    return;
                }
                if (!win.StartFound)
                {
                    SafeLog("⚠ " + label + "：找不到起点钉子（账本已裁掉 " + (win.Trimmed ? "部分内容" : "早期内容")
                            + "）⇒ 只拿到后半段，" + win.Items.Count + " 条");
                }

                // 账本条目 → 纯逻辑核的输入（私有窗过滤掉：我们自己弹的窗不该出现在"见闻"里）
                var buf = new List<DungeonDramaLine>();
                int privSkipped = 0;
                for (int i = 0; i < win.Items.Count; i++)
                {
                    DramaLedgerEntry e = win.Items[i];
                    if (e.Kind == "event") continue;
                    if (e.Private) { privSkipped++; continue; }
                    if (!win.StartFound && e.Kind == "page" && e.UnitId.Length == 0) continue;
                    DungeonDramaCore.Add(buf, e.Kind, e.Speaker, e.Name, e.Text, e.DramaId, e.At);
                }
                if (buf.Count == 0)
                {
                    SafeLog(label + "：窗口内 " + win.Items.Count + " 条，去掉私有窗 "
                            + privSkipped + " 条后无可写内容，跳过");
                    return;
                }

                string playerName = TeamTool.NameOf(player, "玩家");
                List<string> lines = DungeonDramaCore.Format(buf, playerName);
                if (lines.Count == 0)
                {
                    SafeLog(label + "：格式化后为空，跳过");
                    return;
                }
                int dropped = DungeonDramaCore.DroppedCount(buf);
                string summary = string.Join("；", lines.ToArray());

                // 模板必须先就位（缺失时 AddLogData 静默丢弃，回读才发现）
                // 决斗另起一条模板（`与{0}共历此战：{1}`）：`{0}` 同行者名单、`{1}` 成品句。
                // 副本那条是单槽 `{0}`，语义不同，不能混用。
                bool tplOk = duel
                    ? RoleLogTemplate.EnsureLogTemplate(ModIds.LogTeamDuelDrama, ModIds.LogTeamDuelDramaKey,
                                        DuelDramaTpl, "决斗见闻", Tag)
                    : EnsureDramaLogTemplate();
                if (!tplOk)
                {
                    SafeLog("❌ " + label + "未写：模板既不在表里、运行时也注册失败（见上一条日志）");
                    return;
                }

                SafeLog(label + "：窗口内 " + win.Items.Count + " 条 → 写入 " + lines.Count + " 句"
                        + (dropped > 0 ? "（★超上限丢掉最早的 " + dropped + " 条★）" : "")
                        + (privSkipped > 0 ? "（另过滤私有窗 " + privSkipped + " 条）" : "")
                        + "：" + summary);

                int ok = 0, fail = 0;
                var units = new List<WorldUnitBase>();
                var vals = new List<string>();
                for (int i = 0; i < _mates.Count; i++)
                {
                    var mate = _mates[i];
                    if (mate == null) continue;
                    string nm = TeamTool.NameOf(mate, "?");
                    UnitConditionData cd = null;
                    try { cd = new UnitConditionData(mate, player); } catch { }
                    // 决斗：每个队友的「同行者名单」都不同（不含自己）⇒ 每人一个 {0}
                    string[] values;
                    if (duel)
                    {
                        var others = new List<string>();
                        others.Add(TeamTool.NameOf(player, "玩家"));
                        for (int j = 0; j < _mateNames.Count; j++)
                            if (j != i) others.Add(_mateNames[j]);
                        values = new string[] { Quote(others), summary };
                    }
                    else values = new string[] { summary };

                    if (TryAddLog(logMgr, mate, duel ? ModIds.LogTeamDuelDramaKey : DramaLogKey,
                                  values, cd, nm, false))
                    {
                        ok++;
                        units.Add(mate);
                        vals.Add(summary);
                    }
                    else fail++;
                }
                SafeLog(label + "落库：成功 " + ok + " / 未落库 " + fail
                        + (fail > 0 ? "（未落库 = 模板没注册上，见上一条）" : ""));

                // 剧情那条也要立刻进面板（与同行那条同一个理由：AddLogData 只进暂存层）
                if (units.Count > 0) ForceMergeToPanel(units, vals);
            }
            catch (Exception ex)
            {
                SafeLog(label + "写入异常（不影响同行/归队）：" + ex.GetType().Name + " " + ex.Message);
            }
        }

        /// <summary>
        /// 确保「副本见闻」模板可用。**先查表，没有就运行时注册一份**。
        ///
        /// ## 为什么要运行时注册（而不是只靠 xlsx）
        /// xlsx 那行要你重新导出 mod 才进游戏。运行时注册让功能**立刻可用**，
        /// 你可以先在游戏里看到效果，再决定要不要把它固化进表。
        ///
        /// ## 怎么注册
        /// 注册逻辑、反编依据、以及"为什么必须补 `allItemInKey`"全部收在
        /// `game/RoleLogTemplate.cs` —— **唯一实现**，本文件与 `NpcDuel` 都只是传参调用。
        /// 这里只留一句结论：`ConfRoleLogLocal::AddItem` 尾部只有一句 `List::Add`
        /// （8 字节，不碰 `allItemInKey`），**不补索引 = 模板注册成功但经历全被静默丢弃**。
        ///
        /// ## 为什么不直接改现成行的 ch
        /// 那些行（`MID&amp;1~3`）是**别的经历共用的**（同行/重伤/击杀）。改它们的 `ch`
        /// 会把正常履历也一起改坏。克隆一份新的、只改自己那份的 `ch`，是唯一安全的做法。
        /// </summary>
        private static bool EnsureDramaLogTemplate()
        {
            return RoleLogTemplate.EnsureLogTemplate(ModIds.LogTeamDungeonDrama, DramaLogKey, "{0}", "副本见闻", Tag);
        }

        /// <summary>
        /// 写一个人的经历，自动决定用哪个 key、传几个值。
        ///
        /// ## 三个 key 的分工（方案 A 定案）
        ///
        /// | 情形 | key | values |
        /// |---|---|---|
        /// | 有击杀 + 未受伤 | `eventLogTeamDungeonKill` | `[同行者名单, 战绩]` |
        /// | 有击杀 + 重伤 | `eventLogTeamDungeonKill` | `[同行者名单, 战绩]`（重伤者那条已另行记录过，这里仍要写战果） |
        /// | 无击杀 + 未受伤 | `eventLogTeamDungeon` | `[同行者名单]` |
        /// | 无击杀 + 重伤 | `eventLogTeamDungeonHurt` | `[同行者名单]` |
        ///
        /// ⚠ 有击杀时**优先用击杀模板**：它已经含「与{0}同闯秘境」，
        ///   若再额外写一条原模板，同一个人会有两条重复的同行记录。
        /// </summary>
        private static bool WriteOne(object logMgr, WorldUnitBase unit, string who,
                                     string matesValue, string killSummary,
                                     bool useKillKey, bool hurt, UnitConditionData cond)
        {
            if (useKillKey)
            {
                var vals = new string[] { matesValue, killSummary };
                if (TryAddLog(logMgr, unit, KillLogKey, vals, cond, who, hurt)) return true;
                // ★ 击杀模板写失败（多半是表里还没加 `eventLogTeamDungeonKill`）★
                //   必须**退回原模板**，否则"有击杀"这一场里所有人的同行经历全丢 ——
                //   那比没有击杀统计更糟。回落只打一次提示，避免刷屏。
                if (!_killKeyWarned)
                {
                    _killKeyWarned = true;
                    SafeLog("⚠ 击杀模板未落库（RoleLogLocal 缺 key=" + KillLogKey
                            + "，模板应形如「与{0}同闯秘境，斩杀{1}。」）→ 本场回落原模板");
                }
                SafeLog("  击杀信息未写入经历，仅存于 [KillStats] 日志");
            }
            var one = new string[] { matesValue };
            return TryAddLog(logMgr, unit, hurt ? HurtLogKey : JourneyLogKey, one, cond, who, hurt);
        }

        /// <summary>
        /// 写一条经历。
        ///
        /// `values` 支持**多个占位符**：引擎侧 `GameTool::StrPar(text, string[])`
        /// 按数组逐个替换 `{0}`/`{1}`/…，所以传几个就填几个。
        /// </summary>
        /// <param name="probeValue">
        /// **回读校验串**（默认 `values[0]`）。必须是这这条经历里**独一无二**的一段。
        /// 组队那几条的 `values[0]` 是"同行者名单"，天然唯一，所以传默认即可；
        /// 而入宗/退宗的 `values[0]` 是**宗门名** —— 同一个 NPC 的经历里本来就可能有
        /// 「退出「青木宗」」这种旧条目，拿它当校验串会让回读**恒为真**，
        /// "这次到底写进去了没有"就再也测不出来（整条回读机制失效，且日志一片成功）。
        /// 那种情况下由调用方传入理由（一整句话，唯一）。
        /// </param>
        /// <param name="displayText">
        /// **日志里展示的成品文案**（默认按各组队模板临时拼）。只影响日志可读性 ——
        /// 不传时按内置的几条模板猜；入宗/退宗这类"外部模板"必须由调用方给，
        /// 否则会落进最后的 `else` 打出「与…同闯秘境，并肩作战」这种完全错误的文案，
        /// 排障时极容易被误判成"模板写错了"。
        /// </param>
        internal static bool TryAddLog(object logMgr, WorldUnitBase unit, string key,
                                      string[] values, UnitConditionData cond, string who, bool hurt,
                                      string probeValue = null, string displayText = null)
        {
            try
            {
                if (values == null || values.Length == 0) return false;
                var vals = values;
                ((dynamic)logMgr).AddLogData(unit, key, vals, cond);
                // 回读用的串：默认取 values[0]（组队那几条的唯一值），可由调用方覆盖
                string value = string.IsNullOrEmpty(probeValue) ? values[0] : probeValue;

                // ★ 必须回读：缺 key 时引擎只 LogError、不抛异常 —— 这正是旧版假成功的根源。
                //   读暂存层最近 3 条，`Data.values` 里出现我们写的那串才算真落库。
                bool ok = PendingHasValue(unit, value);
                // 日志里展示"引擎实际会渲染成什么"（按模板逐个填占位符），
                // 这样看到日志就等于看到游戏里那句经历，不用再猜。
                string text;
                if (!string.IsNullOrEmpty(displayText))
                    text = displayText;   // 外部模板（入宗/退宗）由调用方给成品句
                else if (key == KillLogKey)
                    text = "与" + values[0] + "同闯秘境，斩杀" + (values.Length > 1 ? values[1] : "?") + "。";
                else if (key == DramaLogKey)
                    text = value;      // 模板就是 `{0}`，渲染出来 === 传进去的那段话
                else if (key == ModIds.LogTeamDuelKey) text = Fill(DuelJourneyTpl, values);
                else if (key == ModIds.LogTeamDuelHurtKey) text = Fill(DuelHurtTpl, values);
                else if (key == ModIds.LogTeamDuelDramaKey) text = Fill(DuelDramaTpl, values);
                else if (hurt) text = "与" + value + "同闯秘境，战斗中身负重伤。";
                else text = "与" + value + "同闯秘境，并肩作战。";
                SafeLog("写经历 → " + who + "：" + text
                        + (ok ? "  ✅已落库" : "  ❌未落库（RoleLogLocal 缺 key=" + key + "？）"));
                return ok;
            }
            catch (Exception ex)
            {
                SafeLog("写经历失败（" + who + "）：" + ex.GetType().Name + " " + ex.Message);
                return false;
            }
        }

        /// <summary>把 `{0}`/`{1}`… 按数组填掉（只给日志展示用，与引擎侧 `StrPar` 同语义）。</summary>
        private static string Fill(string tpl, string[] values)
        {
            string t = tpl ?? "";
            if (values == null) return t;
            for (int i = 0; i < values.Length; i++)
                t = t.Replace("{" + i + "}", values[i] ?? "");
            return t;
        }

        /// <summary>
        /// 回读 `GetCacheWriteLog`（暂存层）：最近 3 条里有没有 `values` 等于我们写入的串。
        /// 只用来判"引擎收没收"，不猜。
        /// </summary>
        private static bool PendingHasValue(WorldUnitBase unit, string value)
        {
            try
            {
                string uid = null;
                try { uid = unit?.data?.unitData?.unitID; } catch { }
                if (string.IsNullOrEmpty(uid)) return false;

                var pend = g.world?.unitLog?.GetCacheWriteLog(uid);
                if (pend == null) return false;
                int n = 0; try { n = pend.Count; } catch { return false; }
                int from = n > 3 ? n - 3 : 0;
                for (int i = from; i < n; i++)
                {
                    object item = null;
                    try { var e = pend[i]; if (e != null) item = e.logData; } catch { continue; }
                    if (item == null) continue;
                    try
                    {
                        var logs = ((dynamic)item).logs;
                        int ln = logs == null ? 0 : (int)logs.Count;
                        for (int j = 0; j < ln; j++)
                        {
                            var vs = ((dynamic)logs[j]).values;
                            if (vs == null) continue;
                            int vn = 0;
                            try { vn = (int)vs.Length; } catch { try { vn = (int)vs.Count; } catch { } }
                            for (int k = 0; k < vn; k++)
                                if (vs[k] == value) return true;
                        }
                    }
                    catch { }
                }
            }
            catch { }
            return false;
        }

        // ══════════════════════════════════════════════════════════
        //  立即合并到面板（10-19 用户要求：面板要即时显示）
        // ══════════════════════════════════════════════════════════
        //
        // AddLogData 只写暂存层 allAddLogData / allAddVitalLogData；
        // 面板读的是 DataUnitLog.LogData.allLogData（解码缓存）。
        // 引擎自己的合并链是 SaveLogData → WriteLogData → MerageAddLog →
        // DataUnitLog.LogData::AddLog；AddLog 只追加 allLog，不失效 _allLogData。
        // ⇒ 写完必须主动走一遍 WriteLogData，并把 lastUpdate*Month 置脏，
        //    否则要等月结/存档才显示（用户实测：过一个月才看到）。
        //
        // 全程 try/catch：合并失败只打日志，不影响归队/战斗。
        internal static void ForceMergeToPanel(List<WorldUnitBase> units, List<string> values)
        {
            try
            {
                var mgr = g.world?.unitLog;
                if (mgr == null) return;

                object regular = Refl.Get(mgr, "allAddLogData");
                object vital = Refl.Get(mgr, "allAddVitalLogData");
                int rc = CountOf(regular), vc = CountOf(vital);
                if (rc == 0 && vc == 0)
                {
                    SafeLog("合并到面板跳过：暂存层为空");
                    return;
                }

                var mi = mgr.GetType().GetMethod("WriteLogData",
                    BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (mi == null) { SafeLog("合并到面板失败：找不到 WriteLogData"); return; }
                var ps = mi.GetParameters();
                if (ps.Length != 4) { SafeLog("合并到面板失败：WriteLogData 参数数=" + ps.Length); return; }

                object allUnit = null;
                try { allUnit = Activator.CreateInstance(ps[0].ParameterType); } catch { }
                if (allUnit == null) { SafeLog("合并到面板失败：建不出 allUnit 字典"); return; }

                int month = 1;
                try { month = NpcInitiativeMonitor.CurrentAccountMonth(); } catch { }
                if (month <= 0) month = 1;

                mi.Invoke(mgr, new object[] { allUnit, regular, vital, month });
                SafeLog("合并到面板：WriteLogData 已执行（暂存 regular=" + rc
                        + " vital=" + vc + " month=" + month + "）");

                for (int i = 0; i < units.Count; i++)
                {
                    var u = units[i];
                    string uid = UnitIdOf(u);
                    if (string.IsNullOrEmpty(uid)) continue;
                    var ld = g.world.unitLog.GetLogDataSync(uid);
                    if (ld == null) { SafeLog("面板回读 → " + TeamTool.NameOf(u, "?") + "：GetLogDataSync=null"); continue; }

                    // 强制解码缓存下次 getter 重建（字段名见 DataUnitLog.LogData 反编译）
                    ResetLogCache(ld);
                    bool has = MergedHasValue(ld, values[i]);
                    SafeLog("面板回读 → " + TeamTool.NameOf(u, "?") + "："
                            + (has ? "✅已显示" : "❌合并后仍读不到（allLog 已写，缓存没刷新？）"));
                }
            }
            catch (Exception ex) { SafeLog("合并到面板异常: " + ex.GetType().Name + " " + ex.Message); }
        }

        internal static string UnitIdOf(WorldUnitBase u)
        {
            try { return u?.data?.unitData?.unitID; } catch { return null; }
        }

        private static int CountOf(object dict)
        {
            try { return dict == null ? 0 : (int)((dynamic)dict).Count; } catch { return 0; }
        }

        /// <summary>把 DataUnitLog.LogData 的解码缓存置脏，让 allLogData getter 重新解码。</summary>
        private static void ResetLogCache(object ld)
        {
            try
            {
                var t = ld.GetType();
                foreach (string name in new string[] { "lastUpdateLogDataMonth", "lastUpdateVitalLogDataMonth" })
                {
                    var f = t.GetField(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                    if (f != null) f.SetValue(ld, -1);
                }
            }
            catch { }
        }

        /// <summary>看解码后的 allLogData / allVitalLogData 里有没有我们写的那串。</summary>
        private static bool MergedHasValue(object ld, string value)
        {
            if (HasValueInList(Refl.Get(ld, "allLogData"), value)) return true;
            if (HasValueInList(Refl.Get(ld, "allVitalLogData"), value)) return true;
            return false;
        }

        private static bool HasValueInList(object list, string value)
        {
            if (list == null) return false;
            int n = 0;
            try { n = (int)((dynamic)list).Count; } catch { return false; }
            for (int i = 0; i < n; i++)
            {
                object item = null;
                try { item = ((dynamic)list)[i]; } catch { continue; }
                if (item == null) continue;

                object logs = null;
                try { logs = ((dynamic)item).logs; } catch { continue; }
                if (logs == null) continue;
                int ln = 0;
                try { ln = (int)((dynamic)logs).Count; } catch { continue; }
                for (int j = 0; j < ln; j++)
                {
                    object d = null;
                    try { d = ((dynamic)logs)[j]; } catch { continue; }
                    object vs = null;
                    try { vs = ((dynamic)d).values; } catch { continue; }
                    if (vs == null) continue;
                    int vn = 0;
                    try { vn = (int)((dynamic)vs).Length; } catch { try { vn = (int)((dynamic)vs).Count; } catch { } }
                    for (int k = 0; k < vn; k++)
                        if (((dynamic)vs)[k] == value) return true;
                }
            }
            return false;
        }

        // ══════════════════════════════════════════════════════════
        //  小工具
        // ══════════════════════════════════════════════════════════

        /// <summary>该单位是否已在目标格。读不到坐标时当"不同处"（宁可多试一次传送）。</summary>
        private static bool SamePoint(WorldUnitBase u, int x, int y)
        {
            try
            {
                var d = u.data.unitData;
                return d.pointX == x && d.pointY == y;
            }
            catch { return false; }
        }

        /// <summary>「甲」「乙」「丙」</summary>
        private static string Quote(List<string> names)
        {
            var sb = new StringBuilder();
            for (int i = 0; i < names.Count; i++)
            {
                if (string.IsNullOrEmpty(names[i])) continue;
                sb.Append("「").Append(names[i]).Append("」");
            }
            return sb.ToString();
        }

        internal static void SafeLog(string s)
        {
            try { ModMain.P(Tag + s); } catch { }
        }
    }
}
#endif
