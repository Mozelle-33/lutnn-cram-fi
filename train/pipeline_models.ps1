# From trained models to bitstreams: wait until every model's spec exists, generate its DUT,
# simulate it against the software reference, then build all bitstreams in parallel.
# Usage: powershell -File train\pipeline_models.ps1 <name>:<build_id>:<dut_id> ...
#   e.g. train\pipeline_models.ps1 dwn_md_s1:14:12 dwn_md_s2:15:13
# Build and DUT ids must be unique; they are reported in the status word and checked by the host.
Set-Location (Split-Path -Parent $PSScriptRoot)          # repository root
# Vivado installation: $env:XILINX_VIVADO if set (e.g. by settings64.bat), else the local default
$vivado = if ($env:XILINX_VIVADO) { $env:XILINX_VIVADO } else { 'G:\AMDDesignTools\2026.1.1\Vivado' }
$models = foreach ($a in $args) { $m, $b, $d = $a.Split(':'); @{ m = $m; bid = $b; did = $d } }

# 1. wait for the trainings (train.py writes models/<name>.json only after its integer check passes)
while (@($models | Where-Object { -not (Test-Path "models\$($_.m).json") }).Count -gt 0) {
    $err = $models | ForEach-Object { "results\train_$($_.m).err" } | Where-Object { Test-Path $_ }
    if ($err -and (Select-String -Path $err -Pattern 'Traceback' -Quiet)) { "TRAINING FAILED"; exit 1 }
    Start-Sleep -Seconds 10
}
"TRAINED"
# 2. DUT generation and RTL simulation against the software reference (must report 0 mismatches)
foreach ($r in $models) {
    & env\venv\Scripts\python.exe train\gen_dut.py $r.m 2>&1 | Select-Object -Last 1
    $sim = powershell -NoProfile -ExecutionPolicy Bypass -File hw\sim\run_dut_sim.ps1 $r.m 2>&1 | Out-String
    "SIM $($r.m): $($sim.Trim())"
}
# 3. bitstreams, one Vivado process per model
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
    "BUILD $($r.m): bit=$(Test-Path "hw\build\$($r.m)\fi_$($r.m).bit")"
    Get-Content "hw\build\$($r.m)\pblock.txt" -ErrorAction SilentlyContinue
}
