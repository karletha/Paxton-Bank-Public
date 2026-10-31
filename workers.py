"""Turn-driven analysis workers: Pulse (J6 sentiment), Panel (J9 insights),
Scorer (J2 five-dimension qualification). Run as async tasks off the voice path;
every invocation is a telemetry span with full content. Results broadcast to the
room + mirrored to Firestore for the Console/Dashboard.

Scoring rubric = the deck's five dimensions (strategic fit, revenue potential,
buying intent, implementation complexity, overall) — never BANT."""
import asyncio
import json
import time

import config
from telemetry import clip, set_genai_attrs, trace

try:
    from google import genai
    from google.genai import types
    client = genai.Client(vertexai=True, project=config.PROJECT_ID, location="global")
except Exception:
    genai = None  # type: ignore
    types = None  # type: ignore
    client = None

tracer = trace.get_tracer("paxtonprospect-bridge")


class _Resp:
    __slots__ = ("text",)

    def __init__(self, text):
        self.text = text


def _contents_text(contents) -> str:
    """Flatten a generate_content `contents` into the reviewable PROMPT text for
    the durable agent log: join every text part; note each audio part as
    '[audio Ns]' (native-perception WAV = 16kHz mono 16-bit -> 32000 bytes/s).
    Handles a bare string, a Part, a Content, or a list of any of those."""
    def _part(p):
        t = getattr(p, "text", None)
        if t:
            return t
        blob = getattr(p, "inline_data", None)
        if blob is not None:
            data = getattr(blob, "data", b"") or b""
            return f"[audio {max(1, round(len(data) / 32000))}s]"
        return ""
    if contents is None:
        return ""
    if isinstance(contents, str):
        return contents
    items = contents if isinstance(contents, (list, tuple)) else [contents]
    out = []
    for it in items:
        if isinstance(it, str):
            out.append(it)
        elif getattr(it, "parts", None):
            out.extend(_part(p) for p in it.parts)
        else:
            out.append(_part(it))
    return "\n".join(x for x in out if x)


async def _gen(room, comp, **kw):
    """STREAMING generate_content — records TTFT (time-to-first-token, the
    realtime-latency number) AND total, emits both on the perf span. Returns a
    shim with .text (accumulated) so callers are unchanged. Also writes ONE
    durable agent_log record (full prompt in, accumulated output out, model,
    phase) — this single call site covers every text agent: Pulse/Panel/
    Scorer/Archivist/OptionSmith/AvaPlanner/Scripter."""
    t0 = time.monotonic()
    ttft = None
    parts = []
    ok = True
    try:
        # ADK 2.x path (config.ADK_AGENTS): the agent runs as a real LlmAgent on
        # an ADK Runner, so ADK's own call_llm spans join our trace. Falls back
        # to the direct streaming call automatically if ADK is unavailable.
        import adk_runtime
        cfg = kw.get("config")
        # ADK 2.x requires a pydantic LlmAgent.output_schema and REFUSES a
        # genai response_schema in the generation config. Agents that pin a
        # response_schema (Panel/Scorer) therefore stay on the direct path
        # until their schemas are ported to pydantic models — dropping the
        # schema made their JSON truncate mid-object (gate g2 caught it).
        # Schema-less agents (Pulse, Archivist, Sentinel classifier, ...) run
        # on ADK today and emit ADK's own call_llm spans.
        _schema_pinned = getattr(cfg, "response_schema", None) is not None
        # force_direct: long plain-text outputs truncate on the ADK event path
        # (~35 tokens; first seen on the email drafter 2026-08-11 — short-output
        # agents like Pulse never trip it). Until root-caused, long-form text
        # agents pin themselves to the proven direct streaming path.
        _force_direct = kw.pop("force_direct", False)
        if adk_runtime.available() and not _schema_pinned and not _force_direct:
            instr = getattr(cfg, "system_instruction", "") or ""
            contents = kw.get("contents")
            if not isinstance(contents, list):
                contents = [contents]
            flat = []
            for c in contents:
                flat.extend(getattr(c, "parts", None) or [c])
            gcfg = None
            if cfg is not None:
                try:
                    from google.genai import types as _t
                    d = cfg.model_dump(exclude_none=True)
                    d.pop("system_instruction", None)   # lives on the LlmAgent
                    d.pop("tools", None)
                    gcfg = _t.GenerateContentConfig(**d)
                except Exception:
                    gcfg = None
            txt = await adk_runtime.run(comp, kw.get("model", ""), instr, flat,
                                        room_id=getattr(room, "room_id", "room"),
                                        gen_cfg=gcfg)
            ttft = (time.monotonic() - t0) * 1000
            return _Resp(txt)
        stream = await client.aio.models.generate_content_stream(**kw)
        async for ch in stream:
            txt = getattr(ch, "text", None)
            if txt:
                if ttft is None:
                    ttft = (time.monotonic() - t0) * 1000
                parts.append(txt)
        return _Resp("".join(parts))
    except Exception:
        ok = False
        raise
    finally:
        _ttft = round(ttft, 1) if ttft is not None else None
        try:
            await room.perf(comp, (time.monotonic() - t0) * 1000,
                            phase="ok" if ok else "err",
                            meta={"ttft_ms": _ttft})
        except Exception:
            pass
        try:
            await room.agent_log(
                comp, "ok" if ok else "err",
                _contents_text(kw.get("contents")), "".join(parts),
                model=kw.get("model", ""), meta={"ttft_ms": _ttft})
        except Exception:
            pass



