import React, { useEffect, useRef, useState } from 'react';
import { bindModelPreview, unbindModelPreview } from '../services/geminiLiveClient';

/** Model View — the avatar stream at EVERY hop of the zoomv1 pipeline, in
 *  true sequence (all six hops the video actually crosses):
 *    1. Model → Bridge        (raw stream as the model delivers it)     [tile]
 *    2. Fan-out ring → Feeder (the exact WS lane the bot consumes)      [tile]
 *    3. Feeder queue → ffmpeg (bytes unchanged; timing-only hop)        [card]
 *    4. ffmpeg decode/scale   (1fps samples of frames handed onward)    [tile]
 *    5. Bot pacing → Zoom SDK (newest-wins 25fps raw send; timing-only) [card]
 *    6. Zoom encode+delivery  (what participants see)                   [card]
 *  Hops that carry identical bytes get explanation cards, not fake duplicate
 *  tiles. All tiles are muted — sound stays on the Zoom side. */

const useStats = (elRef: React.RefObject<HTMLVideoElement | null>) => {
  const [s, setS] = useState({ fps: 0, lag: 0, playing: false });
  useEffect(() => {
    let frames0 = 0;
    const t = setInterval(() => {
      const el = elRef.current;
      if (!el) return;
      const q = (el as any).getVideoPlaybackQuality ? (el as any).getVideoPlaybackQuality() : null;
      const frames = q ? q.totalVideoFrames : 0;
      const lag = el.buffered.length ? el.buffered.end(el.buffered.length - 1) - el.currentTime : 0;
      setS({ fps: frames0 ? Math.max(0, frames - frames0) : 0, lag: Math.round(lag * 100) / 100, playing: !el.paused && frames > 0 });
      frames0 = frames;
    }, 1000);
    return () => clearInterval(t);
  }, []);
  return s;
};

const StepCard: React.FC<{ n: number; title: string; sub: string; badge: string; live?: boolean; children: React.ReactNode }> =
  ({ n, title, sub, badge, live, children }) => (
    <div className="bg-slate-950 border border-slate-700 rounded-2xl overflow-hidden flex flex-col">
      <div className="px-4 py-3 border-b border-slate-800 flex items-center gap-3">
        <span className="w-7 h-7 rounded-full bg-sky-900 text-sky-300 font-bold text-sm flex items-center justify-center">{n}</span>
        <div className="flex-1">
          <div className="text-sm font-bold text-white">{title}</div>
          <div className="text-xs text-slate-400">{sub}</div>
        </div>
        <span className={`text-[11px] font-mono px-2 py-1 rounded ${live ? 'bg-emerald-900/70 text-emerald-300' : 'bg-slate-800/70 text-slate-400'}`}>{badge}</span>
      </div>
      <div className="relative flex-1 flex items-center justify-center bg-black" style={{ minHeight: 260 }}>
        {children}
      </div>
    </div>
  );

