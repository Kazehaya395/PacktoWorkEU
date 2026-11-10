param(
    [string]$PythonExe = '',
    [switch]$BundleOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root

function Get-Version {
    $pyproject = Join-Path $Root 'pyproject.toml'
    if (-not (Test-Path -LiteralPath $pyproject -PathType Leaf)) {
        throw "pyproject.toml not found: $pyproject"
    }
    $text = Get-Content -Raw -LiteralPath $pyproject -Encoding UTF8
    if ($text -match '(?m)^\s*version\s*=\s*"([^"]+)"') {
        return $Matches[1]
    }
    throw 'Unable to read version from pyproject.toml'
}

function Invoke-BuildPython {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $lines = @()
    $code = 1
    try {
        $lines = @(& $Python -I @args 2>&1 | ForEach-Object {
            $line = "$_"
            Write-Host $line
            $line
        })
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    $script:NativeOutput = $lines
    if ($code -ne 0) {
        $tail = ($lines | Select-Object -Last 25) -join "`n"
        throw "Build Python command failed (exit $code): $($args -join ' ')`n$tail"
    }
}

function Assert-AppClosed([string]$TargetExe) {
    $targetNorm = $TargetExe.Replace('/', '\').TrimEnd('\')
    $exeLeaf = Split-Path -Leaf $targetNorm
    $running = @(Get-CimInstance -ClassName Win32_Process -Filter "Name = '$exeLeaf'")
    foreach ($proc in $running) {
        if ($null -eq $proc) { continue }
        $procPath = $proc.ExecutablePath
        if ([string]::IsNullOrWhiteSpace($procPath)) {
            throw "A running $exeLeaf process has an unreadable ExecutablePath; close PacktoWorkEU and retry."
        }
        $procNorm = "$procPath".Replace('/', '\').TrimEnd('\')
        if ([string]::Equals($procNorm, $targetNorm, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Target application is currently running ($TargetExe). Close it and retry."
        }
    }
}

function Test-Payload([string]$ExeFile) {
    $logFile = $env:PACKTOWORKEU_SELF_CHECK_LOG
    if ($logFile -and (Test-Path -LiteralPath $logFile -PathType Leaf)) {
        Remove-Item -LiteralPath $logFile -Force -ErrorAction SilentlyContinue
    }
    $depCheck = Start-Process -FilePath $ExeFile -ArgumentList '--self-check' -Wait -PassThru
    if ($depCheck.ExitCode -ne 0) {
        $details = if ($logFile -and (Test-Path -LiteralPath $logFile -PathType Leaf)) { "`n" + (Get-Content -Raw -LiteralPath $logFile) } else { '' }
        throw "Packaged dependency self-check failed (exit $($depCheck.ExitCode)): $ExeFile$details"
    }
    $guiCheck = Start-Process -FilePath $ExeFile -ArgumentList '--self-check-gui' -Wait -PassThru
    if ($guiCheck.ExitCode -ne 0) {
        $details = if ($logFile -and (Test-Path -LiteralPath $logFile -PathType Leaf)) { "`n" + (Get-Content -Raw -LiteralPath $logFile) } else { '' }
        throw "Packaged GUI self-check failed (exit $($guiCheck.ExitCode)): $ExeFile$details"
    }
    if ($env:PACKTOWORKEU_VALIDATE_BROWSER -eq '1') {
        $browserCheck = Start-Process -FilePath $ExeFile -ArgumentList '--self-check-browser' -Wait -PassThru
        if ($browserCheck.ExitCode -ne 0) {
            $details = if ($logFile -and (Test-Path -LiteralPath $logFile -PathType Leaf)) { "`n" + (Get-Content -Raw -LiteralPath $logFile) } else { '' }
            throw "Selected browser validation failed (exit $($browserCheck.ExitCode)): $ExeFile$details"
        }
    }
    if ($logFile -and (Test-Path -LiteralPath $logFile -PathType Leaf)) {
        Get-Content -LiteralPath $logFile | ForEach-Object { Write-Host "  $_" }
    }
    $payloadDir = Split-Path -Parent $ExeFile
    Invoke-BuildPython (Join-Path $Root 'build_hooks\verify_payload.py') $payloadDir
}

function Write-Artifact(
    [string]$Version,
    [string]$BundleDir,
    [string]$StagedDir,
    [string]$InstallerPath,
    [string]$ReportPath,
    [bool]$IsBundleOnly
) {
    $bundleFiles = @(Get-ChildItem -LiteralPath $BundleDir -Recurse -File -Force)
    $bundleMeasure = $bundleFiles | Measure-Object -Property Length -Sum
    $bundleBytes = if ($null -ne $bundleMeasure -and $null -ne $bundleMeasure.Sum) { [int64]$bundleMeasure.Sum } else { [int64]0 }
    $bundleMiB = [math]::Round($bundleBytes / 1MB, 2)

    $stagedFiles = @(Get-ChildItem -LiteralPath $StagedDir -Recurse -File -Force)
    $stagedMeasure = $stagedFiles | Measure-Object -Property Length -Sum
    $stagedBytes = if ($null -ne $stagedMeasure -and $null -ne $stagedMeasure.Sum) { [int64]$stagedMeasure.Sum } else { [int64]0 }
    $stagedMiB = [math]::Round($stagedBytes / 1MB, 2)

    Write-Host ("Application folder: $BundleDir ($bundleMiB MiB unpacked, $($bundleFiles.Count) files)")
    Write-Host ("Staged installer payload: $stagedMiB MiB unpacked ($($stagedFiles.Count) files)")

    $report = [ordered]@{
        version               = $Version
        bundle_only           = $IsBundleOnly
        bundle_dir            = $BundleDir
        bundle_unpacked_bytes = $bundleBytes
        bundle_unpacked_mib   = $bundleMiB
        staged_unpacked_bytes = $stagedBytes
        staged_unpacked_mib   = $stagedMiB
        installer_built       = (-not $IsBundleOnly)
        installer_path        = $null
        installer_bytes       = $null
        installer_mib         = $null
        installer_sha256      = $null
    }

    if ($IsBundleOnly) {
        Write-Host 'No installer was built (-BundleOnly specified).' -ForegroundColor Yellow
    } else {
        $artifactAuditPath = Join-Path (Join-Path $Root '.build\windows') 'artifact-report.json'
        Invoke-BuildPython (Join-Path $Root 'build_hooks\verify_payload.py') '--artifact' $InstallerPath '--report' $artifactAuditPath
        $item = Get-Item -LiteralPath $InstallerPath
        $instBytes = [int64]$item.Length
        $instMiB = [math]::Round($instBytes / 1MB, 2)
        $hash = (Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash.ToLowerInvariant()
        Write-Host ("Installer artifact: $InstallerPath ($instMiB MiB compressed, $instBytes bytes)") -ForegroundColor Green
        Write-Host ("SHA256: $hash")
        $report.installer_path = $InstallerPath
        $report.installer_bytes = $instBytes
        $report.installer_mib = $instMiB
        $report.installer_sha256 = $hash
    }

    $json = $report | ConvertTo-Json -Depth 5
    [System.IO.File]::WriteAllText($ReportPath, $json, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "Build report written to $ReportPath"
}

function Find-Iscc {
    $candidates = New-Object System.Collections.Generic.List[string]
    $localPrograms = if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'Programs' } else { $null }
    foreach ($base in @(${env:ProgramFiles(x86)}, $env:ProgramFiles, $localPrograms)) {
        if (-not [string]::IsNullOrWhiteSpace($base)) {
            $candidates.Add((Join-Path $base 'Inno Setup 7\ISCC.exe'))
            $candidates.Add((Join-Path $base 'Inno Setup 6\ISCC.exe'))
        }
    }
    $cmd = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if (-not $cmd) { $cmd = Get-Command ISCC -ErrorAction SilentlyContinue }
    if ($cmd -and $cmd.Source) {
        $candidates.Add($cmd.Source)
    }
    $regRoots = @(
        'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup*',
        'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup*',
        'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup*'
    )
    foreach ($pattern in $regRoots) {
        $entries = @(Get-ItemProperty -Path $pattern -ErrorAction SilentlyContinue)
        foreach ($entry in $entries) {
            foreach ($prop in @('InstallLocation', 'Inno Setup: App Path')) {
                if ($entry.PSObject.Properties.Match($prop).Count -gt 0 -and $entry.$prop) {
                    $candidates.Add((Join-Path "$($entry.$prop)" 'ISCC.exe'))
                }
            }
        }
    }
    if ($env:USERPROFILE) {
        $candidates.Add((Join-Path $env:USERPROFILE 'scoop\apps\innosetup\current\ISCC.exe'))
    }
    if ($env:ProgramData) {
        $candidates.Add((Join-Path $env:ProgramData 'chocolatey\bin\ISCC.exe'))
    }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    return $null
}

$AppName         = 'PacktoWorkEU'
$Version         = Get-Version
$DistDir         = Join-Path $Root 'dist'
$BuildDir        = Join-Path $DistDir $AppName
$ExePath         = Join-Path $BuildDir "$AppName.exe"
$Venv            = Join-Path $Root '.build\windows-venv'
$WinBuildRoot    = Join-Path $Root '.build\windows'
$WorkDir         = Join-Path $WinBuildRoot 'work'
$FrozenDir       = Join-Path $WinBuildRoot 'frozen'
$InstallerSrcDir = Join-Path $WinBuildRoot 'installer-src'
$CompilerOutDir  = Join-Path $WinBuildRoot 'installer-out'
$InstallerName   = "packtoworkeu-$Version-setup.exe"
$FreshInstaller  = Join-Path $CompilerOutDir $InstallerName
$InstallerOut    = Join-Path $DistDir $InstallerName
$BuildReportOut  = Join-Path $DistDir "packtoworkeu-$Version-build-report.json"

$Iscc = Find-Iscc
if (-not $BundleOnly -and -not $Iscc) {
    throw ("Inno Setup compiler (ISCC.exe) was not found. Install Inno Setup 6/7 " +
           "(https://jrsoftware.org/isinfo.php or 'winget install JRSoftware.InnoSetup'), " +
           "or run .\build_exe.ps1 -BundleOnly to build only dist\$AppName.")
}

Assert-AppClosed $ExePath

# Resolve bootstrap interpreter without touching the caller's environment packages.
$Bootstrap = $null
if ($PythonExe) {
    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
        throw "Specified -PythonExe does not exist: $PythonExe"
    }
    $Bootstrap = (Resolve-Path -LiteralPath $PythonExe).Path
}
if (-not $Bootstrap) {
    if ($env:VIRTUAL_ENV) {
        $venvPython = Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe'
        if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
            $Bootstrap = $venvPython
        }
    }
}
if (-not $Bootstrap) {
    $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        $prevErr = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $detected = & $pyLauncher.Source -3 -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1
            $pyCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $prevErr
        }
        if ($pyCode -eq 0 -and $detected -and (Test-Path -LiteralPath "$detected" -PathType Leaf)) {
            $Bootstrap = "$detected"
        } else {
            $Bootstrap = $pyLauncher.Source
        }
    } else {
        $pyCmd = Get-Command python.exe -ErrorAction SilentlyContinue
        if (-not $pyCmd) { $pyCmd = Get-Command python -ErrorAction SilentlyContinue }
        if ($pyCmd) {
            $Bootstrap = $pyCmd.Source
        }
    }
}
if (-not $Bootstrap) {
    throw 'Python 3.10+ not found. Activate your venv, install Python, or pass -PythonExe C:\path\python.exe.'
}

$Python = Join-Path $Venv 'Scripts\python.exe'
Write-Host "[1/4] Preparing isolated build environment in $Venv..." -ForegroundColor Cyan
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $Venv) -Force | Out-Null
    & $Bootstrap -I -m venv $Venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw "Failed to create isolated build environment at $Venv. Move any incomplete $Venv directory aside and retry."
    }
}

