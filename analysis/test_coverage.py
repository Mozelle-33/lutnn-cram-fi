"""How much do the hardware test vectors miss? Parameter bits evaluated on the full test set.

  python analysis/test_coverage.py [model ...]
For every table bit of a DWN, the parameter fault model gives the number of changed predictions on
the full test set (JSC 166 k, MNIST 10 k) and on the first NVEC vectors used in hardware (4096 for
JSC, 2048 for MNIST; hw/gen/<model>/meta.json). Because
the hardware result equals the model for these bits (Section "exact"), the comparison measures the
detection coverage of the 4096-vector campaign for critical, severe and catastrophic bits.
Writes results/test_coverage.json.
"""
from pathlib import Path
import json
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
from sw_faults import dwn_single_flips, dwn2_single_flips  # noqa: E402

DATA = {"dwn_sm": "jsc_q10", "dwn_md": "jsc_q10", "dwn_md_fa2": "jsc_q10", "dwn_mnist": "mnist_q1"}


def flips(spec, X, y):
    """Mispredictions and correct counts for every table bit (single- or two-layer DWN), flattened."""
    if len(spec["layers"]) == 1:
        mism, corr, c0 = dwn_single_flips(spec, X, y)
        return mism.ravel(), corr.ravel(), c0
    ml, cl, c0 = dwn2_single_flips(spec, X, y)
    return np.concatenate([m.ravel() for m in ml]), np.concatenate([c.ravel() for c in cl]), c0


def main():
    """Coverage statistics per model, merged into results/test_coverage.json."""
    names = sys.argv[1:] or ["dwn_sm", "dwn_md", "dwn_mnist"]
    out = {}
    for name in names:
        spec = json.loads((ROOT / f"models/{name}.json").read_text())
        d = np.load(ROOT / f"data/{DATA[name]}.npz")
        X, y = d["Xte"].astype(np.int64), d["yte"]
        n_full = len(y)
        n_hw = json.loads((ROOT / f"hw/gen/{name}/meta.json").read_text())["NVEC"]
        m_full, _, _ = flips(spec, X, y)
        m_hw, _, _ = flips(spec, X[:n_hw], y[:n_hw])
        crit_f, crit_h = m_full > 0, m_hw > 0
        assert not (crit_h & ~crit_f).any()          # the hardware vectors are a subset
        rate = m_full / n_full
        missed = crit_f & ~crit_h
        p_det = 1 - (1 - rate[crit_f]) ** n_hw        # detection probability for a random subset
        sev_f, sev_h = rate >= 0.01, m_hw >= n_hw / 100
        cat_f, cat_h = rate >= 0.10, m_hw >= n_hw / 10
        r = {
            "bits": int(m_full.size), "n_full": n_full, "n_hw": n_hw,
            "critical_full": int(crit_f.sum()), "critical_hw": int(crit_h.sum()),
            "coverage": float(crit_h.sum() / crit_f.sum()),
            "coverage_expected_random_subset": float(p_det.mean()),
            "missed": int(missed.sum()),
            "missed_rate_mean": float(rate[missed].mean()) if missed.any() else 0.0,
            "missed_rate_max": float(rate[missed].max()) if missed.any() else 0.0,
            "missed_share_of_mispredictions": float(m_full[missed].sum() / m_full.sum()),
            "severe_full": int(sev_f.sum()), "severe_hw": int(sev_h.sum()), "severe_both": int((sev_f & sev_h).sum()),
            "catastrophic_full": int(cat_f.sum()), "catastrophic_hw": int(cat_h.sum()),
            "catastrophic_both": int((cat_f & cat_h).sum()),
        }
        out[name] = r
        print(name, json.dumps(r))
    p = ROOT / "results/test_coverage.json"
    old = json.loads(p.read_text()) if p.exists() else {}
    old.update(out)
    p.write_text(json.dumps(old, indent=1))


if __name__ == "__main__":
    main()
