"""Deterministic synthetic product images for the offline evaluation harness.

generate(recipe, seed) -> encoded image bytes (JPEG or PNG).

The images stand in for what a search provider would return: retailer
packshots on pure #FFFFFF, white cartons on white, brand banners, cluttered
shelf photos, random-noise junk and tiny thumbnails. They are drawn with PIL
and numpy only, from a built-in 5x7 bitmap font (no system fonts) and a
seeded PCG64 generator, so the same recipe and seed render the same pixels on
any platform; the encoded bytes are identical for the same Pillow build.

A recipe is a dict with a "kind" and optional parameters:

    kind           one of RECIPES
    size           [width, height] in pixels
    fill           packshots: product height as a fraction of the frame height
    shape          bottle | carton | jar | bag | box | can | pouch
    label_text     label lines separated by "|", e.g. "ALMARAI|FULL FAT MILK|1L"
    cap_color, body_color, label_color, accent_color   "#rrggbb"
    background     packshot_white only: "white" (default) or "transparent" (RGBA PNG)
    count          banner: number of products; lifestyle_clutter: number of other items
    headline       banner: headline text
    softness       packshots: lens blur radius in px (default long side / 1400)
    format         "JPEG" (default) or "PNG"
    quality        JPEG quality (default 90)

The label text is drawn on the product so an image carries the brand, variant
and size of the fixture candidate it belongs to.
"""

from __future__ import annotations

import hashlib
import io
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

RECIPES = (
    "packshot_white",
    "carton_white_on_white",
    "banner",
    "lifestyle_clutter",
    "noise",
    "tiny_120",
    "tall_bottle_600x1200",
)

DEFAULT_SIZES = {
    "packshot_white": (1200, 1200),
    "carton_white_on_white": (1200, 1200),
    "banner": (1200, 630),
    "lifestyle_clutter": (1200, 900),
    "noise": (1000, 1000),
    "tiny_120": (120, 120),
    "tall_bottle_600x1200": (600, 1200),
}

# ---------------------------------------------------------------------------
# 5x7 bitmap font (uppercase Latin, digits and common label punctuation)
# ---------------------------------------------------------------------------

_FONT_ROWS = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01110", "10001", "10000", "10000", "10000", "10001", "01110"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    "J": ("00111", "00010", "00010", "00010", "00010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "10001", "11001", "10101", "10011", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11111", "00010", "00100", "00010", "00001", "10001", "01110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
    ",": ("00000", "00000", "00000", "00000", "01100", "00100", "01000"),
    "%": ("11001", "11001", "00010", "00100", "01000", "10011", "10011"),
    "&": ("01100", "10010", "10100", "01000", "10101", "10010", "01101"),
    "/": ("00001", "00001", "00010", "00100", "01000", "10000", "10000"),
    "+": ("00000", "00100", "00100", "11111", "00100", "00100", "00000"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    "'": ("01100", "00100", "01000", "00000", "00000", "00000", "00000"),
    ":": ("00000", "01100", "01100", "00000", "01100", "01100", "00000"),
    "!": ("00100", "00100", "00100", "00100", "00100", "00000", "00100"),
    "(": ("00010", "00100", "01000", "01000", "01000", "00100", "00010"),
    ")": ("01000", "00100", "00010", "00010", "00010", "00100", "01000"),
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
}
_FONT = {ch: np.array([[c == "1" for c in row] for row in rows], dtype=bool) for ch, rows in _FONT_ROWS.items()}
_UNKNOWN = np.array([[True] * 5] + [[True, False, False, False, True]] * 5 + [[True] * 5], dtype=bool)


def _is_arabic(ch: str) -> bool:
    return "؀" <= ch <= "ۿ" or "ݐ" <= ch <= "ݿ" or "ﭐ" <= ch <= "﻿"


def _arabic_glyph(ch: str) -> np.ndarray:
    """A connected, script-like 4x7 glyph derived from the code point.

    Arabic cannot be shaped without a font engine; this keeps Arabic labels
    visually present (strokes on a baseline, dots above or below).
    """
    code = ord(ch)
    g = np.zeros((7, 4), dtype=bool)
    g[5, :] = True                              # baseline joins letters together
    stem = 1 + code % 4                         # vertical stroke height
    g[5 - stem:5, (code >> 2) % 4] = True
    if code % 3 == 0:
        g[0, (code >> 3) % 4] = True           # dot above
    elif code % 3 == 1:
        g[6, (code >> 4) % 4] = True           # dot below
    return g


