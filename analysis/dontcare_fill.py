"""Zero-overhead hardening of DWN LUTs by filling never-addressed ("don't care") entries.

  python analysis/dontcare_fill.py <model> [--out <new model name>]
Entries of a layer-0 LUT that no training sample addresses are set to the visit-weighted majority
of their Hamming-distance-1 addressed neighbours, so a fault that corrupts one LUT input (a routing
upset feeding the LUT, the dominant CRAM failure) tends to return the same output.
Reports fault-free accuracy before/after on the full test set, and a software LUT-input fault model
(stuck-at-0, stuck-at-1, inversion of every LUT input) on the 4096 hardware test vectors.
Writes models/<out>.json (same mapping, filled tables).
"""
from pathlib import Path
import argparse
import json
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "train"))
import data_jsc  # noqa: E402
import models as M  # noqa: E402
from sw_faults import load_vectors  # noqa: E402


def addresses(spec, X):
    """Address of every layer-0 LUT for every sample (N x L)."""
    m = np.asarray(spec["layers"][0]["mapping"])
    tb = M._thermo_np(spec, X)
    addr = np.zeros((len(X), m.shape[0]), dtype=np.int64)
    for l in range(m.shape[1]):
        addr |= tb[:, m[:, l]].astype(np.int64) << l
    return addr


def fill(spec, Xtr):
    """Fill the never-visited entries of each LUT; returns the new spec, the number of don't-care
    entries, the number of entries that changed value, and the visit counts."""
    t = np.asarray(spec["layers"][0]["tables"], dtype=np.int64).copy()
    L, A = t.shape
    n = int(np.log2(A))
    addr = addresses(spec, Xtr)
    visits = np.zeros((L, A), dtype=np.int64)
    for j in range(L):
        visits[j] = np.bincount(addr[:, j], minlength=A)
    changed = 0
    dc_total = 0
    for j in range(L):
        dc = np.nonzero(visits[j] == 0)[0]
        dc_total += len(dc)
        for a in dc:
            w1 = w0 = 0
            for l in range(n):
                nb = a ^ (1 << l)
                if visits[j, nb] > 0:
                    if t[j, nb]:
                        w1 += visits[j, nb]
                    else:
                        w0 += visits[j, nb]
            if w1 + w0 == 0:
                continue
            v = 1 if w1 > w0 else 0
            if v != t[j, a]:
                t[j, a] = v
                changed += 1
    new = json.loads(json.dumps(spec))
    new["layers"][0]["tables"] = t.tolist()
    return new, dc_total, changed, visits


def input_fault_model(spec, X, y):
    """mismatches for stuck0/stuck1/invert on each (LUT j, input l): arrays [L, n, 3]."""
    m = np.asarray(spec["layers"][0]["mapping"])
    t = np.asarray(spec["layers"][0]["tables"], dtype=np.int64)
    L, n = m.shape
    classes = spec["classes"]
    g = L // classes
    addr = addresses(spec, X)
    out = t[np.arange(L)[None, :], addr]
    scores = out.reshape(len(X), classes, g).sum(-1)
    pred0 = np.argmax(scores, axis=1)
    res = np.zeros((L, n, 3), dtype=np.int64)
    for j in range(L):
        c = j // g
        for l in range(n):
            for f, fa in enumerate((addr[:, j] & ~(1 << l), addr[:, j] | (1 << l), addr[:, j] ^ (1 << l))):
                nv = t[j, fa]
                delta = nv - out[:, j]
                idx = np.nonzero(delta)[0]
                if len(idx) == 0:
                    continue
                s = scores[idx].copy()
                s[:, c] += delta[idx]
                res[j, l, f] = int((np.argmax(s, axis=1) != pred0[idx]).sum())
    return res


def main():
    """Fill a model's tables, compare accuracy and the input-fault model before and after, save the spec."""
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    spec = json.loads((ROOT / f"models/{a.model}.json").read_text())
    d = data_jsc.load(spec["meta"]["qbits"])
    new, dc_total, changed, visits = fill(spec, d["Xtr"])
    L, A = visits.shape
    acc0 = float((M.predict_spec(spec, d["Xte"])[1] == d["yte"]).mean())
    acc1 = float((M.predict_spec(new, d["Xte"])[1] == d["yte"]).mean())
    X, y = load_vectors(a.model)
    f0 = input_fault_model(spec, X, y)
    f1 = input_fault_model(new, X, y)
    names = ["stuck0", "stuck1", "invert"]
    rep = {"model": a.model, "entries": L * A, "dont_care_entries": dc_total, "dont_care_frac": dc_total / (L * A),
           "entries_changed": changed, "test_acc_before": acc0, "test_acc_after": acc1,
           "input_faults": {nm: {"critical_before": int((f0[:, :, i] > 0).sum()), "critical_after": int((f1[:, :, i] > 0).sum()),
                                 "mism_sum_before": int(f0[:, :, i].sum()), "mism_sum_after": int(f1[:, :, i].sum())}
                            for i, nm in enumerate(names)}}
    print(json.dumps(rep, indent=1))
    out = a.out or f"{a.model}_dc"
    new["meta"] = dict(spec["meta"], name=out, test_acc=acc1, derived_from=a.model, dontcare_filled=True)
    (ROOT / f"models/{out}.json").write_text(json.dumps(new))
    (ROOT / f"results/dontcare_{a.model}.json").write_text(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