def _jdict(text):
    """json.loads that tolerates the model wrapping an OBJECT in a one-element
    list (observed on schema-less JSON mode 2026-07-31 — crashed pulse/J8)."""
    out = json.loads(text)
    if isinstance(out, list):
        out = out[0] if out and isinstance(out[0], dict) else {}
    return out if isinstance(out, dict) else {}


def perceive(room, task_prompt: str, seconds: int = 45):
    """NATIVE-AUDIO perception for every agent (user directive: no STT). Returns
    a `contents` list. When live meeting audio is available it is fed as a raw
    audio Part and the model LISTENS; the running memory + recent named turns
    ride along as light text context (names/history the audio alone can't
    carry). Falls back to text-only when no audio has arrived yet (headless
    tests, pre-join)."""
    wav = b""
    try:
        wav = room.meeting_audio_wav(seconds)
    except Exception:
        wav = b""
    mem = getattr(room, "meeting_memory", "")
    tailtxt = "\n".join(f"{w}: {t}" for w, t in list(getattr(room, "convo", []))[-6:])
    ctx = ""
    if mem:
        ctx += f"\nMEETING MEMORY (older context):\n{mem}\n"
    if tailtxt:
        ctx += f"\nRECENT TURNS (for names/history):\n{tailtxt}\n"
    if wav:
        return [
            types.Part.from_bytes(data=wav, mime_type="audio/wav"),
            types.Part(text=(
                "You are LISTENING to the live meeting audio above (the last "
                f"~{seconds}s). Use what you HEAR — words AND tone/pace/hesitation."
                f"{ctx}\n{task_prompt}")),
        ]
    # no audio yet -> text-only (headless/pre-join); behaviour unchanged
    return [types.Part(text=(ctx + "\n" + task_prompt) if ctx else task_prompt)]

SCORE_SYS = """[CONTEXT] You are a background agent supporting KAIROS, the Paxton Bank AI companion that sits in a LIVE Paxton Bank banker <-> prospective-client meeting. The banker is human and in control; the client is a live human. You never speak to anyone directly — you produce structured output that helps the banker via the cockpit. Your journey: J2 — qualify the opportunity from what the CLIENT actually says. You are the qualification Scorer for a bank's AI sales companion. From the
live meeting you are LISTENING to, score the prospect on the deck's five dimensions, each
0-100 with a one-line rationale QUOTING what was said:
strategic_fit, revenue_potential, buying_intent, implementation_complexity (higher =
LESS complex/easier), overall.
Also produce: disqualifiers_checked (list of {rule, triggered:bool}) with at least
["active litigation or sanctions exposure","no cross-border or treasury need",
"revenue below segment floor"], and next_best_action (one sentence).
Only use what you actually heard or the enrichment. JSON only."""

SCORE_SCHEMA = {"type": "OBJECT", "properties": {
    "strategic_fit": {"type": "OBJECT", "properties": {"score": {"type": "INTEGER"}, "rationale": {"type": "STRING"}}, "required": ["score", "rationale"]},
    "revenue_potential": {"type": "OBJECT", "properties": {"score": {"type": "INTEGER"}, "rationale": {"type": "STRING"}}, "required": ["score", "rationale"]},
    "buying_intent": {"type": "OBJECT", "properties": {"score": {"type": "INTEGER"}, "rationale": {"type": "STRING"}}, "required": ["score", "rationale"]},
    "implementation_complexity": {"type": "OBJECT", "properties": {"score": {"type": "INTEGER"}, "rationale": {"type": "STRING"}}, "required": ["score", "rationale"]},
    "overall": {"type": "OBJECT", "properties": {"score": {"type": "INTEGER"}, "rationale": {"type": "STRING"}}, "required": ["score", "rationale"]},
    "disqualifiers_checked": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"rule": {"type": "STRING"}, "triggered": {"type": "BOOLEAN"}}, "required": ["rule", "triggered"]}},
    "next_best_action": {"type": "STRING"}},
    "required": ["strategic_fit", "revenue_potential", "buying_intent",
                 "implementation_complexity", "overall", "disqualifiers_checked",
                 "next_best_action"]}

PANEL_SYS = """[CONTEXT] You are a background agent supporting KAIROS, the Paxton Bank AI companion that sits in a LIVE Paxton Bank banker <-> prospective-client meeting. The banker is human and in control; the client is a live human. You never speak to anyone directly — you produce structured output that helps the banker via the cockpit. Your journey: J9 — surface real-time insight cards from what the CLIENT says. You are the banker-intelligence Panel behind the Kairos cockpit's live capability canvas: each insight becomes a solution card the banker (and later the client) sees. Title = the Paxton Bank capability or client need in plain words; detail = one plain-English sentence; recommended_action = why it matters commercially / the next step. From the live meeting you are
LISTENING to, extract NEW actionable insights for the banker (do not repeat earlier insights, given
below). Each: title (<=60 chars), detail (1 sentence = the CAPABILITY SUMMARY: what the Paxton Bank
capability does for them, plain English), source_quote (verbatim from what was said),
category (need|pain_point|product_interest|signal|risk), ONE recommended_action for
the banker, and commercial_impact (1 sentence: the business outcome for the CLIENT —
cost/time/risk/visibility gains, concrete not fluffy). If the prospect revealed ANY new fact, need, pain
point, or concern since the shown insights, produce 1-2 cards — return [] only when
truly nothing new was said."""

PANEL_SCHEMA = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
    "title": {"type": "STRING"}, "detail": {"type": "STRING"},
    "source_quote": {"type": "STRING"}, "category": {"type": "STRING"},
    "recommended_action": {"type": "STRING"}, "commercial_impact": {"type": "STRING"}},
    "required": ["title", "detail", "source_quote", "category", "recommended_action",
                 "commercial_impact"]}}




