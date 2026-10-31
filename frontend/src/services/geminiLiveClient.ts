/**
 * Bridget bridge client — the Bridget UI stitched onto the REAL engine.
 *
 * This file keeps the original Bridget event-bus API (components are unchanged)
 * but speaks the paxtonprospect bridge protocol instead of the old simulated
 * backend: one WebSocket to /ws/{room} carrying hello/caption/audio/video/perf
 * frames and {type:'feed', kind, payload} agent events from the live fleet
 * (Pulse sentiment, Panel insights, Sentinel guardrails, audit stream).
 *
 * What changed vs the original:
 *  - NO browser speech recognition (project directive: no STT — Zoom RTMS is
 *    the system's ears; the banker talks in the real Zoom meeting).
 *  - Ava's face+voice arrive as fragmented MP4 over the socket (MSE), not as
 *    a static image: bindAvatarVideo() pipes them into a <video> element and
 *    the analyser is fed from that element, so the waveform is her REAL voice.
 *  - "sessionId" is the bridge room id (zoom-XXXXXXXX), minted by
 *    startZoomMeeting().
 */

type TranscriptCallback = (speaker: string, text: string, stateUpdate?: any) => void;
type AudioCallback = (base64Audio: string) => void;
export type PresenceState = 'idle' | 'listening' | 'thinking' | 'speaking' | 'paused' | 'handoff';
type StateCallback = (state: PresenceState) => void;
export type ParticipantCallback = (data: { bankerConnected: boolean; prospectConnected: boolean; participantCount: number }) => void;
export type FeedCallback = (payload: any) => void;

let transcriptListeners: TranscriptCallback[] = [];
let audioListeners: AudioCallback[] = [];
let stateListeners: StateCallback[] = [];
let participantListeners: ParticipantCallback[] = [];
const feedListeners: Record<string, FeedCallback[]> = {};

export const onTranscriptUpdate = (cb: TranscriptCallback) => { transcriptListeners.push(cb); return () => { transcriptListeners = transcriptListeners.filter(l => l !== cb); }; };
export const onAudioChunk = (cb: AudioCallback) => { audioListeners.push(cb); return () => { audioListeners = audioListeners.filter(l => l !== cb); }; };
export const onStateChange = (cb: StateCallback) => { stateListeners.push(cb); return () => { stateListeners = stateListeners.filter(l => l !== cb); }; };
export const onParticipantChange = (cb: ParticipantCallback) => { participantListeners.push(cb); return () => { participantListeners = participantListeners.filter(l => l !== cb); }; };

/** Subscribe to a bridge feed kind: sentiment | insight | audit | consent |
 *  risk | approval | handoff | turn | agent_activity | grounding | system |
 *  perf(frames) */
export const onFeed = (kind: string, cb: FeedCallback) => {
  (feedListeners[kind] = feedListeners[kind] || []).push(cb);
  return () => { feedListeners[kind] = (feedListeners[kind] || []).filter(l => l !== cb); };
};
const emitFeed = (kind: string, payload: any) => (feedListeners[kind] || []).forEach(l => { try { l(payload); } catch {} });

