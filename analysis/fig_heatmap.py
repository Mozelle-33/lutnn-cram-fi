"""Spatial maps over a DUT pblock: used LUTs per CLB tile vs critical configuration bits per tile.

  python analysis/fig_heatmap.py <campaign dir> <build> <out.pdf>
Frame column -> x (order of the FAR columns inside the campaign range), word -> tile row
(two words per tile, word 50 is the HCLK row in the middle of the clock region).
"""
from pathlib import Path
import csv
import json
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from cram_map import CramMap, load_farlist

ROOT = Path(__file__).resolve().parents[1]
plt.rcParams.update({"font.size": 7, "font.family": "serif"})


def main():
    """Count used LUTs and critical bits per CLB tile of the pblock and draw both maps side by side;
    also print their per-tile correlation."""
    cdir, build, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    subs = [cdir] if (cdir / "summary.txt").exists() else sorted(d for d in cdir.iterdir() if (d / "summary.txt").exists())
    far_list, far_index, fl = load_farlist()
    cols = [c for c in fl["columns"]]
    colpos = {}
    for sub in subs[:1]:
        summ = dict(kv.split("=", 1) for kv in (sub / "summary.txt").read_text().split())
        f0, f1 = int(summ["far_first"]), int(summ["far_last"])
        sel = [c for c in cols if f0 <= c["first_index"] <= f1]
        for x, c in enumerate(sel):
            colpos[c["far_base"]] = x
    ncol = len(colpos)
    crit = np.zeros((50, ncol))
    for sub in subs[:1]:
        for line in (sub / "events.tsv").read_text().splitlines():
            fi, w, b, mm = map(int, line.split("\t")[:4])
            if mm <= 0:
                continue
            base = far_list[fi] & ~0x7F
            if base not in colpos:
                continue
            row = w // 2 if w < 50 else (w - 1) // 2
            crit[min(row, 49), colpos[base]] += 1
    m = CramMap()
    used = np.zeros((50, ncol))
    y0 = None
    with open(ROOT / f"hw/build/{build}/dut_cells.csv") as f:
        rows = [r for r in csv.DictReader(f) if r["ref"].startswith("LUT") and r["site"].startswith("SLICE")]
    for r in rows:
        t = m.tg[m.site_tile[r["site"]]]
        base = int(t["bits"]["CLB_IO_CLK"]["baseaddr"], 16)
        if base not in colpos:
            continue
        off = t["bits"]["CLB_IO_CLK"]["offset"]
        row = off // 2 if off < 50 else (off - 1) // 2
        used[row, colpos[base]] += 1
    fig, axs = plt.subplots(1, 2, figsize=(3.45, 1.9), sharey=True)
    for ax, data, title, cmap in ((axs[0], used, "Used LUTs / tile", "Blues"), (axs[1], crit, "Critical bits / tile", "Oranges")):
        im = ax.imshow(data, origin="lower", aspect="auto", cmap=cmap, interpolation="nearest")
        ax.set_title(title, fontsize=7)
        ax.set_xlabel("Frame column")
        cb = fig.colorbar(im, ax=ax, fraction=0.06, pad=0.02)
        cb.ax.tick_params(labelsize=5)
    axs[0].set_ylabel("CLB row")
    fig.tight_layout(pad=0.3)
    fig.savefig(out)
    corr = np.corrcoef(used.ravel(), crit.ravel())[0, 1]
    print(f"{out}: columns {ncol}, total critical {crit.sum():.0f}, LUTs {used.sum():.0f}, corr(used, critical) = {corr:.2f}")


if __name__ == "__main__":
    main()
