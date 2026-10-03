"""The cutout quality gate on realistic synthetic packshots (anti-aliased, with ground truth; tests/packshot_synth.py).

Each test is a review finding: what a good packshot must cost (paid calls) and that it publishes clean, or that a
damaged one is flagged. Everything is offline: PhotoRoom / remove.bg / the Gemini box are fakes, sockets are blocked.
"""

import numpy as np
import pytest

import config
import image_processor as ip
import packshot_synth as ps


@pytest.fixture
def work(tmp_path):
    return tmp_path


def run(shot, work, **kwargs):
    return ps.run_shot(ip, config, shot, work, **kwargs)


# ---------------------------------------------------------------------------
# #1 / 7c: an opaque photo backdrop, not a printed carton
# ---------------------------------------------------------------------------

PRINTED_CARTONS = {
    "red_white_logo": dict(face=(200, 30, 35), logo=(250, 250, 250)),
    "blue_yellow_logo": dict(face=(30, 70, 190), logo=(250, 215, 30), print_=(250, 215, 30)),
    "green_small_print": dict(face=(30, 150, 80), small_print=True),
}


@pytest.mark.parametrize("name", sorted(PRINTED_CARTONS))
def test_printed_carton_png_is_free_source_alpha(work, name):
    shot = ps.transparent(ps.make("carton", (800, 1000), fill=0.8, **PRINTED_CARTONS[name]), name)
    assert ip.assess_cutout(shot.product, check_backdrop=True) == []

    result, services = run(shot, work, remove_bg="truth")

    assert (result.isolated, result.provider, result.quality_flags) == (True, "source_alpha", [])
    assert services.paid == 0 and services.box_calls == 0


def test_white_printed_carton_png_is_confirmed_by_one_provider_call(work):
    # Low saturation: the check cannot tell it from a white photo card, so one provider looks; it returns the
    # same rectangle (IoU > 0.95), which confirms the source. Before: PhotoRoom twice, then review.
    shot = ps.transparent(ps.make("carton", (600, 800), fill=0.85, face=(245, 245, 242), logo=(40, 110, 200),
                                  print_=(40, 110, 200)), "white_carton")
    assert ip.assess_cutout(shot.product, check_backdrop=True) == [ip.FLAG_OPAQUE_BACKDROP]

    result, services = run(shot, work, box=False, remove_bg="truth")

    assert (result.isolated, result.provider, result.quality_flags) == (True, "source_alpha", [])
    assert services.names() == ["photoroom"]


def test_grey_card_png_is_isolated_by_the_provider(work):
    bottle = ps.make("bottle", (700, 900))
    shot = ps.Shot("carded_png", bottle.product, None, extras_under=[ps.photo_card(bottle)], box=bottle.box)

    result, services = run(shot, work)

    assert (result.isolated, result.provider, result.quality_flags) == (True, "photoroom", [])
    assert services.paid == 1
    out = ps.canvas_array(result)
    grey = (np.abs(out.astype(int) - np.array(ps.CARD_GREY)).max(axis=2) <= 12).sum()
    assert grey == 0, "the photo card was published"


@pytest.mark.parametrize("with_box", [False, True])
def test_provider_that_keeps_a_grey_card_on_an_opaque_photo_is_flagged(work, with_box):
    # 7c: the backdrop check used to run only on source alpha, so this was published clean.
    bottle = ps.make("bottle", (700, 900))
    shot = ps.with_extras(bottle, "bottle_on_card", under=[ps.photo_card(bottle)])
    keeper = ps.truth_provider(shot, keep=(shot.extras_under[0],))

    result, services = run(shot, work, photoroom=keeper, box=with_box)

    assert result.path and result.isolated is False
    # full frame: the card around the bottle; the Gemini crop lies inside the card, so it comes back all opaque
    assert result.quality_flags == [ip.FLAG_OPAQUE_FILL if with_box else ip.FLAG_OPAQUE_BACKDROP]


def test_white_carton_on_an_opaque_photo_matches_the_gemini_box(work):
    # The provider's rectangle is where Gemini located the product: a carton, not a kept photo card.
    shot = ps.make("carton", (700, 900), fill=0.75, face=(245, 245, 242), logo=(40, 110, 200),
                   print_=(40, 110, 200), bg=ps.STUDIO)

    result, services = run(shot, work)

    assert (result.isolated, result.provider, result.quality_flags) == (True, "photoroom", [])
    assert services.paid == 1


