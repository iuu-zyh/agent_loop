using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using AgentLoopBridge;
using HarmonyLib;
using UnhollowerBaseLib;

// 真正的 Harmony 绑定生产接线代码；游戏对象用最小替身，避免测试依赖运行中的 Unity。
namespace UnhollowerBaseLib
{
    public class Il2CppStringArray
    {
        private readonly string[] _values;
        public Il2CppStringArray(string[] values) { _values = values; }
        public int Length { get { return _values.Length; } }
        public string this[int i] { get { return _values[i]; } }
    }
}
public class WorldUnitBase
{
    public string Id; public bool Player; public WorldUnitData data;
    public WorldUnitBase() { data = new WorldUnitData { unit = this }; }
}
public class WorldUnitData
{
    private static int next = 10000;
    public IntPtr Pointer = new IntPtr(++next);
    public WorldUnitBase unit;
    public object unitData = new object();
}
public enum UnitType { NPC }
public class UnitCtrlBase { public IntPtr Pointer; }
public class UnitCtrlHumanNPC : UnitCtrlBase
{
    [MethodImpl(MethodImplOptions.NoInlining)]
    public void InitData(WorldUnitData data, UnitType type) { }
}
public class WorldBattleData
{
    public IntPtr Pointer = new IntPtr(100);
    public List<WorldUnitBase> npcUnits = new List<WorldUnitBase>();
    public DungeonItem dungeonBaseItem = new DungeonItem();
}
public class DungeonItem { public int type; }
public class Battle { public WorldBattleData data = new WorldBattleData(); }
public class World { public Battle battle = new Battle(); public WorldUnitBase playerUnit = new WorldUnitBase { Player = true }; }
public static class g { public static World world = new World(); }
public class ConfDungeonTriggerItem { public int id; }
public class BattleTriggerBase
{
    [MethodImpl(MethodImplOptions.NoInlining)]
    public bool IsTrigger(ConfDungeonTriggerItem item) { return true; }
}
public class DramaData { }
public static class DramaTool
{
    public static int Opened;
    [MethodImpl(MethodImplOptions.NoInlining)]
    public static bool OpenDrama(int id, DramaData data) { Opened++; return true; }
    [MethodImpl(MethodImplOptions.NoInlining)]
    public static bool OpenDrama(string id, DramaData data) { Opened++; return true; }
}
public class BattleFunction
{
    public class BattleFunctionData { public string Tag; }
    public List<string> Trace = new List<string>();
    public List<int> Generated = new List<int>();
    public List<int> Injured = new List<int>();
    public bool Flag, Deferred;
    public HashSet<string> ReturnNull = new HashSet<string>();
    public bool ThrowOnSpawn;
    public bool ThrowAfterInit;
    public Action DuringSpawn;
    public UnitCtrlHumanNPC LastCreated;
    private List<Action> _pending;
    private static int _pointer = 1000;

