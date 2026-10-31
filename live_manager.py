"""Live API session manager for the Companion (and Simulator voice mode).

Owns: connect/reconnect with self-managed continuity (transcript replay — no
native resumption handles on this preview path, proven in G1 spikes), manual
activity signaling, tool-call dispatch, interrupt, and per-turn telemetry spans
with full-content capture.
"""
import asyncio
import os
import json
import time
import uuid

from google import genai
from google.genai import types
from opentelemetry import trace

import config
from telemetry import clip, set_genai_attrs

client = genai.Client(vertexai=True, project=config.PROJECT_ID, location=config.LIVE_LOCATION)

# ---- customized-avatar aliasing -------------------------------------------
# TARGET personas are custom reference faces (config.AVATAR_CUSTOM_REFS), but
# the feature is a separate Google private-preview allowlist this project is
# not on yet. Every avatar connect tries the custom ref first; the server's
# 1007 "not allowlisted for customized avatar" flips _CUSTOM_BLOCKED for the
# process and all sessions fall back to the preset names transparently. The
# day Google allowlists the project, the custom faces go live on their own.
_CUSTOM_REFS: dict = {}      # avatar_name -> (mime, bytes), loaded lazily once
_CUSTOM_BLOCKED = False


def _load_custom_refs():
    global _CUSTOM_REFS
    if _CUSTOM_REFS:
        return
    for name, path in getattr(config, "AVATAR_CUSTOM_REFS", {}).items():
        try:
            with open(path, "rb") as f:
                _CUSTOM_REFS[name] = ("image/jpeg", f.read())
        except Exception:
            pass
    _CUSTOM_REFS.setdefault("__loaded__", ("", b""))


def _avatar_config(name: str) -> types.AvatarConfig:
    _load_custom_refs()
    # output bitrate caps ride every avatar session (live + one-shot renders)
    rates = {}
    if getattr(config, "AVA_VIDEO_BITRATE", 0) > 0:
        rates["video_bitrate_bps"] = config.AVA_VIDEO_BITRATE
    if getattr(config, "AVA_AUDIO_BITRATE", 0) > 0:
        rates["audio_bitrate_bps"] = config.AVA_AUDIO_BITRATE
    ref = None if _CUSTOM_BLOCKED else _CUSTOM_REFS.get(name)
    if ref and ref[1]:
        return types.AvatarConfig(customized_avatar=types.CustomizedAvatar(
            image_mime_type=ref[0], image_data=ref[1]), **rates)
    return types.AvatarConfig(avatar_name=name, **rates)


def _custom_rejected(exc: Exception) -> bool:
    """True (and flips the process-wide fallback) when the server refused the
    customized-avatar reference for allowlist/safety reasons."""
    global _CUSTOM_BLOCKED
    s = str(exc)
    if ("not allowlisted for customized avatar" in s
            or "Avatar reference image was blocked" in s):
        _CUSTOM_BLOCKED = True
        return True
    return False


tracer = trace.get_tracer("paxtonprospect-bridge")


