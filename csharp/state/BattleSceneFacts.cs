/// <summary>
/// BattleSceneFacts —— 「这一场战斗里到底有谁、谁倒了」的**只读取证口**（10-26）。
///
/// ## 为什么需要它
///
/// NPC 对 NPC 的观战（见 `NpcDuel`）里，玩家不是参战单位，所以
/// `BattleOutcome` 那套"读页面原话 / 读处置页"的判据在这里全都读不到东西。
/// 唯一能回答"谁赢了"的地方是**战场本身**：`SceneBattle.unit` 里那两个 `UnitCtrlBase` 的
/// `isDie`。这是引擎自己的事实（`SpecialBattle79::OnUnitDie` 就是拿它判的）。
///
/// ## 为什么全是反射
///
/// `SceneBattle` 在引擎里**没有任何 static 入口**（本仓 `TeamMultiProbe` 10-19 反射实证，
/// 见其 `FindSceneBattle` 的长注释），唯一持有它的是 `SceneType.battle`。
/// 而 `TeamBattleFollowup` 已经用这条链在真机上取到过战场单位
/// （`BattleEndFront` 里 `GetAllUnitIncludeDie()` → 逐个读 `isDie`，日志有"战场单位 N 个"）。
/// 这里把同一条链抽出来复用，**编译期只依赖 `object`**：
/// 哪天游戏改了字段名，本类整体退化为"读不到"，调用方按字段缺失处理，不会编译不过。
///
/// ## 一条判据两个取值口径
///
/// `GetAllUnitIncludeDie(bool isGetAllUnitType)` 的布尔会过滤单位种类
/// （`TeamBattleFollowup` 传 `false` 只拿它要的那几种）。观战要的是"全体"，
/// 而"全体"到底包含什么，只靠反编不能确定 ⇒ **两个口径各取一次，按引用去重合并**。
/// 多调一次的代价是几个反射调用，换来的是"不会因为一个布尔值漏掉参战的两个人"。
/// 诊断行里会打印两个口径各自的条数，一眼能看出有没有差异。
/// </summary>
using System;
using System.Collections.Generic;
using System.Reflection;

namespace AgentLoopBridge
{
    internal static class BattleSceneFacts
    {
        /// <summary>战场上的一个单位（只留观战/诊断用得上的字段）。</summary>
        internal sealed class Entry
        {
            public string UnitId = "";      // 世界单位 id（读不到就是空，**不猜**）
            public string Name = "";        // 显示名（`UnitDataBase.name`）
            public string Kind = "";        // 运行时类型名（`UnitCtrlHumanNPC` / `UnitCtrlPlayer` …）
            public bool IsDie;
            public int Hp = -1;
            public int HpMax = -1;

            /// <summary>一行给人看的描述（诊断与日志共用）。</summary>
            internal string Line()
            {
                return Kind + " " + (Name.Length > 0 ? Name : "?")
                     + (UnitId.Length > 0 ? "(" + UnitId + ")" : "")
                     + " hp=" + (Hp >= 0 ? Hp.ToString() : "?")
                     + "/" + (HpMax > 0 ? HpMax.ToString() : "?")
                     + " 倒下=" + IsDie;
            }
        }

        // ── 反射句柄缓存（只在第一次调用时解析；解析失败留 null，下次仍会重试一次） ──
        private static Type _sceneType;
        private static bool _sceneTypeTried;

        /// <summary>`SceneType`（拿 `SceneBattle` 的唯一入口）。解析不到返回 null。</summary>
        private static Type SceneType()
        {
            if (_sceneTypeTried) return _sceneType;
            _sceneTypeTried = true;
            try
            {
                try { _sceneType = Type.GetType("SceneType, Assembly-CSharp"); } catch { }
                if (_sceneType == null)
                {
                    foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
                    {
                        try { _sceneType = asm.GetType("SceneType"); } catch { }
                        if (_sceneType != null) break;
                    }
                }
            }
            catch { }
            return _sceneType;
        }

