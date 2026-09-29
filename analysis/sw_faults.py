"""Software fault models on the exact test vectors used in hardware.

dwn_single_flips(spec, X): for every (layer-0 LUT j, entry k) the number of the N vectors whose
predicted class changes (vs fault-free) and the number classified correctly after flipping
table[j][k]. This is the parameter-level fault model of Bacellar et al. (TMLR'26) evaluated
exhaustively; with LOCK_PINS identity mapping it predicts the hardware LUT-INIT injections exactly.
"""
from pathlib import Path
import json
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "train"))
import models as M  # noqa: E402


def load_vectors(name):
    """Hardware test vectors of a generated network (hw/gen/<name>/vectors.mem): features, labels."""
    meta = json.loads((ROOT / f"hw/gen/{name}/meta.json").read_text())
    in_w, q = meta["IN_W"], meta["QBITS"]
    X, y = [], []
    for line in (ROOT / f"hw/gen/{name}/vectors.mem").read_text().split():
        v = int(line, 16)
        y.append(v >> in_w)
        X.append([(v >> (i * q)) & ((1 << q) - 1) for i in range(in_w // q)])
    return np.array(X, dtype=np.int64), np.array(y, dtype=np.uint8)


def dwn_single_flips(spec, X, y, ntest=None):
    """Returns arrays mism[L, 64], corr[L, 64] for layer 0 of a single-layer DWN."""
    assert len(spec["layers"]) == 1, "single-layer DWN only"
    if ntest:
        X, y = X[:ntest], y[:ntest]
    classes = spec["classes"]
    m = np.asarray(spec["layers"][0]["mapping"])
    t = np.asarray(spec["layers"][0]["tables"], dtype=np.int64)
    L = m.shape[0]
    g = L // classes
    tb = M._thermo_np(spec, X)
    addr = np.zeros((len(X), L), dtype=np.int64)
    for l in range(m.shape[1]):
        addr |= tb[:, m[:, l]].astype(np.int64) << l
    out = t[np.arange(L)[None, :], addr]
    scores = out.reshape(len(X), classes, g).sum(-1)
    pred0 = np.argmax(scores, axis=1)
    corr0 = int((pred0 == y).sum())
    mism = np.zeros((L, 64), dtype=np.int64)
    corr = np.full((L, 64), corr0, dtype=np.int64)
    for j in range(L):
        c = j // g
        aj = addr[:, j]
        for k in np.unique(aj):
            idx = np.nonzero(aj == k)[0]
            s = scores[idx].copy()
            s[:, c] += 1 if t[j, k] == 0 else -1
            p = np.argmax(s, axis=1)
            mism[j, k] = int((p != pred0[idx]).sum())
            corr[j, k] = corr0 - int((pred0[idx] == y[idx]).sum()) + int((p == y[idx]).sum())
    return mism, corr, corr0


def dwn2_single_flips(spec, X, y):
    """Two-layer DWN: exact effect of every single table-entry flip in either layer.
    Returns [mism_l0 (L0x64), mism_l1 (L1x64)], [corr_l0, corr_l1], baseline correct."""
    assert len(spec["layers"]) == 2
    classes = spec["classes"]
    m0 = np.asarray(spec["layers"][0]["mapping"]); t0 = np.asarray(spec["layers"][0]["tables"], dtype=np.int64)
    m1 = np.asarray(spec["layers"][1]["mapping"]); t1 = np.asarray(spec["layers"][1]["tables"], dtype=np.int64)
    L0, L1, n = m0.shape[0], m1.shape[0], m0.shape[1]
    g = L1 // classes
    tb = M._thermo_np(spec, X)
    a0 = np.zeros((len(X), L0), dtype=np.int64)
    for l in range(n):
        a0 |= tb[:, m0[:, l]].astype(np.int64) << l
    o0 = t0[np.arange(L0)[None, :], a0]
    a1 = np.zeros((len(X), L1), dtype=np.int64)
    for l in range(n):
        a1 |= o0[:, m1[:, l]] << l
    o1 = t1[np.arange(L1)[None, :], a1]
    scores = o1.reshape(len(X), classes, g).sum(-1)
    pred0 = np.argmax(scores, axis=1)
    corr0 = int((pred0 == y).sum())
    # last layer: popcount delta
    mism1 = np.zeros((L1, 64), dtype=np.int64); corr1 = np.full((L1, 64), corr0, dtype=np.int64)
    for j in range(L1):
        c = j // g
        for k in np.unique(a1[:, j]):
            idx = np.nonzero(a1[:, j] == k)[0]
            s = scores[idx].copy()
            s[:, c] += 1 if t1[j, k] == 0 else -1
            p = np.argmax(s, axis=1)
            mism1[j, k] = int((p != pred0[idx]).sum())
            corr1[j, k] = corr0 - int((pred0[idx] == y[idx]).sum()) + int((p == y[idx]).sum())
    # first layer: flip propagates to every consumer input in layer 1
    consumers = [np.argwhere(m1 == j) for j in range(L0)]      # rows of (lut, position)
    mism0 = np.zeros((L0, 64), dtype=np.int64); corr0a = np.full((L0, 64), corr0, dtype=np.int64)
    for j in range(L0):
        cons = consumers[j]
        if len(cons) == 0:
            continue
        luts = np.unique(cons[:, 0])
        xor = np.zeros(len(luts), dtype=np.int64)
        for q, (lut, pos) in enumerate(cons):
            xor[np.searchsorted(luts, lut)] ^= 1 << pos
        cls = luts // g
        for k in np.unique(a0[:, j]):
            idx = np.nonzero(a0[:, j] == k)[0]
            na = a1[np.ix_(idx, luts)] ^ xor[None, :]
            delta = t1[luts[None, :], na] - o1[np.ix_(idx, luts)]
            s = scores[idx].copy()
            np.add.at(s, (np.arange(len(idx))[:, None].repeat(len(luts), 1), cls[None, :].repeat(len(idx), 0)), delta)
            p = np.argmax(s, axis=1)
            mism0[j, k] = int((p != pred0[idx]).sum())
            corr0a[j, k] = corr0 - int((pred0[idx] == y[idx]).sum()) + int((p == y[idx]).sum())
    return [mism0, mism1], [corr0a, corr1], corr0


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "dwn_md"
    spec = json.loads((ROOT / f"models/{name}.json").read_text())
    X, y = load_vectors(name)
    if len(spec["layers"]) == 1:
        mism, corr, corr0 = dwn_single_flips(spec, X, y)
        mis_l, cor_l = [mism], [corr]
    else:
        mis_l, cor_l, corr0 = dwn2_single_flips(spec, X, y)
    for li, mism in enumerate(mis_l):
        nz = mism > 0
        print(f"{name} layer {li}: baseline correct {corr0}/{len(y)}; entries {mism.size}; critical {nz.sum()} "
              f"({100 * nz.mean():.2f}%); mean mismatches of critical {mism[nz].mean() if nz.any() else 0:.2f}; max {mism.max()}")
    np.savez(ROOT / f"results/sw_single_flips_{name}.npz", mism=mis_l[0], corr=cor_l[0], corr0=corr0,
             **{f"mism_l{li}": mm for li, mm in enumerate(mis_l)}, **{f"corr_l{li}": cc for li, cc in enumerate(cor_l)})
