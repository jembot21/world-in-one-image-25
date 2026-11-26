#!/usr/bin/env python3
"""Render one complete, deterministic newspaper using only bundled resources."""

import hashlib
import importlib
import io
import json
import math
import os
from pathlib import Path
import platform
import sys
import tempfile
import zipfile
from functools import lru_cache


ROOT = Path(__file__).resolve().parent
OUTPUT = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", os.getcwd())).resolve()
sys.dont_write_bytecode = True  # Never leave import caches beside the output.
SIZE = 1024
SCALE = 3  # Supersample type and silhouettes, then reduce once for clean edges.
MARGIN = 28
PAPER = "#ffffff"
INK = "#101010"
RED = INK  # The existing illustration accents now print in black ink.
WATER = "#eeeeee"
GREY = "#737373"
SCENE_WIDTH = 120
SCENE_HEIGHT = 50
SCENE_GAP = 9


def load_pillow():
    """Use the bundled Pillow wheel, cached outside the project and output."""
    tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    wheels = sorted((ROOT / "vendor").glob(f"*-{tag}-{tag}-*manylinux*x86_64.whl"))
    if (sys.implementation.name != "cpython" or sys.platform != "linux"
            or platform.machine().lower() not in ("x86_64", "amd64") or not wheels):
        raise RuntimeError("Use CPython 3.12/3.13 on Linux x86_64 for bundled Pillow.")
    wheel = wheels[0]
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    # TMPDIR can be overridden: explicitly reject a cache in either forbidden tree.
    for candidate in (Path(tempfile.gettempdir()), Path("/tmp"), Path("/var/tmp")):
        candidate = candidate.resolve()
        if not candidate.is_relative_to(ROOT) and not candidate.is_relative_to(OUTPUT):
            temporary_root = candidate
            break
    else:
        raise RuntimeError("No OS temporary directory outside the repo and output.")
    cache_root = temporary_root / "world-in-one-image-25-pillow"
    cache_root.mkdir(parents=True, exist_ok=True)
    cache = cache_root / f"{tag}-{digest}"
    if not (cache / "PIL" / "Image.py").is_file():
        with tempfile.TemporaryDirectory(prefix="unpack-", dir=cache_root) as staging:
            staging = Path(staging)
            with zipfile.ZipFile(wheel) as archive:
                for member in archive.infolist():
                    if not (staging / member.filename).resolve().is_relative_to(staging):
                        raise RuntimeError("Unsafe path in vendored Pillow wheel.")
                archive.extractall(staging)
            try:
                staging.rename(cache)
            except OSError:
                if not (cache / "PIL" / "Image.py").is_file():
                    raise
    sys.path.insert(0, str(cache))
    importlib.invalidate_caches()
    for name in list(sys.modules):
        if name == "PIL" or name.startswith("PIL."):
            del sys.modules[name]
    from PIL import Image, ImageDraw, ImageFont
    return Image, ImageDraw, ImageFont


Image, ImageDraw, ImageFont = load_pillow()


@lru_cache(maxsize=256)
def font(size, style="Regular"):
    return ImageFont.truetype(
        str(ROOT / "fonts" / f"LiberationSans-{style}.ttf"),
        round(size * SCALE),
        layout_engine=ImageFont.Layout.BASIC,
    )


def width(text, face):
    bounds = face.getbbox(text, anchor="lt")
    return max(face.getlength(text), bounds[2] - min(0, bounds[0])) / SCALE


def wrap(text, face, available):
    """Word wrap without elision: even a long single word must fit."""
    lines, line = [], ""
    for word in text.split():
        if width(word, face) > available:
            raise ValueError(f"Word too wide at this font size: {word}")
        trial = f"{line} {word}" if line else word
        if line and width(trial, face) > available:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    return lines


def block_height(lines, face, leading):
    if not lines:
        return 0
    # Share baselines even when lines start with different-height glyphs.
    bounds = [face.getbbox(line, anchor="ls") for line in lines]
    baseline = max(-box[1] / SCALE for box in bounds)
    return baseline + max(index * leading + box[3] / SCALE
                          for index, box in enumerate(bounds))