// Trial-1 fixes: strip context markers the model echoes in its text channel
// ("[speaking: X] …"), and correct the two chronic ASR misspells in display.
const cleanTranscriptText = (t: string) => (t || '')
  .replace(/\[(?:speaking|log_event)[^\]]*\]\s*/gi, '')
  .replace(/\bChaos\b/g, 'Bridget')
  .replace(/\bKairos\b/g, 'Bridget')
  .replace(/\bCity(?=('s)?\s+(AI|advisor|services|bank|team|Bridget))\b/g, 'Paxton Bank');
const notifyTranscript = (speaker: string, text: string, s?: any) =>
  transcriptListeners.forEach(l => l(speaker, cleanTranscriptText(text), s));
const notifyAudioChunk = (b64: string) => audioListeners.forEach(l => l(b64));
let currentState: PresenceState = 'idle';
const notifyStateChange = (state: PresenceState) => { currentState = state; stateListeners.forEach(l => l(state)); };
export const getPresence = () => currentState;

// ---------------------------------------------------------------- origin ----
const origin = (): string =>
  (import.meta as any).env?.VITE_BACKEND_URL || window.location.origin;
const wsOrigin = () => origin().replace(/^http/, 'ws');

// ------------------------------------------------------------- connection ---
let socket: WebSocket | null = null;
let roomId = '';
let isConnected = false;
let reconnectAttempts = 0;
const MAX_RECONNECT_ATTEMPTS = 8;
let onTakeoverCallback: ((topic: string, text: string) => void) | null = null;
export const registerTakeoverHandler = (cb: (topic: string, text: string) => void) => { onTakeoverCallback = cb; };

export const getRoomId = () => roomId;

// ------------------------------------------------------- audio playback -----
// 24kHz PCM playback (sim/audio mode + prospect voice). In avatar mode Ava's
// voice arrives muxed in the fMP4 stream and plays through the <video> element
// bound by bindAvatarVideo(); both paths feed the shared analyser.
export let audioAnalyserNode: AnalyserNode | null = null;
let audioContext: AudioContext | null = null;
let masterGain: GainNode | null = null;
let zoomActive = false;
let myName = '';
let webMeetingActive = false;   // webv2 in-app meeting live (gates external-audio playback)

/** Zoom carries ALL meeting audio when a real meeting is live — the cockpit
 *  must go silent (else the user hears Bridget twice). The analyser keeps
 *  receiving signal (FFT/waveform stays alive) via a muted master gain. */
export const setZoomActive = (on: boolean) => {
  zoomActive = on;
  // masterGain alone silences output — the MediaElementSource reroutes the
  // <video> through the graph, so element .muted would ALSO silence the
  // analyser and flatline the waveform (vet finding P0-5).
  if (masterGain) masterGain.gain.value = on ? 0 : 1;
};
export const isZoomActive = () => zoomActive;

const ensureCtx = () => {
  if (!audioContext) {
    audioContext = new (window.AudioContext || (window as any).webkitAudioContext)({ sampleRate: 24000 });
    audioAnalyserNode = audioContext.createAnalyser();
    audioAnalyserNode.fftSize = 256;
    masterGain = audioContext.createGain();
    masterGain.gain.value = zoomActive ? 0 : 1;
    audioAnalyserNode.connect(masterGain);
    masterGain.connect(audioContext.destination);
  }
  if (audioContext.state === 'suspended') audioContext.resume();
  return audioContext;
};

export const initLiveAudioContext = async (): Promise<void> => { try { ensureCtx(); } catch (e) { console.error('[Bridget] AudioContext init failed', e); } };

// Buffered-lane PCM player (sanrepo StreamAudioPlayer design): prebuffer
// before starting, a scheduling-horizon cap, a fade-in on every (re)start so
// restarts never click, a fade-out flush for barge-in. One lane per content
// rate, both on the ONE shared context — createBuffer at the content rate and
// the browser resamples; no second clock to drift against.
type Lane = {
  q: Float32Array[]; qSec: number; next: number; live: boolean; need: number;
  srcs: Set<AudioBufferSourceNode>; drainT: number; gain: GainNode | null;
  push: (b64: string) => void; flush: () => void;
};
const LANE_MIN_START = 0.24;  // prebuffer before the first start
const LANE_REBUFFER = 0.12;   // smaller cushion after an underrun
const LANE_AHEAD = 0.48;      // never schedule further ahead than this
const LANE_LEAD = 0.05;       // restart lead: slack for the next late chunk
const LANE_FADE = 0.008;

const makeLane = (rate: number, drivesSpeaking: boolean): Lane => {
  const L: Lane = { q: [], qSec: 0, next: 0, live: false, need: LANE_MIN_START,
                    srcs: new Set(), drainT: 0, gain: null,
                    push: () => {}, flush: () => {} };
  const pump = () => {
    const ctx = audioContext;
    if (!ctx || ctx.state !== 'running') return;
    if (L.live && !L.q.length && ctx.currentTime > L.next) {
      L.live = false; L.need = LANE_REBUFFER; L.drainT = ctx.currentTime;
      if (drivesSpeaking) setTimeout(() => {
        if (!L.live && L.q.length === 0 && isConnected && currentState === 'speaking') notifyStateChange('listening');
      }, 1200);
    }
    if (!L.live) {
      if (L.qSec < L.need) return;
      L.live = true;
      L.next = ctx.currentTime + LANE_LEAD;
      L.gain = ctx.createGain();
      L.gain.gain.setValueAtTime(0, L.next);
      L.gain.gain.linearRampToValueAtTime(1, L.next + LANE_FADE);
      L.gain.connect(audioAnalyserNode!);
    }
    while (L.q.length && (L.next - ctx.currentTime) < LANE_AHEAD) {
      const f32 = L.q.shift()!;
      const dur = f32.length / rate;
      L.qSec -= dur;
      const buf = ctx.createBuffer(1, f32.length, rate);
      buf.copyToChannel(f32, 0);
      const s = ctx.createBufferSource();
      s.buffer = buf; s.connect(L.gain!);
      const at = Math.max(L.next, ctx.currentTime);
      s.start(at); L.next = at + dur;
      L.srcs.add(s); s.onended = () => { L.srcs.delete(s); pump(); };
    }
  };
  setInterval(pump, 100);
  L.push = (b64: string) => {
    const bin = atob(b64), n = bin.length >> 1;
    const f32 = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      f32[i] = (((bin.charCodeAt(2 * i) | (bin.charCodeAt(2 * i + 1) << 8)) << 16) >> 16) / 32768;
    }
    L.q.push(f32); L.qSec += n / rate;
    // backlog drift clamp: a live conversation must never build a delay
    while (L.qSec > 1.5 && L.q.length > 1) {
      const dropped = L.q.shift()!;
      L.qSec -= dropped.length / rate;
    }
    if (drivesSpeaking) notifyStateChange('speaking');
    pump();
  };
  L.flush = () => {
    const ctx = audioContext;
    if (ctx && L.gain) { try { L.gain.gain.setTargetAtTime(0, ctx.currentTime, 0.015); } catch {} }
    const old = [...L.srcs]; L.srcs.clear();
    setTimeout(() => old.forEach(s => { try { s.stop(); } catch {} }), 80);
    L.q = []; L.qSec = 0; L.live = false; L.need = LANE_REBUFFER;
    L.next = 0; L.gain = null;
  };
  return L;
};
let lane24: Lane | null = null;   // Bridget's voice (sim/audio mode)
let lane16: Lane | null = null;   // other humans' relayed mics (webv2)

const playIncomingPCMChunk = (base64Data: string, rate: number = 24000) => {
  try {
    ensureCtx();
    const lane = rate === 16000 ? (lane16 ||= makeLane(16000, false))
                                : (lane24 ||= makeLane(24000, true));
    lane.push(base64Data);
  } catch (e) { console.error('[Bridget] PCM playback error', e); }
};

export const interruptPlayback = (): void => {
  lane24?.flush();
  lane16?.flush();
  if (isConnected) notifyStateChange('listening');
};

// ------------------------------------------------ avatar video (MSE pipe) ---
// Ava's live face+voice: contiguous fMP4 chunks broadcast as {type:'video',
// who:'companion', mp4:b64}. Same codec/path as the proven console pipe.
type Pipe = { ms: MediaSource; sb: SourceBuffer | null; q: Uint8Array[]; el: HTMLVideoElement;
              who: string; started: boolean; strikes: number; lastT: number; watch: any;
              awaitMoof: boolean; firstAt: number };
// avatar-hybrid-vad reference constants (taken as given):
const PREBUFFER_MAX_WAIT_MS = 120;   // stranding guard: start with 1 chunk after this
const MAX_BUFFER_BEHIND_S = 12;      // SourceBuffer eviction window behind playhead
const EVICT_SAFETY_S = 2;            // keep this much extra when trimming
const EVICT_MIN_BATCH_S = 10;        // only trim in >=10s batches (cheap, rare)
const HANDOFF_FALLBACK_MS = 400;     // ping-pong promote even without a decoded frame
const pipes: Record<string, Pipe> = {};
const CODEC = 'video/mp4; codecs="avc1.42C020, mp4a.40.2"';
// 2026-08-19 optimization pass: ADAPTIVE live-edge chase — 1.08x between
// 0.35s and 1.2s of lag (browser preserves pitch), hard snap past 1.2s to
// 0.30s behind the edge — plus a 1000ms liveness watchdog with 3-strike
// self-heal (cached-init local rebuild + server replay).
const CHASE_MIN_LAG_S = 0.35;
const CHASE_MAX_LAG_S = 1.2;
const CHASE_RATE = 1.08;
const LIVE_EDGE_TARGET_S = 0.30;
const MIN_PREBUFFER_CHUNKS = 2;

export const bindAvatarVideo = (who: string, el: HTMLVideoElement) => {
  if (pipes[who]?.watch) clearInterval(pipes[who].watch);
  const ms = new MediaSource();
  const pipe: Pipe = { ms, sb: null, q: [], el, who, started: false, strikes: 0, lastT: -1, watch: null,
                       awaitMoof: false, firstAt: 0 };
  pipes[who] = pipe;
  el.src = URL.createObjectURL(ms);
  // 'companion-preview' = the Model View tab: raw model stream, VIDEO ONLY —
  // element-muted and never routed into the audio graph (no double voice).
  el.muted = who === 'companion-preview';
  el.playbackRate = 1.0;
  ms.addEventListener('sourceopen', () => {
    if (pipe.sb) return;
    try {
      pipe.sb = ms.addSourceBuffer(CODEC);
      pipe.sb.mode = 'sequence';
      pipe.sb.addEventListener('updateend', () => pump(pipe));
      pump(pipe);
    } catch (e) { console.error('[Bridget] MSE open failed', e); }
  });
  // live-edge control + 2-strike stall watchdog (soft seek, then rebuild)
  pipe.watch = setInterval(() => {
    if (pipes[who] !== pipe) { clearInterval(pipe.watch); return; }
    if (!el.buffered.length) return;
    if (Date.now() - lastInterruptAt < 3000) return;   // post-interrupt grace (reference guard)
    const end = el.buffered.end(el.buffered.length - 1);
    const lag = end - el.currentTime;
    if (lag > CHASE_MAX_LAG_S) {
      el.playbackRate = 1.0;
      el.currentTime = end - LIVE_EDGE_TARGET_S;
      return;
    }
    el.playbackRate = lag > CHASE_MIN_LAG_S ? CHASE_RATE : 1.0;
    // SourceBuffer eviction (reference): keep 12s behind the playhead, trim
    // only in >=10s batches so removals stay rare and cheap
    try {
      const b0 = el.buffered.start(0);
      if (el.currentTime - b0 > MAX_BUFFER_BEHIND_S + EVICT_MIN_BATCH_S
          && pipe.sb && !pipe.sb.updating) {
        pipe.sb.remove(b0, el.currentTime - MAX_BUFFER_BEHIND_S - EVICT_SAFETY_S);
      }
    } catch {}
    if (!el.paused && el.currentTime === pipe.lastT && lag > 0.25) {
      if (++pipe.strikes >= 3) { pipe.strikes = 0; rebuildMediaSourceLocally(who); }
      else { el.currentTime = end - 0.05; el.play().catch(() => {}); }
    } else pipe.strikes = 0;
    pipe.lastT = el.currentTime;
  }, 1000);
  el.onerror = () => rebuildMediaSourceLocally(who);   // decode fault: self-heal
  // analyser tap: the element carries her REAL voice — feed the waveform.
  // (never tap the preview element: it must stay silent)
  if (who !== 'companion-preview') try {
    const ctx = ensureCtx();
    if (!(el as any).__kairosTapped) {
      const src = ctx.createMediaElementSource(el);
      src.connect(audioAnalyserNode!);
      (el as any).__kairosTapped = true;
    }
  } catch { /* element may already be attached to a context */ }
};

// A LOCAL rebuild can never recover mid-stream (ftyp+moov exist only at
// stream start) — on decode trouble ask the server to re-seat us from its
// warm ring (webv2). In zoomv1 rooms the request is a harmless no-op and the
// server still drives recovery through its own video_reset broadcasts.
let lastReplayReq = 0;
const requestVideoReplay = () => {
  const now = Date.now();
  if (now - lastReplayReq < 4000) return;
  lastReplayReq = now;
  try { if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'video_replay' })); } catch {}
};

