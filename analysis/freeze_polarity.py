"""Does a disconnected route input read its frozen idle value, or the other one?

  python analysis/freeze_polarity.py <build> <idle tag> <frozen-0 tag> <frozen-1 tag> [...]
Compares the per-bit outputs of route_model.py run on the same campaign with the idle-vector value
(default), with --frozen 0 and with --frozen 1. For every freeze bit for which the two polarities
predict different outcomes, the hardware outcome is classified as "held" (the idle value), "flipped"
(the other value) or "other" (neither, e.g. a value that changed during the test run), separately for
idle values 0 and 1. Writes results/freeze_polarity_<build>.json.
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def bits(build, tag):
    return json.loads((ROOT / f"results/route_model_{build}_{tag}_bits.json").read_text())


def main():
    build, args = sys.argv[1], sys.argv[2:]
    out = {}
    for t_idle, t0, t1 in zip(args[::3], args[1::3], args[2::3]):
        a, b0, b1 = bits(build, t_idle), bits(build, t0), bits(build, t1)
        res = {}
        for v in (0, 1):
            c = Counter()
            for x, y0, y1 in zip(a, b0, b1):
                assert x[:4] == y0[:4] == y1[:4]
                if x[4] != "freeze":
                    continue
                p_idle = tuple(x[7])
                p_v, p_other = (tuple(y0[7]), tuple(y1[7])) if v == 0 else (tuple(y1[7]), tuple(y0[7]))
                if p_idle != p_v or p_v == p_other:
                    continue                      # idle value is not v, or both values give the same outcome
                hw = tuple(x[6])
                c["held" if hw == p_v else "flipped" if hw == p_other else "other"] += 1
            n = sum(c.values())
            res[f"idle {v}"] = dict(c, distinguishable=n, held_frac=c["held"] / max(1, n),
                                    flipped_frac=c["flipped"] / max(1, n))
            print(t_idle, f"idle value {v}:", dict(c), f"held {100 * c['held'] / max(1, n):.2f}% of {n}")
        out[t_idle] = res
    (ROOT / f"results/freeze_polarity_{build}.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
