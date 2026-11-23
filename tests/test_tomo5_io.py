#!/usr/bin/env python3
"""Unit tests for .claude/skills/tomo5-data/scripts/tomo5_io.py on small synthetic Tomo5-like files.
Run:  python3 tests/test_tomo5_io.py   (or: make check)"""
import math
import os
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".claude", "skills", "tomo5-data", "scripts"))
import tomo5_io as t5  # noqa: E402

try:
    import numpy as np
except ImportError:
    np = None

NS = 'xmlns="http://schemas.datacontract.org/2004/07/Fei.SharedObjects"'


def image_xml(stage_m, pixel_m, m, custom_copy=True):
    """A minimal Tomo5 image XML (MicroscopeImage) with stage, pixel size and ReferenceTransformation;
    CustomData holds a decoy copy of the matrix, as in real files."""
    mat = "".join(f"<_m{k}>{v}</_m{k}>" for k, v in zip(("11", "12", "21", "22"), m))
    decoy = f"<CustomData><ReferenceTransformation><matrix>{''.join(f'<_m{k}>9</_m{k}>' for k in ('11', '12', '21', '22'))}</matrix></ReferenceTransformation></CustomData>" if custom_copy else ""
    return (f'<?xml version="1.0"?><MicroscopeImage {NS}>{decoy}<microscopeData><stage><Position><A>0.0175</A>'
            f'<X>{stage_m[0]}</X><Y>{stage_m[1]}</Y></Position></stage></microscopeData>'
            f'<ReferenceTransformation><matrix>{mat}</matrix></ReferenceTransformation>'
            f'<SpatialScale><pixelSize><x><numericValue>{pixel_m}</numericValue></x></pixelSize></SpatialScale></MicroscopeImage>')


def write_mrc(path, stack, mode=1, fei_tilts=None, pixel_a=2.0):
    """MRC stack; with fei_tilts, an FEI2 extended header (888-byte blocks, alpha at byte 100)."""
    nz, ny, nx = stack.shape
    blk = 888
    nxt = blk * 1024 if fei_tilts is not None else 0
    h = bytearray(1024)
    struct.pack_into("<4i", h, 0, nx, ny, nz, mode)
    struct.pack_into("<3i", h, 28, nx, ny, nz)
    struct.pack_into("<3f", h, 40, nx * pixel_a, ny * pixel_a, nz * pixel_a)
    struct.pack_into("<i", h, 92, nxt)
    ext = bytearray(nxt)
    for z, a in enumerate(fei_tilts or []):
        struct.pack_into("<2i", ext, z * blk, blk, 2)
        struct.pack_into("<d", ext, z * blk + 100, a)
        struct.pack_into("<d", ext, z * blk + 156, pixel_a * 1e-10)
    dt = {1: "<i2", 2: "<f4"}[mode]
    with open(path, "wb") as fh:
        fh.write(bytes(h) + bytes(ext) + stack.astype(dt).tobytes())


def put(path, text):
    with open(path, "w") as fh:
        fh.write(text)


def write_mdoc(path, angles_in_acquisition_order):
    lines = ["PixelSpacing = 2.0", ""]
    for i, a in enumerate(angles_in_acquisition_order):
        lines += [f"[ZValue = {i}]", f"TiltAngle = {a:.2f}", f"ExposureDose = {2 + i / 100:.2f}", f"DateTime = 20-Nov-26  12:{10 + i // 60:02d}:{i % 60:02d}", ""]
    with open(path, "w") as fh:
        fh.write("\n".join(lines))


DOSE_SYMMETRIC = [0, 3, -3, -6, 6, 9, -9, -12, 12]