const pump = (p: Pipe) => {
  if (pipes[p.who] !== p) return;   // stale updateend after a rebind
  if (!p.sb || p.sb.updating || p.q.length === 0) return;
  if (!p.started) {
    // jitter cushion first — but never strand a lone chunk (reference guard)
    const stranded = p.firstAt && Date.now() - p.firstAt >= PREBUFFER_MAX_WAIT_MS;
    if (p.q.length < MIN_PREBUFFER_CHUNKS && !stranded) return;
    p.started = true;
  }
  try { p.sb.appendBuffer(p.q[0]); p.q.shift(); }
  catch (e: any) {
    if (e?.name === 'QuotaExceededError' && p.el.currentTime > 3) {
      try { p.sb.remove(0, p.el.currentTime - 2); } catch { requestVideoReplay(); }
    } else { p.q.shift(); requestVideoReplay(); }
  }
  if (p.el.paused) p.el.play().catch(() => {});
};

// 2026-08-19 optimization pass: 400ms barge-in drop window (in-flight tail of
// an interrupted turn), client-side init-segment cache for local decoder
// rebuilds, and moof re-alignment so a mid-stream splice never poisons MSE.
const BARGE_IN_DROP_MS = 400;
let bargeDropUntil = 0;
let lastInterruptAt = 0;
let mediaEpoch = 0;
// stale test (reference pattern): the server's epoch stamp is authoritative;
// the 400ms wall-clock window is only the fallback for unstamped frames
const staleMedia = (d: any): boolean =>
  (typeof d._epoch === 'number') ? d._epoch < mediaEpoch : Date.now() < bargeDropUntil;
export const noteMediaEpoch = (d: any) => {
  if (typeof d._epoch === 'number' && d._epoch > mediaEpoch) mediaEpoch = d._epoch;
};
export const armBargeInDrop = () => {
  bargeDropUntil = Date.now() + BARGE_IN_DROP_MS;
  lastInterruptAt = Date.now();
  setTimeout(requestVideoReplay, BARGE_IN_DROP_MS + 50);
};
const cachedInits: Record<string, Uint8Array> = {};
const initScans: Record<string, { parts: Uint8Array[]; got: number }> = {};
const feedInitScan = (who: string, u8: Uint8Array) => {
  if (u8.length >= 8 && u8[4] === 102 && u8[5] === 116 && u8[6] === 121 && u8[7] === 112) {
    initScans[who] = { parts: [], got: 0 };      // 'ftyp' — a new stream begins
    delete cachedInits[who];
  }
  const scan = initScans[who];
  if (!scan) return;
  scan.parts.push(u8); scan.got += u8.length;
  if (scan.got > 4e6) { delete initScans[who]; return; }
  const all = new Uint8Array(scan.got);
  let o = 0; for (const part of scan.parts) { all.set(part, o); o += part.length; }
  let off = 0;
  while (off + 8 <= all.length) {
    const size = ((all[off] << 24) | (all[off+1] << 16) | (all[off+2] << 8) | all[off+3]) >>> 0;
    if (size < 8) { delete initScans[who]; return; }
    if (off + size > all.length) return;         // incomplete box — wait
    const typ = String.fromCharCode(all[off+4], all[off+5], all[off+6], all[off+7]);
    off += size;
    if (typ === 'moov') { cachedInits[who] = all.slice(0, off); delete initScans[who]; return; }
    if (typ !== 'ftyp' && typ !== 'free') { delete initScans[who]; return; }
  }
};
const alignToMoof = (u8: Uint8Array): Uint8Array | null => {
  for (let i = 4; i + 4 <= u8.length; i++) {
    if (u8[i] === 109 && u8[i+1] === 111 && u8[i+2] === 111 && u8[i+3] === 102) return u8.slice(i - 4);
  }
  return null;
};

