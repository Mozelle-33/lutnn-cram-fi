# From trained models to bitstreams: wait until every model's spec exists, generate each DUT,
# simulate it against the software reference, then build all bitstreams in parallel.
# Usage: powershell -File train\pipeline_models.ps1 <build>:<build_id>:<dut_id>[:<model>[:tmr]] ...
#   dwn_md_s1:14:12                        DUT of model dwn_md_s1
#   dwn_md_tmr_s1:16:14:dwn_md_s1:tmr      model dwn_md_s1 with a triplicated output stage
# Build and DUT ids must be unique; they are reported in the status word and checked by the host.
Set-Location (Split-Path -Parent $PSScriptRoot)          # repository root
# Vivado installation: $env:XILINX_VIVADO if set (e.g. by settings64.bat), else the local default
$vivado = if ($env:XILINX_VIVADO) { $env:XILINX_VIVADO } else { throw 'Set XILINX_VIVADO to the Vivado installation directory (settings64.bat sets it)' }
$builds = foreach ($a in $args) {
    $f = $a.Split(':')
    @{ b = $f[0]; bid = $f[1]; did = $f[2]; m = $(if ($f.Count -gt 3) { $f[3] } else { $f[0] }); tmr = ($f.Count -gt 4 -and $f[4] -eq 'tmr') }
}

# 1. wait for the trainings (train.py writes models/<name>.json only after its integer check passes)
while (@($builds | Where-Object { -not (Test-Path "models\$($_.m).json") }).Count -gt 0) {
    $err = $builds | ForEach-Object { "results\train_$($_.m).err" } | Where-Object { Test-Path $_ }
    if ($err -and (Select-String -Path $err -Pattern 'Traceback' -Quiet)) { "TRAINING FAILED"; exit 1 }
    Start-Sleep -Seconds 10
}
"TRAINED"
# 2. DUT generation and RTL simulation against the software reference (must report 0 mismatches)
foreach ($r in $builds) {
    $opt = @('--out', $r.b) + $(if ($r.tmr) { @('--tmr_out') } else { @() })
    & env\venv\Scripts\python.exe train\gen_dut.py $r.m @opt 2>&1 | Select-Object -Last 1
    $sim = powershell -NoProfile -ExecutionPolicy Bypass -File hw\sim\run_dut_sim.ps1 $r.b 2>&1 | Out-String
    "SIM $($r.b): $($sim.Trim())"
}
# 3. bitstreams, one Vivado process per build
$procs = foreach ($r in $builds) {
    $meta = Get-Content "hw\gen\$($r.b)\meta.json" | ConvertFrom-Json
    New-Item -ItemType Directory -Force "hw\build\$($r.b)" | Out-Null
    Start-Process -FilePath "$vivado\bin\vivado.bat" -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput "hw\build\$($r.b)\vivado_stdout.log" `
        -ArgumentList '-mode', 'batch', '-nojournal', '-source', 'hw\tcl\build.tcl',
                      '-tclargs', $r.b, $meta.LATENCY, $meta.IN_W, $r.bid, $r.did, 'auto', '200', $r.b, $meta.CW, $meta.NVEC
}
$procs | Wait-Process
foreach ($r in $builds) {
    "BUILD $($r.b): bit=$(Test-Path "hw\build\$($r.b)\fi_$($r.b).bit")"
    Get-Content "hw\build\$($r.b)\pblock.txt" -ErrorAction SilentlyContinue
}