class Page:
    def __init__(self):
        self.image = Image.new("RGB", (SIZE * SCALE, SIZE * SCALE), PAPER)
        self.draw = ImageDraw.Draw(self.image)

    def rule(self, x1, y1, x2, y2, color=INK, thickness=0.5):
        self.draw.line(tuple(round(v * SCALE) for v in (x1, y1, x2, y2)),
                       fill=color, width=max(1, round(thickness * SCALE)))

    def rectangle(self, box, fill=None, outline=None, thickness=0.5):
        self.draw.rectangle(tuple(round(v * SCALE) for v in box), fill=fill,
                            outline=outline, width=max(1, round(thickness * SCALE)))

    def ellipse(self, box, fill=None, outline=None, thickness=1):
        self.draw.ellipse(tuple(round(v * SCALE) for v in box), fill=fill,
                          outline=outline, width=max(1, round(thickness * SCALE)))

    def polygon(self, vertices, color=INK):
        self.draw.polygon([(round(x * SCALE), round(y * SCALE))
                           for x, y in vertices], fill=color)

    def polyline(self, vertices, color=INK, thickness=1):
        self.draw.line([(round(x * SCALE), round(y * SCALE))
                        for x, y in vertices], fill=color,
                       width=max(1, round(thickness * SCALE)), joint="curve")

    def dashed(self, vertices, color=RED, thickness=1, dash=4, gap=3):
        """Keep the dash phase continuous around bends in a route."""
        distance = 0
        period = dash + gap
        for (x1, y1), (x2, y2) in zip(vertices, vertices[1:]):
            length = math.hypot(x2 - x1, y2 - y1)
            cursor = 0
            while cursor < length:
                phase = (distance + cursor) % period
                drawing = phase < dash
                step = min(length - cursor, (dash if drawing else period) - phase)
                if drawing and step > 0:
                    a, b = cursor / length, (cursor + step) / length
                    self.rule(x1 + (x2 - x1) * a, y1 + (y2 - y1) * a,
                              x1 + (x2 - x1) * b, y1 + (y2 - y1) * b,
                              color, thickness)
                cursor += max(step, 0.000001)
            distance += length

    def text(self, text, x, y, face, color=INK, box=None, anchor="lt"):
        position = (round(x * SCALE), round(y * SCALE))
        bounds = self.draw.textbbox(position, text, font=face, anchor=anchor)
        if box is not None:
            actual = tuple(v / SCALE for v in bounds)
            if (actual[0] < box[0] - 0.5 or actual[1] < box[1] - 0.5
                    or actual[2] > box[2] + 0.5 or actual[3] > box[3] + 0.5):
                raise ValueError(f"Text exceeds its allocated box: {text!r}")
        self.draw.text(position, text, font=face, fill=color, anchor=anchor)

    def line(self, text, box, maximum, minimum=7, style="Regular", color=INK,
             align="left"):
        x, y, right, bottom = box
        for step in range(round(maximum * SCALE), round(minimum * SCALE) - 1, -1):
            face = font(step / SCALE, style)
            if (width(text, face) <= right - x
                    and face.getbbox(text, anchor="lt")[3] / SCALE <= bottom - y):
                if align == "center":
                    x += (right - x - width(text, face)) / 2
                elif align == "right":
                    x = right - width(text, face)
                self.text(text, x, y, face, color, box)
                return
        raise ValueError(f"Cannot fit text: {text!r}")

    def lines(self, lines, x, y, face, leading, box):
        baseline = max((-face.getbbox(line, anchor="ls")[1] / SCALE
                        for line in lines), default=0)
        for index, line in enumerate(lines):
            self.text(line, x, y + baseline + index * leading, face,
                      box=box, anchor="ls")

    def scene(self, kind, box):
        """Paste a whole drawing into its reserved panel, away from all text."""
        x, y, right, bottom = box
        if (right - x != SCENE_WIDTH or bottom - y != SCENE_HEIGHT
                or x < 0 or y < 0 or right > SIZE or bottom > SIZE):
            raise ValueError("Invalid illustration zone.")
        illustration = Scene()
        getattr(illustration, "draw_" + kind)()
        self.image.paste(illustration.image, (round(x * SCALE), round(y * SCALE)))

    def number_motif(self, kind, x, y):
        """A quiet 16×12 companion, placed after the centered figure."""
        if kind in ("up", "down"):
            for offset in (0, 5):
                vertices = ([(x + 3, y + 4 + offset), (x + 7, y + offset),
                             (x + 11, y + 4 + offset)] if kind == "up" else
                            [(x + 3, y + offset), (x + 7, y + 4 + offset),
                             (x + 11, y + offset)])
                self.polyline(vertices, thickness=1.1)
        elif kind == "coin":
            self.ellipse((x + 2, y, x + 14, y + 12), outline=INK)
            self.ellipse((x + 4, y + 2, x + 12, y + 10), outline=GREY,
                         thickness=0.5)
            self.rule(x + 8, y + 3, x + 8, y + 9, thickness=1)
        elif kind == "controller":
            self.polygon([(x + 3, y + 1), (x + 13, y + 1), (x + 16, y + 10),
                          (x + 12, y + 11), (x + 10, y + 8), (x + 6, y + 8),
                          (x + 4, y + 11), (x, y + 10)])
            self.rule(x + 3, y + 4, x + 7, y + 4, PAPER, 1)
            self.rule(x + 5, y + 2, x + 5, y + 6, PAPER, 1)
            self.ellipse((x + 11, y + 3, x + 12.5, y + 4.5), fill=PAPER)
            self.ellipse((x + 13, y + 5, x + 14.5, y + 6.5), fill=PAPER)
        elif kind == "bars":
            for left, top in ((1, 8), (6, 4), (11, 0)):
                self.rectangle((x + left, y + top, x + left + 3, y + 12), fill=INK)
        elif kind == "bolt":
            self.polygon([(x + 8, y), (x + 2, y + 7), (x + 7, y + 7),
                          (x + 5, y + 12), (x + 14, y + 4), (x + 9, y + 4)])

    def region_dots(self, top):
        """Quiet repeated clusters across the width, below the date line."""
        for cluster in range(24):
            x = MARGIN + 8 + cluster * 41
            for dx, dy in ((0, 3), (4, 0), (4, 6), (8, 3), (12, 3)):
                self.ellipse((x + dx, top + dy, x + dx + 1, top + dy + 1),
                             fill=GREY)


