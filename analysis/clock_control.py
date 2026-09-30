"""Clock-rate controls: the same configuration of a network injected twice at one clock rate and once at
a lower one.

  python analysis/clock_control.py            -> results/clock_control.json
  DWN-MNIST: 100 MHz (twice) and 50 MHz; the 50 MHz bitstream is the routed design with a clock manager
             inserted after the clock input buffer (hw/tcl/eco_clkdiv.tcl).
  DWN-M:     a build with a separate network clock (fi_top_dual.v, SEM controller at 100 MHz) at 200 MHz
             (twice) and at 100 MHz; the 100 MHz bitstream differs only in the divider of the clock manager
             (hw/tcl/eco_fastdiv.tcl).
In both cases the frames of the DUT region are identical. Critical bits that fail in both runs at the
higher rate but not at the lower one bound the failures that only a slowed route causes at the higher
rate; the two runs at the higher rate give the run-to-run variation. The change of the summed
mispredictions is broken down by the resource class and, for routing bits, by the DUT stage of the
net (route_attrib.py).
"""
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from cram_map import CramMap, load_farlist
from route_attrib import driver_group, net_group

ROOT = Path(__file__).resolve().parents[1]
CONTROLS = {
    "DWN-MNIST": {"build": "dwn_mnist_r", "high": 100, "low": 50,
                  "runs": {"high": "camp_dwn_mnist_r", "high repeat": "camp_dwn_mnist_r_rep",
                           "low": "camp_dwn_mnist_r_50"}},
    "DWN-M": {"build": "dwn_md_f200", "high": 200, "low": 100,
              "runs": {"high": "camp_dwn_md_f200", "high repeat": "camp_dwn_md_f200_rep",
                       "low": "camp_dwn_md_f200_100"}},
}


def events(cdir):
    """Mispredictions m_b of every critical bit (frame index, word, bit)."""
    ev = {}
    for line in (ROOT / "results" / cdir / "events.tsv").read_text().splitlines():
        fi, w, b, mm = map(int, line.split("\t")[:4])
        if mm > 0:
            ev[(fi, w, b)] = mm
    return ev


def compare(ctl, far_list, cm):
    ev = {k: events(c) for k, c in ctl["runs"].items()}
    nt = json.loads((ROOT / "results" / ctl["runs"]["high"] / "analysis.json").read_text())["ntest"]
    a, r, b = ev["high"], ev["high repeat"], ev["low"]
    res = {"clock_mhz": {"high": ctl["high"], "low": ctl["low"]}, "runs": {}}
    for k, e in ev.items():
        an = json.loads((ROOT / "results" / ctl["runs"][k] / "analysis.json").read_text())
        m = list(e.values())
        res["runs"][k] = {"campaign": ctl["runs"][k], "critical": len(m), "sum_mism": sum(m),
                          "severe": sum(x >= nt / 100 for x in m), "catastrophic": sum(x >= nt / 10 for x in m),
                          "golden_corr": an["golden_corr"], "param_exact": an["dwn_lut_layer_hw_vs_sw"]["exact_agree"],
                          "param_bits": an["dwn_lut_layer_hw_vs_sw"]["bits"]}
    lost = (set(a) & set(r)) - set(b)          # fail in both runs at the higher rate, silent at the lower
    gained = set(b) - (set(a) | set(r))        # silent in both runs at the higher rate, fail at the lower
    res["lost_at_low"] = {"bits": len(lost), "share_of_critical": len(lost) / len(a),
                          "max_mism": max((a[k] for k in lost), default=0), "sum_mism": sum(a[k] for k in lost)}
    res["gained_at_low"] = {"bits": len(gained), "max_mism": max((b[k] for k in gained), default=0)}
    res["net_change_critical"] = len(b) / len(a) - 1
    res["status_changes"] = {"high vs low": len(set(a) ^ set(b)), "high vs high repeat": len(set(a) ^ set(r))}
    res["sum_mism_change"] = {"high to low": sum(b.values()) / sum(a.values()) - 1,
                              "high to high repeat": sum(r.values()) / sum(a.values()) - 1}
    # where the summed mispredictions change: resource class, and DUT stage for routing bits
    pipnet = {}
    with open(ROOT / f"hw/build/{ctl['build']}/dut_pips.csv") as f:
        for row in csv.DictReader(f):
            m = re.match(r".*/[A-Z0-9_]+\.([A-Z0-9_\[\]]+)-+>+([A-Z0-9_\[\]]+)$", row["pip"])
            if m:
                pipnet[(row["tile"], m.group(2))] = driver_group(row.get("net_type", ""), row.get("driver", "-")) \
                    if "driver" in row else net_group(row["net"])
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
    return res


def main():
    far_list, _, _ = load_farlist()
    cm = CramMap()
    out = {}
    for name, ctl in CONTROLS.items():
        if all((ROOT / "results" / c / "analysis.json").exists() for c in ctl["runs"].values()):
            out[name] = compare(ctl, far_list, cm)
    (ROOT / "results/clock_control.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
