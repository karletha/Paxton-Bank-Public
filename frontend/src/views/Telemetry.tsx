// @ts-nocheck
/**
 * Telemetry — the Live Ops console ported into Bridget.
 *
 * End-to-end observability for the meeting fleet: a connected flow graph whose
 * nodes light up in sync with live traffic or a replay playhead, a per-reply
 * latency waterfall (hear→think gap / TTFT / speaking / transport), a Gantt
 * timeline of every component span, a component glossary, and a latency table.
 *
 * Data comes from the real bridge via the Bridget service adapter:
 *   fetchPerfRooms()  GET /perf/rooms          (replay room list)
 *   fetchRoomPerf(r)  GET /rooms/{r}/perf      (spans for one room)
 *   onFeed('perf')    live perf frames         ({comp, ms, phase, t, meta})
 * Span timing: start = t*1000 - ms, end = t*1000 (t is epoch seconds).
 */
import React from 'react'
import { Activity, Gauge, Play, Pause, Radio, Rewind, Clock, BookOpen, ChevronDown } from 'lucide-react'
import { fetchPerfRooms, fetchRoomPerf, fetchRoomAgentLog, onFeed, getRoomId } from '../services/geminiLiveClient'
import { ccolor, label, pctl, COMP_WHAT, COMP_ORDER, ACTIVE_AGENTS, PARKED_AGENTS } from '../lib/perf'

const fmt = v => (v == null ? '—' : v >= 1000 ? (v / 1000).toFixed(1) + 's' : Math.round(v) + 'ms')
const NAVY = '#00254A'
const SKY = '#0284C7'
// transport = relay(~17ms) + SDK encode + Zoom distribution. Not in spans; a
// measured-elsewhere constant so the end-to-end total is honest, not implied 0.
const TRANSPORT_MS = 1200

// ---- Connected node graph: individual components wired by their real data
// flow; each node lights when active (live now, or under the replay playhead),
// and the edge feeding it pulses — so you can eye-track the traffic. The fleet
// was trimmed to Pulse/Panel/Archivist (+ Sentinel guardrails); the retired
// agents stay on the map as small dimmed "parked" nodes so the graph is honest.
const FLOW_NODES = [
  { k: 'Zoom', label: 'Zoom meeting', x: 16, y: 218, kind: 'io' },
  { k: 'ZoomEars (RTMS)', label: 'Meeting Listener', x: 168, y: 218, kind: 'ears' },
  // ANALYSIS agents (trimmed fleet) — fed by the ears
  { k: 'Pulse', label: 'Sentiment Agent', x: 392, y: 40, kind: 'brain' },
  { k: 'Panel', label: 'Insights Agent', x: 392, y: 100, kind: 'brain' },
  { k: 'Archivist', label: 'Memory Agent', x: 392, y: 160, kind: 'brain' },
  { k: 'Sentinel', label: 'Compliance Agent', x: 392, y: 220, kind: 'brain' },
  // RENDERER: Bridget hears the meeting directly AND consumes agent context
  { k: 'AvaLive', label: 'Bridget (live agent · barge-in)', x: 392, y: 316, kind: 'mouth', w: 230 },
  { k: 'Render', label: 'Zoom seat (bot)', x: 640, y: 316, kind: 'render' },
  { k: 'ZoomOut', label: 'Bridget in Zoom', x: 812, y: 316, kind: 'io' },
  // UX COLUMN — what the banker sees, one surface per agent output
  { k: 'UX:Sentiment', label: 'Sentiment panel', x: 690, y: 40, kind: 'ux' },
  { k: 'UX:Canvas', label: 'Insight cards', x: 690, y: 100, kind: 'ux' },
  { k: 'UX:Memory', label: 'internal memory · no UX yet', x: 690, y: 160, kind: 'gap' },
  { k: 'UX:Guardrails', label: 'Compliance board', x: 690, y: 220, kind: 'ux' },
  { k: 'UX:Transcript', label: 'Transcript & captions', x: 916, y: 128, kind: 'ux' },
  { k: 'UX:AvatarTile', label: 'Avatar tile (cockpit)', x: 916, y: 250, kind: 'ux' },
]
// [from, to, style] — style 'ctx' = dashed context injection into the renderer
const FLOW_EDGES = [
  ['Zoom', 'ZoomEars (RTMS)'],
  ...ACTIVE_AGENTS.map(k => ['ZoomEars (RTMS)', k]),
  ['ZoomEars (RTMS)', 'AvaLive'],
  ['Archivist', 'AvaLive', 'ctx'],    // meeting memory -> her prompt
  ['Sentinel', 'AvaLive', 'ctx'],     // trip -> interrupt + safe line
  ['AvaLive', 'Render'],
  // barge-in: a human talking over her makes the live agent stop and the
  // bridge drop every queued avatar chunk before it reaches the seat
  ['ZoomEars (RTMS)', 'Render', 'ctx'],
  ['Render', 'ZoomOut'],
  ['Pulse', 'UX:Sentiment'],
  ['Panel', 'UX:Canvas'],
  ['Archivist', 'UX:Memory', 'ctx'],
  ['Sentinel', 'UX:Guardrails'],
  ['ZoomEars (RTMS)', 'UX:Transcript'],
  ['AvaLive', 'UX:AvatarTile'],
]
const KIND_COL = { io: NAVY, ears: '#8B5CF6', brain: '#0891B2', mouth: SKY, render: '#0EA5A0', ux: '#0F766E', gap: '#B45309' }
const NW = 128, NH = 26
const PW = 100, PH = 20   // parked node size
const PARKED_POS = PARKED_AGENTS.map((k, i) => ({ k, x: 252 + i * 118, y: 428 }))
const UX_SOURCE = { 'UX:Sentiment': 'Pulse', 'UX:Canvas': 'Panel', 'UX:Memory': 'Archivist',
                    'UX:Guardrails': 'Sentinel', 'UX:Transcript': 'ZoomEars (RTMS)', 'UX:AvatarTile': 'AvaLive' }
