"""Sentinel — the J4 risk tripwire. Two layers:
  1. Deterministic keyword/regex on the INPUT transcription (fires while the
     prospect is still speaking; <1ms).
  2. Fast classifier (gemini-3.5-flash-lite) for nuanced cases (async, ~sub-2.5s).
On a trip: the room interrupts the Companion, injects the scripted safe line,
and logs an audit event. Every check is telemetry-spanned.
"""
import json
import re
import time

import config
from telemetry import set_genai_attrs, trace

try:
    from google import genai
    from google.genai import types
    client = genai.Client(vertexai=True, project=config.PROJECT_ID, location="global")
except Exception:
    genai = None  # type: ignore
    types = None  # type: ignore
    client = None

tracer = trace.get_tracer("paxtonprospect-bridge")

# Canonical prohibited-topics checklist (Policy Panel + command center render
# this; ids are Sentinel risk_type categories).
TOPICS = [
    {"id": "commitment", "label": "pricing commitments"},
    {"id": "credit", "label": "credit approval"},
    {"id": "legal", "label": "legal advice"},
    {"id": "sanctions", "label": "sanctions"},
    {"id": "kyc", "label": "KYC commitments"},
    {"id": "account", "label": "account opening guarantees"},
    {"id": "binding", "label": "binding product claims"},
    {"id": "regulatory", "label": "regulatory interpretations"},
]

_ASK = r"(can|could|will|would|do)\s+(you|we)\s+"
_PATTERNS = [
    (_ASK + r"(approve|pre[- ]?approve|greenlight)\s+.{0,30}\b(credit|loan|facilit|line)\b", "credit"),
    (r"\b(guarantee|promise)\s+.{0,30}\b(credit|loan|facilit)(y|ies)?\b", "credit"),
    (_ASK + r"(confirm|clear|certify)\s+.{0,40}\b(sanctions?|ofac|embargo(es)?)\b", "sanctions"),
    (r"\b(exempt|waive|bypass|skip)\s+.{0,25}\b(sanctions?|ofac)\b", "sanctions"),
    (_ASK + r"(waive|skip|bypass|fast[- ]?track|expedite)\s+.{0,25}\bkyc\b", "kyc"),
    (r"\b(commit|guarantee|promise)\s+.{0,30}\bkyc\b.{0,25}\b(timeline|days?|weeks?|approval)\b", "kyc"),
    (r"\b(guarantee|promise)\s+.{0,30}\baccount\s+(opening|open)\b", "account"),
    (_ASK + r"guarantee\s+.{0,25}\b(the\s+)?account\b", "account"),
    (r"\b(binding|contractual(ly)?)\s+.{0,30}\b(claim|offer|commitment|terms?)\b.{0,25}\b(product|service|solution)\b", "binding"),
    (_ASK + r"(make|give)\s+.{0,20}\b(that|this|it)\s+binding\b", "binding"),
    (_ASK + r"interpret\s+.{0,40}\b(regulation|rule|statute|requirement)\b", "regulatory"),
    (r"\b(guarantee|promise|lock)\s+.{0,30}\b(yield|apy|interest rate|deposit rate|return)\b", "commitment"),
    (_ASK + r"(guarantee|promise)\s+.{0,25}\b(yield|apy|returns?)\b", "commitment"),
    (_ASK + r"(guarantee|promise|assure|commit to|lock in)", "commitment"),
    (r"\bguarantee\s+(me|us|the|that|this)\b", "commitment"),
    (r"\b(promise|guarantee)\s+.{0,30}\b(rate|price|pricing|timeline|settlement|fees?)\b", "commitment"),
    (_ASK + r"confirm\s+.{0,40}\b(compl(y|ies|iant|iance)|regulat|banxico|fincen|sec\b|occ\b)", "regulatory"),
    (r"\bconfirm\s+(for|to)\s+(my|our|the)\s+board\b", "regulatory"),
    (r"\b(give|need|want)\s+.{0,20}\blegal advice\b", "legal"),
    (r"\blegal advice on\b", "legal"),
    (r"\bwrite\s+.{0,20}\b(in|into)\s+the\s+contract\b", "commitment"),
    (r"\b(locked?|lock)[- ]?in\s+.{0,25}\b(rate|price|pricing)\b.{0,20}\bguarantee", "commitment"),
]
SAFE_LINE = ("That's an important question, and it's exactly the kind of thing a Paxton Bank "
             "specialist should confirm for you directly rather than me. Let me bring your "
             "banker in on this.")

_CLASSIFIER_SYS = (
    "You are a compliance tripwire for a bank's AI sales companion. Trip ONLY when the "
    "prospect explicitly ASKS the companion to guarantee/commit/promise something "
    "(pricing, credit approval, account opening, KYC timelines), to make a binding "
    "product claim, to confirm regulatory/sanctions/compliance status, to interpret a "
    "regulation, or for legal advice. Merely DISCUSSING compliance, regulators, "
    "sanctions, KYC, or contracts as topics is NOT a trip. Answer strictly as JSON: "
    "{\"trip\": bool, \"risk_type\": \"commitment|credit|legal|sanctions|kyc|account|"
    "binding|regulatory|none\", \"reason\": str}.")


def regex_check(text: str) -> dict:
    """Deterministic layer. Returns {'trip', 'risk_type', 'phrase'} in ~microseconds."""
    with tracer.start_as_current_span("sentinel.regex") as span:
        low = text.lower()
        for pat, rtype in _PATTERNS:
            m = re.search(pat, low)
            if m:
                span.set_attribute("sentinel.trip", True)
                span.set_attribute("sentinel.risk_type", rtype)
                span.set_attribute("sentinel.phrase", m.group(0))
                span.set_attribute("sentinel.layer", "regex")
                return {
                    "trip": True,
                    "risk_type": rtype,
                    "phrase": m.group(0),
                    "layer": "regex",
                    "regulation": "FINRA Rule 2210 / OCC Bulletin 2023-17",
                }
        span.set_attribute("sentinel.trip", False)
        return {"trip": False, "risk_type": "none"}


async def classifier_check(text: str) -> dict:
    """Nuanced layer (async). Use when regex is clean but caution warranted."""
    t0 = time.monotonic()
    with tracer.start_as_current_span("sentinel.classifier") as span:
        set_genai_attrs(span, model=config.MODEL_FAST, system=_CLASSIFIER_SYS,
                        input_messages=text)
        try:
            if client is None or types is None:
                return regex_check(text)
            resp = await client.aio.models.generate_content(
                model=config.MODEL_FAST,
                contents=text,
                config=types.GenerateContentConfig(
                    system_instruction=_CLASSIFIER_SYS,
                    response_mime_type="application/json",
                    temperature=0.0,
                    max_output_tokens=120,
                ),
            )
            out = json.loads(resp.text)
            set_genai_attrs(span, output_messages=resp.text)
            span.set_attribute("sentinel.trip", bool(out.get("trip")))
            span.set_attribute("sentinel.risk_type", out.get("risk_type", "none"))
            span.set_attribute("sentinel.layer", "classifier")
            span.set_attribute("sentinel.latency_ms", round((time.monotonic() - t0) * 1000, 1))
            return {**out, "layer": "classifier"}
        except Exception as e:
            span.set_attribute("sentinel.error", str(e)[:120])
            return {"trip": False, "error": str(e)}
