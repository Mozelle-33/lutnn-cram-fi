# Exhaustive campaign + analysis for each listed build.
# Usage: powershell -File fi\run_campaigns.ps1 <build>[:<model>] ...
#   The model defaults to the build name; a triplicated-output build names its model explicitly,
#   e.g. dwn_md_tmr_s1:dwn_md_s1.
# For each build: FAR range of its pblock, LUT-mode skip list, program, campaign, analysis.
Set-Location (Split-Path -Parent $PSScriptRoot)          # repository root
# Vivado installation: $env:XILINX_VIVADO if set (e.g. by settings64.bat), else the local default
$vivado = if ($env:XILINX_VIVADO) { $env:XILINX_VIVADO } else { throw 'Set XILINX_VIVADO to the Vivado installation directory (settings64.bat sets it)' }
$xsdb = "$vivado\bin\xsdb.bat"
$py = 'env\venv\Scripts\python.exe'
foreach ($a in $args) {
    $b, $m = $a.Split(':')
    if (-not $m) { $m = $b }
    $bit = "hw\build\$b\fi_$b.bit"
    if (-not (Test-Path $bit)) { "MISSING $bit"; continue }
    # one FAR range per clock-region row of the pblock; a DUT over two rows (the MLPs) is covered by
    # two sub-campaigns r1, r2, which the analysis scripts merge
    $ranges = @(& $py analysis\campaign_range.py $b | Where-Object { $_ -match '^\s*\d+\s+\d+' })
    $out = "results\camp_$b"
    New-Item -ItemType Directory -Force $out | Out-Null
    $i = 0
    foreach ($rg in $ranges) {
        $i++
        $f0, $f1 = $rg.Trim().Split()[0..1]
        $sub = if ($ranges.Count -gt 1) { "$out\r$i" } else { $out }
        New-Item -ItemType Directory -Force $sub | Out-Null
        & $py analysis\skiplist.py $b $f0 $f1 0 100 "$sub\skip.txt" | Select-Object -Last 1
        & $xsdb fi\program.tcl $bit 2>&1 | Select-Object -Last 1
        # 4096 test vectors, 1024 verify vectors, flags 4 = stop (and reprogram) on a persistent error
        & $xsdb fi\run_campaign.tcl $sub $bit $f0 $f1 0 100 4096 1024 4 "$sub\skip.txt" 2>&1 | Select-Object -Last 1
    }
    $f0, $f1 = $ranges[0].Trim().Split()[0..1]
    # DWN: the parameter model's prediction for every table bit, compared bit by bit by the analysis
    if ((Get-Content "models\$m.json" -Raw | ConvertFrom-Json).type -eq 'dwn' -and -not (Test-Path "results\sw_single_flips_$m.npz")) {
        & $py analysis\sw_faults.py $m 2>&1 | Select-Object -Last 1
    }
    & $py analysis\analyze_campaign.py $m $out $b 2>&1 | Out-Null
    $an = Get-Content "$out\analysis.json" | ConvertFrom-Json
    "$b ($m) rows $($ranges.Count) first range $f0..$f1 injected $($an.injected) critical $($an.critical) param-exact $($an.dwn_lut_layer_hw_vs_sw.exact_agree)/$($an.dwn_lut_layer_hw_vs_sw.bits)"
}
