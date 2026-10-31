// @ts-nocheck
/**
 * Paxton Bank Bridget — banker cockpit on the REAL engine (merged 2026-07-31).
 * The UI is Karletha's Bridget cockpit; every panel is now fed by live bridge
 * events (Pulse sentiment, Panel insights, Sentinel guardrails, audit stream,
 * perf spans) and the meeting is a REAL Zoom meeting with the AI advisor
 * seated by a self-hosted Zoom SDK bot. No keyword heuristics, no fabricated
 * metrics: if a number renders, the engine measured it.
 */
import React, { useState, useEffect, useRef } from 'react';
import { initialAppState, AppState, defaultProspectProfile } from './services/sessionState';
import { AvatarPanel } from './components/AvatarPanel';
import { TranscriptPanel, TranscriptMessage } from './components/TranscriptPanel';
import { MilestoneTracker } from './components/MilestoneTracker';
import { CapabilityCanvas } from './components/CapabilityCanvas';
import { BankerCommandCenter } from './components/BankerCommandCenter';
import { GovernanceDrawer } from './components/GovernanceDrawer';
import { SalesHandoffPacket } from './components/SalesHandoffPacket';
import { ZoomMeetingModal } from './components/ZoomMeetingModal';
import { ZoomStage } from './components/ZoomStage';
import { SentimentPanel } from './components/SentimentPanel';
import { Shield } from 'lucide-react';
import Telemetry from './views/Telemetry';
import Journeys from './views/Journeys';
import Agents from './views/Agents';
import ModelView from './views/ModelView';
import { ClientProfile } from './views/ClientProfile';
import {
  connectGeminiLive, disconnectGeminiLive, sendTextTurn,
  onTranscriptUpdate, onStateChange, registerTakeoverHandler,
  initLiveAudioContext, setMicMuted, muteWebMic, unmuteWebMic, muteAllPartyMics, onFeed,
  startZoomMeeting, meetingStarted, grantBankerConsent, endAllZoom, endAllZoomBeacon,
  bankerTakeover, resumeAI, fetchGuardrails, fetchPolicyPanel, registerBankerName, setZoomActive,
  fetchPresentLibrary, presentVisual, stopPresenting, uploadAndPresent,
  fetchVariant, setVariantOnServer, startWebMeeting, startWebMedia, getWebStream, stopWebMedia, endWebMeeting, onPeerCam,
} from './services/geminiLiveClient';

type Tab = 'cockpit' | 'profile' | 'telemetry' | 'journeys' | 'agents' | 'modelview';

