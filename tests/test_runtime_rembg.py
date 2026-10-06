"""Local BiRefNet cutouts on a server (runtime package, item 4).

Each rembg.remove call on CPU takes 2-3 GB of memory. The worker runs WORKER_CONCURRENCY products at once, and a
PhotoRoom outage sends all of them to the local fallback together: REMBG_MAX_PARALLEL (default 1) inferences run at
once, the others wait their turn. rembg itself is a stub here: nothing is installed or downloaded.
"""

import io
import sys
import threading
import time
import types

import pytest
from PIL import Image


@pytest.fixture
def fake_rembg(monkeypatch):
    """A stub rembg module whose remove() records how many calls overlap."""
    seen = {"now": 0, "most": 0, "calls": 0}
    guard = threading.Lock()

    def remove(data, session=None):
        with guard:
            seen["now"] += 1
            seen["calls"] += 1
            seen["most"] = max(seen["most"], seen["now"])
        time.sleep(0.05)
        with guard:
            seen["now"] -= 1
        out = io.BytesIO()
        Image.new("RGBA", (40, 40), (200, 10, 10, 255)).save(out, format="PNG")
        return out.getvalue()

    module = types.ModuleType("rembg")
    module.remove = remove
    module.new_session = lambda model: object()
    monkeypatch.setitem(sys.modules, "rembg", module)
    import image_processor

    image_processor.reset_rembg_sessions(everything=True)
    yield image_processor, seen
    image_processor.reset_rembg_sessions(everything=True)


def _cut_at_once(image_processor, threads):
    img = Image.new("RGB", (40, 40), (255, 255, 255))
    results = []

    def one():
        results.append(image_processor._isolate_rembg(img))

    pool = [threading.Thread(target=one) for _ in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join(10)
    return results


def test_one_local_cutout_at_a_time_by_default(fake_rembg, monkeypatch):
    image_processor, seen = fake_rembg
    monkeypatch.delenv("REMBG_MAX_PARALLEL", raising=False)
    from catalog_match import settings

    assert settings.DEFAULTS["REMBG_MAX_PARALLEL"] == 1 and settings.rembg_max_parallel() == 1
    results = _cut_at_once(image_processor, 5)
    assert len(results) == 5 and all(cutout is not None and error is None for cutout, error in results)
    assert seen["calls"] == 5 and seen["most"] == 1


def test_the_setting_allows_more_and_is_read_again_at_the_next_run(fake_rembg, monkeypatch):
    image_processor, seen = fake_rembg
    monkeypatch.setenv("REMBG_MAX_PARALLEL", "2")
    image_processor.reset_rembg_sessions()          # the worker does this at the start of every run
    _cut_at_once(image_processor, 6)
    assert seen["most"] == 2


@pytest.mark.parametrize("value, expected", [("0", 1), ("-3", 1), ("8", 8), ("50", 8), ("abc", 1), (" 3 ", 3)])
def test_the_setting_is_clamped(monkeypatch, value, expected):
    from catalog_match import settings

    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.setenv("REMBG_MAX_PARALLEL", value)
    assert settings.rembg_max_parallel() == expected


def test_a_failing_inference_frees_its_slot(fake_rembg, monkeypatch):
    image_processor, seen = fake_rembg
    monkeypatch.delenv("REMBG_MAX_PARALLEL", raising=False)

    def broken(data, session=None):
        raise RuntimeError("out of memory")

    monkeypatch.setattr(sys.modules["rembg"], "remove", broken)
    img = Image.new("RGB", (40, 40), (255, 255, 255))
    assert image_processor._isolate_rembg(img) == (None, "rembg_failed")
    assert image_processor._isolate_rembg(img) == (None, "rembg_failed")     # not blocked by the first failure
