/// <summary>
/// 玩家关系网络统一采集 —— 「与玩家有社交关系的单位」唯一出口。
///
/// 数据源（全部反编实锤，主线程廉价读）：
///  ① 关系十容器：parent/children/brother/brotherBack/lover/master/student/married
///     + parentBack/childrenBack（玩家 relationData 直读）
///  ② 好友簿：GetIntimUnitData(isGetHideUnit, isContainsDieUnit).friendUnits
///  ③ 仇人簿：同上 IntimUnitData.enemyUnits（types2.txt:82-84，与 friendUnits 并列实锤）
///  ④ 关系记录簿兜底：GetAllGoodRelationUnitID(isGetHideUnit, isGetEnemyUnit)
///     （types27_relation.txt:153；第二参即"是否含仇人"，(true,true)=全部有记录的人）
///  ⑤ 同格单位：一次 O(N) 遍历 GetUnits(true) 按 pointX/pointY 相等（游戏同款判定，types36:48488）
///
/// 通讯录好友层①=①②③④（∪ 手动名单，由 Presenter 合并）；主动开口候选=①~⑤（见
/// NpcInitiativeMonitor）。每字段独立 try/catch，单字段失败不拖垮全候选；按 unitID 去重、
/// 排除玩家、排除空名。硬约束：触 g.world，仅主线程调用。
/// </summary>
using System;
using System.Collections.Generic;

namespace AgentLoopBridge
{
    internal static class RelationNetwork
    {
        /// <summary>采集玩家关系记录全集（①~④，含仇人；来源即"通讯录认识的人"）。</summary>
        public static List<WorldUnitBase> CollectKnownUnits()
        {
            var list = new List<WorldUnitBase>();
            CollectKnownUnits(list, new HashSet<string>(), null);
            return list;
        }

        /// <summary>
        /// 把**活体**关系层映射成通讯录快照条目（`known.json` 的输入）。
        ///
        /// ★ 唯一能抓这三样东西的时刻就是现在 ★ name↔unitID 来自真身，`relation` 是
        /// `GetRelation(玩家)` 的冻结前值 —— 人一死 `GetUnit=null`，死者快照的
        /// `GetRelation(玩家)` 返回 **None**（真机确认），"他是我的道侣"就永久查不回来了。
        /// 所以这份快照不是缓存，是**游戏会抹掉的那部分事实的唯一副本**。
        ///
        /// 只收有精确 unitID 的（没有 ID 的条目，死后也无法确认"那个 id 是不是他"）。
        /// 传入已采好的列表是为了让调用方**只扫一次**（面板打开时与渲染共用同一次采集）。
        /// </summary>
        public static List<KnownContact> ToContactSnapshot(List<WorldUnitBase> units)
        {
            var outp = new List<KnownContact>();
            if (units == null || units.Count == 0) return outp;
            string pid = null;
            try { pid = UnitSnapshot.PlayerUnitId(); } catch { }
            for (int i = 0; i < units.Count; i++)
            {
                var u = units[i];
                if (u == null) continue;
                string name = null, uid = null;
                try { name = u.data.unitData.propertyData.GetName(); } catch { }
                if (string.IsNullOrEmpty(name) || name == pid) continue;
                try { uid = u.data.unitData.unitID; } catch { }
                if (string.IsNullOrEmpty(uid)) continue;
                string rel = "";
                try
                {
                    var player = g.world.playerUnit;
                    if (player != null)
                        rel = UnitSnapshot.RelationCn(u.data.unitData.relationData.GetRelation(player).ToString());
                    if (rel == "None") rel = "";
                }
                catch { }
                outp.Add(new KnownContact { Name = name, UnitId = uid, Relation = rel });
            }
            return outp;
        }

