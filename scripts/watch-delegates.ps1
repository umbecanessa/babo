# Watch live delegate batch state for a Babo agent (desktop data dir).
param(
    [string]$AgentPrefix = "05bc2c61",
    [int]$IntervalSec = 5
)

$base = Join-Path $env:APPDATA "babo-desktop\data\agents"
$agentDir = Get-ChildItem $base -Directory | Where-Object { $_.Name -like "$AgentPrefix*" } | Select-Object -First 1
if (-not $agentDir) {
    Write-Error "No agent dir matching '$AgentPrefix' under $base"
    exit 1
}

$delegatesPath = Join-Path $agentDir.FullName "delegates.json"
$logDir = Join-Path $agentDir.FullName "agentic_logs"
Write-Host "Watching $($agentDir.Name)" -ForegroundColor Cyan
Write-Host "  delegates: $delegatesPath"
Write-Host "  logs:      $logDir"
Write-Host ""

while ($true) {
    Clear-Host
    Write-Host ("[{0}] Agent {1}" -f (Get-Date -Format "HH:mm:ss"), $agentDir.Name) -ForegroundColor Cyan
    if (Test-Path $delegatesPath) {
        $state = Get-Content $delegatesPath -Raw | ConvertFrom-Json
        foreach ($batch in $state.batches.PSObject.Properties) {
            $b = $batch.Value
            Write-Host ("Batch {0}: {1}/{2} completed" -f $b.batch_id, $b.completed, $b.total) -ForegroundColor Yellow
        }
        Write-Host ""
        foreach ($d in $state.delegates.PSObject.Properties) {
            $x = $d.Value
            $task = ($x.task -split "`n")[0]
            if ($task.Length -gt 72) { $task = $task.Substring(0, 69) + "..." }
            Write-Host ("  #{0} {1} | iter {2}/{3} | tc={4} | {5}s" -f `
                $x.delegate_number, $x.state, $x.iteration, $x.max_iterations, $x.total_tool_calls, [math]::Round($x.elapsed))
            Write-Host "       $task"
            if ($x.last_actions) {
                Write-Host ("       last: {0}" -f ($x.last_actions[-1])) -ForegroundColor DarkGray
            }
        }
    } else {
        Write-Host "No delegates.json yet" -ForegroundColor DarkYellow
    }

    $latestOrch = Get-ChildItem $logDir -Filter "loop_*.jsonl" |
        Where-Object { $_.Name -notmatch "60ff1146|04b61a61|5a9be079|60251ccb|ccc8b4c4" } |
        Sort-Object LastWriteTime -Descending | Select-Object -First 3
    Write-Host ""
    Write-Host "Recent orchestrator loop events:" -ForegroundColor Green
    foreach ($f in $latestOrch) {
        $tail = Get-Content $f.FullName -Tail 2 | ForEach-Object {
            try { ($_ | ConvertFrom-Json) } catch { $null }
        } | Where-Object { $_ }
        foreach ($ev in $tail) {
            $line = if ($ev.event -eq "generation") {
                "  gen iter={0} tools={1}" -f $ev.iteration, ($ev.tool_calls | ForEach-Object { $_.name }) -join ","
            } elseif ($ev.event -eq "loop_end") {
                "  END {0} iters={1} reason={2}" -f $f.Name, $ev.iterations, $ev.exit_reason
            } else {
                "  {0} iter={1}" -f $ev.event, $ev.iteration
            }
            Write-Host $line
        }
    }
    Start-Sleep -Seconds $IntervalSec
}
