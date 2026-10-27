#!/usr/bin/env python3
"""
Desktop preview of the badge renderer. Executes the *same* viper kernels from
badge/apps/sfmap/__init__.py in plain Python (with ptr8/ptr16 stubs) against a
world.bin, and writes PNG frames. Slow (pure Python) but exact, so what you see
here is what the badge draws.

  python preview.py out/world.bin --x 48 --y 50 --angle -90 -o frame.png
"""
import argparse
import math
import os
import re
import struct
from array import array

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "..", "badge", "apps", "sfmap", "__init__.py")


def load_kernels():
    src = open(APP).read()
    out = {}
    for name in ("render16", "topdown16"):
        m = re.search(r"@micropython\.viper\ndef %s\((.*?)\n(?=\n\n|@micropython)" % name, src, re.S)
        body = "def %s(%s\n" % (name, m.group(1))
        body = re.sub(r"(\w+): int", r"\1", body)  # drop viper type hints
        ns = {
            "ptr8": lambda b: b,
            "ptr16": lambda b: b,
            "int": int,
            "range": range,
        }
        exec(body, ns)
        out[name] = ns[name]
    return out


def load_world(path):
    with open(path, "rb") as f:
        assert f.read(4) == b"SFMP"
        version, mw, mh, scale, ymin, ncol = struct.unpack("<BHHBhB", f.read(9))
        pal = [tuple(f.read(3)) for _ in range(ncol)]
        heights = bytearray(f.read(mw * mh))
        blocks = bytearray(f.read(mw * mh))
    return mw, mh, pal, heights, blocks


def rgb565(r, g, b):
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def to_rgb(v):
    return (((v >> 11) & 31) << 3, ((v >> 5) & 63) << 2, (v & 31) << 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("world")
    ap.add_argument("--x", type=float, default=None)
    ap.add_argument("--y", type=float, default=None)
    ap.add_argument("--angle", type=float, default=-90, help="degrees, -90 = north (map up)")
    ap.add_argument("--w", type=int, default=160)
    ap.add_argument("--h", type=int, default=120)
    ap.add_argument("--zfar", type=int, default=160)
    ap.add_argument("--hscale", type=int, default=90)
    ap.add_argument("--map", action="store_true", help="render the top-down view instead")
    ap.add_argument("--zoom", type=int, default=1)
    ap.add_argument("--up", type=int, default=3, help="upscale output")
    ap.add_argument("-o", "--out", default="frame.png")
    a = ap.parse_args()

    k = load_kernels()
    mw, mh, pal, heights, blocks = load_world(a.world)
    W, H = a.w, a.h
    fb = array("H", [0] * (W * H))
    palraw = array("H", [0] * (len(pal) * 4))
    for i, (r, g, b) in enumerate(pal):
        for f_ in range(4):
            kk = 1.0 - 0.22 * f_
            palraw[i * 4 + f_] = rgb565(int(r * kk + 150 * (1 - kk)), int(g * kk + 170 * (1 - kk)), int(b * kk + 210 * (1 - kk)))
    sky = array("H", [rgb565(150, 190, 235)])
    ybuf = array("H", [0] * W)

    cx = a.x if a.x is not None else mw / 2
    cy = a.y if a.y is not None else mh * 0.75
    ang = math.radians(a.angle)

    if a.map:
        ox = int(cx) - W // (2 * a.zoom)
        oy = int(cy) - H // (2 * a.zoom)
        k["topdown16"](fb, palraw, heights, blocks, W, H, mw, mh, ox, oy, a.zoom)
    else:
        xi, yi = int(cx), int(cy)
        eye = (heights[yi * mw + xi] if 0 <= xi < mw and 0 <= yi < mh else 0) + 6.0
        k["render16"](fb, palraw, sky, heights, blocks, ybuf, W, H, mw, mh,
                      int(cx * 65536), int(cy * 65536), int(eye * 65536), H // 2,
                      int(math.sin(ang) * 16384), int(math.cos(ang) * 16384),
                      a.zfar << 16, a.hscale)

    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        for x in range(W):
            px[x, y] = to_rgb(fb[y * W + x])
    if a.up > 1:
        img = img.resize((W * a.up, H * a.up), Image.NEAREST)
    img.save(a.out)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
