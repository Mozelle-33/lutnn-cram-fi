"""JSC (hls4ml LHC jet substructure, OpenML 42468) preparation.

Every model sees the same integer inputs as the hardware: each of the 16 features is
clipped to its [0.1%, 99.9%] training quantiles and scaled to an unsigned QBITS-bit integer.
Output: data/jsc_q{QBITS}.npz with Xtr, ytr, Xte, yte (uint16/uint8), lo, hi, classes.

Usage: python train/data_jsc.py [--qbits 10] [--seed 0]
"""
from pathlib import Path
import argparse
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def prepare(qbits=10, seed=0, test_frac=0.2):
    from sklearn.datasets import fetch_openml
    X, y = fetch_openml(data_id=42468, return_X_y=True, as_frame=False,
                        data_home=str(ROOT / "data" / "openml"), parser="auto")
    X = np.asarray(X, dtype=np.float64)
    classes = sorted(set(y.tolist()))
    yi = np.array([classes.index(v) for v in y], dtype=np.uint8)
    # random 80/20 train/test split with a fixed seed (the hardware test set is the first 4096 test samples)
    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(X))
    nte = int(len(X) * test_frac)
    te, tr = perm[:nte], perm[nte:]
    # clipping range from the training data only
    lo = np.quantile(X[tr], 0.001, axis=0)
    hi = np.quantile(X[tr], 0.999, axis=0)
    top = (1 << qbits) - 1
    Xq = np.clip(np.rint((X - lo) / (hi - lo) * top), 0, top).astype(np.uint16)
    out = ROOT / "data" / f"jsc_q{qbits}.npz"
    np.savez_compressed(out, Xtr=Xq[tr], ytr=yi[tr], Xte=Xq[te], yte=yi[te], lo=lo, hi=hi,
                        classes=np.array(classes), qbits=qbits, seed=seed)
    print(f"{out}: train {len(tr)} test {nte} features {X.shape[1]} classes {classes}")
    print("class balance (train):", np.bincount(yi[tr]) / len(tr))
    return out


def load(qbits=10):
    """The prepared arrays as a dict (run prepare() once first)."""
    d = np.load(ROOT / "data" / f"jsc_q{qbits}.npz", allow_pickle=True)
    return {k: d[k] for k in d.files}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--qbits", type=int, default=10)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    prepare(a.qbits, a.seed)
