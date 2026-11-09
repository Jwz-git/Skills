import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import image_io  # noqa: E402
import split_image  # noqa: E402
from extract_utils import crop_image, package_zip  # noqa: E402
from prepare_image import TIERS, prepare  # noqa: E402

ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def run_script(name, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / name), *map(str, args)],
        capture_output=True,
        text=True,
        env=ENV,
    )


def document(width, height, image=None, left=40, right=None):
    """合成文档：20 px 高的“词块”行，行距 12 px，每 5 行后有 60 px 段落空白。"""
    image = image or Image.new("L", (width, height), 255)
    right = width - 40 if right is None else right
    draw = ImageDraw.Draw(image)
    y, lines = 40, 0
    while y + 20 < height - 40:
        x = left
        while x < right - 40:
            word = 20 + (x * 7 + y) % 60
            draw.rectangle([x, y, min(x + word, right), y + 19], fill=0)
            x += word + 15
        y += 32
        lines += 1
        if lines % 5 == 0:
            y += 60
    return image


def noise(width, height, seed=0):
    rng = np.random.default_rng(seed)
    return Image.fromarray(rng.integers(0, 256, (height, width), dtype=np.uint8))


def oriented_jpeg(path, orientation=6):
    """80×40 白图，左上角有红块；写入 EXIF 方向标签。"""
    image = Image.new("RGB", (80, 40), "white")
    ImageDraw.Draw(image).rectangle([0, 0, 15, 15], fill=(255, 0, 0))
    exif = image.getexif()
    exif[image_io.ORIENTATION] = orientation
    image.save(path, "JPEG", quality=95, exif=exif.tobytes())


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.runs = 0

    def prepared(self, path, **options):
        self.runs += 1
        options.setdefault("output_dir", self.tmp / f"out{self.runs}")
        return prepare(path, **options)

    def boxes(self, result):
        return [item["box"] for item in result["files_to_read"]]

    def assert_tiles_fit(self, result):
        limits = result["limits"]
        for left, top, right, bottom in self.boxes(result):
            self.assertTrue(
                split_image.fits(right - left, bottom - top, limits["max_edge"], limits["max_visual_tokens"])
            )


