using System;
using System.Collections.Generic;
using HarmonyLib;
using UnhollowerBaseLib;

namespace AgentLoopBridge
{
    internal static class TeamBattleSafety
    {
        private static readonly TeamBattleAdmissionCore Core = new TeamBattleAdmissionCore();
        private static readonly HashSet<string> Notes = new HashSet<string>();
        private static readonly TeamHurtInstallCore HurtInstall = new TeamHurtInstallCore();
        private static string _spawningId;
        internal const int SelectionCondition = ModIds.Mod + 3;
        internal const int HurtEffect = ModIds.Mod + 1;

        internal static long BattleKey
        {
            get
            {
                try { return g.world?.battle?.data?.Pointer.ToInt64() ?? 0; }
                catch { return 0; }
            }
        }

        internal static string[] Read(Il2CppStringArray values)
        {
            if (values == null) return null;
            var result = new string[values.Length];
            for (int i = 0; i < result.Length; i++) result[i] = values[i];
            return result;
        }

        internal static bool OwnSelection(Il2CppStringArray values)
        {
            return TeamBattleAdmissionCore.IsSelection(Read(values), SelectionCondition.ToString());
        }

        internal static bool Admitted { get { return Core.HasTeam(BattleKey); } }
        internal static bool Spawned { get { return Core.HasSpawned(BattleKey); } }
        internal static bool CanHurt(string id, bool player, bool dead, bool used) { return Core.CanHurt(id, player, dead, used); }
        internal static bool Participated(string id) { return Core.Participated(id); }
        internal static bool Registered(string id) { return Core.Contains(id); }

        internal static void Register(WorldBattleData data, int start)
        {
            var ids = new List<string>();
            if (data?.npcUnits != null)
                for (int i = start; i < data.npcUnits.Count; i++) ids.Add(TeamTool.UnitIdOf(data.npcUnits[i]));
            Core.Register(data == null ? 0 : data.Pointer.ToInt64(), ids, start + 3);
            ModMain.P("[TeamBattleSafety] 已登记本场真实注入队友 " + ids.Count + " 人");
        }

        internal static void Reset()
        {
            Core.Reset();
            _spawningId = null;
            Notes.Clear();
        }

        internal static bool Reject(string reason)
        {
            if (Notes.Add(BattleKey + ":" + reason)) ModMain.P("[TeamBattleSafety] 已阻止：" + reason);
            return false;
        }

        internal static string InstallReport()
        {
            int found = 0, total = 0;
            foreach (var method in HarmonyLib.Harmony.GetAllPatchedMethods())
            {
                var info = HarmonyLib.Harmony.GetPatchInfo(method);
                if (info == null) continue;
                foreach (var patch in info.Prefixes)
                    if (patch.PatchMethod.DeclaringType != null && patch.PatchMethod.DeclaringType.Name.StartsWith("Team")
                        && patch.PatchMethod.DeclaringType.Name.EndsWith("Safety")) found++;
                foreach (var patch in info.Postfixes)
                    if (patch.PatchMethod.DeclaringType == typeof(TeamBattleSpawnIdentity)) total++;
            }
            return "队友安全序列：前置=" + found + "，真实战斗单位绑定=" + total
                + "，重伤页保护=" + (HurtInstall.Installed ? "已启用" : HurtInstall.Stopped ? "未启用" : "等待挂载");
        }

        private static HarmonyLib.Harmony _hurtHarmony;

        internal static void AttachHurtGuards()
        {
            DetachHurtGuards();
            FrameTicker.Ensure(FrameTicker.KTeamHurtInstall, "teamhurt", TryInstallHurtGuards);
            ModMain.P("[TeamBattleSafety] 重伤页钩子延后到玩家与主地图就绪，最多尝试3次");
        }

        internal static void DetachHurtGuards()
        {
            FrameTicker.Stop(FrameTicker.KTeamHurtInstall);
            if (_hurtHarmony != null) _hurtHarmony.UnpatchSelf();
            _hurtHarmony = null;
            HurtInstall.Reset();
        }