class LiveSession:
    """One Live API connection with reconnect-and-replay continuity."""

    def __init__(self, *, name: str, model: str, system_instruction: str,
                 modality: str = "AUDIO", tools=None, room_id: str = "",
                 voice_name: str = "", avatar_name: str = "",
                 auto_vad: bool = False,
                 on_audio=None, on_text=None, on_input_tx=None, on_output_tx=None,
                 on_interrupted=None, on_tool_call=None, on_turn_complete=None,
                 on_video=None):
        self.auto_vad = auto_vad             # live-converse: model detects turns
        self.name = name
        self.model = model
        self.voice_name = voice_name
        self.avatar_name = avatar_name       # non-empty -> Gemini Live VIDEO avatar
        self.on_video = on_video
        self.system_instruction = system_instruction
        self.modality = modality
        self.tools = tools or []
        self.room_id = room_id
        self.session_id = f"{room_id}:{name}:{uuid.uuid4().hex[:8]}"
        self.on_audio = on_audio
        self.on_text = on_text
        self.on_input_tx = on_input_tx
        self.on_output_tx = on_output_tx
        self.on_interrupted = on_interrupted
        self.on_tool_call = on_tool_call
        self.on_turn_complete = on_turn_complete
        self.transcript: list[dict] = []   # [{"role","text","t"}]
        self._sess = None
        self._ctx = None
        self._rx_task = None
        self._closed = False
        self._reconnect_lock = asyncio.Lock()
        self.on_stream_start = None  # room hook: a NEW raw fMP4 byte stream begins here
        self._speaking = False
        self._turn_open_tx = ""
        self._turn_in_tx = ""
        self._turn_t0 = None
        self._first_audio_ms = None
        self.last_ttft_ms = None   # persists past turn end (perf reads it)
        self.last_turn_ms = None   # duration of her last SPOKEN turn (first word->end)
        self._turn_wall0 = None    # wall time of her first word this turn
        self.generation = 0  # bumped on every (re)connect
        self._suspended = False  # baton parked -> auto-reconnects must not fire
        self.events: list = []   # in-memory forensics ring (tests read this)
        self.turn_cuts = 0       # streams the server killed MID-SENTENCE
        self.producing = False   # True from first output part until turn end
                                 # (works on the VAD path where _turn_t0 is None)
        self.voicing = False     # True ONLY while she is actually SPEAKING
                                 # (first output-transcription word -> turn end).
                                 # The half-duplex ear gate keys on THIS, never
                                 # on `producing`: avatar sessions stream idle
                                 # VIDEO continuously, so producing is ~always
                                 # True and would keep the gate shut forever.
        self._ctx_pending: list = []  # context hints deferred during her turn
        self._resume_handle = None  # rolling session-resumption handle
                                    # (avatar-hybrid-vad pattern: transparent
                                    # resumption always requested; this model
                                    # issues no handles today — probed
                                    # 2026-08-19 — so transcript-replay stays
                                    # the working fallback until Google flips it)
        self._rx_count = 0   # server messages received (zombie-stream detector)
        self._last_rx = 0.0  # monotonic time of last server message

    def _config(self):
        # Session resumption, reference-style: always request transparent
        # resumption; reuse the rolling handle when the server has issued one.
        _resume = types.SessionResumptionConfig(
            transparent=True,
            **({"handle": self._resume_handle} if self._resume_handle else {}))
        # Avatar mode: Gemini Live streams fragmented MP4 (audio+video muxed);
        # output transcription still delivers the spoken words for scoring.
        if self.avatar_name:
            if self.auto_vad:
                # LIVE-CONVERSE avatar: meeting audio streams in continuously,
                # the model's own VAD decides turns — this is what makes Ava
                # respond to speech with no transcription wait
                return types.LiveConnectConfig(
                    response_modalities=["VIDEO"],
                    session_resumption=_resume,
                    system_instruction=self.system_instruction,
                    output_audio_transcription={},
                    input_audio_transcription={},
                    **({"speech_config": types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=self.voice_name)))} if self.voice_name else {}),
                    avatar_config=_avatar_config(self.avatar_name),
                    # BARGE-IN TUNING (aligned to avatar-hybrid-vad reference,
                    # 2026-08-19 — its params are vetted and taken as given):
                    # START_SENSITIVITY_LOW makes the interrupt harder to trip,
                    # so avatar bleed / room noise doesn't false-start a barge-in.
                    # END_SENSITIVITY_LOW + silence_duration_ms=800 is the
                    # reference combo: the firm 800ms silence bound ends turns,
                    # not an eager sensitivity — steadier turn-taking with the
                    # client-side echo gate in front.
                    realtime_input_config=types.RealtimeInputConfig(
                        automatic_activity_detection=types.AutomaticActivityDetection(
                            start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_LOW,
                            end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
                            # server VAD safety net (2026-08-19 optimization pass;
                            # probed OK on gemini-3.5-flash-live-preview): a firm
                            # 800ms end-of-turn silence bound + minimal prefix pad
                            prefix_padding_ms=20,
                            silence_duration_ms=800)),
                    # affective dialog: SUPPORTED here but env-gated OFF — the
                    # live backend hard-rejects it today ("not yet supported on
                    # Beyond backend", probed 2026-08-19) and it was rejected on
                    # the avatar surface by the expression bench. Flip
                    # CP_AFFECTIVE_DIALOG=1 only when the backend accepts it.
                    **({"enable_affective_dialog": True}
                       if os.environ.get("CP_AFFECTIVE_DIALOG") == "1" else {}),
                    context_window_compression=types.ContextWindowCompressionConfig(
                        sliding_window=types.SlidingWindow(target_tokens=30000),
                        trigger_tokens=40000))
            return types.LiveConnectConfig(
                response_modalities=["VIDEO"],
                session_resumption=_resume,
                system_instruction=self.system_instruction,
                output_audio_transcription={},
                # voice must be pinned in avatar mode too — Vera's default came
                # out MALE in the live demo
                **({"speech_config": types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=self.voice_name)))} if self.voice_name else {}),
                avatar_config=_avatar_config(self.avatar_name),
                context_window_compression=types.ContextWindowCompressionConfig(
                    sliding_window=types.SlidingWindow(target_tokens=30000),
                    trigger_tokens=40000),
                realtime_input_config=types.RealtimeInputConfig(
                    automatic_activity_detection=types.AutomaticActivityDetection(disabled=True)),
                **({"tools": self.tools} if self.tools else {}))
        cfg = dict(
            response_modalities=[self.modality],
            session_resumption=_resume,
            system_instruction=self.system_instruction,
            context_window_compression=types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow(target_tokens=30000),
                trigger_tokens=40000),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(disabled=True)),
        )
        if self.modality == "AUDIO":
            cfg["output_audio_transcription"] = {}
            cfg["input_audio_transcription"] = {}
            if self.voice_name:
                cfg["speech_config"] = types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=self.voice_name)))
        if self.tools:
            cfg["tools"] = self.tools
        return types.LiveConnectConfig(**cfg)

    def _ev(self, ev: str, **kw):
        self.events.append({"t": round(time.monotonic() % 10000, 2), "ev": ev, **kw})
        if len(self.events) > 300:
            del self.events[:100]

    async def start(self):
        await self._connect(replay=False)

    async def _connect(self, replay: bool):
        if self._closed or self._suspended:
            return          # closed room / baton-parked session must stay down
        self.generation += 1
        gen = self.generation
        # fresh stream => no output can be flowing. If a stream died between
        # her first output part and turn_complete, `producing` stayed True and
        # the room's half-duplex gate ate ALL inbound audio forever (deaf Ava,
        # chat still fine — text is ungated). Reset on every (re)connect.
        self.producing = False
        self._speaking = False
        self.voicing = False
        if replay and self.transcript and self.auto_vad and not self._resume_handle:
            # NEVER send an open client_content seed on a VAD session: a turn
            # left hanging (turn_complete=False) suppresses the automatic-VAD
            # commit path -> she stops answering AUDIO after every reconnect
            # while explicit text turns still work. Fold context into the
            # system instruction instead (rebuilt fresh each reconnect).
            base = getattr(self, "_base_sys", None) or self.system_instruction
            self._base_sys = base
            recent = self.transcript[-12:]
            self.system_instruction = (
                base + "\n\n[Session reconnected mid-meeting. Conversation so "
                "far — continue naturally, do NOT greet again, do NOT repeat "
                "yourself:]\n"
                + "\n".join(f"{t['role']}: {t['text']}" for t in recent))
        with tracer.start_as_current_span("live_session.connect") as span:
            span.set_attribute("gen_ai.conversation.id", self.session_id)
            span.set_attribute("live.room_id", self.room_id)
            span.set_attribute("live.agent", self.name)
            span.set_attribute("live.replay", replay)
            span.set_attribute("gen_ai.request.model", self.model)
            self._ev("connect", replay=replay, gen=gen)
            try:
                self._ctx = client.aio.live.connect(model=self.model, config=self._config())
                self._sess = await self._ctx.__aenter__()
            except Exception as e:
                if self._resume_handle:
                    # stale/rejected resumption handle: drop it and reconnect
                    # fresh (transcript replay covers continuity)
                    self._ev("resume_handle_rejected", err=str(e)[:80])
                    self._resume_handle = None
                    return await self._connect(replay)
                # custom-avatar ref refused (allowlist/safety) -> retry once on
                # the preset face; _custom_rejected flipped the process flag so
                # _config() now builds AvatarConfig(avatar_name=...)
                if not (self.avatar_name and _custom_rejected(e)):
                    raise
                self._ev("custom_avatar_fallback", err=str(e)[:80])
                self._ctx = client.aio.live.connect(model=self.model, config=self._config())
                self._sess = await self._ctx.__aenter__()
            self._last_rx = time.monotonic()   # fresh stream = fresh idle clock
            if self.on_stream_start:
                # every (re)connect emits a fresh ftyp+moov — viewers appending
                # the old and new streams back-to-back hit MSE decode errors, so
                # the room must flush the old tail and mark an init point FIRST
                try:
                    await self.on_stream_start()
                except Exception:
                    pass
            if replay and self.transcript and not self.auto_vad and not self._resume_handle:
                recent = self.transcript[-12:]
                seed = ("Session reconnected. Conversation so far (continue naturally, "
                        "do NOT greet again, do NOT repeat yourself):\n" +
                        "\n".join(f"{t['role']}: {t['text']}" for t in recent))
                await self._sess.send_client_content(
                    turns=types.Content(role="user", parts=[types.Part(text=seed)]),
                    turn_complete=False)
                span.set_attribute("live.replayed_turns", len(recent))
            elif replay and self.transcript:
                span.set_attribute("live.replay_via", "system_instruction")
        self._rx_task = asyncio.create_task(self._rx_loop(gen))

    async def _rx_loop(self, gen: int):
        reason = "server_closed_stream"   # graceful close ends async-for WITHOUT exception
        try:
            async for msg in self._sess.receive():
                if self._closed or gen != self.generation:
                    return
                await self._handle(msg)
        except Exception as e:
            reason = str(e)[:300]
        self.producing = False        # stream is gone; nothing is flowing
        self.voicing = False
        if gen == self.generation and self._speaking:
            # server killed the stream MID-SENTENCE — flush the partial words
            # and mark the cut so Room._speak can re-deliver the full line
            self._speaking = False
            self.turn_cuts += 1
            self._ev("turn_cut", gen=gen, partial=(self._turn_open_tx or "")[:60])
            self._flush_turn(interrupted=True)
        self._ev("stream_end", reason=reason[:110], gen=gen,
                 stale=(self._closed or self._suspended or gen != self.generation))
        if self._closed or self._suspended or gen != self.generation:
            return
        # Either path (graceful close OR error) -> self-managed continuity.
        with tracer.start_as_current_span("live_session.reconnect") as span:
            span.set_attribute("gen_ai.conversation.id", self.session_id)
            span.set_attribute("live.reason", clip(reason))
        await asyncio.sleep(0.2)
        try:
            await self._connect(replay=True)
        except Exception as e2:
            with tracer.start_as_current_span("live_session.reconnect_failed") as s2:
                s2.set_attribute("live.error", clip(str(e2)[:300]))

    async def _guarded(self, op_name: str, coro_factory):
        """Run a send op; on failure reconnect-with-replay once and retry."""
        try:
            return await coro_factory()
        except Exception as e:
            gen_before = self.generation
            with tracer.start_as_current_span("live_session.send_retry") as span:
                span.set_attribute("gen_ai.conversation.id", self.session_id)
                span.set_attribute("live.op", op_name)
                span.set_attribute("live.reason", clip(str(e)[:300]))
            async with self._reconnect_lock:
                if self.generation == gen_before:   # nobody else reconnected meanwhile
                    await self._connect(replay=True)
            return await coro_factory()

    async def _handle(self, msg):
        self._rx_count += 1
        self._last_rx = time.monotonic()
        sru = getattr(msg, "session_resumption_update", None)
        if sru is not None and getattr(sru, "new_handle", None):
            self._resume_handle = sru.new_handle
            self._ev("resume_handle", n=len(sru.new_handle))
        sc = msg.server_content
        if msg.tool_call and self.on_tool_call:
            for fc in msg.tool_call.function_calls:
                with tracer.start_as_current_span(f"execute_tool {fc.name}") as span:
                    span.set_attribute("gen_ai.conversation.id", self.session_id)
                    span.set_attribute("gen_ai.tool.name", fc.name)
                    span.set_attribute("gen_ai.tool.call.arguments", clip(fc.args or {}))
                    result = await self.on_tool_call(fc.name, fc.args or {})
                    span.set_attribute("gen_ai.tool.call.result", clip(result))
                await self._sess.send_tool_response(function_responses=[
                    types.FunctionResponse(id=fc.id, name=fc.name, response=result)])
        if not sc:
            return
        if sc.model_turn:
            for p in sc.model_turn.parts:
                if p.inline_data and p.inline_data.data:
                    self.producing = True    # output flowing (VAD path too)
                    if not self._speaking and self._turn_t0:
                        # prompted turn only — idle avatar video is not speech
                        self._speaking = True
                        self._ev("first_media", gen=self.generation)
                        self._first_audio_ms = round((time.monotonic() - self._turn_t0) * 1000)
                        if not self.avatar_name:
                            self.last_ttft_ms = self._first_audio_ms
                    mime = p.inline_data.mime_type or ""
                    if mime.startswith("video") and self.on_video:
                        await self.on_video(p.inline_data.data)
                    elif self.on_audio:
                        await self.on_audio(p.inline_data.data)
                if p.text and self.on_text:
                    self._turn_open_tx += p.text
                    await self.on_text(p.text)
        if sc.input_transcription and sc.input_transcription.text:
            self._turn_in_tx += sc.input_transcription.text
            if self.on_input_tx:
                await self.on_input_tx(sc.input_transcription.text)
        if sc.output_transcription and sc.output_transcription.text:
            self.voicing = True
            if self._turn_wall0 is None:
                self._turn_wall0 = time.time()   # her first word this turn
            # TRUE TTFT = first spoken WORD after the turn was sent. First-media
            # is unreliable for avatars (idle video streams continuously in
            # auto-VAD), so anchor TTFT on the first output-transcription delta.
            if self._turn_t0 and not self._turn_open_tx:
                self.last_ttft_ms = round((time.monotonic() - self._turn_t0) * 1000)
            self._turn_open_tx += sc.output_transcription.text
            if self.on_output_tx:
                await self.on_output_tx(sc.output_transcription.text)
        if sc.interrupted:
            self._speaking = False
            self.producing = False
            self.voicing = False
            with tracer.start_as_current_span("turn.interrupted") as span:
                span.set_attribute("gen_ai.conversation.id", self.session_id)
                span.set_attribute("turn.partial_output", clip(self._turn_open_tx))
            if self.on_interrupted:
                await self.on_interrupted()
            self._flush_turn(interrupted=True)
        if sc.turn_complete:
            self._speaking = False
            self.producing = False
            self.voicing = False
            if self._turn_wall0:
                self.last_turn_ms = round((time.time() - self._turn_wall0) * 1000)
                self._turn_wall0 = None
            self._ev("turn_complete", gen=self.generation,
                     tx=(self._turn_open_tx or "")[:60])
            self._flush_turn(interrupted=False)
            if self.on_turn_complete:
                await self.on_turn_complete()
            asyncio.create_task(self._flush_ctx_pending())

    def _flush_turn(self, interrupted: bool):
        if self._turn_in_tx.strip():
            self.transcript.append({"role": "user", "text": self._turn_in_tx.strip(),
                                    "t": time.time()})
        if self._turn_open_tx.strip():
            self.transcript.append({"role": self.name, "text": self._turn_open_tx.strip(),
                                    "t": time.time()})
        with tracer.start_as_current_span("turn") as span:
            span.set_attribute("gen_ai.conversation.id", self.session_id)
            span.set_attribute("live.agent", self.name)
            span.set_attribute("turn.interrupted", interrupted)
            if self._first_audio_ms is not None:
                span.set_attribute("turn.first_audio_ms", self._first_audio_ms)
            set_genai_attrs(span, model=self.model,
                            input_messages=self._turn_in_tx or "",
                            output_messages=self._turn_open_tx or "")
        self._turn_in_tx, self._turn_open_tx = "", ""
        self._turn_t0, self._first_audio_ms = None, None

    # ---- inbound API used by the room ----
    async def activity_start(self):
        if self._sess is None:
            await self.resume()
        await self._sess.send_realtime_input(activity_start=types.ActivityStart())

    async def activity_end(self):
        await self._sess.send_realtime_input(activity_end=types.ActivityEnd())
        self._turn_t0 = time.monotonic()

    async def send_pcm(self, pcm: bytes):
        if self._sess is None:
            return
        await self._sess.send_realtime_input(
            audio=types.Blob(data=pcm, mime_type="audio/pcm;rate=16000"))

    async def suspend(self):
        """Drop the live connection but keep transcript + callbacks. The
        project allows only ONE live avatar session — the room passes a baton:
        suspend the idle avatar, resume (reconnect-with-replay) the speaker.
        The flag goes up FIRST: a graceful-close auto-reconnect racing this
        call must lose (it re-checks the flag inside _connect)."""
        self._suspended = True
        self._ev("suspend", had_sess=self._sess is not None)
        if self._sess is None:
            return
        with tracer.start_as_current_span("live_session.suspend") as span:
            span.set_attribute("gen_ai.conversation.id", self.session_id)
            span.set_attribute("live.agent", self.name)
        self.generation += 1          # orphan the rx loop: no auto-reconnect
        t, self._rx_task = self._rx_task, None
        if t:
            t.cancel()
        try:
            await self._ctx.__aexit__(None, None, None)
        except Exception:
            pass
        self._sess, self._ctx = None, None
        self._speaking = False

    async def resume(self):
        if self._closed:
            return
        self._suspended = False
        self._ev("resume", had_sess=self._sess is not None)
        if self._sess is not None:
            return
        gen = self.generation
        async with self._reconnect_lock:
            if self.generation == gen and self._sess is None:
                await self._connect(replay=True)

    async def ensure_fresh(self, max_idle: float = 45.0):
        """Avatar streams die quietly after sitting idle — if we've heard
        nothing from the server for max_idle seconds, rebuild the connection
        (with transcript replay) BEFORE a critical send instead of after a
        silent failure."""
        idle = time.monotonic() - max(self._last_rx, 0.0)
        if self._last_rx and idle > max_idle and not self._speaking:
            with tracer.start_as_current_span("live_session.refresh_stale") as span:
                span.set_attribute("gen_ai.conversation.id", self.session_id)
                span.set_attribute("live.idle_s", round(idle, 1))
            gen = self.generation
            async with self._reconnect_lock:
                if self.generation == gen:   # nobody else reconnected meanwhile
                    await self._connect(replay=True)

    async def send_text_turn(self, text: str, role_label: str = "user",
                             watchdog_s: float = 0):
        """watchdog_s > 0: if the server sends NOTHING back within that window
        (zombie stream — send 'succeeded' into a dead socket), rebuild the
        connection with replay and resend the turn exactly once."""
        if self._sess is None:
            await self.resume()       # suspended by the avatar baton
        if watchdog_s:
            await self.ensure_fresh()
        self._ev("send", text=text[:60], gen=self.generation)
        self.transcript.append({"role": role_label, "text": text, "t": time.time()})
        self._turn_t0 = time.monotonic()
        await self._guarded("send_text_turn", lambda: self._sess.send_client_content(
            turns=types.Content(role="user", parts=[types.Part(text=text)])))
        if watchdog_s:
            asyncio.create_task(self._turn_watchdog(self._rx_count, text, watchdog_s))

    async def _turn_watchdog(self, rx_mark: int, text: str, wait_s: float,
                             tries: int = 2):
        await asyncio.sleep(wait_s)
        if self._closed or self._rx_count != rx_mark:
            return                       # server responded — healthy
        self._ev("watchdog_fire", tries=tries)
        with tracer.start_as_current_span("live_session.zombie_resend") as span:
            span.set_attribute("gen_ai.conversation.id", self.session_id)
            span.set_attribute("live.agent", self.name)
            span.set_attribute("live.tries_left", tries)
        # the avatar slot frees LAZILY server-side: drop, wait, THEN reconnect
        await self.suspend()
        await asyncio.sleep(3)
        await self.resume()
        self._turn_t0 = time.monotonic()
        try:                             # resend WITHOUT re-appending transcript
            await self._sess.send_client_content(
                turns=types.Content(role="user", parts=[types.Part(text=text)]))
        except Exception:
            pass
        if tries > 1:
            asyncio.create_task(
                self._turn_watchdog(self._rx_count, text, wait_s, tries - 1))

    async def inject_context(self, text: str):
        """Non-turn context injection (speaker annotations, corrections, steering).
        BEST-EFFORT: it's a steering hint, never turn-critical — a failure (e.g.
        text realtime-input rejected on an avatar/auto-VAD session) must never
        break the caller. On any issue, stash it for the next reconnect replay.
        PARROT GUARD: text arriving while the model is producing/committing gets
        folded into her SPEECH (observed live: she voiced '[speaking: Banker]…').
        While she is producing, queue the hint and flush it after turn end."""
        if self.producing or self.voicing or self._speaking:
            self._ctx_pending.append(text)
            if len(self._ctx_pending) > 8:
                del self._ctx_pending[:len(self._ctx_pending) - 8]
            return
        try:
            if self._sess is None:
                self.transcript.append({"role": "context", "text": text, "t": time.time()})
                return
            await self._sess.send_realtime_input(text=text)
        except Exception:
            self.transcript.append({"role": "context", "text": text, "t": time.time()})

    async def _flush_ctx_pending(self):
        """Deliver context hints deferred during her turn. Speaker markers
        supersede each other — only the LATEST marker is sent; other hints
        (corrections, consent state) are delivered in order."""
        if not self._ctx_pending:
            return
        pend, self._ctx_pending = self._ctx_pending, []
        markers = [t for t in pend if t.startswith("[speaking")]
        keep = [t for t in pend if not t.startswith("[speaking")]
        if markers:
            keep.append(markers[-1])
        for t in keep:
            try:
                if self._sess is not None:
                    await self._sess.send_realtime_input(text=t)
            except Exception:
                self.transcript.append({"role": "context", "text": t, "t": time.time()})

    async def interrupt(self):
        """Hard stop of current generation (27ms proven). Caller decides what's next."""
        if self._sess is None:
            return
        await self._sess.send_realtime_input(activity_start=types.ActivityStart())

    async def close(self):
        self._closed = True
        try:
            if self._rx_task:
                self._rx_task.cancel()
            if self._ctx:
                await self._ctx.__aexit__(None, None, None)
        except Exception:
            pass

