"""Analyse an exhaustive fault-injection campaign (optionally split into sub-campaigns).

  python analysis/analyze_campaign.py <model> <campaign dir> [build name]
<campaign dir> holds summary.txt + events.tsv, or sub-directories that do (one per clock-region
row); they are merged. Uses the build's placement (hw/build/<build>/dut_cells.csv), essential
bits (.ebd) and the prjxray mapping; writes <campaign dir>/analysis.json with totals, per-class
tables (LUT_INIT / ROUTING / CLB_OTHER / HCLK / BRAM_DSP / UNMAPPED: injected, essential,
critical, accuracy-degrading), the per-module split of critical LUT-INIT bits, and for DWN the
one-to-one hardware vs software comparison of every LUT-layer INIT bit.
"""
from pathlib import Path
import csv
import json
import re
import sys
from collections import Counter, defaultdict
import numpy as np
from cram_map import CramMap, load_farlist

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "tools/prjxray-db/kintex7"
FIT_PER_MB = 40.0          # UG116 v10.21 (2026) Table 1, 28 nm Kintex-7 CRAM, real-time SER (NYC sea level)
XS_PER_BIT = 5.69e-15      # cm^2, LANSCE neutron cross-section per CRAM bit, same table
CLASSES = ["LUT_INIT", "ROUTING", "CLB_OTHER", "HCLK", "BRAM_DSP", "OTHER", "UNMAPPED"]


# ---------------------------------------------------------------- essential bits
def frame_sequence():
    """Frame addresses in the order of a full readback image (the layout of the .ebd file): logic
    frames, then block-RAM frames, per half, row and column, with two pad frames after each row."""
    part = json.loads((DB / "xc7k325tffg900-2/part.json").read_text())
    seq = []
    for bus, bt in (("CLB_IO_CLK", 0), ("BLOCK_RAM", 1)):
        for half_name, half in (("top", 0), ("bottom", 1)):
            rows = part["global_clock_regions"][half_name]["rows"]
            for row in sorted(rows, key=int):
                cols = rows[row]["configuration_buses"].get(bus, {}).get("configuration_columns", {})
                for col in sorted(cols, key=int):
                    for minor in range(cols[col]["frame_count"]):
                        seq.append((bt << 23) | (half << 22) | (int(row) << 17) | (int(col) << 7) | minor)
                seq.extend([None, None])
    return seq


def load_ebd(path):
    """Essential-bits mask from Vivado's .ebd file: one 32-bit word per line after the header."""
    lines = Path(path).read_text().splitlines()
    i = next(k for k, l in enumerate(lines) if l.startswith("Bits:")) + 1
    return np.array([int(l, 2) for l in lines[i:] if l], dtype=np.uint64)


class Essential:
    """Essential-bit lookup by (FAR, word, bit). The number of leading pad frames of the image is
    found by checking which offset marks the most known LUT-INIT bits (probe_addrs) as essential."""

    def __init__(self, ebd_path, probe_addrs):
        self.words = load_ebd(ebd_path)
        seq = frame_sequence()
        best = None
        for lead in range(0, 4):                     # leading pad frames in the readback image
            idx = {f: lead + k for k, f in enumerate(seq) if f is not None}
            hits = sum(self._bit(idx, *a) for a in probe_addrs)
            if best is None or hits > best[0]:
                best = (hits, lead, idx)
        self.probe_hits, self.lead, self.idx = best
        self.nprobe = len(probe_addrs)

    def _bit(self, idx, far, word, bit):
        k = idx.get(far)
        if k is None:
            return 0
        p = k * 101 + word
        return int((int(self.words[p]) >> bit) & 1) if p < len(self.words) else 0

    def is_essential(self, far, word, bit):
        """1 if the bit is essential (configures a used resource), else 0."""
        return self._bit(self.idx, far, word, bit)


