"""Common-mode failures of the output-stage TMR: critical bits that touch the triplicated read-out.

  python analysis/tmr_cmf.py <campaign dir> <build> [...]   -> results/tmr_cmf.json
The three read-out replicas are the cells and nets under u_dut/u_pc0, u_pc1, u_pc2. An upset that
reaches only one replica is out-voted, so every critical bit that touches a replica needs a second
path: a routing bit whose multiplexer drives nets of two replicas, a replica LUT or CLB setting shared
with another replica, or a bit that also touches the shared logic (encoder, LUT layer, voter, clock).
Counts the critical bits by the replicas and shared stages their routing multiplexers or CLB sites
touch.
"""
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from cram_map import CramMap, load_farlist

ROOT = Path(__file__).resolve().parents[1]
REPL = re.compile(r"u_dut/u_pc(\d)/")


def main():
    args = sys.argv[1:]
    out = {}
    far_list, _, _ = load_farlist()
    cm = CramMap()
    for cdir, build in zip(args[::2], args[1::2]):
        net_at = defaultdict(set)                     # (tile, destination wire) -> nets
        with open(ROOT / f"hw/build/{build}/dut_pips.csv") as f:
            for r in csv.DictReader(f):
                m = re.match(r".*/[A-Z0-9_]+\.([A-Z0-9_\[\]]+)-+>+([A-Z0-9_\[\]]+)$", r["pip"])
                if m:
                    net_at[(r["tile"], m.group(2))].add(r["net"])
        site_repl = defaultdict(set)                  # CLB site -> replicas with cells in it
        with open(ROOT / f"hw/build/{build}/dut_cells.csv") as f:
            for r in csv.DictReader(f):
                m = REPL.match(r["cell"])
                if r["site"]:
                    site_repl[r["site"]].add(int(m.group(1)) if m else -1)
        d = ROOT / "results" / cdir
        subs = [d] if (d / "summary.txt").exists() else sorted(s.parent for s in d.glob("*/summary.txt"))
        cnt = Counter()
        mism = defaultdict(int)
        sev, cat = Counter(), Counter()
        ntest = int(dict(kv.split("=", 1) for kv in (subs[0] / "summary.txt").read_text().split())["ntest"]) if subs else 4096
        for sub in subs:
            for line in (sub / "events.tsv").read_text().splitlines():
                fi, w, b, mm, corr, flags, seg = map(int, line.split("\t"))
                if mm <= 0:
                    continue
                c, tile, feats = cm.classify(far_list[fi], w, b)
                if c == "ROUTING":
                    nets = set()
                    for f_, _ in feats:
                        nets |= net_at.get((tile, f_.split(".")[1]), set())
                    reps = {int(m.group(1)) for n in nets for m in [REPL.match(n)] if m}
                    shared = any(not REPL.match(n) for n in nets)
                elif c in ("LUT_INIT", "CLB_OTHER"):
                    # the slice of the bit: feature SLICEx_X0 / _X1 = the site with the lower / higher X
                    sites = sorted(cm.tg[tile].get("sites", {}), key=lambda s: int(re.search(r"X(\d+)", s).group(1)))
                    idx = {int(m.group(1)) for f_, _ in feats for m in [re.search(r"SLICE[LM]_X(\d)", f_)] if m}
                    rs = set()
                    for i in idx:
                        if i < len(sites):
                            rs |= site_repl.get(sites[i], set())
                    reps = {r for r in rs if r >= 0}
                    shared = -1 in rs or not idx
                else:
                    reps, shared = set(), True
                key = ("routing " if c == "ROUTING" else c + " ") + (
                    f"{len(reps)} replicas" + (" + shared" if shared else "") if reps else "shared only")
                cnt[key] += 1
                mism[key] += mm
                sev[key] += mm >= ntest / 100
                cat[key] += mm >= ntest / 10
        out[cdir] = {k: {"critical": v, "mean_mism": mism[k] / v, "severe": sev[k], "catastrophic": cat[k]} for k, v in cnt.most_common()}
        for k, v in out[cdir].items():
            print(f"{cdir:24s} {k:32s} {v['critical']:7d} {v['mean_mism']:8.1f} {v['severe']:6d} {v['catastrophic']:5d}")
    (ROOT / "results/tmr_cmf.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
