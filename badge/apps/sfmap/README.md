# SF Map

Walk around a Minecraft-style world of the San Francisco waterfront, generated from OpenStreetMap data, right on the badge.

## Usage

Copy a `world.bin` into this folder (see `tools/arnis_to_badge.py` in the repo
root), then launch the app from the menu.

- **Up / Down** walk forward / back
- **Left / Right** turn (3D) or pan (map)
- **Select** switch between first-person 3D and the top-down map
- **Menu** toggle tilt steering with the accelerometer (3D)
- **Back** cycle view distance (3D) or zoom level (map)
- **Home** return to the launcher

No Wi-Fi or `secrets.py` needed. The world file lives next to the app, so the
only RAM used is the map itself (2 bytes per cell).
