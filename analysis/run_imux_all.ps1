# Export PIPs and LUT-pin maps for the DWN-M variants, then validate the IMUX model on each.
Set-Location (Split-Path -Parent $PSScriptRoot)          # repository root
# Vivado installation: $env:XILINX_VIVADO if set (e.g. by settings64.bat), else the local default
$vivado = if ($env:XILINX_VIVADO) { $env:XILINX_VIVADO } else { 'G:\AMDDesignTools\2026.1.1\Vivado' }
$viv = "$vivado\bin\vivado.bat"
$py = 'env\venv\Scripts\python.exe'
$runs = @(
    @{ b = "dwn_md_dc";      m = "dwn_md_dc";  c = "results\camp_dwn_md_dc" },
    @{ b = "dwn_md_fa2";     m = "dwn_md_fa2"; c = "results\camp_dwn_md_fa2" },
    @{ b = "dwn_md_fa5";     m = "dwn_md_fa5"; c = "results\camp_dwn_md_fa5" },
    @{ b = "dwn_md_tmr";     m = "dwn_md";     c = "results\camp_dwn_md_tmr" },
    @{ b = "dwn_md_fa2_tmr"; m = "dwn_md_fa2"; c = "results\camp_dwn_md_fa2_tmr" }
)
foreach ($r in $runs) {
    if (-not (Test-Path "hw\build\$($r.b)\dut_pips.csv")) {
        & $viv -mode batch -nojournal -nolog -source hw\tcl\export_pips.tcl -tclargs $r.b 2>&1 | Select-String -Pattern 'PIPS|ERROR'
    }
    if (-not (Test-Path "hw\build\$($r.b)\lut_pins.csv")) {
        & $viv -mode batch -nojournal -nolog -source hw\tcl\export_lutpins.tcl -tclargs $r.b 2>&1 | Select-String -Pattern 'LUTPINS|ERROR'
    }
    "== $($r.b)"
    & $py analysis\imux_model.py $r.m $r.c $r.b 2>&1 | Select-Object -Last 1
}