async def _agent_tick(room, agent, action, done=False, preview=""):
    """Every specialist agent announces itself in the live activity lane —
    the multi-agent layer is visible, auditable execution, not a label."""
    try:
        if done:
            await room._feed("agent_result", {"agent": agent, "action": action,
                                              "result_preview": preview[:200]})
        else:
            await room._feed("agent_activity", {"agent": agent, "action": action})
    except Exception:
        pass


# Pulse's task prompt (single source — also surfaced verbatim by GET /agents).
PULSE_SYS = (
    "[CONTEXT] You are a background agent supporting BRIDGET, the Paxton Bank AI companion that sits in a LIVE Paxton Bank banker <-> prospective-client meeting. The banker is human and in control; the client is a live human. You never speak to anyone directly — you produce structured output that helps the banker via the cockpit. Your journey: J6 — read the CLIENT's sentiment from voice + words. Score the CLIENT's current sentiment 0-100 (0=hostile, "
    "50=neutral, 100=delighted) from their VOICE and words — "
    "tone, pace and hesitation matter as much as content. "
    "JSON: {\"score\": int, \"driver\": \"3-6 words on the TONE reason\", "
    "\"topic\": \"2-4 words: the subject they reacted to\"}"
)


async def pulse_sentiment(room, prospect_text: str):
    """J6: sentiment of the CLIENT ONLY — never the banker, never Ava, never
    idle. Hard-gated on real client words."""
    if not config.agent_on("Pulse"):
        return
    if not prospect_text or len(prospect_text.split()) < 3:
        return
    await _agent_tick(room, "Pulse", "client sentiment")
    with tracer.start_as_current_span("worker.pulse") as span:
        span.set_attribute("live.room_id", room.room_id)
        try:
            r = await _gen(room, "Pulse",
                model=config.MODEL_FAST,
                contents=perceive(room, PULSE_SYS, seconds=20),
                config=types.GenerateContentConfig(response_mime_type="application/json",
                                                   temperature=0, max_output_tokens=160))
            out = _jdict(r.text)
        except Exception as e:
            span.set_attribute("worker.error", str(e)[:150])
            return
        span.set_attribute("pulse.score", out.get("score", -1))
        prev = room.sentiment_series[-1].get('score') if getattr(room, 'sentiment_series', None) else None
        out['delta'] = (out.get('score', 50) - prev) if prev is not None else 0
        room.sentiment_series.append({"t": time.time(), **out})
        await room._feed("sentiment", out)
        await _agent_tick(room, "Pulse", "sentiment", done=True, preview=str(out.get("score")))


def _client_turns(room):
    """Client speech = sim 'prospect' turns OR (Zoom rooms) any human speaker
    who is neither an AI participant nor the identified banker."""
    banker = (getattr(room, "banker_name", "") or "").lower()
    bots = getattr(room, "_BOT_NAMES", ("(AI)", "Ava", "Kairos", "Bridget"))
    out = []
    for who, t in getattr(room, "convo", []):
        if who == "prospect":
            out.append(t)
        elif who not in ("companion", "banker") and not any(b in who for b in bots) \
                and not (banker and banker in who.lower()):
            out.append(t)
    return out


def _content_hash(room):
    return hash(tuple(t for _, t in getattr(room, "convo", [])[-12:]))


async def panel_insights(room):
    """J9: insights ONLY when the client has actually said something new."""
    if not config.agent_on("Panel"):
        return
    if len(_client_turns(room)) < 1:
        return
    h = _content_hash(room)
    if getattr(room, "_panel_hash", None) == h:
        return                      # nothing new said — never invent
    room._panel_hash = h
    await _agent_tick(room, "Panel", "mining new insights")
    with tracer.start_as_current_span("worker.panel") as span:
        span.set_attribute("live.room_id", room.room_id)
        seen = [c["title"] for c in room.insight_cards]
        try:
            r = await _gen(room, "Panel",
                model=config.MODEL_WORKER,
                contents=perceive(room,
                    f"From the live meeting you just heard, extract NEW actionable "
                    f"insights. Insights already shown (do not repeat): {seen}"),
                config=types.GenerateContentConfig(
                    system_instruction=PANEL_SYS, response_mime_type="application/json",
                    response_schema=_schema(PANEL_SCHEMA), temperature=0.2,
                    max_output_tokens=1000,
                    # 3.6-flash thinks by default and thinking counts against
                    # max_output_tokens -- without this the card JSON truncates
                    # mid-field and json.loads drops the whole batch (g2 flake
                    # root-caused 2026-08-19: 3 of 6 Panel outputs cut short)
                    thinking_config=types.ThinkingConfig(thinking_budget=0)))
            cards = json.loads(r.text)
            if isinstance(cards, dict):
                cards = cards.get("cards") or cards.get("insights") or [cards]
            set_genai_attrs(span, model=config.MODEL_WORKER,
                            output_messages=clip(cards))
        except Exception as e:
            span.set_attribute("worker.error", str(e)[:150])
            return
        for c in cards[:2]:
            c['t'] = time.time()
            # VERIFIED GROUNDING (canvas cards, user ask 2026-08-11): attach the
            # best-matching paxtonbank.com source to every card so the prospect
            # canvas can print a real, clickable "verified grounding" URL —
            # deterministic corpus retrieval, never invented by the model.
            try:
                import grounding
                hits = grounding.retrieve(f"{c.get('title','')} {c.get('detail','')}",
                                          k=1, min_score=2.0)
                if hits:
                    c['grounding_url'] = hits[0]['url']
                    c['grounding_title'] = hits[0]['title']
            except Exception:
                pass
            room.insight_cards.append(c)
            await room._feed("insight", c)
        if cards:
            # fresh substance -> refresh the client follow-up email draft
            asyncio.create_task(draft_followup(room))


