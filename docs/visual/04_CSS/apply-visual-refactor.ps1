[CmdletBinding()]
param(
    [string]$RepoRoot = ".",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $RepoRoot)) {
    throw "RepoRoot 不存在：$RepoRoot"
}

$root = (Resolve-Path -LiteralPath $RepoRoot).Path
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$scriptDir = $PSScriptRoot

$targets = @(
    @{
        Name   = "global.css"
        Path   = Join-Path $root "frontend\src\styles\global.css"
        Append = "global.tokens.css"
        Marker = "TINGKE_VISUAL_REFACTOR_V1_GLOBAL"
    },
    @{
        Name   = "limeng.css"
        Path   = Join-Path $root "frontend\src\shell\limeng.css"
        Append = "limeng.append.css"
        Marker = "TINGKE_VISUAL_REFACTOR_V1"
    },
    @{
        Name   = "ui.css"
        Path   = Join-Path $root "frontend\src\components\ui\ui.css"
        Append = "ui.overrides.css"
        Marker = "TINGKE_VISUAL_REFACTOR_V1_UI"
    }
)

$colorMap = @(
    @{ Old = "#8b84e8"; New = "#5F6F96" },
    @{ Old = "#7b74db"; New = "#52618A" },
    @{ Old = "#bdb8f2"; New = "#A6B2CC" },
    @{ Old = "#afa9ec"; New = "#A6B2CC" },
    @{ Old = "#3c3489"; New = "#4E5D80" },
    @{ Old = "rgba(139,132,232,"; New = "rgba(95,111,150," },
    @{ Old = "rgba(139, 132, 232,"; New = "rgba(95, 111, 150," },
    @{ Old = "#2dd4cf"; New = "#5F8F84" },
    @{ Old = "#25c5c0"; New = "#527D73" },
    @{ Old = "#8ce9e5"; New = "#A6C8C0" },
    @{ Old = "#7ff5f1"; New = "#A6C8C0" },
    @{ Old = "#00cec9"; New = "#5F8F84" },
    @{ Old = "#006b68"; New = "#4E776D" },
    @{ Old = "rgba(45,212,207,"; New = "rgba(95,143,132," },
    @{ Old = "rgba(45, 212, 207,"; New = "rgba(95, 143, 132," }
)

$fontMap = @{
    "8"    = "var(--fs-micro)"
    "8.5"  = "var(--fs-micro)"
    "9"    = "var(--fs-micro)"
    "9.5"  = "var(--fs-micro)"
    "10"   = "var(--fs-caption)"
    "10.5" = "var(--fs-caption)"
    "11"   = "var(--fs-caption)"
    "11.5" = "var(--fs-ui)"
    "12"   = "var(--fs-ui)"
    "12.5" = "var(--fs-ui)"
    "13"   = "var(--fs-body)"
    "13.5" = "var(--fs-body)"
    "14"   = "var(--fs-body)"
}

function Read-Utf8([string]$path) {
    return [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)
}

$results = @()

foreach ($target in $targets) {
    if (-not (Test-Path -LiteralPath $target.Path)) {
        $results += [pscustomobject]@{
            File      = $target.Name
            Status    = "MISSING"
            ColorHits = 0
            FontHits  = 0
            Appended  = $false
            Backup    = ""
        }
        continue
    }

    $text = Read-Utf8 $target.Path
    $colorHits = 0

    foreach ($pair in $colorMap) {
        $pattern = [regex]::Escape($pair.Old)
        $matches = [regex]::Matches($text, $pattern, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
        if ($matches.Count -gt 0) {
            $colorHits += $matches.Count
            $text = [regex]::Replace(
                $text,
                $pattern,
                $pair.New,
                [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
            )
        }
    }

    $fontHits = 0
    $fontPattern = "font-size:\s*([0-9]+(?:\.[0-9]+)?)px"
    foreach ($match in [regex]::Matches($text, $fontPattern)) {
        $value = $match.Groups[1].Value
        if ($fontMap.ContainsKey($value)) {
            $fontHits++
        }
    }

    $evaluator = [System.Text.RegularExpressions.MatchEvaluator]{
        param($m)
        $value = $m.Groups[1].Value
        if ($fontMap.ContainsKey($value)) {
            return "font-size: $($fontMap[$value])"
        }
        return $m.Value
    }
    $text = [regex]::Replace($text, $fontPattern, $evaluator)

    $appended = $false
    $appendPath = Join-Path $scriptDir $target.Append
    if (Test-Path -LiteralPath $appendPath) {
        if ($text -notmatch [regex]::Escape($target.Marker)) {
            $appendText = Read-Utf8 $appendPath
            $text = $text.TrimEnd() + "`r`n`r`n" + $appendText + "`r`n"
            $appended = $true
        }
    }

    $backup = ""
    if (-not $DryRun) {
        $backup = "$($target.Path).bak-$stamp"
        Copy-Item -LiteralPath $target.Path -Destination $backup -Force
        $utf8 = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($target.Path, $text, $utf8)
    }

    $results += [pscustomobject]@{
        File      = $target.Name
        Status    = if ($DryRun) { "DRY-RUN" } else { "WRITTEN" }
        ColorHits = $colorHits
        FontHits  = $fontHits
        Appended  = $appended
        Backup    = $backup
    }
}

$results | Format-Table -AutoSize

if ($DryRun) {
    Write-Host ""
    Write-Host "DryRun：没有写文件。去掉 -DryRun 再正式执行。" -ForegroundColor Yellow
} else {
    Write-Host ""
    Write-Host "完成。备份文件后缀：.bak-$stamp" -ForegroundColor Green
    Write-Host "下一步：运行 verify-visual-refactor.ps1，再 cd frontend && npm test" -ForegroundColor Green
}
