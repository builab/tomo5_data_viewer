#!/usr/bin/env python3
"""Read Thermo Fisher Tomography 5 (Tomo5) session data: image XMLs, MRC files (with the FEI
extended header), .mdoc files, the batch-position list, search-map tiles, star ratings, and
stitch a search map. Python 3.7+ standard library; numpy for images.

Same rules as the Tomo5 Data Viewer (index.html / tomo5_viewer.py); the format is described in
tomo5_data_struct.md. Units: stage positions in µm, pixel sizes in µm per saved pixel unless named
otherwise, angles in degrees.

As a module:  import tomo5_io as t5;  t5.batch_positions("/data/Session_X", tilt_dir=...)
As a tool:    python3 tomo5_io.py --help
"""
import csv
import glob
import json
import math
import os
import re
import struct
import sys
import xml.etree.ElementTree as ET
from datetime import datetime

try:
    import numpy as np
except ImportError:                  # XML, .mdoc and batch lists work without numpy; images need it
    np = None

UNIT = {"m": 1, "mm": 1e-3, "µm": 1e-6, "um": 1e-6, "nm": 1e-9, "Å": 1e-10, "A": 1e-10}
MAP_RE = re.compile(r"^SearchMap_(\d+)_(\d{8}_\d{6})$", re.I)
TS_RE = re.compile(r"^Position_(\d+)(?:_(\d+))?\.mrc$", re.I)
MRC_DTYPES = {0: "u1", 1: "<i2", 2: "<f4", 6: "<u2", 12: "<f2"}


# ---------------------------------------------------------------- XML (image XMLs, SearchMap.dm, …)
def _local(tag):
    return tag.rsplit("}", 1)[-1]


