[CmdletBinding()]
param(
    [string]$RepoRoot = "."
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $RepoRoot)) {
    throw "RepoRoot 不存在：$RepoRoot"
}

$root = (Resolve-Path -LiteralPath $RepoRoot).Path
$files = @(
    @{ Name = "global.css"; Path = Join-Path $root "frontend\src\styles\global.css" },
    @{ Name = "limeng.css"; Path = Join-Path $root "frontend\src\shell\limeng.css" },
    @{ Name = "ui.css"; Path = Join-Path $root "frontend\src\components\ui\ui.css" }
)

$oldColors = @(
    "#8b84e8", "#7b74db", "#bdb8f2", "#afa9ec", "#3c3489",
    "#2dd4cf", "#25c5c0", "#8ce9e5", "#7ff5f1", "#00cec9", "#006b68",
    "rgba(139,132,232,", "rgba(139, 132, 232,",
    "rgba(45,212,207,", "rgba(45, 212, 207,"
)

$smallFontPattern = "font-size:\s*(8(?:\.5)?|9(?:\.5)?|10(?:\.5)?)px"
$results = @()

foreach ($file in $files) {
    if (-not (Test-Path -LiteralPath $file.Path)) {
        $results += [pscustomobject]@{
            File        = $file.Name
            Status      = "MISSING"
            OldColors   = 0
            SmallFonts  = 0
            Notes       = "文件不存在"
        }
        continue
    }

    $text = [System.IO.File]::ReadAllText($file.Path, [System.Text.Encoding]::UTF8)

    $oldColorHits = 0
    $oldColorLines = @()
    foreach ($color in $oldColors) {
        $matches = [regex]::Matches($text, [regex]::Escape($color), [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
        if ($matches.Count -gt 0) {
            $oldColorHits += $matches.Count
            foreach ($m in $matches | Select-Object -First 3) {
                $line = ($text.Substring(0, $m.Index) -split "`n").Count
                $oldColorLines += "$color@L$line"
            }
        }
    }

    $smallMatches = [regex]::Matches($text, $smallFontPattern)
    $smallFontHits = $smallMatches.Count
    $smallFontLines = @()
    foreach ($m in $smallMatches | Select-Object -First 3) {
        $line = ($text.Substring(0, $m.Index) -split "`n").Count
        $smallFontLines += "L${line}:$($m.Value)"
    }

    $status = "PASS"
    if ($oldColorHits -gt 0 -or $smallFontHits -gt 0) {
        $status = "FAIL"
    }

    $results += [pscustomobject]@{
        File       = $file.Name
        Status     = $status
        OldColors  = $oldColorHits
        SmallFonts = $smallFontHits
        Notes      = (($oldColorLines + $smallFontLines) -join "; ")
    }
}

$results | Format-Table -AutoSize -Wrap

# ---------- 关键规则存在性 ----------
$globalPath = Join-Path $root "frontend\src\styles\global.css"
$limengPath = Join-Path $root "frontend\src\shell\limeng.css"
$uiPath = Join-Path $root "frontend\src\components\ui\ui.css"

$checks = @()

if (Test-Path -LiteralPath $globalPath) {
    $g = [System.IO.File]::ReadAllText($globalPath, [System.Text.Encoding]::UTF8)
    $checks += [pscustomobject]@{
        Check  = "global.css 有 --fs-subtitle"
        Status = if ($g -match "--fs-subtitle\s*:") { "PASS" } else { "FAIL" }
        Detail = ""
    }
    $checks += [pscustomobject]@{
        Check  = "global.css 有新线上主色 #5f6f96"
        Status = if ($g -match "#5f6f96") { "PASS" } else { "FAIL" }
        Detail = ""
    }
    $checks += [pscustomobject]@{
        Check  = "global.css 有新线下主色 #5f8f84"
        Status = if ($g -match "#5f8f84") { "PASS" } else { "FAIL" }
        Detail = ""
    }
}

if (Test-Path -LiteralPath $limengPath) {
    $l = [System.IO.File]::ReadAllText($limengPath, [System.Text.Encoding]::UTF8)

    $checks += [pscustomobject]@{
        Check  = "limeng.css 有 .web-view-guest"
        Status = if ($l -match "\.web-view-guest") { "PASS" } else { "FAIL" }
        Detail = ""
    }
    $checks += [pscustomobject]@{
        Check  = "limeng.css 有 data-contrast=high"
        Status = if ($l -match 'data-contrast="high"') { "PASS" } else { "FAIL" }
        Detail = ""
    }
    $checks += [pscustomobject]@{
        Check  = "limeng.css 有 .recognition-page.web-fill"
        Status = if ($l -match "\.recognition-page\.web-fill") { "PASS" } else { "FAIL" }
        Detail = ""
    }
    $checks += [pscustomobject]@{
        Check  = "limeng.css 保留 --purple: var(--accent)"
        Status = if ($l -match "--purple\s*:\s*var\(--accent\)") { "PASS" } else { "FAIL" }
        Detail = ""
    }
    $checks += [pscustomobject]@{
        Check  = "limeng.css 保留 --cyan: var(--accent-offline)"
        Status = if ($l -match "--cyan\s*:\s*var\(--accent-offline\)") { "PASS" } else { "FAIL" }
        Detail = ""
    }

    $rootMatch = [regex]::Match($l, "(?s):root\s*\{(.*?)\}")
    if ($rootMatch.Success) {
        $rootBody = $rootMatch.Groups[1].Value
        $hardcoded = [regex]::Matches($rootBody, "#[0-9a-fA-F]{3,8}|rgba?\(")
        $checks += [pscustomobject]@{
            Check  = "limeng.css :root 无硬编码 hex/rgba"
            Status = if ($hardcoded.Count -eq 0) { "PASS" } else { "FAIL" }
            Detail = if ($hardcoded.Count -eq 0) { "" } else { "发现 $($hardcoded.Count) 处，检查 :root 别名桥" }
        }
    }
}

if (Test-Path -LiteralPath $uiPath) {
    $u = [System.IO.File]::ReadAllText($uiPath, [System.Text.Encoding]::UTF8)
    $checks += [pscustomobject]@{
        Check  = "ui.css 有 TINGKE_VISUAL_REFACTOR_V1_UI"
        Status = if ($u -match "TINGKE_VISUAL_REFACTOR_V1_UI") { "PASS" } else { "FAIL" }
        Detail = ""
    }
}

$checks | Format-Table -AutoSize -Wrap

$failed = ($results | Where-Object { $_.Status -eq "FAIL" }).Count +
          ($checks | Where-Object { $_.Status -eq "FAIL" }).Count

if ($failed -gt 0) {
    Write-Host ""
    Write-Host "有 $failed 项未通过；先修 FAIL，再跑 npm test。" -ForegroundColor Red
    exit 1
} else {
    Write-Host ""
    Write-Host "全部检查通过。下一步：cd frontend && npm test" -ForegroundColor Green
}
