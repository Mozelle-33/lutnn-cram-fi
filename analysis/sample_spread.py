"""Sampling spread of the critical-bit count for the table bits, where the parameter model gives the
outcome of every bit on any test sample.

  python analysis/sample_spread.py <K> <model | build:model> [...]   -> results/sample_spread.json
Draws K random samples of the hardware size n (without replacement) from the full test set, counts the
table bits that are critical on each, and reports mean, standard deviation and where the hardware
sample (hw/gen/<build>/vectors.mem) falls. Bits share samples, so their detection is correlated; the
simulation includes this, unlike a per-bit binomial estimate.
"""
import json
import sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
from test_coverage import DATA, flips  # noqa: E402
from sw_faults import load_vectors  # noqa: E402


def main():
    k = int(sys.argv[1])
    out = {}
    for arg in sys.argv[2:]:
        name, _, model = arg.partition(":")
        model = model or name
        spec = json.loads((ROOT / f"models/{model}.json").read_text())
        d = np.load(ROOT / f"data/{DATA[model]}.npz")
        X, y = d["Xte"].astype(np.int64), d["yte"]
        Xh, yh = load_vectors(name)
        n = len(yh)
        hw = int((flips(spec, Xh, yh)[0] > 0).sum())
        rng = np.random.default_rng(1)
        counts = []
        for _ in range(k):
            idx = np.sort(rng.choice(len(y), n, replace=False))
            counts.append(int((flips(spec, X[idx], y[idx])[0] > 0).sum()))
        c = np.array(counts)
        out[name] = {"n": n, "samples": k, "mean": float(c.mean()), "sd": float(c.std(ddof=1)),
                     "sd_rel": float(c.std(ddof=1) / c.mean()), "min": int(c.min()), "max": int(c.max()),
                     "hardware_sample": hw, "hardware_z": float((hw - c.mean()) / c.std(ddof=1))}
        print(name, out[name], flush=True)
        (ROOT / "results/sample_spread.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
