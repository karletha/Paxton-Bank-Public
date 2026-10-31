// @ts-nocheck
/**
 * Agents — the fleet transparency view.
 *
 * One card per agent, straight from GET /agents (the REAL constants on the
 * bridge: personas.COMPANION_SYS, workers.*_SYS, journeys.COACH_SYS, sentinel
 * rules). Each card shows the friendly name, role, active/parked state, model,
 * purpose, the journeys it serves, its ACTUAL system prompt (collapsible), and
 * its tools — so the prompts and tools can be read and flagged for changes.
 */
import React from 'react'
import { Bot, Shield, Cpu, Wrench, ChevronDown, Route, Terminal, Users } from 'lucide-react'
import { fetchAgents } from '../services/geminiLiveClient'
import { ccolor } from '../lib/perf'

const NAVY = '#00254A'
const SKY = '#0284C7'
const ALL_J = ['J1', 'J2', 'J3', 'J4', 'J5', 'J6', 'J7', 'J8', 'J9']
const ROLE_COLOR = { ADVISOR: SKY, AGENT: '#0891B2', EARS: '#8B5CF6' }

function RoleBadge({ role }) {
  return (
    <span className="rounded px-1.5 py-0.5 text-[8.5px] font-extrabold tracking-wide text-white"
      style={{ background: ROLE_COLOR[role] || '#64748B' }}>{role}</span>
  )
}

function AgentCard({ a }) {
  const [open, setOpen] = React.useState(false)
  const serves = new Set(a.journeys || [])
  const parked = !a.active
  return (
    <div className={`rounded-xl border border-gray-200 bg-white p-5 shadow-sm transition ${parked ? 'opacity-60' : ''}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="h-2.5 w-2.5 flex-none rounded-full" style={{ background: ccolor(a.key) }} />
        <h2 className="text-[15px] font-extrabold tracking-tight text-[#00254A]">{a.name}</h2>
        <RoleBadge role={a.role} />
        {a.active
          ? <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[9px] font-extrabold uppercase tracking-wider text-emerald-700 ring-1 ring-emerald-200">active</span>
          : <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[9px] font-extrabold uppercase tracking-wider text-gray-500 ring-1 ring-gray-200">parked</span>}
        <span className="flex-1" />
        <span className="inline-flex items-center gap-1 rounded-full bg-sky-50 px-2.5 py-0.5 text-[10.5px] font-mono font-semibold text-[#00254A]" title="model">
          <Cpu size={11} className="text-sky-600" /> {a.model}
        </span>
        <span className="font-mono text-[9.5px] text-gray-400">{a.key}</span>
      </div>

      <p className="mt-2 text-[12.5px] leading-snug text-gray-600">{a.purpose}</p>

      {/* journeys served */}
      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <span className="mr-0.5 inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-gray-400">
          <Route size={12} className="text-sky-600" /> Journeys
        </span>
        {a.key === 'AvaLive'
          ? <span className="rounded-md bg-sky-100 px-2 py-0.5 text-[10.5px] font-extrabold text-sky-700">J1–J9 (all)</span>
          : (a.journeys && a.journeys.length)
            ? ALL_J.filter(j => serves.has(j)).map(j => (
                <span key={j} className="rounded-md bg-sky-100 px-2 py-0.5 text-[10.5px] font-extrabold text-sky-700">{j}</span>
              ))
            : <span className="text-[11px] italic text-gray-400">cross-journey / internal — no single journey</span>}
      </div>

      {/* collapsible system prompt */}
      <button onClick={() => setOpen(o => !o)}
        className="mt-3 flex w-full items-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-left text-[11.5px] font-bold text-[#00254A] transition hover:border-sky-300">
        <Terminal size={13} className="text-sky-600" /> System prompt
        <span className="text-[10px] font-normal text-gray-400">({(a.system_prompt || '').length.toLocaleString()} chars)</span>
        <span className="flex-1" />
        <ChevronDown size={15} className={`text-gray-400 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <pre className="mt-2 max-h-[28rem] overflow-auto whitespace-pre-wrap rounded-lg border border-gray-200 bg-gray-50 p-3 font-mono text-[11px] leading-relaxed text-gray-700">{a.system_prompt || '(no prompt)'}</pre>
      )}

      {/* tools */}
      {a.tools && a.tools.length > 0 && (
        <div className="mt-3">
          <div className="mb-1.5 flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">
            <Wrench size={12} className="text-sky-600" /> Tools ({a.tools.length})
          </div>
          <div className="grid gap-1.5 sm:grid-cols-2">
            {a.tools.map(t => (
              <div key={t.name} className="rounded-lg border border-gray-200 bg-gray-50 p-2.5">
                <span className="font-mono text-[11px] font-bold text-[#00254A]">{t.name}</span>
                <span className="text-[11px] text-gray-600"> — {t.description}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

export default function Agents() {
  const [agents, setAgents] = React.useState([])
  const [loaded, setLoaded] = React.useState(false)

  React.useEffect(() => { fetchAgents().then(a => { setAgents(a || []); setLoaded(true) }) }, [])

  // active first (stable), parked dimmed below
  const ordered = [...agents].sort((a, b) => (b.active ? 1 : 0) - (a.active ? 1 : 0))
  const activeN = agents.filter(a => a.active).length

  return (
    <div className="mx-auto max-w-[1160px] px-4 py-6">
      <div className="text-[10px] font-bold uppercase tracking-wider text-gray-400">Fleet transparency</div>
      <h1 className="flex items-center gap-2 text-[24px] font-extrabold tracking-tight text-[#00254A]">
        <Users size={22} className="text-sky-600" /> Agents — purpose, prompt, tools &amp; journeys
      </h1>
      <p className="mb-4 mt-1 text-[13px] text-gray-500">
        Every agent in the fleet, sourced live from the bridge: its role, the model it runs on, the journeys it serves,
        its <b>actual system prompt</b>, and the tools it can call. <b>Review these prompts and tools; flag anything to change.</b>
      </p>

      <div className="mb-5 flex items-start gap-3 rounded-xl border border-sky-100 bg-sky-50/60 p-4">
        <Shield size={16} className="mt-0.5 flex-none text-sky-600" />
        <p className="text-[12.5px] leading-snug text-[#33506E]">
          {activeN} of {agents.length} agents are <b>active</b> in this build; the rest are <b>parked</b> (gated off in
          <code className="mx-1 rounded bg-white px-1 font-mono text-[11px]">config.ACTIVE_AGENTS</code>, shown dimmed below).
          The Bridget Advisor and the Compliance Agent are always-on core infrastructure. Prompts shown here are the single
          source of truth — nothing is paraphrased.
        </p>
      </div>

      {!loaded ? (
        <p className="rounded-xl border border-dashed border-gray-200 bg-gray-50 py-8 text-center text-[12.5px] italic text-gray-400">Loading the fleet…</p>
      ) : ordered.length === 0 ? (
        <p className="rounded-xl border border-dashed border-gray-200 bg-gray-50 py-8 text-center text-[12.5px] italic text-gray-400">No agents returned by /agents.</p>
      ) : (
        <div className="flex flex-col gap-4">
          {ordered.map(a => <AgentCard key={a.key} a={a} />)}
        </div>
      )}
    </div>
  )
}
