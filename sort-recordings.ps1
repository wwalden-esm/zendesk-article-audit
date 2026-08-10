$DaysBack = 7
$Source = "$env:USERPROFILE\OneDrive - ESM Solutions Corporation\Recordings"
$Dest = "$env:USERPROFILE\OneDrive - ESM Solutions Corporation\Implementation Customers - Documents"

$ClientMap = [ordered]@{
    "Monterey PC"          = "Monterey Peninsula College (MPC)"
    "Jackson State"        = "Jackson State University (JSU)"
    "Yavapai"              = "Yavapai College (YC)"
    "East Central"         = "East Central College (ECC)"
    "Indian River"         = "Indian River State College (IRSC)"
    "UNE "                 = "University of New England (UNE)"
    "University of New Haven" = "University of New Haven (UNH)"
    "New Haven"            = "University of New Haven (UNH)"
    "Citadel"              = "Citadel Military College (CMC)"
    "Conestoga"            = "Conestoga College (CC)"
    "Kean"                 = "Kean University (KU)"
    "Kentucky State"       = "Kentucky State University (KYSU)"
    "Kern "                = "Kern Community College District (KCCD)"
    "Redlands"             = "University of Redlands (UR)"
    "Weatherford"          = "Weatherford College (WC)"
}

Write-Host ""
Write-Host "=== Meeting Recording Sorter ===" -ForegroundColor Cyan
Write-Host "Source:    $Source"
Write-Host "Dest:      $Dest"
Write-Host "Lookback:  $DaysBack days"
Write-Host ""

if (-not (Test-Path $Source)) {
    Write-Host "ERROR: Source folder not found." -ForegroundColor Red
    exit 1
}

$cutoff = (Get-Date).AddDays(-$DaysBack)
$recordings = Get-ChildItem $Source -Filter "*.mp4" -File |
    Where-Object { $_.LastWriteTime -ge $cutoff } |
    Sort-Object LastWriteTime -Descending

if ($recordings.Count -eq 0) {
    Write-Host "No recordings found in the last $DaysBack days." -ForegroundColor Yellow
    exit 0
}

Write-Host "Found $($recordings.Count) recording(s) from the last $DaysBack days:" -ForegroundColor White
foreach ($r in $recordings) {
    Write-Host "  - $($r.Name)" -ForegroundColor DarkGray
}
Write-Host ""

$copied = 0
$skipped = 0
$skippedExist = 0

foreach ($rec in $recordings) {
    $matched = $false

    foreach ($key in $ClientMap.Keys) {
        if ($rec.Name -like "*$key*") {
            $clientFolder = $ClientMap[$key]
            $destFolder = Join-Path (Join-Path $Dest $clientFolder) "Recordings"

            if (-not (Test-Path $destFolder)) {
                New-Item -ItemType Directory -Path $destFolder -Force | Out-Null
                Write-Host "  Created folder: $clientFolder\Recordings" -ForegroundColor DarkYellow
            }

            $destFile = Join-Path $destFolder $rec.Name
            if (Test-Path $destFile) {
                Write-Host "ALREADY EXISTS: $($rec.Name)" -ForegroundColor Yellow
                $skippedExist++
            } else {
                Copy-Item $rec.FullName $destFile
                Write-Host "COPIED: $($rec.Name)" -ForegroundColor Green
                Write-Host "    -> $clientFolder\Recordings\" -ForegroundColor DarkGreen
                $copied++
            }

            $matched = $true
            break
        }
    }

    if (-not $matched) {
        Write-Host ""
        Write-Host "UNMATCHED: $($rec.Name)" -ForegroundColor Cyan
        Write-Host "Pick a destination folder:" -ForegroundColor White

        $folders = Get-ChildItem $Dest -Directory |
            Where-Object { $_.Name -ne "Z) INTERNAL" } |
            Select-Object -ExpandProperty Name

        for ($i = 0; $i -lt $folders.Count; $i++) {
            Write-Host "  [$($i + 1)] $($folders[$i])" -ForegroundColor Gray
        }
        Write-Host "  [S] Skip this file" -ForegroundColor DarkGray
        Write-Host ""

        $choice = Read-Host "Enter number or S"

        if ($choice -eq "S" -or $choice -eq "s") {
            Write-Host "  Skipped." -ForegroundColor DarkGray
            $skipped++
        } else {
            $idx = 0
            if ([int]::TryParse($choice, [ref]$idx) -and $idx -ge 1 -and $idx -le $folders.Count) {
                $clientFolder = $folders[$idx - 1]
                $destFolder = Join-Path (Join-Path $Dest $clientFolder) "Recordings"

                if (-not (Test-Path $destFolder)) {
                    New-Item -ItemType Directory -Path $destFolder -Force | Out-Null
                    Write-Host "  Created folder: $clientFolder\Recordings" -ForegroundColor DarkYellow
                }

                $destFile = Join-Path $destFolder $rec.Name
                if (Test-Path $destFile) {
                    Write-Host "  ALREADY EXISTS — skipped." -ForegroundColor Yellow
                    $skippedExist++
                } else {
                    Copy-Item $rec.FullName $destFile
                    Write-Host "  COPIED -> $clientFolder\Recordings\" -ForegroundColor Green
                    $copied++
                }
            } else {
                Write-Host "  Invalid choice — skipped." -ForegroundColor Red
                $skipped++
            }
        }
        Write-Host ""
    }
}

Write-Host ""
Write-Host "=== Done ===" -ForegroundColor Cyan
Write-Host "  Copied:          $copied"
Write-Host "  Already existed: $skippedExist"
Write-Host "  Skipped:         $skipped"
Write-Host ""