    [MethodImpl(MethodImplOptions.NoInlining)]
    public void OptionsFunction(string function, BattleFunctionData data)
    {
        _pending = new List<Action>();
        foreach (string token in function.Split('|'))
        {
            var values = new Il2CppStringArray(token.Split('_'));
            _pending.Add(() => {
                if (values[0] == "addBattleNPCData") AddBattleNPCData(values);
                else if (values[0] == "addBattleUnit") AddBattleUnit(values);
                else if (values[0] == "setBattleData") SetBattleData(values);
                else if (values[0] == "unitAddEffect") UnitAddEffect(values);
                else Trace.Add(token + ":" + data.Tag);
            });
        }
        if (!Deferred) RunPending();
    }
    public void RunPending() { foreach (var action in _pending) action(); }
    public void AddBattleNPCData(Il2CppStringArray args)
    {
        if (args[1] != TeamBattleSafety.SelectionCondition.ToString() || TeamBattleSafety.Admitted) return;
        int start = g.world.battle.data.npcUnits.Count;
        foreach (var mate in TeamTool.Mates) g.world.battle.data.npcUnits.Add(mate);
        TeamBattleSafety.Register(g.world.battle.data, start);
    }
    [MethodImpl(MethodImplOptions.NoInlining)]
    public void AddBattleUnit(Il2CppStringArray args)
    {
        if (ThrowOnSpawn) throw new InvalidOperationException("native generation failure");
        if (args.Length != 8) throw new Exception("internal marker leaked into native spawn");
        int slot = int.Parse(args[1]);
        var mate = g.world.battle.data.npcUnits[slot - 3];
        Trace.Add("spawn:" + slot);
        var nested = DuringSpawn; DuringSpawn = null;
        if (nested != null) nested();
        var result = AddBattleUnit(mate, !ReturnNull.Contains(mate.Id));
        LastCreated = result;
        if (ThrowAfterInit) throw new InvalidOperationException("failure after initialization");
        if (result != null) Generated.Add(slot);
    }
    [MethodImpl(MethodImplOptions.NoInlining)]
    public static UnitCtrlHumanNPC AddBattleUnit(WorldUnitBase unit, bool success)
    {
        if (!success) return null;
        var result = new UnitCtrlHumanNPC { Pointer = new IntPtr(++_pointer) };
        result.InitData(unit.data, UnitType.NPC);
        return result;
    }
    [MethodImpl(MethodImplOptions.NoInlining)]
    public void SetBattleData(Il2CppStringArray args)
    {
        if (args.Length != 3) throw new Exception("internal marker leaked into native flag");
        if (args[1] == "alTeamIn") Flag = true;
        Trace.Add("flag:" + args[1]);
    }
    [MethodImpl(MethodImplOptions.NoInlining)]
    public void UnitAddEffect(Il2CppStringArray args)
    {
        if (args.Length != 3) throw new Exception("internal marker leaked into native effect");
        Injured.Add(int.Parse(args[1]));
    }
}
namespace AgentLoopBridge
{
    internal static class ModIds { internal const int Mod = -803158451, DungeonTypeDuel = 40, DramaTeamHurtBase = -803158261; }
    internal static class ModMain { internal static void P(string value) { } }
    internal static class TeamTool
    {
        internal static List<WorldUnitBase> Mates = new List<WorldUnitBase>();
        internal static string UnitIdOf(WorldUnitBase u) { return u == null ? "" : u.Id; }
        internal static bool IsPlayer(WorldUnitBase u) { return u.Player; }
        internal static List<WorldUnitBase> Members(out int duplicates) { duplicates = 0; return Mates; }
    }
    internal static class TeamContainerInject
    {
        internal static bool NoInject = false, HurtPending;
        internal static List<UnitCtrlBase> Registered = new List<UnitCtrlBase>();
        internal static void RegisterBattleUnit(UnitCtrlBase unit) { Registered.Add(unit); }
        internal static void SweepDeadBattleUnits() { }
        internal static bool HasPendingHurtMate() { return HurtPending; }
    }
    internal enum JoinAnswer { None, Yes }
    internal static class TeamJoinGate { internal static JoinAnswer PeekConsent() { return JoinAnswer.Yes; } }
    internal static class WorldStateFacts { internal static bool Ready; internal static bool MainWorldReady() { return Ready; } }
    internal static class FrameTicker
    {
        internal const int KTeamHurtInstall = 22;
        internal static Action Callback;
        internal static void Ensure(int key, string name, Action action) { Callback = action; }
        internal static void Stop(int key) { Callback = null; }
    }
}
internal static class Program
{
    static int count, installCalls;
    static bool fatal;
    static void Check(bool ok, string name) { if (!ok) throw new Exception(name); count++; }
    static string Select { get { return "addBattleNPCData_" + TeamBattleSafety.SelectionCondition + "_0"; } }
    static string Generation { get { return Select + "|addBattleUnit_3_-1_10_2_1_2_0|addBattleUnit_4_-1_10_2_1_2_0|addBattleUnit_5_-1_10_2_1_2_0|setBattleData_alTeamIn_ok"; } }
    static string Injury { get { return string.Join("|", new[] { 3, 4, 5 }.Select(i => "unitAddEffect_" + i + "_" + TeamBattleSafety.HurtEffect)); } }
    static BattleFunction Start(int mates)
    {
        TeamBattleSafety.Reset();
        g.world = new World();
        g.world.battle.data.npcUnits.Add(new WorldUnitBase { Id = "enemy1" });
        g.world.battle.data.npcUnits.Add(new WorldUnitBase { Id = "enemy2" });
        TeamTool.Mates = Enumerable.Range(1, mates).Select(i => new WorldUnitBase { Id = "mate" + i }).ToList();
        TeamContainerInject.HurtPending = false;
        TeamContainerInject.Registered.Clear();
        return new BattleFunction();
    }
    static void Execute(BattleFunction f, string text) { f.OptionsFunction(text, new BattleFunction.BattleFunctionData { Tag = "context" }); }
    static void InstallFailure()
    {
        installCalls++;
        if (fatal) throw new TypeInitializationException("DramaTool", new Exception("poisoned"));
        if (installCalls < 3) throw new InvalidOperationException("transient failure");
    }
    static void AdvanceRetry()
    {
        var retry = (TeamHurtInstallCore)AccessTools.Field(typeof(TeamBattleSafety), "HurtInstall").GetValue(null);
        AccessTools.Field(typeof(TeamHurtInstallCore), "_nextAttempt").SetValue(retry, 0d);
        if (FrameTicker.Callback != null) FrameTicker.Callback();
    }
    static void Main()
    {
        var harmony = new Harmony("TeamBattleWiringTests");
        harmony.PatchAll(Assembly.GetExecutingAssembly());
        var nativeGenerator = AccessTools.Method(typeof(BattleFunction), "AddBattleUnit", new[] { typeof(WorldUnitBase), typeof(bool) });
        Check(Harmony.GetPatchInfo(nativeGenerator) == null, "native bool generator must have no identity hook");
        var initializer = AccessTools.Method(typeof(UnitCtrlHumanNPC), "InitData", new[] { typeof(WorldUnitData), typeof(UnitType) });
        Check(Harmony.GetPatchInfo(initializer).Postfixes.Any(p => p.PatchMethod.DeclaringType == typeof(TeamBattleSpawnIdentity)), "identity observes exact no-bool initializer");
        var f = Start(0); Execute(f, Generation);
        Check(f.Generated.Count == 0 && !f.Flag, "empty party must not clone enemies or set flag");
        Check(g.world.battle.data.npcUnits.Count == 2, "original NPC list remains intact");
        f = Start(2); Execute(f, "before|" + Generation + "|after");
        Check(f.Generated.SequenceEqual(new[] { 5, 6 }) && f.Flag, "actual appended positions generate and allow flag");
        Check(f.Trace.First() == "before:context" && f.Trace.Last() == "after:context", "extra commands and caller context remain in order");
        Check(!TeamBattleSafety.HasSpawnScope, "spawn scope cleaned on success");
        Check(TeamContainerInject.Registered.Count == 2, "successful teammates reach death and followup registry");
        Execute(f, Injury);
        Check(f.Injured.SequenceEqual(new[] { 5, 6 }), "injury effects target actual participants");
        Execute(f, Generation);
        Check(f.Generated.Count == 2 && g.world.battle.data.npcUnits.Count == 4, "repeated sequence neither appends nor respawns");
        f = Start(2); f.ReturnNull.UnionWith(new[] { "mate1", "mate2" }); Execute(f, Generation);
        Check(TeamBattleSafety.Admitted && !TeamBattleSafety.Spawned && !f.Flag, "registered but zero generated must not set flag");
        Execute(f, Injury); Check(f.Injured.Count == 0, "unspawned members receive no effects");
        f = Start(2); f.ReturnNull.Add("mate1"); Execute(f, Generation); Execute(f, Injury);
        Check(f.Generated.SequenceEqual(new[] { 6 }) && f.Flag && f.Injured.SequenceEqual(new[] { 6 }), "partial generation settles successful participant only");
        f = Start(1); f.ThrowOnSpawn = true;
        try { Execute(f, Generation); } catch (InvalidOperationException) { }
        Check(!TeamBattleSafety.HasSpawnScope && !f.Flag, "native exception cleans scope and cannot set flag");
        f = Start(1); f.ThrowAfterInit = true;
        try { Execute(f, Generation); } catch (InvalidOperationException) { }
        Check(!TeamBattleSafety.HasSpawnScope && !TeamBattleSafety.Spawned && TeamContainerInject.Registered.Count == 0,
            "initialized candidate cannot register after failed native command");
        f = Start(1);
        f.DuringSpawn = () => BattleFunction.AddBattleUnit(new WorldUnitBase { Id = "mate1" }, true);
        Execute(f, Generation);
        Check(f.Flag && TeamContainerInject.Registered.Count == 1 && TeamContainerInject.Registered[0].Pointer == f.LastCreated.Pointer,
            "same ID with foreign world data cannot steal registration");
        f = Start(1);
        f.DuringSpawn = () => f.AddBattleUnit(new Il2CppStringArray("addBattleUnit_5_-1_10_2_1_2_0".Split('_')));
        Execute(f, Generation);
        Check(f.Flag && !TeamBattleSafety.HasSpawnScope && TeamContainerInject.Registered.Count == 1,
            "nested foreign command is isolated and outer owned scope restored");
        f = Start(2);
        f.DuringSpawn = () => f.AddBattleUnit(new Il2CppStringArray(("addBattleUnit_4_-1_10_2_1_2_0_@Team:" + TeamBattleSafety.SelectionCondition).Split('_')));
        Execute(f, Generation);
        Check(f.Flag && !TeamBattleSafety.HasSpawnScope && TeamContainerInject.Registered.Count == 2
            && TeamBattleSafety.Participated("mate1") && TeamBattleSafety.Participated("mate2"),
            "nested owned commands retain distinct successful teammate registries");
        f = Start(1);
        f.DuringSpawn = () => {
            try { f.ThrowOnSpawn = true; f.AddBattleUnit(new Il2CppStringArray("addBattleUnit_5_-1_10_2_1_2_0".Split('_'))); }
            catch (InvalidOperationException) { }
            finally { f.ThrowOnSpawn = false; }
        };
        Execute(f, Generation);
        Check(f.Flag && !TeamBattleSafety.HasSpawnScope && TeamContainerInject.Registered.Count == 1,
            "caught nested failure restores outer scope without granting inner permission");
        f = Start(1); f.Deferred = true; Execute(f, Generation);
        Check(!TeamBattleSafety.Admitted && !f.Flag, "deferred queue has no premature injection");
        f.RunPending(); Check(f.Generated.SequenceEqual(new[] { 5 }) && f.Flag, "markers survive queue execution after interpreter returns");
        f = Start(1); f.AddBattleNPCData(new Il2CppStringArray(Select.Split('_')));
        BattleFunction.AddBattleUnit(TeamTool.Mates[0], true);
        Check(!TeamBattleSafety.Spawned, "unrelated generation outside owned spawn does not grant permission");
        f = Start(0); Execute(f, "addBattleNPCData_other_0|addBattleUnit_3_-1_10_2_1_2_0");
        Check(f.Generated.SequenceEqual(new[] { 3 }) && !TeamBattleSafety.Spawned, "foreign original generation remains untouched");
        TeamBattleSafety.AttachHurtGuards();
        WorldStateFacts.Ready = false; FrameTicker.Callback();
        Check(FrameTicker.Callback != null, "world not ready keeps deferred callback");
        var injector = new Harmony("TeamBattleInstallFailureTests");
        injector.Patch(AccessTools.Method(typeof(TeamBattleSafety), "InstallHurtGuards"),
            prefix: new HarmonyMethod(AccessTools.Method(typeof(Program), "InstallFailure")));
        WorldStateFacts.Ready = true; AdvanceRetry();
        Check(installCalls == 1 && FrameTicker.Callback != null, "first failure keeps callback");
        FrameTicker.Callback(); Check(installCalls == 1, "no retry every frame");
        AdvanceRetry(); Check(installCalls == 2 && FrameTicker.Callback != null, "second failure remains retryable");
        AdvanceRetry(); Check(installCalls == 3 && FrameTicker.Callback == null, "third success installs and stops callback");
        DramaTool.Opened = 0;
        Check(!DramaTool.OpenDrama(ModIds.DramaTeamHurtBase, new DramaData()) && DramaTool.Opened == 0, "empty injury page has no native side effects");
        TeamContainerInject.HurtPending = true;
        Check(DramaTool.OpenDrama(ModIds.DramaTeamHurtBase.ToString(), new DramaData()) && DramaTool.Opened == 1, "real injury still opens native page");
        TeamBattleSafety.DetachHurtGuards();
        Check(DramaTool.OpenDrama(ModIds.DramaTeamHurtBase, new DramaData()), "destroy removes injury hook");
        fatal = true; installCalls = 0; TeamBattleSafety.AttachHurtGuards(); AdvanceRetry();
        Check(installCalls == 1 && FrameTicker.Callback == null, "type initialization failure terminates useless retries");
        TeamBattleSafety.DetachHurtGuards();
        injector.UnpatchSelf(); harmony.UnpatchSelf();
        Console.WriteLine(count + " production wiring checks passed with real Harmony");
    }
}
