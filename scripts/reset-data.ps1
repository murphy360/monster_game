param(
    [switch]$Force,
    [switch]$DeleteAllData,
    [switch]$SkipRestart,
    [switch]$NoBuild
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$dataDir = Join-Path $repoRoot 'data'
$levelsDir = Join-Path $dataDir 'levels'
$composeFile = Join-Path $repoRoot 'docker-compose.yml'
$envFile = Join-Path $repoRoot 'backend/.env'

if (-not (Test-Path -LiteralPath $composeFile)) {
    throw "Could not find docker-compose.yml at '$composeFile'. Run this script from the repo checkout."
}

$targetDir = if ($DeleteAllData) { $dataDir } else { $levelsDir }
$targetLabel = if ($DeleteAllData) { 'all backend data under data/' } else { 'saved level JSON files under data/levels/' }

if (-not (Test-Path -LiteralPath $targetDir)) {
    New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
}

if (-not $Force) {
    Write-Host "This will delete $targetLabel and stop the current Docker Compose stack." -ForegroundColor Yellow
    if (-not $SkipRestart) {
        Write-Host "It will then start the stack again from a clean state." -ForegroundColor Yellow
    }

    $confirmation = Read-Host "Type RESET to continue"
    if ($confirmation -cne 'RESET') {
        Write-Host 'Aborted.'
        exit 0
    }
}

Push-Location $repoRoot
try {
    Write-Host 'Stopping Docker Compose services...'
    & docker compose down
    if ($LASTEXITCODE -ne 0) {
        throw 'docker compose down failed.'
    }

    Write-Host "Deleting $targetLabel..."
    Get-ChildItem -LiteralPath $targetDir -Force -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction Stop

    if (-not (Test-Path -LiteralPath $targetDir)) {
        New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
    }

    if (-not $SkipRestart) {
        Write-Host 'Starting Docker Compose services...'

        $composeArgs = @('compose')
        if (Test-Path -LiteralPath $envFile) {
            $composeArgs += @('--env-file', 'backend/.env')
        }
        $composeArgs += @('up', '-d')
        if (-not $NoBuild) {
            $composeArgs += '--build'
        }

        & docker @composeArgs
        if ($LASTEXITCODE -ne 0) {
            throw 'docker compose up failed.'
        }
    }

    Write-Host 'Reset complete.' -ForegroundColor Green
}
finally {
    Pop-Location
}