export default function App() {
  const urlParams = new URLSearchParams(window.location.search);
  if (urlParams.get('role') === 'prospect') {
    // W2 (prospect second screen on the real room feed) ships next iteration
    return (
      <div className="min-h-screen bg-[#001428] text-white flex items-center justify-center">
        <div className="text-center max-w-md p-8">
          <div className="text-sky-300 font-mono text-xs uppercase tracking-widest mb-3">Paxton Bank Bridget</div>
          <h1 className="text-xl font-bold mb-2">Prospect screen — next iteration</h1>
          <p className="text-white/60 text-sm">The shared capability canvas for the prospect's device is being rebuilt on the live engine. Join the Zoom meeting directly for now.</p>
        </div>
      </div>
    );
  }

  const [tab, setTab] = useState<Tab>('cockpit');
  const [appState, setAppState] = useState<AppState>({ ...initialAppState, sessionState: 'PRE_MEETING' });
  const [isLiveConnected, setIsLiveConnected] = useState(false);
  const [roomId, setRoomId] = useState('');
  const [avatarState, setAvatarState] = useState('idle');
  const [captionText, setCaptionText] = useState('Start a Zoom meeting to bring the Bridget advisor into a live call.');
  const [consentStatus, setConsentStatus] = useState<'PENDING' | 'GRANTED' | 'DECLINED'>('PENDING');
  const [isGovDrawerOpen, setIsGovDrawerOpen] = useState(false);
  const [isMicMutedLocal, setIsMicMutedLocal] = useState(false);
  const [isAllMuted, setIsAllMuted] = useState(false);
  const [transcriptMessages, setTranscriptMessages] = useState<TranscriptMessage[]>([]);
  const [sentiment, setSentiment] = useState({
    score: 0, label: 'Awaiting Conversation',
    insights: 'Live sentiment from the client\'s actual voice — tone, pace and hesitation.',
    timeline: [],
  });
  const [insightCards, setInsightCards] = useState<any[]>([]);
  const [complianceLogs, setComplianceLogs] = useState<string[]>([]);
  const [guardrailTopics, setGuardrailTopics] = useState<any[]>([]);
  const [groundingSources, setGroundingSources] = useState<any[]>([]);
  const [groundingOn, setGroundingOn] = useState(true);
  const [groundingHits, setGroundingHits] = useState<any[]>([]);
  const [presentLib, setPresentLib] = useState<any[]>([]);
  const [nowPresenting, setNowPresenting] = useState<any>(null);
  const [viableScore, setViableScore] = useState<string | null>(null);
  const [emailBody, setEmailBody] = useState('');
  const [avaTtftMs, setAvaTtftMs] = useState<number | null>(null);
  const [zoomMeetingInfo, setZoomMeetingInfo] = useState<any>(null);
  const [isZoomModalOpen, setIsZoomModalOpen] = useState(false);
  const [joinedZoom, setJoinedZoom] = useState(false);
  const [embedded, setEmbedded] = useState(false);
  const [bankerName, setBankerName] = useState('');
  const [variant, setVariant] = useState('zoomv1');
  const [webInfo, setWebInfo] = useState<any>(null);            // webv2/meetv3 meeting info
  const [peerTile, setPeerTile] = useState<{name: string; jpg: string} | null>(null);
  const selfCamRef = useRef<HTMLVideoElement | null>(null);
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const insightCount = useRef(0);
  const guardrailTopicsRef = useRef<any[]>([]);

  const log = (line: string) =>
    setComplianceLogs(prev => [`${new Date().toLocaleTimeString()} · ${line}`, ...prev].slice(0, 120));

  // ---- real-event wiring --------------------------------------------------
  useEffect(() => {
    const unsubs = [
      onTranscriptUpdate((speaker, text, stateUpdate) => {
        setCaptionText(`${speaker}: ${text}`);
        setTranscriptMessages(prev => [...prev, {
          id: 'msg-' + Date.now() + '-' + Math.random().toString(36).slice(2, 5),
          speaker,
          role: stateUpdate?.role,
          text,
          timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
          state: 'speaking',
        }].slice(-200));
      }),
      onStateChange(st => setAvatarState(st)),
      onFeed('connection', (p) => setIsLiveConnected(!!p.connected)),

      // Sentiment Agent (real client sentiment from voice): driver + topic +
      // server-computed delta ride each turn so the tile can show the "why" + trend.
      onFeed('sentiment', (p) => {
        const label = p.score >= 70 ? 'Positive' : p.score >= 45 ? 'Neutral' : 'Skeptical';
        setSentiment(prev => ({
          score: p.score, label, delta: p.delta ?? 0,
          insights: p.driver ? `${p.driver}${p.topic ? ' · ' + p.topic : ''}` : 'Live sentiment from the client\'s voice.',
          timeline: [...prev.timeline, {
            turn: prev.timeline.length + 1, score: p.score, label,
            driver: p.driver || '', topic: p.topic || '',
            timestamp: new Date().toLocaleTimeString([], { minute: '2-digit', second: '2-digit' }),
          }].slice(-8),
        }));
      }),

      // Insights Agent -> capability canvas + milestones (every field bound:
      // category chip, heard quote, recommended action, captured-at time).
      onFeed('insight', (p) => {
        insightCount.current += 1;
        setInsightCards(prev => [...prev, {
          id: 'ins-' + insightCount.current,
          capabilityName: p.title,
          painPointLink: p.source_quote || '',
          plainEnglishSummary: p.detail || '',
          whyItMatters: p.recommended_action || '',
          category: p.category || 'signal',
          capturedAt: p.t ? new Date(p.t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '',
          sourceStatus: 'Heard live in this meeting',
          humanReviewRequired: p.category === 'risk',
          groundingUrls: [],
        }].slice(-8));
        setAppState(prev => {
          const discovery = {
            ...prev.discoveryMilestones,
            pain_points_identified: true,
            complexity_revealed: insightCount.current >= 2 || prev.discoveryMilestones.complexity_revealed,
            expansion_discussed: insightCount.current >= 3 || prev.discoveryMilestones.expansion_discussed,
          };
          // Phase II progresses live like Phase I (not only on approval clicks):
          // discovery complete -> needs confirmed + advance to Solutioning;
          // further insights while solutioning -> solutions being proposed.
          const discoveryDone = discovery.pain_points_identified && discovery.complexity_revealed && discovery.expansion_discussed;
          const inSolutioning = prev.sessionState === 'SOLUTIONING_EDUCATION';
          return {
            ...prev,
            sessionState: discoveryDone && prev.sessionState === 'DISCOVERY' ? 'SOLUTIONING_EDUCATION' : prev.sessionState,
            discoveryMilestones: discovery,
            solutioningMilestones: {
              ...prev.solutioningMilestones,
              needs_confirmed: discoveryDone || prev.solutioningMilestones.needs_confirmed,
              solutions_proposed: (inSolutioning && insightCount.current >= 4) || prev.solutioningMilestones.solutions_proposed,
            },
          };
        });
        log(`Insight: ${p.title}`);
      }),

      // consent state machine (real: verbal detection + banker click, replayed on reconnect)
      onFeed('consent', (p) => {
        if (p.all) {
          setConsentStatus('GRANTED');
          setAppState(prev => ({ ...prev, sessionState: prev.sessionState === 'PRE_MEETING' ? 'DISCOVERY' : prev.sessionState }));
        }
      }),

      // Sentinel/audit -> terminal stream
      onFeed('audit', (p) => log(`${p.event}${p.detail ? ' — ' + p.detail : ''}`)),
      onFeed('system', (p) => {
        if (p.event === 'note' && p.text) log(p.text);
        if (p.event === 'discovery_unlocked') setAppState(prev => ({ ...prev, sessionState: 'DISCOVERY' }));
        if (p.event === 'meeting_concluded') setAppState(prev => ({ ...prev, sessionState: 'DEMO_COMPLETE' }));
        // Phase II #3: banker or Bridget SAID the next steps out loud — that
        // spoken intent is the milestone (approval/handoff still count too).
        if (p.event === 'next_steps_stated') {
          setAppState(prev => ({ ...prev, solutioningMilestones: { ...prev.solutioningMilestones, next_steps_outlined: true } }));
          log(`Next steps stated by ${p.by || 'the banker'}`);
        }
      }),

      // approvals/handoff -> solutioning milestones
      onFeed('approval', (p) => {
        setAppState(prev => ({
          ...prev,
          sessionState: prev.sessionState === 'DISCOVERY' ? 'SOLUTIONING_EDUCATION' : prev.sessionState,
          solutioningMilestones: {
            ...prev.solutioningMilestones,
            needs_confirmed: true,
            solutions_proposed: p.status === 'approved' || prev.solutioningMilestones.solutions_proposed,
          },
        }));
        log(`Materials ${p.status}: ${p.area || p.kind || ''}`);
      }),
      onFeed('handoff', () => {
        setAppState(prev => ({
          ...prev, sessionState: 'SALES_HANDOFF',
          solutioningMilestones: { ...prev.solutioningMilestones, next_steps_outlined: true },
        }));
        log('Warm handoff package prepared');
      }),

      // honest telemetry strip: Bridget's real TTFT
      onFeed('perf', (d) => {
        if (d.comp === 'AvaLive' && d.meta?.ttft_ms) setAvaTtftMs(Math.round(d.meta.ttft_ms));
      }),

      // live "analyzing…" state so the Sentiment/Insight tiles show the agents
      // working between turns (not just when a result lands)
      onFeed('agent_activity', (p) => setBusy(b => ({ ...b, [p.agent]: true }))),
      onFeed('agent_result', (p) => setBusy(b => ({ ...b, [p.agent]: false }))),

      // Opportunity Analysis (Lead Score Agent): 5-dim viability -> the
      // handoff packet's vetted-lead score.
      onFeed('score', (p) => {
        const o = p?.overall?.score;
        if (o != null) setViableScore(`${o}/100 ${o >= 70 ? '· strong' : o >= 40 ? '· qualified' : '· early'}`);
      }),

      // client follow-up email, drafted live by the Archivist agent from
      // captured meeting substance (banker owns the send — copy button only)
      onFeed('email_draft', (p) => {
        if (p?.body) { setEmailBody(p.body); log('Client follow-up email drafted from meeting content'); }
      }),

      // live presenting state (stage on the prospect canvas)
      onFeed('presenting', (p) => {
        setNowPresenting(p && p.img ? p : null);
        if (p && p.img) log(`Presenting on canvas: ${p.title}${p.by === 'auto' ? ' (autonomous)' : ''}`);
      }),

      // corporate knowledge grounding: corpus excerpts Bridget was just handed
      onFeed('grounding', (p) => {
        const hits = p?.hits || [];
        if (!hits.length) return;
        setGroundingHits(prev => [...hits.map((h: any) => ({ ...h, at: Date.now() })), ...prev].slice(0, 6));
        // Bridget grounding an answer in a Paxton Bank capability during Solutioning IS
        // proposing solutions — advance the Phase II milestone live.
        setAppState(prev => prev.sessionState === 'SOLUTIONING_EDUCATION'
          ? { ...prev, solutioningMilestones: { ...prev.solutioningMilestones, solutions_proposed: true } }
          : prev);
        log(`Grounded reply against: ${hits.map((h: any) => h.title).join(' · ')}`);
      }),
    ];

    registerTakeoverHandler((topic, text) => {
      setAvatarState('handoff');
      const label = topic === 'banker_takeover' ? null
        : (guardrailTopicsRef.current.find(t => t.id === topic)?.label || topic);
      setAppState(prev => ({ ...prev, sessionState: 'HUMAN_TAKEOVER', restrictedTriggerTopic: label }));
      setCaptionText(topic === 'banker_takeover' ? 'Banker has the floor — Bridget stepped aside.' : `Banker takeover — ${text || label}`);
      log(topic === 'banker_takeover' ? 'Manual takeover — banker has the floor'
          : `RISK ALERT: ${label} — AI stepped aside, banker has the floor`);
    });

    return () => unsubs.forEach(u => u());
  }, []);

  useEffect(() => { fetchVariant().then(v => {
    setVariant(v);
    if (v === 'webv2') setCaptionText('Start a web meeting to bring Kira (Bridget) into a live 3-way call.');
    if (v === 'meetv3') setCaptionText('Start a Meet meeting — Kira (Bridget) joins through the companion stage.');
  }); }, []);
  useEffect(() => onPeerCam((name, jpg) => setPeerTile({ name, jpg })), []);
  useEffect(() => {
    const s = getWebStream();
    if (webInfo && selfCamRef.current && s && selfCamRef.current.srcObject !== s) {
      selfCamRef.current.srcObject = s;
      selfCamRef.current.play().catch(() => {});
    }
  }, [webInfo]);

  // Policy Panel data is room-independent — load once so the shield drawer and
  // command center are populated before any meeting exists.
  useEffect(() => {
    (async () => {
      const p = await fetchPolicyPanel();
      fetchPresentLibrary().then(setPresentLib);
      if (p?.topics) { setGuardrailTopics(p.topics); guardrailTopicsRef.current = p.topics; }
      if (p?.sources) setGroundingSources(p.sources);
      if (p) setGroundingOn(!!p.grounding_on);
    })();
  }, []);

  // Nothing may outlive the tab: closing/reloading halts every Zoom session.
  useEffect(() => {
    const bye = () => { if (zoomMeetingInfo) endAllZoomBeacon(); };
    window.addEventListener('pagehide', bye);
    window.addEventListener('beforeunload', bye);
    return () => {
      window.removeEventListener('pagehide', bye);
      window.removeEventListener('beforeunload', bye);
    };
  }, [zoomMeetingInfo]);

  // ---- meeting lifecycle --------------------------------------------------
  const handleStartWeb = async (company: string, name: string, kind: 'web' | 'meet') => {
    setBankerName(name);
    await initLiveAudioContext();
    setCaptionText(kind === 'web' ? 'Starting your meeting…' : 'Starting the Meet companion…');
    const m = await startWebMeeting(company, name, kind);
    if (!m.ok) { setCaptionText(`Start failed: ${m.error || 'unknown'}`); return; }
    setWebInfo(m);
    setRoomId(m.room);
    setIsZoomModalOpen(false);
    const ok = await connectGeminiLive(m.room, 'avatar', name);
    setIsLiveConnected(ok);
    await registerBankerName(name);
    setAppState(prev => ({ ...prev, sessionState: 'PRE_MEETING' }));
    if (kind === 'web') {
      setZoomActive(false);                  // Bridget plays right here in the page
      try {
        const stream = await startWebMedia();
        if (selfCamRef.current && stream) {
          selfCamRef.current.srcObject = stream;
          selfCamRef.current.play().catch(() => {});
        }
        log('Camera and microphone live — Kira is on stage and listening');
      } catch (e: any) { log(`Camera/mic note: ${e?.message || e}`); }
    } else {
      setZoomActive(true);                   // companion window is the sound source
      window.open(m.companion_url, '_blank');
      log('Companion stage opened — present that tab into Google Meet');
    }
    const g = await fetchGuardrails();
    if (g?.topics) { setGuardrailTopics(g.topics); guardrailTopicsRef.current = g.topics; }
    setCaptionText(kind === 'web'
      ? 'Meeting live. Copy the invite link for your client — Bridget opens when she hears a voice.'
      : 'Present the companion tab into Meet with tab audio, then start her ears there.');
  };

  const handleEndWeb = async () => {
    if (!webInfo) return;
    const r = await endWebMeeting(webInfo.room);
    stopWebMedia(); setPeerTile(null); setWebInfo(null);
    setIsMicMutedLocal(false);   // engine restarts unmuted — UI must match (hot-mic finding)
    disconnectGeminiLive(); setIsLiveConnected(false); setRoomId('');
    setAvatarState('idle');
    log(`Meeting ended (${r?.rooms_closed ?? 0} room closed)`);
  };

  const handleStartZoom = async (company: string, name: string) => {
    setBankerName(name);
    await initLiveAudioContext();
    setCaptionText('Creating the Zoom meeting and seating the AI advisor…');
    const m = await startZoomMeeting(company, 'ava');
    if (!m.ok) { setCaptionText(`Zoom start failed: ${m.error || 'unknown'}`); return; }
    setZoomMeetingInfo(m);
    setRoomId(m.room);
    setIsZoomModalOpen(false);
    const ok = await connectGeminiLive(m.room, 'avatar', name);
    setIsLiveConnected(ok);
    setZoomActive(true);          // a real meeting exists: Zoom owns ALL audio
    setEmbedded(true);            // join in-cockpit by default (no extra click);
                                  // ZoomStage falls back to the Zoom app if the
                                  // embedded SDK is unavailable
    await registerBankerName(name);
    setAppState(prev => ({ ...prev, sessionState: 'PRE_MEETING' }));
    log(`Zoom meeting ${m.meeting_number} created — room ${m.room}; SDK bot joining (~5s)`);
    const g = await fetchGuardrails();
    if (g?.topics) { setGuardrailTopics(g.topics); guardrailTopicsRef.current = g.topics; }
    setCaptionText('Joining the meeting here — the advisor connects and opens as soon as you are in.');
  };

  const handleJoinedZoom = async () => {
    if (!roomId) return;
    setJoinedZoom(true);
    await meetingStarted(roomId);
    log('Banker joined the Zoom meeting — advisor released');
  };

  const handleEndAll = async () => {
    // immediate: no confirmation dialog (user directive) — halt everything now
    setCaptionText('Ending all Zoom sessions…');
    const r = await endAllZoom();
    setZoomActive(false);
    setEmbedded(false);
    disconnectGeminiLive();
    setIsLiveConnected(false); setJoinedZoom(false); setZoomMeetingInfo(null); setRoomId('');
    setAvatarState('idle');
    log(`Swept: ${r.meetings_ended ?? 0} meeting(s), ${r.bots_stopped ?? 0} bot seat(s) — all sessions halted`);
  };

  const handleReset = () => {
    if (zoomMeetingInfo && !window.confirm('Reset the cockpit? The Zoom meeting keeps running — use End all Zoom to stop it.')) return;
    setZoomActive(false);
    disconnectGeminiLive();
    setIsLiveConnected(false); setJoinedZoom(false); setZoomMeetingInfo(null); setRoomId('');
    setAvatarState('idle'); setTranscriptMessages([]); setConsentStatus('PENDING');
    setIsMicMutedLocal(false);
    // TRIAL FEEDBACK 2026-08-11: reset must clear the compliance audit and the
    // 'Referenced live' grounding excerpts too — they survived until a browser
    // restart before.
    setComplianceLogs([]); setGroundingHits([]); setViableScore(null); setNowPresenting(null); setEmailBody('');
    setAppState({ ...initialAppState, sessionState: 'PRE_MEETING' });
    setInsightCards([]); insightCount.current = 0;
    setSentiment({ score: 0, label: 'Awaiting Conversation', insights: 'Sentiment scores stream from the Pulse agent.', timeline: [] });
    setCaptionText('Session reset. Start a Zoom meeting to begin.');
  };

  const handleMicToggle = () => {
    const next = !isMicMutedLocal;
    setIsMicMutedLocal(next);
    if (webInfo) {
      if (next) muteWebMic(); else unmuteWebMic();
      log(next ? 'Banker microphone muted' : 'Banker microphone live');
      return;
    }
    setMicMuted(next);   // = hold_floor: Ava pauses while the banker holds the floor
    log(next ? 'Floor held — advisor paused' : 'Floor released — advisor listening');
  };

  const handlePauseAIClick = () => {
    const pauseNext = avatarState !== 'paused';
    setMicMuted(pauseNext);
    log(pauseNext ? 'AI paused — Bridget ears muted while you speak' : 'AI resumed — Bridget listening');
  };

  const handleMuteAllToggle = () => {
    const next = !isAllMuted;
    setIsAllMuted(next);
    setIsMicMutedLocal(next);
    muteAllPartyMics(next);
    log(next
      ? 'Presenter Mode ON — Banker mic, Prospect mic, and Bridget ears all muted'
      : 'Presenter Mode OFF — Mics and Bridget ears unmuted');
  };

  const handleTakeoverClick = () => {
    bankerTakeover();
    setAvatarState('handoff');
    setAppState(prev => ({ ...prev, sessionState: 'HUMAN_TAKEOVER', restrictedTriggerTopic: 'Banker manual takeover' }));
    log('Banker initiated manual takeover');
  };
  const handleResumeAI = () => {
    resumeAI();
    setAppState(prev => ({ ...prev, sessionState: 'DISCOVERY', restrictedTriggerTopic: null }));
    log('Banker resumed the AI advisor');
  };

  const handleSendCustomText = (text: string) => { sendTextTurn(text); };

  const handleConsentResponse = async (granted: boolean) => {
    if (granted) {
      await grantBankerConsent();
      setConsentStatus('GRANTED');
      log('Banker consent recorded');
    } else {
      setConsentStatus('DECLINED');
      handleTakeoverClick();
    }
  };

  const restrictedTopicLabels = guardrailTopics.length
    ? guardrailTopics.map(t => t.label || t)
    : ['Rate / pricing commitments & guarantees', 'Regulatory & compliance confirmations', 'Legal / tax advice'];

  // ---- layout -------------------------------------------------------------
  const copyTextToClipboard = async (text: string) => {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
        return;
      }
    } catch {}
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    try { document.execCommand('copy'); } catch {}
    document.body.removeChild(ta);
  };

  const handleSwitchVariant = async (v: string) => {
    const updated = await setVariantOnServer(v);
    setVariant(updated);
    if (updated === 'webv2') setCaptionText('Start a web meeting to bring Kira (Bridget) into a live 3-way call.');
    else if (updated === 'meetv3') setCaptionText('Start a Meet meeting — Kira (Bridget) joins through the companion stage.');
    else setCaptionText('Start a Zoom meeting to bring the Kira (Bridget) advisor into a live call.');
  };

  return (
    <div className="min-h-screen bg-[#F4F6F9] text-[#1F2933] flex flex-col antialiased">
      <div className="bg-[#001428] text-sky-200/90 text-[11px] px-6 py-1.5 flex flex-wrap justify-between items-center border-b border-white/10">
        <div>
          Created by <strong className="text-white">Karletha Paxton</strong>, AI ML Customer Engineer Google ·{' '}
          <a href="https://www.linkedin.com/in/karletha-paxton/" target="_blank" rel="noreferrer" className="text-sky-300 hover:underline font-semibold">
            https://www.linkedin.com/in/karletha-paxton/
          </a>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-sky-300/70 mr-1">Meeting Platform:</span>
          {([
            ['webv2', 'In-Browser 3-Way Call'],
            ['meetv3', 'Google Meet'],
            ['zoomv1', 'Zoom SDK'],
          ] as [string, string][]).map(([vKey, vLabel]) => (
            <button
              key={vKey}
              disabled={!!webInfo || !!zoomMeetingInfo}
              onClick={() => handleSwitchVariant(vKey)}
              className={`px-2 py-0.5 rounded text-[10px] font-bold transition-all cursor-pointer ${
                variant === vKey
                  ? 'bg-sky-500 text-white'
                  : 'bg-white/10 text-sky-200/80 hover:bg-white/20 disabled:opacity-40'
              }`}
            >
              {vLabel}
            </button>
          ))}
        </div>
      </div>
      <header className="bg-[#00254A] text-white px-6 py-4 flex justify-between items-center shadow-lg sticky top-0 z-40">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <span className="text-xl font-black tracking-wider text-white">PAXTON BANK</span>
            <span className="bg-sky-400/20 text-sky-300 font-mono text-xs px-2 py-0.5 rounded border border-sky-400/30 uppercase tracking-widest font-bold">BRIDGET</span>
          </div>
          <span className="hidden md:inline text-xs text-sky-200/70 border-l border-white/20 pl-4">Commercial Banking AI Companion</span>
          <nav className="hidden lg:flex items-center gap-1 border-l border-white/20 pl-4">
            {([['cockpit', 'Cockpit'], ['profile', 'Client Profile'], ['telemetry', 'Telemetry'], ['journeys', 'Journeys'], ['agents', 'Agents'], ['modelview', 'Model View']] as [Tab, string][]).map(([k, label]) => (
              <button key={k} onClick={() => setTab(k)}
                className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all cursor-pointer ${tab === k ? 'bg-sky-500 text-white' : 'text-sky-200/70 hover:bg-white/10'}`}>
                {label}
              </button>
            ))}
            <a href="/guide/Bridget-USER-GUIDE.pdf?v=paxton2" target="_blank" rel="noreferrer"
              className="px-3 py-1.5 rounded-lg text-xs font-bold transition-all cursor-pointer text-sky-200/70 hover:bg-white/10">
              User Guide
            </a>
          </nav>
          {roomId && <span className="hidden xl:inline text-xs text-sky-300/80 font-mono">room <strong className="text-sky-300">{roomId}</strong></span>}
        </div>

        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 bg-black/30 px-3 py-1.5 rounded-full border border-white/10 text-xs">
            <span className={`w-2.5 h-2.5 rounded-full ${isLiveConnected ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'}`} />
            <span className="font-semibold text-white/90">{isLiveConnected ? 'Live' : 'Offline'}</span>
            {avaTtftMs != null && <span className="text-sky-300 font-mono">TTFT {avaTtftMs}ms</span>}
          </div>
          {appState.sessionState === 'HUMAN_TAKEOVER' && (
            <button onClick={handleResumeAI} className="bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-xs px-4 py-2 rounded-lg cursor-pointer">Resume AI</button>
          )}
          {!zoomMeetingInfo && !webInfo ? (
            <button onClick={() => setIsZoomModalOpen(true)}
              className="bg-sky-500 hover:bg-sky-400 text-white font-bold text-xs px-4 py-2 rounded-lg flex items-center gap-1.5 shadow transition-all cursor-pointer">
              {variant === 'webv2' ? 'Start web meeting' : variant === 'meetv3' ? 'Start Meet meeting' : 'Start Zoom meeting'}
            </button>
          ) : (
            <button onClick={webInfo ? handleEndWeb : handleEndAll}
              className="bg-red-600 hover:bg-red-500 text-white font-bold text-xs px-4 py-2 rounded-lg cursor-pointer">
              {webInfo ? 'End meeting' : 'End all Zoom'}
            </button>
          )}
          <button onClick={handleReset} className="bg-white/10 hover:bg-white/20 text-white font-semibold text-xs px-3.5 py-2 rounded-lg cursor-pointer border border-white/10">Reset</button>
          <button onClick={() => setIsGovDrawerOpen(true)} className="p-2 rounded-lg bg-white/10 hover:bg-white/20 text-white cursor-pointer" title="Governance">
            <Shield size={16} />
          </button>
        </div>
      </header>

      <div className={tab === 'profile' ? '' : 'hidden'}><ClientProfile /></div>
      <div className={tab === 'telemetry' ? '' : 'hidden'}><Telemetry /></div>
      <div className={tab === 'journeys' ? '' : 'hidden'}><Journeys /></div>
      <div className={tab === 'agents' ? '' : 'hidden'}><Agents /></div>
      {tab === 'modelview' && <ModelView />}
      <div className={tab === 'cockpit' ? '' : 'hidden'}>{(
        <main className="flex-1 p-6 max-w-[1160px] mx-auto w-full flex flex-col gap-6">
          {/* webv2 / meetv3 meeting strip */}
          {webInfo && (
            <div className="bg-white p-4 rounded-xl border border-gray-200 shadow-sm flex flex-wrap items-center gap-4">
              <div className="text-xs">
                <div className="font-bold text-[#00254A]">{webInfo.kind === 'meet' ? 'Google Meet companion' : 'Web meeting'} · room {webInfo.room}</div>
                <div className="text-gray-500">{webInfo.kind === 'meet'
                  ? 'Present the companion tab into Meet (with tab audio), then start her ears there.'
                  : 'Send your client the invite link — no app, no account, camera and mic in the browser.'}</div>
              </div>
              {webInfo.kind === 'web' ? (
                <div className="flex flex-wrap items-center gap-2 flex-1 justify-end">
                  <input
                    readOnly
                    value={webInfo.join_url}
                    onClick={e => (e.target as HTMLInputElement).select()}
                    className="text-xs font-mono bg-gray-50 border border-gray-300 rounded-lg px-3 py-1.5 text-gray-700 min-w-[280px] flex-1 max-w-md"
                  />
                  <button onClick={() => { copyTextToClipboard(webInfo.join_url); log('Invite URL copied'); }}
                    className="bg-sky-600 hover:bg-sky-500 text-white font-bold text-xs px-4 py-2 rounded-lg cursor-pointer">
                    Copy invite link
                  </button>
                  <button onClick={handleMuteAllToggle}
                    className={`font-bold text-xs px-3.5 py-2 rounded-lg cursor-pointer transition-all ${
                      isAllMuted
                        ? 'bg-red-600 hover:bg-red-500 text-white shadow-sm'
                        : 'bg-slate-800 hover:bg-slate-700 text-white'
                    }`}
                    title="Mutes Banker mic, Prospect mic, and pauses Bridget's ears so you can narrate to the audience without interruption">
                    {isAllMuted ? '🔇 All Mics Muted (Click to Unmute)' : '🎙 Mute All Mics (Presenter Mode)'}
                  </button>
                </div>
              ) : (
                <>
                  <button onClick={() => window.open(webInfo.companion_url, '_blank')}
                    className="bg-sky-600 hover:bg-sky-500 text-white font-bold text-xs px-4 py-2 rounded-lg cursor-pointer">
                    Open companion stage
                  </button>
                  <button onClick={() => window.open('https://meet.new', '_blank')}
                    className="bg-gray-100 hover:bg-gray-200 text-gray-700 font-semibold text-xs px-3 py-2 rounded-lg cursor-pointer">
                    Open Google Meet
                  </button>
                </>
              )}
            </div>
          )}
          {webInfo && webInfo.kind === 'web' && (
            <div className="grid grid-cols-2 gap-3 max-w-[560px]">
              <div className="relative bg-[#0b1220] rounded-xl overflow-hidden border border-gray-300 aspect-[4/3]">
                <video ref={selfCamRef} autoPlay playsInline muted className="w-full h-full object-cover" />
                <span className="absolute left-2 bottom-1.5 text-[11px] text-white bg-black/50 px-2 py-0.5 rounded">{bankerName || 'You'}</span>
                <button
                  onClick={handleMicToggle}
                  className={`absolute right-2 bottom-1.5 text-[10.5px] font-bold px-2.5 py-1 rounded cursor-pointer transition-all ${
                    isMicMutedLocal ? 'bg-red-600 text-white' : 'bg-emerald-600 text-white'
                  }`}
                >
                  {isMicMutedLocal ? '🔇 Mic Muted' : '🎙 Mic Live'}
                </button>
              </div>
              <div className="relative bg-[#0b1220] rounded-xl overflow-hidden border border-gray-300 aspect-[4/3]">
                {peerTile ? <img src={`data:image/jpeg;base64,${peerTile.jpg}`} alt="" className="w-full h-full object-cover" />
                          : <div className="w-full h-full flex items-center justify-center text-[11px] text-gray-400">Waiting for your client to join…</div>}
                <span className="absolute left-2 bottom-1.5 text-[11px] text-white bg-black/50 px-2 py-0.5 rounded">{peerTile?.name || 'Client'}</span>
              </div>
            </div>
          )}

          {/* Meeting strip: real join link + state */}
          {zoomMeetingInfo && (
            <div className="bg-white p-4 rounded-xl border border-gray-200 shadow-sm flex flex-wrap items-center gap-4">
              <div className="text-xs">
                <div className="font-bold text-[#00254A]">Zoom meeting {zoomMeetingInfo.meeting_number}</div>
                <div className="text-gray-500">The Bridget advisor is seated by a self-hosted Zoom SDK bot (~5s join).</div>
              </div>
              {!embedded && (
                <button onClick={() => setEmbedded(true)}
                  className="bg-sky-600 hover:bg-sky-500 text-white font-bold text-xs px-4 py-2 rounded-lg cursor-pointer">
                  Join here (embedded)
                </button>
              )}
              <a href={zoomMeetingInfo.join_url} target="_blank" rel="noreferrer" onClick={handleJoinedZoom}
                className="bg-gray-100 hover:bg-gray-200 text-gray-700 font-semibold text-xs px-3 py-2 rounded-lg cursor-pointer">
                Open in Zoom app
              </a>
              <button onClick={() => copyTextToClipboard(zoomMeetingInfo.join_url)}
                className="bg-gray-100 hover:bg-gray-200 text-gray-700 font-semibold text-xs px-3 py-2 rounded-lg cursor-pointer">
                Copy invite
              </button>
              {!joinedZoom && <span className="text-[11px] text-amber-600 font-semibold">The advisor greets once you join.</span>}
            </div>
          )}

          <section className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
            <div className="lg:col-span-7 flex flex-col gap-3">
              {embedded && zoomMeetingInfo && (
                <ZoomStage meetingNumber={zoomMeetingInfo.meeting_number} passcode={zoomMeetingInfo.passcode}
                  bankerName={bankerName} onJoined={handleJoinedZoom}
                  onLeft={() => { setEmbedded(false); handleEndAll(); }}
                  onEmbedUnavailable={() => { setEmbedded(false); if (zoomMeetingInfo?.join_url) { window.open(zoomMeetingInfo.join_url, '_blank'); handleJoinedZoom(); } log('Embedded view unavailable — opened Zoom app; advisor unaffected'); }} />
              )}
              {/* While embedded, Bridget's face is already in the Zoom grid — the
                  avatar panel collapses to a slim control strip (no duplicate tile). */}
              <AvatarPanel
                embedded={embedded && !!zoomMeetingInfo}
                state={avatarState}
                isLiveConnected={isLiveConnected}
                captionText={captionText}
                onTakeoverClick={handleTakeoverClick}
                onPauseClick={handlePauseAIClick}
                onResetSession={undefined}
                onMicMuteToggle={handleMicToggle}
                onCameraMuteToggle={undefined}
                isMicMuted={isMicMutedLocal}
                isCameraMuted={false}
                consentStatus={consentStatus}
                onConsentResponse={handleConsentResponse}
              />
            </div>
            <div className="lg:col-span-5 flex flex-col">
              <TranscriptPanel messages={transcriptMessages} onSendCustomText={handleSendCustomText} onClearTranscript={() => setTranscriptMessages([])} />
            </div>
          </section>

          {/* Prospect share strip — below the Zoom widget, above Client Sentiment
              (user-specified placement). Opens the client-safe canvas in a new
              tab; the banker screen-shares it in Zoom. */}
          {roomId && (
            <div className="bg-white px-4 py-3 rounded-xl border border-gray-200 shadow-sm flex flex-wrap items-center justify-between gap-3">
              <div className="text-xs">
                <div className="font-bold text-[#00254A]">Share live insights with your client</div>
                <div className="text-gray-500">Opens a client-safe view in a new tab — product capabilities, referenced Paxton Bank sources, and verified figures only. Screen-share it in Zoom.</div>
              </div>
              <button
                onClick={() => { window.open(`${window.location.origin}/?share=${encodeURIComponent(roomId)}`, '_blank'); log('Prospect share canvas opened in a new tab'); }}
                className="bg-[#00254A] hover:bg-[#003566] text-white font-bold text-xs px-4 py-2 rounded-lg flex items-center gap-1.5 shadow transition-all cursor-pointer">
                Open shared canvas
              </button>
            </div>
          )}

          {/* PRESENT ON CANVAS — CCB visual library + ad-hoc upload. Autonomous
              presenting is primary (Bridget matches prospect questions to
              slides); these controls override or stop the stage. */}
          {roomId && (
            <div className="bg-white px-4 py-3 rounded-xl border border-gray-200 shadow-sm flex flex-col gap-2.5">
              <div className="flex items-center justify-between flex-wrap gap-2">
                <div className="text-xs">
                  <div className="font-bold text-[#00254A]">Present on the shared canvas</div>
                  <div className="text-gray-500 text-[11px]">
                    Autonomous mode answers location/capability questions with a visual automatically.
                    {nowPresenting ? <> Now showing: <b className="text-sky-700">{nowPresenting.title}</b></> : ' Nothing on stage.'}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <label className="bg-gray-100 hover:bg-gray-200 text-gray-700 font-bold text-[11px] px-3 py-1.5 rounded-lg cursor-pointer">
                    Upload & present
                    <input type="file" accept="image/png,image/jpeg" className="hidden"
                      onChange={async e => {
                        const f = e.target.files?.[0]; if (!f) return;
                        const r = await uploadAndPresent(roomId, f);
                        if (r?.ok) log(`Uploaded & presenting: ${f.name}`);
                        e.target.value = '';
                      }} />
                  </label>
                  {nowPresenting && (
                    <button onClick={async () => { await stopPresenting(roomId); log('Stage cleared'); }}
                      className="bg-red-50 hover:bg-red-100 text-red-600 font-bold text-[11px] px-3 py-1.5 rounded-lg cursor-pointer border border-red-100">
                      Stop presenting
                    </button>
                  )}
                </div>
              </div>
              <div className="flex gap-1.5 flex-wrap">
                {presentLib.map(v => (
                  <button key={v.id}
                    onClick={async () => { const r = await presentVisual(roomId, v.id); if (r?.ok) log(`Presenting: ${v.title}`); }}
                    className={`text-[10.5px] font-bold px-2.5 py-1.5 rounded-lg border transition-colors cursor-pointer ${nowPresenting?.id === v.id ? 'bg-sky-600 text-white border-sky-600' : 'bg-gray-50 text-gray-600 border-gray-200 hover:border-sky-300 hover:text-sky-700'}`}>
                    {v.title}
                  </button>
                ))}
              </div>
              {/* Live mirror of the prospect's stage — so the banker (and any
                  dashboard-view recording) SEES what the client is seeing. */}
              {nowPresenting?.img && (
                <a href={`${window.location.origin}/?share=${encodeURIComponent(roomId)}`} target="_blank" rel="noreferrer"
                  title="What the client sees now — click to open the shared canvas" className="block w-fit">
                  <img src={nowPresenting.img} alt={nowPresenting.title}
                    className="max-h-48 rounded-lg border-2 border-sky-300 shadow-md" />
                  <div className="text-[10px] text-sky-700 font-semibold mt-1">● On the client's briefing page now — {nowPresenting.title}</div>
                </a>
              )}
            </div>
          )}

          <section className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
            <div className="lg:col-span-5"><SentimentPanel sentiment={sentiment} analyzing={busy.Pulse} /></div>
            <div className="lg:col-span-7">
              <MilestoneTracker
                sessionState={appState.sessionState}
                discoveryMilestones={appState.discoveryMilestones}
                solutioningMilestones={appState.solutioningMilestones}
              />
            </div>
          </section>

          <section>
            <CapabilityCanvas cards={insightCards} needsConfirmed={insightCards.length > 0} analyzing={busy.Panel} />
          </section>

          <section>
            <BankerCommandCenter
              bankerName={bankerName || undefined}
              groundingSources={groundingSources}
              groundingOn={groundingOn}
              groundingHits={groundingHits}
              restrictedTopics={restrictedTopicLabels}
              activeTriggeredTopic={appState.restrictedTriggerTopic}
              complianceLogs={complianceLogs}
              systemLogs={[]}
              armed={true}
              telemetryMetrics={{
                latencyMs: avaTtftMs,
                tokensPerSecond: null,
                websocketStatus: isLiveConnected ? 'Connected (WSS)' : 'Standby',
                groundingConfidence: null,
              }}
              onResetDemo={undefined}
            />
          </section>

          {/* Handoff packet compiles only once the meeting has produced insights
              (or reached handoff) — no empty scaffolding pre-meeting. */}
          {(insightCards.length > 0 || appState.sessionState === 'SALES_HANDOFF') ? (
            <section className="grid grid-cols-1 lg:grid-cols-12 gap-6">
              <div className="lg:col-span-12">
                <SalesHandoffPacket
                  prospectProfile={defaultProspectProfile}
                  discoveredPainPoints={insightCards.map(c => c.painPointLink).filter(Boolean)}
                  confirmedNeeds={insightCards.map(c => c.plainEnglishSummary).filter(Boolean)}
                  proposedSolutions={insightCards.map(c => c.whyItMatters).filter(Boolean)}
                  emailBody={emailBody}
                  viableScore={viableScore}
                  crmPayloadSummary={JSON.stringify({
                    room: roomId || 'no-session',
                    insights: insightCards.map(c => c.capabilityName),
                    stage: appState.sessionState,
                    ai_agent: 'Bridget (Paxton Bank AI advisor)',
                  }, null, 2)}
                />
              </div>
            </section>
          ) : (
            <div className="rounded-xl border border-dashed border-gray-200 bg-gray-50 py-6 text-center text-[12px] italic text-gray-400">
              Handoff packet compiles as the meeting produces insights.
            </div>
          )}
        </main>
      )}</div>

      <footer className="bg-[#001B38] text-sky-200/80 text-xs py-3 px-6 border-t border-white/10 text-center">
        Created by <strong className="text-white">Karletha Paxton</strong>, AI ML Customer Engineer Google ·{' '}
        <a href="https://www.linkedin.com/in/karletha-paxton/" target="_blank" rel="noreferrer" className="text-sky-300 hover:underline font-semibold">
          https://www.linkedin.com/in/karletha-paxton/
        </a>
      </footer>
      <GovernanceDrawer isOpen={isGovDrawerOpen} onClose={() => setIsGovDrawerOpen(false)}
        groundingSources={groundingSources} restrictedTopics={restrictedTopicLabels} />
      <ZoomMeetingModal isOpen={isZoomModalOpen} onClose={() => setIsZoomModalOpen(false)} onStart={variant === 'zoomv1' ? handleStartZoom : (c, n) => handleStartWeb(c, n, variant === 'meetv3' ? 'meet' : 'web')} />
    </div>
  );
}

