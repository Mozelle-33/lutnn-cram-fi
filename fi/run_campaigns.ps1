# Exhaustive campaign + analysis for each listed build.
# Usage: powershell -File fi\run_campaigns.ps1 <build> ...        (model spec = models\<build>.json)
# For each build: FAR range of its pblock, LUT-mode skip list, program, campaign, analysis.
Set-Location (Split-Path -Parent $PSScriptRoot)          # repository root
# Vivado installation: $env:XILINX_VIVADO if set (e.g. by settings64.bat), else the local default
$vivado = if ($env:XILINX_VIVADO) { $env:XILINX_VIVADO } else { 'G:\AMDDesignTools\2026.1.1\Vivado' }
$xsdb = "$vivado\bin\xsdb.bat"
$py = 'env\venv\Scripts\python.exe'
foreach ($m in $args) {
    $bit = "hw\build\$m\fi_$m.bit"
    if (-not (Test-Path $bit)) { "MISSING $bit"; continue }
    $f0, $f1 = (& $py analysis\campaign_range.py $m | Select-Object -Last 1).Trim().Split()[0..1]
    $out = "results\camp_$m"
    New-Item -ItemType Directory -Force $out | Out-Null
    & $py analysis\skiplist.py $m $f0 $f1 0 100 "$out\skip.txt" | Select-Object -Last 1
    & $xsdb fi\program.tcl $bit 2>&1 | Select-Object -Last 1
    # 4096 test vectors, 1024 verify vectors, flags 4 = stop (and reprogram) on a persistent error
    & $xsdb fi\run_campaign.tcl $out $bit $f0 $f1 0 100 4096 1024 4 "$out\skip.txt" 2>&1 | Select-Object -Last 1
    # DWN: the parameter model's prediction for every table bit, compared bit by bit by the analysis
    if ((Get-Content "models\$m.json" -Raw | ConvertFrom-Json).type -eq 'dwn') {
        & $py analysis\sw_faults.py $m 2>&1 | Select-Object -Last 1
    }
    & $py analysis\analyze_campaign.py $m $out 2>&1 | Out-Null
    $a = Get-Content "$out\analysis.json" | ConvertFrom-Json
    "$m range $f0..$f1 injected $($a.injected) critical $($a.critical) param-exact $($a.dwn_lut_layer_hw_vs_sw.exact_agree)/$($a.dwn_lut_layer_hw_vs_sw.bits)"
}
