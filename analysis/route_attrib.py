"""Attribute critical routing bits to the nets (and DUT stages) whose routing they configure.

  python analysis/route_attrib.py <model> <campaign dir> [build]
A routing bit belongs to the input multiplexer of one destination wire (prjxray feature
TILE_TYPE.DST.SRC); the net that uses that wire in that tile (from hw/build/<build>/dut_pips.csv)
is the one an upset re-routes or opens. Writes <campaign dir>/route_attrib.json.
"""
from pathlib import Path
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from cram_map import CramMap, load_farlist

ROOT = Path(__file__).resolve().parents[1]


def driver_group(net_type, driver, last_layer=0):
    """Stage of the DUT a routed net belongs to, from its type and driver cell. The registered outputs
    o<k>_r of LUT layer k feed the next LUT layer (learned connectivity) unless k is the last layer,
    whose outputs feed the population count (fixed wiring)."""
    if net_type in ("GROUND", "POWER"):
        return "constant"
    if net_type == "GLOBAL_CLOCK" or "BUFG" in driver:
        return "clock"
    if not driver.startswith("u_dut/"):
        return "inputs"                      # driven by the harness (dut_x)
    c = driver[len("u_dut/"):]
    if re.match(r"x_r", c):
        return "encoder"                     # input register -> comparators
    if re.match(r"t_r", c):
        return "thermometer->LUT" if "_reg" in c else "encoder"
    if re.match(r"lut_l\d+_", c):
        return "LUT->register"
    m = re.match(r"o(\d+)_r", c)
    if m:
        return "LUT layer->popcount" if int(m.group(1)) >= last_layer else "LUT layer->LUT layer"
    if c.startswith(("pc", "u_pc")):
        return "popcount"
    if c.startswith(("am", "y")):
        return "argmax/output"
    m = re.match(r"g(\d+)", c)
    if m:
        # DLGN: the registered outputs of the last gate layer feed the population count
        return "LUT layer->popcount" if last_layer and int(m.group(1)) >= last_layer else "gate layers"
    if re.match(r"[psav]\d+_", c):
        return "MAC/activation"
    return "other"


def net_group(net):
    """Stage of a net from its name alone (fallback for PIP exports without driver information)."""
    n = net.split("/", 1)[1] if net.startswith("u_dut/") else net
    if net in ("<const0>", "<const1>") or "const" in net or re.search(r"(^|/)(GND|VCC)", net):
        return "constant"
    if "clk" in net.lower():
        return "clock"
    if net.startswith("dut_x") or re.match(r"x_r\[\d+\]$", n):
        return "inputs"
    if re.match(r"t_r\[\d+\]$", n):
        return "thermometer->LUT"
    if n.startswith(("t_r", "x_r")):
        return "encoder"
    if re.match(r"o\d+\[\d+\]$", n):
        return "LUT->register"
    if re.match(r"o\d+_r\[\d+\]$", n):
        return "LUT layer->popcount"
    if n.startswith("pc"):
        return "popcount"
    if n.startswith(("am", "y")) or net.startswith("dut_y"):
        return "argmax/output"
    if re.match(r"g\d+", n):
        return "gate layers"
    if re.match(r"[ps]\d+_", n) or re.match(r"[av]\d+_", n):
        return "MAC/activation"
    return "other"


def main():
    """Group the critical routing bits of a campaign by the stage of the net they configure."""
    model, cdir = sys.argv[1], Path(sys.argv[2])
    build = sys.argv[3] if len(sys.argv) > 3 else model
    subs = [cdir] if (cdir / "summary.txt").exists() else sorted(d for d in cdir.iterdir() if (d / "summary.txt").exists())
    spec_file = ROOT / f"models/{model}.json"
    last_layer = len(json.loads(spec_file.read_text())["layers"]) - 1 if spec_file.exists() else 0
    pipnet = {}
    with open(ROOT / f"hw/build/{build}/dut_pips.csv") as f:
        for r in csv.DictReader(f):
            m = re.match(r".*/[A-Z0-9_]+\.([A-Z0-9_\[\]]+)-+>+([A-Z0-9_\[\]]+)$", r["pip"])
            if m:
                pipnet[(r["tile"], m.group(2))] = driver_group(r.get("net_type", ""), r.get("driver", "-"), last_layer) \
                    if "driver" in r else net_group(r["net"])
    far_list, _, _ = load_farlist()
    cm = CramMap()
    crit = Counter()
    mism = defaultdict(int)
    unresolved = 0
    for sub in subs:
        for line in (sub / "events.tsv").read_text().splitlines():
            fi, w, b, mm, corr, flags, seg = map(int, line.split("\t"))
            if mm <= 0:
                continue
            c, tile, feats = cm.classify(far_list[fi], w, b)
            if c != "ROUTING":
                continue
            dsts = {f.split(".")[1] for f, _ in feats}
            nets = {pipnet.get((tile, d)) for d in dsts} - {None}
            if not nets:
                crit["unused wire"] += 1
                mism["unused wire"] += mm
                continue
            g = sorted(nets)[0]
            crit[g] += 1
            mism[g] += mm
    tot = sum(crit.values())
    res = {"model": model, "critical_routing_bits": tot,
           "by_group": {k: {"critical": v, "share": v / tot, "mean_mism": mism[k] / v} for k, v in crit.most_common()}}
    (cdir / "route_attrib.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