        /// <summary>
        /// 追加玩家关系记录全集进调用方列表。seenIds 跨调用去重；contactIds 非 null 时
        /// 把本方法采到的 unitID 记为"通讯录成员"（⑤同格陌生人来源不记）。
        /// </summary>
        public static void CollectKnownUnits(List<WorldUnitBase> into, HashSet<string> seenIds, HashSet<string> contactIds)
        {
            if (into == null || seenIds == null) return;
            try
            {
                var player = g.world.playerUnit;
                if (player == null) return;
                var rel = player.data.unitData.relationData;
                if (rel == null) return;

                void AddIds(object ids)
                {
                    if (ids == null) return;
                    try
                    {
                        var dyn = (dynamic)ids;
                        int cnt = 0;
                        try { cnt = (int)dyn.Count; } catch { try { cnt = (int)dyn.Length; } catch { return; } }
                        for (int i = 0; i < cnt; i++)
                        {
                            string id = null;
                            try { id = dyn[i]?.ToString(); } catch { continue; }
                            if (string.IsNullOrEmpty(id) || !seenIds.Add(id)) continue;
                            WorldUnitBase u = null;
                            try { u = g.world.unit.GetUnit(id); } catch { }
                            TryAdd(into, contactIds, u, id);
                        }
                    }
                    catch { }
                }

                void AddUnits(object units)
                {
                    if (units == null) return;
                    try
                    {
                        var dyn = (dynamic)units;
                        int cnt = 0;
                        try { cnt = (int)dyn.Count; } catch { try { cnt = (int)dyn.Length; } catch { return; } }
                        for (int i = 0; i < cnt; i++)
                        {
                            WorldUnitBase u = null;
                            try { u = dyn[i] as WorldUnitBase; } catch { try { u = (WorldUnitBase)dyn[i]; } catch { } }
                            if (u == null) continue;
                            string id = null;
                            try { id = u.data.unitData.unitID; } catch { }
                            if (string.IsNullOrEmpty(id) || !seenIds.Add(id)) continue;
                            TryAdd(into, contactIds, u, id);
                        }
                    }
                    catch { }
                }

                // ① 关系十容器
                try { AddIds(rel.parent); } catch { }
                try { AddIds(rel.children); } catch { }
                try { AddIds(rel.brother); } catch { }
                try { AddIds(rel.brotherBack); } catch { }
                try { AddIds(rel.lover); } catch { }
                try { AddIds(rel.master); } catch { }
                try { AddIds(rel.student); } catch { }
                try
                {
                    string mid = null;
                    try { mid = rel.married as string; } catch { try { mid = rel.married?.ToString(); } catch { } }
                    if (!string.IsNullOrEmpty(mid) && seenIds.Add(mid))
                    {
                        WorldUnitBase u = null;
                        try { u = g.world.unit.GetUnit(mid); } catch { }
                        TryAdd(into, contactIds, u, mid);
                    }
                }
                catch { }
                try { AddIds(rel.parentBack); } catch { }
                try { AddIds(rel.childrenBack); } catch { }

                // ②③ 好友簿 + 仇人簿（GetIntimUnitData(isGetHideUnit=false, isContainsDieUnit=false)）
                try
                {
                    var iud = ((dynamic)rel).GetIntimUnitData(false, false);
                    if (iud != null)
                    {
                        dynamic d = (dynamic)iud;
                        try { AddUnits(d.friendUnits); } catch { }
                        try { AddUnits(d.enemyUnits); } catch { }
                    }
                }
                catch { }

                // ④ 关系记录簿兜底（isGetHideUnit=true, isGetEnemyUnit=true → 全部有记录的人，含敌）
                try
                {
                    var dyn = (dynamic)rel;
                    var ids = dyn.GetAllGoodRelationUnitID(true, true);
                    AddIds(ids);
                }
                catch { }
            }
            catch { }
        }

        // ==================================================================== 同格扫描（09-28 治本改造）
        //
        // 背景：原实现无条件 `GetUnits(true)` 全量遍历（实机 1556 个单位、单次 ~100ms，
        // 且绝大多数时候"同格 0 命中"）。反编 `Assembly-CSharp.dll` 元数据发现 `WorldUnitMgr`
        // **自带按坐标查询**（不必再靠全量扫描找同格者）：
        //   · `List<WorldUnitBase> GetUnit(Vector2Int point, Int32 range)`
        //   · `List<WorldUnitBase> GetUnitExact(Vector2Int, Int32, Boolean, Boolean)`
        //
        // 安全策略（本仓最忌"静默漏候选"）：
        //   ① 快查结果**逐个复核**坐标（pointX/pointY 必须等于玩家坐标）—— API 语义即使
        //      含 ±range / 含隐藏单位也不会混进来；
        //   ② **首次调用做一次性对照**：同一次同时跑全量遍历与两个候选快查，比较"同格
        //      unitID 集合"是否完全一致；一致才切换快查（此后不再全量），不一致就永远走
        //      全量并把差异打进日志（零风险；一次实机就能定论该用哪个 API）；
        //   ③ 快查抛异常/返回 null → 立即回退全量并留一行日志。
        // ====================================================================

