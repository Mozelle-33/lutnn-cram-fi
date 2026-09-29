"""Calibration plan: inject chosen DWN LUT-INIT bits and compare with the software prediction.

  python analysis/make_calib_plan.py <name> [--n 24]
Uses hw/build/<name>/dut_cells.csv (placement), results/sw_single_flips_<name>.npz and writes
results/calib_<name>.plan: addr_hex, lut j, entry k, far, word, bit, sw_mismatches, sw_correct.
"""
from pathlib import Path
import argparse
import csv
import numpy as np
from cram_map import CramMap, sem_pfa

ROOT = Path(__file__).resolve().parents[1]


def main():
    """Pick table bits with a spread of predicted effects (including silent ones) and write their SEM
    addresses with the software prediction, for fi/calibrate.tcl."""
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--n", type=int, default=24)
    a = ap.parse_args()
    cells = {}
    with open(ROOT / f"hw/build/{a.name}/dut_cells.csv") as f:
        for r in csv.DictReader(f):
            if r["cell"].startswith("u_dut/lut_l0_"):
                cells[int(r["cell"].rsplit("_", 1)[1])] = (r["site"], r["bel"])
    d = np.load(ROOT / f"results/sw_single_flips_{a.name}.npz")
    mism, corr = d["mism"], d["corr"]
    rng = np.random.default_rng(1)
    crit = np.argwhere(mism > 0)
    order = crit[np.argsort(-mism[crit[:, 0], crit[:, 1]])]
    picks, used_j = [], set()
    for j, k in order:                              # strongest effects, distinct LUTs
        if j not in used_j:
            picks.append((int(j), int(k))); used_j.add(j)
        if len(picks) >= a.n // 2:
            break
    for j, k in crit[rng.choice(len(crit), a.n // 4, replace=False)]:   # random critical entries
        picks.append((int(j), int(k)))
    silent = np.argwhere(mism == 0)
    for j, k in silent[rng.choice(len(silent), a.n - len(picks), replace=False)]:
        picks.append((int(j), int(k)))
    m = CramMap()
    out = ROOT / f"results/calib_{a.name}.plan"
    with open(out, "w") as f:
        for j, k in picks:
            site, bel = cells[j]
            far, word, bit, val, feat = m.lut_init_address(site, bel, k)
            f.write(f"{sem_pfa(far, word, bit):010X}\t{j}\t{k}\t{far}\t{word}\t{bit}\t{mism[j, k]}\t{corr[j, k]}\t{site}/{bel}\n")
    print(out, len(picks), "entries")


if __name__ == "__main__":
    main()
