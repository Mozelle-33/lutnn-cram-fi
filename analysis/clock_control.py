"""Clock-rate control: DWN-MNIST injected at 100 MHz twice and at 50 MHz with the same DUT configuration.

  python analysis/clock_control.py            -> results/clock_control.json
The 50 MHz bitstream is the routed 100 MHz design with a clock manager inserted after the clock input
buffer (hw/tcl/eco_clkdiv.tcl); the frames of the DUT region are identical. Critical bits that fail in
both 100 MHz runs but not at 50 MHz bound the failures that only a slowed route causes at 100 MHz; the
two 100 MHz runs give the run-to-run variation. The change of the summed mispredictions is broken
down by the resource class and, for routing bits, by the DUT stage of the net (route_attrib.py).
"""
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from cram_map import CramMap, load_farlist
from route_attrib import driver_group, net_group

ROOT = Path(__file__).resolve().parents[1]
RUNS = {"100": "camp_dwn_mnist_r", "100 repeat": "camp_dwn_mnist_r_rep", "50": "camp_dwn_mnist_r_50"}
BUILD = "dwn_mnist_r"


def events(cdir):
    """Mispredictions m_b of every critical bit (frame index, word, bit)."""
    ev = {}
    for line in (ROOT / "results" / cdir / "events.tsv").read_text().splitlines():
        fi, w, b, mm = map(int, line.split("\t")[:4])
        if mm > 0:
            ev[(fi, w, b)] = mm
    return ev


def main():
    ev = {k: events(c) for k, c in RUNS.items()}
    nt = json.loads((ROOT / "results" / RUNS["100"] / "analysis.json").read_text())["ntest"]
    a, r, b = ev["100"], ev["100 repeat"], ev["50"]
    res = {"runs": {}}
    for k, e in ev.items():
        an = json.loads((ROOT / "results" / RUNS[k] / "analysis.json").read_text())
        m = list(e.values())
        res["runs"][k] = {"campaign": RUNS[k], "critical": len(m), "sum_mism": sum(m),
                          "severe": sum(x >= nt / 100 for x in m), "catastrophic": sum(x >= nt / 10 for x in m),
                          "param_exact": an["dwn_lut_layer_hw_vs_sw"]["exact_agree"],
                          "param_bits": an["dwn_lut_layer_hw_vs_sw"]["bits"]}
    lost = (set(a) & set(r)) - set(b)          # fail in both 100 MHz runs, silent at 50 MHz
    gained = set(b) - (set(a) | set(r))        # silent in both 100 MHz runs, fail at 50 MHz
    res["lost_at_50"] = {"bits": len(lost), "share_of_critical": len(lost) / len(a),
                         "max_mism": max(a[k] for k in lost), "sum_mism": sum(a[k] for k in lost)}
    res["gained_at_50"] = {"bits": len(gained)}
    res["net_change_critical"] = len(b) / len(a) - 1
    res["status_changes"] = {"100 vs 50": len(set(a) ^ set(b)), "100 vs 100 repeat": len(set(a) ^ set(r))}
    res["sum_mism_change"] = {"100 vs 50": sum(b.values()) / sum(a.values()) - 1,
                              "100 vs 100 repeat": sum(r.values()) / sum(a.values()) - 1}
    # where the summed mispredictions change: resource class, and DUT stage for routing bits
    pipnet = {}
    with open(ROOT / f"hw/build/{BUILD}/dut_pips.csv") as f:
        for row in csv.DictReader(f):
            m = re.match(r".*/[A-Z0-9_]+\.([A-Z0-9_\[\]]+)-+>+([A-Z0-9_\[\]]+)$", row["pip"])
            if m:
                pipnet[(row["tile"], m.group(2))] = driver_group(row.get("net_type", ""), row.get("driver", "-")) \
                    if "driver" in row else net_group(row["net"])
    far_list, _, _ = load_farlist()
    cm = CramMap()
    dm = defaultdict(int)
    for k in set(a) | set(b):
        d = b.get(k, 0) - a.get(k, 0)
        if d:
            c, tile, feats = cm.classify(far_list[k[0]], k[1], k[2])
            if c == "ROUTING":
                nets = {pipnet.get((tile, ft.split(".")[1])) for ft, _ in feats} - {None}
                c = "routing: " + (sorted(nets)[0] if nets else "unused wire")
            dm[c] += d
    res["sum_mism_change_by_group"] = dict(Counter(dm).most_common())
    (ROOT / "results/clock_control.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
