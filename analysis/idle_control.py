"""Frozen-input control: the IMUX bits whose upset disconnects a LUT input, re-injected while the DUT
inputs rest on another idle vector (fi/idle_control.tcl).

  python analysis/idle_control.py <model> <build> <control.tsv> <idle vector>
If a disconnected input keeps the value it had at the time of the upset, the hardware outcome must
now match the model evaluated with the thermometer bit of the new idle vector, and no longer the one
of vector 1024 (the idle vector of the exhaustive campaigns). Uses results/imux_model_<build>_bits.json.
Writes results/idle_control_<build>.json.
"""
from pathlib import Path
import json
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "train"))
sys.path.insert(0, str(ROOT / "analysis"))
import models as M  # noqa: E402
from cram_map import CramMap, load_farlist  # noqa: E402
from sw_faults import load_vectors  # noqa: E402


def main():
    model, build, ctl, idle = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
    spec = json.loads((ROOT / f"models/{model}.json").read_text())
    X, y = load_vectors(build if (ROOT / f"hw/gen/{build}/vectors.mem").exists() else model)
    tb = M._thermo_np(spec, X)
    mp = np.asarray(spec["layers"][0]["mapping"])
    tab = np.asarray(spec["layers"][0]["tables"], dtype=np.int64)
    L, n = mp.shape
    g = L // spec["classes"]
    addr = np.zeros((len(X), L), dtype=np.int64)
    for l in range(n):
        addr |= tb[:, mp[:, l]].astype(np.int64) << l
    out = tab[np.arange(L)[None, :], addr]
    scores = out.reshape(len(X), spec["classes"], g).sum(-1)
    pred0 = np.argmax(scores, axis=1)
    corr0 = int((pred0 == y).sum())

    def frozen(j, l, value):
        """(mism, corr) with input l of LUT j held at a constant."""
        a = (addr[:, j] & ~(1 << l)) | (int(value) << l)
        d = tab[j, a] - out[:, j]
        idx = np.nonzero(d)[0]
        if len(idx) == 0:
            return 0, corr0
        s = scores[idx].copy()
        s[:, j // g] += d[idx]
        p = np.argmax(s, axis=1)
        return int((p != pred0[idx]).sum()), corr0 - int((pred0[idx] == y[idx]).sum()) + int((p == y[idx]).sum())

    # hardware outcomes of the control run, by CRAM address
    hw = {}
    for line in Path(ctl).read_text().splitlines():
        fi, w, b, mm, cc, vm = map(int, line.split("\t"))
        hw[(fi, w, b)] = (mm, cc)
    cm = CramMap()
    far_list, far_index, _ = load_farlist()
    bits = json.loads((ROOT / f"results/imux_model_{build}_bits.json").read_text())
    n = ex_new = ex_old = crit_new = crit_old_hw = flipped = changed_value = 0
    for j, l, tile, wire, bit, why, hw_old, pred_old, *_ in bits:
        if why != "open":
            continue
        ff, bb = map(int, bit.split("_"))
        base = cm.tg[tile]["bits"]["CLB_IO_CLK"]
        key = (far_index[int(base["baseaddr"], 16) + ff], base["offset"] + bb // 32, bb % 32)
        if key not in hw:
            continue
        k = mp[j, l]
        p_new = frozen(j, l, tb[idle, k])        # model with the new idle vector
        p_old = frozen(j, l, tb[1024, k])        # model with the campaign's idle vector
        h = hw[key]
        n += 1
        ex_new += tuple(h) == tuple(p_new)
        ex_old += tuple(h) == tuple(p_old)
        crit_new += h[0] > 0
        crit_old_hw += hw_old[0] > 0
        flipped += (h[0] > 0) != (hw_old[0] > 0)
        changed_value += int(tb[idle, k]) != int(tb[1024, k])
    res = {"build": build, "idle_vector": idle, "bits": n, "frozen_value_changed": changed_value,
           "exact_with_new_idle": ex_new / n, "exact_with_old_idle": ex_old / n,
           "critical_campaign_idle1024": crit_old_hw, "critical_control": crit_new, "critical_status_changed": flipped}
    (ROOT / f"results/idle_control_{build}.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
