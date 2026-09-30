# Exhaustive configuration-memory fault injection of LUT-native neural networks

This repository contains the platform, models, scripts and measurement data of

> Y. Guo, "Beyond Parameter Bit-Flips: Exhaustive Configuration-Memory Fault Injection of
> LUT-Native Neural Networks on SRAM FPGAs," submitted to *IEEE Transactions on Very Large Scale
> Integration (VLSI) Systems*, 2026.

On an AMD/Xilinx Kintex-7 XC7K325T, an autonomous on-chip injector built around the Soft Error
Mitigation (SEM) controller flips, tests, restores and verifies **every** configuration-memory
(CRAM) bit of the region that holds a neural network, at up to 10,000 injections per second
(5,900–8,400 averaged over complete campaigns). The study covers differentiable weightless networks
(DWNs), differentiable logic-gate networks (DLGNs) and fixed-point MLPs of similar accuracy (dense
and 70 % pruned) on the JSC and MNIST benchmarks (39 exhaustive campaigns, 137.7 M injections), and
adds:

* bit-level attribution of every critical bit to its fabric resource and net (Project X-Ray database),
* the exactness of the parameter bit-flip model for the LUT tables (1.68 M table bits in 25 builds),
  and the test-set coverage of critical-bit counts,
* the persistent upsets of the LUT-mode bits (shift register, LUT-RAM), injected exhaustively with
  reconfiguration after every persistent error, and a SLICEL-only placement that removes them,
* accumulated-upset experiments in hardware versus the parameter bit-flip model,
* a functional model of interconnect-multiplexer upsets (a disconnected multiplexer freezes at its
  value at the time of the upset, a doubly selected one forms a wired-AND, an undriven wire acts as
  logic one, the unused output of a used LUT carries its value), validated bit by bit, with
  second exhaustive campaigns at another idle input vector (unhardened and fault-aware DWN-M, three
  training runs each), a model sweep over all idle vectors and a hold test of frozen values,
* clock-rate controls: DWN-MNIST, whose maximum clock rate is closest to the 100 MHz of the
  campaigns, injected again at 100 MHz and at 50 MHz with an unchanged configuration of the network;
  DWN-M implemented for a 200 MHz network clock (the SEM controller keeps its own 100 MHz clock),
  injected twice at 200 MHz and once, with the same configuration, at 100 MHz,
* hardening measured exhaustively over three training runs per variant: don't-care filling,
  fault-aware training (inverted and physically modelled input faults), selective TMR, alone and
  combined with either kind of fault-aware training, SLICEL-only placement. Two exploratory single runs (fault-aware 5 %, physical 2 %) and a first DWN-MNIST
  campaign on the first 2048 test images (replaced in the paper by a random sample of 2048 images)
  are included in `results/` but not reported in the paper.

## Layout

| Directory | Contents |
|---|---|
| `hw/rtl` | Injection platform: `fi_ctrl.v` (autonomous campaign controller), `jtag_regs.v` (BSCANE2 register file), `fi_top.v` (top level with the SEM controller), `fi_top_dual.v` (the same with a separate, faster clock for the controller and the network) |
| `hw/xdc` | Board constraints (NetFirm-4E40-C card with XC7K325T-FFG900-2; adapt the pins for other boards) |
| `hw/tcl` | Vivado scripts: SEM IP generation, build with isolated pblocks and essential bits, exports for the analysis, post-route timing of the network (`dut_timing.tcl`) |
| `hw/sim` | RTL simulation of a generated network against its software reference |
| `hw/gen` | Generated networks (`dut.v`), test vectors, software reference outputs, frame-address list |
| `fi` | Host scripts for `xsdb`: programming, campaigns, targeted and multiple upsets, timing, recovery |
| `train` | Data preparation, models with bit-exact integer references, training, network generation |
| `analysis` | CRAM bit mapping, campaign analysis, routing attribution, coverage, accumulated upsets, multiplexer fault models, figures |
| `models` | Trained models as JSON specs (tables, mappings, gates, integer weights) |
| `results` | Campaign events and summaries, analysis results, accumulated-upset trials |

## Requirements

* Vivado 2026.1 (SEM IP v4.1, `xsdb`, `hw_server`). The PowerShell scripts need `XILINX_VIVADO`
  set to the installation directory (`settings64.bat` sets it).
