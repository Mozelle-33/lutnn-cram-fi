"""LUT-native and arithmetic models for JSC, with integer-exact reference inference.

- Thermometer: distributive (quantile) thresholds on the integer inputs, bit = x > t.
- DWN: Differentiable Weightless Network (Bacellar et al., ICML'24). Pure-PyTorch re-implementation
  of the official CUDA kernels (github.com/alanbacellar/DWN): real-valued LUT tables, address from
  binary inputs, STE binarization, EFD input gradient with alpha=0.5*0.75^(n-1), beta=0.25/0.75,
  learnable mapping (argmax forward, softmax backward), GroupSum.
- DLGN: Differentiable logic gate network (Petersen et al., NeurIPS'22), 16 two-input gates.
- QMLP: fixed-point MLP with power-of-two scales (hls4ml-style arithmetic baseline).

Each model exports a plain-dict "spec" (tables, mappings, gates, integer weights) consumed by
the Verilog generator and by the software fault models; `predict_spec` runs the spec in numpy
with exactly the hardware's arithmetic (argmax ties -> lowest class index).
"""
import math
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F


# ----------------------------------------------------------------------------- thermometer
class Thermometer:
    """Distributive thermometer code: for each feature, thresholds at evenly spaced quantiles of the
    training data (duplicates and never-firing thresholds removed); bit k of feature f is x_f > t_k."""

    def __init__(self, bits_per_feature):
        self.bpf = int(bits_per_feature)
        self.thresholds = None  # list of int arrays (unique, ascending), one per feature

    def fit(self, Xq):
        th = []
        for f in range(Xq.shape[1]):
            s = np.sort(Xq[:, f])
            idx = [int(len(s) * i / (self.bpf + 1)) for i in range(1, self.bpf + 1)]
            t = np.unique(s[idx]).astype(np.int64)
            t = t[t < s[-1]]  # a threshold >= max value never fires
            th.append(t)
        self.thresholds = th
        return self

    @property
    def n_bits(self):
        return int(sum(len(t) for t in self.thresholds))

    def bit_list(self):
        """[(feature, threshold)] in encoded bit order."""
        return [(f, int(t)) for f, ts in enumerate(self.thresholds) for t in ts]

    def encode_np(self, Xq):
        return np.concatenate([(Xq[:, [f]] > ts[None, :]) for f, ts in enumerate(self.thresholds)],
                              axis=1).astype(np.uint8)

    def encode(self, Xq, device):
        return torch.from_numpy(self.encode_np(Xq)).to(device=device, dtype=torch.float32)


# ----------------------------------------------------------------------------- DWN
def _popcount(v):
    """Element-wise number of set bits of an integer tensor."""
    c = torch.zeros_like(v)
    while bool((v > 0).any()):
        c += v & 1
        v = v >> 1
    return c


def efd_kernels(n, alpha, beta):
    """K[l, a, a2] = sign(a2_l) * alpha * beta^popc((a ^ a2) & ~(1<<l)).

    Extended finite difference (EFD) of the DWN paper: the gradient of a LUT output with respect to
    its input bit l, at address a, is sum_a2 K[l, a, a2] * table[a2], i.e. the difference between
    entries with bit l set and cleared, weighted by beta^(Hamming distance to a on the other bits).
    """
    A = 1 << n
    a = torch.arange(A)
    K = torch.empty(n, A, A)
    for l in range(n):
        mask = (A - 1) & ~(1 << l)
        dist = _popcount((a[:, None] ^ a[None, :]) & mask).float()
        sign = (((a[None, :] >> l) & 1) * 2 - 1).float()
        K[l] = sign * alpha * beta ** dist
    return K


