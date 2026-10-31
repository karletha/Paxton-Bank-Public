import React from 'react';
import { Activity } from 'lucide-react';

export interface SentimentData {
  score: number; // 0 to 100
  label: 'Positive' | 'Neutral' | 'Skeptical' | 'Alert' | 'Awaiting Conversation';
  insights: string;
  delta?: number;
  timeline: { turn: number; score: number; label: string; timestamp: string; driver?: string }[];
}

interface SentimentPanelProps {
  sentiment: SentimentData;
  analyzing?: boolean;
}

// Score → tier color for the timeline dots. Mirrors App's Pulse mapping:
// >=70 Positive (emerald), >=45 Neutral (sky), else Skeptical (red).
const tierColor = (score: number) =>
  score >= 70 ? '#10B981' : score >= 45 ? '#0284C7' : '#DC2626';

export const SentimentPanel: React.FC<SentimentPanelProps> = ({ sentiment, analyzing }) => {
  const { score, insights, timeline, delta } = sentiment;
  const hasConversation = timeline.length > 0 || score > 0;

  // Get color themes for the gauge.
  const getTheme = () => {
    if (!hasConversation) {
      return { text: 'text-gray-400', label: 'Awaiting conversation', color: '#6B7280' };
    }
    if (score >= 70) {
      return { text: 'text-emerald-600', label: 'Positive — receptive & engaged', color: '#10B981' };
    } else if (score >= 45) {
      return { text: 'text-sky-600', label: 'Neutral discovery', color: '#0284C7' };
    } else {
      return { text: 'text-red-600', label: 'Skeptical — needs attention', color: '#DC2626' };
    }
  };

  const theme = getTheme();

  // SVG Circle Gauge calculations
  const radius = 38;
  const circumference = 2 * Math.PI * radius;
  const strokeDashoffset = circumference - (score / 100) * circumference;

  const trendGlyph = delta == null ? null : delta < 0 ? '▼' : delta > 0 ? '▲' : '—';
  const trendColor = delta == null ? '' : delta < 0 ? 'text-red-600' : delta > 0 ? 'text-emerald-600' : 'text-gray-400';

  return (
    <div className="flex-1 rounded-xl border border-gray-200 bg-white p-5 shadow-sm flex flex-col gap-4 max-h-[440px] overflow-hidden select-none">

      {/* Header */}
      <div className="flex items-start justify-between gap-2 shrink-0">
        <div>
          <div className="mb-1 text-[10px] font-bold uppercase tracking-wider text-gray-400">Client sentiment (Sentiment Agent)</div>
          <h3 className="flex items-center gap-2 text-[13px] font-bold text-[#00254A]">
            <Activity size={14} className="text-sky-600" /> Live sentiment
          </h3>
        </div>
        {analyzing && (
          <span className="inline-flex items-center gap-1 rounded-full bg-sky-50 px-2.5 py-0.5 text-[10.5px] font-bold text-[#00254A] animate-pulse">
            <span className="h-1.5 w-1.5 rounded-full bg-sky-500" />
            analyzing…
          </span>
        )}
      </div>

      {/* Gauge Row */}
      <div className="flex items-center gap-4 bg-gray-50 p-3 border border-gray-200 rounded-lg shrink-0">

        {/* SVG Circular Progress Gauge */}
        <div className="relative w-20 h-20 shrink-0">
          <svg className="w-full h-full transform -rotate-90" viewBox="0 0 100 100">
            <circle cx="50" cy="50" r={radius} className="stroke-current text-gray-200" strokeWidth="8" fill="transparent" />
            <circle
              cx="50" cy="50" r={radius}
              className="stroke-current transition-all duration-500 ease-out"
              stroke={theme.color}
              strokeWidth="8"
              fill="transparent"
              strokeDasharray={circumference}
              strokeDashoffset={hasConversation ? strokeDashoffset : circumference}
              strokeLinecap="round"
            />
          </svg>
          <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
            <span className={`text-base font-black ${theme.text}`}>
              {hasConversation ? `${score}%` : '0%'}
            </span>
            {hasConversation && trendGlyph ? (
              <span className={`text-[11px] font-bold ${trendColor}`}>
                {trendGlyph} {Math.abs(delta as number)}
              </span>
            ) : (
              <span className="text-[9px] uppercase tracking-wider font-bold text-gray-400">
                {hasConversation ? 'Score' : 'Idle'}
              </span>
            )}
          </div>
        </div>

        {/* Label and Summary */}
        <div className="flex-grow">
          <div className="flex items-center gap-1.5 mb-1">
            <span className={`w-2 h-2 rounded-full ${hasConversation ? 'bg-emerald-500 animate-pulse' : 'bg-gray-400'}`} />
            <h4 className={`text-xs font-bold ${theme.text}`}>{theme.label}</h4>
          </div>
          <p className="text-[11px] text-gray-600 leading-snug">
            {hasConversation
              ? insights
              : 'Awaiting call initiation. Real-time client sentiment and commercial intent will score dynamically as topics are discussed.'}
          </p>
        </div>
      </div>

      {/* Timeline Turns List */}
      <div className="flex flex-col gap-1.5 min-h-0">
        <span className="text-[10px] uppercase font-bold tracking-wider text-gray-400 px-1">
          Turn intent timeline
        </span>

        {timeline.length === 0 ? (
          <div className="rounded-lg border border-dashed border-gray-200 bg-gray-50 py-6 text-center text-[12px] italic text-gray-400">
            Turn analytics will register here in real time.
          </div>
        ) : (
          <div className="flex flex-col gap-2 max-h-[140px] overflow-y-auto pr-1">
            {timeline.slice(-4).reverse().map((item, idx) => (
              <div
                key={idx}
                className="flex items-center justify-between gap-2 p-2 bg-white border border-gray-100 rounded-lg text-xs"
              >
                <div className="flex items-center gap-2 min-w-0">
                  <span className="h-2 w-2 rounded-full shrink-0" style={{ background: tierColor(item.score) }} />
                  <span className="font-medium text-gray-800 truncate">{item.driver || item.label}</span>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="font-bold text-sky-600">{item.score}%</span>
                  <span className="text-[10px] text-gray-400 font-mono">{item.timestamp}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
