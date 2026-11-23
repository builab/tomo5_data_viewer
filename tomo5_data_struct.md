# Tomo5 Data Structure

Layout of a Thermo Scientific Tomography 5 (Tomo5) session folder, as used by the viewer.
The organisation is similar to EPU: an atlas, search maps made of tiles, and batch positions
that each produce a tilt series.

Example session (atlas acquired 2026-09-21, search maps 2026-09-24).

## Overview

```text
<Session>/
├── Session.dm                         # Session metadata
├── Position_1.mrc                     # Tilt series stack, position 1
├── Position_1.mdoc                    # Tilt series metadata, position 1
├── Position_1_2.mrc                   # Tilt series stack, position 1_2
├── Position_1_2.mdoc
├── Position_2.mrc                     # Tilt series stack, position 2
├── Position_2.mdoc
│
├── Atlas/
│   ├── Atlas_20260921_201329.jpg      # Atlas preview
│   ├── Atlas_20260921_201329.mrc      # Atlas image
│   ├── Atlas_20260921_201329.xml      # Atlas metadata
│   └── AtlasImportParameters.xml      # Import parameters
│
├── Batch/
│   ├── BatchPositionList.xml          # List of all batch positions
│   ├── Position_1_Exposure.mrc
│   ├── Position_1_Search.mrc
│   ├── Position_1_Search.xml
│   ├── Position_1_Tracking.mrc
│   ├── Position_1_2_Exposure.mrc      # Offaxis position: exposure only
│   ├── Position_2_Exposure.mrc
│   ├── Position_2_Search.mrc
│   ├── Position_2_Search.xml
│   └── Position_2_Tracking.mrc
│
├── DiscoveryViewers/
│   ├── Atlas_20260921_201329.mrc
│   ├── Position_1_Search.mrc
│   └── Position_2_Search.mrc
│
├── SearchMaps/
│   ├── QueuedSearchMapList.xml        # Queue of search maps
│   ├── SearchMap_1_20260924_093255/   # One folder per search map (see below)
│   └── SearchMap_2_20260924_093505/
│
└── Thumbnails/
    ├── Position_1.mrc
    ├── Position_1_2.mrc
    └── Position_2.mrc
```

## Session root

| File | Description |
|---|---|
| `Session.dm` | Session metadata |
| `Position_<N>.mrc` | Tilt series stack for a batch position |
| `Position_<N>.mdoc` | Tilt series metadata (SerialEM-style mdoc) |

**Section order.** The stack `Position_<N>.mrc` is sorted by tilt angle (section 0 = lowest angle,
e.g. −60°), while the `.mdoc` lists the images in acquisition order (dose-symmetric: `[ZValue = 0]`
0°, `[ZValue = 1]` −3°, `[ZValue = 2]` +3°, …). So `ZValue` is the acquisition index, not the section
number. Each section's own stage tilt is in the MRC's FEI extended header (one 888-byte block per
section, metadata version 2: alpha tilt in degrees at byte 100, pixel size in metres at byte 156);
match it to the `.mdoc` block with the same `TiltAngle` to get that image's dose and time.

Several positions can share one search image. The first, the anchoring position, is `Position_<N>`, and the
following ones get a sub-index: `Position_<N>_2`, `Position_<N>_3`, …
In this example, `Position_1` and `Position_1_2` share the search image of position 1.

## Atlas/

| File | Description |
|---|---|
| `Atlas_<YYYYMMDD>_<HHMMSS>.mrc` | Atlas image |
| `Atlas_<YYYYMMDD>_<HHMMSS>.xml` | Atlas metadata |
| `Atlas_<YYYYMMDD>_<HHMMSS>.jpg` | Atlas preview image |
| `AtlasImportParameters.xml` | Parameters used to import the atlas |

## Batch/

One set of files per batch position.

| File | Description |
|---|---|
| `BatchPositionList.xml` | List of all batch positions |
| `Position_<N>_Search.mrc` | Search image at the position |
| `Position_<N>_Search.xml` | Search image metadata |
| `Position_<N>_Exposure.mrc` | Exposure image at the position |
| `Position_<N>_Tracking.mrc` | Tracking image at the position |

Only the anchoring position (`Position_<N>`) has the full set of files. Extra positions
on the same search image (`Position_<N>_2`, …) have only `Position_<N>_<M>_Exposure.mrc`;
they use the anchoring position's search, search metadata and tracking files.

## DiscoveryViewers/

Copies of the images shown in the Discovery viewers.

