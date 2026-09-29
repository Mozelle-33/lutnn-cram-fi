# Export PIPs and LUT-pin maps for DWN-M variants, then validate the IMUX model on each.
# Usage: powershell -File analysis\run_imux_all.ps1 [<build>[:<model>] ...]
#   Without arguments: the hardened variants of the first campaign series. The model defaults to the
#   build name; a triplicated-output build names its model, e.g. dwn_md_tmr_s1:dwn_md_s1.
Set-Location (Split-Path -Parent $PSScriptRoot)          # repository root
# Vivado installation: $env:XILINX_VIVADO if set (e.g. by settings64.bat), else the local default
$vivado = if ($env:XILINX_VIVADO) { $env:XILINX_VIVADO } else { throw 'Set XILINX_VIVADO to the Vivado installation directory (settings64.bat sets it)' }
$viv = "$vivado\bin\vivado.bat"
$py = 'env\venv\Scripts\python.exe'
$list = if ($args.Count) { $args } else { @('dwn_md_dc', 'dwn_md_fa2', 'dwn_md_fa5', 'dwn_md_tmr:dwn_md', 'dwn_md_fa2_tmr:dwn_md_fa2') }
foreach ($a in $list) {
    $b, $m = $a.Split(':')
    if (-not $m) { $m = $b }
    if (-not (Test-Path "hw\build\$b\dut_pips.csv")) {
        & $viv -mode batch -nojournal -nolog -source hw\tcl\export_pips.tcl -tclargs $b 2>&1 | Select-String -Pattern 'PIPS|ERROR'
    }
    if (-not (Test-Path "hw\build\$b\lut_pins.csv")) {
        & $viv -mode batch -nojournal -nolog -source hw\tcl\export_lutpins.tcl -tclargs $b 2>&1 | Select-String -Pattern 'LUTPINS|ERROR'
    }
    "== $b"
    & $py analysis\imux_model.py $m "results\camp_$b" $b 2>&1 | Select-Object -Last 1
}
