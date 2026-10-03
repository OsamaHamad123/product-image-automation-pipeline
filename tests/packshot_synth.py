"""Realistic synthetic packshots for the cutout quality gate, and offline fakes of the paid services.

Every product is drawn at 4x and downsampled with a box filter (area coverage, premultiplied), so its edges are
anti-aliased the way a real photo or a provider matte is. A shot keeps its ground truth: the product layer
(straight colours + alpha) and, separately, anything that is not product (a shadow, a reflection, a photo card).

`Services` fakes PhotoRoom / remove.bg (behind requests.post), the Gemini box (image_processor._locate_product_box)
and blocks every socket. A fake provider answers with the ground-truth product for the exact frame it was sent (the
full source, or the Gemini crop computed with image_processor._box_rect), optionally damaged by an `edit` hook, and
crops to the product when PhotoRoom is asked for crop=true. Paid calls are counted per provider.
"""

import io
import json
import socket
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

SS = 4   # supersampling factor

WHITE = (255, 255, 255)
STUDIO = (238, 238, 236)
CARD_GREY = (178, 178, 176)


# ---------------------------------------------------------------------------
# Drawing at 4x
# ---------------------------------------------------------------------------

class Layer:
    """An RGBA layer drawn at ss x the final size; `done()` downsamples it with area coverage."""

    def __init__(self, size, ss=SS):
        self.size = (int(size[0]), int(size[1]))
        self.ss = int(ss)
        self.img = Image.new("RGBA", (self.size[0] * self.ss, self.size[1] * self.ss), (0, 0, 0, 0))
        self.draw = ImageDraw.Draw(self.img)

    def _xy(self, box):
        return [int(round(v * self.ss)) for v in box]

    def rect(self, box, fill, radius=0):
        xy = self._xy(box)
        if radius:
            self.draw.rounded_rectangle(xy, radius=int(round(radius * self.ss)), fill=_rgba(fill))
        else:
            self.draw.rectangle(xy, fill=_rgba(fill))

    def ellipse(self, box, fill):
        self.draw.ellipse(self._xy(box), fill=_rgba(fill))

    def polygon(self, points, fill):
        self.draw.polygon([(int(round(x * self.ss)), int(round(y * self.ss))) for x, y in points], fill=_rgba(fill))

    def clear(self, box, radius=0):
        """A real hole (transparent), e.g. a jerry-can handle."""
        self.rect(box, (0, 0, 0, 0), radius)

    def done(self) -> Image.Image:
        return self.img.resize(self.size, Image.Resampling.BOX)


def _rgba(colour):
    colour = tuple(int(c) for c in colour)
    return colour if len(colour) == 4 else colour + (255,)


def text_bars(layer, x0, y0, x1, y1, colour, lines=3):
    """Printed text: thin bars."""
    h = (y1 - y0) / (2 * lines)
    for i in range(lines):
        top = y0 + 2 * i * h
        layer.rect((x0, top, x1 - (i % 2) * (x1 - x0) * 0.25, top + h * 0.8), colour)


# ---------------------------------------------------------------------------
# Products (final-pixel coordinates; x0, y0 = top-left of the product's box)
# ---------------------------------------------------------------------------

def draw_bottle(L, x0, y0, w, h, body=(200, 40, 40), cap=(25, 40, 160), label=(245, 210, 60), print_=(30, 30, 30)):
    cx = x0 + w / 2
    L.rect((cx - 0.2 * w, y0, cx + 0.2 * w, y0 + 0.09 * h), cap, radius=0.03 * w)
    L.rect((cx - 0.15 * w, y0 + 0.08 * h, cx + 0.15 * w, y0 + 0.2 * h), body)
    L.polygon([(cx - 0.15 * w, y0 + 0.18 * h), (cx + 0.15 * w, y0 + 0.18 * h), (x0 + w, y0 + 0.33 * h),
               (x0, y0 + 0.33 * h)], body)
    L.rect((x0, y0 + 0.3 * h, x0 + w, y0 + h), body, radius=0.1 * w)
    L.rect((x0, y0 + 0.45 * h, x0 + w, y0 + 0.75 * h), label)
    text_bars(L, x0 + 0.15 * w, y0 + 0.5 * h, x0 + 0.85 * w, y0 + 0.7 * h, print_)


