---
name: tomo5-data
description: Read and process Thermo Fisher Tomography 5 (Tomo5) session data — image XMLs, MRC files with FEI extended headers, .mdoc files, BatchPositionsList.xml, search-map tiles and star ratings — and stitch search maps or map positions between images. Use for any script or analysis on a Tomo5 session folder (Atlas/, SearchMaps/, Batch/, Position_N.mrc tilt series).
---

# Tomo5 session data

Use `scripts/tomo5_io.py` (Python 3.7+, numpy for images) rather than re-deriving the format: it
carries the rules that were checked against real Tomo5 5.26 data and matches the Tomo5 Data Viewer
to within 0.5 nm. The full file layout and the geometry derivation are in
[tomo5_data_struct.md](../../../tomo5_data_struct.md); read it when a task needs a file or field
not covered here.

## Session layout (short)

```
Session/                      Session.dm, Position_N[_k].mrc + .mdoc (tilt series, may be elsewhere)
├── Atlas/Atlas_<date>.mrc|xml          stitched atlas (one image)
├── SearchMaps/SearchMap_<N>_<date>/    Overview.mrc|xml, Tile_*.mrc|xml, SearchMap.mrc|xml|dm
└── Batch/                              BatchPositionsList.xml, Position_N_Search.mrc|xml,
                                        Position_N[_k]_Exposure.mrc, Position_N_Tracking.mrc
```

`Position_N` is a batch position; its exposure areas are N.1 (= `Position_N`), N.2
(= `Position_N_2`), … Only area 1 has Search / Tracking / XML files.

## Quick start

```python
import sys; sys.path.insert(0, ".claude/skills/tomo5-data/scripts")
import tomo5_io as t5

maps = t5.search_maps(session)                     # id, name, tiles, rating (0–5), stage_um, geo (footprint)
pos = t5.batch_positions(session, tilt_dir)        # status, stage_um, areas[k]: stage_um, tilt_series, mdoc
secs, src = t5.tilt_sections(mrc, mdoc)            # angle / time / dose of every stack section
geo = t5.image_geo("Overview.xml", "Overview.mrc") # geo.pixel_to_stage(x, y), geo.stage_to_pixel(sx, sy)
img = t5.read_section(mrc, z=0, bin=4)             # float32, FEI extended header skipped
img, geo, rep = t5.stitch_search_map(map_dir, bin=4)
ratings = t5.tilt_ratings(tilt_dir)                # {"Position_1_2.mrc": 4} (Movie Player stars)
```

Command line (`python3 scripts/tomo5_io.py <cmd> --help`): `info FILE.xml|FILE.mrc`,
`maps SESSION`, `positions SESSION [--tilt DIR] [--csv out.csv]`, `tilts MRC [MDOC]`,
`stitch MAP_DIR out.mrc [--bin 4] [--no-align]`.

## Recipes

- **Table of acquired areas with rating and defocus**: `python3 tomo5_io.py positions SESSION
  --tilt TILTDIR --csv areas.csv` (one row per area: stage, status, defocus, tilt series, stars).
- **Put something from one image onto another** (e.g. a position on the atlas): stage →
  `geo.stage_to_pixel(...)` of the target image. Positions are already in stage µm.
- **Mark search maps on the atlas**: `m["geo"].corners()` (from `SearchMap.xml` + `SearchMap.mrc`)
  → `atlas_geo.stage_to_pixel` for each corner; maps without `SearchMap.mrc` only have `stage_um`.
- **Stitch a search map**: `stitch_search_map` (grid from `SearchMap.dm`, overlaps matched at
  full size, least squares, gains, feathered blend); `rep["matched"]/rep["overlaps"]` tells how
  many seams aligned. Tomo5's own result is `SearchMap.mrc` in the same folder.
- **Pick tilt series to reconstruct**: `tilt_ratings()` ≥ 4, or `rating` of the search map.

## Pitfalls (each one has bitten)

1. **Matrix sign.** `ReferenceTransformation/matrix` has the *opposite* sign of pixel → stage as
   Tomo5 draws it. Use only its direction: `R = −M / sqrt(|det M|)` (`image_info` does this), then
   `stage = S + ps · R · (pixel − centre)`. Without the minus, maps land off the grid on the atlas
   and tiles are swapped.
2. **Pixel size** (`SpatialScale/pixelSize/x`) is per pixel of the *saved* image, also for
   stitched images; the matrix scale is per unbinned camera pixel — never take size from it.
3. **CustomData** in image XMLs holds decoy copies (e.g. a BackwardAlignment matrix); read fields
   outside `CustomData/` first.
4. **MRC extended header**: Tomo5 MRCs carry a ~909 kB FEI header (`nxt` at byte 92); data
   start at `1024 + nxt`. Per-section 888-byte blocks: alpha tilt (degrees) at byte 100, pixel size
   (m) at byte 156.
5. **Tilt series order**: the stack is sorted by angle (section 0 = −60°) but the `.mdoc` is in
   acquisition order (`[ZValue = 0]` = 0°, `[ZValue = 1]` = −3°, …). `ZValue` is **not** the
   section. Use `tilt_sections()`: FEI angles first, else the `.mdoc` sorted by angle.
6. **Areas**: an area's stage position is `StagePosition − (PositionX, PositionY)` (metres in the
   XML; the script returns µm). Status comes from `BatchPositionStatus` when present.
7. **Tile positions**: the tile XML stage positions are ~5 % short and ~1° rotated; start from
   `AtlasPixelPosition` in `SearchMap.dm` (`tile_grid`). Overlaps are only 50–70 px: match at full
   size, not binned.
8. **SearchMap.dm / .dm files are XML** (Key/Value pairs); `StarRating` is the map's 0–5 rating.

## Checking work

Run `python3 tests/test_tomo5_io.py` after changing the script. To check a result by eye, open the
session in the viewer (`python3 tomo5_viewer.py SESSION_PARENT`) — it uses the same rules.
