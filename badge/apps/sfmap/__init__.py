"""
SF Map: walk around a Minecraft world of San Francisco on the badge.

Loads world.bin (made by tools/arnis_to_badge.py from an Arnis world) and
renders it two ways:
  * voxelspace first-person view (Comanche style) written straight into the
    screen buffer with a viper kernel
  * top-down Minecraft-map view with pan and zoom

Controls (logical, orientation aware):
  UP / DOWN      walk forward / back
  LEFT / RIGHT   turn (3D) or pan (map)
  SELECT         toggle 3D / map
  MENU           toggle tilt steering (IMU) in 3D
  BACK           cycle view distance (3D) or zoom (map)
  HOME           back to launcher (firmware)
"""

import math
import os
import struct
import sys

APP_DIR = "/system/apps/sfmap"
os.chdir(APP_DIR)
sys.path.insert(0, APP_DIR)

# 160x120 keeps the voxel kernel comfortably interactive. Set HIRES = True for
# 320x240 at roughly a quarter of the frame rate.
USE_HIRES = False
if USE_HIRES:
    badge.mode(HIRES | VSYNC)
badge.default_clear = None  # we fill every pixel ourselves

W = screen.width
H = screen.height
PIXEL_BYTES = len(screen.raw) // (W * H)


# ------------------------------------------------------------- load world

def fatal(msg):
    while True:
        screen.pen = color.rgb(20, 24, 28)
        screen.clear()
        screen.pen = color.rgb(255, 255, 255)
        screen.font = font.nope
        screen.text("SF Map", 8, 8)
        screen.text(msg, 8, 28)
        badge.update()


try:
    with open("world.bin", "rb") as f:
        magic = f.read(4)
        if magic != b"SFMP":
            fatal("world.bin: bad magic")
        version, MW, MH, SCALE, YMIN, NCOL = struct.unpack("<BHHBhB", f.read(9))
        PALETTE_RGB = [tuple(f.read(3)) for _ in range(NCOL)]
        HEIGHTS = bytearray(f.read(MW * MH))
        BLOCKS = bytearray(f.read(MW * MH))
except OSError:
    fatal("world.bin missing")

# Raw pixel value per palette entry, in the screen's native format, by drawing
# each colour into a 1-pixel-high strip and reading the buffer back. A plain
# image(w, h) is a 32-bit buffer regardless of the screen mode, so when the
# screen is RGB565 the strip must be created as RGB565 too (as brattal does),
# or the ptr16 lookups in the kernels read half-pixels and every colour is wrong.


def screen_like_image(w, h):
    if PIXEL_BYTES == 2:
        return image(w, h, bytearray(w * h * 2), image.RGB565)
    return image(w, h)


strip = screen_like_image(NCOL * 4, 1)
strip.antialias = image.OFF
for i, (r, g, b) in enumerate(PALETTE_RGB):
    # 4 fog levels per colour: 0 = near, 3 = far
    for f_ in range(4):
        k = 1.0 - 0.22 * f_
        strip.pen = color.rgb(int(r * k + 150 * (1 - k)), int(g * k + 170 * (1 - k)), int(b * k + 210 * (1 - k)))
        strip.put(i * 4 + f_, 0)
PAL_RAW = bytearray(strip.raw)

sky = screen_like_image(1, 1)
sky.pen = color.rgb(150, 190, 235)
sky.clear()
SKY_RAW = bytearray(sky.raw)

water_idx = 0
for i, rgb in enumerate(PALETTE_RGB):
    if rgb == (63, 118, 228):
        water_idx = i
        break

# ------------------------------------------------------------- 3D kernel
#
# Classic voxelspace: for each distance z, walk the segment between the
# left and right ray endpoints, project the terrain height to a screen row
# and fill the span above the previous row stored in ybuf. 16.16 fixed point.

YBUF = bytearray(W * 2)  # u16 per column


