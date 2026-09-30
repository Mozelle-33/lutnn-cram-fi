"""Critical bits of a DWN campaign by network stage: learned connectivity, encoder, population-count
read-out, parameters, clock and the remainder (other CLB settings, constants, unused wires).

  python analysis/stage_breakdown.py <campaign dir> [...]   -> results/stage_breakdown.json
Combines the LUT-content bits by module (analyze_campaign.py) with the routing bits by the stage of the
net they configure (route_attrib.py). Other CLB settings (flip-flop, carry, multiplexer) are not
attributed to a stage.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def breakdown(cdir):
    a = json.loads((ROOT / "results" / cdir / "analysis.json").read_text())
    ra = json.loads((ROOT / "results" / cdir / "route_attrib.json").read_text())["by_group"]
    lut = a["lut_init_critical_by_module"]
    r = {k: v["critical"] for k, v in ra.items()}
    cl = {k: v.get("critical", 0) for k, v in a["classes"].items()}
    st = {
        # the connections into and between the LUT (gate) layers: learned in DWN, random and fixed in DLGN
        "learned connectivity": r.get("thermometer->LUT", 0) + r.get("LUT layer->LUT layer", 0)
                                + r.get("gate layers", 0),
        "encoder": lut.get("encoder", 0) + r.get("encoder", 0) + r.get("inputs", 0),
        "read-out": lut.get("popcount", 0) + lut.get("argmax", 0) + r.get("LUT layer->popcount", 0)
                    + r.get("popcount", 0) + r.get("argmax/output", 0),
        "parameters": lut.get("lut_layer", 0) + lut.get("gate_layers", 0),
        "clock": r.get("clock", 0) + cl.get("HCLK", 0),
        "other CLB settings": cl.get("CLB_OTHER", 0),
        "rest": r.get("constant", 0) + r.get("unused wire", 0) + lut.get("other", 0) + r.get("other", 0)
                + r.get("LUT->register", 0),
    }
    tot = sum(st.values())
    assert tot == a["critical"], (cdir, tot, a["critical"])
    return {k: {"critical": v, "share": v / tot} for k, v in st.items()}


LABELS = {"camp_dwn_sm": "DWN-S", "camp_dwn_md": "DWN-M", "camp_dwn_mnist_r": "DWN-MNIST", "camp_dlgn_a": "DLGN"}


def table(out):
    """paper_tvlsi/table_stages_tvlsi.tex: the shares of the stages, one row per campaign."""
    L = [r"\begin{tabular}{lrrrrrrr}", r"\toprule",
         r"Network & Conn. & Enc. & Read-out & Tables & Clock & Other CLB & Rest \\", r"\midrule"]
    for c, b in out.items():
        L.append(" & ".join([LABELS.get(c, c)] + [f"{100 * v['share']:.1f}" for v in b.values()]) + r" \\")
    L += [r"\bottomrule", r"\end{tabular}"]
    (ROOT / "paper_tvlsi/table_stages_tvlsi.tex").write_text("\n".join(L) + "\n")


def main():
    out = {c: breakdown(c) for c in sys.argv[1:]}
    (ROOT / "results/stage_breakdown.json").write_text(json.dumps(out, indent=1))
    table(out)
    for c, b in out.items():
        print(c, ", ".join(f"{k} {100 * v['share']:.1f}%" for k, v in b.items()))


if __name__ == "__main__":
    main()
