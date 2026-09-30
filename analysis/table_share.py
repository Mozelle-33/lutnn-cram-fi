"""Share of the LUT tables (the parameters) in the critical bits and in the summed mispredictions of a
DWN campaign (Table III of the paper).

  python analysis/table_share.py <campaign dir>:<model>:<coverage key> [...]  -> results/table_share.json
The table bits' hardware outcome equals the parameter model on the same vectors, so their summed
mispredictions come from results/sw_single_flips_<build or model>.npz; the bound assumes that every
other critical bit was already found and adds the table bits that are critical only on the full test
set (results/test_coverage.json).
"""
import json
import sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def main():
    cov = json.loads((ROOT / "results/test_coverage.json").read_text())
    out = {}
    for arg in sys.argv[1:]:
        cdir, model, key = arg.split(":")
        a = json.loads((ROOT / "results" / cdir / "analysis.json").read_text())
        build = a["build"]
        swf = ROOT / f"results/sw_single_flips_{build}.npz"
        swf = swf if swf.exists() else ROOT / f"results/sw_single_flips_{model}.npz"
        d = np.load(swf)
        layers = [d[k] for k in d.files if k.startswith("mism_l")] or [d["mism"]]
        table_mism = int(sum(int(m.sum()) for m in layers))
        table_crit = int(a["lut_init_critical_by_module"].get("lut_layer", 0))
        mism = 0
        subs = [ROOT / "results" / cdir] if (ROOT / "results" / cdir / "summary.txt").exists() else \
            sorted(s.parent for s in (ROOT / "results" / cdir).glob("*/summary.txt"))
        for s in subs:
            for line in (s / "events.tsv").read_text().splitlines():
                m = int(line.split("\t")[3])
                mism += max(m, 0)
        full = cov[key]["critical_full"]
        out[cdir] = {"critical": a["critical"], "table_critical": table_crit, "table_share": table_crit / a["critical"],
                     "table_share_bound": full / (a["critical"] - table_crit + full),
                     "sum_mism": mism, "table_mism": table_mism, "table_mism_share": table_mism / mism}
        print(cdir, {k: (round(v, 5) if isinstance(v, float) else v) for k, v in out[cdir].items()})
    (ROOT / "results/table_share.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