* A board with an XC7K325T-FFG900-2 reachable over JTAG.
* Python 3.12 with the packages in `requirements.txt` (a CUDA build of PyTorch speeds up training).
* The Project X-Ray database for the XC7K325T (openXC7 fork,
  <https://github.com/openXC7/prjxray-db>), checked out as `tools/prjxray-db`.
* The scripts were run on Windows (PowerShell); the Python and Tcl code is platform independent.

## Reproducing a campaign (DWN-M as an example)

```
python train/data_jsc.py                                            # JSC, 10-bit inputs
python train/train.py dwn --name dwn_md --luts 1000 --tbits 200 --epochs 30
python train/gen_dut.py dwn_md                                      # hw/gen/dwn_md
powershell -File hw/sim/run_dut_sim.ps1 dwn_md                      # RTL vs. software: 0 mismatches
python analysis/gen_farlist.py                                      # frame-address list
vivado -mode batch -source hw/tcl/gen_sem_ip.tcl                    # once
vivado -mode batch -source hw/tcl/build.tcl -tclargs dwn_md 8 160 2 1 auto
hw_server -s tcp::3121                                              # in another terminal
xsdb fi/program.tcl hw/build/dwn_md/fi_dwn_md.bit                   # configure the FPGA over JTAG
python analysis/campaign_range.py dwn_md                            # -> far_first far_last
python analysis/skiplist.py dwn_md 72 967 0 100 results/camp_dwn_md/skip.txt
xsdb fi/run_campaign.tcl results/camp_dwn_md hw/build/dwn_md/fi_dwn_md.bit 72 967 0 100 4096 1024 4 results/camp_dwn_md/skip.txt
python analysis/analyze_campaign.py dwn_md results/camp_dwn_md
python analysis/make_figures.py
```

`train/pipeline_models.ps1` and `fi/run_campaigns.ps1` chain these steps for several models (a DUT
over two clock regions, like the MLPs, is injected as two sub-campaigns `r1`, `r2`); the
SLICEL-only build uses the optional `slices` argument `slicel` of `hw/tcl/build.tcl`, the pruned MLP
`train/train.py --prune 0.7` and `fi/run_pruned_mlp.ps1`;
`fi/board_queue.ps1` runs campaigns as their builds finish and injects the LUT-mode bits of a design
in between, and resumes after an interruption (as does `train/train.py`, which checkpoints every
epoch). The other experiments have their own scripts: LUT-mode bits (`fi/inject_list.tcl`,
`analysis/mode_bits.py`), accumulated upsets (`analysis/multi_upset.py`, `fi/multi_upset.tcl`),
multiplexer fault models (`hw/tcl/export_lutpins.tcl`, `hw/tcl/export_routetree.tcl`,
`analysis/imux_model.py`, `analysis/route_model.py` with `--clbout 1` for the unused CLB outputs,
`analysis/freeze_polarity.py`), frozen-value controls (`fi/idle_control.tcl`,
`analysis/idle_control.py`; whole-region campaigns with the verify length set to another vector
count, `FI_VERIFY=1479 FI_SUFFIX=_idle1479 fi/run_campaigns.ps1 ...`, compared by
`analysis/idle_compare.py` and `analysis/state_hardening.py`; model sweep over all idle vectors
`analysis/state_sweep.py`), clock-rate controls (`hw/tcl/dut_timing.tcl` for the maximum clock
rates, `hw/tcl/eco_clkdiv.tcl` for the 50 MHz bitstream, `FI_SUFFIX=_rep fi/run_campaigns.ps1
dwn_mnist_r:dwn_mnist` for the repeat; the 200 MHz build with `hw/rtl/fi_top_dual.v` and
`hw/xdc/fi_dual.xdc`, selected by the last argument of `hw/tcl/build.tcl`,
`build.tcl ... dwn_md_f200 8 160 40 1 auto 200 dwn_md 3 4096 all 5`, its 100 MHz variant with
`hw/tcl/eco_fastdiv.tcl dwn_md_f200 dwn_md_f200_100 10`; `analysis/clock_control.py`), injection timing
(`fi/throughput.tcl`), frozen-input hold test
(`analysis/freeze_hold.py`, `analysis/hold_stage.py`, `fi/hold_test.tcl`, die temperature
`fi/read_temp.tcl`), critical bits by network stage (`analysis/stage_breakdown.py`), common-mode
failures of the TMR (`analysis/tmr_cmf.py`), effective rate of the physical training faults
(`analysis/pf_effective.py`), don't-care filling (`analysis/dontcare_fill.py`,
`hw/tcl/reinit_luts.tcl`), test-set coverage and sampling spread (`analysis/test_coverage.py`,
`analysis/sample_spread.py`; a random test sample is generated with `train/gen_dut.py
--sample_seed`). Each script documents its usage in its header.

## Data formats

* `results/camp_*/events.tsv`: one line per non-silent bit: frame index into
  `hw/gen/farlist.mem`, word, bit, mispredictions (of 4096 test vectors; 2048 for MNIST), correct
  classifications, flags, session. Bits without a line were silent.
* `results/camp_*/summary.txt`: campaign parameters and counters (injections, skipped mode bits,
  golden accuracy, duration). Split campaigns keep one sub-directory (`r1`, `r2`) per part.
* `results/camp_*/analysis.json`: per-resource statistics, essential bits, FIT, and the
  hardware-versus-software check of every table bit.
* `results/mode_dwn_md_all.tsv`: every LUT-mode bit of DWN-M: frame index, word, bit,
  mispredictions, correct classifications, mispredictions after the restore, persistent flag,
  feature; summarised in `results/mode_bits_dwn_md.json`.
* `results/hardening_stats.json`, `results/composition_seeds.json`, `results/campaign_totals.json`:
  statistics of the hardening variants over the training runs, the per-run composition check of
  fault-aware training and TMR, and the totals of all campaigns.
* `results/dut_timing.tsv`, `results/dut_timing_f200.tsv`, `results/clock_control.json`: maximum
  clock rate of each network and the comparison of the DWN-MNIST campaigns at 100 MHz (two runs) and
  50 MHz and of the DWN-M campaigns at 200 MHz (two runs) and 100 MHz.

## License

Code: MIT (see `LICENSE`). Measurement data in `results/`: CC BY 4.0.
