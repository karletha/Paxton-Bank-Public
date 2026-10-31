import React from 'react';
import { Package, AlertTriangle, CheckCircle2, Boxes, Copy, Check } from 'lucide-react';
import { ProspectProfile } from '../services/sessionState';

interface SalesHandoffPacketProps {
  prospectProfile: ProspectProfile;
  discoveredPainPoints: string[];
  confirmedNeeds: string[];
  proposedSolutions: string[];
  viableScore: string | null;
  crmPayloadSummary: string;
  emailBody?: string;
}

export const SalesHandoffPacket: React.FC<SalesHandoffPacketProps> = ({
  prospectProfile,
  discoveredPainPoints,
  confirmedNeeds,
  proposedSolutions,
  viableScore,
  emailBody,
}) => {
  const [copied, setCopied] = React.useState(false);

  const handleCopy = () => {
    if (!emailBody) return;
    navigator.clipboard.writeText(emailBody);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const markets = [
    prospectProfile.hasExpansionHK ? 'Hong Kong' : null,
    prospectProfile.hasExpansionSG ? 'Singapore' : null,
  ].filter(Boolean) as string[];

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm flex flex-col gap-6">

      {/* Header */}
      <div className="flex justify-between items-start gap-3 border-b border-gray-100 pb-4">
        <div>
          <div className="mb-1 text-[10px] font-bold uppercase tracking-wider text-gray-400">Warm handoff</div>
          <h3 className="flex items-center gap-2 text-[13px] font-bold text-[#00254A]">
            <Package size={14} className="text-sky-600" /> Sales handoff packet
            <span className="rounded-full bg-red-50 px-2.5 py-0.5 text-[10px] uppercase font-bold text-red-600 ring-1 ring-red-200 flex items-center gap-0.5">
              <AlertTriangle size={11} /> Review required
            </span>
          </h3>
          <p className="text-xs text-gray-400 mt-1">
            Automated intelligence handoff derived from live virtual client meeting telemetry.
          </p>
        </div>

        <div className="text-right max-w-[220px] shrink-0">
          <span className="text-[10px] uppercase font-bold text-gray-400 block tracking-widest">
            Lead qualification
          </span>
          {viableScore ? (
            <span className="text-lg font-extrabold text-emerald-600 block">{viableScore}</span>
          ) : (
            <span className="text-[11px] font-medium text-gray-400 block leading-snug">
              Scoring agent parked — enable Scorer for fit tier
            </span>
          )}
        </div>
      </div>

      {/* Profile summary info */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="p-3 bg-gray-50 rounded-lg border border-gray-200">
          <span className="text-[10px] text-gray-400 uppercase font-bold block mb-1">Company profile</span>
          <h4 className="font-bold text-sm text-[#00254A]">{prospectProfile.companyName}</h4>
          <span className="text-xs text-gray-500">{prospectProfile.industry}</span>
        </div>

        <div className="p-3 bg-gray-50 rounded-lg border border-gray-200">
          <span className="text-[10px] text-gray-400 uppercase font-bold block mb-1">Target markets</span>
          {markets.length > 0 ? (
            <div className="flex gap-1.5 mt-1">
              {markets.map((m) => (
                <span key={m} className="bg-sky-50 text-sky-700 text-xs font-bold px-2 py-0.5 rounded">{m}</span>
              ))}
            </div>
          ) : (
            <span className="text-xs text-gray-400 italic">Captured during live discovery</span>
          )}
        </div>

        <div className="p-3 bg-gray-50 rounded-lg border border-gray-200">
          <span className="text-[10px] text-gray-400 uppercase font-bold block mb-1">Client contact</span>
          <h4 className="font-bold text-sm text-[#00254A]">{prospectProfile.contactName}</h4>
          <span className="text-xs text-gray-500">Commercial banking prospect</span>
        </div>
      </div>

      {/* Structured Discovery Points */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="flex flex-col gap-2">
          <span className="text-xs font-bold text-[#00254A] flex items-center gap-1.5">
            <AlertTriangle size={14} className="text-red-600" /> Discovered pain points
          </span>
          <ul className="text-xs text-gray-600 space-y-1.5 bg-gray-50 p-3 rounded-lg border border-gray-100">
            {discoveredPainPoints.length === 0 ? (
              <li className="text-gray-400 italic">None captured yet.</li>
            ) : discoveredPainPoints.map((item, idx) => (
              <li key={idx} className="flex items-start gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-red-600 mt-1.5 shrink-0" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>

        <div className="flex flex-col gap-2">
          <span className="text-xs font-bold text-[#00254A] flex items-center gap-1.5">
            <CheckCircle2 size={14} className="text-amber-500" /> Confirmed needs
          </span>
          <ul className="text-xs text-gray-600 space-y-1.5 bg-gray-50 p-3 rounded-lg border border-gray-100">
            {confirmedNeeds.length === 0 ? (
              <li className="text-gray-400 italic">None captured yet.</li>
            ) : confirmedNeeds.map((item, idx) => (
              <li key={idx} className="flex items-start gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-amber-500 mt-1.5 shrink-0" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>

        <div className="flex flex-col gap-2">
          <span className="text-xs font-bold text-[#00254A] flex items-center gap-1.5">
            <Boxes size={14} className="text-emerald-600" /> Proposed Paxton Bank TTS solutions
          </span>
          <ul className="text-xs text-gray-600 space-y-1.5 bg-gray-50 p-3 rounded-lg border border-gray-100">
            {proposedSolutions.length === 0 ? (
              <li className="text-gray-400 italic">None captured yet.</li>
            ) : proposedSolutions.map((item, idx) => (
              <li key={idx} className="flex items-start gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 mt-1.5 shrink-0" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      {/* Drafted follow-up email */}
      <div className="flex flex-col gap-3">
        <div className="flex justify-between items-center">
          <span className="text-xs font-bold text-[#00254A]">
            Pre-drafted client follow-up email
          </span>
          {emailBody && (
            <button
              onClick={handleCopy}
              className="text-xs bg-sky-50 text-sky-700 font-bold px-3 py-1.5 rounded-lg flex items-center gap-1 hover:bg-sky-100 transition-all cursor-pointer"
            >
              {copied ? <Check size={14} /> : <Copy size={14} />}
              {copied ? 'Copied to clipboard' : 'Copy email draft'}
            </button>
          )}
        </div>

        {emailBody ? (
          <div className="p-4 bg-gray-50 rounded-lg border border-gray-200 text-xs text-gray-700 font-mono whitespace-pre-wrap leading-relaxed">
            {emailBody}
          </div>
        ) : (
          <div className="rounded-lg border border-dashed border-gray-200 bg-gray-50 py-6 text-center text-[12px] italic text-gray-400">
            Drafts itself from the live conversation once discovery items are captured.
          </div>
        )}
      </div>
    </div>
  );
};
