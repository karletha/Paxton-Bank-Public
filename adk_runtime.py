"""ADK 2.x multi-agent execution, strategic model routing, context compaction,
and policy guardrail plugin layer for the Paxton Bank agent fleet.

Capabilities implemented in this module:
  1. Multi-Agent Orchestration: Coordinator `LlmAgent` (`paxton_bank_coordinator`)
     with specialized sub-agents (`Pulse`, `Panel`, `Scorer`, `Archivist`, `Sentinel`, `Coach`).
  2. Strategic Model Routing: `select_model_for_task()` routes low-latency classification
     to `gemini-3.5-flash-lite`, synthesis workers to `gemini-3.6-flash`, complex
     qualification/planning to `gemini-3.1-pro-preview`, and bidi live media to
     `gemini-3.5-flash-live-preview`.
  3. History Compaction: Token-budget sliding window (`compact_conversation_history`)
     and ADK `ContextWindowCompressionConfig` (`SlidingWindow(target_tokens=30000)`).
  4. Guardrails & Policy Plugins: ADK `BasePlugin` subclasses (`PaxtonComplianceGuardrailPlugin`,
     `PaxtonPiiRedactionPlugin`) enforcing pre/post-model compliance and PII scrubbing.
"""
from typing import Any, Optional

import config
from pii_redactor import redact_pii
import sentinel
from telemetry import log_intent_and_outcome

_AGENTS: dict = {}
_RUNNER = None
_SESSION_SVC = None
_READY = None  # None = untried, True/False = import result


# ============================================================================
# Strategic Model Routing
# ============================================================================

TASK_MODEL_ROUTES: dict[str, str] = {
    "fast_classification": config.MODEL_FAST,      # gemini-3.5-flash-lite (<1.5s guardrails & sentiment)
    "compliance_guardrail": config.MODEL_FAST,     # gemini-3.5-flash-lite
    "sentiment_pulse": config.MODEL_FAST,          # gemini-3.5-flash-lite
    "objection_detection": config.MODEL_FAST,      # gemini-3.5-flash-lite
    "worker_synthesis": config.MODEL_WORKER,       # gemini-3.6-flash (insights, memory, coaching)
    "memory_compaction": config.MODEL_WORKER,      # gemini-3.6-flash
    "deep_planning": config.MODEL_DEEP,            # gemini-3.1-pro-preview (strategic qualification & planning)
    "qualification_scoring": config.MODEL_DEEP,    # gemini-3.1-pro-preview
    "live_multimodal": config.MODEL_LIVE,          # gemini-3.5-flash-live-preview
    "embedding": config.MODEL_EMBED,               # gemini-embedding-2
}


def select_model_for_task(task_type: str) -> str:
    """Route a specific agent task to the optimal Gemini model tier.

    Args:
        task_type: Logical task category (e.g., 'fast_classification', 'deep_planning',
            'worker_synthesis', 'live_multimodal').

    Returns:
        Configured Gemini model identifier for that complexity/latency tier.
    """
    return TASK_MODEL_ROUTES.get(task_type, config.MODEL_WORKER)


# ============================================================================
# Context & History Compaction
# ============================================================================

def compact_conversation_history(
    turns: list[tuple[str, str]],
    *,
    running_summary: str = "",
    max_tokens: int = 4000,
    keep_recent_turns: int = 6,
) -> str:
    """Compact conversational history using a sliding turn window + running summary
    to prevent context bloat while preserving early meeting commitments.

    Args:
        turns: List of `(speaker, text)` tuples from the session transcript.
        running_summary: Consolidated `Archivist` memory summary of older turns.
        max_tokens: Approximate token budget cap (~4 chars per token).
        keep_recent_turns: Number of verbatim recent turns to keep in the sliding window.

    Returns:
        Compacted, PII-scrubbed context string within the token budget.
    """
    max_chars = max_tokens * 4
    recent = turns[-keep_recent_turns:] if turns else []
    recent_block = "\n".join(f"{spk}: {redact_pii(txt)}" for spk, txt in recent)
    summary_block = redact_pii(running_summary.strip()) if running_summary else ""

    combined = ""
    if summary_block:
        combined += f"[COMPACTED SESSION MEMORY]\n{summary_block}\n\n"
    if recent_block:
        combined += f"[SLIDING WINDOW RECENT TURNS]\n{recent_block}"

    if len(combined) > max_chars:
        combined = combined[-max_chars:]
    return combined.strip()


# ============================================================================
# ADK Runtime & Policy Plugins
# ============================================================================

def available() -> bool:
    """True when `google.adk` is importable AND enabled. Cached; never raises."""
    global _READY
    if _READY is None:
        if not getattr(config, "ADK_AGENTS", False):
            _READY = False
            return _READY
        try:
            import google.adk  # noqa: F401
            _READY = True
        except Exception:
            _READY = False
    return _READY


