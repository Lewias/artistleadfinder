$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $projectRoot
$pythonRuntime = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonRuntime)) { throw 'Create the project .venv first (see README).' }
function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $Executable" }
}
$testTemporaryPath = Join-Path $projectRoot ('build/test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path (Join-Path $projectRoot 'build') -Force | Out-Null
Invoke-Checked $pythonRuntime @('-m', 'pytest', 'backend/tests', '-q', '-p', 'no:cacheprovider', '--basetemp', $testTemporaryPath)
Remove-Item -LiteralPath $testTemporaryPath -Recurse -Force -ErrorAction SilentlyContinue
Invoke-Checked $pythonRuntime @('-m', 'ruff', 'check', 'backend')
Invoke-Checked 'npm.cmd' @('run', 'typecheck')
Invoke-Checked 'npm.cmd' @('run', 'lint')
Invoke-Checked 'npm.cmd' @('test')
Invoke-Checked $pythonRuntime @('scripts/build-core.py')
# Updater artifacts are signed with the key from `npx tauri signer generate` kept outside the
# repository; without it the local build skips them (the release workflow always signs).
$signingKey = Join-Path $env:USERPROFILE '.tauri/artist-lead-finder.key'
if (Test-Path -LiteralPath $signingKey) {
    $env:TAURI_SIGNING_PRIVATE_KEY = Get-Content -LiteralPath $signingKey -Raw
    $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = Get-Content -LiteralPath ($signingKey + '.password') -Raw
    Invoke-Checked 'npm.cmd' @('run', 'desktop:build', '--', '--bundles', 'nsis')
} else {
    $noUpdater = Join-Path $projectRoot 'build/no-updater.conf.json'
    Set-Content -LiteralPath $noUpdater -Value '{"bundle":{"createUpdaterArtifacts":false}}' -Encoding ascii
    Invoke-Checked 'npm.cmd' @('run', 'desktop:build', '--', '--bundles', 'nsis', '--config', $noUpdater)
}
$desktopExecutable = Join-Path $projectRoot 'src-tauri/target/release/artist-lead-finder.exe'
Invoke-Checked $pythonRuntime @('scripts/smoke-scout.py', $desktopExecutable)
$portableDirectory = Join-Path $projectRoot 'artifacts/ArtistLeadFinder-Portable'
New-Item -ItemType Directory -Path $portableDirectory -Force | Out-Null
$portableDirectories = @($portableDirectory)
$coreExecutable = Join-Path $projectRoot 'src-tauri/binaries/artist-core-x86_64-pc-windows-msvc.exe'
$desktopHash = (Get-FileHash -LiteralPath $desktopExecutable -Algorithm SHA256).Hash
$coreHash = (Get-FileHash -LiteralPath $coreExecutable -Algorithm SHA256).Hash
foreach ($directory in $portableDirectories) {
    $portableDesktop = Join-Path $directory 'artist-lead-finder.exe'
    $portableCore = Join-Path $directory 'artist-core.exe'
    Copy-Item -LiteralPath $desktopExecutable -Destination $portableDesktop
    Copy-Item -LiteralPath $coreExecutable -Destination $portableCore
    if ((Get-FileHash -LiteralPath $portableDesktop -Algorithm SHA256).Hash -ne $desktopHash -or
        (Get-FileHash -LiteralPath $portableCore -Algorithm SHA256).Hash -ne $coreHash) {
        throw "Portable copy verification failed: $directory"
    }
}
$installer = Get-ChildItem -LiteralPath 'src-tauri/target/release/bundle/nsis' -Filter '*-setup.exe' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($null -eq $installer) { throw 'NSIS installer not found.' }
New-Item -ItemType Directory -Path 'artifacts' -Force | Out-Null
Copy-Item -LiteralPath $installer.FullName -Destination 'artifacts/ArtistLeadFinder-Setup.exe'
Get-FileHash -LiteralPath 'artifacts/ArtistLeadFinder-Setup.exe' -Algorithm SHA256 | Format-List
(Get-FileHash -LiteralPath 'artifacts/ArtistLeadFinder-Setup.exe' -Algorithm SHA256).Hash | Set-Content -LiteralPath 'artifacts/ArtistLeadFinder-Setup.sha256' -Encoding ascii
