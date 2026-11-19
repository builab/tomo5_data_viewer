# tomo5_data_viewer

A web-based interface to view Thermo Scientific Tomography 5 (Tomo5) data, in the spirit of the
[SerialEM Grid Viewer](https://github.com/builab/serialEM_grid_viewer).

It is one HTML page (`index.html`) plus a small Python server (`tomo5_viewer.py`) that serves your
session folders to the browser, read-only.

**Status: v0.1, atlas only.** The viewer opens a Tomo5 session, reads the atlas XML and shows the
stitched atlas image. Search maps, batch positions and tilt series come next.
The session layout is described in [tomo5_data_struct.md](tomo5_data_struct.md).

## Quick start

On the computer that has the data:

```bash
git clone https://github.com/builab/tomo5_data_viewer.git
cd tomo5_data_viewer
python3 tomo5_viewer.py /path/to/Tomo5/sessions --port 8766
```

If the data is on a remote workstation, tunnel the port from your own computer:

```bash
ssh -N -f -L localhost:8766:localhost:8766 user@remote_server
```

Then open **http://localhost:8766** in Chrome, Edge or Firefox, click **Open session…** and pick
a session folder (the one that contains `Atlas/`, `Batch/` and `SearchMaps/`).

Without the server, you can also open `index.html` directly in the browser and use
**Open folder from this computer…**. Only the atlas files are read.

## Atlas

- The viewer finds `Atlas/Atlas_<date>_<time>.xml` and the `.mrc` with the same name.
  The atlas `.mrc` is a single stitched image (like EPU), not a stack of tiles.
- **Atlas panel:** image size, pixel size, field of view, stage position, magnification, camera
  and acquisition time, read from the XML. If the XML has no pixel size, the MRC header is used.
  A warning shows when the two differ.
- **Display:** black and white levels (as percentiles, with a histogram), invert, and a switch
  to the `.jpg` preview.
- **Atlas XML:** every field of the XML, flattened into `path = value`, with a filter.
- **Map:** scroll to zoom, drag to pan, double-click or **F** to fit, **+** / **−** to zoom.
  The scale bar and the cursor readout (pixel, offset from the centre in µm, value) use the
  pixel size.

Atlases larger than 4096 pixels are binned when they load. With numpy on the server, the server
does the binning; otherwise the browser reads the full file and bins it.

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