class EFDFunction(torch.autograd.Function):
    """LUT lookup with EFD gradients. forward: address each table with its n mapped input bits and
    return the real-valued entries; backward: table gradient = scatter-add of the output gradient
    to the addressed entries, input gradient = EFD (see efd_kernels) accumulated over all LUTs that
    read the input."""

    @staticmethod
    def forward(ctx, x, mapping, luts, K):
        n = mapping.shape[1]
        L, A = luts.shape
        bits = (x[:, mapping] > 0).long()                               # (B, L, n)
        addr = (bits << torch.arange(n, device=x.device)).sum(-1)       # (B, L)
        flat = (torch.arange(L, device=x.device) * A)[None, :] + addr
        out = luts.reshape(-1)[flat]
        ctx.save_for_backward(flat, addr, mapping, luts, K)
        ctx.in_size = x.shape[1]
        return out

    @staticmethod
    def backward(ctx, g):
        flat, addr, mapping, luts, K = ctx.saved_tensors
        B, L = addr.shape
        n = mapping.shape[1]
        g = g.contiguous()
        luts_grad = torch.zeros(luts.numel(), device=g.device).index_add_(0, flat.reshape(-1), g.reshape(-1))
        W = torch.einsum("lab,jb->jla", K, luts)                        # (L, n, A)
        idx = addr.t()[:, None, :].expand(L, n, B)
        contrib = torch.gather(W, 2, idx) * g.t()[:, None, :]           # (L, n, B)
        in_grad = torch.zeros(B, ctx.in_size, device=g.device)
        in_grad.index_add_(1, mapping.reshape(-1), contrib.permute(2, 0, 1).reshape(B, L * n))
        return in_grad, None, luts_grad.view_as(luts), None


class STE(torch.autograd.Function):
    """Binarise LUT outputs (entry > 0 -> 1) with a straight-through gradient."""

    @staticmethod
    def forward(ctx, x):
        return (x > 0).float()

    @staticmethod
    def backward(ctx, g):
        return g


class LearnableMappingFn(torch.autograd.Function):
    """Learnable connectivity of the first DWN layer: every LUT input picks the input bit with the
    largest weight (forward, hard selection); the backward pass uses a softmax over the weights, so
    that the connections can move during training."""

    @staticmethod
    def forward(ctx, x, weights, tau):
        m = weights.argmax(dim=0)
        ctx.save_for_backward(x, weights)
        ctx.tau = tau
        return x[:, m]

    @staticmethod
    def backward(ctx, g):
        x, weights = ctx.saved_tensors
        wg = (2 * x - 1).t() @ g
        ig = g @ F.softmax(weights / ctx.tau, dim=0).t()
        return ig, wg, None


class LUTLayer(nn.Module):
    """out_size n-input LUTs with real-valued tables in [-1, 1] (binarised by sign for inference)
    and a fixed random or learnable input mapping. Optional fault-aware training noise on the input
    connections (attributes in_noise and noise_kind, set by train.py)."""

    def __init__(self, in_size, out_size, n=6, mapping="random", lm_tau=1e-3, seed=0):
        super().__init__()
        self.in_size, self.out_size, self.n = in_size, out_size, n
        self.learnable = mapping == "learnable"
        gen = torch.Generator().manual_seed(seed)
        if self.learnable:
            self.map_w = nn.Parameter(torch.rand(in_size, out_size * n, generator=gen))
            self.lm_tau = lm_tau
            self.register_buffer("mapping", torch.arange(out_size * n).view(out_size, n))
        else:
            reps = math.ceil(out_size * n / in_size)
            m = torch.cat([torch.randperm(in_size, generator=gen) for _ in range(reps)])[: out_size * n]
            self.register_buffer("mapping", m.view(out_size, n))
        self.luts = nn.Parameter(torch.rand(out_size, 1 << n, generator=gen) * 2 - 1)
        alpha, beta = 0.5 * 0.75 ** (n - 1), 0.25 / 0.75
        self.register_buffer("K", efd_kernels(n, alpha, beta))

    def forward(self, x):
        if self.training:
            with torch.no_grad():
                self.luts.clamp_(-1, 1)
        if self.learnable:
            x = LearnableMappingFn.apply(x, self.map_w, self.lm_tau)
        noise = getattr(self, "in_noise", 0.0)
        if self.training and noise > 0:
            # fault-aware training: every LUT input connection is faulty with probability `noise`
            xm = x[:, self.mapping.reshape(-1)] if not self.learnable else x
            hit = torch.rand_like(xm) < noise
            if getattr(self, "noise_kind", "flip") == "phys":
                # connection faults observed in hardware (IMUX upsets): the input freezes at a value
                # it takes for another sample (30 %) or is wired-ANDed with another connection (70 %)
                frozen = hit & (torch.rand_like(xm) < 0.3)
                bridged = hit & ~frozen
                other_sample = xm[torch.randperm(xm.shape[0], device=xm.device)]
                other_input = xm[:, torch.randint(0, xm.shape[1], (xm.shape[1],), device=xm.device)]
                xm = torch.where(frozen, other_sample, torch.where(bridged, xm * other_input, xm))
            else:
                xm = xm + (1 - 2 * xm) * hit.float()
            mapping = torch.arange(xm.shape[1], device=x.device).view(self.out_size, self.n)
            return STE.apply(EFDFunction.apply(xm, mapping, self.luts, self.K))
        return STE.apply(EFDFunction.apply(x, self.mapping, self.luts, self.K))

    def final_mapping(self):
        if self.learnable:
            return self.map_w.argmax(dim=0).view(self.out_size, self.n).cpu().numpy()
        return self.mapping.cpu().numpy()

    def tables(self):
        return (self.luts > 0).cpu().numpy().astype(np.uint8)