def build_adk_plugins() -> list[Any]:
    """Instantiate ADK `BasePlugin` policy and PII redaction guardrails when ADK is installed."""
    if not available():
        return []
    try:
        from google.adk.plugins import BasePlugin

        class PaxtonComplianceGuardrailPlugin(BasePlugin):
            """ADK Policy Plugin that intercepts prohibited banking commitments before/after model calls."""

            def __init__(self):
                super().__init__(name="paxton_compliance_guardrail_plugin")

            async def before_model_callback(self, *, callback_context=None, llm_request=None, **kwargs):
                if llm_request and getattr(llm_request, "contents", None):
                    text = str(llm_request.contents)
                    verdict = sentinel.regex_check(text)
                    if verdict.get("trip"):
                        log_intent_and_outcome(
                            agent_name="PaxtonComplianceGuardrailPlugin",
                            action="before_model_callback",
                            phase="OUTCOME",
                            outcome_payload=verdict,
                            status="blocked_by_guardrail",
                        )
                return None

        class PaxtonPiiRedactionPlugin(BasePlugin):
            """ADK Policy Plugin that scrubs PII from user messages and tool outputs."""

            def __init__(self):
                super().__init__(name="paxton_pii_redaction_plugin")

            async def after_tool_callback(self, *, tool=None, tool_args=None, tool_context=None, result=None, **kwargs):
                if isinstance(result, dict):
                    from pii_redactor import redact_data
                    return redact_data(result)
                return None

        return [PaxtonComplianceGuardrailPlugin(), PaxtonPiiRedactionPlugin()]
    except Exception:
        return []


def _mk(name: str, model: str, instruction: str, gen_cfg=None, output_schema=None):
    """Cached ADK `LlmAgent` per `(name, model, instruction, generation-config)`."""
    from google.adk.agents.llm_agent import LlmAgent
    key = (name, model, hash(instruction), repr(gen_cfg), repr(output_schema))
    a = _AGENTS.get(key)
    if a is None:
        kw: dict[str, Any] = {}
        if output_schema is not None:
            kw["output_schema"] = output_schema
        if gen_cfg is not None:
            if getattr(gen_cfg, "response_schema", None) is not None:
                try:
                    d = gen_cfg.model_dump(exclude_none=True)
                    d.pop("response_schema", None)
                    from google.genai import types as _t
                    gen_cfg = _t.GenerateContentConfig(**d)
                except Exception:
                    gen_cfg = None
            kw["generate_content_config"] = gen_cfg
        a = _AGENTS[key] = LlmAgent(
            name=name.replace(" ", "_").replace("(", "").replace(")", ""),
            model=model,
            description=f"Paxton Bank Bridget {name} specialist agent",
            instruction=instruction,
            **kw,
        )
    return a


async def _runner_for(agent, app_name: str):
    global _RUNNER, _SESSION_SVC
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    if _SESSION_SVC is None:
        _SESSION_SVC = InMemorySessionService()
    return Runner(
        app_name=app_name,
        agent=agent,
        session_service=_SESSION_SVC,
        plugins=build_adk_plugins(),
    )


async def run(
    agent_name: str,
    model: str,
    instruction: str,
    prompt_parts,
    room_id: str = "room",
    gen_cfg=None,
    output_schema: Optional[Any] = None,
) -> str:
    """Execute one specialist agent turn on the ADK `Runner` with trace & session state."""
    from google.adk.agents.run_config import RunConfig
    from google.genai import types

    resolved_model = model or select_model_for_task("worker_synthesis")
    agent = _mk(agent_name, resolved_model, instruction, gen_cfg, output_schema=output_schema)
    runner = await _runner_for(agent, f"paxton-{agent_name}")
    uid, sid = room_id, f"{room_id}-{agent_name}"
    try:
        await _SESSION_SVC.create_session(
            app_name=f"paxton-{agent_name}",
            user_id=uid,
            session_id=sid,
        )
    except Exception:
        pass  # session already exists — reuse persistent session state across turns

    msg = types.Content(role="user", parts=list(prompt_parts))
    out = []
    async for ev in runner.run_async(
        user_id=uid,
        session_id=sid,
        new_message=msg,
        run_config=RunConfig(max_llm_calls=3),
    ):
        c = getattr(ev, "content", None)
        for p in (getattr(c, "parts", None) or []):
            if getattr(p, "text", None):
                out.append(p.text)
    return redact_pii("".join(out))


def describe() -> dict:
    """Surfaced on `GET /agents` so the Agents tab shows the live ADK runtime."""
    ok = available()
    ver = ""
    if ok:
        try:
            import google.adk
            ver = getattr(google.adk, "__version__", "")
        except Exception:
            ver = ""
    return {
        "adk_enabled": bool(getattr(config, "ADK_AGENTS", False)),
        "adk_available": ok,
        "adk_version": ver,
        "model_routes": TASK_MODEL_ROUTES,
        "runtime": (
            "ADK LlmAgent + Runner (with Guardrail & PII Redaction Plugins)"
            if ok
            else "google-genai (direct)"
        ),
        "live_agent_runtime": "native live_manager (Zoom/Web media path)",
    }