# ---------------- one-shot render farm primitives (client persona) ----------------
async def render_avatar_clip(avatar: str, voice: str, text: str,
                             timeout_s: float = 60.0, on_chunk=None,
                             model: str = "") -> dict:
    """Fresh session -> verbatim line -> complete fMP4 clip, VERIFIED (words
    present, not truncated). If on_chunk is given, each fMP4 fragment is handed
    over AS IT ARRIVES (streaming render — first word into Zoom ~2s instead of
    waiting for the whole clip). Spike: short ~5s, long ~13-16s, ~80% first-try."""
    t0 = time.monotonic()
    def _cfg():
        return types.LiveConnectConfig(
            response_modalities=["VIDEO"],
            system_instruction=("You deliver provided lines verbatim with natural, "
                                "warm delivery. Say ONLY the line given. Never add "
                                "or omit words."),
            output_audio_transcription={},
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))),
            avatar_config=_avatar_config(avatar),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(disabled=True)))
    buf, tx, done = bytearray(), "", False
    first_media = None
    t_send = time.monotonic()
    for _try in (0, 1):
        try:
            async with client.aio.live.connect(model=model or config.MODEL_LIVE,
                                               config=_cfg()) as sess:
                await sess.send_client_content(turns=types.Content(
                    role="user", parts=[types.Part(text=(
                        f"Say exactly this, verbatim, with natural delivery — nothing "
                        f"before or after: \"{text}\""))]))
                t_send = time.monotonic()
                try:
                    async for msg in sess.receive():
                        sc = msg.server_content
                        if not sc:
                            continue
                        if sc.model_turn:
                            for p in sc.model_turn.parts:
                                if p.inline_data and p.inline_data.data:
                                    if first_media is None:
                                        first_media = time.monotonic() - t_send
                                    buf.extend(p.inline_data.data)
                                    if on_chunk is not None:
                                        try:
                                            await on_chunk(p.inline_data.data)
                                        except Exception:
                                            pass
                        if sc.output_transcription and sc.output_transcription.text:
                            tx += sc.output_transcription.text
                        if sc.turn_complete:
                            done = True
                            break
                        if time.monotonic() - t_send > timeout_s:
                            break
                except Exception:
                    pass
            break
        except Exception as e:
            # connect-time custom-avatar rejection -> retry once on the preset
            if _try == 0 and _custom_rejected(e):
                continue
            raise
    want = {w.strip('.,—"?!').lower() for w in text.split() if len(w) > 3}
    got = {w.strip('.,—"?!').lower() for w in tx.split()}
    overlap = len(want & got) / max(1, len(want))
    dur = max(1.5, (time.monotonic() - t_send) - (first_media or 0))
    ok = done and len(buf) > 100_000 and overlap >= 0.7
    return {"ok": ok, "mp4": bytes(buf) if ok else b"", "tx": tx.strip(),
            "overlap": round(overlap, 2), "dur_s": round(dur, 1),
            "render_s": round(time.monotonic() - t0, 1)}


async def render_avatar_idle(avatar: str, voice: str, seconds: float = 8.0,
                             model: str = None) -> bytes:
    """Capture the avatar's UNPROMPTED idle animation from a fresh connect —
    used as the client's living portrait before/after clips."""
    def _cfg():
        return types.LiveConnectConfig(
            response_modalities=["VIDEO"],
            system_instruction="You are in a meeting, listening attentively.",
            output_audio_transcription={},
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))),
            avatar_config=_avatar_config(avatar),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(disabled=True)))
    buf = bytearray()
    t0 = time.monotonic()
    for _try in (0, 1):
        try:
            async with client.aio.live.connect(model=model or config.MODEL_LIVE,
                                               config=_cfg()) as sess:
                async for msg in sess.receive():
                    sc = msg.server_content
                    if sc and sc.model_turn:
                        for p in sc.model_turn.parts:
                            if p.inline_data and p.inline_data.data:
                                buf.extend(p.inline_data.data)
                    if time.monotonic() - t0 > seconds:
                        break
            break
        except Exception as e:
            if _try == 0 and _custom_rejected(e):
                continue      # custom ref refused at connect -> preset retry
            break             # idle capture is best-effort; keep old swallow
    return bytes(buf)
