"""Accumulated upsets: K simultaneous CRAM upsets in hardware vs the parameter bit-flip model.

  python analysis/multi_upset.py gen  <campaign> <out_trials> <seed>      # trial lists for fi/multi_upset.tcl
  python analysis/multi_upset.py sw   <model> [ntrials]                    # parameter-model Monte Carlo
  python analysis/multi_upset.py pred <model> <campaign>                   # additive single-bit prediction
  python analysis/multi_upset.py plot                                      # fig_multi.pdf from all results

The hardware x-axis is the per-bit upset probability p = K / N over the N injectable bits of the
network's region (the exhaustive campaign's bits minus the LUT-mode bits). The parameter model flips
each parameter bit with the same probability p: DWN table entries, DLGN gate truth-table bits, MLP
weight (6-bit) and bias (two's complement) bits, and is evaluated on the same test vectors.
"""
from pathlib import Path
import json
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "train"))
sys.path.insert(0, str(ROOT / "analysis"))
import models as M  # noqa: E402
from sw_faults import load_vectors  # noqa: E402

# per-bit upset probabilities injected in hardware (per campaign) and trials per probability;
# the parameter model is evaluated over a wider range (P_SW)
P_HW = {"camp_dwn_md": [3e-6, 1e-5, 3e-5, 1e-4, 3e-4, 1e-3],
        "camp_dlgn_a": [3e-6, 1e-5, 3e-5, 1e-4, 3e-4, 1e-3],
        "camp_mlp_32_16": [1e-6, 3e-6, 1e-5, 3e-5, 1e-4],
        "camp_mlp_32_16_p70": [1e-6, 3e-6, 1e-5, 3e-5, 1e-4]}
NETS = ["dwn_md", "dlgn_a", "mlp_32_16", "mlp_32_16_p70"]
TRIALS = {1e-6: 100, 3e-6: 100, 1e-5: 60, 3e-5: 40, 1e-4: 30, 3e-4: 20, 1e-3: 10}
P_SW = [1e-6, 3e-6, 1e-5, 3e-5, 1e-4, 3e-4, 1e-3, 3e-3, 1e-2, 3e-2, 1e-1]
MLP_BIAS_BITS = [14, 8, 8]   # two's-complement width of the biases of the three MLP layers


def subcampaigns(cdir):
    """The campaign directory itself, or its sub-campaigns (split runs such as r1/r2)."""
    d = ROOT / "results" / cdir
    return [d] if (d / "summary.txt").exists() else sorted(s.parent for s in d.glob("*/summary.txt"))


def region(cdir):
    """[(f0, nw, w0, lin0, lin1, skipset)] for the sub-campaigns, and the number of injectable bits."""
    out, n = [], 0
    for sub in subcampaigns(cdir):
        s = dict(kv.split("=", 1) for kv in (sub / "summary.txt").read_text().split())
        f0, f1, w0, w1 = int(s["far_first"]), int(s["far_last"]), int(s["word_lo"]), int(s["word_hi"])
        nw = w1 - w0 + 1
        lin0 = int(s.get("start_lin", 0))
        lin1 = int(s.get("end_lin", (f1 - f0 + 1) * nw * 32))
        skip = set()
        if s.get("skipfile", "-") != "-":
            sp = Path(s["skipfile"]); sp = sp if sp.is_absolute() else ROOT / sp
            for line in sp.read_text().split("\n"):
                if line.strip():
                    a, b, c = map(int, line.split()[:3])
                    skip.add(((a - f0) * nw + (b - w0)) * 32 + c)
        nsk = sum(1 for x in skip if lin0 <= x < lin1)
        out.append((f0, nw, w0, lin0, lin1, skip))
        n += (lin1 - lin0) - nsk
    return out, n