class GroupSum(nn.Module):
    """Class scores: the LUT outputs are split into k equal groups, one per class, and each group is
    summed (the population count in hardware) and divided by the temperature tau."""

    def __init__(self, k, tau):
        super().__init__()
        self.k, self.tau = k, tau

    def forward(self, x):
        return x.view(x.shape[0], self.k, -1).sum(-1) / self.tau


class DWN(nn.Module):
    """Differentiable weightless network: thermometer bits -> LUT layers (the first with learnable
    connectivity, later ones with random connectivity) -> GroupSum."""

    def __init__(self, in_bits, layers, n=6, classes=5, tau=1 / 0.071, first_mapping="learnable", seed=0):
        super().__init__()
        assert layers[-1] % classes == 0
        sizes = [in_bits] + list(layers)
        self.layers = nn.ModuleList(
            LUTLayer(sizes[i], sizes[i + 1], n, first_mapping if i == 0 else "random", seed=seed + i)
            for i in range(len(layers)))
        self.gs = GroupSum(classes, tau)
        self.classes = classes

    def forward(self, x):
        for l in self.layers:
            x = l(x)
        return self.gs(x)

    def spec(self, thermo):
        return {"type": "dwn", "classes": self.classes, "n": self.layers[0].n,
                "thermometer": thermo.bit_list(),
                "layers": [{"mapping": l.final_mapping().tolist(), "tables": l.tables().tolist()}
                           for l in self.layers]}


# ----------------------------------------------------------------------------- DLGN
def gate_ops(a, b):
    """16 relaxed two-input gates, index = truth table [f(0,0), f(0,1), f(1,0), f(1,1)] as
    bits 0..3 (a is the first input), i.e. gate g outputs bit ((a<<1)|b) of g... see GATE_TT."""
    ab = a * b
    return torch.stack([
        torch.zeros_like(a), ab, a - ab, a, b - ab, b, a + b - 2 * ab, a + b - ab,
        1 - (a + b - ab), 1 - (a + b - 2 * ab), 1 - b, 1 - b + ab, 1 - a, 1 - a + ab, 1 - ab,
        torch.ones_like(a)], dim=-1)


def _gate_tt():
    tt = []
    for g in range(16):
        v = 0
        for a in (0, 1):
            for b in (0, 1):
                o = gate_ops(torch.tensor(float(a)), torch.tensor(float(b)))[g].item()
                v |= int(round(o)) << ((a << 1) | b)
        tt.append(v)
    return tt


