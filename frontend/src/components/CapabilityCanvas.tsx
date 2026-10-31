import React from 'react';
import { LayoutGrid, Link2, Gavel, Globe, Lock, Eye, CheckCircle2 } from 'lucide-react';
import { Capability } from '../types';

// The live insight cards extend the base Capability with optional metadata the
// Insights Agent now attaches. Confidence is optional (frequently absent).
export interface InsightCardData extends Omit<Capability, 'confidence'> {
  confidence?: number;
  category?: string;
  capturedAt?: string;
}

// category → chip style. label = category.replace('_', ' ').
const CATEGORY_STYLE: Record<string, string> = {
  pain_point: 'bg-red-50 text-red-600 ring-red-200',
  risk: 'bg-amber-50 text-amber-700 ring-amber-200',
  need: 'bg-sky-50 text-sky-700 ring-sky-200',
  product_interest: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  signal: 'bg-violet-50 text-violet-700 ring-violet-200',
};

interface CapabilityCardProps {
  card: InsightCardData;
}

export const CapabilityCard: React.FC<CapabilityCardProps> = ({ card }) => {
  const chip = card.category ? (CATEGORY_STYLE[card.category] ?? 'bg-gray-50 text-gray-600 ring-gray-200') : null;

  return (
    <div id={`card-${card.id}`} className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm flex flex-col gap-3 hover:shadow-md hover:border-sky-500/35 transition-all">
      {/* Category chip + name + review badge */}
      <div className="flex justify-between items-start gap-2">
        <div className="flex flex-col gap-1.5 min-w-0">
          {card.category && (
            <span className={`w-fit rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide ring-1 ${chip}`}>
              {card.category.replace('_', ' ')}
            </span>
          )}
          <h4 className="font-bold text-sm text-[#00254A] leading-snug">{card.capabilityName}</h4>
        </div>
        <div className="flex gap-1.5 shrink-0">
          {card.humanReviewRequired ? (
            <span className="bg-amber-50 text-amber-700 ring-1 ring-amber-200 text-[10px] font-bold px-2 py-0.5 rounded-full flex items-center gap-0.5" title="Human review needed">
              <Eye size={10} /> H-Review
            </span>
          ) : (
            <span className="bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200 text-[10px] font-bold px-2 py-0.5 rounded-full flex items-center gap-0.5">
              <CheckCircle2 size={10} /> Automated
            </span>
          )}
        </div>
      </div>

      {/* Linked Discovered Pain Point */}
      <div className="bg-sky-50 px-2.5 py-1.5 rounded-lg border border-sky-100 text-[11px] flex gap-1.5 leading-normal">
        <Link2 size={13} className="text-sky-600 shrink-0 mt-0.5" />
        <span className="text-gray-600 font-medium">
          <strong className="text-gray-800 font-bold">Heard:</strong> {card.painPointLink}
        </span>
      </div>

      {/* Summary and Executive Meaning */}
      <div className="text-xs text-gray-700 flex flex-col gap-1.5">
        <p className="leading-relaxed">
          <strong className="text-[#00254A] font-bold">Insight:</strong> {card.plainEnglishSummary}
        </p>
        <p className="leading-relaxed">
          <strong className="text-[#00254A] font-bold">Recommended action:</strong> {card.whyItMatters}
        </p>
      </div>

      {/* Grounding footer + capture time */}
      <div className="mt-auto pt-3 border-t border-gray-200 flex justify-between items-center text-[10px] font-semibold text-gray-500">
        <div className="flex items-center gap-1">
          <Gavel size={12} className="text-emerald-600" />
          <span>{card.sourceStatus}</span>
        </div>
        {card.capturedAt && <span className="text-gray-400 font-normal font-mono">{card.capturedAt}</span>}
      </div>

      {/* Source Links */}
      {card.groundingUrls && card.groundingUrls.length > 0 && (
        <div className="pt-2 flex flex-col gap-1 text-[9px] text-sky-700">
          <span className="text-gray-400 font-mono">Verified grounding:</span>
          {card.groundingUrls.map((url, i) => (
            <a
              key={i}
              href={url}
              target="_blank"
              rel="noopener noreferrer nofollow"
              className="hover:underline flex items-center gap-0.5 font-mono truncate"
            >
              <Globe size={10} className="shrink-0" />
              {url}
            </a>
          ))}
        </div>
      )}
    </div>
  );
};

interface CapabilityCanvasProps {
  cards: InsightCardData[];
  needsConfirmed: boolean;
  analyzing?: boolean;
}

export const CapabilityCanvas: React.FC<CapabilityCanvasProps> = ({
  cards,
  needsConfirmed,
  analyzing,
}) => {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm flex flex-col gap-4">
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="mb-1 text-[10px] font-bold uppercase tracking-wider text-gray-400">Live insights (Insights Agent)</div>
          <h3 className="flex items-center gap-2 text-[13px] font-bold text-[#00254A]">
            <LayoutGrid size={14} className="text-sky-600" /> Shared capability canvas
          </h3>
        </div>
        <div className="flex items-center gap-2">
          {analyzing && (
            <span className="inline-flex items-center gap-1 rounded-full bg-sky-50 px-2.5 py-0.5 text-[10.5px] font-bold text-[#00254A] animate-pulse">
              <span className="h-1.5 w-1.5 rounded-full bg-sky-500" />
              analyzing…
            </span>
          )}
          <span className={needsConfirmed
            ? 'rounded-full bg-sky-50 px-2.5 py-0.5 text-[10.5px] font-bold text-[#00254A]'
            : 'rounded-full bg-gray-100 px-2.5 py-0.5 text-[10.5px] font-bold text-gray-600'}>
            {needsConfirmed ? `${cards.length} suggested` : 'Awaiting unlock'}
          </span>
        </div>
      </div>

      {/* Render conditional content */}
      {!needsConfirmed ? (
        <div className="flex flex-col items-center justify-center p-10 text-center text-gray-400 gap-2 rounded-lg border border-dashed border-gray-200 bg-gray-50 min-h-[160px]">
          <Lock size={32} className="text-gray-300 animate-pulse" />
          <h4 className="font-bold text-sm text-gray-700 mt-2">Dynamic capabilities locked</h4>
          <p className="text-[12px] italic max-w-md">
            Unlocks as the Insights Agent hears discovery topics in the live meeting.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {cards.map((card) => (
            <CapabilityCard key={card.id} card={card} />
          ))}
        </div>
      )}
    </div>
  );
};
