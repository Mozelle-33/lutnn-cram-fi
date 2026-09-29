# Pruned MLP baseline (70 % of the weights removed): exhaustive campaign, then the accumulated-upset
# trials in hardware and the additive single-bit prediction (as fi/run_multi_all.ps1 for the others).
# Usage: powershell -File fi\run_pruned_mlp.ps1
Set-Location (Split-Path -Parent $PSScriptRoot)          # repository root
$vivado = if ($env:XILINX_VIVADO) { $env:XILINX_VIVADO } else { throw 'Set XILINX_VIVADO to the Vivado installation directory (settings64.bat sets it)' }
$xsdb = "$vivado\bin\xsdb.bat"
$py = 'env\venv\Scripts\python.exe'
$m = 'mlp_32_16_p70'
powershell -NoProfile -ExecutionPolicy Bypass -File fi\run_campaigns.ps1 $m
# 3 x the trials per upset probability of analysis/multi_upset.py (as the two batches of the MLP)
& $py analysis\multi_upset.py gen "camp_$m" "results\multi\trials_$m.txt" 11 3
# the range only configures the controller's test runs; the trials name their bits explicitly
$f0, $f1 = (& $py analysis\campaign_range.py $m | Select-Object -First 1).Trim().Split()[0..1]
$bit = "hw\build\$m\fi_$m.bit"
& $xsdb fi\program.tcl $bit 2>&1 | Select-Object -Last 1
& $xsdb fi\multi_upset.tcl $bit "results\multi\trials_$m.txt" "results\multi\hw_$m.tsv" 4096 $f0 $f1 2>&1 | Select-Object -Last 2
& $py analysis\multi_upset.py pred $m "camp_$m"
& $py analysis\multi_upset.py numbers | Select-Object -Last 4
"PRUNED MLP DONE $(Get-Date -Format HH:mm)"
