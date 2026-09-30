"""Two exhaustive campaigns of the same bitstream that differ only in the idle input vector (the vector the
DUT inputs rest on while a bit is flipped): how many bits change status, and how much the totals move.

  python analysis/idle_compare.py <campaign A> <campaign B>   -> results/idle_compare_<A>_<B>.json
Critical = at least one misprediction on the test set. Severe >= N/100, catastrophic >= N/10.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(cdir):
    summ = dict(kv.split("=", 1) for kv in (ROOT / "results" / cdir / "summary.txt").read_text().split())
    ev = {}
    for line in (ROOT / "results" / cdir / "events.tsv").read_text().splitlines():
        fi, w, b, mism, corr, flags, _ = map(int, line.split("\t"))
        if mism >= 0:
            ev[(fi, w, b)] = (mism, corr)
    return int(summ["ntest"]), int(summ["golden_corr"]), int(summ["inj"]), ev


def totals(ev, n, golden):
    m = [v[0] for v in ev.values() if v[0] > 0]
    return {"critical": len(m), "sum_mism": sum(m), "severe": sum(x >= n / 100 for x in m),
            "catastrophic": sum(x >= n / 10 for x in m),
            "acc_loss_sum": sum(golden - v[1] for v in ev.values() if v[0] > 0) / n}


def main():
    a, b = sys.argv[1], sys.argv[2]
    n, ga, inj_a, ea = load(a)
    _, gb, inj_b, eb = load(b)
    ca = {k for k, v in ea.items() if v[0] > 0}
    cb = {k for k, v in eb.items() if v[0] > 0}
    both = ca & cb
    same = sum(ea[k][0] == eb[k][0] for k in both)
    res = {
        "A": a, "B": b, "injected": [inj_a, inj_b], "A_totals": totals(ea, n, ga), "B_totals": totals(eb, n, gb),
        "critical_both": len(both), "critical_only_A": len(ca - cb), "critical_only_B": len(cb - ca),
        "changed_status": len(ca ^ cb), "changed_status_frac_of_union": len(ca ^ cb) / max(1, len(ca | cb)),
        "both_same_mism": same, "both_diff_mism": len(both) - same,
    }
    out = ROOT / f"results/idle_compare_{a.removeprefix('camp_')}_{b.removeprefix('camp_')}.json"
    out.write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
