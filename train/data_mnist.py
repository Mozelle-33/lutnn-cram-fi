"""MNIST (OpenML mnist_784) with binarised pixels (pixel > 127), standard 60k/10k split.

The harness stores one 784-bit vector per test sample, so the DWN reads the binary pixels
directly (1-bit "features"; the thermometer layer degenerates to x > 0).
Output: data/mnist_q1.npz with the same keys as data_jsc (Xtr, ytr, Xte, yte, qbits=1).
Usage: python train/data_mnist.py
"""
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def prepare():
    from sklearn.datasets import fetch_openml
    X, y = fetch_openml("mnist_784", version=1, return_X_y=True, as_frame=False,
                        data_home=str(ROOT / "data" / "openml"), parser="auto")
    Xb = (np.asarray(X) > 127).astype(np.uint16)
    y = np.asarray(y).astype(np.uint8)
    out = ROOT / "data" / "mnist_q1.npz"
    np.savez_compressed(out, Xtr=Xb[:60000], ytr=y[:60000], Xte=Xb[60000:], yte=y[60000:],
                        classes=np.arange(10), qbits=1)
    print(out, Xb.shape, np.bincount(y[:60000]))


def load(qbits=1):
    """The prepared arrays as a dict; qbits is accepted for interface compatibility with data_jsc."""
    d = np.load(ROOT / "data" / "mnist_q1.npz", allow_pickle=True)
    return {k: d[k] for k in d.files}


if __name__ == "__main__":
    prepare()
