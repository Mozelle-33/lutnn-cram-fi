# Board queue: the exhaustive campaign of each listed build as soon as its bitstream is built, and in
# between the remaining LUT-mode bits of one design in chunks (fi/inject_list.tcl), so that neither
# waits for the other. Resumable after a host crash: finished campaigns (analysis.json) are skipped,
# an interrupted one is moved to results\interrupted and run again, and the mode-bit injection
# continues after the last bit in its output file (inject_list.tcl appends, in list order).
# Usage: powershell -File fi\board_queue.ps1 <mode build> <mode-bit list> <mode-bit output>
#                          <far_first> <far_last> <build>[:<model>] ...
param([string]$modeBuild, [string]$modeList, [string]$modeOut, [int]$farFirst, [int]$farLast,
      [Parameter(ValueFromRemainingArguments = $true)][string[]]$builds)
Set-Location (Split-Path -Parent $PSScriptRoot)          # repository root
$vivado = if ($env:XILINX_VIVADO) { $env:XILINX_VIVADO } else { throw 'Set XILINX_VIVADO to the Vivado installation directory (settings64.bat sets it)' }
$xsdb = "$vivado\bin\xsdb.bat"
$chunk = 200                                             # mode bits between two checks for new builds
$pending = [System.Collections.ArrayList]@($builds)
while ($true) {
    foreach ($a in @($pending)) {
        $b = $a.Split(':')[0]
        if (Test-Path "results\camp_$b\analysis.json") { $pending.Remove($a); continue }
        $log = "hw\build\$b\vivado_stdout.log"
        if ((Test-Path $log) -and (Select-String -Path $log -Pattern 'BUILD_DONE' -Quiet)) {
            if (Test-Path "results\camp_$b") {                # left over from an interrupted run
                New-Item -ItemType Directory -Force results\interrupted | Out-Null
                Move-Item "results\camp_$b" "results\interrupted\camp_${b}_$(Get-Date -Format yyyyMMddHHmm)"
            }
            "CAMPAIGN $a $(Get-Date -Format HH:mm)"
            powershell -NoProfile -ExecutionPolicy Bypass -File fi\run_campaigns.ps1 $a
            $pending.Remove($a)
        }
    }
    $all = @(Get-Content $modeList)
    $done = if (Test-Path $modeOut) { @(Get-Content $modeOut).Count } else { 0 }
    if ($done -lt $all.Count) {
        $part = "$modeOut.chunk"
        $all[$done..([math]::Min($done + $chunk, $all.Count) - 1)] | Set-Content $part -Encoding ascii
        & $xsdb fi\program.tcl "hw\build\$modeBuild\fi_$modeBuild.bit" 2>&1 | Select-Object -Last 1
        & $xsdb fi\inject_list.tcl "hw\build\$modeBuild\fi_$modeBuild.bit" $part $modeOut 4096 $farFirst $farLast 2>&1 |
            Select-Object -Last 1
        "MODE $(@(Get-Content $modeOut).Count)/$($all.Count) $(Get-Date -Format HH:mm)"
    } elseif ($pending.Count -eq 0) {
        break
    } else {
        Start-Sleep -Seconds 60                          # all mode bits done, builds still running
    }
}
"QUEUE DONE $(Get-Date -Format HH:mm)"
