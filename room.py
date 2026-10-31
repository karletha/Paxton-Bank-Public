"""Room — orchestrates one meeting: the Companion, optional Prospect Simulator,
human participants (browser), floor control, Sentinel wiring, and live feeds to
Firestore for the Console. Floor token prevents Companion and Simulator talking
over each other (documented bot-to-bot fix)."""
import asyncio
import base64
import concurrent.futures
import json
import time
from collections import deque

from opentelemetry import trace

import config
import personas
import sentinel
import tools
from telemetry import glog
from live_manager import LiveSession

tracer = trace.get_tracer("paxtonprospect-bridge")

# Single worker -> feed/CRM writes stay ordered; done-callback consumes
# exceptions so failed writes never surface as GC warnings.
_io_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1,
                                                 thread_name_prefix="room-io")


def _consume(fut):
    try:
        fut.exception()
    except Exception:
        pass


def _clip(s, n: int = 2000) -> str:
    """Truncate arbitrary input/output to a reviewable length for the durable
    agent-interaction log (keeps Firestore docs small, feeds stay readable)."""
    s = s if isinstance(s, str) else str(s)
    return s if len(s) <= n else s[:n] + "…[clip]"


class Room:
    def __init__(self, room_id: str, company: str = "Meridian Components Inc.",
                 lead_id: str = "HERO-01", modality: str = "avatar"):
        self.room_id = room_id
        self.company = company
        self.lead_id = lead_id
        self.clients: set = set()          # browser websockets
        self.companion: LiveSession | None = None
        self.simulator: LiveSession | None = None
        self.floor = "open"                 # open | companion | simulator | human
        self.sim_disposition = "neutral"
        self.j4_armed = True                # Sentinel active
        self.audit: list[dict] = []
        self._lock = asyncio.Lock()
        # G2 worker state
        self.sentiment_series: list[dict] = []
        self.insight_cards: list[dict] = []
        self.email_draft = ""     # client follow-up, drafted live by Archivist
        self.score_versions: list[dict] = []
        self.enrichment: dict = {}
        self.corrections = 0
        self._prospect_turns = 0
        self.approvals: dict = {}
        self.objections: list[dict] = []
        self.modality = modality            # avatar | voice | text (avatar = voice + tile)
        self.direction = "auto"             # auto | suggest | approve | beats
        self.current_speaker = ""           # J3a: active human speaker name
        self.participants: dict = {}        # ws -> {"who","name"}
        # exchange state must exist before _wire_exchange (director endpoints
        # can be hit before /exchange)
        self._turns = 0
        self._scene_t0 = time.monotonic()
        self._prospect_farewelled = False
        self._resume_after_reply = False
        self._live_utterance = ""
        self._tripped_utterance = False
        self._watchdog: asyncio.Task | None = None
        self.pace_s = 2.2                   # breathing room between AI turns
        self._mic_buf = bytearray()         # banker/colleague utterance buffer
        self._speech_who = "banker"
        self._speech_name = ""
        self._relay_gen = 0                 # bumped on human input -> cancels pending relays
        self._last_turn = {}                # speaker -> last fed text (dedupe)
        self.convo: list = []               # [(who, text)] real transcript for teleprompter
        self.client_avatar = "Paul"   # photoreal exec preset (Ben/Kai/Leo are cartoons)
        self._avatar_buf = bytearray()      # accumulates client turn's fMP4
        self._comp_buf = bytearray()        # accumulates companion turn's fMP4
        self.avatar_turn_mp4 = b""          # last complete client turn (Recall stage)
        self.avatar_turn_id = 0
        self.companion_turn_mp4 = b""       # last complete Ava turn (her own stage)
        self.companion_turn_id = 0
        # continuous stage stream -> GCS segments (~1.2MB), one per flush.
        # Off by default: only rooms with a Recall stage (Zoom) pay for GCS.
        self.stage_stream_on = False
        self._seg_buf = {"companion": bytearray(), "prospect": bytearray()}
        self._seg_n = {"companion": 0, "prospect": 0}
        self._relay_q = {}       # who -> asyncio.Queue feeding the push relay
        self._relay_task = {}
        self.solo = False        # consent pre-granted + no greeting ritual
        self.mute_fleet = False   # ONLY pure who=ava isolation mutes the fleet
        self._pcm_pending = bytearray()   # audio buffered while her session reconnects
        self._spk_seen = {}       # name -> last-heard monotonic (active-speaker window)
        self._spk_marker = ""     # last '[speaking: ...]' marker injected to Ava
        self.banker_name = config.BANKER_NAME_STATIC   # default; cockpit POST /banker_name overrides with the real join name
        # ears pipeline telemetry: where does meeting audio fall off?
        self.roster: list = []      # Zoom display names actually HEARD this meeting
        self.ears = {"in_pkts": 0, "in_bytes": 0, "drop_ai": 0, "drop_gate": 0,
                     "sent_bytes": 0, "send_err": 0, "last_err": "",
                     "last_in": 0.0, "last_sent": 0.0,
                     "heard": "", "last_heard": 0.0}
        self._heard_buf = ""
        self._heard_flush = None
        self.live_gate_pending = False      # armed: connect her only when a human is really in the meeting
        self.web_conf_on = False            # webv2/meetv3: browsers ARE the meeting; mic PCM goes to her native ears
        self.pending_kickoff = ""           # (legacy) greeting text for Ava's lane
        self.greeted = False                # Ava's greeting card was delivered
        self.ava_thanked = False            # consent-confirmation card delivered
        # asymmetric speech: Ava = LIVE avatar session (the product); client =
        # rendered clips (in real life he's a human). ONE avatar slot exists —
        # this lock serializes Ava's live turns and client render bursts.
        self._slot_lock = asyncio.Lock()
        self.clip_cache: dict = {}          # norm(text) -> {mp4, tx, dur_s}
        self._render_q: list = []           # pending client lines (priority order)
        self._render_task: asyncio.Task | None = None
        self._render_job: asyncio.Task | None = None
        self._lane_gen = 0                  # bumped per lane regen; stale renders drop
        self.idle_clips: dict = {}          # who -> genuine idle-loop fMP4
        self._heard_refresh: asyncio.Task | None = None
        self._lane_state: dict = {}         # last script/client_options/ava_options
        self.recall_key = ""                # for in-meeting chat posts
        self.bot_by_who: dict = {}          # who -> recall bot id
        self.recall_bot_ids: list = []
        self.companion_mode = "video"
        self.hold_floor = False             # mic live: AI answers once, then waits for human
        # CONSENT IS PROSPECT-ONLY (user directive 2026-08-11): the banker never
        # consents — pre-granted so the gate completes on the client's yes alone.
        self.consents = {"prospect": False, "banker": True}
        self._last_turn_t = time.monotonic()
        self._stall_task: asyncio.Task | None = None
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    # ---------- browser fan-out ----------
    # Per-client sender pipes: broadcast() must NEVER await a browser socket.
    # The old serial send loop meant ONE slow client back-pressured whoever
    # called broadcast — including Bridget's live receive loop, freezing her
    # mid-turn (the sanrepo-proven root cause of the webv2 jitter). Each client
    # gets unbounded ctrl/media queues drained by its own task: control frames
    # flush first (a caption/widget never waits behind buffered video), media
    # stays strictly FIFO so the fMP4 byte stream remains contiguous, and cam
    # tiles are newest-wins (stale JPEGs are worthless). A send timeout or an
    # oversized backlog marks the client dead-slow: shed it, never the room.
    _SEND_TIMEOUT_S = 15
    _MAX_BACKLOG_BYTES = 24_000_000

    def _pipe_for(self, ws):
        pipes = getattr(self, "_pipes", None)
        if pipes is None:
            pipes = self._pipes = {}
        p = pipes.get(ws)
        if p is None:
            p = pipes[ws] = {"ctrl": deque(), "media": deque(), "cam": {},
                             "bytes": 0, "wake": asyncio.Event(), "dead": False}
            p["task"] = asyncio.create_task(self._pipe_drain(ws, p))
        return p

    def _drop_pipe(self, ws):
        p = getattr(self, "_pipes", {}).pop(ws, None)
        if p:
            p["dead"] = True
            p["wake"].set()
        self.clients.discard(ws)

    def _shed_client(self, ws):
        """Dead-slow/broken socket: drop the pipe AND close the websocket, so
        the browser's onclose fires and its refresh/reconnect UX engages —
        otherwise it sits 'connected' forever receiving nothing while its mic
        still feeds the room (review finding, 2026-08-19)."""
        self._drop_pipe(ws)
        async def _close():
            try:
                await ws.close(code=1013)
            except Exception:
                pass
        try:
            asyncio.create_task(_close())
        except Exception:
            pass

    async def _pipe_drain(self, ws, p):
        failed = False
        try:
            while not p["dead"]:
                sent = False
                while p["ctrl"]:
                    await asyncio.wait_for(ws.send_text(p["ctrl"].popleft()),
                                           self._SEND_TIMEOUT_S)
                    sent = True
                for k in list(p["cam"]):
                    payload = p["cam"].pop(k, None)
                    if payload:
                        await asyncio.wait_for(ws.send_text(payload),
                                               self._SEND_TIMEOUT_S)
                        sent = True
                if p["media"]:
                    _, payload = p["media"].popleft()
                    p["bytes"] -= len(payload)
                    await asyncio.wait_for(ws.send_text(payload),
                                           self._SEND_TIMEOUT_S)
                    sent = True
                if not sent:
                    p["wake"].clear()
                    if p["ctrl"] or p["media"] or p["cam"]:
                        continue          # raced an enqueue — drain it
                    if ws not in self.clients or p["dead"]:
                        break
                    await p["wake"].wait()
        except Exception:
            failed = True
        finally:
            if failed:
                self._shed_client(ws)     # send died/timed out: real shed
            else:
                self._drop_pipe(ws)       # normal detach: socket owner closes it

    async def broadcast(self, msg: dict):
        if not self.clients:
            return
        # media-epoch stamping (avatar-hybrid-vad pattern): every payload
        # carries the room's interrupt epoch; clients drop companion media
        # whose epoch predates their high-water mark. The client-side 400ms
        # window is only the fallback for unstamped frames.
        msg = {**msg, "_epoch": getattr(self, "_media_epoch", 0)}
        payload = json.dumps(msg)
        t = msg.get("type")
        is_cam = t == "peer_cam"
        # video_reset/video_end are stream-boundary markers: they must stay
        # in-band with the fMP4 chunks or a client rebuilds its pipe while the
        # old stream's tail is still queued (sanrepo's reorder-corruption bug).
        # flush_audio deliberately jumps the queue (ctrl) — barge-in wants
        # speed — but then it must also PURGE queued audio, or the stale AI
        # speech it refers to plays after the flush.
        is_media = t in ("video", "audio", "video_reset", "video_end")
        for ws in list(self.clients):
            p = self._pipe_for(ws)
            if p["dead"]:
                continue
            if t == "flush_audio" and p["media"]:
                kept = deque(x for x in p["media"] if x[0] != "audio")
                p["media"] = kept
                p["bytes"] = sum(len(x[1]) for x in kept)
            if is_cam:
                p["cam"][msg.get("name") or "cam"] = payload
            elif is_media:
                p["media"].append((t, payload))
                p["bytes"] += len(payload)
                if p["bytes"] > self._MAX_BACKLOG_BYTES:
                    self._shed_client(ws)
                    continue
            else:
                p["ctrl"].append(payload)
            p["wake"].set()

    async def perf(self, comp: str, ms: float, phase: str = "", meta: dict = None):
        """One component execution span — the atom of the performance view.
        Rings on the room, streams to the live UI (Gantt + diagram light-up),
        mirrors to Firestore for post-hoc analysis."""
        rec = {"comp": comp, "ms": round(ms, 1), "phase": phase,
               "t": time.time(), "meta": meta or {}}
        ring = getattr(self, "perf_ring", None)
        if ring is None:
            ring = self.perf_ring = []
        ring.append(rec)
        if len(ring) > 400:
            del ring[:120]
        try:
            await self.broadcast({"type": "perf", **rec})
        except Exception:
            pass
        try:
            asyncio.get_running_loop().run_in_executor(
                _io_pool, tools.perf_write, self.room_id, rec).add_done_callback(_consume)
        except Exception:
            pass
        glog(f"perf {comp} {phase} {rec['ms']}ms", severity="DEBUG", log_type="perf",
             room=self.room_id, agent=comp, phase=phase, ms=rec["ms"],
             ttft_ms=(meta or {}).get("ttft_ms"))

    async def agent_log(self, agent: str, phase: str, inp, out,
                        model: str = "", meta: dict = None):
        """Durable, reviewable record of ONE agent interaction — the exact input
        the agent got, the output it produced, its model, phase and timestamp.
        Additive to perf() (which is latency-only): this is the CONTENT trail a
        reviewer reads after a session. Rings on the room (cap ~500) and mirrors
        to Firestore 'agent_logs' OFF the audio event loop — never blocks the
        audio path, swallows all write errors."""
        rec = {"room": self.room_id, "agent": agent, "phase": phase,
               "input": _clip(inp), "output": _clip(out),
               "model": model or "", "t": time.time(), "meta": meta or {}}
        ring = getattr(self, "agent_logs", None)
        if ring is None:
            ring = self.agent_logs = []
        ring.append(rec)
        if len(ring) > 500:
            del ring[:150]
        try:
            asyncio.get_running_loop().run_in_executor(
                _io_pool, tools.agent_log_write, self.room_id, rec
            ).add_done_callback(_consume)
        except Exception:
            pass
        glog(f"agent {agent} {phase}", log_type="agent_interaction",
             room=self.room_id, agent=agent, phase=phase, model=rec["model"],
             input=rec["input"], output=rec["output"],
             ttft_ms=(meta or {}).get("ttft_ms"))

    async def _log_companion_turn(self, last: str):
        """Lightweight agent_log for a completed Kairos LIVE avatar turn — the
        heard context in, her spoken transcript out. Best-effort; never raises."""
        if not last:
            return
        try:
            await self._check_next_steps("Bridget", last)
        except Exception:
            pass
        try:
            heard = ""
            try:
                heard = (self.ears.get("heard") or "").strip()
            except Exception:
                heard = ""
            if not heard:
                heard = next((t for w, t in reversed(self.convo)
                              if w != "companion"), "")
            await self.agent_log(
                "AvaLive", "speak", heard or "[live audio]", last,
                model=getattr(self.companion, "model", "") if self.companion else "",
                meta={"ttft_ms": getattr(self.companion, "last_ttft_ms", None)
                      if self.companion else None})
        except Exception:
            pass

    async def _feed(self, kind: str, payload: dict):
        if kind in ("script", "client_options", "ava_options"):
            # lanes must survive WS (re)connects — start_demo seeds them BEFORE
            # the browser attaches, so hello replays this state
            self._lane_state[kind] = payload
        await self.broadcast({"type": "feed", "kind": kind, "payload": payload})
        try:
            # Firestore write is network I/O — keep it OFF the audio event loop
            asyncio.get_running_loop().run_in_executor(
                _io_pool, tools.feed_write, self.room_id, kind, payload
            ).add_done_callback(_consume)
        except Exception:
            pass

    # ---------- companion lifecycle ----------
    async def start_companion(self, mode: str = "video", avatar: str = "Vera",
                              model: str = ""):
        """Companion = the banker's AI teammate. mode 'video' -> female avatar
        (Vera); mode 'audio' -> female voice. NOTE: tools don't fire in avatar
        mode, so materials/handoff (J7/J5) run in the workers layer regardless."""
        sys = (personas.COMPANION_SYS
               + f"\n\nThe prospect company is {self.company}."
               + f"\n[BANKER IDENTITY] The Paxton Bank banker who arranged this meeting is "
                 f"{self.banker_name}. Confirm who is actually speaking from their "
                 f"[speaking: NAME] markers — if a different colleague speaks or takes "
                 f"over, address that person by THEIR marker name.")
        self.companion_mode = mode
        if mode == "video":
            self.companion = LiveSession(
                name="Companion", model=model or self._live_model(),
                system_instruction=sys, voice_name=config.AVA_VOICE,   # user pick 2026-07-31
                room_id=self.room_id, avatar_name=avatar,
                on_video=self._companion_video,
                on_output_tx=lambda t: self._companion_tx(t),
                on_turn_complete=self._companion_turn_done)
        else:
            self.companion = LiveSession(
                name="Companion", model=self._live_model(), system_instruction=sys,
                modality="AUDIO", tools=personas.companion_tools(), room_id=self.room_id,
                voice_name=config.AVA_VOICE,            # user pick
                on_audio=self._companion_audio,
                on_output_tx=lambda t: self._companion_tx(t),
                on_input_tx=self._human_input_tx,
                on_interrupted=self._on_interrupted,
                on_tool_call=self._companion_tool,
                on_turn_complete=self._companion_turn_done)
        await self.companion.start()
        await self._feed("system", {"event": "companion_ready", "mode": mode})
        asyncio.create_task(self._inject_premeeting_brief())

    _SEG_FIRST = 120_000     # tiny first segment (init+first frames) = fast paint
    _SEG_FLUSH = 320_000     # steady ~0.3MB segments -> lower streaming latency

    def _stage_stream(self, who: str, mp4: bytes, flush: bool = False):
        """Mirror the avatar's continuous fMP4 stream to GCS+Firestore in
        segments; the public stage pages (Recall bots) append them to one
        MediaSource. First segment is small so the first frame reaches Zoom
        ~1-2s sooner; steady segments stay small for smooth realtime flow."""
        if not self.stage_stream_on:
            return
        if mp4:
            self._relay_send(who, "seg", mp4)   # push path: chunk-level, ~30ms
        buf = self._seg_buf[who]
        buf.extend(mp4)
        thresh = self._SEG_FIRST if self._seg_n[who] == 0 else self._SEG_FLUSH
        if len(buf) >= thresh or (flush and buf):
            data = bytes(buf)
            self._seg_buf[who] = bytearray()
            self._seg_n[who] += 1
            n = self._seg_n[who]
            # hot path (Firestore push -> ops RAM) + durable fallback (GCS)
            asyncio.get_running_loop().run_in_executor(
                _io_pool, tools.stage_push_live, self.room_id, who, n, data
            ).add_done_callback(_consume)
            asyncio.get_running_loop().run_in_executor(
                _io_pool, tools.stage_publish_seg, self.room_id, who,
                n, data).add_done_callback(_consume)

    # ---- Pulse relay publisher (push path; GCS/poll stays as fallback) ----
    # Every raw avatar chunk is ALSO pushed over one outbound WSS to the public
    # token-gated relay the instant it arrives (~10-20KB granularity), so the
    # stage pages get bytes ~30ms after Gemini emits them instead of waiting on
    # 320KB segmentation + 250ms poll + GCS fetch. Init markers ride the same
    # pipe so the relay ring always replays from a clean ftyp+moov boundary.
    async def session_index(self) -> dict:
        """One durable record per meeting so any past session can be pulled up
        whole later: who was in it, what model, how many turns/interrupts, and
        the exact log/trace filters that reconstruct it. Written to Firestore
        'sessions' on close AND mirrored to Cloud Logging (log_type=session)."""
        return {
            "room": self.room_id,
            "company": getattr(self, "company", ""),
            "banker_name": getattr(self, "banker_name", ""),
            "zoom_meeting": getattr(self, "zoom_meeting", ""),
            "participants": list(getattr(self, "roster", [])),
            "ended_at": time.time(),
            "model_live": config.MODEL_LIVE,
            "model_worker": config.MODEL_WORKER,
            "adk_agents": bool(getattr(config, "ADK_AGENTS", False)),
            "turns": len(self.convo),
            "prospect_turns": getattr(self, "_prospect_turns", 0),
            "barge_ins": sum(1 for a in getattr(self, "audit", [])
                             if a.get("event") == "barge_in"),
            "insight_cards": len(getattr(self, "insight_cards", [])),
            "audit": list(getattr(self, "audit", []))[-40:],
            "replay": {
                "agentlog_api": f"/rooms/{self.room_id}/agentlog?n=500",
                "cloud_logging": f'jsonPayload.room="{self.room_id}"',
                "cloud_trace": f'live.room_id:"{self.room_id}"',
            },
        }

    async def _write_session_index(self):
        try:
            rec = await self.session_index()
            import telemetry
            telemetry.glog("session complete", log_type="session", **{
                k: v for k, v in rec.items() if k not in ("audit", "replay")})
            import tools
            await asyncio.to_thread(
                lambda: tools.fs().collection("sessions").document(self.room_id).set(rec))
        except Exception:
            pass

    async def _on_interrupted(self):
        """BARGE-IN: a human started talking while Kairos was speaking. The model
        already stopped generating; drop every avatar chunk still queued for the
        relay so the Zoom seat goes quiet immediately instead of draining a
        backlog over the interrupter. Bounded tail = whatever already left the
        bridge (feeder/ffmpeg buffer, ~1s)."""
        dropped = 0
        q = self._relay_q.get("companion")
        while q is not None and not q.empty():
            try:
                q.get_nowait()
                dropped += 1
            except Exception:
                break
        self._seg_buf["companion"] = bytearray()
        self._log_event("barge_in", f"human interrupted — dropped {dropped} queued chunks")
        await self.perf("AvaLive", 0, phase="interrupted", meta={"dropped_chunks": dropped})
        await self._feed("companion", {"event": "interrupted", "dropped": dropped})
        await self.broadcast({"type": "flush_audio"})

    async def _ava_interrupted(self):
        """BARGE-IN on the LIVE AVATAR surface (auto-VAD tripped by a human).
        avatar-hybrid-vad pattern: bump the media epoch FIRST (so everything
        broadcast from here on is stamped fresh), purge every pipe's queued
        companion media, trim the warm ring's completed fragments (they hold
        her interrupted spoken tail — the post-barge replay must NOT re-speak
        it), then run the standard relay drop + flush and tell every browser
        so their stale-epoch / 400ms drop logic engages."""
        self._media_epoch = getattr(self, "_media_epoch", 0) + 1
        for p in list(getattr(self, "_pipes", {}).values()):
            if p["media"]:
                kept = deque(x for x in p["media"] if x[0] not in ("audio", "video"))
                p["media"] = kept
                p["bytes"] = sum(len(x[1]) for x in kept)
        st = getattr(self, "_ring_st", None)
        if st and not st["broken"]:
            st["frags"].clear()
            st["frag_bytes"] = 0
        await self._on_interrupted()
        await self.broadcast({"type": "interrupted", "who": "companion"})

    def _relay_send(self, who: str, kind: str, data: bytes = b""):
        import os as _os
        host = _os.environ.get("CP_RELAY_HOST", "")
        if not host:
            return
        q = self._relay_q.get(who)
        if q is None:
            q = self._relay_q[who] = asyncio.Queue(maxsize=256)
            self._relay_task[who] = asyncio.create_task(
                self._relay_pub_loop(who, q, host))
        if kind == "init":
            # a new stream starts: everything queued before it is obsolete
            while not q.empty():
                try:
                    q.get_nowait()
                except Exception:
                    break
        try:
            q.put_nowait((kind, data))
        except asyncio.QueueFull:
            # relay unreachable and buffer full: the byte stream is broken
            # either way (any gap corrupts fMP4) — drain and wait for the next
            # init to start clean; pages self-heal to the GCS poll path
            while not q.empty():
                try:
                    q.get_nowait()
                except Exception:
                    break

    async def _relay_pub_loop(self, who: str, q: asyncio.Queue, host: str):
        import json as _j
        import websockets
        # HOP BYPASS (2026-08-19 fluidity pass): with CP_RELAY_LOCAL=1 the
        # bridge feeds its OWN mounted relay channels in-process — no outbound
        # WSS to the relay service, no GFE hop, no serial-fanout coupling with
        # stage subscribers on another instance. The feeder then subscribes to
        # the bridge host directly (single instance + session affinity, so the
        # publisher and subscriber always colocate).
        import os as _os
        if _os.environ.get("CP_RELAY_LOCAL") == "1":
            import relay as _relay
            ch = _relay._chan(self.room_id, who)
            n = 0
            while not getattr(self, "_closed_flag", False):
                try:
                    kind, data = await q.get()
                    if kind == "init":
                        meta = _j.dumps({"ev": "init", "seq": n})
                        ch.remember(meta, b"", is_init=True)
                        await _relay._fanout(ch, meta, b"")
                    else:
                        n += 1
                        meta = _j.dumps({"ev": "seg", "seq": n,
                                         "t": int(time.time() * 1000)})
                        ch.remember(meta, data, is_init=False)
                        await _relay._fanout(ch, meta, data)
                except asyncio.CancelledError:
                    return
                except Exception:
                    await asyncio.sleep(0.2)
            return
        url = (f"wss://{host}/relay/pub/{self.room_id}/{who}"
               f"?t={tools.stream_token(self.room_id, f'pub:{who}')}")
        n = 0
        while not getattr(self, "_closed_flag", False):
            try:
                async with websockets.connect(url, max_size=None,
                                              open_timeout=10) as ws:
                    while True:
                        kind, data = await q.get()
                        if kind == "init":
                            await ws.send(_j.dumps({"ev": "init", "seq": n}))
                        else:
                            n += 1
                            await ws.send(_j.dumps(
                                {"ev": "seg", "seq": n,
                                 "t": int(time.time() * 1000)}))
                            await ws.send(data)
            except asyncio.CancelledError:
                return
            except Exception:
                await asyncio.sleep(1.5)

    async def _stage_mark_restart(self, who: str, notify: bool = True):
        """A NEW fMP4 byte stream begins for `who` (session (re)connect, or a
        whole-clip dump). Flush the previous stream's tail as its own segment,
        then record the NEXT segment as an init point (it starts with ftyp+moov)
        so stage pages can enter — or recover — at a clean decoder boundary
        instead of replaying, or dying on, the truncated tail before it."""
        if getattr(self, "web_conf_on", False) and who == "companion":
            # web rooms: the warm ring restarts at this init boundary, and the
            # browsers MUST hear about the new byte stream even though the
            # Zoom stage lanes are off — else a session reconnect splices two
            # fMP4 streams into one MSE pipe (review finding, 2026-08-19)
            self._ring_st = None
            if notify:
                await self.broadcast({"type": "video_reset", "who": who})
        if not self.stage_stream_on:
            return
        self._stage_stream(who, b"", flush=True)
        self._relay_send(who, "init")
        n = self._seg_n[who] + 1
        asyncio.get_running_loop().run_in_executor(
            _io_pool, tools.stage_mark_init, self.room_id, who, n
        ).add_done_callback(_consume)
        if notify:
            # webapp tiles pipe the same bytes over WS — they must re-init too
            await self.broadcast({"type": "video_reset", "who": who})

    async def _companion_video(self, mp4: bytes):
        self._comp_buf.extend(mp4)
        if not self.web_conf_on:
            # Zoom-era fan-outs (bot relay + stage fallback) — a web meeting has
            # no bot, so these are dead weight per chunk (webv2 lean-down)
            self._stream_push("companion", mp4)
            self._stage_stream("companion", mp4)
        else:
            self._web_ring_feed(mp4)     # BEFORE broadcast: replay must be a superset
        # skip the base64 encode entirely when no browser is attached — this
        # runs INLINE on the model rx loop, and in a Zoom meeting with the
        # cockpit embedded there are often zero WS clients (fluidity pass)
        if self.clients:
            await self.broadcast({"type": "video", "who": "companion",
                                  "mp4": base64.b64encode(mp4).decode()})

    # ---- webv2 warm ring: init-anchored replay for browser MSE pipes ----
    # The avatar fMP4 is ONE continuous byte stream per live session — ftyp+
    # moov exist only at its start. A browser that joins late, refreshes, or
    # rebuilds its SourceBuffer after a decode error therefore has NO way back
    # in without a replay from an init boundary (same pattern as the Pulse
    # relay's warm ring). We keep the init segment plus a rolling tail of
    # COMPLETE moof..mdat fragments (~8MB ≈ 8s) and the partial fragment in
    # flight, so replay + subsequent live chunks are byte-contiguous.
    _RING_TAIL_CAP = 8_000_000

    def _web_ring_feed(self, data: bytes):
        st = getattr(self, "_ring_st", None)
        if st is None:
            st = self._ring_st = {"buf": bytearray(), "init": bytearray(),
                                  "frags": deque(), "frag_bytes": 0,
                                  "cur": bytearray(), "broken": False}
        if st["broken"]:
            return
        buf = st["buf"]
        buf.extend(data)
        while True:
            if len(buf) < 8:
                break
            size = int.from_bytes(buf[0:4], "big")
            typ = bytes(buf[4:8])
            if size == 1:                      # 64-bit largesize box
                if len(buf) < 16:
                    break
                size = int.from_bytes(buf[8:16], "big")
            if size < 8 or size > 64_000_000:  # lost box sync — ring unusable
                st["broken"] = True
                glog("web ring lost mp4 box sync", severity="WARNING",
                     log_type="media", room=self.room_id)
                break
            if len(buf) < size:
                break
            box = bytes(buf[:size])
            del buf[:size]
            if typ in (b"ftyp", b"moov"):
                if typ == b"ftyp":             # a brand-new stream begins
                    st.update(init=bytearray(), frags=deque(),
                              frag_bytes=0, cur=bytearray())
                st["init"].extend(box)
            else:
                st["cur"].extend(box)
                if typ == b"mdat":             # fragment complete
                    st["frags"].append(bytes(st["cur"]))
                    st["frag_bytes"] += len(st["cur"])
                    st["cur"] = bytearray()
                    while (st["frag_bytes"] > self._RING_TAIL_CAP
                           and len(st["frags"]) > 1):
                        st["frag_bytes"] -= len(st["frags"].popleft())

    async def web_video_replay(self, ws):
        """Re-seat one browser on the live avatar stream: purge its queued
        video (the ring supersedes it), send video_reset, then init + tail
        fragments + the in-flight partial — after which the normal broadcast
        chunks continue byte-contiguously. No awaits before the enqueues are
        done, so nothing can interleave."""
        st = getattr(self, "_ring_st", None)
        if not st or not st["init"] or st["broken"]:
            return
        p = self._pipe_for(ws)
        if p["dead"]:
            return
        kept = deque(x for x in p["media"] if x[0] == "audio")
        p["media"] = kept
        p["bytes"] = sum(len(x[1]) for x in kept)
        def _put(t, obj):
            payload = json.dumps({**obj, "_epoch": getattr(self, "_media_epoch", 0)})
            p["media"].append((t, payload))
            p["bytes"] += len(payload)
        _put("video_reset", {"type": "video_reset", "who": "companion"})
        tail_frags = list(st["frags"])[-2:]
        pieces = ([bytes(st["init"])] + tail_frags
                  + ([bytes(st["cur"])] if st["cur"] else [])
                  + ([bytes(st["buf"])] if st["buf"] else []))
        for c in pieces:
            _put("video", {"type": "video", "who": "companion",
                           "mp4": base64.b64encode(c).decode()})
        p["wake"].set()

    async def _companion_turn_done(self):
        """Default turn-complete for the Companion (listen / single-agent modes).
        In AI-vs-AI, _wire_exchange overrides this with _companion_done."""
        if self._comp_buf:                          # end of a companion avatar turn
            await self.broadcast({"type": "video_end", "who": "companion"})
            self._comp_buf = bytearray()
            self.companion_turn_id += 1
            # push the turn's tail bytes out promptly; NO idle-clip injection —
            # her live session keeps streaming real idle (blinking) in the SAME
            # fMP4 stream, and splicing a pre-rendered clip's ftyp+moov into it
            # corrupts MSE on the stage pages (MediaError 3, face frozen)
            self._stage_stream("companion", b"", flush=True)
        last = next((t["text"] for t in reversed(self.companion.transcript)
                     if t["role"] == "Companion"), None) if self.companion else None
        last = self._strip_markers(last) if last else last
        if last and last != self._last_turn.get("companion"):
            self._last_turn["companion"] = last
            # AvaLive telemetry for EVERY reply (incl. autonomous VAD turns, which
            # never went through _speak). Duration = her speaking length; the
            # span's start = end - duration = her first word, so the telemetry
            # 'hear->think' gap (from the ZoomEars heard span) = true response lag.
            dur = getattr(self.companion, "last_turn_ms", None)
            if dur:
                await self.perf("AvaLive", dur, phase="speak",
                                meta={"ttft_ms": getattr(self.companion, "last_ttft_ms", None),
                                      "text": last[:60]})
            # durable content trail for the live avatar turn (heard in / spoken out)
            await self._log_companion_turn(last)
            # the workers' prompts must SEE Ava's lines (e.g. her consent ask)
            self.convo.append(("companion", last))
            await self._feed("turn", {"who": "companion", "text": last})
            if getattr(self, "_rtms_attached", False):
                asyncio.create_task(self._maybe_farewell(last))
            # J1: right after Ava speaks pre-consent (her consent ask), hand the
            # exec the client's reply options — first one always grants consent
            import workers
            if self.mute_fleet:
                pass                       # pure Ava-isolation: no fleet follow-ups
            elif not self.consents["prospect"] and self.greeted:
                asyncio.create_task(
                    workers.client_options(self, last, ensure_consent=True))
            elif all(self.consents.values()):
                asyncio.create_task(workers.ava_options(self))
        await self._feed("companion", {"event": "turn_complete"})

    async def _avatar_baton(self, speaker: str):
        """The project allows ONE live avatar (VIDEO) session at a time — a
        second connected avatar silently starves the first (proven in a2/a3
        isolation tests). Before an avatar speaks, suspend the other one; it
        reconnects-with-replay when the baton comes back."""
        me = self.companion if speaker == "companion" else self.simulator
        other = self.simulator if speaker == "companion" else self.companion
        if not (me and getattr(me, "avatar_name", "")):
            return                              # speaker isn't an avatar session
        if other and getattr(other, "avatar_name", ""):
            for _ in range(60):                 # let a mid-turn finish first
                if not getattr(other, "_speaking", False):
                    break
                await asyncio.sleep(0.5)
            await other.suspend()
        # Fresh pipe per line: long-lived avatar streams go deaf to new turns
        # (proven: sends right after (re)connect work 100%, sends into an
        # idle-streaming session get ignored). The settle sleep matters too:
        # the avatar slot frees lazily server-side after a suspend.
        await me.suspend()
        await asyncio.sleep(2.5)
        await me.resume()

    # ---------- client clip farm (zoom flow: client is RENDERED, Ava is LIVE) ----------
    @staticmethod
    def _clip_key(text: str) -> str:
        return " ".join(text.lower().split())[:300]

    def enqueue_client_renders(self, texts: list, front: bool = False):
        if self.simulator and getattr(self.simulator, "auto_vad", False):
            return    # live client: picks are spoken live, no pre-renders needed
        """Speculatively voice the client's candidate lines the moment they
        appear in the exec's lane — picks then play instantly."""
        if not self.stage_stream_on:
            return
        gen = self._lane_gen
        items = [(gen, t) for t in texts
                 if t and self._clip_key(t) not in self.clip_cache]
        if not items:
            return
        if front:
            self._render_q = items + self._render_q
        else:
            self._render_q.extend(items)
        if not self._render_task or self._render_task.done():
            self._render_task = asyncio.create_task(self._render_loop())

    def preempt_render(self):
        """An exec pick outranks background work — kill the in-flight render
        (it re-queues) so the slot frees in ~0.5s."""
        j = self._render_job
        if j and not j.done():
            j.cancel()

    async def _do_render_one(self, text: str):
        import live_manager
        async with self._slot_lock:   # serializes renders with each other only
            for _ in range(2):
                r = await live_manager.render_avatar_clip(
                    self.client_avatar, config.CLIENT_VOICE, text,
                    model=self._live_model_alt())
                if r["ok"]:
                    return r
                await asyncio.sleep(2.0)
        return None

    async def _render_loop(self):
        while self._render_q:
            gen, text = self._render_q.pop(0)
            key = self._clip_key(text)
            if key in self.clip_cache:
                continue
            if gen < self._lane_gen - 1:
                continue                      # two lane regens old — stale
            await self._feed("agent_activity", {"agent": "ClipFarm",
                                                "action": f"voicing: {text[:60]}"})
            self._render_job = asyncio.create_task(self._do_render_one(text))
            try:
                clip = await self._render_job
            except asyncio.CancelledError:
                self._render_q.insert(0, (gen, text))   # retry after the pick
                await asyncio.sleep(3)
                continue
            if clip:
                self.clip_cache[key] = clip
                await self._feed("clip_ready", {"text": text})
                await self._feed("agent_result", {"agent": "ClipFarm",
                                                  "action": f"ready in {clip['render_s']}s"})
                await self.perf("ClipFarm", clip.get("render_s", 0) * 1000,
                                phase="render", meta={"dur_s": clip.get("dur_s")})

    async def speak_client_clip(self, text: str):
        """Exec picked a client line: play its clip into the meeting (via the
        segment stream his bot page follows) and drive the J1/worker hooks off
        the KNOWN verbatim text — fully deterministic."""
        key = self._clip_key(text)
        if key in self.clip_cache:
            # cache hit — dump the ready clip; the bot page plays it progressively
            data = self.clip_cache[key]["mp4"] + self.idle_clips.get("prospect", b"")
            await self._stage_mark_restart("prospect", notify=False)
            for i in range(0, len(data), self._SEG_FLUSH):
                self._stage_stream("prospect", data[i:i + self._SEG_FLUSH])
            self._stage_stream("prospect", b"", flush=True)
        else:
            # cache MISS — STREAM the render live into Zoom (first word ~2s)
            import live_manager
            await self._feed("system", {"event": "note", "text": "Client is responding…"})
            await self._stage_mark_restart("prospect", notify=False)
            async def _sink(chunk):
                self._stage_stream("prospect", chunk)
            async with self._slot_lock:
                r = await live_manager.render_avatar_clip(
                    self.client_avatar, config.CLIENT_VOICE, text, on_chunk=_sink,
                    model=self._live_model_alt())
            self._stage_stream("prospect", b"", flush=True)
            if not r.get("ok"):
                await self._feed("system", {"event": "note",
                                            "text": "The client didn't respond — pick again."})
                return {"ok": False}
            self.clip_cache[key] = r            # cache for any repeat
            # tail the idle loop so his tile keeps breathing
            idle = self.idle_clips.get("prospect", b"")
            if idle:
                await self._stage_mark_restart("prospect", notify=False)
                for i in range(0, len(idle), self._SEG_FLUSH):
                    self._stage_stream("prospect", idle[i:i + self._SEG_FLUSH])
                self._stage_stream("prospect", b"", flush=True)
        self._turns += 1
        self.convo.append(("prospect", text))
        await self._feed("turn", {"who": "prospect", "text": text})
        if self._CONSENT_RX.search(text):
            self._chat_post("prospect", "I consent.")
        self._on_prospect_turn(text)
        if all(self.consents.values()):
            import workers
            asyncio.create_task(workers.banker_script(self))
        self._log_event("director_line", text[:140])
        return {"ok": True, "dur_s": self.clip_cache.get(key, {}).get("dur_s")}

    async def publish_idles(self, personas=None):
        """At meeting start (during bot boot): capture ~8s genuine idle
        animation per persona. Tiles breathe from second one. `personas` limits
        which tiles to publish (seat control)."""
        import live_manager
        want = set(personas) if personas is not None else {"prospect", "companion"}
        for who, avatar, voice in (("prospect", self.client_avatar, config.CLIENT_VOICE),
                                   ("companion", config.AVA_AVATAR, config.AVA_VOICE)):
            if who not in want:
                continue
            async with self._slot_lock:
                data = await live_manager.render_avatar_idle(
                avatar, voice,
                model=(self._live_model_alt() if who == "prospect" else None))
            if data:
                self.idle_clips[who] = data
                live_sess = self.companion if who == "companion" else self.simulator
                if live_sess and getattr(live_sess, "auto_vad", False):
                    # her LIVE session already streams real idle frames — splicing
                    # a second fMP4 (own ftyp+moov) into that byte stream corrupts
                    # MSE playback on the stage pages (proven: MediaError 3)
                    continue
                await self._stage_mark_restart(who, notify=False)
                for i in range(0, len(data), self._SEG_FLUSH):
                    self._stage_stream(who, data[i:i + self._SEG_FLUSH])
                self._stage_stream(who, b"", flush=True)

    async def _speak(self, who: str, prompt: str, tries: int = 3,
                     wait_s: float = 25.0, in_role: str = ""):
        """Deliver a critical line with proof-of-speech: after each send, wait
        for a NON-EMPTY spoken turn (media alone doesn't count — empty turns
        with tx='' are a real failure mode). No words -> fresh pipe, resend."""
        sess = self.companion if who == "companion" else self.simulator
        role = "Companion" if who == "companion" else "Prospect"
        if not sess:
            return False
        rl = in_role or ("system" if who == "companion" else "Director")
        was_cut = False
        for attempt in range(tries):
            if getattr(sess, "auto_vad", False):
                # ava-live: her session is hot (audio flowing) — send directly,
                # NEVER suspend (that would deafen her mid-meeting)
                n0 = len([t for t in sess.transcript
                          if t["role"] == role and t["text"].strip()])
                _t0 = time.monotonic()
                try:
                    await sess.send_text_turn(
                        prompt if attempt == 0 else
                        "[You may not have been heard — say it NOW] " + prompt,
                        role_label=rl)
                except Exception:
                    continue
                for _ in range(int(wait_s * 2)):
                    await asyncio.sleep(0.5)
                    n1 = len([t for t in sess.transcript
                              if t["role"] == role and t["text"].strip()])
                    if n1 > n0:
                        # AvaLive span now emitted in _companion_turn_done (covers
                        # VAD + directed). Client still logged here (no turn_done
                        # perf path for the simulator).
                        if who != "companion":
                            await self.perf("ClientVoice",
                                (time.monotonic() - _t0) * 1000, phase="speak",
                                meta={"ttft_ms": getattr(sess, "last_ttft_ms", None)})
                        return True
                continue
            self.preempt_render()             # exec picks outrank background work
            if who == "companion":
                # full-conversation awareness rides her system prompt into
                # every fresh pipe — late callbacks stay grounded
                base = getattr(sess, "_base_sys", None) or sess.system_instruction
                sess._base_sys = base
                mem = getattr(self, "meeting_memory", "")
                tail = "\n".join(f"{w}: {t}" for w, t in self.convo[-8:])
                sess.system_instruction = (
                    base + (f"\n\nMEETING MEMORY (everything so far):\n{mem}" if mem else "")
                    + (f"\n\nRECENT TURNS:\n{tail}" if tail else ""))
            await self._slot_lock.acquire()
            try:
                await sess.suspend()
                await asyncio.sleep(1.0)
                await sess.resume()
            finally:
                self._slot_lock.release()
            n0 = len([t for t in sess.transcript
                      if t["role"] == role and t["text"].strip()])
            cuts0 = sess.turn_cuts
            txt = prompt
            if attempt:
                txt = ("[You were cut off — deliver the COMPLETE line again "
                       "from the beginning] " if was_cut else
                       "[You may not have been heard — say it NOW] ") + prompt
            try:
                await sess.send_text_turn(txt, role_label=rl)
            except Exception:
                continue
            landed = False
            for _ in range(int(wait_s * 2)):
                await asyncio.sleep(0.5)
                n1 = len([t for t in sess.transcript
                          if t["role"] == role and t["text"].strip()])
                if n1 > n0:
                    landed = True
                    break
            if landed:
                await asyncio.sleep(1.5)          # let a late mid-turn cut surface
                was_cut = sess.turn_cuts > cuts0
                if not was_cut or attempt == tries - 1:
                    return True                    # clean — or accept the partial
            with tracer.start_as_current_span("room.speak_retry") as span:
                span.set_attribute("live.room_id", self.room_id)
                span.set_attribute("speak.who", who)
                span.set_attribute("speak.attempt", attempt + 1)
                span.set_attribute("speak.cut", was_cut)
        await self._feed("system", {"event": "note",
                                    "text": f"{role} didn't respond — try the line again."})
        return False

    def _chat_post(self, who: str, message: str):
        """Post to the Zoom meeting chat as one of our bots (fire-and-forget)."""
        bid = self.bot_by_who.get(who, "")
        if self.recall_key and bid:
            asyncio.get_running_loop().run_in_executor(
                _io_pool, tools.recall_chat_send, self.recall_key, bid, message
            ).add_done_callback(_consume)

    async def _park_companion(self):
        await asyncio.sleep(2.0)
        if self.companion and not getattr(self.companion, "_speaking", False):
            await self.companion.suspend()

    async def meeting_started(self):
        """The banker is in the room. Releases the armed live session.

        2026-08-13: she used to connect at meeting CREATION and would greet an
        empty room — the opener was spent before anyone could hear it. The
        session is now armed at creation and connected here, the moment a human
        is actually present."""
        await self._feed("system", {"event": "meeting_started"})
        await self.release_live_gate("banker joined")
        return {"ok": True}

    async def release_live_gate(self, why: str = "", auto_greet: bool = False):
        """Connect the armed live session exactly once. In real meetings, Kira
        waits on stage in live idle video until a human participant speaks or
        addresses her — never starting a staged monologue unprompted."""
        if not self.live_gate_pending or getattr(self, "ava_live", False):
            return
        self.live_gate_pending = False
        self._log_event("live_gate_released", why)
        await self.start_ava_live()
        await self._feed("system", {"event": "note",
                                    "text": f"Advisor connected — {why}."})
        if auto_greet:
            try:
                await self.companion.inject_context(
                    "[MEETING IS LIVE] A human has just joined and can hear you now. "
                    "Open the conversation yourself with your greeting and consent "
                    "request (playbook step 1). Do not wait to be prompted.")
            except Exception:
                pass

    async def ava_say(self, text: str):
        """The banker picked one of Ava's suggested lines — she delivers it
        VERBATIM in the meeting (words-verified, baton'd, cut-retried)."""
        if not self.companion:
            return {"ok": False, "error": "no companion"}
        low = text.lower()
        if ("permission" in low or "consent" in low) and not self.greeted:
            self.greeted = True
            self._chat_post("companion",
                            "Bridget here — if you consent to my participation and "
                            "note-taking today, please reply 'I consent' in this chat.")
        if "consent recorded" in low:
            self.ava_thanked = True
        self._log_event("ava_line", text[:140])
        asyncio.create_task(self._speak(
            "companion",
            f"Say exactly this, warmly, verbatim — nothing before or after: \"{text}\""))
        import workers
        asyncio.create_task(workers.ava_options(self))   # lane advances immediately
        return {"ok": True}

    _GENERIC_SPK = ("zoom", "speaker", "participant", "unknown", "",
                    # TRIAL-1 P0: Zoom delivered a participant literally named
                    # "Banker Name" (placeholder profile) and Kairos said it
                    # aloud. Role/placeholder-looking names must NEVER become
                    # [speaking: X] markers — better no name than a wrong one.
                    "banker name", "banker", "client", "client name", "prospect",
                    "guest", "user", "host", "admin", "test", "user name")
    _DEVICE_SPK_RX = __import__("re").compile(
        r"(iphone|ipad|android|'s (phone|tablet)|galaxy|pixel)\b", 2)

    def _speaker_is_generic(self, name: str) -> bool:
        n = (name or "").strip().lower()
        return n in self._GENERIC_SPK or bool(self._DEVICE_SPK_RX.search(n))

    # TRIAL-1 P1: the model echoes injected context markers in its TEXT channel
    # ("[speaking: X] Understood…") — never spoken, but they leaked into the
    # transcript UI and logs. Strip at every point her turn text is consumed.
    _MARKER_RX = __import__("re").compile(r"\[(?:speaking|log_event)[^\]]*\]\s*", 2)

    def _strip_markers(self, text: str) -> str:
        return self._MARKER_RX.sub("", text or "").strip()

    def _matches_banker(self, name: str) -> bool:
        """Robust banker-identity match: exact/containment either way after
        normalization, then fuzzy ratio (trial-1: 'Banker Name' != 'Karletha
        Paxton' silently declassified the banker; typos must not repeat that)."""
        if not self.banker_name or not name:
            return False
        a = " ".join((name or "").lower().split())
        b = " ".join(self.banker_name.lower().split())
        if a == b or a in b or b in a:
            return True
        import difflib
        return difflib.SequenceMatcher(None, a, b).ratio() >= 0.78

    async def _mark_speaker(self, name: str):
        """Tell the live Kairos session who is speaking, by REAL name, via a
        deduped '[speaking: NAME]' context marker (or '[speaking simultaneously:
        A, B]' on overlap). Names come from the diarized transcript / named
        audio — never from the nameless raw-audio stream. Also anchors
        current_speaker so J3a per-name addressing works."""
        if not getattr(self, "ava_live", False) or not self.companion:
            return
        if self._speaker_is_generic(name) or any(b in name for b in self._AI_SPK):
            return
        self.current_speaker = name
        now = time.monotonic()
        self._spk_seen[name] = now
        active = sorted(n for n, t in self._spk_seen.items() if now - t < 3.0)
        marker = ", ".join(active)
        if marker and marker != self._spk_marker:
            self._spk_marker = marker
            tag = ("[speaking: " + marker + "]") if len(active) == 1 else \
                  ("[speaking simultaneously: " + marker + "]")
            await self.companion.inject_context(tag)

    async def ingest_meeting_speech(self, speaker: str, text: str):
        """REALTIME hearing loop: a human spoke in the Zoom meeting (diarized
        transcript — carries the real participant name). NOTHING speaks in
        response — the words update shared context; the real name is what lets
        Kairos attribute who said what."""
        # authoritative speaker identity for Kairos comes from THIS named path
        if speaker and not self._speaker_is_generic(speaker) and speaker not in self.roster:
            self.roster.append(speaker)
        await self._mark_speaker(speaker)
        self.convo.append((speaker or "participant", text))
        # perf span marking the HEARD moment — makes the gap from 'user finished
        # speaking' -> 'Ava's first word' measurable in the telemetry timeline
        # (that gap = turn-detection + model think time, a key latency segment).
        self._last_heard_t = time.time()
        await self.perf("ZoomEars (RTMS)", 1, phase="heard",
                        meta={"speaker": speaker or "participant", "text": text[:60]})
        await self._feed("agent_activity", {"agent": "ZoomEars (RTMS)",
                                            "action": f"heard {speaker or 'banker'}"})
        _is_banker = self._matches_banker(speaker)
        await self._feed("turn", {"who": "banker" if _is_banker else "participant",
                                  "name": speaker, "text": text})
        if _is_banker:
            await self._check_next_steps(speaker or "banker", text)
            # TRIAL-1 J1 fix: the banker can consent OUT LOUD, symmetric with
            # the prospect's verbal detection — the cockpit click stays as
            # fallback (the card was buried under the embed in trial 1).
            if not self.consents.get("banker") and self._CONSENT_RX.search(text or ""):
                self.consents["banker"] = True
                self._log_event("consent_captured", f"banker verbal ({speaker})")
                asyncio.create_task(self._consent_update("banker"))
        # Kairos UX consent contract: a DECLINE from any human pre-consent means
        # the AI steps aside immediately (cockpit flips to Banker Takeover).
        if (not all(self.consents.values())
                and self._DECLINE_RX.search(text or "")):
            self._log_event("consent_declined", f"{speaker or 'participant'}")
            self.pause_ai_exchange()
            await self._feed("system", {"event": "banker_takeover",
                                        "reason": "consent_declined"})
            if self.companion:
                await self.companion.inject_context(
                    "[CONSENT DECLINED] A participant declined AI participation. "
                    "Say ONE gracious sentence stepping aside (no persuasion), "
                    "then remain silent until the banker explicitly re-invites you.")
        if self.mute_fleet:
            return          # pure Ava-isolation: no lane/fleet regeneration
        # P0 (vet 2026-08-02): the Zoom path never reached _on_prospect_turn,
        # so Pulse/Panel/verbal-consent were FROZEN in real meetings. Client
        # speech = any non-banker human (bots already filtered upstream).
        if not _is_banker:
            self._on_prospect_turn(text)
        if not (self._heard_refresh and not self._heard_refresh.done()):
            self._heard_refresh = asyncio.create_task(self._refresh_after_heard())

    async def _refresh_after_heard(self):
        """Coalesce: regenerate the lanes ONCE ~3.5s after speech starts
        arriving — not per fragment (card churn kills the exec's clicks)."""
        await asyncio.sleep(2.5)
        import workers
        asyncio.create_task(workers.meeting_memory(self))
        last = next((t for w, t in reversed(self.convo) if w == "banker"), "")
        jr, jt = getattr(self, "_just_read", ("", 0))
        if jr and time.monotonic() - jt < 25:
            a = {x.strip('.,?—"').lower() for x in jr.split() if len(x) > 3}
            b = {x.strip('.,?—"').lower() for x in last.split() if len(x) > 3}
            if a and len(a & b) / len(a) > 0.5:
                return   # they read the teleprompter line — options already made
        asyncio.create_task(workers.client_options(
            self, last, ensure_consent=not self.consents["prospect"]))
        asyncio.create_task(workers.ava_options(self))
        if all(self.consents.values()):
            asyncio.create_task(workers.banker_script(self))

    def _live_model(self):
        import config
        return config.MODEL_LIVE

    def _live_model_alt(self):
        # Avatar generation starves when TWO sessions share one live model
        # (proven: solo client speaks, goes silent once Ava connects). The
        # Companion therefore rides a second avatar-capable model.
        import config
        return getattr(config, "MODEL_LIVE_ALT", config.MODEL_LIVE)

    async def _companion_audio(self, pcm: bytes):
        await self.broadcast({"type": "audio", "who": "companion",
                              "pcm": base64.b64encode(pcm).decode()})

    async def _companion_tx(self, text: str):
        await self.broadcast({"type": "caption", "who": "companion", "text": text})

    async def _human_input_tx(self, text: str):
        # Accumulate the running utterance; regex-check the ACCUMULATED text so the
        # deterministic layer can trip mid-utterance without fragment blindness.
        # TRIAL-1 P1: in Zoom rooms the named mt17 transcript is the ONLY
        # transcript lane — broadcasting the model's own input transcription too
        # double-posted every human turn (mislabeled "Banker"). Sentinel still
        # sees every fragment either way.
        if not getattr(self, "_rtms_attached", False):
            who = self.current_speaker or "prospect"
            await self.broadcast({"type": "caption", "who": who, "text": text})
        self._live_utterance = getattr(self, "_live_utterance", "") + text
        if self.j4_armed:
            await self._sentinel_regex_only(self._live_utterance)

    async def _companion_tool(self, name: str, args: dict):
        await self._feed("agent_activity",
                         {"agent": "Companion", "action": name, "args": args})
        fnmap = {
            "enrich_prospect": lambda: tools.enrich_prospect(args.get("company", self.company)),
            "get_market_snapshot": lambda: tools.get_market_snapshot(args.get("pairs", "")),
            "find_materials": lambda: tools.find_materials(args.get("area", ""), args.get("kind", "")),
            "screen_entity": lambda: tools.screen_entity(args.get("name", "")),
            "lookup_specialist": lambda: tools.lookup_specialist(args.get("soeid", "")),
            "get_slots": lambda: tools.get_slots(args.get("soeid", "")),
            "log_event": lambda: self._log_event(args.get("event", ""), args.get("detail", "")),
        }
        try:
            import journeys
            if name == "request_materials":
                result = await journeys.request_materials(
                    self, args.get("area", ""), args.get("note", ""))
            elif name == "initiate_handoff":
                result = await journeys.initiate_handoff(
                    self, args.get("specialist_soeid", ""), args.get("availability_note", ""))
            elif name == "present_visual":
                # Bridget-driven stage: she says "let me pull that up on the
                # briefing page" AND it actually appears (library-only).
                import presenter
                topic = args.get("topic", "")
                v, score = presenter.match(f"show {topic}")
                if not v:
                    v = next((x for x in presenter.VISUALS if x["id"] == topic), None)
                if v:
                    result = await presenter.present(self, v, by="Bridget")
                else:
                    result = {"error": f"no visual matches '{topic}'",
                              "available": [x["id"] for x in presenter.VISUALS]}
            else:
                result = await asyncio.to_thread(fnmap.get(name, lambda: {"error": "unknown tool"}))
        except Exception as e:
            result = {"error": str(e)[:150]}
        await self._feed("agent_result", {"agent": "Companion", "action": name,
                                          "result_preview": json.dumps(result)[:300]})
        return result

    def _log_event(self, event: str, detail: str = ""):
        rec = {"event": event, "detail": detail, "t": time.time()}
        self.audit.append(rec)
        glog(f"audit {event}", severity="NOTICE", log_type="audit",
             room=self.room_id, event=event, detail=detail[:300])
        # live audit stream for the Kairos guardrail board (terminal panel).
        # _log_event is called from tool worker threads too — same thread-safe
        # pattern as the consent path below.
        try:
            asyncio.get_running_loop().create_task(self._feed("audit", rec))
        except RuntimeError:
            if self._loop:
                asyncio.run_coroutine_threadsafe(self._feed("audit", rec), self._loop)
        if event == "consent_captured" and detail != "verbal (detected)" \
                and not self.consents["prospect"]:
            # the Companion logs this milestone when the prospect agrees.
            # NB: tool calls run in a worker thread — create_task would throw
            self.consents["prospect"] = True
            try:
                asyncio.get_running_loop().create_task(
                    self._consent_update("prospect"))
            except RuntimeError:
                if self._loop:
                    asyncio.run_coroutine_threadsafe(
                        self._consent_update("prospect"), self._loop)
        try:
            # CRM audit write is network I/O — never block the audio event loop
            asyncio.get_running_loop().run_in_executor(
                _io_pool, lambda: self._crm_write(event, detail)
            ).add_done_callback(_consume)
        except RuntimeError:
            self._crm_write(event, detail)   # sync caller (tests)
        return {"logged": True, **rec}

    def _crm_write(self, event: str, detail: str):
        try:
            tools.crm_add_activity(self.lead_id, f"ai_{event}", detail)
        except Exception:
            pass

    # ---------- J4 Sentinel ----------
    async def _sentinel_regex_only(self, text: str):
        """Per-delta deterministic layer on the accumulated live utterance."""
        if getattr(self, "_tripped_utterance", False):
            return
        hit = sentinel.regex_check(text)
        if hit.get("trip"):
            self._tripped_utterance = True
            await self._trip(hit, text)

    async def _sentinel_on_input(self, text: str):
        """Complete-utterance path: regex + nuanced classifier."""
        hit = sentinel.regex_check(text)
        if hit.get("trip"):
            await self._trip(hit, text)
            return
        asyncio.create_task(self._sentinel_nuanced(text))

    async def _sentinel_nuanced(self, text: str):
        out = await sentinel.classifier_check(text)
        if out.get("trip"):
            await self._trip({"risk_type": out.get("risk_type", "regulatory"),
                              "phrase": "(classifier)", "layer": "classifier"}, text)

    async def _trip(self, hit: dict, text: str):
        async with self._lock:
            t0 = time.monotonic()
            with tracer.start_as_current_span("j4.escalation") as span:
                span.set_attribute("live.room_id", self.room_id)
                span.set_attribute("j4.risk_type", hit["risk_type"])
                span.set_attribute("j4.trigger_phrase", hit.get("phrase", ""))
                span.set_attribute("j4.layer", hit.get("layer", "regex"))
                span.set_attribute("j4.trigger_text", text[:300])
                # 1. hard stop the companion — including audio already queued
                # client-side (the browser flushes scheduled sources)
                if self.companion:
                    await self.companion.interrupt()
                    await self.broadcast({"type": "flush_audio"})
                # 2. deliver the scripted safe line + steer
                if self.companion:
                    await self.companion.inject_context(
                        f"[COMPLIANCE INTERRUPT] The prospect asked something requiring a "
                        f"specialist ({hit['risk_type']}). Say exactly, warmly: "
                        f"\"{sentinel.SAFE_LINE}\" Then STOP and wait for the banker. "
                        f"Do NOT answer the original question.")
                # 3. audit + console
                rec = self._log_event("risk_escalation",
                                      f"{hit['risk_type']}: {hit.get('phrase','')}")
                span.set_attribute("j4.audit_ms", round((time.monotonic() - t0) * 1000))
            await self._feed("risk", {"risk_type": hit["risk_type"], "phrase": hit.get("phrase"),
                                      "text": text, "safe_line": sentinel.SAFE_LINE,
                                      "banker_takeover": True})
            # a compliance stop must never be auto-resumed by a pending
            # typed-chat flag — same semantics as an explicit takeover
            self.pause_ai_exchange()

    # ---------- Prospect Simulator (AI client — video avatar OR audio) ----------
    async def start_simulator(self, contact_role="CFO", disposition="neutral", trap=False,
                              avatar="Paul", mode="video", auto_vad=False):
        self.sim_disposition = disposition
        sys = personas.simulator_sys(self.company, contact_role, disposition)
        if trap:
            sys += personas.PROSPECT_TRAP_HINT
        if auto_vad:
            # Zoom director mode: always-on live session (same infra as Ava —
            # push relay, init marks, hold-frame). NO meeting audio is ever fed
            # to him, so VAD can't self-trigger: he speaks ONLY picked lines,
            # delivered live in ~1-2s instead of a 5-13s pre-render.
            sys += ("\n\nDIRECTOR MODE: you speak ONLY when the Director hands "
                    "you a line — deliver it verbatim, naturally, then stop.")
        self.client_avatar = avatar
        self.client_mode = mode
        if mode == "video":
            self.simulator = LiveSession(
                name="Prospect", model=self._live_model_alt(), system_instruction=sys,
                voice_name=config.CLIENT_VOICE,              # user pick 2026-07-31
                room_id=self.room_id, avatar_name=avatar, auto_vad=auto_vad,
                on_video=self._client_video,
                on_output_tx=lambda t: self._sim_tx(t),
                on_turn_complete=self._client_turn_done)
        else:
            self.simulator = LiveSession(
                name="Prospect", model=self._live_model_alt(), system_instruction=sys,
                modality="AUDIO", room_id=self.room_id, voice_name=config.CLIENT_VOICE,
                on_audio=lambda pcm: self.broadcast({"type": "audio", "who": "prospect",
                                                     "pcm": base64.b64encode(pcm).decode()}),
                on_output_tx=lambda t: self._sim_tx(t),
                on_turn_complete=self._client_turn_done)
        self.simulator.on_stream_start = (
            lambda: self._stage_mark_restart("prospect"))
        await self.simulator.start()
        await self._feed("system", {"event": "simulator_ready",
                                    "disposition": disposition, "avatar": avatar, "mode": mode})

    async def _client_video(self, mp4: bytes):
        # fragmented-MP4 chunk (audio+video muxed) -> browser <video> via MSE,
        # AND the continuous GCS segment stream for the Recall stage page
        self._avatar_buf.extend(mp4)
        self._stage_stream("prospect", mp4)
        await self.broadcast({"type": "video", "who": "prospect",
                              "mp4": base64.b64encode(mp4).decode()})

    async def _client_turn_done(self):
        """AI client (avatar) finished a turn: record it, run the intelligence
        workers, and regenerate the banker's teleprompter from the real
        transcript. Exec-driven — the client only ever speaks in reply to the
        banker, never chained to the Companion."""
        await self.broadcast({"type": "video_end", "who": "prospect"})
        if self._avatar_buf:
            self._avatar_buf = bytearray()
            self.avatar_turn_id += 1
            self._stage_stream("prospect", b"", flush=True)    # speech tail out now
        last = next((t["text"] for t in reversed(self.simulator.transcript)
                     if t["role"] == "Prospect"), None) if self.simulator else None
        if last and last != self._last_turn.get("prospect"):
            self._last_turn["prospect"] = last
            self.convo.append(("prospect", last))
            await self._feed("turn", {"who": "prospect", "text": last})
            self._on_prospect_turn(last)
            # J1 gate: no discovery teleprompter until every party consented —
            # _consent_update fires the first post-consent script itself
            if all(self.consents.values()):
                import workers
                asyncio.create_task(workers.banker_script(self))

    async def _sim_tx(self, text: str):
        await self.broadcast({"type": "caption", "who": "prospect", "text": text})
        self._live_utterance = getattr(self, "_live_utterance", "") + text
        if self.j4_armed:
            await self._sentinel_regex_only(self._live_utterance)

    # ---------- AI-vs-AI floor-controlled exchange ----------
    # Text-feed pattern: each agent's completed transcription becomes the other's
    # text turn (audio still broadcast). Floor token + hard caps prevent loops.
    MAX_TURNS = 60
    MAX_SCENE_SECONDS = 40 * 60
    SOFT_WRAP_TURNS = 22          # nudge the prospect to wrap after this many turns
    # Strong cues end the call whenever they close the utterance; soft cues
    # ("take care", "talk soon") only count in a short closing line — mid-meeting
    # phrases like "let's talk soon about pricing" must NOT kill the demo.
    _FAREWELL_STRONG = __import__("re").compile(
        r"\b(goodbye|bye now|bye|thanks for (your|the) time|"
        r"have a (great|good) (day|one|week|weekend|evening|afternoon|morning))"
        r"\b[\s.!,]*$", 2)
    _FAREWELL_SOFT = __import__("re").compile(
        r"\b(take care|talk (to you )?soon|speak (then|soon)|"
        r"we'?ll (talk|speak) (then|soon))\b[\s.!,]*$", 2)

    def _is_farewell(self, text: str) -> bool:
        if not text:
            return False
        # the opening-exchanges guard protects against false positives, but a
        # DIRECTED close (typed farewell line / 'close' beat) is explicit intent
        if self._turns < 4 and not getattr(self, "_director_close", False):
            return False
        t = text.strip()
        if self._FAREWELL_STRONG.search(t[-60:]):
            return True
        return len(t) <= 160 and bool(self._FAREWELL_SOFT.search(t[-60:]))

    async def _conclude(self, reason: str):
        if self.floor != "ai_exchange":
            return
        self.floor = "concluded"
        self._log_event("meeting_concluded", reason)
        await self._feed("system", {"event": "meeting_concluded", "reason": reason})
        await self.broadcast({"type": "caption", "who": "system",
                              "text": "Meeting concluded — " + reason})

    def _wire_exchange(self):
        self._turns = 0
        self._scene_t0 = time.monotonic()
        self._prospect_farewelled = False
        self.companion.on_turn_complete = self._companion_done
        self.simulator.on_turn_complete = self._simulator_done

    def _caps_hit(self) -> bool:
        if self._turns >= self.MAX_TURNS:
            return True
        if time.monotonic() - self._scene_t0 > self.MAX_SCENE_SECONDS:
            return True
        return False

    async def _companion_done(self):
        last = next((t["text"] for t in reversed(self.companion.transcript)
                     if t["role"] == "Companion"), None)
        last = self._strip_markers(last) if last else last
        # the Live server can emit turn_complete twice for one generation —
        # without this guard the turn is fed AND relayed twice (double replies)
        if last and last == self._last_turn.get("companion"):
            return
        if last:
            self._last_turn["companion"] = last
            self._last_turn_t = time.monotonic()
            await self._log_companion_turn(last)
            # the worker fleet reads convo — without this append the in-app
            # exchange looks EMPTY to Scorer/Panel/perceive (deaf-fleet bug)
            self.convo.append(("companion", last))
            await self._feed("turn", {"who": "companion", "text": last})
        await self._feed("companion", {"event": "turn_complete"})
        # typed banker chat paused the exchange for the reply — resume now
        if self.floor == "human" and self._resume_after_reply:
            self._resume_after_reply = False
            self.floor = "ai_exchange"
        if self.floor == "ai_exchange" and self._caps_hit():
            await self._conclude("time — the Companion wrapped the meeting")
            return
        if self.floor != "ai_exchange" or not self.simulator:
            return
        # graceful end: prospect already said goodbye and companion answered it
        if getattr(self, "_prospect_farewelled", False):
            await self._conclude("the prospect closed the call; the Companion said its goodbyes")
            return
        if last:
            if self.direction == "approve":
                # Director mode: pause; offer 3 in-character candidates + override.
                # The meeting must NEVER stall on a worker failure — fall back to
                # generic in-character lines so the exec always has buttons.
                import workers
                try:
                    choices = await workers.prospect_choices(self)
                except Exception:
                    choices = []
                if not choices:
                    choices = [
                        "That makes sense — walk me through how it would work for us.",
                        "Before we go further, what does this actually cost?",
                        "I'm not convinced yet — what makes this different from our bank today?",
                    ]
                await self._feed("choices", {"choices": choices})
                return
            self._turns += 1
            nudge = ""
            if self._turns >= self.SOFT_WRAP_TURNS:
                nudge = (" [Your next reply: you are out of time — wrap up in ONE short "
                         "sentence confirming the next step, then say goodbye and stop.]")
            await self._paced_relay(lambda: self.simulator.send_text_turn(
                f"The bank's AI companion just said to you: \"{last}\" — respond in character.{nudge}",
                role_label="Companion"))
            if self.direction == "suggest":
                import workers
                async def _sugg():
                    try:
                        ch = await workers.prospect_choices(self)
                        if ch:
                            await self._feed("suggestions", {"choices": ch})
                    except Exception:
                        pass
                asyncio.create_task(_sugg())

    async def _simulator_done(self):
        last = next((t["text"] for t in reversed(self.simulator.transcript)
                     if t["role"] == "Prospect"), None)
        if last and last == self._last_turn.get("prospect"):
            return
        if last:
            self._last_turn["prospect"] = last
            self._last_turn_t = time.monotonic()
            self.convo.append(("prospect", last))
            await self._feed("turn", {"who": "prospect", "text": last})
            self._on_prospect_turn(last)
        if self.floor == "ai_exchange" and self._caps_hit():
            await self._conclude("time — the Companion wrapped the meeting")
            return
        if self.floor != "ai_exchange" or not self.companion:
            return
        if last:
            if self._is_farewell(last):
                # let the Companion say ONE goodbye, then conclude
                self._prospect_farewelled = True
                self._turns += 1
                await self.companion.send_text_turn(
                    last + " [The prospect is ending the call — reply with ONE brief, "
                           "warm goodbye that confirms the next step. Nothing after.]",
                    role_label="Prospect")
                return
            self._turns += 1
            await self._paced_relay(lambda: self.companion.send_text_turn(
                last, role_label="Prospect"))

    async def _paced_relay(self, send):
        """Natural breathing room between AI turns — a live meeting, not a
        machine-gun exchange. Re-checks floor AND relay generation after the
        pause so any human input during the gap cancels the stale relay."""
        gen = self._relay_gen
        async def _go():
            await asyncio.sleep(self.pace_s)
            if self.floor == "ai_exchange" and gen == self._relay_gen:
                await send()
        asyncio.create_task(_go())

    _DECLINE_RX = __import__("re").compile(
        r"\b(i (do not|don'?t) consent|no ai|without (the )?ai|decline (the )?ai|"
        r"prefer (a )?human|not comfortable with (the )?ai|turn (off|it off)|"
        r"rather not (use|have) (the )?ai)\b", 2)

    _CONSENT_RX = __import__("re").compile(
        r"\b(that'?s fine|fine by me|go ahead|yes,? (you may|please|of course|go ahead)|"
        r"i consent|i agree|no objection|sure thing|of course( you (can|may))?|"
        r"you have my (permission|consent)|that works)\b", 2)

    # Phase II "next steps outlined": the INTENT is stated out loud by the
    # banker or by Kairos (user directive 2026-08-07) — same deterministic
    # backstop style as consent. Client turns are never checked (a client
    # ASKING about next steps is not an outline).
    _NEXT_STEPS_RX = __import__("re").compile(
        r"\b(next steps?|as a next step|follow[- ]?up (call|meeting|conversation|session)|"
        r"i('| wi)ll (send|share|schedule|set up|connect you|introduce you|put together)|"
        r"we('| wi)ll (send|schedule|set up|follow up|circle back)|"
        r"let'?s (schedule|set up|book|plan|get (a|some)? ?time)|"
        r"put (some )?time on (the|your|our) calendar|schedule (a|some) time|"
        r"(treasury )?specialist will (reach out|follow up|be in touch))\b", 2)

    # TRIAL-1 P2: after everyone said goodbye the meeting lingered ~50s until a
    # manual Leave. When Kairos says farewell AND a human wrapped the call in
    # the preceding turns, end the meeting after a 12s grace window (any new
    # human speech cancels). main wires on_meeting_end per zoom room.
    _GOODBYE_RX = __import__("re").compile(
        r"\b(goodbye|good[- ]?bye|bye,? (everyone|all)|take care|"
        r"have a (great|good|wonderful) (day|one|evening))\b", 2)
    _WRAP_RX = __import__("re").compile(
        r"\b(conclude|wrap(ping)? (up|the))|end the (call|meeting)|that('| i)s all"
        r"|(got|have) to (go|run|drop)\b", 2)

    async def _maybe_farewell(self, text: str):
        if not getattr(self, "on_meeting_end", None) or getattr(self, "_farewell_done", False):
            return
        if not self._GOODBYE_RX.search(text or ""):
            return
        # TRIAL-2 feedback: her goodbye ALONE arms the auto-end — requiring an
        # explicit human wrap phrase was too narrow (nobody says the magic
        # words). A wrap phrase just shortens the grace window; any new human
        # speech still cancels.
        recent = " ".join(t for w, t in self.convo[-5:] if w != "companion")
        grace = 10 if self._WRAP_RX.search(recent) else 20
        self._farewell_done = True
        self._log_event("farewell_detected", f"auto-end in {grace}s unless someone speaks")
        heard_at = self._last_heard_t
        await asyncio.sleep(grace)
        if self._last_heard_t != heard_at:          # someone kept talking — stand down
            self._farewell_done = False
            return
        self._log_event("meeting_auto_ended", "farewell grace elapsed")
        try:
            await self.on_meeting_end()
        except Exception:
            pass

    def _start_idle_watchdog(self):
        """Zoom rooms: if NO human speech arrives for 4 minutes (everyone left,
        or dead air), end the meeting — clicking Leave never told the server
        anything, so the bot kept meetings alive (trial-2 feedback)."""
        if getattr(self, "_idle_wd", None):
            return
        self._idle_anchor = time.time()
        self._idle_wd = asyncio.create_task(self._idle_watch())

    async def _idle_watch(self, idle_s: int = 240):
        while True:
            await asyncio.sleep(60)
            if not getattr(self, "on_meeting_end", None):
                continue
            last = max(getattr(self, "_last_heard_t", 0) or 0,
                       getattr(self, "_idle_anchor", 0))
            if time.time() - last > idle_s:
                self._log_event("meeting_idle_ended",
                                f"no human speech for {idle_s}s")
                try:
                    await self.on_meeting_end()
                except Exception:
                    pass
                return

    async def _check_next_steps(self, by: str, text: str):
        """Fire-once verbal next-steps detector on banker/Kairos turns."""
        if getattr(self, "_next_steps_stated", False):
            return
        if not self._NEXT_STEPS_RX.search(text or ""):
            return
        self._next_steps_stated = True
        self._log_event("next_steps_stated", by)
        await self._feed("system", {"event": "next_steps_stated", "by": by})

    def _on_prospect_turn(self, text: str):
        """Fire G2 analysis workers off the voice path (async tasks)."""
        import workers
        if self.mute_fleet:
            return                    # pure Ava-isolation: no analysis fleet
        self._prospect_turns += 1
        # Deterministic consent backstop: the Companion is INSTRUCTED to log
        # consent_captured, but model tool-calls are probabilistic — detect the
        # prospect's verbal agreement directly in the opening turns.
        if (not self.consents["prospect"] and self._prospect_turns <= 4
                and self._CONSENT_RX.search(text or "")):
            self.consents["prospect"] = True
            self._log_event("consent_captured", "verbal (detected)")
            asyncio.create_task(self._consent_update("prospect"))
        self._live_utterance = ""            # utterance complete -> reset accumulator
        self._tripped_utterance = False
        import journeys
        if self.j4_armed:
            asyncio.create_task(self._sentinel_nuanced(text))   # full-turn classifier
        asyncio.create_task(journeys.check_objection(self, text))
        asyncio.create_task(workers.pulse_sentiment(self, text))
        # Front-load insight production: every turn for the first 4 (Console fills
        # fast for the exec), then every 2nd turn.
        if self._prospect_turns <= 4 or self._prospect_turns % 2 == 0:
            asyncio.create_task(workers.panel_insights(self))
        if self._prospect_turns % 3 == 0:
            asyncio.create_task(workers.score_update(self))
        if self._prospect_turns % 2 == 0:
            asyncio.create_task(workers.meeting_memory(self))
        asyncio.create_task(self._ground_turn(text))
        import presenter
        asyncio.create_task(presenter.maybe_auto_present(self, text))

    async def _inject_premeeting_brief(self):
        """DEMO-QUALITY OPENER (2026-08-11): a real RM walks in prepared. Inject
        the prospect COMPANY's dossier context (company-level only — the
        people-knowledge hard rule is untouched) so Bridget can open with a
        researched hook ("congratulations on the Hong Kong expansion") instead
        of a cold greeting."""
        try:
            import personas
            b = (personas.load_bibles() or {}).get(self.company)
            if not b or not self.companion:
                return
            tp = b.get("treasury_profile") or {}
            brief = (f"[PRE-MEETING BRIEF — company research, fair to reference naturally "
                     f"as 'our preparation'; NEVER recite verbatim or mention this note] "
                     f"Company: {b.get('company')} — {b.get('industry','')}, HQ {b.get('hq','')}. "
                     f"Known context: {', '.join(b.get('seed_motifs', [])[:4])}. "
                     f"Treasury pain points on record: {'; '.join((tp.get('pain_points') or [])[:3])}. "
                     f"FX corridors: {', '.join(tp.get('fx_corridors', []) if isinstance(tp.get('fx_corridors'), list) else [str(tp.get('fx_corridors',''))])}. "
                     f"Use at most ONE of these as a warm, congratulatory hook in your "
                     f"greeting; let the client confirm details themselves.")
            await self.companion.inject_context(brief)
            self._log_event("premeeting_brief_injected", self.company)
        except Exception:
            pass

    async def _ground_turn(self, text: str):
        """Corporate knowledge grounding (Policy Panel feature): retrieve
        verified corpus excerpts matching this utterance (~0ms, pure Python)
        and hand them to Kairos parrot-safe + surface to the cockpit. The
        retrieval itself is deterministic; skipped entirely by the
        config.GROUNDING_ON kill switch (grounding.retrieve returns [])."""
        import grounding
        # TRIAL-1 P2: short conversational turns ("what about pricing?") pulled
        # weak excerpts — demand much stronger overlap before injecting on them.
        short = len((text or "").split()) < 8
        hits = grounding.retrieve(text, min_score=4.0 if short else 2.5)
        if not hits:
            return
        # retained for the prospect share view (GET /rooms/{id}/share)
        ring = getattr(self, "grounding_hits", None)
        if ring is None:
            ring = self.grounding_hits = []
        ring.extend(hits)
        del ring[:-30]
        if self.companion:
            lines = "\n".join(f"- {h['title']}: {h['snippet']}" for h in hits)
            await self.companion.inject_context(
                "[GROUNDING] Verified Paxton Bank reference for the current topic — private "
                "context, use only if relevant, weave into your own words, never read "
                "aloud as a list or cite URLs verbatim:\n" + lines)
        await self._feed("grounding", {"hits": [
            {"source_id": h["source_id"], "title": h["title"], "url": h["url"],
             "snippet": h["snippet"][:220], "score": h["score"]} for h in hits]})
        await self.agent_log("grounding", "retrieve", text,
                             {"hits": hits}, model="corpus")

    async def _consent_update(self, party: str):
        allc = all(self.consents.values())
        await self._feed("consent", {"party": party, **self.consents, "all": allc})
        if self.companion:
            if allc:
                await self._feed("system", {"event": "discovery_unlocked"})
                # 2026-08-11: the old VERBATIM ritual line ("...Jordan, over to
                # you") is GONE — it was a July-script fossil that survived two
                # persona reworks and said a name that doesn't exist. Consent
                # completion now just nudges her to acknowledge and lead into
                # HER agenda (playbook stage 1) in her own words.
                self.ava_thanked = True
                await self.companion.inject_context(
                    "[CONSENT COMPLETE] (context only — do NOT produce an extra turn "
                    "for this note) If you have already thanked the client, do not "
                    "thank again. Whatever you say next MUST be your agenda frame — "
                    "never 'over to you', never handing the conversation to anyone.")
                self._chat_post("companion",
                                "✓ Client consent recorded — thank you. — Bridget")
                import workers
                asyncio.create_task(workers.banker_script(self))
                asyncio.create_task(workers.ava_options(self))
            elif party == "banker":
                await self.companion.inject_context(
                    "[The banker has confirmed consent. Still waiting on the client.]")
            else:
                await self.companion.inject_context(
                    "[The client has consented. Continue per your playbook.]")

    async def grant_consent(self, party: str = "banker"):
        if party in self.consents and not self.consents[party]:
            self.consents[party] = True
            self._log_event("consent_confirmed", party)
            await self._consent_update(party)
        return {"ok": True, **self.consents, "all": all(self.consents.values())}

    async def banker_correction(self, correction: str):
        """J2 banker-correction beat: two clicks on Console -> Companion adjusts
        mid-conversation, audit logs it, counter ticks (docs' 'minimal banker
        correction' metric, operationalized)."""
        self.corrections += 1
        if self.companion:
            await self.companion.inject_context(
                f"[BANKER CORRECTION] The banker corrects a fact you stated: {correction}. "
                f"Gracefully acknowledge and incorporate this in your next sentence — "
                f"do not over-apologize.")
        self._log_event("banker_correction", correction)
        await self._feed("correction", {"text": correction, "count": self.corrections})

    async def start_ai_exchange(self, opening: str = ""):
        """Begin a Companion<->Simulator conversation (demo autopilot / gate test)."""
        self._wire_exchange()
        self.floor = "ai_exchange"
        # deterministic KPI event — the LLM's voluntary log_event is a bonus
        self._log_event("discovery_started", self.company)
        kickoff = opening or ("Greet the prospect, confirm consent to proceed with an "
                              "AI-assisted conversation, then begin discovery.")
        await self.companion.send_text_turn(f"[MEETING STARTED] {kickoff}",
                                            role_label="system")
        if self._watchdog:
            self._watchdog.cancel()
        self._watchdog = asyncio.create_task(self._kickoff_watchdog(kickoff))
        if self._stall_task:
            self._stall_task.cancel()
        self._stall_task = asyncio.create_task(self._stall_watchdog())

    async def _stall_watchdog(self):
        """Self-healing: a tool-only turn or dropped relay can silence the
        exchange. If nothing has spoken for 45s while the floor is live and
        both sessions are idle, re-kick the chain."""
        while True:
            await asyncio.sleep(15)
            if self.floor != "ai_exchange":
                continue
            idle = time.monotonic() - self._last_turn_t
            busy = any(getattr(s, "_speaking", False)
                       for s in (self.companion, self.simulator) if s)
            if idle > 45 and not busy and self.companion and self.simulator:
                with tracer.start_as_current_span("exchange.stall_rekick") as span:
                    span.set_attribute("live.room_id", self.room_id)
                    span.set_attribute("exchange.idle_s", round(idle))
                self._last_turn_t = time.monotonic()
                self.resume_ai_exchange()

    async def _kickoff_watchdog(self, kickoff: str, wait_s: int = 30):
        """Demo resilience: if the Companion never produced ANY output shortly
        after kickoff (dead-at-birth session), force-reconnect and resend once.
        Liveness = companion transcript, NOT _turns — in approve mode the
        exchange legitimately sits at _turns==0 while the exec picks a line."""
        await asyncio.sleep(wait_s)
        c = self.companion
        alive = c and (getattr(c, "_speaking", False)
                       or getattr(c, "_turn_open_tx", "")
                       or any(t["role"] == "Companion" for t in c.transcript))
        if self.floor == "ai_exchange" and c and not alive:
            with tracer.start_as_current_span("exchange.kickoff_retry") as span:
                span.set_attribute("live.room_id", self.room_id)
            async with c._reconnect_lock:      # never race _rx_loop's reconnect
                await c._connect(replay=True)
            await c.send_text_turn(
                f"[MEETING STARTED — retry] {kickoff}", role_label="system")

    def pause_ai_exchange(self):
        """Human takes the floor (exec interjects / banker takeover).
        An explicit takeover must cancel any pending auto-resume from a
        typed banker line — the human decides when the AI resumes."""
        self.floor = "human"
        self._resume_after_reply = False

    def resume_ai_exchange(self):
        self.floor = "ai_exchange"
        self._prospect_farewelled = False
        # If both sides are idle (the usual case — the companion finished its
        # reply while the human held the floor), re-kick the exchange or the
        # room sits in dead air forever.
        if (self.companion and self.simulator
                and not getattr(self.companion, "_speaking", False)
                and not getattr(self.simulator, "_speaking", False)):
            last = next((t["text"] for t in reversed(self.companion.transcript)
                         if t["role"] == "Companion"), None)
            if last:
                self._turns += 1
                asyncio.create_task(self.simulator.send_text_turn(
                    f"The bank's AI companion just said to you: \"{last}\" — "
                    f"respond in character.", role_label="Companion"))

    # ---------- human audio path (browser mic) ----------
    # The utterance is BUFFERED, transcribed + addressee-classified in one fast
    # model call, then routed to whoever was actually addressed — so asking the
    # CFO something makes the CFO answer, not the Companion.
    async def human_speech_start(self, name: str = "", who: str = "banker"):
        self.pause_ai_exchange()
        self._relay_gen += 1                          # cancel pending paced relays
        self._mic_buf = bytearray()
        self._speech_who = who
        self._speech_name = name or who
        for s in (self.companion, self.simulator):
            if s and getattr(s, "_speaking", False):
                await s.interrupt()                   # barge-in (27ms proven)
        await self.broadcast({"type": "flush_audio"})
        # J3a: per-connection identity -> speaker annotation for name-aware replies
        if name and name != self.current_speaker:
            self.current_speaker = name
            if self.companion:
                await self.companion.inject_context(f"[{name} is speaking now]")
            await self._feed("speaker", {"name": name})

    async def web_pcm(self, pcm: bytes, speaker: str):
        """webv2/meetv3 ears: browser mic (16k int16 mono) streams straight into
        the native-audio path — same as Zoom RTMS, but the speaker name comes
        from the join, so attribution is exact. No transcription anywhere."""
        await self.ingest_external_audio(pcm, speaker=speaker or "Guest")

    async def web_cam(self, sender_ws, who: str, name: str, jpeg_b64: str):
        """Lightweight video tiles between humans: fan each participant's frame
        ONLY to OTHER connected pages (never back to the sender or same role)."""
        if not hasattr(self, "last_cams"):
            self.last_cams = {}
        msg = {"type": "peer_cam", "who": who, "name": name or who, "jpg": jpeg_b64}
        self.last_cams[who] = msg
        payload = json.dumps(msg)
        for ws in list(self.clients):
            if ws is sender_ws:
                continue
            pt = self.participants.get(ws, {})
            if pt.get("who") == who and who in ("banker", "prospect"):
                continue
            p = self._pipe_for(ws)
            if not p["dead"]:
                p["cam"][msg["name"]] = payload
                p["wake"].set()

    async def human_pcm(self, pcm: bytes):
        self._mic_buf.extend(pcm)
        if len(self._mic_buf) > 16000 * 2 * 120:      # 2-min safety cap
            del self._mic_buf[:len(self._mic_buf) - 16000 * 2 * 120]

    async def human_speech_end(self):
        buf = bytes(self._mic_buf)
        self._mic_buf = bytearray()
        if getattr(self, "stage_stream_on", False):
            return   # Zoom room: RTMS is the ONLY ear — webapp mic would make
                     # Ava hear the banker TWICE (native audio + routed text turn)
        # reset the Sentinel accumulator so the regex layer re-arms
        self._live_utterance = ""
        self._tripped_utterance = False
        if len(buf) < 16000 * 2 // 4:                 # < 250 ms -> noise blip
            return
        import workers
        asyncio.create_task(workers.route_banker_utterance(
            self, buf, self._speech_name, self._speech_who))

    async def route_human_text(self, text: str, addressee: str,
                               speaker: str = "banker", who: str = "banker"):
        """Deliver a (transcribed or typed) human utterance to whoever was
        addressed. 'open' goes to the Companion — it facilitates the room."""
        if not text.strip():
            return
        # human input always takes the floor: cancel pending relays, stop
        # whoever is mid-utterance (typed path has no speech_start barge-in)
        self._relay_gen += 1
        for s in (self.companion, self.simulator):
            if s and getattr(s, "_speaking", False):
                await s.interrupt()
                await self.broadcast({"type": "flush_audio"})
        await self.broadcast({"type": "caption", "who": speaker, "text": text})
        await self._feed("turn", {"who": who, "name": speaker, "text": text})
        self.convo.append((speaker, text))
        if self.j4_armed and who != "banker":
            await self._sentinel_on_input(text)
        if who != "banker":
            self._on_prospect_turn(text)
        if addressee == "prospect" and self.simulator:
            if self.companion:
                await self.companion.inject_context(
                    f"[{speaker} (the banker) said to the PROSPECT: \"{text}\". "
                    f"Do not respond — stay silent and listen.]")
            self._turns += 1
            # mic live (hold_floor): the prospect answers ONCE and the room
            # then waits for the human — no AI-to-AI chain, no talk-over
            self.floor = "human" if self.hold_floor else "ai_exchange"
            _p = (f"{speaker}, the banker, says to you directly: \"{text}\" — "
                  f"respond to them in character.")
            if getattr(self.simulator, "avatar_name", ""):
                asyncio.create_task(self._speak("simulator", _p, in_role=speaker))
            else:
                await self.simulator.send_text_turn(_p, role_label=speaker)
        elif self.companion:
            if self.simulator:
                await self.simulator.inject_context(
                    f"[{speaker} (the banker) said to the AI companion: \"{text}\"]")
            if self.floor != "concluded":
                self.floor = "human"
                # with the mic live the exchange does NOT auto-resume
                self._resume_after_reply = not self.hold_floor
            if getattr(self.companion, "avatar_name", ""):
                asyncio.create_task(self._speak("companion", text, in_role=speaker))
            else:
                await self.companion.send_text_turn(text, role_label=speaker)

    async def human_chat(self, text: str, who: str = "banker", speaker: str = ""):
        """Text-mode input path (chat lane) — same addressee routing as voice,
        so a typed question to the CFO gets answered by the CFO."""
        import workers
        addressee = "open"
        if who == "banker" and self.simulator:
            addressee = await workers.classify_addressee(text, self)
        await self.route_human_text(text, addressee, speaker=speaker or who, who=who)

    # ---------- director actions (modes B/C/D) ----------
    async def speak_as_prospect(self, text: str):
        """Human-chosen line, voiced by the client. Zoom flow: rendered clip
        (instant when speculative render already ran). In-app flow: live sim."""
        if self.stage_stream_on and not self.simulator:
            return await self.speak_client_clip(text)
        if not self.simulator:
            return {"ok": False, "error": "no simulator"}
        if getattr(self.simulator, "_speaking", False):
            await self.simulator.interrupt()
            await self.broadcast({"type": "flush_audio"})
        t = text.strip()
        if (self._FAREWELL_STRONG.search(t[-60:])
                or (len(t) <= 160 and self._FAREWELL_SOFT.search(t[-60:]))):
            self._director_close = True   # explicit intent overrides the early-turn guard
        self._turns += 1
        asyncio.create_task(self._speak(
            "simulator",
            f"Say exactly this, verbatim, with natural delivery — nothing before or after: \"{text}\""))
        if self.stage_stream_on and self._CONSENT_RX.search(t):
            self._chat_post("prospect", "I consent.")   # J1 chat-consent ritual
        self._log_event("director_line", text[:140])
        return {"ok": True}

    async def steer_beat(self, beat: str):
        """Beat navigator: prospect naturally steers the scene toward a journey beat."""
        STEER = {
            "discovery": "share one concrete operational detail, inviting deeper discovery",
            "objection": "raise a realistic pricing or complexity concern (J8)",
            "materials": "ask them to send you case studies or materials on what was discussed (J7)",
            "risk": "demand a rate GUARANTEE and regulatory confirmation for your board (J4)",
            "handoff": "agree it makes sense to speak with their treasury specialist (J5)",
            "close": "wrap up: confirm the next step in one sentence, then say goodbye (end the call)",
        }
        instr = STEER.get(beat)
        if not instr or not self.simulator:
            return {"ok": False, "error": "unknown beat or no simulator"}
        if beat == "close":
            self._director_close = True   # explicit intent overrides the early-turn guard
        if getattr(self.simulator, "_speaking", False):
            await self.simulator.interrupt()
            await self.broadcast({"type": "flush_audio"})
        self._turns += 1
        await self.simulator.send_text_turn(
            f"[DIRECTION: In your next reply, in character, {instr}.] Respond now.",
            role_label="Director")
        self._log_event("director_beat", beat)
        return {"ok": True}

    # ---------- listen mode (Zoom RTMS / external sources) ----------
    async def start_listen_mode(self, source: str = "external"):
        """No Companion voice into the source; intelligence pipeline runs on
        ingested transcript (Pulse/Panel/Sentinel/Coach + Console feeds)."""
        self.listen_mode = True
        self.floor = "listen"
        await self._feed("system", {"event": "listen_mode", "source": source})
        await self.system_note(f"Listen mode: receiving live {source} meeting")

    async def system_note(self, text: str):
        await self.broadcast({"type": "caption", "who": "system", "text": text})
        await self._feed("system", {"event": "note", "text": text})

    # rolling raw meeting audio (16k mono PCM) for the NATIVE-AUDIO agent fleet
    _AUDIO_WINDOW_S = 60
    _AI_SPK = ("(AI)", "Ava", "Companion", "Kairos", "Bridget")

    def _buf_audio(self, pcm: bytes):
        buf = getattr(self, "_meeting_pcm", None)
        if buf is None:
            buf = self._meeting_pcm = bytearray()
        buf.extend(pcm)
        cap = self._AUDIO_WINDOW_S * 16000 * 2
        if len(buf) > cap:
            del buf[:len(buf) - cap]

    def meeting_audio_wav(self, seconds: int = 45) -> bytes:
        """Last N seconds of meeting audio as a WAV (16k mono 16-bit) — the
        native perception input for every agent. Empty until RTMS audio flows."""
        buf = getattr(self, "_meeting_pcm", None)
        if not buf:
            return b""
        pcm = bytes(buf[-(seconds * 16000 * 2):])
        import struct
        n = len(pcm)
        hdr = (b"RIFF" + struct.pack("<I", 36 + n) + b"WAVEfmt " +
               struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16) +
               b"data" + struct.pack("<I", n))
        return hdr + pcm

    async def _ava_heard(self, t: str):
        """Input-audio transcription delta = PROOF the model actually heard
        the meeting audio (the decisive hop in the ears pipeline). Coalesced
        into AvaEars feed events so telemetry shows 'audio reached Ava' even
        when she chooses not to respond."""
        e = self.ears
        e["heard"] = (e["heard"] + " " + t.strip())[-200:]
        e["last_heard"] = time.time()
        who = self.current_speaker or "banker"
        if not getattr(self, "_rtms_attached", False):
            await self.broadcast({"type": "caption", "who": who, "text": t})
            self._heard_buf = getattr(self, "_heard_buf", "") + t
            if getattr(self, "_heard_flush_task", None):
                self._heard_flush_task.cancel()

            async def _flush_heard(spk: str):
                await asyncio.sleep(1.4)
                txt = getattr(self, "_heard_buf", "").strip()
                self._heard_buf = ""
                if len(txt) >= 3:
                    await self.ingest_meeting_speech(spk, txt)

            self._heard_flush_task = asyncio.create_task(_flush_heard(who))

    async def ears_pulse(self, every: float = 15.0):
        """SOLO mode: SHORT ear telemetry — one 'audio flowing' confirmation
        when the pipeline first proves healthy, then feed notes ONLY on
        anomalies (no audio arriving, everything gated, send errors). Deep
        counters stay on GET /rooms/{id}/ears."""
        prev = dict(self.ears)
        confirmed = False
        while not getattr(self, "_closed_flag", False):
            await asyncio.sleep(every)
            e = self.ears
            d = {k: e[k] - prev[k] for k in
                 ("in_bytes", "sent_bytes", "drop_gate", "drop_ai", "send_err")}
            prev = dict(e)
            if d["in_bytes"] == 0:
                continue                      # silence in the meeting — normal
            if not confirmed and d["sent_bytes"] > 0:
                confirmed = True
                await self._feed("system", {"event": "note", "text":
                    "✓ EARS healthy — meeting audio is reaching Bridget."})
                continue
            if d["send_err"] > 0:
                await self._feed("system", {"event": "note", "text":
                    f"⚠ EARS: {d['send_err']} send errors to Ava "
                    f"({e['last_err'][:60]})"})
            elif d["sent_bytes"] == 0 and d["in_bytes"] > 60_000:
                await self._feed("system", {"event": "note", "text":
                    f"⚠ EARS: audio arriving ({d['in_bytes']//1024}KB) but "
                    f"NONE reached Ava (gated {d['drop_gate']//1024}KB, "
                    f"AI-dropped {d['drop_ai']//1024}KB)"})

    async def ingest_chat(self, speaker: str, text: str):
        """A human typed in Zoom chat, delivered via RTMS (msg_type 18) — the
        SDK-mouth replacement for the old Recall chat webhook. J1 consent
        detection + Ava answers OUT LOUD (she has no chat-post channel in SDK
        mode, so she voices the reply through her live session)."""
        txt = (text or "").strip()
        if len(txt) < 2:
            return
        # ignore our own bots' chat echoes
        if any(b in (speaker or "") for b in self._AI_SPK):
            return
        await self._feed("system", {"event": "note",
                                     "text": f"💬 chat from {speaker or 'participant'}: {txt[:90]}"})
        if not self.consents["banker"] and self._CONSENT_RX.search(txt):
            await self.grant_consent("banker")
            return
        if self.companion:
            asyncio.create_task(self._speak(
                "companion",
                f"[Zoom chat message from {speaker or 'a participant'}]: \"{txt}\" "
                f"— answer it out loud now, briefly and naturally (1-2 sentences)."))

    async def ingest_external_audio(self, pcm: bytes, speaker: str = "Zoom"):
        """RTMS meeting audio -> Ava's native ears (model VAD). Two guards keep
        her from hearing herself/the client bot and looping:
        (1) speaker filter: drop any diarized frame from an AI participant;
        (2) half-duplex: drop ALL audio while she is producing output +2.5s
        tail (covers the bridge->bot->Zoom->RTMS round-trip echo of her voice).
        The 'producing' flag is set on her first output part, so it works on
        the VAD path where _turn_t0 is never set."""
        if getattr(self, "hold_floor", False) or getattr(self, "mute_fleet", False) or getattr(self, "floor", "") == "human_takeover":
            return
        # a human is audibly in the room -> release the armed session even if the
        # cockpit never fired meeting_started (e.g. the prospect joined first)
        if self.live_gate_pending and not any(
                b in (speaker or "") for b in self._AI_SPK):
            asyncio.create_task(self.release_live_gate("human audio detected"))
        self._buf_audio(pcm)                 # feed the native-audio agent fleet
        e = self.ears
        e["in_pkts"] += 1
        e["in_bytes"] += len(pcm)
        e["last_in"] = time.time()
        if getattr(self, "ava_live", False) and self.companion:
            is_ai = any(b in (speaker or "") for b in self._AI_SPK)
            if not is_ai and not self._speaker_is_generic(speaker):
                # Only mark speaker active when frame has audible speech energy (not silent room hum)
                import array as _arr
                _samples = _arr.array("h")
                _samples.frombytes(pcm[: min(len(pcm) & ~1, 4096)])
                _rms = (sum(s * s for s in _samples) / max(1, len(_samples))) ** 0.5 if _samples else 0
                if _rms >= 260:
                    if speaker not in self.roster:
                        self.roster.append(speaker)
                        self._log_event("participant_heard", speaker)
                    asyncio.create_task(self._mark_speaker(speaker))
            # Echo gate keys on VOICING (words actually being spoken), NEVER
            # on `producing`: avatar sessions stream idle VIDEO continuously,
            # so producing is ~always True and gated 100% of meeting audio —
            # the exact deafness the ears pulse caught (in>0, sent=0).
            # BARGE-IN ARCHITECTURE (2026-08-08): the LIVE AGENT is the traffic
            # recipient — human audio streams to it CONTINUOUSLY, including while
            # she is speaking, so its own VAD can detect the interruption and stop
            # her mid-sentence. Previously a blanket `busy` gate dropped every
            # frame while she voiced, which made barge-in physically impossible
            # (the model never heard the interrupter).
            # Echo safety now comes from IDENTITY, not silence: RTMS multi-stream
            # labels each frame with its speaker, so her own audio is dropped by
            # the is_ai check. The old time-tail gate only survives for frames we
            # cannot attribute (mixed/nameless fallback).
            named = bool(speaker) and not self._speaker_is_generic(speaker)
            busy = (getattr(self.companion, "voicing", False)
                    or getattr(self.companion, "_speaking", False))
            if is_ai:
                e["drop_ai"] += len(pcm)
                if busy:
                    self._ava_spoke_t = time.monotonic()
            elif busy and not named:
                # unattributable frame while she speaks: could be her echo — the
                # only case where we still gate.
                self._ava_spoke_t = time.monotonic()
                e["drop_gate"] += len(pcm)
            elif named or (time.monotonic() - getattr(self, "_ava_spoke_t", 0)) > 1.2:
                # her own voice is already filtered by the is_ai speaker check
                # above, so a SHORT 1.2s tail is enough for echo — a long tail
                # was eating the user's reply and making Ava 'miss' turns.
                sess_up = getattr(self.companion, "_sess", None) is not None
                if not sess_up:
                    # session reconnecting -> DON'T lose the user's words; buffer
                    # (cap ~4s @16k mono = 128KB) and flush the instant it's back.
                    self._pcm_pending.extend(pcm)
                    if len(self._pcm_pending) > 128_000:
                        del self._pcm_pending[:len(self._pcm_pending) - 128_000]
                    e["drop_gate"] += 0
                else:
                    try:
                        if self._pcm_pending:
                            await self.companion.send_pcm(bytes(self._pcm_pending))
                            self._pcm_pending = bytearray()
                        await self.companion.send_pcm(pcm)
                        # keepalive stamp HERE, not at ingest: only audio that
                        # actually REACHES the model counts — gated echo frames
                        # were muting the keepalive while the model's input
                        # starved, so the server idle-closed the stream MID-TURN
                        # (the 'speaks then stutters then continues' loop)
                        self._last_ear_audio = time.monotonic()
                        e["sent_bytes"] += len(pcm)
                        e["last_sent"] = time.time()
                    except Exception as ex:
                        e["send_err"] += 1
                        e["last_err"] = str(ex)[:120]
            else:
                e["drop_gate"] += len(pcm)
        import base64 as _b64
        await self.broadcast({"type": "audio", "who": "external",
                              "speaker": speaker,
                              "pcm": _b64.b64encode(pcm).decode()})

    _BOT_NAMES = ("(AI)", "Ava", "Kairos", "Bridget")

    async def ingest_external_transcript(self, text: str, speaker: str = "Speaker"):
        if not text.strip():
            return
        # v5 demo rooms: Zoom's native diarized transcript IS the ears for the
        # brains — humans refresh the lanes; our own bots' lines are recorded
        # by their own paths, never re-ingested (echo/self-trigger guard)
        if getattr(self, "stage_stream_on", False):
            if any(b in (speaker or "") for b in self._BOT_NAMES):
                return
            await self.ingest_meeting_speech(speaker, text.strip())
            return
        self.current_speaker = speaker
        await self.broadcast({"type": "caption", "who": speaker, "text": text})
        await self._feed("turn", {"who": speaker, "text": text})
        if self.j4_armed:
            hit = __import__("sentinel").regex_check(text)
            if hit.get("trip"):
                await self._trip(hit, text)
        self._on_prospect_turn(text)
        # Zoom-companion mode: the Companion CONVERSES in the real meeting —
        # it hears every line; when addressed it answers out loud (the browser
        # plays its voice; the host shares computer sound into Zoom so every
        # participant hears it). Zoom policy bans bot participants — the human
        # host carries the AI's voice, which is allowed.
        if getattr(self, "zoom_companion", False) and self.companion:
            import workers

            async def _converse():
                try:
                    addr = await workers.classify_addressee(text, self)
                    if addr == "companion":
                        await self.companion.send_text_turn(
                            f"{speaker} (speaking in the live Zoom meeting) says: "
                            f"\"{text}\" — respond helpfully, briefly, out loud.",
                            role_label=speaker)
                    else:
                        await self.companion.inject_context(
                            f"[Zoom meeting] {speaker}: {text}")
                except Exception:
                    pass
            asyncio.create_task(_converse())

    # ---------- AVA LIVE-CONVERSE (the fluid loop) ----------
    async def start_ava_live(self):
        """Ava, fully live: meeting audio streams INTO her session (via the
        public stream relay), the model's own VAD turns it into responses, and
        her video/audio push straight back out over WebSocket to her bot page.
        Nothing else runs in this mode — she is the whole show."""
        import personas
        sys = (personas.COMPANION_SYS
               + "\n\nYou are LIVE in a real Zoom meeting. You have NOT been briefed on "
                 "who is attending or which company they are from — learn all of that "
                 "from what people say and from their [speaking: NAME] markers. You HEAR participants "
                 "through your ears (audio). Keep EVERY reply to 1-2 short "
                 "sentences unless explicitly asked for detail — brevity is "
                 "critical for a natural, low-latency conversation. If a remark "
                 "clearly isn't for you, give a very short acknowledgement or "
                 "stay quiet. Never invent facts.\n"
                 "SPEAKER AWARENESS: you receive '[speaking: NAME]' markers that "
                 "tell you who is talking right now (audio alone doesn't carry "
                 "names). Address people by their name when you answer them. If a "
                 "marker lists MULTIPLE names, more than one person is talking at "
                 "once — acknowledge that and ask who should go first, or answer "
                 "the clearest question. Use the names you've been given; never "
                 "guess a name you haven't seen.\n"
                 "STAGE DIRECTIONS ARE PRIVATE: any bracketed text you receive — "
                 "[speaking: X], [BANKER CORRECTION], [PRIVATE ...], any [..] — "
                 "is silent stage direction for YOU only. NEVER say bracketed "
                 "text aloud, never read it back, never mention that you "
                 "received it. Speak only your own natural words.\n"
                 "WAIT FOR HUMANS TO SPEAK FIRST: When you join the stage, remain "
                 "silent and attentive in idle video until a human participant "
                 "greets you, introduces you, or asks a question. Never start "
                 "talking unprompted to an empty or silent room.")
        if self.solo:
            sys += ("\n\n[TEST MODE] Consent has already been granted for this "
                    "session — never ask for permission or consent. Answer any "
                    "greeting or question directly, briefly and helpfully.")
        self.companion = LiveSession(
            name="Companion", model=self._live_model(),
            system_instruction=sys, voice_name=config.AVA_VOICE,
            room_id=self.room_id, avatar_name=config.AVA_AVATAR, auto_vad=True,
            on_video=self._companion_video,
            on_output_tx=lambda t: self._companion_tx(t),
            on_input_tx=lambda t: self._ava_heard(t),
            on_interrupted=self._ava_interrupted,
            on_turn_complete=self._companion_turn_done)
        self.companion.on_stream_start = (
            lambda: self._stage_mark_restart("companion"))
        await self.companion.start()
        self.ava_live = True
        self._ava_spoke_t = 0.0
        # EAR KEEPALIVE (2026-08-19 stutter root cause): the live avatar
        # stream is closed by the server after ~15s WITHOUT audio input —
        # proven by restart cadence: solo/quiet zoomv1 churned every 13-17s
        # (each churn = 1-3s frozen face in Zoom) while webv2 with a
        # CONTINUOUS mic tone streamed 90s+ with zero restarts. Zoom RTMS goes
        # quiet between speech and the half-duplex gate mutes her ears while
        # she talks, so gaps >15s are routine mid-meeting. Feed digital
        # silence whenever no real audio has flowed for 250ms — silence never
        # trips VAD, but it keeps the input stream (and thus her fMP4 output
        # stream) alive.
        self._last_ear_audio = time.monotonic()
        async def _ear_keepalive():
            quiet = b"\x00" * 3200            # 100ms @ 16k mono s16le
            while not getattr(self, "_closed_flag", False) and self.ava_live:
                try:
                    s = self.companion
                    if (s and not getattr(s, "_suspended", False)
                            and time.monotonic() - self._last_ear_audio > 0.25):
                        await s.send_pcm(quiet)
                except Exception:
                    pass
                await asyncio.sleep(0.1)
        self._ear_keepalive_task = asyncio.create_task(_ear_keepalive())
        await self._feed("system", {"event": "ava_live_ready"})
        # ears = Zoom RTMS (bridge dials out; ingest_external_audio feeds her);
        # mouth = _companion_video -> Firestore hot path -> her bot page.
        # No public relay needed — the allUsers design is dead.

    async def _stream_link_loop(self):
        """Duplex link to the public relay: meeting audio in, Ava's media out.
        The bridge DIALS OUT — no inbound hole in the IAP posture."""
        import websockets
        if not getattr(self, "stream_key", ""):
            def _key():
                from google.cloud import secretmanager
                c = secretmanager.SecretManagerServiceClient()
                import config as _c
                return c.access_secret_version(name=(
                    f"projects/{_c.PROJECT_ID}/secrets/paxtonprospect-stream-key/"
                    f"versions/latest")).payload.data.decode()
            try:
                self.stream_key = await asyncio.to_thread(_key)
            except Exception:
                self.stream_key = ""
        url = (f"wss://paxtonprospect-stream-229567506693.us-east4.run.app"
               f"/bridge/{self.room_id}?key={self.stream_key}")
        while not getattr(self, "_closed_flag", False):
            try:
                async with websockets.connect(url, max_size=8_000_000,
                                              ping_interval=15) as ws:
                    self._stream_ws = ws
                    await self._feed("system", {"event": "stream_link_up"})
                    async for raw in ws:
                        try:
                            m = json.loads(raw)
                        except Exception:
                            continue
                        if m.get("event") != "audio_separate_raw.data":
                            continue
                        d = (m.get("data") or {}).get("data") or {}
                        name = ((d.get("participant") or {}).get("name")) or ""
                        if "(AI)" in name or name.startswith("Ava"):
                            continue            # never her own or the client's voice
                        if self.companion and getattr(self.companion, "_speaking", False):
                            continue            # half-duplex: don't feed while talking
                        buf = d.get("buffer")
                        if buf and self.companion and self.companion._sess:
                            try:
                                await self.companion.send_pcm(base64.b64decode(buf))
                            except Exception:
                                pass
            except Exception:
                self._stream_ws = None
                await asyncio.sleep(2)

    def _stream_push(self, who: str, chunk: bytes):
        """Push a media chunk out the duplex link (fire-and-forget)."""
        ws = getattr(self, "_stream_ws", None)
        if ws is not None:
            marker = b"\x01" if who == "companion" else b"\x02"
            asyncio.create_task(self._stream_send(ws, marker + chunk))

    async def _stream_send(self, ws, frame: bytes):
        try:
            await ws.send(frame)
        except Exception:
            pass

    # ---------- Zoom hears the banker (Recall realtime -> Firestore -> here) ----------
    def start_zoom_listener(self):
        """The Recall transcript webhook lands on the OPS twin; this room may
        live on the IAP service. The queue is Firestore — poll it."""
        if getattr(self, "_zoom_listen_task", None):
            return
        self._zoom_listen_task = asyncio.create_task(self._zoom_listen_loop())

    async def _zoom_listen_loop(self):
        seen: set = set()
        t0 = time.time()
        while True:
            await asyncio.sleep(0.8)
            try:
                rows = await asyncio.get_running_loop().run_in_executor(
                    _io_pool, tools.zoom_speech_poll, self.room_id)
                rows.sort(key=lambda r: r[1].get("epoch", 0))
                for rid, d in rows:
                    if rid in seen:
                        continue
                    seen.add(rid)
                    if d.get("epoch", 0) < t0 or d.get("bot"):
                        continue          # backlog / our own AI voices
                    txt = (d.get("text") or "").strip()
                    if len(txt) < 2:
                        continue
                    if d.get("src") == "chat":
                        # deterministic J1: a HUMAN typing consent in Zoom chat
                        if (not self.consents["banker"]
                                and self._CONSENT_RX.search(txt)):
                            await self._feed("system", {"event": "note",
                                "text": f"Consent received in Zoom chat from "
                                        f"{d.get('speaker', 'participant')}."})
                            await self.grant_consent("banker")
                        elif self.solo and self.companion:
                            # SOLO TEST: chat ping -> Ava answers OUT LOUD.
                            # _speak's auto_vad path emits an AvaLive perf span
                            # (incl. true first-word TTFT) per reply = the
                            # user's response-time gauge in Live Ops.
                            spk = d.get("speaker", "a participant")
                            await self._feed("system", {"event": "note",
                                "text": f"Chat ping from {spk}: {txt[:90]}"})
                            asyncio.create_task(self._speak(
                                "companion",
                                f"[Zoom chat message from {spk}]: \"{txt}\" — "
                                f"answer it out loud now, briefly and "
                                f"naturally (1-2 sentences)."))
                        continue
                    await self._feed("system", {"event": "zoom_heard",
                                                "speaker": d.get("speaker", ""),
                                                "text": txt[:160]})
                    await self.ingest_meeting_speech(d.get("speaker", ""), txt)
            except asyncio.CancelledError:
                return
            except Exception:
                pass

    async def close(self):
        self._closed_flag = True
        self.presenting = None
        for ws in list(getattr(self, "_pipes", {})):
            self._drop_pipe(ws)          # wake + release every sender task
        await self._write_session_index()     # findable record of THIS meeting
        for t in self._relay_task.values():
            t.cancel()
        self._relay_task, self._relay_q = {}, {}
        # MONEY-CRITICAL: this room's Recall bots die with it, no matter which
        # path closed us (reaper, /rooms/{id}/close, /reset, stop_all)
        if self.recall_bot_ids and self.recall_key:
            import httpx
            for bid in list(self.recall_bot_ids):
                try:
                    async with httpx.AsyncClient() as cl:
                        await cl.post(
                            f"https://us-west-2.recall.ai/api/v1/bot/{bid}/leave_call/",
                            headers={"Authorization": f"Token {self.recall_key}"},
                            timeout=15)
                except Exception:
                    pass
        if getattr(self, "_stream_task", None):
            self._stream_task.cancel()
            self._stream_task = None
        if getattr(self, "_zoom_listen_task", None):
            self._zoom_listen_task.cancel()
            self._zoom_listen_task = None
        if self._watchdog:
            self._watchdog.cancel()
            self._watchdog = None
        if self._stall_task:
            self._stall_task.cancel()
            self._stall_task = None
        self.floor = "concluded"
        for s in (self.companion, self.simulator):
            if s:
                await s.close()
