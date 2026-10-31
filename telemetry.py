"""Telemetry spine: OpenTelemetry distributed tracing, structured JSON logging,
Intent vs. Outcome capture, and active PII redaction for Paxton Bank.

Every service calls `setup_telemetry(service_name)` once at startup, then uses
tracer/span helpers and `glog()`. Correlation attributes follow OTel GenAI
semantic conventions (`gen_ai.*`), and all log/span payloads pass through
`pii_redactor` before export or stdout ingestion.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
import os
from typing import Any

try:
    import google.auth
    import google.auth.transport.grpc
    import google.auth.transport.requests
    import grpc
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    _HAS_OTEL = True
except ImportError:
    _HAS_OTEL = False

    class _DummySpanContext:
        is_valid = True
        trace_id = 0x4BF92F3577B34DA6A3CE929D0E0E4736
        span_id = 0x00F067AA0BA902B7

    class _DummySpan:
        def __init__(self):
            self.attributes = {}

        def set_attribute(self, key: str, value: Any) -> None:
            self.attributes[key] = value

        def get_span_context(self) -> _DummySpanContext:
            return _DummySpanContext()

    class _DummyTracer:
        @contextmanager
        def start_as_current_span(self, name: str):
            yield _DummySpan()

    class _DummyTraceModule:
        def get_tracer(self, name: str) -> _DummyTracer:
            return _DummyTracer()

        def set_tracer_provider(self, provider: Any) -> None:
            pass

        def get_current_span(self) -> _DummySpan:
            return _DummySpan()

    trace = _DummyTraceModule()  # type: ignore

from pii_redactor import redact_data, redact_pii

_TELEMETRY_ENDPOINT = "telemetry.googleapis.com:443"
MAX_ATTR_LEN = 60_000  # stay under the 64 KiB Telemetry API attribute cap
_PROJECT = os.environ.get("CP_PROJECT", "kairos-504105")

# Configure Python structured logger
_logger = logging.getLogger("paxtonprospect.telemetry")
if not _logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(_handler)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False


def setup_telemetry(service_name: str = "paxtonprospect-bridge"):
    """Initialize OpenTelemetry TracerProvider and OTLP gRPC exporter to Cloud Trace."""
    if not _HAS_OTEL:
        return trace.get_tracer(service_name)
    project = os.environ.get("CP_PROJECT", "kairos-504105")
    provider = TracerProvider(resource=Resource.create({
        "service.name": service_name,
        "service.version": os.environ.get("K_REVISION", "local"),
        "gcp.project_id": project,
    }))
    try:
        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"])
        request = google.auth.transport.requests.Request()
        channel_creds = grpc.composite_channel_credentials(
            grpc.ssl_channel_credentials(),
            grpc.metadata_call_credentials(
                google.auth.transport.grpc.AuthMetadataPlugin(creds, request)),
        )
        exporter = OTLPSpanExporter(
            endpoint=_TELEMETRY_ENDPOINT,
            credentials=channel_creds,
            headers=(("x-goog-user-project", project),),
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
    except Exception:
        # Local/offline test environment without GCP credentials: keep TracerProvider active
        pass
    trace.set_tracer_provider(provider)
    return trace.get_tracer(service_name)


tracer = setup_telemetry("paxtonprospect-bridge")


def clip(value: Any) -> str:
    """Serialize, PII-redact, and bound a span attribute or log field."""
    scrubbed = redact_data(value)
    s = scrubbed if isinstance(scrubbed, str) else json.dumps(scrubbed, default=str)
    s = redact_pii(s)
    return s if len(s) <= MAX_ATTR_LEN else s[:MAX_ATTR_LEN] + "...[clipped]"


def set_genai_attrs(span, *, model=None, system=None, input_messages=None,
                    output_messages=None, conversation_id=None, usage=None):
    """Full-content PII-scrubbed GenAI capture per OTel GenAI semantic conventions."""
    if model:
        span.set_attribute("gen_ai.request.model", str(model))
    if system is not None:
        span.set_attribute("gen_ai.system_instructions", clip(system))
    if input_messages is not None:
        span.set_attribute("gen_ai.input.messages", clip(input_messages))
    if output_messages is not None:
        span.set_attribute("gen_ai.output.messages", clip(output_messages))
    if conversation_id:
        span.set_attribute("gen_ai.conversation.id", str(conversation_id))
    if usage:
        for k, v in usage.items():
            span.set_attribute(f"gen_ai.usage.{k}", v)


def glog(message: str, severity: str = "INFO", log_type: str = "app", **fields):
    """Emit a PII-redacted, trace-correlated structured JSON log entry to Cloud Logging."""
    entry: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "severity": severity,
        "message": redact_pii(str(message)),
        "log_type": log_type,
        "service": "paxtonprospect-bridge",
    }
    for k, v in fields.items():
        if v is not None:
            scrubbed = redact_data(v)
            entry[k] = (
                scrubbed
                if isinstance(scrubbed, (str, int, float, bool, dict, list))
                else json.dumps(scrubbed, default=str)[:2000]
            )
    try:
        ctx = trace.get_current_span().get_span_context()
        if ctx and ctx.is_valid:
            entry["logging.googleapis.com/trace"] = (
                f"projects/{_PROJECT}/traces/{format(ctx.trace_id, '032x')}")
            entry["logging.googleapis.com/spanId"] = format(ctx.span_id, "016x")
    except Exception:
        pass
    _logger.info(json.dumps(entry, default=str))
    return entry


def log_intent_and_outcome(
    *,
    agent_name: str,
    action: str,
    phase: str,
    room_id: str = "",
    intent_payload: Any = None,
    outcome_payload: Any = None,
    status: str = "ok",
    latency_ms: float | None = None,
) -> dict[str, Any]:
    """Explicitly capture both the agent's intended action BEFORE execution and
    the actual outcome AFTER execution in trace-correlated structured JSON logs.

    Args:
        agent_name: Name of the agent or tool executing (e.g., 'Pulse', 'enrich_prospect').
        action: Specific operation being performed.
        phase: 'INTENT' (pre-execution) or 'OUTCOME' (post-execution).
        room_id: Active meeting room/session identifier.
        intent_payload: Planned input parameters or prompt before execution.
        outcome_payload: Actual returned result or error after execution.
        status: Execution status ('planned', 'ok', 'error', 'blocked_by_hitl').
        latency_ms: Execution duration in milliseconds (for OUTCOME phase).
    """
    return glog(
        f"agent_{phase.lower()}: {agent_name}.{action}",
        severity="INFO" if status in ("ok", "planned") else "WARNING",
        log_type="intent_outcome",
        agent=agent_name,
        action=action,
        phase=phase.upper(),
        room_id=room_id,
        status=status,
        latency_ms=round(latency_ms, 2) if latency_ms is not None else None,
        intent=redact_data(intent_payload) if intent_payload is not None else None,
        outcome=redact_data(outcome_payload) if outcome_payload is not None else None,
    )