# ---------------------------------------------------------------- module attribution
def module_of(cell):
    """DUT stage of a cell, from the naming convention of train/gen_dut.py."""
    c = cell[len("u_dut/"):] if cell.startswith("u_dut/") else cell
    if c.startswith("lut_l") or re.match(r"o\d+_r", c):
        return "lut_layer"
    if c.startswith(("t_r", "x_r")):
        return "encoder"
    if c.startswith(("pc", "u_pc")):
        return "popcount"
    if c.startswith(("am_", "am", "y")):
        return "argmax"
    if re.match(r"g\d+", c):
        return "gate_layers"
    if re.match(r"[ps]\d+_", c):
        return "mac"
    if re.match(r"a\d+_", c) or re.match(r"v\d+_", c):
        return "activation"
    return "other"


def load_placement(build, m):
    """CRAM address of every INIT bit of every placed DUT LUT -> (cell, entry index)."""
    init_bits = {}                                  # (far, word, bit) -> (cell, k)
    with open(ROOT / f"hw/build/{build}/dut_cells.csv") as f:
        for r in csv.DictReader(f):
            if r["ref"].startswith("LUT") and r["site"]:
                for k in range(64):
                    try:
                        far, word, bit, val, feat = m.lut_init_address(r["site"], r["bel"].replace("5LUT", "6LUT"), k)
                    except KeyError:
                        break
                    init_bits[(far, word, bit)] = (r["cell"], k)
    return init_bits