def _glyphs(text: str) -> List[np.ndarray]:
    out = []
    for ch in text:
        if _is_arabic(ch):
            out.append(_arabic_glyph(ch))
        else:
            out.append(_FONT.get(ch.upper(), _UNKNOWN))
    return out


def text_width(text: str, scale: int) -> int:
    glyphs = _glyphs(text)
    if not glyphs:
        return 0
    return sum((g.shape[1] + 1) * scale for g in glyphs) - scale


def draw_text(canvas: np.ndarray, x: int, y: int, text: str, scale: int, color: Sequence[int]) -> None:
    """Draw text into an HxWx3 uint8 array; right-to-left for Arabic runs."""
    scale = max(1, int(scale))
    glyphs = _glyphs(text)
    if any(_is_arabic(ch) for ch in text):
        glyphs = glyphs[::-1]
    h, w = canvas.shape[:2]
    cx = int(x)
    for g in glyphs:
        big = np.kron(g, np.ones((scale, scale), dtype=bool))
        gh, gw = big.shape
        y0, x0 = int(y), cx
        y1, x1 = min(h, y0 + gh), min(w, x0 + gw)
        if x0 < w and y0 < h and x1 > 0 and y1 > 0:
            sub = big[max(0, -y0):y1 - y0, max(0, -x0):x1 - x0]
            region = canvas[max(0, y0):y1, max(0, x0):x1]
            region[sub] = color
        cx += (g.shape[1] + 1) * scale


