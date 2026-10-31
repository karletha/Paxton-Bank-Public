"""Agent toolkit — Paxton Bank Companion and specialist worker tools.

Each tool is wired to a real microservice, Vertex AI Search datastore, or Cloud
Firestore collection, validated via strict Pydantic JSON schemas (`schemas.py`),
instrumented with Intent vs. Outcome structured logging (`telemetry.py`), and
protected by active PII redaction (`pii_redactor.py`).

Grounding rule: tools return verifiable citations; when nothing is found or an
upstream service fails, they return `{"found": False, "recovery_guidance": ...}`
so the LLM can recover gracefully without hallucinating or crashing.
"""
import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
from typing import Any

try:
    import google.auth
    import google.auth.transport.requests
    from google.cloud import firestore
    import httpx
except ImportError:
    google = None  # type: ignore
    firestore = None  # type: ignore
    httpx = None  # type: ignore

import config
from pii_redactor import redact_data, redact_pii
from schemas import (
    CollateralSearchInput,
    KycScreenInput,
    MarketSnapshotInput,
    ProspectEnrichmentInput,
    SpecialistLookupInput,
)
from telemetry import log_intent_and_outcome

_SVC = "https://paxtonprospect-{name}-229567506693.us-east4.run.app"
_DISCOVERY = (
    "https://discoveryengine.googleapis.com/v1/projects/{p}/locations/global/"
    "collections/default_collection/dataStores/{ds}/servingConfigs/"
    "default_search:search"
)

_fs = None
_SECRET_CACHE: dict[str, str] = {}


def fs():
    """Return a singleton Cloud Firestore client connected to `config.FIRESTORE_DB`."""
    global _fs
    if _fs is None:
        _fs = firestore.Client(database=config.FIRESTORE_DB)
    return _fs


def get_secret(secret_id: str, env_var: str = "") -> str:
    """Retrieve a secret from Google Cloud Secret Manager with safe ephemeral fallback.

    Args:
        secret_id: Secret Manager identifier in `config.PROJECT_ID` (e.g., 'paxtonprospect-stream-key').
        env_var: Optional environment variable name checked in local/CI environments.

    Returns:
        Resolved secret string (never hardcoded in source code).
    """
    if secret_id in _SECRET_CACHE:
        return _SECRET_CACHE[secret_id]
    if env_var and os.getenv(env_var):
        val = os.getenv(env_var, "").strip()
        _SECRET_CACHE[secret_id] = val
        return val
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{config.PROJECT_ID}/secrets/{secret_id}/versions/latest"
        val = client.access_secret_version(name=name).payload.data.decode("utf-8").strip()
        _SECRET_CACHE[secret_id] = val
        return val
    except Exception:
        logging.warning(
            "Secret Manager unreachable for %s; generating ephemeral instance-isolated secret.",
            secret_id,
        )
        ephemeral = secrets.token_hex(32)
        _SECRET_CACHE[secret_id] = ephemeral
        return ephemeral


def _id_token(audience: str) -> str:
    from google.auth.transport.requests import Request
    import google.oauth2.id_token
    return google.oauth2.id_token.fetch_id_token(Request(), audience)


def _access_token() -> str:
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


def _svc_get(name: str, path: str, **params):
    url = _SVC.format(name=name)
    tok = _id_token(url)
    r = httpx.get(url + path, params=params, headers={"Authorization": f"Bearer {tok}"}, timeout=20)
    r.raise_for_status()
    return r.json()


def _svc_post(name: str, path: str, body: dict):
    url = _SVC.format(name=name)
    tok = _id_token(url)
    r = httpx.post(url + path, json=body, headers={"Authorization": f"Bearer {tok}"}, timeout=30)
    r.raise_for_status()
    return r.json()


