"""Generate the DUT Verilog, test vectors and metadata for one trained model.

  python train/gen_dut.py <model name> [--nvec 4096]
Outputs in hw/gen/<name>/: dut.v (module dut(clk, x[IN_W-1:0], y[2:0])), vectors.mem
(one line per vector: {label[2:0], x} hex, feature 0 in the LSBs of x), golden_sw.txt
(software class per vector) and meta.json (LATENCY, IN_W, test accuracy).
LATENCY = number of flip-flop stages between x and y: y in cycle c+LATENCY belongs to x in cycle c.
All adders are balanced trees with a register every two levels, so every neuron of a layer has
the same latency.
"""
from pathlib import Path
import argparse
import json
import numpy as np
import data_jsc
import models as M

TMR_OUT = False   # --tmr_out: triplicate the population count + arg-max of a DWN and vote

ROOT = Path(__file__).resolve().parents[1]


class Emit:
    """Collects the Verilog lines of the dut module body and counts its register stages, which
    become the LATENCY parameter of the harness."""

    def __init__(self, cw_out=3):
        self.lines = []
        self.stages = 0
        self.cw_out = cw_out          # width of the class index y

    def __call__(self, s):
        self.lines.append("    " + s)

    def reg_stage(self, decls_and_assigns):
        """decls_and_assigns: list of (decl_prefix, name, expr); emits regs updated in one block."""
        for decl, name, _ in decls_and_assigns:
            self(f"{decl} {name} = 0;")
        self("always @(posedge clk) begin")
        for _, name, expr in decls_and_assigns:
            self(f"    {name} <= {expr};")
        self("end")


def tree(E, name, terms, width, signed, regs_every=2):
    """Balanced adder tree over Verilog expressions; returns (name, register stages added)."""
    s = "signed " if signed else ""
    cur, level, stages = list(terms), 0, 0
    while len(cur) > 1:
        nxt = [f"{cur[k]} + {cur[k+1]}" if k + 1 < len(cur) else cur[k] for k in range(0, len(cur), 2)]
        level += 1
        if level % regs_every == 0 or len(nxt) == 1:
            items = [(f"reg {s}[{width-1}:0]", f"{name}_r{level}_{i}", e) for i, e in enumerate(nxt)]
            E.reg_stage(items)
            cur = [n for _, n, _ in items]
            stages += 1
        else:
            names = []
            for i, e in enumerate(nxt):
                n = f"{name}_w{level}_{i}"
                E(f"wire {s}[{width-1}:0] {n} = {e};")
                names.append(n)
            cur = names
    if stages == 0:  # single term: register it once for a uniform structure
        E.reg_stage([(f"reg {s}[{width-1}:0]", f"{name}_r0", cur[0])])
        return f"{name}_r0", 1
    return cur[0], stages


def input_reg(E, in_w):
    """Register the quantised feature vector (stage 1)."""
    E(f"reg [{in_w-1}:0] x_r = 0;")
    E("always @(posedge clk) x_r <= x;")
    E.stages += 1


def thermo_block(E, spec, used_bits, qbits):
    """Thermometer encoder: one registered comparator x[f] > threshold per thermometer bit that the
    first layer actually uses (unused bits are not generated)."""
    bl = spec["thermometer"]
    E(f"reg [{len(bl)-1}:0] t_r = 0;")
    E("always @(posedge clk) begin")
    for k in sorted(used_bits):
        f, t = bl[k]
        E(f"    t_r[{k}] <= (x_r[{f*qbits} +: {qbits}] > {qbits}'d{t});")
    E("end")
    E.stages += 1


