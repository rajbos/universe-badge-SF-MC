#!/usr/bin/env python3
"""
Convert an Arnis-generated Minecraft Java Edition world into a compact
heightmap the GitHub Universe 2026 badge can render.

Reads the Anvil region files (world/region/r.X.Z.mca, 1.18+ chunk format),
finds the top solid block of every column, downsamples, and writes:

  world.bin   binary map (see FORMAT below)
  world.json  metadata (dimensions, scale, palette, block legend)
  world.png   optional preview (needs Pillow)

FORMAT (little-endian):
  magic   4 bytes  b"SFMP"
  version u8       1
  width   u16      map cells (x)
  height  u16      map cells (z)
  scale   u8       blocks per cell
  ymin    i16      lowest world y represented (height bytes are relative)
  ncol    u8       palette entries
  palette ncol*3   RGB
  heights width*height  u8, y - ymin, clamped to 255
  blocks  width*height  u8, palette index

Usage:
  python arnis_to_badge.py /path/to/.minecraft/saves/ArnisWorld -o out --scale 2
"""

import argparse
import json
import math
import os
import struct
import sys
import zlib
from collections import Counter

try:
    import numpy as np
except ImportError:  # keep going, just slower
    np = None


# ---------------------------------------------------------------- NBT reader

TAG_END, TAG_BYTE, TAG_SHORT, TAG_INT, TAG_LONG, TAG_FLOAT, TAG_DOUBLE = range(7)
TAG_BYTE_ARRAY, TAG_STRING, TAG_LIST, TAG_COMPOUND, TAG_INT_ARRAY, TAG_LONG_ARRAY = range(7, 13)


class NBT:
    """Minimal big-endian NBT parser. Returns plain Python containers."""

    def __init__(self, data):
        self.d = data
        self.p = 0

    def u8(self):
        v = self.d[self.p]
        self.p += 1
        return v

    def unpack(self, fmt):
        size = struct.calcsize(fmt)
        v = struct.unpack_from(fmt, self.d, self.p)[0]
        self.p += size
        return v

    def string(self):
        n = self.unpack(">H")
        s = self.d[self.p : self.p + n].decode("utf-8", "replace")
        self.p += n
        return s

    def payload(self, tag):
        if tag == TAG_BYTE:
            return self.unpack(">b")
        if tag == TAG_SHORT:
            return self.unpack(">h")
        if tag == TAG_INT:
            return self.unpack(">i")
        if tag == TAG_LONG:
            return self.unpack(">q")
        if tag == TAG_FLOAT:
            return self.unpack(">f")
        if tag == TAG_DOUBLE:
            return self.unpack(">d")
        if tag == TAG_BYTE_ARRAY:
            n = self.unpack(">i")
            v = self.d[self.p : self.p + n]
            self.p += n
            return v
        if tag == TAG_STRING:
            return self.string()
        if tag == TAG_LIST:
            sub = self.u8()
            n = self.unpack(">i")
            return [self.payload(sub) for _ in range(n)]
        if tag == TAG_COMPOUND:
            out = {}
            while True:
                t = self.u8()
                if t == TAG_END:
                    return out
                name = self.string()
                out[name] = self.payload(t)
        if tag == TAG_INT_ARRAY:
            n = self.unpack(">i")
            v = struct.unpack_from(">%di" % n, self.d, self.p)
            self.p += 4 * n
            return v
        if tag == TAG_LONG_ARRAY:
            n = self.unpack(">i")
            raw = self.d[self.p : self.p + 8 * n]
            self.p += 8 * n
            if np is not None:
                return np.frombuffer(raw, dtype=">u8").astype(np.uint64)
            return struct.unpack(">%dQ" % n, raw)
        raise ValueError("unknown tag %d" % tag)

    def root(self):
        t = self.u8()
        assert t == TAG_COMPOUND, "root must be compound"
        self.string()
        return self.payload(TAG_COMPOUND)


# ------------------------------------------------------------ Anvil regions

