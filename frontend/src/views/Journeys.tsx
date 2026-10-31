// @ts-nocheck
import React, { useState } from 'react'
import { ClipboardCheck, MessageSquareText, Activity, TriangleAlert } from 'lucide-react'
import { GOLDEN } from '../lib/golden'
import { getCompany } from '../services/geminiLiveClient'

// Scripts align dynamically with the prospect company selected at meeting
// start — {company}/{contact} tokens interpolate per hero profile.
const CONTACTS: Record<string, string> = {
  'Meridian Components Inc.': 'Arthur Vance',
  'Bluewater Logistics Group': 'Marcus Vance',
  'Helioform Materials': 'Marcus Vance',
}
const COMPANIES = Object.keys(CONTACTS)
const fill = (t: string, company: string) =>
  (t || '').replaceAll('{company}', company).replaceAll('{contact}', CONTACTS[company] || 'the client')

const WHO_STYLE = {
  'Ava': 'bg-sky-100 text-sky-700',
  'Client': 'bg-orange-50 text-orange-700',
  'You (banker)': 'bg-emerald-50 text-emerald-700',
  'System': 'bg-gray-100 text-gray-600',
}
const whoStyle = w => WHO_STYLE[w] || (w.startsWith('You') ? WHO_STYLE['You (banker)'] : 'bg-gray-100 text-gray-600')

// Fleet status — running agents: Sentiment / Insights / Memory + Compliance Agent.
// Journeys leaning on parked agents carry an amber chip on their header.
const PARKED_CHIP = {
  J2: 'Lead Score / Reply Options / Talking Points parked',
  J4: 'Talking Points parked',
  J5: 'Advisor Suggestions parked',
  J8: 'Objection Coach parked',
  J3b: 'next phase',
}

function FlowSteps({ flow }) {
  // group consecutive ∥-items into one parallel block
  const groups = []
  for (const f of flow) {
    const par = f.startsWith('∥')
    const txt = par ? f.replace(/^∥\s*/, '') : f
    const last = groups[groups.length - 1]
    if (par && last && last.par) last.items.push(txt)
    else groups.push({ par, items: [txt] })
  }
  return (
    <div className="ml-[86px] mt-2 border-l-2 border-sky-100 pl-4">
      {groups.map((g, i) => (
        <div key={i} className="relative mb-1.5">
          <span className={`absolute -left-[23px] top-1.5 h-3 w-3 rounded-full border-2 ${g.par ? 'border-emerald-500 bg-emerald-100' : 'border-sky-500 bg-white'}`} />
          {g.par ? (
            <div>
              <div className="mb-1 text-[9.5px] font-extrabold uppercase tracking-[.1em] text-emerald-700">in parallel</div>
              <div className="flex flex-wrap gap-1.5">
                {g.items.map((it, k) => (
                  <span key={k} className="rounded-md bg-emerald-50 px-2 py-1 font-mono text-[10.5px] font-semibold text-emerald-800">{it}</span>
                ))}
              </div>
            </div>
          ) : (
            <span className="rounded-md bg-sky-50 px-2 py-1 font-mono text-[10.5px] font-semibold text-[#33506E]">{g.items[0]}</span>
          )}
        </div>
      ))}
    </div>
  )
}

