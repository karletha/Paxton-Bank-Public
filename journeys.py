"""G3 journey machinery: J7 Human-in-the-Loop (HITL) materials approval gate,
J8 objection Coach, and J5 specialist warm handoff Scribe.

All high-stakes external actions (such as emailing collateral packages to a
prospect) halt at an explicit Human-in-the-Loop hook backed by Cloud Firestore
(`approvals` collection) and require human banker confirmation before execution.
"""
import asyncio
import json
import time
import uuid

import config
from schemas import MaterialsApprovalInput, WarmHandoffInput
from telemetry import clip, log_intent_and_outcome, set_genai_attrs, trace
import tools

try:
    from google import genai
    from google.genai import types
    client = genai.Client(vertexai=True, project=config.PROJECT_ID, location="global")
except Exception:
    genai = None  # type: ignore
    types = None  # type: ignore
    client = None

tracer = trace.get_tracer("paxtonprospect-bridge")

OBJECTION_SYS = (
    "Classify the prospect utterance. JSON: {\"objection\": "
    "\"price|timing|complexity|none\", \"summary\": \"<=15 words\"}. 'none' unless a "
    "clear commercial objection/hesitation is expressed."
)

COACH_SYS = (
    "[CONTEXT] You support BRIDGET, the Paxton Bank AI companion in a LIVE banker<->client meeting. "
    "The banker is human and in control. Your journey: J8 — help the banker reframe a client objection "
    "(the banker delivers it, not you). You are the banker's objection Coach. Given the objection and "
    "transcript context, produce JSON: {\"suggested_response\": \"2-3 sentences the banker could say, "
    "empathetic and concrete\", \"comparable_case\": \"1 sentence referencing the kind of client story "
    "to cite\", \"content_query\": \"search phrase for supporting collateral\"}. Never invent numbers."
)


def require_human_banker_approval(action_name: str, payload: dict) -> dict:
    """Explicit Human-in-the-Loop (HITL) policy hook for high-stakes actions.

    Stops automatic execution and returns a pending approval descriptor that must
    be confirmed by the authenticated human relationship manager.
    """
    approval_id = uuid.uuid4().hex[:10]
    return {
        "hitl_required": True,
        "status": "pending_banker_approval",
        "approval_id": approval_id,
        "action": action_name,
        "payload": payload,
        "requested_at": time.time(),
    }


# ---------------- J7: materials with banker approval gate (HITL) ----------------
async def request_banker_materials_approval(room, area: str, note: str = "") -> dict:
    """Draft a client collateral package and halt at the Human-in-the-Loop banker approval gate.

    High-stakes action: NO email is sent to the prospect until the human banker
    explicitly approves the package via `decide_banker_materials_approval`.

    Args:
        room: Active meeting `Room` instance.
        area: Treasury or commercial banking solution topic (e.g., 'liquidity sweeps').
        note: Optional context note for the human banker reviewing the package.

    Returns:
        Dictionary with `status='pending_banker_approval'`, `approval_id`, `items_found`,
        and explicit `guidance` for the LLM while awaiting human confirmation.
    """
    try:
        validated = MaterialsApprovalInput(area=area, note=note)
    except Exception as e:
        return {
            "status": "validation_error",
            "error": str(e),
            "recovery_guidance": "Provide a valid `area` string (2-120 chars) describing the requested collateral.",
        }

    with tracer.start_as_current_span("j7.request_materials") as span:
        span.set_attribute("live.room_id", room.room_id)
        span.set_attribute("j7.area", validated.area)
        log_intent_and_outcome(
            agent_name="HITLGate",
            action="request_banker_materials_approval",
            phase="INTENT",
            room_id=room.room_id,
            intent_payload={"area": validated.area, "note": validated.note},
            status="planned",
        )
        hits = await asyncio.to_thread(tools.find_materials, validated.area)
        items = (hits.get("results") or [])[:3]
        hitl_gate = require_human_banker_approval("send_collateral_email", {"area": validated.area})
        approval_id = hitl_gate["approval_id"]
        rec = {
            "approval_id": approval_id,
            "kind": "materials",
            "area": validated.area,
            "note": validated.note,
            "items": items,
            "status": "pending",
            "requested_at": hitl_gate["requested_at"],
        }
        room.approvals[approval_id] = rec
        try:
            tools.fs().collection("approvals").document(approval_id).set(
                {**rec, "room_id": room.room_id})
        except Exception:
            pass
        await room._feed("approval", rec)
        room._log_event("materials_requested", f"{validated.area} ({len(items)} items) -> pending approval")
        span.set_attribute("j7.items", len(items))
        out = {
            "status": "pending_banker_approval",
            "approval_id": approval_id,
            "items_found": len(items),
            "guidance": (
                "Tell the prospect the materials are being prepared and "
                "will arrive by email shortly after a quick internal check."
            ),
        }
        log_intent_and_outcome(
            agent_name="HITLGate",
            action="request_banker_materials_approval",
            phase="OUTCOME",
            room_id=room.room_id,
            outcome_payload=out,
            status="blocked_by_hitl",
        )
        return out


request_materials = request_banker_materials_approval