def main():
    """Merge the sub-campaigns, classify every injected bit, and write analysis.json."""
    model, cdir = sys.argv[1], Path(sys.argv[2])
    build = sys.argv[3] if len(sys.argv) > 3 else model
    subs = [cdir] if (cdir / "summary.txt").exists() else sorted(d for d in cdir.iterdir() if (d / "summary.txt").exists())
    far_list, far_index, _ = load_farlist()
    m = CramMap()
    init_bits = load_placement(build, m)
    ebd = next((ROOT / f"hw/build/{build}").glob("*.ebd"))
    probes = [a for a, (c, k) in init_bits.items() if c.startswith("u_dut/lut_l")][:2000] or list(init_bits)[:2000]
    ess = Essential(ebd, probes)

    cls_inj, cls_ess, cls_crit, cls_acc = Counter(), Counter(), Counter(), Counter()
    skip_cls, skip_ess, nskip = Counter(), 0, 0
    crit_mod = Counter()
    mism_by_cls = defaultdict(list)
    ev_all = {}
    timeouts = 0
    golden = ntest = None
    rep_inj = 0
    seconds = 0.0
    ranges = []
    for sub in subs:
        summ = dict(kv.split("=", 1) for kv in (sub / "summary.txt").read_text().split())
        f0, f1 = int(summ["far_first"]), int(summ["far_last"])
        w0, w1 = int(summ["word_lo"]), int(summ["word_hi"])
        nw = w1 - w0 + 1
        lin0 = int(summ.get("start_lin", 0))
        lin1 = int(summ.get("end_lin", (f1 - f0 + 1) * nw * 32))
        ranges.append((f0, f1))
        golden, ntest = int(summ["golden_corr"]), int(summ["ntest"])
        rep_inj += int(summ["inj"])
        seconds += float(summ.get("seconds", 0))
        skip = set()
        if summ.get("skipfile", "-") != "-":
            sp = Path(summ["skipfile"])
            sp = sp if sp.is_absolute() else ROOT / sp
            for line in sp.read_text().split("\n"):
                if line.strip():
                    a, b, c = map(int, line.split()[:3])
                    skip.add((a, b, c))
        ev = {}
        for line in (sub / "events.tsv").read_text().splitlines():
            fi, w, b, mism, corr, flags, seg = map(int, line.split("\t"))
            if mism < 0:
                timeouts += 1
                continue
            ev[(fi, w, b)] = (mism, corr, flags)
        ev_all.update(ev)
        for l in range(lin0, lin1):
            t = l // 32
            fi, w, b = f0 + t // nw, w0 + t % nw, l % 32
            far = far_list[fi]
            c, tile, feats = m.classify(far, w, b)
            e = ess.is_essential(far, w, b)
            if (fi, w, b) in skip:
                skip_cls[c] += 1
                skip_ess += e
                nskip += 1
                continue
            cls_inj[c] += 1
            cls_ess[c] += e
            r = ev.get((fi, w, b))
            if r and r[0] > 0:
                cls_crit[c] += 1
                mism_by_cls[c].append(r[0])
                if r[1] < golden:
                    cls_acc[c] += 1
                if c == "LUT_INIT":
                    crit_mod[module_of(init_bits.get((far, w, b), ("?", -1))[0])] += 1

    n_inj = sum(cls_inj.values())
    ess_total = sum(cls_ess.values())
    crit = [v for v in ev_all.values() if v[0] > 0]
    res = {
        "model": model, "build": build, "campaign": str(cdir), "subcampaigns": [str(s) for s in subs],
        "ranges": ranges, "injected": n_inj, "reported_injections": rep_inj, "seconds": seconds,
        "skipped_mode_bits": nskip, "skipped_by_class": dict(skip_cls), "skipped_essential": skip_ess,
        "essential_in_range": ess_total, "ebd_lead_pad_frames": ess.lead,
        "ebd_probe_hits": f"{ess.probe_hits}/{ess.nprobe}",
        "critical": len(crit), "critical_frac_injected": len(crit) / max(1, n_inj),
        "critical_frac_essential": len(crit) / max(1, ess_total),
        "fit_sea_level": len(crit) * FIT_PER_MB / 1e6, "cross_section_cm2": len(crit) * XS_PER_BIT,
        "acc_degrading": sum(1 for v in crit if v[1] < golden),
        "persistent": sum(1 for v in ev_all.values() if v[2] & 8), "timeouts": timeouts,
        "golden_corr": golden, "ntest": ntest,
        "mean_mism_critical": float(np.mean([v[0] for v in crit])) if crit else 0.0,
        "mean_acc_drop_critical": float(np.mean([(golden - v[1]) / ntest for v in crit])) if crit else 0.0,
        "classes": {c: {"injected": cls_inj[c], "essential": cls_ess[c], "critical": cls_crit[c],
                        "acc_degrading": cls_acc[c],
                        "mean_mism": float(np.mean(mism_by_cls[c])) if mism_by_cls[c] else 0.0}
                    for c in CLASSES if cls_inj[c]},
        "lut_init_critical_by_module": dict(crit_mod),
    }
    # DWN: hardware vs software for every LUT-layer INIT bit
    spec = json.loads((ROOT / f"models/{model}.json").read_text())
    swf = ROOT / f"results/sw_single_flips_{model}.npz"
    if spec["type"] == "dwn" and swf.exists():
        swd = np.load(swf)
        sw_l = [swd[f"mism_l{li}"] for li in range(len(spec["layers"]))] if "mism_l0" in swd.files else [swd["mism"]]
        agree = tp = fp = fn = tn = 0
        diffs = []
        for (far, w, b), (cell, k) in init_bits.items():
            mm = re.match(r"u_dut/lut_l(\d+)_(\d+)$", cell)
            if not mm or far not in far_index or int(mm.group(1)) >= len(sw_l):
                continue
            fi = far_index[far]
            if not any(f0 <= fi <= f1 for f0, f1 in ranges):
                continue
            li, j = int(mm.group(1)), int(mm.group(2))
            hw = ev_all.get((fi, w, b), (0, 0, 0))[0]
            s = int(sw_l[li][j, k])
            agree += hw == s
            tp += (hw > 0) and (s > 0); fp += (hw > 0) and (s == 0); fn += (hw == 0) and (s > 0); tn += (hw == 0) and (s == 0)
            if hw != s:
                diffs.append((j, k, hw, s))
        res["dwn_lut_layer_hw_vs_sw"] = {"bits": agree + len(diffs), "exact_agree": agree, "tp": tp, "fp": fp, "fn": fn,
                                         "tn": tn, "examples_diff": diffs[:20]}
    (cdir / "analysis.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
