"""Paper figures and tables from the campaign analyses.

  python analysis/make_figures.py
Reads results/camp_*/analysis.json and route_attrib.json and writes into paper/:
  table_models.tex   per-model summary
  fig_composition.pdf  critical bits per model split into parameters / other LUT logic / routing /
                       other CLB / clock (stacked horizontal bars, thousands of bits)
  fig_routing.pdf    DWN routing-bit attribution to DUT stages
  numbers.json       every number quoted in the text
"""
from pathlib import Path
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
PAPER = ROOT / "paper"
FIT_PER_MB = 40.0   # UG116 v10.21 (2026) Table 1: 28 nm Kintex-7 CRAM real-time SER, as in analyze_campaign.py
CAMPAIGNS = [  # (label, campaign dir, model, build)
    ("DWN-S", "camp_dwn_sm", "dwn_sm", "dwn_sm"),
    ("DWN-M", "camp_dwn_md", "dwn_md", "dwn_md"),
    ("DLGN", "camp_dlgn_a", "dlgn_a", "dlgn_a"),
    ("MLP", "camp_mlp_32_16", "mlp_32_16", "mlp_32_16"),
    ("MLP-P", "camp_mlp_32_16_p70", "mlp_32_16_p70", "mlp_32_16_p70"),   # 70 % of the weights pruned
    ("DWN-MNIST", "camp_dwn_mnist_r", "dwn_mnist", "dwn_mnist_r"),  # 2048 randomly drawn test images
]
TVLSI = ROOT / "paper_tvlsi"
plt.rcParams.update({"font.size": 7, "font.family": "serif", "axes.linewidth": 0.6})


def event_files(cdir):
    """events.tsv of a finished campaign, or of its sub-campaigns (same rule as analyze_campaign.py)."""
    d = ROOT / "results" / cdir
    if (d / "summary.txt").exists():
        return [d / "events.tsv"]
    return sorted(s.parent / "events.tsv" for s in d.glob("*/summary.txt"))


def load():
    """Per-network rows: campaign analysis, model spec, DUT resources (pblock.txt), routing attribution."""
    rows = []
    for label, cdir, model, build in CAMPAIGNS:
        p = ROOT / "results" / cdir / "analysis.json"
        if not p.exists():
            continue
        a = json.loads(p.read_text())
        spec = json.loads((ROOT / f"models/{model}.json").read_text())
        pb = (ROOT / f"hw/build/{build}/pblock.txt").read_text()
        res = dict(kv.split("=") for kv in pb.split() if "=" in kv)
        ra = ROOT / "results" / cdir / "route_attrib.json"
        rows.append({"label": label, "a": a, "spec": spec, "res": res,
                     "route": json.loads(ra.read_text()) if ra.exists() else None})
    return rows


def composition(r):
    """Critical bits of one network split by resource; for DWNs the LUT-content bits of the LUT layer
    (the parameters) are separated from the other LUT logic (encoder, population count, arg-max)."""
    a = r["a"]
    cl = a["classes"]
    lut = cl.get("LUT_INIT", {}).get("critical", 0)
    param = a["lut_init_critical_by_module"].get("lut_layer", 0) if r["spec"]["type"] == "dwn" else 0
    return {"Parameters (LUT tables)": param, "Other LUT logic": lut - param,
            "Routing": cl.get("ROUTING", {}).get("critical", 0),
            "Other CLB config.": cl.get("CLB_OTHER", {}).get("critical", 0),
            "Clock": cl.get("HCLK", {}).get("critical", 0)}


def table(rows):
    """Per-network summary table of the old conference draft (paper/table_models.tex)."""
    L = [r"\begin{tabular}{lrrrrrr}", r"\toprule",
         r"Model & Acc. & LUTs & Bits & Ess. & Crit. & FIT \\",
         r" & [\%] & & [M] & [k] & [k] & \\", r"\midrule"]
    for r in rows:
        a, spec, res = r["a"], r["spec"], r["res"]
        L.append(f"{r['label']} & {100 * spec['meta']['test_acc']:.1f} & {int(res['dut_luts'])} & "
                 f"{a['injected'] / 1e6:.2f} & {a['essential_in_range'] / 1e3:.0f} & {a['critical'] / 1e3:.1f} & "
                 f"{a['fit_sea_level']:.1f} \\\\")
    L += [r"\bottomrule", r"\end{tabular}"]
    (PAPER / "table_models.tex").write_text("\n".join(L))