async def decide_banker_materials_approval(
    room, approval_id: str, decision: str, to_email: str = ""
) -> dict:
    """Execute or reject a pending collateral package after explicit human banker confirmation.

    Args:
        room: Active meeting `Room` instance.
        approval_id: Pending approval identifier created by `request_banker_materials_approval`.
        decision: Human banker decision ('approved' or 'rejected').
        to_email: Prospect recipient email address.

    Returns:
        Dictionary with `ok` (bool) and final `status`, or `recovery_guidance` if unknown.
    """
    rec = room.approvals.get(approval_id)
    if not rec:
        return {
            "ok": False,
            "error": "unknown approval",
            "recovery_guidance": "Verify the `approval_id` from `room.approvals` before submitting a decision.",
        }
    with tracer.start_as_current_span("j7.decision") as span:
        span.set_attribute("j7.approval_id", approval_id)
        span.set_attribute("j7.decision", decision)
        rec["status"] = decision
        rec["decided_at"] = time.time()
        try:
            tools.fs().collection("approvals").document(approval_id).set(
                {"status": decision, "decided_at": rec["decided_at"]}, merge=True)
        except Exception:
            pass
        if decision == "approved":
            body = (
                "Hello,\n\nAs discussed in our meeting, here are the materials on "
                f"{rec['area']}:\n\n"
                + "\n".join(
                    f"- {i['title']}: https://paxtonprospect-bridge-229567506693."
                    f"us-east4.run.app/sim/content-hub/c/{i['id']}"
                    for i in rec["items"]
                )
                + "\n\nYour banker reviewed this selection. Happy to walk through any "
                "of it.\n— Paxton Bank Sales AI Companion"
            )
            sent = await asyncio.to_thread(
                tools.send_email,
                to_email or "prospect@example.com",
                f"Materials: {rec['area']}",
                body,
            )
            rec["email"] = sent
            room._log_event("materials_sent", f"{rec['area']} -> {to_email} (approved)")
            if room.companion:
                await room.companion.inject_context(
                    f"[MATERIALS APPROVED+SENT] The banker approved the {rec['area']} "
                    "materials; they were just emailed. Briefly tell the prospect and "
                    "ask if they'd like to walk through anything now."
                )
        else:
            room._log_event("materials_rejected", rec["area"])
            if room.companion:
                await room.companion.inject_context(
                    "[MATERIALS NOT APPROVED] Do not promise the materials; tell the "
                    "prospect the banker will follow up with the right collateral."
                )
        await room._feed("approval", rec)
        log_intent_and_outcome(
            agent_name="HITLGate",
            action="decide_banker_materials_approval",
            phase="OUTCOME",
            room_id=room.room_id,
            outcome_payload={"approval_id": approval_id, "decision": decision},
            status="ok",
        )
        return {"ok": True, "status": decision}


decide_materials = decide_banker_materials_approval


# ---------------- J8: objection detection + Coach ----------------
async def check_objection(room, text: str):
    """Classify whether a prospect turn contains a price, timing, or complexity objection."""
    with tracer.start_as_current_span("j8.detect") as span:
        span.set_attribute("live.room_id", room.room_id)
        out = None
        for _ in range(2):
            try:
                r = await client.aio.models.generate_content(
                    model=config.MODEL_FAST,
                    contents=text,
                    config=types.GenerateContentConfig(
                        system_instruction=OBJECTION_SYS,
                        response_mime_type="application/json",
                        temperature=0,
                        max_output_tokens=120,
                    ),
                )
                out = json.loads(r.text)
                if isinstance(out, list):
                    out = out[0] if out and isinstance(out[0], dict) else None
                if out is not None:
                    break
            except Exception:
                await asyncio.sleep(0.5)
        if out is None:
            span.set_attribute("j8.error", "classifier_failed_2x")
            return
        span.set_attribute("j8.objection", out.get("objection", "none"))
        if out.get("objection", "none") == "none":
            return
        asyncio.create_task(coach(room, out, text))


async def coach(room, det: dict, text: str):
    """Generate real-time objection reframing guidance and supporting collateral for the banker."""
    if not config.agent_on("Coach"):
        return
    with tracer.start_as_current_span("j8.coach") as span:
        span.set_attribute("j8.objection", det["objection"])
        try:
            r = await client.aio.models.generate_content(
                model=config.MODEL_WORKER,
                contents=(
                    f"Objection type: {det['objection']} — {det['summary']}\n"
                    f"Utterance: {text}\nRecent transcript:\n"
                    + "\n".join(
                        f"{t['role']}: {t['text']}"
                        for t in (room.companion.transcript[-8:] if room.companion else [])
                    )
                ),
                config=types.GenerateContentConfig(
                    system_instruction=COACH_SYS,
                    response_mime_type="application/json",
                    temperature=0.3,
                    max_output_tokens=2000,
                ),
            )
            advice = json.loads(r.text)
            set_genai_attrs(span, model=config.MODEL_WORKER, output_messages=clip(advice))
        except Exception as e:
            span.set_attribute("j8.error", str(e)[:150])
            return
        support = await asyncio.to_thread(
            tools.find_materials, advice.get("content_query", det["summary"]))
        card = {
            "objection": det["objection"],
            "summary": det["summary"],
            "suggested_response": advice.get("suggested_response"),
            "comparable_case": advice.get("comparable_case"),
            "support": (support.get("results") or [])[:2],
        }
        room.objections.append(card)
        room._log_event("objection_detected", f"{det['objection']}: {det['summary']}")
        await room._feed("objection", card)
        if room.companion:
            await room.companion.inject_context(
                f"[OBJECTION ROUTED TO BANKER] The prospect raised a {det['objection']} "
                "objection. Acknowledge it briefly and warmly, and hand the point to "
                "the banker — do NOT argue pricing/terms yourself."
            )


