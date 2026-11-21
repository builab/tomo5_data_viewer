#!/usr/bin/env python3
"""
tomo5_viewer.py - serve Tomo5 session folders to the Tomo5 Data Viewer (index.html).

The viewer page (index.html) must sit next to this script, or be given with --html.
Only the folders you list are reachable, and the server never writes anything.

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
import socket
import struct
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import numpy as np
except ImportError:
    np = None

APP = "tomo5-data-viewer"
VERSION = "0.4"
TESTED_WITH = "Tomo5 5.26"
CHUNK = 1 << 20          # 1 MiB per write when streaming a file

ROOTS = {}               # name -> absolute path
HTML_PATH = None
VERBOSE = False


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
                                       "roots": [{"name": n, "path": r} for n, r in ROOTS.items()]})
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


def main():
    global HTML_PATH, VERBOSE
    ap = argparse.ArgumentParser(description="Serve Tomo5 session folders to the Tomo5 Data Viewer.")
    ap.add_argument("roots", nargs="+", help="folders to share (read-only)")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--host", default="127.0.0.1", help="127.0.0.1 (default, this computer only) or 0.0.0.0")
    ap.add_argument("--html", help="path to index.html (default: next to this script)")
    ap.add_argument("--verbose", action="store_true", help="log every request")
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

    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"Tomo5 Data Viewer {VERSION} on http://{'localhost' if a.host in ('127.0.0.1', '0.0.0.0') else a.host}:{a.port}")
    print(f"  Tested with {TESTED_WITH}")
    for n, r in ROOTS.items():
        print(f"  sharing  {n}  ->  {r}")
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
