# tomo5_data_viewer

A web viewer for Thermo Scientific Tomography 5 (Tomo5) sessions, in the spirit of the
[SerialEM Grid Viewer](https://github.com/builab/serialEM_grid_viewer): one HTML page
(`index.html`) and a small Python server (`tomo5_viewer.py`). It never changes Tomo5's files.

**v0.9** · atlas, search maps, batch positions, exposures, tilt series (Movie Player), acquisition
movies · tested with Tomo5 5.26 · data layout: [tomo5_data_struct.md](tomo5_data_struct.md)

## Install

The viewer runs on plain Python 3.7+. Two optional extras, installed with pip (no admin rights):

```bash
git clone https://github.com/builab/tomo5_data_viewer.git
cd tomo5_data_viewer
pip install --user -r requirements.txt        # numpy + imageio-ffmpeg (optional)
```

- **numpy**: faster loading; needed for `--make-previews`.
- **imageio-ffmpeg**: brings its own ffmpeg, so movies are `.mp4` (without it the browser makes `.webm`).

At startup the server says what is missing and the pip command to add it. Alternatives: a venv
(`python3 -m venv ~/tomo5_env && ~/tomo5_env/bin/pip install -r requirements.txt`), conda
(`conda env create -f environment.yml`), or any ffmpeg on the PATH (`apt install ffmpeg`,
`module load ffmpeg`, or `--ffmpeg PATH`).

## Quick start

```bash
python3 tomo5_viewer.py /path/to/Tomo5/sessions /path/to/tilt_series
```

Give every folder you want to open (only those are reachable). Open the address it prints
(default **http://localhost:8766**) in Chrome, Edge or Firefox, then:

1. **Open Session Data…**: the session folder (with `Session.dm`, `Atlas/`, `SearchMaps/`, `Batch/`).
2. **Tilt Series Folder…**: the folder with `Position_<N>[_<k>].mrc` and `.mdoc` (subfolders too).

If the port is busy, the next free one is used. The data on another machine? See
[Remote workstation](#remote-workstation).

## Using the viewer

The layout follows Tomo5.

- **Left, Tasks:** *Search Maps*, *Batch Positions* and *Movie Player* choose the table on the right.
- **Centre:** tabs **Atlas › Overview › Search Map › Search › Exposure**. Scroll to zoom, drag to
  pan, double-click or **F** to fit; ‹ › and the arrow keys step through the table (after the last
  comes the first).
- **Right:** the table (click ★ / Rating to sort highest first), **Histogram**, **Filter** (low-pass,
  60 Å by default for exposures) and **Image Information** with every XML / `.mdoc` field.
- **Right-click** the image to zoom in to a deeper level for what is under the cursor (e.g. Atlas ›
  a position's Search image) or out one level, to copy the stage position, and to make movies.
- **?** (top right, or the **?** key) lists all shortcuts.

**Search maps.** The atlas marks every search map (green = acquired, orange = in progress); the
Search Map tab shows the tiles stitched in the browser (aligned and blended) or Tomo5's
`SearchMap.mrc`.

**Batch positions.** Status is Tomo5's own (Initialized, Refined, Acquired, …). The Search tab draws
each exposure area N.k as in Tomo5: camera square, beam circle and the ±60° ellipse in green, the
tracking area in yellow, focus in blue, the tilt axis in blue. Tomo5 does not store the beam size,
so it is drawn as 2× the camera field (adjustable under the image). The Exposure tab shows the tilt
series of the area, or its refined exposure before acquisition.

**Tilt angles.** Tomo5 saves the stack sorted by angle but the `.mdoc` in acquisition order, so the
viewer takes each section's angle from the stack's FEI header (else from the `.mdoc` sorted by
angle) and pairs it with the `.mdoc` block of that angle. Image Information shows the source.

## Movie Player

*Movie Player* lists every tilt series. Select one: it opens in the Exposure tab with a play bar.

- **Play / Pause** (Space), slider, fps, order (*as acquired* or *by angle*). Paused, the
  full-resolution section is shown.
- **next series at end** goes through the whole list (and round again).
- **★ rating** per tilt series: click the stars (top right or in the table) or press **1–5**
  (**0** clears). Saved at once to `tiltseries_movie/tiltseries_ratings.xml`, no `--edit` needed.
- **Export movie** downloads the series as it plays (`.mp4` or `.webm`).

Without previews the frames are read live from the full stacks (playback starts after the first
few frames). For instant playback, make the previews once:

```bash
python3 tomo5_viewer.py --make-previews /path/to/tilt_series --mp4
```

This writes, per tilt series, into `tilt_series/tiltseries_movie/`: a 512 px 8-bit `.mrc`, a
`.json` (angles, times, pixel size, source size/date) and, with `--mp4`, an `.mp4`. Up-to-date
previews are skipped on the next run. Reading the stacks sets the time (~1.4 GB per 41-image
series):

| Storage | 100–200 series |
|---|---|
| Local disk / RAID | ~7–15 min |
| 10 GbE share | ~4–10 min |
| 1 GbE share | ~25–50 min |

It prints the total speed of all workers. On a slow network share try `--workers 4`–`8`, or run it
on the machine where the disks are local. Other options: `--preview-out DIR`, `--preview-size`,
`--preview-fps`, `--preview-lowpass`, `--force`.

## Acquisition movies

Right-click an area in **Batch Positions › Search** (or a position on the Search Map, or the
Exposure tab):

- **Make acquisition movie (N.k)…**: Atlas › Search Map › the area › its tilt series as an inset,
  with the angle and a stage-tilt schematic.
- **Make parallel acquisition movie (Position_N)…**: the areas picked one by one, then all their
  tilt series playing side by side.

The dialog has a preview and settings (titles, zoom, timing, order: start → lowest → highest →
start by default, low-pass, 720p / 1080p / 4K). **Make movie** encodes with ffmpeg on the server
(`.mp4`) or in the browser (`.webm`) and offers a download.

## Remote workstation

Like a Jupyter notebook: run the viewer where the data is and tunnel its port over SSH.

```bash
# on the workstation (tmux or nohup keep it running after you log out)
python3 tomo5_viewer.py /path/to/sessions /path/to/tilt_series

# on your computer: the command the viewer printed, e.g.
ssh -N -L 8766:localhost:8766 user@workstation
```

Then open **http://localhost:8766**. Variations:

- Local port taken: `ssh -N -L 9000:localhost:8766 user@workstation`, then open `localhost:9000`.
- Through a gateway: `ssh -N -J user@gateway -L 8766:localhost:8766 user@workstation`.
- On a cluster node: start with `--host 0.0.0.0`, then `ssh -N -L 8766:node042:8766 user@login-node`.
- `~/.ssh/config` with `LocalForward 8766 localhost:8766` makes it `ssh -N tomo5`; PuTTY:
  *Connection › SSH › Tunnels*; VS Code Remote-SSH forwards it by itself.

| Problem | Fix |
|---|---|
| `bind … Address already in use` (your computer) | An old tunnel uses the port: close it, or use another local port. |
| "connection refused" / `channel 2: open failed` | The viewer is not running, or the tunnel points to the wrong port. |
| *Host '…' is not allowed* | Open it as `http://localhost:PORT`; behind a proxy add `--allow-host NAME`. |
| No **Open Session Data…** button | The page was opened as a file instead of through the server. |

## Server options

```text
python3 tomo5_viewer.py ROOT [ROOT ...] [--port 8766] [--port-retries 50] [--host 127.0.0.1]
                        [--allow-host NAME] [--ffmpeg auto|PATH|none] [--read-only] [--verbose]
python3 tomo5_viewer.py --make-previews TILTDIR [--mp4] [--workers 2] [--force] [...]
```

- Only the listed folders are reachable. The only file written is the star ratings
  (`tiltseries_movie/tiltseries_ratings.xml`); `--read-only` turns that off too.
- Requests must be addressed to `localhost`, an IP address or this machine's name (protection
  against DNS rebinding); `--allow-host` adds a name.
- `--host 0.0.0.0` opens the server to the network; the default with an SSH tunnel is safer.
- Movies are encoded in a temporary folder, deleted after an hour or when the server stops.

## Testing

```bash
pip install playwright && python3 -m playwright install chromium        # once
make test SESSION=/path/to/Session TILT=/path/to/tilt_series             # clicks through everything
make check                                                              # syntax only
```

`make test` runs `tests/smoke_test.py`: a read-only viewer on a free port, every task, tab, button,
menu item and shortcut in a headless browser, and a report of JavaScript and server errors.