def gen(cdir, out_path, seed, mult=1.0):
    """Trial lists for fi/multi_upset.tcl: for each p, TRIALS[p]*mult trials of K = p*N distinct bits
    drawn uniformly from the injectable bits of the campaign (LUT-mode bits excluded)."""
    subs, n = region(cdir)
    # sub-campaigns of one network cover disjoint ranges; r1/r2 of a split run share f0 and differ in lin
    spans = [(s, s[4] - s[3]) for s in subs]
    tot = sum(w for _, w in spans)
    rng = np.random.default_rng(seed)
    lines = []
    tid = 0
    for p in P_HW[cdir]:
        k = max(1, int(round(p * n)))
        for _ in range(int(round(TRIALS[p] * mult))):
            chosen = set()
            while len(chosen) < k:
                # rejection sampling: draw a linear bit index over all sub-campaign ranges and
                # redraw if it is a skipped (mode) bit or already chosen
                u = int(rng.integers(tot))
                for (f0, nw, w0, lin0, lin1, skip), w in spans:
                    if u < w:
                        lin = lin0 + u
                        if lin not in skip:
                            t = lin // 32
                            chosen.add((f0 + t // nw, w0 + t % nw, lin % 32))
                        break
                    u -= w
            bits = sorted(chosen)
            lines.append(f"{tid} {k} {p:g} " + " ".join(f"{a} {b} {c}" for a, b, c in bits))
            tid += 1
    Path(out_path).write_text("\n".join(lines) + "\n")
    print(f"{cdir}: N={n} injectable bits; {tid} trials; K per p:",
          {p: max(1, int(round(p * n))) for p in P_HW[cdir]})


def param_bits(spec):
    """Number of parameter bits the parameter model can flip: DWN table entries, 4 truth-table bits
    per DLGN gate, 6-bit MLP weights plus the biases."""
    if spec["type"] == "dwn":
        return sum(np.asarray(L["tables"]).size for L in spec["layers"])
    if spec["type"] == "dlgn":
        return 4 * sum(len(L["gate"]) for L in spec["layers"])
    return sum(np.asarray(L["w"]).size * 6 + len(L["b"]) * bb for L, bb in zip(spec["layers"], MLP_BIAS_BITS))


class ParamModel:
    """Prediction with a set of parameter bits (flat indices, see param_bits) inverted; the parts of
    the computation that flips cannot change (thermometer code, first-layer addresses) are cached."""

    def __init__(self, spec, X):
        self.spec, self.X = spec, X
        self.kind = spec["type"]
        if self.kind in ("dwn", "dlgn"):
            self.x0 = M._thermo_np(spec, X)
        if self.kind == "dwn":
            assert len(spec["layers"]) == 1
            mp = np.asarray(spec["layers"][0]["mapping"])
            self.tab = np.asarray(spec["layers"][0]["tables"], dtype=np.uint8)
            self.addr = np.zeros((len(X), mp.shape[0]), dtype=np.int64)
            for l in range(mp.shape[1]):
                self.addr |= self.x0[:, mp[:, l]].astype(np.int64) << l
            self.rows = np.arange(mp.shape[0])[None, :]
        elif self.kind == "dlgn":
            tt = np.array(M.GATE_TT, dtype=np.uint8)
            self.g = [tt[np.asarray(L["gate"])] for L in spec["layers"]]
            self.ab = [(np.asarray(L["a"]), np.asarray(L["b"])) for L in spec["layers"]]

    def pred(self, idx):
        spec = self.spec
        if self.kind == "dwn":
            t = self.tab.copy()
            t.flat[idx] ^= 1
            s = t[self.rows, self.addr].reshape(len(self.X), spec["classes"], -1).sum(-1)
            return M.argmax_first(s)
        if self.kind == "dlgn":
            x, off = self.x0, 0
            for g0, (a, b) in zip(self.g, self.ab):
                g = g0.copy()
                sel = idx[(idx >= off) & (idx < off + 4 * len(g))] - off
                np.bitwise_xor.at(g, sel // 4, (1 << (sel % 4)).astype(np.uint8))
                off += 4 * len(g)
                x = (g[None, :] >> ((x[:, a] << 1) | x[:, b])) & 1
            s = x.reshape(x.shape[0], spec["classes"], -1).sum(-1)
            return M.argmax_first(s)
        layers, off = [], 0
        for L, bb in zip(spec["layers"], MLP_BIAS_BITS):
            w = np.asarray(L["w"], dtype=np.int64).copy()
            b = np.asarray(L["b"], dtype=np.int64).copy()
            for i in idx[(idx >= off) & (idx < off + w.size * 6)] - off:
                j, bit = divmod(int(i), 6)
                v = (int(w.flat[j]) & 63) ^ (1 << bit)
                w.flat[j] = v - 64 if v >= 32 else v
            off += w.size * 6
            for i in idx[(idx >= off) & (idx < off + len(b) * bb)] - off:
                j, bit = divmod(int(i), bb)
                v = (int(b[j]) & ((1 << bb) - 1)) ^ (1 << bit)
                b[j] = v - (1 << bb) if v >= (1 << (bb - 1)) else v
            off += len(b) * bb
            layers.append({"w": w, "b": b, "shift": L["shift"]})
        return M.qmlp_forward_spec({**spec, "layers": layers}, self.X)[1]


def sw(model, ntrials=200):
    """Parameter bit-flip model: for each p in P_SW, ntrials Monte-Carlo trials in which every
    parameter bit flips independently with probability p (Binomial number of flips), evaluated on
    the hardware test vectors. Results go to results/multi_upset_sw.json."""
    spec = json.loads((ROOT / f"models/{model}.json").read_text())
    X, y = load_vectors(model)
    nb = param_bits(spec)
    pm = ParamModel(spec, X)
    forward_flipped = lambda spec_, X_, idx: pm.pred(idx)  # noqa: E731
    pred0 = forward_flipped(spec, X, np.array([], dtype=np.int64))
    rng = np.random.default_rng(1)
    res = {"model": model, "param_bits": nb, "ntest": len(y), "correct0": int((pred0 == y).sum()), "p": {}}
    for p in P_SW:
        acc, mis = [], []
        for _ in range(ntrials):
            k = rng.binomial(nb, p)
            idx = np.sort(rng.choice(nb, size=k, replace=False)) if k else np.array([], dtype=np.int64)
            pr = forward_flipped(spec, X, idx)
            acc.append(int((pr == y).sum())); mis.append(int((pr != pred0).sum()))
        res["p"][f"{p:g}"] = {"correct_mean": float(np.mean(acc)), "correct_std": float(np.std(acc)),
                              "mism_mean": float(np.mean(mis)), "trials": ntrials}
        print(model, f"p={p:g}", f"dAcc={100 * (res['correct0'] - np.mean(acc)) / len(y):.3f} pts",
              f"mism={np.mean(mis):.1f}")
    out = ROOT / "results/multi_upset_sw.json"
    old = json.loads(out.read_text()) if out.exists() else {}
    old[model] = res
    out.write_text(json.dumps(old, indent=1))


def pred(model, cdir):
    """Additive prediction from the exhaustive single-bit campaign: E[loss(K)] = K * mean loss per bit."""
    subs, n = region(cdir)
    dcorr = dmis = 0
    golden = None
    for sub in subcampaigns(cdir):
        s = dict(kv.split("=", 1) for kv in (sub / "summary.txt").read_text().split())
        golden = int(s["golden_corr"])
        for line in (sub / "events.tsv").read_text().splitlines():
            f = line.split("\t")
            if int(f[3]) < 0:
                continue
            dcorr += golden - int(f[4]); dmis += int(f[3])
    res = {"campaign": cdir, "N": n, "golden": golden, "dcorr_per_bit": dcorr / n, "mism_per_bit": dmis / n}
    out = ROOT / "results/multi_upset_pred.json"
    old = json.loads(out.read_text()) if out.exists() else {}
    old[model] = res
    out.write_text(json.dumps(old, indent=1))
    print(json.dumps(res))


def hw_summary():
    """Per network and p: accuracy loss mean / standard error over all hardware trial batches."""
    import re
    out = {}
    for m in NETS:
        by = {}
        # hw_<model>.tsv and further batches hw_<model>_b*.tsv (not the files of another model
        # whose name merely starts with this one, e.g. the pruned MLP)
        files = [fp for fp in sorted((ROOT / "results/multi").glob(f"hw_{m}*.tsv"))
                 if re.fullmatch(rf"hw_{re.escape(m)}(_b\d*)?\.tsv", fp.name)]
        if not files:
            continue
        for fp in files:
            golden = None
            for line in fp.read_text().splitlines():
                if line.startswith("#"):
                    golden = int(line.split("golden=")[1].split()[0])
                    continue
                tid, k, p, mism, corr, vm, fl, sec = line.split("\t")
                if int(fl):
                    continue
                by.setdefault(float(p), []).append((int(k), int(mism), 100 * (golden - int(corr)) / 4096))
        out[m] = {}
        for p, v in sorted(by.items()):
            a = np.array(v, dtype=float)
            out[m][f"{p:g}"] = {"K": int(a[0, 0]), "n": len(a), "dacc_mean": a[:, 2].mean(),
                                "dacc_se": a[:, 2].std(ddof=1) / np.sqrt(len(a)), "mism_mean": a[:, 1].mean(),
                                "p_gt1": float(np.mean(a[:, 2] > 1))}
    (ROOT / "results/multi_upset_summary.json").write_text(json.dumps(out, indent=1))
    return out


def plot():
    """Fig. (accumulated upsets): accuracy loss against the per-bit upset probability, in hardware
    (solid, filled markers, mean +- standard error) and in the parameter bit-flip model (dashed, open
    markers). Colours are a validated colour-blind-safe set (every pair apart); markers keep the
    networks apart in greyscale."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    plt.rcParams.update({"font.size": 7.5, "font.family": "serif", "font.serif": ["Times New Roman"],
                         "mathtext.fontset": "stix", "axes.linewidth": 0.6, "xtick.major.width": 0.6,
                         "ytick.major.width": 0.6, "xtick.minor.width": 0.4, "legend.fontsize": 7})
    hw = hw_summary()
    sw_ = json.loads((ROOT / "results/multi_upset_sw.json").read_text())
    nets = [("dwn_md", "DWN-M", "#2a78d6", "o"), ("dlgn_a", "DLGN", "#1baf7a", "s"),
            ("mlp_32_16", "MLP", "#eb6834", "^"), ("mlp_32_16_p70", "MLP-P", "#4a3aa7", "D")]
    nets = [n for n in nets if n[0] in hw]
    fig, ax = plt.subplots(figsize=(3.45, 1.95))
    ax.grid(axis="y", color="#e4e3df", lw=0.5)
    ax.set_axisbelow(True)
    for m, lab, col, mk in nets:
        if m in sw_:                         # parameter model, behind the hardware curves
            s = sw_[m]
            pp = sorted(float(p) for p in s["p"])
            ax.plot(pp, [100 * (s["correct0"] - s["p"][f"{p:g}"]["correct_mean"]) / s["ntest"] for p in pp],
                    color=col, lw=1.0, ls=(0, (4, 2)), marker=mk, ms=3.2, mfc="white", mew=0.9)
        ps = sorted(float(p) for p in hw[m])
        mu = [hw[m][f"{p:g}"]["dacc_mean"] for p in ps]
        se = [hw[m][f"{p:g}"]["dacc_se"] for p in ps]
        ax.errorbar(ps, mu, yerr=se, color=col, lw=1.3, marker=mk, ms=3.6, mec="white", mew=0.5,
                    capsize=1.5, elinewidth=0.7)
    ax.set_xscale("log")
    ax.set_xlim(7e-7, 1.4e-1)
    ax.set_ylim(-1.5, 60)
    ax.set_yticks([0, 20, 40, 60])
    ax.set_xlabel("Per-bit upset probability $p$")
    ax.set_ylabel("Accuracy loss [points]")
    ax.spines[["top", "right"]].set_visible(False)
    # networks: colour and marker, upper left; line style: hardware or parameter model, upper middle
    leg1 = ax.legend(handles=[Line2D([], [], color=c, lw=1.3, marker=mk, ms=3.6, mec=c, label=l)
                              for _, l, c, mk in nets],
                     loc="upper left", bbox_to_anchor=(0.0, 1.02), frameon=False, handlelength=2.2,
                     borderaxespad=0.2, labelspacing=0.3)
    ax.add_artist(leg1)
    ax.legend(handles=[Line2D([], [], color="#52514e", lw=1.3, marker="o", ms=3.6, mec="#52514e",
                              label="Hardware (CRAM of the region)"),
                       Line2D([], [], color="#52514e", lw=1.0, ls=(0, (4, 2)), marker="o", ms=3.2, mfc="white",
                              mew=0.9, label="Parameter bit-flip model")],
              loc="upper left", bbox_to_anchor=(0.27, 1.02), frameon=False, handlelength=2.4,
              borderaxespad=0.2, labelspacing=0.3)
    fig.tight_layout(pad=0.25)
    fig.savefig(ROOT / "paper/fig_multi.pdf")
    print(json.dumps(hw, indent=1))


def p_at_loss(ps, loss, target=1.0):
    """Smallest p where the loss curve crosses target (log-linear interpolation); None if never."""
    for (p0, l0), (p1, l1) in zip(zip(ps, loss), zip(ps[1:], loss[1:])):
        if l0 < target <= l1:
            t = (target - l0) / (l1 - l0)
            return float(10 ** (np.log10(p0) + t * (np.log10(p1) - np.log10(p0))))
    return None


def numbers():
    """Numbers quoted in the paper: the upset probability that costs one accuracy point (hardware and
    parameter model), the corresponding number of upsets, and how well the sum of single-bit effects
    predicts the hardware loss before saturation."""
    hw = hw_summary()
    sw_ = json.loads((ROOT / "results/multi_upset_sw.json").read_text())
    pr = json.loads((ROOT / "results/multi_upset_pred.json").read_text())
    out = {}
    for m in hw:
        ps = sorted(float(p) for p in hw[m])
        loss = [hw[m][f"{p:g}"]["dacc_mean"] for p in ps]
        p_param = None
        if m in sw_:
            s = sw_[m]
            pp = sorted(float(p) for p in s["p"])
            p_param = p_at_loss(pp, [100 * (s["correct0"] - s["p"][f"{p:g}"]["correct_mean"]) / s["ntest"] for p in pp])
        ratio = []
        if m in pr:
            add = [100 * p * pr[m]["N"] * pr[m]["dcorr_per_bit"] / 4096 for p in ps]
            ratio = [l / a for l, a in zip(loss, add) if 0.5 < l < 20]
        p1 = p_at_loss(ps, loss)
        out[m] = {"trials": sum(hw[m][f"{p:g}"]["n"] for p in ps), "p_1pt_hw": p1, "p_1pt_param": p_param,
                  "K_1pt": p1 * pr[m]["N"] if (p1 and m in pr) else None,
                  "loss_at_1e-3": hw[m].get("0.001", {}).get("dacc_mean"),
                  "measured_over_additive": [min(ratio), max(ratio)] if ratio else None}
    out["advantage_hw_dwn_over_mlp"] = out["dwn_md"]["p_1pt_hw"] / out["mlp_32_16"]["p_1pt_hw"]
    if out.get("mlp_32_16_p70", {}).get("p_1pt_hw"):
        out["advantage_hw_dwn_over_pruned_mlp"] = out["dwn_md"]["p_1pt_hw"] / out["mlp_32_16_p70"]["p_1pt_hw"]
    (ROOT / "results/multi_upset_numbers.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "plot":
        plot()
        sys.exit(0)
    if cmd == "numbers":
        numbers()
        sys.exit(0)
    if cmd == "gen":
        gen(sys.argv[2], sys.argv[3], int(sys.argv[4]), float(sys.argv[5]) if len(sys.argv) > 5 else 1.0)
    elif cmd == "sw":
        sw(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 200)
    elif cmd == "pred":
        pred(sys.argv[2], sys.argv[3])
