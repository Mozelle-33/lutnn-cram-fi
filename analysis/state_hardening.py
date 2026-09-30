"""Does the injection state change the hardening comparison? The unhardened, fault-aware (2 %) and
physical fault-aware (5 %) replicas of DWN-M, each campaign repeated with the inputs resting on test
vector 1479 instead of 1024 (campaign directories with the suffix _idle1479).

  python analysis/state_hardening.py   -> results/state_hardening.json
For each state, as in the hardening table: every metric of every replica relative to the mean of the
unhardened replicas at the same state, mean and standard deviation over the replicas, and Welch's
t-test; further, per replica, the change of every metric from the first to the second state, and the
change relative to the unhardened mean over both states together.
"""
import json
from pathlib import Path
import numpy as np
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
VARIANTS = {"DWN-M": ["dwn_md", "dwn_md_s1", "dwn_md_s2"],
            "fault-aware 2%": ["dwn_md_fa2", "dwn_md_fa2_s1", "dwn_md_fa2_s2"],
            "phys. fault-aware 5%": ["dwn_md_pf5", "dwn_md_pf5_s1", "dwn_md_pf5_s2"]}
STATES = {"idle 1024": "", "idle 1479": "_idle1479"}
METRICS = ["critical", "sum_mism", "severe", "catastrophic"]


def totals(cdir):
    """Critical bits, summed mispredictions, severe (>= N/100) and catastrophic (>= N/10) upsets."""
    summ = dict(kv.split("=", 1) for kv in (cdir / "summary.txt").read_text().split())
    n = int(summ["ntest"])
    m = [int(line.split("\t")[3]) for line in (cdir / "events.tsv").read_text().splitlines()]
    m = [x for x in m if x > 0]
    return {"critical": len(m), "sum_mism": sum(m), "severe": sum(x >= n / 100 for x in m),
            "catastrophic": sum(x >= n / 10 for x in m)}


def main():
    raw = {s: {v: [totals(ROOT / "results" / f"camp_{b}{suf}") for b in builds] for v, builds in VARIANTS.items()}
           for s, suf in STATES.items()}
    out = {"raw": raw, "vs_unhardened": {}, "state_change": {}}
    for s in STATES:
        base = raw[s]["DWN-M"]
        res = {}
        for v, runs in raw[s].items():
            row = {}
            for k in METRICS:
                b = np.array([r[k] for r in base], float)
                x = np.array([r[k] for r in runs], float)
                rel = 100 * (x / b.mean() - 1)
                row[k] = {"mean": float(rel.mean()), "sd": float(rel.std(ddof=1))}
                if v != "DWN-M":
                    row[k]["welch_p"] = float(stats.ttest_ind(x, b, equal_var=False).pvalue)
            res[v] = row
        out["vs_unhardened"][s] = res
    # both states together: the mean of the two states per replica
    both = {}
    base = [{k: (a[k] + b[k]) / 2 for k in METRICS} for a, b in zip(raw["idle 1024"]["DWN-M"], raw["idle 1479"]["DWN-M"])]
    for v in VARIANTS:
        runs = [{k: (a[k] + b[k]) / 2 for k in METRICS} for a, b in zip(raw["idle 1024"][v], raw["idle 1479"][v])]
        row = {}
        for k in METRICS:
            b = np.array([r[k] for r in base], float)
            x = np.array([r[k] for r in runs], float)
            rel = 100 * (x / b.mean() - 1)
            row[k] = {"mean": float(rel.mean()), "sd": float(rel.std(ddof=1))}
            if v != "DWN-M":
                row[k]["welch_p"] = float(stats.ttest_ind(x, b, equal_var=False).pvalue)
        both[v] = row
    out["vs_unhardened"]["both states"] = both
    for v in VARIANTS:
        out["state_change"][v] = {k: [100 * (b[k] / a[k] - 1) for a, b in zip(raw["idle 1024"][v], raw["idle 1479"][v])]
                                  for k in METRICS}
    (ROOT / "results/state_hardening.json").write_text(json.dumps(out, indent=1))
    for s, res in out["vs_unhardened"].items():
        for v, row in res.items():
            print(f"{s:11s} {v:22s} " + "  ".join(
                f"{k} {row[k]['mean']:+6.1f}+-{row[k]['sd']:4.1f}" + (f" P={row[k]['welch_p']:.3f}" if 'welch_p' in row[k] else "")
                for k in METRICS))
    for v, ch in out["state_change"].items():
        print(f"state change {v:22s} " + "  ".join(f"{k} " + ",".join(f"{x:+.1f}" for x in ch[k]) for k in METRICS))


if __name__ == "__main__":
    main()
