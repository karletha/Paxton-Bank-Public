// DATA SOURCE VIEWER — client-presentable pages for the simulated bank
// systems (?source=crm|news|market|kyc|dossier). Every "data source" link in
// the Client Profile opens one of these styled readers in a new tab — raw API
// JSON is never shown to a client. Backed by the live demo services (Cloud
// Run), not files: the CRM is mutable (the fleet writes call reports into it).
import React, { useEffect, useState } from 'react';
import { Building2, Newspaper, TrendingUp, ShieldCheck, History, ArrowLeft } from 'lucide-react';
import { fetchClientProfile } from '../services/geminiLiveClient';

const origin = () => (import.meta as any).env?.VITE_BACKEND_URL || window.location.origin;

const Shell: React.FC<{ icon: React.ReactNode; system: string; sub: string; children: React.ReactNode }> =
  ({ icon, system, sub, children }) => (
    <div className="min-h-screen bg-[#F4F6F9] text-[#1F2933] antialiased">
      <header className="bg-[#00254A] text-white px-8 py-4 flex items-center justify-between sticky top-0 z-10 shadow">
        <div className="flex items-center gap-3">
          <span className="text-xl font-black tracking-wider">PAXTON BANK</span>
          <span className="bg-sky-400/20 text-sky-300 font-mono text-xs px-2 py-0.5 rounded border border-sky-400/30 uppercase tracking-widest font-bold">BRIDGET</span>
          <span className="text-sky-300">{icon}</span>
          <div>
            <div className="text-sm font-bold leading-tight">{system}</div>
            <div className="text-[10.5px] text-sky-200/70 leading-tight">{sub}</div>
          </div>
        </div>
        <span className="text-[10px] font-bold uppercase tracking-widest bg-white/10 border border-white/15 rounded-full px-3 py-1 text-sky-200">Simulated demo system · live service</span>
      </header>
      <main className="max-w-4xl mx-auto px-6 py-8">{children}</main>
    </div>
  );

