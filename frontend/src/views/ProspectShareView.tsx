// PROSPECT-FACING shared capability canvas — opened by the banker in a second
// tab (?share=<room>) and screen-shared in Zoom. Strictly client-safe: the
// /rooms/{room}/share endpoint already filters to product-related insight
// cards and grounded excerpts; nothing internal (sentiment, compliance,
// telemetry) is ever fetched here. Polls every 3s — live during the call.
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Sparkles, Globe2, BookOpenCheck, Quote } from 'lucide-react';

const origin = () => (import.meta as any).env?.VITE_BACKEND_URL || window.location.origin;

type Share = {
  ok: boolean; room: string; banker_name: string;
  presenting?: { id: string; title: string; img: string; source_title: string; source_url: string; kind: string };
  insights: { title: string; detail: string; recommended_action?: string; source_quote?: string;
              commercial_impact?: string; grounding_url?: string; grounding_title?: string; t?: number }[];
  grounding: { source_id: string; title: string; url: string; snippet: string }[];
  stats: { tiles: { group: string; label: string; value: string; source_id: string }[];
           regions: { region: string; countries: number; highlights: string[]; source_id: string }[] };
  sources: { id: string; title: string; url: string }[];
};

// Static sections collapse by default (2026-08-11): the banker screen-shares
// without scrolling, so live content must own the viewport — reference figures
// are one click away.
const Collapsible: React.FC<{ title: React.ReactNode; highlight?: boolean; children: React.ReactNode }> =
  ({ title, highlight, children }) => {
    const [open, setOpen] = useState(false);
    return (
      <section className={`flex flex-col gap-3 rounded-xl transition-shadow ${highlight ? 'ring-2 ring-sky-300 ring-offset-4 ring-offset-[#F7F9FB]' : ''}`}>
        <button onClick={() => setOpen(o => !o)}
          className="flex items-center justify-between w-full text-left cursor-pointer group">
          <h2 className="text-[11px] font-extrabold uppercase tracking-widest text-gray-400 group-hover:text-gray-600 flex items-center gap-1.5">
            {title}
          </h2>
          <span className="text-[10px] font-bold text-sky-600 group-hover:text-sky-700">
            {open ? 'collapse ▴' : 'expand ▾'}
          </span>
        </button>
        {open && children}
      </section>
    );
  };

const GROUP_LABEL: Record<string, string> = {
  network: 'Global network', liquidity: 'Liquidity', payments: 'Payments',
  trade: 'Trade', ecommerce: 'Digital & e-commerce', 'commercial-bank': 'Commercial Bank',
};