const feedVideo = (who: string, b64: string) => {
  const p = pipes[who];
  if (!p) return;
  if (Date.now() < bargeDropUntil) { p.awaitMoof = true; return; }
  const bin = atob(b64);
  let bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  feedInitScan(who, bytes);
  if (p.awaitMoof) {
    if (bytes.length >= 8 && bytes[4] === 102 && bytes[5] === 116 && bytes[6] === 121 && bytes[7] === 112) p.awaitMoof = false;
    else {
      const a = alignToMoof(bytes); if (!a) return; bytes = a; p.awaitMoof = false;
      // parser was left mid-fragment by the last pre-drop append — abort()
      // resets it to segment-start so the spliced moof parses cleanly
      try { if (p.sb && !p.sb.updating) p.sb.abort(); } catch {}
    }
  }
  if (!p.firstAt) {
    p.firstAt = Date.now();
    setTimeout(() => pump(p), PREBUFFER_MAX_WAIT_MS + 10);   // stranding-guard re-pump
  }
  p.q.push(bytes);
  pump(p);
};

// Ping-pong dual-<video> handoff (reference pattern): rebuilds attach to the
// HIDDEN sibling element; the visible one keeps showing the old stream's last
// frames until the new pipe decodes its first frame (or 400ms passes), then
// they swap — no black flash on session reconnects or self-heals.
const pairs: Record<string, { a: HTMLVideoElement; b: HTMLVideoElement }> = {};
// one pending handoff per stream; gen guards stale promotes (review finding:
// overlapping promotes inverted visibility — frozen face, live element hidden)
const handoffs: Record<string, { timer: any; gen: number }> = {};
const cancelHandoff = (who: string) => {
  const h = handoffs[who];
  if (h) { h.gen++; if (h.timer) { clearTimeout(h.timer); h.timer = null; } }
};
export const bindAvatarVideoPair = (who: string, a: HTMLVideoElement, b: HTMLVideoElement) => {
  cancelHandoff(who);
  pairs[who] = { a, b };
  b.style.opacity = '0';
  a.style.opacity = '1';
  bindAvatarVideo(who, a);
};
export const unbindAvatarPair = (who: string) => {
  cancelHandoff(who);
  const p = pipes[who];
  if (p?.watch) clearInterval(p.watch);
  delete pipes[who];
  delete pairs[who];
};
const standbyOf = (who: string): HTMLVideoElement | null => {
  const pr = pairs[who];
  if (!pr) return null;
  return pipes[who]?.el === pr.a ? pr.b : pr.a;
};
const promoteWhenLive = (who: string, fresh: HTMLVideoElement, old: HTMLVideoElement) => {
  const h = handoffs[who] || (handoffs[who] = { timer: null, gen: 0 });
  const gen = ++h.gen;
  if (h.timer) { clearTimeout(h.timer); h.timer = null; }
  let done = false;
  const promote = () => {
    if (done || h.gen !== gen) return;              // superseded handoff
    if (pipes[who]?.el !== fresh) return;           // fresh pipe already replaced
    done = true;
    if (h.timer) { clearTimeout(h.timer); h.timer = null; }
    fresh.style.opacity = '1';
    old.style.opacity = '0';
    try { old.pause(); } catch {}
  };
  const rvfc = (fresh as any).requestVideoFrameCallback;
  if (typeof rvfc === 'function') rvfc.call(fresh, promote);
  // NEVER promote a frame-less element (review finding: a blind 400ms swap
  // blanked the face on every real session reconnect). Keep the OLD face
  // visible; poll until the fresh element can actually render — with a hard
  // 10s ceiling so the pair can't wedge if the stream never returns.
  const t0 = Date.now();
  const poll = () => {
    if (done || h.gen !== gen) return;
    if (fresh.readyState >= 2 /* HAVE_CURRENT_DATA */ || Date.now() - t0 > 10_000) { promote(); return; }
    h.timer = setTimeout(poll, HANDOFF_FALLBACK_MS);
  };
  h.timer = setTimeout(poll, HANDOFF_FALLBACK_MS);
};

const resetVideo = (who: string) => {
  const p = pipes[who];
  if (!p) return;
  const standby = standbyOf(who);
  if (standby) {
    const old = p.el;
    bindAvatarVideo(who, standby);   // fresh ftyp+moov -> fresh pipe, hidden
    promoteWhenLive(who, standby, old);
  } else {
    bindAvatarVideo(who, p.el);      // single-element surfaces: in-place rebind
  }
};

/** Model View tab: bind a muted, graph-isolated second pipe carrying the raw
 *  model stream in parallel with the Zoom seat. Joins mid-stream by seeding
 *  the cached init segment and moof-aligning (same splice machinery). */
export const bindModelPreview = (el: HTMLVideoElement) => {
  bindAvatarVideo('companion-preview', el);
  const p = pipes['companion-preview'];
  const init = cachedInits['companion'];
  if (p && init) { p.q.push(init.slice()); p.awaitMoof = true; }
};
export const unbindModelPreview = () => {
  const p = pipes['companion-preview'];
  if (p?.watch) clearInterval(p.watch);
  delete pipes['companion-preview'];
};

// self-heal: rebuild the local MediaSource seeded with the cached init segment
// (report technique) and moof-align the continuing stream; the server warm-ring
// replay supersedes the splice wherever it's available.
const rebuildMediaSourceLocally = (who: string) => {
  const p = pipes[who];
  if (!p) return;
  const standby = standbyOf(who);
  const old = p.el;
  bindAvatarVideo(who, standby || p.el);   // ping-pong when a pair exists
  const np = pipes[who];
  const init = cachedInits[who];
  if (np && init) { np.q.push(init.slice()); np.awaitMoof = true; }
  if (standby) promoteWhenLive(who, standby, old);
  requestVideoReplay();
};

