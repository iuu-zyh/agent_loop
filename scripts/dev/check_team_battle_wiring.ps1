param([Parameter(Mandatory=$true)][string]$GameRoot, [string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
if (!$OutputDirectory) { $OutputDirectory = Join-Path (Split-Path $repo -Parent) '.team-battle-wiring-tests' }
[void][IO.Directory]::CreateDirectory($OutputDirectory)
$sdk = @(& dotnet --list-sdks)
if ($sdk[-1] -notmatch '^(\S+) \[(.*)\]$') { throw 'Cannot locate .NET SDK compiler' }
$compiler = Join-Path $Matches[2] "$($Matches[1])\Roslyn\bincore\csc.dll"
$framework = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
$loader = Join-Path $GameRoot 'MelonLoader'
$refs = @('mscorlib.dll','System.dll','System.Core.dll') | ForEach-Object { '/reference:' + (Join-Path $framework $_) }
$refs += '/reference:' + (Join-Path $loader '0Harmony.dll')
$sources = @('csharp/tools/TeamBattleAdmissionCore.cs','csharp/tools/TeamBattleCommandCore.cs','csharp/tools/TeamHurtInstallCore.cs','csharp/tools/TeamBattleSafety.cs','tests/fixtures/team_battle_wiring/Program.cs') | ForEach-Object { Join-Path $repo $_ }
$exe = Join-Path $OutputDirectory 'TeamBattleWiringTests.exe'
& dotnet $compiler /nologo /noconfig /nostdlib+ /target:exe /optimize+ /codepage:65001 @refs "/out:$exe" @sources
if ($LASTEXITCODE -ne 0) { throw 'Wiring tests compilation failed' }
foreach ($file in @('0Harmony.dll','Mono.Cecil.dll','Mono.Cecil.Mdb.dll','Mono.Cecil.Pdb.dll','Mono.Cecil.Rocks.dll','MonoMod.RuntimeDetour.dll','MonoMod.Utils.dll')) {
    Copy-Item -LiteralPath (Join-Path $loader $file) -Destination $OutputDirectory
}
& $exe
if ($LASTEXITCODE -ne 0) { throw 'Wiring tests failed' }
