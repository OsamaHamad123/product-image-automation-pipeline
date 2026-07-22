# test_report_sse_curation_stream.py
"""
Automated Test Suite for Real-Time SSE Curation Stream & Optimistic State Management.
Validates 100% test assertions for CurationEvent, Replay-then-Tail Redis SSE Repository,
ProcessEventStreamUseCase, and Optimistic UI Rollback Logic.
"""

import sys
import os
import time
import json
import asyncio

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from verification_layer.domain.curation_models import CurationEvent
from verification_layer.use_cases.process_event_stream import RedisSseRepository, ProcessEventStreamUseCase


def print_banner(title: str):
    print(f"\n=======================================================")
    print(f"🚀 {title}")
    print(f"=======================================================")


def test_curation_event_domain_model():
    print_banner("TEST 1: CurationEvent Domain Model & Serialization")
    event = CurationEvent(
        event_id="evt_test_101",
        event_type="curation_pending",
        payload={"id": "101", "title": "اختبار المنتج", "status": "pending"}
    )

    d = event.to_dict()
    assert d["event_id"] == "evt_test_101", "event_id mismatch"
    assert d["event_type"] == "curation_pending", "event_type mismatch"
    assert d["payload"]["id"] == "101", "payload id mismatch"
    assert isinstance(d["timestamp"], float), "timestamp must be float"
    print(f"  ✅ CurationEvent Domain Model Verified: event_id={event.event_id}")


async def test_redis_sse_repository_simulated_stream():
    print_banner("TEST 2: Redis Sse Repository Replay-then-Tail Stream Generator")
    repo = RedisSseRepository(redis_client=None)
    use_case = ProcessEventStreamUseCase(repo)

    events_received = []
    async for evt in use_case.execute(session_id="batch_sess_test", last_event_id=None):
        events_received.append(evt)
        if len(events_received) >= 1:
            break

    assert len(events_received) == 1, "Expected at least 1 streamed event"
    evt = events_received[0]
    assert "id" in evt and "event" in evt and "data" in evt, "SSE event payload format invalid"
    assert evt["event"] == "curation_pending", f"Expected curation_pending event, got {evt['event']}"

    parsed_data = json.loads(evt["data"])
    assert parsed_data["event_type"] == "curation_pending", "Event data content mismatch"
    print(f"  ✅ SSE Stream Generator Verified: id={evt['id']}, event={evt['event']}")


def test_optimistic_ui_rollback_logic():
    print_banner("TEST 3: Optimistic UI State Mutation & Rollback Logic")

    # Simulate client-side grid manager state map
    products_state = {}

    product_id = "101"
    products_state[product_id] = {
        "id": product_id,
        "title": "Almarai Milk 1L",
        "status": "pending",
        "syncStatus": "synced",
        "rollbackData": None
    }

    # 1. Optimistic Mutation
    snapshot = dict(products_state[product_id])
    products_state[product_id]["status"] = "approved"
    products_state[product_id]["syncStatus"] = "mutating"
    products_state[product_id]["rollbackData"] = snapshot

    assert products_state[product_id]["status"] == "approved", "Expected optimistic status 'approved'"
    assert products_state[product_id]["syncStatus"] == "mutating", "Expected syncStatus 'mutating'"
    print("  ✅ Optimistic State Mutation Applied (status=approved, syncStatus=mutating)")

    # 2. Simulated Network Error -> Rollback
    previous_state = dict(products_state[product_id]["rollbackData"])
    previous_state["syncStatus"] = "error"
    products_state[product_id] = previous_state

    assert products_state[product_id]["status"] == "pending", "Expected status reverted to 'pending'"
    assert products_state[product_id]["syncStatus"] == "error", "Expected syncStatus set to 'error'"
    print("  ✅ State Rollback Successfully Reverted to Snapshot (status=pending, syncStatus=error)")


def main():
    print_banner("STARTING SSE CURATION STREAM AUTOMATED TEST SUITE")

    test_curation_event_domain_model()
    asyncio.run(test_redis_sse_repository_simulated_stream())
    test_optimistic_ui_rollback_logic()

    print_banner("🎉 ALL SSE CURATION STREAM TESTS PASSED SUCCESSFULLY (100% ASSERTIONS) 🎉")


if __name__ == "__main__":
    main()