def iter_chunks(region_path):
    """Yield (chunk_x, chunk_z, nbt_dict) for every chunk in a region file."""
    with open(region_path, "rb") as f:
        data = f.read()
    if len(data) < 8192:
        return
    base = os.path.basename(region_path)
    _, rx, rz, _ = base.split(".")
    rx, rz = int(rx), int(rz)
    for i in range(1024):
        entry = struct.unpack_from(">I", data, i * 4)[0]
        offset = (entry >> 8) * 4096
        sectors = entry & 0xFF
        if offset == 0 or sectors == 0:
            continue
        length = struct.unpack_from(">I", data, offset)[0]
        comp = data[offset + 4]
        payload = data[offset + 5 : offset + 4 + length]
        if comp == 2:
            raw = zlib.decompress(payload)
        elif comp == 1:
            import gzip
            raw = gzip.decompress(payload)
        elif comp == 3:
            raw = payload
        else:
            continue
        nbt = NBT(raw).root()
        cx = rx * 32 + (i % 32)
        cz = rz * 32 + (i // 32)
        yield cx, cz, nbt


def unpack_section(section):
    """Return (palette_names, indices[4096]) for a 1.18+ section, or None if all air."""
    bs = section.get("block_states")
    if not bs:
        return None
    palette = [p["Name"] for p in bs["palette"]]
    if "data" not in bs:
        return palette, None  # single-block section
    data = bs["data"]
    bits = max(4, (len(palette) - 1).bit_length())
    per_long = 64 // bits
    mask = (1 << bits) - 1
    if np is not None:
        longs = np.asarray(data, dtype=np.uint64)
        shifts = (np.arange(per_long, dtype=np.uint64) * np.uint64(bits))
        idx = ((longs[:, None] >> shifts[None, :]) & np.uint64(mask)).reshape(-1)[:4096]
        return palette, idx.astype(np.int32)
    idx = []
    for l in data:
        for k in range(per_long):
            idx.append((l >> (k * bits)) & mask)
            if len(idx) == 4096:
                break
        if len(idx) == 4096:
            break
    return palette, idx


AIR = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air"}


def chunk_surface(nbt):
    """Return two 16x16 lists: top y and top block name per column (x, z)."""
    sections = nbt.get("sections") or nbt.get("Level", {}).get("Sections") or []
    sections = sorted(sections, key=lambda s: s.get("Y", 0), reverse=True)
    top_y = [[None] * 16 for _ in range(16)]
    top_b = [[None] * 16 for _ in range(16)]
    remaining = 256
    for sec in sections:
        if remaining == 0:
            break
        up = unpack_section(sec)
        if up is None:
            continue
        palette, idx = up
        if idx is None:
            if palette[0] in AIR:
                continue
            # whole section is one solid block: fills every unfilled column
            y = sec["Y"] * 16 + 15
            for x in range(16):
                for z in range(16):
                    if top_y[x][z] is None:
                        top_y[x][z] = y
                        top_b[x][z] = palette[0]
                        remaining -= 1
            continue
        air_ids = {i for i, n in enumerate(palette) if n in AIR}
        base_y = sec["Y"] * 16
        for y in range(15, -1, -1):
            row = y * 256
            for z in range(16):
                rz = row + z * 16
                for x in range(16):
                    if top_y[x][z] is not None:
                        continue
                    b = int(idx[rz + x])
                    if b in air_ids:
                        continue
                    top_y[x][z] = base_y + y
                    top_b[x][z] = palette[b]
                    remaining -= 1
            if remaining == 0:
                break
    return top_y, top_b


# ------------------------------------------------------------- block colours

# Minecraft-map-ish colours. Anything not listed falls back by keyword, then grey.
BLOCK_RGB = {
    "water": (63, 118, 228),
    "grass_block": (127, 178, 56),
    "sand": (247, 233, 163),
    "stone": (112, 112, 112),
    "cobblestone": (112, 112, 112),
    "gravel": (136, 136, 136),
    "dirt": (151, 109, 77),
    "coarse_dirt": (151, 109, 77),
    "podzol": (129, 94, 58),
    "oak_leaves": (0, 124, 0),
    "birch_leaves": (0, 124, 0),
    "spruce_leaves": (0, 100, 0),
    "oak_log": (102, 76, 51),
    "oak_planks": (162, 130, 78),
    "bricks": (151, 66, 50),
    "stone_bricks": (112, 112, 112),
    "white_concrete": (207, 213, 214),
    "light_gray_concrete": (125, 125, 115),
    "gray_concrete": (54, 57, 61),
    "black_concrete": (8, 10, 15),
    "red_concrete": (142, 32, 32),
    "orange_concrete": (224, 97, 0),
    "yellow_concrete": (240, 175, 21),
    "blue_concrete": (44, 46, 143),
    "light_blue_concrete": (35, 137, 198),
    "cyan_concrete": (21, 119, 136),
    "green_concrete": (73, 91, 36),
    "brown_concrete": (96, 59, 31),
    "white_terracotta": (209, 177, 161),
    "terracotta": (152, 94, 67),
    "glass": (180, 220, 240),
    "snow": (255, 255, 255),
    "asphalt": (54, 57, 61),
    "gray_wool": (76, 76, 76),
    "smooth_stone": (159, 159, 159),
    "andesite": (136, 136, 136),
    "polished_andesite": (136, 136, 136),
    "deepslate": (70, 70, 70),
    "mud": (70, 56, 40),
    "farmland": (120, 80, 50),
    "moss_block": (89, 125, 39),
    "rail": (90, 90, 90),
    "oak_fence": (162, 130, 78),
}
KEYWORD_RGB = [
    ("water", (63, 118, 228)),
    ("leaves", (0, 124, 0)),
    ("grass", (127, 178, 56)),
    ("sand", (247, 233, 163)),
    ("concrete", (120, 120, 120)),
    ("terracotta", (152, 94, 67)),
    ("brick", (151, 66, 50)),
    ("planks", (162, 130, 78)),
    ("log", (102, 76, 51)),
    ("wood", (102, 76, 51)),
    ("stone", (112, 112, 112)),
    ("slate", (70, 70, 70)),
    ("glass", (180, 220, 240)),
    ("wool", (160, 160, 160)),
    ("flower", (127, 178, 56)),
    ("rail", (90, 90, 90)),
]


def block_rgb(name):
    short = name.split(":", 1)[-1]
    if short in BLOCK_RGB:
        return BLOCK_RGB[short]
    for key, rgb in KEYWORD_RGB:
        if key in short:
            return rgb
    return (140, 140, 140)


# ------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("world", help="path to the Minecraft world folder (contains region/)")
    ap.add_argument("-o", "--out", default="out", help="output directory")
    ap.add_argument("--scale", type=int, default=2, help="blocks per map cell (default 2)")
    ap.add_argument("--max-colours", type=int, default=48, help="palette size cap (<=255)")
    ap.add_argument("--no-png", action="store_true", help="skip preview image")
    args = ap.parse_args()

    region_dir = os.path.join(args.world, "region")
    if not os.path.isdir(region_dir):
        sys.exit("no region/ folder in %s" % args.world)
    files = sorted(f for f in os.listdir(region_dir) if f.endswith(".mca"))
    if not files:
        sys.exit("no .mca files found")

    # pass 1: collect per-chunk surfaces
    chunks = {}
    for i, f in enumerate(files):
        print("reading %s (%d/%d)" % (f, i + 1, len(files)))
        for cx, cz, nbt in iter_chunks(os.path.join(region_dir, f)):
            status = nbt.get("Status", "")
            if status and "full" not in status and "minecraft:" not in status and status != "":
                pass  # Arnis writes full chunks; keep everything anyway
            ty, tb = chunk_surface(nbt)
            if all(v is None for col in ty for v in col):
                continue
            chunks[(cx, cz)] = (ty, tb)
    if not chunks:
        sys.exit("no non-empty chunks found")

    xs = [c[0] for c in chunks]
    zs = [c[1] for c in chunks]
    min_bx, max_bx = min(xs) * 16, max(xs) * 16 + 15
    min_bz, max_bz = min(zs) * 16, max(zs) * 16 + 15
    bw, bh = max_bx - min_bx + 1, max_bz - min_bz + 1
    print("world covers %d x %d blocks" % (bw, bh))

    # dense block-level arrays
    ys = [[None] * bw for _ in range(bh)]
    bs = [[None] * bw for _ in range(bh)]
    for (cx, cz), (ty, tb) in chunks.items():
        ox, oz = cx * 16 - min_bx, cz * 16 - min_bz
        for x in range(16):
            for z in range(16):
                ys[oz + z][ox + x] = ty[x][z]
                bs[oz + z][ox + x] = tb[x][z]

    # pass 2: downsample
    s = args.scale
    mw, mh = math.ceil(bw / s), math.ceil(bh / s)
    print("map is %d x %d cells at %d blocks/cell (%.1f KB)" % (mw, mh, s, mw * mh * 2 / 1024))
    ally = [v for row in ys for v in row if v is not None]
    ymin = min(ally)
    heights = bytearray(mw * mh)
    names = []  # per cell top block name
    for mz in range(mh):
        for mx in range(mw):
            best_y, best_b = None, None
            cnt = Counter()
            for dz in range(s):
                for dx in range(s):
                    z, x = mz * s + dz, mx * s + dx
                    if z >= bh or x >= bw:
                        continue
                    y, b = ys[z][x], bs[z][x]
                    if y is None:
                        continue
                    cnt[b] += 1
                    if best_y is None or y > best_y:
                        best_y, best_b = y, b
            if best_y is None:
                heights[mz * mw + mx] = 0
                names.append("minecraft:water")
            else:
                heights[mz * mw + mx] = min(255, best_y - ymin)
                # mode block keeps roads/water looking right; tallest keeps buildings
                names.append(cnt.most_common(1)[0][0] if best_b in AIR else best_b)

    # pass 3: palette
    freq = Counter(names)
    palette_names = [n for n, _ in freq.most_common(args.max_colours)]
    palette_rgb = [block_rgb(n) for n in palette_names]
    idx_of = {n: i for i, n in enumerate(palette_names)}
    # unlisted blocks map to nearest colour already in palette
    def nearest(rgb):
        return min(range(len(palette_rgb)), key=lambda i: sum((a - b) ** 2 for a, b in zip(palette_rgb[i], rgb)))
    blocks = bytearray(mw * mh)
    for i, n in enumerate(names):
        blocks[i] = idx_of[n] if n in idx_of else nearest(block_rgb(n))

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "world.bin"), "wb") as f:
        f.write(b"SFMP")
        f.write(struct.pack("<BHHBhB", 1, mw, mh, s, ymin, len(palette_rgb)))
        for rgb in palette_rgb:
            f.write(bytes(rgb))
        f.write(heights)
        f.write(blocks)
    meta = {
        "width": mw, "height": mh, "scale": s, "ymin": ymin,
        "origin_block": [min_bx, min_bz],
        "palette": [{"block": n, "rgb": c, "count": freq[n]} for n, c in zip(palette_names, palette_rgb)],
    }
    with open(os.path.join(args.out, "world.json"), "w") as f:
        json.dump(meta, f, indent=2)
    print("wrote %s/world.bin (%d bytes)" % (args.out, 4 + 9 + 3 * len(palette_rgb) + 2 * mw * mh))

    if not args.no_png:
        try:
            from PIL import Image
            img = Image.new("RGB", (mw, mh))
            px = img.load()
            hmax = max(heights) or 1
            for z in range(mh):
                for x in range(mw):
                    r, g, b = palette_rgb[blocks[z * mw + x]]
                    k = 0.6 + 0.4 * heights[z * mw + x] / hmax
                    px[x, z] = (int(r * k), int(g * k), int(b * k))
            img.save(os.path.join(args.out, "world.png"))
            print("wrote %s/world.png" % args.out)
        except ImportError:
            print("Pillow not installed, skipping preview")


if __name__ == "__main__":
    main()
