// GOLDEN TRANSCRIPTS — the acceptance guardrail for the nine journeys.
// Each journey: goal, the exact turn-by-turn exchange that PROVES it, and the
// system signals that must fire. These are the test oracle: a live meeting
// "meets" a journey when its transcript/events match this shape.

export interface GoldenTurn {
  who: string
  text: string
  note?: string
  flow?: string[]
}

export interface GoldenJourney {
  id: string
  title: string
  goal: string
  signals: string[]
  plumbing?: string[]
  transcript: GoldenTurn[]
}

export const GOLDEN: GoldenJourney[] = [
  {
    id: 'J1', title: 'Prospect Invitation & Consent',
    goal: 'No substance until the CLIENT has consented, on the record (banker never needs to consent — 2026-08-11).',
    signals: ['feed consent {prospect:true}', 'feed consent {banker:true, all:true}', 'audit ai_consent_captured'],
    plumbing: [
      'START — Zoom API → ONE self-hosted Zoom SDK bot seats the advisor (Bridget Advisor (agent) warm); the client is a live human participant',
      'EARS — Zoom RTMS (we dial out): diarized AUDIO → Ava natively (no STT) · native transcript → agents — human words refresh the panels',
      'AGENTS — Sentiment Agent ▸ sentiment · Insights Agent ▸ insights · Memory Agent ▸ memory · Compliance Agent ▸ guardrails',
    ],
    transcript: [
      { who: 'Client', text: 'Hi everyone — this is {contact} from {company}. Nice to meet you.', note: 'prospect initiates; diarized name reaches Bridget as [speaking: {contact}]',
        flow: ['Client (human) speaks in Zoom', 'Meeting Listener ▸ diarized name', '∥ Sentiment Agent ▸ first read'] },
      { who: 'Bridget', text: 'Nice to meet you, {contact} from {company} — I’m Bridget, the Paxton Bank AI advisor. Before we begin — may I have your permission to participate and take notes?',
        flow: ['Ava responds (Bridget avatar, seated by the advisor bot)', 'Stage → Zoom', '∥ Compliance Agent ▸ consent watch'] },
      { who: 'Client', text: 'That’s fine by me — go ahead.', note: 'verbal consent detected → consent card shows Prospect ✓',
        flow: ['Client (human) speaks in Zoom', 'Meeting Listener → Room', 'Consent check ✓ prospect', '∥ Sentiment Agent ▸ sentiment'] },
      { who: 'Bridget', text: 'Thank you — consent recorded. Alex, over to you.',
        flow: ['Ava speaks (Bridget Advisor (agent))', 'Audit → CRM trail'] },
    ],
  },
  {
    id: 'J2', title: 'Discovery, Qualification & Assessment',
    goal: 'The client’s answers build a five-dimension score with evidence.',
    signals: ['feed score {strategic_fit, revenue_potential, buying_intent, implementation_complexity, overall}', 'score rationale cites client quotes'],
    transcript: [
      { who: 'Client', text: 'We’re looking at expanding {company} into Mexico — curious what exactly Paxton Bank can offer us there.', note: 'prospect initiates discovery; Bridget responds while the banker stays present', flow: ['Client (human) speaks in Zoom', 'Meeting Listener → Room', '∥ Insights Agent · Sentiment Agent'] },
      { who: 'Bridget', text: 'That’s exciting — are you thinking a physical expansion or more online? And what would you most want from a banking partner there?', flow: ['Ava responds (one topic per turn)', '∥ Grounding ▸ verified Paxton Bank corpus on the corridor'] },
      { who: 'Client', text: 'Four regional banks, no consolidated view. Mexico supplier payments cost us 2.1% all-in and settle in three days.', note: 'Lead Score Agent (parked) updates: revenue_potential ↑, buying_intent ↑', flow: ['Client (human) speaks in Zoom', 'Meeting Listener → Room', '∥ Lead Score Agent (parked) · Sentiment Agent · Insights Agent (parallel Vertex agents)', 'Console panels update'] },
      { who: 'You (banker)', text: 'What volume are you moving through that corridor a month?', flow: ['You speak in Zoom', 'Meeting Listener → Room', '∥ Reply Options Agent (parked) · Talking Points Agent (parked) ▸ refresh'] },
      { who: 'Client', text: 'Roughly $4M to Mexico, growing — plus new APAC vendors next quarter.', note: 'score refresh — overall climbs with rationale', flow: ['Client (human) speaks in Zoom', 'Meeting Listener → Room', '∥ Lead Score Agent (parked) ▸ 5-dim update · Memory Agent ▸ memory'] },
    ],
  },
  {
    id: 'J3a', title: 'Multi-party — speaker attribution',
    goal: 'Every human is recognized and addressed by name.',
    signals: ['feed speaker {name}', 'transcript lines attributed per person'],
    transcript: [
      { who: 'Ana (COO, joins)', text: 'Sorry to jump in late — Ana here, I run operations.', flow: ['Meeting Listener ▸ diarized name', 'Room ▸ speaker{Ana}', 'Transcript attributes per person'] },
      { who: 'Bridget', text: 'Welcome, Ana. We were just discussing the Mexico corridor — your operational view would help.', note: 'feed speaker {name: Ana} — per-name attribution', flow: ['Meeting Listener ▸ diarized [speaking: Ana]', 'Bridget addresses Ana by name (autonomous)', 'Memory Agent ▸ notes Ana=COO'] },
    ],
  },
  {
    id: 'J3b', title: 'Multilingual', goal: 'Client converses in their language, live translated (next phase).',
    signals: ['(planned) translated captions'], transcript: [
      { who: '—', text: 'Designed; scheduled after English MVP.', note: 'marked “later” in System tab' }],
  },
  {
    id: 'J3c', title: 'Companion modality',
    goal: 'Banker picks how each AI shows up — video avatar or audio.',
    signals: ['companion_ready {mode}', 'simulator_ready {mode, avatar}'],
    transcript: [
      { who: 'You (banker)', text: '[Today: Companion (Ava) = Video, Client = Video]', flow: ['Today pickers', 'Bridget Advisor (agent) ▸ avatar session', 'Client (human) on their own camera in Zoom'] },
      { who: 'System', text: 'Bridget joins as the video avatar (the only AI). The banker and client are live humans on their own cameras.', note: 'Bridget renders as a live avatar; humans appear via their own Zoom video' },
    ],
  },
  {
    id: 'J4', title: 'Risk Escalation — guarantee/commitment',
    goal: 'The instant a guarantee is demanded, the AI stops mid-sentence, says the safe line, and hands the banker the floor — audited.',
    signals: ['feed risk {risk_type, banker_takeover:true} <1s', 'audit ai_risk_escalation', 'companion interrupted + flush_audio'],
    transcript: [
      { who: 'Client', text: 'Before we go further — I need you to guarantee us that rate and confirm the regulatory side in writing.', flow: ['You pick trap reply', 'Client (human) speaks in Zoom', 'Meeting Listener → Room', 'Compliance Agent ✓ regex <1s (deterministic on known text)'] },
      { who: 'Bridget', text: '— [stops mid-sentence] That’s a commitment I’m not able to make. Let me bring in your banker for anything binding.', note: 'Compliance Agent trip; red banner; floor → banker', flow: ['Compliance Agent ▸ interrupt Bridget Advisor (agent)', 'safe line (fixed verbatim)', 'Audit ▸ ai_risk_escalation'] },
      { who: 'You (banker)', text: 'Happy to take that one — here’s what I can commit to today…', note: 'audit trail shows risk_escalation with trigger phrase', flow: ['You speak', 'Meeting Listener → Room', '∥ Talking Points Agent (parked) ▸ recovery moves'] },
    ],
  },
  {
    id: 'J5', title: 'Hand-off to Sales',
    goal: 'Momentum becomes a booked specialist meeting with full context, before the call ends.',
    signals: ['feed handoff {specialist, slots, sent:{sent:true}}', 'package email in the email outbox (demo)', 'audit ai_handoff_sent'],
    transcript: [
      { who: 'Client', text: 'This is useful — it’s probably worth going deeper with your treasury specialist.', flow: ['Client (human) speaks in Zoom', 'Meeting Listener → Room', '∥ Advisor Suggestions Agent (parked) ▸ suggests handoff card'] },
      { who: 'You (banker)', text: 'Specialist SOEID is FG78901.', note: 'typed in chat or spoken', flow: ['You type/speak', 'Handoff ▸ directory + scheduler tools'] },
      { who: 'Bridget', text: 'Dana Ruiz covers your corridor — she has Tuesday 10:00 or Thursday 14:00 open. I’ll send the full meeting package now.', note: 'handoff card + email lands in the email outbox (demo)', flow: ['You pick → Bridget Advisor (agent) speaks', 'Handoff ▸ package → email outbox (demo)', 'Audit ▸ ai_handoff_sent'] },
    ],
  },
  {
    id: 'J6', title: 'Sentiment Agent Analysis',
    goal: 'The banker reads the client’s mood turn by turn at a glance.',
    signals: ['feed sentiment {score 0-100, driver} on every client turn'],
    transcript: [
      { who: 'Client', text: 'Honestly, the fees are killing us.', note: 'sentiment dips — driver: “fee frustration”', flow: ['Client (human) speaks in Zoom', 'Meeting Listener → Room', 'Sentiment Agent ▸ score 0-100 + driver'] },
      { who: 'Client', text: 'Now that — that would actually solve a real problem for us.', note: 'sentiment jumps — driver: “solution fit”', flow: ['Client (human) speaks in Zoom', 'Meeting Listener → Room', 'Sentiment Agent ▸ jump detected'] },
    ],
  },
  {
    id: 'J7', title: 'Materials with Banker Approval',
    goal: 'Nothing reaches the client until the banker clicks Approve.',
    signals: ['feed approval {status:pending}', 'no email before click', 'feed approval {status:approved} + email in the email outbox (demo)', 'audit ai_materials_sent'],
    transcript: [
      { who: 'Client', text: 'Could you send a couple of case studies on cross-border cash for firms like ours?', flow: ['Client (human) speaks in Zoom', 'Meeting Listener → Room', 'Materials ▸ content-hub search'] },
      { who: 'Bridget', text: 'Absolutely — I’ll prepare those and your banker will send them right over.', note: 'approval card appears: PENDING, nothing sent', flow: ['You pick → Bridget Advisor (agent) speaks', 'Materials ▸ package PENDING', 'Approval gate ▸ blocks send'] },
      { who: 'You (banker)', text: '[clicks Approve & send]', note: 'email lands in the email outbox (demo); audit ai_materials_sent', flow: ['You click Approve', 'Materials ▸ send → email outbox (demo)', 'Audit ▸ ai_materials_sent'] },
    ],
  },
  {
    id: 'J8', title: 'Objection Handling',
    goal: 'Every objection is detected and the banker is armed with a coached response instantly.',
    signals: ['feed objection {objection, suggested_response}', 'coach card in Insights Agent panel'],
    transcript: [
      { who: 'Client', text: 'Your pricing looks steep next to what we pay today — convince me.', flow: ['You pick (J8-tagged)', 'Client (human) speaks in Zoom', 'Meeting Listener → Room', 'Objection Coach Agent (parked) ✓ fires on tag (deterministic)'] },
      { who: 'System', text: 'Objection Coach Agent (parked) card: “Price objection — reframe on total landed cost: fees + FX spread + 3-day float. Ask what the delay costs them.”', note: 'banker delivers reframe in own words', flow: ['Objection Coach Agent (parked) ▸ reframe card', '∥ Talking Points Agent (parked) ▸ reframe move in your lane'] },
      { who: 'You (banker)', text: 'Fair challenge — let’s compare total landed cost, not the headline fee…', flow: ['You speak', 'Meeting Listener → Room', 'Memory Agent ▸ objection logged'] },
    ],
  },
  {
    id: 'J9', title: 'Real-Time Banker Intelligence',
    goal: 'A live cockpit — score, sentiment, insights, next-best-action — builds through the whole meeting.',
    signals: ['feed insight (title, quote, recommended_action)', 'agent_activity/agent_result lane', 'score.next_best_action'],
    transcript: [
      { who: 'Client', text: 'We’re opening a plant in Monterrey in Q4.', flow: ['Client (human) speaks in Zoom', 'Meeting Listener → Room', '∥ Insights Agent ▸ insight card · Memory Agent ▸ memory'] },
      { who: 'System', text: 'Insight: “Monterrey expansion → pre-fund MXN payroll account. Recommended: raise FX forward program.”', note: 'insight card with verbatim quote + one action', flow: ['Insights Agent ▸ quote + action', 'Trace span → Telemetry tab'] },
    ],
  },
]