export const ProspectShareView: React.FC<{ room: string }> = ({ room }) => {
  const [data, setData] = useState<Share | null>(null);
  const [hoverRegion, setHoverRegion] = useState<string | null>(null);
  const seenGrounding = useRef(0);
  const [freshGrounding, setFreshGrounding] = useState(false);

  useEffect(() => {
    let alive = true;
    const pull = async () => {
      try {
        const d = await (await fetch(`${origin()}/rooms/${encodeURIComponent(room)}/share`)).json();
        if (!alive || !d?.ok) return;
        if (d.grounding?.length > seenGrounding.current) {
          seenGrounding.current = d.grounding.length;
          setFreshGrounding(true);
          setTimeout(() => alive && setFreshGrounding(false), 6000);
        }
        setData(d);
      } catch { /* keep last good frame */ }
    };
    pull();
    // Adaptive cadence: 1s while a visual is on stage (page flips and stage
    // changes must feel immediate over a Zoom screen-share), 3s otherwise.
    let t: any;
    const loop = () => { t = setTimeout(async () => { await pull(); loop(); },
      (dataRef.current?.presenting?.img) ? 1000 : 3000); };
    loop();
    return () => { alive = false; clearTimeout(t); };
  }, [room]);
  const dataRef = useRef<any>(null);
  useEffect(() => { dataRef.current = data; }, [data]);
  // preload the stage image before first paint — no white flash on screen-share
  useEffect(() => {
    const src = data?.presenting?.img;
    if (src) { const i = new Image(); i.src = src; }
  }, [data?.presenting?.img]);

  const srcIndex = useMemo(() => {
    const m: Record<string, number> = {};
    (data?.sources || []).forEach((s, i) => { m[s.id] = i + 1; });
    return m;
  }, [data?.sources]);

  const regions = data?.stats?.regions || [];
  const maxCountries = Math.max(1, ...regions.map(r => r.countries));
  const regionGrounded = (data?.grounding || []).some(g => g.source_id === 'global-presence');

  return (
    <div className="min-h-screen bg-[#F7F9FB] text-[#1F2933] antialiased">
      {/* header */}
      <header className="bg-[#00254A] text-white px-8 py-4 flex items-center justify-between sticky top-0 z-10 shadow">
        <div className="flex items-center gap-3">
          <span className="text-xl font-black tracking-wider">PAXTON BANK</span>
          <span className="bg-sky-400/20 text-sky-300 font-mono text-xs px-2 py-0.5 rounded border border-sky-400/30 uppercase tracking-widest font-bold">BRIDGET</span>
          <span className="hidden sm:inline text-xs text-sky-200/70 border-l border-white/20 pl-3">Live capability briefing</span>
        </div>
        <div className="flex items-center gap-2 bg-black/30 px-3 py-1.5 rounded-full border border-white/10 text-xs">
          <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
          <span className="font-semibold text-white/90">Updating live</span>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-6 py-8 flex flex-col gap-8">
        <div>
          <h1 className="text-xl font-bold text-[#00254A]">Prepared live during your conversation with Paxton Bank</h1>
          <p className="text-sm text-gray-500 mt-1">
            This page reflects what we're hearing and the published Paxton Bank capabilities it maps to. It updates as the meeting progresses.
          </p>
        </div>

        {/* NOW PRESENTING — appears only while the banker/Bridget has a visual
            on stage; everything below is untouched (Paxton Bank liked the canvas). */}
        {data?.presenting?.img && (
          <section className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <h2 className="text-[11px] font-extrabold uppercase tracking-widest text-sky-700 flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-sky-500 animate-pulse" /> Now presenting
              </h2>
              <span className="text-[10.5px] text-gray-400">{data.presenting.kind === 'upload' ? data.presenting.source_title : 'Paxton Commercial Bank'}</span>
            </div>
            <div className="bg-white rounded-2xl border-2 border-sky-200 shadow-lg overflow-hidden">
              <img src={data.presenting.img} alt={data.presenting.title}
                className="w-full aspect-video object-contain bg-white" />
            </div>
            {data.presenting.source_url && (
              <div className="text-[10.5px] text-gray-400 px-1">
                Source: <a className="text-sky-700 hover:underline" href={data.presenting.source_url}
                  target="_blank" rel="noopener noreferrer">{data.presenting.source_title}</a>
                <span className="ml-1.5 text-gray-300">{data.presenting.source_url}</span>
              </div>
            )}
          </section>
        )}

        {/* A — product-related insights */}
        <section className="flex flex-col gap-3">
          <h2 className="text-[11px] font-extrabold uppercase tracking-widest text-gray-400 flex items-center gap-1.5">
            <Sparkles size={13} className="text-sky-600" /> Your priorities, mapped to capabilities
          </h2>
          {!data?.insights?.length ? (
            <div className="rounded-xl border border-dashed border-gray-200 bg-white py-10 text-center text-sm italic text-gray-400">
              This canvas fills in as your conversation progresses.
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {data.insights.slice().reverse().map((c, i) => (
                <div key={i} className="bg-white rounded-xl border border-gray-200 shadow-sm p-4 flex flex-col gap-2">
                  <div className="font-bold text-[#00254A] text-sm">{c.title}</div>
                  {c.source_quote && (
                    <div className="text-xs text-gray-500 italic flex gap-1.5">
                      <Quote size={12} className="shrink-0 mt-0.5 text-gray-300" />"{c.source_quote}"
                    </div>
                  )}
                  <div className="text-xs text-gray-600 leading-relaxed">
                    <b className="text-gray-700">Capability summary:</b> {c.detail}
                  </div>
                  {c.commercial_impact && (
                    <div className="text-xs text-gray-600 leading-relaxed">
                      <b className="text-gray-700">Commercial impact:</b> {c.commercial_impact}
                    </div>
                  )}
                  {c.recommended_action && (
                    <div className="text-xs text-sky-800 bg-sky-50 border border-sky-100 rounded-lg px-3 py-2 leading-relaxed">
                      {c.recommended_action}
                    </div>
                  )}
                  {c.grounding_url && (
                    <div className="text-[10px] text-gray-400 border-t border-gray-100 pt-2 mt-0.5">
                      Verified grounding: <a className="text-sky-700 hover:underline break-all"
                        href={c.grounding_url} target="_blank" rel="noopener noreferrer">{c.grounding_url}</a>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>

        {/* B — grounded excerpts */}
        {!!data?.grounding?.length && (
          <section className={`flex flex-col gap-3 rounded-xl transition-shadow ${freshGrounding ? 'ring-2 ring-sky-300 ring-offset-4 ring-offset-[#F7F9FB]' : ''}`}>
            <h2 className="text-[11px] font-extrabold uppercase tracking-widest text-gray-400 flex items-center gap-1.5">
              <BookOpenCheck size={13} className="text-sky-600" /> From Paxton Bank's published materials — referenced in this conversation
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {data.grounding.slice(-4).reverse().map((g, i) => (
                <div key={i} className="bg-white rounded-xl border border-sky-100 p-4">
                  <a href={g.url} target="_blank" rel="noopener noreferrer"
                    className="text-xs font-bold text-sky-700 hover:underline">
                    {g.title}<sup className="ml-0.5 text-gray-400">{srcIndex[g.source_id] || ''}</sup>
                  </a>
                  <p className="text-xs text-gray-600 leading-relaxed mt-1.5 line-clamp-6">{g.snippet}</p>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* C — verified figures (collapsed by default) */}
        {!!data?.stats?.tiles?.length && (
          <Collapsible title={<>Paxton Bank at a glance — figures from published sources</>}>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {data.stats.tiles.map((t, i) => (
                <div key={i} className="bg-white rounded-xl border border-gray-200 shadow-sm px-4 py-3.5 flex flex-col gap-0.5">
                  <div className="text-[22px] leading-7 font-extrabold text-[#00254A] tabular-nums">
                    {t.value}<sup className="ml-0.5 text-[10px] font-semibold text-gray-400">{srcIndex[t.source_id] || ''}</sup>
                  </div>
                  <div className="text-[11px] text-gray-500 leading-snug">{t.label}</div>
                  <div className="text-[9.5px] font-bold uppercase tracking-wider text-sky-600/70 mt-1">{GROUP_LABEL[t.group] || t.group}</div>
                </div>
              ))}
            </div>
          </Collapsible>
        )}

        {/* D — global presence by region (single measure, single hue, direct labels) */}
        {!!regions.length && (
          <Collapsible highlight={regionGrounded && freshGrounding}
            title={<><Globe2 size={13} className="text-sky-600" /> Global presence — countries with Paxton Bank coverage, by region</>}>
            <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
              <div className="flex flex-col gap-2.5">
                {regions.map((r) => (
                  <div key={r.region} className="relative flex items-center gap-3 group cursor-default"
                    onMouseEnter={() => setHoverRegion(r.region)} onMouseLeave={() => setHoverRegion(null)}>
                    <div className="w-40 shrink-0 text-xs text-gray-600 text-right">{r.region}</div>
                    <div className="flex-1 h-5 relative">
                      <div data-bar className="absolute inset-y-1 left-0 rounded-r-[4px] bg-[#0284c7] transition-all"
                        style={{ width: `${(r.countries / maxCountries) * 100}%`, opacity: hoverRegion && hoverRegion !== r.region ? 0.45 : 1 }} />
                      <span className="absolute inset-y-0 flex items-center text-[11px] font-bold text-[#00254A] tabular-nums"
                        style={{ left: `calc(${(r.countries / maxCountries) * 100}% + 8px)` }}>
                        {r.countries}
                      </span>
                    </div>
                    {hoverRegion === r.region && !!r.highlights?.length && (
                      <div className="absolute left-44 -top-7 z-10 bg-[#00254A] text-white text-[10.5px] px-2.5 py-1 rounded-md shadow-lg whitespace-nowrap pointer-events-none">
                        {r.region}: {r.countries} countries — incl. {r.highlights.join(', ')}
                      </div>
                    )}
                  </div>
                ))}
              </div>
              <div className="text-[10px] text-gray-400 mt-4">
                Source: Paxton Bank Global Presence<sup>{srcIndex['global-presence'] || ''}</sup> — counting the markets the source enumerates per region.
              </div>
              <details className="mt-2">
                <summary className="text-[10.5px] text-gray-400 cursor-pointer hover:text-gray-600">View as table</summary>
                <table className="mt-2 text-xs text-gray-600">
                  <thead><tr className="text-left text-gray-400"><th className="pr-6 font-semibold">Region</th><th className="pr-6 font-semibold">Countries</th><th className="font-semibold">Notable markets</th></tr></thead>
                  <tbody>{regions.map(r => (
                    <tr key={r.region}><td className="pr-6 py-0.5">{r.region}</td><td className="pr-6 tabular-nums">{r.countries}</td><td>{r.highlights?.join(', ')}</td></tr>
                  ))}</tbody>
                </table>
              </details>
            </div>
          </Collapsible>
        )}

        {/* sources + disclaimer */}
        <footer className="border-t border-gray-200 pt-4 pb-10 flex flex-col gap-2">
          <div className="text-[10px] font-extrabold uppercase tracking-widest text-gray-400">Sources</div>
          <ol className="text-[10.5px] text-gray-500 flex flex-col gap-0.5 list-decimal list-inside">
            {(data?.sources || []).map(s => (
              <li key={s.id}><a className="hover:text-sky-700 hover:underline" href={s.url} target="_blank" rel="noopener noreferrer">{s.title}</a></li>
            ))}
          </ol>
          <p className="text-[10px] text-gray-400 mt-2 max-w-3xl">
            All figures are drawn from Paxton Bank's published materials listed above and describe capabilities, not commitments.
            Pricing, credit decisions, and product terms are always confirmed by your Paxton Bank coverage team.
          </p>
        </footer>
      </main>
    </div>
  );
};