def fig_composition(rows):
    """Stacked bars: critical bits per network by resource, labelled with the parameter share."""
    cats = ["Parameters (LUT tables)", "Other LUT logic", "Routing", "Other CLB config.", "Clock"]
    colors = ["#2b6cb0", "#90cdf4", "#dd6b20", "#68d391", "#a0aec0"]
    fig, ax = plt.subplots(figsize=(3.45, 1.75))
    y = list(range(len(rows)))[::-1]
    left = [0.0] * len(rows)
    for cat, col in zip(cats, colors):
        vals = [composition(r)[cat] / 1e3 for r in rows]
        ax.barh(y, vals, left=left, color=col, height=0.62, label=cat, edgecolor="white", linewidth=0.3)
        left = [l + v for l, v in zip(left, vals)]
    for yi, r, tot in zip(y, rows, left):
        comp = composition(r)
        s = f"{tot:.0f}k"
        if comp["Parameters (LUT tables)"]:
            s += f"  (params {100 * comp['Parameters (LUT tables)'] / (tot * 1e3):.1f}%)"
        ax.text(tot + 6, yi, s, va="center", fontsize=6)
    ax.set_yticks(y, [r["label"] for r in rows])
    ax.set_xlim(0, max(left) * 1.32)
    ax.set_xlabel("Critical configuration bits (thousands)")
    ax.legend(fontsize=5.5, frameon=False, ncol=3, loc="lower center", bbox_to_anchor=(0.45, 1.0),
              handlelength=1.2, columnspacing=0.8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout(pad=0.2)
    fig.savefig(PAPER / "fig_composition.pdf")


def fig_routing(rows):
    """DWN-M: share of the critical routing bits per network stage (analysis/route_attrib.py)."""
    r = next((x for x in rows if x["label"] == "DWN-M" and x["route"]), None)
    if not r:
        return
    groups = r["route"]["by_group"]
    order = sorted(groups, key=lambda k: -groups[k]["critical"])
    fig, ax = plt.subplots(figsize=(3.45, 1.35))
    vals = [groups[k]["share"] * 100 for k in order]
    ax.barh(range(len(order))[::-1], vals, color="#dd6b20", height=0.6)
    ax.set_yticks(range(len(order))[::-1], order)
    ax.set_xlabel("Share of critical routing bits [%] (DWN-M)")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout(pad=0.2)
    fig.savefig(PAPER / "fig_routing.pdf")


HARDEN = [  # (label, campaign dir, model)
    ("DWN-M", "camp_dwn_md", "dwn_md"),
    ("+ don't-care fill", "camp_dwn_md_dc", "dwn_md_dc"),
    ("+ fault-aware (2\\,\\%)", "camp_dwn_md_fa2", "dwn_md_fa2"),
    ("+ fault-aware (5\\,\\%)", "camp_dwn_md_fa5", "dwn_md_fa5"),
]


def tail_counts(cdir, ntest=4096):
    """(# critical, sum of mismatches, # with >1 % of predictions changed, # with >10 %)."""
    n = s = t1 = t10 = 0
    for fp in event_files(cdir):
        for line in fp.read_text().splitlines():
            m = int(line.split("\t")[3])
            if m > 0:
                n += 1; s += m; t1 += m > ntest // 100; t10 += m > ntest // 10
    return n, s, t1, t10


# Hardening variants of DWN-M with their training replicas: (label, [(campaign dir, model, build)])
SEEDED = [
    ("DWN-M", [("camp_dwn_md", "dwn_md", "dwn_md"), ("camp_dwn_md_s1", "dwn_md_s1", "dwn_md_s1"),
               ("camp_dwn_md_s2", "dwn_md_s2", "dwn_md_s2")]),
    ("+ don't-care fill", [("camp_dwn_md_dc", "dwn_md_dc", "dwn_md_dc"),
                           ("camp_dwn_md_dc_s1", "dwn_md_dc_s1", "dwn_md_dc_s1"),
                           ("camp_dwn_md_dc_s2", "dwn_md_dc_s2", "dwn_md_dc_s2")]),
    ("+ SLICEL-only placement", [("camp_dwn_md_sl", "dwn_md", "dwn_md_sl")]),
    ("+ fault-aware (2\\,\\%)", [("camp_dwn_md_fa2", "dwn_md_fa2", "dwn_md_fa2"),
                                ("camp_dwn_md_fa2_s1", "dwn_md_fa2_s1", "dwn_md_fa2_s1"),
                                ("camp_dwn_md_fa2_s2", "dwn_md_fa2_s2", "dwn_md_fa2_s2")]),
    ("+ phys.\\ fault-aware (5\\,\\%)", [("camp_dwn_md_pf5", "dwn_md_pf5", "dwn_md_pf5"),
                                        ("camp_dwn_md_pf5_s1", "dwn_md_pf5_s1", "dwn_md_pf5_s1"),
                                        ("camp_dwn_md_pf5_s2", "dwn_md_pf5_s2", "dwn_md_pf5_s2")]),
    ("+ output-stage TMR", [("camp_dwn_md_tmr", "dwn_md", "dwn_md_tmr"),
                            ("camp_dwn_md_tmr_s1", "dwn_md_s1", "dwn_md_tmr_s1"),
                            ("camp_dwn_md_tmr_s2", "dwn_md_s2", "dwn_md_tmr_s2")]),
    ("+ fault-aware (2\\,\\%) + TMR", [("camp_dwn_md_fa2_tmr", "dwn_md_fa2", "dwn_md_fa2_tmr"),
                                      ("camp_dwn_md_fa2_tmr_s1", "dwn_md_fa2_s1", "dwn_md_fa2_tmr_s1"),
                                      ("camp_dwn_md_fa2_tmr_s2", "dwn_md_fa2_s2", "dwn_md_fa2_tmr_s2")]),
]
METRICS = ["crit", "smism", "gt1", "gt10"]
# exploratory single runs (fault-aware 5 %, physical fault-aware 2 %) and the first DWN-MNIST campaign
# (first 2048 test images, replaced by a random sample) whose data are released but not reported in the
# journal paper
NOT_REPORTED = {"camp_dwn_md_fa5", "camp_dwn_md_pf2", "camp_dwn_mnist"}
# second campaigns of already reported builds (another idle input vector, suffix _idle1479): counted in
# the injections, not as further builds in the parameter-model comparison
CONTROL = {p.name for p in (ROOT / "results").glob("camp_*_idle1479")}


def run_metrics(cdir, model, build):
    """Accuracy, DUT LUTs, essential bits and the four vulnerability metrics of one campaign."""
    spec = json.loads((ROOT / f"models/{model}.json").read_text())
    pb = dict(kv.split("=") for kv in (ROOT / f"hw/build/{build}/pblock.txt").read_text().split() if "=" in kv)
    a = json.loads((ROOT / "results" / cdir / "analysis.json").read_text())
    n, s, t1, t10 = tail_counts(cdir)
    return {"acc": 100 * spec["meta"]["test_acc"], "luts": int(pb["dut_luts"]), "ess": a["essential_in_range"],
            "crit": n, "smism": s, "gt1": t1, "gt10": t10}


def seeded_runs():
    """[(label, [metrics of each available replica])] for the SEEDED variants."""
    out = []
    for label, runs in SEEDED:
        ms = [run_metrics(*r) for r in runs if event_files(r[0]) and (ROOT / "results" / r[0] / "analysis.json").exists()]
        if ms:
            out.append((label, ms))
    return out


def hardening_table():
    """Hardening table of the old conference draft; returns the per-variant numbers."""
    L = [r"\begin{tabular}{lrrrrr}", r"\toprule",
         r"Variant & Acc. & Crit. & $\sum$mism. & $\geq$1\,\% & $\geq$10\,\% \\",
         r" & [\%] & [k] & [M] & [k] & \\", r"\midrule"]
    base = None
    out = {}
    for label, cdir, model in HARDEN:
        if not event_files(cdir):
            continue
        spec = json.loads((ROOT / f"models/{model}.json").read_text())
        n, s, t1, t10 = tail_counts(cdir)
        if base is None:
            base = (n, s, t1, t10)
        out[label] = {"acc": spec["meta"]["test_acc"], "critical": n, "sum_mism": s, "gt1pct": t1, "gt10pct": t10,
                      "d_crit": n / base[0] - 1, "d_mism": s / base[1] - 1, "d_gt1": t1 / base[2] - 1, "d_gt10": t10 / base[3] - 1}
        L.append(f"{label} & {100 * spec['meta']['test_acc']:.2f} & {n / 1e3:.1f} & {s / 1e6:.2f} & {t1 / 1e3:.1f} & {t10} \\\\")
    L += [r"\bottomrule", r"\end{tabular}"]
    (PAPER / "table_hardening.tex").write_text("\n".join(L))
    return out


def table_models_tvlsi(rows):
    """Table of networks and campaigns for the journal paper (paper_tvlsi/table_models_tvlsi.tex)."""
    L = [r"\begin{tabular}{llrrrrrrrrrr}", r"\toprule",
         r"Network & Data & Acc. & LUTs & FFs & Lat. & Bits & Ess. & Crit. & Crit./ & Crit./ & FIT \\",
         r" & & [\%] & & & [cyc] & [M] & [k] & [k] & Ess. [\%] & LUT & \\", r"\midrule"]
    for r in rows:
        a, spec, res = r["a"], r["spec"], r["res"]
        data = "MNIST" if "mnist" in r["label"].lower() else "JSC"
        fit = a["critical"] * FIT_PER_MB / 1e6       # recomputed with the current UG116 rate
        L.append(f"{r['label']} & {data} & {100 * spec['meta']['test_acc']:.1f} & {int(res['dut_luts'])} & {int(res['dut_ffs'])} & "
                 f"{res['latency']} & {a['injected'] / 1e6:.2f} & {a['essential_in_range'] / 1e3:.0f} & {a['critical'] / 1e3:.1f} & "
                 f"{100 * a['critical_frac_essential']:.1f} & {a['critical'] / int(res['dut_luts']):.1f} & {fit:.1f} \\\\")
    L += [r"\bottomrule", r"\end{tabular}"]
    TVLSI.mkdir(exist_ok=True)
    (TVLSI / "table_models_tvlsi.tex").write_text("\n".join(L))


def table_composition_tvlsi(rows):
    """Critical bits by fabric resource for every network (the numbers behind the composition result;
    paper_tvlsi/table_composition_tvlsi.tex). DLGN has no parameter column: its gate parameters do not
    map one-to-one to CRAM bits."""
    L = [r"\begin{tabular}{lrrrrrr}", r"\toprule",
         r"Network & Crit. & Param. & Other & Routing & Other & Clock \\",
         r" & [k] & [\%] & LUT [\%] & [\%] & CLB [\%] & [\%] \\", r"\midrule"]
    for r in rows:
        comp = composition(r)
        tot = sum(comp.values())
        sh = {k: 100 * v / tot for k, v in comp.items()}
        param = "--" if r["spec"]["type"] != "dwn" else f"{sh['Parameters (LUT tables)']:.1f}"
        L.append(f"{r['label']} & {tot / 1e3:.1f} & {param} & {sh['Other LUT logic']:.1f} & {sh['Routing']:.1f} & "
                 f"{sh['Other CLB config.']:.1f} & {sh['Clock']:.1f} \\\\")
    L += [r"\bottomrule", r"\end{tabular}"]
    TVLSI.mkdir(exist_ok=True)
    (TVLSI / "table_composition_tvlsi.tex").write_text("\n".join(L))


def hardening_table_tvlsi():
    """Hardening table for the journal paper (double column): per variant the number of training
    replicas, accuracy, LUTs, essential and critical bits, summed mispredictions and severe /
    catastrophic upsets; mean and standard deviation where there are several replicas
    (paper_tvlsi/table_hardening_tvlsi.tex)."""
    import numpy as np

    def fmt(vals, scale, digits):
        v = np.array(vals, dtype=float) / scale
        if len(v) == 1:
            return f"{v[0]:.{digits}f}"
        return f"{v.mean():.{digits}f}$\\pm${v.std(ddof=1):.{digits}f}"

    L = [r"\begin{tabular}{lrrrrrrrrr}", r"\toprule",
         r"Variant & Runs & Acc. & LUTs & Ess. & Crit. & Crit./Ess. & $\sum$mism. & $\geq$1\,\% & $\geq$10\,\% \\",
         r" & & [\%] & & [k] & [k] & [\%] & [M] & [k] & \\", r"\midrule"]
    for label, ms in seeded_runs():
        col = lambda k: [m[k] for m in ms]          # noqa: E731
        ratio = [100 * m["crit"] / m["ess"] for m in ms]
        L.append(" & ".join([label, str(len(ms)), fmt(col("acc"), 1, 2), fmt(col("luts"), 1, 0), fmt(col("ess"), 1e3, 0),
                             fmt(col("crit"), 1e3, 1), fmt(ratio, 1, 1), fmt(col("smism"), 1e6, 2),
                             fmt(col("gt1"), 1e3, 1), fmt(col("gt10"), 1, 0)]) + r" \\")
    L += [r"\bottomrule", r"\end{tabular}"]
    TVLSI.mkdir(exist_ok=True)
    (TVLSI / "table_hardening_tvlsi.tex").write_text("\n".join(L))


def composition_check():
    """Does fault-aware training (2 %) compose multiplicatively with output-stage TMR in every training
    replica? For each seed: remaining fraction FA2 x TMR (predicted) vs FA2+TMR (measured), relative to
    the unhardened network of the same seed. Writes results/composition_seeds.json."""
    groups = {label: runs for label, runs in SEEDED}
    names = ["DWN-M", "+ fault-aware (2\\,\\%)", "+ output-stage TMR", "+ fault-aware (2\\,\\%) + TMR"]
    out = []
    for s in range(3):
        runs = [groups[n][s] for n in names]
        if not all(event_files(r[0]) for r in runs):
            continue
        base, fa, tmr, both = [run_metrics(*r) for r in runs]
        row = {"seed": s}
        for k in METRICS:
            pred, meas = (fa[k] / base[k]) * (tmr[k] / base[k]), both[k] / base[k]
            row[k] = {"pred_remaining": pred, "meas_remaining": meas, "diff_pts": 100 * (meas - pred)}
        out.append(row)
    (ROOT / "results/composition_seeds.json").write_text(json.dumps(out, indent=1))
    return out


def hardening_stats():
    """Statistics behind the hardening section (results/hardening_stats.json).
    Every variant: change of each metric relative to the mean of the unhardened replicas (accuracy:
    difference in points), mean and standard deviation over its replicas, and for variants with
    several replicas Welch's t-test against the unhardened ones. TMR leaves the trained network
    unchanged, so it is also compared per seed with the same network without TMR."""
    import numpy as np
    from scipy import stats
    runs = dict(seeded_runs())
    base = runs["DWN-M"]
    out = {}
    for label, ms in runs.items():
        row = {"runs": len(ms)}
        for k in METRICS + ["luts", "ess", "acc"]:
            b = np.array([m[k] for m in base], float)
            v = np.array([m[k] for m in ms], float)
            rel = v - b.mean() if k == "acc" else 100 * (v / b.mean() - 1)
            e = {"mean": float(rel.mean()), "sd": float(rel.std(ddof=1)) if len(v) > 1 else None}
            if len(v) > 1 and label != "DWN-M":
                e["welch_p"] = float(stats.ttest_ind(v, b, equal_var=False).pvalue)
            row[k] = e
        out[label] = row
    groups = dict(SEEDED)
    paired = {}
    # variants that keep the trained network, compared with that network (same seed)
    for hard, plain in [("+ output-stage TMR", "DWN-M"), ("+ fault-aware (2\\,\\%) + TMR", "+ fault-aware (2\\,\\%)"),
                        ("+ don't-care fill", "DWN-M"), ("+ SLICEL-only placement", "DWN-M")]:
        rows = []
        for h, p in zip(groups[hard], groups[plain]):
            if event_files(h[0]) and event_files(p[0]):
                mh, mp = run_metrics(*h), run_metrics(*p)
                rows.append({k: 100 * (mh[k] / mp[k] - 1) for k in METRICS + ["luts"]})
        if rows:
            paired[f"{hard} vs {plain}"] = {
                k: {"mean": float(np.mean([r[k] for r in rows])),
                    "sd": float(np.std([r[k] for r in rows], ddof=1)) if len(rows) > 1 else None,
                    "per_seed": [r[k] for r in rows]} for k in rows[0]}
    res = {"vs_unhardened_mean": out, "tmr_per_seed": paired}
    (ROOT / "results/hardening_stats.json").write_text(json.dumps(res, indent=1))
    return res


def campaign_totals():
    """Injections, board time and average rate of every complete exhaustive campaign (the directories
    with an analysis.json), and their totals; results/campaign_totals.json."""
    runs = {}
    for a in sorted((ROOT / "results").glob("camp_*/analysis.json")):
        if a.parent.name in NOT_REPORTED:
            continue
        j = json.loads(a.read_text())
        runs[a.parent.name] = {"injected": j["injected"], "seconds": j["seconds"], "rate": j["injected"] / j["seconds"]}
        hs = j.get("dwn_lut_layer_hw_vs_sw")         # DWN: table bits compared with the parameter model
        if hs and a.parent.name not in CONTROL:
            runs[a.parent.name].update(param_bits=hs["bits"], param_exact=hs["exact_agree"])
    rates = [r["rate"] for r in runs.values()]
    dwn = [r for r in runs.values() if "param_bits" in r]
    out = {"campaigns": len(runs), "injected": sum(r["injected"] for r in runs.values()),
           "hours": sum(r["seconds"] for r in runs.values()) / 3600, "rate_min": min(rates), "rate_max": max(rates),
           "dwn_builds": len(dwn), "param_bits": sum(r["param_bits"] for r in dwn),
           "param_exact": sum(r["param_exact"] for r in dwn), "runs": runs}
    (ROOT / "results/campaign_totals.json").write_text(json.dumps(out, indent=1))
    return {k: v for k, v in out.items() if k != "runs"}


def fig_hardening():
    """Relative change of each metric versus the mean of the unhardened DWN-M replicas (double-column
    figure). Bars: mean over the training replicas of a variant; error bars: their standard deviation.
    The first group shows the replicas of the unhardened network themselves, i.e. the variation
    between training runs against which every retrained variant must be judged."""
    import numpy as np
    short = {"DWN-M": "DWN-M\n(3 runs)", "+ don't-care fill": "Don't-care\nfill",
             "+ SLICEL-only placement": "SLICEL\nonly",
             "+ fault-aware (2\\,\\%)": "Fault-aware\n2%", "+ fault-aware (5\\,\\%)": "Fault-aware\n5%",
             "+ phys.\\ fault-aware (2\\,\\%)": "Physical\nFA 2%", "+ phys.\\ fault-aware (5\\,\\%)": "Physical\nFA 5%",
             "+ output-stage TMR": "Output\nTMR", "+ fault-aware (2\\,\\%) + TMR": "Fault-aware\n2% + TMR"}
    runs = seeded_runs()
    base = {k: np.mean([m[k] for m in runs[0][1]]) for k in METRICS + ["luts"]}
    labels, mean, sd = [], [], []
    for label, ms in runs:
        rel = np.array([[100 * (m[k] / base[k] - 1) for k in METRICS + ["luts"]] for m in ms])
        labels.append(short.get(label, label))
        mean.append(rel.mean(0))
        sd.append(rel.std(0, ddof=1) if len(ms) > 1 else np.zeros(rel.shape[1]))
    mean, sd = np.array(mean), np.array(sd)
    metrics = ["Critical bits", "$\\sum$ mispredictions", "Severe ($\\geq$1%)", "Catastrophic ($\\geq$10%)", "LUTs"]
    colors = ["#2b6cb0", "#90cdf4", "#dd6b20", "#c53030", "#a0aec0"]
    fig, ax = plt.subplots(figsize=(7.16, 1.6))
    w = 0.16
    x = np.arange(len(labels))
    for k, (mname, col) in enumerate(zip(metrics, colors)):
        ax.bar(x + (k - 2) * w, mean[:, k], width=w, color=col, label=mname,
               yerr=sd[:, k], error_kw={"elinewidth": 0.6, "capsize": 1.2, "ecolor": "#333333"})
    ax.axvline(0.5, color="gray", lw=0.6, ls="--")
    ax.axhline(0, color="black", lw=0.5)
    ax.set_xticks(x, labels, fontsize=6.3)
    ax.tick_params(axis="x", length=0)
    ax.set_ylabel("Change vs. DWN-M [%]")
    ax.legend(fontsize=6, frameon=False, ncol=5, loc="lower center", bbox_to_anchor=(0.5, 1.0), handlelength=1.2,
              columnspacing=1.0)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout(pad=0.2)
    fig.savefig(PAPER / "fig_hardening.pdf")


def fig_severity():
    """Complementary CDF of mispredictions per critical bit (4096 vectors)."""
    import numpy as np
    runs = [("DWN-S", "camp_dwn_sm", "#90cdf4"), ("DWN-M", "camp_dwn_md", "#2b6cb0"),
            ("DLGN", "camp_dlgn_a", "#68d391"), ("MLP", "camp_mlp_32_16", "#dd6b20"),
            ("MLP-P", "camp_mlp_32_16_p70", "#b7791f")]
    fig, ax = plt.subplots(figsize=(3.45, 1.45))
    for label, cdir, col in runs:
        m = np.array([int(l.split("\t")[3]) for fp in event_files(cdir) for l in fp.read_text().splitlines()])
        m = np.sort(m[m > 0])
        if len(m) == 0:
            continue
        x = np.unique(m)
        ccdf = 1 - np.searchsorted(m, x, side="left") / len(m)
        ax.step(x, ccdf, where="post", color=col, lw=1.1, label=f"{label} ({len(m) / 1e3:.0f}k)")
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlabel("Mispredictions per upset (of 4096)")
    ax.set_ylabel("Fraction $\\geq x$")
    ax.axvline(41, color="gray", lw=0.5, ls=":"); ax.axvline(410, color="gray", lw=0.5, ls=":")
    ax.legend(fontsize=5.8, frameon=False, loc="lower left")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout(pad=0.2)
    fig.savefig(PAPER / "fig_severity.pdf")


def numbers(rows):
    """Every per-network number quoted in the text, written to paper/numbers.json."""
    out = {}
    for r in rows:
        a = r["a"]
        comp = composition(r)
        tot = sum(comp.values())
        out[r["label"]] = {"critical": a["critical"], "injected": a["injected"], "essential": a["essential_in_range"],
                           "crit_frac_ess": a["critical_frac_essential"], "fit": a["fit_sea_level"],
                           "acc": r["spec"]["meta"]["test_acc"], "luts": int(r["res"]["dut_luts"]),
                           "crit_per_lut": a["critical"] / int(r["res"]["dut_luts"]),
                           "composition_share": {k: v / tot for k, v in comp.items()},
                           "mode_bits": a["skipped_mode_bits"], "seconds": a.get("seconds"),
                           "hw_vs_sw": a.get("dwn_lut_layer_hw_vs_sw")}
    (PAPER / "numbers.json").write_text(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    rows = load()
    print([r["label"] for r in rows])
    if rows:
        table(rows)
        fig_composition(rows)
        fig_routing(rows)
        print(json.dumps(numbers(rows), indent=1))
        print(json.dumps(hardening_table(), indent=1))
        fig_severity()
        table_models_tvlsi(rows)
        table_composition_tvlsi(rows)
        hardening_table_tvlsi()
        fig_hardening()
        print(json.dumps(composition_check(), indent=1))
        print(json.dumps(campaign_totals(), indent=1))
        hardening_stats()
