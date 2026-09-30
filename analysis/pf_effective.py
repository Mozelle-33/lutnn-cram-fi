"""How often does a fault of the physical fault-aware training actually change a LUT input?

  python analysis/pf_effective.py [model]   (default dwn_md; hardware test vectors)
Training (train/models.py, noise_kind "phys") hits each LUT input with probability p; a hit input
freezes at its value for another sample (30 %) or is ANDed with another, random LUT input (70 %). A
freeze changes the input only when the two samples differ, an AND only when the input is 1 and the
other input 0. Prints the change probabilities per hit and the resulting rate for p = 2 % and 5 %,
to compare with inversion, which changes every hit input.
"""
import json
import sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "train"))
sys.path.insert(0, str(ROOT / "analysis"))
import models as M  # noqa: E402
from sw_faults import load_vectors  # noqa: E402


def main():
    model = sys.argv[1] if len(sys.argv) > 1 else "dwn_md"
    spec = json.loads((ROOT / f"models/{model}.json").read_text())
    X, _ = load_vectors(model)
    xm = M._thermo_np(spec, X).astype(np.int8)[:, np.asarray(spec["layers"][0]["mapping"]).reshape(-1)]
    rng = np.random.default_rng(0)
    n, c = xm.shape
    ch_freeze = float((xm[rng.permutation(n)] != xm).mean())
    ch_and = float(((xm == 1) & (xm[:, rng.integers(0, c, c)] == 0)).mean())
    per_hit = 0.3 * ch_freeze + 0.7 * ch_and
    print(f"P(change | freeze) {ch_freeze:.3f}, P(change | AND) {ch_and:.3f}, P(change | hit) {per_hit:.3f}")
    for p in (0.02, 0.05):
        print(f"p = {p:.0%}: inputs changed {100 * p * per_hit:.2f} %")


if __name__ == "__main__":
    main()
