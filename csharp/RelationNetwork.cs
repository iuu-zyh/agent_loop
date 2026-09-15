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

        /// <summary>⑤ 同格所有人（含与玩家无任何关系的陌生人——当面可搭话）。记名语义：非通讯录成员。</summary>
        public static void CollectSameGridUnits(List<WorldUnitBase> into, HashSet<string> seenIds)
        {
            if (into == null || seenIds == null) return;
            try
            {
                var player = g.world.playerUnit;
                if (player == null) return;
                int px = 0, py = 0;
                try { px = player.data.unitData.pointX; py = player.data.unitData.pointY; } catch { return; }

                var units = g.world.unit.GetUnits(true);
                var dyn = (dynamic)units;
                int cnt = (int)dyn.Count;
                for (int i = 0; i < cnt; i++)
                {
                    WorldUnitBase u = null;
                    try { u = dyn[i]; } catch { continue; }
                    if (u == null) continue;
                    string id = null;
                    try { id = u.data.unitData.unitID; } catch { }
                    if (string.IsNullOrEmpty(id) || !seenIds.Add(id)) continue;
                    int x = int.MinValue, y = int.MinValue;
                    try { x = u.data.unitData.pointX; y = u.data.unitData.pointY; } catch { continue; }
                    if (x != px || y != py) continue;
                    TryAdd(into, null, u, id);
                }
            }
            catch { }
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