        /// <summary>同格快查模式：0=未定/不可用（→全量），1=`GetUnit(point,0)`，2=`GetUnitExact(point,0,false,false)`。</summary>
        private static int _sameGridQuickMode;
        /// <summary>首查对照是否已做过（做过且不一致 → 保持全量，不再重复对照）。</summary>
        private static bool _sameGridProbeDone;
        // 对照用 id 集合（静态复用；本类只在主线程调用）
        private static readonly HashSet<string> _sgIdsFull = new HashSet<string>();
        private static readonly HashSet<string> _sgIdsQuick1 = new HashSet<string>();
        private static readonly HashSet<string> _sgIdsQuick2 = new HashSet<string>();

        /// <summary>⑤ 同格所有人（含与玩家无任何关系的陌生人——当面可搭话）。记名语义：非通讯录成员。
        /// 返回**扫描过的全量单位数**（走快查时恒为 0：没有再全量遍历；供调用方量化成本）。
        /// 快查/复核/对照的完整策略见上方区块注释。</summary>
        public static int CollectSameGridUnits(List<WorldUnitBase> into, HashSet<string> seenIds)
        {
            if (into == null || seenIds == null) return 0;
            int px = 0, py = 0;
            try
            {
                var player = g.world.playerUnit;
                if (player == null) return 0;
                try { px = player.data.unitData.pointX; py = player.data.unitData.pointY; } catch { return 0; }
            }
            catch { return 0; }

            // 首次：对照一次（决定走哪条路；对照本身用全量结果保证首查正确）
            if (!_sameGridProbeDone) ProbeSameGridOnce(px, py);

            // ---- 快路径（已证实等价；行走/换日的高频路径）----
            if (_sameGridQuickMode == 1)
            {
                if (ConsumePointUnits(SafePointUnits(false, px, py), into, seenIds, px, py, null) >= 0) return 0;
                _sameGridQuickMode = 0;
                ModMain.P("[RelationNetwork] 同格快查(GetUnit)失败 → 回退全量扫描");
            }
            else if (_sameGridQuickMode == 2)
            {
                if (ConsumePointUnits(SafePointUnits(true, px, py), into, seenIds, px, py, null) >= 0) return 0;
                _sameGridQuickMode = 0;
                ModMain.P("[RelationNetwork] 同格快查(GetUnitExact)失败 → 回退全量扫描");
            }

            // ---- 全量（对照不一致 / 快查不可用）----
            return ScanAllUnits(into, seenIds, px, py, null);
        }

        /// <summary>取该格单位列表（快查 API 封套；异常返回 null，由调用方回退全量）。</summary>
        private static object SafePointUnits(bool exact, int px, int py)
        {
            try
            {
                var point = new UnityEngine.Vector2Int(px, py);
                return exact
                    ? (object)g.world.unit.GetUnitExact(point, 0, false, false)
                    : (object)g.world.unit.GetUnit(point, 0);
            }
            catch (Exception e)
            {
                try { ModMain.P("[RelationNetwork] 同格快查 API 异常（" + (exact ? "GetUnitExact" : "GetUnit") + "）：" + e.Message); } catch { }
                return null;
            }
        }

        /// <summary>首查对照（只跑一次）：全量遍历 vs 两个候选快查的"同格 unitID 集合"；
        /// 一致才切换到快查，否则保持全量并把两个集合的差异数打进日志（供人工核对）。</summary>
        private static void ProbeSameGridOnce(int px, int py)
        {
            _sameGridProbeDone = true;
            try
            {
                _sgIdsFull.Clear();
                int scanned = ScanAllUnits(null, null, px, py, _sgIdsFull);

                _sgIdsQuick1.Clear();
                int c1 = ConsumePointUnits(SafePointUnits(false, px, py), null, null, px, py, _sgIdsQuick1);
                bool ok1 = c1 >= 0 && _sgIdsQuick1.SetEquals(_sgIdsFull);

                _sgIdsQuick2.Clear();
                int c2 = ConsumePointUnits(SafePointUnits(true, px, py), null, null, px, py, _sgIdsQuick2);
                bool ok2 = c2 >= 0 && _sgIdsQuick2.SetEquals(_sgIdsFull);

                if (ok1) _sameGridQuickMode = 1;
                else if (ok2) _sameGridQuickMode = 2;

                ModMain.P("[RelationNetwork] 同格首查对照（全量 " + scanned + " 单位 / 同格 " + _sgIdsFull.Count + " 人）："
                          + "GetUnit(point,0)=" + (c1 >= 0 ? c1 + "人 " + (ok1 ? "一致✓" : "★不一致") : "不可用")
                          + "；GetUnitExact(point,0,false,false)=" + (c2 >= 0 ? c2 + "人 " + (ok2 ? "一致✓" : "★不一致") : "不可用")
                          + " → " + (_sameGridQuickMode == 1 ? "此后走 GetUnit"
                                   : _sameGridQuickMode == 2 ? "此后走 GetUnitExact"
                                   : "★两个都不一致/不可用，保持全量（请把本行发作者）"));
            }
            catch (Exception e)
            {
                _sameGridQuickMode = 0;
                try { ModMain.P("[RelationNetwork] 同格首查对照异常（保持全量）：" + e.Message); } catch { }
            }
        }