class Scene(Page):
    """Ten geometric editorial scenes on a common 120×50 drawing surface."""

    def __init__(self):
        self.image = Image.new("RGB", (SCENE_WIDTH * SCALE, SCENE_HEIGHT * SCALE),
                               PAPER)
        self.draw = ImageDraw.Draw(self.image)

    def draw_hormuz(self):
        self.rectangle((1, 1, 119, 49), fill=WATER)
        # Two coasts pinch the navigable channel into a central dogleg.
        self.polygon([(1, 1), (119, 1), (119, 9), (99, 9), (85, 14),
                      (74, 13), (62, 21), (51, 15), (30, 14), (18, 10), (1, 13)])
        self.polygon([(1, 36), (18, 32), (32, 36), (46, 31), (55, 28),
                      (66, 31), (76, 29), (89, 36), (105, 38), (119, 34),
                      (119, 49), (1, 49)])
        self.dashed([(3, 27), (25, 25), (45, 23), (58, 25), (71, 22),
                     (88, 23), (116, 25)], thickness=1.4, dash=5, gap=3)
        for x, y in ((26, 19), (87, 29)):
            self.polygon([(x, y + 2), (x + 17, y + 2), (x + 20, y + 4),
                          (x + 17, y + 6), (x, y + 6), (x - 2, y + 4)])
            self.rectangle((x + 2, y + 2.7, x + 5, y + 5.3), fill=PAPER)
            for deck in (8, 12, 16):
                self.rule(x + deck, y + 3, x + deck, y + 5, GREY, 0.6)

    def draw_warship(self):
        for y in (38, 44):
            self.polyline([(3, y), (13, y - 1.5), (25, y), (38, y - 1.5),
                           (51, y), (66, y - 1.5), (81, y), (97, y - 1.5),
                           (117, y)], GREY, 0.7)
        self.polygon([(15, 28), (100, 28), (89, 36), (29, 36)], GREY)
        self.rectangle((39, 20, 69, 28), fill=GREY)
        self.polygon([(45, 20), (50, 15), (60, 15), (64, 20)], GREY)
        self.rule(54, 10, 54, 19, thickness=1.3)
        self.rule(49, 12, 62, 12, thickness=0.8)
        self.rule(75, 25, 87, 19, thickness=1.5)
        self.rectangle((71, 25, 81, 28), fill=INK)
        for x in (45, 52, 59):
            self.rectangle((x, 21, x + 3, 23), fill=PAPER)
        self.ellipse((11, 7, 104, 43), outline=RED, thickness=1.2)
        for x, y, dx, dy in ((57, 5, 0, 5), (57, 40, 0, 5),
                             (8, 25, 6, 0), (101, 25, 6, 0)):
            self.rule(x, y, x + dx, y + dy, RED, 1)
        # A small drone above and clear of the target ring.
        self.rule(91, 8, 108, 8, thickness=1.4)
        self.polygon([(97, 5), (103, 5), (101, 11), (98, 11)])
        for x in (91, 108):
            self.ellipse((x - 4, 5, x + 4, 7), outline=INK, thickness=0.7)

    def draw_aid(self):
        self.rule(3, 45, 117, 45, thickness=0.7)
        for box, color in (((41, 32, 53, 44), INK), ((59, 22, 71, 44), INK),
                           ((77, 9, 89, 44), RED)):
            self.rectangle(box, fill=color)
        self.ellipse((3, 12, 36, 45), fill=PAPER, outline=INK, thickness=1.6)
        self.ellipse((6, 15, 33, 42), outline=GREY, thickness=0.6)
        # Draw a large euro sign as geometry, including its two crossbars.
        self.polyline([(26, 21), (21, 18), (15, 20), (12, 25), (12, 32),
                       (15, 37), (21, 39), (26, 36)], thickness=1.7)
        self.rule(9, 26, 24, 26, thickness=1.4)
        self.rule(9, 31, 23, 31, thickness=1.4)
        self.polygon([(96, 35), (112, 10), (116, 4), (115, 12), (101, 38)])
        self.polygon([(100, 28), (94, 30), (95, 38), (101, 35)])
        self.polygon([(102, 31), (108, 34), (103, 39), (100, 36)])
        self.rule(104, 24, 112, 12, PAPER, 0.7)

    def draw_space(self):
        for x, y, radius in ((6, 7, 0.8), (16, 18, 0.6), (43, 5, 0.7),
                             (57, 32, 0.8), (109, 32, 0.6), (81, 43, 0.7),
                             (115, 5, 0.6), (9, 40, 0.6)):
            self.ellipse((x - radius, y - radius, x + radius, y + radius), fill=INK)
        self.dashed([(40, 33), (48, 23), (58, 17)], GREY, 0.7, dash=2, gap=3)
        # Rocket lifting toward the station, with a long red exhaust.
        self.polygon([(29, 12), (35, 20), (35, 34), (23, 34), (23, 20)])
        self.polygon([(23, 27), (18, 37), (23, 35)])
        self.polygon([(35, 27), (40, 37), (35, 35)])
        self.ellipse((26, 21, 32, 27), fill=PAPER)
        self.polygon([(25, 35), (33, 35), (31, 43), (29, 48), (27, 42)], RED)
        self.rule(26, 32, 32, 32, PAPER, 0.6)
        self.rule(61, 16, 109, 16, thickness=1.5)
        self.rectangle((79, 11, 92, 21), fill=INK)
        self.rectangle((83, 6, 88, 26), fill=INK)
        self.rectangle((57, 6, 75, 25), fill=GREY, outline=INK, thickness=0.8)
        self.rectangle((97, 6, 115, 25), fill=GREY, outline=INK, thickness=0.8)
        for left in (57, 97):
            for x in (left + 6, left + 12):
                self.rule(x, 7, x, 24, PAPER, 0.6)
            self.rule(left + 1, 15.5, left + 17, 15.5, PAPER, 0.6)

    def draw_pacific(self):
        self.rectangle((1, 1, 119, 49), fill=WATER)
        for y in (12, 39, 45):
            self.rule(5, y, 23, y, GREY, 0.5)
            self.rule(50, y + 1, 68, y + 1, GREY, 0.5)
        self.polygon([(9, 27), (15, 20), (23, 21), (30, 17), (39, 23),
                      (36, 31), (27, 35), (15, 32)])
        self.polygon([(74, 30), (82, 23), (90, 26), (103, 23), (113, 31),
                      (109, 39), (96, 41), (85, 37)])
        self.dashed([(26, 24), (38, 13), (53, 7), (67, 7), (80, 11),
                     (93, 20), (97, 30)], RED, 1.3, dash=4, gap=3)
        self.ellipse((23, 21, 29, 27), fill=RED)
        self.rectangle((91, 17, 109, 37), fill=PAPER, outline=INK, thickness=0.9)
        self.polygon([(104, 17), (109, 22), (104, 22)], GREY)
        for y in (23, 26, 29):
            self.rule(94, y, 103, y, thickness=0.7)
        self.ellipse((99, 30, 105, 36), fill=RED)

    def draw_court(self):
        self.polygon([(15, 14), (60, 2), (105, 14)])
        self.rectangle((19, 16, 101, 19), fill=INK)
        for x in (26, 56, 86):
            self.rectangle((x, 20, x + 8, 39), fill=GREY)
            self.rule(x + 3, 21, x + 3, 38, PAPER, 0.7)
        self.rectangle((17, 40, 103, 43), fill=INK)
        self.rectangle((12, 45, 108, 47), fill=INK)
        # Paper edging separates the foreground scale from all three columns.
        self.rule(60, 19, 60, 40, PAPER, 3.5)
        self.rule(42, 24, 78, 24, PAPER, 3.5)
        self.rule(60, 19, 60, 40, thickness=1.5)
        self.rule(42, 24, 78, 24, thickness=1.5)
        self.ellipse((57.5, 19, 62.5, 24), fill=RED)
        for x in (46, 74):
            self.polyline([(x - 6, 34), (x, 25), (x + 6, 34)], PAPER, 2.5)
            self.polyline([(x - 6, 34), (x, 25), (x + 6, 34)], thickness=0.8)
            self.polygon([(x - 7, 34), (x + 7, 34), (x + 4, 38), (x - 4, 38)])
        self.polygon([(60, 37), (53, 41), (67, 41)])

    def draw_slide(self):
        self.rule(5, 3, 5, 47, GREY, 0.7)
        self.rule(5, 47, 117, 47, GREY, 0.7)
        self.dashed([(7, 23), (116, 23)], GREY, 0.8, dash=3, gap=3)
        self.line("IPO price", (63, 9, 116, 20), 8.5, minimum=8.5, color=GREY)
        self.polyline([(9, 7), (23, 13), (34, 10), (47, 19), (57, 16),
                       (70, 28), (79, 25), (91, 35), (102, 33), (111, 40)],
                      thickness=1.8)
        self.rule(111, 34, 111, 43, RED, 2)
        self.polygon([(106, 40), (116, 40), (111, 46)], RED)

    def draw_shop(self):
        self.rectangle((12, 14, 102, 45), fill=PAPER, outline=INK, thickness=1)
        self.rectangle((16, 5, 98, 13), fill=INK)
        self.rectangle((19, 25, 48, 39), fill=WATER, outline=INK, thickness=0.8)
        self.rule(33.5, 25, 33.5, 39, thickness=0.8)
        self.rectangle((58, 25, 79, 45), fill=INK)
        self.rectangle((61, 28, 75, 40), fill=PAPER)
        self.polygon([(16, 14), (98, 14), (106, 22), (8, 22)])
        for x in range(19, 95, 16):
            self.polygon([(x, 14), (x + 7, 14), (x + 10, 22), (x - 2, 22)], PAPER)
        for x in range(10, 104, 8):
            self.ellipse((x, 19, x + 8, 25), fill=INK if (x - 10) % 16 == 0 else PAPER)
        self.rule(8, 46, 113, 46, GREY, 0.7)
        # The shield stands at the doorway, not over the shop window.
        shield = [(84, 24), (98, 28), (97, 37), (93, 43), (84, 48),
                  (75, 43), (71, 37), (70, 28)]
        self.polygon(shield, PAPER)
        self.polyline(shield + [shield[0]], thickness=1.5)
        self.polyline([(77, 35), (82, 40), (91, 30)], RED, 2.1)

    def draw_trophy(self):
        # Open handles remain distinct from the bowl.
        self.ellipse((25, 9, 48, 29), outline=INK, thickness=2)
        self.ellipse((72, 9, 95, 29), outline=INK, thickness=2)
        self.polygon([(39, 6), (81, 6), (77, 23), (71, 29), (63, 32),
                      (57, 32), (49, 29), (43, 23)])
        self.rule(46, 9, 74, 9, PAPER, 0.8)
        self.rectangle((57, 29, 63, 39), fill=INK)
        self.polygon([(57, 36), (49, 41), (71, 41), (63, 36)])
        self.polygon([(38, 17), (49, 19), (44, 37), (39, 33), (34, 35)], RED)
        self.polygon([(71, 19), (82, 17), (86, 35), (81, 33), (76, 37)], RED)
        self.rectangle((25, 43, 95, 47), fill=INK)

    def draw_energy(self):
        self.polyline([(9, 25), (42, 5), (75, 25)], thickness=1.8)
        self.polyline([(16, 22), (16, 45), (68, 45), (68, 22)], thickness=1.5)
        self.rectangle((53, 6, 59, 16), fill=INK)
        self.rectangle((34, 31, 46, 45), fill=INK)
        self.rectangle((22, 29, 29, 36), outline=INK, thickness=0.9)
        self.rectangle((52, 29, 59, 36), outline=INK, thickness=0.9)
        # Roof lightning and a thermometer whose upper mercury is red.
        self.polygon([(45, 2), (35, 15), (43, 15), (39, 26),
                      (55, 10), (46, 10)], RED)
        self.rectangle((91, 7, 101, 36), fill=PAPER, outline=INK, thickness=1)
        self.ellipse((88, 32, 104, 48), fill=PAPER, outline=INK, thickness=1)
        self.rectangle((94, 22, 98, 40), fill=GREY)
        self.rectangle((94, 10, 98, 22), fill=RED)
        self.ellipse((92, 36, 100, 44), fill=GREY)
        for y in (11, 18, 25, 32):
            self.rule(104, y, 111, y, thickness=0.7)