def draw_white_bottle(L, x0, y0, w, h, cap=(30, 140, 60)):
    """A white bottle as photographed: white body with grey shading at its sides (a visible edge on white)."""
    cx = x0 + w / 2
    L.rect((cx - 0.2 * w, y0, cx + 0.2 * w, y0 + 0.09 * h), cap, radius=0.03 * w)
    L.rect((cx - 0.15 * w, y0 + 0.08 * h, cx + 0.15 * w, y0 + 0.2 * h), (205, 205, 205))
    L.polygon([(cx - 0.15 * w, y0 + 0.18 * h), (cx + 0.15 * w, y0 + 0.18 * h), (x0 + w, y0 + 0.33 * h),
               (x0, y0 + 0.33 * h)], (205, 205, 205))
    L.rect((x0, y0 + 0.3 * h, x0 + w, y0 + h), (200, 200, 200), radius=0.1 * w)
    L.rect((x0 + 0.06 * w, y0 + 0.31 * h, x0 + 0.94 * w, y0 + 0.99 * h), (236, 236, 236), radius=0.08 * w)
    L.rect((x0 + 0.14 * w, y0 + 0.32 * h, x0 + 0.86 * w, y0 + 0.98 * h), (250, 250, 250), radius=0.06 * w)
    text_bars(L, x0 + 0.2 * w, y0 + 0.55 * h, x0 + 0.8 * w, y0 + 0.7 * h, (40, 90, 160))


def draw_can(L, x0, y0, w, h, colour=(20, 110, 190), print_=(250, 250, 250)):
    L.rect((x0 + 0.04 * w, y0, x0 + 0.96 * w, y0 + 0.06 * h), (190, 190, 195), radius=0.03 * w)
    L.rect((x0, y0 + 0.04 * h, x0 + w, y0 + 0.97 * h), colour, radius=0.06 * w)
    L.rect((x0 + 0.04 * w, y0 + 0.94 * h, x0 + 0.96 * w, y0 + h), (170, 170, 175), radius=0.03 * w)
    L.ellipse((x0 + 0.2 * w, y0 + 0.3 * h, x0 + 0.8 * w, y0 + 0.55 * h), print_)
    text_bars(L, x0 + 0.15 * w, y0 + 0.65 * h, x0 + 0.85 * w, y0 + 0.85 * h, print_)


def draw_carton(L, x0, y0, w, h, face=(200, 30, 35), logo=(250, 250, 250), print_=(250, 250, 250), gable=False,
                small_print=False):
    top = y0
    if gable:
        L.polygon([(x0, y0 + 0.12 * h), (x0 + w / 2, y0), (x0 + w, y0 + 0.12 * h)], face)
        L.rect((x0 + 0.45 * w, y0 - 0.0, x0 + 0.55 * w, y0 + 0.04 * h), face)
        top = y0 + 0.12 * h
    L.rect((x0, top, x0 + w, y0 + h), face)
    if small_print:
        # print only (no logo): a few lines of text, about 6% of the face
        text_bars(L, x0 + 0.2 * w, y0 + 0.35 * h, x0 + 0.8 * w, y0 + 0.65 * h, print_, lines=5)
        return
    L.ellipse((x0 + 0.18 * w, top + 0.12 * h, x0 + 0.82 * w, top + 0.42 * h), logo)
    text_bars(L, x0 + 0.15 * w, y0 + 0.62 * h, x0 + 0.85 * w, y0 + 0.86 * h, print_)


def draw_jar(L, x0, y0, w, h, glass=(150, 90, 40), lid=(30, 120, 60), label=(245, 240, 220)):
    L.rect((x0 + 0.05 * w, y0, x0 + 0.95 * w, y0 + 0.16 * h), lid, radius=0.04 * w)
    L.rect((x0, y0 + 0.14 * h, x0 + w, y0 + h), glass, radius=0.14 * w)
    L.rect((x0, y0 + 0.4 * h, x0 + w, y0 + 0.78 * h), label)
    text_bars(L, x0 + 0.15 * w, y0 + 0.47 * h, x0 + 0.85 * w, y0 + 0.7 * h, (120, 30, 30))


def draw_jerrycan(L, x0, y0, w, h, colour=(240, 200, 40), cap=(200, 30, 30)):
    """Handle bottle (oil / detergent): the handle has a real hole."""
    L.rect((x0 + 0.08 * w, y0, x0 + 0.3 * w, y0 + 0.08 * h), cap, radius=0.02 * w)
    L.rect((x0, y0 + 0.07 * h, x0 + w, y0 + h), colour, radius=0.08 * w)
    L.clear((x0 + 0.5 * w, y0 + 0.14 * h, x0 + 0.86 * w, y0 + 0.3 * h), radius=0.05 * w)
    L.rect((x0 + 0.08 * w, y0 + 0.45 * h, x0 + 0.92 * w, y0 + 0.85 * h), (250, 250, 250))
    text_bars(L, x0 + 0.15 * w, y0 + 0.52 * h, x0 + 0.85 * w, y0 + 0.78 * h, (20, 60, 140))