export const SourceViewer: React.FC<{ source: string; company: string }> = ({ source, company }) => {
  const [profile, setProfile] = useState<any>(null);
  const [articles, setArticles] = useState<any[]>([]);
  const [article, setArticle] = useState<any>(null);

  useEffect(() => { fetchClientProfile(company).then(setProfile); }, [company]);
  useEffect(() => {
    if (source !== 'news') return;
    (async () => {
      try {
        const d = await (await fetch(`${origin()}/sim/newswire/api/articles?company=${encodeURIComponent(company.split(' ')[0])}&limit=30`)).json();
        setArticles(Array.isArray(d) ? d : d.articles || []);
      } catch { /* leave empty */ }
    })();
  }, [source, company]);

  const doss = profile?.dossier || {};
  const crm = profile?.crm || {};
  const market = profile?.market || {};
  const kyc = profile?.kyc || {};

  if (source === 'crm') {
    return (
      <Shell icon={<History size={18} />} system="CRM — Client Relationship Record" sub={`Lead file · ${company}`}>
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6 flex flex-col gap-4">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 pb-4">
            <div>
              <div className="text-lg font-bold text-[#00254A]">{crm.company || company}</div>
              <div className="text-xs text-gray-500">{crm.industry} {crm.state ? `· ${crm.state}` : ''} {crm.revenue_musd ? `· $${crm.revenue_musd}M revenue` : ''}</div>
            </div>
            <div className="flex gap-2">
              {crm.lead_id && <span className="text-[10.5px] font-bold bg-gray-100 rounded-full px-3 py-1 font-mono">{crm.lead_id}</span>}
              {crm.status && <span className="text-[10.5px] font-bold bg-sky-50 text-sky-700 border border-sky-100 rounded-full px-3 py-1 uppercase">{crm.status}</span>}
              {crm.fit_score != null && <span className="text-[10.5px] font-bold bg-emerald-50 text-emerald-700 border border-emerald-100 rounded-full px-3 py-1">Fit {crm.fit_score}</span>}
            </div>
          </div>
          <div>
            <div className="text-[10.5px] font-extrabold uppercase tracking-widest text-gray-400 mb-2">Call reports</div>
            <div className="flex flex-col gap-2.5">
              {(crm.call_reports || []).slice().reverse().map((r: any) => (
                <div key={r.report_id} className="border border-gray-200 rounded-xl p-4">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[10.5px] font-mono text-gray-400">{r.report_date} · {r.rtype || 'call'}{r.contact ? ` · ${r.contact}` : ''}</span>
                    <span className="text-[9.5px] font-bold uppercase tracking-wider text-gray-300">{r.source || ''}</span>
                  </div>
                  <p className="text-xs text-gray-700 leading-relaxed">{r.summary}</p>
                  {r.next_step && <p className="text-xs text-sky-700 mt-1.5 font-medium">Next step: {r.next_step}</p>}
                </div>
              ))}
              {!(crm.call_reports || []).length && <div className="text-xs italic text-gray-400 py-6 text-center">No call reports on file.</div>}
            </div>
          </div>
        </div>
      </Shell>
    );
  }

  if (source === 'news') {
    return (
      <Shell icon={<Newspaper size={18} />} system="News Wire" sub={`Coverage · ${company}`}>
        {article ? (
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-8">
            <button onClick={() => setArticle(null)} className="text-xs font-bold text-sky-600 hover:underline flex items-center gap-1 mb-4 cursor-pointer"><ArrowLeft size={12} /> All coverage</button>
            <div className="text-[10.5px] font-mono text-gray-400 mb-2">{article.outlet} · {article.date}</div>
            <h1 className="text-xl font-bold text-[#00254A] leading-snug mb-4">{article.headline}</h1>
            <div className="text-[13px] text-gray-700 leading-relaxed whitespace-pre-line">{article.body || 'Full text unavailable.'}</div>
          </div>
        ) : (
          <div className="flex flex-col gap-2.5">
            {articles.map((a: any) => (
              <button key={a.article_id}
                onClick={async () => {
                  try { setArticle(await (await fetch(`${origin()}/sim/newswire/api/articles/${a.article_id}`)).json()); }
                  catch { setArticle(a); }
                }}
                className="bg-white rounded-xl border border-gray-200 shadow-sm px-5 py-4 text-left hover:border-sky-300 transition-colors cursor-pointer">
                <div className="text-sm font-bold text-[#00254A] leading-snug">{a.headline}</div>
                <div className="text-[10.5px] text-gray-400 mt-1">{a.outlet} · {a.date}{a.motif ? ` · ${a.motif}` : ''}</div>
              </button>
            ))}
            {!articles.length && <div className="text-xs italic text-gray-400 py-10 text-center bg-white rounded-xl border border-dashed border-gray-200">No coverage found.</div>}
          </div>
        )}
      </Shell>
    );
  }

  if (source === 'market') {
    return (
      <Shell icon={<TrendingUp size={18} />} system="Market Data" sub="FX corridors — latest & year-over-year">
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
          <table className="w-full text-sm">
            <thead><tr className="text-left text-[10.5px] uppercase tracking-widest text-gray-400 border-b-2 border-gray-100">
              <th className="py-2">Corridor</th><th className="py-2 text-right">Latest</th><th className="py-2 text-right">As of</th><th className="py-2 text-right">Change y/y</th></tr></thead>
            <tbody>
              {Object.entries(market).filter(([, v]: any) => v && v.latest).map(([sid, v]: [string, any]) => (
                <tr key={sid} className="border-b border-gray-50">
                  <td className="py-3 font-mono font-bold text-[#00254A]">{sid}</td>
                  <td className="py-3 text-right tabular-nums font-semibold">{v.latest.value}</td>
                  <td className="py-3 text-right text-xs text-gray-400">{v.latest.date}</td>
                  <td className={`py-3 text-right tabular-nums font-bold ${(v.yoy_change_pct ?? 0) >= 0 ? 'text-emerald-600' : 'text-red-600'}`}>
                    {v.yoy_change_pct > 0 ? '+' : ''}{v.yoy_change_pct}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Shell>
    );
  }

  if (source === 'kyc') {
    const style = kyc.verdict === 'clear' ? 'bg-emerald-50 text-emerald-700 ring-emerald-200'
      : kyc.verdict === 'review' ? 'bg-amber-50 text-amber-700 ring-amber-200' : 'bg-red-50 text-red-700 ring-red-200';
    return (
      <Shell icon={<ShieldCheck size={18} />} system="KYC Screening" sub={`Watchlist screen · ${company}`}>
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-8 flex flex-col items-center gap-4">
          <span className={`text-sm font-extrabold uppercase tracking-widest rounded-full px-6 py-2 ring-1 ${style}`}>{kyc.verdict || 'n/a'}</span>
          <div className="text-xs text-gray-500">Entity screened: <b className="text-gray-700">{kyc.query || company}</b></div>
          {!!(kyc.candidates || []).length && (
            <div className="w-full mt-2">
              <div className="text-[10.5px] font-extrabold uppercase tracking-widest text-gray-400 mb-2">Nearest watchlist entries</div>
              {kyc.candidates.map((c: any, i: number) => (
                <div key={i} className="flex items-center justify-between border border-gray-200 rounded-lg px-4 py-2.5 mb-1.5 text-xs">
                  <span className="font-semibold text-gray-700">{c.entry?.name}</span>
                  <span className="text-gray-400">{c.entry?.list} · {c.entry?.country} · similarity {Math.round((c.similarity || 0) * 100)}%</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </Shell>
    );
  }

  // dossier (default)
  const tp = doss.treasury_profile || {};
  return (
    <Shell icon={<Building2 size={18} />} system="Company Dossier" sub={`Relationship file · ${company}`}>
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-8 flex flex-col gap-6">
        <div>
          <h1 className="text-xl font-bold text-[#00254A]">{doss.company || company}</h1>
          <div className="text-xs text-gray-500 mt-1">{doss.industry} · {doss.hq} {doss.revenue_musd ? `· $${doss.revenue_musd}M revenue` : ''}</div>
        </div>
        <p className="text-[13px] text-gray-700 leading-relaxed">{doss.profile}</p>
        {!!(tp.pain_points || []).length && (
          <div>
            <div className="text-[10.5px] font-extrabold uppercase tracking-widest text-gray-400 mb-2">Treasury pain points</div>
            <ul className="flex flex-col gap-1.5">{tp.pain_points.map((p: string, i: number) => (
              <li key={i} className="text-xs text-gray-700 pl-4 relative before:content-['•'] before:absolute before:left-0 before:text-sky-500">{p}</li>))}
            </ul>
          </div>
        )}
        {!!(doss.timeline || []).length && (
          <div>
            <div className="text-[10.5px] font-extrabold uppercase tracking-widest text-gray-400 mb-2">Timeline</div>
            <div className="flex flex-col gap-1.5">{doss.timeline.map((t: any, i: number) => (
              <div key={i} className="flex gap-4 text-xs"><span className="font-mono text-[10px] text-gray-400 w-20 shrink-0">{t.date}</span><span className="text-gray-700">{t.event}</span></div>))}
            </div>
          </div>
        )}
      </div>
    </Shell>
  );
};