        private static void TryInstallHurtGuards()
        {
            // 主菜单时反射挂载 DramaTool 会提前运行依赖配置的静态构造，失败会污染整个进程。
            bool ready = TeamBattleAdmissionCore.CanInstallHurtGuards(g.world != null,
                g.world?.playerUnit?.data?.unitData != null, WorldStateFacts.MainWorldReady());
            double now = (double)System.Diagnostics.Stopwatch.GetTimestamp() / System.Diagnostics.Stopwatch.Frequency;
            if (!HurtInstall.TryBegin(ready, now)) return;
            try
            {
                InstallHurtGuards();
                HurtInstall.Succeed();
                ModMain.P("[TeamBattleSafety] 重伤页保护已启用，尝试次数=" + HurtInstall.Attempts);
            }
            catch (Exception e)
            {
                bool fatal = false;
                // 类型初始化失败会在本进程缓存，继续重试不能修复它。
                for (Exception current = e; current != null; current = current.InnerException)
                    if (current is TypeInitializationException) fatal = true;
                HurtInstall.Fail(fatal);
                ModMain.P("[TeamBattleSafety] 重伤页挂载失败 " + HurtInstall.Attempts + "/" + TeamHurtInstallCore.MaxAttempts
                    + (fatal ? "；类型初始化失败，请完整重启游戏：" : HurtInstall.Stopped ? "；保护未启用，本轮停止重试：" : "；1秒后重试：") + e);
            }
            if (HurtInstall.Stopped) FrameTicker.Stop(FrameTicker.KTeamHurtInstall);
        }

        [System.Runtime.CompilerServices.MethodImpl(System.Runtime.CompilerServices.MethodImplOptions.NoInlining)]
        private static void InstallHurtGuards()
        {
            var harmony = new HarmonyLib.Harmony("AgentLoopBridge.TeamHurtSafety");
            try
            {
                var first = AccessTools.Method(typeof(DramaTool), "OpenDrama", new[] { typeof(int), typeof(DramaData) });
                var second = AccessTools.Method(typeof(DramaTool), "OpenDrama", new[] { typeof(string), typeof(DramaData) });
                if (first == null || second == null) throw new MissingMethodException("DramaTool.OpenDrama");
                harmony.Patch(first, prefix: new HarmonyMethod(AccessTools.Method(typeof(TeamHurtDramaIntSafety), "Prefix")));
                harmony.Patch(second, prefix: new HarmonyMethod(AccessTools.Method(typeof(TeamHurtDramaStringSafety), "Prefix")));
                if (!HarmonyLib.Harmony.GetPatchInfo(first).Owners.Contains(harmony.Id)
                    || !HarmonyLib.Harmony.GetPatchInfo(second).Owners.Contains(harmony.Id))
                    throw new InvalidOperationException("重伤页两个入口未全部登记");
                _hurtHarmony = harmony;
                ModMain.P("[TeamBattleSafety] 世界就绪，重伤页两个入口挂载成功");
            }
            catch { harmony.UnpatchSelf(); throw; }
        }

        internal static bool CanGenerate()
        {
            var mates = TeamTool.Members(out _);
            bool hasMembers = false;
            if (mates != null)
                foreach (var mate in mates)
                    if (mate != null && !TeamTool.IsPlayer(mate) && !string.IsNullOrEmpty(TeamTool.UnitIdOf(mate)))
                    { hasMembers = true; break; }
            bool duel = g.world?.battle?.data?.dungeonBaseItem?.type == ModIds.DungeonTypeDuel;
            return TeamBattleAdmissionCore.CanStart(hasMembers, TeamContainerInject.NoInject,
                duel, TeamJoinGate.PeekConsent() == JoinAnswer.Yes);
        }