GATE_TT = _gate_tt()  # GATE_TT[g] bit ((a<<1)|b) = output of gate g


class LogicLayer(nn.Module):
    """out_size two-input gates with fixed random inputs (each input used about equally often).
    Training: a softmax mixture of the 16 relaxed gates per neuron; inference: the argmax gate."""

    def __init__(self, in_size, out_size, seed=0):
        super().__init__()
        gen = torch.Generator().manual_seed(seed)
        c = torch.randperm(2 * out_size, generator=gen) % in_size
        c = c[torch.randperm(2 * out_size, generator=gen)]
        a, b = c[:out_size].clone(), c[out_size:].clone()
        same = a == b
        b[same] = (b[same] + 1) % in_size
        self.register_buffer("ia", a)
        self.register_buffer("ib", b)
        self.w = nn.Parameter(torch.randn(out_size, 16, generator=gen))

    def forward(self, x):
        a, b = x[:, self.ia], x[:, self.ib]
        if self.training:
            return (gate_ops(a, b) * F.softmax(self.w, dim=-1)).sum(-1)
        g = self.w.argmax(-1)
        return torch.gather(gate_ops(a, b), -1, g[None, :, None].expand(x.shape[0], -1, 1)).squeeze(-1)


class DLGN(nn.Module):
    def __init__(self, in_bits, layers, classes=5, tau=10.0, seed=0):
        super().__init__()
        sizes = [in_bits] + list(layers)
        self.layers = nn.ModuleList(LogicLayer(sizes[i], sizes[i + 1], seed=seed + i) for i in range(len(layers)))
        self.gs = GroupSum(classes, tau)
        self.classes = classes

    def forward(self, x):
        for l in self.layers:
            x = l(x)
        return self.gs(x)

    def spec(self, thermo):
        return {"type": "dlgn", "classes": self.classes, "thermometer": thermo.bit_list(),
                "layers": [{"a": l.ia.cpu().tolist(), "b": l.ib.cpu().tolist(),
                            "gate": l.w.argmax(-1).cpu().tolist()} for l in self.layers]}


# ----------------------------------------------------------------------------- QMLP
class _RoundSTE(torch.autograd.Function):
    """round() with a straight-through gradient (weight and bias quantisation)."""

    @staticmethod
    def forward(ctx, x):
        return torch.round(x)

    @staticmethod
    def backward(ctx, g):
        return g


class _FloorSTE(torch.autograd.Function):
    """floor() with a straight-through gradient (activation quantisation = arithmetic shift)."""

    @staticmethod
    def forward(ctx, x):
        return torch.floor(x)

    @staticmethod
    def backward(ctx, g):
        return g