def search_datastore(ds: str, query: str, n: int = 3) -> dict[str, Any]:
    """Query a Vertex AI Search (Discovery Engine) datastore for grounded documents.

    Args:
        ds: Vertex AI Search datastore ID (e.g., 'paxtonprospect-news', 'paxtonprospect-content-hub').
        query: Natural-language search query string.
        n: Maximum number of grounded document snippets to retrieve (1 to 10).

    Returns:
        Dictionary containing `found` (bool), `results` (list of cited documents
        with `id`, `title`, `link`, and `snippet`), or `error` and `recovery_guidance`.
    """
    t0 = time.monotonic()
    log_intent_and_outcome(
        agent_name="VertexSearchTool",
        action="search_datastore",
        phase="INTENT",
        intent_payload={"datastore": ds, "query": query, "page_size": n},
    )
    url = _DISCOVERY.format(p=config.PROJECT_ID, ds=ds)
    r = None
    for attempt in range(2):
        try:
            r = httpx.post(
                url,
                json={
                    "query": query,
                    "pageSize": n,
                    "contentSearchSpec": {"snippetSpec": {"returnSnippet": True}},
                },
                headers={
                    "Authorization": f"Bearer {_access_token()}",
                    "Content-Type": "application/json",
                },
                timeout=25,
            )
            if r.status_code == 200:
                break
        except Exception as e:
            if attempt == 1:
                err_out = {
                    "found": False,
                    "error": str(e)[:200],
                    "recovery_guidance": (
                        f"Vertex AI Search datastore '{ds}' was unreachable. Do not invent citations; "
                        "continue discovery using verified corpus facts or ask the banker to share materials."
                    ),
                }
                log_intent_and_outcome(
                    agent_name="VertexSearchTool",
                    action="search_datastore",
                    phase="OUTCOME",
                    outcome_payload=err_out,
                    status="error",
                    latency_ms=(time.monotonic() - t0) * 1000,
                )
                return err_out
    if r is None or r.status_code != 200:
        err_out = {
            "found": False,
            "error": (r.text[:200] if r is not None else "no response"),
            "recovery_guidance": (
                f"Datastore '{ds}' returned non-200 status. Inform the prospect that you will "
                "have the banker follow up with the requested reference document."
            ),
        }
        log_intent_and_outcome(
            agent_name="VertexSearchTool",
            action="search_datastore",
            phase="OUTCOME",
            outcome_payload=err_out,
            status="error",
            latency_ms=(time.monotonic() - t0) * 1000,
        )
        return err_out
    results = []
    for item in r.json().get("results", []):
        d = item.get("document", {})
        derived = d.get("derivedStructData", {})
        snippets = [s.get("snippet", "") for s in derived.get("snippets", [])]
        results.append({
            "id": d.get("id"),
            "title": derived.get("title") or d.get("id"),
            "link": derived.get("link", ""),
            "snippet": redact_pii(" ".join(snippets)[:500]),
        })
    out = {"found": bool(results), "results": results}
    log_intent_and_outcome(
        agent_name="VertexSearchTool",
        action="search_datastore",
        phase="OUTCOME",
        outcome_payload={"found": out["found"], "count": len(results)},
        status="ok",
        latency_ms=(time.monotonic() - t0) * 1000,
    )
    return out


# ---------------- Companion-facing tools ----------------

def enrich_prospect_intelligence(company: str) -> dict[str, Any]:
    """Retrieve cited public news signals and corporate treasury context for a prospect company.

    Args:
        company: Legal or commercial name of the prospective client company
            (e.g., 'Meridian Components Inc.').

    Returns:
        JSON-serializable dictionary with `company`, `news` citations, and `public_context`,
        or `recovery_guidance` if validation fails.
    """
    t0 = time.monotonic()
    try:
        validated = ProspectEnrichmentInput(company=company)
    except Exception as e:
        return {
            "found": False,
            "error": f"Invalid company argument: {e}",
            "recovery_guidance": "Pass a non-empty company name between 2 and 120 characters.",
        }
    log_intent_and_outcome(
        agent_name="CompanionTool",
        action="enrich_prospect_intelligence",
        phase="INTENT",
        intent_payload={"company": validated.company},
    )
    news = search_datastore("paxtonprospect-news", f"{validated.company} treasury expansion financing", 4)
    public = search_datastore("paxtonprospect-public-sim", f"solutions for {validated.company}", 2)
    out = {"company": validated.company, "news": news, "public_context": public}
    log_intent_and_outcome(
        agent_name="CompanionTool",
        action="enrich_prospect_intelligence",
        phase="OUTCOME",
        outcome_payload=out,
        status="ok",
        latency_ms=(time.monotonic() - t0) * 1000,
    )
    return out


enrich_prospect = enrich_prospect_intelligence