        internal static bool SlotMatches(int slot)
        {
            try
            {
                var units = g.world?.battle?.data?.npcUnits;
                int index = slot - 3;
                return units != null && index >= 0 && index < units.Count
                    && Core.CanUseSlot(BattleKey, slot, TeamTool.UnitIdOf(units[index]));
            }
            catch { return false; }
        }


        internal static bool ResolveUnit(object unit, out string id)
        {
            var ctrl = unit as UnitCtrlBase;
            id = null;
            return ctrl != null && Core.ResolveUnit(BattleKey, ctrl.Pointer.ToInt64(), out id);
        }

        internal static void Capture(WorldUnitBase world, UnitCtrlHumanNPC unit)
        {
            string id = TeamTool.UnitIdOf(world);
            if (unit == null || id != _spawningId || !Core.CaptureUnit(BattleKey, unit.Pointer.ToInt64(), id)) return;
            TeamContainerInject.RegisterBattleUnit(unit);
            ModMain.P("[TeamBattleSafety] 已绑定实际战斗队友 " + id);
        }

        internal static string SpawningId { get { return _spawningId; } set { _spawningId = value; } }

        internal static bool TryMemberSlot(string value, bool requireSpawned, out int slot, out string id)
        {
            slot = 0;
            id = null;
            int index = TeamBattleCommandCore.MemberIndex(value);
            int[] slots = Core.Slots;
            if (!Admitted || index < 0 || index >= slots.Length) return false;
            slot = slots[index];
            if (!SlotMatches(slot)) return false;
            id = TeamTool.UnitIdOf(g.world.battle.data.npcUnits[slot - 3]);
            return requireSpawned ? Core.Participated(id) : !Core.Participated(id);
        }

        internal static bool AllowHurtPage()
        {
            // 死亡回调和战后扫尾可能相邻，先用已登记单位补齐死亡记录，再检查伤者身份。
            TeamContainerInject.SweepDeadBattleUnits();
            return TeamContainerInject.HasPendingHurtMate();
        }
    }

    [HarmonyPatch(typeof(BattleFunction), nameof(BattleFunction.OptionsFunction), new Type[] { typeof(string), typeof(BattleFunction.BattleFunctionData) })]
    internal static class TeamBattleSequenceSafety
    {
        private static void Prefix(ref string __0)
        {
            __0 = TeamBattleCommandCore.Rewrite(__0, TeamBattleSafety.SelectionCondition.ToString(), TeamBattleSafety.HurtEffect.ToString());
        }
    }

    [HarmonyPatch(typeof(BattleFunction), nameof(BattleFunction.AddBattleUnit), new[] { typeof(Il2CppStringArray) })]
    internal static class TeamBattleSpawnCallSafety
    {
        private static bool Prefix(ref Il2CppStringArray __0, out string __state)
        {
            __state = TeamBattleSafety.SpawningId;
            var args = TeamBattleSafety.Read(__0);
            if (!TeamBattleCommandCore.IsMarked(args, TeamBattleSafety.SelectionCondition.ToString(), 8)) return true;
            int slot;
            string id;
            if (!TeamBattleSafety.TryMemberSlot(args[1], false, out slot, out id))
                return TeamBattleSafety.Reject("本场没有对应的待生成队友，取消该生成命令");
            __0 = new Il2CppStringArray(TeamBattleCommandCore.Remap(args, slot));
            TeamBattleSafety.SpawningId = id;
            return true;
        }

        private static Exception Finalizer(Exception __exception, string __state)
        {
            TeamBattleSafety.SpawningId = __state;
            return __exception;
        }
    }

    [HarmonyPatch]
    internal static class TeamBattleSpawnIdentity
    {
        private static System.Collections.Generic.IEnumerable<System.Reflection.MethodBase> TargetMethods()
        {
            foreach (var method in typeof(BattleFunction).GetMethods())
                if (method.Name == "AddBattleUnit" && method.IsStatic && method.ReturnType == typeof(UnitCtrlHumanNPC)
                    && method.GetParameters().Length > 0 && method.GetParameters()[0].ParameterType == typeof(WorldUnitBase))
                    yield return method;
        }
        private static void Postfix(WorldUnitBase __0, UnitCtrlHumanNPC __result)
        {
            TeamBattleSafety.Capture(__0, __result);
        }
    }