class TestXmlAndGeometry(unittest.TestCase):
    def test_image_info_negates_and_normalises_matrix_and_skips_customdata(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "Overview.xml")
            c, s = math.cos(0.3) * 4e-9, math.sin(0.3) * 4e-9          # scale per unbinned pixel: ignored
            put(p, image_xml((-5.0e-4, 3.0e-4), 2.0e-8, (c, -s, s, c)))
            info = t5.image_info(p)
            self.assertAlmostEqual(info["stage_um"][0], -500.0)
            self.assertAlmostEqual(info["pixel_um"], 0.02)
            self.assertAlmostEqual(info["tilt_deg"], math.degrees(0.0175))
            R = info["R"]
            self.assertAlmostEqual(R[0][0], -math.cos(0.3))            # negated, unit length
            self.assertAlmostEqual(R[1][0], -math.sin(0.3))

    def test_geo_roundtrip(self):
        g = t5.Geo((100.0, -50.0), [[0.0, -1.0], [1.0, 0.0]], 0.01, 400, 300)
        self.assertEqual(g.pixel_to_stage(200, 150), (100.0, -50.0))   # centre → S
        x, y = g.stage_to_pixel(*g.pixel_to_stage(17.5, 230.25))
        self.assertAlmostEqual(x, 17.5)
        self.assertAlmostEqual(y, 230.25)

    def test_key_value_pairs_flatten_to_named_paths(self):
        rows = t5.flatten_xml("<a><Item><Key>StarRating</Key><Value>4</Value></Item><b>1</b><b>2</b></a>")
        self.assertIn(("Item/StarRating", "4"), rows)
        self.assertIn(("b[2]", "2"), rows)


@unittest.skipIf(np is None, "numpy needed")
class TestMrcAndTiltSeries(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.sorted = sorted(DOSE_SYMMETRIC)                           # Tomo5: stack sorted by angle
        self.stack = np.stack([np.full((8, 12), z, np.int16) for z in range(len(self.sorted))])

    def test_section_skips_fei_header_and_bins(self):
        p = os.path.join(self.d, "Position_1.mrc")
        stack = np.arange(2 * 8 * 12, dtype=np.int16).reshape(2, 8, 12)
        write_mrc(p, stack, fei_tilts=[-3.0, 3.0])
        self.assertTrue(np.array_equal(t5.read_section(p, 1), stack[1].astype(np.float32)))
        self.assertTrue(np.allclose(t5.read_section(p, 0, 2), stack[0].reshape(4, 2, 6, 2).mean(axis=(1, 3))))
        self.assertAlmostEqual(t5.mrc_pixel_a(p), 2.0)

    def test_fei_angles_pair_with_mdoc_blocks(self):
        p = os.path.join(self.d, "Position_1.mrc")
        write_mrc(p, self.stack, fei_tilts=[a + 0.003 for a in self.sorted])
        write_mdoc(p[:-4] + ".mdoc", DOSE_SYMMETRIC)
        secs, src = t5.tilt_sections(p, p[:-4] + ".mdoc")
        self.assertEqual(src, "FEI header")
        self.assertAlmostEqual(secs[0]["angle"], -12, places=2)
        self.assertEqual(secs[0]["mdoc_z"], DOSE_SYMMETRIC.index(-12))  # its own .mdoc block
        self.assertEqual(secs[4]["mdoc_z"], 0)                          # 0° was acquired first

    def test_without_fei_a_non_monotonic_mdoc_is_sorted(self):
        p = os.path.join(self.d, "Position_2.mrc")
        write_mrc(p, self.stack)
        write_mdoc(p[:-4] + ".mdoc", DOSE_SYMMETRIC)
        secs, src = t5.tilt_sections(p, p[:-4] + ".mdoc")
        self.assertEqual(src, "mdoc, sorted by angle")
        self.assertEqual([s["angle"] for s in secs], self.sorted)

    def test_monotonic_mdoc_means_zvalue_is_the_section(self):
        p = os.path.join(self.d, "Position_3.mrc")
        write_mrc(p, self.stack)
        write_mdoc(p[:-4] + ".mdoc", self.sorted)
        secs, src = t5.tilt_sections(p, p[:-4] + ".mdoc")
        self.assertEqual(src, "mdoc")
        self.assertEqual([s["mdoc_z"] for s in secs], list(range(len(self.sorted))))


class TestBatchList(unittest.TestCase):
    def test_areas_sit_at_stage_minus_offset(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "Batch"))
            put(os.path.join(d, "Batch", "BatchPositionsList.xml"), 
                f'<BatchPositionsList {NS}><BatchPositions><BatchPositionParameters>'
                '<AdditionalExposureTemplateAreas><ExposureTemplateAreaParameters><Defocus>-4</Defocus><Name>Position_7_2</Name>'
                '<PositionX>2.0E-07</PositionX><PositionY>-3.0E-06</PositionY></ExposureTemplateAreaParameters></AdditionalExposureTemplateAreas>'
                '<BatchPositionStatus>Initialized</BatchPositionStatus><Defocus>-5</Defocus><Name>Position_7</Name>'
                '<StagePositionX>-5.0E-04</StagePositionX><StagePositionY>4.0E-04</StagePositionY>'
                '</BatchPositionParameters></BatchPositions></BatchPositionsList>')
            [p] = t5.batch_positions(d)
            self.assertEqual((p["name"], p["status"]), ("Position_7", "Initialized"))
            a2 = next(a for a in p["areas"] if a["k"] == 2)
            self.assertAlmostEqual(a2["stage_um"][0], -500.2)
            self.assertAlmostEqual(a2["stage_um"][1], 403.0)
            self.assertEqual(a2["name"], "Position_7_2")


