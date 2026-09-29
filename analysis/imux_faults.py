"""Functional effect of upsets in the input multiplexers (IMUX) of DWN LUT pins, from exhaustive data.

  python analysis/imux_faults.py <model> <campaign dir> [build]
For every LUT-layer input pin, the last hop of its route is an INT-tile IMUX multiplexer (24 sources,
selected by one of six column bits and a 4-bit row pattern; prjxray segbits). For each configuration
bit of that multiplexer, the hardware outcome of its upset (exhaustive campaign: mispredictions and
correct classifications on the test set) is compared with software fault hypotheses on the affected
LUT input: stuck-at-0, stuck-at-1, replaced by another source S', wired-AND / wired-OR with S'.
Hypotheses involving S' are evaluated only when S' carries a thermometer bit of the same pipeline
stage. Writes results/imux_<model>.json with the per-bit matches and the rules that emerge.
"""
from pathlib import Path
from collections import Counter, defaultdict
import csv
import json
import re
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "train"))
sys.path.insert(0, str(ROOT / "analysis"))
import models as M  # noqa: E402
from cram_map import CramMap, load_farlist  # noqa: E402
from sw_faults import load_vectors  # noqa: E402


def main():
    """Record, for every IMUX bit of the LUT-layer inputs, the hardware outcome and which software
    fault hypotheses reproduce it exactly (exploratory analysis behind imux_model.py)."""
    model, cdir = sys.argv[1], Path(sys.argv[2])
    build = sys.argv[3] if len(sys.argv) > 3 else model
    spec = json.loads((ROOT / f"models/{model}.json").read_text())
    assert len(spec["layers"]) == 1
    X, y = load_vectors(build)
    tb = M._thermo_np(spec, X)
    mp = np.asarray(spec["layers"][0]["mapping"])
    tab = np.asarray(spec["layers"][0]["tables"], dtype=np.int64)
    L, n = mp.shape
    g = L // spec["classes"]
    addr = np.zeros((len(X), L), dtype=np.int64)
    for l in range(n):
        addr |= tb[:, mp[:, l]].astype(np.int64) << l
    out = tab[np.arange(L)[None, :], addr]
    scores = out.reshape(len(X), spec["classes"], g).sum(-1)
    pred0 = np.argmax(scores, axis=1)
    corr0 = int((pred0 == y).sum())

    def effect(j, l, v):
        """(mism, corr) when input l of LUT j sees the 0/1 vector v instead of its thermometer bit."""
        a = (addr[:, j] & ~(1 << l)) | (v.astype(np.int64) << l)
        d = tab[j, a] - out[:, j]
        idx = np.nonzero(d)[0]
        if len(idx) == 0:
            return 0, corr0
        s = scores[idx].copy()
        s[:, j // g] += d[idx]
        p = np.argmax(s, axis=1)
        return int((p != pred0[idx]).sum()), corr0 - int((pred0[idx] == y[idx]).sum()) + int((p == y[idx]).sum())

    # routed design: used PIPs per (tile, dst wire) and the net on every wire of a tile
    used, wire_net = {}, defaultdict(dict)
    with open(ROOT / f"hw/build/{build}/dut_pips.csv") as f:
        for r in csv.DictReader(f):
            m = re.match(r"([^/]+)/[A-Z0-9_]+\.([A-Z0-9_\[\]]+)-+>+([A-Z0-9_\[\]]+)$", r["pip"])
            if m:
                t, s, d = m.groups()
                used[(t, d)] = (s, r["net"])
                wire_net[t][s] = r["net"]; wire_net[t][d] = r["net"]
    thermo_of_net = {}
    for i in range(tb.shape[1]):
        thermo_of_net[f"u_dut/t_r[{i}]"] = i
    # LUT pins -> IMUX wire
    pins = []
    with open(ROOT / f"hw/build/{build}/lut_pins.csv") as f:
        for r in csv.DictReader(f):
            m = re.match(r"u_dut/lut_l0_(\d+)$", r["cell"])
            if m and r["imux_wire"]:
                j, l = int(m.group(1)), int(r["pin"][1:])
                tile, wire = r["imux_wire"].split("/")
                pins.append((j, l, tile, wire, r["net"]))
    cm = CramMap()
    far_list, far_index, _ = load_farlist()
    ev = {}
    subs = [cdir] if (cdir / "summary.txt").exists() else sorted(d for d in cdir.iterdir() if (d / "summary.txt").exists())
    for sub in subs:
        for line in (sub / "events.tsv").read_text().splitlines():
            fi, w, b, mm, cc, fl, se = map(int, line.split("\t"))
            ev[(fi, w, b)] = (mm, cc)

    records = []
    bad_map = 0
    for j, l, tile, wire, net in pins:
        if thermo_of_net.get(net) != mp[j, l]:
            bad_map += 1
            continue
        ttype = cm.tg[tile]["type"]
        fwd, _ = cm.segbits(ttype)
        srcs = {k.split(".")[2]: v for k, v in fwd.items() if k.split(".")[1] == wire and k.count(".") == 2}
        cur = used.get((tile, wire))
        if cur is None or cur[0] not in srcs:
            continue
        cur_src = cur[0]
        mux_bits = sorted({(ff, bb) for bits in srcs.values() for ff, bb, _ in bits})
        state = {k: 0 for k in mux_bits}
        for ff, bb, v in srcs[cur_src]:
            state[(ff, bb)] = v
        base = cm.tg[tile]["bits"]["CLB_IO_CLK"]
        b0, off = int(base["baseaddr"], 16), base["offset"]
        # hypotheses on this pin
        cur_v = tb[:, mp[j, l]]
        hyp = {"sa0": effect(j, l, np.zeros(len(X), np.uint8)), "sa1": effect(j, l, np.ones(len(X), np.uint8))}
        for s2 in srcs:
            if s2 == cur_src:
                continue
            n2 = wire_net[tile].get(s2)
            k2 = thermo_of_net.get(n2)
            if k2 is None:
                continue
            v2 = tb[:, k2]
            hyp[f"src:{s2}"] = effect(j, l, v2)
            hyp[f"and:{s2}"] = effect(j, l, cur_v & v2)
            hyp[f"or:{s2}"] = effect(j, l, cur_v | v2)
        for (ff, bb) in mux_bits:
            far, word, bit = b0 + ff, off + bb // 32, bb % 32
            fi = far_index.get(far)
            if fi is None:
                continue
            hw = ev.get((fi, word, bit), (0, corr0))
            flipped = dict(state); flipped[(ff, bb)] ^= 1
            # sources whose documented pattern the flipped configuration satisfies
            sel = [s for s, bits in srcs.items() if all(flipped[(a, c)] == v for a, c, v in bits)]
            role = ("col" if bb % 32 != 35 % 32 or True else "")
            records.append({"j": j, "l": l, "tile": tile, "wire": wire, "cur": cur_src, "bit": f"{ff}_{bb}",
                            "was": state[(ff, bb)], "sel_after": sel,
                            "other_nets": {s: wire_net[tile].get(s) for s in sel if s != cur_src},
                            "hw": hw, "matches": sorted(h for h, r in hyp.items() if r == tuple(hw)),
                            "sa0": hyp["sa0"], "sa1": hyp["sa1"]})
    out_p = ROOT / f"results/imux_{build}.json"
    out_p.write_text(json.dumps({"corr0": corr0, "bad_map": bad_map, "records": records}))
    print(f"{len(pins)} LUT-layer pins, {bad_map} mapping mismatches, {len(records)} IMUX bits")


if __name__ == "__main__":
    main()
