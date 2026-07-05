# verify_image_search.py
import sys
import os
import config
from image_search import search_best_product_image

print("Testing updated search_best_product_image with DDG integration, lower minimum size limits, and fixed is_image_ecommerce_style function...")
print(f"BG_REMOVAL_METHOD configured as: {config.BG_REMOVAL_METHOD}")

test_cases = [
    {"name": "Long Life Organic Milk 180ml", "brand": "Meliha", "query": "Long Life Organic Milk 180ml Meliha"},
    {"name": "Chocolate Milk 180ml", "brand": "Meliha", "query": "Chocolate Milk 180ml Meliha"},
    {"name": "Drinking Water 500ml", "brand": "Mai Dubai", "query": "Drinking Water 500ml Mai Dubai"},
    {"name": "Chakki Fresh Atta 5kg", "brand": "Saba Sanabel", "query": "Chakki Fresh Atta 5kg Saba Sanabel"}
]

for tc in test_cases:
    print(f"\n--- Testing: [{tc['name']}] (Brand: {tc['brand']}) ---")
    trace = {}
    img = search_best_product_image(tc['query'], tc['name'], tc['brand'], trace=trace)
    if img:
        print(f"✅ SUCCESS: URL={img['url']} | Title={img.get('title', '')} | Size={img.get('width',0)}x{img.get('height',0)}")
    else:
        print("❌ FAILED: No specific image found for this product category and brand.")
    
    # Print trace details for debugging
    if 'steps' in trace:
        print("\n🔍 Trace Details:")
        for step in trace['steps']:
            print(f"  Step Query: '{step['query']}' | Results count: {step['results_count']}")
            if 'candidates' in step and step['candidates']:
                for cand in step['candidates']:
                    status_symbol = "✅" if cand['status'] == 'accepted' else "❌"
                    print(f"    - {status_symbol} Candidate: {cand['title'][:60]} | Status: {cand['status']}")
                    print(f"      URL: {cand['url']}")
                    if cand.get('reasons'):
                        print(f"      Reasons: {', '.join(cand['reasons'])}")

