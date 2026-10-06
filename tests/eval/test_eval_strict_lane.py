"""AUTO_PUBLISH_STRICT_LANE on the golden set: once the lane's reviews prove it (readiness 'ready'), the lane publishes
exactly what an allow-list of every brand ('*') publishes (the eval's auto-publish numbers measure it); with the lane
off, or on (the default) but not proven yet, and no brand listed nothing is auto-published (the lane is only
recorded). A sample of the golden set keeps this quick; the eval report (scripts/eval_report.py) runs the whole set
with the default settings, where no readiness reader is wired, so the lane publishes nothing there.
"""

from __future__ import annotations

import contextlib
import os
from unittest import mock

import pytest

import runners  # noqa: E402

pytestmark = pytest.mark.eval


def _with(values):
    original = runners._v2_settings

    @contextlib.contextmanager
    def patched(auto_publish):
        with original(auto_publish):
            env = {k: ("true" if v is True else "false" if v is False else ",".join(v)) for k, v in values.items()}
            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.dict(os.environ, env))
                from catalog_match import settings
                cfg = getattr(settings, "_config", None)
                if cfg is not None:
                    for key, value in values.items():
                        stack.enter_context(mock.patch.object(cfg, key, value, create=True))
                yield
    return patched


def _run(golden, cassette, values=None, readiness=None):
    from catalog_match import decide

    skus = golden["skus"][::4]
    decide.set_strict_lane_reader((lambda: readiness) if readiness else None)
    try:
        with runners.network_blocked():
            if values is None:
                return {s["id"]: runners.run_v2(s, cassette) for s in skus}
            with mock.patch.object(runners, "_v2_settings", _with(values)):
                return {s["id"]: runners.run_v2(s, cassette) for s in skus}
    finally:
        decide.set_strict_lane_reader(None)


def test_the_strict_lane_publishes_what_every_brand_listed_would(golden, cassette):
    star = _run(golden, cassette)                                         # the eval's own settings: '*'
    lane = _run(golden, cassette, {"AUTO_PUBLISH_BRANDS": [], "AUTO_PUBLISH_STRICT_LANE": True}, readiness="ready")
    shadow = _run(golden, cassette, {"AUTO_PUBLISH_BRANDS": [], "AUTO_PUBLISH_STRICT_LANE": False}, readiness="ready")
    waiting = _run(golden, cassette, {"AUTO_PUBLISH_BRANDS": [], "AUTO_PUBLISH_STRICT_LANE": True},
                   readiness="needs_reviews")
    unwired = _run(golden, cassette, {"AUTO_PUBLISH_BRANDS": [], "AUTO_PUBLISH_STRICT_LANE": True})
    assert sum(o.decision == "AUTO_PUBLISH" for o in star.values()) >= 5
    for sku_id, ref in star.items():
        assert (lane[sku_id].decision, lane[sku_id].chosen_id) == (ref.decision, ref.chosen_id), sku_id
        expected = "REVIEW_PRESELECTED" if ref.decision == "AUTO_PUBLISH" else ref.decision
        for held in (shadow, waiting, unwired):
            assert (held[sku_id].decision, held[sku_id].chosen_id) == (expected, ref.chosen_id), sku_id
