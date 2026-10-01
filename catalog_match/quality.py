"""Soft image quality (decision D5). Only truly broken images are hard-rejected.

assess(pil_img) -> QualityReport

Hard gates (hard_ok=False), and nothing else:
    short_side<250      the short side of the ORIGINAL image is below 250 px
    aspect_ratio        width / height outside 0.25 .. 4.0
    foreground<3%       almost nothing but background (blank or near-blank frame)
    foreground>98%      no background at all (full-bleed banner, shelf photo, noise)
    noise               edge density > 0.35 AND a near-uniform hue histogram

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

Every metric is measured on a copy downscaled to a 512 px long side. RGBA (and
palette images with transparency) are composited on WHITE, never on black, before
any metric. The foreground mask is min(R, G, B) < 245 on the composited image; for
an image with real transparency (>= 2 % transparent pixels) its opaque pixels
(alpha >= 128) also count as foreground, so a white product cut out on a
transparent background is still seen.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple

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
    if fg_ratio < FG_MIN:
        hard.append("foreground<3%")
    elif fg_ratio > FG_MAX:
        hard.append("foreground>98%")

    sample_edges = float((_sobel_magnitude(_gray(sample.astype(np.float32))) > EDGE_LEVEL).mean())
    hue_entropy, saturated_share = _hue_entropy(sample)
    if (sample_edges > NOISE_EDGE_DENSITY and saturated_share >= NOISE_MIN_SATURATED
            and hue_entropy >= NOISE_HUE_ENTROPY):
        hard.append("noise")

    # Soft features use an area-averaged copy (what a viewer sees).
    smooth = np.asarray(_downscale(rgb_img, Image.BOX), dtype=np.float32)
    smooth_alpha = (np.asarray(_downscale(alpha_img, Image.BOX), dtype=np.uint8)
                    if alpha_img is not None else None)
    smooth_mask = _foreground_mask(smooth, smooth_alpha)
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
    }

    if hard:
        score = 0.0
    else:
        fill_score = max(0.0, 1.0 - abs(soft["fill_ratio"] - 0.75) / 0.75)
        resolution = min(1.0, max(width, height) / 1200.0)
        score = (0.35 * soft["white_border_ratio"] + 0.25 * fill_score
                 + 0.20 * soft["fg_sharpness"] + 0.10 * (1.0 - soft["clutter"])
                 + 0.10 * resolution)
    return QualityReport(
        hard_ok=not hard,
        hard_reasons=hard,
        soft=soft,
        quality_score=round(max(0.0, min(1.0, score)), 4),
    )
