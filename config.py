"""Shared configuration for all Paxton Bank Prospecting demo components."""
import os

PROJECT_ID = os.environ.get("CP_PROJECT", "kairos-504105")
REGION = os.environ.get("CP_REGION", "us-east4")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "1")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", PROJECT_ID)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
# LIVE avatar models exist ONLY in us-central1 (verified 2026-07-31) — the
# live session location is pinned separately from the infra region.
LIVE_LOCATION = os.environ.get("CP_LIVE_LOCATION", "us-central1")
PREFIX = "paxtonprospect"

# Data stores
BQ_CORE = f"{PROJECT_ID}.paxtonprospect_core"
BQ_AUDIT = f"{PROJECT_ID}.paxtonprospect_audit"
GCS_BUCKET = f"{PROJECT_ID}-paxtonprospect"
FIRESTORE_DB = "paxtonprospect"
SQL_INSTANCE = f"{PROJECT_ID}:us-east4:paxtonprospect-sql"

# Models (verified in-project 2026-07-22; see plan/phase0-gcp.md)
MODEL_LIVE = os.environ.get("CP_MODEL_LIVE", "gemini-3.5-flash-live-preview")
# 2026-08-08: migrated 3.1 -> 3.5 live (probed in-project: avatar+voice+tools OK;
# proactivity/affective_dialog are NOT supported on 3.5 — never enable them).
MODEL_LIVE_PREV = "gemini-3.1-flash-live-preview-04-2026"   # instant rollback via CP_MODEL_LIVE
# second avatar-capable live model: Ava must not share the client model
# SECOND avatar-capable model for the CLIENT seat: two sustained avatar
# sessions on ONE live model starve each other (a2/a3-proven; re-observed
# 2026-07-31 as Ava-stream jitter in who=both) — the prospect rides this.
MODEL_LIVE_ALT = "gemini-3.5-flash-lite-live-preview"
MODEL_WORKER = "gemini-3.6-flash"
MODEL_FAST = "gemini-3.5-flash-lite"
MODEL_DEEP = "gemini-3.1-pro-preview"
MODEL_EMBED = "gemini-embedding-2"
TTS_VOICE = "en-US-Chirp3-HD-Charon"       # Companion default voice
TTS_VOICE_ALT = "en-US-Chirp3-HD-Kore"     # second persona voice

# Personas (user pick 2026-07-31): TARGET is custom faces (Portrait A / Portrait E
# from avatar_samples/). Custom avatars are a separate Google private-preview
# allowlist this project is NOT on yet — live_manager tries the custom ref on
# every fresh process and falls back to the preset on the 1007 allowlist error,
# so the day Google flips the switch the custom faces go live with zero changes.
AVA_AVATAR = "Kira"                        # preset fallback face (visibly new today)
AVA_VOICE = "Erinome"                      # user pick — active today
# Avatar output bitrate caps (2026-08-19 fluidity pass): the ONLY output-media
# knobs the Live API exposes (AvatarConfig.video/audio_bitrate_bps — probed
# accepted+effective on gemini-3.5-flash-live-preview). Smaller chunks = less
# queueing/jitter across every hop. 0 disables the cap.
AVA_VIDEO_BITRATE = int(os.environ.get("CP_AVA_VBR", "3000000"))
AVA_AUDIO_BITRATE = int(os.environ.get("CP_AVA_ABR", "96000"))
# The client is ALWAYS a live human in Zoom (user directive 2026-08-02).
# These two remain ONLY for the g2 regression harness's in-app simulator:
CLIENT_AVATAR = "Sam"                      # TEST HARNESS ONLY
CLIENT_VOICE = "Iapetus"                   # TEST HARNESS ONLY
AVATAR_CUSTOM_REFS = {}                    # use preset Kira avatar directly (no 1007 delay)

# Zoom SDK bot hosting: one warm Cloud Run service per avatar SEAT (a bot is
# one Zoom participant; two seats never share a container — the SDK audio
# engine owns the instance's single pulseaudio). Same image on both.
AVABOT_SVC = "https://paxtonprospect-avabot-svc-229567506693.us-east4.run.app"
# (clientbot-svc decommissioned 2026-08-02 — the client is a live human;
#  to resurrect a second seat, see HANDOVER.md "two-seat architecture")

# KAIROS MERGE (2026-07-31): the fleet is trimmed to the agents the Kairos UI
# surfaces (user directive — "stick to what Karletha had built; we will add
# other agents as we see fit"). Parked agents are gated, not deleted: add the
# name back here to re-enable. Sentinel (guardrails) and the live sessions are
# core infrastructure, not gated by this list.
ACTIVE_AGENTS = {
    "Pulse",       # client sentiment  -> Kairos sentiment panel
    "Panel",       # insight mining    -> Kairos capability canvas
    "Archivist",   # meeting memory    -> context for Pulse/Panel + handoff packet
    "Scorer",      # OPPORTUNITY ANALYSIS (re-enabled 2026-08-11): 5-dim lead
                   # viability -> handoff packet "vetted lead" score
    # parked: "Scripter" (teleprompter), "OptionSmith" (client reply options),
    #         "AvaPlanner" (Ava line suggestions), "Coach" (objection coaching)
}


def agent_on(name: str) -> bool:
    return name in ACTIVE_AGENTS


# GUARDRAILS FEATURE (ported from Karletha's Policy Panel 2026-08-07).
# Kill switch: False stops corpus retrieval/injection entirely (the Policy
# Panel still renders, marked disarmed) — no rollback needed to disable.
GROUNDING_ON = True

# Static banker identity for the testing phase (user directive 2026-08-11):
# the avatar always refers to the banker as Alex.
BANKER_NAME_STATIC = os.environ.get("CP_BANKER_NAME", "Alex Drummond")

# AUTONOMOUS visual presenting on the prospect canvas (Paxton Bank primary ask
# 2026-08-11). False = banker-only presenting; library content only either way.
AUTO_PRESENT_ON = os.environ.get("CP_AUTO_PRESENT", "1") != "0"

# AGENT RUNTIME (2026-08-08): the text fleet runs on ADK 2.x (LlmAgent+Runner)
# for native agent tracing/state. False = legacy direct google-genai path
# (instant rollback, no redeploy of code — flip and redeploy the service).
ADK_AGENTS = os.environ.get("CP_ADK_AGENTS", "1") != "0"
STT_MODEL = "chirp_3"

SERVICE_VERSION = os.environ.get("K_REVISION", "local")

# Deployment variant: "webv2" (in-app conferencing with Banker + Prospect + Kira, default) | "meetv3" (Google Meet companion) | "zoomv1" (Zoom SDK)
VARIANT = os.environ.get("CP_VARIANT", "webv2")
