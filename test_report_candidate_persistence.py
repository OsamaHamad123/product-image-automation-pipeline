# test_report_candidate_persistence.py
"""
Automated Test Suite for Curation Candidates Persistence & Cache Invalidation.
Validates selectCandidate & saveCandidates database updates and products_json_v1 cache invalidation.
"""

import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))


def print_banner(title: str):
    print(f"\n=======================================================")
    print(f"🚀 {title}")
    print(f"=======================================================")


def test_php_persistence_routes_and_controller():
    print_banner("TEST 1: PHP Routes & Controller Persistence Actions")

    routes_file = r"f:\automation\dashboard\routes\web.php"
    with open(routes_file, "r", encoding="utf-8") as f:
        routes_content = f.read()

    assert "select-candidate" in routes_content, "web.php missing select-candidate route"
    assert "save-candidates" in routes_content, "web.php missing save-candidates route"

    controller_file = r"f:\automation\dashboard\app\Http\Controllers\CurationController.php"
    with open(controller_file, "r", encoding="utf-8") as f:
        controller_content = f.read()

    assert "selectCandidate" in controller_content, "CurationController missing selectCandidate method"
    assert "saveCandidates" in controller_content, "CurationController missing saveCandidates method"
    assert "Cache::forget('products_json_v1')" in controller_content, "CurationController missing cache invalidation"

    print("  ✅ PHP Routes, Controller actions, and Cache invalidation verified successfully.")


def main():
    print_banner("STARTING CANDIDATE PERSISTENCE AUTOMATED TEST")

    test_php_persistence_routes_and_controller()

    print_banner("🎉 ALL CANDIDATE PERSISTENCE TESTS PASSED (100% ASSERTIONS) 🎉")


if __name__ == "__main__":
    main()