# ---------------------------------------------------------------------------
# #3 / 7b / clear bottles: what is one product, what is a second object
# ---------------------------------------------------------------------------

PACKS = {
    "bottles": lambda: ps.make_pack("bottle", (900, 800), gap=18),
    "cans": lambda: ps.make_pack("can", (900, 700), gap=20),
    "boxes": lambda: ps.make_pack("carton", (1000, 700), gap=24, face=(200, 120, 30)),
}


@pytest.mark.parametrize("opaque", [True, False])
@pytest.mark.parametrize("kind", sorted(PACKS))
def test_two_pack_with_a_gap_is_one_product(work, kind, opaque):
    # Before: second_object (boxes also too_small_on_canvas) after 2 paid calls, then review.
    shot = PACKS[kind]()
    if not opaque:
        shot = ps.transparent(shot)

    result, services = run(shot, work, remove_bg="truth")

    assert result.isolated is True and result.quality_flags == []
    assert services.paid == (1 if opaque else 0)
    assert result.provider == ("photoroom" if opaque else "source_alpha")
    x0, y0, x1, y1 = ps.ink_box(ps.canvas_array(result))
    assert max(x1 - x0, y1 - y0) >= 700, "the pair fills the canvas"


def test_a_distinct_smaller_object_is_still_a_second_object():
    bottle = ps.make("bottle", (900, 900), fill=0.6)
    small = ps.make("bottle", (900, 900), fill=0.4, offset=(0.3, 0.1))   # a smaller, shorter bottle beside it
    pair = bottle.product.copy()
    pair.alpha_composite(small.product)
    assert ip.assess_cutout(pair) == [ip.FLAG_SECOND_OBJECT]
    tagged = ps.with_extras(bottle, "tagged", over=[ps.price_tag(bottle, share=0.03)]).scene()
    assert ip.assess_cutout(tagged) == [ip.FLAG_SECOND_OBJECT]


def test_many_small_stray_pieces_add_up_to_a_second_object():
    # 7b: nine watermark letters at 0.54% each (4.9% together) passed because each was compared alone with 1%.
    bottle = ps.make("bottle", (700, 900))
    marked = ps.with_extras(bottle, "marked", over=[ps.watermark_letters(bottle, count=9, share=0.0054)]).scene()
    assert ip.assess_cutout(marked) == [ip.FLAG_SECOND_OBJECT]
    one = ps.with_extras(bottle, "one", over=[ps.watermark_letters(bottle, count=1, share=0.0054)]).scene()
    assert ip.assess_cutout(one) == [], "a single speck under 1% is not an object"


@pytest.mark.parametrize("body_alpha", [40, 90])
def test_clear_bottle_body_joins_cap_and_label(work, body_alpha):
    # The provider gives a clear plastic body alpha < 128: cap and label are separate solid parts. Grouping on
    # alpha > 24 keeps them one product (before: second_object + too_small_on_canvas after every fallback).
    shot = ps.transparent(ps.make("clear_bottle", (600, 900), body_alpha=body_alpha), f"clear_{body_alpha}")
    assert ip.assess_cutout(shot.product) == []

    result, services = run(shot, work)

    assert (result.isolated, result.provider, result.quality_flags) == (True, "source_alpha", [])
    assert services.paid == 0


# ---------------------------------------------------------------------------
# #4: opaque_fill means "nothing was removed", not "the product fills the frame"
# ---------------------------------------------------------------------------

def tight_carton(margin, size=(600, 800), **style):
    layer = ps.Layer(size)
    ps.draw_carton(layer, margin, margin, size[0] - 2 * margin, size[1] - 2 * margin, **style)
    product = layer.done()
    return ps.Shot(f"tight_{margin}", product, ps.WHITE, box=ps.gemini_box_of(np.asarray(product.getchannel("A"))))


@pytest.mark.parametrize("margin", [0, 1, 3])
def test_tightly_cropped_carton_is_not_opaque_fill(work, margin):
    # Before: opaque_fill after PhotoRoom and after remove.bg (2 paid calls), then review.
    shot = tight_carton(margin)

    result, services = run(shot, work, remove_bg="truth")

    assert (result.isolated, result.provider, result.quality_flags) == (True, "photoroom", [])
    assert services.names() == ["photoroom"], "no fallback spent on a carton that fills its frame"


