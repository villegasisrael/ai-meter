param(
    [switch]$IncludeSensorProbe
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $root 'src\ai_meter\bin\win-x64'
New-Item -ItemType Directory -Force $out | Out-Null

$amdSdk = if ($env:AMDRMMONITORSDKPATH) {
    $env:AMDRMMONITORSDKPATH
} else {
    'C:\Program Files\AMD\RyzenMasterMonitoringSDK'
}
$vswhere = 'C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe'
$msbuild = $null
if (Test-Path -LiteralPath $vswhere) {
    $vsRoot = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if ($vsRoot) {
        $candidate = Join-Path $vsRoot 'MSBuild\Current\Bin\MSBuild.exe'
        if (Test-Path -LiteralPath $candidate) {
            $msbuild = $candidate
        }
    }
}
# Kill any running sensor-probe instances so the dll isn't locked during copy
Stop-Process -Name 'ai-meter-sensor-probe' -Force -ErrorAction SilentlyContinue

if ($IncludeSensorProbe) {
    Write-Host 'Building legacy LibreHardwareMonitor sensor probe...'
    dotnet publish (Join-Path $root 'native\AiMeter.SensorProbe\AiMeter.SensorProbe.csproj') -c Release -r win-x64 --self-contained false -o $out
} else {
    Write-Host 'Skipping legacy LibreHardwareMonitor sensor probe.'
}

$gpp = Get-Command g++ -ErrorAction SilentlyContinue
if ($gpp) {
    Write-Host 'Building C++ Windows metrics probe...'
    try {
        & $gpp.Source -std=c++17 -O2 (Join-Path $root 'native\winprobe\ai_meter_winprobe.cpp') -o (Join-Path $out 'ai-meter-winprobe.exe') -liphlpapi
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "C++ winprobe build exited with code $LASTEXITCODE"
        }
    } catch {
        Write-Warning "C++ winprobe build failed: $($_.Exception.Message)"
    }
} else {
    Write-Warning 'g++ not found; C++ winprobe was not rebuilt.'
}

if ($msbuild -and (Test-Path -LiteralPath (Join-Path $amdSdk 'include\ICPUEx.h'))) {
    Write-Host 'Building AMD Ryzen Master Monitoring SDK probe...'
    # Some orchestrated shells contain both Path and PATH. MSBuild treats those
    # as duplicate keys when it launches cl.exe, so normalize the process block.
    $cleanPath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($userPath) {
        $cleanPath = "$cleanPath;$userPath"
    }
    [Environment]::SetEnvironmentVariable('PATH', $null, 'Process')
    [Environment]::SetEnvironmentVariable('Path', $null, 'Process')
    [Environment]::SetEnvironmentVariable('Path', $cleanPath, 'Process')
    & $msbuild (Join-Path $root 'native\amdprobe\ai_meter_amd_probe.vcxproj') `
        /p:Configuration=Release /p:Platform=x64 "/p:AmdSdkRoot=$amdSdk" /nologo /verbosity:minimal
    if ($LASTEXITCODE -ne 0) {
        throw "AMD probe build exited with code $LASTEXITCODE"
    }
} elseif (-not (Test-Path -LiteralPath (Join-Path $amdSdk 'include\ICPUEx.h'))) {
    Write-Warning 'AMD Ryzen Master Monitoring SDK not found; AMD probe was not rebuilt.'
} else {
    Write-Warning 'Visual Studio 2022 C++ Build Tools not found; AMD probe was not rebuilt.'
}

# Unblock binaries so Windows security doesn't block subprocess execution
Get-ChildItem $out -Filter '*.exe' | ForEach-Object { Unblock-File $_.FullName }
Get-ChildItem $out -Filter '*.dll' | ForEach-Object { Unblock-File $_.FullName }

Write-Host "Native probes output: $out"
