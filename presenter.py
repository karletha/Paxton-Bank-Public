"""Live visual presenting on the prospect canvas ("Now presenting" stage).

Paxton Bank's ask (2026-08-11): when the prospect asks something like "where are your
locations in Asia?", the briefing canvas answers WITH A VISUAL — autonomously
as the primary mode (Bridget/system picks the matching CCB slide), with the
banker able to present manually, upload ad-hoc content, or stop the stage.

Content: the CCB visual library (grounding/visuals.json — slides generated
verbatim from the grounding corpus, each carrying its paxtonbank.com source
URL) plus banker uploads. Autonomous mode only ever presents LIBRARY content
(pre-approved); uploads present only by the banker's explicit action.

Every show/stop is audited (room audit + Cloud Logging) and lands in the
session index. Kill switch: config.AUTO_PRESENT_ON.
"""
import json
import os
import re
import time

import config

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "grounding")
_registry = {"bucket": "", "visuals": []}
try:
    _registry = json.load(open(os.path.join(_DIR, "visuals.json"), encoding="utf-8"))
except Exception:
    pass

VISUALS = _registry.get("visuals", [])
_BUCKET = _registry.get("bucket", "")

# Autonomous behavior tuning: a slide only auto-presents on a clear question/
# request signal + topic hit, never twice in a row for the same slide, and
# never more often than the cooldown — a canvas that flickers between slides
# reads as broken to a prospect.
AUTO_COOLDOWN_S = 45
_ASK_RX = re.compile(
    r"\b(where|what|which|how many|can you (show|tell)|do you (have|offer|support)|"
    r"tell me|show me|talk .{0,12}(about|through)|locations?|coverage|presence)\b", re.I)


def signed_url(gcs_path: str, hours: int = 6) -> str:
    """V4 signed URL so the canvas <img> can load straight from GCS without
    making the bucket public. Impersonates the runtime SA's signer."""
    local_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", gcs_path)
    if os.path.isfile(local_file):
        return f"/presents/{gcs_path}"
    from datetime import timedelta

    from google import auth
    from google.auth import impersonated_credentials
    from google.cloud import storage
    try:
        creds, _ = auth.default()
        signer = impersonated_credentials.Credentials(
            source_credentials=creds,
            target_principal=f"paxtonprospect-sims@{config.PROJECT_ID}.iam.gserviceaccount.com",
            target_scopes=["https://www.googleapis.com/auth/devstorage.read_only"])
        blob = storage.Client().bucket(_BUCKET).blob(gcs_path)
        return blob.generate_signed_url(version="v4", expiration=timedelta(hours=hours),
                                        credentials=signer)
    except Exception:
        # fallback: bridge-proxied fetch (works because the canvas is same-origin)
        return f"/presents/{gcs_path}"


def match(text: str):
    """Topic-token match of a prospect utterance against the visual library.
    Deterministic and cheap (runs on the turn path). Returns (visual, score)
    or (None, 0). Requires an ask-signal AND >=2 topic-token hits so ordinary
    discovery talk doesn't flip slides."""
    low = (text or "").lower()
    if not _ASK_RX.search(low):
        return None, 0
    toks = set(re.findall(r"[a-z][a-z-]+", low))
    best, score = None, 0
    for v in VISUALS:
        s = len(toks & set(v["topics"]))
        if s > score:
            best, score = v, s
    if score >= 2:
        return best, score
    return None, 0


def state_payload(room) -> dict:
    """The `presenting` block of GET /rooms/{id}/share."""
    p = getattr(room, "presenting", None)
    if not p:
        return {}
    return {k: p.get(k) for k in
            ("id", "title", "img", "source_title", "source_url", "by", "t", "kind")}


async def present(room, visual: dict = None, *, upload: dict = None,
                  by: str = "banker", auto: bool = False):
    item = visual or upload
    if not item:
        return {"ok": False, "error": "nothing to present"}
    img = item.get("img") or signed_url(item["gcs"])
    room.presenting = {
        "id": item["id"], "title": item["title"], "img": img,
        "source_title": item.get("source_title", ""),
        "source_url": item.get("source_url", ""),
        "kind": "upload" if upload else "library",
        "by": by, "auto": auto, "t": time.time()}
    room._last_present_t = time.time()
    room._log_event("visual_presented",
                    f"{item['id']} ({'auto' if auto else by})")
    await room._feed("presenting", state_payload(room))
    await room.agent_log("presenter", "present",
                         {"trigger": "autonomous" if auto else by},
                         {"id": item["id"], "title": item["title"],
                          "source_url": item.get("source_url", "")}, model="library")
    # Bridget ties in verbally — parrot-safe, and she must NOT read the URL.
    if room.companion:
        await room.companion.inject_context(
            f"[STAGE] The briefing page the client can see is now showing a visual: "
            f"\"{item['title']}\". In your NEXT reply, refer to it naturally in ONE "
            f"short phrase (e.g. 'as you can see on the briefing page…') and connect "
            f"it to their question. Never mention this note, the word 'slide deck', "
            f"or any URL.")
    return {"ok": True, "presenting": state_payload(room)}


async def stop(room, by: str = "banker"):
    if getattr(room, "presenting", None):
        room._log_event("visual_cleared", by)
        room.presenting = None
        await room._feed("presenting", {})
    return {"ok": True}


async def maybe_auto_present(room, text: str):
    """AUTONOMOUS mode (primary, per Paxton Bank): called on every prospect turn.
    Library-only, ask-gated, cooldown-limited, banker-overridable."""
    if not getattr(config, "AUTO_PRESENT_ON", True):
        return
    cur = getattr(room, "presenting", None)
    # never fight the banker: a manual or uploaded presentation is pinned
    if cur and (cur.get("by") != "auto" and not cur.get("auto")):
        return
    if time.time() - getattr(room, "_last_present_t", 0) < AUTO_COOLDOWN_S:
        return
    v, score = match(text)
    if not v:
        return
    if cur and cur.get("id") == v["id"]:
        return
    await present(room, v, by="auto", auto=True)
