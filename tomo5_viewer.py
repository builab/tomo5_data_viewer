#!/usr/bin/env python3
"""
tomo5_viewer.py - serve Tomo5 session folders to the Tomo5 Data Viewer (index.html).

The viewer page (index.html) must sit next to this script, or be given with --html.
Only the folders you list are reachable, and the server never writes into them.
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
import getpass
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
VERSION = "0.7"
TESTED_WITH = "Tomo5 5.26"
CHUNK = 1 << 20          # 1 MiB per write when streaming a file

ROOTS = {}               # name -> absolute path
HTML_PATH = None
VERBOSE = False
EDIT = False                    # --edit: allow saving edits (experimental)
EDIT_DIRS = {"batch": "Batch/Edit", "maps": "SearchMaps/Edit"}   # where edits go, relative to the session folder
MAX_SAVE = 32 << 20             # largest save request accepted
FFMPEG = "auto"                 # --ffmpeg: path, "auto" (look on PATH) or "none"
MOVIES = {}                     # id -> {"proc", "dir", "out", "log", "n", "t"}: movies being encoded by ffmpeg
MOVIE_LOCK = threading.Lock()
MOVIE_MAX_FRAMES = 30000        # about 16 minutes at 30 frames per second
MOVIE_MAX_FRAME = 16 << 20
# files a save may write, relative to the edit folder
SAVE_OK = {"batch": re.compile(r"^(BatchPositionsList\.xml|edit_session\.json)$"),
           "maps": re.compile(r"^SearchMap_\d+_\d{8}_\d{6}/SearchMap\.dm$")}


def ffmpeg_path():
    if FFMPEG == "none":
        return None
    if FFMPEG != "auto":
        return FFMPEG if os.path.isfile(FFMPEG) else shutil.which(FFMPEG)
    exe = shutil.which("ffmpeg")
    if not exe:
        try:                                   # the copy that comes with the imageio-ffmpeg Python package
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            exe = None
    return exe


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
    a = a[:bh * binf, :bw * binf].astype(np.float32).reshape(bh, binf, bw, binf).mean(axis=(1, 3))
    return a.astype("<f4").tobytes(), bw, bh


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

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        try:
            if url.path.startswith("/api/movie/"):
                return self.api_movie(url.path[len("/api/movie/"):], urllib.parse.parse_qs(url.query))
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
            return self.send_err(400, f"Bad save request: {e}")
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
                                       "edit": EDIT, "editDirs": EDIT_DIRS, "ffmpeg": bool(ffmpeg_path())})
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
    global HTML_PATH, VERBOSE, EDIT, FFMPEG
    ap = argparse.ArgumentParser(description="Serve Tomo5 session folders to the Tomo5 Data Viewer.")
    ap.add_argument("roots", nargs="+", help="folders to share (read-only)")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--host", default="127.0.0.1", help="127.0.0.1 (default, this computer only) or 0.0.0.0")
    ap.add_argument("--html", help="path to index.html (default: next to this script)")
    ap.add_argument("--verbose", action="store_true", help="log every request")
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
    import atexit
    atexit.register(lambda: [movie_drop(k) for k in list(MOVIES)])     # no temporary movie folders left behind
    for t, v in (("batch", a.edit_dir), ("maps", a.edit_maps_dir)):
        v = v.replace("\\", "/").strip("/")
        if not v or ".." in v.split("/"):
            sys.exit("edit folders must stay inside the session folder")
        EDIT_DIRS[t] = v

    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"Tomo5 Data Viewer {VERSION} on http://{'localhost' if a.host in ('127.0.0.1', '0.0.0.0') else a.host}:{a.port}")
    print(f"  Tested with {TESTED_WITH}")
    if EDIT:
        print(f"  EDIT MODE (experimental): batch positions are saved to <session>/{EDIT_DIRS['batch']}, "
              f"star ratings to <session>/{EDIT_DIRS['maps']}; Tomo5's files are not changed.")
    for n, r in ROOTS.items():
        print(f"  sharing  {n}  ->  {r}")
    exe = ffmpeg_path()
    print("  movies   " + (f"encoded with {exe} ({ffmpeg_encoder(exe)[0]})" if exe else "ffmpeg not found (pass --ffmpeg PATH): the browser encodes them (.webm)"))
    if np is None:
        print("  numpy not found: large images are sent in full and shrunk in the browser.")
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
