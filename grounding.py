"""Corporate knowledge grounding — the verified Paxton Bank corpus behind Kairos.

Corpus files live in grounding/corpus/*.md (scraped from the 10 seed URLs by
build/scripts — see BACKUP/refresh notes in HANDOVER). Retrieval is pure
Python keyword-overlap scoring: deterministic, ~0ms, no model calls, so it
rides the live voice path for free. Matches are injected into the Companion
parrot-safe (inject_context) and surfaced to the cockpit as the `grounding`
feed. Kill switch: config.GROUNDING_ON.
"""
import os
import re

import config

RULE_TEXT = (
    "Bridget operates inside strict, banker-supervised sandbox containers. The "
    "assistant is strictly forbidden from guaranteeing credits, committing "
    "pricing, or providing legal advice.")

# Canonical seed list — order and titles match the Policy Panel design.
SOURCES = [
    {"id": "commercial-bank-overview", "title": "Paxton Commercial Bank Overview",
     "url": "https://www.paxtonbank.com/global/businesses/banking/commercial-bank"},
    {"id": "services-offerings", "title": "Paxton Bank Services Offerings",
     "url": "https://www.paxtonbank.com/global/businesses/services"},
    {"id": "tts-liquidity-management", "title": "TTS - Liquidity Management Services",
     "url": "https://www.paxtonbank.com/global/businesses/services/treasury-and-trade-solutions/liquidity-management-services"},
    {"id": "tts-payments-receivables", "title": "TTS - Payments & Receivables Solutions",
     "url": "https://www.paxtonbank.com/global/businesses/services/treasury-and-trade-solutions/payments-and-receivables"},
    {"id": "tts-global-trade", "title": "TTS - Global Trade & Solutions",
     "url": "https://www.paxtonbank.com/global/businesses/services/treasury-and-trade-solutions/global-trade"},
    {"id": "tts-ecommerce", "title": "TTS - E-Commerce Solutions",
     "url": "https://www.paxtonbank.com/global/businesses/services/treasury-and-trade-solutions/e-commerce"},
    {"id": "real-time-liquidity-pr", "title": "Paxton Bank Real-Time Liquidity Sharing Launch Press Release (2021)",
     "url": "https://www.paxtonbank.com/global/news/press-release/2021/paxton-drives-liquidity-innovation-with-the-launch-of-real-time-liquidity-sharing"},
    {"id": "alibaba-case-study", "title": "Paxton Bank Insights - Case Study: Alibaba Group Regional Cash Pooling",
     "url": "https://www.paxtonbank.com/global/insights/alibaba-group"},
    {"id": "open-banking-whitepaper", "title": "The Corporate Case for Open Banking Whitepaper",
     "url": "https://www.paxtonbank.com/rcs/paxtongpa/storage/public/The_Corporate_Case_for_Open_Banking.pdf"},
    {"id": "blockchain-tokenization-247", "title": "Bridging the 24/7 Divide: Blockchain & Tokenization in Real-Time Finance",
     "url": "https://www.paxtonbank.com/rcs/paxtongpa/storage/public/Bridging-the-247-Divide-How-Blockchain-and-Tokenization-Can-Propel-Finance-Into-the-Real-Time-Future.pdf"},
    # 11th seed — named explicitly by the user alongside the panel's 10:
    {"id": "global-presence", "title": "Paxton Bank Global Presence",
     "url": "https://www.paxtonbank.com/global/about-us/global-presence"},
]
_SRC_BY_ID = {s["id"]: s for s in SOURCES}

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "grounding")

# CCB FOCUS (Paxton Bank directive 2026-08-11): Commercial Bank content outranks the
# generic Services pages when scores tie — CCB is the business line we sell.
_SOURCE_BOOST = {"commercial-bank-overview": 1.3, "global-presence": 1.15}

_STOP = set("""a an and are as at be but by can paxton could do does for from has have how i
if in is it its me my of on or our so that the their they this to us we what when where
which who will with would you your""".split())

