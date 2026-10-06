"""Soft image quality (decision D5). Only truly broken images are hard-rejected.

assess(pil_img) -> QualityReport

Hard gates (hard_ok=False), and nothing else:
    short_side<250      the short side of the ORIGINAL image is below 250 px
    aspect_ratio        width / height outside 0.25 .. 4.0
    foreground<3%       almost nothing but background (blank or near-blank frame, white or a
                        plain colour)
    busy_background     no white background (more than 98 % of the pixels are not white) AND
                        the border band is not a plain backdrop (background_report): a shelf,
                        street or kitchen photo, a promo collage
    noise               edge density > 0.35 AND a near-uniform hue histogram

No white background is not a reject by itself (it was, as 'foreground>98%', until the run
exports of 2026-10-04/05 showed it hard-rejecting 89 real packshots in 49 rows, Lulu's 1920 px
'Shan Meat Masala 100 g' among them: a pack on a light grey or coloured studio backdrop, or a
pack front cropped to the frame, has min(R, G, B) < 245 almost everywhere). Such a frame is
judged from its border band instead (background_report): a plain border of any colour, with
the product filling the rest of the frame, passes with a soft penalty (soft 'full_frame' 1.0,
a lower background term in quality_score than a white backdrop's); a busy border is
'busy_background'. The label reader stays the judge of what the picture shows (a banner or
a lifestyle view is never a MATCH).

There is deliberately NO exposure, whole-frame Laplacian, contrast, blockiness or
GrabCut gate: a white background is the target look of a packshot, and the
Laplacian variance of a sharp photo falls as its resolution rises.

Review warning (never a reject): low_resolution(width, height) is True when the short
side is below 500 px; decide.route flags such a pick 'warn:low_resolution'.

Soft features (tie-break only, never a reject):
    white_border_ratio  share of the outer band that is white
    fill_ratio          foreground bounding box area / frame area
    fg_sharpness        0..1, Laplacian variance inside the foreground
    clutter             0..1, edge density of the frame
    foreground_ratio    share of foreground pixels (diagnostic)
    full_frame          1.0 when the frame has no white background but a plain border (the soft
                        penalty above), else 0.0
    border_uniformity   share of the border band within BG_TOLERANCE of its median colour

Border band (background_report): the outer BORDER_BAND share of each side of the measured copy.
A side is plain when at least PLAIN_SIDE_MIN of its pixels lie within BG_TOLERANCE (per channel)
of the side's median colour (colour uniformity) and those pixels vary little (standard deviation
of their grey level <= PLAIN_SIDE_MAX_STD: no wood grain, fabric or foliage). The backdrop is
plain when at least PLAIN_SIDES_MIN sides are plain (a pack may touch the other ones) and the
band's edge density is at most PLAIN_BAND_MAX_EDGES (no scene detail along the border). On a
plain backdrop the product is what differs from its colour: fewer than FG_MIN of such pixels is
a blank frame ('foreground<3%': a solid-colour graphic with a line of text).

Every metric is measured on a copy downscaled to a 512 px long side. RGBA (and
palette images with transparency) are composited on WHITE, never on black, before
any metric. The foreground mask is min(R, G, B) < 245 on the composited image; for
an image with real transparency (>= 2 % transparent pixels) its opaque pixels
(alpha >= 128) also count as foreground, so a white product cut out on a
transparent background is still seen.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

import numpy as np
from PIL import Image

from .models import QualityReport

logger = logging.getLogger(__name__)

MEASURE_LONG_SIDE = 512
MIN_SHORT_SIDE = 250
LOW_RES_SHORT_SIDE = 500          # below this a pick carries a review warning (not a gate)
ASPECT_MIN, ASPECT_MAX = 0.25, 4.0
FG_MIN, FG_MAX = 0.03, 0.98
WHITE_LEVEL = 245                 # min(R,G,B) >= 245 is background white
ALPHA_OPAQUE = 128
TRANSPARENT_SHARE_MIN = 0.02      # below this, transparency is only rounded corners etc.
EDGE_LEVEL = 20.0                 # Sobel magnitude / 4 above this is an edge pixel
NOISE_EDGE_DENSITY = 0.35
NOISE_HUE_ENTROPY = 0.95          # normalised entropy of a 36-bin hue histogram
NOISE_MIN_SATURATED = 0.20        # hue entropy is meaningless on a mostly grey image
HUE_BINS = 36
# background judgement of a frame with no white background (background_report)
BORDER_BAND = 0.04                # outer share of each side that is the border band
BG_TOLERANCE = 24                 # per-channel distance to the backdrop colour still counted as backdrop
PLAIN_SIDE_MIN = 0.90             # share of a side's pixels near its median colour for a plain side
PLAIN_SIDE_MAX_STD = 8.0          # grey-level spread of those pixels: texture (wood, fabric) is not plain
PLAIN_SIDES_MIN = 2               # plain sides for a plain backdrop (the pack may touch the others)
PLAIN_BAND_MAX_EDGES = 0.02       # edge density along the border of a plain backdrop
PLAIN_BACKGROUND_CREDIT = 0.7     # background term of a plain non-white backdrop (a white one is 1.0)


# ---------------------------------------------------------------------------
# Image preparation
# ---------------------------------------------------------------------------

def _has_alpha(img: Image.Image) -> bool:
    return img.mode in ("RGBA", "LA", "PA", "RGBa", "La") or (
        img.mode == "P" and "transparency" in img.info
    )


def _to_rgb_on_white(img: Image.Image) -> Tuple[Image.Image, Optional[Image.Image]]:
    """(RGB image composited on white, alpha channel or None)."""
    if _has_alpha(img):
        rgba = img.convert("RGBA")
        white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        rgb = Image.alpha_composite(white, rgba).convert("RGB")
        return rgb, rgba.getchannel("A")
    return img.convert("RGB"), None


def _downscale(img: Image.Image, resample: int) -> Image.Image:
    w, h = img.size
    scale = MEASURE_LONG_SIDE / float(max(w, h))
    if scale >= 1.0:
        return img
    size = (max(1, int(round(w * scale))), max(1, int(round(h * scale))))
    return img.resize(size, resample)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _foreground_mask(rgb: np.ndarray, alpha: Optional[np.ndarray]) -> np.ndarray:
    mask = rgb.min(axis=2) < WHITE_LEVEL
    if alpha is not None:
        transparent_share = float((alpha < ALPHA_OPAQUE).mean())
        if transparent_share >= TRANSPARENT_SHARE_MIN:
            mask = mask | (alpha >= ALPHA_OPAQUE)
    return mask


def _gray(rgb: np.ndarray) -> np.ndarray:
    return (0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]).astype(np.float32)


def _sobel_magnitude(gray: np.ndarray) -> np.ndarray:
    """Sobel gradient magnitude scaled to roughly 0..255 (numpy only, no OpenCV needed)."""
    p = np.pad(gray, 1, mode="edge")
    gx = (p[:-2, 2:] + 2 * p[1:-1, 2:] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[1:-1, :-2] + p[2:, :-2])
    gy = (p[2:, :-2] + 2 * p[2:, 1:-1] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[:-2, 1:-1] + p[:-2, 2:])
    return np.sqrt(gx * gx + gy * gy) / 4.0


def _laplacian(gray: np.ndarray) -> np.ndarray:
    p = np.pad(gray, 1, mode="edge")
    return p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:] - 4.0 * p[1:-1, 1:-1]


def _hue_entropy(rgb: np.ndarray) -> Tuple[float, float]:
    """(normalised hue entropy over saturated pixels, share of saturated pixels)."""
    arr = rgb.astype(np.float32) / 255.0
    mx = arr.max(axis=2)
    mn = arr.min(axis=2)
    delta = mx - mn
    sat = np.where(mx > 0, delta / np.maximum(mx, 1e-6), 0.0)
    saturated = (sat > 0.16) & (mx > 0.08)
    share = float(saturated.mean())
    if not saturated.any():
        return 0.0, share
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    d = np.maximum(delta, 1e-6)
    hue = np.where(
        mx == r, ((g - b) / d) % 6.0,
        np.where(mx == g, (b - r) / d + 2.0, (r - g) / d + 4.0),
    ) / 6.0
    bins = np.minimum((hue[saturated] * HUE_BINS).astype(int), HUE_BINS - 1)
    hist = np.bincount(bins, minlength=HUE_BINS).astype(np.float64)
    p = hist / hist.sum()
    p = p[p > 0]
    return float(-(p * np.log(p)).sum() / np.log(HUE_BINS)), share


def _bbox_fill(mask: np.ndarray) -> float:
    """Foreground bounding-box area / frame area, ignoring rows/cols with only stray specks."""
    h, w = mask.shape
    rows = mask.sum(axis=1) >= max(1, int(0.005 * w))
    cols = mask.sum(axis=0) >= max(1, int(0.005 * h))
    if not rows.any() or not cols.any():
        return 0.0
    r0, r1 = np.argmax(rows), h - np.argmax(rows[::-1])
    c0, c1 = np.argmax(cols), w - np.argmax(cols[::-1])
    return float((r1 - r0) * (c1 - c0)) / float(h * w)


def _white_border_ratio(rgb: np.ndarray) -> float:
    h, w = rgb.shape[:2]
    bh = max(2, int(round(h * 0.03)))
    bw = max(2, int(round(w * 0.03)))
    band = np.zeros((h, w), dtype=bool)
    band[:bh, :] = True
    band[-bh:, :] = True
    band[:, :bw] = True
    band[:, -bw:] = True
    return float((rgb[band].min(axis=1) >= WHITE_LEVEL).mean())


def _near(pixels: np.ndarray, colour: np.ndarray) -> np.ndarray:
    """Pixels within BG_TOLERANCE of `colour` on every channel."""
    return np.abs(pixels - colour).max(axis=-1) <= BG_TOLERANCE


def background_report(rgb: np.ndarray) -> Dict[str, Any]:
    """The border-band judgement of a measured (area-averaged) copy, see the module docstring.

    {'plain': bool, 'plain_sides': int, 'border_uniformity': 0..1, 'band_edges': 0..1,
     'colour': (r, g, b) of the backdrop, 'object_mask': pixels that are not the backdrop}
    """
    h, w = rgb.shape[:2]
    bh = max(2, int(round(h * BORDER_BAND)))
    bw = max(2, int(round(w * BORDER_BAND)))
    sides = (rgb[:bh].reshape(-1, 3), rgb[-bh:].reshape(-1, 3), rgb[:, :bw].reshape(-1, 3),
             rgb[:, -bw:].reshape(-1, 3))
    plain = []                                       # (median colour, pixels) of each plain side
    for side in sides:
        median = np.median(side, axis=0)
        near = _near(side, median)
        if near.mean() >= PLAIN_SIDE_MIN and float(_gray(side[near]).std()) <= PLAIN_SIDE_MAX_STD:
            plain.append((median, side))
    # one backdrop: the plain sides of (about) one colour, the largest such group; the two ends of a banner's
    # gradient are plain sides of two colours
    group: list = []
    for median, _ in plain:
        same = [pixels for other, pixels in plain if np.abs(other - median).max() <= 2 * BG_TOLERANCE]
        if len(same) > len(group):
            group = same
    band = np.zeros((h, w), dtype=bool)
    band[:bh, :] = band[-bh:, :] = True
    band[:, :bw] = band[:, -bw:] = True
    band_pixels = rgb[band]
    band_edges = float((_sobel_magnitude(_gray(rgb)) > EDGE_LEVEL)[band].mean())
    colour = np.median(np.concatenate(group) if group else band_pixels, axis=0)
    return {
        "plain": len(group) >= PLAIN_SIDES_MIN and band_edges <= PLAIN_BAND_MAX_EDGES,
        "plain_sides": len(group),
        "border_uniformity": float(_near(band_pixels, np.median(band_pixels, axis=0)).mean()),
        "band_edges": band_edges,
        "colour": tuple(int(round(c)) for c in colour),
        "object_mask": ~_near(rgb, colour),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def low_resolution(width: Optional[int], height: Optional[int]) -> bool:
    """True when both sides are known and the short side is below LOW_RES_SHORT_SIDE."""
    if not width or not height:
        return False
    return min(width, height) < LOW_RES_SHORT_SIDE


def assess(pil_img: Image.Image) -> QualityReport:
    """Measure one decoded image. Never raises; an unreadable image is a hard fail."""
    try:
        width, height = pil_img.size
        rgb_img, alpha_img = _to_rgb_on_white(pil_img)
    except Exception as exc:  # corrupt or exotic mode
        logger.warning("quality: cannot read image: %s", exc)
        return QualityReport(hard_ok=False, hard_reasons=["decode_error"])

    hard = []
    if min(width, height) < MIN_SHORT_SIDE:
        hard.append("short_side<250")
    aspect = width / float(height) if height else 0.0
    if not (ASPECT_MIN <= aspect <= ASPECT_MAX):
        hard.append("aspect_ratio")

    # Gates use a nearest-neighbour sample: pixel statistics stay independent of
    # the source resolution (a smoothing filter would average noise away).
    sample = np.asarray(_downscale(rgb_img, Image.NEAREST), dtype=np.uint8)
    sample_alpha = (np.asarray(_downscale(alpha_img, Image.NEAREST), dtype=np.uint8)
                    if alpha_img is not None else None)
    fg_mask = _foreground_mask(sample, sample_alpha)
    fg_ratio = float(fg_mask.mean())
    # Soft features (and the backdrop judgement) use an area-averaged copy (what a viewer sees).
    smooth = np.asarray(_downscale(rgb_img, Image.BOX), dtype=np.float32)
    smooth_alpha = (np.asarray(_downscale(alpha_img, Image.BOX), dtype=np.uint8)
                    if alpha_img is not None else None)
    smooth_mask = _foreground_mask(smooth, smooth_alpha)
    background: Optional[Dict[str, Any]] = None
    full_frame = False
    if fg_ratio < FG_MIN:
        hard.append("foreground<3%")
    elif fg_ratio > FG_MAX:
        # no white background: a plain backdrop of another colour passes (soft penalty), a busy one does not
        background = background_report(smooth)
        if not background["plain"]:
            hard.append("busy_background")
        elif float(background["object_mask"].mean()) < FG_MIN:
            hard.append("foreground<3%")
        else:
            full_frame = True
            smooth_mask = background["object_mask"]     # the product is what differs from the backdrop

    sample_edges = float((_sobel_magnitude(_gray(sample.astype(np.float32))) > EDGE_LEVEL).mean())
    hue_entropy, saturated_share = _hue_entropy(sample)
    if (sample_edges > NOISE_EDGE_DENSITY and saturated_share >= NOISE_MIN_SATURATED
            and hue_entropy >= NOISE_HUE_ENTROPY):
        hard.append("noise")

    gray = _gray(smooth)
    edges = float((_sobel_magnitude(gray) > EDGE_LEVEL).mean())
    lap = _laplacian(gray)
    fg_lap_var = float(lap[smooth_mask].var()) if smooth_mask.sum() > 16 else 0.0

    soft: Dict[str, float] = {
        "white_border_ratio": round(_white_border_ratio(smooth), 4),
        "fill_ratio": round(_bbox_fill(smooth_mask), 4),
        "fg_sharpness": round(min(1.0, fg_lap_var / 1000.0), 4),
        "clutter": round(min(1.0, edges / NOISE_EDGE_DENSITY), 4),
        "foreground_ratio": round(fg_ratio, 4),
        "full_frame": 1.0 if full_frame else 0.0,
    }
    if background is not None:
        soft["border_uniformity"] = round(background["border_uniformity"], 4)

    if hard:
        score = 0.0
    else:
        fill_score = max(0.0, 1.0 - abs(soft["fill_ratio"] - 0.75) / 0.75)
        resolution = min(1.0, max(width, height) / 1200.0)
        # a plain backdrop of another colour is a clean packshot, a step below a white one (the soft penalty);
        # a busy frame that passes the gates (it has some white) keeps its white_border_ratio, about 0
        backdrop = PLAIN_BACKGROUND_CREDIT if full_frame else soft["white_border_ratio"]
        score = (0.35 * backdrop + 0.25 * fill_score
                 + 0.20 * soft["fg_sharpness"] + 0.10 * (1.0 - soft["clutter"])
                 + 0.10 * resolution)
    return QualityReport(
        hard_ok=not hard,
        hard_reasons=hard,
        soft=soft,
        quality_score=round(max(0.0, min(1.0, score)), 4),
    )