@micropython.viper
def render16(fb, pal, skyraw, heights, blocks, ybuf,
             w: int, h: int, mw: int, mh: int,
             px: int, py: int, pz: int, horizon: int,
             sinA: int, cosA: int, zfar: int, hscale: int):
    # px/py/pz/z are 16.16 fixed point; sinA/cosA are 2.14 fixed point
    screen_p = ptr16(fb)
    palette = ptr16(pal)
    skyp = ptr16(skyraw)
    hmap = ptr8(heights)
    bmap = ptr8(blocks)
    yb = ptr16(ybuf)

    skyv = skyp[0]
    total = w * h
    for i in range(total):
        screen_p[i] = skyv
    for x in range(w):
        yb[x] = h

    z = 1 << 16          # 16.16
    dz = 1 << 16
    fogstep = (zfar >> 2)
    while z < zfar:
        # endpoints of the view segment at distance z (16.16 world units)
        # forward = (cos, sin), right = (-sin, cos); sin/cos are 2.14 fixed
        zq = z >> 8
        fx_ = (cosA * zq) >> 6
        fy_ = (sinA * zq) >> 6
        plx = px + fx_ + fy_
        ply = py + fy_ - fx_
        prx = px + fx_ - fy_
        pry = py + fy_ + fx_
        dx = (prx - plx) // w
        dy = (pry - ply) // w
        fog = z // fogstep
        if fog > 3:
            fog = 3
        invz = (hscale << 16) // (z >> 8)   # hscale * 2^8 / Z
        for x in range(w):
            mx = plx >> 16
            my = ply >> 16
            plx += dx
            ply += dy
            if mx < 0 or my < 0 or mx >= mw or my >= mh:
                continue
            idx = my * mw + mx
            hh = int(hmap[idx]) << 16
            # screen row of this column's surface
            sy = horizon + ((((pz - hh) >> 12) * invz) >> 12)
            top = yb[x]
            if sy < 0:
                sy = 0
            if sy < top:
                c = palette[int(bmap[idx]) * 4 + fog]
                p = sy * w + x
                for yy in range(sy, top):
                    screen_p[p] = c
                    p += w
                yb[x] = sy
        z += dz
        dz += 1 << 10  # coarser steps further away


@micropython.viper
def render32(fb, pal, skyraw, heights, blocks, ybuf,
             w: int, h: int, mw: int, mh: int,
             px: int, py: int, pz: int, horizon: int,
             sinA: int, cosA: int, zfar: int, hscale: int):
    screen_p = ptr32(fb)
    palette = ptr32(pal)
    skyp = ptr32(skyraw)
    hmap = ptr8(heights)
    bmap = ptr8(blocks)
    yb = ptr16(ybuf)

    skyv = skyp[0]
    total = w * h
    for i in range(total):
        screen_p[i] = skyv
    for x in range(w):
        yb[x] = h

    z = 1 << 16
    dz = 1 << 16
    fogstep = (zfar >> 2)
    while z < zfar:
        # forward = (cos, sin), right = (-sin, cos); sin/cos are 2.14 fixed
        zq = z >> 8
        fx_ = (cosA * zq) >> 6
        fy_ = (sinA * zq) >> 6
        plx = px + fx_ + fy_
        ply = py + fy_ - fx_
        prx = px + fx_ - fy_
        pry = py + fy_ + fx_
        dx = (prx - plx) // w
        dy = (pry - ply) // w
        fog = z // fogstep
        if fog > 3:
            fog = 3
        invz = (hscale << 16) // (z >> 8)
        for x in range(w):
            mx = plx >> 16
            my = ply >> 16
            plx += dx
            ply += dy
            if mx < 0 or my < 0 or mx >= mw or my >= mh:
                continue
            idx = my * mw + mx
            hh = int(hmap[idx]) << 16
            sy = horizon + ((((pz - hh) >> 12) * invz) >> 12)
            top = yb[x]
            if sy < 0:
                sy = 0
            if sy < top:
                c = palette[int(bmap[idx]) * 4 + fog]
                p = sy * w + x
                for yy in range(sy, top):
                    screen_p[p] = c
                    p += w
                yb[x] = sy
        z += dz
        dz += 1 << 10


render = render16 if PIXEL_BYTES == 2 else render32


# ------------------------------------------------------------- map kernel

@micropython.viper
def topdown16(fb, pal, heights, blocks, w: int, h: int, mw: int, mh: int, ox: int, oy: int, zoom: int):
    screen_p = ptr16(fb)
    palette = ptr16(pal)
    hmap = ptr8(heights)
    bmap = ptr8(blocks)
    p = 0
    for y in range(h):
        my = oy + y // zoom
        for x in range(w):
            mx = ox + x // zoom
            if mx < 0 or my < 0 or mx >= mw or my >= mh:
                screen_p[p] = 0
            else:
                idx = my * mw + mx
                screen_p[p] = palette[int(bmap[idx]) * 4]
            p += 1


@micropython.viper
def topdown32(fb, pal, heights, blocks, w: int, h: int, mw: int, mh: int, ox: int, oy: int, zoom: int):
    screen_p = ptr32(fb)
    palette = ptr32(pal)
    hmap = ptr8(heights)
    bmap = ptr8(blocks)
    p = 0
    for y in range(h):
        my = oy + y // zoom
        for x in range(w):
            mx = ox + x // zoom
            if mx < 0 or my < 0 or mx >= mw or my >= mh:
                screen_p[p] = 0
            else:
                idx = my * mw + mx
                screen_p[p] = palette[int(bmap[idx]) * 4]
            p += 1