// legacy API kept for component compatibility (no image-frame avatar anymore)
export type VideoFrameCallback = (base64Data: string, mimeType: string) => void;
export const registerOnVideoFrame = (_cb: VideoFrameCallback) => {};
export const unregisterOnVideoFrame = () => {};
export type PeerVideoFrameCallback = (image: string, role: string) => void;
export const onPeerVideoFrame = (_cb: PeerVideoFrameCallback) => () => {};
export const sendVideoFrame = (_image: string, _role: string = 'banker') => {};

// -------------------------------------------------------- mic semantics -----
// The banker speaks IN ZOOM (RTMS is the ears). The old mic button becomes the
// FLOOR control: "mute AI" = hold the floor (Ava pauses), unmute = release.
let micMuted = false;
export const setMicMuted = (muted: boolean) => {
  micMuted = muted;
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: 'hold_floor', on: muted }));
  }
  notifyStateChange(muted ? 'paused' : (isConnected ? 'listening' : 'idle'));
};

export const bankerTakeover = () => {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'takeover' }));
};
export const resumeAI = () => {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'resume_ai' }));
  notifyStateChange('listening');
};

// ------------------------------------------------------------ transcripts ---
const BRIDGET_LABEL = 'Bridget';
const speakerLabel = (who: string, name?: string): string => {
  if (who === 'companion') return BRIDGET_LABEL;
  if (who === 'prospect') return name || 'Prospect';
  if (who === 'banker') return name || 'Banker';
  return name || who || 'Speaker';
};

// caption fragments stream during speech — coalesce per speaker turn
let capWho = ''; let capText = '';
const onCaption = (who: string, text: string) => {
  if (who !== capWho) { flushCaption(); capWho = who; }
  capText = text;
  if (who === 'companion') notifyStateChange('speaking');
};
const flushCaption = () => {
  if (capWho && capText) notifyTranscript(speakerLabel(capWho), capText,
    { role: capWho === 'companion' ? 'ai' : 'participant' });
  capWho = ''; capText = '';
};

// ---------------------------------------------------------------- connect ---
export const connectGeminiLive = async (sessionId: string, _modality: 'audio' | 'avatar' = 'avatar', displayName = 'Banker'): Promise<boolean> => {
  return new Promise((resolve) => {
    try {
      disconnectGeminiLive();
      roomId = sessionId;
      myName = displayName;
    const url = `${wsOrigin()}/ws/${sessionId}?who=banker&name=${encodeURIComponent(displayName)}`;
      socket = new WebSocket(url);

      socket.onopen = () => {
        isConnected = true;
        reconnectAttempts = 0;
        emitFeed('connection', { connected: true });
        notifyStateChange('listening');
        resolve(true);
      };

      socket.onmessage = (event) => {
        let d: any;
        try { d = JSON.parse(event.data); } catch { return; }
        noteMediaEpoch(d);
        switch (d.type) {
          case 'hello':
            emitFeed('consent', { ...d.consents, replay: true });
            emitFeed('hello', d);
            return;
          case 'caption':
            if (d.who === 'companion') noteBotSpeech();
            onCaption(d.who, d.text);
            return;
          case 'audio':
            if (d.who === 'external') {
              // webv2 ONLY: other humans' voices (16k mic rate) — never my own
              // echo. In zoomv1, Zoom itself carries all meeting audio; playing
              // the RTMS relay here would echo the room (review finding).
              if (webMeetingActive && !zoomActive && d.speaker && d.speaker !== myName) playIncomingPCMChunk(d.pcm, 16000);
              return;
            }
            if (d.who === 'companion' && staleMedia(d)) return;   // tail of an interrupted turn
            if (zoomActive && d.who === 'companion') return;  // no double Bridget voice
            if (d.who === 'companion') noteBotSpeech();
            playIncomingPCMChunk(d.pcm);
            notifyAudioChunk(d.pcm);
            return;
          case 'flush_audio':
            interruptPlayback();
            return;
          case 'interrupted':
            // human barge-in: drop the in-flight tail of her interrupted turn
            armBargeInDrop();
            vadResetLatch();
            interruptPlayback();
            return;
          case 'video':
            if (d.who === 'companion' && staleMedia(d)) {
              const sp = pipes[d.who]; if (sp) sp.awaitMoof = true;
              const pp = pipes['companion-preview']; if (pp) pp.awaitMoof = true;
              return;
            }
            feedVideo(d.who, d.mp4);
            // tee the raw stream into the Model View tab when it's open
            if (d.who === 'companion' && pipes['companion-preview']) feedVideo('companion-preview', d.mp4);
            return;
          case 'video_reset':
            resetVideo(d.who);
            if (d.who === 'companion' && pipes['companion-preview']) resetVideo('companion-preview');
            return;
          case 'hop_frame':
            // Model View pipeline sequence: decoded-frame samples from the bot
            window.dispatchEvent(new CustomEvent('bridget-hop-frame', { detail: d }));
            return;
          case 'perf':
            emitFeed('perf', d);
            if (d.comp === 'ZoomEars (RTMS)' && currentState === 'listening') {
              notifyStateChange('thinking');
              setTimeout(() => { if (currentState === 'thinking') notifyStateChange('listening'); }, 6000);
            }
            return;
          case 'peer_cam':
            if (d.who === 'banker' || (d.name && d.name === myName)) return;
            peerCamCbs.forEach(cb => { try { cb(d.name || 'Client', d.jpg); } catch {} });
            return;
          case 'feed': {
            const { kind, payload } = d;
            emitFeed(kind, payload);
            if (kind === 'turn') {
              capWho = ''; capText = '';   // turn IS the full line — drop fragments
              const role = payload.who === 'companion' ? 'ai'
                : payload.who === 'banker' ? 'banker' : 'participant';
              notifyTranscript(speakerLabel(payload.who, payload.name), payload.text, { role });
              if (payload.who === 'companion') notifyStateChange('listening');
            }
            if (kind === 'risk') {
              notifyStateChange('handoff');
              onTakeoverCallback?.(payload.risk_type || 'compliance', payload.phrase || payload.safe_line || '');
            }
            if (kind === 'system' && payload.event === 'banker_takeover') {
              notifyStateChange('handoff');
              onTakeoverCallback?.('banker_takeover', 'Banker took the floor.');
            }
            if (kind === 'companion' && payload.event === 'turn_complete') {
              capWho = ''; capText = '';
              if (currentState === 'speaking') notifyStateChange('listening');
            }
            return;
          }
        }
      };

      socket.onclose = (event) => {
        const wasConnected = isConnected;
        isConnected = false;
        emitFeed('connection', { connected: false });
        if (!wasConnected) resolve(false);
        if (event.code !== 1000 && reconnectAttempts < MAX_RECONNECT_ATTEMPTS && roomId) {
          reconnectAttempts++;
          const delay = Math.min(Math.pow(2, reconnectAttempts) * 500, 8000);
          setTimeout(() => { if (!isConnected && roomId) connectGeminiLive(roomId, 'avatar', displayName); }, delay);
        } else {
          notifyStateChange('idle');
        }
      };
      socket.onerror = () => notifyStateChange('idle');
    } catch (err) {
      console.error('[Bridget] connect failed', err);
      resolve(false);
    }
  });
};

