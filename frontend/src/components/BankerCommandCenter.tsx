import React from 'react';
import { Scale, Gavel, ScrollText, RotateCcw, BookOpenCheck } from 'lucide-react';

interface BankerCommandCenterProps {
  bankerName?: string;
  armed?: boolean;
  groundingSources?: { id: string; title: string; url: string }[];
  groundingOn?: boolean;
  groundingHits?: { source_id: string; title: string; url: string; snippet: string; at?: number }[];
  restrictedTopics: string[];
  activeTriggeredTopic: string | null;
  complianceLogs: string[];
  systemLogs: string[];
  telemetryMetrics: {
    latencyMs: number | null;
    tokensPerSecond: number | null;
    websocketStatus: string;
    groundingConfidence: number | null;
  };
  onForceTakeover?: () => void;
  onAdvanceDemoStep?: () => void;
  onResetDemo?: () => void;
  currentPlaybackStep?: number;
}

export const BankerCommandCenter: React.FC<BankerCommandCenterProps> = ({
  bankerName,
  armed = true,
  groundingSources = [],
  groundingOn = true,
  groundingHits = [],
  restrictedTopics,
  activeTriggeredTopic,
  complianceLogs = [],
  telemetryMetrics,
  onResetDemo,
}) => {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm flex flex-col gap-5">

      {/* Header */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3">
        <div>
          <div className="mb-1 text-[10px] font-bold uppercase tracking-wider text-gray-400">Governance &amp; guardrails</div>
          <h3 className="flex items-center gap-2 text-[13px] font-bold text-[#00254A]">
            <Scale size={14} className="text-sky-600" /> Banker command center
          </h3>
          <p className="mt-1 text-xs text-gray-500">
            {bankerName && (
              <>Banker: <strong className="text-[#00254A] font-bold">{bankerName}</strong> · </>
            )}
            Bridget policy guardrails
          </p>
        </div>

        {/* Action Controls */}
        <div className="flex items-center gap-2 shrink-0">
          {onResetDemo && (
            <button
              id="reset-demo-btn"
              onClick={onResetDemo}
              className="bg-gray-100 hover:bg-gray-200 text-gray-700 font-bold text-xs px-3 py-1.5 rounded-lg flex items-center gap-1 transition-colors"
              title="Reset session state"
            >
              <RotateCcw size={14} /> Reset session
            </button>
          )}
        </div>
      </div>

      {/* Grid of Command panels */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">

        {/* Compliance Guardrails Panel */}
        <div className="bg-gray-50 p-4 border border-gray-200 rounded-xl flex flex-col gap-3.5">
          <div className="flex items-center justify-between">
            <h4 className="font-bold text-xs text-[#00254A] uppercase tracking-wider flex items-center gap-1.5">
              <Gavel size={14} className="text-sky-600" /> Governance guardrails
            </h4>
            {activeTriggeredTopic ? (
              <span className="rounded-full bg-red-50 px-2.5 py-0.5 text-[10.5px] font-bold text-red-600 ring-1 ring-red-200 animate-pulse flex items-center gap-0.5">
                ⚠ Risk warning
              </span>
            ) : (
              <span className="rounded-full bg-emerald-50 px-2.5 py-0.5 text-[10.5px] font-bold text-emerald-700 ring-1 ring-emerald-200 flex items-center gap-0.5">
                ● Secured
              </span>
            )}
          </div>

          <div id="risk-flag-panel" className="flex flex-col gap-1.5 max-h-[190px] overflow-y-auto pr-1">
            {restrictedTopics.map((topic, i) => {
              const matchesTrigger = activeTriggeredTopic?.toLowerCase() === topic.toLowerCase();
              return (
                <div
                  key={i}
                  className={`px-3 py-2 rounded-lg text-xs flex justify-between items-center border transition-all ${
                    matchesTrigger
                      ? 'bg-red-50 border-red-300 text-red-600 font-bold shadow-sm'
                      : 'bg-white border-gray-200 text-gray-600'
                  }`}
                >
                  <span className="capitalize">{topic}</span>
                  {matchesTrigger ? (
                    <span className="text-[10px] bg-red-600 text-white font-bold px-1.5 py-0.5 rounded-full animate-bounce">
                      Breached!
                    </span>
                  ) : (
                    <span className="text-[10px] text-gray-400 font-mono">Monitored</span>
                  )}
                </div>
              );
            })}
          </div>

          <p className="text-[10px] text-gray-400 italic">
            *Bridget’s underlying LLM is system-grounded against committing binding product claims or pricing guarantees. Violations trigger an immediate handoff view.
          </p>
        </div>

        {/* Compliance Logs & Console Messages */}
        <div className="bg-gray-50 p-4 border border-gray-200 rounded-xl flex flex-col gap-3.5">
          <h4 className="font-bold text-xs text-[#00254A] uppercase tracking-wider flex items-center gap-1.5">
            <ScrollText size={14} className="text-sky-600" /> Compliance audit — THIS meeting (live)
          </h4>

          <div className={`bg-gray-50 border border-gray-200 text-gray-700 font-mono text-[10px] p-2.5 rounded-lg overflow-y-auto flex flex-col gap-1 leading-snug ${complianceLogs.length ? 'h-[200px]' : 'h-[80px]'}`}>
            {complianceLogs.length === 0 ? (
              <div className="text-gray-400 italic p-2 text-center h-full flex items-center justify-center">
                System telemetry online. Stream queue empty.
              </div>
            ) : (
              complianceLogs.map((log, idx) => (
                <div key={idx} className="border-b border-gray-200/70 pb-1 last:border-b-0">
                  <span className="text-sky-600">{`[CCB-AUDIT] `}</span>
                  {log}
                </div>
              ))
            )}
          </div>
        </div>

      </div>

      {/* Corporate knowledge grounding — the verified Paxton Bank corpus Bridget
          references live (Policy Panel feature) */}
      <div className="bg-gray-50 p-4 border border-gray-200 rounded-xl flex flex-col gap-3.5">
        <div className="flex items-center justify-between">
          <h4 className="font-bold text-xs text-[#00254A] uppercase tracking-wider flex items-center gap-1.5">
            <BookOpenCheck size={14} className="text-sky-600" /> Corporate knowledge grounding
          </h4>
          <span className={`rounded-full px-2.5 py-0.5 text-[10.5px] font-bold ring-1 flex items-center gap-0.5 ${
            groundingOn ? 'bg-emerald-50 text-emerald-700 ring-emerald-200' : 'bg-gray-100 text-gray-400 ring-gray-200'}`}>
            ● {groundingOn ? `Armed · ${groundingSources.length} sources` : 'Disabled'}
          </span>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="flex flex-col gap-1.5 max-h-[190px] overflow-y-auto pr-1">
            {groundingSources.map((s, i) => (
              <a key={s.id} href={s.url} target="_blank" rel="noopener noreferrer"
                className="block shrink-0 px-3 py-2 rounded-lg text-[11px] leading-snug bg-white border border-gray-200 text-gray-600 hover:border-sky-300 hover:text-sky-700 truncate transition-colors"
                title={s.title}>
                {i + 1}. {s.title}
              </a>
            ))}
            {groundingSources.length === 0 && (
              <div className="text-[11px] italic text-gray-400 p-2">No grounding sources loaded.</div>
            )}
          </div>

          <div className="flex flex-col gap-1.5">
            <div className="text-[10px] font-bold uppercase tracking-wider text-gray-400">Referenced live — THIS meeting</div>
            {groundingHits.length === 0 ? (
              <div className="text-[11px] italic text-gray-400 bg-white border border-dashed border-gray-200 rounded-lg p-3 text-center">
                When the conversation touches a covered topic, the excerpt Bridget grounds her answer in appears here.
              </div>
            ) : (
              <div className="flex flex-col gap-1.5 max-h-[150px] overflow-y-auto pr-1">
                {groundingHits.map((h, i) => (
                  <div key={i} className="px-3 py-2 rounded-lg bg-white border border-sky-100 text-[11px]">
                    <div className="font-semibold text-sky-700 truncate">{h.title}</div>
                    <div className="text-gray-500 leading-snug line-clamp-2">{h.snippet}</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Mini Telemetry status strip */}
      <div className="bg-sky-50 px-4 py-2.5 rounded-xl flex flex-wrap justify-between items-center text-[10px] font-semibold text-gray-600 gap-2 border border-sky-100">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-1">
            <span>Advisor TTFT: <strong className="font-bold font-mono text-sky-700">{telemetryMetrics.latencyMs != null ? `${telemetryMetrics.latencyMs}ms` : '— measuring'}</strong></span>
          </div>
        </div>
        <div className="flex items-center gap-1.5 font-mono">
          <span>Guardrails:</span>
          <span className={`font-bold ${armed ? 'text-emerald-600' : 'text-gray-400'}`}>
            {armed ? 'Sentinel armed' : 'Sentinel off'}
          </span>
        </div>
      </div>

    </div>
  );
};
