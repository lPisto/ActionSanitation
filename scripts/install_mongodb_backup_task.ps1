[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$TaskName = "ActionSanitationMongoBackup",
    [string]$BackendDir,
    [string]$PythonPath,
    [string[]]$RunAt = @("00:00", "20:00")
)

$ErrorActionPreference = "Stop"

if (-not $BackendDir) {
    $BackendDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
} else {
    $BackendDir = (Resolve-Path $BackendDir).Path
}

$BackupScript = Join-Path $BackendDir "scripts\mongodb_backup.py"
if (-not (Test-Path $BackupScript)) {
    throw "Backup script not found: $BackupScript"
}

if (-not $PythonPath) {
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $PythonCommand) {
        throw "Python was not found in PATH. Pass -PythonPath with the full python.exe path."
    }
    $PythonPath = $PythonCommand.Source
}

$NormalizedRunAt = foreach ($Value in $RunAt) {
    $Value -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ }
}

$Triggers = foreach ($TimeText in $NormalizedRunAt) {
    $TriggerTime = [datetime]::ParseExact(
        $TimeText,
        "HH:mm",
        [System.Globalization.CultureInfo]::InvariantCulture
    )
    New-ScheduledTaskTrigger -Daily -At $TriggerTime
}

$Action = New-ScheduledTaskAction `
    -Execute $PythonPath `
    -Argument "`"$BackupScript`"" `
    -WorkingDirectory $BackendDir

$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3)

if ($PSCmdlet.ShouldProcess($TaskName, "Register MongoDB backup scheduled task")) {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $Action `
        -Trigger $Triggers `
        -Settings $Settings `
        -Description "MongoDB cluster backup for Action Sanitation." `
        -Force | Out-Null

    Write-Host "Scheduled task '$TaskName' registered."
    Write-Host "Runs daily at: $($NormalizedRunAt -join ', ') local time."
    Write-Host "Backup script: $BackupScript"
}
