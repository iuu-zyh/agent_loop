param(
    [string]$Path = 'F:\_il2cpp_ref\Assembly-CSharp.dll',
    [string]$Filter = '.',
    [switch]$NoNested
)

$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$dir = [System.IO.Path]::GetDirectoryName($Path)
Get-ChildItem -Path $dir -Filter *.dll | ForEach-Object {
    try { [System.Reflection.Assembly]::LoadFrom($_.FullName) | Out-Null } catch {}
}

$asm = [System.Reflection.Assembly]::LoadFrom($Path)
$types = @()
try { $types = $asm.GetTypes() }
catch [System.Reflection.ReflectionTypeLoadException] { $types = $_.Exception.Types | Where-Object { $_ -ne $null } }

$rx = [regex]$Filter

function TName($t) {
    if ($t -eq $null) { return '<null>' }
    try { return $t.FullName } catch { return '<err>' }
}

foreach ($t in $types) {
    $fn = TName $t
    if ($fn -eq '<err>' -or $fn -eq $null) { continue }
    if (-not $rx.IsMatch($fn)) { continue }
    if ($NoNested -and $fn.Contains('+')) { continue }

    Write-Output ("### TYPE: " + $fn)
    try { if ($t.BaseType) { Write-Output ("    base: " + (TName $t.BaseType)) } } catch {}

    try {
        foreach ($c in $t.GetConstructors([System.Reflection.BindingFlags]'Public,NonPublic,Instance')) {
            $ps = @()
            foreach ($p in $c.GetParameters()) { $ps += ((TName $p.ParameterType) + ' ' + $p.Name) }
            $vis = if ($c.IsPublic) {'pub'} elseif ($c.IsFamily) {'prot'} else {'pri'}
            Write-Output ("    CTOR [{0}] ({1})" -f $vis, ($ps -join ', '))
        }
    } catch { Write-Output "    CTOR <err>" }

    try {
        foreach ($p in $t.GetProperties([System.Reflection.BindingFlags]'Public,NonPublic,Instance,Static,DeclaredOnly')) {
            $acc = @()
            try { if ($p.CanRead) { $acc += 'get' } } catch {}
            try { if ($p.CanWrite) { $acc += 'set' } } catch {}
            Write-Output ("    P: " + (TName $p.PropertyType) + "  " + $p.Name + "  [" + ($acc -join '/') + "]")
        }
    } catch { Write-Output "    P <err>" }

    try {
        foreach ($f in $t.GetFields([System.Reflection.BindingFlags]'Public,NonPublic,Instance,Static,DeclaredOnly')) {
            Write-Output ("    F: " + (TName $f.FieldType) + "  " + $f.Name)
        }
    } catch { Write-Output "    F <err>" }

    try {
        foreach ($m in $t.GetMethods([System.Reflection.BindingFlags]'Public,NonPublic,Instance,Static,DeclaredOnly')) {
            if ($m.IsSpecialName) { continue }
            $ps = @()
            foreach ($p in $m.GetParameters()) { $ps += ((TName $p.ParameterType) + ' ' + $p.Name) }
            $vis = if ($m.IsPublic) {'pub'} elseif ($m.IsFamily) {'prot'} elseif ($m.IsAssembly) {'int'} else {'pri'}
            $kind = if ($m.IsStatic) {'static'} else {'inst'}
            Write-Output ("    M [{0}/{1}] {2} {3}({4})" -f $vis, $kind, (TName $m.ReturnType), $m.Name, ($ps -join ', '))
        }
    } catch { Write-Output "    M <err>" }

    Write-Output ""
}
