# Exhaustive configuration-memory fault injection of LUT-native neural networks

This repository contains the platform, models, scripts and measurement data of

> Y. Guo, "Beyond Parameter Bit-Flips: Exhaustive Configuration-Memory Fault Injection of
> LUT-Native Neural Networks on SRAM FPGAs," submitted to *IEEE Transactions on Very Large Scale
> Integration (VLSI) Systems*, 2026.

On an AMD/Xilinx Kintex-7 XC7K325T, an autonomous on-chip injector built around the Soft Error
Mitigation (SEM) controller flips, tests, restores and verifies **every** configuration-memory
(CRAM) bit of the region that holds a neural network, at about 10,000 injections per second. The
study covers differentiable weightless networks (DWNs), differentiable logic-gate networks (DLGNs)
and an iso-accuracy fixed-point MLP on the JSC and MNIST benchmarks (41 M injections), and adds:

* bit-level attribution of every critical bit to its fabric resource and net (Project X-Ray database),
* the exactness of the parameter bit-flip model for the LUT tables, and the test-set coverage of
  critical-bit counts,
* accumulated-upset experiments in hardware versus the parameter bit-flip model,
* a functional model of interconnect-multiplexer upsets (a disconnected multiplexer freezes, a
  doubly selected one forms a wired-AND, an undriven wire acts as logic one), validated bit by bit,
* hardening measured exhaustively: don't-care filling, fault-aware training, selective TMR.

## Layout

| Directory | Contents |
|---|---|
| `hw/rtl` | Injection platform: `fi_ctrl.v` (autonomous campaign controller), `jtag_regs.v` (BSCANE2 register file), `fi_top.v` (top level with the SEM controller) |
| `hw/xdc` | Board constraints (NetFirm-4E40-C card with XC7K325T-FFG900-2; adapt the pins for other boards) |
| `hw/tcl` | Vivado scripts: SEM IP generation, build with isolated pblocks and essential bits, exports for the analysis |
| `hw/sim` | RTL simulation of a generated network against its software reference |
| `hw/gen` | Generated networks (`dut.v`), test vectors, software reference outputs, frame-address list |
| `fi` | Host scripts for `xsdb`: programming, campaigns, targeted and multiple upsets, timing, recovery |
| `train` | Data preparation, models with bit-exact integer references, training, network generation |
| `analysis` | CRAM bit mapping, campaign analysis, routing attribution, coverage, accumulated upsets, multiplexer fault models, figures |
| `models` | Trained models as JSON specs (tables, mappings, gates, integer weights) |
| `results` | Campaign events and summaries, analysis results, accumulated-upset trials |

## Requirements

* Vivado 2026.1 (SEM IP v4.1, `xsdb`, `hw_server`). Set `XILINX_VIVADO` to the installation
  directory, or edit the default path at the top of the PowerShell scripts.
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

`train/pipeline_models.ps1` and `fi/run_campaigns.ps1` chain these steps for several models. The
other experiments have their own scripts: accumulated upsets (`analysis/multi_upset.py`,
`fi/multi_upset.tcl`), multiplexer fault models (`hw/tcl/export_lutpins.tcl`,
`hw/tcl/export_routetree.tcl`, `analysis/imux_model.py`, `analysis/route_model.py`), injection
timing (`fi/throughput.tcl`), frozen-input hold test (`fi/hold_test.tcl`), test-set coverage
(`analysis/test_coverage.py`). Each script documents its usage in its header.

## Data formats

* `results/camp_*/events.tsv`: one line per non-silent bit: frame index into
  `hw/gen/farlist.mem`, word, bit, mispredictions (of 4096 test vectors; 2048 for MNIST), correct
  classifications, flags, session. Bits without a line were silent.
* `results/camp_*/summary.txt`: campaign parameters and counters (injections, skipped mode bits,
  golden accuracy, duration). Split campaigns keep one sub-directory (`r1`, `r2`) per part.
* `results/camp_*/analysis.json`: per-resource statistics, essential bits, FIT, and the
  hardware-versus-software check of every table bit.

## License

Code: MIT (see `LICENSE`). Measurement data in `results/`: CC BY 4.0.
