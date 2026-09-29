"""LUT-mode bits of the SLICEMs used by a DUT (they turn a logic LUT into SRL/LUTRAM and corrupt its
contents persistently, so they are injected separately with full reconfiguration).

  python analysis/skiplist.py <build name> <far_first> <far_last> <word_lo> <word_hi> <out file>
Output lines: "fi w b feature", sorted in campaign order.
"""
from pathlib import Path
import csv
import re
import sys
from cram_map import CramMap, load_farlist

ROOT = Path(__file__).resolve().parents[1]
MODE = re.compile(r"SLICEM_X0\.([A-D]LUT\.(SRL|RAM|SMALL)|WA7USED|WA8USED|WEMUX\.CE)$")


def main():
    """List the LUT-mode bits (SRL, LUT-RAM, wide-mux and write-enable settings) of every SLICEM that
    the DUT uses, in campaign order, so that run_campaign.tcl skips them."""
    name, f0, f1, w0, w1, out = sys.argv[1], *map(int, sys.argv[2:6]), sys.argv[6]
    m = CramMap()
    far_list, far_index, _ = load_farlist()
    used_tiles = set()
    with open(ROOT / f"hw/build/{name}/dut_cells.csv") as f:
        for r in csv.DictReader(f):
            if r["site"].startswith("SLICE_"):
                t = m.site_tile.get(r["site"])
                if t and m.tg[t]["sites"].get(r["site"]) == "SLICEM":
                    used_tiles.add(t)
    rows = []
    for tile in sorted(used_tiles):
        t = m.tg[tile]
        b = t["bits"]["CLB_IO_CLK"]
        base = int(b["baseaddr"], 16)
        fwd, _ = m.segbits(t["type"])
        for feat, bits in fwd.items():
            if not MODE.search(feat):
                continue
            for ff, bb, val in bits:
                far = base + ff
                word, bit = b["offset"] + bb // 32, bb % 32
                fi = far_index.get(far)
                if fi is None or not (f0 <= fi <= f1 and w0 <= word <= w1):
                    continue
                rows.append((fi, word, bit, feat))
    rows = sorted(set(rows))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text("".join(f"{a} {b} {c} {d}\n" for a, b, c, d in rows))
    print(f"{len(used_tiles)} used SLICEM tiles, {len(rows)} mode bits -> {out}")


if __name__ == "__main__":
    main()
