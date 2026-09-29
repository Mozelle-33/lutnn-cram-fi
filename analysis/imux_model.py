"""Complete functional model of IMUX configuration upsets, validated bit by bit against hardware.

  python analysis/imux_model.py <model> <campaign dir> [build]
Model (derived from the exhaustive campaign, see imux_faults.py): an IMUX multiplexer has six one-hot
column bits and four row bits r0..r3; row pass-gates are A = !r3, B = !r2, C = r0, D = r1 and the
source (column c, gate g) is connected when column bit c is set and gate g is on. After an upset:
  no source connected   -> the input keeps the value it had when the upset occurred (keeper);
  several sources       -> wired-AND of their values (0 dominates);
  unchanged selection   -> no effect.
The prediction is exact (mispredictions and correct classifications) when every connected source is a
thermometer bit of the same stage. Writes results/imux_model_<build>.json.
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
IDLE_VECTOR = 1024   # the harness holds the vector after the last verify run while injecting


def main():
    model, cdir = sys.argv[1], Path(sys.argv[2])
    build = sys.argv[3] if len(sys.argv) > 3 else model
    spec = json.loads((ROOT / f"models/{model}.json").read_text())
    X, y = load_vectors(build if (ROOT / f"hw/gen/{build}/vectors.mem").exists() else model)
    # fault-free forward pass of the single-layer DWN on the hardware test vectors
    tb = M._thermo_np(spec, X)                      # thermometer bits (N x bits)
    mp = np.asarray(spec["layers"][0]["mapping"])   # LUT j input l <- thermometer bit mp[j, l]
    tab = np.asarray(spec["layers"][0]["tables"], dtype=np.int64)
    L, n = mp.shape
    g = L // spec["classes"]                        # LUTs per class (population-count group)
    addr = np.zeros((len(X), L), dtype=np.int64)
    for l in range(n):
        addr |= tb[:, mp[:, l]].astype(np.int64) << l
    out = tab[np.arange(L)[None, :], addr]
    scores = out.reshape(len(X), spec["classes"], g).sum(-1)
    pred0 = np.argmax(scores, axis=1)
    corr0 = int((pred0 == y).sum())

    def effect(j, l, v):
        """(mispredictions, correct classifications) when input l of LUT j sees the 0/1 vector v:
        only LUT j changes, so only its class score is updated."""
        a = (addr[:, j] & ~(1 << l)) | (v.astype(np.int64) << l)
        d = tab[j, a] - out[:, j]
        idx = np.nonzero(d)[0]
        if len(idx) == 0:
            return 0, corr0
        s = scores[idx].copy()
        s[:, j // g] += d[idx]
        p = np.argmax(s, axis=1)
        return int((p != pred0[idx]).sum()), corr0 - int((pred0[idx] == y[idx]).sum()) + int((p == y[idx]).sum())

    # routed design: the used PIP of every (tile, destination wire), and the net on every wire that
    # appears in a used PIP of the tile (as source or destination)
    used, wire_net = {}, defaultdict(dict)
    with open(ROOT / f"hw/build/{build}/dut_pips.csv") as f:
        for r in csv.DictReader(f):
            m = re.match(r"([^/]+)/[A-Z0-9_]+\.([A-Z0-9_\[\]]+)-+>+([A-Z0-9_\[\]]+)$", r["pip"])
            if m:
                t, s, d = m.groups()
                used[(t, d)] = (s, r["net"])
                wire_net[t][s] = r["net"]; wire_net[t][d] = r["net"]
    thermo = {f"u_dut/t_r[{i}]": i for i in range(tb.shape[1])}
    cm = CramMap()
    far_list, far_index, _ = load_farlist()
    # hardware outcome of every non-silent bit; bits without a record were silent (0, corr0)
    ev = {}
    subs = [cdir] if (cdir / "summary.txt").exists() else sorted(d for d in cdir.iterdir() if (d / "summary.txt").exists())
    for sub in subs:
        for line in (sub / "events.tsv").read_text().splitlines():
            fi, w, b, mm, cc, fl, se = map(int, line.split("\t"))
            ev[(fi, w, b)] = (mm, cc)

    stats = defaultdict(Counter)
    per_bit = []
    with open(ROOT / f"hw/build/{build}/lut_pins.csv") as f:
        pins = [r for r in csv.DictReader(f) if re.match(r"u_dut/lut_l0_\d+$", r["cell"]) and r["imux_wire"]]
    for r in pins:
        j, l = int(r["cell"].rsplit("_", 1)[1]), int(r["pin"][1:])
        tile, wire = r["imux_wire"].split("/")
        # the 24 documented sources of this IMUX and their bit patterns (prjxray segbits)
        fwd, _ = cm.segbits(cm.tg[tile]["type"])
        feats = {k.split(".")[2]: v for k, v in fwd.items() if k.split(".")[1] == wire and k.count(".") == 2}
        # row bits appear in every source's pattern (4 of them); the other 6 are one-hot column bits
        cnt = Counter((ff, bb) for bits in feats.values() for ff, bb, _ in bits)
        rows = sorted(k for k, c in cnt.items() if c == len(feats))
        cols = sorted(k for k in cnt if k not in rows)
        # source at (column bit, row gate): the row pattern of a source names its gate A..D
        src_at = {}
        for s, bits in feats.items():
            c = [(ff, bb) for ff, bb, v in bits if (ff, bb) not in rows and v == 1][0]
            pat = tuple(next(v for ff, bb, v in bits if (ff, bb) == rb) for rb in rows)
            src_at[(c, PAT[pat])] = s
        # configuration of the multiplexer as routed: the bits of the current source's pattern
        cur_src = used[(tile, wire)][0]
        state = {k: 0 for k in rows + cols}
        for ff, bb, v in feats[cur_src]:
            state[(ff, bb)] = v
        base = cm.tg[tile]["bits"]["CLB_IO_CLK"]
        b0, off = int(base["baseaddr"], 16), base["offset"]
        k_cur = mp[j, l]
        # flip each of the multiplexer's bits in turn and decode which sources are then connected
        for x in rows + cols:
            st = dict(state); st[x] ^= 1
            rv = [st[rb] for rb in rows]
            on = [gname for gname, fn in GATES.items() if fn(rv)]
            conn = [src_at[(c, gname)] for c in cols if st[c] for gname in on if (c, gname) in src_at]
            kind = "row" if x in rows else "col"
            if conn == [cur_src]:
                pred, why = (0, corr0), "none"
            elif not conn:
                # no source: the pin keeps the value it had at the upset (idle input vector)
                pred, why = effect(j, l, np.full(len(X), tb[IDLE_VECTOR, k_cur], np.uint8)), "open"
            else:
                # several sources: wired-AND; exact only if every source carries a thermometer bit
                ks = [thermo.get(wire_net[tile].get(s)) for s in conn]
                if any(k is None for k in ks):
                    others = {("unused" if wire_net[tile].get(s) is None else "net") for s in conn if s != cur_src}
                    pred, why = None, "bridge-" + "+".join(sorted(others))
                else:
                    v = np.ones(len(X), np.uint8)
                    for k in ks:
                        v &= tb[:, k]
                    pred, why = effect(j, l, v), "bridge"
            # CRAM address of the bit: frame = tile base address + frame offset ff, word/bit from bb
            fi = far_index.get(b0 + x[0])
            hw = ev.get((fi, off + x[1] // 32, x[1] % 32), (0, corr0))
            key = (kind, why)
            stats[key]["n"] += 1
            stats[key]["crit"] += hw[0] > 0
            if pred is not None:
                stats[key]["exact"] += tuple(hw) == tuple(pred)
                stats[key]["crit_exact"] += hw[0] > 0 and tuple(hw) == tuple(pred)
                stats[key]["pred_crit"] += pred[0] > 0
                stats[key]["both_crit"] += pred[0] > 0 and hw[0] > 0
            extra = [[s, wire_net[tile].get(s)] for s in conn if s != cur_src]
            sa0 = effect(j, l, np.zeros(len(X), np.uint8)) if why.startswith("bridge-") else None
            per_bit.append([j, l, tile, wire, f"{x[0]}_{x[1]}", why, list(hw), list(pred) if pred else None, extra,
                            list(sa0) if sa0 else None])
    res = {"build": build, "corr0": corr0, "stats": {f"{k[0]}/{k[1]}": dict(v) for k, v in stats.items()}}
    tot = Counter()
    for k, v in stats.items():
        if not k[1].startswith("bridge-"):
            tot.update(v)
    res["evaluable"] = dict(tot)
    all_n = sum(v["n"] for v in stats.values())
    all_crit = sum(v["crit"] for v in stats.values())
    res["coverage_bits"] = tot["n"] / all_n
    res["coverage_critical"] = tot["crit"] / all_crit
    (ROOT / f"results/imux_model_{build}.json").write_text(json.dumps(res, indent=1))
    (ROOT / f"results/imux_model_{build}_bits.json").write_text(json.dumps(per_bit))
    for k in sorted(stats):
        v = stats[k]
        ex = f"exact {100 * v['exact'] / v['n']:6.2f}%  critical exact {v['crit_exact']}/{v['crit']}" if k[1] and not k[1].startswith("bridge-") else ""
        print(f"{k[0]:4s} {k[1]:22s} n={v['n']:6d} critical={v['crit']:6d}  {ex}")
    print(f"evaluable: {tot['n']}/{all_n} bits ({100 * res['coverage_bits']:.1f}%), "
          f"{tot['crit']}/{all_crit} critical ({100 * res['coverage_critical']:.1f}%); exact on evaluable: "
          f"{100 * tot['exact'] / tot['n']:.2f}% of bits, {100 * tot['crit_exact'] / max(1, tot['crit']):.2f}% of critical; "
          f"predicted critical {tot['pred_crit']}, both {tot['both_crit']}")


if __name__ == "__main__":
    main()
