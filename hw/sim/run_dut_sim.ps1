# Simulate hw/gen/<name>/dut.v against its software reference with xsim.
# Usage: powershell -File hw/sim/run_dut_sim.ps1 <name>
param([Parameter(Mandatory = $true)][string]$name)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)     # repository root (script is in hw\sim)
# Vivado installation: $env:XILINX_VIVADO if set (e.g. by settings64.bat), else the local default
$vivado = if ($env:XILINX_VIVADO) { $env:XILINX_VIVADO } else { throw 'Set XILINX_VIVADO to the Vivado installation directory (settings64.bat sets it)' }
$bin = Join-Path $vivado 'bin'
$gen = Join-Path $root "hw\gen\$name"
$meta = Get-Content (Join-Path $gen 'meta.json') | ConvertFrom-Json
$work = Join-Path $gen 'xsim'
New-Item -ItemType Directory -Force $work | Out-Null
Set-Location $work
Copy-Item (Join-Path $gen 'vectors.mem') . -Force
Copy-Item (Join-Path $gen 'golden_sw.txt') golden_sw.hex -Force
$glbl = Join-Path $vivado 'data\verilog\src\glbl.v'    # global set/reset module needed by UNISIM primitives
& "$bin\xvlog.bat" (Join-Path $gen 'dut.v') (Join-Path $root 'hw\sim\tb_dut.v') $glbl *> xvlog.out
if ($LASTEXITCODE -ne 0) { Get-Content xvlog.out | Select-String -Pattern 'ERROR' | Select-Object -First 20; exit 1 }
# .bat wrappers split arguments at '=', so generics go through an option file
$cw = if ($meta.CW) { $meta.CW } else { 3 }
@("-debug off", "-L unisims_ver", "-generic_top IN_W=$($meta.IN_W)", "-generic_top LATENCY=$($meta.LATENCY)",
  "-generic_top NVEC=$($meta.NVEC)", "-generic_top CW=$cw", "tb_dut glbl", "-s tb_snap") | Set-Content -Encoding ascii xelab_opts.txt
& "$bin\xelab.bat" -f xelab_opts.txt *> xelab.out
if ($LASTEXITCODE -ne 0) { Get-Content xelab.out | Select-String -Pattern 'ERROR' | Select-Object -First 20; exit 1 }
& "$bin\xsim.bat" tb_snap -R *> xsim.out
Get-Content xsim.out | Select-String -Pattern 'RESULT|MISMATCH|ERROR|Fatal'
