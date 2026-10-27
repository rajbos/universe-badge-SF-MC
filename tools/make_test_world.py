#!/usr/bin/env python3
"""
Build a tiny synthetic 1.18+ Anvil world (water, a shoreline, a pier and a
few buildings) so arnis_to_badge.py can be tested without running Arnis.

  python make_test_world.py /tmp/testworld
"""
import math
import os
import struct
import sys
import zlib


def tag_string(s):
    b = s.encode()
    return struct.pack(">H", len(b)) + b


def nbt(value):
    """Encode a python value as (tag_id, payload)."""
    if isinstance(value, bool):
        return 1, struct.pack(">b", int(value))
    if isinstance(value, int):
        return 3, struct.pack(">i", value)
    if isinstance(value, str):
        return 8, tag_string(value)
    if isinstance(value, dict):
        out = b""
        for k, v in value.items():
            t, p = nbt(v)
            out += bytes([t]) + tag_string(k) + p
        return 10, out + b"\x00"
    if isinstance(value, list):
        if not value:
            return 9, b"\x00" + struct.pack(">i", 0)
        encoded = [nbt(v) for v in value]
        t = encoded[0][0]
        return 9, bytes([t]) + struct.pack(">i", len(value)) + b"".join(p for _, p in encoded)
    if isinstance(value, tuple):  # long array
        return 12, struct.pack(">i", len(value)) + struct.pack(">%dq" % len(value), *[v - (1 << 64) if v >= (1 << 63) else v for v in value])
    raise TypeError(type(value))


def pack_section(names):
    """names: 4096 block names in yzx order -> block_states compound."""
    palette = sorted(set(names))
    if len(palette) == 1:
        return {"palette": [{"Name": palette[0]}]}
    idx = {n: i for i, n in enumerate(palette)}
    bits = max(4, (len(palette) - 1).bit_length())
    per_long = 64 // bits
    longs = []
    for i in range(0, 4096, per_long):
        v = 0
        for k, n in enumerate(names[i : i + per_long]):
            v |= idx[n] << (k * bits)
        longs.append(v)
    return {"palette": [{"Name": n} for n in palette], "data": tuple(longs)}


def surface(wx, wz):
    """World design: returns (ground_y, block) for a block column."""
    # shoreline running diagonally, water to the north-east
    if wz < 20 + wx * 0.3:
        if 30 <= wx <= 34 and wz > -10:  # a pier sticking into the water
            return 64, "minecraft:oak_planks"
        return 62, "minecraft:water"
    y = 63 + int(3 * math.sin(wx / 9.0) + 2 * math.cos(wz / 7.0))
    if (wx // 16) % 3 == 1 and (wz // 16) % 2 == 1:  # a block of buildings
        h = 6 + ((wx * 7 + wz * 3) % 9)
        return y + h, "minecraft:light_gray_concrete"
    if wx % 24 in (0, 1) or wz % 24 in (0, 1):  # roads
        return y, "minecraft:gray_concrete"
    if (wx * 3 + wz * 5) % 17 == 0:
        return y + 4, "minecraft:oak_leaves"
    return y, "minecraft:grass_block"


def build_chunk(cx, cz):
    sections = []
    for sy in range(3, 7):  # y 48..111
        names = []
        for y in range(16):
            wy = sy * 16 + y
            for z in range(16):
                for x in range(16):
                    gy, top = surface(cx * 16 + x, cz * 16 + z)
                    if wy < gy:
                        names.append("minecraft:stone" if top != "minecraft:water" else "minecraft:sand")
                    elif wy == gy:
                        names.append(top)
                    else:
                        names.append("minecraft:air")
        sections.append({"Y": sy, "block_states": pack_section(names)})
    return {"DataVersion": 3465, "xPos": cx, "zPos": cz, "yPos": -4, "Status": "minecraft:full", "sections": sections}


def write_region(path, chunks):
    """chunks: dict {(cx, cz): nbt dict} all in region 0,0."""
    header = bytearray(8192)
    body = b""
    sector = 2
    for (cx, cz), chunk in chunks.items():
        t, p = nbt(chunk)
        raw = bytes([t]) + tag_string("") + p
        comp = zlib.compress(raw)
        blob = struct.pack(">I", len(comp) + 1) + b"\x02" + comp
        pad = (-len(blob)) % 4096
        blob += b"\x00" * pad
        n = len(blob) // 4096
        i = (cx % 32) + (cz % 32) * 32
        struct.pack_into(">I", header, i * 4, (sector << 8) | n)
        body += blob
        sector += n
    with open(path, "wb") as f:
        f.write(header + body)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "testworld"
    os.makedirs(os.path.join(out, "region"), exist_ok=True)
    chunks = {(cx, cz): build_chunk(cx, cz) for cx in range(6) for cz in range(4)}
    write_region(os.path.join(out, "region", "r.0.0.mca"), chunks)
    print("wrote", out, "with", len(chunks), "chunks (96 x 64 blocks)")


if __name__ == "__main__":
    main()