def argmax(E, names, width, signed):
    """Registered arg-max over the class scores; ties go to the lowest class index, exactly as
    models.argmax_first in the integer reference."""
    s = "signed " if signed else ""
    E(f"wire {s}[{width-1}:0] am_v [0:{len(names)-1}];")
    for i, n in enumerate(names):
        E(f"assign am_v[{i}] = {n};")
    E(f"reg {s}[{width-1}:0] am_best;")
    E(f"reg [{E.cw_out-1}:0] am_idx;")
    E("integer ai;")
    E("always @(posedge clk) begin")
    E(f"    am_best = am_v[0]; am_idx = {E.cw_out}'d0;")
    E(f"    for (ai = 1; ai < {len(names)}; ai = ai + 1)")
    E(f"        if (am_v[ai] > am_best) begin am_best = am_v[ai]; am_idx = ai; end")
    E(f"    y <= am_idx;")
    E("end")
    E.stages += 1


def popcount_argmax(E, src, n, classes):
    """Population count per class over the n output bits of the last layer (class c owns bits
    c*g .. c*g+g-1), then arg-max. Groups of six bits are summed first so that each partial sum
    fits one LUT level; the rest is a balanced adder tree with equal latency for every class."""
    g = n // classes
    cw = max(1, int(np.ceil(np.log2(g + 1))))
    finals, pstages = [], None
    # level A: 6-bit groups -> 3-bit partial sums (registered)
    items = []
    parts = {}
    for c in range(classes):
        bits = [f"{src}[{c*g + i}]" for i in range(g)]
        parts[c] = []
        for k in range(0, g, 6):
            grp = bits[k:k + 6]
            name = f"pc{c}_a{k//6}"
            items.append(("reg [2:0]", name, " + ".join(f"{{2'b0, {b}}}" for b in grp)))
            parts[c].append(name)
    E.reg_stage(items)
    E.stages += 1
    for c in range(classes):
        name, st = tree(E, f"pc{c}", [f"{{{cw-3}'b0, {p}}}" if cw > 3 else p for p in parts[c]], max(cw, 3), False)
        finals.append(name)
        pstages = st if pstages is None else pstages
        assert st == pstages
    E.stages += pstages
    argmax(E, finals, max(cw, 3), False)


def gen_dwn(spec, qbits, in_w):
    """DWN: every trained table becomes one LUT6 primitive with its INIT value and DONT_TOUCH, so that
    synthesis cannot merge or re-encode it; with the identity LOCK_PINS set by hw/tcl/build.tcl, table
    entry k of LUT j is exactly one configuration bit (INIT[k]) and input l drives pin A(l+1)."""
    E = Emit()
    input_reg(E, in_w)
    used = set(np.asarray(spec["layers"][0]["mapping"]).reshape(-1).tolist())
    thermo_block(E, spec, used, qbits)
    src = "t_r"
    for li, layer in enumerate(spec["layers"]):
        m = np.asarray(layer["mapping"])
        tabs = np.asarray(layer["tables"], dtype=np.uint8)
        nl, n = m.shape
        assert n == 6, "generator emits LUT6 primitives"
        E(f"wire [{nl-1}:0] o{li};")
        E(f"reg  [{nl-1}:0] o{li}_r = 0;")
        for j in range(nl):
            init = sum(1 << a for a in range(64) if tabs[j, a])
            ins = ", ".join(f".I{l}({src}[{int(m[j, l])}])" for l in range(6))
            E(f"(* DONT_TOUCH = \"TRUE\" *) LUT6 #(.INIT(64'h{init:016X})) lut_l{li}_{j} (.O(o{li}[{j}]), {ins});")
        E(f"always @(posedge clk) o{li}_r <= o{li};")
        E.stages += 1
        src = f"o{li}_r"
    nlast = len(spec["layers"][-1]["tables"])
    if not TMR_OUT:
        popcount_argmax(E, src, nlast, spec["classes"])
        return E
    # three independent copies of population count + arg-max and a majority voter
    P = Emit(E.cw_out)
    popcount_argmax(P, "src", nlast, spec["classes"])
    cw = E.cw_out
    for k in range(3):
        E(f"wire [{cw-1}:0] yv{k};")
        E(f"(* DONT_TOUCH = \"TRUE\", KEEP_HIERARCHY = \"yes\" *) pcam u_pc{k} (.clk(clk), .src({src}), .y(yv{k}));")
    E("always @(posedge clk) y <= (yv0 == yv1 || yv0 == yv2) ? yv0 : yv1;")
    E.stages += P.stages + 1
    E.extra_modules = ["module pcam (", "    input  wire clk,", f"    input  wire [{nlast-1}:0] src,",
                       f"    output reg  [{cw-1}:0] y = 0", ");"] + P.lines + ["endmodule", ""]
    return E


