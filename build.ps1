param(
    [string]$Python = "python"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VersionPath = Join-Path $RepoRoot "VERSION"

if (-not (Test-Path -LiteralPath $VersionPath)) {
    throw "VERSION file not found: $VersionPath"
}

$Version = (Get-Content -LiteralPath $VersionPath -Raw).Trim()
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "VERSION must use MAJOR.MINOR.PATCH, for example 0.1.0. Current value: $Version"
}

$BuildName = Get-Date -Format "yyyyMMdd_HHmmss"
$DistRoot = Join-Path $RepoRoot "dist"
$BuildDir = Join-Path $DistRoot $BuildName
$WorkDir = Join-Path $RepoRoot ".build\pyinstaller"
$SpecPath = Join-Path $RepoRoot "DevSTT.spec"
$ExePath = Join-Path $BuildDir "DevSTT_$Version.exe"

if (Test-Path -LiteralPath $BuildDir) {
    throw "Build directory already exists: $BuildDir"
}

$env:DEVSTT_VERSION = $Version
$env:DEVSTT_BUILD_NAME = $BuildName

try {
    & $Python -m PyInstaller --noconfirm --clean --distpath $DistRoot --workpath $WorkDir $SpecPath
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }

    New-Item -ItemType Directory -Force -Path (Join-Path $BuildDir "data\models") | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $BuildDir "data\settings") | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $BuildDir "data\logs") | Out-Null

    if (-not (Test-Path -LiteralPath $ExePath)) {
        throw "Expected exe was not created: $ExePath"
    }

    Write-Host "Built $ExePath"
    Write-Host "Runtime data directory: $(Join-Path $BuildDir 'data')"
}
finally {
    Remove-Item Env:\DEVSTT_VERSION -ErrorAction SilentlyContinue
    Remove-Item Env:\DEVSTT_BUILD_NAME -ErrorAction SilentlyContinue
}