def layout_lead(story, available):
    """Share the headline area with the scene; give the prose a full column."""
    head_size, body_size = 21, 14
    head, body = font(head_size, "Bold"), font(body_size)
    text_width = available - SCENE_WIDTH - SCENE_GAP
    head_lines = wrap(story["headline"], head, text_width - 0.5)
    body_lines = wrap(story["body"], body, available - 0.5)
    head_leading, body_leading = 20, 17
    body_y = max(12 + block_height(head_lines, head, head_leading),
                 SCENE_HEIGHT) + 2
    body_height = block_height(body_lines, body, body_leading)
    return {"head": head, "body": body,
            "head_lines": head_lines, "body_lines": body_lines,
            "head_leading": head_leading, "body_leading": body_leading,
            "body_y": body_y, "text_width": text_width,
            "height": body_y + body_height + 1}


def draw_lead(page, story, box, scene, layout):
    x, y, right, bottom = box
    # Keep both metadata labels above the headline, clear of the illustration.
    head_right = x + layout["text_width"]
    stat_width = width(story["stat"], font(11.3, "Bold")) + 0.5
    stat_left = head_right - stat_width
    page.line(story["kicker"], (x, y, stat_left - 8, y + 10), 9.4, minimum=9.4,
              style="Bold")
    page.line(story["stat"], (stat_left, y, head_right, y + 11),
              11.3, minimum=11.3, style="Bold", align="right")
    head_y, body_y = y + 12, y + layout["body_y"]
    page.lines(layout["head_lines"], x, head_y, layout["head"],
               layout["head_leading"], (x, head_y, head_right, body_y - 2))
    page.lines(layout["body_lines"], x, body_y, layout["body"],
               layout["body_leading"], (x, body_y, right, bottom - 1))
    scene_left = right - SCENE_WIDTH
    page.scene(scene, (scene_left, y, right, y + SCENE_HEIGHT))


