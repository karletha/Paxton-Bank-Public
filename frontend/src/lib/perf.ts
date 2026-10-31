// Shared performance/telemetry helpers for the Telemetry view. Component names
// MUST match the perf spans the bridge emits (room.perf / workers._gen labels).

export const COMP_COLOR: Record<string, string> = {
  AvaLive: '#0284C7', ClientVoice: '#B4551F', ClipFarm: '#B4551F',
  'ZoomEars (RTMS)': '#8B5CF6', ZoomEars: '#8B5CF6', RecallEars: '#8B5CF6',
  Scripter: '#0EA5A0', OptionSmith: '#D97706', AvaPlanner: '#0284C7',
  Scorer: '#2563EB', Pulse: '#DB2777', Panel: '#7C3AED', Archivist: '#0891B2',
  Coach: '#DC2626', Curator: '#059669', Scribe: '#7C3AED', Sentinel: '#DC2626',
};
export const ccolor = (c: string): string => COMP_COLOR[c] || '#64748B';

// Friendly, plain-English display names. Internal span/feed/config keys stay as
// they are (the gate + telemetry match on them); the UI shows these instead.
export const LABEL: Record<string, string> = {
  // agents carry "Agent" in the name so it's obvious what is an agent vs. a
  // data pipe (Meeting Listener) or a UI surface. Internal keys are unchanged.
  'ZoomEars (RTMS)': 'Meeting Listener', ZoomEars: 'Meeting Listener', RecallEars: 'Meeting Listener',
  AvaLive: 'Bridget (live avatar agent)',
  Pulse: 'Sentiment Agent', Panel: 'Insights Agent', Archivist: 'Memory Agent', Sentinel: 'Compliance Agent',
  Scripter: 'Talking Points Agent', OptionSmith: 'Reply Options Agent', AvaPlanner: 'Advisor Suggestions Agent',
  Scorer: 'Lead Score Agent', Coach: 'Objection Coach Agent',
  ClipFarm: 'Client Clips (retired)', ClientVoice: 'Client Clips (retired)',
};
export const label = (c: string): string => LABEL[c] || c;

export const pctl = (arr: number[], p: number): number => {
  if (!arr.length) return 0;
  const s = [...arr].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.floor((p / 100) * s.length))];
};

// The fleet was trimmed for this build: only these agents actually run
// (Sentinel is always-on guardrails). The rest stay visible as "parked" so the
// flow graph is honest about what exists vs what is switched off.
export const ACTIVE_AGENTS = ['Pulse', 'Panel', 'Archivist', 'Sentinel'];
export const PARKED_AGENTS = ['Scripter', 'OptionSmith', 'AvaPlanner', 'Scorer', 'Coach'];

// Display order for the Gantt timeline / latency table.
export const COMP_ORDER = [
  'ZoomEars (RTMS)',
  ...ACTIVE_AGENTS,
  'AvaLive',
  ...PARKED_AGENTS,
];

export interface CompInfo { role: 'EARS' | 'BRAIN' | 'MOUTH'; one: string; lat: string; parked?: boolean }

// plain-language: what each component IS, and how it affects latency.
export const COMP_WHAT: Record<string, CompInfo> = {
  'ZoomEars (RTMS)': { role: 'EARS', one: 'Zoom streams the live meeting audio out to us; a "heard" mark is logged the instant a turn is transcribed.', lat: 'Marks when you finished speaking — the start of the response clock.' },
  AvaLive:    { role: 'MOUTH', one: "Bridget: hears the meeting, decides on her own when to talk, and speaks into Zoom.", lat: 'TTFT = time from her turn starting to her FIRST spoken word — her realtime latency.' },
  ClipFarm:   { role: 'MOUTH', one: 'RETIRED — the client is a live human now; no AI client clips are rendered.', lat: 'Retired.', parked: true },
  ClientVoice: { role: 'MOUTH', one: 'RETIRED — the client is a live human now.', lat: 'Retired.', parked: true },
  Pulse:      { role: 'BRAIN', one: 'Client sentiment read (tone + words).', lat: 'Background.' },
  Panel:      { role: 'BRAIN', one: 'Mines insight cards from the conversation.', lat: 'Background.' },
  Archivist:  { role: 'BRAIN', one: 'Maintains whole-meeting memory that every other agent reads.', lat: 'Background.' },
  Sentinel:   { role: 'BRAIN', one: 'Always-on risk / compliance tripwire (guardrails on every exchange).', lat: 'Background; runs on every turn.' },
  Scripter:   { role: 'BRAIN', one: 'The banker’s teleprompter — journey-tagged moves to read aloud.', lat: 'Parked in this build.', parked: true },
  OptionSmith: { role: 'BRAIN', one: 'Generates client reply options the banker can pick from.', lat: 'Parked in this build.', parked: true },
  AvaPlanner: { role: 'BRAIN', one: 'Suggests the advisor’s next possible lines (the banker can pick one).', lat: 'Parked in this build.', parked: true },
  Scorer:     { role: 'BRAIN', one: 'Five-dimension deal qualification score from what’s been said.', lat: 'Parked in this build.', parked: true },
  Coach:      { role: 'BRAIN', one: 'Objection reframes for the banker.', lat: 'Parked in this build.', parked: true },
};
