"""pHash / BK-tree mechanics (image_dedup_bktree), carried over from the retired upgrades report.

v2 uses pHash only for pool de-duplication and for excluding images a reviewer rejected.
"""

from PIL import Image, ImageDraw

from image_dedup_bktree import PerceptualDeduplicationTree, calculate_hamming_distance, calculate_phash


def _red_box(fill):
    img = Image.new("RGB", (300, 300), (255, 255, 255))
    ImageDraw.Draw(img).rectangle([50, 50, 250, 250], fill=fill)
    return img


def test_phash_and_bktree_deduplication():
    hash1 = calculate_phash(_red_box((200, 0, 0)))
    hash2 = calculate_phash(_red_box((198, 0, 0)))       # tiny colour drift
    dist = calculate_hamming_distance(hash1, hash2)

    assert hash1 != 0 and hash2 != 0
    assert dist <= 3, f"expected a Hamming distance <= 3 for near-duplicates, got {dist}"

    tree = PerceptualDeduplicationTree()
    tree.insert_node(hash1, "img_001", {"name": "Red Box Original"})
    dups = tree.query_duplicates(hash2, tolerance_threshold=5)
    assert len(dups) >= 1
    assert dups[0]["image_id"] == "img_001"


def test_bktree_does_not_match_a_different_image():
    boxed = calculate_phash(_red_box((200, 0, 0)))
    img = Image.new("RGB", (300, 300), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    for x in range(0, 300, 20):
        draw.rectangle([x, 0, x + 9, 300], fill=(0, 0, 0))
    stripes = calculate_phash(img)
    assert calculate_hamming_distance(boxed, stripes) > 6

    tree = PerceptualDeduplicationTree()
    tree.insert_node(boxed, "img_001", {})
    assert tree.query_duplicates(stripes, tolerance_threshold=5) == []