class PrepareFormatTests(TempDirCase):
    def test_supported_image_within_limits_is_passed_through(self):
        source = self.tmp / "small.png"
        Image.new("RGB", (300, 200), "white").save(source)

        result = self.prepared(source)

        self.assertEqual(result["files_to_read"], [{"path": str(source.resolve()), "page": 1, "box": [0, 0, 300, 200]}])
        self.assertIsNone(result["output_dir"])
        self.assertEqual(sorted(path.name for path in self.tmp.iterdir()), ["small.png"])

    def test_exif_orientation_is_applied(self):
        source = self.tmp / "photo.jpg"
        oriented_jpeg(source)

        result = self.prepared(source)

        self.assertEqual(result["pages"][0]["orientation_applied"], 6)
        self.assertEqual((result["pages"][0]["width"], result["pages"][0]["height"]), (40, 80))
        self.assertEqual(self.boxes(result), [[0, 0, 40, 80]])
        with Image.open(result["files_to_read"][0]["path"]) as tile:
            self.assertEqual(tile.size, (40, 80))
            red, green, _ = tile.convert("RGB").getpixel((34, 5))
            self.assertGreater(red, 180)
            self.assertLess(green, 90)
            self.assertGreater(tile.convert("RGB").getpixel((5, 5))[1], 200)

    def test_cmyk_jpeg_is_converted_to_rgb(self):
        source = self.tmp / "print.jpg"
        Image.new("CMYK", (64, 64), (0, 0, 0, 0)).save(source, "JPEG")

        result = self.prepared(source)

        self.assertEqual(result["pages"][0]["converted_from"], "CMYK")
        with Image.open(result["files_to_read"][0]["path"]) as tile:
            self.assertEqual(tile.mode, "RGB")
            self.assertGreater(min(tile.getpixel((32, 32))), 240)

    def test_la_png_is_converted_and_split(self):
        source = self.tmp / "transparent.png"
        mask = document(300, 3600)
        alpha = Image.eval(mask, lambda value: 255 - value)
        Image.merge("LA", (Image.new("L", mask.size, 0), alpha)).save(source)

        result = self.prepared(source)

        self.assertEqual(result["pages"][0]["converted_from"], "LA")
        self.assertGreater(len(result["files_to_read"]), 1)
        self.assert_tiles_fit(result)
        for item in result["files_to_read"]:
            with Image.open(item["path"]) as tile:
                self.assertEqual(tile.mode, "RGBA")

    def test_16_bit_png_is_scaled_to_8_bit(self):
        source = self.tmp / "scan16.png"
        gradient = np.tile(np.linspace(0, 65535, 256), (64, 1)).astype(np.uint16)
        Image.fromarray(gradient).save(source)

        result = self.prepared(source)

        self.assertTrue(result["pages"][0]["converted_from"].startswith("I"))
        with Image.open(result["files_to_read"][0]["path"]) as tile:
            self.assertEqual(tile.mode, "L")
            self.assertEqual(tile.getextrema(), (0, 255))

    def test_small_tiff_and_bmp_are_converted_to_png(self):
        for name in ("scan.tiff", "scan.bmp"):
            with self.subTest(name=name):
                source = self.tmp / name
                Image.new("RGB", (120, 80), "white").save(source)

                result = self.prepared(source)

                path = Path(result["files_to_read"][0]["path"])
                self.assertNotEqual(path, source.resolve())
                with Image.open(path) as tile:
                    self.assertEqual(tile.format, "PNG")
                self.assertTrue(any("已转换" in note for note in result["notes"]))

    def test_large_photo_falls_back_to_jpeg_under_size_limit(self):
        source = self.tmp / "photo.png"
        rng = np.random.default_rng(1)
        yy, xx = np.mgrid[0:1800, 0:2000]
        base = np.stack([xx * 255 / 2000, yy * 255 / 1800, (xx + yy) * 127 / 3800], -1)
        Image.fromarray(np.clip(base + rng.normal(0, 3, base.shape), 0, 255).astype(np.uint8)).save(source)
        self.assertGreater(source.stat().st_size, image_io.MAX_BYTES)

        result = self.prepared(source)

        path = Path(result["files_to_read"][0]["path"])
        self.assertEqual(path.suffix, ".jpg")
        self.assertLessEqual(path.stat().st_size, image_io.MAX_BYTES)
        self.assertTrue(any("JPEG" in note for note in result["notes"]))

    def test_multipage_tiff_is_expanded_in_page_order(self):
        source = self.tmp / "pages.tiff"
        first = Image.new("RGB", (100, 100), "white")
        first.save(source, save_all=True, append_images=[Image.new("RGB", (100, 100), "black")])

        result = self.prepared(source)

        self.assertEqual([item["page"] for item in result["files_to_read"]], [1, 2])
        colors = []
        for item in result["files_to_read"]:
            with Image.open(item["path"]) as page:
                colors.append(page.convert("L").getpixel((0, 0)))
        self.assertEqual(colors, [255, 0])

    def test_animation_reads_first_frame_unless_all_frames(self):
        source = self.tmp / "animated.gif"
        Image.new("RGB", (20, 20), "white").save(
            source, save_all=True, append_images=[Image.new("RGB", (20, 20), "black")], duration=100, loop=0
        )

        first_only = self.prepared(source)
        every_frame = self.prepared(source, all_frames=True)

        self.assertEqual(first_only["source"]["frames"], 2)
        self.assertEqual(len(first_only["files_to_read"]), 1)
        with Image.open(first_only["files_to_read"][0]["path"]) as frame:
            self.assertEqual(frame.convert("RGB").getpixel((0, 0)), (255, 255, 255))
        self.assertEqual([item["page"] for item in every_frame["files_to_read"]], [1, 2])

    def test_default_output_dir_is_new_system_temp_dir(self):
        source = self.tmp / "scan.bmp"
        Image.new("RGB", (50, 50), "white").save(source)

        result = prepare(source)

        output_dir = Path(result["output_dir"])
        self.addCleanup(shutil.rmtree, output_dir, ignore_errors=True)
        self.assertTrue(output_dir.name.startswith("img2md-"))
        self.assertEqual(output_dir.parent.resolve(), Path(tempfile.gettempdir()).resolve())
        self.assertFalse(output_dir.resolve().is_relative_to(source.parent.resolve()))
        self.assertEqual(sorted(path.name for path in self.tmp.iterdir()), ["scan.bmp"])

    def test_output_dir_must_be_empty(self):
        source = self.tmp / "scan.bmp"
        Image.new("RGB", (50, 50), "white").save(source)
        occupied = self.tmp / "occupied"
        occupied.mkdir()
        (occupied / "keep.txt").write_text("x", encoding="utf-8")

        with self.assertRaises(ValueError):
            prepare(source, output_dir=occupied)
        self.assertEqual([path.name for path in occupied.iterdir()], ["keep.txt"])

    def test_cli_prints_one_json_object_and_reports_errors(self):
        source = self.tmp / "small.png"
        Image.new("RGB", (30, 30), "white").save(source)

        ok = run_script("prepare_image.py", source)
        missing = run_script("prepare_image.py", self.tmp / "missing.png")

        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout)["files_to_read"][0]["path"], str(source.resolve()))
        self.assertEqual(missing.returncode, 1)
        self.assertIn("error", json.loads(missing.stdout))