        /// <summary>`SceneType.battle` → `SceneBattle` 实例。不在战斗里/解析不到 ⇒ null。</summary>
        internal static object Scene()
        {
            try
            {
                var t = SceneType();
                if (t == null) return null;
                var p = t.GetProperty("battle",
                    BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic);
                if (p != null) { try { return p.GetValue(null); } catch { } }
                var f = t.GetField("battle",
                    BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic);
                return f == null ? null : f.GetValue(null);
            }
            catch { return null; }
        }

        /// <summary>
        /// 战场单位名单（含已倒下）。**读不到就返回 null**，绝不返回"空名单"冒充"场上没人"——
        /// 这两件事在结果里含义完全不同（见 `NpcDuel.BuildResult` 的 `observed`）。
        /// </summary>
        internal static List<Entry> Roster(out string diag)
        {
            diag = "";
            object scene = Scene();
            if (scene == null) { diag = "拿不到 SceneBattle（SceneType.battle 为空）"; return null; }
            object mgr = Refl.Get(scene, "unit");
            if (mgr == null) { diag = "SceneBattle.unit 为空"; return null; }

            // ★ 只用"全体"这一口径（10-26 真机定案）★
            //   实测同一时刻：`false` 给 2 个（玩家 + 姜萌），`true` 给 3 个（玩家 + 江宇文 + 姜萌）
            //   ⇒ `true` 是**超集**，两个都取只会把同一个人数两遍（那天报告里就出现了
            //     "场上 5 个"其实只有 3 个人）。`false` 只在 `true` 拿不到时兜底。
            var refs = new List<object>();
            int nTrue = Collect(mgr, true, refs);
            int nFalse = nTrue >= 0 ? -2 : Collect(mgr, false, refs);
            diag = "口径 true=" + nTrue + (nFalse == -2 ? "" : " false=" + nFalse) + " 合计=" + refs.Count;
            if (nTrue < 0 && nFalse < 0) { diag += "（两种口径都调不通）"; return null; }

            var list = new List<Entry>();
            foreach (var u in refs)
            {
                var e = Describe(u);
                if (e != null) list.Add(e);
            }
            return list;
        }

