"""Check the vectorized EFD backward against a direct loop over the CUDA kernel's formula."""
import torch
import models as M

torch.manual_seed(0)
B, IN, L, n = 7, 20, 5, 6
x = (torch.rand(B, IN) > 0.5).float().requires_grad_(True)
layer = M.LUTLayer(IN, L, n, mapping="random", seed=1)
mapping, luts, K = layer.mapping, layer.luts, layer.K
out = M.EFDFunction.apply(x, mapping, luts, K)
g = torch.randn(B, L)
out.backward(g)

# reference: evaluate the kernel's formula entry by entry
alpha, beta = 0.5 * 0.75 ** (n - 1), 0.25 / 0.75
ig = torch.zeros(B, IN)
lg = torch.zeros(L, 1 << n)
for i in range(B):
    for j in range(L):
        addr = sum(int(x[i, mapping[j, l]] > 0) << l for l in range(n))
        lg[j, addr] += g[i, j]
        for l in range(n):
            w = 0.0
            for a2 in range(1 << n):
                dist = bin((addr & ~(1 << l)) ^ (a2 & ~(1 << l))).count("1")
                fd = luts[j, a2].item() * alpha * beta ** dist
                w += fd if (a2 >> l) & 1 else -fd
            ig[i, mapping[j, l]] += w * g[i, j]
        assert out[i, j].item() == luts[j, addr].item()
print("input grad max err", (ig - x.grad).abs().max().item())
print("lut grad max err", (lg - luts.grad).abs().max().item())
assert torch.allclose(ig, x.grad, atol=1e-5) and torch.allclose(lg, luts.grad, atol=1e-6)
print("EFD OK")
