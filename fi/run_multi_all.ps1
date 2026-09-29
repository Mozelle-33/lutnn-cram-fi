# Accumulated-upset experiments for DWN-M, DLGN and MLP.
# Usage: powershell -File fi\run_multi_all.ps1 [suffix]
param([string]$suffix = "")
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)          # repository root
# Vivado installation: $env:XILINX_VIVADO if set (e.g. by settings64.bat), else the local default
$vivado = if ($env:XILINX_VIVADO) { $env:XILINX_VIVADO } else { throw 'Set XILINX_VIVADO to the Vivado installation directory (settings64.bat sets it)' }
$xsdb = "$vivado\bin\xsdb.bat"
$runs = @(
    @{ m = "dwn_md";    f0 = 72; f1 = 967 },
    @{ m = "dlgn_a";    f0 = 72; f1 = 1175 },
    @{ m = "mlp_32_16"; f0 = 72; f1 = 1671 }
)
foreach ($r in $runs) {
    $bit = "hw\build\$($r.m)\fi_$($r.m).bit"
    & $xsdb fi\program.tcl $bit 2>&1 | Select-Object -Last 1
    if ($LASTEXITCODE -ne 0) { throw "programming $bit failed" }
    & $xsdb fi\multi_upset.tcl $bit "results\multi\trials_$($r.m)$suffix.txt" "results\multi\hw_$($r.m)$suffix.tsv" 4096 $r.f0 $r.f1 2>&1 | Select-Object -Last 2
    if ($LASTEXITCODE -ne 0) { throw "multi_upset $($r.m) failed" }
}