function nodeActive(k, activeSet) {
  if (k === 'Render' || k === 'ZoomOut') return activeSet.has('AvaLive')
  if (k === 'Zoom') return activeSet.has('ZoomEars (RTMS)')
  if (UX_SOURCE[k]) return activeSet.has(UX_SOURCE[k])
  return activeSet.has(k)
}
function FlowGraph({ activeSet }) {
  const byK = Object.fromEntries(FLOW_NODES.map(n => [n.k, n]))
  const cx = n => n.x + NW / 2, cyv = n => n.y + NH / 2
  return (
    <svg viewBox="0 0 1080 470" className="w-full" style={{ maxHeight: 480 }}>
      <defs>
        <marker id="tel-arw" markerWidth="7" markerHeight="7" refX="6" refY="3" orient="auto">
          <path d="M0,0 L6,3 L0,6 Z" fill="#94A3B8" />
        </marker>
        <marker id="tel-arwOn" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
          <path d="M0,0 L6,3 L0,6 Z" fill={SKY} />
        </marker>
      </defs>
      {FLOW_EDGES.map(([a, b, style], i) => {
        const na = byK[a], nb = byK[b]; if (!na || !nb) return null
        const on = nodeActive(b, activeSet) || nodeActive(a, activeSet)
        const naw = na.w || NW
        const x1 = na.x + naw, y1 = cyv(na), x2 = nb.x, y2 = cyv(nb)
        const mx = (x1 + x2) / 2
        const d = `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`
        const isCtx = style === 'ctx'
        return (
          <path key={i} d={d} fill="none"
            stroke={on ? (isCtx ? '#7C3AED' : SKY) : '#E5E7EB'} strokeWidth={on ? (isCtx ? 1.8 : 2.4) : 1.2}
            strokeDasharray={isCtx ? '5,4' : (on ? undefined : undefined)}
            markerEnd={`url(#${on ? 'tel-arwOn' : 'tel-arw'})`}
            style={on ? { filter: 'drop-shadow(0 0 3px rgba(2,132,199,.4))' } : {}}>
            {on && !isCtx && <animate attributeName="stroke-dasharray" values="1,7;8,0" dur="0.7s" repeatCount="indefinite" />}
          </path>
        )
      })}
      {FLOW_NODES.map(n => {
        const on = nodeActive(n.k, activeSet); const col = KIND_COL[n.kind]
        const w = n.w || NW
        const isGap = n.kind === 'gap'
        return (
          <g key={n.k} opacity={isGap ? 0.85 : 1}>
            <rect x={n.x} y={n.y} rx="7" width={w} height={NH}
              fill={on ? col : (isGap ? '#FFFBEB' : '#FBFDFF')}
              stroke={on ? col : (isGap ? '#F59E0B' : '#E5E7EB')} strokeWidth={on ? 2 : 1}
              strokeDasharray={isGap ? '4,3' : undefined}
              style={on ? { filter: `drop-shadow(0 0 8px ${col})` } : {}} />
            <text x={n.x + w / 2} y={cyv(n) + 3.5} textAnchor="middle"
              fontSize={isGap ? 9.5 : 11} fontWeight="700" fill={on ? '#fff' : (isGap ? '#B45309' : '#5A6B80')}>{n.label}</text>
          </g>
        )
      })}
      {/* legend */}
      <g fontSize="9.5" fill="#64748B">
        <line x1={16} y1={402} x2={44} y2={402} stroke={SKY} strokeWidth="2.2" />
        <text x={50} y={405}>media / audio path</text>
        <line x1={160} y1={402} x2={188} y2={402} stroke="#7C3AED" strokeWidth="1.8" strokeDasharray="5,4" />
        <text x={194} y={405}>context injected into the renderer</text>
        <rect x={370} y={396} rx="3" width={12} height={12} fill="#F0FDFA" stroke="#0F766E" />
        <text x={388} y={405}>cockpit surface (what the banker sees)</text>
        <rect x={600} y={396} rx="3" width={12} height={12} fill="#FFFBEB" stroke="#F59E0B" strokeDasharray="4,3" />
        <text x={618} y={405}>agent output with NO UX surface yet (design gap)</text>
      </g>
      {/* parked fleet — present but switched off in this build */}
      <rect x={232} y={414} rx="9" width={640} height={44} fill="#F8FAFC" stroke="#E5E7EB" strokeDasharray="4,4" />
      <text x={244} y={426} fontSize="9" fontWeight="700" fill="#94A3B8" letterSpacing="0.8">PARKED AGENTS — FLEET TRIMMED FOR THIS BUILD</text>
      {PARKED_POS.map(p => {
        const on = activeSet.has(p.k); const col = ccolor(p.k)
        return (
          <g key={p.k} opacity={on ? 1 : 0.6}>
            <rect x={p.x} y={p.y} rx="5" width={PW} height={PH}
              fill={on ? col : '#F1F5F9'} stroke={on ? col : '#E2E8F0'} strokeWidth={1} />
            <text x={p.x + PW / 2} y={p.y + PH / 2 + 3} textAnchor="middle"
              fontSize="9" fontWeight="600" fill={on ? '#fff' : '#94A3B8'}>{label(p.k)}</text>
          </g>
        )
      })}
    </svg>
  )
}