def draw_juicebox(L, x0, y0, w, h, colour=(250, 140, 20), straw_w=4.0, straw_h=None, straw=(250, 250, 250)):
    """Juice box with a straw on top: (x0, y0) is the top of the straw."""
    straw_h = straw_h if straw_h is not None else 0.22 * h
    box_top = y0 + straw_h
    sx = x0 + 0.7 * w
    L.rect((sx, y0, sx + straw_w, box_top + 2), straw)
    L.rect((x0, box_top, x0 + w, y0 + h), colour)
    L.ellipse((x0 + 0.15 * w, box_top + 0.1 * h, x0 + 0.85 * w, box_top + 0.4 * h), (60, 160, 40))
    text_bars(L, x0 + 0.15 * w, y0 + 0.75 * h, x0 + 0.85 * w, y0 + 0.92 * h, (255, 255, 255))


def draw_clear_bottle(L, x0, y0, w, h, body_alpha=40, cap=(20, 60, 170), label=(30, 120, 200)):
    """Clear plastic water bottle as a provider mattes it: the body is translucent (alpha < 128), so only the cap
    and the label are solid and the body is what joins them."""
    cx = x0 + w / 2
    clear = (215, 228, 240, body_alpha)
    L.rect((cx - 0.15 * w, y0 + 0.08 * h, cx + 0.15 * w, y0 + 0.2 * h), clear)
    L.polygon([(cx - 0.15 * w, y0 + 0.18 * h), (cx + 0.15 * w, y0 + 0.18 * h), (x0 + w, y0 + 0.33 * h),
               (x0, y0 + 0.33 * h)], clear)
    L.rect((x0, y0 + 0.3 * h, x0 + w, y0 + h), clear, radius=0.1 * w)
    L.rect((cx - 0.2 * w, y0, cx + 0.2 * w, y0 + 0.09 * h), cap, radius=0.03 * w)
    L.rect((x0, y0 + 0.42 * h, x0 + w, y0 + 0.66 * h), label)
    text_bars(L, x0 + 0.15 * w, y0 + 0.46 * h, x0 + 0.85 * w, y0 + 0.62 * h, (250, 250, 250))


def draw_white_jar(L, x0, y0, w, h, lid=(40, 150, 60)):
    """A noise-free white jar: body 249-253 (soft shading, no darker outline) and a green lid."""
    L.rect((x0 + 0.05 * w, y0, x0 + 0.95 * w, y0 + 0.16 * h), lid, radius=0.04 * w)
    bands = (249, 250, 251, 252, 253, 253, 252, 251, 250, 249)
    for i, value in enumerate(bands):
        left = x0 + w * i / len(bands)
        L.rect((left, y0 + 0.14 * h, left + w / len(bands) + 1, y0 + h), (value, value, value))


DRAWERS = {"bottle": draw_bottle, "clear_bottle": draw_clear_bottle, "white_jar": draw_white_jar, "can": draw_can, "carton": draw_carton, "jar": draw_jar,
           "jerrycan": draw_jerrycan, "juicebox": draw_juicebox, "white_bottle": draw_white_bottle}


# ---------------------------------------------------------------------------
# A shot: ground truth + what the photo also contains
# ---------------------------------------------------------------------------

