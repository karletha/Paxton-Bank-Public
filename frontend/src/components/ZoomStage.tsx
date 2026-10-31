// @ts-nocheck
/**
 * Embedded Zoom — COMPONENT VIEW: the real Zoom meeting renders INSIDE this
 * cockpit panel (zoomAppRoot div). The page never changes; every Bridget panel
 * stays visible around the meeting. Zero AI-latency impact: the advisor hears
 * via server-side RTMS and speaks via the native SDK bot regardless of how the
 * banker views the meeting. While embedded, the cockpit's own playback is
 * muted (setZoomActive) — Zoom is the single audio source.
 */
import React, { useEffect, useRef, useState } from 'react';
import { setZoomActive } from '../services/geminiLiveClient';

const V = '3.13.2';

const js = (src: string) => new Promise<void>((res, rej) => {
  const existing = document.querySelector(`script[src="${src}"]`);
  if (existing) {
    if ((existing as any).__loaded) return res();
    existing.addEventListener('load', () => res());
    existing.addEventListener('error', () => rej(new Error('script error')));
    return;
  }
  const s = document.createElement('script'); s.src = src; s.async = true;
  s.onload = () => { (s as any).__loaded = true; res(); };
  s.onerror = () => rej(new Error(`failed to load ${src}`));
  document.body.appendChild(s);
});

// The embedded SDK's UMD global appears a beat AFTER onload — poll for it so a
// fast join click doesn't check too early (that was the "failed to load" bug).
const waitForGlobal = (name: string, ms = 8000) => new Promise<any>((res, rej) => {
  const t0 = Date.now();
  const tick = () => {
    const g = (window as any)[name];
    if (g) return res(g);
    if (Date.now() - t0 > ms) return rej(new Error(`${name} did not initialize`));
    setTimeout(tick, 80);
  };
  tick();
});

const origin = () => (import.meta as any).env?.VITE_BACKEND_URL || window.location.origin;

// ZoomMtgEmbedded.createClient() is a PAGE-LEVEL SINGLETON: the same client is
// handed back on every call. A leftover client from an earlier attempt (failed
// join, remount, retry) is still "joining/joined", so the next join throws
// "Duplicated join operation" (2026-08-13). Always tear the old one down first
// and never let two joins overlap.
let sdkClient: any = null;
let joinGate: Promise<any> = Promise.resolve();

const teardownClient = async (Z: any) => {
  try { await sdkClient?.leave?.(); } catch {}
  try { Z?.destroyClient?.(); } catch {}
  sdkClient = null;
  await new Promise(r => setTimeout(r, 350));   // let the SDK settle
};