        /// <summary>消费"该格单位列表"：复核坐标 → 收集 id（对照用）→ 按需并入候选。
        /// 返回该格命中数；`unitsObj` 为 null / 结构不符 / 异常 → -1（调用方回退全量）。</summary>
        private static int ConsumePointUnits(object unitsObj, List<WorldUnitBase> into, HashSet<string> seenIds,
                                             int px, int py, HashSet<string> idsOut)
        {
            if (unitsObj == null) return -1;
            try
            {
                var dyn = (dynamic)unitsObj;
                int cnt = (int)dyn.Count;
                int hit = 0;
                for (int i = 0; i < cnt; i++)
                {
                    WorldUnitBase u = null;
                    try { u = (WorldUnitBase)dyn[i]; } catch { continue; }
                    if (u == null) continue;
                    // 复核：只认真正同格的（API 语义即使含 ±range / 隐藏单位也不会混进来）
                    int x = int.MinValue, y = int.MinValue;
                    try { x = u.data.unitData.pointX; y = u.data.unitData.pointY; } catch { continue; }
                    if (x != px || y != py) continue;
                    string id = null;
                    try { id = u.data.unitData.unitID; } catch { }
                    if (string.IsNullOrEmpty(id)) continue;
                    hit++;
                    if (idsOut != null) idsOut.Add(id);
                    if (into == null || seenIds == null) continue;
                    if (!seenIds.Add(id)) continue;
                    TryAdd(into, null, u, id);
                }
                return hit;
            }
            catch { return -1; }
        }

        /// <summary>
        /// **以任意坐标为心**收集同格单位（11-11 新增）。
        ///
        /// ## 与 `CollectSameGridUnits` 的区别
        /// 那个函数里写死了 `g.world.playerUnit` 的坐标 —— 它回答的是
        /// "**玩家**那一格有谁"（自主交互/通讯录用）。本函数回答的是
        /// "**这个 NPC** 那一格有谁"，两者在同一个人身上往往不是同格。
        /// 所以这里不能直接调它，只能复用底层的 `ScanAllUnits(px, py)`。
        ///
        /// ## 为什么先试快查
        /// 全量遍历实机 1556 个单位约 100ms（见本文件上方"同格扫描"区块），而 L1 每轮都要取一次。
        /// 快查 API（`GetUnit`/`GetUnitExact`）按坐标直达，成本几乎为 0。
        /// 复用**同一个** `_sameGridQuickMode` 静态状态：两条路共用一套"首次对照→选定模式"的
        /// 自适应结论，避免各自探一遍、各自漂移。
        ///
        /// ## 失败一律回落全量
        /// 快查抛异常 / 返回 null / 复核后坐标不符 ⇒ 立即走 `ScanAllUnits`。
        /// 绝不因为"快查没给"就少算同格的人（漏人比慢更糟：NPC 会觉得眼前空无一人）。
        ///
        /// ## ★ 11-20 修：这里**从来没走过快查** ★
        /// 症状：`get_context` 每回合都调本方法，而 `_sameGridQuickMode` 恒为 0 ⇒ 每次都全量
        /// 扫 1556 个单位（就是上面那个 ~100ms）。日志铁证（11-20 那份 Player.log）：
        /// 16 次 `[MAIL-PROBE] 同格段`、**0 次** `[RelationNetwork] 同格首查对照` ——
        /// 对照一次都没跑过，模式自然永远停在"未定"。
        ///
        /// 根因：模式是**惰性**决定的，钥匙在 `ProbeSameGridOnce()`，而它的唯一调用点在
        /// `CollectSameGridUnits()`（回答"**玩家**那一格有谁"，只有自主交互/通讯录才走）。
        /// `get_context` 这条链只经过本方法 ⇒ 那个老入口一辈子没跑过 ⇒ 本方法守着一个
        /// 永远不成立的 `if` 干全量活。**快查实现了，"启用快查"的那一步没接上。**
        ///
        /// 修法：本方法自己也触发首查对照（与老入口共用 `_sameGridProbeDone`，全局只跑一次）。
        /// 对照用**本方法自己的坐标**而不是玩家的：对照验证的是"快查 API 与全量扫描在同一格
        /// 上是否等价"，这条判据与具体哪一格无关，但用自己这一格才是直接证据
        /// （NPC 与玩家常常不同格，拿玩家的格去验证这一格属于无谓的间接推理）。
        /// 对照不一致时 `ProbeSameGridOnce` 会把模式钉回 0 并打警告 ⇒ 行为与改动前逐字相同。
        /// </summary>
        /// <returns>收集到的数量（已去重、已剔除玩家）。</returns>
        public static int CollectSameGridAt(List<WorldUnitBase> into, HashSet<string> seenIds, int px, int py)
        {
            if (into == null || seenIds == null) return 0;

            // ★ 11-20：首次调用先做对照（原先只在 `CollectSameGridUnits` 里做，见上方注释）★
            if (!_sameGridProbeDone) ProbeSameGridOnce(px, py);

            // —— 快查（与 CollectSameGridUnits 共用同一套模式探测结论）——
            if (_sameGridQuickMode == 1)
            {
                if (ConsumePointUnits(SafePointUnits(false, px, py), into, seenIds, px, py, null) >= 0)
                    return into.Count;
                _sameGridQuickMode = 0;
                ModMain.P("[RelationNetwork] 同格快查(GetUnit)失败 → 回退全量扫描（坐标 "
                          + px + "," + py + "）");
            }
            else if (_sameGridQuickMode == 2)
            {
                if (ConsumePointUnits(SafePointUnits(true, px, py), into, seenIds, px, py, null) >= 0)
                    return into.Count;
                _sameGridQuickMode = 0;
                ModMain.P("[RelationNetwork] 同格快查(GetUnitExact)失败 → 回退全量扫描（坐标 "
                          + px + "," + py + "）");
            }

            // —— 全量兜底 ——
            ScanAllUnits(into, seenIds, px, py, null);
            return into.Count;
        }

