param(
    [string]$Path = 'E:\SteamLibrary\steamapps\common\鬼谷八荒\MelonLoader\Managed\Assembly-CSharp.dll',
    [string]$NeedlesCsv = '',
    [int]$MaxHits = 6,
    [int]$ContextChars = 140
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$Needles = @($NeedlesCsv -split ',') | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' }
if ($Needles.Count -eq 0) {
    Write-Output 'No needles given.'
    exit
}

$bytes = [System.IO.File]::ReadAllBytes($Path)
Write-Output ("File: {0}  ({1:N0} bytes)" -f $Path, $bytes.Length)
$utf16 = [System.Text.Encoding]::Unicode.GetString($bytes)
$utf8 = [System.Text.Encoding]::UTF8.GetString($bytes)

function Show-Matches([string]$hay, [string]$needle, [string]$label, [int]$maxHits, [int]$ctx) {
    $hits = 0
    $pos = 0
    Write-Output ("----- {0}  [{1}] -----" -f $needle, $label)
    while ($hits -lt $maxHits) {
        $i = $hay.IndexOf($needle, $pos)
        if ($i -lt 0) { break }
        $hits++
        $start = [Math]::Max(0, $i - $ctx)
        $len = [Math]::Min($hay.Length - $start, $ctx + $needle.Length + $ctx)
        $frag = $hay.Substring($start, $len)
        $frag = $frag -replace '[\x00-\x08\x0B\x0C\x0E-\x1F]', '.'
        Write-Output ("  #$hits @char=$i")
        Write-Output ("    ...{0}..." -f $frag)
        $pos = $i + $needle.Length
    }
    if ($hits -eq 0) { Write-Output "  (no match)" }
}

foreach ($n in $Needles) {
    Show-Matches $utf16 $n 'UTF-16LE' $MaxHits $ContextChars
    Show-Matches $utf8  $n 'UTF-8'   $MaxHits $ContextChars
}
