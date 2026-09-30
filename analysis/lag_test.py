"""Do upsets change their effect after the test that follows them? DWN-M retested ~1 ms after the upset.

  python analysis/lag_test.py lists       -> results/lag_list_dwn_md_{sample,severe}.txt
  xsdb fi/hold_test.tcl hw/build/dwn_md/fi_dwn_md.bit results/lag_list_dwn_md_sample.txt \
       results/lag_dwn_md_sample.tsv 4096 72 967 0      (and the same for the severe list)
  python analysis/lag_test.py estimate    -> results/lag_dwn_md.json
The campaign tests right after the SEM has flipped the bit (first vector about 25 us after the upset);
fi/hold_test.tcl flips it from the host, which starts the test about 1 ms later. Every severe bit
(m_b >= N_t/100) is retested, the other critical bits and the silent bits in a random sample; the
long-term totals are the retested severe bits plus ratio (critical) and expansion (silent) estimates
of the rest, with bootstrap intervals.
"""
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
R = ROOT / "results"
CAMP = R / "camp_dwn_md"
NT = 4096
N_SAMPLE_CRIT, N_SAMPLE_SILENT, SEED = 2000, 20000, 20261001


def campaign():
    ev = {}
    for line in (CAMP / "events.tsv").read_text().splitlines():
        f = [int(x) for x in line.split("\t")]
        if f[3] > 0:
            ev[(f[0], f[1], f[2])] = (f[3], f[4])
    skip = {tuple(int(x) for x in line.split()[:3]) for line in (CAMP / "skip.txt").read_text().splitlines()}
    return ev, skip


def lists():
    ev, skip = campaign()
    rng = random.Random(SEED)
    cs = rng.sample(sorted(ev), N_SAMPLE_CRIT)
    silent = set()
    while len(silent) < N_SAMPLE_SILENT:
        k = (rng.randint(72, 967), rng.randint(0, 100), rng.randint(0, 31))
        if k not in ev and k not in skip:
            silent.add(k)
    with open(R / "lag_list_dwn_md_sample.txt", "w") as f:
        for k in cs:
            f.write(f"{k[0]} {k[1]} {k[2]} {ev[k][0]} {ev[k][1]}\n")
        for k in sorted(silent):
            f.write(f"{k[0]} {k[1]} {k[2]} 0 3133\n")
    with open(R / "lag_list_dwn_md_severe.txt", "w") as f:
        for k in sorted(ev):
            if ev[k][0] >= NT / 100:
                f.write(f"{k[0]} {k[1]} {k[2]} {ev[k][0]} {ev[k][1]}\n")


def outcomes(name):
    """(campaign m_b, delayed m_b) per retested bit."""
    rows = []
    for line in (R / name).read_text().splitlines():
        f = line.split("\t")
        rows.append((int(f[6]), int(f[4])))
    return rows


def estimate():
    ev, skip = campaign()
    n_crit = len(ev)
    n_silent = 896 * 101 * 32 - len(skip) - n_crit
    sum_m = sum(v[0] for v in ev.values())
    n_sev = sum(1 for v in ev.values() if v[0] >= NT / 100)
    n_cat = sum(1 for v in ev.values() if v[0] >= NT / 10)
    sev = outcomes("lag_dwn_md_severe.tsv")
    smp = outcomes("lag_dwn_md_sample.tsv")
    low = [r for r in smp if 0 < r[0] < NT / 100]
    sil = [r for r in smp if r[0] == 0]
    n_low = n_crit - len(sev)
    sum_low = sum_m - sum(e for e, _ in sev)

    def totals(low, sil):
        frac = lambda rows, c: sum(1 for r in rows if c(r)) / len(rows)          # noqa: E731
        ratio = sum(m for _, m in low) / sum(e for e, _ in low)
        return {
            "critical": n_crit - sum(1 for _, m in sev if m == 0) - n_low * frac(low, lambda r: r[1] == 0)
                        + n_silent * frac(sil, lambda r: r[1] > 0),
            "sum_mism": sum(m for _, m in sev) + ratio * sum_low + n_silent * sum(m for _, m in sil) / len(sil),
            "severe": sum(1 for _, m in sev if m >= NT / 100) + n_low * frac(low, lambda r: r[1] >= NT / 100)
                      + n_silent * frac(sil, lambda r: r[1] >= NT / 100),
            "catastrophic": sum(1 for _, m in sev if m >= NT / 10) + n_low * frac(low, lambda r: r[1] >= NT / 10)
                            + n_silent * frac(sil, lambda r: r[1] >= NT / 10)}

    base = {"critical": n_crit, "sum_mism": sum_m, "severe": n_sev, "catastrophic": n_cat}
    est = totals(low, sil)
    rng = random.Random(1)
    boot = {k: [] for k in est}
    for _ in range(2000):
        b = totals([low[rng.randrange(len(low))] for _ in low], [sil[rng.randrange(len(sil))] for _ in sil])
        for k in b:
            boot[k].append(b[k] / base[k] - 1)
    res = {"campaign": base, "delayed_estimate": est,
           "change": {k: est[k] / base[k] - 1 for k in est},
           "change_ci95": {k: [sorted(v)[len(v) // 40], sorted(v)[len(v) - len(v) // 40 - 1]] for k, v in boot.items()},
           "retested": {"severe_all": len(sev), "critical_sample": len(low) + sum(1 for r in smp if r[0] >= NT / 100),
                        "silent_sample": len(sil)},
           "severe_bits_unchanged": sum(1 for e, m in sev if e == m),
           "silent_sample_critical_when_delayed": sum(1 for _, m in sil if m > 0)}
    (R / "lag_dwn_md.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    {"lists": lists, "estimate": estimate}[sys.argv[1]]()