def draw_leads(page, stories, top):
    gutter, minimum_gap = 24, 4
    column_width = (SIZE - 2 * MARGIN - gutter) / 2
    scenes = ("hormuz", "warship", "aid", "space", "pacific",
              "court", "slide", "shop", "trophy", "energy")
    layouts = [layout_lead(story, column_width) for story in stories]
    columns = (tuple(range(0, 10, 2)), tuple(range(1, 10, 2)))
    totals = [sum(layouts[index]["height"] for index in column)
              for column in columns]
    height = max(totals) + 4 * minimum_gap
    bottom = top + height
    for column, indices in enumerate(columns):
        x = MARGIN + column * (column_width + gutter)
        # Equalize only the inter-card gaps; each card keeps its measured height.
        gap = (height - totals[column]) / (len(indices) - 1)
        y = top
        for row, index in enumerate(indices):
            card_bottom = y + layouts[index]["height"]
            draw_lead(page, stories[index], (x, y, x + column_width, card_bottom),
                      scenes[index], layouts[index])
            if row < len(indices) - 1:
                page.rule(x, card_bottom + gap / 2,
                          x + column_width, card_bottom + gap / 2)
            y = card_bottom + gap
    page.rule(SIZE / 2, top, SIZE / 2, bottom)
    return bottom