@pytest.mark.parametrize("bg", [ps.WHITE, (150, 160, 170)])
def test_nothing_removed_on_a_studio_backdrop_is_still_opaque_fill(work, bg):
    shot = ps.make("bottle", (600, 900), bg=bg)
    noop = shot.source().convert("RGBA")
    assert ip.assess_cutout(noop, frame_size=noop.size) == [ip.FLAG_OPAQUE_FILL]

    result, services = run(shot, work, photoroom=ps.opaque_provider(shot), remove_bg="truth")

    assert (result.isolated, result.provider, result.quality_flags) == (True, "remove_bg_api", [])


# ---------------------------------------------------------------------------
# 7a: a thin part cut by the Gemini crop
# ---------------------------------------------------------------------------

def test_thin_straw_cut_by_the_gemini_box_is_edge_clipped(work):
    # The box encloses the carton only: its top crop line cuts the 4 px straw (1.4% of the line, under the old 2%).
    juice = ps.make("juicebox", (600, 900), straw_w=4.0)
    alpha = juice.alpha()
    left, top, right, bottom = ps.product_box(alpha)
    box = ps.gemini_box_of(alpha, region=(left, top + int(round(0.22 * (bottom - top))), right, bottom))
    rect = ip._box_rect(juice.size, box)
    assert rect[1] > top + 10, "precondition: the crop line runs through the straw"
    crop = juice.product.crop(rect)
    sides = (rect[0] > 0, rect[1] > 0, rect[2] < juice.size[0], rect[3] < juice.size[1])
    assert ip.assess_cutout(crop, frame_size=crop.size, crop_sides=sides) == [ip.FLAG_EDGE_CLIPPED]
    # A product that does not reach the crop line is not clipped.
    assert ip.assess_cutout(juice.product.crop(ip._box_rect(juice.size, juice.box)), crop_sides=(True,) * 4) == []

    result, services = run(juice, work, gemini_box=box)

    assert [size for _name, size, _rect in services.calls] == [crop.size, juice.size], "retried without the box"
    assert (result.isolated, result.provider, result.quality_flags) == (True, "photoroom", [])
    whole = ps.ink_box(np.asarray(ip.compose_on_white_canvas(juice.product, (800, 800))))
    assert ps.ink_box(ps.canvas_array(result)) == whole, "the straw is whole on the canvas"


# ---------------------------------------------------------------------------
# 7d: a visible shadow kept with the product
# ---------------------------------------------------------------------------

def test_offset_shadow_kept_by_the_provider_is_flagged(work):
    # alpha 48-97: above the haze limit (32), so it used to set the canvas size: the can was published ~8% smaller
    # and off-centre. remove.bg, which drops the shadow, is used instead.
    can = ps.make("can", (600, 800))
    shot = ps.with_extras(can, "can_shadow", under=[ps.offset_shadow(can)])
    keeper = ps.truth_provider(shot, keep=(shot.extras_under[0],))
    kept = ps.Shot("kept", shot.scene(), None).product
    assert ip.assess_cutout(kept) == [ip.FLAG_KEPT_SHADOW]

    result, services = run(shot, work, photoroom=keeper, box=False)
    assert result.isolated is False and result.quality_flags == [ip.FLAG_KEPT_SHADOW]

    result, services = run(shot, work, photoroom=keeper, remove_bg="truth", box=False)
    assert (result.isolated, result.provider, result.quality_flags) == (True, "remove_bg_api", [])
    x0, y0, x1, y1 = ps.ink_box(ps.canvas_array(result))
    assert y1 - y0 >= 700 and abs((x0 + x1) / 2 - 400) <= 1 and abs((y0 + y1) / 2 - 400) <= 1


def test_transparent_png_with_a_baked_in_shadow_is_not_published_as_source_alpha(work):
    can = ps.make("can", (600, 800))
    baked = ps.Shot("baked_png", can.product, None, extras_under=[ps.offset_shadow(can)], box=can.box)
    assert ip.assess_cutout(baked.scene()) == [ip.FLAG_KEPT_SHADOW]

    result, services = run(baked, work, photoroom=None)   # no provider key: nothing can re-isolate it
    assert (result.isolated, result.provider, result.quality_flags) == (False, "source_alpha", [ip.FLAG_KEPT_SHADOW])

    result, services = run(baked, work)                   # the provider re-isolates the product without it
    assert (result.isolated, result.provider, result.quality_flags) == (True, "photoroom", [])
    assert services.paid == 1