def draw_text_centered(canvas: np.ndarray, cx: int, y: int, text: str, max_width: int, max_scale: int,
                       color: Sequence[int]) -> int:
    """Draw text centred on cx, shrinking the scale to fit max_width. Returns the used height."""
    scale = max(1, int(max_scale))
    while scale > 1 and text_width(text, scale) > max_width:
        scale -= 1
    tw = text_width(text, scale)
    draw_text(canvas, cx - tw // 2, y, text, scale, color)
    return 7 * scale


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rgb(value: Union[str, Sequence[int], None], default: Tuple[int, int, int]) -> Tuple[int, int, int]:
    if value is None:
        return default
    if isinstance(value, str):
        v = value.lstrip("#")
        return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
    return (int(value[0]), int(value[1]), int(value[2]))


def _rng(recipe: Mapping[str, Any], seed: int) -> np.random.Generator:
    """RNG seeded from the seed and the recipe content (platform independent PCG64)."""
    blob = repr(sorted((k, repr(v)) for k, v in recipe.items())).encode("utf-8")
    digest = hashlib.sha256(blob + str(int(seed)).encode("ascii")).digest()
    return np.random.default_rng(int.from_bytes(digest[:8], "little"))


def _size(recipe: Mapping[str, Any], kind: str) -> Tuple[int, int]:
    size = recipe.get("size") or DEFAULT_SIZES[kind]
    return int(size[0]), int(size[1])


def _label_lines(recipe: Mapping[str, Any]) -> List[str]:
    text = str(recipe.get("label_text") or "PRODUCT")
    return [line.strip() for line in text.split("|") if line.strip()]


def _mask(width: int, height: int, draw_fn) -> np.ndarray:
    img = Image.new("L", (width, height), 0)
    draw_fn(ImageDraw.Draw(img))
    return np.asarray(img) > 127


def _shade(mask: np.ndarray, x0: int, x1: int, base: Tuple[int, int, int], strength: float) -> np.ndarray:
    """Cylinder-style horizontal shading of a colour across [x0, x1)."""
    h, w = mask.shape
    xs = np.arange(w, dtype=np.float32)
    span = max(1.0, float(x1 - x0))
    t = np.clip((xs - x0) / span, 0.0, 1.0)
    profile = 1.0 - strength * (np.abs(t - 0.38) * 1.6) ** 2   # highlight left of centre
    colour = np.array(base, dtype=np.float32)[None, None, :] * profile[None, :, None]
    return np.broadcast_to(colour, (h, w, 3))


def _paint(canvas: np.ndarray, mask: np.ndarray, colour_img: np.ndarray) -> None:
    np.copyto(canvas, colour_img, where=mask[:, :, None])


def _grain(canvas: np.ndarray, rng: np.random.Generator, sigma: float, mask: Optional[np.ndarray] = None) -> None:
    """Add sensor-like Gaussian grain, to the whole canvas or only inside mask."""
    if mask is None:
        canvas += rng.standard_normal(size=canvas.shape, dtype=np.float32) * np.float32(sigma)
    else:
        count = int(mask.sum())
        if count:
            canvas[mask] += rng.standard_normal(size=(count, 3), dtype=np.float32) * np.float32(sigma)


def _fine_print(canvas: np.ndarray, rng: np.random.Generator, box: Tuple[int, int, int, int],
                colour: Tuple[int, int, int], rows: int) -> None:
    """Rows of short dark bars that read like ingredient or nutrition small print."""
    x0, y0, x1, y1 = box
    if x1 - x0 < 8 or y1 - y0 < 8 or rows <= 0:
        return
    row_h = max(2, (y1 - y0) // (rows * 2))
    for r in range(rows):
        y = y0 + r * row_h * 2
        x = x0
        while x < x1 - 4:
            word = int(rng.integers(2, 9)) * max(1, row_h // 2)
            canvas[y:y + max(1, row_h // 2 + 1), x:min(x1, x + word)] = colour
            x += word + max(2, row_h)


# ---------------------------------------------------------------------------
# Product drawing
# ---------------------------------------------------------------------------

def _product_geometry(shape: str, box: Tuple[int, int, int, int]):
    """Return (outline_fn, body_x_range, label_box, cap_box) for a product drawn inside box."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0

    if shape == "bottle":
        neck_w = w * 0.34
        cx = (x0 + x1) / 2
        cap = (cx - neck_w / 2, y0, cx + neck_w / 2, y0 + h * 0.08)
        shoulder_y = y0 + h * 0.24

        def outline(d):
            d.rectangle((cx - neck_w / 2, y0 + h * 0.06, cx + neck_w / 2, y0 + h * 0.15), fill=255)
            d.polygon([(cx - neck_w / 2, y0 + h * 0.14), (cx + neck_w / 2, y0 + h * 0.14),
                       (x1, shoulder_y), (x0, shoulder_y)], fill=255)
            d.rounded_rectangle((x0, shoulder_y - 2, x1, y1), radius=int(w * 0.12), fill=255)
        label = (x0, y0 + h * 0.38, x1, y0 + h * 0.80)
        return outline, (x0, x1), label, cap

    if shape == "carton":
        gable = h * 0.16

        def outline(d):
            d.polygon([(x0, y0 + gable), ((x0 + x1) / 2, y0 + h * 0.03), (x1, y0 + gable)], fill=255)
            d.rectangle((x0 + w * 0.2, y0, x1 - w * 0.2, y0 + h * 0.04), fill=255)
            d.rectangle((x0, y0 + gable, x1, y1), fill=255)
        label = (x0, y0 + h * 0.30, x1, y0 + h * 0.86)
        cap = (x1 - w * 0.34, y0 + gable * 0.35, x1 - w * 0.12, y0 + gable * 0.8)
        return outline, (x0, x1), label, cap

    if shape == "jar":
        lid_h = h * 0.16

        def outline(d):
            d.rounded_rectangle((x0 + w * 0.05, y0, x1 - w * 0.05, y0 + lid_h), radius=int(w * 0.05), fill=255)
            d.rounded_rectangle((x0, y0 + lid_h - 2, x1, y1), radius=int(w * 0.14), fill=255)
        label = (x0, y0 + h * 0.34, x1, y0 + h * 0.84)
        cap = (x0 + w * 0.05, y0, x1 - w * 0.05, y0 + lid_h)
        return outline, (x0, x1), label, cap

    if shape == "bag":
        def outline(d):
            d.polygon([(x0 + w * 0.06, y0), (x1 - w * 0.06, y0), (x1, y0 + h * 0.10), (x1, y1 - h * 0.03),
                       (x1 - w * 0.04, y1), (x0 + w * 0.04, y1), (x0, y1 - h * 0.03), (x0, y0 + h * 0.10)],
                      fill=255)
        label = (x0 + w * 0.06, y0 + h * 0.22, x1 - w * 0.06, y0 + h * 0.80)
        cap = (x0 + w * 0.06, y0, x1 - w * 0.06, y0 + h * 0.06)
        return outline, (x0, x1), label, cap

    if shape == "can":
        rim = h * 0.06

        def outline(d):
            d.ellipse((x0, y0, x1, y0 + 2 * rim), fill=255)
            d.rectangle((x0, y0 + rim, x1, y1 - rim), fill=255)
            d.ellipse((x0, y1 - 2 * rim, x1, y1), fill=255)
        label = (x0, y0 + h * 0.22, x1, y0 + h * 0.84)
        cap = (x0, y0, x1, y0 + 2 * rim)
        return outline, (x0, x1), label, cap

    if shape == "pouch":
        def outline(d):
            d.polygon([(x0 + w * 0.08, y0), (x1 - w * 0.08, y0), (x1, y1), (x0, y1)], fill=255)
        label = (x0 + w * 0.10, y0 + h * 0.25, x1 - w * 0.10, y0 + h * 0.82)
        cap = (x0 + w * 0.3, y0 + h * 0.04, x1 - w * 0.3, y0 + h * 0.09)
        return outline, (x0, x1), label, cap

    # box (default): front face plus a darker side face
    side = w * 0.14

    def outline(d):
        d.rectangle((x0, y0 + side * 0.5, x1 - side, y1), fill=255)
        d.polygon([(x1 - side, y0 + side * 0.5), (x1, y0), (x1, y1 - side * 0.5), (x1 - side, y1)], fill=255)
        d.polygon([(x0, y0 + side * 0.5), (x0 + side, y0), (x1, y0), (x1 - side, y0 + side * 0.5)], fill=255)
    label = (x0, y0 + h * 0.22, x1 - side, y0 + h * 0.86)
    cap = (x0 + (w - side) * 0.1, y0 + h * 0.10, x0 + (w - side) * 0.5, y0 + h * 0.18)
    return outline, (x0, x1 - side), label, cap


def _draw_product(canvas: np.ndarray, alpha: Optional[np.ndarray], rng: np.random.Generator,
                  box: Tuple[int, int, int, int], shape: str, lines: Sequence[str],
                  body: Tuple[int, int, int], cap_c: Tuple[int, int, int], label_c: Tuple[int, int, int],
                  accent: Tuple[int, int, int], outline_c: Optional[Tuple[int, int, int]] = None,
                  shade_strength: float = 0.22, grain: float = 2.0) -> np.ndarray:
    """Draw one product into canvas (float32 HxWx3). Returns the full-frame product mask.

    Drawing happens on the product's own region of the canvas, which keeps
    scenes with many products fast.
    """
    full_h, full_w = canvas.shape[:2]
    rx0, ry0 = max(0, int(box[0]) - 2), max(0, int(box[1]) - 2)
    rx1, ry1 = min(full_w, int(box[2]) + 2), min(full_h, int(box[3]) + 2)
    full = np.zeros((full_h, full_w), dtype=bool)
    if rx1 <= rx0 or ry1 <= ry0:
        return full
    local = (box[0] - rx0, box[1] - ry0, box[2] - rx0, box[3] - ry0)
    sub_alpha = alpha[ry0:ry1, rx0:rx1] if alpha is not None else None
    full[ry0:ry1, rx0:rx1] = _draw_product_local(
        canvas[ry0:ry1, rx0:rx1], sub_alpha, rng, local, shape, lines, body, cap_c, label_c, accent,
        outline_c, shade_strength, grain)
    return full


def _draw_product_local(canvas: np.ndarray, alpha: Optional[np.ndarray], rng: np.random.Generator,
                        box: Tuple[int, int, int, int], shape: str, lines: Sequence[str],
                        body: Tuple[int, int, int], cap_c: Tuple[int, int, int], label_c: Tuple[int, int, int],
                        accent: Tuple[int, int, int], outline_c: Optional[Tuple[int, int, int]],
                        shade_strength: float, grain: float) -> np.ndarray:
    h, w = canvas.shape[:2]
    outline_fn, (bx0, bx1), label_box, cap_box = _product_geometry(shape, box)
    mask = _mask(w, h, outline_fn)
    _paint(canvas, mask, _shade(mask, int(bx0), int(bx1), body, shade_strength))

    if outline_c is not None:
        edge = mask & ~_erode(mask, max(1, (box[2] - box[0]) // 120))
        canvas[edge] = outline_c

    lx0, ly0, lx1, ly1 = (int(v) for v in label_box)
    label_mask = np.zeros_like(mask)
    label_mask[max(0, ly0):max(0, ly1), max(0, lx0):max(0, lx1)] = True
    label_mask &= mask
    _paint(canvas, label_mask, _shade(label_mask, lx0, lx1, label_c, shade_strength * 0.8))

    # accent stripe and a logo ellipse make the pack recognisable
    stripe_h = max(2, (ly1 - ly0) // 9)
    stripe = np.zeros_like(mask)
    stripe[ly0 + stripe_h:ly0 + 2 * stripe_h, lx0:lx1] = True
    canvas[stripe & mask] = accent
    logo_w = (lx1 - lx0) * 0.62
    logo_h = (ly1 - ly0) * 0.22
    lcx = (lx0 + lx1) / 2
    logo = _mask(w, h, lambda d: d.ellipse((lcx - logo_w / 2, ly0 + 2.4 * stripe_h,
                                            lcx + logo_w / 2, ly0 + 2.4 * stripe_h + logo_h), fill=255))
    canvas[logo & mask] = accent

    cx0, cy0, cx1, cy1 = (int(v) for v in cap_box)
    cap_mask = np.zeros_like(mask)
    cap_mask[max(0, cy0):max(0, cy1), max(0, cx0):max(0, cx1)] = True
    cap_mask &= mask
    _paint(canvas, cap_mask, _shade(cap_mask, cx0, cx1, cap_c, shade_strength))

    # label text: first line inside the logo, the rest below it
    text_c = _contrast_colour(label_c)
    logo_text_c = _contrast_colour(accent)
    max_w = int((lx1 - lx0) * 0.86)
    if lines:
        s = max(1, int(logo_h / 7 * 0.55))
        draw_text_centered(canvas, int(lcx), int(ly0 + 2.4 * stripe_h + logo_h * 0.22), lines[0],
                           int(logo_w * 0.86), s, logo_text_c)
    y = int(ly0 + 2.4 * stripe_h + logo_h + stripe_h * 0.6)
    for line in lines[1:]:
        s = max(1, int((ly1 - ly0) / 7 * 0.11))
        used = draw_text_centered(canvas, int(lcx), y, line, max_w, s, text_c)
        y += used + max(2, used // 3)
    _fine_print(canvas, rng, (lx0 + (lx1 - lx0) // 10, min(ly1 - 4, y + stripe_h // 2), lx1 - (lx1 - lx0) // 10,
                              ly1 - stripe_h // 2), _mix(text_c, label_c, 0.45), rows=3)

    if grain:
        _grain(canvas, rng, grain, mask)
    if alpha is not None:
        alpha[mask] = 255
    return mask


def _erode(mask: np.ndarray, r: int) -> np.ndarray:
    out = mask.copy()
    for _ in range(r):
        inner = out.copy()
        inner[1:, :] &= out[:-1, :]
        inner[:-1, :] &= out[1:, :]
        inner[:, 1:] &= out[:, :-1]
        inner[:, :-1] &= out[:, 1:]
        out = inner
    return out


def _contrast_colour(c: Tuple[int, int, int]) -> Tuple[int, int, int]:
    lum = 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
    return (20, 24, 28) if lum > 140 else (250, 250, 250)


def _mix(a: Sequence[int], b: Sequence[int], t: float) -> Tuple[int, int, int]:
    return tuple(int(round(a[i] * (1 - t) + b[i] * t)) for i in range(3))  # type: ignore[return-value]


def _product_box(width: int, height: int, fill: float, shape: str) -> Tuple[int, int, int, int]:
    aspect = {"bottle": 0.40, "carton": 0.52, "jar": 0.78, "bag": 0.70, "box": 0.72, "can": 0.56,
              "pouch": 0.68}.get(shape, 0.6)
    ph = height * fill
    pw = min(width * 0.92, ph * aspect)
    x0 = (width - pw) / 2
    y0 = (height - ph) / 2
    return int(x0), int(y0), int(x0 + pw), int(y0 + ph)


# ---------------------------------------------------------------------------
# Recipes
# ---------------------------------------------------------------------------

def _packshot(recipe: Mapping[str, Any], seed: int, kind: str) -> Image.Image:
    rng = _rng(recipe, seed)
    width, height = _size(recipe, kind)
    shape = recipe.get("shape") or ("bottle" if kind == "tall_bottle_600x1200" else "box")
    fill = float(recipe.get("fill", 0.82 if kind == "tall_bottle_600x1200" else 0.78))
    canvas = np.full((height, width, 3), 255.0, dtype=np.float32)
    transparent = recipe.get("background") == "transparent"
    alpha = np.zeros((height, width), dtype=np.uint8) if transparent else None
    box = _product_box(width, height, fill, shape)
    mask = _draw_product(
        canvas, alpha, rng, box, shape, _label_lines(recipe),
        body=_rgb(recipe.get("body_color"), (236, 238, 240)),
        cap_c=_rgb(recipe.get("cap_color"), (30, 90, 170)),
        label_c=_rgb(recipe.get("label_color"), (246, 246, 246)),
        accent=_rgb(recipe.get("accent_color"), (0, 110, 70)),
        outline_c=(208, 210, 212), grain=1.2,
    )
    # a soft contact shadow under the product, as studio packshots have
    x0, y0, x1, y1 = box
    sh = _mask(width, height, lambda d: d.ellipse((x0 + (x1 - x0) * 0.1, y1 - (y1 - y0) * 0.012,
                                                  x1 - (x1 - x0) * 0.1, y1 + (y1 - y0) * 0.02), fill=255))
    sh &= ~mask
    canvas[sh] = np.minimum(canvas[sh], 232.0)
    if alpha is not None:
        alpha[sh] = 70
    arr = np.clip(np.rint(canvas), 0, 255).astype(np.uint8)
    if alpha is not None:
        rgba = np.dstack([arr, alpha])
        rgba[alpha == 0, :3] = 0          # transparent pixels stored as (0,0,0,0), as many exporters do
        return _optics(Image.fromarray(rgba, "RGBA"), recipe)
    return _optics(Image.fromarray(arr, "RGB"), recipe)


def _optics(img: Image.Image, recipe: Mapping[str, Any]) -> Image.Image:
    """Soften edges the way a real lens and resampling do.

    Studio packshots have anti-aliased edges and smooth label print, so their
    whole-frame sharpness falls as resolution rises. The blur radius scales
    with the long side; the flat white background is unchanged by it.
    """
    radius = float(recipe.get("softness", max(img.size) / 1400.0))
    if radius <= 0:
        return img
    return img.filter(ImageFilter.GaussianBlur(radius))


def _carton_white_on_white(recipe: Mapping[str, Any], seed: int) -> Image.Image:
    """A white gable-top carton on a pure white background, with a printed front panel."""
    rng = _rng(recipe, seed)
    width, height = _size(recipe, "carton_white_on_white")
    canvas = np.full((height, width, 3), 255.0, dtype=np.float32)
    box = _product_box(width, height, float(recipe.get("fill", 0.8)), "carton")
    _draw_product(
        canvas, None, rng, box, "carton", _label_lines(recipe),
        body=_rgb(recipe.get("body_color"), (253, 253, 252)),
        cap_c=_rgb(recipe.get("cap_color"), (250, 250, 250)),
        label_c=_rgb(recipe.get("label_color"), (252, 252, 252)),
        accent=_rgb(recipe.get("accent_color"), (0, 94, 170)),
        outline_c=(226, 228, 230), shade_strength=0.035, grain=0.8,
    )
    arr = np.clip(np.rint(canvas), 0, 255).astype(np.uint8)
    return _optics(Image.fromarray(arr, "RGB"), recipe)


def _backdrop(width: int, height: int, c0: Tuple[int, int, int], c1: Tuple[int, int, int]) -> np.ndarray:
    t = np.linspace(0.0, 1.0, width, dtype=np.float32)[None, :, None]
    grad = np.array(c0, np.float32)[None, None, :] * (1 - t) + np.array(c1, np.float32)[None, None, :] * t
    return np.broadcast_to(grad, (height, width, 3)).copy()


_PALETTE = [(200, 40, 50), (30, 90, 170), (0, 130, 80), (240, 170, 20), (120, 60, 150), (230, 110, 30),
            (20, 150, 170), (90, 90, 90), (180, 30, 110), (60, 120, 40)]


def _banner(recipe: Mapping[str, Any], seed: int) -> Image.Image:
    """A wide brand/promo banner: gradient backdrop, headline, several products in a row."""
    rng = _rng(recipe, seed)
    width, height = _size(recipe, "banner")
    c0 = _rgb(recipe.get("body_color"), (18, 70, 140))
    c1 = _mix(c0, (255, 255, 255), 0.35)
    canvas = _backdrop(width, height, c0, c1)
    # abstract splash shapes behind the products
    for _ in range(6):
        cx, cy, r = rng.integers(0, width), rng.integers(0, height), rng.integers(height // 8, height // 3)
        m = _mask(width, height, lambda d, cx=cx, cy=cy, r=r: d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255))
        canvas[m] = canvas[m] * 0.8 + np.array(_mix(c1, (255, 255, 255), 0.5), np.float32) * 0.2
    lines = _label_lines(recipe)
    headline = str(recipe.get("headline") or (lines[0] if lines else "NEW"))
    draw_text_centered(canvas, width // 2, int(height * 0.06), headline, int(width * 0.9),
                       max(2, height // 60), (255, 255, 255))
    n = int(recipe.get("count", 4))
    slot = width / (n + 1)
    for i in range(n):
        shape = ["bottle", "carton", "can", "jar"][i % 4]
        ph = height * (0.62 if i % 2 == 0 else 0.52)
        pw = min(slot * 0.8, ph * 0.5)
        cx = slot * (i + 1)
        y1 = height * 0.95
        box = (int(cx - pw / 2), int(y1 - ph), int(cx + pw / 2), int(y1))
        body = (238, 238, 238) if i % 2 == 0 else _PALETTE[(i + seed) % len(_PALETTE)]
        _draw_product(canvas, None, rng, box, shape, lines if i == n // 2 else lines[:1],
                      body=body, cap_c=_PALETTE[(i * 3 + seed) % len(_PALETTE)],
                      label_c=(245, 245, 245), accent=_PALETTE[(i * 7 + 1) % len(_PALETTE)], grain=0)
    _grain(canvas, rng, 3.0)
    return Image.fromarray(np.clip(np.rint(canvas), 0, 255).astype(np.uint8), "RGB")


def _lifestyle_clutter(recipe: Mapping[str, Any], seed: int) -> Image.Image:
    """A shelf or kitchen scene: textured background, many other items, the product among them."""
    rng = _rng(recipe, seed)
    width, height = _size(recipe, "lifestyle_clutter")
    canvas = _backdrop(width, height, (122, 96, 70), (160, 132, 100))
    # shelf planks and wood grain
    for k in range(1, 4):
        y = int(height * k / 4)
        canvas[y:y + max(3, height // 60), :] = (70, 55, 40)
    grain_rows = rng.normal(0.0, 10.0, size=(height, 1, 1)).astype(np.float32)
    canvas += grain_rows
    n = int(recipe.get("count", 18))
    for _ in range(n):
        pw = int(rng.integers(width // 16, width // 7))
        ph = int(pw * rng.uniform(1.1, 2.4))
        x0 = int(rng.integers(0, max(1, width - pw)))
        y1 = int(height * int(rng.integers(1, 5)) / 4)
        y0 = max(0, y1 - ph)
        colour = _PALETTE[int(rng.integers(0, len(_PALETTE)))]
        shape = ["box", "bottle", "can", "bag", "jar"][int(rng.integers(0, 5))]
        _draw_product(canvas, None, rng, (x0, y0, x0 + pw, y1), shape, ["BRAND"], body=colour,
                      cap_c=_PALETTE[int(rng.integers(0, len(_PALETTE)))], label_c=_mix(colour, (255, 255, 255), 0.5),
                      accent=(250, 250, 250), grain=0)
    # the target product, in front and at an angle-free but off-centre position
    ph = int(height * 0.55)
    pw = int(ph * 0.45)
    x0 = int(width * 0.18)
    y1 = int(height * 0.92)
    _draw_product(canvas, None, rng, (x0, y1 - ph, x0 + pw, y1), recipe.get("shape") or "bottle",
                  _label_lines(recipe), body=_rgb(recipe.get("body_color"), (236, 236, 236)),
                  cap_c=_rgb(recipe.get("cap_color"), (30, 90, 170)),
                  label_c=_rgb(recipe.get("label_color"), (246, 246, 246)),
                  accent=_rgb(recipe.get("accent_color"), (0, 110, 70)), grain=0)
    _grain(canvas, rng, 4.0)
    return Image.fromarray(np.clip(np.rint(canvas), 0, 255).astype(np.uint8), "RGB")


def _noise(recipe: Mapping[str, Any], seed: int) -> Image.Image:
    rng = _rng(recipe, seed)
    width, height = _size(recipe, "noise")
    arr = rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8)
    return Image.fromarray(arr, "RGB")


def _tiny(recipe: Mapping[str, Any], seed: int) -> Image.Image:
    params = dict(recipe)
    params["size"] = list(recipe.get("size") or DEFAULT_SIZES["tiny_120"])
    return _packshot(params, seed, "tiny_120")


def render(recipe: Union[Mapping[str, Any], str], seed: int = 0) -> Image.Image:
    """Render a recipe to a PIL image (RGB, or RGBA for transparent packshots)."""
    if isinstance(recipe, str):
        recipe = {"kind": recipe}
    kind = recipe.get("kind")
    if kind in ("packshot_white", "tall_bottle_600x1200"):
        return _packshot(recipe, seed, kind)
    if kind == "carton_white_on_white":
        return _carton_white_on_white(recipe, seed)
    if kind == "banner":
        return _banner(recipe, seed)
    if kind == "lifestyle_clutter":
        return _lifestyle_clutter(recipe, seed)
    if kind == "noise":
        return _noise(recipe, seed)
    if kind == "tiny_120":
        return _tiny(recipe, seed)
    raise ValueError(f"unknown image recipe kind: {kind!r}")


def image_format(recipe: Union[Mapping[str, Any], str]) -> str:
    if isinstance(recipe, str):
        recipe = {"kind": recipe}
    if recipe.get("background") == "transparent":
        return "PNG"
    return str(recipe.get("format") or "JPEG").upper()


def generate(recipe: Union[Mapping[str, Any], str], seed: int = 0) -> bytes:
    """Encode a recipe to bytes. Same recipe and seed -> identical bytes."""
    if isinstance(recipe, str):
        recipe = {"kind": recipe}
    img = render(recipe, seed)
    fmt = image_format(recipe)
    buf = io.BytesIO()
    if fmt == "PNG":
        img.save(buf, format="PNG", optimize=False, compress_level=6)
    else:
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=int(recipe.get("quality", 90)), subsampling=2, optimize=False)
    return buf.getvalue()


def mime_type(recipe: Union[Mapping[str, Any], str]) -> str:
    return "image/png" if image_format(recipe) == "PNG" else "image/jpeg"


def white_ratio(data: bytes, threshold: int = 254) -> float:
    """Share of grey pixels >= threshold (the legacy 'clamped_255_ratio')."""
    with Image.open(io.BytesIO(data)) as im:
        grey = np.asarray(im.convert("L"))
    return float(np.mean(grey >= threshold))


def foreground_ratio(data: bytes, long_side: int = 512) -> float:
    """Foreground share per D5: min(RGB) < 245 on a 512px copy, RGBA composited on white first.

    Transparent pixels become white background after compositing, so they are
    not counted as foreground.
    """
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        if im.mode in ("RGBA", "LA", "P"):
            rgba = im.convert("RGBA")
            white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            white.alpha_composite(rgba)
            im_rgb = white.convert("RGB")
        else:
            im_rgb = im.convert("RGB")
        scale = long_side / max(im_rgb.size)
        if scale < 1:
            new = (max(1, round(im_rgb.size[0] * scale)), max(1, round(im_rgb.size[1] * scale)))
            im_rgb = im_rgb.resize(new, Image.Resampling.BILINEAR)
        rgb = np.asarray(im_rgb)
    return float((rgb.min(axis=2) < 245).mean())


def recipes_in(golden: Iterable[Mapping[str, Any]]) -> List[str]:
    """All recipe kinds used by a golden SKU list (for coverage checks)."""
    kinds = set()
    for sku in golden:
        for cand in sku.get("candidates", []):
            recipe = cand.get("image_recipe") or {}
            if recipe.get("kind"):
                kinds.add(recipe["kind"])
    return sorted(kinds)
