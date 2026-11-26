"""原版多人战斗复发：空队也生成3/4/5，DeadTeamIds=0仍开四月重伤页。"""
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def body(text, marker):
    start = text.index("{", text.index(marker))
    depth = 1
    end = start + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]

def source(name):
    return (ROOT / "csharp" / name).read_text(encoding="utf-8-sig")

def test_owned_commands_keep_the_native_queue_and_context():
    s = source("tools/TeamBattleSafety.cs")
    b = body(s, "class TeamBattleSequenceSafety")
    assert "nameof(BattleFunction.OptionsFunction)" in s
    assert "Prefix(ref string __0)" in b
    assert "__0 = TeamBattleCommandCore.Rewrite(" in b
    assert "return false;" not in b and "_data =" not in b
    spawn = body(s, "class TeamBattleSpawnCallSafety")
    assert "TeamBattleCommandCore.IsMarked" in spawn and "TryMemberSlot(args[1], false" in spawn
    assert "new Il2CppStringArray(TeamBattleCommandCore.Remap(args, slot))" in spawn
    assert "TeamBattleSafety.SuspendSpawn()" in spawn
    assert "TeamBattleSafety.EndSpawn(__state, __exception == null);" in body(spawn, "Exception Finalizer")

def test_injection_keeps_original_npcs_and_never_runs_native_selection_on_failure():
    s = source("tools/TeamContainerInject.cs")
    b = body(s, "bool InjectInto")
    assert "nu.Clear()" not in b
    assert "int start = nu.Count;" in b
    assert "TeamBattleSafety.Register(data, start);" in b
    assert "return true;" not in b
    prefix = body(s, "bool Prefix(UnhollowerBaseLib.Il2CppStringArray")
    assert "if (!TeamBattleSafety.OwnSelection(__0)) return true;" in prefix
    assert ".Clear()" not in prefix

def test_hurt_page_guard_precedes_ui_and_fortune_side_effects():
    s = source("tools/TeamBattleSafety.cs")
    for marker in ("class TeamHurtDramaIntSafety", "class TeamHurtDramaStringSafety"):
        b = body(s, marker)
        assert "TeamBattleSafety.AllowHurtPage()" in b and "__result = false;" in b
    inject = source("tools/TeamContainerInject.cs")
    assert "PickHurtMate" not in body(s,"bool AllowHurtPage")
    assert "TeamBattleSafety.CanHurt" in body(inject,"WorldUnitBase PickHurtMate")
    assert "data.functionData.unitLeft = target;" in body(inject,"void DramaInitDataPrefix")

def test_spawn_identity_avoids_native_bool_bridge_and_commits_after_success():
    s = source("tools/TeamBattleSafety.cs")
    b = body(s,"class TeamBattleSpawnIdentity")
    assert "TargetMethods" not in b
    assert "[HarmonyPatch(typeof(UnitCtrlHumanNPC), nameof(UnitCtrlHumanNPC.InitData), new[] { typeof(WorldUnitData), typeof(UnitType) })]" in s
    assert "TeamBattleSafety.ObserveSpawn(__0, __instance);" in b
    assert "world.Pointer.ToInt64(), TeamTool.UnitIdOf(world.unit), unit.Pointer.ToInt64()" in body(s, "void ObserveSpawn")
    finish = body(s, "void EndSpawn")
    assert finish.index("current.Scope.Complete(BattleKey, success)") < finish.index("RegisterBattleUnit(current.Unit)")
    assert "finally" in finish and "previous.Scope.IsCurrent(BattleKey)" in finish
    inject = source("tools/TeamContainerInject.cs")
    resolve = body(inject,"bool TryResolveMate")
    assert resolve.index("TeamBattleSafety.ResolveUnit") < resolve.index('Refl.Get(unit, "data")')
    assert "mn == dn" not in body(inject,"void AddUnitPostfix")

def test_reload_does_not_reuse_team_permission_or_dead_units():
    inject = source("tools/TeamContainerInject.cs")
    start = body(inject,"void OnBattleStarting")
    assert "TeamBattleSafety.Reset();" in start and "DeadTeamIds.Clear();" in start
    assert "_mateBattleUnits.Clear();" in start
    assert "TeamBattleSafety.Reset();" in source("app/ModMain.cs")
    assert "TeamBattleSafety.Reset();" in source("tools/TeamBattleFollowup.cs")


def test_followup_does_not_return_unspawned_or_original_npcs_as_teammates():
    s = source("tools/TeamBattleFollowup.cs")
    front = body(s, "void OnBattleEndFront")
    assert "if (!TeamBattleSafety.Participated(UnitIdOf(u))) continue;" in front
    assert "if (!TeamBattleSafety.Participated(mid)) continue;" in front
    assert 'if (put > 0) DramaLedger.BeginWindow("")' in body(source("tools/TeamContainerInject.cs"), "bool InjectInto")


def test_drama_tool_cannot_be_initialized_by_startup_patchall():
    s = source("tools/TeamBattleSafety.cs")
    assert "[HarmonyPatch(typeof(DramaTool)" not in s
    attach = body(s, "void AttachHurtGuards")
    assert "FrameTicker.Ensure(FrameTicker.KTeamHurtInstall" in attach
    pump = body(s, "void TryInstallHurtGuards")
    assert "bool ready = TeamBattleAdmissionCore.CanInstallHurtGuards" in pump
    assert pump.index("CanInstallHurtGuards") < pump.index("InstallHurtGuards();")
    assert "playerUnit?.data?.unitData != null" in pump and "WorldStateFacts.MainWorldReady()" in pump
    assert "typeof(DramaTool)" not in pump
    assert "if (!HurtInstall.TryBegin(ready, now)) return;" in pump
    assert "if (HurtInstall.Stopped) FrameTicker.Stop" in pump
    assert "finally { FrameTicker.Stop" not in pump
    assert "current is TypeInitializationException" in pump
    assert "MethodImplOptions.NoInlining" in s
    install = body(s, "void InstallHurtGuards")
    assert "typeof(TeamHurtDramaIntSafety)" in install and "typeof(TeamHurtDramaStringSafety)" in install
    assert "harmony.UnpatchSelf(); throw;" in install
    main = source("app/ModMain.cs")
    assert "TeamBattleSafety.AttachHurtGuards();" in main
    assert "TeamBattleSafety.DetachHurtGuards();" in body(main, "void Destroy")


def test_in_battle_flag_and_effects_require_actual_generated_units():
    s = source("tools/TeamBattleSafety.cs")
    assert "TeamBattleSafety.Spawned" in body(s, "class TeamBattleFlagSafety")
    assert "TeamBattleSafety.Spawned" in body(s, "class TeamBattleTriggerSafety")
    assert "TryMemberSlot(a[1], true" in body(s, "class TeamBattleEffectSafety")
    assert "Core.HasSpawned(BattleKey)" in s
    core = source("tools/TeamBattleAdmissionCore.cs")
    assert "Participated(id)" in body(core, "bool CanHurt")
