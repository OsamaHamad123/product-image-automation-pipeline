"""A fake embedder for the logic of catalog_match.embeddings (no onnxruntime, no model file).

ColourEmbedder reads the mean colour of a picture's non-white pixels and returns it centred on mid-grey as a unit
vector: two packs of the same colour point the same way (cosine 1), a red and a blue pack point apart (cosine about
-0.4). It is deterministic, instant and runs one 'inference' at a time like the real one, so the tests can count
calls and batch sizes.
"""

import threading

from catalog_match import embeddings


class ColourEmbedder:
    model_id = "fake:colour"
    dim = 3

    def __init__(self):
        self.calls = []                  # batch sizes, one entry per embed() call
        self._busy = threading.Lock()

    def vector(self, img):
        import numpy as np

        pixels = np.asarray(embeddings._on_white(img), dtype=np.float32).reshape(-1, 3)
        pack = pixels[pixels.min(axis=1) < embeddings.WHITE_CUTOFF]
        mean = (pack if len(pack) else pixels).mean(axis=0)
        return embeddings.unit(mean - 128.0)

    def embed(self, images):
        assert self._busy.acquire(blocking=False), "two inferences at once"
        try:
            images = list(images)
            self.calls.append(len(images))
            return [self.vector(img) if img is not None else None for img in images]
        finally:
            self._busy.release()


def packshot(colour, size=(400, 600), margin=60):
    """A white picture with one coloured pack in the middle (the shape every fixture packshot has)."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", size, (255, 255, 255))
    ImageDraw.Draw(img).rectangle((margin, margin, size[0] - margin, size[1] - margin), fill=colour)
    return img
