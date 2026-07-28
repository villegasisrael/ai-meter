param(
  [string]$Python = '.\.venv\Scripts\python.exe'
)

$ErrorActionPreference = 'Stop'

& "$PSScriptRoot\build_native.ps1"

& $Python -m pip install pyinstaller
$nativeBin = Resolve-Path ".\src\ai_meter\bin\win-x64"
$winprobe = Join-Path $nativeBin 'ai-meter-winprobe.exe'
$amdProbe = Join-Path $nativeBin 'ai-meter-amd-probe.exe'
$pyInstallerArgs = @(
  '--onefile',
  '--name', 'ai-meter',
  '--collect-all', 'textual',
  '--collect-all', 'rich',
  '--add-data', "$winprobe;ai_meter/bin/win-x64",
  '-m', 'ai_meter.main'
)
if (Test-Path -LiteralPath $amdProbe) {
  $pyInstallerArgs += @('--add-data', "$amdProbe;ai_meter/bin/win-x64")
}
& $Python -m PyInstaller @pyInstallerArgs

Write-Host 'EXE generado en .\dist\ai-meter.exe'
