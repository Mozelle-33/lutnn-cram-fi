"""Connectivity fault model for every multiplexer on the routes of the thermometer nets of a DWN.

  python analysis/route_model.py <model> <campaign dir> [build] [--idle N] [--tables MODEL] [--tag TAG]
--idle: the input vector the upsets were injected on (default 1024); --tables: predict with the tables
of another model with the same mapping (e.g., a don't-care-filled variant on the same routing); --frozen
0|1: value of a disconnected input instead of the idle-vector value; --tag: suffix of the output files.
Extends imux_model.py from the last hop to whole routing trees (hw/build/<build>/route_pips.csv and
route_sinks.csv from hw/tcl/export_routetree.tcl). For each INT-tile multiplexer that a thermometer
net t_r[k] passes through, each configuration bit is flipped in the model:
  * which sources are connected after the flip: multiplexers with the IMUX-style encoding (four row
    bits with documented patterns, incl. BYP_ALT/FAN_ALT) use the row gates A=!r3, B=!r2, C=r0,
    D=r1; the others (one-hot row x one-hot column) connect a source when all its bits are set;
  * no source -> every LUT input downstream keeps the value of t_r[k] at the time of the upset;
    several  -> every LUT input downstream sees the wired-AND of the connected sources' nets;
and the resulting mispredictions and correct classifications are compared with the exhaustive
hardware campaign. Writes results/route_model_<build>.json.
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

GATES = {"A": lambda r: 1 - r[3], "B": lambda r: 1 - r[2], "C": lambda r: r[0], "D": lambda r: r[1]}
PAT = {(0, 0, 1, 0): "A", (0, 0, 0, 1): "B", (1, 0, 1, 1): "C", (0, 1, 1, 1): "D"}
IDLE_VECTOR = 1024
THERMO = re.compile(r"u_dut/t_r\[(\d+)\]$")
PIP = re.compile(r"([^/]+)/([A-Z0-9_]+)\.([A-Z0-9_\[\]]+)-+>+([A-Z0-9_\[\]]+)$")


class Mux:
    """Configuration bits of one multiplexer and the sources connected for a given bit state."""

    def __init__(self, feats):
        self.feats = feats                                   # src -> [(ff, bb, val)]
        cnt = Counter((ff, bb) for bits in feats.values() for ff, bb, _ in bits)
        rows = sorted(k for k, c in cnt.items() if c == len(feats))
        self.bits = sorted(cnt)
        self.imux = False
        if len(rows) == 4 and any(v == 0 for bits in feats.values() for _, _, v in bits):
            pats = {}
            for s, bits in feats.items():
                d = {(ff, bb): v for ff, bb, v in bits}
                pats[s] = tuple(d[r] for r in rows)
            if set(pats.values()) <= set(PAT):
                self.imux, self.rows = True, rows
                self.src_at = {}
                for s, bits in feats.items():
                    col = [(ff, bb) for ff, bb, v in bits if (ff, bb) not in rows and v == 1]
                    if len(col) != 1:
                        self.imux = False
                        break
                    self.src_at[(col[0], PAT[pats[s]])] = s
                self.cols = sorted({c for c, _ in getattr(self, "src_at", {})})

    def state_of(self, src):
        st = {b: 0 for b in self.bits}
        for ff, bb, v in self.feats[src]:
            st[(ff, bb)] = v
        return st

    def connected(self, st):
        if self.imux:
            rv = [st[r] for r in self.rows]
            on = [g for g, fn in GATES.items() if fn(rv)]
            return sorted(self.src_at[(c, g)] for c in self.cols if st[c] for g in on if (c, g) in self.src_at)
        return sorted(s for s, bits in self.feats.items() if all(st[(ff, bb)] == 1 for ff, bb, v in bits if v == 1))


def main():
    args, opt = [], {}
    it = iter(sys.argv[1:])
    for a in it:
        if a.startswith("--"):
            opt[a[2:]] = next(it)
        else:
            args.append(a)
    model, cdir = args[0], Path(args[1])
    build = args[2] if len(args) > 2 else model
    idle = int(opt.get("idle", IDLE_VECTOR))
    tag = f"_{opt['tag']}" if "tag" in opt else ""
    spec = json.loads((ROOT / f"models/{model}.json").read_text())
    X, y = load_vectors(build if (ROOT / f"hw/gen/{build}/vectors.mem").exists() else model)
    # fault-free forward pass of the single-layer DWN on the hardware test vectors
    tb = M._thermo_np(spec, X)
    mp = np.asarray(spec["layers"][0]["mapping"])
    tspec = json.loads((ROOT / f"models/{opt['tables']}.json").read_text()) if "tables" in opt else spec
    assert np.array_equal(np.asarray(tspec["layers"][0]["mapping"]), mp)
    tab = np.asarray(tspec["layers"][0]["tables"], dtype=np.int64)
    L, n = mp.shape
    g = L // spec["classes"]
    addr = np.zeros((len(X), L), dtype=np.int64)
    for l in range(n):
        addr |= tb[:, mp[:, l]].astype(np.int64) << l
    out = tab[np.arange(L)[None, :], addr]
    scores = out.reshape(len(X), spec["classes"], g).sum(-1)
    pred0 = np.argmax(scores, axis=1)
    corr0 = int((pred0 == y).sum())

    def effect(changes):
        """changes: {(j, l): 0/1 vector} -- new values of several LUT inputs at once (an upset
        upstream of a branch reaches every LUT input below it). Returns (mism, corr)."""
        s = scores.copy()
        touched = np.zeros(len(X), dtype=bool)
        byj = defaultdict(list)
        for (j, l), v in changes.items():
            byj[j].append((l, v))
        for j, lv in byj.items():
            a = addr[:, j].copy()
            for l, v in lv:
                a = (a & ~(1 << l)) | (v.astype(np.int64) << l)
            d = tab[j, a] - out[:, j]
            if d.any():
                s[:, j // g] += d
                touched |= d != 0
        idx = np.nonzero(touched)[0]
        if len(idx) == 0:
            return 0, corr0
        p = np.argmax(s[idx], axis=1)
        return int((p != pred0[idx]).sum()), corr0 - int((pred0[idx] == y[idx]).sum()) + int((p == y[idx]).sum())

    # routed design: nets on every wire of every tile, and the routing trees of the thermometer nets
    wire_net = defaultdict(dict)
    with open(ROOT / f"hw/build/{build}/dut_pips.csv") as f:
        for r in csv.DictReader(f):
            m = PIP.match(r["pip"])
            if m:
                t, tt, s, d = m.groups()
                wire_net[t][s] = r["net"]; wire_net[t][d] = r["net"]
    # node-level nets of source wires (hw/tcl/export_wirenets.tcl): a wire without a PIP in this tile can
    # still belong to a node that another tile drives
    wn_path = ROOT / f"hw/build/{build}/wire_nets.csv"
    if wn_path.exists():
        with open(wn_path) as f:
            for r in csv.DictReader(f):
                t, w = r["wire"].split("/")
                if r["net"]:
                    wire_net[t][w] = r["net"]
                else:
                    wire_net[t].pop(w, None)
    # routing trees of the thermometer nets: node graph (up_node -> down_node per PIP) and the
    # interconnect-tile PIPs, i.e. the multiplexers whose bits are modelled
    children = defaultdict(list)
    pips = []
    with open(ROOT / f"hw/build/{build}/route_pips.csv") as f:
        for r in csv.DictReader(f):
            m = THERMO.match(r["net"])
            p = PIP.match(r["pip"])
            if not m or not p:
                continue
            children[r["up_node"]].append(r["down_node"])
            tile, tt, s, d = p.groups()
            if tt in ("INT_L", "INT_R"):
                pips.append((int(m.group(1)), tile, tt, s, d, r["down_node"]))
    # sink node -> (LUT index, input index) for the LUT-layer inputs
    sink_at = {}
    with open(ROOT / f"hw/build/{build}/route_sinks.csv") as f:
        for r in csv.DictReader(f):
            m = re.match(r"u_dut/lut_l0_(\d+)$", r["cell"])
            if THERMO.match(r["net"]) and m:
                sink_at[r["node"]] = (int(m.group(1)), int(r["pin"][1:]))

    def downstream(node, seen=None):
        """LUT inputs reached from a node through the net's routing tree (depth-first search)."""
        seen = set() if seen is None else seen
        res = []
        stack = [node]
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            if x in sink_at:
                res.append(sink_at[x])
            stack.extend(children.get(x, []))
        return res

    cm = CramMap()
    far_list, far_index, _ = load_farlist()
    ev = {}
    subs = [cdir] if (cdir / "summary.txt").exists() else sorted(d for d in cdir.iterdir() if (d / "summary.txt").exists())
    for sub in subs:
        for line in (sub / "events.tsv").read_text().splitlines():
            fi, w, b, mm, cc, fl, se = map(int, line.split("\t"))
            ev[(fi, w, b)] = (mm, cc)
    thermo_net = {f"u_dut/t_r[{i}]": i for i in range(tb.shape[1])}

    stats = defaultdict(Counter)
    muxes = {}
    seen_bits = set()
    per_bit = []
    per_bit_unused = []
    cand = set()
    # one multiplexer per used interconnect PIP: its destination wire, current source and the LUT
    # inputs below it; every configuration bit of the multiplexer is modelled once
    for k, tile, tt, src, dst, dnode in pips:
        key = (tile, dst)
        if key not in muxes:
            fwd, _ = cm.segbits(tt)
            feats = {f.split(".")[2]: v for f, v in fwd.items() if f.split(".")[1] == dst and f.count(".") == 2}
            muxes[key] = Mux(feats) if src in feats else None
        mux = muxes[key]
        sinks = downstream(dnode)
        mtype = re.sub(r"_?L?\d+$", "", dst.replace("_L", "")) if mux else "?"
        if mux is None or not sinks:
            stats[(mtype, "no-model")]["n"] += 1
            continue
        if mux.imux:
            kind = "IMUX-type"
        else:
            kind = "2-hot"
        state = mux.state_of(src)
        base = cm.tg[tile]["bits"]["CLB_IO_CLK"]
        b0, off = int(base["baseaddr"], 16), base["offset"]
        frozen = tb[idle, k] if opt.get("frozen", "idle") == "idle" else int(opt["frozen"])
        for x in mux.bits:
            # CRAM address of the bit: frame = tile base + frame offset, word/bit from the bit offset
            addr_x = (far_index.get(b0 + x[0]), off + x[1] // 32, x[1] % 32)
            if addr_x in seen_bits or addr_x[0] is None:
                continue
            seen_bits.add(addr_x)
            st = dict(state); st[x] ^= 1
            conn = mux.connected(st)
            if conn == [src]:
                pred, why = (0, corr0), "none"
            elif not conn:
                # disconnected: every downstream LUT input freezes at the net's value
                pred, why = effect({s: np.full(len(X), frozen, np.uint8) for s in sinks}), "freeze"
            else:
                # doubly driven: wired-AND of the connected sources. ks: thermometer index of each
                # source's net, -1 for an undriven wire, None for a net the model cannot evaluate
                ks = []
                for s in conn:
                    if s != src:
                        cand.add(f"{tile}/{s}")
                    ks.append(k if s == src else thermo_net.get(wire_net[tile].get(s), -1 if wire_net[tile].get(s) is None else None))
                clbout = any(kk == -1 and s.startswith("LOGIC_OUTS") for s, kk in zip(conn, ks))
                if any(v is None for v in ks):
                    pred, why = None, "and-other-net"
                elif clbout:
                    # an unused CLB output is driven by its site with a BEL-dependent constant
                    pred, why = None, "and-unused-clbout"
                else:
                    v = tb[:, k].copy()
                    for kk in ks:
                        if kk >= 0:            # an unused wire (-1) acts as logic one
                            v &= tb[:, kk]
                    pred, why = effect({s: v for s in sinks}), ("and-unused" if -1 in ks else "and")
                    if why == "and-unused":
                        # alternative: the undriven wire holds 0 -> every downstream input reads 0
                        unused = [f"{tile}/{s}" for s, kk in zip(conn, ks) if kk == -1]
                        p_zero = effect({s: np.zeros(len(X), np.uint8) for s in sinks})
                        per_bit_unused.append([unused, list(ev.get(addr_x, (0, corr0))), list(pred), list(p_zero)])
            hw = ev.get(addr_x, (0, corr0))
            fan = "1 sink" if len(sinks) == 1 else "2+ sinks"
            for key2 in [(kind, why), ("all", why), (fan, why)]:
                c = stats[key2]
                c["n"] += 1
                c["crit"] += hw[0] > 0
                if pred is not None:
                    c["exact"] += tuple(hw) == tuple(pred)
                    c["crit_exact"] += hw[0] > 0 and tuple(hw) == tuple(pred)
            per_bit.append([k, tile, dst, f"{x[0]}_{x[1]}", why, len(sinks), list(hw), list(pred) if pred else None])
    if not tag:
        (ROOT / f"hw/build/{build}/bridge_wires.txt").write_text("\n".join(sorted(cand)) + "\n")
    ev_pred = [b[7] for b in per_bit if b[7] is not None]
    res = {"build": build, "corr0": corr0, "muxes": len(muxes), "bits": len(per_bit), "idle": idle,
           "tables": opt.get("tables", model),
           "predicted": {"evaluable": len(ev_pred), "critical": sum(p[0] > 0 for p in ev_pred),
                         "sum_mism": sum(p[0] for p in ev_pred)},
           "stats": {f"{a}/{b}": dict(v) for (a, b), v in stats.items()}}
    (ROOT / f"results/route_model_{build}{tag}.json").write_text(json.dumps(res, indent=1))
    (ROOT / f"results/route_model_{build}{tag}_bits.json").write_text(json.dumps(per_bit))
    (ROOT / f"results/route_model_{build}{tag}_unused.json").write_text(json.dumps(per_bit_unused))
    print("predicted over the evaluable bits:", res["predicted"])
    for (a, b), v in sorted(stats.items()):
        ex = f"exact {100 * v['exact'] / v['n']:6.2f}%  critical {v['crit_exact']}/{v['crit']}" if "exact" in v or b in ("none", "freeze", "and", "and-unused") else ""
        print(f"{a:10s} {b:14s} n={v['n']:6d} crit={v['crit']:6d}  {ex}")
    ev_bits = [b for b in per_bit if b[7] is not None]
    crit = [b for b in ev_bits if b[6][0] > 0]
    print(f"evaluable {len(ev_bits)}/{len(per_bit)} bits; exact {100 * sum(tuple(b[6]) == tuple(b[7]) for b in ev_bits) / len(ev_bits):.2f}%;"
          f" critical evaluable {len(crit)}, exact {100 * sum(tuple(b[6]) == tuple(b[7]) for b in crit) / max(1, len(crit)):.2f}%")


if __name__ == "__main__":
    main()
