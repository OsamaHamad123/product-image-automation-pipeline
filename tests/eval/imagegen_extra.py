"""More synthetic image recipes, for the sets other than the committed golden set (fixtures/realistic).

imagegen.py is fingerprinted by the v1 baseline (harness.fixture_fingerprint), so it never changes; the recipes
the realistic set needs live here and every other kind is passed to imagegen unchanged:

    packshot_backdrop   a studio packshot on a uniform NON-white background (background_color, default a warm
                        grey): the retailer photos shot on a coloured sweep that live runs keep finding. The product,
                        its label and its contact shadow are drawn exactly like imagegen's packshot_white.

generate(recipe, seed) / mime_type(recipe) have imagegen's signatures.
"""

from __future__ import annotations

import io
from typing import Any, Mapping, Union

from PIL import Image

import imagegen

EXTRA_RECIPES = ("packshot_backdrop",)
DEFAULT_BACKDROP = "#d8d2c8"


def render(recipe: Union[Mapping[str, Any], str], seed: int = 0) -> Image.Image:
    if isinstance(recipe, str):
        recipe = {"kind": recipe}
    if recipe.get("kind") != "packshot_backdrop":
        return imagegen.render(recipe, seed)
    params = dict(recipe, kind="packshot_white", background="transparent")
    cut = imagegen.render(params, seed)                     # RGBA: product + soft shadow, transparent elsewhere
    colour = imagegen._rgb(recipe.get("background_color"), imagegen._rgb(DEFAULT_BACKDROP, (216, 210, 200)))
    canvas = Image.new("RGBA", cut.size, colour + (255,))
    canvas.alpha_composite(cut)
    return canvas.convert("RGB")


def image_format(recipe: Union[Mapping[str, Any], str]) -> str:
    if isinstance(recipe, str):
        recipe = {"kind": recipe}
    if recipe.get("kind") == "packshot_backdrop":
        return str(recipe.get("format") or "JPEG").upper()
    return imagegen.image_format(recipe)


def generate(recipe: Union[Mapping[str, Any], str], seed: int = 0) -> bytes:
    if isinstance(recipe, str):
        recipe = {"kind": recipe}
    if recipe.get("kind") not in EXTRA_RECIPES:
        return imagegen.generate(recipe, seed)
    img = render(recipe, seed)
    buf = io.BytesIO()
    if image_format(recipe) == "PNG":
        img.save(buf, format="PNG", optimize=False, compress_level=6)
    else:
        img.save(buf, format="JPEG", quality=int(recipe.get("quality", 90)), subsampling=2, optimize=False)
    return buf.getvalue()


def mime_type(recipe: Union[Mapping[str, Any], str]) -> str:
    return "image/png" if image_format(recipe) == "PNG" else "image/jpeg"
