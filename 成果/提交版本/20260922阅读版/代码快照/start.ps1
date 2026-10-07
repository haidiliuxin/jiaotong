param(
    [ValidateSet('check', 'serve', 'run')][string]$Mode = 'serve',
    [string]$Task = 'examples/parking_quickstart_v2.json',
    [int]$Port = 8769,
    [Alias('GccDirectory')][string]$CompilerBin = '',
    [string]$Python = '',
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    if ($CheckOnly) { $Mode = 'check' }
    $taskPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
    $taskArgs = @('-m', 'traffic_agent.startup', '--mode', $Mode, '--task', $Task, '--port', "$Port")
    if ($CompilerBin) { $taskArgs += @('--compiler-bin', $CompilerBin) }
    if ($Python) { & $Python @taskArgs }
    elseif (Test-Path -LiteralPath $taskPython) { & $taskPython @taskArgs }
    elseif (Get-Command py -ErrorAction SilentlyContinue) { & py -3.12 @taskArgs }
    elseif (Get-Command python -ErrorAction SilentlyContinue) { & python @taskArgs }
    else { throw 'Python 3.12 is required. Install it, then run this launcher again.' }
    $taskExit = $LASTEXITCODE
} finally { Pop-Location }
exit $taskExit