export const disconnectGeminiLive = () => {
  interruptPlayback();
  const s = socket;
  socket = null;
  roomId = '';
  if (s) { try { s.close(1000, 'client disconnect'); } catch {} }
  isConnected = false;
  notifyStateChange('idle');
};

// typed text from the cockpit goes through the real banker_say route (the
// fleet + Ava both see it; her reply comes back over the socket)
export const sendTextTurn = async (text: string) => {
  if (!roomId) return;
  notifyTranscript('Banker', text, { role: 'banker' });
  notifyStateChange('thinking');
  try {
    await fetch(`${origin()}/rooms/${roomId}/banker_say?text=${encodeURIComponent(text)}&addressee=companion`, { method: 'POST' });
  } catch { notifyTranscript('System', 'Message did not reach the room — try again.', { role: 'participant' }); }
};

// --------------------------------------------------------- meeting control --
export interface ZoomMeeting { ok: boolean; room: string; join_url: string; meeting_number: string; passcode: string; seats: string; error?: string }

/** Create a REAL Zoom meeting with the chosen AI seats and return the join
 *  link + bridge room id. who: 'ava' (default — the Bridget advisor alone),
 *  'client' or 'both'. */
let currentCompany = 'Meridian Components Inc.';
export const getCompany = () => currentCompany;

export const startZoomMeeting = async (company = 'Meridian Components Inc.', who: 'ava' | 'client' | 'both' = 'ava'): Promise<ZoomMeeting> => {
  currentCompany = company;
  const r = await fetch(`${origin()}/zoom/start_demo?company=${encodeURIComponent(company)}&who=${who}`, { method: 'POST' });
  return await r.json();
};

/** Live presenting: CCB visual library + present/stop/upload controls. */
export const fetchPresentLibrary = async (): Promise<any[]> => {
  try { const d = await (await fetch(`${origin()}/presents/library`)).json(); return d.visuals || []; } catch { return []; }
};
export const presentVisual = async (room: string, visualId: string): Promise<any> => {
  try { return await (await fetch(`${origin()}/rooms/${room}/present?visual_id=${encodeURIComponent(visualId)}`, { method: 'POST' })).json(); } catch { return null; }
};
export const stopPresenting = async (room: string): Promise<any> => {
  try { return await (await fetch(`${origin()}/rooms/${room}/present?stop=true`, { method: 'POST' })).json(); } catch { return null; }
};
export const uploadAndPresent = async (room: string, file: File): Promise<any> => {
  try {
    return await (await fetch(`${origin()}/rooms/${room}/present/upload`, {
      method: 'POST', body: file,
      headers: { 'Content-Type': file.type, 'X-Title': file.name.replace(/\.[a-z]+$/i, '') },
    })).json();
  } catch { return null; }
};

/** Client Profile tab payload: dossier + live CRM/news/market/KYC + sources. */
export const fetchClientProfile = async (company: string): Promise<any> => {
  try { return await (await fetch(`${origin()}/profile?company=${encodeURIComponent(company)}`)).json(); } catch { return null; }
};

/** The banker clicked the Zoom join link — releases Ava's greeting hold. */
export const meetingStarted = async (room: string) => {
  try { await fetch(`${origin()}/rooms/${room}/meeting_started`, { method: 'POST' }); } catch {}
};

export const registerBankerName = async (name: string) => {
  if (!roomId || !name.trim()) return;
  try { await fetch(`${origin()}/rooms/${roomId}/banker_name?name=${encodeURIComponent(name.trim())}`, { method: 'POST' }); } catch {}
};

export const grantBankerConsent = async () => {
  if (!roomId) return;
  try { await fetch(`${origin()}/rooms/${roomId}/consent?party=banker`, { method: 'POST' }); } catch {}
};

// ---------------------------------------------------------- webv2 / meetv3 ----
const peerCamCbs: Array<(name: string, jpg: string) => void> = [];
export const onPeerCam = (cb: (name: string, jpg: string) => void) => {
  peerCamCbs.push(cb);
  return () => { const i = peerCamCbs.indexOf(cb); if (i >= 0) peerCamCbs.splice(i, 1); };
};

export const fetchVariant = async (): Promise<string> => {
  try { return (await (await fetch(`${origin()}/variant`)).json()).variant || 'webv2'; }
  catch { return 'webv2'; }
};

export const setVariantOnServer = async (v: string): Promise<string> => {
  try { return (await (await fetch(`${origin()}/variant?v=${encodeURIComponent(v)}`, { method: 'POST' })).json()).variant || v; }
  catch { return v; }
};

export const startWebMeeting = async (company: string, banker: string, kind: 'web' | 'meet'): Promise<any> => {
  const r = await fetch(`${origin()}/web/start?company=${encodeURIComponent(company)}&banker=${encodeURIComponent(banker)}&kind=${kind}`, { method: 'POST' });
  const out = await r.json();
  if (out?.ok && kind === 'web') webMeetingActive = true;
  return out;
};

let webStream: MediaStream | null = null;
let webCamTimer: any = null;
let webAc: AudioContext | null = null;
let webMicOn = true;
let webMuting = false;

// AdaptiveClientVAD echo gate — avatar-hybrid-vad reference mechanics, taken
// as given (2026-08-19): int16-RMS chunks smoothed over a rolling 8-chunk
// mean; noise floor (init 100, clamp [0,1500]) adapts ONLY on non-speech
// frames, 0.08 up / 0.05 down; 150ms of speech arms an isUserSpeaking latch
// (cleared on interrupt/mute) holding the gate open for the whole barge-in;
// while Bridget is audibly speaking, sub-threshold packets are zero-filled
// AND SENT. refLevel is the reference's 0-255 scale (float RMS x500, capped),
// read at gate time from the shared analyser (buffer sized to fftSize).
const VAD_MIN_THRESHOLD = 210;
const BOT_SPEECH_HOLD_MS = 1200;
const vad = { noiseFloor: 110, hist: [] as number[], speechMs: 0, silenceMs: 1000, userSpeaking: false };
const vadResetLatch = () => { vad.userSpeaking = false; vad.speechMs = 0; vad.silenceMs = 1000; };
let lastBotSpeechAt = 0;
const noteBotSpeech = () => { lastBotSpeechAt = Date.now(); };
const isBotSpeakingNow = () =>
  currentState === 'speaking' || Date.now() - lastBotSpeechAt < BOT_SPEECH_HOLD_MS;