def rich_lines(story, size, available):
    """Wrap the inline brief headline in bold and its entire body in regular."""
    words = ([(word, "Bold") for word in story["headline"].split()]
             + [(word, "Regular") for word in story["body"].split()])
    lines, line, used = [], [], 0
    for word, style in words:
        face = font(size, style)
        advance = width(word, face)
        if advance > available:
            raise ValueError(f"Brief word too wide: {word}")
        gap = width(" ", font(size, line[-1][1])) if line else 0
        if line and used + gap + advance > available:
            lines.append(line)
            line, used, gap = [], 0, 0
        line.append((word, style, gap))
        used += gap + advance
    if line:
        lines.append(line)
    return lines


def draw_briefs(page, stories, top, bottom):
    gutter = 22
    column_width = (SIZE - 2 * MARGIN - 2 * gutter) / 3
    # Fixed type and leading: edits must fit the page without shrinking copy.
    size, leading, minimum_gap = 12.5, 15.5, 6
    ascent = max(font(size, style).getmetrics()[0]
                 for style in ("Regular", "Bold")) / SCALE
    wrapped = [rich_lines(story, size, column_width - 0.5) for story in stories]
    heights = [len(lines) * leading for lines in wrapped]
    choices = []
    for first in range(1, len(stories) - 1):
        for second in range(first + 1, len(stories)):
            breaks = (0, first, second, len(stories))
            totals = [sum(heights[a:b]) + (b - a - 1) * minimum_gap
                      for a, b in zip(breaks, breaks[1:])]
            choices.append((max(totals), sum(t * t for t in totals), breaks))
    required, _, breaks = min(choices)
    if required > bottom - top:
        raise ValueError("Briefs cannot fit without losing content.")
    for column, (start, end) in enumerate(zip(breaks, breaks[1:])):
        x = MARGIN + column * (column_width + gutter)
        if column:
            page.rule(x - gutter / 2, top, x - gutter / 2, bottom)
        # Distribute spare height between briefs, never as an empty bottom block.
        gap = (bottom - top - sum(heights[start:end])) / max(1, end - start - 1)
        y = top
        for index in range(start, end):
            for line in wrapped[index]:
                cursor = x
                baseline = y + ascent
                for word, style, space in line:
                    cursor += space
                    face = font(size, style)
                    # A glyph's top varies (notably for "a"); share one baseline.
                    page.text(word, cursor, baseline, face,
                              box=(x, top, x + column_width, bottom), anchor="ls")
                    cursor += width(word, face)
                y += leading
            if index < end - 1:
                page.rule(x, y + gap / 2 - 1,
                          x + column_width, y + gap / 2 - 1,
                          thickness=0.35)
            y += gap


