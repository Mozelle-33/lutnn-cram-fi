"""FAR-list index range covering a build's DUT pblock.

  python analysis/campaign_range.py <name>   -> prints "far_first far_last frames injections"
The pblock is SLICE_X<a>..X<b> x one or two clock regions; every frame column from the column
holding SLICE_X<a> to the column holding SLICE_X<b> (including BRAM/DSP interconnect columns in
between) is covered, i.e. every routing resource inside the pblock rectangle.
"""
from pathlib import Path
import json
import re
import sys

ROOT = Path(__file__).resolve().parents[1]


def dut_range(name):
    """Per clock-region row of the DUT pblock: (first, last) FAR-list index of the frame columns
    from the column holding its leftmost slice to the column holding its rightmost slice."""
    pb = (ROOT / f"hw/build/{name}/pblock.txt").read_text()
    x0, x1 = map(int, re.search(r"slice_x=(\d+)\.\.(\d+)", pb).groups())
    y0, y1 = map(int, re.search(r" y=(\d+)\.\.(\d+)", pb).groups())
    fl = json.loads((ROOT / "hw/gen/farlist.json").read_text())
    # FAR row of a top-half clock region X0Yk is k - 3 (X0Y4 -> row 1, X0Y5 -> row 2)
    want_rows = set(range(y0 // 50 - 3, y1 // 50 - 3 + 1))
    cols = [c for c in fl["columns"] if c["row"] in want_rows and c["half"] == 0]
    out = []
    for row in sorted(want_rows):
        rc = [c for c in cols if c["row"] == row]
        first = min(c["col"] for c in rc if c["slice_x"] and min(c["slice_x"]) >= x0 and min(c["slice_x"]) <= x1)
        last = max(c["col"] for c in rc if c["slice_x"] and max(c["slice_x"]) <= x1 and max(c["slice_x"]) >= x0)
        sel = [c for c in rc if first <= c["col"] <= last]
        out.append((min(c["first_index"] for c in sel), max(c["first_index"] + c["frames"] - 1 for c in sel)))
    return out


if __name__ == "__main__":
    for a, b in dut_range(sys.argv[1]):
        n = b - a + 1
        print(a, b, n, n * 101 * 32)
