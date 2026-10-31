import React from 'react';
import { Shield, Info, X } from 'lucide-react';

interface GroundingSourceItem {
  id: string;
  title: string;
  url: string;
}

interface GovernanceDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  groundingSources: GroundingSourceItem[];
  restrictedTopics: string[];
}

export const GovernanceDrawer: React.FC<GovernanceDrawerProps> = ({
  isOpen,
  onClose,
  groundingSources,
  restrictedTopics,
}) => {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 overflow-hidden" id="governance-drawer-overlay">
      {/* Background shade overlay */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-xs transition-opacity"
        onClick={onClose}
      />

      <div className="absolute inset-y-0 right-0 max-w-full flex pl-10">
        <div className="w-screen max-w-md bg-white shadow-sm border-l border-gray-200 flex flex-col h-full transform transition-transform duration-300">

          {/* Header */}
          <div className="px-6 py-5 border-b border-gray-200 flex justify-between items-center">
            <div>
              <div className="mb-1 text-[10px] font-bold uppercase tracking-wider text-gray-400">Compliance &amp; grounding</div>
              <h3 className="flex items-center gap-2 text-[13px] font-bold text-[#00254A]">
                <Shield size={14} className="text-sky-600" /> Paxton Bank Bridget policy panel
              </h3>
            </div>

            <button
              onClick={onClose}
              id="close-governance-drawer-btn"
              className="p-1 rounded-full text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-all"
            >
              <X size={18} />
            </button>
          </div>

          {/* Drawer Scrollable Body */}
          <div className="flex-1 overflow-y-auto p-6 flex flex-col gap-6">

            {/* Context/Summary */}
            <div className="flex flex-col gap-2 bg-sky-50 border border-sky-100 rounded-xl p-4 text-xs text-[#00254A]">
              <h4 className="font-bold flex items-center gap-1">
                <Info size={14} className="text-sky-600" /> Grounding &amp; compliance rule
              </h4>
              <p className="leading-relaxed text-gray-600">
                Bridget operates inside strict, banker-supervised sandbox containers. The assistant is strictly forbidden from guaranteeing credits, committing pricing, or providing legal advice.
              </p>
            </div>

            {/* List of Prohibited / Restricted Topics */}
            <div className="flex flex-col gap-3">
              <h4 className="font-bold text-xs uppercase tracking-wider text-gray-400">
                Mandatory prohibited topics checklist
              </h4>

              <div className="grid grid-cols-2 gap-2">
                {restrictedTopics.map((topic, idx) => (
                  <div
                    key={idx}
                    className="flex items-center gap-2 px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-xs font-semibold text-gray-700"
                  >
                    <span className="w-1.5 h-1.5 rounded-full bg-red-600 shrink-0" />
                    <span className="capitalize truncate">{topic}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Grounding Source Listing */}
            <div className="flex flex-col gap-3">
              <h4 className="font-bold text-xs uppercase tracking-wider text-gray-400">
                Grounding sources ({groundingSources.length})
              </h4>
              <p className="text-xs text-gray-400 -mt-1 leading-normal">
                These domains define the verified corpus of text Bridget utilizes to construct solutions answers.
              </p>

              {groundingSources.length === 0 ? (
                <div className="rounded-lg border border-dashed border-gray-200 bg-gray-50 py-6 text-center text-[12px] italic text-gray-400">
                  No grounding sources loaded in this session.
                </div>
              ) : (
                <div className="flex flex-col gap-2.5 max-h-[220px] overflow-y-auto pr-1">
                  {groundingSources.map((source, i) => (
                    <div
                      key={source.id}
                      className="p-2.5 bg-gray-50 border border-gray-200 rounded-lg text-[11px]"
                    >
                      <div className="font-semibold text-gray-800 truncate leading-snug">
                        {i + 1}. {source.title}
                      </div>
                      <div className="text-[10px] text-sky-700 hover:underline truncate mt-0.5">
                        <a href={source.url} target="_blank" rel="noopener noreferrer">
                          {source.url}
                        </a>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

          </div>

        </div>
      </div>
    </div>
  );
};