def gen_dlgn(spec, qbits, in_w, reg_every=2):
    """DLGN: each two-input gate is written as the sum of the minterms of its truth table and left
    to synthesis, which packs several gates into one LUT6; a register every reg_every layers."""
    E = Emit()
    input_reg(E, in_w)
    used = set(spec["layers"][0]["a"]) | set(spec["layers"][0]["b"])
    thermo_block(E, spec, used, qbits)
    src = "t_r"
    nlayers = len(spec["layers"])
    for li, layer in enumerate(spec["layers"]):
        n = len(layer["gate"])
        E(f"wire [{n-1}:0] g{li};")
        for j, (a, b, g) in enumerate(zip(layer["a"], layer["b"], layer["gate"])):
            t = M.GATE_TT[g]
            A, B = f"{src}[{a}]", f"{src}[{b}]"
            terms = [f"({'' if av else '~'}{A} & {'' if bv else '~'}{B})"
                     for av in (0, 1) for bv in (0, 1) if (t >> ((av << 1) | bv)) & 1]
            E(f"assign g{li}[{j}] = {' | '.join(terms) if terms else chr(49) + chr(39) + 'b0'};")
        if (li + 1) % reg_every == 0 or li == nlayers - 1:
            E(f"reg [{n-1}:0] g{li}_r = 0;")
            E(f"always @(posedge clk) g{li}_r <= g{li};")
            E.stages += 1
            src = f"g{li}_r"
        else:
            src = f"g{li}"
    popcount_argmax(E, src, len(spec["layers"][-1]["gate"]), spec["classes"])
    return E


