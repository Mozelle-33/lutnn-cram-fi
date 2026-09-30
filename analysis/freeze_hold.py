"""How long does a disconnected LUT input hold a frozen 0? (companion of fi/hold_test.tcl)

  python analysis/freeze_hold.py list <n> <seed> <out list>   bits for the hold test
  python analysis/freeze_hold.py eval <hold tsv> [...]         classify every test run
'list' picks disconnecting IMUX bits of DWN-M (results/imux_model_dwn_md_bits.json) whose input rests
on 0 at the campaign's idle vector (1024), for which a frozen 0 and a frozen 1 give different outcomes
and the campaign outcome was the frozen-0 one. 'eval' classifies each test run of the hold test as
"0" (outcome of a frozen 0), "1" (outcome of a frozen 1) or "mixed" (neither: the value changed
during the run), per input file and test time; results/freeze_hold_dwn_md.json.
"""
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "train"))
sys.path.insert(0, str(ROOT / "analysis"))
import models as M  # noqa: E402
from cram_map import CramMap, load_farlist  # noqa: E402
from sw_faults import load_vectors  # noqa: E402

IDLE = 1024


class Frozen:
    """Outcome (mispredictions, correct) of DWN-M with one LUT input held at a constant."""

    def __init__(self):
        spec = json.loads((ROOT / "models/dwn_md.json").read_text())
        X, self.y = load_vectors("dwn_md")
        self.tb = M._thermo_np(spec, X)
        self.mp = np.asarray(spec["layers"][0]["mapping"])
        self.tab = np.asarray(spec["layers"][0]["tables"], dtype=np.int64)
        L, n = self.mp.shape
        self.g = L // spec["classes"]
        self.addr = np.zeros((len(X), L), dtype=np.int64)
        for l in range(n):
            self.addr |= self.tb[:, self.mp[:, l]].astype(np.int64) << l
        self.out = self.tab[np.arange(L)[None, :], self.addr]
        self.scores = self.out.reshape(len(X), spec["classes"], self.g).sum(-1)
        self.pred0 = np.argmax(self.scores, axis=1)
        self.corr0 = int((self.pred0 == self.y).sum())

    def __call__(self, j, l, value):
        a = (self.addr[:, j] & ~(1 << l)) | (int(value) << l)
        d = self.tab[j, a] - self.out[:, j]
        idx = np.nonzero(d)[0]
        if len(idx) == 0:
            return 0, self.corr0
        s = self.scores[idx].copy()
        s[:, j // self.g] += d[idx]
        p = np.argmax(s, axis=1)
        return (int((p != self.pred0[idx]).sum()),
                self.corr0 - int((self.pred0[idx] == self.y[idx]).sum()) + int((p == self.y[idx]).sum()))


def candidates():
    fz = Frozen()
    cm = CramMap()
    _, far_index, _ = load_farlist()
    bits = json.loads((ROOT / "results/imux_model_dwn_md_bits.json").read_text())
    out = []
    for j, l, tile, wire, bit, why, hw_old, pred_old, *_ in bits:
        if why != "open" or int(fz.tb[IDLE, fz.mp[j, l]]) != 0:
            continue
        o0, o1 = fz(j, l, 0), fz(j, l, 1)
        if o0 == o1 or tuple(hw_old) != o0:
            continue
        ff, bb = map(int, bit.split("_"))
        base = cm.tg[tile]["bits"]["CLB_IO_CLK"]
        key = (far_index[int(base["baseaddr"], 16) + ff], base["offset"] + bb // 32, bb % 32)
        out.append((key, o0, o1))
    return out


def main():
    if sys.argv[1] == "list":
        n, seed, path = int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
        c = candidates()
        random.Random(seed).shuffle(c)
        Path(path).write_text("".join(f"{k[0]} {k[1]} {k[2]} {o0[0]} {o0[1]} {o1[0]} {o1[1]}\n" for k, o0, o1 in c[:n]))
        print(f"{len(c)} candidates, {min(n, len(c))} written to {path}")
        return
    exp = {k: (o0, o1) for k, o0, o1 in candidates()}
    res = {}
    for f in sys.argv[2:]:
        by_t = defaultdict(lambda: defaultdict(int))
        for line in Path(f).read_text().splitlines():
            fi, w, b, t, m, c, _ = line.split("\t")
            key = (int(fi), int(w), int(b))
            if key not in exp:
                continue
            o0, o1 = exp[key]
            h = (int(m), int(c))
            cls = "0" if h == o0 else "1" if h == o1 else "mixed"
            tb = min((0, 0.1, 1, 10, 30, 60), key=lambda x: abs(x - float(t)))
            by_t[tb][cls] += 1
        res[Path(f).name] = {str(t): dict(v) for t, v in sorted(by_t.items())}
    (ROOT / "results/freeze_hold_dwn_md.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