@dataclass
class Shot:
    name: str
    product: Image.Image                  # RGBA ground truth (straight colours, anti-aliased alpha)
    bg: Optional[Tuple[int, int, int]]    # None: a transparent PNG source
    extras_under: List[Image.Image] = field(default_factory=list)   # shadow / card / reflection, under the product
    extras_over: List[Image.Image] = field(default_factory=list)    # watermark, price tag, over the product
    box: Optional[List[float]] = None     # the Gemini box [ymin, xmin, ymax, xmax] on the 0-1000 scale
    noise: float = 0.0                    # gaussian noise sigma on an opaque source (deterministic)
    jpeg: bool = False

    @property
    def size(self):
        return self.product.size

    def alpha(self) -> np.ndarray:
        return np.asarray(self.product.getchannel("A"))

    def solid(self) -> np.ndarray:
        return self.alpha() >= 128

    def scene(self) -> Image.Image:
        """Everything in the photo except the background, as RGBA."""
        out = Image.new("RGBA", self.size, (0, 0, 0, 0))
        for extra in self.extras_under:
            out.alpha_composite(extra)
        out.alpha_composite(self.product)
        for extra in self.extras_over:
            out.alpha_composite(extra)
        return out

    def source(self) -> Image.Image:
        scene = self.scene()
        if self.bg is None:
            return scene
        flat = Image.new("RGBA", self.size, self.bg + (255,))
        flat.alpha_composite(scene)
        rgb = flat.convert("RGB")
        if self.noise:
            arr = np.asarray(rgb).astype(np.float64)
            arr += np.random.default_rng(7).normal(0.0, self.noise, arr.shape)
            rgb = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")
        return rgb

    def source_bytes(self) -> bytes:
        buf = io.BytesIO()
        src = self.source()
        if self.jpeg and src.mode == "RGB":
            src.save(buf, format="JPEG", quality=92)
        else:
            src.save(buf, format="PNG")
        return buf.getvalue()

    def save(self, directory) -> str:
        path = str(directory / f"{self.name}.{'jpg' if self.jpeg and self.bg is not None else 'png'}")
        with open(path, "wb") as fh:
            fh.write(self.source_bytes())
        return path


def product_box(alpha: np.ndarray, threshold=8):
    ys, xs = np.nonzero(alpha > threshold)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def gemini_box_of(alpha: np.ndarray, pad=0.0, region=None) -> List[float]:
    """The Gemini answer for the product: [ymin, xmin, ymax, xmax] on 0-1000 (region = (l, t, r, b) in px)."""
    h, w = alpha.shape
    l, t, r, b = region or product_box(alpha)
    pw, ph = (r - l) * pad, (b - t) * pad
    return [max(0.0, (t - ph) / h * 1000), max(0.0, (l - pw) / w * 1000),
            min(1000.0, (b + ph) / h * 1000), min(1000.0, (r + pw) / w * 1000)]


def make(kind, size=(600, 800), fill=0.62, aspect=None, bg=WHITE, offset=(0.0, 0.0), ss=SS, **style) -> Shot:
    """One product centred in the frame. fill: product height / frame height (or width for a wide product).
    ss: supersampling (lower it for very large shots)."""
    W, H = size
    aspect = aspect or {"bottle": 0.36, "white_bottle": 0.36, "clear_bottle": 0.36, "can": 0.55, "carton": 0.5,
                        "jar": 0.8, "white_jar": 0.8, "jerrycan": 0.75, "juicebox": 0.55}.get(kind, 0.5)
    h = H * fill
    w = h * aspect
    if w > W * 0.9:
        w = W * fill
        h = w / aspect
    x0 = (W - w) / 2 + offset[0] * W
    y0 = (H - h) / 2 + offset[1] * H
    L = Layer(size, ss)
    DRAWERS[kind](L, x0, y0, w, h, **style)
    product = L.done()
    return Shot(f"{kind}_{W}x{H}", product, bg, box=gemini_box_of(np.asarray(product.getchannel("A"))))


def make_pack(kind, size=(900, 700), gap=14.0, fill=0.6, aspect=None, bg=WHITE, **style) -> Shot:
    """A two-pack side by side: gap > 0 leaves background between them, gap <= 0 makes them touch / overlap."""
    W, H = size
    aspect = aspect or {"bottle": 0.36, "can": 0.55, "carton": 0.6}.get(kind, 0.5)
    h = H * fill
    w = h * aspect
    total = 2 * w + gap
    x0 = (W - total) / 2
    y0 = (H - h) / 2
    L = Layer(size)
    DRAWERS[kind](L, x0, y0, w, h, **style)
    DRAWERS[kind](L, x0 + w + gap, y0, w, h, **style)
    product = L.done()
    return Shot(f"{kind}_pack_gap{int(gap)}_{W}x{H}", product, bg,
                box=gemini_box_of(np.asarray(product.getchannel("A"))))


# ---------------------------------------------------------------------------
# What else a photo contains (ground truth: not product)
# ---------------------------------------------------------------------------

def soft_shadow(shot: Shot, dx=0.0, dy=0.0, opacity=0.45, blur=14.0, squash=0.12, colour=(20, 20, 20)) -> Image.Image:
    """A contact / cast shadow: an ellipse under the product's base, blurred."""
    l, t, r, b = product_box(shot.alpha())
    L = Layer(shot.size)
    hh = (b - t) * squash
    L.ellipse((l - 0.05 * (r - l) + dx, b - hh / 2 + dy, r + 0.05 * (r - l) + dx, b + hh / 2 + dy),
              colour + (int(255 * opacity),))
    return L.done().filter(ImageFilter.GaussianBlur(blur))