        /// <summary>全量遍历版（原实现，09-28 保留为 fallback 与首查对照）。返回扫描的全量单位数。</summary>
        private static int ScanAllUnits(List<WorldUnitBase> into, HashSet<string> seenIds, int px, int py, HashSet<string> idsOut)
        {
            int scanned = 0;
            try
            {
                var units = g.world.unit.GetUnits(true);
                var dyn = (dynamic)units;
                int cnt = (int)dyn.Count;
                scanned = cnt;
                for (int i = 0; i < cnt; i++)
                {
                    WorldUnitBase u = null;
                    try { u = dyn[i]; } catch { continue; }
                    if (u == null) continue;
                    // ★09-28 顺序优化★：**先比坐标、后读 unitID**。原实现对全量单位（实机 1556 个）
                    // 每个都读一次 `unitID`（interop + 字符串封送），而绝大多数必然不同格 ——
                    // 那次读取是纯白读；同格（罕见）才需要 id 去重，语义不变。
                    int x = int.MinValue, y = int.MinValue;
                    try { x = u.data.unitData.pointX; y = u.data.unitData.pointY; } catch { continue; }
                    if (x != px || y != py) continue;
                    string id = null;
                    try { id = u.data.unitData.unitID; } catch { }
                    if (string.IsNullOrEmpty(id)) continue;
                    if (idsOut != null) idsOut.Add(id);
                    if (into == null || seenIds == null) continue;
                    if (!seenIds.Add(id)) continue;
                    TryAdd(into, null, u, id);
                }
            }
            catch { }
            return scanned;
        }

        /// <summary>公共入列检查：非空、**非玩家**、有名字（单字段失败安全）。
        /// 玩家判据统一走 UnitSnapshot.IsPlayerUnit（unitID 主判据）——旧写法
        /// `ReferenceEquals(u, player)` 在 IL2CPP 下对列表元素不成立，因此把玩家
        /// 自己收进了自主交互候选集。</summary>
        private static void TryAdd(List<WorldUnitBase> into, HashSet<string> contactIds, WorldUnitBase u, string unitId)
        {
            try
            {
                if (u == null) return;
                if (UnitSnapshot.IsPlayerUnit(u)) return;
                string name = null;
                try { name = u.data.unitData.propertyData.GetName(); } catch { }
                if (string.IsNullOrEmpty(name)) return;
                into.Add(u);
                contactIds?.Add(unitId);
            }
            catch { }
        }
    }
}
