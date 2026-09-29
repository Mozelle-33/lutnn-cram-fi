"""Configuration-memory address <-> fabric feature mapping for the XC7K325T (prjxray-db, openXC7).

Forward: lut_init_address(site, bel, k) -> (FAR, word, bit) of INIT[k] of a LUT6 BEL.
Reverse: CramMap.lookup(far, word, bit) -> (tile, tile_type, [features]) and classify() ->
  LUT_INIT | CLB_OTHER | ROUTING | HCLK | BRAM_DSP | UNMAPPED
Addresses: segbits "FF_BB" = frame offset FF from the tile's baseaddr, bit BB counted from the
first word of the tile (word = offset + BB // 32, bit-in-word = BB % 32).
"""
from pathlib import Path
import json
import re
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "tools/prjxray-db/kintex7"


class CramMap:
    def __init__(self):
        """Index the tile grid by frame-column base address; segbits are loaded lazily per tile type."""
        self.tg = json.loads((DB / "xc7k325t/tilegrid.json").read_text())
        # column base -> list of (offset, words, tile, type, frames)
        self.col = defaultdict(list)
        self.site_tile = {}
        for name, t in self.tg.items():
            b = t["bits"].get("CLB_IO_CLK")
            if b:
                self.col[int(b["baseaddr"], 16)].append((b["offset"], b["words"], name, t["type"], b["frames"]))
            for s in t["sites"]:
                self.site_tile[s] = name
        self.bases = sorted(self.col)
        self._seg = {}

    # ---- segbits ----
    def segbits(self, ttype):
        """feature -> [(ff, bb, value)], and reverse (ff, bb) -> [(feature, value)]."""
        if ttype not in self._seg:
            fwd, rev = {}, defaultdict(list)
            p = DB / f"segbits_{ttype.lower()}.db"
            if p.exists():
                for line in p.read_text().splitlines():
                    parts = line.split()
                    if len(parts) < 2:
                        continue
                    bits = []
                    for tok in parts[1:]:
                        neg = tok.startswith("!")
                        ff, bb = tok.lstrip("!").split("_")
                        bits.append((int(ff), int(bb), 0 if neg else 1))
                        rev[(int(ff), int(bb))].append((parts[0], 0 if neg else 1))
                    fwd[parts[0]] = bits
            self._seg[ttype] = (fwd, rev)
        return self._seg[ttype]

    # ---- forward: LUT INIT bit ----
    def lut_init_address(self, site, bel, k):
        """(FAR, word, bit, value, feature) of INIT[k] of the LUT at site/bel. The SLICEM of a CLBLM
        tile is the first slice (X0); INIT bits are documented per LUT letter A..D."""
        tile = self.site_tile[site]
        t = self.tg[tile]
        ttype = t["type"]
        xs = sorted(int(re.match(r"SLICE_X(\d+)Y", s).group(1)) for s in t["sites"] if s.startswith("SLICE"))
        pos = xs.index(int(re.match(r"SLICE_X(\d+)Y", site).group(1)))
        kind = "SLICEM" if (ttype.startswith("CLBLM") and pos == 0) else "SLICEL"
        letter = bel.split(".")[-1][0]          # Vivado BEL "SLICEL.A6LUT" -> "A"
        feat = f"{ttype}.{kind}_X{pos}.{letter}LUT.INIT[{k:02d}]"
        fwd, _ = self.segbits(ttype)
        (ff, bb, val), = fwd[feat]
        b = t["bits"]["CLB_IO_CLK"]
        return int(b["baseaddr"], 16) + ff, b["offset"] + bb // 32, bb % 32, val, feat

    # ---- reverse ----
    def lookup(self, far, word, bit):
        """A CLB tile and its INT tile share frames and words; return the tile whose segbits
        document this bit (the first covering tile if none does)."""
        base = far & ~0x7F
        minor = far & 0x7F
        first = None
        for off, words, tile, ttype, frames in self.col.get(base, []):
            if off <= word < off + words and minor < frames:
                ff, bb = minor, (word - off) * 32 + bit
                _, rev = self.segbits(ttype)
                feats = rev.get((ff, bb), [])
                if feats:
                    return tile, ttype, feats
                if first is None:
                    first = (tile, ttype, [])
        return first if first else (None, None, [])

    def classify(self, far, word, bit):
        """Resource class of a CRAM bit, from the tile type and feature that document it."""
        tile, ttype, feats = self.lookup(far, word, bit)
        if tile is None:
            return "UNMAPPED", tile, feats
        if not feats:
            return "UNMAPPED", tile, feats
        if ttype.startswith("CLBL"):
            if any(re.search(r"\.[A-D]LUT\.INIT\[", f) for f, _ in feats):
                return "LUT_INIT", tile, feats
            return "CLB_OTHER", tile, feats
        if ttype.startswith("INT_"):
            return "ROUTING", tile, feats
        if ttype.startswith("HCLK"):
            return "HCLK", tile, feats
        if ttype.startswith(("BRAM", "DSP")):
            return "BRAM_DSP", tile, feats
        return "OTHER", tile, feats


def load_farlist():
    """Frame addresses of hw/gen/farlist.json: (list, address -> index, full JSON)."""
    d = json.loads((ROOT / "hw/gen/farlist.json").read_text())
    far = [f["far"] for f in d["frames"]]
    return far, {f: i for i, f in enumerate(far)}, d


def sem_pfa(far, word, bit):
    """40-bit SEM error-injection command, physical frame address (PG036 Fig. 3-17)."""
    bt, half, row = (far >> 23) & 3, (far >> 22) & 1, (far >> 17) & 31
    col, minor = (far >> 7) & 1023, far & 127
    return (bt << 35) | (half << 34) | (row << 29) | (col << 19) | (minor << 12) | (word << 5) | bit


if __name__ == "__main__":
    m = CramMap()
    print(m.lut_init_address("SLICE_X0Y200", "A6LUT", 0))
    print(m.lut_init_address("SLICE_X3Y210", "D6LUT", 63))
    far, word, bit, val, feat = m.lut_init_address("SLICE_X2Y205", "C6LUT", 17)
    print(feat, hex(far), word, bit, m.classify(far, word, bit)[0], hex(sem_pfa(far, word, bit)))