const getRefLevel = (): number => {
  if (!audioAnalyserNode) return 0;
  const buf = new Uint8Array(audioAnalyserNode.fftSize);
  audioAnalyserNode.getByteTimeDomainData(buf);
  let acc = 0;
  for (let i = 0; i < buf.length; i++) { const d = (buf[i] - 128) / 128; acc += d * d; }
  return Math.min(255, Math.sqrt(acc / buf.length) * 500);
};

const processAndSendPcm = (rawPcm: ArrayBuffer) => {
  if (socket?.readyState !== WebSocket.OPEN) return;
  if (!webMicOn && !webMuting) return;
  let pcm: ArrayBuffer = rawPcm;
  if (webMuting) { pcm = new ArrayBuffer(pcm.byteLength); }   // digital silence flush
  else {
    const i16 = new Int16Array(pcm);
    let acc = 0;
    for (let i = 0; i < i16.length; i++) acc += i16[i] * i16[i];
    const rms = Math.sqrt(acc / (i16.length || 1));
    vad.hist.push(rms); if (vad.hist.length > 8) vad.hist.shift();
    const smoothed = vad.hist.reduce((a, b) => a + b, 0) / vad.hist.length;
    const bot = isBotSpeakingNow();
    const refNow = bot ? getRefLevel() : 0;
    const dynamicThreshold = Math.max(
      VAD_MIN_THRESHOLD,
      vad.noiseFloor * 1.8 + 80 + (bot ? (38 + (refNow > 0 ? refNow * 0.75 + 14 : 0)) : 0));
    const hasSpeech = rms > dynamicThreshold;
    const chunkMs = (i16.length / 16000) * 1000;
    if (hasSpeech) {
      vad.speechMs += chunkMs;
      vad.silenceMs = 0;
      if (vad.speechMs >= 150) vad.userSpeaking = true;
    } else {
      vad.speechMs = 0;
      vad.silenceMs += chunkMs;
      if (vad.silenceMs > 550) vad.userSpeaking = false;
      vad.noiseFloor += (smoothed - vad.noiseFloor) * (smoothed > vad.noiseFloor ? 0.08 : 0.05);
      vad.noiseFloor = Math.min(1500, Math.max(0, vad.noiseFloor));
    }
    if (!hasSpeech && !vad.userSpeaking && vad.silenceMs > 600) return;
    if (!hasSpeech && vad.silenceMs <= 600) pcm = new ArrayBuffer(pcm.byteLength);
  }
  socket.send(pcm);
};

const wireWebMic = async () => {
  if (!webStream || !webStream.getAudioTracks || webStream.getAudioTracks().length === 0) return;
  try {
    webAc = new (window.AudioContext || (window as any).webkitAudioContext)();
    if (webAc.state === 'suspended') webAc.resume().catch(() => {});
    const src = webAc.createMediaStreamSource(webStream);
    let workletOk = false;
    if (webAc.audioWorklet) {
      try {
        await webAc.audioWorklet.addModule('/static/mic-worklet.js');
        const worklet = new AudioWorkletNode(webAc, 'mic-capture');
        src.connect(worklet);
        worklet.port.onmessage = (e: MessageEvent) => processAndSendPcm((e.data as any).pcm);
        workletOk = true;
      } catch (wErr) {
        console.warn('[Bridget] AudioWorklet fallback to ScriptProcessor:', wErr);
      }
    }
    if (!workletOk) {
      // HTTP non-localhost fallback: ScriptProcessorNode resampled to 16kHz Int16
      const sp = webAc.createScriptProcessor(2048, 1, 1);
      const ratio = webAc.sampleRate / 16000;
      sp.onaudioprocess = (ev) => {
        const input = ev.inputBuffer.getChannelData(0);
        const outLen = Math.floor(input.length / ratio);
        const out = new Int16Array(outLen);
        for (let i = 0; i < outLen; i++) {
          const s = Math.max(-1, Math.min(1, input[Math.floor(i * ratio)]));
          out[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        processAndSendPcm(out.buffer);
      };
      src.connect(sp);
      const muteGain = webAc.createGain();
      muteGain.gain.value = 0;
      sp.connect(muteGain);
      muteGain.connect(webAc.destination);
    }
  } catch (err) {
    console.warn('[Bridget] Mic worklet setup note:', err);
  }
};

// Graceful mute: immediately disable hardware audio track + send digital silence
const FLUSH_SILENCE_MS = 250;
let muteTimer: any = null;
export const muteWebMic = (): void => {
  webStream?.getAudioTracks().forEach(t => { t.enabled = false; });
  if (!webMicOn || webMuting) return;
  webMuting = true;
  vadResetLatch();
  if (socket?.readyState === WebSocket.OPEN) {
    try { socket.send(new ArrayBuffer(6400)); } catch {}
  }
  if (muteTimer) clearTimeout(muteTimer);
  muteTimer = setTimeout(() => {
    muteTimer = null;
    if (!webMuting) return;
    webMicOn = false; webMuting = false;
    webAc?.close().catch(() => {}); webAc = null;
  }, FLUSH_SILENCE_MS);
};
export const unmuteWebMic = async (): Promise<void> => {
  webStream?.getAudioTracks().forEach(t => { t.enabled = true; });
  if (webMicOn && !webMuting) return;
  if (muteTimer) { clearTimeout(muteTimer); muteTimer = null; }
  webMuting = false; webMicOn = true;
  if (!webAc) await wireWebMic();
};
export const isWebMicOn = (): boolean => webMicOn;

export const muteAllPartyMics = (muted: boolean): void => {
  if (muted) muteWebMic(); else unmuteWebMic();
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: 'mute_all', muted }));
  }
  notifyStateChange(muted ? 'paused' : (isConnected ? 'listening' : 'idle'));
};

export const getWebStream = (): MediaStream | null => webStream;

