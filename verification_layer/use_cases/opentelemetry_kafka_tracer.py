# verification_layer/use_cases/opentelemetry_kafka_tracer.py
import uuid
import time
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Tuple


@dataclass
class W3CTraceContext:
    trace_id: str
    span_id: str
    trace_parent_header: str
    conversation_id: str
    baggage: Dict[str, str] = field(default_factory=dict)


class OpenTelemetryW3CTracer:
    """
    تتبع مسار المعالجة الموزع ونقل سياق البيانات غير المتزامن عبر OpenTelemetry و W3C Trace Context
    - W3C Traceparent: 00-{trace_id}-{span_id}-01
    - Latency_E2E = T_completion - T_production
    """

    @classmethod
    def generate_w3c_trace_context(
        cls, conversation_id: Optional[str] = None, baggage: Optional[Dict[str, str]] = None
    ) -> W3CTraceContext:
        trace_id = uuid.uuid4().hex
        span_id = uuid.uuid4().hex[:16]
        cid = conversation_id or f"tx-global-{uuid.uuid4().hex[:8]}"

        traceparent = f"00-{trace_id}-{span_id}-01"

        return W3CTraceContext(
            trace_id=trace_id,
            span_id=span_id,
            trace_parent_header=traceparent,
            conversation_id=cid,
            baggage=baggage or {"environment": "production", "isolation_level": "standard"},
        )

    @classmethod
    def inject_kafka_headers(cls, context: W3CTraceContext, destination_topic: str) -> Dict[str, str]:
        headers = {
            "traceparent": context.trace_parent_header,
            "messaging.system": "kafka",
            "messaging.operation.type": "send",
            "messaging.destination.name": destination_topic,
            "messaging.message.conversation_id": context.conversation_id,
            "production_timestamp": str(time.time()),
        }
        for k, v in context.baggage.items():
            headers[f"baggage.{k}"] = v
        return headers

    @classmethod
    def calculate_end_to_end_latency(cls, production_timestamp: float, completion_timestamp: Optional[float] = None) -> float:
        """
        Latency_E2E = T_completion - T_production
        """
        t_comp = completion_timestamp or time.time()
        latency = t_comp - production_timestamp
        return float(round(max(0.0, latency), 4))