const ModelView: React.FC = () => {
  const v1Ref = useRef<HTMLVideoElement | null>(null);
  const v2Ref = useRef<HTMLVideoElement | null>(null);
  const [hopJpg, setHopJpg] = useState<string>('');
  const [hopAt, setHopAt] = useState(0);
  const [tapState, setTapState] = useState('connecting to the bot lane…');
  const s1 = useStats(v1Ref);
  const s2 = useStats(v2Ref);

  // Step 1: raw model stream (teed second pipe on the cockpit socket)
  useEffect(() => {
    const el = v1Ref.current;
    if (!el) return;
    bindModelPreview(el);
    return () => unbindModelPreview();
  }, []);

  // Step 2: subscribe to the SAME relay lane the bot's feeder consumes
  useEffect(() => {
    const el = v2Ref.current;
    if (!el) return;
    let ws: WebSocket | null = null, ms: MediaSource | null = null, sb: SourceBuffer | null = null;
    let vq: ArrayBuffer[] = [], alive = true, expectBin = false;
    const CODEC = 'video/mp4; codecs="avc1.42C020, mp4a.40.2"';
    const pump = () => {
      if (!sb || sb.updating || !vq.length) return;
      try { sb.appendBuffer(vq.shift()!); } catch { newPipe(); }
      if (el.paused) el.play().catch(() => {});
      if (el.buffered.length) {
        const end = el.buffered.end(el.buffered.length - 1);
        if (end - el.currentTime > 1.2) el.currentTime = end - 0.3;
      }
    };
    const newPipe = () => {
      ms = new MediaSource(); vq = []; sb = null;
      el.src = URL.createObjectURL(ms);
      ms.addEventListener('sourceopen', () => {
        if (!ms) return;
        sb = ms.addSourceBuffer(CODEC); sb.mode = 'sequence';
        sb.addEventListener('updateend', pump); pump();
      });
    };
    (async () => {
      try {
        const r = await fetch('/zoom/hoptap');
        if (!r.ok) { setTapState('no live Zoom meeting — start one from the Cockpit'); return; }
        const d = await r.json();
        if (!alive) return;
        newPipe();
        ws = new WebSocket(`wss://${location.host}/relay/sub/${d.room}/companion?t=${encodeURIComponent(d.token)}`);
        ws.binaryType = 'arraybuffer';
        ws.onopen = () => setTapState(`LIVE · room ${d.room}`);
        ws.onclose = () => setTapState('bot lane disconnected');
        ws.onmessage = (ev) => {
          if (typeof ev.data === 'string') {
            try {
              const m = JSON.parse(ev.data);
              if (m.ev === 'init') newPipe();
              else if (m.ev === 'seg') expectBin = true;
            } catch {}
          } else if (expectBin) {
            expectBin = false;
            vq.push(ev.data); pump();
          }
        };
      } catch { setTapState('bot lane unavailable'); }
    })();
    return () => { alive = false; try { ws?.close(); } catch {} };
  }, []);

  // Step 3: decoded-frame samples POSTed by the bot's feeder (1fps)
  useEffect(() => {
    const h = (e: any) => { setHopJpg(e.detail?.jpg || ''); setHopAt(Date.now()); };
    window.addEventListener('bridget-hop-frame', h);
    return () => window.removeEventListener('bridget-hop-frame', h);
  }, []);
  const hopFresh = Date.now() - hopAt < 5000;

  return (
    <div className="max-w-7xl mx-auto p-6">
      <div className="mb-4">
        <h1 className="text-xl font-bold text-white">Model View — every hop the video crosses, in order</h1>
        <p className="text-sm text-slate-400 mt-1">
          All six hops of the Zoom pipeline in true sequence. The first stage that shows the problem
          is the hop that introduces it. Hops where the bytes pass through untouched are explained
          rather than duplicated. All tiles are muted; audio stays on the Zoom side.
        </p>
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <StepCard n={1} title="Model → Bridge" sub="Raw stream exactly as the model delivers it (us-central1 → server)"
          badge={s1.playing ? `LIVE · ${s1.fps} fps · ${s1.lag}s buffer` : 'waiting for stream…'} live={s1.playing}>
          <video ref={v1Ref} autoPlay playsInline muted className="w-full max-h-[42vh] object-contain" />
        </StepCard>
        <StepCard n={2} title="Fan-out ring → Bot feeder" sub="The exact WS lane the Zoom bot subscribes to (post server fan-out)"
          badge={s2.playing ? `${tapState} · ${s2.fps} fps` : tapState} live={s2.playing}>
          <video ref={v2Ref} autoPlay playsInline muted className="w-full max-h-[42vh] object-contain" />
        </StepCard>
        <StepCard n={3} title="Feeder queue → ffmpeg" sub="Bytes unchanged — a timing-only hop (write queue in front of the decoder)"
          badge="timing only">
          <div className="text-slate-300 text-sm p-6 leading-relaxed">
            The feeder forwards the stage-2 bytes verbatim into the decoder through a dedicated
            write thread. Nothing visual changes here — only <b>delay</b> can be added, and only if
            the queue backs up. Health check: the feeder log line
            <span className="font-mono text-xs text-sky-300"> q=…KB peak=…KB</span> — a steadily
            small <span className="font-mono text-xs">q</span> means this hop adds ~0ms.
          </div>
        </StepCard>
        <StepCard n={4} title="ffmpeg decode + scale" sub="1fps samples of the exact decoded, scaled, letterboxed frames"
          badge={hopFresh ? 'LIVE · 1 fps sample' : 'waiting for bot frames…'} live={hopFresh}>
          {hopJpg
            ? <img src={`data:image/jpeg;base64,${hopJpg}`} alt="decoded frame" className="w-full max-h-[42vh] object-contain" />
            : <span className="text-slate-500 text-sm p-8 text-center">Frames appear here once the bot is decoding a live meeting.</span>}
        </StepCard>
        <StepCard n={5} title="Bot pacing → Zoom SDK" sub="Newest-wins frame slot, sent to Zoom at a fixed 25fps raw-send tick"
          badge="timing only">
          <div className="text-slate-300 text-sm p-6 leading-relaxed">
            The bot always sends Zoom the <b>freshest</b> decoded frame on a strict 25fps clock —
            stale frames are superseded, never queued. Nothing visual changes; staleness is the only
            possible artifact. Health check: bot log line
            <span className="font-mono text-xs text-sky-300"> video: sent=… fresh=… read=… superseded=…</span> —
            <span className="font-mono text-xs"> sent</span> climbing at 125/5s means the clock is perfect.
          </div>
        </StepCard>
        <StepCard n={6} title="Zoom encode + delivery" sub="Zoom re-encodes the raw frames and ships them to participants"
          badge="compare externally">
          <div className="text-slate-300 text-sm p-6 leading-relaxed">
            Judged on the <b>Cockpit tab's embedded meeting</b> or a second joined device (Zoom
            renders once per browser). If stages 1–5 are clean but Zoom stutters, the suspects are
            Zoom's own encoder, the bot's network egress (UDP vs TCP fallback), the Zoom data
            center, or the viewer's downlink.
          </div>
        </StepCard>
      </div>
      <p className="text-xs text-slate-500 mt-3">
        Reading order 1 → 6 follows the media path end to end. Motion smoothness is judged on
        stages 1–2; sharpness/framing on stage 4 (it refreshes once per second by design);
        stages 3 and 5 can only add delay, never artifacts.
      </p>
    </div>
  );
};

export default ModelView;
