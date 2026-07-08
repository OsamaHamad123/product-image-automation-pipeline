# verify_upgrades.py
import os
import time
import numpy as np
from PIL import Image, ImageDraw
import cv2

def test_micro_classifier():
    print("\n--- 1. Testing MicroClassifierEngine ---")
    from image_quality_gatekeeper import MicroClassifierEngine
    
    # Create a temporary config to test the engine loader
    config_path = "calibrated_gate_config.json"
    dummy_created = False
    if not os.path.exists(config_path):
        import json
        dummy_config = {
            "weights": [1.2, 0.8, 1.5, 2.0, 1.1],
            "intercept": -0.5,
            "mean": [5.0, 500.0, 0.80, 0.95, 0.85],
            "scale": [1.0, 100.0, 0.10, 0.05, 0.10]
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(dummy_config, f, indent=4)
        dummy_created = True
        print("Generated a temporary calibrated_gate_config.json for validation testing.")
        
    engine = MicroClassifierEngine(config_path)
    assert engine.is_loaded, "Failed to load active learning classifier!"
    print("✅ MicroClassifierEngine loaded weights successfully.")
    
    # Test inference math
    features = np.array([6.5, 600.0, 0.82, 0.98, 0.90], dtype=np.float64)
    prob = engine.evaluate_probability(features)
    decision = engine.make_decision(features)
    print(f"Features: {features}")
    print(f"Sigmoid Probability: {prob:.4f}")
    print(f"Model Decision: {decision}")
    
    if dummy_created:
        try:
            os.remove(config_path)
            print("Removed temporary calibrated_gate_config.json.")
        except Exception:
            pass

def test_grabcut_floodfill():
    print("\n--- 2. Testing BoundaryComplianceSegmenter ---")
    from image_quality_gatekeeper import BoundaryComplianceSegmenter
    
    # Create a dummy image: white background with blue square in the center
    img = Image.new("RGB", (300, 300), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([50, 50, 250, 250], fill="blue")
    
    img_cv = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    
    segmenter = BoundaryComplianceSegmenter()
    binary_mask, segmented_img = segmenter.segment_foreground(img_cv)
    report = segmenter.verify_background_purity(img_cv, binary_mask)
    
    print(f"Segmented Foreground Shape: {segmented_img.shape}")
    print(f"Background Purity Report: {report}")
    assert report["compliant"], "Background compliance failed on a clean white background!"
    print("✅ BoundaryComplianceSegmenter GrabCut and FloodFill executed and passed.")

def test_async_sheets_queue():
    print("\n--- 3. Testing Async Sheets Queue and SQLite WAL Queue ---")
    import google_sheets
    import config
    import sqlite3
    
    db_path = "local_cache.db"
    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE IF EXISTS sheet_updates")
    conn.commit()
    conn.close()
    
    google_sheets.init_async_queue("credentials.json", config.SPREADSHEET_NAME_OR_URL, sync_interval=1)
    
    google_sheets.update_image_link(None, 9999, 5, "https://example.com/test_image.jpg")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT row_number, col_index, value, sync_status FROM sheet_updates")
    row = cursor.fetchone()
    print(f"Queued Row in SQLite: {row}")
    assert row is not None, "Failed to queue row in SQLite database!"
    assert row[0] == 9999, "Incorrect row number queued!"
    assert row[2] == "https://example.com/test_image.jpg", "Incorrect value queued!"
    print("✅ SQLite database WAL transaction queued correctly.")
    
    google_sheets.stop_async_queue()
    print("✅ Async Sheets Queue stopped and cleaned up.")

if __name__ == "__main__":
    try:
        test_grabcut_floodfill()
        test_micro_classifier()
        test_async_sheets_queue()
        print("\n🎉 ALL UPGRADES VERIFIED SUCCESSFULLY!")
    except AssertionError as e:
        print(f"\n❌ VERIFICATION FAILED: {e}")
