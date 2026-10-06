"""False-positive corpus for the cutout quality gate (tests/packshot_synth.py).

Good packshots at 500-1200 px (bottles, cans, printed cartons, a jar, a handle bottle with a hole, a juice box with
a straw, a white bottle, a wide box, transparent printed carton PNGs, touching and gapped two-packs) must publish
clean with the expected number of paid calls: 1 for an opaque photo (PhotoRoom), 0 for a clean transparent PNG
(a white printed carton PNG costs 1: one provider confirms it is not a photo card). remove.bg is configured, so any
needless fallback shows up as an extra call. Damaged ones must be flagged.
"""

import functools

import numpy as np
import pytest

import config
import image_processor as ip
import packshot_synth as ps


DAMAGED = ["kept_card_no_box", "kept_card_with_box", "kept_offset_shadow", "baked_shadow_png", "watermark_letters",
           "price_tag", "opaque_noop", "straw_cut_by_box"]


@functools.lru_cache(maxsize=None)
def good():
    return ps.good_corpus()


@functools.lru_cache(maxsize=None)
def damaged():
    return ps.damaged_corpus()


@pytest.mark.parametrize("set_name", ["opaque", "transparent", "two_packs"])
def test_good_packshots_publish_clean_with_the_expected_paid_calls(tmp_path, set_name):
    problems = []
    for shot, options in good()[set_name]:
        assert 500 <= min(shot.size) and max(shot.size) <= 1200
        expected = options.get("expect_paid", 1)
        result, services = ps.run_shot(ip, config, shot, tmp_path, remove_bg="truth")
        if not result.isolated or result.quality_flags or services.paid != expected:
            problems.append((shot.name, result.provider, result.quality_flags, services.names()))
            continue
        # published as the product itself: centred and filling the 88% box on its long side (of the adaptive canvas)
        x0, y0, x1, y1 = ps.ink_box(ps.canvas_array(result))
        side = result.width
        if (result.height != side or side < 800 or max(x1 - x0, y1 - y0) < side * 0.875
                or abs((x0 + x1) / 2 - side / 2) > 2 or abs((y0 + y1) / 2 - side / 2) > 2):
            problems.append((shot.name, "canvas", (x0, y0, x1, y1)))
    assert problems == []


@pytest.mark.parametrize("name", DAMAGED)
def test_damaged_packshots_are_flagged(tmp_path, name):
    shot, options, (how, flag) = damaged()[name]

    result, services = ps.run_shot(ip, config, shot, tmp_path, **options)

    if how == "flag":
        assert result.path and result.isolated is False
        assert flag in result.quality_flags
    else:
        # the crop attempt was flagged, so the full frame was tried and the whole product published
        assert [size for _name, size, _rect in services.calls][-1] == shot.size and services.paid == 2
        assert result.isolated is True
        whole = ps.ink_box(np.asarray(ip.compose_on_white_canvas(shot.product, (result.width, result.height))))
        assert ps.ink_box(ps.canvas_array(result)) == whole


def test_the_damaged_set_is_complete():
    assert sorted(damaged()) == sorted(DAMAGED)