# ---------------------------------------------------------------------------
# #5: small web images (policy: blocking above 3x, a non-blocking note between 2x and 3x)
# ---------------------------------------------------------------------------

def small_bottle(long_side):
    """A web image whose product is `long_side` px tall (the canvas box is 704 px on 800)."""
    size = (int(round(long_side * 0.6)), int(round(long_side / 0.8)))
    return ps.make("bottle", size, fill=0.8)


@pytest.mark.parametrize("long_side, flags, notes", [
    (400, [], []),                      # 1.76x
    (300, [], [ip.NOTE_UPSCALED]),      # 2.35x: used to be blocked (review)
    (250, [], [ip.NOTE_UPSCALED]),      # 2.8x
    (220, [ip.FLAG_UPSCALED], []),      # 3.2x: still blocked
])
def test_small_web_image_publishes_with_an_upscale_note(work, long_side, flags, notes):
    shot = small_bottle(long_side)

    result, services = run(shot, work, remove_bg="truth")

    assert (result.quality_flags, result.quality_notes) == (flags, notes)
    assert result.isolated is (not flags)
    assert services.names() == ["photoroom"], "upscaling cannot be fixed by another provider"
    assert result.path and (result.width, result.height) == (800, 800)


# ---------------------------------------------------------------------------
# #6: no paid call for a result that can never publish (remove.bg configured in every case)
# ---------------------------------------------------------------------------

def _tagged_bottle():
    bottle = ps.make("bottle", (700, 900))
    tagged = ps.with_extras(bottle, "tagged", over=[ps.price_tag(bottle, share=0.04)])
    return tagged, ps.truth_provider(tagged, keep=(tagged.extras_over[0],))


def _cost_case(name):
    """(shot, run options, expected provider calls, isolated, flags, notes)"""
    if name == "second_object_no_box":
        shot, keeper = _tagged_bottle()
        return shot, dict(photoroom=keeper, box=False), ["photoroom"], False, [ip.FLAG_SECOND_OBJECT], []
    if name == "second_object_inside_the_box":
        shot, keeper = _tagged_bottle()
        region = ps.product_box(np.asarray(shot.scene().getchannel("A")))
        return (shot, dict(photoroom=keeper, gemini_box=ps.gemini_box_of(shot.alpha(), region=region)),
                ["photoroom"], False, [ip.FLAG_SECOND_OBJECT], [])
    if name == "second_object_in_source_alpha":
        shot, _keeper = _tagged_bottle()
        return ps.Shot("tagged_png", shot.scene(), None, box=shot.box), {}, [], False, [ip.FLAG_SECOND_OBJECT], []
    if name == "small_two_pack_with_box":
        # the reviewer's repro: was PhotoRoom crop, PhotoRoom full frame, remove.bg, then review
        shot = ps.make_pack("bottle", (400, 400), gap=8, fill=0.75)
        return shot, {}, ["photoroom"], True, [], [ip.NOTE_UPSCALED]
    if name == "too_small_source_with_box":
        shot = small_bottle(220)
        return shot, {}, ["photoroom"], False, [ip.FLAG_UPSCALED], []
    if name == "opaque_fill_on_the_box_crop":
        shot = ps.make("bottle", (600, 900), bg=(150, 160, 170))
        return (shot, dict(photoroom=ps.opaque_provider(shot)), ["photoroom", "remove_bg_api"], True, [], [])
    raise KeyError(name)


@pytest.mark.parametrize("name", ["second_object_no_box", "second_object_inside_the_box",
                                  "second_object_in_source_alpha", "small_two_pack_with_box",
                                  "too_small_source_with_box", "opaque_fill_on_the_box_crop"])
def test_paid_calls_per_case(work, name):
    shot, options, calls, isolated, flags, notes = _cost_case(name)

    result, services = run(shot, work, remove_bg="truth", **options)

    assert services.names() == calls
    assert (result.isolated, result.quality_flags, result.quality_notes) == (isolated, flags, notes)
    if name == "opaque_fill_on_the_box_crop":
        # the box was not the problem: remove.bg gets the same crop, no PhotoRoom full-frame retry
        assert services.calls[0][1] == services.calls[1][1] != shot.size