def get_fx_market_snapshot(pairs: str) -> dict[str, Any]:
    """Fetch live foreign-exchange (FX) spot rates and treasury market index snapshots.

    Args:
        pairs: Comma-separated currency pair or rate series identifiers
            (e.g., 'USDMXN,USDSGD,EURUSD').

    Returns:
        Dictionary containing `found` (bool) and `snapshot` market rates, or
        `error` and `recovery_guidance` on failure.
    """
    t0 = time.monotonic()
    try:
        validated = MarketSnapshotInput(pairs=pairs)
    except Exception as e:
        return {
            "found": False,
            "error": f"Invalid currency pair format: {e}",
            "recovery_guidance": "Provide comma-separated alphanumeric currency codes such as 'USDMXN,USDSGD'.",
        }
    log_intent_and_outcome(
        agent_name="CompanionTool",
        action="get_fx_market_snapshot",
        phase="INTENT",
        intent_payload={"pairs": validated.pairs},
    )
    try:
        out = {"found": True, "snapshot": _svc_get("market-data", "/api/snapshot", ids=validated.pairs)}
        log_intent_and_outcome(
            agent_name="CompanionTool",
            action="get_fx_market_snapshot",
            phase="OUTCOME",
            outcome_payload=out,
            status="ok",
            latency_ms=(time.monotonic() - t0) * 1000,
        )
        return out
    except Exception as e:
        err = {
            "found": False,
            "error": str(e)[:150],
            "recovery_guidance": (
                "Live FX market-data feed is temporarily unavailable. Do not quote or guess "
                "exchange rates; tell the prospect the banker will share an indicative FX sheet."
            ),
        }
        log_intent_and_outcome(
            agent_name="CompanionTool",
            action="get_fx_market_snapshot",
            phase="OUTCOME",
            outcome_payload=err,
            status="error",
            latency_ms=(time.monotonic() - t0) * 1000,
        )
        return err


get_market_snapshot = get_fx_market_snapshot


def search_collateral_materials(area: str, kind: str = "") -> dict[str, Any]:
    """Search the Paxton Bank content hub for client-ready collateral, case studies, and product sheets.

    Args:
        area: Treasury or commercial banking solution area (e.g., 'liquidity sweeps', 'ERP APIs').
        kind: Optional collateral document type (e.g., 'case study', 'whitepaper').

    Returns:
        Dictionary with `found` (bool) and `results` list of matching collateral items.
    """
    try:
        validated = CollateralSearchInput(area=area, kind=kind)
    except Exception as e:
        return {
            "found": False,
            "error": f"Invalid collateral search parameters: {e}",
            "recovery_guidance": "Specify a valid treasury topic in `area` (2-120 chars).",
        }
    return search_datastore("paxtonprospect-content-hub", f"{validated.kind} {validated.area}".strip(), 4)


find_materials = search_collateral_materials


def screen_counterparty_kyc_watchlist(name: str) -> dict[str, Any]:
    """Screen a company or counterparty entity name against OFAC, sanctions, and AML watchlists.

    Args:
        name: Full legal name of the company or counterparty entity to screen.

    Returns:
        Dictionary with `verdict` ('clear', 'review', or 'error') and watchlist screening details.
    """
    t0 = time.monotonic()
    try:
        validated = KycScreenInput(name=name)
    except Exception as e:
        return {
            "verdict": "error",
            "error": f"Invalid entity name: {e}",
            "recovery_guidance": "Pass a valid company or counterparty name (2-120 characters).",
        }
    log_intent_and_outcome(
        agent_name="CompanionTool",
        action="screen_counterparty_kyc_watchlist",
        phase="INTENT",
        intent_payload={"name": validated.name},
    )
    try:
        out = _svc_get("kyc", "/api/screen", name=validated.name)
        log_intent_and_outcome(
            agent_name="CompanionTool",
            action="screen_counterparty_kyc_watchlist",
            phase="OUTCOME",
            outcome_payload=out,
            status="ok",
            latency_ms=(time.monotonic() - t0) * 1000,
        )
        return out
    except Exception as e:
        err = {
            "verdict": "error",
            "error": str(e)[:150],
            "recovery_guidance": (
                "KYC screening service is unreachable. Never confirm KYC or sanctions clearance "
                "verbally; escalate to the human banker and compliance officer."
            ),
        }
        log_intent_and_outcome(
            agent_name="CompanionTool",
            action="screen_counterparty_kyc_watchlist",
            phase="OUTCOME",
            outcome_payload=err,
            status="error",
            latency_ms=(time.monotonic() - t0) * 1000,
        )
        return err