def offset_shadow(shot: Shot, dx=40, dy=24, alpha_range=(48, 97), blur=3.0) -> Image.Image:
    """The product's silhouette shifted (a drop shadow, slightly blurred), at a visible partial alpha."""
    a = shot.alpha().astype(np.float64) / 255.0
    shifted = np.zeros_like(a)
    h, w = a.shape
    shifted[max(0, dy):h, max(0, dx):w] = a[0:h - max(0, dy), 0:w - max(0, dx)]
    lo, hi = alpha_range
    yy = np.linspace(0, 1, h)[:, None]
    level = lo + (hi - lo) * yy     # darker at the bottom
    out = np.zeros((h, w, 4), np.uint8)
    out[..., :3] = (30, 30, 30)
    out[..., 3] = np.clip(shifted * level, 0, 255).astype(np.uint8)
    img = Image.fromarray(out, "RGBA")
    return img.filter(ImageFilter.GaussianBlur(blur)) if blur else img


def reflection(shot: Shot, strength=0.35, length=0.3) -> Image.Image:
    """A mirror reflection under the product that fades out."""
    prod = np.asarray(shot.product).astype(np.float64)
    l, t, r, b = product_box(shot.alpha())
    h, w = prod.shape[:2]
    n = min(int((b - t) * length), h - b)
    out = np.zeros((h, w, 4), np.float64)
    for i in range(n):
        src_row = b - 1 - i
        fade = strength * (1.0 - i / n)
        out[b + i, :, :3] = prod[src_row, :, :3] * 0.5 + 127
        out[b + i, :, 3] = prod[src_row, :, 3] * fade
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGBA")


def photo_card(shot: Shot, margin=0.12, colour=CARD_GREY) -> Image.Image:
    """A grey photo card behind the product (a product photo pasted on a card)."""
    l, t, r, b = product_box(shot.alpha())
    mw, mh = (r - l) * margin + 30, (b - t) * margin + 30
    L = Layer(shot.size)
    L.rect((max(0, l - mw), max(0, t - mh), min(shot.size[0], r + mw), min(shot.size[1], b + mh)), colour)
    return L.done()


def watermark_letters(shot: Shot, count=9, share=0.0054, colour=(60, 60, 60)) -> Image.Image:
    """Separate letters (each `share` of the product's solid area) in a row under the product."""
    area = float(shot.solid().sum()) * share
    side = max(4.0, area ** 0.5)
    l, t, r, b = product_box(shot.alpha())
    W, H = shot.size
    y = min(H - side - 4, b + 12)
    start = max(4.0, (W - count * side * 1.6) / 2)
    L = Layer(shot.size)
    for i in range(count):
        x = start + i * side * 1.6
        L.rect((x, y, x + side, y + side), colour)
    return L.done()


def price_tag(shot: Shot, share=0.05, colour=(250, 220, 30)) -> Image.Image:
    l, t, r, b = product_box(shot.alpha())
    area = float(shot.solid().sum()) * share
    w = (area * 1.6) ** 0.5
    h = area / w
    L = Layer(shot.size)
    x = max(4.0, l - w - 20)
    L.rect((x, b - h, x + w, b), colour)
    return L.done()


def with_extras(shot: Shot, name, under=(), over=(), box=None, **kwargs) -> Shot:
    return Shot(name, shot.product, kwargs.pop("bg", shot.bg), list(shot.extras_under) + list(under),
                list(shot.extras_over) + list(over), box if box is not None else shot.box, **kwargs)


# ---------------------------------------------------------------------------
# Offline fakes
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status_code=200, content=b""):
        self.status_code = status_code
        self.content = content
        self.text = content.decode("utf-8", "replace") if isinstance(content, bytes) else str(content)

    def json(self):
        return json.loads(self.content)


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _refuse(*_args, **_kwargs):
    raise RuntimeError("network access is blocked in tests")


Edit = Callable[[np.ndarray, Tuple[int, int, int, int]], np.ndarray]


def _at_work_size(img: Image.Image, work_size) -> Image.Image:
    return img if img.size == tuple(work_size) else img.resize(tuple(work_size), Image.Resampling.BOX)


