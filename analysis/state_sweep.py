"""How much does the injection state move the vulnerability of the learned connectivity?

  python analysis/state_sweep.py <build> <tag frozen 0> <tag frozen 1> <model> [n vectors]
      -> results/state_sweep_<build>.json
route_model.py run with --frozen 0 and --frozen 1 gives, for every evaluable configuration bit on the
routes of the thermometer nets, the predicted outcome for both values of a disconnected input; the other
bits do not depend on the state. Taking each of the first n test vectors (default all) as the idle
state selects, per freeze bit, the outcome for the thermometer value of that vector, and gives the
number of critical bits and the summed mispredictions of the evaluable connectivity bits as a
distribution over injection states; with --leak f, a frozen 0 is taken as 1 with probability f (the
fraction of frozen 0s that turned into 1 within 0.1 s in the hold test), as an expected value.
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
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    leak = float(sys.argv[sys.argv.index("--leak") + 1]) if "--leak" in sys.argv else 0.0
    if "--leak" in sys.argv:
        args.remove(sys.argv[sys.argv.index("--leak") + 1])
    build, t0, t1, model = args[:4]
    spec = json.loads((ROOT / f"models/{model}.json").read_text())
    X, _ = load_vectors(build)
    n = int(args[4]) if len(args) > 4 else len(X)
    tb = M._thermo_np(spec, X[:n])
    a = json.loads((ROOT / f"results/route_model_{build}_{t0}_bits.json").read_text())
    b = json.loads((ROOT / f"results/route_model_{build}_{t1}_bits.json").read_text())
    fk, m0, m1, fixed_m = [], [], [], []
    for x, y in zip(a, b):
        assert x[:4] == y[:4]
        if x[7] is None:
            continue
        if x[4] == "freeze":
            fk.append(x[0])
            m0.append(x[7][0])
            m1.append(y[7][0])
        else:
            fixed_m.append(x[7][0])
    fk, m0, m1 = np.array(fk), np.array(m0, float), np.array(m1, float)
    fixed_m = np.array(fixed_m, float)
    base_crit, base_sum = int((fixed_m > 0).sum()), float(fixed_m.sum())
    v = tb[:, fk].astype(float)                    # frozen value of every freeze bit per idle state
    p1 = v + (1 - v) * leak                        # probability that the input reads 1
    crit = base_crit + ((1 - p1) * (m0 > 0) + p1 * (m1 > 0)).sum(1)
    summ = base_sum + ((1 - p1) * m0 + p1 * m1).sum(1)
    res = {"build": build, "states": n, "leak": leak, "evaluable_bits": int(len(fk) + len(fixed_m)),
           "freeze_bits": int(len(fk)),
           "critical": {"mean": float(crit.mean()), "sd": float(crit.std()), "min": float(crit.min()),
                        "max": float(crit.max()), "at_1024": float(crit[1024]) if n > 1024 else None,
                        "at_1479": float(crit[1479]) if n > 1479 else None},
           "sum_mism": {"mean": float(summ.mean()), "sd": float(summ.std()), "min": float(summ.min()),
                        "max": float(summ.max()), "at_1024": float(summ[1024]) if n > 1024 else None,
                        "at_1479": float(summ[1479]) if n > 1479 else None,
                        "pct_1024": float((summ < summ[1024]).mean()) if n > 1024 else None,
                        "pct_1479": float((summ < summ[1479]).mean()) if n > 1479 else None}}
    tag = f"_leak{leak:g}" if leak else ""
    (ROOT / f"results/state_sweep_{build}{tag}.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
