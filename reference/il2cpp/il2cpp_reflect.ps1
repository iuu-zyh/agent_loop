param(
    [string]$Path = 'E:\SteamLibrary\steamapps\common\鬼谷八荒\MelonLoader\Dependencies\Il2CppAssemblyGenerator\Cpp2IL\cpp2il_out\Assembly-CSharp.dll',
    [string]$TypeFilter = '',
    [string]$MethodFilter = '',
    [string]$FieldFilter = '',
    [switch]$Members,
    [switch]$NoNested
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$dir = [System.IO.Path]::GetDirectoryName($Path)

# 1) Load every assembly in the dir first (so dependencies resolve from load context)
Get-ChildItem -Path $dir -Filter *.dll | ForEach-Object {
    try { [System.Reflection.Assembly]::LoadFrom($_.FullName) | Out-Null }
    catch { Write-Output ("preload failed: " + $_.Name + " -> " + $_.Exception.Message) }
}

try {
    $asm = [System.Reflection.Assembly]::LoadFrom($Path)
} catch {
    Write-Output ("LOAD FAILED: " + $_.Exception.Message)
    exit 1
}

$types = @()
try {
    $types = $asm.GetTypes()
} catch [System.Reflection.ReflectionTypeLoadException] {
    $types = $_.Exception.Types | Where-Object { $_ -ne $null }
    Write-Output ("GetTypes partial, loader errors: " + $_.Exception.LoaderExceptions.Count)
} catch {
    Write-Output ("GetTypes error: " + $_.Exception.Message)
    $types = @()
}

Write-Output ("Assembly loaded, total types: " + $types.Count)

if ($MethodFilter) {
    Write-Output ("Searching types declaring method matching: " + $MethodFilter)
    foreach ($t in $types) {
        if ($NoNested -and $t.FullName.Contains('+')) { continue }
        try {
            $ms = @($t.GetMethods([System.Reflection.BindingFlags]'Public,NonPublic,Instance,Static,DeclaredOnly'))
            foreach ($m in $ms) {
                if ($m.IsSpecialName) { continue }
                if ($m.Name -like $MethodFilter) {
                    Write-Output ("### TYPE: " + $t.FullName)
                    break
                }
            }
        } catch {}
    }
    exit 0
}

if ($FieldFilter) {
    Write-Output ("Searching types declaring field matching: " + $FieldFilter)
    foreach ($t in $types) {
        if ($NoNested -and $t.FullName.Contains('+')) { continue }
        try {
            $fs = @($t.GetFields([System.Reflection.BindingFlags]'Public,NonPublic,Instance,Static,DeclaredOnly'))
            foreach ($f in $fs) {
                if ($f.Name -like $FieldFilter) {
                    Write-Output ("### TYPE: " + $t.FullName)
                    break
                }
            }
        } catch {}
    }
    exit 0
}

$matched = @()
foreach ($t in $types) {
    if ($NoNested -and $t.FullName.Contains('+')) { continue }
    $fn = $null
    try { $fn = $t.FullName } catch { $fn = $t.Name }
    if ($fn -like $TypeFilter) { $matched += $t }
}
Write-Output ("Matched types: " + $matched.Count)
Write-Output "=================================================="

foreach ($t in $matched) {
    $fn = $null
    try { $fn = $t.FullName } catch { $fn = $t.Name }
    Write-Output ("### TYPE: " + $fn)
    try {
        $bt = $t.BaseType
        if ($bt) { Write-Output ("    BaseType: " + $bt.FullName) }
    } catch { Write-Output "    BaseType: <resolverror>" }
    try {
        $ifs = $t.GetInterfaces() | ForEach-Object { $_.FullName }
        if ($ifs.Count -gt 0) { Write-Output ("    Interfaces: " + ($ifs -join ', ')) }
    } catch { Write-Output "    Interfaces: <resolverror>" }

    if ($Members) {
        try {
            $fs = @($t.GetFields([System.Reflection.BindingFlags]'Public,NonPublic,Instance,Static,DeclaredOnly'))
            foreach ($f in $fs) {
                $ft = '<err>'
                try { $ft = $f.FieldType.Name } catch {}
                Write-Output ("    F: " + $ft + "  " + $f.Name)
            }
        } catch { Write-Output "    Fields: <resolverror>" }
        try {
            $ms = @($t.GetMethods([System.Reflection.BindingFlags]'Public,NonPublic,Instance,Static,DeclaredOnly'))
            foreach ($m in $ms) {
                if ($m.IsSpecialName) { continue }
                $ps = ''
                try {
                    $ps = ($m.GetParameters() | ForEach-Object {
                        $pt = '<err>'
                        try { $pt = $_.ParameterType.Name } catch {}
                        $pt + ' ' + $_.Name
                    }) -join ', '
                } catch {}
                $ret = '<err>'
                try { $ret = $m.ReturnType.Name } catch {}
                $vis = if ($m.IsPublic) {'pub'} elseif ($m.IsPrivate) {'pri'} elseif ($m.IsFamily) {'prot'} elseif ($m.IsAssembly) {'int'} else {'??'}
                $kind = if ($m.IsStatic) {'static'} else {'inst'}
                Write-Output ("    M: [{0}/{1}/{2}] {3}({4})" -f $vis, $kind, $ret, $m.Name, $ps)
            }
        } catch { Write-Output "    Methods: <resolverror>" }
    }
    Write-Output ""
}