class SplitTests(TempDirCase):
    def test_plain_tall_image_has_no_redundant_tail(self):
        source = self.tmp / "tall.png"
        Image.new("RGB", (200, 2000), "white").save(source)

        result = self.prepared(source, tier="standard")

        self.assertEqual(self.boxes(result), [[0, 0, 200, 1000], [0, 1000, 200, 2000]])

    def test_document_is_cut_in_blank_gaps_with_exact_offsets(self):
        source = self.tmp / "doc.png"
        document(1000, 3000).save(source)
        pixels = np.asarray(Image.open(source))

        result = self.prepared(source, tier="standard")

        boxes = self.boxes(result)
        self.assertGreater(len(boxes), 1)
        self.assert_tiles_fit(result)
        self.assertEqual(boxes[0][1], 0)
        self.assertEqual(boxes[-1][3], 3000)
        for previous, current in zip(boxes, boxes[1:]):
            self.assertEqual(previous[3], current[1])
            cut = current[1]
            self.assertTrue((pixels[cut - 4:cut + 4] == 255).all())
        for item in result["files_to_read"]:
            left, top, right, bottom = item["box"]
            with Image.open(item["path"]) as tile:
                np.testing.assert_array_equal(np.asarray(tile), pixels[top:bottom, left:right])
        self.assertFalse(any("交叠" in note for note in result["notes"]))

    def test_forced_cut_through_content_overlaps(self):
        source = self.tmp / "noise.png"
        noise(300, 4000).save(source)

        result = self.prepared(source)

        boxes = self.boxes(result)
        self.assert_tiles_fit(result)
        limit = split_image.max_band(300, *TIERS["high"])
        for previous, current in zip(boxes, boxes[1:]):
            overlap = previous[3] - current[1]
            self.assertGreater(overlap, 0)
            self.assertLessEqual(overlap, limit // 4)
        self.assertTrue(any("交叠" in note for note in result["notes"]))

    def test_wide_two_column_page_is_cut_at_gutter(self):
        source = self.tmp / "two-column.png"
        page = Image.new("L", (4000, 1200), 255)
        document(4000, 1200, image=page, left=40, right=1900)
        document(4000, 1200, image=page, left=2100, right=3960)
        page.save(source)

        result = self.prepared(source)

        (first, second) = self.boxes(result)
        self.assertEqual((first[0], first[1], first[3]), (0, 0, 1200))
        self.assertEqual(first[2], second[0])
        self.assertTrue(1900 < first[2] < 2100)
        self.assertEqual(second[2:], [4000, 1200])
        self.assert_tiles_fit(result)

    def test_wide_content_is_tiled_with_horizontal_overlap(self):
        source = self.tmp / "wide.png"
        noise(3000, 300).save(source)

        result = self.prepared(source)

        (first, second) = self.boxes(result)
        self.assertEqual((first[0], first[1], first[3]), (0, 0, 300))
        self.assertGreater(first[2], second[0])
        self.assertEqual(second[1:], [0, 3000, 300])
        self.assert_tiles_fit(result)

    def test_tiles_fit_and_cover_page_in_both_tiers(self):
        page = document(2480, 3508)
        for tier, (edge, tokens) in TIERS.items():
            with self.subTest(tier=tier):
                covered = np.zeros((3508, 2480), dtype=bool)
                for left, top, right, bottom in split_image.plan_tiles(page, edge, tokens):
                    self.assertTrue(split_image.fits(right - left, bottom - top, edge, tokens))
                    covered[top:bottom, left:right] = True
                self.assertTrue(covered.all())

    def test_max_edge_caps_tiles(self):
        source = self.tmp / "doc.png"
        document(1200, 2500).save(source)

        result = self.prepared(source, max_edge=2000)

        self.assertEqual(result["limits"]["max_edge"], 2000)
        self.assertTrue(all(max(r - l, b - t) <= 2000 for l, t, r, b in self.boxes(result)))
        self.assertEqual(self.prepared(source, tier="standard", max_edge=2000)["limits"]["max_edge"], 1568)
        with self.assertRaises(ValueError):
            self.prepared(source, max_edge=100)


class CropTests(TempDirCase):
    def crop(self, source, coords, system, output="crop.png", **options):
        output = self.tmp / output
        crop_image(source, output, *coords, system, **options)
        return Image.open(output)

    def white(self, size=(800, 600)):
        source = self.tmp / "source.png"
        Image.new("RGB", size, "white").save(source)
        return source

    def test_pixel_coordinates_under_1000_are_not_normalized(self):
        with self.crop(self.white(), (100, 100, 500, 500), "pixels") as cropped:
            self.assertEqual(cropped.size, (400, 400))

    def test_unit_coordinates(self):
        with self.crop(self.white(), (0.25, 0.25, 0.75, 0.75), "unit") as cropped:
            self.assertEqual(cropped.size, (400, 300))

    def test_thousand_coordinates_round_outward(self):
        with self.crop(self.white(), (333, 0, 667, 1000), "thousand") as cropped:
            self.assertEqual(cropped.size, (534 - 266, 600))

    def test_pixel_coordinates_outside_image_are_rejected(self):
        with self.assertRaises(ValueError):
            crop_image(self.white((500, 400)), self.tmp / "crop.png", 0, 0, 1000, 1000, "pixels")

    def test_cli_requires_explicit_coordinate_system(self):
        output = self.tmp / "crop.png"

        completed = run_script("extract_utils.py", "crop", self.white(), output, 0, 0, 10, 10)

        self.assertEqual(completed.returncode, 2)
        self.assertIn("--coords", completed.stderr)
        self.assertFalse(output.exists())

    def test_crop_uses_upright_coordinates(self):
        source = self.tmp / "photo.jpg"
        oriented_jpeg(source)

        with self.crop(source, (0, 0, 40, 80), "pixels") as cropped:
            self.assertEqual(cropped.size, (40, 80))
        with self.assertRaises(ValueError):
            crop_image(source, self.tmp / "bad.png", 0, 0, 80, 40, "pixels")

    def test_crop_selects_page(self):
        source = self.tmp / "pages.tiff"
        first = Image.new("RGB", (100, 100), "white")
        first.save(source, save_all=True, append_images=[Image.new("RGB", (100, 100), "black")])

        with self.crop(source, (0, 0, 1, 1), "unit", page=2) as cropped:
            self.assertEqual(cropped.convert("L").getpixel((0, 0)), 0)

    def test_jpeg_output_flattens_transparency(self):
        source = self.tmp / "clear.png"
        Image.new("RGBA", (40, 40), (0, 0, 0, 0)).save(source)

        with self.crop(source, (0, 0, 1000, 1000), "thousand", output="crop.jpg") as cropped:
            self.assertEqual(cropped.mode, "RGB")
            self.assertGreater(min(cropped.getpixel((20, 20))), 240)


class PackageTests(TempDirCase):
    def test_zip_contains_markdown_and_relative_assets(self):
        md_path = self.tmp / "report.md"
        image_path = self.tmp / "img" / "chart.png"
        image_path.parent.mkdir()
        md_path.write_text("![chart](img/chart.png)\n", encoding="utf-8")
        Image.new("RGB", (10, 10), "white").save(image_path)
        zip_path = self.tmp / "out" / "report.zip"

        package_zip(md_path, zip_path)

        with zipfile.ZipFile(zip_path) as archive:
            self.assertEqual(set(archive.namelist()), {"report.md", "img/chart.png"})


if __name__ == "__main__":
    unittest.main()
