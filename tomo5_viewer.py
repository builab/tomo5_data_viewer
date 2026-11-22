#!/usr/bin/env python3
"""
tomo5_viewer.py - serve Tomo5 session folders to the Tomo5 Data Viewer (index.html).

The viewer page (index.html) must sit next to this script, or be given with --html.
Only the folders you list are reachable. The server writes only the Movie Player's star ratings
(tiltseries_movie/tiltseries_ratings.xml; --read-only turns that off) and, with --edit, edits.
Acquisition movies are encoded by ffmpeg on this machine (if installed) in a temporary
folder and downloaded from there.

Usage
    python3 tomo5_viewer.py /path/to/data [/another/root ...] [--port 8766] [--host 127.0.0.1]

Then open  http://localhost:8766  in Chrome, Edge or Firefox.

To view data on a remote workstation without opening a port to the network,
start the server there on 127.0.0.1 and tunnel it:
    ssh -N -f -L localhost:8766:localhost:8766 user@workstation
then open http://localhost:8766 on your own computer.

Requires Python 3.7+. If numpy is installed, the server shrinks large images
before sending them (much less data over the network); without numpy the viewer
reads the full file and shrinks it itself.
"""
import argparse
import errno
import getpass
import ipaddress
import json
import os
import re
import secrets
import socket
import struct
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import numpy as np
except ImportError:
    np = None

APP = "tomo5-data-viewer"
VERSION = "0.9"
TESTED_WITH = "Tomo5 5.26"
CHUNK = 1 << 20          # 1 MiB per write when streaming a file

ROOTS = {}               # name -> absolute path
HTML_PATH = None
VERBOSE = False
EDIT = False                    # --edit: allow saving edits (experimental)
EDIT_DIRS = {"batch": "Batch/Edit", "maps": "SearchMaps/Edit"}   # where edits go, relative to the session folder
MAX_SAVE = 32 << 20             # largest save request accepted
READ_ONLY = False               # --read-only: no tilt-series ratings either
FFMPEG = "auto"                 # --ffmpeg: path, "auto" (look on PATH) or "none"
ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1"}   # Host headers accepted (DNS-rebinding guard), plus IP addresses
MOVIES = {}                     # id -> {"proc", "dir", "out", "log", "n", "t"}: movies being encoded by ffmpeg
MOVIE_LOCK = threading.Lock()
MOVIE_MAX_FRAMES = 30000        # about 16 minutes at 30 frames per second
MOVIE_MAX_FRAME = 16 << 20
# files a save may write, relative to the edit folder
SAVE_OK = {"batch": re.compile(r"^(BatchPositionsList\.xml|edit_session\.json)$"),
           "maps": re.compile(r"^SearchMap_\d+_\d{8}_\d{6}/SearchMap\.dm$")}


_FFMPEG_EXE = {}


def ffmpeg_path():
    """The ffmpeg to use (looked up once)."""
    if FFMPEG in _FFMPEG_EXE:
        return _FFMPEG_EXE[FFMPEG]
    if FFMPEG == "none":
        exe = None
    elif FFMPEG != "auto":
        exe = FFMPEG if os.path.isfile(FFMPEG) else shutil.which(FFMPEG)
    else:
        exe = shutil.which("ffmpeg")
        if not exe:
            try:                                   # the copy that comes with the imageio-ffmpeg Python package
                import imageio_ffmpeg
                exe = imageio_ffmpeg.get_ffmpeg_exe()
            except Exception:
                exe = None
    _FFMPEG_EXE[FFMPEG] = exe
    return exe


def host_ok(header):
    """Accept requests addressed to this machine only: localhost, an IP address, this machine's
    names or --allow-host names. A web page that points its own domain name at 127.0.0.1 (DNS
    rebinding) sends that domain as Host and is refused."""
    if not header:
        return True                                # HTTP/1.0 clients
    h = header.strip().lower()
    if h.startswith("["):                          # [::1]:8766
        h = h[1:h.find("]")] if "]" in h else h
    elif h.count(":") == 1:
        h = h.rsplit(":", 1)[0]
    h = h.rstrip(".")
    if h in ALLOWED_HOSTS or h.endswith(".localhost"):
        return True
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False


_ENCODER = {}


