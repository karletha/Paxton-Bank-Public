// @ts-nocheck
import React from 'react';
import {
  Bot, Mic, MicOff, Hand, PauseCircle, Play, Headset,
  ShieldCheck, Check, X, Volume2, Brain, CircleDot, RotateCcw,
} from 'lucide-react';
import { audioAnalyserNode, bindAvatarVideoPair, unbindAvatarPair } from '../services/geminiLiveClient';

interface AvatarPanelProps {
  state: 'idle' | 'listening' | 'thinking' | 'speaking' | 'paused' | 'handoff';
  isLiveConnected: boolean;
  captionText: string;
  onTakeoverClick: () => void;
  onPauseClick: () => void;
  onResetSession?: () => void;
  onMicMuteToggle?: () => void;
  onCameraMuteToggle?: () => void;
  isMicMuted?: boolean;
  isCameraMuted?: boolean;
  consentStatus?: 'PENDING' | 'GRANTED' | 'DECLINED';
  onConsentResponse?: (granted: boolean) => void;
  // NEW: slim in-grid strip (Bridget's face already lives in the Zoom grid, so
  // no video stage is rendered here — just state badge + caption + controls).
  embedded?: boolean;
}

export const AvatarPanel: React.FC<AvatarPanelProps> = ({
  state,
  isLiveConnected,
  captionText,
  onTakeoverClick,
  onPauseClick,
  onResetSession,
  onMicMuteToggle,
  onCameraMuteToggle = undefined,
  isMicMuted = false,
  isCameraMuted = false,
  consentStatus = 'PENDING',
  onConsentResponse,
  embedded = false,
}) => {
  const waveBarsCount = 24;
  const [reactiveHeights, setReactiveHeights] = React.useState<number[]>(new Array(waveBarsCount).fill(6));
  const [glowIntensity, setGlowIntensity] = React.useState<number>(1);
  // LIVE face: our Kira/custom avatar stream (fMP4 over the room socket) pipes
  // into this element. There is NO static person photo — the pre-connect state
  // is a neutral branded monogram so there's never a face-swap.
  const videoRef = React.useRef<HTMLVideoElement | null>(null);
  const videoRefB = React.useRef<HTMLVideoElement | null>(null);
  const [videoLive, setVideoLive] = React.useState(false);

  React.useEffect(() => {
    const a = videoRef.current, b = videoRefB.current;
    // `embedded` MUST be a dep: leaving embed mode (fallback to the in-panel
    // face) mounts the videos AFTER connect — without it the pair never binds
    // and the panel shows "connecting…" forever (review finding)
    if (!isLiveConnected || !a || !b) {
      setVideoLive(false);
      unbindAvatarPair('companion');   // never feed detached elements
      return;
    }
    // ping-pong pair: rebuilds land on the hidden sibling and swap in only
    // once decoding — session reconnects never flash black
    bindAvatarVideoPair('companion', a, b);
    const onPlaying = () => setVideoLive(true);
    a.addEventListener('playing', onPlaying);
    b.addEventListener('playing', onPlaying);
    return () => { a.removeEventListener('playing', onPlaying); b.removeEventListener('playing', onPlaying); };
  }, [isLiveConnected, embedded]);

  React.useEffect(() => {
    if (!audioAnalyserNode || (state !== 'speaking' && state !== 'listening')) {
      setGlowIntensity(1);
      return;
    }

    let animationFrameId: number;
    const bufferLength = audioAnalyserNode.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);

    const updateWave = () => {
      if (!audioAnalyserNode) return;
      audioAnalyserNode.getByteFrequencyData(dataArray);

      const newHeights: number[] = [];
      const step = Math.floor(bufferLength / waveBarsCount) || 1;
      let totalAmplitude = 0;

      for (let i = 0; i < waveBarsCount; i++) {
        let sum = 0;
        let count = 0;
        for (let j = i * step; j < (i + 1) * step && j < bufferLength; j++) {
          sum += dataArray[j];
          count++;
        }
        const avg = count > 0 ? sum / count : 0;
        totalAmplitude += avg;
        const normalizedHeight = Math.max(4, Math.floor((avg / 255) * 28) + 4);
        newHeights.push(normalizedHeight);
      }

      const avgAmp = totalAmplitude / waveBarsCount;
      setGlowIntensity(Math.max(1, 1 + (avgAmp / 255) * 0.45));
      setReactiveHeights(newHeights);
      animationFrameId = requestAnimationFrame(updateWave);
    };

    updateWave();

    return () => {
      cancelAnimationFrame(animationFrameId);
    };
  }, [state, isLiveConnected]);

  // Light-themed state chip (matches the white-card system).
  const getStateBadge = () => {
    switch (state) {
      case 'speaking':
        return { chip: 'bg-sky-50 border-sky-200 text-sky-700', dot: 'bg-sky-500 animate-pulse', label: 'Bridget Speaking', Icon: Volume2 };
      case 'listening':
        return { chip: 'bg-emerald-50 border-emerald-200 text-emerald-700', dot: 'bg-emerald-500 animate-ping', label: 'Listening', Icon: Mic };
      case 'thinking':
        return { chip: 'bg-indigo-50 border-indigo-200 text-indigo-700', dot: 'bg-indigo-500 animate-pulse', label: 'Thinking', Icon: Brain };
      case 'handoff':
        return { chip: 'bg-red-50 border-red-200 text-red-700', dot: 'bg-red-500 animate-ping', label: 'Banker Takeover', Icon: Headset };
      case 'paused':
        return { chip: 'bg-amber-50 border-amber-200 text-amber-700', dot: 'bg-amber-500', label: 'AI Paused', Icon: PauseCircle };
      default:
        return { chip: 'bg-gray-100 border-gray-200 text-gray-600', dot: 'bg-gray-400', label: 'Ready / Standby', Icon: CircleDot };
    }
  };

  // State badge renders ONLY for active states — hidden at idle.
  const renderStateBadge = () => {
    if (state === 'idle') return null;
    const { chip, dot, label, Icon } = getStateBadge();
    return (
      <div className={`flex items-center gap-1.5 border px-2.5 py-1 rounded-full ${chip}`}>
        <span className={`w-1.5 h-1.5 rounded-full ${dot}`} />
        <span className="text-[10px] font-bold tracking-wider uppercase flex items-center gap-1">
          <Icon size={12} />
          {label}
        </span>
      </div>
    );
  };

  // Action controls (mic / Pause / Takeover / Resume) — gated behind
  // isLiveConnected by the callers so pre-meeting shows only stage + caption.
  const renderControls = () => (
    <>
      {onMicMuteToggle && (
        <button
          onClick={onMicMuteToggle}
          className={`p-2.5 rounded-xl border transition-all cursor-pointer flex items-center justify-center ${
            isMicMuted
              ? 'bg-red-50 border-red-200 text-red-600'
              : 'bg-gray-50 hover:bg-gray-100 border-gray-200 text-gray-700'
          }`}
          title={isMicMuted ? 'Unmute Microphone' : 'Mute Microphone'}
        >
          {isMicMuted ? <MicOff size={18} /> : <Mic size={18} />}
        </button>
      )}

      <button
        onClick={onPauseClick}
        className="bg-gray-50 hover:bg-gray-100 active:scale-95 text-[#00254A] font-semibold text-xs px-3.5 py-2 rounded-xl border border-gray-200 transition-all flex items-center gap-1.5 cursor-pointer"
      >
        {state === 'paused' ? <Play size={14} /> : <PauseCircle size={14} />}
        {state === 'paused' ? 'Resume AI' : 'Pause AI'}
      </button>

      <button
        onClick={onTakeoverClick}
        className="bg-gradient-to-r from-red-600 to-red-700 hover:from-red-500 hover:to-red-600 active:scale-95 text-white font-bold text-xs px-4 py-2 rounded-xl shadow-sm transition-all flex items-center gap-1.5 cursor-pointer"
      >
        <Hand size={14} />
        Banker Takeover
      </button>
    </>
  );

  // ---- EMBEDDED: slim strip only (no video stage — Bridget is in the Zoom grid) ----
  if (embedded) {
    return (
      <div className="bg-white border border-gray-200 rounded-xl shadow-sm p-3 flex items-center gap-3 select-none">
        {renderStateBadge()}
        <div className="flex-1 min-w-0 flex items-center gap-2">
          <span
            className={`w-2 h-2 rounded-full shrink-0 ${
              state === 'speaking' ? 'bg-sky-500 animate-pulse' :
              state === 'listening' ? 'bg-emerald-500 animate-ping' :
              state === 'handoff' ? 'bg-red-500' : 'bg-gray-300'
            }`}
          />
          <p className="truncate text-sm font-medium text-[#00254A]">
            {captionText || 'Bridget is standing by.'}
          </p>
        </div>
        {isLiveConnected && (
          <div className="flex items-center gap-2 shrink-0">
            {renderControls()}
          </div>
        )}
      </div>
    );
  }

  // ---- FULL PANEL: white shared card with an inner dark stage ----
  return (
    <div className="bg-white border border-gray-200 rounded-xl shadow-sm p-5 relative overflow-hidden flex flex-col min-h-[440px] select-none">
      {/* Header (kicker + title) — header owns global connection chrome now */}
      <div className="flex items-start justify-between mb-3">
        <div>
          <div className="mb-1 text-[10px] font-bold uppercase tracking-wider text-gray-400">Advisor · Kira Live Model</div>
          <h3 className="flex items-center gap-2 text-[13px] font-bold text-[#00254A]">
            <Bot size={14} className="text-sky-600" /> Bridget (Kira)
          </h3>
        </div>
        <div className="flex items-center gap-2">
          {renderStateBadge()}
          {onResetSession && (
            <button
              onClick={onResetSession}
              title="Reset session and clear transcript"
              className="flex items-center gap-1 rounded-full border border-gray-200 bg-white px-2.5 py-1 text-[10px] font-semibold text-gray-500 hover:text-[#00254A] hover:border-sky-300 transition-all cursor-pointer"
            >
              <RotateCcw size={12} />
              Reset
            </button>
          )}
        </div>
      </div>

      {/* INNER DARK STAGE — the only dark treatment; wraps video/placeholder */}
      <div className="relative flex-1 min-h-[220px] rounded-xl overflow-hidden bg-[#001428] border border-white/10">
        {/* Background Radial Gradient */}
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,_rgba(0,82,165,0.4)_0%,_rgba(0,32,68,0.9)_55%,_#000d1a_100%)] pointer-events-none z-0" />

        {/* Center content: halos + avatar + waveform */}
        <div className="relative z-10 h-full flex flex-col items-center justify-center p-4">
          {/* Pulsing Orbital Halo Rings */}
          <div
            className={`absolute rounded-full border transition-all duration-700 pointer-events-none ${
              state === 'speaking'
                ? 'w-72 h-72 border-sky-400/35 animate-spin'
                : state === 'thinking'
                ? 'w-64 h-64 border-indigo-400/35 animate-spin'
                : state === 'handoff'
                ? 'w-64 h-64 border-red-500/40 animate-pulse'
                : state === 'listening'
                ? 'w-64 h-64 border-emerald-400/30 animate-pulse'
                : 'w-56 h-56 border-sky-500/15'
            }`}
            style={{
              animationDuration: state === 'speaking' ? '10s' : '16s',
              transform: `scale(${glowIntensity})`,
            }}
          />

          {/* Ambient Halo Glow */}
          <div
            className={`absolute w-56 h-56 rounded-full blur-3xl transition-all duration-700 pointer-events-none ${
              state === 'speaking'
                ? 'bg-sky-400/40'
                : state === 'listening'
                ? 'bg-emerald-400/30'
                : state === 'thinking'
                ? 'bg-indigo-500/35'
                : state === 'handoff'
                ? 'bg-red-500/40'
                : 'bg-sky-600/20'
            }`}
            style={{ transform: `scale(${glowIntensity * 1.1})` }}
          />

          {/* Avatar Portrait Card — LIVE stream + neutral pre-connect monogram */}
          <div
            className={`relative w-44 h-44 sm:w-52 sm:h-52 rounded-xl overflow-hidden shadow-2xl transition-all duration-500 border-2 bg-[#001D3D] ${
              state === 'speaking'
                ? 'border-sky-300 shadow-[0_0_55px_rgba(56,189,248,0.65)]'
                : state === 'listening'
                ? 'border-emerald-300 shadow-[0_0_45px_rgba(52,211,153,0.55)]'
                : state === 'thinking'
                ? 'border-indigo-300 shadow-[0_0_50px_rgba(129,140,248,0.65)]'
                : state === 'handoff'
                ? 'border-red-400 shadow-[0_0_55px_rgba(239,68,68,0.75)]'
                : 'border-sky-400/30 shadow-[0_0_30px_rgba(0,130,255,0.3)]'
            }`}
            style={{ transform: `scale(${glowIntensity})` }}
          >
            {/* LIVE avatar video — ping-pong PAIR bound via bindAvatarVideoPair;
                rebuilds attach to the hidden sibling and swap in seamlessly */}
            <div className={`relative w-full h-full ${videoLive ? '' : 'hidden'}`}>
              <video
                ref={videoRef}
                playsInline
                className="absolute inset-0 w-full h-full object-cover object-top select-none"
              />
              <video
                ref={videoRefB}
                playsInline
                className="absolute inset-0 w-full h-full object-cover object-top select-none"
                style={{ opacity: 0 }}
              />
            </div>

            {/* Photorealistic Bridget avatar portrait on stage before/between live video streams */}
            {!videoLive && (
              <div className="w-full h-full relative">
                <img
                  src="/static/companion_avatar.png?v=kira2"
                  alt="Bridget (Kira) — Paxton Bank AI Advisor"
                  className="w-full h-full object-cover object-top select-none"
                />
                <div className="absolute bottom-2.5 inset-x-2.5 bg-black/70 backdrop-blur-md px-3 py-1.5 rounded-xl border border-white/15 flex items-center justify-between shadow-lg">
                  <div className="flex items-center gap-1.5">
                    <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                    <span className="text-[11px] font-extrabold text-white tracking-wider uppercase">Kira · Bridget</span>
                  </div>
                  <span className="text-[9px] text-sky-200 font-semibold uppercase tracking-wider">Standby</span>
                </div>
              </div>
            )}

            {/* Glass identity badge — only over the live face */}
            {videoLive && (
              <div className="absolute bottom-2.5 inset-x-2.5 bg-black/70 backdrop-blur-md px-3 py-1.5 rounded-xl border border-white/15 flex items-center justify-between shadow-lg">
                <div className="flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full bg-sky-400 animate-pulse" />
                  <span className="text-[11px] font-extrabold text-white tracking-wider uppercase">Kira · Bridget</span>
                </div>
                <span className="text-[9px] text-sky-200 font-semibold uppercase tracking-wider">Live 24fps HD</span>
              </div>
            )}
          </div>

          {/* Voice Waveform — only while speaking / listening / thinking */}
          {(state === 'speaking' || state === 'listening' || state === 'thinking') && (
            <div className="flex items-end justify-center gap-1.5 h-8 mt-5 z-10">
              {reactiveHeights.map((h, i) => (
                <div
                  key={i}
                  className={`w-1 rounded-full transition-all duration-75 ${
                    state === 'speaking'
                      ? 'bg-gradient-to-t from-sky-500 to-cyan-200'
                      : state === 'listening'
                      ? 'bg-gradient-to-t from-emerald-500 to-emerald-200'
                      : 'bg-gradient-to-t from-indigo-500 to-purple-200'
                  }`}
                  style={{ height: `${h}px` }}
                />
              ))}
            </div>
          )}
        </div>

        {/* Takeover overlay (handoff) — over the stage, video stays mounted */}
        {state === 'handoff' && (
          <div className="absolute inset-0 z-30 bg-[#001428]/95 flex flex-col items-center justify-center text-center p-4">
            <Headset size={56} className="text-red-400 mb-2 animate-pulse" />
            <span className="text-xs font-bold uppercase tracking-wider text-white">Banker Takeover</span>
            <span className="mt-1 text-[10px] text-red-300/80">AI Stepped Aside</span>
          </div>
        )}
      </div>

      {/* Consent Prompt (light) */}
      {/* Consent is PROSPECT-ONLY (2026-08-11): the banker never consents, so
          the old interactive card is gone — this is a passive status chip.
          Bridget collects the client's verbal consent herself. */}
      {isLiveConnected && consentStatus !== 'DECLINED' && (
        <div className="mt-3 flex items-center gap-2 text-[11px]">
          <ShieldCheck size={14} className={consentStatus === 'GRANTED' ? 'text-emerald-500' : 'text-gray-300'} />
          <span className={consentStatus === 'GRANTED' ? 'text-emerald-600 font-semibold' : 'text-gray-400'}>
            {consentStatus === 'GRANTED' ? 'Client consent recorded' : 'Awaiting client consent — Bridget will ask'}
          </span>
        </div>
      )}

      {/* Live Caption (light) */}
      <div className="mt-3 relative rounded-xl border border-gray-200 bg-gray-50 p-3 min-h-[56px] flex items-center">
        <span
          className={`absolute left-3 top-3.5 w-2 h-2 rounded-full ${
            state === 'speaking' ? 'bg-sky-500 animate-pulse' :
            state === 'listening' ? 'bg-emerald-500 animate-ping' :
            state === 'handoff' ? 'bg-red-500' : 'bg-gray-300'
          }`}
        />
        <p className="pl-5 pr-1 text-sm font-medium leading-relaxed text-[#00254A]">
          {captionText || 'Click "Start Live Call" to begin the briefing with Bridget.'}
        </p>
      </div>

      {/* Action Controls — gated: pre-meeting shows only stage + caption */}
      {isLiveConnected && (
        <div className="mt-3 flex items-center justify-end gap-2 border-t border-gray-200 pt-3">
          {renderControls()}
        </div>
      )}
    </div>
  );
};
