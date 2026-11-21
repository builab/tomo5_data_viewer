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
│   ├── Position_1_2_Exposure.mrc      # Extra position: exposure only
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

## Thumbnails/

| File | Description |
|---|---|
| `Position_<N>.mrc` | Thumbnail for each position |

## Metadata used by the viewer (Tomo5 5.26)

Image XML files (`Atlas_*.xml`, `Overview.xml`, `Tile_*.xml`, `SearchMap.xml`) are
Thermo Fisher `MicroscopeImage` documents, like EPU's.

| Field | Meaning |
|---|---|
| `microscopeData/stage/Position/X, Y, Z` | Stage position of the image centre, in metres. For tiles, it includes the image shift used to reach the tile. |
| `microscopeData/stage/Position/A` | Tilt angle, in radians |
| `SpatialScale/pixelSize/x/numericValue` | Pixel size of the image, in metres |
| `ReferenceTransformation/matrix/_m11 … _m22` | Image pixels (x right, y down) ↔ stage (rotation and flip), per unbinned camera pixel. As Tomo5 displays it, `stage = S − M · (pixel − centre)`: note the minus sign. |
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

Atlas (`Atlas/`):

- `Atlas_<date>_<time>.xml`: the stitched atlas. Stage X/Y = 0, 0 (centre of the grid),
  pixel size ≈ 505 nm at 110×. `CustomData` has `NumberOfTilesPlanned` / `NumberOfTilesAcquired`
  (e.g. 25 = 5 × 5 tiles). Written by `TemAppCommon`.
- `AtlasImportParameters.xml`: `ImageFileFormat` (`Mrc`) and `Name` (the atlas file stem).

Search map folder:

- `SearchMap.xml`: the stitched search map: stage X/Y of its centre, pixel size, direction;
  `CustomData` has `NumberOfTilesPlanned` / `NumberOfTilesAcquired` and `AppliedDefocus`.

- `TileSetAcquisitionOptions.xml`: tile grid size (`GridSize/width`, `GridSize/height`), e.g. 3 × 3.
- `SearchMap.dm`: the tile set (`TileSetXml`), including `AcquiredWithImageBeamShift` and the
  user's rating, `StarRating` (0 = not rated, 1–5 stars).
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
- The camera field and beam size are not in this list. The exposure pixel size and image size are in
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