def ffmpeg_encoder(exe):
    """An H.264 encoder this ffmpeg really has (some builds lack libx264), else MPEG-4."""
    if exe in _ENCODER:
        return _ENCODER[exe]
    try:
        out = subprocess.run([exe, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        out = ""
    table = [("libx264", ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"]),
             ("h264_videotoolbox", ["-c:v", "h264_videotoolbox", "-b:v", "20M", "-allow_sw", "1"]),
             ("libopenh264", ["-c:v", "libopenh264", "-b:v", "20M"]),
             ("h264_nvenc", ["-c:v", "h264_nvenc", "-cq", "19", "-b:v", "0"])]
    res = next(((n, a) for n, a in table if f" {n} " in out), ("mpeg4", ["-c:v", "mpeg4", "-q:v", "2"]))
    _ENCODER[exe] = res
    return res


def movie_drop(mid):
    """Stop the encoder of a movie and delete its temporary folder."""
    with MOVIE_LOCK:
        m = MOVIES.pop(mid, None)
    if not m:
        return
    try:
        if m["proc"].poll() is None:
            m["proc"].kill()
    except Exception:
        pass
    try:
        m["log"].close()
    except Exception:
        pass
    shutil.rmtree(m["dir"], ignore_errors=True)


def movie_sweep():
    """Forget movies nobody touched for an hour."""
    now = time.time()
    for mid in [k for k, m in list(MOVIES.items()) if now - m["t"] > 3600]:
        movie_drop(mid)


def norm(p):
    return os.path.normcase(os.path.normpath(os.path.abspath(p)))


def inside(path, root):
    try:
        return os.path.commonpath([norm(path), norm(root)]) == norm(root)
    except ValueError:   # different drives on Windows
        return False


def resolve(p):
    """Map an API path ('root/sub/file') to (real filesystem path, API path)."""
    parts = [x for x in (p or "").replace("\\", "/").split("/") if x not in ("", ".")]
    if not parts:
        return None, ""
    if ".." in parts:
        raise PermissionError("'..' is not allowed in paths.")
    if parts[0] not in ROOTS:
        raise FileNotFoundError(f"Unknown root '{parts[0]}'.")
    root = ROOTS[parts[0]]
    real = os.path.normpath(os.path.join(root, *parts[1:]))
    if not inside(real, root):
        raise PermissionError("That path is outside the folders this server shares.")
    return real, "/".join(parts)


MRC_DTYPES = {1: "<i2", 2: "<f4", 6: "<u2", 12: "<f2"}


def mrc_header(real):
    with open(real, "rb") as f:
        h = f.read(1024)
    if len(h) < 1024:
        raise ValueError("file is too short to be an MRC file")
    nx, ny, nz, mode = struct.unpack("<4i", h[:16])
    if not (0 < nx < 200000 and 0 < ny < 200000 and 0 < nz < 100000):
        raise ValueError("not a little-endian MRC file")
    nxt = max(0, struct.unpack("<i", h[92:96])[0])
    stamp, flags = struct.unpack("<2i", h[152:160])
    return nx, ny, nz, mode, nxt, stamp == 1146047817 and (flags & 1) == 1


def mrc_pixel_a(real):
    """Pixel size in Å of an MRC file itself: the FEI extended header (Tomo5, metadata version ≥ 2,
    metres at byte 156 of the first section's block), else the MRC header (cell size / grid size;
    1 Å or 0 there means "not set"). None if neither has it."""
    try:
        with open(real, "rb") as f:
            h = f.read(1024)
            nxt = max(0, struct.unpack("<i", h[92:96])[0])
            ext = f.read(480) if nxt >= 480 else b""
        if len(ext) == 480 and struct.unpack("<i", ext[4:8])[0] >= 2:
            px = struct.unpack("<d", ext[156:164])[0] * 1e10
            if 0.01 < px < 1e5:
                return px
        mx, xlen = struct.unpack("<i", h[28:32])[0], struct.unpack("<f", h[40:44])[0]
        if mx > 0 and xlen > 0:
            px = xlen / mx
            if abs(px - 1) > 1e-6 and 0.01 < px < 1e5:
                return px
    except (OSError, struct.error):
        pass
    return None


def read_binned_section(real, z, binf):
    """Read MRC section z and average bin x bin blocks. Returns (float32 bytes, width, height)."""
    nx, ny, nz, mode, nxt, signed = mrc_header(real)
    if mode == 0:
        dt = np.dtype(np.int8 if signed else np.uint8)
    elif mode in MRC_DTYPES:
        dt = np.dtype(MRC_DTYPES[mode])
    else:
        raise ValueError(f"MRC mode {mode} is not supported")
    if not 0 <= z < nz:
        raise ValueError(f"section {z} is outside the file (nz = {nz})")
    a = np.fromfile(real, dtype=dt, count=nx * ny, offset=1024 + nxt + z * nx * ny * dt.itemsize)
    if a.size < nx * ny:
        raise ValueError(f"file is shorter than expected at section {z}")
    a = a.reshape(ny, nx)
    bw, bh = nx // binf, ny // binf
    if binf == 1:
        return a.astype("<f4").tobytes(), bw, bh
    # sum in integers for integer data (2–3× faster than converting the whole section to float first)
    acc = np.int64 if dt.kind in "iu" else np.float64
    a = a[:bh * binf, :bw * binf].reshape(bh, binf, bw * binf).sum(axis=1, dtype=acc).reshape(bh, bw, binf).sum(axis=2)
    return (a / (binf * binf)).astype("<f4").tobytes(), bw, bh


# ---------- tilt-series previews: python3 tomo5_viewer.py --make-previews TILTDIR ----------
PREVIEW_DIR = "tiltseries_movie"
TS_RE = re.compile(r"^Position_(\d+)(?:_(\d+))?\.mrc$", re.I)


def parse_mdoc(path):
    """PixelSpacing (Å) and, per section, TiltAngle and DateTime, from a SerialEM-style .mdoc."""
    px, secs, cur = None, {}, None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.match(r"\s*\[\s*ZValue\s*=\s*(\d+)\s*\]", line, re.I)
                if m:
                    cur = secs.setdefault(int(m.group(1)), {})
                    continue
                if line.lstrip().startswith("["):
                    cur = None
                    continue
                kv = re.match(r"\s*([^=]+?)\s*=\s*(.*?)\s*$", line)
                if not kv:
                    continue
                k, v = kv.group(1).lower(), kv.group(2)
                if k == "pixelspacing" and px is None:
                    try:
                        px = float(v)
                    except ValueError:
                        pass
                elif cur is not None and k == "tiltangle":
                    try:
                        cur["a"] = float(v)
                    except ValueError:
                        pass
                elif cur is not None and k == "datetime":
                    cur["t"] = v
    except OSError:
        pass
    return px, secs


def find_tilt_series(tiltdir, skip):
    """Position_N[_k].mrc in the folder and one level of subfolders (like the viewer), with their .mdoc."""
    out = []
    dirs = [tiltdir] + sorted(os.path.join(tiltdir, d) for d in os.listdir(tiltdir)
                              if os.path.isdir(os.path.join(tiltdir, d)) and norm(os.path.join(tiltdir, d)) != norm(skip))
    for d in dirs:
        for name in sorted(os.listdir(d)):
            m = TS_RE.match(name)
            p = os.path.join(d, name)
            if not m or not os.path.isfile(p) or os.path.getsize(p) == 0:
                continue
            stem = name[:-4]
            mdoc = next((os.path.join(d, c) for c in (stem + ".mdoc", name + ".mdoc") if os.path.isfile(os.path.join(d, c))), None)
            out.append({"path": p, "stem": stem, "mdoc": mdoc})
    return out


def write_mrc_u8(path, stack, pixel_a):
    """An 8-bit (mode 0, unsigned) MRC stack with the pixel size in the header."""
    nz, ny, nx = stack.shape
    h = bytearray(1024)
    struct.pack_into("<10i", h, 0, nx, ny, nz, 0, 0, 0, 0, nx, ny, nz)
    struct.pack_into("<3f", h, 40, nx * pixel_a, ny * pixel_a, nz * pixel_a)
    struct.pack_into("<3f3i", h, 52, 90.0, 90.0, 90.0, 1, 2, 3)
    struct.pack_into("<3f", h, 76, 0.0, 255.0, float(stack.mean()))
    h[208:212] = b"MAP "
    h[212:216] = bytes([0x44, 0x44, 0, 0])
    struct.pack_into("<i", h, 220, 1)
    lab = b"tomo5_viewer.py preview of a Tomo5 tilt series"
    h[224:224 + len(lab)] = lab
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(bytes(h))
        f.write(np.ascontiguousarray(stack, dtype=np.uint8).tobytes())
    os.replace(tmp, path)


def make_preview(ts, out, size, mp4, lp, fps, exe):
    """Bin one tilt series to about `size` px, scale to 8 bits (0.5–99.5 % of the whole stack), save
    <stem>.mrc + <stem>.json (angles, times, pixel size, source) and optionally <stem>.mp4."""
    t0 = time.time()
    src = ts["path"]
    st = os.stat(src)
    nx, ny, nz, mode, nxt, signed = mrc_header(src)
    dt = np.dtype(np.int8 if signed else np.uint8) if mode == 0 else np.dtype(MRC_DTYPES[mode]) if mode in MRC_DTYPES else None
    if dt is None:
        raise ValueError(f"MRC mode {mode} is not supported")
    b = max(1, -(-max(nx, ny) // size))
    bw, bh = nx // b, ny // b
    acc = np.int64 if dt.kind in "iu" else np.float64
    stack = np.empty((nz, bh, bw), np.float32)
    with open(src, "rb") as f:
        f.seek(1024 + nxt)
        for z in range(nz):
            a = np.frombuffer(f.read(nx * ny * dt.itemsize), dt)
            if a.size < nx * ny:
                raise ValueError(f"file is shorter than its header says (section {z})")
            a = a.reshape(ny, nx)[:bh * b, :bw * b]
            s = a.reshape(bh, b, bw * b).sum(axis=1, dtype=acc).reshape(bh, bw, b).sum(axis=2)
            stack[z] = s / (b * b)
    smp = stack[:, ::4, ::4].ravel()
    smp = smp[np.isfinite(smp)]
    lo, hi = (np.percentile(smp, [0.5, 99.5]) if smp.size else (0.0, 1.0))
    hi = hi if hi > lo else lo + 1
    u8 = np.clip((np.nan_to_num(stack, nan=lo) - lo) * (255.0 / (hi - lo)), 0, 255).round().astype(np.uint8)
    px_src, secs = parse_mdoc(ts["mdoc"]) if ts["mdoc"] else (None, {})
    px_from = "mdoc" if px_src else None
    if not px_src:                                 # no .mdoc (or no PixelSpacing): from the MRC file itself
        px_src = mrc_pixel_a(src)
        px_from = "mrc" if px_src else None
    px = (px_src or 0) * b
    angles = [secs.get(z, {}).get("a") for z in range(nz)]
    times = [secs.get(z, {}).get("t") for z in range(nz)]
    write_mrc_u8(os.path.join(out, ts["stem"] + ".mrc"), u8, px or 1.0)
    meta = {"tool": f"tomo5_viewer.py {VERSION}", "made": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "source": os.path.basename(src), "sourceSize": st.st_size, "sourceMtime": int(st.st_mtime),
            "sourceShape": [nx, ny, nz], "bin": b, "shape": [bw, bh, nz], "pixelA": px or None,
            "sourcePixelA": px_src, "pixelFrom": px_from, "angles": angles, "times": times, "range": [float(lo), float(hi)]}
    if mp4 and exe:
        order = sorted(range(nz), key=lambda z: angles[z]) if all(a is not None for a in angles) else list(range(nz))
        vf = ["pad=ceil(iw/2)*2:ceil(ih/2)*2"]
        sigma = 0.187 * lp / px if lp and px else 0
        if sigma >= 0.5:
            vf.insert(0, f"gblur=sigma={sigma:.2f}")
        enc, args = ffmpeg_encoder(exe)
        dest = os.path.join(out, ts["stem"] + ".mp4")
        tmp = dest[:-4] + ".tmp.mp4"
        p = subprocess.Popen([exe, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{bw}x{bh}", "-r", str(fps), "-i", "-",
                              "-vf", ",".join(vf)] + args + ["-pix_fmt", "yuv420p", "-movflags", "+faststart", tmp],
                             stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        _, err = p.communicate(b"".join(u8[z].tobytes() for z in order))
        if p.returncode != 0:
            raise RuntimeError("ffmpeg: " + err.decode("utf-8", "replace")[-300:])
        os.replace(tmp, dest)
        meta["mp4"] = {"file": ts["stem"] + ".mp4", "order": "angle" if order != list(range(nz)) else "file", "fps": fps, "lowpassA": lp if sigma >= 0.5 else None}
    tmp = os.path.join(out, ts["stem"] + ".json.tmp")
    with open(tmp, "w") as f:
        json.dump(meta, f, indent=1)
    os.replace(tmp, os.path.join(out, ts["stem"] + ".json"))
    return time.time() - t0, st.st_size


def make_previews(a):
    if np is None:
        sys.exit("--make-previews needs numpy. Install it with:  pip install --user numpy imageio-ffmpeg")
    tiltdir = os.path.abspath(os.path.expanduser(a.make_previews))
    if not os.path.isdir(tiltdir):
        sys.exit(f"Not a folder: {a.make_previews}")
    out = os.path.abspath(os.path.expanduser(a.preview_out)) if a.preview_out else os.path.join(tiltdir, PREVIEW_DIR)
    os.makedirs(out, exist_ok=True)
    series = find_tilt_series(tiltdir, out)
    exe = ffmpeg_path() if a.mp4 else None
    if a.mp4 and not exe:
        print("ffmpeg not found: making the previews without .mp4. Install it with  pip install --user imageio-ffmpeg  (or pass --ffmpeg PATH).")
    todo = []
    for ts in series:
        jp = os.path.join(out, ts["stem"] + ".json")
        fresh = False
        if not a.force and os.path.isfile(jp) and os.path.isfile(os.path.join(out, ts["stem"] + ".mrc")):
            try:
                m = json.load(open(jp))
                st = os.stat(ts["path"])
                fresh = m.get("sourceSize") == st.st_size and m.get("sourceMtime") == int(st.st_mtime) and (not (a.mp4 and exe) or "mp4" in m)
            except Exception:
                fresh = False
        if not fresh:
            todo.append(ts)
    print(f"Tilt series in {tiltdir}: {len(series)}; previews to make: {len(todo)} (up to date: {len(series) - len(todo)})")
    print(f"Previews go to {out}")
    if not todo:
        return
    import concurrent.futures as cf
    t0, done, nbytes, fails = time.time(), 0, 0, []
    with cf.ThreadPoolExecutor(max_workers=max(1, a.workers)) as pool:
        futs = {pool.submit(make_preview, ts, out, a.preview_size, a.mp4, a.preview_lowpass, a.preview_fps, exe): ts for ts in todo}
        for fu in cf.as_completed(futs):
            ts = futs[fu]
            done += 1
            try:
                took, size = fu.result()
                nbytes += size
                el = time.time() - t0
                eta = el / done * (len(todo) - done)
                print(f"  [{done}/{len(todo)}] {ts['stem']}: {took:.1f} s ({size / 1e9:.2f} GB, {size / 1e6 / max(took, 1e-3):.0f} MB/s)"
                      f"  · all {a.workers} worker{'s' if a.workers != 1 else ''}: {nbytes / 1e6 / max(el, 1e-3):.0f} MB/s"
                      f"  · elapsed {el / 60:.1f} min, about {eta / 60:.1f} min left", flush=True)
            except Exception as e:
                fails.append(ts["stem"])
                print(f"  [{done}/{len(todo)}] {ts['stem']}: FAILED: {e}", flush=True)
    el = time.time() - t0
    rate = nbytes / 1e6 / max(el, 1e-3)
    if done >= 3 and nbytes > 2e9 and rate < 150:
        print(f"Note: the files were read at {rate:.0f} MB/s in total, so reading the disk or network share is what sets the time"
              f" (shrinking takes 1–2 s per series). On a network share, try --workers {min(8, a.workers * 2)};"
              " running it on the machine where the disks are local is fastest.")
    print(f"Done: {done - len(fails)} previews in {el / 60:.1f} min ({nbytes / 1e9:.1f} GB read, {nbytes / 1e6 / max(el, 1e-3):.0f} MB/s)"
          + (f"; failed: {', '.join(fails)}" if fails else ""))


# ---------- tilt-series star ratings (Movie Player): <base>/tiltseries_movie/tiltseries_ratings.xml ----------
RATINGS_FILE = "tiltseries_ratings.xml"
RATE_LOCK = threading.Lock()


def read_tilt_ratings(path):
    """{name: {"rating": n, "updated": "..."}} from a ratings file (missing file = none)."""
    import xml.etree.ElementTree as ET
    if not os.path.isfile(path):
        return {}
    out = {}
    for el in ET.parse(path).getroot().iter("TiltSeries"):
        try:
            r = int(el.get("rating", "0"))
        except ValueError:
            continue
        if el.get("name") and 0 < r <= 5:
            out[el.get("name")] = {"rating": r, "updated": el.get("updated", "")}
    return out


def rate_tilt(req):
    """Set (or clear, rating 0) the star rating of one tilt series. Writes only
    <base>/tiltseries_movie/tiltseries_ratings.xml, where base is a shared folder."""
    from xml.sax.saxutils import quoteattr
    base, _ = resolve(req["base"])
    if not base or not os.path.isdir(base):
        raise ValueError("unknown folder")
    name = os.path.basename(str(req["name"]))
    m = TS_RE.match(name)
    if not m:
        raise ValueError("not a tilt series name (Position_N[_k].mrc)")
    rating = int(req["rating"])
    if not 0 <= rating <= 5:
        raise ValueError("rating must be 0 to 5")
    folder = os.path.join(base, PREVIEW_DIR)
    path = os.path.join(folder, RATINGS_FILE)
    with RATE_LOCK:                                   # read, change, write: one at a time
        os.makedirs(folder, exist_ok=True)
        cur = read_tilt_ratings(path)
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        if rating:
            cur[name] = {"rating": rating, "updated": now}
        else:
            cur.pop(name, None)
        key = lambda n: tuple(int(x or 1) for x in TS_RE.match(n).groups()) if TS_RE.match(n) else (1 << 30, 0)
        lines = ['<?xml version="1.0" encoding="utf-8"?>',
                 f'<TiltSeriesRatings tool="tomo5_viewer.py {VERSION}" updated="{now}">',
                 "  <!-- Star ratings (1-5) of tilt series, set in the Tomo5 Data Viewer's Movie Player -->"]
        for n in sorted(cur, key=key):
            g = TS_RE.match(n).groups() if TS_RE.match(n) else (None, None)
            pos = f' position="{g[0]}" area="{g[1] or 1}"' if g[0] else ""
            lines.append(f'  <TiltSeries name={quoteattr(n)}{pos} rating="{cur[n]["rating"]}" updated={quoteattr(cur[n]["updated"])} />')
        lines.append("</TiltSeriesRatings>")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, path)
    return {"ratings": {n: v["rating"] for n, v in cur.items()}, "file": f"{req['base'].rstrip('/')}/{PREVIEW_DIR}/{RATINGS_FILE}"}


class Handler(BaseHTTPRequestHandler):
    server_version = f"tomo5Viewer/{VERSION}"

    def log_message(self, fmt, *args):
        if VERBOSE:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_err(self, status, msg):
        self.send_json({"error": msg}, status)

    def send_path(self, real, ctype="application/octet-stream"):
        size = os.path.getsize(real)
        start, end, status = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
            if m and (m.group(1) or m.group(2)):
                if m.group(1) == "":
                    start = max(0, size - int(m.group(2)))
                else:
                    start = int(m.group(1))
                    if m.group(2):
                        end = min(int(m.group(2)), size - 1)
            if not m or start >= size or start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(real, "rb") as f:
            f.seek(start)
            left = length
            while left > 0:
                buf = f.read(min(CHUNK, left))
                if not buf:
                    break
                self.wfile.write(buf)
                left -= len(buf)

    def host_refused(self):
        if host_ok(self.headers.get("Host")):
            return False
        self.send_err(403, f"Host '{self.headers.get('Host')}' is not allowed. Open the viewer as http://localhost:PORT, "
                           "or start the server with --allow-host NAME.")
        return True

    def do_POST(self):
        if self.host_refused():
            return
        url = urllib.parse.urlparse(self.path)
        try:
            if url.path.startswith("/api/movie/"):
                return self.api_movie(url.path[len("/api/movie/"):], urllib.parse.parse_qs(url.query))
            if url.path == "/api/rate-tilt":
                if READ_ONLY:
                    return self.send_err(403, "Ratings are off: the server was started with --read-only.")
                if not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
                    return self.send_err(415, "Send application/json.")
                req = json.loads((self.body(1 << 16) or b"{}").decode("utf-8"))
                return self.send_json(rate_tilt(req))
            if url.path != "/api/save":
                return self.send_err(404, "Unknown address.")
            if not EDIT:
                return self.send_err(403, "Saving is off. Start the server with --edit to save edits.")
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_SAVE:
                return self.send_err(413, "Save request is empty or too large.")
            req = json.loads(self.rfile.read(n).decode("utf-8"))
            return self.send_json(save_edits(req))
        except PermissionError as e:
            return self.send_err(403, str(e))
        except (ValueError, KeyError, TypeError) as e:
            return self.send_err(400, f"Bad request: {e}")
        except Exception as e:
            return self.send_err(500, f"{type(e).__name__}: {e}")

    # ---------- acquisition movies: the page sends JPEG frames, ffmpeg on this machine encodes them ----------
    def body(self, limit):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > limit:
            return None
        return self.rfile.read(n)

    def api_movie(self, action, q):
        arg = lambda k, d="": q.get(k, [d])[0]
        ctype = (self.headers.get("Content-Type") or "").lower()
        if action == "start":
            # JSON content type: a page from another site cannot start an encoder here (CORS preflight is never answered)
            if not ctype.startswith("application/json"):
                return self.send_err(415, "Send application/json.")
            exe = ffmpeg_path()
            if not exe:
                return self.send_err(501, "ffmpeg is not installed on the server.")
            try:
                o = json.loads((self.body(1 << 16) or b"{}").decode("utf-8"))
                w, h, fps = int(o.get("w", 1920)), int(o.get("h", 1080)), float(o.get("fps", 30))
            except Exception:
                return self.send_err(400, "Bad movie settings.")
            if not (64 <= w <= 4096 and 64 <= h <= 4096 and w % 2 == 0 and h % 2 == 0 and 1 <= fps <= 60):
                return self.send_err(400, "Movie size must be even, 64 to 4096 px, at 1 to 60 frames per second.")
            movie_sweep()
            if sum(1 for m in MOVIES.values() if not m.get("done")) >= 2:
                return self.send_err(429, "Two movies are already being encoded on this server.")
            d = tempfile.mkdtemp(prefix="tomo5_movie_")
            out, log = os.path.join(d, "movie.mp4"), open(os.path.join(d, "ffmpeg.log"), "wb")
            enc, args = ffmpeg_encoder(exe)
            proc = subprocess.Popen([exe, "-y", "-loglevel", "error", "-f", "image2pipe", "-c:v", "mjpeg", "-framerate", f"{fps:g}", "-i", "-"]
                                    + args + ["-r", f"{fps:g}", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out],
                                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
            mid = secrets.token_urlsafe(18)
            with MOVIE_LOCK:
                MOVIES[mid] = {"proc": proc, "dir": d, "out": out, "log": log, "n": 0, "t": time.time(), "lock": threading.Lock()}
            return self.send_json({"id": mid, "encoder": enc})
        m = MOVIES.get(arg("id"))
        if not m:
            return self.send_err(404, "Unknown movie.")
        m["t"] = time.time()

        def why():
            try:
                m["log"].flush()
                return open(os.path.join(m["dir"], "ffmpeg.log"), "rb").read()[-400:].decode("utf-8", "replace").strip()
            except Exception:
                return ""
        if action == "frame":
            if not ctype.startswith("image/jpeg"):
                return self.send_err(415, "Send each frame as image/jpeg.")
            data = self.body(MOVIE_MAX_FRAME)
            if not data or data[:2] != b"\xff\xd8":
                return self.send_err(400, "Not a JPEG frame.")
            if m.get("done") or m["n"] >= MOVIE_MAX_FRAMES:
                return self.send_err(400, "This movie takes no more frames.")
            try:
                with m["lock"]:
                    m["proc"].stdin.write(data)
                    m["n"] += 1
            except (BrokenPipeError, OSError):
                return self.send_err(500, "ffmpeg stopped: " + (why() or "no message"))
            return self.send_json({"n": m["n"]})
        if action == "end":
            try:
                m["proc"].stdin.close()
            except Exception:
                pass
            try:
                code = m["proc"].wait(timeout=900)
            except subprocess.TimeoutExpired:
                movie_drop(arg("id"))
                return self.send_err(500, "ffmpeg did not finish.")
            if code != 0 or not os.path.isfile(m["out"]):
                msg = why()
                movie_drop(arg("id"))
                return self.send_err(500, "ffmpeg failed: " + (msg or f"exit code {code}"))
            m["done"] = True
            return self.send_json({"frames": m["n"], "size": os.path.getsize(m["out"])})
        if action == "cancel":
            movie_drop(arg("id"))
            return self.send_json({"ok": True})
        return self.send_err(404, "Unknown address.")

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if self.host_refused():
            return
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        arg = lambda k, d="": q.get(k, [d])[0]
        try:
            if url.path in ("/", "/index.html"):
                if not HTML_PATH or not os.path.isfile(HTML_PATH):
                    return self.send_err(404, "index.html not found. Put it next to tomo5_viewer.py or pass --html.")
                return self.send_path(HTML_PATH, "text/html; charset=utf-8")
            if url.path == "/api/info":
                return self.send_json({"app": APP, "version": VERSION, "numpy": np is not None,
                                       "roots": [{"name": n, "path": r} for n, r in ROOTS.items()],
                                       "edit": EDIT, "editDirs": EDIT_DIRS, "ffmpeg": bool(ffmpeg_path()),
                                       "readOnly": READ_ONLY, "ratingsFile": f"{PREVIEW_DIR}/{RATINGS_FILE}"})
            if url.path == "/api/list":
                return self.api_list(arg("path"))
            if url.path == "/api/section":
                if np is None:
                    return self.send_err(501, "numpy is not installed on the server.")
                real, _ = resolve(arg("path"))
                if not real or not os.path.isfile(real):
                    return self.send_err(404, "Not found.")
                body, bw, bh = read_binned_section(real, int(arg("z", "0")), max(1, int(arg("bin", "1"))))
                extra = {}
                if arg("fmt") == "u16":
                    # half the bytes of float32: 65536 levels between the min and max, plenty for display
                    a = np.frombuffer(body, dtype="<f4")
                    fin = a[np.isfinite(a)]
                    lo, hi = (float(fin.min()), float(fin.max())) if fin.size else (0.0, 1.0)
                    span = hi - lo if hi > lo else 1.0
                    body = np.clip(np.nan_to_num((a - lo) * (65535.0 / span), nan=0.0), 0, 65535).round().astype("<u2").tobytes()
                    extra = {"X-Lo": repr(lo), "X-Hi": repr(lo + span)}
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Bw", str(bw))
                self.send_header("X-Bh", str(bh))
                for k, v in extra.items():
                    self.send_header(k, v)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)
                return
            if url.path == "/api/movie/file":
                m = MOVIES.get(arg("id"))
                if not m or not m.get("done") or not os.path.isfile(m["out"]):
                    return self.send_err(404, "That movie is not available (any more).")
                m["t"] = time.time()
                return self.send_path(m["out"], "video/mp4")
            if url.path == "/api/file":
                real, _ = resolve(arg("path"))
                if not real or not os.path.isfile(real):
                    return self.send_err(404, "Not found.")
                low = real.lower()
                ctype = ("text/xml; charset=utf-8" if low.endswith(".xml") else
                         "text/plain; charset=utf-8" if low.endswith((".mdoc", ".txt")) else
                         "image/jpeg" if low.endswith((".jpg", ".jpeg")) else
                         "application/octet-stream")
                return self.send_path(real, ctype)
            return self.send_err(404, "Unknown address.")
        except PermissionError as e:
            return self.send_err(403, str(e))
        except FileNotFoundError as e:
            return self.send_err(404, str(e))
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as e:  # report instead of dropping the connection
            try:
                return self.send_err(500, f"{type(e).__name__}: {e}")
            except Exception:
                return

    def api_list(self, path):
        """List one folder: its subfolders and files. The empty path lists the shared roots."""
        real, api = resolve(path)
        if real is None:
            return self.send_json({"path": "", "dirs": [{"name": n} for n in ROOTS], "files": []})
        if not os.path.isdir(real):
            return self.send_err(404, "Not a folder.")
        dirs, files = [], []
        with os.scandir(real) as it:
            for e in it:
                if e.name.startswith("."):
                    continue
                try:
                    if e.is_dir():
                        dirs.append({"name": e.name})
                    elif e.is_file():
                        files.append({"name": e.name, "size": e.stat().st_size})
                except OSError:
                    continue
        key = lambda d: d["name"].lower()
        return self.send_json({"path": api, "dirs": sorted(dirs, key=key), "files": sorted(files, key=key)})


def save_edits(req):
    """Write edited files into the session's edit folders:
         batch -> <session>/<EDIT_DIRS["batch"]>/  (BatchPositionsList.xml, edit_session.json)
         maps  -> <session>/<EDIT_DIRS["maps"]>/   (<map folder>/SearchMap.dm with the new rating)
    A save is one snapshot: whatever was saved before in either folder is moved into
    <folder>/history/<time>/ first. Tomo5's own files are never written."""
    session, _ = resolve(req["session"])
    if not session or not os.path.isdir(session):
        raise ValueError("unknown session folder")
    roots = {}
    for t, d in EDIT_DIRS.items():
        r = os.path.normpath(os.path.join(session, *d.split("/")))
        if not inside(r, session) or r == os.path.normpath(session):
            raise PermissionError("edit folders must be inside the session folder")
        roots[t] = r
    files = req["files"]
    if not isinstance(files, list) or not files:
        raise ValueError("no files to save")
    for f in files:
        t, rel = f.get("target"), str(f.get("path", "")).replace("\\", "/")
        if t not in SAVE_OK or not SAVE_OK[t].match(rel):
            raise PermissionError(f"not allowed to write {t}:{rel!r}")
        f["path"] = rel
        if not isinstance(f.get("content"), str):
            raise ValueError(f"content of {rel} must be text")
    stamp, histories = time.strftime("%Y%m%d_%H%M%S"), {}
    for t, root in roots.items():               # keep the previous save
        if not os.path.isdir(root):
            continue
        previous = [e for e in os.listdir(root) if e != "history"]
        if not previous:
            continue
        base = os.path.join(root, "history", stamp)
        history, k = base, 2
        while os.path.exists(history):
            history = f"{base}_{k}"; k += 1
        os.makedirs(history)
        for e in previous:
            shutil.move(os.path.join(root, e), os.path.join(history, e))
        histories[t] = os.path.relpath(history, session).replace(os.sep, "/")
    saved = []
    for f in files:
        root = roots[f["target"]]
        dest = os.path.normpath(os.path.join(root, *f["path"].split("/")))
        if not inside(dest, root):
            raise PermissionError("path outside the edit folder")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(f["content"])
        os.replace(tmp, dest)            # atomic: a half-written file never replaces a good one
        saved.append(f"{EDIT_DIRS[f['target']]}/{f['path']}")
    print(f"  saved {len(saved)} file(s) in {session}: " + ", ".join(saved))
    return {"saved": saved, "editDirs": EDIT_DIRS, "history": histories}


def main():
    global HTML_PATH, VERBOSE, EDIT, FFMPEG, READ_ONLY
    ap = argparse.ArgumentParser(description="Serve Tomo5 session folders to the Tomo5 Data Viewer.")
    ap.add_argument("roots", nargs="*", help="folders to share")
    ap.add_argument("--port", type=int, default=8766, help="port to listen on (default 8766); if it is busy the next free one is used, like Jupyter")
    ap.add_argument("--port-retries", type=int, default=50, help="how many following ports to try when the port is busy (default 50; 0 = fail instead)")
    ap.add_argument("--host", default="127.0.0.1", help="127.0.0.1 (default, this computer only) or 0.0.0.0")
    ap.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                    help="also accept requests addressed to this host name (e.g. behind a proxy); localhost, IP addresses and this machine's names are always accepted")
    ap.add_argument("--html", help="path to index.html (default: next to this script)")
    ap.add_argument("--verbose", action="store_true", help="log every request")
    ap.add_argument("--read-only", action="store_true", help=f"write nothing at all (by default the Movie Player saves star ratings to {PREVIEW_DIR}/tiltseries_ratings.xml)")
    pv = ap.add_argument_group("tilt-series previews (run once, then the Movie Player starts instantly)")
    pv.add_argument("--make-previews", metavar="TILTDIR", help=f"make 8-bit previews of every Position_N[_k].mrc in TILTDIR into TILTDIR/{PREVIEW_DIR}, then exit")
    pv.add_argument("--preview-out", metavar="DIR", help=f"where the previews go (default: TILTDIR/{PREVIEW_DIR})")
    pv.add_argument("--preview-size", type=int, default=512, help="largest preview side in px (default 512)")
    pv.add_argument("--mp4", action="store_true", help="also write a <stem>.mp4 movie per tilt series (by angle, needs ffmpeg)")
    pv.add_argument("--preview-fps", type=int, default=10, help="frames per second of the .mp4 (default 10)")
    pv.add_argument("--preview-lowpass", type=float, default=60, help="low-pass of the .mp4 in Å, 0 = off (default 60)")
    pv.add_argument("--workers", type=int, default=2, help="tilt series processed at once (default 2; use 1 on a slow network share)")
    pv.add_argument("--force", action="store_true", help="remake previews that are up to date")
    ap.add_argument("--ffmpeg", default="auto", help="ffmpeg used to encode acquisition movies: a path, 'auto' (default: look on PATH) or 'none'")
    # experimental edit mode: not listed in --help yet
    ap.add_argument("--edit", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--edit-dir", default=EDIT_DIRS["batch"], help=argparse.SUPPRESS)        # batch positions
    ap.add_argument("--edit-maps-dir", default=EDIT_DIRS["maps"], help=argparse.SUPPRESS)    # search-map ratings
    a = ap.parse_args()

    for r in a.roots:
        full = os.path.abspath(os.path.expanduser(r))
        if not os.path.isdir(full):
            sys.exit(f"Not a folder: {r}")
        name = os.path.basename(os.path.normpath(full)) or full
        base, i = name, 2
        while name in ROOTS:
            name, i = f"{base}_{i}", i + 1
        ROOTS[name] = full
    HTML_PATH = os.path.abspath(a.html) if a.html else os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    VERBOSE = a.verbose
    EDIT = a.edit
    FFMPEG = a.ffmpeg
    READ_ONLY = a.read_only
    names = {a.host} | set(a.allow_host)
    for get in (socket.gethostname, socket.getfqdn):
        try:
            names.add(get())
        except Exception:
            pass
    ALLOWED_HOSTS.update(n.lower().rstrip(".") for n in names if n)
    if READ_ONLY and EDIT:
        sys.exit("--read-only and --edit cannot be used together.")
    if a.make_previews:
        return make_previews(a)
    if not a.roots:
        ap.error("give at least one folder to share (or --make-previews TILTDIR)")
    import atexit
    atexit.register(lambda: [movie_drop(k) for k in list(MOVIES)])     # no temporary movie folders left behind
    for t, v in (("batch", a.edit_dir), ("maps", a.edit_maps_dir)):
        v = v.replace("\\", "/").strip("/")
        if not v or ".." in v.split("/"):
            sys.exit("edit folders must stay inside the session folder")
        EDIT_DIRS[t] = v

    # like Jupyter: if the port is busy, take the next free one (--port-retries)
    srv, port = None, a.port
    for port in range(a.port, a.port + max(0, a.port_retries) + 1):
        try:
            srv = ThreadingHTTPServer((a.host, port), Handler)
            break
        except OSError as e:
            if e.errno not in (errno.EADDRINUSE, errno.EACCES) or port >= a.port + max(0, a.port_retries):
                sys.exit(f"Cannot listen on {a.host}:{port}: {e.strerror or e}. Try another --port.")
            print(f"The port {port} is already in use, trying another port.")
    a.port = port
    print(f"Tomo5 Data Viewer {VERSION} on http://{'localhost' if a.host in ('127.0.0.1', '0.0.0.0') else a.host}:{a.port}")
    print(f"  Tested with {TESTED_WITH}")
    if EDIT:
        print(f"  EDIT MODE (experimental): batch positions are saved to <session>/{EDIT_DIRS['batch']}, "
              f"star ratings to <session>/{EDIT_DIRS['maps']}; Tomo5's files are not changed.")
    for n, r in ROOTS.items():
        print(f"  sharing  {n}  ->  {r}")
    print("  ratings  " + ("off (--read-only): nothing is written" if READ_ONLY else
                           f"tilt-series stars are saved to {PREVIEW_DIR}/{RATINGS_FILE} (next to the tilt series); nothing else is written"))
    exe = ffmpeg_path()
    print("  movies   " + (f"encoded with {exe} ({ffmpeg_encoder(exe)[0]})" if exe else
                             "ffmpeg off (--ffmpeg none): the browser encodes them (.webm)" if FFMPEG == "none" else
                             "ffmpeg not found (pass --ffmpeg PATH): the browser encodes them (.webm)"))
    missing = (["numpy"] if np is None else []) + ([] if exe or FFMPEG != "auto" else ["imageio-ffmpeg"])
    if np is None:
        print("  numpy not found: large images are sent in full and shrunk in the browser (slower).")
    if missing:
        print(f"  To add what is missing (no admin rights needed):  pip install --user {' '.join(missing)}")
    # like Jupyter: tell the user how to reach this server from their own computer
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    host = "this-workstation"
    for get in (socket.getfqdn, socket.gethostname):
        try:
            h = get()
        except Exception:
            continue
        if h and not h.startswith("localhost"):
            host = h
            break
    target = "localhost" if a.host in ("127.0.0.1", "0.0.0.0", "localhost") else a.host
    print("To view it from your own computer, run there:")
    print(f"  ssh -N -L {a.port}:{target}:{a.port} {user}@{host}")
    print(f"then open http://localhost:{a.port}")
    print("Press Ctrl+C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
