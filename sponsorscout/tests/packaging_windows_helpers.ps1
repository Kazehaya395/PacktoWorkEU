param([string]$RepoRoot, [string]$PythonForTest)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $RepoRoot 'build_exe.ps1'), [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw "Builder parse errors: $errors" }
# Import only these actual helper definitions, never the Windows-only build body.
$names = @('Invoke-BuildPython','Test-Payload','Assert-AppClosed','Write-Artifact')
$definitions = $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $false)
foreach ($definition in $definitions) {
    if ($names -contains $definition.Name) { Invoke-Expression $definition.Extent.Text }
}
function Require([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
function MustThrow([scriptblock]$Action) {
    $caught = $false
    try { & $Action } catch { $caught = $true }
    Require $caught 'Expected failure was incorrectly accepted'
}
$Root = $RepoRoot
$Python = $PythonForTest
Invoke-BuildPython -c 'import sys; print("fixture stderr", file=sys.stderr)'
MustThrow { Invoke-BuildPython -c 'import sys; sys.exit(7)' }
Require ($ErrorActionPreference -eq 'Stop') 'Native wrapper did not restore error policy'
# Replace process-start calls only; never launch an application or touch user data.
function Invoke-BuildPython { $script:AuditCalls++ }
function Start-Process {
    param([string]$FilePath, [string]$ArgumentList, [switch]$Wait, [switch]$PassThru)
    Require ($Wait -and $PassThru) 'Self-check must wait and inspect exit status'
    $script:Calls += $ArgumentList
    $code = $script:Codes[$script:Calls.Count - 1]
    return [pscustomobject]@{ExitCode=$code}
}
function Set-Codes([int[]]$Values) {
    $script:Calls = @(); $script:Codes = $Values; $script:AuditCalls = 0
}
$env:SPONSORSCOUT_VALIDATE_BROWSER = '0'
Set-Codes @(0,0)
Test-Payload '/fixture folder/SponsorScout.exe'
Require ($script:Calls.Count -eq 2 -and $script:AuditCalls -eq 1) 'Missing driver/GUI/audit calls'
Require ($script:Calls[1] -eq '--self-check-gui') 'GUI check missing'
Set-Codes @(3,0)
MustThrow { Test-Payload '/fixture folder/SponsorScout.exe' }
Require ($script:Calls.Count -eq 1) 'Failed driver check did not abort'
Set-Codes @(0,4)
MustThrow { Test-Payload '/fixture folder/SponsorScout.exe' }
$env:SPONSORSCOUT_VALIDATE_BROWSER = '1'
Set-Codes @(0,0,0)
Test-Payload '/fixture folder/SponsorScout.exe'
Require ($script:Calls[2] -eq '--self-check-browser') 'Explicit optional browser check missing'
Set-Codes @(0,0,5)
MustThrow { Test-Payload '/fixture folder/SponsorScout.exe' }
function Get-CimInstance { return $script:Running }
$script:Running = @([pscustomobject]@{ExecutablePath='C:\other\SponsorScout.exe'})
Assert-AppClosed 'C:\target\SponsorScout.exe'
$script:Running = @([pscustomobject]@{ExecutablePath='C:\TARGET\SponsorScout.exe'})
MustThrow { Assert-AppClosed 'C:\target\SponsorScout.exe' }
$script:Running = @([pscustomobject]@{ExecutablePath=$null})
MustThrow { Assert-AppClosed 'C:\target\SponsorScout.exe' }
Write-Host 'PowerShell helper checks OK: exit-code failures, driver/GUI/browser order, audit and scoped process refusal. Process calls mocked; not a native installer test.'
