$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = (Get-Command python).Source
$appPath = Join-Path $projectDir "app.py"

function Register-WeatherTask {
    param(
        [string]$TaskName,
        [string]$Arguments,
        [datetime]$At,
        [timespan]$RepetitionInterval,
        [timespan]$RepetitionDuration
    )

    $action = New-ScheduledTaskAction -Execute $pythonExe -Argument "`"$appPath`" $Arguments" -WorkingDirectory $projectDir

    if ($RepetitionInterval) {
        $trigger = New-ScheduledTaskTrigger -Once -At $At `
            -RepetitionInterval $RepetitionInterval `
            -RepetitionDuration $RepetitionDuration
    } else {
        $trigger = New-ScheduledTaskTrigger -Daily -At $At
    }

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Description "天气通知自动任务: $Arguments" `
        -Force | Out-Null
}

Register-WeatherTask -TaskName "天气通知-明早天气" -Arguments "tomorrow" -At ([datetime]"22:00") -RepetitionInterval $null -RepetitionDuration $null
Register-WeatherTask -TaskName "天气通知-未来三小时" -Arguments "hourly" -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 3650)

Write-Host "已注册 2 个天气通知计划任务。"
