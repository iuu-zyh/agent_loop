"""变异生产代码副本，证明队友身份、队列接线和挂载重试断言会拦住回退。"""
from pathlib import Path
import argparse
import shutil
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[2]
INPUTS = ["csharp/tools/TeamBattleAdmissionCore.cs", "csharp/tools/TeamBattleCommandCore.cs",
          "csharp/tools/TeamHurtInstallCore.cs", "csharp/tools/TeamBattleSafety.cs",
          "csharp/tests/TeamBattleAdmissionCoreTests/Program.cs",
          "csharp/tests/TeamBattleAdmissionCoreTests/TeamBattleAdmissionCoreTests.csproj",
          "tests/fixtures/team_battle_wiring/Program.cs", "scripts/dev/check_team_battle_wiring.ps1"]
MUTATIONS = [
    ("registered-is-spawned", "csharp/tools/TeamBattleAdmissionCore.cs", "HasTeam(battle) && _units.Count > 0", "HasTeam(battle)", "core"),
    ("unspawned-can-hurt", "csharp/tools/TeamBattleAdmissionCore.cs", "return Participated(id) && !player", "return Contains(id) && !player", "core"),
    ("foreign-spawn-taken", "csharp/tools/TeamBattleCommandCore.cs", "bool spawn = selection &&", "bool spawn = true &&", "core"),
    ("unbounded-retry", "csharp/tools/TeamHurtInstallCore.cs", "Installed || Fatal || Attempts >= MaxAttempts", "Installed || Fatal", "core"),
    ("retry-every-frame", "csharp/tools/TeamHurtInstallCore.cs", "!ready || Stopped || now < _nextAttempt", "!ready || Stopped", "core"),
    ("unrelated-unit-grants-permission", "csharp/tools/TeamBattleSafety.cs", "unit == null || id != _spawningId ||", "unit == null ||", "wiring"),
    ("flag-before-spawn", "csharp/tools/TeamBattleSafety.cs", "return TeamBattleSafety.Spawned ||", "return TeamBattleSafety.Admitted ||", "wiring"),
    ("scope-leaks-on-exception", "csharp/tools/TeamBattleSafety.cs", "TeamBattleSafety.SpawningId = __state;", "TeamBattleSafety.SpawningId = TeamBattleSafety.SpawningId;", "wiring"),
    ("stop-after-first-install-failure", "csharp/tools/TeamBattleSafety.cs", "if (HurtInstall.Stopped) FrameTicker.Stop", "if (true) FrameTicker.Stop", "wiring"),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--game-root", required=True)
    parser.add_argument("--work-dir", type=Path, default=ROOT.parent / ".team-battle-mutations")
    args = parser.parse_args()
    work = args.work_dir / uuid.uuid4().hex[:8]
    work.mkdir(parents=True)
    for name, file, old, new, mode in MUTATIONS:
        copy = work / name
        for relative in INPUTS:
            target = copy / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        target = copy / file
        source = target.read_text(encoding="utf-8-sig")
        assert source.count(old) == 1, (name, "mutation location changed")
        target.write_text(source.replace(old, new), encoding="utf-8")
        if mode == "core":
            command = ["dotnet", "run", "--project", str(copy / "csharp/tests/TeamBattleAdmissionCoreTests"), "-c", "Release"]
        else:
            command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                       str(copy / "scripts/dev/check_team_battle_wiring.ps1"), "-GameRoot", args.game_root,
                       "-OutputDirectory", str(copy / "output")]
        result = subprocess.run(command, cwd=copy, capture_output=True, encoding="utf-8", errors="replace", timeout=90)
        output = result.stdout + result.stderr
        (copy / "result.log").write_text(output, encoding="utf-8")
        assert result.returncode != 0 and "error CS" not in output, (name, "mutant survived or failed to compile", output[-1000:])
        print("caught: " + name, flush=True)
    print(str(len(MUTATIONS)) + " mutation checks passed; " + str(work))


if __name__ == "__main__":
    main()