$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
Invoke-BuildPython -m pip --version
Invoke-BuildPython -m pip install -r (Join-Path $Root 'requirements.txt')
Invoke-BuildPython -m pip check
New-Item -ItemType Directory -Path $WinBuildRoot -Force | Out-Null
Invoke-BuildPython -m pip freeze
$FrozenDeps = $script:NativeOutput -join "`r`n"
[System.IO.File]::WriteAllText((Join-Path $WinBuildRoot 'build-dependencies.txt'), $FrozenDeps, (New-Object System.Text.UTF8Encoding($false)))
Invoke-BuildPython -c "from playwright.sync_api import sync_playwright"

Write-Host "[2/4] Building $AppName from PacktoWorkEU.spec..." -ForegroundColor Cyan
foreach ($dir in @($WorkDir, $FrozenDir, $InstallerSrcDir, $CompilerOutDir)) {
    if (Test-Path -LiteralPath $dir) {
        Remove-Item -LiteralPath $dir -Recurse -Force
    }
}
New-Item -ItemType Directory -Path $DistDir -Force | Out-Null

Invoke-BuildPython -m PyInstaller `
    --clean `
    --noconfirm `
    --workpath $WorkDir `
    --distpath $FrozenDir `
    (Join-Path $Root 'PacktoWorkEU.spec')

$FrozenAppDir = Join-Path $FrozenDir $AppName
$FrozenExe = Join-Path $FrozenAppDir "$AppName.exe"
if (-not (Test-Path -LiteralPath $FrozenExe -PathType Leaf)) {
    throw "PyInstaller did not produce expected executable: $FrozenExe"
}

if (Test-Path -LiteralPath $BuildDir) {
    Remove-Item -LiteralPath $BuildDir -Recurse -Force
}
New-Item -ItemType Directory -Path $BuildDir -Force | Out-Null
Copy-Item -Path (Join-Path $FrozenAppDir '*') -Destination $BuildDir -Recurse -Force

New-Item -ItemType Directory -Path $InstallerSrcDir -Force | Out-Null
Copy-Item -Path (Join-Path $BuildDir '*') -Destination $InstallerSrcDir -Recurse -Force
Copy-Item -LiteralPath (Join-Path $Root 'packtoworkeu\data\packtoworkeu.ico') -Destination (Join-Path $InstallerSrcDir 'packtoworkeu.ico') -Force

Write-Host "[3/4] Validating final staged payload (dependencies, 8-tab GUI smoke, payload audit)..." -ForegroundColor Cyan
$env:PACKTOWORKEU_SELF_CHECK_LOG = Join-Path $WinBuildRoot 'self-check.log'
try {
    Test-Payload (Join-Path $InstallerSrcDir "$AppName.exe")
} finally {
    Remove-Item Env:\PACKTOWORKEU_SELF_CHECK_LOG -ErrorAction SilentlyContinue
}

if (-not $BundleOnly) {
    Write-Host "[4/4] Compiling Inno Setup installer into fresh staging directory..." -ForegroundColor Cyan
    New-Item -ItemType Directory -Path $CompilerOutDir -Force | Out-Null
    $prevErr = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Iscc "/DMyAppVersion=$Version" "/DInstallerSourceDir=$InstallerSrcDir" "/O$CompilerOutDir" "/Fpacktoworkeu-$Version-setup" (Join-Path $Root 'installer.iss')
        $CompilerCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevErr
    }
    if ($CompilerCode -ne 0) {
        throw "Inno Setup compiler failed (exit $CompilerCode)."
    }
    if (-not (Test-Path -LiteralPath $FreshInstaller -PathType Leaf) -or (Get-Item -LiteralPath $FreshInstaller).Length -le 0) {
        throw "Inno Setup finished with exit 0 but did not produce a nonempty fresh output at $FreshInstaller."
    }
    Move-Item -LiteralPath $FreshInstaller -Destination $InstallerOut -Force
} else {
    Write-Host "[4/4] Skipping Inno Setup installer compilation (-BundleOnly)." -ForegroundColor Yellow
}

Write-Host ""
Write-Artifact `
    -Version $Version `
    -BundleDir $BuildDir `
    -StagedDir $InstallerSrcDir `
    -InstallerPath $InstallerOut `
    -ReportPath $BuildReportOut `
    -IsBundleOnly ([bool]$BundleOnly)

if (Test-Path -LiteralPath $InstallerSrcDir) {
    Remove-Item -LiteralPath $InstallerSrcDir -Recurse -Force
}
if (Test-Path -LiteralPath $CompilerOutDir) {
    Remove-Item -LiteralPath $CompilerOutDir -Recurse -Force
}
