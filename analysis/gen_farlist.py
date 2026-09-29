"""Frame address list for the fault-injection region (prjxray-db xc7k325t tilegrid).

  python analysis/gen_farlist.py [--regions X0Y4 X0Y5]
Writes hw/gen/farlist.mem (25-bit FAR per line, hex) and hw/gen/farlist.json:
  frames: [{far, col, row, half, minor}], columns: [{far_base, frames, types, regions, slice_x}]
Frames are ordered by (half, row, column, minor), i.e. every column's frames are contiguous, so
a pblock covering a set of adjacent columns maps to one contiguous index range of the list.
"""
from pathlib import Path
import argparse
import json
import re
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]
TG = ROOT / "tools/prjxray-db/kintex7/xc7k325t/tilegrid.json"


def far_fields(a):
    """7-series frame address: block type, top/bottom half, row, column, minor (frame in column)."""
    return {"bt": (a >> 23) & 7, "half": (a >> 22) & 1, "row": (a >> 17) & 31, "col": (a >> 7) & 1023, "minor": a & 127}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--regions", nargs="+", default=["X0Y4", "X0Y5"])
    a = ap.parse_args()
    tg = json.loads(TG.read_text())
    # one entry per frame column (base address): the tiles stacked in a column share its frames.
    # A campaign covers the columns of its DUT pblock only (analysis/campaign_range.py).
    cols = defaultdict(lambda: {"frames": 0, "types": set(), "regions": set(), "slice_x": set()})
    for name, t in tg.items():
        if t.get("clock_region") not in a.regions:
            continue
        b = t["bits"].get("CLB_IO_CLK")
        if not b:
            continue
        base = int(b["baseaddr"], 16)
        c = cols[base]
        c["frames"] = max(c["frames"], b["frames"])
        c["types"].add(t["type"])
        c["regions"].add(t["clock_region"])
        for s in t["sites"]:
            m = re.match(r"SLICE_X(\d+)Y\d+", s)
            if m:
                c["slice_x"].add(int(m.group(1)))
    order = sorted(cols, key=lambda v: (far_fields(v)["half"], far_fields(v)["row"], far_fields(v)["col"]))
    frames, columns = [], []
    for base in order:
        c = cols[base]
        first = len(frames)
        for m in range(c["frames"]):
            f = base + m
            frames.append({"far": f, **far_fields(f)})
        columns.append({"far_base": base, "first_index": first, "frames": c["frames"],
                        "types": sorted(c["types"]), "regions": sorted(c["regions"]),
                        "slice_x": sorted(c["slice_x"]), **far_fields(base)})
    out = ROOT / "hw" / "gen"
    out.mkdir(parents=True, exist_ok=True)
    (out / "farlist.mem").write_text("".join(f"{f['far'] & 0x1FFFFFF:07X}\n" for f in frames))
    (out / "farlist.json").write_text(json.dumps({"regions": a.regions, "frames": frames, "columns": columns}))
    ncl = sum(1 for c in columns if any(t.startswith("CLBL") for t in c["types"]))
    print(f"{len(frames)} frames in {len(columns)} columns ({ncl} CLB columns) for {a.regions}")
    for c in columns[:6]:
        print(c["far_base"], c["frames"], c["types"], c["slice_x"])


if __name__ == "__main__":
    main()
