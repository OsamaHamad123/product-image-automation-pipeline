# verification_layer/domain/curation_models.py
"""
Real-Time SSE Curation Stream Domain Models.
Dataclasses representing curation events, payloads, and session streams.
"""

import time
from dataclasses import dataclass, field
from typing import Dict, Any, Optional


@dataclass
class CurationEvent:
    """Represents a domain event directed to human curation grid."""
    event_id: str
    event_type: str
    payload: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "payload": self.payload,
            "timestamp": self.timestamp
        }