let fallbackTimer: any = null;
const makeBankerFallbackCanvas = (label: string) => {
  const fc = document.createElement('canvas');
  fc.width = 320; fc.height = 240;
  const fctx = fc.getContext('2d')!;
  const initials = (label || 'Banker').split(/\s+/).map(w => w[0] || '').join('').slice(0, 2).toUpperCase() || 'PB';
  let tick = 0;
  if (fallbackTimer) clearInterval(fallbackTimer);
  fallbackTimer = setInterval(() => {
    tick++;
    const grad = fctx.createLinearGradient(0, 0, 320, 240);
    grad.addColorStop(0, '#001B38');
    grad.addColorStop(1, '#00254A');
    fctx.fillStyle = grad;
    fctx.fillRect(0, 0, 320, 240);
    const r = 44 + Math.sin(tick * 0.25) * 3;
    fctx.beginPath();
    fctx.arc(160, 100, r, 0, Math.PI * 2);
    fctx.fillStyle = 'rgba(56, 189, 248, 0.22)';
    fctx.fill();
    fctx.beginPath();
    fctx.arc(160, 100, 38, 0, Math.PI * 2);
    fctx.fillStyle = '#0284c7';
    fctx.fill();
    fctx.fillStyle = '#ffffff';
    fctx.font = 'bold 26px sans-serif';
    fctx.textAlign = 'center';
    fctx.textBaseline = 'middle';
    fctx.fillText(initials, 160, 101);
    fctx.fillStyle = '#f8fafc';
    fctx.font = 'bold 14px sans-serif';
    fctx.fillText(label || 'Paxton Bank Banker', 160, 170);
    fctx.fillStyle = '#38bdf8';
    fctx.font = '11px monospace';
    fctx.fillText('PAXTON BANK · HOST', 160, 192);
  }, 150);
  return { canvas: fc, stream: (fc as any).captureStream ? (fc as any).captureStream(10) as MediaStream : null };
};

/** webv2: banker's mic (16k, straight into her native ears) + ~5fps camera tile. */
export const startWebMedia = async (): Promise<MediaStream | null> => {
  if (webStream) return webStream;
  if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
    try {
      webStream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
        video: { width: 640, height: 480 },
      });
    } catch {
      try {
        webStream = await navigator.mediaDevices.getUserMedia({
          audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
        });
      } catch (e2) {
        console.warn('[Bridget] Camera/mic hardware unavailable or busy, using live host tile:', e2);
      }
    }
  }
  let fbCanvas: HTMLCanvasElement | null = null;
  const hasVideo = webStream && webStream.getVideoTracks && webStream.getVideoTracks().length > 0;
  if (!hasVideo) {
    const fb = makeBankerFallbackCanvas(myName || 'Alex Drummond');
    fbCanvas = fb.canvas;
    if (fb.stream) {
      if (webStream && webStream.getAudioTracks().length > 0) {
        fb.stream.addTrack(webStream.getAudioTracks()[0]);
      }
      webStream = fb.stream;
    }
  }
  await wireWebMic();
  const v = document.createElement('video');
  if (webStream) { v.srcObject = webStream; v.muted = true; v.play().catch(() => {}); }
  const cvs = document.createElement('canvas'); cvs.width = 320; cvs.height = 240;
  const cctx = cvs.getContext('2d')!;
  webCamTimer = setInterval(() => {
    if (socket?.readyState !== WebSocket.OPEN) return;
    if (v.videoWidth > 0) {
      cctx.drawImage(v, 0, 0, 320, 240);
    } else if (fbCanvas) {
      cctx.drawImage(fbCanvas, 0, 0, 320, 240);
    } else {
      return;
    }
    socket.send(JSON.stringify({ type: 'cam', name: myName || 'Banker', jpg: cvs.toDataURL('image/jpeg', 0.55).split(',')[1] }));
  }, 200);
  return webStream;
};

export const stopWebMedia = (): void => {
  if (webCamTimer) { clearInterval(webCamTimer); webCamTimer = null; }
  if (fallbackTimer) { clearInterval(fallbackTimer); fallbackTimer = null; }
  webStream?.getTracks().forEach(t => t.stop()); webStream = null;
  webAc?.close().catch(() => {}); webAc = null;
  webMicOn = true; webMuting = false;   // next meeting starts unmuted
};

/** End a webv2/meetv3 meeting room (no Zoom involved). */
export const endWebMeeting = async (roomId: string): Promise<any> => {
  webMeetingActive = false;
  stopWebMedia();
  const r = await fetch(`${origin()}/rooms/${encodeURIComponent(roomId)}/end`, { method: 'POST' });
  return await r.json();
};


/** MONEY RULE: nothing may keep running after a session. */
export const endAllZoom = async (_roomId?: string): Promise<any> => {
  // USER DIRECTIVE 2026-08-13: End must HALT EVERYTHING, immediately — no
  // confirmation, no scoping. force=true deliberately bypasses the
  // active-meeting guard: nothing may keep running (or billing) after this.
  const r = await fetch(`${origin()}/zoom/stop_all?force=true`, { method: 'POST' });
  return await r.json();
};

/** Fire-and-forget hard stop that survives tab close / navigation. */
export const endAllZoomBeacon = (): void => {
  try {
    navigator.sendBeacon(`${origin()}/zoom/stop_all?force=true`, new Blob([], { type: 'text/plain' }));
  } catch { /* best effort */ }
};

// ------------------------------------------------------------- telemetry ----
export const fetchPerfRooms = async (): Promise<any[]> => {
  try { return await (await fetch(`${origin()}/perf/rooms`)).json(); } catch { return []; }
};
export const fetchRoomPerf = async (room: string): Promise<any> => {
  try { return await (await fetch(`${origin()}/rooms/${room}/perf`)).json(); } catch { return { spans: [] }; }
};
export const fetchGuardrails = async (): Promise<any> => {
  if (!roomId) return null;
  try { return await (await fetch(`${origin()}/rooms/${roomId}/guardrails`)).json(); } catch { return null; }
};
/** Policy Panel data (room-independent): rule text, the 8-topic prohibited
 *  checklist, the 10 grounding seed sources + corpus load state. Available
 *  before any meeting exists — the shield drawer renders from this. */
export const fetchPolicyPanel = async (): Promise<any> => {
  try { return await (await fetch(`${origin()}/guardrails`)).json(); } catch { return null; }
};
export const fetchRoomState = async (): Promise<any> => {
  if (!roomId) return null;
  try { return await (await fetch(`${origin()}/rooms/${roomId}/state`)).json(); } catch { return null; }
};

/** Agent transparency: the whole fleet's purpose/prompt/tools/model/journeys
 *  (GET /agents). Same-origin via the bridge; returns [] on any failure. */
export const fetchRoomAgentLog = async (room: string): Promise<any[]> => {
  try { const d = await (await fetch(`${origin()}/rooms/${room}/agentlog?n=500`)).json(); return d.logs || []; } catch { return []; }
};
export const fetchAgents = async (): Promise<any[]> => {
  try { const r = await (await fetch(`${origin()}/agents`)).json(); return Array.isArray(r) ? r : []; }
  catch { return []; }
};