EMAIL_SYS = (
    "[CONTEXT] You support BRIDGET, the Paxton Bank AI companion in a LIVE banker<->client "
    "meeting. You draft the client-facing FOLLOW-UP EMAIL the banker will send after "
    "the call. Output ONLY the email text — plain text, no markdown, no commentary.\n"
    "STRUCTURE (match exactly):\n"
    "Subject: <specific, 6-10 words, drawn from what was actually discussed>\n"
    "<blank line>\n"
    "Dear <client's first name — or 'team' if no client name appears in the meeting>,\n"
    "<blank line>\n"
    "<one sentence: pleasure meeting today, naming their strategic context in their "
    "own words>\n<blank line>\n"
    "<one or two sentences acknowledging the operational challenges THEY described>\n"
    "<blank line>\n"
    "We captured several active items from our discussion:\n"
    "- <Short topic label>: <the specific need or Paxton Bank capability discussed, one line>\n"
    "(3-5 bullets, ONLY items actually discussed in this meeting)\n"
    "<blank line>\n"
    "<one or two sentences on the agreed next step — name the specialist meeting and "
    "time if one was agreed; otherwise propose a 30-minute deep-dive discovery>\n"
    "<blank line>\n"
    "Thank you again for choosing Paxton Commercial Banking. We look forward to "
    "supporting your business milestones.\n<blank line>\n"
    "Best regards,\n<blank line>\n{banker}\nPaxton Commercial Banking\n"
    "(Drafted by Bridget — Paxton Bank AI Advisor)\n"
    "HARD RULES: capabilities, never commitments — NO pricing, rates, credit promises, "
    "approvals or guarantees anywhere; anything binding is 'confirmed by our "
    "specialists'. Use ONLY facts from the meeting content provided. Never invent "
    "names, dates or figures."
)


async def draft_followup(room):
    """Client follow-up email, drafted live from THIS meeting's captured substance
    (insight cards + heard turns). Re-drafts as the meeting develops; the banker
    owns the send (copy button) — nothing is ever emailed automatically."""
    if not config.agent_on("Archivist") or not room.insight_cards:
        return
    h = _content_hash(room)
    if getattr(room, "_email_hash", None) == h and getattr(room, "email_draft", ""):
        return
    room._email_hash = h
    await _agent_tick(room, "Archivist", "drafting client follow-up email")
    with tracer.start_as_current_span("worker.email_draft") as span:
        span.set_attribute("live.room_id", room.room_id)
        cards = [{k: c.get(k) for k in ("title", "source_quote", "detail",
                                        "recommended_action", "category")}
                 for c in room.insight_cards[-8:]]
        banker = getattr(room, "banker_name", "") or "Your Paxton Bank banker"
        try:
            r = await _gen(room, "Archivist",
                model=config.MODEL_WORKER,
                force_direct=True,
                contents=perceive(room,
                    "Draft the client follow-up email now. Captured items from this "
                    f"meeting (source for the bullets): {json.dumps(cards)}"),
                config=types.GenerateContentConfig(
                    system_instruction=EMAIL_SYS.replace("{banker}", banker),
                    temperature=0.3, max_output_tokens=4000,
                    # 3.6-flash thinks by default and max_output_tokens caps
                    # thinking+text COMBINED — 700 left ~35 text tokens (the
                    # "cut off after one sentence" bug). Email needs no chain
                    # of thought: budget 0.
                    thinking_config=types.ThinkingConfig(thinking_budget=0)))
            body = (r.text or "").strip()
            # completeness guard: a partial draft must NEVER replace a good one
            # (the closing line is fixed by EMAIL_SYS, so its absence = truncated)
            if not body.lower().startswith("subject:") or "best regards" not in body.lower():
                span.set_attribute("worker.error", f"draft incomplete ({len(body)} chars) — kept previous")
                return
            room.email_draft = body
            await room._feed("email_draft", {"body": body, "t": time.time()})
        except Exception as e:
            span.set_attribute("worker.error", str(e)[:150])


async def score_update(room):
    """J2: qualification ONLY from real client substance — ≥2 client turns and
    new content since the last score. Silence never moves the gauges."""
    if not config.agent_on("Scorer"):
        return
    if len(_client_turns(room)) < 2:
        return
    h = _content_hash(room)
    if getattr(room, "_score_hash", None) == h:
        return
    room._score_hash = h
    await _agent_tick(room, "Scorer", "five-dimension qualification")
    with tracer.start_as_current_span("worker.scorer") as span:
        span.set_attribute("live.room_id", room.room_id)
        try:
            r = await _gen(room, "Scorer",
                model=config.MODEL_WORKER,
                contents=perceive(room,
                    f"Company: {room.company}\nEnrichment notes: "
                    f"{json.dumps(room.enrichment)[:2000]}\n\nFrom the live meeting "
                    f"you just heard, score the five dimensions with evidence.", seconds=60),
                config=types.GenerateContentConfig(
                    system_instruction=SCORE_SYS, response_mime_type="application/json",
                    response_schema=_schema(SCORE_SCHEMA), temperature=0.1,
                    max_output_tokens=1600))
            score = _jdict(r.text)
            set_genai_attrs(span, model=config.MODEL_WORKER, output_messages=clip(score))
        except Exception as e:
            span.set_attribute("worker.error", str(e)[:150])
            return
        score["version"] = len(room.score_versions) + 1
        room.score_versions.append(score)
        await room._feed("score", score)
        if score["overall"]["score"] < 40:
            # J2 neutral-exit path: PRIVATE banker flag; companion never announces it.
            room._log_event("low_fit_flag", f"overall={score['overall']['score']}")
            await room._feed("low_fit", {"overall": score["overall"]["score"],
                                         "guidance": "Banker delivers neutral exit"})
            if room.companion:
                await room.companion.inject_context(
                    "[PRIVATE — LOW FIT] Qualification is low. Do NOT tell the prospect. "
                    "Stay courteous, wind the discovery down naturally, and let the "
                    "banker close the meeting.")
        # CRM writeback (async, audit-correlated)
        try:
            import tools
            await asyncio.to_thread(
                tools.crm_add_activity, room.lead_id, "ai_qualification_updated",
                f"v{score['version']} overall={score['overall']['score']}")
        except Exception:
            pass


