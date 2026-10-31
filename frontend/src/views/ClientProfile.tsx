// CLIENT PROFILE — one tab with the prospect company's full context for the
// banker: dossier (who they are), live CRM record, news coverage, FX corridor
// data, KYC screen — every block labeled with its data source and linked, so
// the banker always knows where a fact came from. Company follows the meeting
// (selected in the start modal) and can be switched manually pre-meeting.
import React, { useEffect, useState } from 'react';
import { Building2, Newspaper, TrendingUp, ShieldCheck, Users, Landmark, Link2, History } from 'lucide-react';
import { fetchClientProfile, getCompany } from '../services/geminiLiveClient';

const COMPANIES = ['Meridian Components Inc.', 'Bluewater Logistics Group', 'Helioform Materials'];

const Section: React.FC<{ icon: React.ReactNode; title: string; source?: string; sourceHref?: string; children: React.ReactNode }> =
  ({ icon, title, source, sourceHref, children }) => (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5 flex flex-col gap-3">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h3 className="text-xs font-extrabold uppercase tracking-widest text-[#00254A] flex items-center gap-1.5">
          <span className="text-sky-600">{icon}</span> {title}
        </h3>
        {source && (
          <a href={sourceHref || '#'} target="_blank" rel="noopener noreferrer"
            className="text-[10px] font-semibold text-gray-400 hover:text-sky-600 flex items-center gap-1">
            <Link2 size={10} /> {source}
          </a>
        )}
      </div>
      {children}
    </div>
  );

export const ClientProfile: React.FC = () => {
  const [company, setCompany] = useState(getCompany());
  const [d, setD] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    fetchClientProfile(company).then(r => { if (alive) { setD(r); setLoading(false); } });
    return () => { alive = false; };
  }, [company]);

  const doss = d?.dossier || {};
  const tp = doss.treasury_profile || {};
  const crm = d?.crm || {};
  const news = d?.news?.articles || (Array.isArray(d?.news) ? d.news : []);
  const market = d?.market || {};
  const kyc = d?.kyc || {};
  const verdictStyle = kyc.verdict === 'clear' ? 'bg-emerald-50 text-emerald-700 ring-emerald-200'
    : kyc.verdict === 'review' ? 'bg-amber-50 text-amber-700 ring-amber-200'
    : kyc.verdict ? 'bg-red-50 text-red-700 ring-red-200' : 'bg-gray-100 text-gray-400 ring-gray-200';

  return (
    <main className="flex-1 p-6 max-w-[1160px] mx-auto w-full flex flex-col gap-5">
      {/* company header */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5 flex flex-wrap items-center justify-between gap-4">
        <div>
          <div className="text-[10px] font-extrabold uppercase tracking-widest text-gray-400 mb-1">Client profile — banker &amp; prospect context</div>
          <h2 className="text-xl font-bold text-[#00254A]">{doss.company || company}</h2>
          <div className="text-xs text-gray-500 mt-1 flex flex-wrap gap-x-4 gap-y-0.5">
            {doss.industry && <span>{doss.industry}</span>}
            {doss.hq && <span>HQ: {doss.hq}</span>}
            {doss.revenue_musd && <span>Revenue: ${doss.revenue_musd}M</span>}
            {doss.domain && <a className="text-sky-600 hover:underline" href={`https://${doss.domain}`} target="_blank" rel="noreferrer">{doss.domain}</a>}
          </div>
        </div>
        <select value={company} onChange={e => setCompany(e.target.value)}
          className="text-xs font-semibold border border-gray-200 rounded-lg px-3 py-2 bg-gray-50 text-gray-700 cursor-pointer">
          {COMPANIES.map(c => <option key={c}>{c}</option>)}
        </select>
      </div>

      {loading ? (
        <div className="rounded-xl border border-dashed border-gray-200 bg-white py-14 text-center text-sm italic text-gray-400">Loading profile…</div>
      ) : (
        <>
          {/* dossier + treasury */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
            <Section icon={<Building2 size={14} />} title="Company dossier" source="Company dossier" sourceHref={`/?source=dossier&company=${encodeURIComponent(company)}`} >
              <p className="text-xs text-gray-600 leading-relaxed">{doss.profile || 'No dossier on file for this company.'}</p>
              {!!(doss.seed_motifs || []).length && (
                <div className="flex flex-wrap gap-1.5 mt-1">
                  {doss.seed_motifs.map((m: string) => (
                    <span key={m} className="text-[10px] font-bold bg-sky-50 text-sky-700 border border-sky-100 rounded-full px-2 py-0.5">{m}</span>
                  ))}
                </div>
              )}
            </Section>

            <Section icon={<Landmark size={14} />} title="Treasury profile" source="Company dossier" sourceHref={`/?source=dossier&company=${encodeURIComponent(company)}`}>
              <div className="text-xs text-gray-600 flex flex-col gap-1.5">
                {tp.current_banks && <div><b className="text-gray-700">Current banks:</b> {(tp.current_banks || []).join(', ')}</div>}
                {tp.payment_flows && <div><b className="text-gray-700">Payment flows:</b> {tp.payment_flows}</div>}
                {tp.fx_corridors && <div><b className="text-gray-700">FX corridors:</b> {Array.isArray(tp.fx_corridors) ? tp.fx_corridors.join(', ') : tp.fx_corridors}</div>}
                {!!(tp.pain_points || []).length && (
                  <div><b className="text-gray-700">Known pain points:</b>
                    <ul className="mt-1 flex flex-col gap-1">{tp.pain_points.map((p: string, i: number) => (
                      <li key={i} className="pl-3 relative before:content-['•'] before:absolute before:left-0 before:text-sky-500">{p}</li>))}
                    </ul>
                  </div>
                )}
              </div>
            </Section>
          </div>

          {/* contacts + CRM */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
            <Section icon={<Users size={14} />} title="Key contacts" source="Company dossier" sourceHref={`/?source=dossier&company=${encodeURIComponent(company)}`}>
              {(doss.contacts || []).length ? (
                <div className="flex flex-col gap-2">
                  {doss.contacts.map((c: any, i: number) => (
                    <div key={i} className="flex items-center justify-between bg-gray-50 border border-gray-200 rounded-lg px-3 py-2">
                      <div>
                        <div className="text-xs font-bold text-[#00254A]">{c.name} <span className="font-medium text-gray-500">— {c.title}</span></div>
                        <div className="text-[10.5px] text-gray-400">{c.email}{c.phone ? ` · ${c.phone}` : ''}</div>
                      </div>
                      {c.disposition && <span className="text-[10px] font-bold text-gray-500 bg-white border border-gray-200 rounded-full px-2 py-0.5 capitalize">{c.disposition}</span>}
                    </div>
                  ))}
                </div>
              ) : <div className="text-xs italic text-gray-400">No contacts on file.</div>}
            </Section>

            <Section icon={<History size={14} />} title="CRM record" source="CRM system" sourceHref={`/?source=crm&company=${encodeURIComponent(company)}`}>
              {crm.lead_id ? (
                <div className="text-xs text-gray-600 flex flex-col gap-1.5">
                  <div className="flex flex-wrap gap-x-4 gap-y-1">
                    <span><b className="text-gray-700">Lead:</b> {crm.lead_id}</span>
                    <span><b className="text-gray-700">Status:</b> {crm.status}</span>
                    {crm.fit_score != null && <span><b className="text-gray-700">Fit score:</b> {crm.fit_score}</span>}
                    <span><b className="text-gray-700">AI consent on file:</b> {String(crm.ai_consent ?? '—')}</span>
                  </div>
                  {!!(crm.call_reports || []).length && (
                    <div className="mt-1"><b className="text-gray-700">Recent call reports:</b>
                      {crm.call_reports.slice(-3).reverse().map((r: any) => (
                        <div key={r.report_id} className="bg-gray-50 border border-gray-200 rounded-lg px-3 py-1.5 mt-1">
                          <span className="text-[10px] text-gray-400 font-mono">{r.report_date}</span> {r.summary}
                          {r.next_step && <div className="text-[10.5px] text-sky-700 mt-0.5">Next: {r.next_step}</div>}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ) : <div className="text-xs italic text-gray-400">No CRM lead found for this company.</div>}
            </Section>
          </div>

          {/* news + market + kyc */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
            <Section icon={<Newspaper size={14} />} title="News coverage" source="News wire" sourceHref={`/?source=news&company=${encodeURIComponent(company)}`}>
              {(news || []).length ? (
                <div className="flex flex-col gap-1.5 max-h-[240px] overflow-y-auto pr-1">
                  {news.slice(0, 8).map((a: any) => (
                    <div key={a.article_id} className="bg-gray-50 border border-gray-200 rounded-lg px-3 py-2">
                      <div className="text-xs font-semibold text-gray-700 leading-snug">{a.headline}</div>
                      <div className="text-[10px] text-gray-400 mt-0.5">{a.outlet} · {a.date}{a.motif ? ` · ${a.motif}` : ''}</div>
                    </div>
                  ))}
                </div>
              ) : <div className="text-xs italic text-gray-400">No coverage found.</div>}
            </Section>

            <Section icon={<TrendingUp size={14} />} title="FX corridors" source="Market data" sourceHref={`/?source=market&company=${encodeURIComponent(company)}`}>
              {Object.keys(market || {}).length ? (
                <div className="flex flex-col gap-1.5">
                  {Object.entries(market).map(([sid, v]: [string, any]) => (
                    <div key={sid} className="flex items-center justify-between bg-gray-50 border border-gray-200 rounded-lg px-3 py-2">
                      <span className="text-xs font-bold text-[#00254A] font-mono">{sid}</span>
                      <span className="text-xs text-gray-600 tabular-nums">{v?.latest?.value}</span>
                      <span className={`text-[10.5px] font-bold tabular-nums ${(v?.yoy_change_pct ?? 0) >= 0 ? 'text-emerald-600' : 'text-red-600'}`}>
                        {v?.yoy_change_pct != null ? `${v.yoy_change_pct > 0 ? '+' : ''}${v.yoy_change_pct}% y/y` : ''}
                      </span>
                    </div>
                  ))}
                </div>
              ) : <div className="text-xs italic text-gray-400">Market data unavailable.</div>}
            </Section>

            <Section icon={<ShieldCheck size={14} />} title="KYC screen" source="KYC screening" sourceHref={`/?source=kyc&company=${encodeURIComponent(company)}`}>
              <div className="flex items-center gap-2">
                <span className={`text-[11px] font-extrabold uppercase rounded-full px-3 py-1 ring-1 ${verdictStyle}`}>{kyc.verdict || 'n/a'}</span>
                <span className="text-[10.5px] text-gray-400">screened: {kyc.query || company}</span>
              </div>
              {!!(kyc.candidates || []).length && (
                <div className="mt-1 text-[10.5px] text-gray-500">
                  Nearest watchlist entries:
                  {kyc.candidates.slice(0, 2).map((c: any, i: number) => (
                    <div key={i} className="bg-gray-50 border border-gray-200 rounded px-2 py-1 mt-1">
                      {c.entry?.name} <span className="text-gray-400">({c.entry?.list}, similarity {Math.round((c.similarity || 0) * 100)}%)</span>
                    </div>
                  ))}
                </div>
              )}
            </Section>
          </div>

          {/* timeline + sources */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
            <Section icon={<History size={14} />} title="Company timeline" source="Company dossier" sourceHref={`/?source=dossier&company=${encodeURIComponent(company)}`}>
              {(doss.timeline || []).length ? (
                <div className="flex flex-col gap-1 max-h-[220px] overflow-y-auto pr-1">
                  {doss.timeline.map((t: any, i: number) => (
                    <div key={i} className="flex gap-3 text-xs">
                      <span className="font-mono text-[10px] text-gray-400 shrink-0 w-20">{t.date}</span>
                      <span className="text-gray-600">{t.event}</span>
                    </div>
                  ))}
                </div>
              ) : <div className="text-xs italic text-gray-400">No timeline on file.</div>}
            </Section>

            <Section icon={<Link2 size={14} />} title="All data sources" >
              <div className="flex flex-col gap-1 max-h-[220px] overflow-y-auto pr-1">
                {(d?.datasources || []).map((s: any, i: number) => (
                  <a key={i} href={s.link} target="_blank" rel="noopener noreferrer"
                    className="flex items-center justify-between text-[11px] bg-gray-50 border border-gray-200 rounded-lg px-3 py-1.5 hover:border-sky-300 hover:text-sky-700 text-gray-600">
                    <span className="truncate">{s.name}</span>
                    <span className="text-[9.5px] font-bold uppercase tracking-wider text-gray-400 shrink-0 ml-2">{s.kind}</span>
                  </a>
                ))}
              </div>
            </Section>
          </div>
        </>
      )}
    </main>
  );
};