def truth_provider(shot: Shot, keep: Tuple[Image.Image, ...] = (), edit: Optional[Edit] = None):
    """A provider that isolates exactly the product (+ `keep`: extras it wrongly keeps). edit(rgba, rect) -> rgba.
    frame_rect is in the pipeline's work image (the source reduced to MAX_WORK_SIDE)."""

    def answer(frame_rect, form, work_size):
        cut = Image.new("RGBA", shot.size, (0, 0, 0, 0))
        for extra in keep:
            cut.alpha_composite(extra)
        cut.alpha_composite(shot.product)
        cut = _at_work_size(cut, work_size).crop(frame_rect)
        arr = np.array(cut)
        arr[arr[..., 3] == 0, :3] = 0
        if edit is not None:
            arr = edit(arr, frame_rect)
        out = Image.fromarray(arr, "RGBA")
        if form.get("crop") == "true":
            ys, xs = np.nonzero(arr[..., 3] > 8)
            out = out.crop((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))
        return FakeResponse(200, png_bytes(out))

    return answer


def opaque_provider(shot: Shot):
    """A no-op segmentation: the frame comes back fully opaque."""

    def answer(frame_rect, form, work_size):
        return FakeResponse(200, png_bytes(_at_work_size(shot.source().convert("RGBA"), work_size).crop(frame_rect)))

    return answer


class Services:
    """Installs the fakes on image_processor `ip` through `mp` (a pytest MonkeyPatch)."""

    def __init__(self, mp, ip, config, shot: Shot, photoroom=None, remove_bg=None, box: bool = True,
                 crop: bool = False, white_mode: str = "log", gemini_box=None):
        self.ip, self.shot = ip, shot
        self.calls: List[Tuple[str, Tuple[int, int], Tuple[int, int, int, int]]] = []
        self.box_calls = 0
        self.handlers = {"photoroom": photoroom, "remove_bg_api": remove_bg}
        self.urls = {ip.PHOTOROOM_URL: "photoroom", ip.REMOVE_BG_URL: "remove_bg_api"}
        self.gemini_box = gemini_box if gemini_box is not None else (shot.box if box else None)

        mp.setattr(socket.socket, "connect", _refuse)
        mp.setattr(socket.socket, "connect_ex", _refuse)
        mp.setattr(socket, "create_connection", _refuse)
        mp.setattr(socket, "getaddrinfo", _refuse)
        mp.setattr(ip.requests, "post", self.post)
        mp.setattr(config, "GEMINI_API_KEY", "")
        mp.setattr(config, "PHOTOROOM_API_KEY", "test-photoroom-key" if photoroom else "")
        mp.setattr(config, "REMOVE_BG_API_KEY", "test-removebg-key" if remove_bg else "")
        mp.setattr(config, "BG_REMOVAL_METHOD", "photoroom")
        mp.setattr(config, "PHOTOROOM_CROP", crop)
        mp.setattr(config, "WHITE_SOURCE_MODE", white_mode, raising=False)
        mp.setattr(config, "ENABLE_STUDIO_SHADOWS", False)
        mp.setattr(config, "PROXY_URL", "")
        if hasattr(config, "OUTPUT_CANVAS_SIZE"):
            mp.delattr(config, "OUTPUT_CANVAS_SIZE")
        mp.delenv("OUTPUT_CANVAS_SIZE", raising=False)
        mp.setattr(ip, "_locate_product_box", self.locate)
        mp.setattr(ip, "_isolate_grabcut", lambda img: (_ for _ in ()).throw(AssertionError("GrabCut used")))
        mp.setattr(ip, "_isolate_rembg", lambda img: (_ for _ in ()).throw(AssertionError("rembg used")))

    def locate(self, img, name, brand):
        self.box_calls += 1
        return list(self.gemini_box) if self.gemini_box else None

    @property
    def work_size(self):
        """The pipeline's work image size (the source reduced to MAX_WORK_SIDE, as _limit_work_size does)."""
        probe = Image.new("1", self.shot.size)
        side = self.ip.MAX_WORK_SIDE
        if max(probe.size) > side:
            probe.thumbnail((side, side))
        return probe.size

    def frame_rect(self, sent_size):
        W, H = self.work_size
        if tuple(sent_size) == (W, H):
            return (0, 0, W, H)
        assert self.gemini_box, f"a {sent_size} frame was sent without a Gemini box"
        rect = self.ip._box_rect((W, H), self.gemini_box)
        assert (rect[2] - rect[0], rect[3] - rect[1]) == tuple(sent_size), (rect, sent_size)
        return rect

    def post(self, url, headers=None, files=None, data=None, timeout=None, **_kw):
        name = self.urls.get(url)
        assert name is not None, f"unexpected POST to {url}"
        _filename, blob, _mime = files["image_file"]
        sent = Image.open(io.BytesIO(blob))
        sent.load()
        rect = self.frame_rect(sent.size)
        self.calls.append((name, sent.size, rect))
        handler = self.handlers[name]
        assert handler is not None, f"{name} must not be called"
        return handler(rect, dict(data or {}), self.work_size)

    @property
    def paid(self) -> int:
        return len(self.calls)

    def names(self) -> List[str]:
        return [name for name, _size, _rect in self.calls]


