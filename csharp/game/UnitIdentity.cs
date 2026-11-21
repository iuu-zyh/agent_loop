/// <summary>
/// 单位身份 —— 取中文名 / 取 unitID / 取玩家。**全仓唯一一份**。
///
/// 【为什么要收拢】
/// 11-19 复核：同一句 `u.data.unitData.propertyData.GetName()` 和
/// `u.data.unitData.unitID` 被包了 12 遍，散在 `TeamTool` / `NpcDuel` / `UnitSnapshot` /
/// `ToolExecutor` / `BattleOutcome` / `DramaSegmentRecorder` / `ShowDramaService` /
/// `TeamMultiProbe` / `DramaNativeClaim`。差异全在「取不到时给什么」：
/// `""` / `null` / `"?"` / `"(空)"` / `"(无名)"` / `"(取不到)"` —— 六种。
/// 后果不是崩溃，是**日志与界面上同一件事有三种写法**：排障时按名字 grep 会漏，
/// 而且每个新面板都会再抄一份、再选一种。
///
/// 【口径（定死）】
/// · `Id` / `PlayerId`：取不到 = `""`（空串，不返回 null）。
/// · `Name`：取不到 = 调用方给的 fallback（默认 `""`）。
/// · `NameOr`：**只给"要显示给人看的兜底词"的场合用**（`(空)`/`(无名)`/`(取不到)`），
///   三个兜底词显式传进来 —— 这样兜底文案仍是调用方的表达，不再是"另一份实现"。
///
/// 【为什么不在这里碰 g.world 的其它东西】
/// 本类只做"从 `WorldUnitBase` 读两个字段"。查人（名字 → 单位）是
/// `game/UnitLookup.cs` 的职责，别在这里再长一个 `Resolve`。
/// </summary>
using System;

namespace AgentLoopBridge
{
    internal static class UnitIdentity
    {
        /// <summary>玩家单位（未进世界/取不到 = null）。主线程调用。</summary>
        public static WorldUnitBase Player()
        {
            try { return g.world != null ? g.world.playerUnit : null; } catch { return null; }
        }

        /// <summary>单位 unitID（空 = 取不到）。</summary>
        public static string Id(WorldUnitBase u)
        {
            if (u == null) return "";
            try { return u.data != null && u.data.unitData != null ? (u.data.unitData.unitID ?? "") : ""; }
            catch { return ""; }
        }

        /// <summary>单位中文名（取不到 = <paramref name="fallback"/>）。</summary>
        public static string Name(WorldUnitBase u, string fallback = "")
        {
            if (u == null) return fallback ?? "";
            try
            {
                string nm = u.data?.unitData?.propertyData?.GetName();
                if (!string.IsNullOrEmpty(nm)) return nm;
            }
            catch { }
            return fallback ?? "";
        }

        /// <summary>
        /// 单位中文名，**三种失败分开兜底**（只给"要显示给人看的那一处"用）：
        /// 单位为空 → <paramref name="ifNull"/>；读到了但名字为空 → <paramref name="ifEmpty"/>；
        /// 读的过程抛异常 → <paramref name="ifError"/>（不传则退 <paramref name="ifEmpty"/>）。
        /// </summary>
        public static string NameOr(WorldUnitBase u, string ifNull, string ifEmpty, string ifError = null)
        {
            if (u == null) return ifNull ?? "";
            string nm;
            try { nm = u.data?.unitData?.propertyData?.GetName(); }
            catch { return ifError ?? ifEmpty ?? ""; }
            return string.IsNullOrEmpty(nm) ? (ifEmpty ?? "") : nm;
        }

        /// <summary>unitID → 中文名（取不到 = <paramref name="fallback"/>）。走 `g.world.unit.GetUnit`。</summary>
        public static string NameOfId(string unitId, string fallback = "")
        {
            if (string.IsNullOrEmpty(unitId)) return fallback ?? "";
            try
            {
                var u = g.world?.unit?.GetUnit(unitId);
                string nm = Name(u, null);
                return string.IsNullOrEmpty(nm) ? (fallback ?? "") : nm;
            }
            catch { return fallback ?? ""; }
        }

        /// <summary>玩家 unitID（取不到 = ""）。</summary>
        public static string PlayerId()
        {
            try
            {
                var p = Player();
                return p == null ? "" : Id(p);
            }
            catch { return ""; }
        }

        /// <summary>玩家中文名（取不到 = <paramref name="fallback"/>）。</summary>
        public static string PlayerName(string fallback = "")
        {
            try { return Name(Player(), fallback); } catch { return fallback ?? ""; }
        }
    }
}