screen_entity = screen_counterparty_kyc_watchlist


def lookup_treasury_specialist(soeid: str) -> dict[str, Any]:
    """Look up a Paxton Bank Treasury & Trade Solutions specialist in the employee directory by SOEID.

    Args:
        soeid: Alphanumeric specialist directory ID (e.g., 'TS-104').

    Returns:
        Dictionary with `found` (bool), specialist `name`, `email`, and `specialty`, or
        `error` and `recovery_guidance` if the ID does not exist.
    """
    try:
        validated = SpecialistLookupInput(soeid=soeid)
    except Exception as e:
        return {
            "found": False,
            "error": f"Invalid SOEID format: {e}",
            "recovery_guidance": "Ask the human banker to provide a valid alphanumeric SOEID in the Console.",
        }
    try:
        return {"found": True, **_svc_get("directory", f"/api/soeid/{validated.soeid}")}
    except Exception:
        return {
            "found": False,
            "error": f"SOEID {validated.soeid} not found",
            "recovery_guidance": (
                f"Specialist ID '{validated.soeid}' was not found in the Paxton Bank directory. "
                "Ask the banker to verify the SOEID in the Console before initiating handoff."
            ),
        }


lookup_specialist = lookup_treasury_specialist


def get_specialist_calendar_slots(soeid: str) -> dict[str, Any]:
    """Retrieve open calendar appointment slots for a Paxton Bank treasury specialist.

    Args:
        soeid: Alphanumeric specialist directory ID (e.g., 'TS-104').

    Returns:
        Dictionary with `found` (bool) and `slots` list of available ISO timestamps, or
        `error` and `recovery_guidance` on failure.
    """
    try:
        validated = SpecialistLookupInput(soeid=soeid)
    except Exception as e:
        return {
            "found": False,
            "error": f"Invalid SOEID format: {e}",
            "recovery_guidance": "Verify the specialist SOEID format before querying calendar availability.",
        }
    try:
        return {"found": True, "slots": _svc_get("scheduler", "/api/slots", soeid=validated.soeid)}
    except Exception as e:
        return {
            "found": False,
            "error": str(e)[:150],
            "recovery_guidance": (
                "Calendar scheduler service is unavailable. Offer to have the specialist email "
                "their availability directly after the briefing."
            ),
        }


get_slots = get_specialist_calendar_slots


def send_approved_prospect_email(to: str, subject: str, body: str) -> dict[str, Any]:
    """Dispatch a banker-approved follow-up or warm-handoff email via the transactional mail service.

    Args:
        to: Recipient email address.
        subject: Email subject line.
        body: Plain-text email body (must be approved by the human banker for client collateral).

    Returns:
        Dictionary with `sent` (bool) and message `id`, or `error` and `recovery_guidance`.
    """
    t0 = time.monotonic()
    log_intent_and_outcome(
        agent_name="CompanionTool",
        action="send_approved_prospect_email",
        phase="INTENT",
        intent_payload={"to": redact_pii(to), "subject": subject},
    )
    try:
        r = _svc_post("mailpit", "/sim/mailpit/api/v1/send", {
            "From": {"Email": "companion@paxtonbank.example.com", "Name": "Paxton Bank Sales AI Companion"},
            "To": [{"Email": to}],
            "Subject": subject,
            "Text": body,
        })
        out = {"sent": True, "id": r.get("ID")}
        log_intent_and_outcome(
            agent_name="CompanionTool",
            action="send_approved_prospect_email",
            phase="OUTCOME",
            outcome_payload=out,
            status="ok",
            latency_ms=(time.monotonic() - t0) * 1000,
        )
        return out
    except Exception as e:
        err = {
            "sent": False,
            "error": str(e)[:150],
            "recovery_guidance": (
                "Email dispatch failed. Inform the banker in the Console so the package can be "
                "sent manually after the call."
            ),
        }
        log_intent_and_outcome(
            agent_name="CompanionTool",
            action="send_approved_prospect_email",
            phase="OUTCOME",
            outcome_payload=err,
            status="error",
            latency_ms=(time.monotonic() - t0) * 1000,
        )
        return err


