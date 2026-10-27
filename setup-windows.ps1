<#
.SYNOPSIS
  One-shot Windows setup for sf-badge-map: tools, Arnis, world generation,
  conversion, preview, and (optionally) copying to the badge.

.EXAMPLE
  .\setup-windows.ps1
  .\setup-windows.ps1 -Bbox "37.803,-122.432,37.808,-122.420" -Scale 1   # smaller test area
  .\setup-windows.ps1 -SkipGenerate                                        # reuse .\sf_world
  .\setup-windows.ps1 -Deploy                                              # also copy to the BADGER drive

Run from the repo root. Re-running is safe; finished steps are skipped.
#>
param(
    # Fort Mason west edge -> Pier 39 (min_lat,min_lng,max_lat,max_lng)
    [string]$Bbox = "37.800,-122.437,37.812,-122.408",
    [int]$Scale = 2,
    [string]$WorldDir = ".\sf_world",
    [string]$OutDir = ".\out",
    [switch]$SkipGenerate,
    [switch]$Deploy
)

$ErrorActionPreference = "Stop"
function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

# ---------------------------------------------------------------- Python
Step "Python"
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py -or -not ((& python --version 2>&1) -match "Python 3")) {
    Write-Host "Installing Python 3 via winget..."
    winget install --id Python.Python.3.12 -e --silent --accept-package-agreements --accept-source-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
}
& python --version
& python -m pip install --quiet --upgrade pip
& python -m pip install --quiet numpy pillow mpremote
Write-Host "numpy, pillow, mpremote ready"

# ---------------------------------------------------------------- Arnis
Step "Arnis"
$toolsDir = Join-Path $PSScriptRoot "tools"
$arnis = Join-Path $toolsDir "arnis-windows.exe"
if (-not (Test-Path $arnis)) {
    # REST API, not GraphQL: higher rate limit and no auth needed
    $rel = Invoke-RestMethod "https://api.github.com/repos/louis-e/arnis/releases/latest" -Headers @{ "User-Agent" = "sf-badge-map" }
    $asset = $rel.assets | Where-Object { $_.name -eq "arnis-windows.exe" } | Select-Object -First 1
    if (-not $asset) { throw "arnis-windows.exe not found in release $($rel.tag_name)" }
    Write-Host "Downloading Arnis $($rel.tag_name)..."
    Invoke-WebRequest $asset.browser_download_url -OutFile $arnis
    Unblock-File $arnis
}
Write-Host "Arnis at $arnis"

# ---------------------------------------------------------------- World
Step "Generate world ($Bbox)"
if ($SkipGenerate -and (Test-Path (Join-Path $WorldDir "region"))) {
    Write-Host "Skipping, using existing $WorldDir"
} else {
    if (Test-Path $WorldDir) { Remove-Item $WorldDir -Recurse -Force }
    New-Item -ItemType Directory $WorldDir | Out-Null
    # Needs internet (OpenStreetMap download). A 2.5 km bbox takes a few minutes.
    & $arnis --bbox="$Bbox" --output-dir="$WorldDir"
    if ($LASTEXITCODE -ne 0) { throw "Arnis failed (exit $LASTEXITCODE)" }
}

# ---------------------------------------------------------------- Convert
Step "Convert for the badge (scale $Scale)"
& python (Join-Path $toolsDir "arnis_to_badge.py") $WorldDir -o $OutDir --scale $Scale
if ($LASTEXITCODE -ne 0) { throw "Converter failed" }

# ---------------------------------------------------------------- Preview
Step "Desktop preview frames"
$bin = Join-Path $OutDir "world.bin"
& python (Join-Path $toolsDir "preview.py") $bin -o (Join-Path $OutDir "frame_3d.png")
& python (Join-Path $toolsDir "preview.py") $bin --map -o (Join-Path $OutDir "frame_map.png")
Write-Host "Look at $OutDir\world.png (full map), frame_3d.png and frame_map.png"

# ---------------------------------------------------------------- Deploy
$appSrc = Join-Path $PSScriptRoot "badge\apps\sfmap"
Copy-Item $bin (Join-Path $appSrc "world.bin") -Force
Write-Host "world.bin placed in badge\apps\sfmap ($([math]::Round((Get-Item $bin).Length / 1MB, 2)) MB)"

if ($Deploy) {
    Step "Deploy to badge"
    $drive = Get-Volume -ErrorAction SilentlyContinue | Where-Object { $_.FileSystemLabel -eq "BADGER" } | Select-Object -First 1
    if (-not $drive) {
        Write-Warning "No BADGER drive found. Plug the badge in over USB-C and put it in mass-storage mode, then run again with -Deploy -SkipGenerate."
    } else {
        $dest = "$($drive.DriveLetter):\apps\sfmap"
        if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
        Copy-Item $appSrc $dest -Recurse
        Write-Host "Copied to $dest. Eject the BADGER drive; the badge reboots to the menu."
    }
} else {
    Write-Host "`nTo install: put the badge in USB mass-storage mode and copy badge\apps\sfmap to <BADGER>:\apps\sfmap (or re-run with -Deploy)."
}
