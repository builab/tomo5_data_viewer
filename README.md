# tomo5_data_viewer

A web-based interface to view Thermo Scientific Tomography 5 (Tomo5) data, in the spirit of the
[SerialEM Grid Viewer](https://github.com/builab/serialEM_grid_viewer).

It is one HTML page (`index.html`) plus a small Python server (`tomo5_viewer.py`) that serves your
session folders to the browser, read-only.

**Status: v0.5, atlas, search maps, batch positions and their exposures. Tested with Tomo5 5.26.**
The session layout is described in [tomo5_data_struct.md](tomo5_data_struct.md).

## Quick start

On the computer that has the data:

```bash
git clone https://github.com/builab/tomo5_data_viewer.git
cd tomo5_data_viewer
python3 tomo5_viewer.py /path/to/Tomo5/sessions --port 8766
```

Then open **http://localhost:8766** in Chrome, Edge or Firefox, click **Open session…** and pick
a session folder (the one that contains `Atlas/`, `Batch/` and `SearchMaps/`).

If the data is on a remote workstation, see
[Running on a remote workstation](#running-on-a-remote-workstation-port-forwarding).

Without the server, you can also open `index.html` directly in the browser and use
**Open folder from this computer…**. Only the files being shown are read.

## Interface

The layout follows Tomo5:

- **Left, Tasks:** *Search Maps* and *Batch Positions* (acquired / total) switch the table on
  the right. *Data Acquisition* and *Movie Player* are placeholders for now.
- **Centre, viewer:** tabs **Atlas › Overview › Search Map › Search › Exposure** (*Search* and *Exposure* with Batch Positions selected).
  The bar above the image shows the selected search map, with ‹ › to step through them.
  Below the image: **−** / **+** and a zoom slider. Scroll to zoom, drag to pan, double-click
  or **F** to fit. A scale bar is drawn on the image, and the stage X/Y under the cursor is shown
  at the bottom right.
- **Right:** the search-map table (Id, tilt, status, name, rating) or the batch-position table
  (Id, tilt, status, name, exposure areas acquired / planned). Click a row, or use the arrow keys,
  to select. The search-map rating (`StarRating` in `SearchMap.dm`) is shown as 5 stars, yellow for
  the rating, also in the bar above the image. Below it, the **Histogram** (Auto Filter, black/white levels, invert) and
  **Image Information** (size, pixel size, field of view, defocus, dose, exposure, magnification,
  stage, tilt, camera, date, min/mean/max). *All XML fields* lists every field of the XML,
  with a filter.

### Tabs (with Search Maps selected)

| Tab | Shows |
|---|---|
| Atlas | `Atlas/Atlas_<date>_<time>.mrc`, the stitched atlas, with every search map marked: green = acquired, orange = in progress / queued, white outline = selected. Click a marker to select that map; double-click to open its overview. |
| Overview | `SearchMaps/SearchMap_<N>_<date>_<time>/Overview.mrc`, with the outline of every `Tile_*` drawn in green |
| Search Map | The `Tile_*.mrc` images stitched in the browser (see below). The drop-down switches to the tiles at their stage positions without blending, or to `SearchMap.mrc`, the map as stitched by Tomo5. |

### Loading speed

Like the SerialEM Grid Viewer, the viewer shows something small first and loads detail only
when it is needed:

- Every image appears first as a **preview** of about 1024 px (stitched tiles: 384 px per tile,
  blended at their stage positions), usually in well under a second.
- The atlas, the overview and `SearchMap.mrc` load **more resolution only when you zoom in**
  far enough to see the difference, and only as much as that zoom needs (2×, then full size;
  at most about 4096 px for the atlas and `SearchMap.mrc`, 2048 px for the overview). On a
  high-DPI screen this happens straight away.
- The stitched tiles load at full display size right after the preview, because the overlap
  alignment needs them.
- While you look at one view, the **previews of the selected search map** (overview and tiles)
  load in the background, so the next tab opens at once.
- With numpy, the server bins images and sends **16-bit data** (half the bytes of float32).
  Without numpy, the browser reads the full files and bins them itself, which is slower.

Measured on a 5 MB/s link (a slow VPN): the atlas appears in about 1.5 s (2 MB) instead of
about 10 s (67 MB); the overview opens at once.

### Seamless stitching

*Tiles, stitched here (aligned, blended)* works like the SerialEM Grid Viewer's montages:

1. **Align.** The overlap of each pair of neighbouring tiles is cross-correlated (after a
   high-pass filter, so ice gradients do not dominate), coarse at half size and then refined.
   Least-squares tile positions are solved from all overlaps; a match that disagrees with the
   others is dropped, and a failed match falls back to the stage positions.
2. **Match brightness.** Each tile gets a gain so that tiles agree in their overlaps
   (an offset instead, if the data are not positive counts).
3. **Blend.** The tiles are merged into one image as a weighted average. Each tile fades out over
   the overlap width, like IMOD's `blendmont`.

The Image Information panel reports how many overlaps matched, the largest position correction,
the range of tile gains and the fade width. On a 3 × 3 map of 2048 px tiles this takes about
2 seconds in the browser.

### Batch positions

Read from `Batch/BatchPositionsList.xml` (Tomo5's list of positions) and the files in `Batch/` and
the session folder. `Position_<N>` is one position; its exposure areas are N.1, N.2, … The status
is Tomo5's own (`BatchPositionStatus`: Initialized, Acquired, Failed, …); without the list it comes
from the files present:

| Status | Files |
|---|---|
| Initialized | `Position_<N>_Search.mrc`, `Position_<N>_Search.xml` only |
| Refined | also `Position_<N>_Exposure.mrc`, `Position_<N>_Tracking.mrc`, `Position_<N>_<k>_Exposure.mrc` |
| Acquired | tilt series `Position_<N>[_<k>].mrc` and `.mdoc` in the session folder (*Acquired 1/3* when only some areas are done) |

With *Batch Positions* selected, the **Search** tab shows `Position_<N>_Search.mrc` with the
position's areas drawn as in Tomo5:

- **green**: each exposure area N.k, with a square (camera field), a circle (beam) and an ellipse
  (the beam at ±60° tilt: 1/cos 60° = 2× longer across the tilt axis); filled stronger once acquired;
- **yellow**: the tracking area; **small blue square**: the focus area;
- **red cross** on N.1, **blue line**: the tilt axis, thin lines from N.1 to the other areas.

The areas are also drawn on the Overview and Search Map tabs for every position (as dots when
zoomed out; dots on the Atlas). Click a position to select it, double-click to open its Search image.
The Overview and Search Map tabs show the search map that holds the selected position.

**Exposure tab.** In the batch-position table the *Areas* column shows each exposure area as a
numbered circle (white = the area shown, dim = no image yet). Click a circle to show that area in the
**Exposure** tab:

- the tilt series `Position_<N>.mrc`, `Position_<N>_2.mrc`, … (only the section shown is read, so
  large stacks open quickly); it starts at the section closest to 0°, and the drop-down in the bar
  lists the sections by tilt angle, from the `.mdoc`;
- before acquisition, the refined exposure image `Position_<N>[_<k>]_Exposure.mrc`.

The tab is greyed out when the position is only *Initialized* or the area has no image. Image
Information shows the section's tilt, dose, defocus, exposure time, magnification and time from the
`.mdoc`, and *All XML fields* lists the `.mdoc` header and section.

**Filter.** Counted low-dose exposures (tens of electrons per pixel) are dominated by shot noise and
look like grain at full resolution. The **Filter** panel under the histogram has a Gaussian
**low-pass** set by resolution (10–500 Å): it keeps detail down to about that size. It is on by
default at 60 Å for the Exposure tab and off for maps and Search images (separate settings,
remembered in the browser). It changes only the display; Image Information shows unfiltered values.

**Sizes.** The camera field is read from the exposure pixel size and image size (a tilt-series
`.mdoc`, or the FEI extended header of an Exposure / tilt-series MRC; 0.81 µm by default). Tomo5 does
not store the beam size, so the beam is drawn as **2× the camera field**. Both can be changed in the
toolbar under the image (*Camera … µm*, *Beam … × camera*, shown on views that draw areas) and are
remembered in the browser.

### How tiles are placed

Every Tomo5 image XML (`Overview.xml`, `Tile_*.xml`, …) has:

- `microscopeData/stage/Position/X, Y`: the stage position of the image centre. For tiles,
  Tomo5 folds the image shift into it, so each tile has its own position.
- `SpatialScale/pixelSize/x`: the pixel size of the image.
- `ReferenceTransformation/matrix`: a 2 × 2 matrix from image pixels to stage metres. It holds
  the camera rotation and a flip, and differs slightly between magnifications (about 3° between
  the overview and the tiles). It is given per unbinned camera pixel, so the viewer uses only its
  direction and takes the size from the pixel size. **Its sign is the opposite of pixel → stage
  as Tomo5 draws it**: the viewer uses −R. This was checked on real data (search maps on the
  atlas, and tiles against `SearchMap.mrc`).

So `stage = S − pixelSize · R · (pixel − centre)`. The pixel size always refers to the saved
image, also for stitched images (the atlas, `SearchMap.mrc`) whose size differs from the camera
readout. A search map is marked on the atlas with the footprint of `SearchMap.mrc`
(`SearchMap.xml` position, pixel size and direction); a map without `SearchMap.mrc` yet is shown
as a point at its overview centre. The view is turned so the main image (atlas,
overview, or the first tile) looks as it does in Tomo5. If the tile XML files have no positions,
the tiles are laid out as a grid in file order (grid size from `TileSetAcquisitionOptions.xml`),
and a note says so.

## Running on a remote workstation (port forwarding)

This works the same way as running a Jupyter notebook on a remote machine. The viewer runs on
the workstation that has the data and listens only on that machine. An SSH tunnel then brings
its port to your own computer, so nothing is opened to the network.

### 1. Start the viewer on the workstation

```bash
ssh user@workstation
cd tomo5_data_viewer
python3 tomo5_viewer.py /path/to/Tomo5/sessions --port 8766
```

When it starts, the viewer prints the tunnel command to run on your own computer:

```text
Tomo5 Data Viewer 0.1 on http://localhost:8766
  Tested with Tomo5 5.26
  sharing  sessions  ->  /path/to/Tomo5/sessions
To view it from your own computer, run there:
  ssh -N -L 8766:localhost:8766 user@workstation
then open http://localhost:8766
Press Ctrl+C to stop.
```

To keep the viewer running after you log out, start it inside `tmux` or `screen`:

```bash
tmux new -s tomo5                                   # start a session
python3 tomo5_viewer.py /path/to/Tomo5/sessions --port 8766
# detach with Ctrl+B then D; reattach later with:  tmux attach -t tomo5
```

or with `nohup`:

```bash
nohup python3 tomo5_viewer.py /path/to/Tomo5/sessions --port 8766 > tomo5_viewer.log 2>&1 &
```

### 2. Open the tunnel from your own computer

In a new terminal **on your own computer** (macOS, Linux, or Windows PowerShell):

```bash
ssh -N -L 8766:localhost:8766 user@workstation
```

- `-L 8766:localhost:8766` means: local port 8766 → port 8766 on the workstation.
- `-N` opens the tunnel without starting a shell. The terminal looks frozen, which means it is working.
  Leave it open while you use the viewer, and press Ctrl+C to close the tunnel.
- Add `-f` (`ssh -N -f -L …`) to send the tunnel to the background instead.

### 3. Open the viewer

Open **http://localhost:8766** in Chrome, Edge or Firefox on your own computer.

### Common variations

**Port already in use on your computer** (for example, the SerialEM Grid Viewer on 8765, or
another tunnel). Pick a different local port. Only the first number changes:

```bash
ssh -N -L 9000:localhost:8766 user@workstation      # then open http://localhost:9000
```

**Port already in use on the workstation** (someone else runs the viewer there). Start the viewer
on another port and tunnel that one:

```bash
python3 tomo5_viewer.py /path/to/sessions --port 8770        # on the workstation
ssh -N -L 8770:localhost:8770 user@workstation               # on your computer
```

**Through a gateway / login node** (the workstation is only reachable through another host):

```bash
ssh -N -J user@gateway -L 8766:localhost:8766 user@workstation
```

**Viewer on a compute node of a cluster** (the viewer runs on `node042`, which you reach from the
login node). Start the viewer on the node with `--host 0.0.0.0`, then forward through the login node:

```bash
python3 tomo5_viewer.py /path/to/sessions --port 8766 --host 0.0.0.0   # on node042
ssh -N -L 8766:node042:8766 user@login-node                             # on your computer
```

**Save it in `~/.ssh/config`** so the tunnel is one short command:

```text
Host tomo5
    HostName workstation.example.org
    User user
    LocalForward 8766 localhost:8766
    # ProxyJump user@gateway        # if needed
```

Then run `ssh -N tomo5`.

**Windows without OpenSSH:** in PuTTY, go to *Connection → SSH → Tunnels*, set
*Source port* `8766` and *Destination* `localhost:8766`, click *Add*, then connect as usual.

**VS Code Remote-SSH:** if you started the viewer from a VS Code terminal connected to the
workstation, VS Code usually forwards the port by itself (see the *Ports* tab).

### Troubleshooting

| Problem | Fix |
|---|---|
| `bind [127.0.0.1]:8766: Address already in use` | A tunnel or program already uses that local port. Close the old tunnel (`pkill -f "ssh -N"` on macOS/Linux) or use another local port. |
| `OSError: [Errno 98] Address already in use` on the workstation | The viewer is already running there, or another program uses the port. Use `--port 8770`. |
| The browser says "connection refused" | The viewer is not running on the workstation, or the tunnel points to the wrong port. Check both terminals. |
| `channel 2: open failed: connect failed` in the tunnel terminal | The tunnel works but nothing listens on that port on the workstation. Check the viewer's port. |
| The page loads but **Open session…** is missing | The page was opened as a file, not through `http://localhost:…`. |

## Server options

```text
python3 tomo5_viewer.py ROOT [ROOT ...] [--port 8766] [--host 127.0.0.1] [--html index.html] [--verbose]
```

- Only the listed folders are reachable, and nothing is ever written.
- `--host 0.0.0.0` makes the server reachable from other machines. The default, `127.0.0.1`,
  with an SSH tunnel is safer.

## Requirements

- Python 3.7+ (numpy optional, recommended).
- A current Chrome, Edge or Firefox.