send_email = send_approved_prospect_email


def get_crm_lead_dossier(lead_id: str) -> dict[str, Any]:
    """Fetch a prospect lead dossier from the Paxton Bank CRM service by lead ID.

    Args:
        lead_id: CRM lead record identifier (e.g., 'HERO-01').

    Returns:
        Dictionary containing the CRM lead profile, or `error` and `recovery_guidance`.
    """
    try:
        return _svc_get("crm-sim", f"/api/leads/{lead_id}")
    except Exception as e:
        return {
            "found": False,
            "error": str(e)[:150],
            "recovery_guidance": "CRM lead lookup failed; rely on live conversation discovery only.",
        }


crm_get_lead = get_crm_lead_dossier


def record_crm_prospect_activity(lead_id: str, atype: str, detail: str, trace_id: str = "") -> dict[str, Any]:
    """Record a PII-scrubbed meeting milestone or activity entry in the Paxton Bank CRM.

    Args:
        lead_id: CRM lead record identifier (e.g., 'HERO-01').
        atype: Activity type code (e.g., 'consent_captured', 'discovery_started', 'handoff_initiated').
        detail: Human-readable summary of the activity (automatically scrubbed for PII).
        trace_id: Optional OpenTelemetry trace ID linking the CRM activity to Cloud Trace.

    Returns:
        Dictionary with `ok` (bool) or `error` and `recovery_guidance`.
    """
    try:
        clean_detail = redact_pii(detail)
        return _svc_post(
            "crm-sim",
            "/api/activities",
            {"lead_id": lead_id, "atype": atype, "detail": clean_detail, "trace_id": trace_id},
        )
    except Exception as e:
        return {
            "ok": False,
            "error": str(e)[:150],
            "recovery_guidance": "CRM activity write failed; milestone remains recorded in local session audit.",
        }


crm_add_activity = record_crm_prospect_activity


# ---------------- avatar stage store (GCS + Firestore) ----------------
_gcs_bucket = None


def _stage_bucket():
    global _gcs_bucket
    if _gcs_bucket is None:
        from google.cloud import storage
        _gcs_bucket = storage.Client().bucket(config.GCS_BUCKET)
    return _gcs_bucket


def stage_publish_seg(room_id: str, who: str, seq: int, data: bytes):
    """Publish ONE segment of the avatar's continuous fMP4 stream to GCS + Firestore."""
    _stage_bucket().blob(f"stage/{room_id}/{who}/{seq:06d}.bin").upload_from_string(
        data, content_type="application/octet-stream")
    fs().collection("stage").document(room_id).set(
        {f"{who}_seq": seq, "ts": firestore.SERVER_TIMESTAMP}, merge=True)


def stage_mark_init(room_id: str, who: str, seq: int):
    """Record that segment `seq` begins a brand-new fMP4 stream (ftyp+moov at byte 0)."""
    fs().collection("stage").document(room_id).set(
        {f"{who}_inits": firestore.ArrayUnion([seq]),
         "ts": firestore.SERVER_TIMESTAMP}, merge=True)


def stage_state_read(room_id: str) -> dict:
    doc = fs().collection("stage").document(room_id).get()
    return doc.to_dict() or {} if doc.exists else {}


def stage_media_read(room_id: str, who: str, seq: int) -> bytes:
    try:
        return _stage_bucket().blob(
            f"stage/{room_id}/{who}/{seq:06d}.bin").download_as_bytes()
    except Exception:
        return b""


STREAM_HOST = "paxtonprospect-stream-229567506693.us-east4.run.app"


def _stream_secret() -> str:
    """Fetch the HMAC stream signing key from Google Cloud Secret Manager."""
    return get_secret("paxtonprospect-stream-key", env_var="PAXTON_STREAM_KEY")


def stream_token(room: str, role: str, ttl_s: int = 6 * 3600) -> str:
    """Mint an HMAC token bound to room+role+expiry for the stream relay."""
    secret = _stream_secret().encode()
    body = f"{room}|{role}|{int(time.time()) + ttl_s}"
    sig = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(body.encode()).decode().rstrip("=") + "." + sig