def render(data):
    if (len(data["leads"]), len(data["numbers"]), len(data["briefs"])) != (10, 8, 18):
        raise ValueError("Expected exactly 10 leads, 8 numbers and 18 briefs.")
    page = Page()
    # A compact nameplate and ruled dateline leave the page to the reporting.
    page.rule(MARGIN, 8, SIZE - MARGIN, 8, thickness=1.5)
    page.rule(MARGIN, 11, SIZE - MARGIN, 11)
    page.line("THE WORLD IN ONE IMAGE", (MARGIN, 17, SIZE - MARGIN, 57),
              56, minimum=56, style="Bold", align="center")
    page.rule(MARGIN, 62, SIZE - MARGIN, 62, thickness=1.2)
    page.line(data["date"], (MARGIN, 69, 520, 80), 11.5, minimum=11.5,
              style="Bold")
    page.line("All the world news that fits in one image",
              (550, 69, SIZE - MARGIN, 80), 11.5, minimum=11.5,
              style="Italic", align="right")
    page.rule(MARGIN, 82, SIZE - MARGIN, 82)

    lead_bottom = draw_leads(page, data["leads"], 89)
    numbers_top = lead_bottom + 7
    numbers_bottom = numbers_top + 87

    page.rule(MARGIN, numbers_top, SIZE - MARGIN, numbers_top, thickness=1)
    page.line("THE DAY IN NUMBERS",
              (MARGIN, numbers_top + 5, 400, numbers_top + 14),
              11, minimum=11, style="Bold")
    page.rule(MARGIN, numbers_top + 18, SIZE - MARGIN, numbers_top + 18)
    page.rule(MARGIN, numbers_bottom, SIZE - MARGIN, numbers_bottom, thickness=1)
    number_width = (SIZE - 2 * MARGIN) / 8
    for index, number in enumerate(data["numbers"]):
        x = MARGIN + index * number_width
        y = numbers_top + 23
        page.line(number["figure"], (x + 6, y, x + number_width - 6, y + 23),
                  24, minimum=24, style="Bold", align="center")
        caption_face = font(11.5)
        caption_lines = wrap(number["caption"], caption_face, number_width - 12.5)
        baseline = max(-caption_face.getbbox(line, anchor="ls")[1] / SCALE
                       for line in caption_lines)
        for row, line in enumerate(caption_lines):
            page.text(line, x + (number_width - width(line, caption_face)) / 2,
                      y + 23 + baseline + row * 12.5, caption_face,
                      box=(x + 6, y + 23, x + number_width - 6, numbers_bottom - 3),
                      anchor="ls")
        if index:
            page.rule(x, numbers_top + 18, x, numbers_bottom)

    briefs_heading = numbers_bottom + 7
    page.line("AROUND THE WORLD — 18 BRIEFS",
              (MARGIN, briefs_heading, 650, briefs_heading + 12),
              12.5, minimum=12.5, style="Bold")
    page.rule(MARGIN, briefs_heading + 15, SIZE - MARGIN, briefs_heading + 15)
    draw_briefs(page, data["briefs"], briefs_heading + 21, 1005)
    page.rule(MARGIN, 1009, SIZE - MARGIN, 1009)
    return page.image.resize((SIZE, SIZE), Image.Resampling.LANCZOS)


def main():
    with (ROOT / "stories.json").open(encoding="utf-8") as source:
        data = json.load(source)
    image = render(data)
    encoded = io.BytesIO()
    image.save(encoded, format="PNG", optimize=False, compress_level=9)
    if encoded.tell() > 1024 * 1024:
        image = image.quantize(colors=256, method=Image.Quantize.MEDIANCUT,
                               dither=Image.Dither.NONE)
        encoded = io.BytesIO()
        image.save(encoded, format="PNG", optimize=False, compress_level=9)
    payload = encoded.getvalue()
    if len(payload) > 1024 * 1024:
        raise RuntimeError("PNG exceeds the 1 MiB output limit.")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    destination = OUTPUT / "entry.png"
    # Encode in memory and write directly to the final path: no output-side temps.
    with destination.open("wb") as result:
        result.write(payload)
    destination.chmod(0o644)


if __name__ == "__main__":
    main()
