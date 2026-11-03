<#
.SYNOPSIS
    Install SoftKVM on Windows.

.DESCRIPTION
    Two modes are available:

    Task    (default, RECOMMENDED for a KVM)
        A scheduled task that starts SoftKVM when you log on, inside your
        interactive session. Screen capture and mouse/keyboard injection work
        because the process can reach the desktop.

    Service
        A real Windows Service (session 0). It starts with the machine and
        restarts on failure, but session 0 has no interactive desktop, so the
        screen stream and input injection are unavailable. Use this for
        headless telemetry.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy\install-windows.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy\install-windows.ps1 -Mode Service
#>
[CmdletBinding()]
param(
    [ValidateSet('Task', 'Service')]
    [string]$Mode = 'Task',

    [int]$Port = 3699,

    [string]$InstallDir = "$env:ProgramFiles\SoftKVM",

    [switch]$SkipDependencies
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$TaskName = 'SoftKVM'

function Write-Step($message) { Write-Host "==> $message" -ForegroundColor Cyan }
function Write-Ok($message) { Write-Host "    $message" -ForegroundColor Green }

if ($Mode -eq 'Service') {
    Write-Step 'Installing SoftKVM as a Windows Service (session 0 - telemetry only)'

    if (-not $SkipDependencies) {
        python -m pip install --upgrade pip | Out-Null
        python -m pip install -r (Join-Path $ProjectRoot 'requirements.txt') pywin32
    }

    if (-not (Test-Path $InstallDir)) {
        New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    }

    robocopy $ProjectRoot $InstallDir /E /XD .git data .venv __pycache__ /NFL /NDL /NJH /NJS /NP | Out-Null
    Write-Ok "copied to $InstallDir"

    Push-Location $InstallDir
    try {
        python (Join-Path $InstallDir 'deploy\windows_service.py') install
    } finally {
        Pop-Location
    }

    Write-Step 'Starting the service'
    Start-Service -Name 'SoftKVM'
    Write-Ok 'service started'
    Write-Host ''
    Write-Host '  Manage it with:  Get-Service SoftKVM | Format-List' -ForegroundColor Yellow
    Write-Host '  View the log:    Get-Content "$env:ProgramData\SoftKVM\softkvm.log"' -ForegroundColor Yellow
    Write-Host ''
    Write-Host '  NOTE: a service runs in session 0 with no desktop, so the screen' -ForegroundColor Yellow
    Write-Host '  stream and input injection are unavailable. Use -Mode Task for full KVM.' -ForegroundColor Yellow
    exit 0
}

# ---------------------------------------------------------------- Task mode
Write-Step 'Installing SoftKVM as a scheduled task (interactive session - full KVM)'

if (-not $SkipDependencies) {
    python -m pip install --upgrade pip | Out-Null
    python -m pip install -r (Join-Path $ProjectRoot 'requirements.txt')
}

$pythonExe = (Get-Command python).Source
$serverScript = Join-Path $ProjectRoot 'server.py'
$dataDir = Join-Path $env:LOCALAPPDATA 'SoftKVM'
if (-not (Test-Path $dataDir)) { New-Item -ItemType Directory -Force -Path $dataDir | Out-Null }

Write-Ok "python   : $pythonExe"
Write-Ok "server   : $serverScript"
Write-Ok "data dir : $dataDir"

# Stop and remove any previous task before recreating it.
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Ok 'removed the previous task'
}

$action = New-ScheduledTaskAction -Execute $pythonExe -Argument "`"$serverScript`"" -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

# "Run with highest privileges" so the task can drive the desktop session.
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description 'SoftKVM - browser based KVM and system dashboard' | Out-Null

Write-Ok 'scheduled task registered'

Write-Step 'Starting the task'
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 3

$state = (Get-ScheduledTask -TaskName $TaskName).State
Write-Ok "task state: $state"

$lan = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike '169.254.*' -and $_.IPAddress -notlike '127.*' -and $_.PrefixOrigin -ne 'WellKnown' } |
    Select-Object -ExpandProperty IPAddress -Unique

Write-Host ''
Write-Host "  SoftKVM is running on port $Port" -ForegroundColor Green
foreach ($ip in $lan) {
    Write-Host "    http://${ip}:$Port" -ForegroundColor Green
}
Write-Host "    http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host ''
Write-Host '  The first-run password is printed in the task output:' -ForegroundColor Yellow
Write-Host "    Get-ScheduledTaskInfo -TaskName $TaskName | Select-Object -ExpandProperty LastTaskResult" -ForegroundColor Yellow
Write-Host '  or read the log file:' -ForegroundColor Yellow
Write-Host "    Get-Content `"$dataDir\softkvm.log`" -Tail 20" -ForegroundColor Yellow
Write-Host ''
Write-Host '  Manage it with:  Get-ScheduledTask -TaskName SoftKVM' -ForegroundColor Yellow
Write-Host '  Stop it with:     Stop-ScheduledTask -TaskName SoftKVM' -ForegroundColor Yellow
Write-Host '  Remove it with:   Unregister-ScheduledTask -TaskName SoftKVM -Confirm:$false' -ForegroundColor Yellow