def verify_stream_token(tok: str, room: str, want_prefix: str) -> bool:
    """Validate a `stream_token` minted above: room match, role prefix, expiry, HMAC."""
    try:
        body_b64, sig = tok.rsplit(".", 1)
        body = base64.urlsafe_b64decode(body_b64 + "=" * (-len(body_b64) % 4)).decode()
        r, role, exp = body.split("|")
        if r != room or int(exp) < time.time() or not role.startswith(want_prefix):
            return False
        want = hmac.new(_stream_secret().encode(), body.encode(),
                        hashlib.sha256).hexdigest()[:32]
        return hmac.compare_digest(want, sig)
    except Exception:
        return False


def stream_url(room: str, role: str, kind: str) -> str:
    """kind: 'recall'|'bridge' -> wss endpoint; 'page' -> https page URL."""
    tok = stream_token(room, role)
    if kind == "page":
        who = role.split(":", 1)[1]
        return f"https://{STREAM_HOST}/page/{room}/{who}?t={tok}"
    ep = "recall" if role == "recall" else "bridge"
    return f"wss://{STREAM_HOST}/{ep}/{room}?t={tok}"


def recall_chat_send(key: str, bot_id: str, message: str):
    """Post into the Zoom meeting chat as the given bot (sync; call off-loop)."""
    try:
        httpx.post(f"https://us-west-2.recall.ai/api/v1/bot/{bot_id}/send_chat_message/",
                   headers={"Authorization": f"Token {key}"},
                   json={"to": "everyone", "message": message[:1000]}, timeout=15)
    except Exception:
        pass


def perf_write(room_id: str, rec: dict):
    """Write a PII-scrubbed performance telemetry record to Cloud Firestore."""
    fs().collection("perf_spans").add({"room": room_id, **redact_data(rec)})


def perf_read(room_id: str, limit: int = 400):
    docs = (fs().collection("perf_spans").where("room", "==", room_id)
            .limit(limit).stream())
    return sorted([d.to_dict() for d in docs], key=lambda r: r.get("t", 0))


def agent_log_write(room_id: str, rec: dict):
    """Persist a PII-redacted durable agent-interaction record (intent & outcome) to Firestore."""
    sanitized = redact_data(rec)
    fs().collection("agent_logs").add({"room_id": room_id, **sanitized})


def agent_log_read(room_id: str, limit: int = 200):
    """Read the last `limit` durable agent-interaction records for a session."""
    docs = (fs().collection("agent_logs").where("room_id", "==", room_id)
            .limit(2000).stream())
    rows = sorted([d.to_dict() for d in docs], key=lambda r: r.get("t", 0))
    return rows[-limit:]


def zoom_bot_write(bot_id: str, room_id: str, who: str):
    fs().collection("zoom_bots").document(bot_id).set({"room": room_id, "who": who})


def zoom_bot_room(bot_id: str) -> str:
    try:
        d = fs().collection("zoom_bots").document(bot_id).get()
        return (d.to_dict() or {}).get("room", "") if d.exists else ""
    except Exception:
        return ""


def zoom_speech_write(room_id: str, who: str, text: str, speaker: str = ""):
    fs().collection("zoom_speech").add({
        "room": room_id, "who": who, "text": redact_pii(text), "speaker": speaker,
        "t": time.time(),
    })


def zoom_speech_poll(room_id: str, since_t: float) -> list[dict]:
    docs = fs().collection("zoom_speech").where("room", "==", room_id).stream()
    rows = [d.to_dict() for d in docs]
    return sorted([r for r in rows if r.get("t", 0) > since_t], key=lambda r: r["t"])


def session_index_write(room_id: str, rec: dict):
    """Write a PII-scrubbed session summary document to Cloud Firestore `sessions`."""
    try:
        fs().collection("sessions").document(room_id).set(redact_data(rec), merge=True)
    except Exception:
        pass


def session_index_list(limit: int = 50) -> list[dict]:
    try:
        docs = fs().collection("sessions").limit(200).stream()
        rows = sorted([d.to_dict() for d in docs], key=lambda r: r.get("started_at", 0), reverse=True)
        return rows[:limit]
    except Exception:
        return []