def flatten_xml(source):
    """[(path, text)] of every leaf, namespaces dropped. Repeated siblings get [1], [2], …; a
    <Key>/<Value> pair becomes path/<key text>. Accepts a file path or XML text."""
    if source.lstrip().startswith("<"):
        text = source
    else:
        with open(source, encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read()
    root = ET.fromstring(text.encode("utf-8"))
    rows = []

    def walk(el, path):
        ch = list(el)
        if not ch:
            rows.append((path, (el.text or "").strip()))
            return
        names = [_local(c.tag) for c in ch]
        lk = [n.lower() for n in names]
        if len(ch) == 2 and "key" in lk and "value" in lk:
            v = ch[lk.index("value")]
            key = f"{path}/{(ch[lk.index('key')].text or '').strip()}"
            if len(v):
                walk(v, key)
            else:
                rows.append((key, (v.text or "").strip()))
            return
        count, seen = {}, {}
        for n in names:
            count[n] = count.get(n, 0) + 1
        for c, n in zip(ch, names):
            seen[n] = seen.get(n, 0) + 1
            walk(c, (f"{path}/" if path else "") + n + (f"[{seen[n]}]" if count[n] > 1 else ""))

    walk(root, "")
    return rows


def _find(rows, pattern):
    """First value whose path matches; CustomData holds copies of other settings, so it comes last."""
    rx = re.compile(pattern, re.I)
    for skip_custom in (True, False):
        for p, v in rows:
            if rx.search(p) and not (skip_custom and p.startswith("CustomData/")):
                return v
    return None


def _num(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def image_info(xml_path):
    """What the viewer reads from a Tomo5 image XML (Atlas_*.xml, Overview.xml, Tile_*.xml,
    SearchMap.xml, Position_N_Search.xml, …)."""
    rows = flatten_xml(xml_path)
    f = lambda pat: _num(_find(rows, pat))
    unit = _find(rows, r"pixelSize/x/unit/[^/]*symbol[^/]*$") or "m"
    px = f(r"pixelSize/x/numericValue$")
    m = [f(rf"^ReferenceTransformation/matrix/_m{k}$") for k in ("11", "12", "21", "22")]
    R = None
    if all(v is not None for v in m):
        n = math.sqrt(abs(m[0] * m[3] - m[1] * m[2]))
        if n > 0:     # direction only (rotation + flip), and NEGATED: see tomo5_data_struct.md
            R = [[-m[0] / n, -m[1] / n], [-m[2] / n, -m[3] / n]]
    sx, sy, a = f(r"stage/Position/X$"), f(r"stage/Position/Y$"), f(r"stage/Position/A$")
    return {
        "stage_um": (sx * 1e6, sy * 1e6) if sx is not None and sy is not None else None,
        "tilt_deg": math.degrees(a) if a is not None else None,
        "pixel_um": px * UNIT.get(unit, 1) * 1e6 if px else None,     # per pixel of the SAVED image
        "R": R,
        "magnification": _find(rows, r"NominalMagnification$"),
        "defocus_um": (lambda d: d * 1e6 if d is not None and abs(d) < 1e-3 else d)(f(r"AppliedDefocus$")),
        "exposure_s": f(r"camera/ExposureTime$"),
        "camera": _find(rows, r"camera/Name$"),
        "date": _find(rows, r"acquisitionDateTime$"),
        "binning": f(r"Binning/x$"),
    }


# ---------------------------------------------------------------- MRC
def mrc_header(path):
    """nx, ny, nz, mode, nxt (extended header bytes), dtype, cell_pixel_a (None when unset)."""
    with open(path, "rb") as fh:
        h = fh.read(1024)
    nx, ny, nz, mode = struct.unpack("<4i", h[:16])
    nxt = max(0, struct.unpack("<i", h[92:96])[0])
    mx, xlen = struct.unpack("<i", h[28:32])[0], struct.unpack("<f", h[40:44])[0]
    cell = xlen / mx if mx > 0 and xlen > 0 else None
    signed = mode == 0 and (h[152] & 1 if len(h) > 152 else 0)    # imodFlags bit 0: signed bytes
    dt = ("i1" if signed else "u1") if mode == 0 else MRC_DTYPES.get(mode)
    return {"nx": nx, "ny": ny, "nz": nz, "mode": mode, "nxt": nxt, "dtype": dt,
            "cell_pixel_a": cell if cell and abs(cell - 1) > 1e-6 else None}


def _fei_blocks(path, h=None):
    """FEI extended header: (block size, raw bytes of nz blocks) or None. One block per section."""
    h = h or mrc_header(path)
    if h["nxt"] < 480:
        return None
    with open(path, "rb") as fh:
        fh.seek(1024)
        blk = struct.unpack("<i", fh.read(4))[0]
        if blk < 164 or blk * h["nz"] > h["nxt"]:
            return None
        fh.seek(1024)
        return blk, fh.read(blk * h["nz"])


def mrc_pixel_a(path):
    """Pixel size in Å of the saved image: FEI extended header (byte 156, metres), else the MRC cell."""
    h = mrc_header(path)
    fei = _fei_blocks(path, h)
    if fei and struct.unpack("<i", fei[1][4:8])[0] >= 2:
        px = struct.unpack("<d", fei[1][156:164])[0] * 1e10
        if 0.01 < px < 1e5:
            return px
    return h["cell_pixel_a"]


def fei_section_tilts(path):
    """Stage tilt (alpha, degrees, byte 100) of every section from the FEI header, or None when
    absent or not varying. This is the authoritative angle of each section of a Tomo5 stack."""
    h = mrc_header(path)
    fei = _fei_blocks(path, h)
    if not fei or h["nz"] < 2:
        return None
    blk, e = fei
    out = []
    for z in range(h["nz"]):
        ver = struct.unpack("<i", e[z * blk + 4:z * blk + 8])[0]
        out.append(struct.unpack("<d", e[z * blk + 100:z * blk + 108])[0] if ver >= 1 else float("nan"))
    if all(abs(a) <= 91 for a in out) and max(out) - min(out) > 1:
        return out
    return None


def read_section(path, z=0, bin=1):
    """Section z as float32 (ny // bin, nx // bin), averaged over bin × bin. Skips the FEI header."""
    if np is None:
        raise RuntimeError("numpy is needed to read images (pip install numpy)")
    h = mrc_header(path)
    if not h["dtype"]:
        raise ValueError(f"MRC mode {h['mode']} is not supported")
    dt = np.dtype(h["dtype"])
    n = h["nx"] * h["ny"]
    a = np.fromfile(path, dtype=dt, count=n, offset=1024 + h["nxt"] + z * n * dt.itemsize).reshape(h["ny"], h["nx"])
    if bin > 1:
        bw, bh = h["nx"] // bin, h["ny"] // bin
        acc = np.int64 if dt.kind in "iu" else np.float64
        a = a[:bh * bin, :bw * bin].reshape(bh, bin, bw * bin).sum(axis=1, dtype=acc).reshape(bh, bw, bin).sum(axis=2) / (bin * bin)
    return a.astype(np.float32)


def write_mrc(path, img, pixel_a=1.0):
    """float32 2-D image to MRC (mode 2) with the pixel size in the header."""
    img = np.ascontiguousarray(img, dtype="<f4")
    ny, nx = img.shape
    h = bytearray(1024)
    struct.pack_into("<10i", h, 0, nx, ny, 1, 2, 0, 0, 0, nx, ny, 1)
    struct.pack_into("<3f3f3i", h, 40, nx * pixel_a, ny * pixel_a, pixel_a, 90, 90, 90, 1, 2, 3)
    struct.pack_into("<3f", h, 76, float(img.min()), float(img.max()), float(img.mean()))
    h[208:212] = b"MAP "
    h[212:216] = bytes([0x44, 0x44, 0, 0])
    with open(path, "wb") as fh:
        fh.write(bytes(h))
        fh.write(img.tobytes())


# ---------------------------------------------------------------- geometry
class Geo:
    """Where an image sits on the stage: stage = S − (−R) · ps · (pixel − centre) as Tomo5 draws it,
    written here with the already-negated R:  stage = S + ps · R · (pixel − centre).
    S: stage of the image centre (µm); R: 2×2 direction (from image_info); ps: µm per pixel of the
    saved image; nx, ny: saved image size. Pixel (0, 0) is the top-left corner of the image."""

    def __init__(self, S, R, ps, nx, ny):
        self.S, self.R, self.ps, self.nx, self.ny = tuple(S), R or [[1, 0], [0, 1]], ps, nx, ny

    def pixel_to_stage(self, x, y):
        dx, dy = (x - self.nx / 2) * self.ps, (y - self.ny / 2) * self.ps
        return (self.S[0] + self.R[0][0] * dx + self.R[0][1] * dy, self.S[1] + self.R[1][0] * dx + self.R[1][1] * dy)

    def stage_to_pixel(self, sx, sy):
        (a, b), (c, d) = self.R
        det = a * d - b * c
        ux, uy = sx - self.S[0], sy - self.S[1]
        dx, dy = (d * ux - b * uy) / det, (-c * ux + a * uy) / det
        return (self.nx / 2 + dx / self.ps, self.ny / 2 + dy / self.ps)

    def corners(self):
        return [self.pixel_to_stage(x, y) for x, y in ((0, 0), (self.nx, 0), (self.nx, self.ny), (0, self.ny))]

    def __repr__(self):
        return f"Geo(S=({self.S[0]:.3f}, {self.S[1]:.3f}) µm, ps={self.ps:.6g} µm, {self.nx}×{self.ny})"


def image_geo(xml_path, mrc_path):
    """Geo of an image from its XML (stage, direction, pixel size) and MRC (size; pixel size fallback)."""
    info = image_info(xml_path) if xml_path and os.path.isfile(xml_path) else {}
    h = mrc_header(mrc_path)
    ps = info.get("pixel_um") or ((mrc_pixel_a(mrc_path) or 0) * 1e-4) or 1.0
    return Geo(info.get("stage_um") or (0, 0), info.get("R"), ps, h["nx"], h["ny"])


# ---------------------------------------------------------------- .mdoc and tilt series
def read_mdoc(path):
    """{"head": {key: value}, "sections": [{"z": ZValue, "tilt": angle, "rows": {key: value}}]}.
    In Tomo5 the ZValue is the ACQUISITION index, not the stack section: use tilt_sections()."""
    head, secs, cur = {}, [], None
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    for line in lines:
        m = re.match(r"\s*\[\s*ZValue\s*=\s*(\d+)\s*\]", line, re.I)
        if m:
            cur = {"z": int(m.group(1)), "rows": {}}
            secs.append(cur)
            continue
        if line.lstrip().startswith("["):
            cur = None
            continue
        kv = re.match(r"\s*([^=]+?)\s*=\s*(.*?)\s*$", line)
        if kv:
            (cur["rows"] if cur is not None else head)[kv.group(1)] = kv.group(2)
    for s in secs:
        s["tilt"] = _num(s["rows"].get("TiltAngle"))
    return {"head": head, "sections": secs}


def mdoc_time(v):
    """'20-Nov-26  12:10:00' (SerialEM/Tomo5 DateTime) → datetime, or None."""
    m = re.search(r"(\d{1,2})-(\w{3})-(\d{2,4})\s+(\d{1,2}):(\d{2}):(\d{2})", v or "")
    if not m:
        return None
    y = int(m.group(3))
    return datetime(2000 + y if y < 100 else y, ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"].index(m.group(2).lower()) + 1,
                    int(m.group(1)), int(m.group(4)), int(m.group(5)), int(m.group(6)))


def tilt_sections(mrc_path, mdoc_path=None):
    """Angle of every section z of a tilt series, and its .mdoc block. Tomo5 saves the stack sorted
    by angle (−60 … +60) but the .mdoc in acquisition order ([ZValue = 0] 0°, [ZValue = 1] −3°, …).
    1. FEI header angles, each paired with the .mdoc block of the nearest angle (≤ 1.5°);
    2. else the .mdoc: ZValue = z when its angles are monotonic in ZValue, otherwise sorted by angle.
    Returns ([{"z", "angle", "mdoc_z", "time", "dose", "defocus", "rows"}], source)."""
    nz = mrc_header(mrc_path)["nz"]
    md = read_mdoc(mdoc_path) if mdoc_path and os.path.isfile(mdoc_path) else {"sections": []}
    ms = [s for s in md["sections"] if s["tilt"] is not None]

    def entry(z, a, s):
        r = s["rows"] if s else {}
        return {"z": z, "angle": a, "mdoc_z": s["z"] if s else None, "time": mdoc_time(r.get("DateTime")),
                "dose": _num(r.get("ExposureDose")), "defocus": _num(r.get("Defocus")), "rows": r}

    fei = fei_section_tilts(mrc_path)
    if fei:
        used, out = set(), []
        for z, a in enumerate(fei):
            best = min((s for s in ms if id(s) not in used and abs(s["tilt"] - a) <= 1.5), key=lambda s: abs(s["tilt"] - a), default=None)
            if best:
                used.add(id(best))
            out.append(entry(z, a, best))
        return out, "FEI header"
    if nz > 1 and len(ms) == nz:
        by_z = sorted(ms, key=lambda s: s["z"])
        d = [b["tilt"] - a["tilt"] for a, b in zip(by_z, by_z[1:])]
        if all(x >= 0 for x in d) or all(x <= 0 for x in d):
            return [entry(z, s["tilt"], s) for z, s in enumerate(by_z)], "mdoc"
        return [entry(z, s["tilt"], s) for z, s in enumerate(sorted(ms, key=lambda s: s["tilt"]))], "mdoc, sorted by angle"
    by = {s["z"]: s for s in ms}
    return [entry(z, by[z]["tilt"] if z in by else None, by.get(z)) for z in range(nz)], ("mdoc (incomplete)" if ms else None)


# ---------------------------------------------------------------- session: search maps, positions, ratings
def search_maps(session):
    """Search maps of a session: id, name (SearchMap_N), folder, dir, tiles (stems), rating (StarRating in
    SearchMap.dm, 0–5), stage_um and, when SearchMap.xml + .mrc exist, their Geo (footprint)."""
    out = []
    for d in sorted(glob.glob(os.path.join(session, "SearchMaps", "SearchMap_*"))):
        m = MAP_RE.match(os.path.basename(d))
        if not m or not os.path.isdir(d):
            continue
        tiles = sorted((os.path.basename(f)[:-4] for f in glob.glob(os.path.join(d, "Tile_*.mrc"))),
                       key=lambda s: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", s)])
        rating = 0
        if os.path.isfile(os.path.join(d, "SearchMap.dm")):
            r = _find(flatten_xml(os.path.join(d, "SearchMap.dm")), r"(^|/)StarRating$")
            rating = max(0, min(5, int(_num(r) or 0)))
        xml = next((os.path.join(d, f) for f in ("SearchMap.xml", "Overview.xml") if os.path.isfile(os.path.join(d, f))), None)
        info = image_info(xml) if xml else {}
        geo = image_geo(os.path.join(d, "SearchMap.xml"), os.path.join(d, "SearchMap.mrc")) \
            if all(os.path.isfile(os.path.join(d, f)) for f in ("SearchMap.xml", "SearchMap.mrc")) else None
        out.append({"id": int(m.group(1)), "name": f"SearchMap_{m.group(1)}", "folder": os.path.basename(d), "dir": d, "tiles": tiles, "rating": rating,
                    "stage_um": info.get("stage_um"), "tilt_deg": info.get("tilt_deg"), "geo": geo})
    return sorted(out, key=lambda x: x["id"])


def parse_batch_list(path):
    """Batch/BatchPositionsList.xml → {N: plan}. Offsets of areas are in µm; an area sits at
    stage_um − offset_um (StagePosition − (PositionX, PositionY))."""
    root = ET.parse(path).getroot()
    kid = lambda el, n: next((c for c in el if _local(c.tag) == n), None) if el is not None else None
    txt = lambda el, n: (lambda c: (c.text or "").strip() if c is not None else None)(kid(el, n))
    num = lambda el, n: _num(txt(el, n))
    off = lambda el: (num(el, "PositionX") * 1e6, num(el, "PositionY") * 1e6) if el is not None and num(el, "PositionX") is not None else None
    out = {}
    for bp in root.iter():
        if _local(bp.tag) != "BatchPositionParameters":
            continue
        m = re.match(r"^Position_(\d+)$", txt(bp, "Name") or "", re.I)
        if not m:
            continue
        areas = [{"k": 1, "offset_um": off(kid(bp, "ExposureTemplateAreaParameters")) or (0.0, 0.0), "defocus": num(bp, "Defocus")}]
        extra = kid(bp, "AdditionalExposureTemplateAreas")
        for a in (list(extra) if extra is not None else []):
            k = re.search(r"_(\d+)$", txt(a, "Name") or "")
            if k and off(a):
                areas.append({"k": int(k.group(1)), "offset_um": off(a), "defocus": num(a, "Defocus")})
        sx, sy, sa = num(bp, "StagePositionX"), num(bp, "StagePositionY"), num(bp, "StagePositionA")
        out[int(m.group(1))] = {
            "status": txt(bp, "BatchPositionStatus"), "stage_um": (sx * 1e6, sy * 1e6) if sx is not None else None,
            "tilt_deg": math.degrees(sa) if sa is not None else None, "tilt_axis_deg": num(bp, "TiltAxisAngleInDegrees") or 0.0,
            "defocus": num(bp, "Defocus"), "comment": txt(bp, "Comment") or "", "areas": sorted(areas, key=lambda a: a["k"]),
            "tracking_um": off(kid(bp, "TrackingTemplateAreaParameters")), "focus_um": off(kid(bp, "FocusTemplateAreaParameters")),
        }
    return out


def _tilt_files(folder):
    """{(N, k): {"mrc": path, "mdoc": path}} for Position_N[_k].mrc/.mdoc in folder and one level down
    (tiltseries_movie/ is skipped)."""
    out = {}
    if not folder or not os.path.isdir(folder):
        return out
    dirs = [folder] + [os.path.join(folder, d) for d in sorted(os.listdir(folder))
                       if os.path.isdir(os.path.join(folder, d)) and d.lower() != "tiltseries_movie"]
    for d in dirs:
        for f in os.listdir(d):
            m = re.match(r"^Position_(\d+)(?:_(\d+))?(\.mrc|(?:\.mrc)?\.mdoc)$", f, re.I)
            if m and os.path.getsize(os.path.join(d, f)) > 0:
                key = (int(m.group(1)), int(m.group(2) or 1))
                out.setdefault(key, {})["mdoc" if m.group(3).lower().endswith("mdoc") else "mrc"] = os.path.join(d, f)
    return out


def batch_positions(session, tilt_dir=None):
    """Batch positions with their exposure areas (k = 1 is Position_N, k ≥ 2 is Position_N_k), each
    area's stage position, files and status (Tomo5's own, else from the files: Initialized → Refined
    → Acquired). Tilt series are looked for in the session folder and tilt_dir."""
    bdir = os.path.join(session, "Batch")
    plans = parse_batch_list(os.path.join(bdir, "BatchPositionsList.xml")) if os.path.isfile(os.path.join(bdir, "BatchPositionsList.xml")) else {}
    names = os.listdir(bdir) if os.path.isdir(bdir) else []
    ids = set(plans) | {int(m.group(1)) for f in names for m in [re.match(r"^Position_(\d+)", f)] if m}
    ts = _tilt_files(session)
    ts.update(_tilt_files(tilt_dir))
    out = []
    for n in sorted(ids):
        plan = plans.get(n, {})
        f = lambda name: os.path.join(bdir, name) if name in names else None
        ks = {a["k"] for a in plan.get("areas", [])} | {k for (p, k) in ts if p == n} | \
             {int(m.group(1) or 1) for x in names for m in [re.match(rf"^Position_{n}(?:_(\d+))?_Exposure\w*\.mrc$", x, re.I)] if m}
        areas = []
        for k in sorted(ks or {1}):
            pa = next((a for a in plan.get("areas", []) if a["k"] == k), {})
            o = pa.get("offset_um")
            st = plan.get("stage_um")
            areas.append({"k": k, "name": f"Position_{n}" + (f"_{k}" if k > 1 else ""), "offset_um": o, "defocus": pa.get("defocus"),
                          "stage_um": (st[0] - o[0], st[1] - o[1]) if st and o else None,
                          "exposure": f(f"Position_{n}{'_' + str(k) if k > 1 else ''}_Exposure.mrc"),
                          "tilt_series": ts.get((n, k), {}).get("mrc"), "mdoc": ts.get((n, k), {}).get("mdoc")})
        acq = sum(1 for a in areas if a["tilt_series"])
        refined = f(f"Position_{n}_Tracking.mrc") or any(a["exposure"] for a in areas)
        file_status = (f"Acquired {acq}/{len(areas)}" if acq < len(areas) else "Acquired") if acq else "Refined" if refined else "Initialized"
        out.append({"id": n, "name": f"Position_{n}", "status": plan.get("status") or file_status, "file_status": file_status,
                    "stage_um": plan.get("stage_um"), "tilt_deg": plan.get("tilt_deg"), "tilt_axis_deg": plan.get("tilt_axis_deg"),
                    "search": f(f"Position_{n}_Search.mrc"), "search_xml": f(f"Position_{n}_Search.xml"),
                    "tracking_um": plan.get("tracking_um"), "focus_um": plan.get("focus_um"), "areas": areas})
    return out


def tilt_ratings(folder):
    """Movie Player star ratings: {"Position_1_2.mrc": 4, …} from <folder>/tiltseries_movie/tiltseries_ratings.xml
    (folder = the tilt-series folder or the session)."""
    p = os.path.join(folder, "tiltseries_movie", "tiltseries_ratings.xml")
    if not os.path.isfile(p):
        return {}
    return {el.get("name"): int(el.get("rating")) for el in ET.parse(p).getroot().iter("TiltSeries")
            if el.get("name") and (el.get("rating") or "").isdigit()}


# ---------------------------------------------------------------- stitching a search map
def tile_grid(map_dir):
    """Tomo5's tile grid from SearchMap.dm: {stem: (cx, cy, width)} — AtlasPixelPosition of each
    tile (pixels of the stitched SearchMap.mrc, axis-aligned in the camera frame). The best start
    for stitching: the tile XML stage positions are ~5 % short and ~1° rotated."""
    p = os.path.join(map_dir, "SearchMap.dm")
    if not os.path.isfile(p):
        return {}
    T = {}
    for path, v in flatten_xml(p):
        m = re.search(r"TileSetTileXml(?:\[(\d+)\])?/(?:AtlasPixelPosition/(x|y|width|height)|TileImageReference/(BaseFileName))$", path)
        if m:
            T.setdefault(m.group(1) or "1", {})[m.group(2) or "name"] = _num(v) if m.group(2) else v
    return {t["name"]: (t["x"] + t["width"] / 2, t["y"] + t["height"] / 2, t["width"])
            for t in T.values() if t.get("name") and t.get("x") is not None and (t.get("width") or 0) > 0}


def _high_pass(a, r=6):
    """a − box blur (radius r), normalised: what the overlap matching uses."""
    k = 2 * r + 1
    c = np.cumsum(np.cumsum(np.pad(a, r + 1, mode="edge"), 0), 1)
    box = (c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k])[:a.shape[0], :a.shape[1]] / (k * k)
    d = a - box
    return d / (d.std() or 1)


def _ncc(A, B, ox, oy, min_n):
    """Normalised cross-correlation of A and B placed at offset (ox, oy) (B's top-left in A's frame)."""
    h, w = A.shape
    x0, x1, y0, y1 = max(0, ox), min(w, ox + w), max(0, oy), min(h, oy + h)
    if x1 - x0 < 4 or y1 - y0 < 4 or (x1 - x0) * (y1 - y0) < min_n:
        return -1.0
    a = A[y0:y1, x0:x1].ravel()
    b = B[y0 - oy:y1 - oy, x0 - ox:x1 - ox].ravel()
    a = a - a.mean()
    b = b - b.mean()
    den = math.sqrt(float(a @ a) * float(b @ b))
    return float(a @ b) / den if den > 0 else -1.0


def _match(A, B, D, lim, min_n):
    """Best offset of B near the nominal offset D (±lim px): coarse at 1/2 size, then refined ±2 px.
    Returns (offset, score, distinct) — distinct: the runner-up (≥ 4 px away) is < 0.7 × best."""
    A2, B2 = A[: A.shape[0] // 2 * 2:2, : A.shape[1] // 2 * 2:2], B[: B.shape[0] // 2 * 2:2, : B.shape[1] // 2 * 2:2]
    l2, scores = max(1, lim // 2), {}
    for sy in range(-l2, l2 + 1):
        for sx in range(-l2, l2 + 1):
            scores[(sx, sy)] = _ncc(A2, B2, int(round(D[0] / 2)) + sx, int(round(D[1] / 2)) + sy, min_n / 4)
    (bx, by), best = max(scores.items(), key=lambda kv: kv[1])
    second = max((v for (sx, sy), v in scores.items() if math.hypot(sx - bx, sy - by) >= 4), default=-1)
    fine = max(((_ncc(A, B, int(round(D[0])) + 2 * bx + dx, int(round(D[1])) + 2 * by + dy, min_n), (2 * bx + dx, 2 * by + dy))
                for dy in range(-2, 3) for dx in range(-2, 3)))
    s = fine[1]
    at_edge = abs(s[0]) >= lim - 1 or abs(s[1]) >= lim - 1
    return (D[0] + s[0], D[1] + s[1]), fine[0], best > 0 and second < 0.7 * best and fine[0] > 0.02 and not at_edge


def stitch_search_map(map_dir, bin=4, align=True):
    """Stitch the Tile_*.mrc of a search map like the viewer: start from Tomo5's tile grid
    (SearchMap.dm), refine each overlap by cross-correlation (±120 px), solve positions by least
    squares, match brightness (gains), and blend with edge feathering (like IMOD blendmont).
    Returns (image float32, Geo, report). Without SearchMap.dm the tile XML stage positions are used."""
    if np is None:
        raise RuntimeError("numpy is needed to stitch (pip install numpy)")
    stems = sorted((os.path.basename(f)[:-4] for f in glob.glob(os.path.join(map_dir, "Tile_*.mrc"))),
                   key=lambda s: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", s)])
    if not stems:
        raise ValueError(f"no Tile_*.mrc in {map_dir}")
    geos = [image_geo(os.path.join(map_dir, s + ".xml"), os.path.join(map_dir, s + ".mrc")) for s in stems]
    g0, n = geos[0], len(stems)
    # overlaps are only ~50–70 px wide, so they are matched at full size (bin 2 for tiles > 4k);
    # positions are then scaled to the output binning
    abin = (1 if max(g0.nx, g0.ny) <= 4096 else 2) if align else bin
    imgs = [read_section(os.path.join(map_dir, s + ".mrc"), 0, abin) for s in stems]
    h, w = imgs[0].shape
    grid = tile_grid(map_dir)
    if all(s in grid for s in stems):            # nominal centres, px of the first tile's frame
        c0, k = grid[stems[0]], g0.nx / grid[stems[0]][2] / abin
        nom = [(w / 2 + (grid[s][0] - c0[0]) * k, h / 2 + (grid[s][1] - c0[1]) * k) for s in stems]
        layout = "SearchMap.dm"
    else:
        nom = [(lambda p: (w / 2 + (p[0] - g0.nx / 2) / abin, h / 2 + (p[1] - g0.ny / 2) / abin))(g0.stage_to_pixel(*g.S)) for g in geos]
        layout = "tile XML"
    hp = [_high_pass(a) for a in imgs] if align else None
    lim = max(8, round(120 / abin))
    cons = []
    for i in range(n):
        for j in range(i + 1, n):
            D = (nom[j][0] - nom[i][0], nom[j][1] - nom[i][1])
            ow, oh = w - abs(D[0]), h - abs(D[1])
            if ow < 16 or oh < 16 or not (ow >= 0.5 * w or oh >= 0.5 * h):
                continue                         # side neighbours only
            if not align:
                cons.append({"i": i, "j": j, "D": D, "t": D, "ok": True, "score": 0})
                continue
            t, score, ok = _match(hp[i], hp[j], D, lim, 0.25 * ow * oh)
            cons.append({"i": i, "j": j, "D": D, "t": t, "ok": ok, "score": score})

    def solve(skip=None, iters=300):            # Gauss-Seidel least squares; failed matches keep D, weakly
        q = [list(p) for p in nom]
        adj = [[] for _ in range(n)]
        for c in cons:
            if c is skip:
                continue
            t, wt = (c["t"], 1.0) if c["ok"] else (c["D"], 0.02)
            adj[c["i"]].append((c["j"], (-t[0], -t[1]), wt))
            adj[c["j"]].append((c["i"], t, wt))
        for _ in range(iters):
            for i in range(n):
                sw = sum(a[2] for a in adj[i])
                if sw:
                    q[i] = [sum(a[2] * (q[a[0]][0] + a[1][0]) for a in adj[i]) / sw, sum(a[2] * (q[a[0]][1] + a[1][1]) for a in adj[i]) / sw]
        return q

    pos = solve()
    for _ in range(6):                           # drop the match that disagrees most with the others
        deg = [0] * n
        for c in cons:
            if c["ok"]:
                deg[c["i"]] += 1
                deg[c["j"]] += 1
        worst, wr = None, 0
        for c in cons:
            if c["ok"] and deg[c["i"]] >= 2 and deg[c["j"]] >= 2:
                q = solve(c, 120)
                r = math.hypot(q[c["j"]][0] - q[c["i"]][0] - c["t"][0], q[c["j"]][1] - q[c["i"]][1] - c["t"][1])
                if r > wr:
                    worst, wr = c, r
        if not worst or wr <= 3:
            break
        worst["ok"] = False
        pos = solve()
    ex, ey = sum(p[0] - m[0] for p, m in zip(pos, nom)) / n, sum(p[1] - m[1] for p, m in zip(pos, nom)) / n
    pos = [(p[0] - ex, p[1] - ey) for p in pos]
    shifts = [((p[0] - m[0]) * abin, (p[1] - m[1]) * abin) for p, m in zip(pos, nom)]    # full-size px vs the grid
    if abin != bin:                              # to the output binning
        f, wa, ha = abin / bin, w, h
        imgs = [read_section(os.path.join(map_dir, s + ".mrc"), 0, bin) for s in stems]
        h, w = imgs[0].shape
        pos = [(w / 2 + (p[0] - wa / 2) * f, h / 2 + (p[1] - ha / 2) * f) for p in pos]
    off = [(int(round(p[0] - w / 2)), int(round(p[1] - h / 2))) for p in pos]
    # gains from the mean brightness in each overlap (multiplicative for positive counts)
    pairs = []
    for c in cons:
        dx, dy = off[c["j"]][0] - off[c["i"]][0], off[c["j"]][1] - off[c["i"]][1]
        x0, x1, y0, y1 = max(0, dx), min(w, dx + w), max(0, dy), min(h, dy + h)
        if x1 - x0 >= 4 and y1 - y0 >= 4:
            pairs.append((c["i"], c["j"], float(imgs[c["i"]][y0:y1, x0:x1].mean()), float(imgs[c["j"]][y0 - dy:y1 - dy, x0 - dx:x1 - dx].mean())))
    positive = bool(pairs) and all(a > 0 and b > 0 for _, _, a, b in pairs)
    x = [0.0] * n
    for _ in range(300):
        for i in range(n):
            vals = [x[pi] + (math.log(ma / mb) if positive else ma - mb) for pi, pj, ma, mb in pairs if pj == i] + \
                   [x[pj] - (math.log(ma / mb) if positive else ma - mb) for pi, pj, ma, mb in pairs if pi == i]
            if vals:
                x[i] = sum(vals) / len(vals)
    xm = sum(x) / n
    gain = [math.exp(v - xm) if positive else 1.0 for v in x]
    add = [0.0 if positive else v - xm for v in x]
    # blend: each tile's weight ramps up from its edges over the overlap width
    narrow = min((min(w - abs(off[c["j"]][0] - off[c["i"]][0]), h - abs(off[c["j"]][1] - off[c["i"]][1])) for c in cons), default=None)
    F = max(2.0, 0.8 * narrow) if narrow else 2.0
    X0, Y0 = min(o[0] for o in off), min(o[1] for o in off)
    W, H = max(o[0] for o in off) + w - X0, max(o[1] for o in off) + h - Y0
    acc, wsum = np.zeros((H, W), np.float32), np.zeros((H, W), np.float32)
    rx = np.minimum(1, np.minimum((np.arange(w) + 0.5) / F, (w - np.arange(w) - 0.5) / F))
    ry = np.minimum(1, np.minimum((np.arange(h) + 0.5) / F, (h - np.arange(h) - 0.5) / F))
    wt = (ry[:, None] * rx[None, :]).astype(np.float32)
    for i, a in enumerate(imgs):
        ox, oy = off[i][0] - X0, off[i][1] - Y0
        acc[oy:oy + h, ox:ox + w] += wt * (a * gain[i] + add[i])
        wsum[oy:oy + h, ox:ox + w] += wt
    out = np.where(wsum > 0, acc / np.maximum(wsum, 1e-12), np.nan)
    out = np.nan_to_num(out, nan=float(np.nanmin(out)))
    cx, cy = X0 + W / 2, Y0 + H / 2
    S = g0.pixel_to_stage(g0.nx / 2 + (cx - w / 2) * bin, g0.ny / 2 + (cy - h / 2) * bin)
    geo = Geo(S, g0.R, g0.ps * bin, W, H)
    report = {"tiles": n, "layout": layout, "matched": sum(c["ok"] for c in cons), "overlaps": len(cons),
              "max_shift_px": max((math.hypot(*d) for d in shifts), default=0),
              "gains": (min(gain), max(gain)), "feather_px": F * bin, "bin": bin,
              "shifts_px": shifts}
    return out, geo, report


# ---------------------------------------------------------------- command line
def _json(o):
    if isinstance(o, Geo):
        return {"S_um": o.S, "R": o.R, "pixel_um": o.ps, "nx": o.nx, "ny": o.ny, "corners_um": o.corners()}
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(type(o))


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("info", help="what is read from an image XML or MRC file")
    p.add_argument("file")
    p = sub.add_parser("maps", help="search maps of a session (rating, stage, footprint)")
    p.add_argument("session")
    p = sub.add_parser("positions", help="batch positions and areas (stage, status, files); --csv for a table")
    p.add_argument("session")
    p.add_argument("--tilt", help="tilt-series folder")
    p.add_argument("--csv", help="write one row per area to this CSV file")
    p = sub.add_parser("tilts", help="angle, time and dose of every section of a tilt series")
    p.add_argument("mrc")
    p.add_argument("mdoc", nargs="?")
    p = sub.add_parser("stitch", help="stitch a search map's tiles to an MRC file")
    p.add_argument("map_dir")
    p.add_argument("out")
    p.add_argument("--bin", type=int, default=4)
    p.add_argument("--no-align", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "info":
        if a.file.lower().endswith(".mrc"):
            out = {**mrc_header(a.file), "pixel_a": mrc_pixel_a(a.file), "fei_tilts": fei_section_tilts(a.file)}
        else:
            out = image_info(a.file)
        print(json.dumps(out, indent=1, default=_json, ensure_ascii=False))
    elif a.cmd == "maps":
        for m in search_maps(a.session):
            m.pop("dir")
            m["tiles"] = len(m["tiles"])
            print(json.dumps(m, default=_json, ensure_ascii=False))
    elif a.cmd == "positions":
        pos = batch_positions(a.session, a.tilt)
        rate = {**tilt_ratings(a.session), **(tilt_ratings(a.tilt) if a.tilt else {})}
        rows = [{"position": p["name"], "area": f"{p['id']}.{ar['k']}", "status": p["status"],
                 "stage_x_um": ar["stage_um"] and round(ar["stage_um"][0], 3), "stage_y_um": ar["stage_um"] and round(ar["stage_um"][1], 3),
                 "defocus_um": ar["defocus"], "tilt_series": ar["tilt_series"] or "",
                 "rating": rate.get(os.path.basename(ar["tilt_series"] or ""), "")} for p in pos for ar in p["areas"]]
        if a.csv:
            with open(a.csv, "w", newline="") as fh:
                wr = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["position"])
                wr.writeheader()
                wr.writerows(rows)
            print(f"{len(rows)} areas of {len(pos)} positions → {a.csv}")
        else:
            for r in rows:
                print(json.dumps(r, ensure_ascii=False))
    elif a.cmd == "tilts":
        secs, src = tilt_sections(a.mrc, a.mdoc)
        print(f"# angles from: {src}")
        for s in secs:
            print(f"z {s['z']:3d}  {s['angle'] if s['angle'] is None else round(s['angle'], 2):>7}°  mdoc ZValue {s['mdoc_z']}  "
                  f"{s['time'] or ''}  dose {s['dose']}")
    elif a.cmd == "stitch":
        img, geo, rep = stitch_search_map(a.map_dir, a.bin, not a.no_align)
        write_mrc(a.out, img, geo.ps * 1e4)
        print(json.dumps({**rep, "geo": geo}, default=_json, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
