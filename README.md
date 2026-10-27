# sf-badge-map

Minecraft San Francisco on the GitHub Universe 2026 badge.

[Arnis](https://github.com/louis-e/arnis) turns a bounding box of OpenStreetMap
data into a Minecraft Java world. This repo squashes that world into a
heightmap plus colour index and renders it on the badge's RP2350 with a
voxelspace (Comanche-style) renderer written in MicroPython viper.

## Pipeline

1. Generate the world with Arnis (desktop app or CLI). Fort Mason to Pier 39:

       arnis --bbox="37.800,-122.437,37.812,-122.408" --output-dir ./sf_world

   That's roughly 2.5 km × 1.3 km. Start smaller if you just want to try it.

2. Convert it for the badge (needs Python 3, numpy and Pillow are optional but
   make it faster and give you a preview PNG):

       python tools/arnis_to_badge.py ./sf_world -o out --scale 2

   `--scale 2` means one map cell per 2 × 2 blocks. The full bbox at scale 2 is
   about 850k cells, ~1.7 MB on flash and in RAM. Use `--scale 3` or `4` if the
   badge runs out of memory loading it.

3. Preview on your desktop using the exact kernels the badge runs:

       python tools/preview.py out/world.bin --x 600 --y 500 --angle -90 -o frame.png
       python tools/preview.py out/world.bin --map --zoom 1 -o map.png

4. Copy `out/world.bin` into `badge/apps/sfmap/` and install the app the usual
   way (USB mass storage → `/apps/sfmap`, or `mpremote` per the badger/home
   USB guide).

## Testing without Arnis

`tools/make_test_world.py /tmp/testworld` writes a tiny synthetic Anvil world
(water, shoreline, pier, roads, buildings) so you can run the converter and
previews end to end.

## How it renders

- Converter reads 1.18+ Anvil region files, takes the top non-air block of
  every column, downsamples and builds a ≤48 colour Minecraft-map-ish palette.
- `world.bin` is header + palette + height bytes + block bytes.
- On the badge, each palette colour is drawn into a 1-px image and read back
  as a raw pixel value, so the viper kernel can write native pixels straight
  into `screen.raw` (works with both 16- and 32-bit pixel formats).
- 3D: for each distance step, walk the left→right view segment, project each
  cell's height to a screen row and fill the span above the previous row
  (per-column y-buffer). Four fog levels per colour. Runs at the default
  160×120; flip `USE_HIRES` for 320×240 at ~¼ the frame rate.
- Map: straight blit of the colour index with integer zoom.

## Ideas / next steps

- Keep a second shaded palette for the map view so hills and buildings show.
- Isometric cube view from the same data.
- Native C firmware (pico-sdk) with real 3D voxels, Doom-badge style, if the
  viper version isn't fast enough for you.

## Windows quick start

    .\setup-windows.ps1            # installs Python deps + Arnis, generates the SF world, converts, previews
    .\setup-windows.ps1 -Deploy    # same, then copies to the BADGER drive if plugged in

See the script header for -Bbox / -Scale / -SkipGenerate.
