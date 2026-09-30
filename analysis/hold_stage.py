"""Does the decay of a frozen 0 depend on the multiplexer stage that the upset disconnects?

  python analysis/hold_stage.py   -> results/hold_stage_dwn_md.json
Classifies every bit of the hold tests (results/freeze_hold_a.tsv, freeze_hold_b.tsv, see
fi/hold_test.tcl and freeze_hold.py) by the IMUX bit that was flipped: a column bit (the one-hot
column bit of the current source is cleared) or a row bit (the row pattern no longer turns on any
gate). The row bits of an IMUX are the four bits that appear in the pattern of every source
(imux_model.py). Per stage: bits whose first test run already read a 1 ("flip by first run"), bits
that turned into 1 at a later test run while the inputs rested ("flip later"), and bits that held.
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
from cram_map import CramMap, load_farlist  # noqa: E402
import freeze_hold as FH  # noqa: E402


def main():
    cm = CramMap()
    _, far_index, _ = load_farlist()
    bits = json.loads((ROOT / "results/imux_model_dwn_md_bits.json").read_text())
    stage, rows_of = {}, {}
    for j, l, tile, wire, bit, why, *_ in bits:
        if why != "open":
            continue
        tt = cm.tg[tile]["type"]
        if (tt, wire) not in rows_of:
            fwd, _ = cm.segbits(tt)
            feats = {k.split(".")[2]: v for k, v in fwd.items() if k.split(".")[1] == wire and k.count(".") == 2}
            cnt = Counter((ff, bb) for b in feats.values() for ff, bb, _ in b)
            rows_of[(tt, wire)] = {k for k, c in cnt.items() if c == len(feats)}
        ff, bb = map(int, bit.split("_"))
        base = cm.tg[tile]["bits"]["CLB_IO_CLK"]
        key = (far_index[int(base["baseaddr"], 16) + ff], base["offset"] + bb // 32, bb % 32)
        stage[key] = "row" if (ff, bb) in rows_of[(tt, wire)] else "column"
    exp = {k: (o0, o1) for k, o0, o1 in FH.candidates()}
    res = defaultdict(Counter)
    for f in ("freeze_hold_a.tsv", "freeze_hold_b.tsv"):
        runs = defaultdict(list)
        for line in (ROOT / "results" / f).read_text().splitlines():
            fi, w, b, t, m, c, _ = line.split("\t")
            k = (int(fi), int(w), int(b))
            o0, o1 = exp[k]
            h = (int(m), int(c))
            runs[k].append("0" if h == o0 else "1" if h == o1 else "mixed")
        for k, rr in runs.items():
            cls = "flip by first run" if rr[0] != "0" else ("flip later" if rr[-1] != "0" else "held")
            res[stage[k]][cls] += 1
    out = {s: dict(v) for s, v in res.items()}
    (ROOT / "results/hold_stage_dwn_md.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