export const ZoomStage: React.FC<{
  meetingNumber: string; passcode: string; bankerName: string;
  onJoined: () => void; onLeft: () => void; onEmbedUnavailable?: () => void;
}> = ({ meetingNumber, passcode, bankerName, onJoined, onLeft, onEmbedUnavailable }) => {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const clientRef = useRef<any>(null);
  const [phase, setPhase] = useState<'joining' | 'inmeeting' | 'error'>('joining');
  const [err, setErr] = useState('');
  const [waitNote, setWaitNote] = useState('');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // The embedded UMD bundle expects React/ReactDOM (+redux/lodash) as
        // PAGE GLOBALS — our Vite bundle never exposes them, so without these
        // vendor preloads the SDK throws "React is not defined" and
        // ZoomMtgEmbedded never appears (the original "failed to load" bug).
        // Zoom's own vendor copies are isolated from our bundled React 19.
        for (const v of ['react', 'react-dom', 'redux', 'redux-thunk', 'lodash']) {
          await js(`https://source.zoom.us/${V}/lib/vendor/${v}.min.js`);
        }
        await js(`https://source.zoom.us/${V}/zoom-meeting-embedded-${V}.min.js`);
        const ZoomMtgEmbedded = await waitForGlobal('ZoomMtgEmbedded');
        // serialize against any other mount still joining, then start clean
        const prev = joinGate;
        let release: () => void = () => {};
        joinGate = new Promise<void>(r => { release = r; });
        try { await prev; } catch {}
        await teardownClient(ZoomMtgEmbedded);
        if (cancelled) { release(); return; }
        const client = ZoomMtgEmbedded.createClient();
        sdkClient = client;
        clientRef.current = client;
        const sig = await (await fetch(`${origin()}/zoom/sdk_sign?meeting_number=${encodeURIComponent(String(meetingNumber).replace(/\s+/g, ''))}&role=0`)).json();
        if (cancelled || !rootRef.current) { release(); return; }
        await client.init({
          zoomAppRoot: rootRef.current,
          language: 'en-US',
          patchJsMedia: true,
          customize: {
            video: {
              isResizable: false,
              viewSizes: { default: { width: Math.min(rootRef.current.clientWidth, 880), height: 500 } },
            },
          },
        });
        const joinArgs = {
          sdkKey: sig.sdkKey,
          signature: sig.signature,
          meetingNumber: String(meetingNumber).replace(/\s+/g, ''),
          password: passcode || '',
          userName: bankerName || 'Paxton Banker',
        };
        // The advisor bot is the host and needs ~5s to seat and START the
        // meeting; auto-embed fires immediately after creation, so "Meeting
        // has not started" (3008) is a normal wait-state here, not a failure.
        let joined = false;
        let active = client;
        for (let attempt = 0; attempt < 20 && !joined && !cancelled; attempt++) {
          try {
            await active.join(joinArgs);
            joined = true;
          } catch (je: any) {
            const jm = String(je?.reason || je?.errorMessage || je?.message
              || (typeof je === 'object' ? JSON.stringify(je) : je));
            const notStarted = /not started|3008/i.test(jm);
            const dup = /duplicat/i.test(jm);
            if (!notStarted && !dup) throw je;
            setWaitNote(notStarted
              ? 'Waiting for the advisor to open the meeting room…'
              : 'Resetting a previous session…');
            await teardownClient(ZoomMtgEmbedded);
            if (cancelled) break;
            active = ZoomMtgEmbedded.createClient();
            sdkClient = active; clientRef.current = active;
            await active.init({ zoomAppRoot: rootRef.current, language: 'en-US', patchJsMedia: true,
              customize: { video: { isResizable: false,
                viewSizes: { default: { width: Math.min(rootRef.current.clientWidth, 880), height: 500 } } } } });
            await new Promise(r => setTimeout(r, 3000));
          }
        }
        if (!joined) throw new Error('The meeting did not open in time — use "Open in Zoom app".');
        setWaitNote('');
        release();
        if (cancelled) return;
        setZoomActive(true);              // Zoom owns the audio from here
        setPhase('inmeeting');
        onJoined();
      } catch (e: any) {
        try { (joinGate as any) = Promise.resolve(); } catch {}
        const msg = e?.reason || e?.errorMessage || e?.message
          || (typeof e === 'object' ? JSON.stringify(e).slice(0, 180) : String(e));
        setErr(msg);
        console.error('[Bridget ZoomStage] embed failed:', e);
        setPhase('error');
        // graceful fallback: hand back to the app to open the Zoom app instead
        // of stranding the banker on a red error card (advisor is unaffected).
        if (onEmbedUnavailable) setTimeout(() => onEmbedUnavailable(), 2500);
      }
    })();
    return () => {
      cancelled = true;
      try { clientRef.current?.leave?.(); } catch {}
      try { (window as any).ZoomMtgEmbedded?.destroyClient?.(); } catch {}
      sdkClient = null;
      setZoomActive(false);
    };
  }, [meetingNumber]);

  const leave = () => {
    try { clientRef.current?.leave?.(); } catch {}
    setZoomActive(false);
    onLeft();
  };

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden flex flex-col">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-gray-100">
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-extrabold uppercase tracking-widest text-gray-500">Live Zoom meeting</span>
          <span className="text-[11px] font-mono text-sky-600">{meetingNumber}</span>
          {phase === 'inmeeting' && <span className="flex items-center gap-1 text-[10px] font-bold text-emerald-600"><span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />IN MEETING</span>}
        </div>
        <button onClick={leave} className="text-[11px] font-bold text-gray-500 hover:text-red-600 cursor-pointer">Leave</button>
      </div>
      <div className="relative min-h-[500px] bg-[#0b0b0d] flex items-center justify-center">
        <div ref={rootRef} className="w-full flex items-center justify-center" />
        {phase === 'joining' && (
          <div className="absolute inset-0 flex flex-col items-center justify-center text-white/80 text-sm gap-2 pointer-events-none">
            <div className="w-6 h-6 border-2 border-sky-400 border-t-transparent rounded-full animate-spin" />
            {waitNote || 'Joining the meeting in this panel…'}
          </div>
        )}
        {phase === 'error' && (
          <div className="absolute inset-0 flex flex-col items-center justify-center text-center p-6 gap-2 bg-[#0b0b0d]">
            <div className="text-red-400 text-sm font-bold">Embedded join failed</div>
            <div className="text-white/70 text-xs max-w-md break-words">{err}</div>
            <div className="text-white/50 text-[11px] max-w-md">
              If this says "signature is invalid" or similar, the Zoom app needs the
              Meeting SDK feature enabled in the Zoom Marketplace (Features → Embed).
              Use "open in Zoom app" meanwhile — the advisor works either way.
            </div>
          </div>
        )}
      </div>
      <div className="px-4 py-2 text-[10.5px] text-gray-400 border-t border-gray-100">
        Bridget hears through Zoom's own media stream (RTMS) and speaks through her native seat —
        this panel only changes where YOUR meeting client renders. Cockpit audio is muted while embedded.
      </div>
    </div>
  );
};