def _tail(room, n: int) -> str:
    turns = room.companion.transcript[-n:] if room.companion else []
    return "\n".join(f"{t['role']}: {t['text']}" for t in turns)


def _schema(d):
    return d  # google-genai accepts plain dict schemas in this SDK version


# ---------------- banker-utterance routing (who was addressed?) ----------------
def _wav(pcm: bytes, rate: int = 16000) -> bytes:
    import struct
    n = len(pcm)
    return (b"RIFF" + struct.pack("<I", 36 + n) + b"WAVEfmt " +
            struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) +
            b"data" + struct.pack("<I", n) + pcm)


def _route_sys(room) -> str:
    return (
        "You process one short spoken utterance from a human participant in a live "
        "three-way sales meeting. Participants: the BANKER (human, and possibly named "
        "colleagues), the bank's AI COMPANION (called 'companion', 'AI', 'assistant'), "
        "and the CLIENT side (any participant who is not the banker or the AI; refer to "
        "them by the names heard in the meeting, "
        "the company name, or 'you' when the speaker is clearly continuing a thread "
        "with the client). Decide who the utterance is ADDRESSED to: 'prospect' if it "
        "is directed at the CFO/client; 'companion' if directed at the AI assistant; "
        "'open' if it is to the room or unclear. Default to 'open' when in doubt.")


ADDR_SCHEMA = {"type": "OBJECT", "properties": {
    "text": {"type": "STRING"}, "addressee": {"type": "STRING",
                                              "enum": ["companion", "prospect", "open"]}},
    "required": ["text", "addressee"]}