class QMLP(nn.Module):
    """Integer semantics per layer i: acc = W_int @ a_int + b_int (scale 2^-(fa_in + fw)),
    hidden: a_out = clamp(acc >> (fa_in + fw - fa), 0, 2^abits - 1); output: argmax(acc)."""

    def __init__(self, in_features, hidden, classes=5, wbits=6, abits=6, in_frac=10, act_frac=4):
        super().__init__()
        sizes = [in_features] + list(hidden) + [classes]
        self.fcs = nn.ModuleList(nn.Linear(sizes[i], sizes[i + 1]) for i in range(len(sizes) - 1))
        self.wbits, self.abits, self.in_frac, self.act_frac = wbits, abits, in_frac, act_frac
        self.classes = classes

    def wfrac(self, w):
        """Fractional bits of a layer's weights: the largest power-of-two scale at which the largest
        |weight| still fits into wbits signed bits."""
        m = float(w.detach().abs().max().clamp_min(1e-8))
        return int(math.floor(math.log2((2 ** (self.wbits - 1) - 1) / m)))

    def forward(self, xq):
        a = xq / 2 ** self.in_frac
        fa_in = self.in_frac
        for i, fc in enumerate(self.fcs):
            fw = self.wfrac(fc.weight)
            wq = torch.clamp(_RoundSTE.apply(fc.weight * 2 ** fw), -2 ** (self.wbits - 1), 2 ** (self.wbits - 1) - 1) / 2 ** fw
            bq = _RoundSTE.apply(fc.bias * 2 ** (fa_in + fw)) / 2 ** (fa_in + fw)
            acc = a @ wq.t() + bq
            if i == len(self.fcs) - 1:
                return acc
            a = torch.clamp(_FloorSTE.apply(F.relu(acc) * 2 ** self.act_frac), 0, 2 ** self.abits - 1) / 2 ** self.act_frac
            fa_in = self.act_frac

    def spec(self):
        layers, fa_in = [], self.in_frac
        for i, fc in enumerate(self.fcs):
            fw = self.wfrac(fc.weight)
            w = torch.clamp(torch.round(fc.weight * 2 ** fw), -2 ** (self.wbits - 1), 2 ** (self.wbits - 1) - 1)
            b = torch.round(fc.bias * 2 ** (fa_in + fw))
            last = i == len(self.fcs) - 1
            layers.append({"w": w.long().cpu().tolist(), "b": b.long().cpu().tolist(),
                           "shift": None if last else fa_in + fw - self.act_frac})
            fa_in = self.act_frac
        return {"type": "qmlp", "classes": self.classes, "abits": self.abits, "in_frac": self.in_frac,
                "layers": layers}


# ----------------------------------------------------------------------------- integer reference
def argmax_first(s):
    return np.argmax(s, axis=1).astype(np.uint8)  # numpy argmax returns the first maximum


def dwn_forward_spec(spec, Xq, flips=None):
    """Returns (scores, pred). flips: optional list of (layer, lut, entry) to invert."""
    tb = _thermo_np(spec, Xq)
    x = tb
    for li, L in enumerate(spec["layers"]):
        m = np.asarray(L["mapping"])
        t = np.asarray(L["tables"], dtype=np.uint8).copy()
        if flips:
            for (fl, j, e) in flips:
                if fl == li:
                    t[j, e] ^= 1
        n = m.shape[1]
        addr = np.zeros((x.shape[0], m.shape[0]), dtype=np.int64)
        for l in range(n):
            addr |= x[:, m[:, l]].astype(np.int64) << l
        x = t[np.arange(m.shape[0])[None, :], addr]
    s = x.reshape(x.shape[0], spec["classes"], -1).sum(-1)
    return s, argmax_first(s)


def dlgn_forward_spec(spec, Xq):
    x = _thermo_np(spec, Xq)
    tt = np.array(GATE_TT, dtype=np.uint8)
    for L in spec["layers"]:
        a = x[:, L["a"]].astype(np.uint8)
        b = x[:, L["b"]].astype(np.uint8)
        g = tt[np.asarray(L["gate"])]
        x = (g[None, :] >> ((a << 1) | b)) & 1
    s = x.reshape(x.shape[0], spec["classes"], -1).sum(-1)
    return s, argmax_first(s)


def qmlp_forward_spec(spec, Xq):
    a = Xq.astype(np.int64)
    for L in spec["layers"]:
        acc = a @ np.asarray(L["w"], dtype=np.int64).T + np.asarray(L["b"], dtype=np.int64)[None, :]
        if L["shift"] is None:
            return acc, argmax_first(acc)
        sh = L["shift"]
        v = acc >> sh if sh >= 0 else acc << (-sh)
        a = np.clip(v, 0, 2 ** spec["abits"] - 1)


def _thermo_np(spec, Xq):
    bl = spec["thermometer"]
    f = np.array([b[0] for b in bl])
    t = np.array([b[1] for b in bl])
    return (Xq[:, f] > t[None, :]).astype(np.uint8)


def predict_spec(spec, Xq):
    return {"dwn": dwn_forward_spec, "dlgn": dlgn_forward_spec, "qmlp": qmlp_forward_spec}[spec["type"]](spec, Xq)