def gen_qmlp(spec, qbits, in_w):
    """Fixed-point MLP: per layer a registered stage of constant-coefficient products, a balanced
    adder tree with the bias, then shift and clamp to the activation range (ReLU with saturation).
    The accumulator width is derived from the worst-case |sum|, so no intermediate result overflows."""
    E = Emit()
    input_reg(E, in_w)
    abits = spec["abits"]
    nfeat = in_w // qbits
    prev = [f"$signed({{1'b0, x_r[{i*qbits} +: {qbits}]}})" for i in range(nfeat)]
    prev_max = [(1 << qbits) - 1] * nfeat
    for li, layer in enumerate(spec["layers"]):
        W = np.asarray(layer["w"], dtype=np.int64)
        bvec = np.asarray(layer["b"], dtype=np.int64)
        nout, nin = W.shape
        bound = max(abs(int(bvec[j])) + sum(abs(int(W[j, i])) * prev_max[i] for i in range(nin)) for j in range(nout))
        accw = int(np.ceil(np.log2(bound + 1))) + 2
        # product stage (constant-coefficient multiplies in fabric: synth runs with -max_dsp 0)
        items = [(f"reg signed [{accw-1}:0]", f"p{li}_{j}_{i}", f"{prev[i]} * $signed({int(W[j, i])})")
                 for j in range(nout) for i in range(nin)]
        E.reg_stage(items)
        E.stages += 1
        sums, sst = [], None
        for j in range(nout):
            terms = [f"$signed({accw}'sd{int(bvec[j])})" if bvec[j] >= 0 else f"-$signed({accw}'sd{-int(bvec[j])})"]
            terms += [f"p{li}_{j}_{i}" for i in range(nin)]
            name, st = tree(E, f"s{li}_{j}", terms, accw, True)
            sums.append(name)
            sst = st if sst is None else sst
            assert st == sst
        E.stages += sst
        if layer["shift"] is None:
            argmax(E, sums, accw, True)
            return E
        sh = layer["shift"]
        amax = (1 << abits) - 1
        items = []
        for j in range(nout):
            v = f"({sums[j]} >>> {sh})" if sh >= 0 else f"({sums[j]} <<< {-sh})"
            E(f"wire signed [{accw + max(0, -sh) - 1}:0] v{li}_{j} = {v};")
            items.append((f"reg [{abits-1}:0]", f"a{li}_{j}",
                          f"(v{li}_{j} < 0) ? {abits}'d0 : (v{li}_{j} > {amax}) ? {abits}'d{amax} : v{li}_{j}[{abits-1}:0]"))
        E.reg_stage(items)
        E.stages += 1
        prev = [f"$signed({{1'b0, a{li}_{j}}})" for j in range(nout)]
        prev_max = [amax] * nout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--nvec", type=int, default=4096)
    ap.add_argument("--tmr_out", action="store_true", help="DWN: triplicate population count + arg-max")
    ap.add_argument("--out", default=None, help="output name (default: model name)")
    ap.add_argument("--dataset", default="jsc")
    a = ap.parse_args()
    global TMR_OUT
    TMR_OUT = a.tmr_out
    spec = json.loads((ROOT / "models" / f"{a.name}.json").read_text())
    qbits = spec["meta"]["qbits"]
    d = data_jsc.load(qbits) if a.dataset == "jsc" else __import__(f"data_{a.dataset}").load(qbits)
    # the hardware test set is the first nvec samples of the test split
    Xte, yte = d["Xte"][: a.nvec], d["yte"][: a.nvec]
    nfeat = Xte.shape[1]
    in_w = nfeat * qbits
    cw = max(1, int(np.ceil(np.log2(spec["classes"]))))
    Emit.__init__.__defaults__ = (cw,)
    E = {"dwn": gen_dwn, "dlgn": gen_dlgn, "qmlp": gen_qmlp}[spec["type"]](spec, qbits, in_w)
    out = ROOT / "hw" / "gen" / (a.out or a.name)
    out.mkdir(parents=True, exist_ok=True)
    v = [f"// Generated by train/gen_dut.py from models/{a.name}.json ({spec['type']}), LATENCY={E.stages}",
         "`timescale 1ns / 1ps",
         "module dut (",
         "    input  wire clk,",
         f"    input  wire [{in_w-1}:0] x,",
         f"    output reg  [{cw-1}:0] y = 0",
         ");"] + E.lines + ["endmodule", ""] + getattr(E, "extra_modules", [])
    (out / "dut.v").write_text("\n".join(v))
    # test ROM: one hex word per vector, {label, feature n-1, ..., feature 0}
    hexw = (in_w + cw + 3) // 4
    with open(out / "vectors.mem", "w") as f:
        for xv, yv in zip(Xte, yte):
            val = int(yv) << in_w
            for i in range(nfeat):
                val |= int(xv[i]) << (i * qbits)
            f.write(f"{val:0{hexw}X}\n")
    # software reference outputs, compared with the RTL simulation by hw/sim/run_dut_sim.ps1
    _, pred = M.predict_spec(spec, Xte)
    (out / "golden_sw.txt").write_text("\n".join(str(int(p)) for p in pred) + "\n")
    meta = {"name": a.out or a.name, "model": a.name, "type": spec["type"], "LATENCY": E.stages, "IN_W": in_w, "QBITS": qbits,
            "CW": cw, "TMR_OUT": bool(a.tmr_out), "DATASET": a.dataset,
            "NVEC": int(len(yte)), "sw_acc_nvec": float((pred == yte).mean()),
            "test_acc_full": spec["meta"]["test_acc"]}
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps(meta))


if __name__ == "__main__":
    main()
