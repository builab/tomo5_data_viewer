# tomo5_data_viewer

A web-based interface to view Thermo Scientific Tomography 5 (Tomo5) data, in the spirit of the
[SerialEM Grid Viewer](https://github.com/builab/serialEM_grid_viewer).

It is one HTML page (`index.html`) plus a small Python server (`tomo5_viewer.py`) that serves your
session folders to the browser. It does not change Tomo5's files; the only thing it writes is the
Movie Player's star ratings, into `tiltseries_movie/` (see below).

**Status: v0.8: atlas, search maps, batch positions, exposures, tilt series (Movie Player) and
acquisition movies. Tested with Tomo5 5.26.**
The session layout is described in [tomo5_data_struct.md](tomo5_data_struct.md).

## Quick start

On the computer that has the data:

```bash
git clone https://github.com/builab/tomo5_data_viewer.git
cd tomo5_data_viewer
python3 tomo5_viewer.py /path/to/Tomo5/sessions /path/to/tilt_series --port 8766
```

List every folder you want to open: the Tomo5 session data and, if they are kept elsewhere, the
tilt series. Only listed folders are reachable.

Then open **http://localhost:8766** in Chrome, Edge or Firefox and:

1. **Open Session Data…**: pick the session folder, the one with `Session.dm`, `Atlas/`,
   `SearchMaps/`, `Batch/` and `Thumbnails/`.
2. **Tilt Series Folder…**: pick the folder with the tilt series `Position_<N>[_<k>].mrc` and their
   `.mdoc` files (also found one level of subfolders down). They are used for the Exposure tab, the
   *Acquired* status and the camera field. Tilt series that sit in the session folder itself are
   used too. The choice is kept in the page address, so a reload keeps it.

If the port is busy (someone else runs the viewer there), the next free port is used, as
Jupyter does; the address and tunnel command it prints show which one.

If the data is on a remote workstation, see
[Running on a remote workstation](#running-on-a-remote-workstation-port-forwarding).

The viewer always runs through `tomo5_viewer.py`: opening `index.html` directly only shows how to
start the server.

## Install

Nothing to install for the viewer itself: it runs on plain Python 3.7+. Two optional extras make
it better, and both install with pip, without admin rights:

```bash
pip install --user -r requirements.txt        # = numpy + imageio-ffmpeg
```

| Extra | What it adds | Without it |
|---|---|---|
| numpy | faster loading (the server shrinks images and sends 16-bit data); `--make-previews` | the browser reads full files and shrinks them (slower); no preview cache |
| imageio-ffmpeg | its own ffmpeg, so movies are `.mp4` (H.264) | movies are encoded in the browser as `.webm` (Chrome, Edge) |

When it starts, `tomo5_viewer.py` says what it found, and the pip command for anything missing.

Other ways, if you prefer:

- **A virtual environment**, to keep it separate:
  ```bash
  python3 -m venv ~/tomo5_env && ~/tomo5_env/bin/pip install -r requirements.txt
  ~/tomo5_env/bin/python tomo5_viewer.py /path/to/sessions
  ```
- **Conda** (`environment.yml`, with ffmpeg from conda-forge):
  ```bash
  conda env create -f environment.yml && conda activate tomo5_viewer
  python tomo5_viewer.py /path/to/sessions
  ```
- **A system ffmpeg** instead of imageio-ffmpeg: `sudo apt install ffmpeg` (Ubuntu/Debian),
  `sudo dnf install ffmpeg` (Rocky/Alma/RHEL with EPEL and RPM Fusion), or `module load ffmpeg`
  on a cluster. Any ffmpeg on the PATH is used; `--ffmpeg PATH` picks a specific one.

## Interface

The layout follows Tomo5:

- **Left, Tasks:** *Search Maps* and *Batch Positions* (acquired / total) switch the table on
  the right. *Movie Player* lists the tilt series to play (see below).
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

### Right-click: zoom in / zoom out

Right-click on the image opens a menu for what is under the cursor. The levels are
**Atlas › Overview › Search Map › Search › Exposure**: *Zoom in* can jump to any deeper level for that
item (Exposure only from Search Map and Search, where the areas are big enough to pick), *Zoom out* goes exactly one level up (like the previous tab). The item is selected first, so the
table on the right matches, and the next view opens centred on it. Greyed-out entries say why.

| Right-click on | Zoom in | Zoom out |
|---|---|---|
| Atlas: a search map | Overview · Search Map | |
| Atlas: a position | Overview · Search Map (centred on it) · Search | |
| Atlas: empty | Zoom in 2× here | |
| Overview: inside the map | Search Map | Atlas |
| Overview: a position / area | Search Map · Search | Atlas |
| Search Map: a position / area | Search · Exposure N.1, N.2, … (that area first) | Overview (centred on the position) |
| Search: an area / elsewhere | Exposure N.k | Search Map (centred on the position) |
| Exposure | the other areas N.k | Search |

Every menu also has *Fit to window*, *Copy stage X, Y* (the stage position under the cursor, in µm)
and *Select* for the item under the cursor. Left-click still selects and double-click still zooms in
one level.

### Seamless stitching

*Tiles, stitched here (aligned, blended)* works like the SerialEM Grid Viewer's montages:

1. **Align.** The tiles start from Tomo5's own tile grid (`AtlasPixelPosition` of each tile in
   `SearchMap.dm`, axis-aligned in camera pixels), or from the tile XML positions when that is
   missing. The overlap of each pair of neighbouring tiles is then cross-correlated (after a high-pass
   filter) over ±120 px, coarse at half size and refined at full display size. Shifts where the
   tiles overlap by less than a quarter of the expected overlap are ignored, and a match is accepted
   when its peak clearly stands out (low-dose tiles correlate weakly, ~0.1). Least-squares tile
   positions are solved from all overlaps; inconsistent matches are dropped, and failed matches fall
   back to the grid.
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
| Acquired | tilt series `Position_<N>[_<k>].mrc` and `.mdoc` in the tilt series folder or the session folder (*Acquired 1/3* when only some areas are done) |

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

### Acquisition movies

Right-click a sub-position in **Batch Positions › Search** (or a position on the Search Map,
or anywhere on its **Exposure** tab) and choose:

- **Make acquisition movie (N.k)…** — Atlas › zoom to the Search Map › zoom to area N.k (the
  Search image fades in as the close-up) › its tilt series as an inset, with the tilt angle, an
  angle track and a stage-tilt schematic.
- **Make parallel acquisition movie (Position_N)…** — the same up to the Search Map, then the
  Search image with each area picked in turn (1.1, the tilt axis and tracking area, then 1.2,
  1.3, …), then all the tilt series in a grid, playing in step by tilt angle.

Both need the tilt series (**Tilt Series Folder…**). The dialog shows a preview you can play and
scrub, with settings for titles, zoom, timing, playback order (start → lowest → highest →
start by default, as acquired from the `.mdoc` times, or by angle), a low-pass for the tilt images (60 Å by
default), size (720p, 1080p, 4K) and frame rate. **Make movie** encodes it with ffmpeg on the
server (`.mp4`, H.264) and offers it for download; nothing is written into the data folders.
The tilt series play **start → lowest → highest → start** by default (from the first acquired
angle, usually 0°); *as acquired* and *by angle* are the other choices.
Without ffmpeg the browser encodes a `.webm` (current Chrome or Edge). As a guide, a 12 s 720p
parallel movie of three 41-image tilt series took about 16 s to render.

### Movie Player (tilt series)

**Movie Player** on the left lists every tilt series of the session (each area N.k with a
`Position_N[_k].mrc`), with its status, the rating of its search map (click ★ to sort) and
whether it has a preview. Select one and it opens in the **Exposure** tab with a play bar under
the image (the same bar appears in the Exposure tab of Batch Positions):

- **▶ Play / ❚❚ Pause** (or the Space bar). Paused, the full-resolution section where it stopped
  is shown again; the slider scrubs through the frames.
- **fps** (5–30) and **Order**: *as acquired* (the `.mdoc` DateTime, else file order) or *by angle*.
- **next series at end** (Movie Player only): when a series ends, go on with the next one in the
  list, for going through a whole session. Arrow keys step through the list.
- Contrast and the low-pass filter (right panel) apply while it plays; one contrast is used for
  the whole series.
- **★ Star rating** of each tilt series, at the top right of the image (next to its name) and in
  the ★ column of the table, or with the keys **1–5** (**0** clears) while it plays. It works
  without `--edit` and is saved at once (no Save button) in
  `tiltseries_movie/tiltseries_ratings.xml`. Click the ★ header to sort the best first; with
  *next series at end* and the number keys you can go through a whole session in one pass.
- **Export movie** saves the series as it plays (order, contrast, low-pass, angle label) as an
  `.mp4` (ffmpeg on the server) or `.webm` (in the browser), downloaded to your computer.

Frames come from the **preview cache** when there is one, and start at once. Without it they are
read **live**: each section is read from the full-size tilt series and shrunk to 512 px on the
server, so the first play of a series takes as long as reading the stack (see below); later
plays are instant until the page is reloaded.

### Preview cache: `tiltseries_movie/` (recommended)

Make the previews once, on the machine with the data (needs numpy; `--mp4` needs ffmpeg):

```bash
python3 tomo5_viewer.py --make-previews /data/tomo5/TiltSeries_folder --mp4
```

For every `Position_N[_k].mrc` in the folder (and one level of subfolders) this writes, into
**`TiltSeries_folder/tiltseries_movie/`**:

| File | What it is |
|---|---|
| `Position_N[_k].mrc` | the tilt series binned to ≤ 512 px, 8-bit (0.5–99.5 % of the whole stack), pixel size in the header, sections in the original order (≈ 10 MB for 41 images) |
| `Position_N[_k].json` | tilt angles and times (from the `.mdoc`), pixel size, binning, and the size and date of the source, to tell when it is out of date |
| `Position_N[_k].mp4` | with `--mp4`: a movie of the series by angle, 60 Å low-pass, 10 fps (≈ 1–4 MB), to share or play anywhere |

The same folder holds **`tiltseries_ratings.xml`**, the star ratings set in the Movie Player
(written by the viewer, also when there are no previews yet; `--make-previews` never touches it):

```xml
<?xml version="1.0" encoding="utf-8"?>
<TiltSeriesRatings tool="tomo5_viewer.py 0.8" updated="2026-11-21T21:09:28">
  <TiltSeries name="Position_1_2.mrc" position="1" area="2" rating="4" updated="2026-11-21T21:09:22" />
  <TiltSeries name="Position_1_3.mrc" position="1" area="3" rating="5" updated="2026-11-21T21:09:23" />
</TiltSeriesRatings>
```

It is plain XML, easy to read from a script, e.g. to pick the 4- and 5-star tilt series for
reconstruction.

Run it again after more tilt series are acquired: previews that are up to date are skipped
(`--force` remakes them). The viewer finds the folder by itself when you choose the **Tilt
Series Folder…** (it also looks in `<session>/tiltseries_movie`); the top bar shows
*N previews* and the table a ✓. A preview whose tilt series changed is ignored (played live).

Options: `--preview-out DIR` (another place, e.g. when the tilt-series folder is read-only; then
put or link it as `tiltseries_movie` next to the tilt series or in the session folder),
`--preview-size 512`, `--preview-fps 10`, `--preview-lowpass 60` (Å, 0 = off), `--workers 2`.

**How long it takes.** Reading the full-size stacks dominates; shrinking and writing take about
1–2 s per series. A 41-image 4096 × 4096 16-bit series is 1.4 GB (2.8 GB as 32-bit), so per
series and for a session of 100–200 series:

| Storage | Per series | 100–200 series |
|---|---|---|
| Local disk / RAID (~500 MB/s) | ~4 s | ~7–15 min |
| 10 GbE share (~1 GB/s) | ~2–3 s | ~4–10 min |
| 1 GbE share (~110 MB/s) | ~13–15 s | ~25–50 min |

It prints progress with the time left and the speed of all workers together. If that total stays
low (tens of MB/s), the disk or share sets the pace: on a network share try more `--workers`
(4–8 help when each read waits on the network, e.g. sshfs, SMB, or NFS with small reads; they do
not help when the link is simply full, or on spinning disks), or run it on the machine where the
disks are local. Playing *live* in the viewer costs the same reading time,
per series, each time the page is loaded.

### Making movies: summary

| You want | Use |
|---|---|
| A quick look at many tilt series | **Movie Player**, with the preview cache |
| One tilt series as a movie file | **Export movie** in the play bar, or `--make-previews … --mp4` for all of them at once (in `tiltseries_movie/`) |
| A presentation movie: atlas → search map → area → tilt series | right-click › **Make acquisition movie (N.k)…** |
| The same for all areas of a position, in parallel | right-click › **Make parallel acquisition movie (Position_N)…** |

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

When it starts, the viewer prints the tunnel command to run on your own computer. If the port is
already taken on the workstation, it moves on to the next free one (like Jupyter) and the
printed address and tunnel command use that port:

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

**Port already in use on the workstation** (someone else runs the viewer there). The viewer
takes the next free port by itself and prints, for example:

```text
The port 8766 is already in use, trying another port.
Tomo5 Data Viewer 0.8 on http://localhost:8767
…
  ssh -N -L 8767:localhost:8767 user@workstation
```

Use the printed tunnel command. To choose the port yourself, pass `--port 8770`; to fail instead
of moving on, pass `--port-retries 0`.

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
| The viewer printed *The port 8766 is already in use, trying another port.* | Normal: it took the next free port. Use the address and tunnel command it printed. |
| The browser says "connection refused" | The viewer is not running on the workstation, or the tunnel points to the wrong port. Check both terminals. |
| `channel 2: open failed: connect failed` in the tunnel terminal | The tunnel works but nothing listens on that port on the workstation. Check the viewer's port. |
| The page loads but **Open Session Data…** is missing | The page was opened as a file, not through `http://localhost:…`. |

## Server options

```text
python3 tomo5_viewer.py ROOT [ROOT ...] [--port 8766] [--port-retries 50] [--host 127.0.0.1] [--html index.html] [--verbose] [--ffmpeg auto] [--read-only]
python3 tomo5_viewer.py --make-previews TILTDIR [--mp4] [--preview-out DIR] [--preview-size 512]
                        [--preview-fps 10] [--preview-lowpass 60] [--workers 2] [--force]
```

- Only the listed folders are reachable. The server never changes Tomo5's files. It writes only
  the Movie Player's star ratings (`tiltseries_movie/tiltseries_ratings.xml`, next to the tilt
  series) and, in the experimental edit mode, the edit folders.
- `--read-only` writes nothing at all: the star ratings are shown but cannot be changed.
  (`--make-previews` is a separate command you run yourself; it writes only into
  `TILTDIR/tiltseries_movie/` or `--preview-out`, and then exits.)
- `--ffmpeg` sets the ffmpeg used for acquisition movies: a path, `auto` (default: look on the
  PATH, or the `imageio-ffmpeg` Python package) or `none` (the browser encodes them). Movies are
  encoded in a temporary folder that is deleted after an hour or when the server stops.
- `--port` is where it listens (default 8766). If that port is busy, the next of up to
  `--port-retries` (default 50) ports is used, as Jupyter does; `--port-retries 0` stops instead.
- `--host 0.0.0.0` makes the server reachable from other machines. The default, `127.0.0.1`,
  with an SSH tunnel is safer.

## Requirements

- Python 3.7+.
- Optional (see [Install](#install)): numpy (faster; needed for `--make-previews`) and ffmpeg via
  `imageio-ffmpeg`, conda or the system (for `.mp4` movies).
- A current Chrome, Edge or Firefox.