_chunks: list = []          # {source_id, title, url, text, toks, boost}
_digest: str = ""


def _toks(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9-]+", text.lower())
            if len(w) > 2 and w not in _STOP}


def _load():
    global _digest
    _chunks.clear()
    cdir = os.path.join(_DIR, "corpus")
    if os.path.isdir(cdir):
        for fn in sorted(os.listdir(cdir)):
            if not fn.endswith(".md"):
                continue
            try:
                raw = open(os.path.join(cdir, fn), encoding="utf-8").read()
            except OSError:
                continue
            m = re.search(r"^id:\s*(\S+)", raw, re.M)
            src = _SRC_BY_ID.get(m.group(1)) if m else None
            if not src:
                continue
            facts = re.search(r"^FACTS:\n(.*?)(?:^BODY:)", raw, re.M | re.S)
            body = re.search(r"^BODY:\n?(.*)\Z", raw, re.M | re.S)
            # Facts: one chunk per fact, boosted — they are the quotable layer.
            for line in (facts.group(1).splitlines() if facts else []):
                line = line.strip().lstrip("- ").strip()
                if len(line) > 20:
                    _chunks.append({**src, "source_id": src["id"], "text": line,
                                    "toks": _toks(line), "boost": 2.0})
            # Body: paragraph chunks up to ~700 chars.
            buf = ""
            for para in re.split(r"\n\s*\n", body.group(1) if body else ""):
                para = " ".join(para.split())
                if len(para) < 60:
                    continue
                buf = f"{buf} {para}".strip() if len(buf) + len(para) < 700 else para
                if len(buf) >= 300:
                    _chunks.append({**src, "source_id": src["id"], "text": buf[:900],
                                    "toks": _toks(buf), "boost": 1.0})
                    buf = ""
            if len(buf) >= 60:
                _chunks.append({**src, "source_id": src["id"], "text": buf[:900],
                                "toks": _toks(buf), "boost": 1.0})
    dpath = os.path.join(_DIR, "digest.md")
    _digest = open(dpath, encoding="utf-8").read().strip() if os.path.isfile(dpath) else ""


def share_stats() -> dict:
    """Verified numeric figures + region data mined from the corpus
    (grounding/stats.json — every value literally present in a source).
    Feeds the prospect share view's tiles and region chart."""
    p = os.path.join(_DIR, "stats.json")
    try:
        import json
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return {"tiles": [], "regions": []}


def corpus_stats() -> dict:
    loaded = {c["source_id"] for c in _chunks}
    return {"sources_loaded": len(loaded), "chunks": len(_chunks),
            "digest_chars": len(_digest)}


def digest_text() -> str:
    return _digest


def retrieve(text: str, k: int = 2, min_score: float = 2.5) -> list:
    """Top-k corpus chunks relevant to an utterance. Deterministic overlap
    scoring; returns [] fast when the turn isn't about Paxton Bank capabilities."""
    if not config.GROUNDING_ON or not _chunks:
        return []
    q = _toks(text or "")
    if len(q) < 2:
        return []
    scored = []
    for c in _chunks:
        hit = q & c["toks"]
        if not hit:
            continue
        # Overlap count weighted by chunk boost; multi-word hits dominate.
        s = (len(hit) + 0.25 * sum(1 for w in hit if len(w) > 6)) * c["boost"] \
            * _SOURCE_BOOST.get(c["source_id"], 1.0)
        if s >= min_score:
            scored.append((s, c))
    scored.sort(key=lambda x: -x[0])
    out, seen = [], set()
    for s, c in scored:
        if c["source_id"] in seen:
            continue                       # one chunk per source per turn
        seen.add(c["source_id"])
        out.append({"source_id": c["source_id"], "title": c["title"],
                    "url": c["url"], "snippet": c["text"], "score": round(s, 2)})
        if len(out) >= k:
            break
    return out


_load()