def run_shot(ip, config, shot: Shot, directory, photoroom="truth", remove_bg=None, box=True, crop=False,
             white_mode="log", gemini_box=None, canvas=(800, 800)):
    """Process one shot offline. Returns (ProcessResult, Services)."""
    import pytest

    if photoroom == "truth":
        photoroom = truth_provider(shot)
    if remove_bg == "truth":
        remove_bg = truth_provider(shot)
    with pytest.MonkeyPatch.context() as mp:
        services = Services(mp, ip, config, shot, photoroom=photoroom, remove_bg=remove_bg, box=box, crop=crop,
                            white_mode=white_mode, gemini_box=gemini_box)
        mp.setattr(ip.tempfile, "tempdir", str(directory))
        src = shot.save(directory)
        result = ip.process_product_image_result(src, "Product", "Brand", canvas[0], canvas[1],
                                                 bg_method="photoroom")
    return result, services


def canvas_array(result) -> np.ndarray:
    with Image.open(result.path) as out:
        out.load()
        return np.asarray(out.convert("RGB")).copy()


def ink_box(arr: np.ndarray, threshold=250):
    ys, xs = np.nonzero((arr < threshold).any(axis=2))
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


# ---------------------------------------------------------------------------
# The false-positive corpus: good packshots and damaged ones
# ---------------------------------------------------------------------------

def transparent(shot: Shot, name=None) -> Shot:
    return Shot(name or shot.name + "_png", shot.product, None, box=shot.box)


def good_corpus() -> Dict[str, List[Tuple[Shot, dict]]]:
    """name of the set -> [(shot, run options)]. Every one must come out clean."""
    sets: Dict[str, List[Tuple[Shot, dict]]] = {"opaque": [], "transparent": [], "two_packs": []}
    opaque = [
        make("bottle", (600, 900)),
        make("can", (500, 700), bg=STUDIO),
        make("carton", (800, 1000), gable=True),
        make("carton", (1200, 1200), fill=0.7, face=(30, 90, 200), logo=(250, 220, 40)),
        make("jar", (700, 700), fill=0.6),
        make("jerrycan", (900, 1000)),
        make("juicebox", (600, 900)),
        make("white_bottle", (600, 900)),
        make("carton", (1000, 600), fill=0.7, aspect=1.7, face=(40, 150, 70)),   # a wide box
        make("bottle", (500, 650), fill=0.55),
    ]
    opaque[8].name = "wide_box_1000x600"
    for shot in opaque:
        sets["opaque"].append((shot, {}))

    cartons = [
        make("carton", (800, 1000), fill=0.8, face=(200, 30, 35), logo=(250, 250, 250)),
        make("carton", (900, 900), fill=0.75, face=(30, 70, 190), logo=(250, 215, 30), print_=(250, 215, 30)),
        make("carton", (700, 900), fill=0.8, face=(30, 150, 80), small_print=True),
        make("carton", (600, 800), fill=0.85, face=(245, 245, 242), logo=(40, 110, 200), print_=(40, 110, 200)),
        make("bottle", (600, 900)),
        make("can", (600, 800)),
        make("jerrycan", (800, 900)),
    ]
    names = ["carton_red_logo", "carton_blue_yellow", "carton_small_print", "carton_white_print", "bottle", "can",
             "jerrycan"]
    for shot, name in zip(cartons, names):
        # a white printed carton: low saturation, so its free source alpha is re-checked by one provider call
        sets["transparent"].append((transparent(shot, name + "_png"),
                                    {"expect_paid": 1 if name == "carton_white_print" else 0}))

    packs = [
        make_pack("bottle", (900, 800), gap=18),
        make_pack("bottle", (800, 800), gap=-2),
        make_pack("can", (900, 700), gap=20),
        make_pack("carton", (1000, 700), gap=24, face=(200, 120, 30)),
        make_pack("carton", (900, 700), gap=0, face=(200, 120, 30)),
    ]
    for shot in packs:
        sets["two_packs"].append((shot, {}))
        sets["two_packs"].append((transparent(shot), {"expect_paid": 0}))
    return sets


