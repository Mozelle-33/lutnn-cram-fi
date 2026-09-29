"""Exhaustive injection of the LUT-mode bits (fi/inject_list.tcl with the mode-bit skip list of a
campaign, full reconfiguration after every persistent error): outcome per mode-bit feature, split by
whether the LUT that the bit configures is used by the DUT.

  python analysis/mode_bits.py <build> <result tsv> [<test vectors>]
Result lines: fi w b mism corr verify_mism persistent feature (verify: the full test set run again
after the bit was restored). Writes results/mode_bits_<build>.json.
"""
from pathlib import Path
from collections import defaultdict
import csv
import json
import re
import sys
from cram_map import CramMap, load_farlist

ROOT = Path(__file__).resolve().parents[1]
MODE = re.compile(r"SLICEM_X0\.(([A-D])LUT\.(SRL|RAM|SMALL)|WA7USED|WA8USED|WEMUX\.CE)$")


def main():
    build, tsv = sys.argv[1], sys.argv[2]
    ntest = int(sys.argv[3]) if len(sys.argv) > 3 else 4096
    m = CramMap()
    _, far_index, _ = load_farlist()
    # SLICEM tiles with DUT cells, and which of their four LUT positions (A-D) the DUT uses
    tiles, luts = set(), defaultdict(set)
    with open(ROOT / f"hw/build/{build}/dut_cells.csv") as f:
        for r in csv.DictReader(f):
            t = m.site_tile.get(r["site"]) if r["site"].startswith("SLICE_") else None
            if t and m.tg[t]["sites"].get(r["site"]) == "SLICEM":
                tiles.add(t)
                if re.fullmatch(r"SLICEM\.[A-D][56]LUT", r["bel"]):
                    luts[t].add(r["bel"][7])
    # CRAM address of every mode bit of these tiles -> (tile, feature), as in skiplist.py
    where = {}
    for tile in tiles:
        t = m.tg[tile]
        b = t["bits"]["CLB_IO_CLK"]
        base = int(b["baseaddr"], 16)
        fwd, _ = m.segbits(t["type"])
        for feat, bits in fwd.items():
            if MODE.search(feat):
                for ff, bb, _val in bits:
                    where[(far_index.get(base + ff), b["offset"] + bb // 32, bb % 32)] = (tile, feat)

    stats = defaultdict(lambda: defaultdict(int))
    mism_sum = defaultdict(int)
    for line in Path(tsv).read_text().splitlines():
        f = line.split("\t")
        fi, w, bit, mism, corr, vmism, pers = map(int, f[:7])
        tile, feat = where[(fi, w, bit)]
        g = MODE.search(feat)
        kind = g.group(3) or g.group(1)                    # SRL / RAM / SMALL / WA7USED / ...
        # a LUT setting counts as "used" if the DUT uses that LUT, a slice-wide one if it uses any
        used = (g.group(2) in luts[tile]) if g.group(2) else bool(luts[tile])
        for key in (f"{kind}|{'used' if used else 'unused'}", "all"):
            s = stats[key]
            s["bits"] += 1
            s["critical"] += mism > 0
            s["severe"] += mism >= ntest / 100
            s["catastrophic"] += mism >= ntest / 10
            s["persistent"] += pers
            s["persistent_silent_in_test"] += pers and mism == 0
            s["recovered_by_restore"] += mism > 0 and not pers
            mism_sum[key] += mism
    out = {}
    for key, s in sorted(stats.items()):
        s = dict(s)
        s["mean_mism_critical"] = mism_sum[key] / s["critical"] if s["critical"] else 0.0
        out[key] = s
    res = {"build": build, "slicem_tiles": len(tiles), "used_luts_in_slicems": sum(len(v) for v in luts.values()),
           "test_vectors": ntest, "by_feature": out}
    (ROOT / f"results/mode_bits_{build}.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