    [HarmonyPatch(typeof(BattleTriggerBase), nameof(BattleTriggerBase.IsTrigger))]
    internal static class TeamBattleTriggerSafety
    {
        private static bool Prefix(ConfDungeonTriggerItem __0, ref bool __result)
        {
            if (__0 == null) return true;
            int id = __0.id;
            if (id != ModIds.Mod + 1 && id != ModIds.Mod + 2 && id != ModIds.Mod + 3 && id != ModIds.Mod + 4) return true;
            try
            {
                bool generation = id == ModIds.Mod + 1 || id == ModIds.Mod + 4;
                if (generation ? TeamBattleSafety.CanGenerate() : TeamBattleSafety.Spawned) return true;
            }
            catch (Exception e) { ModMain.P("[TeamBattleSafety] 本模组触发器校验失败：" + e.Message); }
            __result = false;
            return TeamBattleSafety.Reject("队友触发器 " + id + " 缺少本场许可或真实队友");
        }
    }

    [HarmonyPatch(typeof(BattleFunction), nameof(BattleFunction.SetBattleData))]
    internal static class TeamBattleFlagSafety
    {
        private static bool Prefix(ref Il2CppStringArray __0)
        {
            var a = TeamBattleSafety.Read(__0);
            if (a == null || a.Length < 3 || a[1] != "alTeamIn" || a[2] != "ok") return true;
            if (TeamBattleCommandCore.IsMarked(a, TeamBattleSafety.SelectionCondition.ToString(), 3))
                __0 = new Il2CppStringArray(new[] { a[0], a[1], a[2] });
            return TeamBattleSafety.Spawned || TeamBattleSafety.Reject("没有实际生成的队友，不能置 alTeamIn 标志");
        }
    }

    [HarmonyPatch(typeof(BattleFunction), nameof(BattleFunction.UnitAddEffect))]
    internal static class TeamBattleEffectSafety
    {
        private static bool Prefix(ref Il2CppStringArray __0)
        {
            var a = TeamBattleSafety.Read(__0);
            if (a == null || a.Length < 3 || a[2] != TeamBattleSafety.HurtEffect.ToString()) return true;
            string id;
            int slot;
            if (TeamBattleCommandCore.IsMarked(a, TeamBattleSafety.SelectionCondition.ToString(), 3))
            {
                if (!TeamBattleSafety.TryMemberSlot(a[1], true, out slot, out id))
                    return TeamBattleSafety.Reject("受伤效果没有对应的实际参战队友");
                __0 = new Il2CppStringArray(TeamBattleCommandCore.Remap(a, slot));
                return true;
            }
            return int.TryParse(a[1], out slot) && TeamBattleSafety.SlotMatches(slot)
                && TeamBattleSafety.Participated(TeamTool.UnitIdOf(g.world.battle.data.npcUnits[slot - 3]))
                || TeamBattleSafety.Reject("重伤回调目标不是本场登记的队友");
        }
    }

    internal static class TeamHurtDramaIntSafety
    {
        private static bool Prefix(int __0, ref bool __result)
        {
            if (__0 != ModIds.DramaTeamHurtBase) return true;
            if (TeamBattleSafety.AllowHurtPage()) return true;
            __result = false;
            return TeamBattleSafety.Reject("没有已登记的真实伤者，取消重伤页及其气运副作用");
        }
    }

    internal static class TeamHurtDramaStringSafety
    {
        private static bool Prefix(string __0, ref bool __result)
        {
            if (__0 != ModIds.DramaTeamHurtBase.ToString()) return true;
            if (TeamBattleSafety.AllowHurtPage()) return true;
            __result = false;
            return TeamBattleSafety.Reject("没有已登记的真实伤者，取消重伤页及其气运副作用");
        }
    }
}
