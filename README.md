# tomo5_data_viewer

A web-based interface to view Thermo Scientific Tomography 5 (Tomo5) data, in the spirit of the
[SerialEM Grid Viewer](https://github.com/builab/serialEM_grid_viewer).

It is one HTML page (`index.html`) plus a small Python server (`tomo5_viewer.py`) that serves your
session folders to the browser, read-only.

**Status: v0.1, atlas only. Tested with Tomo5 5.26.** The viewer opens a Tomo5 session, reads the atlas XML and shows the
stitched atlas image. Search maps, batch positions and tilt series come next.
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
