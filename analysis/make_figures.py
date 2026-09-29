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
CAMPAIGNS = [  # (label, campaign dir, model, build)
    ("DWN-S", "camp_dwn_sm", "dwn_sm", "dwn_sm"),
    ("DWN-M", "camp_dwn_md", "dwn_md", "dwn_md"),
    ("DLGN", "camp_dlgn_a", "dlgn_a", "dlgn_a"),
    ("MLP", "camp_mlp_32_16", "mlp_32_16", "mlp_32_16"),
    ("DWN-MNIST", "camp_dwn_mnist", "dwn_mnist", "dwn_mnist"),
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


def hardening_table():
    """Hardening table of the old conference draft; returns the per-variant numbers."""
    L = [r"\begin{tabular}{lrrrrr}", r"\toprule",
         r"Variant & Acc. & Crit. & $\sum$mism. & $>$1\,\% & $>$10\,\% \\",
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
        L.append(f"{r['label']} & {data} & {100 * spec['meta']['test_acc']:.1f} & {int(res['dut_luts'])} & {int(res['dut_ffs'])} & "
                 f"{res['latency']} & {a['injected'] / 1e6:.2f} & {a['essential_in_range'] / 1e3:.0f} & {a['critical'] / 1e3:.1f} & "
                 f"{100 * a['critical_frac_essential']:.1f} & {a['critical'] / int(res['dut_luts']):.1f} & {a['fit_sea_level']:.1f} \\\\")
    L += [r"\bottomrule", r"\end{tabular}"]
    TVLSI.mkdir(exist_ok=True)
    (TVLSI / "table_models_tvlsi.tex").write_text("\n".join(L))


def hardening_table_tvlsi():
    """Hardening table for the journal paper: accuracy, LUTs, critical bits, summed mispredictions and
    severe / catastrophic upsets per variant (paper_tvlsi/table_hardening_tvlsi.tex)."""
    # the two retrained replicas (other seeds, no hardening) show the variation between training runs
    harden = HARDEN[:1] + [("\\quad retrained, seed 1", "camp_dwn_md_s1", "dwn_md_s1"),
                           ("\\quad retrained, seed 2", "camp_dwn_md_s2", "dwn_md_s2")] + HARDEN[1:] + [
        ("+ phys.\\ fault-aware (2\\,\\%)", "camp_dwn_md_pf2", "dwn_md_pf2"),
        ("+ phys.\\ fault-aware (5\\,\\%)", "camp_dwn_md_pf5", "dwn_md_pf5"),
        ("+ output-stage TMR", "camp_dwn_md_tmr", "dwn_md", "dwn_md_tmr"),
        ("+ fault-aware (2\\,\\%) + TMR", "camp_dwn_md_fa2_tmr", "dwn_md_fa2", "dwn_md_fa2_tmr")]
    L = [r"\begin{tabular}{lrrrrrr}", r"\toprule",
         r"Variant & Acc. & LUTs & Crit. & $\sum$mism. & $>$1\,\% & $>$10\,\% \\",
         r" & [\%] & & [k] & [M] & [k] & \\", r"\midrule"]
    for item in harden:
        label, cdir, model = item[:3]
        build = item[3] if len(item) > 3 else model
        if not event_files(cdir):
            continue
        spec = json.loads((ROOT / f"models/{model}.json").read_text())
        pb = (ROOT / f"hw/build/{build}/pblock.txt").read_text()
        luts = int(dict(kv.split("=") for kv in pb.split() if "=" in kv)["dut_luts"])
        n, s, t1, t10 = tail_counts(cdir)
        L.append(f"{label} & {100 * spec['meta']['test_acc']:.2f} & {luts} & {n / 1e3:.1f} & {s / 1e6:.2f} & {t1 / 1e3:.1f} & {t10} \\\\")
    L += [r"\bottomrule", r"\end{tabular}"]
    TVLSI.mkdir(exist_ok=True)
    (TVLSI / "table_hardening_tvlsi.tex").write_text("\n".join(L))


def fig_hardening():
    """Relative change of each metric versus DWN-M for the hardening variants (double-column figure).
    The first two groups are DWN-M retrained with other seeds and no hardening: the variation between
    training runs against which the retrained variants (fault-aware training) must be judged."""
    import numpy as np
    variants = [("Retrained\nseed 1", "camp_dwn_md_s1", "dwn_md_s1"), ("Retrained\nseed 2", "camp_dwn_md_s2", "dwn_md_s2"),
                ("Don't-care\nfill", "camp_dwn_md_dc", "dwn_md_dc"), ("Fault-aware\n2%", "camp_dwn_md_fa2", "dwn_md_fa2"),
                ("Fault-aware\n5%", "camp_dwn_md_fa5", "dwn_md_fa5"),
                ("Physical\nFA 2%", "camp_dwn_md_pf2", "dwn_md_pf2"), ("Physical\nFA 5%", "camp_dwn_md_pf5", "dwn_md_pf5"),
                ("Output\nTMR", "camp_dwn_md_tmr", "dwn_md_tmr"),
                ("Fault-aware\n2% + TMR", "camp_dwn_md_fa2_tmr", "dwn_md_fa2_tmr")]
    base = tail_counts("camp_dwn_md")
    base_luts = int(dict(kv.split("=") for kv in (ROOT / "hw/build/dwn_md/pblock.txt").read_text().split() if "=" in kv)["dut_luts"])
    rows, labels = [], []
    for label, cdir, build in variants:
        if not event_files(cdir):
            continue
        n, s, t1, t10 = tail_counts(cdir)
        luts = int(dict(kv.split("=") for kv in (ROOT / f"hw/build/{build}/pblock.txt").read_text().split() if "=" in kv)["dut_luts"])
        rows.append([100 * (n / base[0] - 1), 100 * (s / base[1] - 1), 100 * (t1 / base[2] - 1), 100 * (t10 / base[3] - 1),
                     100 * (luts / base_luts - 1)])
        labels.append(label)
    rows = np.array(rows)
    metrics = ["Critical bits", "$\\sum$ mispredictions", "Severe ($>$1%)", "Catastrophic ($>$10%)", "LUTs"]
    colors = ["#2b6cb0", "#90cdf4", "#dd6b20", "#c53030", "#a0aec0"]
    nctl = sum(1 for l in labels if l.startswith("Retrained"))
    fig, ax = plt.subplots(figsize=(7.16, 1.85))
    w = 0.16
    x = np.arange(len(labels))
    for k, (mname, col) in enumerate(zip(metrics, colors)):
        bars = ax.bar(x + (k - 2) * w, rows[:, k], width=w, color=col, label=mname)
        for b in list(bars)[:nctl]:          # controls drawn lighter
            b.set_alpha(0.45)
    if nctl:
        ax.axvline(nctl - 0.5, color="gray", lw=0.6, ls="--")
    ax.axhline(0, color="black", lw=0.5)
    ax.set_xticks(x, labels, fontsize=6.3)
    ax.tick_params(axis="x", length=0)
    ax.set_ylabel("Change vs. DWN-M [%]")
    from matplotlib.patches import Patch        # legend patches at full opacity (the controls are faded)
    ax.legend(handles=[Patch(color=c, label=m) for m, c in zip(metrics, colors)], fontsize=6, frameon=False, ncol=5,
              loc="lower center", bbox_to_anchor=(0.5, 1.0), handlelength=1.2, columnspacing=1.0)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout(pad=0.2)
    fig.savefig(PAPER / "fig_hardening.pdf")


def fig_severity():
    """Complementary CDF of mispredictions per critical bit (4096 vectors)."""
    import numpy as np
    runs = [("DWN-S", "camp_dwn_sm", "#90cdf4"), ("DWN-M", "camp_dwn_md", "#2b6cb0"),
            ("DLGN", "camp_dlgn_a", "#68d391"), ("MLP", "camp_mlp_32_16", "#dd6b20")]
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
        hardening_table_tvlsi()
        fig_hardening()
