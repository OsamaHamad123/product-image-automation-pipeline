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
    assert ip.FLAG_OPAQUE_BACKDROP in result.quality_flags


def test_white_carton_on_an_opaque_photo_matches_the_gemini_box(work):
    # The provider's rectangle is where Gemini located the product: a carton, not a kept photo card.
    shot = ps.make("carton", (700, 900), fill=0.75, face=(245, 245, 242), logo=(40, 110, 200),
                   print_=(40, 110, 200), bg=ps.STUDIO)

    result, services = run(shot, work)

    assert (result.isolated, result.provider, result.quality_flags) == (True, "photoroom", [])
    assert services.paid == 1