        /// <summary>调 `GetAllUnitIncludeDie(bool)` 并把**去重后**的引用塞进 `acc`。返回本条口径拿到的条数。</summary>
        private static int Collect(object mgr, bool flag, List<object> acc)
        {
            try
            {
                var mi = mgr.GetType().GetMethod("GetAllUnitIncludeDie",
                    BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                object list = null;
                if (mi != null)
                {
                    var ps = mi.GetParameters();
                    list = mi.Invoke(mgr, ps.Length == 0 ? new object[0] : new object[] { flag });
                }
                if (list == null) list = Refl.Get(mgr, "allUnitIncludeDie");
                if (list == null) return -1;

                int n = 0;
                try { n = Convert.ToInt32(Refl.Get(list, "Count")); } catch { n = -1; }
                if (n < 0) return -1;
                for (int i = 0; i < n; i++)
                {
                    object u = null;
                    try { u = ((dynamic)list)[i]; } catch { continue; }
                    if (u == null) continue;
                    if (!AlreadyHave(acc, u)) acc.Add(u);
                }
                return n;
            }
            catch { return -1; }
        }

        /// <summary>
        /// 同一个战场单位会不会给出两个托管包装器 —— IL2CPP 下**不保证引用相等**（本仓铁律）。
        /// 所以先去重单位 id，id 读不到再退引用比较。
        /// </summary>
        private static bool AlreadyHave(List<object> acc, object u)
        {
            if (acc.Count == 0) return false;
            if (ReferenceEquals(acc[0], u)) return true;
            string id = IdOf(u);
            // ① 有 unitID 就按 unitID
            if (id.Length > 0)
            {
                for (int i = 0; i < acc.Count; i++)
                {
                    if (IdOf(acc[i]) == id) return true;
                }
            }
            // ② ★ 没有 unitID 时必须按"看起来是同一个人"去重 ★
            //   真机：`data.worldUnitData` 这条链在战场单位上读空（本仓 `TeamBattleFollowup` 也记过
            //   同一个坑），于是旧写法退成引用比较 —— 而 IL2CPP 同一个单位会给不同包装器
            //   ⇒ 一个人被数成两个。用 (类型 + 名字 + hp + hpMax) 当指纹，够稳且不会误合并
            //   （同一场里两个不同的人同时同血同上限的概率极低；真撞了也只是少报一个）。
            var me = Describe(u);
            if (me == null) return false;
            for (int i = 0; i < acc.Count; i++)
            {
                var it = Describe(acc[i]);
                if (it == null) continue;
                if (it.Kind == me.Kind && it.Name == me.Name
                    && it.Hp == me.Hp && it.HpMax == me.HpMax) return true;
            }
            return false;
        }

        /// <summary>战场单位 → `Entry`。读不出名字/id 的也保留（它们在场上这件事本身就是事实）。</summary>
        private static Entry Describe(object u)
        {
            if (u == null) return null;
            var e = new Entry();
            try { e.Kind = u.GetType().Name; } catch { }
            object data = Refl.Get(u, "data");
            e.Name = (Refl.Get(data, "name") as string) ?? "";
            if (e.Name.Length == 0) { e.Name = (Refl.Get(u, "name") as string) ?? ""; }
            e.UnitId = IdOf(u);
            e.IsDie = Refl.Bool(Refl.Get(u, "isDie"));
            e.Hp = Refl.Int(Refl.Get(data, "hp"));
            e.HpMax = Refl.Int(DynValue(Refl.Get(data, "maxHP")));
            return e;
        }

        /// <summary>战场单位 → 世界单位 id（`data.worldUnitData.unitData.unitID`）。读不到返回 ""。</summary>
        internal static string IdOf(object unit)
        {
            try
            {
                object data = Refl.Get(unit, "data");
                object wud = Refl.Get(data, "worldUnitData");
                object ud = Refl.Get(wud, "unitData");
                return (Refl.Get(ud, "unitID") as string) ?? "";
            }
            catch { return ""; }
        }

        /// <summary>在名单里按 unitID 找人；id 读不到时才按名字退一步（同名会撞，所以只在无 id 时用）。</summary>
        internal static Entry Match(List<Entry> roster, string unitId, string name)
        {
            if (roster == null) return null;
            if (!string.IsNullOrEmpty(unitId))
                foreach (var e in roster)
                    if (e.UnitId == unitId) return e;
            if (!string.IsNullOrEmpty(name))
                foreach (var e in roster)
                    if (e.Name == name) return e;
            return null;
        }

        /// <summary>名单一行摘要（诊断/日志用）。</summary>
        internal static string Describe(List<Entry> roster)
        {
            if (roster == null) return "（读不到战场单位）";
            if (roster.Count == 0) return "（战场单位 0 个）";
            var sb = new System.Text.StringBuilder();
            foreach (var e in roster)
            {
                if (sb.Length > 0) sb.Append(" ｜ ");
                sb.Append(e.Line());
            }
            return sb.ToString();
        }

        // ══════════════════════════════════════════════════════════
        //  反射小工具（读不到一律 null，不抛）
        // ══════════════════════════════════════════════════════════

        /// <summary>
        /// `DynInt` → int。三参 `Value(null,true,true)` 是面板口径（含加成 + 上下限钳制），
        /// 与 `UnitSnapshot.DynOf` 同一取法；失败退 `.value`。读不到返回 null。
        /// </summary>
        internal static int? DynValue(object box)
        {
            if (box == null) return null;
            try
            {
                var ty = box.GetType();
                foreach (var m in ty.GetMethods())
                {
                    if (m.Name != "Value") continue;
                    if (m.GetParameters().Length != 3) continue;
                    try { return Convert.ToInt32(m.Invoke(box, new object[] { null, true, true })); } catch { }
                }
                var vp = ty.GetProperty("value");
                if (vp != null) { try { return Convert.ToInt32(vp.GetValue(box, null)); } catch { } }
            }
            catch { }
            return null;
        }
    }
}
