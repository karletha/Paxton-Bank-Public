import React from 'react';
import { Gauge, CheckCircle2, Circle } from 'lucide-react';
import { DiscoveryMilestones, SolutioningMilestones, SessionState } from '../services/sessionState';

interface MilestoneTrackerProps {
  sessionState: SessionState;
  discoveryMilestones: DiscoveryMilestones;
  solutioningMilestones: SolutioningMilestones;
}

// Friendly labels for the raw session-state enum.
const STATE_LABEL: Record<string, string> = {
  PRE_MEETING: 'Not started',
  DISCOVERY: 'Discovery',
  SOLUTIONING_EDUCATION: 'Solutioning',
  HUMAN_TAKEOVER: 'Banker leading',
  SALES_HANDOFF: 'Handoff',
  DEMO_COMPLETE: 'Complete',
};

const MilestoneRow: React.FC<{ done: boolean; label: string; hint: string }> = ({ done, label, hint }) => (
  <div className="flex items-center justify-between text-xs font-semibold">
    <div className="flex items-center gap-2 text-gray-700">
      {done ? (
        <CheckCircle2 size={16} className="text-emerald-500 shrink-0" />
      ) : (
        <Circle size={16} className="text-gray-300 shrink-0" />
      )}
      <span>{label}</span>
    </div>
    <span className="text-gray-400 font-mono text-[10px]">{hint}</span>
  </div>
);

export const MilestoneTracker: React.FC<MilestoneTrackerProps> = ({
  sessionState,
  discoveryMilestones,
  solutioningMilestones,
}) => {
  const getIsActive = (state: SessionState) => sessionState === state;

  const isDiscoveryDone =
    discoveryMilestones.expansion_discussed &&
    discoveryMilestones.complexity_revealed &&
    discoveryMilestones.pain_points_identified;

  const isSolutioningDone =
    solutioningMilestones.needs_confirmed &&
    solutioningMilestones.solutions_proposed &&
    solutioningMilestones.next_steps_outlined;

  const statePill =
    sessionState === 'HUMAN_TAKEOVER'
      ? 'rounded-full bg-red-50 px-2.5 py-0.5 text-[10.5px] font-bold text-red-600 ring-1 ring-red-200'
      : sessionState === 'DISCOVERY' || sessionState === 'SOLUTIONING_EDUCATION'
      ? 'rounded-full bg-sky-50 px-2.5 py-0.5 text-[10.5px] font-bold text-[#00254A]'
      : 'rounded-full bg-gray-100 px-2.5 py-0.5 text-[10.5px] font-bold text-gray-600';

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm flex flex-col gap-4">
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="mb-1 text-[10px] font-bold uppercase tracking-wider text-gray-400">Meeting progress</div>
          <h3 className="flex items-center gap-2 text-[13px] font-bold text-[#00254A]">
            <Gauge size={14} className="text-sky-600" /> Milestone tracker
          </h3>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-gray-400">State</span>
          <span className={statePill}>{STATE_LABEL[sessionState] ?? sessionState}</span>
        </div>
      </div>

      {/* Main Phases Layout */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Phase 1: Interactive Discovery */}
        <div className={`p-4 rounded-lg border transition-all ${
          getIsActive('DISCOVERY')
            ? 'bg-sky-50 border-sky-300 ring-1 ring-sky-300/40'
            : isDiscoveryDone
            ? 'bg-gray-50 border-gray-100 opacity-80'
            : 'bg-gray-50 border-gray-100 opacity-50'
        }`}>
          <div className="flex justify-between items-center mb-3">
            <span className="text-sm font-bold text-[#00254A] flex items-center gap-2">
              <span className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold text-white ${
                isDiscoveryDone ? 'bg-emerald-500' : 'bg-sky-600'
              }`}>
                {isDiscoveryDone ? '✓' : '1'}
              </span>
              Phase I: Discovery
            </span>
            <span className="text-xs text-gray-500 font-medium">
              {[
                discoveryMilestones.expansion_discussed,
                discoveryMilestones.complexity_revealed,
                discoveryMilestones.pain_points_identified,
              ].filter(Boolean).length}
              /3 Complete
            </span>
          </div>

          <div className="flex flex-col gap-2.5">
            <MilestoneRow done={discoveryMilestones.expansion_discussed} label="Expansion discussed" hint="Growth plans surfaced" />
            <MilestoneRow done={discoveryMilestones.complexity_revealed} label="Complexity revealed" hint="Operational friction named" />
            <MilestoneRow done={discoveryMilestones.pain_points_identified} label="Pain points identified" hint="Specific pains captured" />
          </div>
        </div>

        {/* Phase 2: Solutions proposed / Education */}
        <div className={`p-4 rounded-lg border transition-all ${
          getIsActive('SOLUTIONING_EDUCATION')
            ? 'bg-sky-50 border-sky-300 ring-1 ring-sky-300/40'
            : isSolutioningDone
            ? 'bg-gray-50 border-gray-100 opacity-80'
            : 'bg-gray-50 border-gray-100 opacity-50'
        }`}>
          <div className="flex justify-between items-center mb-3">
            <span className="text-sm font-bold text-[#00254A] flex items-center gap-2">
              <span className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold text-white ${
                isSolutioningDone ? 'bg-emerald-500' : 'bg-sky-600'
              }`}>
                {isSolutioningDone ? '✓' : '2'}
              </span>
              Phase II: Solutioning &amp; Education
            </span>
            <span className="text-xs text-gray-500 font-medium">
              {[
                solutioningMilestones.needs_confirmed,
                solutioningMilestones.solutions_proposed,
                solutioningMilestones.next_steps_outlined,
              ].filter(Boolean).length}
              /3 Complete
            </span>
          </div>

          <div className="flex flex-col gap-2.5">
            <MilestoneRow done={solutioningMilestones.needs_confirmed} label="Needs confirmed" hint="Client validates priorities" />
            <MilestoneRow done={solutioningMilestones.solutions_proposed} label="Solutions proposed" hint="Capabilities mapped to needs" />
            <MilestoneRow done={solutioningMilestones.next_steps_outlined} label="Next steps outlined" hint="Follow-up agreed" />
          </div>
        </div>
      </div>
    </div>
  );
};