| File | Description |
|---|---|
| `Atlas_<YYYYMMDD>_<HHMMSS>.mrc` | Atlas |
| `Position_<N>_Search.mrc` | Search image for each position |

## SearchMaps/

| File | Description |
|---|---|
| `QueuedSearchMapList.xml` | Queue of search maps |
| `SearchMap_<N>_<YYYYMMDD>_<HHMMSS>/` | One folder per search map |

### Contents of a search map folder

```text
SearchMap_1_20260924_093255/
├── SearchMap.dm                       # Search map metadata
├── SearchMap.mrc                      # Search map image
├── SearchMap.xml
├── SearchMap.jpg
├── Overview.mrc                       # Overview image
├── Overview.xml
├── Overview.jpg
├── TileSetAcquisitionOptions.xml      # Tile set acquisition options
├── Tile_24587770_1.mrc                # Tile 1
├── Tile_24587770_1.xml
├── Tile_24587770_1.jpg
├── Tile_24587775_2.{mrc,xml,jpg}      # Tile 2
├── Tile_24587779_3.{mrc,xml,jpg}
├── Tile_24587784_4.{mrc,xml,jpg}
├── Tile_24587789_5.{mrc,xml,jpg}
├── Tile_24587794_6.{mrc,xml,jpg}
├── Tile_24587798_7.{mrc,xml,jpg}
├── Tile_24587803_8.{mrc,xml,jpg}
└── Tile_24587808_9.{mrc,xml,jpg}      # Tile 9
```

Tiles are named `Tile_<ID>_<index>`, where `<ID>` is a numeric identifier and `<index>`
runs from 1 to the number of tiles (9 in this example, i.e. a 3 × 3 montage).
Each tile has an `.mrc` image, an `.xml` metadata file and a `.jpg` preview.

`SearchMap_2_20260924_093505/` has the same layout, with tiles `Tile_24587909_1`
to `Tile_24587947_9`.

## tiltseries_movie/ (made by `tomo5_viewer.py --make-previews`)

Not part of Tomo5. Previews for the viewer's Movie Player, in the tilt-series folder (or the
session folder):

| File | Description |
|---|---|
| `Position_<N>[_<k>].mrc` | the tilt series binned to ≤ 512 px, 8-bit MRC (mode 0), sections in the original order |
| `Position_<N>[_<k>].json` | `angles`, `times` per section (`anglesFrom`: FEI header or `.mdoc`), `pixelA`, `bin`, `shape`, `sourceShape`, `sourceSize`, `sourceMtime`, `range` |
| `Position_<N>[_<k>].mp4` | optional (`--mp4`): the series by angle, low-passed |
| `tiltseries_ratings.xml` | star ratings (1–5) set in the viewer's Movie Player: `<TiltSeries name="Position_1_2.mrc" position="1" area="2" rating="4" updated="…" />` per rated tilt series |

## Thumbnails/

| File | Description |
|---|---|
| `Position_<N>.mrc` | Thumbnail for each position |

## Batch/Edit/ and SearchMaps/Edit/ (written by the viewer's experimental edit mode)

Not part of Tomo5. The viewer saves edits here so Tomo5's files are never overwritten.
Batch-position edits go to `Batch/Edit/`, search-map star ratings to `SearchMaps/Edit/`.

| File | Description |
|---|---|
| `Batch/Edit/BatchPositionsList.xml` | Copy of `Batch/BatchPositionsList.xml`; only `AdditionalExposureTemplateAreas` of edited positions change |
| `Batch/Edit/edit_session.json` | Manifest: tool, version, save time, source list, edited positions, sub-position offsets (µm), ratings, folders |
| `SearchMaps/Edit/<map>/SearchMap.dm` | Copy of the map's `SearchMap.dm`; only `<StarRating>` (0–5) changes |
| `…/Edit/history/<YYYYmmdd_HHMMSS>/` | The previous save, moved here before each new save (in both folders) |

A sub-position as Tomo5 writes it (offset from the anchoring position, in metres):

```xml
<ExposureTemplateAreaParameters>
  <Comment />
  <Defocus>-5</Defocus>
  <Name>Position_1_2</Name>
  <PositionX>2.0356256413833762E-07</PositionX>
  <PositionY>-2.9610159799631219E-06</PositionY>
</ExposureTemplateAreaParameters>
```

The area is at `StagePosition − (PositionX, PositionY)` of `Position_1`.

## Metadata used by the viewer (Tomo5 5.26)