topdown = topdown16 if PIXEL_BYTES == 2 else topdown32


# ------------------------------------------------------------- state

mode_3d = True
tilt_steer = False
zoom = 1
view_far = 1          # 0 short, 1 medium, 2 long
ZFAR = (90, 160, 260) # map cells
cam_x = MW / 2.0
cam_y = MH * 0.75
cam_a = -math.pi / 2  # facing "north" (negative map y)
EYE = 6.0             # eye height above the surface, in height units
HSCALE = 90 if not USE_HIRES else 180

SPEED = 22.0          # cells per second
TURN = 1.6            # rad per second


def ground_at(x, y):
    xi = int(x)
    yi = int(y)
    if 0 <= xi < MW and 0 <= yi < MH:
        return HEIGHTS[yi * MW + xi]
    return 0


def draw_hud():
    screen.font = font.nope
    screen.pen = color.rgb(0, 0, 0, 120)
    screen.rectangle(0, H - 10, W, 10)
    screen.pen = color.rgb(255, 255, 255)
    if mode_3d:
        label = "3D %s%s  %dfps" % (
            ("tilt " if tilt_steer else ""), ("far", "mid", "near")[2 - view_far],
            1000 // max(1, badge.ticks_delta))
    else:
        label = "map x%d  %d,%d" % (zoom, int(cam_x), int(cam_y))
    screen.text(label, 3, H - 9)


def update():
    global mode_3d, tilt_steer, zoom, view_far, cam_x, cam_y, cam_a
    dt = badge.ticks_delta / 1000.0

    if badge.pressed(BUTTON_SELECT):
        mode_3d = not mode_3d
    if badge.pressed(BUTTON_MENU):
        tilt_steer = not tilt_steer
    if badge.pressed(BUTTON_BACK):
        if mode_3d:
            view_far = (view_far + 1) % 3
        else:
            zoom = 1 if zoom == 4 else zoom * 2

    if mode_3d:
        turn = 0.0
        if badge.held(BUTTON_LEFT):
            turn -= 1.0
        if badge.held(BUTTON_RIGHT):
            turn += 1.0
        if tilt_steer:
            ax, ay, az, gx, gy, gz = badge.imu()
            turn += max(-1.0, min(1.0, ax * 2.0))
        cam_a += turn * TURN * dt
        move = 0.0
        if badge.held(BUTTON_UP):
            move += 1.0
        if badge.held(BUTTON_DOWN):
            move -= 1.0
        if move:
            nx = cam_x + math.cos(cam_a) * move * SPEED * dt
            ny = cam_y + math.sin(cam_a) * move * SPEED * dt
            cam_x = max(0.0, min(MW - 1.0, nx))
            cam_y = max(0.0, min(MH - 1.0, ny))

        # eye height follows the surface (you walk on the buildings too)
        eye = ground_at(cam_x, cam_y) + EYE
        fx = int(cam_x * 65536)
        fy = int(cam_y * 65536)
        fz = int(eye * 65536)
        render(screen.raw, PAL_RAW, SKY_RAW, HEIGHTS, BLOCKS, YBUF,
               W, H, MW, MH, fx, fy, fz, H // 2,
               int(math.sin(cam_a) * 16384), int(math.cos(cam_a) * 16384),
               ZFAR[view_far] << 16, HSCALE)
    else:
        pan = SPEED * 2 * dt / zoom
        if badge.held(BUTTON_LEFT):
            cam_x -= pan
        if badge.held(BUTTON_RIGHT):
            cam_x += pan
        if badge.held(BUTTON_UP):
            cam_y -= pan
        if badge.held(BUTTON_DOWN):
            cam_y += pan
        cam_x = max(0.0, min(MW - 1.0, cam_x))
        cam_y = max(0.0, min(MH - 1.0, cam_y))
        ox = int(cam_x) - W // (2 * zoom)
        oy = int(cam_y) - H // (2 * zoom)
        topdown(screen.raw, PAL_RAW, HEIGHTS, BLOCKS, W, H, MW, MH, ox, oy, zoom)
        # you-are-here marker with heading
        cx, cy = W // 2, H // 2
        screen.pen = color.rgb(255, 60, 60)
        screen.circle(vec2(cx, cy), 2)
        screen.line(cx, cy, cx + int(math.cos(cam_a) * 7), cy + int(math.sin(cam_a) * 7))

    draw_hud()


run(update)