export default function Journeys() {
  const [open, setOpen] = useState('J1')
  const [detailed, setDetailed] = useState(true)
  const [company, setCompany] = useState(getCompany())
  return (
    <div className="mx-auto max-w-[1080px] px-4 py-6">
      <div className="text-[10.5px] font-extrabold uppercase tracking-[.14em] text-sky-600">Acceptance oracle</div>
      <div className="flex items-center gap-4">
        <h1 className="text-[27px] font-extrabold tracking-tight text-[#00254A]">Journeys — the guardrail transcripts</h1>
        <select value={company} onChange={e => setCompany(e.target.value)}
          className="ml-auto rounded-full border border-gray-200 bg-white px-3 py-2 text-[12px] font-bold text-[#00254A] cursor-pointer">
          {COMPANIES.map(c => <option key={c}>{c}</option>)}
        </select>
        <button onClick={() => setDetailed(!detailed)}
          className={`flex items-center gap-2 rounded-full border px-4 py-2 text-[12px] font-bold transition ${detailed ? 'border-sky-600 bg-sky-600 text-white' : 'border-gray-200 bg-white text-[#00254A] hover:border-sky-500'}`}>
          {detailed ? '● Agent flow ON' : '○ Agent flow — who executes what'}
        </button>
      </div>
      <p className="mb-4 text-[13.5px] text-gray-500">For each of the nine journeys: the exact turn-by-turn exchange that proves it, and the system signals that must fire. A live meeting <b>meets</b> a journey when its transcript and events match this shape — this page is the acceptance test. The scripts follow the live flow: <b>the prospect initiates, Bridget responds, the banker stays present</b>; lines adapt to the selected prospect company.{detailed && <> <b>Agent flow</b> lists, under each turn, the exact execution steps top-to-bottom — green dots are agent groups running <b>in parallel</b>, blue dots run in sequence. Vet this against the Telemetry tab: same names, same order.</>}</p>

      <div className="mb-6 flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4">
        <TriangleAlert size={16} className="mt-0.5 flex-none text-amber-600" />
        <p className="text-[12.5px] leading-snug text-amber-900">
          <b>Current fleet is trimmed.</b> Active agents today: <b>Sentiment Agent</b>, <b>Insights Agent</b>, <b>Memory Agent</b>, plus the <b>Compliance Agent</b>. Parked: Lead Score Agent (J2 gauges), Talking Points Agent (teleprompter), Reply Options Agent, Advisor Suggestions Agent, Objection Coach Agent (J8). Journeys that depend on a parked agent carry an amber chip below — their transcripts remain the acceptance shape, but those steps will not fire live until the agent is re-enabled. J1, J6 and J9 run fully on the active fleet; J3, J4, J5 and J7 partially.
        </p>
      </div>

      {GOLDEN.map(j => (
        <div key={j.id} className="mb-3 overflow-hidden rounded-xl border border-gray-200 bg-white shadow-xs">
          <button onClick={() => setOpen(open === j.id ? null : j.id)}
            className="flex w-full items-center gap-3 px-5 py-3.5 text-left transition hover:bg-sky-50/40">
            <span className="rounded-md bg-sky-100 px-2 py-0.5 text-[11px] font-extrabold text-sky-700">{j.id}</span>
            <b className="text-[14px] text-[#00254A]">{j.title}</b>
            {PARKED_CHIP[j.id] && (
              <span className="flex items-center gap-1 whitespace-nowrap rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[9.5px] font-extrabold uppercase tracking-[.08em] text-amber-700">
                <TriangleAlert size={10} /> {PARKED_CHIP[j.id]}
              </span>
            )}
            <span className="hidden flex-1 truncate text-[12px] text-gray-500 md:block">{j.goal}</span>
            <span className="text-[11px] font-bold text-sky-600">{open === j.id ? 'collapse' : 'expand'}</span>
          </button>
          {open === j.id && (
            <div className="grid grid-cols-1 gap-4 border-t border-gray-200 p-5 lg:grid-cols-[1.4fr_1fr]">
              <div>
                <h4 className="mb-2 flex items-center gap-2 text-[10.5px] font-extrabold uppercase tracking-[.1em] text-gray-500"><MessageSquareText size={13} className="text-sky-600" /> Golden transcript — turn by turn</h4>
                {detailed && j.plumbing && (
                  <div className="mb-3 rounded-xl border border-sky-100 bg-sky-50/50 p-3">
                    {j.plumbing.map((p, i) => (
                      <div key={i} className="mb-1.5 flex items-start gap-2 text-[11.5px] leading-snug text-[#33506E]">
                        <span className="mt-1 h-1.5 w-1.5 flex-none rounded-full bg-sky-300" /> {p}
                      </div>
                    ))}
                  </div>
                )}
                {j.transcript.map((t, i) => (
                  <div key={i} className="mb-2.5">
                    <div className="flex items-start gap-2.5">
                      <span className={`mt-0.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-[10.5px] font-extrabold ${whoStyle(t.who)}`}>{t.who}</span>
                      <span className="text-[13px] leading-snug text-gray-800">{fill(t.text, company)}</span>
                    </div>
                    {t.note && <div className="ml-[86px] mt-1 flex items-center gap-1.5 text-[11.3px] italic text-gray-500"><Activity size={11} className="text-sky-600" /> {fill(t.note, company)}</div>}
                    {detailed && t.flow && <FlowSteps flow={t.flow} />}
                  </div>
                ))}
              </div>
              <div>
                <h4 className="mb-2 flex items-center gap-2 text-[10.5px] font-extrabold uppercase tracking-[.1em] text-gray-500"><ClipboardCheck size={13} className="text-sky-600" /> Signals that must fire</h4>
                <div className="rounded-xl bg-[#F4F6F9] p-3.5">
                  {j.signals.map((s, i) => (
                    <div key={i} className="mb-1.5 flex items-start gap-2 text-[12.2px] text-gray-800">
                      <span className="mt-1 h-1.5 w-1.5 flex-none rounded-full bg-sky-500" /> <code className="font-mono text-[11.5px]">{s}</code>
                    </div>
                  ))}
                </div>
                <p className="mt-2.5 text-[11.5px] italic text-gray-500">Goal: {j.goal}</p>
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
