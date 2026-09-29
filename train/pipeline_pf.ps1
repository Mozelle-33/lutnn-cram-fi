# After the physical-noise fault-aware trainings: generate the DUTs, simulate them against the
# software reference and build both bitstreams in parallel (BUILD 12/13, DUT 10/11).
Set-Location (Split-Path -Parent $PSScriptRoot)          # repository root
# Vivado installation: $env:XILINX_VIVADO if set (e.g. by settings64.bat), else the local default
$vivado = if ($env:XILINX_VIVADO) { $env:XILINX_VIVADO } else { 'G:\AMDDesignTools\2026.1.1\Vivado' }
$models = @(@{ m = "dwn_md_pf2"; bid = 12; did = 10 }, @{ m = "dwn_md_pf5"; bid = 13; did = 11 })
while (-not ((Test-Path models\dwn_md_pf2.json) -and (Test-Path models\dwn_md_pf5.json))) {
    if (Select-String -Path results\train_dwn_md_pf2.err, results\train_dwn_md_pf5.err -Pattern 'Traceback' -Quiet) {
        "TRAINING FAILED"; exit 1
    }
    Start-Sleep -Seconds 10
}
"TRAINED"
foreach ($r in $models) {
    & env\venv\Scripts\python.exe train\gen_dut.py $r.m 2>&1 | Select-Object -Last 2
    $sim = powershell -NoProfile -ExecutionPolicy Bypass -File hw\sim\run_dut_sim.ps1 $r.m 2>&1 | Out-String
    "SIM $($r.m): $($sim.Trim())"
}
$procs = foreach ($r in $models) {
    $lat = (Get-Content "hw\gen\$($r.m)\meta.json" | ConvertFrom-Json).LATENCY
    New-Item -ItemType Directory -Force "hw\build\$($r.m)" | Out-Null
    Start-Process -FilePath "$vivado\bin\vivado.bat" -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput "hw\build\$($r.m)\vivado_stdout.log" `
        -ArgumentList '-mode', 'batch', '-nojournal', '-log', "hw\build\$($r.m)\vivado.log", '-source', 'hw\tcl\build.tcl',
                      '-tclargs', $r.m, $lat, '160', $r.bid, $r.did, 'auto'
}
$procs | Wait-Process
foreach ($r in $models) {
    $bit = "hw\build\$($r.m)\fi_$($r.m).bit"
    "BUILD $($r.m): bit=$(Test-Path $bit)"
    Get-Content "hw\build\$($r.m)\pblock.txt" -ErrorAction SilentlyContinue
}
