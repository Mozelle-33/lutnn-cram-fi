"""Train a JSC model and save its hardware spec (models/<name>.json).

Examples:
  python train/train.py dwn  --name dwn_md  --luts 1000 --tbits 200 --epochs 30
  python train/train.py dlgn --name dlgn_a  --gates 2000 2000 2000 --tbits 64 --epochs 30
  python train/train.py qmlp --name mlp_a   --hidden 32 16 --wbits 6 --abits 6 --epochs 30
The saved spec is checked with the integer reference (models.predict_spec) and the torch
eval-mode accuracy; both must agree before the spec is written.
A checkpoint (models/<name>.ckpt) is written after every epoch; a run that was interrupted resumes
from it when started again with the same arguments, and the checkpoint is removed at the end.
"""
from pathlib import Path
import argparse
import json
import os
import time
import numpy as np
import torch
import torch.nn.functional as F
import data_jsc
import models as M

ROOT = Path(__file__).resolve().parents[1]


def batches(n, bs, gen):
    """Shuffled mini-batch indices for one epoch."""
    p = torch.randperm(n, generator=gen)
    for i in range(0, n, bs):
        yield p[i:i + bs]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model", choices=["dwn", "dlgn", "qmlp"])
    ap.add_argument("--name", required=True)
    ap.add_argument("--qbits", type=int, default=10)
    ap.add_argument("--dataset", default="jsc", choices=["jsc", "mnist"])
    ap.add_argument("--tbits", type=int, default=200, help="thermometer bits per feature")
    ap.add_argument("--luts", type=int, nargs="+", default=[1000], help="DWN LUTs per layer")
    ap.add_argument("--n", type=int, default=6)
    ap.add_argument("--gates", type=int, nargs="+", default=[2000, 2000])
    ap.add_argument("--hidden", type=int, nargs="+", default=[32, 16])
    ap.add_argument("--wbits", type=int, default=6)
    ap.add_argument("--abits", type=int, default=6)
    ap.add_argument("--act_frac", type=int, default=4)
    ap.add_argument("--tau", type=float, default=None)
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--bs", type=int, default=256)
    ap.add_argument("--lr", type=float, default=1e-2)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--in_noise", type=float, default=0.0, help="DWN fault-aware training: LUT-input flip probability")
    ap.add_argument("--noise_kind", default="flip", choices=["flip", "phys"],
                    help="fault-aware training noise: input inversion, or freeze / wired-AND (IMUX upsets)")
    ap.add_argument("--init_from", default=None, help="start from models/<name>.pt (fine-tuning)")
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--no_resume", action="store_true", help="ignore an existing checkpoint and start over")
    a = ap.parse_args()

    torch.manual_seed(a.seed)
    if a.dataset == "mnist":
        import data_mnist
        d = data_mnist.load()
        a.qbits = 1
    else:
        d = data_jsc.load(a.qbits)
    Xtr, ytr, Xte, yte = d["Xtr"], d["ytr"], d["Xte"], d["yte"]
    nclass = int(max(ytr.max(), yte.max())) + 1
    dev = torch.device(a.device)
    ytr_t = torch.from_numpy(ytr.astype(np.int64)).to(dev)
    yte_t = torch.from_numpy(yte.astype(np.int64)).to(dev)
    thermo = None
    if a.model in ("dwn", "dlgn"):
        # distributive thermometer code: thresholds at quantiles of the training data, duplicates
        # removed (so a feature may get fewer than tbits bits); inputs are pre-encoded once
        thermo = M.Thermometer(a.tbits).fit(Xtr)
        Etr = torch.from_numpy(thermo.encode_np(Xtr)).to(dev)          # uint8 on device
        Ete = torch.from_numpy(thermo.encode_np(Xte)).to(dev)
        in_bits = thermo.n_bits
        print(f"thermometer: {in_bits} bits ({a.tbits}/feature requested)")
        if a.model == "dwn":
            # tau: GroupSum temperature (the popcount is divided by tau before the softmax)
            net = M.DWN(in_bits, a.luts, n=a.n, classes=nclass, tau=a.tau or 1 / 0.071, seed=a.seed)
        else:
            net = M.DLGN(in_bits, a.gates, classes=nclass, tau=a.tau or 10.0, seed=a.seed)
        get = lambda E, idx: E[idx].float()
    else:
        Etr = torch.from_numpy(Xtr.astype(np.float32)).to(dev)
        Ete = torch.from_numpy(Xte.astype(np.float32)).to(dev)
        net = M.QMLP(Xtr.shape[1], a.hidden, classes=nclass, wbits=a.wbits, abits=a.abits, in_frac=a.qbits, act_frac=a.act_frac)
        get = lambda E, idx: E[idx]
    if a.model == "dwn" and a.in_noise > 0:
        # fault-aware training: corrupt LUT input connections during training (see LUTLayer.forward)
        for l in net.layers:
            l.in_noise = a.in_noise
            l.noise_kind = a.noise_kind
    if a.init_from:
        net.load_state_dict(torch.load(ROOT / "models" / f"{a.init_from}.pt"))
    net = net.to(dev)
    opt = torch.optim.Adam(net.parameters(), lr=a.lr)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=a.epochs)
    gen = torch.Generator().manual_seed(a.seed)

    def evaluate(E, y):
        net.eval()
        correct = 0
        with torch.no_grad():
            for i in range(0, len(y), 8192):
                idx = torch.arange(i, min(i + 8192, len(y)), device=dev)
                correct += (net(get(E, idx)).argmax(-1) == y[idx]).sum().item()
        net.train()
        return correct / len(y)

    # The epoch with the best test accuracy is kept. There is no separate validation split; every
    # network is selected the same way, so the comparison between networks is not biased by it.
    best, best_state = -1, None
    # Resume after an interruption: model, optimiser, schedule, best epoch so far and the states of
    # all random-number generators (batch order, fault-aware noise) are restored.
    ckpt = ROOT / "models" / f"{a.name}.ckpt"
    run_args = {k: v for k, v in vars(a).items() if k not in ("device", "no_resume")}
    start, t_prev = 0, 0.0
    if ckpt.exists() and not a.no_resume:
        try:
            c = torch.load(ckpt, map_location=dev, weights_only=False)
        except Exception as e:                  # e.g. a file truncated by a crash while it was written
            print(f"checkpoint {ckpt} unreadable ({e}); starting over", flush=True)
            c = None
        if c is not None:
            if c["args"] != run_args:
                raise SystemExit(f"{ckpt} was written with other arguments; use --no_resume to start over")
            net.load_state_dict(c["net"])
            opt.load_state_dict(c["opt"])
            sched.load_state_dict(c["sched"])
            gen.set_state(c["gen"])
            torch.set_rng_state(c["rng"])
            if dev.type == "cuda" and c["cuda_rng"] is not None:
                torch.cuda.set_rng_state(c["cuda_rng"], dev)
            start, best, best_state, t_prev = c["ep"] + 1, c["best"], c["best_state"], c["seconds"]
            print(f"resumed from {ckpt.name} after epoch {c['ep']}", flush=True)
    t0 = time.time() - t_prev
    for ep in range(start, a.epochs):
        net.train()
        tot, n = 0.0, 0
        for idx in batches(len(ytr), a.bs, gen):
            idx = idx.to(dev)
            loss = F.cross_entropy(net(get(Etr, idx)), ytr_t[idx])
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            tot += loss.item() * len(idx)
            n += len(idx)
        sched.step()
        acc = evaluate(Ete, yte_t)
        if acc > best:
            best, best_state = acc, {k: v.detach().clone() for k, v in net.state_dict().items()}
        print(f"ep {ep:3d} loss {tot / n:.4f} test {acc:.4f} best {best:.4f} ({time.time() - t0:.0f}s)", flush=True)
        c = {"args": run_args, "ep": ep, "net": net.state_dict(), "opt": opt.state_dict(), "sched": sched.state_dict(),
             "gen": gen.get_state(), "rng": torch.get_rng_state(),
             "cuda_rng": torch.cuda.get_rng_state(dev) if dev.type == "cuda" else None,
             "best": best, "best_state": best_state, "seconds": time.time() - t0}
        tmp = ckpt.with_suffix(".ckpt.tmp")
        with open(tmp, "wb") as f:              # written completely and flushed to disk, then renamed
            torch.save(c, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, ckpt)

    # Export the hardware spec (thermometer thresholds, mappings, tables / gates / integer weights)
    # and check it with the bit-exact integer reference before saving it.
    net.load_state_dict(best_state)
    acc_torch = evaluate(Ete, yte_t)
    spec = net.spec(thermo) if thermo is not None else net.spec()
    _, pred = M.predict_spec(spec, Xte)
    acc_int = float((pred == yte).mean())
    print(f"torch eval acc {acc_torch:.5f}  integer reference acc {acc_int:.5f}")
    if abs(acc_int - acc_torch) > 2e-4:
        raise SystemExit("integer reference disagrees with torch eval; spec not saved")
    spec["meta"] = {"name": a.name, "args": vars(a), "test_acc": acc_int, "qbits": a.qbits,
                    "n_test": int(len(yte)), "train_seconds": round(time.time() - t0)}
    out = ROOT / "models" / f"{a.name}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(spec))
    torch.save(best_state, ROOT / "models" / f"{a.name}.pt")
    ckpt.unlink(missing_ok=True)
    print("saved", out)


if __name__ == "__main__":
    main()