@unittest.skipIf(np is None, "numpy needed")
class TestStitch(unittest.TestCase):
    def test_recovers_tile_offsets_from_a_rough_grid(self):
        rng = np.random.default_rng(0)
        big = rng.poisson(20 + 15 * (rng.random((380, 560)) > 0.97), (380, 560)).astype(np.float32)
        big = np.cumsum(np.cumsum(big, 0), 1)                         # smooth texture with structure
        big = (big[8:, 8:] - big[:-8, 8:] - big[8:, :-8] + big[:-8, :-8]) / 64
        T, true = 200, [(0, 0), (172, 3), (2, 168), (175, 170)]       # 2 × 2 tiles, ~28 px overlaps
        with tempfile.TemporaryDirectory() as d:
            items = []
            for i, (x, y) in enumerate(true, 1):
                write_mrc(os.path.join(d, f"Tile_{i}.mrc"), big[y:y + T, x:x + T][None], mode=2)
                put(os.path.join(d, f"Tile_{i}.xml"), image_xml((0, 0), 1e-9, (1, 0, 0, 1), False))
                gx, gy = [(0, 0), (180, 0), (0, 180), (180, 180)][i - 1]   # Tomo5's grid: off by up to 12 px
                items.append(f"<TileSetTileXml><AtlasPixelPosition><x>{gx}</x><y>{gy}</y><width>{T}</width><height>{T}</height></AtlasPixelPosition>"
                             f"<TileImageReference><BaseFileName>Tile_{i}</BaseFileName></TileImageReference></TileSetTileXml>")
            put(os.path.join(d, "SearchMap.dm"), f"<SearchMap><Tiles>{''.join(items)}</Tiles></SearchMap>")
            img, geo, rep = t5.stitch_search_map(d, bin=1)
            self.assertEqual(rep["layout"], "SearchMap.dm")
            self.assertEqual(rep["matched"], rep["overlaps"])
            grid = [(0, 0), (180, 0), (0, 180), (180, 180)]
            rel = lambda p, i: (p[i][0] - p[0][0], p[i][1] - p[0][1])
            for i in range(1, 4):                                       # recovered = grid + shift ≈ truth
                got = (grid[i][0] + rep["shifts_px"][i][0] - rep["shifts_px"][0][0], grid[i][1] + rep["shifts_px"][i][1] - rep["shifts_px"][0][1])
                self.assertLess(math.hypot(got[0] - rel(true, i)[0], got[1] - rel(true, i)[1]), 1.5, (i, got, rel(true, i)))
            self.assertEqual(img.shape, (geo.ny, geo.nx))


if __name__ == "__main__":
    unittest.main(verbosity=1)