def damaged_corpus():
    """
    name -> (shot, run options, (how, flag)). how 'flag': the final result is not isolated and carries `flag`;
    how 'retry': the first (Gemini crop) attempt carried `flag`, so the full frame was tried (2 calls) and the
    published canvas is the whole product.
    """
    bottle = make("bottle", (600, 900))
    can = make("can", (600, 800))
    juice = make("juicebox", (600, 900), straw_w=4.0)
    out = {}
    # a provider that keeps a grey photo card (opaque source), without and with a Gemini box
    carded = with_extras(bottle, "bottle_on_card", under=[photo_card(bottle)])
    keeper = truth_provider(carded, keep=(carded.extras_under[0],))
    out["kept_card_no_box"] = (carded, {"photoroom": keeper, "box": False}, ("flag", "opaque_backdrop"))
    out["kept_card_with_box"] = (carded, {"photoroom": keeper}, ("flag", "opaque_fill"))
    # an offset shadow kept by the provider at alpha 48-97
    shadowed = with_extras(can, "can_offset_shadow", under=[offset_shadow(can)])
    out["kept_offset_shadow"] = (shadowed, {"photoroom": truth_provider(shadowed, keep=(shadowed.extras_under[0],))},
                                 ("flag", "kept_shadow"))
    # a transparent PNG with a baked-in shadow and no provider key to re-isolate it
    baked = Shot("can_baked_shadow_png", can.product, None, extras_under=[offset_shadow(can)], box=can.box)
    out["baked_shadow_png"] = (baked, {"photoroom": None}, ("flag", "kept_shadow"))
    # nine watermark letters kept by the provider (0.54% each)
    marked = with_extras(bottle, "bottle_watermark", over=[watermark_letters(bottle)])
    out["watermark_letters"] = (marked, {"photoroom": truth_provider(marked, keep=(marked.extras_over[0],))},
                                ("flag", "second_object"))
    # a distinct smaller object kept (a price tag)
    tagged = with_extras(bottle, "bottle_price_tag", over=[price_tag(bottle)])
    out["price_tag"] = (tagged, {"photoroom": truth_provider(tagged, keep=(tagged.extras_over[0],)), "box": False},
                        ("flag", "second_object"))
    # a no-op segmentation of a studio photo
    studio = make("bottle", (600, 900), bg=(150, 160, 170))
    out["opaque_noop"] = (studio, {"photoroom": opaque_provider(studio)}, ("flag", "opaque_fill"))
    # the Gemini box encloses the carton only and cuts the 4 px straw
    a = juice.alpha()
    l, t, r, b = product_box(a)
    carton_top = t + int(round(0.22 * (b - t)))
    out["straw_cut_by_box"] = (juice, {"gemini_box": gemini_box_of(a, region=(l, carton_top, r, b))},
                               ("retry", "edge_clipped"))
    return out


def white_source_good() -> Dict[str, Shot]:
    """Clean packshots on a white background: the free white-source cutout is right for them."""
    shots = {
        "bottle": make("bottle", (600, 900)),
        "can": make("can", (600, 800)),
        "carton": make("carton", (800, 1000), gable=True),
        "jar": make("jar", (700, 700)),
        "jerrycan": make("jerrycan", (900, 1000)),
        "juicebox": make("juicebox", (600, 900), straw=(30, 160, 220)),
        "white_bottle": make("white_bottle", (600, 900)),
        "wide_box": make("carton", (1000, 600), fill=0.7, aspect=1.7, face=(40, 150, 70)),
    }
    noisy = make("bottle", (700, 1000))
    noisy.noise, noisy.jpeg, noisy.name = 1.5, True, "bottle_noisy_jpeg"
    shots["bottle_noisy_jpeg"] = noisy
    return shots


def white_source_bad() -> Dict[str, Shot]:
    """White-background photos where the white-source cutout is wrong (shadow/reflection baked in, body lost)."""
    can = make("can", (600, 800))
    bottle = make("bottle", (600, 900))
    jar = make("white_jar", (700, 700), fill=0.6)
    return {
        "soft_shadow": with_extras(can, "can_soft_shadow", under=[soft_shadow(can, opacity=0.5, blur=10, squash=0.1)]),
        "reflection": with_extras(bottle, "bottle_reflection", under=[reflection(bottle)]),
        "offset_shadow": with_extras(can, "can_offset_shadow", under=[offset_shadow(can, blur=4.0)]),
        "white_jar": Shot("white_jar", jar.product, WHITE, box=jar.box),
        "white_straw": make("juicebox", (600, 900)),   # the white straw (250) is as white as the background
    }