// ---- Response-latency waterfall: for each Bridget reply, break the end-to-end
// delay into its segments and (critically) the GAP between hearing you and her
// first word — that gap is where most of the "delay in between" lives. ----
function LatencyWaterfall({ spans }) {
  const ava = spans.filter(s => s.comp === 'AvaLive' && s.phase !== 'err').sort((a, b) => a.start - b.start)
  const heard = spans.filter(s => s.comp === 'ZoomEars (RTMS)').sort((a, b) => a.start - b.start)
  const turns = ava.slice(-6).map(a => {
    const h = [...heard].reverse().find(x => x.end <= a.start + 300)   // last thing heard before she replied
    const gap = h ? Math.max(0, a.start - h.end) : null                // you-stopped -> her-turn-start (detect+think)
    const ttft = a.ttft != null ? a.ttft : null                        // her-turn-start -> first word
    const speak = a.ttft != null ? Math.max(0, a.ms - a.ttft) : a.ms   // first word -> turn end
    const total = (gap || 0) + a.ms + TRANSPORT_MS
    return { a, gap, ttft, speak, total, heardText: h && h.meta && h.meta.text }
  })
  const segs = t => [
    t.gap != null && { k: 'gap', label: 'hear→think', v: t.gap, c: '#8B5CF6' },
    t.ttft != null && { k: 'ttft', label: 'think→1st word (TTFT)', v: t.ttft, c: SKY },
    { k: 'speak', label: 'speaking', v: t.speak, c: '#0EA5A0' },
    { k: 'xport', label: 'transport→Zoom', v: TRANSPORT_MS, c: '#94A3B8' },
  ].filter(Boolean)
  const gaps = turns.map(t => t.gap).filter(v => v != null)
  const ttfts = turns.map(t => t.ttft).filter(v => v != null)
  return (
    <div className="mb-4 rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
      <div className="mb-1 text-[10px] font-bold uppercase tracking-wider text-gray-400">Response latency</div>
      <h2 className="mb-1 flex items-center gap-2 text-[13px] font-bold text-[#00254A]"><Clock size={14} className="text-sky-600" /> Where the delay lives</h2>
      <p className="mb-3 text-[11.5px] text-gray-500">Each bar is one Bridget reply, split into its segments. The <b style={{ color: '#8B5CF6' }}>hear→think</b> gap (you stop talking → her turn begins) and <b style={{ color: SKY }}>TTFT</b> (→ Bridget's first word) are the model-side delay; <b className="text-slate-500">transport</b> is the fixed ~{(TRANSPORT_MS / 1000).toFixed(1)}s (measured) to encode+deliver into Zoom.</p>
      {!turns.length ? (
        <p className="rounded-lg border border-dashed border-gray-200 bg-gray-50 py-6 text-center text-[12px] italic text-gray-400">
          No Bridget replies in the loaded data yet. Start a meeting and speak to her (or load a meeting with replies) — each reply will appear here broken into hear→think · TTFT · speaking · transport.
        </p>
      ) : (<>
      <div className="mb-3 flex flex-wrap gap-4 text-[11px]">
        <span className="text-gray-500">avg hear→think <b className="text-[#00254A]">{fmt(gaps.length ? pctl(gaps, 50) : null)}</b></span>
        <span className="text-gray-500">avg TTFT <b className="text-[#00254A]">{fmt(ttfts.length ? pctl(ttfts, 50) : null)}</b></span>
        <span className="text-gray-500">avg end-to-end <b className="text-[#00254A]">{fmt(pctl(turns.map(t => t.total), 50))}</b></span>
      </div>
      {turns.slice().reverse().map((t, i) => {
        const ss = segs(t); const max = Math.max(t.total, ...turns.map(x => x.total))
        return (
          <div key={i} className="mb-2.5">
            <div className="mb-1 flex items-center gap-2 text-[10.5px]">
              <span className="font-bold text-[#00254A]">reply {turns.length - i}</span>
              {t.heardText && <span className="truncate italic text-gray-400">after: “{t.heardText}”</span>}
              <span className="flex-1" /><span className="font-bold text-[#00254A]">{fmt(t.total)} end-to-end</span>
            </div>
            <div className="flex h-[22px] w-full overflow-hidden rounded-md bg-gray-100">
              {ss.map((sg, j) => (
                <div key={j} title={`${sg.label}: ${fmt(sg.v)}`} className="flex items-center justify-center text-[9px] font-bold text-white"
                  style={{ width: `${(sg.v / max) * 100}%`, background: sg.c, minWidth: sg.v > 0 ? 2 : 0 }}>
                  {(sg.v / max) > 0.12 ? fmt(sg.v) : ''}
                </div>
              ))}
            </div>
          </div>
        )
      })}
      <div className="mt-2 flex flex-wrap gap-3 text-[9.5px] text-gray-500">
        {[['#8B5CF6', 'hear→think (turn-taking + model start)'], [SKY, 'TTFT (model → first word)'], ['#0EA5A0', 'speaking'], ['#94A3B8', 'transport→Zoom (fixed, measured)']].map(([c, l]) =>
          <span key={l} className="inline-flex items-center gap-1"><span className="h-2 w-2 rounded-full" style={{ background: c }} />{l}</span>)}
      </div>
      </>)}
    </div>
  )
}

function Glossary({ comps }) {
  const [open, setOpen] = React.useState(true)
  const roleColor = { EARS: '#8B5CF6', BRAIN: '#0891B2', MOUTH: SKY }
  const seen = comps.filter(c => COMP_WHAT[c])
  const list = seen.length ? seen : Object.keys(COMP_WHAT)
  return (
    <div className="mb-4 rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
      <button onClick={() => setOpen(o => !o)} className="flex w-full items-center gap-2 text-[13px] font-bold text-[#00254A]">
        <BookOpen size={14} className="text-sky-600" /> What each component does
        <span className="flex-1" /><ChevronDown size={15} className={`text-gray-400 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {list.map(c => {
            const w = COMP_WHAT[c]
            return (
              <div key={c} className={`rounded-lg border border-gray-200 bg-gray-50 p-3 ${w.parked ? 'opacity-60' : ''}`}>
                <div className="mb-0.5 flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-full" style={{ background: ccolor(c) }} />
                  <span className="text-[12px] font-bold text-[#00254A]">{label(c)}</span>
                  <span className="rounded px-1.5 py-0.5 text-[8.5px] font-extrabold text-white" style={{ background: roleColor[w.role] || '#64748B' }}>{w.role === 'BRAIN' ? 'AGENT' : w.role}</span>
                  {w.parked && <span className="rounded bg-gray-200 px-1.5 py-0.5 text-[8.5px] font-extrabold text-gray-500">PARKED</span>}
                </div>
                <div className="text-[11px] leading-snug text-gray-700">{w.one}</div>
                <div className="mt-1 text-[10px] leading-snug text-gray-500"><b>latency:</b> {w.lat}</div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

export default function Telemetry() {
  const [, tickState] = React.useState(0)
  const [mode, setMode] = React.useState('live')
  const [rooms, setRooms] = React.useState([])
  const [picked, setPicked] = React.useState('')
  const [winSec, setWinSec] = React.useState(60)
  const [playhead, setPlayhead] = React.useState(1)
  const [playing, setPlaying] = React.useState(false)
  const [speed, setSpeed] = React.useState(1)
  const rafPrev = React.useRef(0)

  // perf spans + live light-up map (comp -> expiry ts), same semantics as the
  // old console's bus: live frames append (last 300) and arm a glow window.
  const [perf, setPerf] = React.useState([])
  const perfRef = React.useRef(perf)
  React.useEffect(() => { perfRef.current = perf }, [perf])
  const [activeComp, setActiveComp] = React.useState({})

  // WHAT IS ACTUALLY RUNNING (models, agent runtime, barge-in config) — read
  // from the bridge so the tab can never drift from the deployed reality.
  const [runtime, setRuntime] = React.useState(null)
  React.useEffect(() => {
    const o = (import.meta as any).env?.VITE_BACKEND_URL || window.location.origin
    fetch(`${o}/runtime`).then(r => r.json()).then(setRuntime).catch(() => {})
  }, [])

  const [agentLogs, setAgentLogs] = React.useState([])
  const [selected, setSelected] = React.useState(null)   // { comp, s } clicked span
  const loadPerf = React.useCallback(async (room) => {
    const d = await fetchRoomPerf(room)
    const list = Array.isArray(d) ? d : (d && Array.isArray(d.spans) ? d.spans : [])
    setPerf(list.map(s => ({ comp: s.comp, ms: s.ms, phase: s.phase, t: s.t, meta: s.meta })))
    setLoadedRoom(room)
    fetchRoomAgentLog(room).then(setAgentLogs).catch(() => {})
  }, [])
  const [loadedRoom, setLoadedRoom] = React.useState('')
  // pair a clicked perf span with its full input/output from the agent log:
  // same agent (comp) and the nearest timestamp.
  const matchedLog = React.useMemo(() => {
    if (!selected) return null
    const cands = agentLogs.filter(l => l.agent === selected.comp)
    if (!cands.length) return null
    const st = selected.s.t
    return cands.reduce((best, l) => Math.abs(l.t - st) < Math.abs((best?.t ?? -1e9) - st) ? l : best, null)
  }, [selected, agentLogs])

  React.useEffect(() => { const iv = setInterval(() => tickState(x => x + 1), 300); return () => clearInterval(iv) }, [])
  React.useEffect(() => onFeed('perf', d => {
    if (!d || d.comp == null) return
    setPerf(p => [...p, { comp: d.comp, ms: d.ms, phase: d.phase, t: d.t, meta: d.meta }].slice(-300))
    setActiveComp(a => ({ ...a, [d.comp]: Date.now() + Math.min(6000, Math.max(900, d.ms || 0)) }))
  }), [])
  React.useEffect(() => { fetchPerfRooms().then(rs => setRooms((rs || []).slice().sort((a, b) => (b.last || 0) - (a.last || 0)))).catch(() => {}) }, [])

  const rid = getRoomId()
  React.useEffect(() => { if (mode === 'live' && rid && !perfRef.current.length) loadPerf(rid) }, [rid, mode, loadPerf])
  // never show an empty console: if there's no live meeting, auto-load the most
  // recent meeting's spans so the waterfall/timeline/table are populated the
  // moment you open the tab.
  const [autoLoaded, setAutoLoaded] = React.useState(false)
  React.useEffect(() => {
    if (autoLoaded || rid || perfRef.current.length) return
    fetchPerfRooms().then(rs => {
      const sorted = (rs || []).slice().sort((a, b) => (b.last || 0) - (a.last || 0))
      if (sorted.length) { loadPerf(sorted[0].room_id); setAutoLoaded(true) }
    }).catch(() => {})
  }, [rid, autoLoaded, loadPerf])

  const spans = perf.map(s => ({ ...s, start: s.t * 1000 - s.ms, end: s.t * 1000, ttft: s.meta && s.meta.ttft_ms }))
  const comps = COMP_ORDER.filter(c => spans.some(s => s.comp === c))
    .concat([...new Set(spans.map(s => s.comp))].filter(c => !COMP_ORDER.includes(c)))
  const extentMin = spans.length ? Math.min(...spans.map(s => s.start)) : Date.now() - winSec * 1000
  const extentMax = spans.length ? Math.max(...spans.map(s => s.end)) : Date.now()

  const now = Date.now()
  let winEnd, winStart
  if (mode === 'live') { winEnd = now; winStart = now - winSec * 1000 }
  else { winStart = extentMin; winEnd = Math.max(extentMax, extentMin + 1000) }
  const winLen = Math.max(1000, winEnd - winStart)
  const headTime = mode === 'live' ? now : winStart + playhead * winLen

  React.useEffect(() => {
    if (mode !== 'replay' || !playing) return
    let raf; rafPrev.current = performance.now()
    const step = (ts) => {
      const dt = ts - rafPrev.current; rafPrev.current = ts
      setPlayhead(p => { const np = p + (dt * speed) / winLen; if (np >= 1) { setPlaying(false); return 1 } return np })
      raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [mode, playing, speed, winLen])

  // Most perf events are near-instantaneous points (ms=1), which would flash
  // for a single frame and be impossible to eye-track. GLOW keeps a node lit for
  // a visible dwell as the playhead sweeps past its event, so traffic reads as
  // components blinking on/off. Long spans (Ava speaking) still light for their
  // full real duration.
  const GLOW = 500
  const activeSet = React.useMemo(() => {
    if (mode === 'live') return new Set(Object.entries(activeComp || {}).filter(([, v]) => v > now).map(([k]) => k))
    return new Set(spans.filter(s => s.start - GLOW <= headTime && s.end + GLOW >= headTime).map(s => s.comp))
  }, [mode, activeComp, headTime, spans.length]) // eslint-disable-line

  const stats = comps.map(c => {
    const sp = spans.filter(s => s.comp === c)
    const tt = sp.map(s => s.ttft).filter(v => v != null)
    const tot = sp.map(s => s.ms)
    return { c, n: sp.length, ttft: tt.length ? Math.round(pctl(tt, 50)) : null, ttftMax: tt.length ? Math.round(Math.max(...tt)) : null, p50: Math.round(pctl(tot, 50)), p95: Math.round(pctl(tot, 95)) }
  }).sort((a, b) => (b.ttft ?? b.p95) - (a.ttft ?? a.p95))
  const worst = stats[0]

  const setHeadFromClick = e => {
    if (mode !== 'replay') return
    const r = e.currentTarget.getBoundingClientRect()
    setPlayhead(Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)))
  }

  return (
    <div className="mx-auto max-w-[1160px] px-4 py-6">
      <div className="mb-1 flex flex-wrap items-center gap-3">
        <div>
          <div className="text-[10px] font-bold uppercase tracking-wider text-gray-400">System telemetry</div>
          <h1 className="text-[24px] font-extrabold tracking-tight text-[#00254A]">Live Ops — who's doing what, when</h1>
          {runtime?.ok && (
            <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[10.5px]">
              <span className="rounded-full bg-sky-50 px-2 py-0.5 font-bold text-sky-700 ring-1 ring-sky-100">
                live model {String(runtime.models?.live || '').replace('-preview', '')}
              </span>
              <span className="rounded-full bg-sky-50 px-2 py-0.5 font-bold text-sky-700 ring-1 ring-sky-100">
                agents on {runtime.agents?.adk_available ? `ADK ${runtime.agents.adk_version}` : 'direct SDK'}
              </span>
              <span className="rounded-full bg-emerald-50 px-2 py-0.5 font-bold text-emerald-700 ring-1 ring-emerald-100">
                barge-in {runtime.live?.barge_in ? 'on' : 'off'} · {runtime.live?.vad}
              </span>
              <span className="rounded-full bg-gray-100 px-2 py-0.5 font-semibold text-gray-500">
                {runtime.project} · {runtime.region}
              </span>
            </div>
          )}
        </div>
        <span className="flex-1" />
        <div className="flex rounded-full border border-gray-200 bg-white p-0.5">
          {['live', 'replay'].map(m => (
            <button key={m} onClick={() => { setMode(m); if (m === 'replay') { setPlaying(false); setPlayhead(1) } }}
              className={`flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-[11.5px] font-bold transition ${mode === m ? 'bg-sky-600 text-white' : 'text-gray-500 hover:text-[#00254A]'}`}>
              {m === 'live' ? <Radio size={12} /> : <Rewind size={12} />}{m === 'live' ? 'Live' : 'Replay'}
            </button>
          ))}
        </div>
        {mode === 'replay' && rooms.length > 0 && (
          <select value={picked} onChange={e => { setPicked(e.target.value); if (e.target.value) { loadPerf(e.target.value); setPlayhead(1) } }}
            className="cursor-pointer rounded-full border border-gray-200 bg-white px-3.5 py-1.5 text-[11.5px] font-semibold text-[#00254A] outline-none hover:border-sky-500">
            <option value="">Load a meeting…</option>
            {rooms.map(r => <option key={r.room_id} value={r.room_id}>{r.room_id} · {r.spans} spans</option>)}
          </select>
        )}
      </div>
      <p className="mb-5 text-[13px] text-gray-500">End-to-end analysis: the diagram lights in sync with the timeline, the waterfall breaks each reply into its latency segments, and the glossary explains every component. <b>Live</b> follows now; <b>Replay</b> scrubs a past meeting.</p>

      <div className="mb-4 rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
        <div className="mb-3 flex items-center gap-2">
          <div>
            <div className="text-[10px] font-bold uppercase tracking-wider text-gray-400">Data flow</div>
            <h2 className="flex items-center gap-2 text-[13px] font-bold text-[#00254A]"><Activity size={14} className="text-sky-600" /> Execution flow</h2>
          </div>
          <span className="flex-1" />
          <span className="rounded-full bg-sky-50 px-2.5 py-0.5 text-[10.5px] font-bold text-[#00254A]">{mode === 'live' ? 'now' : `t = ${((headTime - winStart) / 1000).toFixed(1)}s`}</span>
        </div>
        <FlowGraph activeSet={activeSet} />
      </div>

      <LatencyWaterfall spans={spans} />

      <div className="mb-4 rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <div>
            <div className="text-[10px] font-bold uppercase tracking-wider text-gray-400">Component spans</div>
            <h2 className="flex items-center gap-2 text-[13px] font-bold text-[#00254A]"><Gauge size={14} className="text-sky-600" /> Timeline</h2>
          </div>
          {worst && <span className="rounded-full bg-red-50 px-2.5 py-0.5 text-[10.5px] font-bold text-red-600 ring-1 ring-red-200">slowest {label(worst.c)} · {worst.ttft != null ? 'TTFT ' + fmt(worst.ttft) : fmt(worst.p95) + ' p95'}</span>}
          <span className="flex-1" />
          {mode === 'live' && (
            <div className="flex items-center gap-1 text-[10.5px] text-gray-500">window
              {[30, 60, 120].map(w => <button key={w} onClick={() => setWinSec(w)} className={`rounded px-1.5 py-0.5 font-bold ${winSec === w ? 'bg-sky-600 text-white' : 'bg-gray-100 text-[#00254A]'}`}>{w}s</button>)}
            </div>
          )}
          {mode === 'replay' && (
            <div className="flex items-center gap-2">
              <button onClick={() => { if (playhead >= 1) setPlayhead(0); setPlaying(p => !p) }} className="rounded-lg bg-sky-600 p-1.5 text-white hover:bg-sky-700">{playing ? <Pause size={13} /> : <Play size={13} />}</button>
              <button onClick={() => { setPlayhead(0); setPlaying(false) }} className="rounded-lg bg-gray-100 p-1.5 text-[#00254A] hover:bg-gray-200"><Rewind size={13} /></button>
              {[1, 2, 4].map(s => <button key={s} onClick={() => setSpeed(s)} className={`rounded px-1.5 py-0.5 text-[10.5px] font-bold ${speed === s ? 'bg-sky-600 text-white' : 'bg-gray-100 text-[#00254A]'}`}>{s}×</button>)}
            </div>
          )}
        </div>
        {spans.length === 0 ? (
          <p className="py-6 text-center text-[12.5px] italic text-gray-400">No spans yet — start a meeting (Live), or load one (Replay).</p>
        ) : (
          <>
            <div className="relative rounded-lg border border-gray-200 bg-gray-50 p-2.5" onClick={setHeadFromClick} style={{ cursor: mode === 'replay' ? 'pointer' : 'default' }}>
              {comps.map(c => (
                <div key={c} className="flex items-center gap-2 py-[3px]">
                  <span className="w-[96px] flex-none truncate text-[10px] font-bold" style={{ color: ccolor(c) }} title={(COMP_WHAT[c] && COMP_WHAT[c].one) || c}>{label(c)}</span>
                  <div className="relative h-[15px] flex-1 overflow-hidden rounded bg-gray-100">
                    {spans.filter(s => s.comp === c && s.end >= winStart && s.start <= winEnd).map((s, i) => {
                      const left = Math.max(0, ((s.start - winStart) / winLen) * 100)
                      const w = Math.max(0.7, Math.min(100 - left, (s.ms / winLen) * 100))
                      const cover = s.start <= headTime && s.end >= headTime
                      const ttL = s.ttft != null ? Math.max(0, ((s.start + s.ttft - winStart) / winLen) * 100) : null
                      return <React.Fragment key={i}>
                        <div title={`${label(c)} · ${fmt(s.ms)}${s.ttft != null ? ' · TTFT ' + fmt(s.ttft) : ''} — click for input/output`} className="absolute top-[2px] h-[11px] cursor-pointer rounded-sm hover:brightness-110"
                          onClick={(ev) => { ev.stopPropagation(); setSelected({ comp: c, s }) }}
                          style={{ left: `${left}%`, width: `${w}%`, background: ccolor(c), opacity: s.phase === 'err' ? 0.35 : (cover ? 1 : 0.7), outline: (selected && selected.s === s) ? `2px solid ${NAVY}` : (cover ? `1px solid ${NAVY}` : 'none') }} />
                        {ttL != null && ttL <= 100 && <div className="absolute top-0 h-[15px] w-[2px] bg-black/55" style={{ left: `${ttL}%` }} />}
                      </React.Fragment>
                    })}
                  </div>
                </div>
              ))}
              <div className="pointer-events-none absolute bottom-1 top-1 w-[2px] bg-sky-500" style={{ left: `calc(96px + 8px + (100% - 112px) * ${Math.max(0, Math.min(1, (headTime - winStart) / winLen))})` }} />
            </div>
            {mode === 'replay' && (
              <input type="range" min="0" max="1000" value={Math.round(playhead * 1000)} onChange={e => { setPlaying(false); setPlayhead(Number(e.target.value) / 1000) }} className="mt-2 w-full accent-sky-600" />
            )}
            <div className="mt-1 flex justify-between text-[9px] text-gray-400"><span>{mode === 'live' ? `−${winSec}s` : '0s'}</span><span>{mode === 'live' ? 'now' : `${(winLen / 1000).toFixed(1)}s`}</span></div>
          </>
        )}
      </div>

      {selected && (
        <div className="mb-4 rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <div className="mb-2 flex items-center justify-between">
            <div>
              <div className="text-[10px] font-bold uppercase tracking-wider text-gray-400">Span inspector · traceability</div>
              <h2 className="flex items-center gap-2 text-[13px] font-bold text-[#00254A]">
                <span className="h-2.5 w-2.5 rounded-full" style={{ background: ccolor(selected.comp) }} />
                {label(selected.comp)}
                <span className="rounded-full bg-sky-50 px-2 py-0.5 text-[10px] font-bold text-[#00254A]">{fmt(selected.s.ms)}{selected.s.ttft != null ? ` · TTFT ${fmt(selected.s.ttft)}` : ''}</span>
                {selected.s.phase === 'err' && <span className="rounded-full bg-red-50 px-2 py-0.5 text-[10px] font-bold text-red-600 ring-1 ring-red-200">error</span>}
                {matchedLog?.model && <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-mono text-gray-600">{matchedLog.model}</span>}
              </h2>
            </div>
            <button onClick={() => setSelected(null)} className="text-gray-400 hover:text-gray-600 cursor-pointer text-lg leading-none">×</button>
          </div>
          {matchedLog ? (
            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <div className="mb-1 text-[10px] font-bold uppercase tracking-wider text-gray-400">Input delivered to the agent</div>
                <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-lg border border-gray-200 bg-gray-50 p-2.5 text-[11px] leading-snug text-gray-700">{matchedLog.input || '—'}</pre>
              </div>
              <div>
                <div className="mb-1 text-[10px] font-bold uppercase tracking-wider text-gray-400">Output produced</div>
                <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-lg border border-gray-200 bg-gray-50 p-2.5 text-[11px] leading-snug text-gray-700">{matchedLog.output || '—'}</pre>
              </div>
              <div className="md:col-span-2 flex flex-wrap gap-x-5 gap-y-1 text-[10.5px] text-gray-500">
                <span>phase <b className="text-[#00254A]">{matchedLog.phase}</b></span>
                <span>total <b className="text-[#00254A]">{fmt(selected.s.ms)}</b></span>
                {selected.s.ttft != null && <span>TTFT <b className="text-[#00254A]">{fmt(selected.s.ttft)}</b></span>}
                {matchedLog.meta?.ttft_ms != null && <span>logged TTFT <b className="text-[#00254A]">{fmt(matchedLog.meta.ttft_ms)}</b></span>}
                <span>at <b className="text-[#00254A]">{new Date(selected.s.t * 1000).toLocaleTimeString()}</b></span>
              </div>
            </div>
          ) : (
            <div className="rounded-lg border border-dashed border-gray-200 bg-gray-50 py-6 text-center text-[12px] italic text-gray-400">
              No captured input/output for this span{loadedRoom ? '' : ' — load a meeting first'}. Live avatar turns and text-agent calls are logged; older spans may predate logging.
            </div>
          )}
        </div>
      )}

      <Glossary comps={comps} />

      {spans.length > 0 && (
        <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <div className="mb-1 text-[10px] font-bold uppercase tracking-wider text-gray-400">Latency by component</div>
          <h2 className="mb-3 flex items-center gap-2 text-[13px] font-bold text-[#00254A]"><Gauge size={14} className="text-sky-600" /> TTFT = realtime latency</h2>
          <div className="grid grid-cols-[1fr_repeat(5,60px)] items-center gap-x-2">
            <div className="text-[10px] font-bold uppercase tracking-[.08em] text-gray-400">component</div>
            {['runs', 'TTFT p50', 'TTFT max', 'tot p50', 'tot p95'].map(h => <div key={h} className="text-right text-[9px] font-bold uppercase tracking-[.05em] text-gray-400">{h}</div>)}
            {stats.map(s => (
              <React.Fragment key={s.c}>
                <div className="flex items-center gap-1.5 border-t border-gray-200 py-1.5 text-[12px] font-semibold text-gray-700"><span className="h-2 w-2 rounded-full" style={{ background: ccolor(s.c) }} />{label(s.c)}</div>
                <div className="border-t border-gray-200 py-1.5 text-right text-[11.5px] text-gray-500">{s.n}</div>
                <div className={`border-t border-gray-200 py-1.5 text-right text-[11.5px] font-bold ${s.ttft == null ? 'text-gray-300' : s.ttft > 2500 ? 'text-red-600' : s.ttft > 1200 ? 'text-amber-600' : 'text-emerald-600'}`}>{fmt(s.ttft)}</div>
                <div className="border-t border-gray-200 py-1.5 text-right text-[11.5px] text-gray-500">{fmt(s.ttftMax)}</div>
                <div className="border-t border-gray-200 py-1.5 text-right text-[11.5px] text-gray-700">{fmt(s.p50)}</div>
                <div className={`border-t border-gray-200 py-1.5 text-right text-[11.5px] ${s.p95 > 6000 ? 'font-bold text-red-600' : 'text-gray-500'}`}>{fmt(s.p95)}</div>
              </React.Fragment>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