# ---------------- J5: handoff Scribe ----------------
async def initiate_specialist_warm_handoff(
    room, specialist_soeid: str, availability_note: str = ""
) -> dict:
    """Compile a vetted warm-handoff briefing packet and dispatch it to a Paxton Bank Treasury Specialist.

    Args:
        room: Active meeting `Room` instance.
        specialist_soeid: Specialist directory ID (SOEID) provided by the banker.
        availability_note: Prospect's preferred meeting times or timezone notes.

    Returns:
        Dictionary with `ok` (bool), `specialist` name, `email_sent`, `slots_offered`,
        and `guidance` (or descriptive `error` recovery instructions if SOEID is not found).
    """
    try:
        validated = WarmHandoffInput(
            specialist_soeid=specialist_soeid,
            availability_note=availability_note,
        )
    except Exception as e:
        return {
            "ok": False,
            "error": f"Invalid handoff parameters: {e} — banker should re-check the ID in the Console.",
            "recovery_guidance": "Ask the human banker to verify the specialist SOEID in the Console.",
        }

    with tracer.start_as_current_span("j5.handoff") as span:
        span.set_attribute("live.room_id", room.room_id)
        span.set_attribute("j5.soeid", validated.specialist_soeid)
        spec = await asyncio.to_thread(tools.lookup_specialist, validated.specialist_soeid)
        if not spec.get("found"):
            await room._feed("handoff", {"error": f"SOEID {validated.specialist_soeid} not found"})
            span.set_attribute("j5.error", "soeid_not_found")
            return {
                "ok": False,
                "error": (
                    f"SOEID {validated.specialist_soeid} not found — banker "
                    "should re-check the ID in the Console."
                ),
                "recovery_guidance": (
                    f"Tell the banker that specialist ID '{validated.specialist_soeid}' was not found "
                    "in the Paxton Bank directory and ask for a valid SOEID."
                ),
            }
        slots = await asyncio.to_thread(tools.get_slots, validated.specialist_soeid)
        slot_lines = "\n".join(f"- {s['start']}" for s in (slots.get("slots") or [])[:4])
        transcript = "\n".join(
            f"{t['role']}: {t['text']}"
            for t in (room.companion.transcript if room.companion else [])
        )
        try:
            r = await client.aio.models.generate_content(
                model=config.MODEL_WORKER,
                contents=(
                    "Summarize this prospect meeting for a treasury sales specialist "
                    "taking a warm handoff (5-8 bullet points: needs, flows, pains, "
                    f"open questions, agreed next step):\n{transcript[-8000:]}"
                ),
                config=types.GenerateContentConfig(temperature=0.2, max_output_tokens=600),
            )
            summary = r.text
        except Exception:
            summary = "(summary unavailable)"
        score = room.score_versions[-1] if room.score_versions else {}
        senti = room.sentiment_series[-1] if room.sentiment_series else {}
        body = (
            f"Warm handoff — {room.company}\n\nMEETING SUMMARY\n{summary}\n\n"
            "QUALIFICATION (five dimensions)\n"
            + "\n".join(
                f"- {k}: {score[k]['score']}"
                for k in (
                    "strategic_fit", "revenue_potential", "buying_intent",
                    "implementation_complexity", "overall",
                )
                if k in score
            )
            + f"\n\nSENTIMENT (latest): {senti.get('score', 'n/a')} ({senti.get('driver', '')})\n"
            f"\nPROSPECT AVAILABILITY\n{validated.availability_note or 'see slots below'}\n"
            f"\nYOUR OPEN SLOTS\n{slot_lines}\n\n— Paxton Bank Sales AI Companion (full transcript "
            "and audit trail attached in CRM)"
        )
        sent = await asyncio.to_thread(
            tools.send_email, spec["email"], f"Warm handoff: {room.company}", body
        )
        room._log_event("handoff_sent", f"{validated.specialist_soeid} ({spec['name']})")
        await room._feed("handoff", {
            "specialist": spec,
            "sent": sent,
            "slots": (slots.get("slots") or [])[:4],
        })
        span.set_attribute("j5.sent", bool(sent.get("sent")))
        return {
            "ok": True,
            "specialist": spec["name"],
            "email_sent": sent.get("sent"),
            "slots_offered": len(slots.get("slots") or []),
            "guidance": (
                "Tell the prospect the specialist will reach out, and "
                "offer the earliest slots."
            ),
        }


initiate_handoff = initiate_specialist_warm_handoff