Image XML files (`Atlas_*.xml`, `Overview.xml`, `Tile_*.xml`, `SearchMap.xml`) are
Thermo Fisher `MicroscopeImage` documents, like EPU's.

| Field | Meaning |
|---|---|
| `microscopeData/stage/Position/X, Y, Z` | Stage position of the image centre, in metres. For tiles, it includes the image shift used to reach the tile. |
| `microscopeData/stage/Position/A` | Tilt angle, in radians |
| `SpatialScale/pixelSize/x/numericValue` | Pixel size of the image, in metres |
| `ReferenceTransformation/matrix/_m11 … _m22` | Image pixels (x right, y down) ↔ stage (rotation and flip), often per unbinned camera pixel. See [Coordinate transforms](#coordinate-transforms-image-pixels--stage): `stage = S − ps · M̂ · (pixel − centre)`, note the minus sign. |
| `microscopeData/optics/TemMagnification/NominalMagnification` | Magnification |
| `microscopeData/acquisition/camera/ExposureTime` | Exposure time, in seconds |
| `CustomData/…/AppliedDefocus` | Applied defocus, in metres |
| `microscopeData/acquisition/acquisitionDateTime` | Acquisition time |

Notes:

- The pixel size is per pixel of the saved image. For stitched images (`Atlas_*.mrc`,
  `SearchMap.mrc`) it is not tied to the camera `ReadoutArea`: e.g. `SearchMap.mrc` is
  4005 × 4005 px at 8.65 nm (34.64 µm) while its XML lists a 2048 × 2048 readout.
- The `ReferenceTransformation` scale can be per unbinned pixel (tiles, `SearchMap.xml`:
  half the pixel size at binning 2), so only its direction should be used.

## Coordinate transforms: image pixels ↔ stage

Every Tomo5 image (atlas, overview, tiles, `SearchMap.mrc`, Search images) can be placed in one
common frame, the **stage frame** (stage X/Y, in metres or µm), from three fields of its XML.
This is what puts search maps on the atlas, tiles on the overview, tiles into a stitched map, and
batch positions on all of them.

### The formula

For a pixel `p = (x, y)` of an image (x to the right, y down, in the row order stored in the MRC;
the first stored row is the top of the image as Tomo5 shows it):

```text
stage(p) = S − ps · M̂ · (p − c)
```

| Symbol | Meaning | Source |
|---|---|---|
| `S` | Stage X/Y of the image centre | `microscopeData/stage/Position/X, Y` |
| `ps` | Pixel size of the saved image | `SpatialScale/pixelSize/x/numericValue` |
| `M` | 2 × 2 matrix `[[_m11, _m12], [_m21, _m22]]` | `ReferenceTransformation/matrix` |
| `M̂` | `M` normalised: `M / sqrt(|det M|)`, a rotation plus a flip | |
| `c` | Image centre, `(nx / 2, ny / 2)` | MRC header |

Inverse, to draw a stage position on an image: `p = c − M̂ᵀ · (stage − S) / ps`. Because `M̂` is
orthonormal, its inverse is its transpose.

### Three things that matter

1. **The minus sign.** `ReferenceTransformation` has the opposite sign of pixel → stage as Tomo5
   displays the images. With `+M`, positions come out point-reflected through the image centre:
   search maps landed off the grid on the atlas, and stitched tiles were swapped across the
   centre tile. With `−M`, everything matches Tomo5. Each image's own orientation is the same either
   way, which is why the sign is easy to miss.
2. **Use only the direction of `M`.** Its scale is not always the saved pixel size: for tiles and
   `SearchMap.xml` it is per *unbinned* camera pixel (2.91 nm, while the tiles are saved at 5.83 nm,
   binning 2). Normalise it, and take the size from `pixelSize`.
3. **`pixelSize` is per saved pixel**, also for stitched images: `SearchMap.mrc` is 4005 px at
   8.65 nm = 34.64 µm, even though its XML lists a 2048 px readout. Do not rescale it by the readout.

### Examples from a Glacios session (Tomo5 5.26)

| Image | `M` (`_m11`, `_m12`, `_m21`, `_m22`) | `|M|` | `pixelSize` | `M̂` ≈ |
|---|---|---|---|---|
| `Atlas_*.xml` (110×) | 4.32e-8, −5.04e-7, −5.04e-7, −4.32e-8 | 5.05e-7 | 5.05e-7 m | `[[0.085, −0.996], [−0.996, −0.085]]` |
| `Overview.xml` (540×) | 1.58e-9, −2.27e-8, −2.27e-8, −1.58e-9 | 2.27e-8 | 2.27e-8 m | `[[0.070, −0.998], [−0.998, −0.070]]` |
| `Tile_*.xml`, `Position_*_Search.xml` (4100×, bin 2) | 3.66e-11, −2.91e-9, −2.91e-9, −3.66e-11 | 2.91e-9 | 5.83e-9 m | `[[0.013, −1.000], [−1.000, −0.013]]` |
| `SearchMap.xml` | 5.44e-11, −4.32e-9, −4.32e-9, −5.44e-11 | 4.32e-9 | 8.65e-9 m | `[[0.013, −1.000], [−1.000, −0.013]]` |

All four are reflections of the form `[[a, b], [b, −a]]`: image x runs roughly along −stage Y and
image y along −stage X, turned by a few degrees that depend on the magnification (about 3.3°
between the overview and the tiles, which is why Tomo5 draws the tile outlines slightly rotated on
the overview).

### How the viewer uses it

- **Display orientation.** The view is turned by `−M̂ᵀ` of the main image (atlas, overview, first
  tile, Search image), so that image appears unrotated and unmirrored, as in Tomo5. Everything else
  is placed in stage coordinates and goes through the same view transform.
- **Search maps on the atlas.** The footprint of `SearchMap.mrc` (its size × pixel size, centred on
  `SearchMap.xml`'s stage position, turned by its `M̂`) drawn on the atlas. Example: SearchMap_1
  at stage (−506.6, 379.2) µm lands on a grid square, as in Tomo5.
- **Tiles on the overview, and stitching.** Each `Tile_*.xml` has its own stage position (Tomo5
  folds the image shift into it) and its own `M̂`. Placing the tiles with the formula reproduces the
  outlines on Tomo5's Overview and the arrangement of Tomo5's `SearchMap.mrc`. All tiles of a map
  share one `M̂`, so within the map they differ only by a translation.
- **Batch positions.** `StagePositionX, Y` of each position (area N.1) is already in the stage
  frame. The other areas, tracking and focus sit at `StagePosition − (PositionX, PositionY)`, the
  same reversed sign as the matrix.
- **Cursor readout.** The stage X/Y under the cursor is `S + (view position turned back)`; it
  matches the stage values in the XML files.

### Tile positions: XML vs reality

The tiles of a search map are taken with image shift, and the stage positions in `Tile_*.xml` come from
the image-shift calibration. Measured on real tiles (3 × 3 map, 2048 px at 5.83 nm), the true offsets
(cross-correlation of the overlaps) are:

| Offset of tile 2 / tile 4 from tile 1 | Tile 2 | Tile 4 |
|---|---|---|
| From the tile XML stage positions | (−23, 1903) px | (1906, 49) px |
| From `SearchMap.dm` `AtlasPixelPosition` (Tomo5's stitching) | (0, 1945) px | (1945, 0) px |
| **True (overlap cross-correlation)** | **(−2, 1998) px** | **(1981, 3) px** |

So the tiles lie on a camera-axis grid with only ~50–70 px of overlap. The XML positions are ~5 %
short and rotated by ~1° (the `ReferenceCorrectionForImageShift` in `SearchMap.xml` is of that
order: 2.7° rotation, 1 % scale), and Tomo5's own grid is ~2–3 % short, which leaves the faint seams
in Tomo5's `SearchMap.mrc`. The viewer starts from `AtlasPixelPosition` and refines every overlap by
cross-correlation, which joins real tiles to within a pixel. The tile XML positions are still used
for the tile outlines on the Overview (they match Tomo5's drawing there).

### Checks used

- Tile outlines drawn on the real `Overview.mrc` match Tomo5's Overview (position, size, ~3°
  rotation).
- Tiles stitched with the formula match Tomo5's `SearchMap.mrc` (ring on the right, beam edge
  bottom-left).
- Search maps and positions land on the same grid squares as in Tomo5's Atlas view.
- Exposure areas, tracking and focus match Tomo5's Search and Search Map views.

Atlas (`Atlas/`):

- `Atlas_<date>_<time>.xml`: the stitched atlas. Stage X/Y = 0, 0 (centre of the grid),
  pixel size ≈ 505 nm at 110×. `CustomData` has `NumberOfTilesPlanned` / `NumberOfTilesAcquired`
  (e.g. 25 = 5 × 5 tiles). Written by `TemAppCommon`.
- `AtlasImportParameters.xml`: `ImageFileFormat` (`Mrc`) and `Name` (the atlas file stem).

Search map folder:

- `SearchMap.xml`: the stitched search map: stage X/Y of its centre, pixel size, direction;
  `CustomData` has `NumberOfTilesPlanned` / `NumberOfTilesAcquired` and `AppliedDefocus`.

- `TileSetAcquisitionOptions.xml`: tile grid size (`GridSize/width`, `GridSize/height`), e.g. 3 × 3.
- `SearchMap.dm`: the tile set (`TileSetXml`), including `AcquiredWithImageBeamShift`, the user's
  rating `StarRating` (0 = not rated, 1–5 stars), `ImageSize` of the stitched `SearchMap.mrc`
  (e.g. 4005 × 4005), and for every tile (`Tiles/_items/TileSetTileXml[n]`) its `Id`,
  `TileImageReference/BaseFileName` (`Tile_<Id>_<n>`), `StagePosition` and **`AtlasPixelPosition`**:
  x, y, width, height of the tile in the stitched `SearchMap.mrc` (e.g. 1379 px wide for a 2048 px tile
  at 5.83 → 8.65 nm). This grid is axis-aligned in camera pixels.
- Tiles are numbered from the centre outward in a spiral: `Tile_<ID>_1` is the centre tile, at
  the same position as the overview.
- `Overview.mrc` has a FEI extended header (`FEI2`, about 0.9 MB) before the image data.
- `SearchMaps/QueuedSearchMapList.xml` lists queued search maps (`QueuedTileSets`).

Batch positions (`Batch/`):

- `Position_<N>` is one position (the anchoring position, area N.1). Its other exposure areas are
  `Position_<N>_2`, `Position_<N>_3`, …
- `BatchPositionsList.xml` (note the *s*) lists every position as `BatchPositionParameters`:

| Field | Meaning |
|---|---|
| `Name` | `Position_<N>` |
| `BatchPositionStatus` | `Initialized`, `Acquired`, `Failed`, … |
| `StagePositionX, Y, Z, A` | Stage position of area N.1, in metres (A in radians) |
| `TiltAxisAngleInDegrees` | Tilt-axis angle |
| `Defocus`, `Comment`, `PercentageCompleted` | Per position |
| `ExposureTemplateAreaParameters/PositionX, Y` | Area N.1 offset (0, 0) |
| `AdditionalExposureTemplateAreas/ExposureTemplateAreaParameters` | Areas N.2, N.3, …: `Name` (`Position_<N>_<k>`), `PositionX, Y`, `Defocus` |
| `TrackingTemplateAreaParameters/PositionX, Y` | Tracking area offset |
| `FocusTemplateAreaParameters/PositionX, Y` | Focus area offset |
| `OverviewImageName` | Sometimes: the search-map overview the position was picked on |

  Offsets are in metres, and an area sits at **`StagePosition − offset`** (checked against Tomo5's
  Search and Search Map views).
- The camera field and beam size are not in this list, nor in `Session.dm`. The exposure pixel size and image size are in
  the tilt-series `.mdoc` (`PixelSpacing`, `ImageSize`) and in the FEI extended header of the MRC
  files (FEI2: pixel size at byte 156, illuminated area at 313, readout area at 451–466); the
  illuminated area is 0 on a Glacios.
- Status from the files present (when the list is missing): **Initialized** = only
  `Position_<N>_Search.mrc/.xml`; **Refined** = also `Position_<N>_Exposure.mrc`,
  `Position_<N>_Tracking.mrc` and `Position_<N>_<k>_Exposure.mrc`; **Acquired** = tilt series
  `Position_<N>[_<k>].mrc` + `.mdoc` in the session folder.
- `Position_<N>_Search.xml`: the Search image (e.g. 2048 px at 5.83 nm, 4100×), centred on area N.1.
  Its `CustomData` holds a copy of other settings (`BackwardAlignmentTransformation`, magnification 1),
  so the image's own `microscopeData` fields must be read first.

Session file:

- `Session.dm` (`TomographySession`): `Name` (session name), `DatasetName`, `Mode` (e.g. `Eftem`),
  `LowDose`, `SpecimenType` (e.g. `Slab`), `Batch`, `AtlasId` (path of the atlas `.dm` on the
  microscope PC), `DoseFractionsFormat`. No presets, so no beam size.
- The tracking area always lies along the tilt axis from area N.1.