async def route_banker_utterance(room, pcm: bytes, speaker: str, who: str = "banker"):
    """One fast call: transcribe the buffered utterance AND classify addressee,
    then hand off to room.route_human_text."""
    with tracer.start_as_current_span("router.banker_utterance") as span:
        span.set_attribute("live.room_id", room.room_id)
        span.set_attribute("router.audio_ms", len(pcm) // 32)
        try:
            r = await client.aio.models.generate_content(
                model=config.MODEL_FAST,
                contents=[types.Content(role="user", parts=[
                    types.Part(text="Transcribe this utterance verbatim, then classify "
                                    "the addressee."),
                    types.Part.from_bytes(data=_wav(pcm), mime_type="audio/wav")])],
                config=types.GenerateContentConfig(
                    system_instruction=_route_sys(room),
                    response_mime_type="application/json",
                    response_schema=ADDR_SCHEMA, temperature=0.0,
                    max_output_tokens=800))
            import json as _j
            out = _j.loads(r.text)
            text = (out.get("text") or "").strip()
            addr = out.get("addressee", "open")
            span.set_attribute("router.addressee", addr)
            span.set_attribute("router.text", text[:200])
        except Exception as e:
            span.set_attribute("router.error", str(e)[:150])
            await room.system_note("(didn't catch that — please say it again)")
            return
        if text:
            await room.route_human_text(text, addr, speaker, who)
        else:
            await room.system_note("(didn't catch that — please say it again)")


async def transcribe_pcm(pcm: bytes) -> str:
    """Plain transcription of 16k mono PCM (Zoom listen-mode fallback)."""
    with tracer.start_as_current_span("router.transcribe_pcm") as span:
        span.set_attribute("router.audio_ms", len(pcm) // 32)
        try:
            r = await client.aio.models.generate_content(
                model=config.MODEL_FAST,
                contents=[types.Content(role="user", parts=[
                    types.Part(text="Transcribe this meeting audio verbatim. If it is "
                                    "silence, noise or music, return an empty string."),
                    types.Part.from_bytes(data=_wav(pcm), mime_type="audio/wav")])],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema={"type": "OBJECT",
                                     "properties": {"text": {"type": "STRING"}},
                                     "required": ["text"]},
                    temperature=0.0, max_output_tokens=1200))
            import json as _j
            return (_j.loads(r.text).get("text") or "").strip()
        except Exception as e:
            span.set_attribute("router.error", str(e)[:150])
            return ""


async def classify_addressee(text: str, room) -> str:
    """Text-only addressee call (typed chat / test endpoint)."""
    with tracer.start_as_current_span("router.classify") as span:
        span.set_attribute("live.room_id", room.room_id)
        try:
            r = await client.aio.models.generate_content(
                model=config.MODEL_FAST,
                contents=f"Utterance: \"{text}\" — classify the addressee.",
                config=types.GenerateContentConfig(
                    system_instruction=_route_sys(room),
                    response_mime_type="application/json",
                    response_schema=ADDR_SCHEMA, temperature=0.0,
                    max_output_tokens=400))
            import json as _j
            addr = _j.loads(r.text).get("addressee", "open")
            span.set_attribute("router.addressee", addr)
            return addr if addr in ("companion", "prospect", "open") else "open"
        except Exception as e:
            span.set_attribute("router.error", str(e)[:150])
            return "open"


# ---------------- banker teleprompter (dynamic script for the exec) ----------------
JOURNEYS_REF = (
    "J1 consent/intro, J2 discovery & qualification scoring, J4 risk/guarantee "
    "escalation, J5 warm hand-off to a specialist, J6 sentiment, J7 materials "
    "(with banker approval), J8 objection handling, J9 real-time intelligence.")

_LINE = {"type": "OBJECT", "properties": {
    "text": {"type": "STRING"}, "journey": {"type": "STRING"}, "label": {"type": "STRING"}},
    "required": ["text", "journey", "label"]}

SCRIPT_SYS = (
    "[CONTEXT] You are a background agent supporting KAIROS, the Paxton Bank AI companion that sits in a LIVE Paxton Bank banker <-> prospective-client meeting. The banker is human and in control; the client is a live human. You never speak to anyone directly — you produce structured output that helps the banker via the cockpit. Your journey: banker teleprompter — suggest the banker's next move. You script the PAXTON BANKER's next MOVES in a live meeting with a prospective client "
    "(played by an AI). Given the dossier, real transcript, and scores, produce 3 "
    "distinct lines the BANKER should say next (read aloud) — each a different "
    "direction that advances the meeting. Natural spoken sentences, no brackets. "
    "Tag each with the ONE user journey it advances, using these codes: "
    f"{JOURNEYS_REF}. Build on what was actually said; target the least-covered "
    "qualification dimensions; move the arc opening->discovery->needs->materials/"
    "specialist->close. JSON: {\"stage\":one of [opening,discovery,needs,advance,"
    "close], \"banker\":[{\"text\":str,\"journey\":\"J2\",\"label\":short}], "
    "\"tip\":short private coaching note}.")
SCRIPT_SCHEMA = {"type": "OBJECT", "properties": {
    "stage": {"type": "STRING", "enum": ["opening", "discovery", "needs", "advance", "close"]},
    "banker": {"type": "ARRAY", "items": _LINE},
    "tip": {"type": "STRING"}},
    "required": ["stage", "banker", "tip"]}

COPT_SYS = (
    "[CONTEXT] You are a background agent supporting KAIROS, the Paxton Bank AI companion that sits in a LIVE Paxton Bank banker <-> prospective-client meeting. The banker is human and in control; the client is a live human. You never speak to anyone directly — you produce structured output that helps the banker via the cockpit. Your journey: reply options — anticipate how the CLIENT might respond. The banker just said a line to the client. "
    "Produce 3 distinct, natural REPLIES the client could give to THAT "
    "exact line — cooperative, probing, and challenging. Verbatim spoken sentences, "
    "no brackets. Tag each with the ONE user journey the reply advances, using: "
    f"{JOURNEYS_REF}. Stay consistent with what the client has already "
    "said. JSON: {\"options\":[{\"text\":str,\"journey\":\"J8\",\"label\":short}]}.")
COPT_SCHEMA = {"type": "OBJECT", "properties": {
    "options": {"type": "ARRAY", "items": _LINE}}, "required": ["options"]}


# J1 golden turn 2 — text is guaranteed to match Room._CONSENT_RX so picking it
# deterministically flips consents['prospect'].
CONSENT_OPTION = {"text": "That's fine by me — go ahead.",
                  "journey": "J1", "label": "Give consent"}


async def client_options(room, banker_text: str, ensure_consent: bool = False):
    if not config.agent_on("OptionSmith"):
        return
    """Given the line the banker (or Ava) just said, generate the CFO's possible
    replies (journey-tagged) for the banker to pick — the client then says it in
    Zoom. ensure_consent=True (pre-consent phase): option 1 is ALWAYS the
    deterministic consent grant."""
    import personas
    await _agent_tick(room, "OptionSmith", "client reply options")
    with tracer.start_as_current_span("worker.client_options") as span:
        span.set_attribute("live.room_id", room.room_id)
        if ensure_consent:
            span.set_attribute("copt.ensure_consent", True)
        try:
            b = personas.load_bibles().get(room.company, {})
            tp = b.get("treasury_profile", {})
            persona = (f"CFO of {room.company} ({b.get('industry','?')}). Pain: "
                       f"{'; '.join(tp.get('pain_points', []))}. Banks: "
                       f"{', '.join(tp.get('current_banks', []))}.")
            r = await _gen(room, "OptionSmith",
                model=config.MODEL_WORKER,
                contents=perceive(room,
                    f"PERSONA:\n{persona}\n\nThe banker just said (or you just "
                    f"heard): \"{banker_text}\"\n\nGive the CFO's 3 possible replies "
                    f"to what was actually said in the meeting you heard."),
                config=types.GenerateContentConfig(
                    system_instruction=COPT_SYS, response_mime_type="application/json",
                    response_schema=COPT_SCHEMA, temperature=0.7, max_output_tokens=800))
            import json as _j
            opts = _j.loads(r.text).get("options", [])[:3]
            if ensure_consent:
                opts = [dict(CONSENT_OPTION)] + [
                    o for o in opts if o.get("journey") != "J1"][:2]
            if not opts:                       # empty model return -> never dead-end
                raise ValueError("empty options")
            await room._feed("client_options", {"to": banker_text, "options": opts})
            room._lane_gen += 1
            room.enqueue_client_renders([o.get("text", "") for o in opts])
        except Exception as e:
            span.set_attribute("copt.error", str(e)[:150])
            # the client lane must NEVER dead-end — always leave the exec cards
            if ensure_consent:
                fallback = [dict(CONSENT_OPTION),
                    {"text": "Before I agree — who exactly sees these notes?",
                     "journey": "J1", "label": "Probe first"},
                    {"text": "As long as it stays within Paxton Bank, no objection here.",
                     "journey": "J1", "label": "Conditional yes"}]
            else:
                fallback = [
                    {"text": "Right now it's spread across a few regional banks — "
                             "no single view, and reconciliation eats a lot of time.",
                     "journey": "J2", "label": "Reveal pain"},
                    {"text": "Before we go deeper — what would this actually cost us?",
                     "journey": "J8", "label": "Push on price"},
                    {"text": "That's helpful. What would the first 90 days look like?",
                     "journey": "J2", "label": "Lean in"}]
            await room._feed("client_options", {"to": banker_text, "options": fallback})
            room._lane_gen += 1
            room.enqueue_client_renders([o["text"] for o in fallback])


MEMORY_SYS = (
    "[CONTEXT] You are a background agent supporting KAIROS, the Paxton Bank AI companion that sits in a LIVE Paxton Bank banker <-> prospective-client meeting. The banker is human and in control; the client is a live human. You never speak to anyone directly — you produce structured output that helps the banker via the cockpit. Your job: maintain concise whole-meeting memory every other agent reads. You are the Archivist for a live sales meeting. Maintain ONE compact "
    "running memory (<=180 words) of the ENTIRE conversation so far: hard "
    "facts and numbers stated (with who said them), commitments, objections, "
    "open questions, and anything likely to be referenced later. Update the "
    "prior memory with the new turns — never drop earlier facts unless "
    "corrected. Plain text, dense, no preamble.")


async def meeting_memory(room):
    if not config.agent_on("Archivist"):
        return
    """Archivist: whole-meeting awareness for every other agent — late
    references to earlier statements stay grounded."""
    await _agent_tick(room, "Archivist", "updating meeting memory")
    with tracer.start_as_current_span("worker.archivist") as span:
        span.set_attribute("live.room_id", room.room_id)
        try:
            r = await _gen(room, "Archivist",
                model=config.MODEL_WORKER,
                contents=perceive(room,
                    f"PRIOR MEMORY:\n{getattr(room, 'meeting_memory', '(none)')}"
                    f"\n\nFold what you just HEARD into the running memory. "
                    f"Updated memory:", seconds=60),
                config=types.GenerateContentConfig(
                    system_instruction=MEMORY_SYS, temperature=0.1,
                    max_output_tokens=400))
            room.meeting_memory = (r.text or "").strip()[:1600]
            await _agent_tick(room, "Archivist", "memory", done=True,
                              preview=room.meeting_memory[:120])
        except Exception as e:
            span.set_attribute("archivist.error", str(e)[:120])


def _memory_block(room) -> str:
    m = getattr(room, "meeting_memory", "")
    return f"\nMEETING MEMORY (full-conversation):\n{m}\n" if m else ""


# Deterministic J1 cards for Ava's lane — the exec picks; Ava NEVER interjects.
AVA_GREETING = {"text": "Good morning, everyone — I'm Bridget, the Paxton Bank AI advisor "
                        "for today. Before we begin, may I have everyone's "
                        "permission to participate and take notes? If you agree, "
                        "please reply 'I consent' in the meeting chat.",
                "journey": "J1", "label": "Open + consent ask (chat)"}
AVA_THANKYOU = {"text": "Thank you — consent recorded.",
                "journey": "J1", "label": "Confirm + hand back"}

AVA_SYS = (
    "[CONTEXT] You are a background agent supporting KAIROS, the Paxton Bank AI companion that sits in a LIVE Paxton Bank banker <-> prospective-client meeting. The banker is human and in control; the client is a live human. You never speak to anyone directly — you produce structured output that helps the banker via the cockpit. Your journey: suggest Kairos's next possible lines (the banker may pick one). You write AVA's next possible spoken contributions. Ava is the Paxton Bank "
    "banker's AI companion INSIDE a live Zoom sales meeting with a prospect "
    "CFO. Ava only ever speaks when the banker picks a line — write 3 short, "
    "distinct, high-value contributions she could make RIGHT NOW: e.g. recap "
    "what the client said, surface a cited insight, offer to prepare "
    "materials (J7), suggest a specialist meeting (J5), gently advance "
    "discovery (J2). Natural spoken sentences, ≤2 sentences each, no "
    "brackets. Tag each with ONE journey code from: "
    f"{JOURNEYS_REF}. JSON: {{\"options\":[{{\"text\":str,\"journey\":\"J9\","
    "\"label\":short}}]}}.")


async def ava_options(room):
    if not config.agent_on("AvaPlanner"):
        return
    """Regenerate Ava's suggestion lane from the live meeting state. Pre-
    consent this is deterministic (J1 golden cards only)."""
    await _agent_tick(room, "AvaPlanner", "Ava's suggested actions")
    allc = all(room.consents.values())
    if not allc:
        cards = [dict(AVA_GREETING)] if not getattr(room, "greeted", False) else []
        await room._feed("ava_options", {"options": cards})
        return
    if not getattr(room, "ava_thanked", False):
        await room._feed("ava_options", {"options": [dict(AVA_THANKYOU)]})
        return
    with tracer.start_as_current_span("worker.ava_options") as span:
        span.set_attribute("live.room_id", room.room_id)
        try:
            r = await _gen(room, "AvaPlanner",
                model=config.MODEL_WORKER,
                contents=perceive(room,
                    "From the live meeting you just heard, write Ava's 3 candidate "
                    "contributions right now."),
                config=types.GenerateContentConfig(
                    system_instruction=AVA_SYS, response_mime_type="application/json",
                    response_schema=COPT_SCHEMA, temperature=0.7, max_output_tokens=900))
            import json as _j
            opts = _j.loads(r.text).get("options", [])[:3]
            await room._feed("ava_options", {"options": opts})
        except Exception as e:
            span.set_attribute("avaopt.error", str(e)[:150])


async def banker_script(room):
    if not config.agent_on("Scripter"):
        return
    """Regenerate the exec's teleprompter from the dossier + REAL transcript +
    score gaps. Fired after every client reply; deviation self-heals because
    the next script is always derived from what was actually said."""
    import personas
    await _agent_tick(room, "Scripter", "your next moves")
    with tracer.start_as_current_span("worker.banker_script") as span:
        span.set_attribute("live.room_id", room.room_id)
        # J1 phase gate: until every party consents on the record, the exec's
        # only moves ARE the consent beat — deterministic, instant, no LLM.
        if not all(getattr(room, "consents", {}).values()):
            span.set_attribute("script.stage", "j1-consent")
            await room._feed("script", {
                "stage": "opening",
                "tip": "Journey 1 — nothing substantive until everyone has "
                       "consented on the record. Ava asks; the client answers; "
                       "you confirm yours on the consent card.",
                "banker": [
                    {"journey": "J1", "label": "Warm welcome",
                     "text": "Thanks for making the time today — before we dive in, "
                             "my AI companion Ava will ask everyone's permission to "
                             "join us and take notes."},
                    {"journey": "J1", "label": "Confirm your consent",
                     "text": "And from my side, Ava — you have my consent as well "
                             "to participate and take notes."}]})
            return
        try:
            b = personas.load_bibles().get(room.company, {})
            tp = b.get("treasury_profile", {})
            dossier = (f"Company: {room.company} ({b.get('industry','?')}, "
                       f"~${b.get('revenue_musd','?')}M). Banks: "
                       f"{', '.join(tp.get('current_banks', []))}. Flows: "
                       f"{tp.get('payment_flows','?')}. Pain points: "
                       f"{'; '.join(tp.get('pain_points', []))}.")
            score = room.score_versions[-1] if room.score_versions else {}
            r = await _gen(room, "Scripter",
                model=config.MODEL_WORKER,
                contents=perceive(room,
                    f"DOSSIER:\n{dossier}\n\nQUALIFICATION STATE:\n{str(score)[:900]}"
                    f"\n\nFrom the live meeting you just heard, write the banker's "
                    f"next lines.", seconds=45),
                config=types.GenerateContentConfig(
                    system_instruction=SCRIPT_SYS, response_mime_type="application/json",
                    response_schema=SCRIPT_SCHEMA, temperature=0.6,
                    max_output_tokens=1800))
            import json as _j
            out = _j.loads(r.text)
            span.set_attribute("script.stage", out.get("stage", "?"))
            await room._feed("script", out)
        except Exception as e:
            span.set_attribute("script.error", str(e)[:180])
            # post-consent fallback: pure discovery (J1 is already done here)
            await room._feed("script", {"stage": "discovery", "tip": "", "banker": [
                {"journey": "J2", "label": "Open discovery",
                 "text": "To start, walk me through how you're handling cross-border payments today and where the friction is."},
                {"journey": "J2", "label": "Quantify the pain",
                 "text": "Roughly what volume are you moving, and what's it costing you in fees and delays right now?"},
                {"journey": "J2", "label": "Map the banks",
                 "text": "Which banks are in the mix today, and who owns which flow?"}]})


# Generic in-character lines used whenever choice generation fails or returns
# empty — the exec must always have working buttons.
FALLBACK_LINES = [
    "That makes sense — walk me through how it would work for us.",
    "Before we go further, what does this actually cost?",
    "I'm not convinced yet — what makes this different from our bank today?",
]

CHOICES_SYS = (
    "You write the prospect's possible NEXT LINES in a live sales meeting, in character. "
    "Given the persona and transcript, produce exactly 3 distinct short candidate replies "
    "(each 1-2 spoken sentences): #1 cooperative/forthcoming, #2 probing/practical question, "
    "#3 challenging (pricing/timing/complexity pushback or - only if natural - a "
    "guarantee/compliance demand). JSON: {\"choices\": [str, str, str]}.")


async def prospect_choices(room) -> list:
    """3 in-character candidate next lines for the prospect (modes B/C)."""
    import personas
    persona = personas.simulator_sys(room.company, "CFO", room.sim_disposition)
    tail = "\n".join(f"{t['role']}: {t['text']}" for t in
                      (room.companion.transcript[-10:] if room.companion else []))
    with tracer.start_as_current_span("director.choices") as span:
        span.set_attribute("live.room_id", room.room_id)
        try:
            r = await client.aio.models.generate_content(
                model=config.MODEL_WORKER,
                contents=f"PERSONA:\n{persona[:2500]}\n\nTRANSCRIPT TAIL:\n{tail}\n\nProduce the 3 candidate next lines.",
                config=types.GenerateContentConfig(
                    system_instruction=CHOICES_SYS, response_mime_type="application/json",
                    temperature=0.9, max_output_tokens=1500))
            import json as _j
            out = [c for c in _j.loads(r.text).get("choices", [])[:3]
                   if isinstance(c, str) and c.strip()]
            span.set_attribute("director.n", len(out))
            return out or list(FALLBACK_LINES)
        except Exception as e:
            span.set_attribute("director.error", str(e)[:150])
            return list(FALLBACK_LINES)
