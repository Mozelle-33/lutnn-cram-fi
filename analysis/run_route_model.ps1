# Full-route connectivity model for one or more builds: route trees -> first pass (bridge wires) ->
# node-level nets of those wires -> final pass.
# Usage: powershell -File analysis\run_route_model.ps1 "<build>:<model>:<campaign dir>" ...
Set-Location (Split-Path -Parent $PSScriptRoot)          # repository root
# Vivado installation: $env:XILINX_VIVADO if set (e.g. by settings64.bat), else the local default
$vivado = if ($env:XILINX_VIVADO) { $env:XILINX_VIVADO } else { throw 'Set XILINX_VIVADO to the Vivado installation directory (settings64.bat sets it)' }
$viv = "$vivado\bin\vivado.bat"
$py = 'env\venv\Scripts\python.exe'
foreach ($spec in $args) {
    $b, $m, $c = $spec.Split(':')
    if (-not (Test-Path "hw\build\$b\dut_pips.csv")) {
        & $viv -mode batch -nojournal -nolog -source hw\tcl\export_pips.tcl -tclargs $b 2>&1 | Select-String -Pattern 'PIPS|ERROR'
    }
    if (-not (Test-Path "hw\build\$b\route_pips.csv")) {
        & $viv -mode batch -nojournal -nolog -source hw\tcl\export_routetree.tcl -tclargs $b 2>&1 | Select-String -Pattern 'ROUTETREE|ERROR'
    }
    & $py analysis\route_model.py $m $c $b 2>&1 | Select-Object -Last 1
    & $viv -mode batch -nojournal -nolog -source hw\tcl\export_wirenets.tcl -tclargs $b 2>&1 | Select-String -Pattern 'WIRENETS|ERROR'
    "== $b"
    & $py analysis\route_model.py $m $c $b 2>&1 | Select-String -Pattern '^all|evaluable'
}
